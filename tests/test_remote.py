"""Remote authorization and independent SDK command/readback failure checks."""
import copy
import json
import socket
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib import request, error
from unittest.mock import patch

import server
from live import LiveService, SDKSource
from pit_control import PitError
from remote import RemoteGateway, RemoteHandler, validate_settings
from test_live import sample


ENGINEER='engineer@example.com'
ORIGIN='https://stint-test.example.ts.net:8443'


class FakeSDK:
    error=''
    def __init__(self):
        self.flags=16|32|64
        self.fuel=12
        self.calls=[]
        self.ignore=set()
        self.fail=None
        self.sequence=0
        self.paused=False
        self.replay=False
        self.driver=100
        self.service=False
        self.thread_ids=[]
        self.pressures={'lf':150,'rf':151,'lr':152,'rr':153}
        self.compound=0;self.auto=False;self.auto_available=True;self.macros=[];self.messages=[]

    def frame(self, t):
        self.sequence+=1
        f=sample(t,tick=self.sequence)
        f['values'].update(PitSvFuel=self.fuel,PitSvFlags=self.flags,PitstopActive=self.service,IsReplayPlaying=self.replay)
        f['values'].update({f'PitSv{c.upper()}P':p for c,p in self.pressures.items()})
        f['values'].update(PitSvTireCompound=self.compound,dpFuelAutoFillEnabled=float(self.auto_available),dpFuelAutoFillActive=float(self.auto),FastRepairAvailable=3)
        f['metadata']['DriverInfo']['DriverTires']=[{'TireIndex':0,'TireCompoundType':'Hard'},{'TireIndex':1,'TireCompoundType':'Wet'}]
        f['metadata']['DriverInfo']['Drivers'][0]['UserID']=self.driver
        return f

    def read(self):
        if self.paused:return None
        return self.frame(self.sequence*.05)

    def close(self):pass

    def pit_command(self, mode, value):
        self.calls.append((mode,value));self.thread_ids.append(threading.get_ident())
        if mode==self.fail:return False
        if mode in self.ignore:return True
        if mode=='clear_tires':self.flags&=~15
        elif mode in ('lf','rf','lr','rr'):
            self.flags|=1<<('lf','rf','lr','rr').index(mode)
            if value:self.pressures[mode]=value
        elif mode=='fuel':self.flags|=16;self.fuel=value
        elif mode=='clear_fuel':self.flags&=~16
        elif mode=='ws':self.flags|=32
        elif mode=='clear_ws':self.flags&=~32
        elif mode=='fr':self.flags|=64
        elif mode=='clear_fr':self.flags&=~64
        elif mode=='tc':self.compound=value
        else:raise AssertionError(mode)
        return True

    def auto_fuel_command(self, enabled, margin):
        self.macros.append((enabled,margin))
        if 'auto_fuel' not in self.ignore:self.auto=enabled
        if enabled:self.flags|=16
        return True

    def private_confirmation(self, car_number, engineer, summary):
        self.messages.append((car_number,engineer,summary,threading.get_ident()))
        return 'chat' not in self.ignore

    def zero_fuel_amount(self, current):
        self.macros.append(('zero_fuel',current));self.fuel=0;self.flags|=16;return True


def command_body(live, **changes):
    snap=live.snapshot()
    body={'id':uuid.uuid4().hex,'collector_id':snap['collector_id'],'session_id':snap['session']['id'],
          'driver_id':snap['session']['driver_id'],'player_car_idx':snap['session']['player_car_idx'],
          'sequence':snap['sequence'],'revision':snap['pit_control']['revision'],'fuel_litres':42,
          'tires':{'lf':True,'rf':True,'lr':False,'rr':False}}
    body.update(changes)
    return body


class PitControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=0.;self.sdk=FakeSDK()
        self.live=LiveService(Path(self.temp.name),self.sdk,clock=lambda:self.now)
        self.feed();self.live.pit.authorize(True,ENGINEER)

    def feed(self, frame=None):
        self.now+=.05;self.live.ingest(frame or self.sdk.frame(self.now))

    def tearDown(self):self.live.close();self.temp.cleanup()

    def test_commands_wait_for_collector_and_fresh_readback_preserve_other_services(self):
        body=command_body(self.live)
        result=self.live.pit.submit(body,ENGINEER)
        self.assertEqual(result['status'],'pending');self.assertEqual(self.sdk.calls,[])
        self.live.pit.process()
        self.assertEqual(self.sdk.calls,[('clear_tires',0),('lf',0),('rf',0),('fuel',42)])
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'pending')
        self.feed()
        result=self.live.pit.status()['commands'][-1]
        self.assertEqual(result['status'],'confirmed');self.assertEqual(self.sdk.flags,16|32|64|1|2)
        self.assertEqual(self.sdk.pressures,{'lf':150,'rf':151,'lr':152,'rr':153})
        self.assertEqual(result['actual']['fuel_litres'],42)
        self.assertEqual(len(self.live.history(self.live.record['id'],'pit_command_result')['journal']),1)

    def test_duplicate_does_not_dispatch_twice_and_reuse_rejected(self):
        body=command_body(self.live);first=self.live.pit.submit(body,ENGINEER)
        self.assertEqual(self.live.pit.submit(body,ENGINEER)['id'],first['id'])
        self.live.pit.process();self.feed();self.live.pit.submit(body,ENGINEER);self.live.pit.process()
        self.assertEqual(len(self.sdk.calls),4)
        with self.assertRaises(PitError):self.live.pit.submit({**body,'fuel_litres':41},ENGINEER)

    def test_fuel_zero_clears_without_requesting_existing_quantity(self):
        self.live.pit.submit(command_body(self.live,fuel_litres=0),ENGINEER);self.live.pit.process();self.feed()
        self.assertIn(('clear_fuel',0),self.sdk.calls);self.assertNotIn(('fuel',0),self.sdk.calls)
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'confirmed')

    def test_all_standard_fields_read_back_and_private_confirmation_once(self):
        self.live.pit.authorize(True,ENGINEER,engineer_name='Race Engineer')
        body=command_body(self.live,fuel_enabled=False,tear_off=False,fast_repair=False,
                          tires={c:True for c in self.sdk.pressures},
                          pressures_kpa={'lf':159,'rf':160,'lr':161,'rr':162},tire_compound=1,
                          actor_name='Spoofed name')
        self.live.pit.submit(body,ENGINEER);self.live.pit.process()
        self.assertEqual(self.sdk.messages,[])
        self.feed();command=self.live.pit.status()['commands'][-1]
        self.assertEqual(command['status'],'confirmed');self.assertEqual(command['actor_name'],'Race Engineer')
        self.assertFalse(command['actual']['fuel_enabled']);self.assertEqual(command['actual']['fuel_litres'],42)
        self.assertEqual(command['actual']['tire_compound'],1)
        for key in ['fuel_enabled','tear_off','fast_repair','tire_compound','pressure_lf','pressure_rf','pressure_lr','pressure_rr']:
            self.assertIn(key,command['confirmed_fields'])
        self.live.pit.process();self.live.pit.process();self.live.pit.submit(body,ENGINEER)
        self.assertEqual(len(self.sdk.messages),1);self.assertEqual(self.sdk.messages[0][:2],('10','Race Engineer'))
        self.assertEqual(self.sdk.messages[0][3],threading.get_ident())

    def test_pressure_changes_keep_unselected_tires_unchecked(self):
        self.live.pit.submit(command_body(self.live,tires={c:False for c in self.sdk.pressures},pressures_kpa={'lf':160}),ENGINEER)
        self.live.pit.process();self.feed();self.live.pit.process()
        self.assertEqual(self.sdk.pressures['lf'],160);self.assertEqual(self.sdk.flags&15,0)
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'confirmed')

    def test_explicit_zero_fuel_restores_amount_and_then_unchecks_refueling(self):
        self.live.pit.submit(command_body(self.live,fuel_litres=0,fuel_enabled=False),ENGINEER)
        self.live.pit.process();self.feed()
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'pending')
        self.live.pit.process();self.feed();self.live.pit.process()
        command=self.live.pit.status()['commands'][-1]
        self.assertEqual(command['status'],'confirmed');self.assertEqual(command['actual']['fuel_litres'],0)
        self.assertFalse(command['actual']['fuel_enabled']);self.assertEqual(self.sdk.macros,[('zero_fuel',12)])

    def test_ignored_pressure_is_partial_and_does_not_send_success_chat(self):
        self.sdk.ignore={'lf'}
        self.live.pit.submit(command_body(self.live,tires={c:False for c in self.sdk.pressures},pressures_kpa={'lf':160}),ENGINEER)
        self.live.pit.process();self.feed();self.now+=2.1;self.live.last_received=self.now
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'partial')
        self.live.pit.process();self.assertEqual(self.sdk.messages,[])

    def test_auto_fuel_margin_is_explicitly_unverified_and_preserved_when_omitted(self):
        self.live.pit.submit(command_body(self.live,fuel_litres=None,auto_fuel=True,auto_fuel_margin_laps=1.2,fuel_enabled=True),ENGINEER)
        self.live.pit.process();self.feed();self.live.pit.process()
        command=self.live.pit.status()['commands'][-1]
        self.assertEqual(command['status'],'confirmed');self.assertEqual(command['unverified_fields'],['auto_fuel_margin_laps'])
        self.assertIn('verify Auto Fuel margin',self.sdk.messages[-1][2])
        self.assertEqual(self.sdk.macros,[(True,1.2)])
        self.live.pit.submit(command_body(self.live,fuel_litres=None,auto_fuel=True,fuel_enabled=True),ENGINEER)
        self.live.pit.process();self.feed();self.live.pit.process()
        self.assertEqual(self.sdk.macros,[(True,1.2)])

    def test_chat_failure_does_not_undo_confirmed_selection_or_retry(self):
        self.sdk.ignore={'chat'}
        self.live.pit.submit(command_body(self.live),ENGINEER);self.live.pit.process();self.feed();self.live.pit.process()
        command=self.live.pit.status()['commands'][-1]
        self.assertEqual(command['status'],'confirmed');self.assertEqual(command['private_confirmation']['status'],'failed')
        self.live.pit.process();self.assertEqual(len(self.sdk.messages),1)

    def test_extended_fields_reject_unknown_compounds_invalid_pressure_and_margin(self):
        for changes in [{'pressures_kpa':{'lf':True}},{'pressures_kpa':{'lf':0}},{'pressures_kpa':{'bad':159}},
                        {'tire_compound':99},{'tire_compound':1},{'tear_off':1},{'auto_fuel':True,'auto_fuel_margin_laps':11},
                        {'auto_fuel':True,'auto_fuel_margin_laps':.15}]:
            with self.subTest(changes=changes),self.assertRaises(PitError):self.live.pit.submit(command_body(self.live,**changes),ENGINEER)

    def test_revocation_before_chat_delivery_skips_the_message(self):
        self.live.pit.submit(command_body(self.live),ENGINEER);self.live.pit.process();self.feed()
        self.live.pit.authorize(False,ENGINEER);self.live.pit.process()
        self.assertEqual(self.sdk.messages,[])
        self.assertEqual(self.live.pit.status()['commands'][-1]['private_confirmation']['status'],'skipped')

    def test_auto_fuel_availability_is_distinct_from_next_stop_checkbox(self):
        self.assertTrue(self.live.pit.status()['auto_fuel_supported'])
        self.assertFalse(self.live.pit.status()['actual']['auto_fuel'])
        self.sdk.auto_available=False;self.feed()
        self.assertFalse(self.live.pit.status()['auto_fuel_supported'])
        with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live,auto_fuel=True),ENGINEER)

    def test_authorization_in_stall_does_not_allow_dispatch_until_the_car_leaves(self):
        self.live.pit.authorize(False,ENGINEER)
        frame=self.sdk.frame(self.now+.05);frame['values']['PlayerCarInPitStall']=True;self.feed(frame)
        self.live.pit.authorize(True,ENGINEER)
        self.assertTrue(self.live.pit.enabled)
        with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live),ENGINEER)
        self.assertEqual(self.sdk.calls,[])

    def test_ignored_fuel_results_partial_without_retry(self):
        self.sdk.ignore={'fuel'}
        self.live.pit.submit(command_body(self.live),ENGINEER);self.live.pit.process();self.feed()
        self.now+=2.1;self.live.last_received=self.now
        result=self.live.pit.status()['commands'][-1]
        self.assertEqual(result['status'],'partial');self.assertNotIn('fuel',result['confirmed_fields'])
        self.live.pit.process();self.assertEqual(len(self.sdk.calls),4)

    def test_entirely_ignored_batch_is_unconfirmed(self):
        self.sdk.ignore={'clear_tires','lf','rf','lr','rr','fuel'}
        self.live.pit.submit(command_body(self.live,tires={c:True for c in ('lf','rf','lr','rr')}),ENGINEER)
        self.live.pit.process();self.feed();self.now+=2.1;self.live.last_received=self.now
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'unconfirmed')

    def test_readback_after_deadline_cannot_confirm(self):
        self.live.pit.submit(command_body(self.live),ENGINEER);self.live.pit.process()
        self.now+=2.1;self.feed()
        result=self.live.pit.status()['commands'][-1]
        self.assertEqual(result['status'],'unconfirmed')
        self.assertEqual(result['actual']['fuel_litres'],42)
        self.feed();self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'unconfirmed')

    def test_delivery_failure_can_show_actual_partial_result(self):
        self.sdk.fail='fuel'
        self.live.pit.submit(command_body(self.live),ENGINEER);self.live.pit.process();self.feed()
        self.now+=2.1;self.live.last_received=self.now
        result=self.live.pit.status()['commands'][-1]
        self.assertEqual(result['status'],'partial');self.assertIn('SDK',result['message'])

    def test_expired_queue_and_reconnect_never_replay(self):
        self.live.pit.submit(command_body(self.live),ENGINEER);self.now+=2.1;self.live.pit.process()
        self.assertEqual(self.sdk.calls,[]);self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'failed')
        self.feed();self.live.pit.process();self.assertEqual(self.sdk.calls,[])

    def test_manual_change_before_dispatch_rejects_and_after_confirmation_is_observed(self):
        body=command_body(self.live);self.live.pit.submit(body,ENGINEER)
        self.sdk.fuel=20;self.feed();self.live.pit.process()
        self.assertEqual(self.sdk.calls,[]);self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'failed')
        with self.assertRaises(PitError):self.live.pit.submit({**body,'id':uuid.uuid4().hex},ENGINEER)
        self.live.pit.submit(command_body(self.live),ENGINEER);self.live.pit.process();self.feed()
        self.sdk.fuel=8;self.feed();self.live.pit.process()
        self.assertEqual(self.live.pit.status()['actual']['fuel_litres'],8);self.assertEqual(len(self.sdk.calls),4)

    def test_replay_stale_service_and_missing_channels_reject(self):
        for key,value in [('IsReplayPlaying',True),('IsOnTrack',False),('PitstopActive',True),('PlayerCarInPitStall',True),('PitSvFlags',None)]:
            frame=self.sdk.frame(self.now+.05);frame['values'][key]=value;self.feed(frame)
            with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live),ENGINEER)
            self.feed()
        self.now+=2.1
        with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live),ENGINEER)
        self.assertEqual(self.sdk.calls,[])

    def test_driver_session_restart_and_local_revoke_disarm(self):
        self.live.pit.submit(command_body(self.live),ENGINEER);self.sdk.driver=200;self.feed();self.live.pit.process()
        self.assertFalse(self.live.pit.enabled);self.assertEqual(self.sdk.calls,[])
        self.live.pit.authorize(True,ENGINEER)
        frame=self.sdk.frame(self.now+.05);frame['values']['SessionUniqueID']=900;self.feed(frame)
        self.assertFalse(self.live.pit.enabled)
        self.live.pit.authorize(True,ENGINEER);self.live.pit.submit(command_body(self.live),ENGINEER)
        self.live.pit.authorize(False,ENGINEER);self.live.pit.process();self.assertEqual(self.sdk.calls,[])
        resumed=LiveService(self.live.data,self.sdk,clock=lambda:self.now)
        try:self.assertFalse(resumed.pit.enabled)
        finally:resumed.close()

    def test_replay_service_disconnect_cancel_pending_and_do_not_retry(self):
        for mode in ('replay','service','disconnect'):
            with self.subTest(mode=mode):
                self.feed();self.live.pit.authorize(True,ENGINEER)
                self.live.pit.submit(command_body(self.live),ENGINEER)
                if mode=='disconnect':self.live.error='Simulator disconnected'
                else:
                    frame=self.sdk.frame(self.now+.05)
                    frame['values']['IsReplayPlaying' if mode=='replay' else 'PitstopActive']=True
                    self.feed(frame)
                self.live.pit.process();self.assertIsNone(self.live.pit.pending)
                self.feed();self.live.pit.process();self.assertEqual(self.sdk.calls,[])

    def test_old_snapshot_and_fuel_missing_do_not_guess(self):
        body=command_body(self.live);self.now+=2.1;self.feed()
        with self.assertRaises(PitError):self.live.pit.submit(body,ENGINEER)
        frame=self.sdk.frame(self.now+.05);frame['values'].pop('PitSvFuel');self.feed(frame)
        self.assertFalse(self.live.pit.status()['fuel_supported'])
        self.live.pit.submit(command_body(self.live,fuel_litres=None),ENGINEER)
        self.live.pit.process();self.feed(frame=self.sdk.frame(self.now+.05))
        self.assertNotIn(('fuel',42),self.sdk.calls)
        self.assertEqual(self.live.pit.status()['commands'][-1]['status'],'confirmed')

    def test_bad_quantities_identities_permissions_and_concurrency(self):
        for changes in [{'fuel_litres':42.5},{'fuel_litres':True},{'fuel_litres':81},{'fuel_litres':-1},
                        {'driver_id':'other'},{'collector_id':'old'},{'sequence':-1},{'revision':-1},
                        {'tires':{'lf':True}},{'id':'bad'}]:
            with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live,**changes),ENGINEER)
        with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live),'viewer@example.com')
        self.live.pit.submit(command_body(self.live),ENGINEER)
        with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live),ENGINEER)

    def test_missing_fuel_and_electric_leave_tires_available(self):
        frame=self.sdk.frame(self.now+.05);frame['metadata']['DriverInfo']['DriverCarIsElectric']=1;self.feed(frame)
        self.assertFalse(self.live.pit.status()['fuel_supported'])
        with self.assertRaises(PitError):self.live.pit.submit(command_body(self.live),ENGINEER)
        self.live.pit.submit(command_body(self.live,fuel_litres=None),ENGINEER);self.live.pit.process();self.feed(frame=self.sdk.frame(self.now+.05))
        self.assertNotIn(('fuel',42),self.sdk.calls)

    def test_sdk_adapter_only_allows_the_command_set(self):
        adapter=SDKSource()
        with self.assertRaises(ValueError):adapter.pit_command('chat_command',0)
        self.assertFalse(adapter.pit_command('fuel',40))

    def test_command_cache_bounded_and_plan_revision_conflicts(self):
        for i in range(132):
            self.live.pit.submit(command_body(self.live,fuel_litres=10+i%2),ENGINEER);self.live.pit.process();self.feed()
        self.assertEqual(len(self.live.pit.commands),128);self.assertLessEqual(len(self.live.pit.observations),80)
        ident=self.live.record['id'];self.live.update_plan({'race_laps':30},ident,0,ENGINEER)
        with self.assertRaises(PitError):self.live.update_plan({'race_laps':40},ident,0)
        self.assertEqual(self.live.plan['race_laps'],30)


class RemoteHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(server,'DATA',Path(self.temp.name));self.patch.start()
        self.sdk=FakeSDK();self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler,live_source=self.sdk,remote_port=0)
        self.http.live.start();threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.local=f'http://127.0.0.1:{self.http.server_port}'
        self.gateway=self.http.remote
        self.gateway.configure({'enabled':True,'origin':ORIGIN,'users':[{'login':ENGINEER,'role':'engineer'},
                                  {'login':'viewer@example.com','role':'viewer'}]})
        self.base=f'http://127.0.0.1:{self.gateway.http.server_port}'
        self.headers={'Tailscale-User-Login':ENGINEER,'Origin':ORIGIN}

    def tearDown(self):self.http.shutdown();self.http.server_close();self.patch.stop();self.temp.cleanup()

    def fetch(self,path,body=None,headers=None):
        headers=self.headers if headers is None else headers
        if body is not None:headers={**headers,'Content-Type':'application/json'}
        req=request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        return request.urlopen(req,timeout=4)

    def assert_code(self, code, path, body=None, headers=None):
        with self.assertRaises(error.HTTPError) as caught:self.fetch(path,body,headers)
        self.assertEqual(caught.exception.code,code);caught.exception.close()

    def test_identity_origin_host_and_route_allowlists(self):
        with self.fetch('/api/remote/me') as r:self.assertEqual(json.load(r)['role'],'engineer')
        self.assert_code(401,'/api/live/snapshot',headers={})
        self.assert_code(403,'/api/live/snapshot',headers={'Tailscale-User-Login':'stranger@example.com'})
        self.assert_code(403,'/api/live/snapshot',headers={**self.headers,'Host':'evil.example'})
        self.assert_code(403,'/api/live/plan',{},headers={'Tailscale-User-Login':ENGINEER})
        self.assert_code(403,'/api/live/plan',{},headers={**self.headers,'Origin':'https://evil.example'})
        for path in ['/api/status','/api/runs','/api/files','/api/ai-jobs','/api/remote/settings','/app.js','/../server.py']:
            self.assert_code(404,path)
        for path in ['/api/remote/control','/api/upload','/api/ai-config']:self.assert_code(404,path,{})

    def test_standalone_assets_have_no_workspace_bootstrap(self):
        with self.fetch('/') as r:
            page=r.read().decode();self.assertIn('/live-boot.js',page);self.assertNotIn('/app.js',page)
            self.assertNotIn('{{LIVE_PANEL}}',page);self.assertIn('live-pit-controls',page)
        with self.fetch('/live-boot.js') as r:self.assertTrue(r.headers['Content-Type'].startswith('text/javascript'))

    def test_viewer_can_read_but_not_change_plan_or_pits(self):
        headers={**self.headers,'Tailscale-User-Login':'viewer@example.com'}
        with self.fetch('/api/live/snapshot',headers=headers) as r:snap=json.load(r)
        for path in ['/api/live/plan','/api/live/pit']:self.assert_code(403,path,{},headers)
        with self.fetch('/api/live/history',headers=headers) as r:self.assertEqual(len(json.load(r)),1)
        self.assertFalse(snap['pit_control']['enabled'])

    def test_engineer_edits_plan_with_conflict_detection_and_collector_dispatch(self):
        with self.fetch('/api/live/snapshot') as r:snap=json.load(r)
        body={'session_id':snap['session']['id'],'revision':snap['plan_revision'],'plan':{'race_laps':35}}
        with self.fetch('/api/live/plan',body) as r:self.assertEqual(json.load(r)['plan']['race_laps'],35)
        self.assert_code(409,'/api/live/plan',body)
        self.assert_code(400,'/api/live/plan',{'session_id':snap['session']['id'],'plan':{}})
        self.assert_code(403,'/api/live/pit',command_body(self.http.live))
        self.http.live.pit.authorize(True,ENGINEER)
        with self.fetch('/api/live/pit',command_body(self.http.live)) as r:command=json.load(r)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            status=self.http.live.pit.status()['commands'][-1]
            if status['status']!='pending':break
            time.sleep(.02)
        self.assertEqual(status['status'],'confirmed');self.assertEqual(status['id'],command['id'])
        self.assertEqual(set(self.sdk.thread_ids),{self.http.live.thread.ident})

    def test_eight_stream_limit_and_live_revoke(self):
        connections=[]
        try:
            for i in range(8):
                r=self.fetch('/api/live/events');self.assertEqual(r.readline(),b'event: snapshot\n');connections.append(r)
            self.assert_code(429,'/api/live/events')
            self.assertEqual(len(self.gateway.streams),8)
            self.gateway.configure({**self.gateway.settings,'users':[]})
            deadline=time.monotonic()+3
            while self.gateway.streams and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(len(self.gateway.streams),0)
            self.assert_code(403,'/api/live/snapshot')
        finally:
            for r in connections:r.close()

    def test_origin_change_during_body_read_rejects_write(self):
        snap=self.http.live.snapshot();body=json.dumps({'session_id':snap['session']['id'],
              'revision':snap['plan_revision'],'plan':{'race_laps':40}}).encode()
        entered=threading.Event();original=RemoteHandler.authorize
        def authorize(handler,write=False):
            member=original(handler,write)
            if write:entered.set()
            return member
        with patch.object(RemoteHandler,'authorize',authorize):
            connection=socket.create_connection(('127.0.0.1',self.gateway.http.server_port),timeout=4)
            try:
                headers=(f'POST /api/live/plan HTTP/1.1\r\nHost: 127.0.0.1:{self.gateway.http.server_port}\r\n'
                         f'Tailscale-User-Login: {ENGINEER}\r\nOrigin: {ORIGIN}\r\n'
                         f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()
                connection.sendall(headers+body[:1]);self.assertTrue(entered.wait(2))
                self.gateway.configure({**self.gateway.settings,'origin':'https://new.example.ts.net:8443'})
                connection.sendall(body[1:]);response=connection.makefile('rb')
                self.assertIn(b'403',response.readline());response.close()
                self.assertEqual(self.http.live.record['plan_revisions'],snap['plan_revision'])
            finally:connection.close()

    def test_slow_stream_disconnects_without_stopping_collection(self):
        original=RemoteHandler.events
        def small_buffer(handler,member):
            handler.connection.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024)
            return original(handler,member)
        payload={'test_backpressure':'x'*2_000_000}
        with patch.object(RemoteHandler,'events',small_buffer),patch.object(self.http.live,'snapshot',return_value=payload):
            connection=socket.socket();connection.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
            connection.settimeout(4);connection.connect(('127.0.0.1',self.gateway.http.server_port))
            try:
                connection.sendall((f'GET /api/live/events HTTP/1.1\r\nHost: 127.0.0.1:{self.gateway.http.server_port}\r\n'
                                    f'Tailscale-User-Login: {ENGINEER}\r\n\r\n').encode())
                self.assertIn(b'200',connection.recv(64))
                sequence=self.http.live.sequence
                deadline=time.monotonic()+5
                while self.gateway.streams and time.monotonic()<deadline:time.sleep(.05)
                self.assertEqual(len(self.gateway.streams),0)
                self.assertGreater(self.http.live.sequence,sequence+10)
            finally:connection.close()

    def test_settings_write_failure_removes_new_listener(self):
        self.gateway.configure({**self.gateway.settings,'enabled':False})
        with patch('remote.write_json',side_effect=OSError('disk unavailable')):
            with self.assertRaises(PitError):self.gateway.configure({**self.gateway.settings,'enabled':True})
        self.assertIsNone(self.gateway.http);self.assertFalse(self.gateway.settings['enabled'])

    def test_disabled_default_persistence_and_no_control_restore(self):
        self.http.live.pit.authorize(True,ENGINEER)
        self.gateway.configure({**self.gateway.settings,'enabled':False})
        self.assertFalse(self.http.live.pit.enabled);self.assertIsNone(self.gateway.http)
        restored=RemoteGateway(self.gateway.data,server.ROOT,self.http.live,port=0)
        self.assertFalse(restored.settings['enabled']);self.assertEqual(restored.engineer(),ENGINEER)
        with tempfile.TemporaryDirectory() as directory:
            clean=RemoteGateway(directory,server.ROOT,self.http.live,port=0)
            self.assertFalse(clean.settings['enabled']);self.assertEqual(clean.settings['users'],[])
        for bad in [{'enabled':True,'origin':'https://public.example','users':[]},
                    {'enabled':True,'origin':ORIGIN,'users':[{'login':ENGINEER,'role':'engineer'},{'login':'second@example.com','role':'engineer'}]},
                    {'enabled':True,'origin':ORIGIN,'users':[{'login':ENGINEER,'role':'owner'}]}]:
            with self.assertRaises(PitError):validate_settings(bad)


class CollectorDisconnectTests(unittest.TestCase):
    def test_read_exception_cancels_queued_batch_before_reconnect(self):
        sdk=FakeSDK();entered=threading.Event();release=threading.Event();reads=0
        def read():
            nonlocal reads
            reads+=1
            if reads==2:
                entered.set();release.wait(3)
                raise OSError('Disconnected during read')
            return sdk.frame(reads*.05)
        sdk.read=read
        with tempfile.TemporaryDirectory() as directory:
            live=LiveService(Path(directory),sdk);live.start()
            try:
                self.assertTrue(entered.wait(2));live.pit.authorize(True,ENGINEER)
                live.pit.submit(command_body(live),ENGINEER);release.set()
                deadline=time.monotonic()+2
                while live.sequence<3 and time.monotonic()<deadline:time.sleep(.02)
                self.assertGreaterEqual(live.sequence,3)
                self.assertEqual(live.pit.status()['commands'][-1]['status'],'failed')
                self.assertEqual(sdk.calls,[])
            finally:release.set();live.close()


if __name__=='__main__':unittest.main()
