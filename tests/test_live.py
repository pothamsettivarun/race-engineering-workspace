"""Independent numerical checks and injected SDK lifecycle/HTTP coverage."""
import copy
import json
import math
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import request, error
from unittest.mock import patch

import server
from live import LiveService, SDKSource
from live_maps import get_map, valid_points
from live_strategy import DEFAULT_PLAN, calculate, simulate, validate_plan, measurements, rejoin


def sample(t=0, *, tick=None, lap=0, pct=0, fuel=30, car_count=3, replay=False, track=448, session=1, race=True):
    drivers = [{'CarIdx': i, 'UserID': 100+i, 'UserName': f'Driver {i}', 'CarNumber': str(10+i),
                'CarScreenName': 'Test GT3', 'CarClassID': 1 if i < 2 else 2, 'CarClassShortName': 'GT3' if i < 2 else 'Prototype',
                'TeamName': f'Team {i}'} for i in range(car_count)]
    v = {'SessionTime': t, 'SessionTick': tick if tick is not None else int(t*20), 'SessionUniqueID': session,
         'SessionNum': 0, 'SessionState': 4, 'SessionFlags': 4, 'PlayerCarIdx': 0,
         'SessionLapsTotal': 20, 'SessionTimeRemain': 604800, 'IsReplayPlaying': replay,
         'LapCompleted': lap, 'LapDistPct': pct, 'Lap': lap+1, 'FuelLevel': fuel, 'Speed': 30,
         'Throttle': .7, 'Brake': 0, 'SteeringWheelAngle': .1, 'Gear': 3, 'RPM': 4500,
         'OnPitRoad': False, 'PlayerTrackSurface': 3, 'PlayerCarMyIncidentCount': 0,
         'IsOnTrack': True, 'PitsOpen': True, 'AirTemp': 25, 'TrackTemp': 35, 'TrackWetness': 1,
         'CarIdxLapCompleted': [lap]*car_count, 'CarIdxLapDistPct': [pct]*car_count,
         'CarIdxPosition': list(range(1,car_count+1)), 'CarIdxClassPosition': [1,2,1][:car_count] if car_count <= 3 else list(range(1,car_count+1)),
         'CarIdxF2Time': [float(i*5) for i in range(car_count)], 'CarIdxEstTime': [pct*60]*car_count,
         'CarIdxLastLapTime': [60]*car_count, 'CarIdxBestLapTime': [59]*car_count,
         'CarIdxOnPitRoad': [False]*car_count, 'CarIdxTrackSurface': [3]*car_count}
    return {'values': v, 'capabilities': {'Speed': {'unit': 'm/s', 'count': 1}},
            'metadata': {'WeekendInfo': {'SessionID': 5, 'SubSessionID': 7, 'TrackID': track,
                'TrackDisplayName': 'Indianapolis', 'TrackConfigName': 'Road Course'},
                'DriverInfo': {'DriverCarIdx': 0, 'DriverCarFuelMaxLtr': 100, 'DriverCarMaxFuelPct': .8,
                               'DriverCarIsElectric': 0, 'Drivers': drivers},
                'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': 'Race' if race else 'Offline Testing'}]}}}


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.plan = copy.deepcopy(DEFAULT_PLAN)
        self.plan.update(pace_seconds=60, burn_litres=2, capacity_litres=10, race_laps=10,
                         pit_loss_seconds=20, refuel_lps=2, tire_seconds=10, driver_change_seconds=5)
        self.ctx = {'fuel': 10, 'burn': 2, 'pace': 60, 'capacity': 10, 'sdk_capacity': 10,
                    'progress': 0, 'finish': 10, 'elapsed': 0, 'driver': '100', 'driver_times': {}}
        self.scenario = {'id':'manual', 'name':'Two stops', 'stops':[
            {'lap':4, 'fuel_litres':8, 'tires':True, 'driver':None},
            {'lap':8, 'fuel_litres':4, 'tires':False, 'driver':None}]}

    def state(self):
        return {'status':'connected', 'player':{'FuelLevel':10, 'LapCompleted':0, 'LapDistPct':0},
                'session':{'elapsed_seconds':0, 'session_type':'Race', 'fuel_capacity_litres':10,
                           'driver_id':'100'}, 'laps':[]}

    def test_refueling_reserve_and_concurrent_service(self):
        result = simulate(self.scenario,self.ctx,self.plan)
        # 600 driving + (20 + max(4,10)) + (20 + 2) = 652 seconds.
        self.assertTrue(result['feasible']);self.assertEqual(result['projected_completion_seconds'],652)
        self.assertEqual(result['finish_fuel'],2);self.assertEqual(result['minimum_margin_litres'],0)
        self.assertEqual(result['timeline'][0]['fuel_before'],2)

    def test_sequential_service_and_driver_schedule(self):
        self.plan['service_mode']='sequential'
        result=simulate(self.scenario,self.ctx,self.plan)
        self.assertEqual(result['projected_completion_seconds'],656)
        self.plan['drivers']=[{'id':'100','name':'First','available_from_minutes':None,'available_until_minutes':5,
                              'max_stint_minutes':None,'min_drive_minutes':None},
                             {'id':'200','name':'Second','available_from_minutes':0,'available_until_minutes':None,
                              'max_stint_minutes':10,'min_drive_minutes':5}]
        self.scenario['stops'][0]['driver']='200'
        result=simulate(self.scenario,self.ctx,self.plan)
        self.assertTrue(result['feasible']);self.assertEqual(result['projected_completion_seconds'],661)
        self.assertEqual(result['driver_times'],{'100':240,'200':360})
        self.plan['drivers'][1]['available_until_minutes']=8
        result=simulate(self.scenario,self.ctx,self.plan)
        self.assertFalse(result['feasible']);self.assertTrue(any('unavailable' in e for e in result['errors']))

    def test_capacity_reserve_impossible_and_missing_inputs(self):
        bad=copy.deepcopy(self.scenario);bad['stops'][0]['fuel_litres']=9
        result=simulate(bad,self.ctx,self.plan)
        self.assertFalse(result['feasible']);self.assertTrue(any('capacity' in e for e in result['errors']))
        bad['stops'][0]['fuel_litres']=8;bad['stops'][0]['lap']=5
        self.assertFalse(simulate(bad,self.ctx,self.plan)['feasible'])
        self.plan['tire_seconds']=None
        result=simulate(self.scenario,self.ctx,self.plan)
        self.assertTrue(result['feasible']);self.assertIsNone(result['projected_completion_seconds'])
        self.assertIn('tire-service duration',result['missing_inputs'])

    def test_suggestions_are_independently_feasible(self):
        result=calculate(self.state(),self.plan)
        longest=result['scenarios'][0]
        self.assertEqual([s['lap'] for s in longest['stops']],[4,8])
        self.assertEqual([s['fuel_litres'] for s in longest['stops']],[8,4])
        self.assertTrue(longest['feasible']);self.assertEqual(longest['finish_fuel'],2)
        self.assertEqual(result['fuel_to_finish'],22);self.assertEqual(result['additional_fuel_to_finish'],12)

    def test_timed_finish_and_partial_lap(self):
        self.plan.update(race_laps=None,race_minutes=10)
        state=self.state();state['session']['elapsed_seconds']=125;state['player'].update(LapCompleted=2,LapDistPct=.5)
        result=calculate(state,self.plan)
        self.assertEqual(result['remaining_laps'],9) # ceil(475/60) + one finish lap
        self.assertEqual(result['fuel_to_finish'],20)
        self.assertEqual(result['latest_safe_pit_lap'],6)

    def test_practice_requires_target_stale_replay_electric(self):
        state=self.state();state['session']['session_type']='Practice';self.plan['race_laps']=None
        self.assertIn('target',calculate(state,self.plan)['message'])
        self.plan['race_laps']=10
        for field in ('stale','replay','electric'):
            s=copy.deepcopy(state)
            if field=='stale':s['status']='stale'
            else:s['session'][field]=True
            result=calculate(s,self.plan)
            self.assertFalse(result['scenarios']);self.assertIsNone(result['latest_safe_pit_lap'])

    def test_measurement_window_minimum_and_exclusions(self):
        laps=[{'eligible':True,'duration':60+i,'fuel_used':2+i*.1} for i in range(8)]
        laps.append({'eligible':False,'duration':1,'fuel_used':100})
        result=measurements(laps);self.assertEqual(result['sample_count'],5)
        self.assertEqual(result['pace_seconds'],65);self.assertEqual(result['burn_litres'],2.5)
        self.assertIsNone(measurements(laps[:2])['pace_seconds'])

    def test_validation_rejects_nonfinite_limits_and_bad_references(self):
        for value in [{'reserve_laps':None},{'reserve_laps':float('nan')},{'race_laps':2.5},
                      {'race_laps':10,'race_minutes':60},{'drivers':[{'id':'a'},{'id':'a'}]},
                      {'scenarios':[{'id':'s','stops':[{'lap':1,'fuel_litres':1,'driver':'unknown'}]}]}]:
            with self.assertRaises(ValueError):validate_plan(value)
        self.assertEqual(validate_plan({})['reserve_laps'],1)

    def test_rejoin_is_conditional_on_observed_field_pace(self):
        snapshot={'session':{'player_car_idx':0,'elapsed_seconds':0},'strategy':{'pace_seconds':60},
                  'cars':[{'car_idx':0,'number':'1','class_id':1,'progress':0},
                          {'car_idx':1,'number':'2','class_id':1,'progress':.1,'observed_pace_seconds':60,'on_pit_road':False,'track_surface':3},
                          {'car_idx':2,'number':'3','class_id':2,'progress':.4,'observed_pace_seconds':30,'on_pit_road':False,'track_surface':3}]}
        scenario={'feasible':True,'missing_inputs':[],'timeline':[{'lap':4,'arrival_seconds':240,'loss_seconds':30}]}
        result=rejoin(snapshot,scenario)
        self.assertEqual(result['horizon_seconds'],270);self.assertEqual(result['class_position'],2)
        self.assertEqual(result['observed_class_cars'],2)
        self.assertAlmostEqual(next(c for c in result['nearby'] if c['car_idx']==1)['gap_seconds'],-24)
        scenario['missing_inputs']=['pit loss'];self.assertIsNone(rejoin(snapshot,scenario))

    def test_manual_capacity_cannot_override_simulator_limit(self):
        self.plan['capacity_litres']=20
        result=calculate(self.state(),self.plan)
        self.assertTrue(all(not s['feasible'] for s in result['scenarios']))


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=0
        self.live=LiveService(Path(self.temp.name)/'data',clock=lambda:self.now)
    def tearDown(self):self.live.close();self.temp.cleanup()
    def feed(self,s):self.now=s['values']['SessionTime'];self.live.ingest(s)

    def test_live_metadata_missing_channels_and_staleness(self):
        self.feed(sample())
        snap=self.live.snapshot(True)
        self.assertEqual(snap['status'],'connected');self.assertEqual(snap['session']['fuel_capacity_litres'],80)
        self.assertIsNone(snap['player']['OilTemp']);self.assertEqual(snap['map']['status'],'outline')
        self.now=3
        self.assertEqual(self.live.snapshot()['status'],'stale')
        self.assertFalse(self.live.snapshot()['strategy']['scenarios'])
        stale=[a for a in self.live.snapshot()['alerts'] if a['key']=='stale'];self.assertEqual(len(stale),1)
        self.feed(sample(4,tick=80))
        self.assertEqual(self.live.snapshot()['status'],'connected');self.assertEqual(len(self.live.record['gaps']),1)

    def test_duplicate_tick_does_not_keep_feed_fresh(self):
        self.feed(sample());self.now=3;self.live.ingest(sample())
        self.assertEqual(self.live.snapshot()['status'],'stale')

    def test_null_sdk_results_and_metadata_lists_keep_collection_fresh(self):
        frame=sample()
        frame['metadata']['SessionInfo']['Sessions'][0]['ResultsPositions']=None
        self.feed(frame)
        snapshot=self.live.snapshot()
        self.assertEqual(snapshot['status'],'connected');self.assertEqual(len(snapshot['cars']),3)
        frame=sample(.05,tick=2)
        frame['metadata']['SessionInfo']['Sessions']=None
        frame['metadata']['DriverInfo']['Drivers']=None
        self.feed(frame)
        snapshot=self.live.snapshot()
        self.assertEqual(snapshot['status'],'connected');self.assertEqual(snapshot['cars'],[])
        self.assertIsNone(snapshot['session']['driver_id'])

    def test_paused_historical_replay_suspends_predictions(self):
        s=sample(100)
        s['values'].update(IsOnTrack=False,IsReplayPlaying=False,ReplaySessionNum=0,ReplaySessionTime=50,ReplayPlaySpeed=0)
        self.feed(s)
        self.assertTrue(self.live.snapshot()['session']['replay'])
        self.assertFalse(self.live.trace)
        self.assertFalse(self.live.snapshot()['strategy']['scenarios'])

    def test_missing_tick_uses_session_time_and_missing_eligibility_is_flagged(self):
        s=sample();s['values']['SessionTick']=None;del s['values']['OnPitRoad'];self.feed(s)
        self.assertEqual(self.live.snapshot()['status'],'connected')
        self.assertIn('eligibility channels unavailable',self.live.lap_reasons)

    def test_new_session_and_driver_change(self):
        self.feed(sample());first=self.live.record['id']
        changed=sample(.1);changed['metadata']['DriverInfo']['Drivers'][0]['UserID']=500
        self.feed(changed)
        self.assertTrue(any(a['key']=='driver-change' for a in self.live.events))
        self.feed(sample(.2,session=2));self.assertNotEqual(first,self.live.record['id'])
        current=self.live.record['id'];self.feed(sample(0,session=2,tick=100))
        # Only substantial backwards jumps are session resets.
        self.feed(sample(2,session=2));self.feed(sample(0,session=2,tick=101))
        self.assertNotEqual(current,self.live.record['id'])

    def test_complete_laps_refuel_pit_incident_and_replay_exclusions(self):
        for tick in range(0,401):
            t=tick*.05;lap=int(t/5);pct=(t%5)/5
            self.feed(sample(t,tick=tick,lap=lap,pct=pct,fuel=30-.4*t))
        m=measurements(self.live.laps)
        self.assertEqual(m['sample_count'],3);self.assertAlmostEqual(m['pace_seconds'],5)
        self.assertAlmostEqual(m['burn_litres'],2)
        for tick in range(401,501):
            t=tick*.05;s=sample(t,tick=tick,lap=int(t/5),pct=(t%5)/5,fuel=30-.4*t)
            if tick>405:s['values']['PlayerCarMyIncidentCount']=1
            if 410<=tick<=450:s['values']['OnPitRoad']=True
            self.feed(s)
        self.assertFalse(self.live.laps[-1]['eligible']);self.assertIn('incident',self.live.laps[-1]['reasons'])
        self.assertIn('pit lane',self.live.laps[-1]['reasons'])
        self.feed(sample(25.1,tick=502,lap=5,pct=.02,fuel=50,replay=True))
        self.assertFalse(self.live.snapshot()['strategy']['scenarios'])

    def test_pit_visits_and_invalid_map_positions(self):
        self.feed(sample())
        entry=sample(.1);entry['values']['CarIdxOnPitRoad'][1]=True;self.feed(entry)
        exit=sample(.2);exit['values']['CarIdxLapDistPct'][2]=-1;self.feed(exit)
        self.assertEqual(self.live.pits[-1]['car_idx'],1);self.assertAlmostEqual(self.live.pits[-1]['duration'],.1)
        self.assertEqual(self.live.state['cars'][1]['observed_pits'],1)
        self.assertIsNone(self.live.state['cars'][2]['lap_dist_pct'])
        invalid=sample(.3);invalid['values']['CarIdxTrackSurface'][1]=-1;self.feed(invalid)
        self.assertIsNone(next(c for c in self.live.state['cars'] if c['car_idx']==1)['progress'])

    def test_classification_and_relative_wrap(self):
        s=sample();s['values']['CarIdxLapDistPct']=[.98,.02,.5]
        s['values']['CarIdxLapCompleted']=[3,2,0];s['values']['CarIdxEstTime']=[58,1,30]
        self.feed(s)
        other=next(c for c in self.live.state['cars'] if c['car_idx']==1)
        self.assertEqual(other['gap_laps'],1);self.assertEqual(other['sdk_gap_seconds'],5)
        self.assertEqual(other['relative_seconds'],2) # 1 - 58 wrapped across a 59-second lap
        practice=sample(.1,race=False);self.feed(practice)
        self.assertIsNone(self.live.state['cars'][1]['sdk_gap_seconds'])

    def test_map_configuration_mismatch_and_validation(self):
        self.assertEqual(get_map(self.live.data,448,'Oval')['status'],'lap_progress')
        self.assertFalse(valid_points([{'distance':i,'lat':float('nan'),'lon':0} for i in range(100)]))
        self.feed(sample(track=999));self.assertEqual(self.live.state['map']['status'],'lap_progress')

    def test_sdk_absence_is_actionable_and_no_simulator_commands(self):
        source=SDKSource()
        with patch.dict('sys.modules', {'irsdk':None}):
            self.assertIsNone(source.read())
            self.assertIn('pip install',source.error)
        source.close()

    def test_limiter_is_not_a_critical_engine_warning(self):
        s=sample();s['values']['EngineWarnings']=16|32;self.feed(s)
        self.assertFalse(any(a['key']=='engine' for a in self.live.events))
        s=sample(.1);s['values']['EngineWarnings']=4;self.feed(s)
        s=sample(.2);s['values']['EngineWarnings']=4;self.feed(s)
        self.assertEqual(len([a for a in self.live.events if a['key']=='engine']),1)
        self.assertIn('Oil pressure',self.live.events[-1]['message'])

    def test_drive_through_does_not_reset_fuel_stint(self):
        self.feed(sample())
        s=sample(.1);s['values']['CarIdxOnPitRoad'][0]=True;s['values']['OnPitRoad']=True;self.feed(s)
        self.feed(sample(.2))
        self.assertFalse(self.live.stints)
        self.assertEqual(self.live.player_stint_start,0)

    def test_pit_tire_snapshot_initial_time_is_unknown(self):
        s=sample();s['values']['LFtempCL']=60;self.feed(s)
        self.assertIsNone(self.live.state['tires']['updated_seconds'])
        s=sample(.1);s['values']['LFtempCL']=70;self.feed(s)
        self.assertEqual(self.live.state['tires']['updated_seconds'],.1)

    def test_plan_is_session_guarded_and_history_resume(self):
        self.feed(sample())
        ident=self.live.record['id']
        with self.assertRaises(ValueError):self.live.update_plan({},'old-session')
        self.live.update_plan({'race_minutes':480},ident)
        self.assertEqual(self.live.history(ident)['plan_revisions'],1)
        self.assertTrue(self.live.history(ident)['journal'])
        self.live.close()
        resumed=LiveService(self.live.data,clock=lambda:10)
        try:
            resumed.ingest(sample(10));self.assertEqual(resumed.record['id'],ident)
            self.assertNotEqual(resumed.snapshot()['collector_id'],self.live.snapshot()['collector_id'])
            self.assertEqual(resumed.plan['race_minutes'],480);self.assertEqual(resumed.record['gaps'][0]['reason'],'server restart')
        finally:resumed.close()

    def test_bounded_traces_and_history_pagination(self):
        for i in range(1500):self.feed(sample(i*.05,tick=i))
        self.assertEqual(len(self.live.trace),1200)
        for i in range(1100):self.live._event('test',f'Alert {i}')
        self.live._checkpoint();self.assertEqual(len(self.live.events),1000)
        page=self.live.history(self.live.record['id'],kind='alert')
        self.assertEqual(len(page['journal']),500)
        previous=self.live.history(self.live.record['id'],kind='alert',before=page['before'])
        self.assertLess(previous['journal'][-1]['index'],page['journal'][0]['index'])


class LiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(server,'DATA',Path(self.temp.name)/'data');self.patch.start()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.http.live.ingest(sample())
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True);self.worker.start()
        self.base=f'http://127.0.0.1:{self.http.server_port}'
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.patch.stop();self.temp.cleanup()

    def test_snapshot_plan_history_and_multiple_streams(self):
        with request.urlopen(self.base+'/api/live/snapshot') as r:snap=json.load(r)
        body=json.dumps({'session_id':snap['session']['id'],'revision':snap['plan_revision'],'plan':{'race_laps':20}}).encode()
        with request.urlopen(request.Request(self.base+'/api/live/plan',data=body,headers={'Content-Type':'application/json'})) as r:
            self.assertEqual(json.load(r)['plan']['race_laps'],20)
        with request.urlopen(self.base+'/api/live/history') as r:self.assertEqual(len(json.load(r)),1)
        a=request.urlopen(self.base+'/api/live/events',timeout=3);b=request.urlopen(self.base+'/api/live/events',timeout=3)
        try:
            self.assertEqual(a.headers['Content-Type'],'text/event-stream; charset=utf-8')
            for response in (a,b):
                self.assertTrue(response.readline().startswith(b'id:'))
                self.assertEqual(response.readline(),b'event: snapshot\n')
                data=json.loads(response.readline()[6:]);self.assertEqual(data['session']['id'],snap['session']['id'])
            self.assertEqual(self.http.live.sequence,1)
        finally:a.close();b.close()

    def test_localhost_security_and_bad_history_cursor(self):
        for path,headers in [('/api/live/snapshot',{'Origin':'https://hostile.example'}),('/api/live/history?before=abc',{})]:
            with self.assertRaises(error.HTTPError) as caught:request.urlopen(request.Request(self.base+path,headers=headers))
            caught.exception.close()


if __name__=='__main__':unittest.main()
