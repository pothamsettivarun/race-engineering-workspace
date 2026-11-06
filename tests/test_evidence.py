import copy,json,tempfile,time,threading,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import server
from analysis import analyze
from telemetry import IBT
from recordings import open_recording,validate_attachment
from corner_map import references,map_for,save_profile
from evidence import Investigation,measure,encode
from ai_jobs import Jobs
from test_workspace import make_log

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.patch=patch.object(server,'DATA',self.root/'data');self.patch.start()
        self.path=self.root/'test.ibt';make_log(self.path);self.run=server.import_run(self.path)
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def investigate(self):
        i=Investigation(server.DATA,self.run);i.resolve_scope('Inspect the run');i.calculate();return i
    def test_retention_original_value_precision_and_no_synthetic_finish(self):
        self.path.unlink();log=open_recording(server.DATA,self.run);a=log.read_samples(['SessionTime','Speed','LapDistPct'],0,120)
        i=self.investigate();page=i.call('read_samples',{'lap_id':0,'channels':['Speed']});rows=page['rows']
        self.assertEqual(len(rows),120);self.assertEqual(rows[-1][0],119);self.assertEqual(rows[-1][2],float(a['LapDistPct'][-1]));self.assertLess(rows[-1][2],1)
        self.assertEqual(rows[1][3],float(a['Speed'][1]));self.assertNotEqual(rows[1][3],round(float(a['Speed'][1]),5))
    def test_pagination_no_gaps_or_duplicate_samples(self):
        log=open_recording(server.DATA,self.run);names=[n for n,h in log.headers.items() if h['count']==1 and h['type']!=0]
        i=self.investigate();page=i.call('read_samples',{'lap_id':0,'channels':names[:16]});rows=[]
        while True:
            self.assertLessEqual(len(encode(page).encode()),16384);self.assertLessEqual(len(page['rows']),256);rows+=page['rows']
            if not page['next_cursor']:break
            page=i.call('read_samples',{'cursor':page['next_cursor']})
        self.assertEqual([r[0] for r in rows],list(range(120)))
    def test_scope_and_corner_reference_resolution(self):
        corners=[{'label':f'T{i}'} for i in range(1,15)]
        self.assertEqual(references('T2, T4, T8–T10, especially Turn 13',corners),['T2','T4','T8','T9','T10','T13'])
        with self.assertRaisesRegex(ValueError,'validated'):references('T13',[])
        i=self.investigate();i.maps[i.primary]={'status':'validated','profile':{'revision':'test','corners':[{'label':'T13','start':80,'reference':83,'end':85}]}}
        i.scope={'kind':'region','distance_range':[10,20]}
        with self.assertRaisesRegex(ValueError,'does not cover'):i.resolve_scope('Inspect T13')
    def test_exclusions_unknown_runs_channels_and_cursor(self):
        self.run['notes']['excluded_laps']=[0];i=self.investigate()
        self.assertIn('error',i.call('read_samples',{'lap_id':0,'channels':['Speed']}))
        self.assertIn('error',i.call('read_samples',{'run_id':'other','lap_id':1,'channels':['Speed']}))
        self.assertIn('error',i.call('read_samples',{'lap_id':1,'channels':['MissingChannel']}))
        self.assertIn('error',i.call('read_samples',{'cursor':'bad'}))
        self.assertEqual(i.manifest['sections'],[])
    def test_budget_records_only_delivered_evidence(self):
        i=self.investigate();i.bytes=131071
        result=i.call('read_samples',{'lap_id':0,'channels':['Speed']});self.assertIn('error',result);self.assertEqual(i.manifest['sections'],[])
        self.assertTrue(i.manifest['limits']);i.calls=24;self.assertTrue(i.call('list_evidence',{}).get('stop_requesting'))
    def test_matching_attachment_preserves_notes_and_history(self):
        old=copy.deepcopy(self.run);old.pop('recording');old['notes']['feedback']='T13 push';old['ai_history']=[{'answer':'saved'}]
        for l in old['laps']:l.pop('sample_start');l.pop('sample_stop')
        server.save_run(old);attached=server.attach_recording(old['id'],self.path)
        self.assertEqual(attached['notes']['feedback'],'T13 push');self.assertEqual(attached['ai_history'],[{'answer':'saved'}]);self.assertEqual(attached['laps'][0]['sample_start'],0)
        make_log(self.path,arb=7)
        # Setup evidence changes must be treated as a different recording.
        candidate=analyze(self.path);candidate['metadata']['car']='Different car'
        with self.assertRaises(ValueError):validate_attachment(old,candidate)
    def test_invalid_values_are_null_and_indexed(self):
        i=self.investigate();log=i.logs[self.run['id']];original=log.read_samples
        def invalid(names,start=0,stop=None):
            a=original(names,start,stop)
            if 'Speed' in a:a['Speed'][0]=np.nan
            return a
        with patch.object(log,'read_samples',side_effect=invalid),patch('evidence.open_recording',return_value=log):page=i.call('read_samples',{'lap_id':0,'channels':['Speed']})
        self.assertIsNone(page['rows'][0][-1]);self.assertEqual(page['invalid_values'][0]['sample_indices'],[0])
    def test_brief_and_sustained_events_preserve_native_event_values(self):
        t=np.arange(41)*.01;a={'SessionTime':t,'LapDistPct':t,'Speed':np.full(41,30.),'Brake':np.zeros(41),'Throttle':np.zeros(41),'SteeringWheelAngle':np.arange(41)*.01}
        a['Brake'][3:5]=.7;a['Brake'][10:25]=.6;a['Throttle'][28:]=.4
        class Log:
            headers={k:{} for k in a}
            def read_samples(self,names,start,stop):return {k:a[k].copy() for k in names}
        m=measure(Log(),{'id':0,'number':1,'sample_start':0,'sample_stop':41},{'label':'T1','start':0,'end':100})
        self.assertFalse(m['events']['braking'][0]['sustained']);self.assertTrue(m['events']['braking'][1]['sustained']);self.assertEqual(m['braking_onset']['sample'],10)
        self.assertEqual(m['throttle_pickup_after_brake_peak']['sample'],28)
    def test_profile_revision_and_saved_manifest_independence(self):
        profile={'track_id':'1','configuration':'','validated':True,'source':{'title':'Map','url':'https://example.com/map'},'corners':[{'label':'T1','start':10,'reference':15,'end':20}]}
        saved=save_profile(server.DATA,self.run,profile,server.atomic_json);i=self.investigate();snapshot=copy.deepcopy(i.manifest['profiles']);profile['corners'][0]['reference']=16
        changed=save_profile(server.DATA,self.run,profile,server.atomic_json)
        self.assertNotEqual(saved['revision'],changed['revision']);self.assertEqual(i.manifest['profiles'],snapshot)
        self.assertEqual(map_for(server.DATA,self.run)['status'],'validated')
    def test_invalid_gps_is_rejected_and_wrong_geometry_is_not_numbered(self):
        profile={'revision':'test','validated':True,'corners':[{'label':'T1','start':10,'reference':15,'end':20}],
                 'geometry':[{'distance':50,'lat':39.8,'lon':-86.2}]}
        class Log:
            headers={'Lat':{},'Lon':{}}
            def __init__(self,lat):self.lat=lat
            def read_samples(self,names,start,stop):
                count=stop-start
                a={'Lat':np.full(count,self.lat),'Lon':np.full(count,-86.2),'LapDistPct':np.arange(count)/count,'SessionTime':np.arange(count)*.1}
                return {k:a[k] for k in names}
        with patch('corner_map.load_profile',return_value=profile),patch('corner_map.open_recording',return_value=Log(0)):
            self.assertEqual(map_for(server.DATA,self.run)['status'],'invalid_position')
        with patch('corner_map.load_profile',return_value=profile),patch('corner_map.open_recording',return_value=Log(40.8)):
            self.assertEqual(map_for(server.DATA,self.run)['status'],'mismatch')

    def test_named_comparison_sections_survive_profile_revisions(self):
        profile={'track_id':'1','configuration':'','validated':True,
                 'source':{'title':'Map','url':'https://example.com/map'},
                 'corners':[{'label':'T1','start':10,'reference':15,'end':20}],
                 'comparison_sections':[{'label':'Bus Stop','start':30,'reference':35,'end':40}]}
        saved=save_profile(server.DATA,self.run,profile,server.atomic_json)
        self.assertEqual(saved['comparison_sections'],profile['comparison_sections'])
        for changes in [{'label':'T1'}, {'reference':float('nan')}, {'start':36}, {'end':101}]:
            invalid=copy.deepcopy(profile);invalid['comparison_sections'][0].update(changes)
            with self.assertRaises(ValueError):save_profile(server.DATA,self.run,invalid,server.atomic_json)
    def test_checksum_change_disables_evidence(self):
        i=self.investigate();path=server.DATA/'recordings'/(self.run['recording']['sha256']+'.ibt')
        with path.open('r+b') as f:f.seek(-1,2);f.write(b'X')
        result=i.call('read_samples',{'lap_id':0,'channels':['Speed']})
        self.assertIn('changed',result['error']);self.assertEqual(i.manifest['sections'],[])
    def test_configuration_identity_controls_profile_reuse(self):
        from corner_map import load_profile
        r=copy.deepcopy(self.run);r['metadata'].update(track_id=448,configuration='Road Course')
        self.assertEqual(len(load_profile(server.DATA,r)['corners']),14)
        r['metadata']['configuration']='Oval';self.assertIsNone(load_profile(server.DATA,r))
    def test_jobs_cancel_and_restart_preserve_evidence(self):
        jobs=Jobs(server.DATA,server.atomic_json);i=self.investigate()
        def work():
            while not i.cancel.is_set():time.sleep(.01)
            raise ValueError('cancelled')
        job=jobs.start(self.run['id'],'Question',i,work);jobs.cancel(job['id'])
        for _ in range(100):
            result=jobs.get(job['id'])
            if result['status']=='cancelled':break
            time.sleep(.01)
        self.assertEqual(result['status'],'cancelled');self.assertIn('calculated',result['evidence_manifest'])
        jobs.update(job['id'],status='running');restarted=Jobs(server.DATA,server.atomic_json)
        self.assertEqual(restarted.get(job['id'])['status'],'interrupted')
    def test_api_tool_loop_receives_original_samples_without_provider_fallback(self):
        captured=[]
        class Response:
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(self.value).encode()
        def call(req,**kwargs):
            payload=json.loads(req.data);captured.append(payload)
            if len(captured)==1:return Response({'output':[{'type':'function_call','call_id':'c1','name':'read_samples','arguments':json.dumps({'lap_id':0,'channels':['Speed']})}]})
            return Response({'output':[{'type':'message','content':[{'type':'output_text','text':'S1 supports this measured observation.'}]}]})
        with patch.object(server.request,'urlopen',side_effect=call):result=server.ai_reply(self.run,'Inspect braking',connection=('api','test','test-model','auto'))
        tool=json.loads(captured[1]['input'][-1]['output']);self.assertEqual(tool['rows'][0][0],0);self.assertEqual(len(result['evidence_manifest']['sections']),1)
        self.assertFalse(captured[1]['store']);self.assertEqual(result['provider'],'api')

if __name__=='__main__':unittest.main()
