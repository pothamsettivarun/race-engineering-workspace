import copy
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from codex_bridge import CodexBridge, CodexError
from test_workspace import make_log


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data=Path(self.temp.name)/'data'
        self.patch=patch.object(server,'DATA',self.data)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_retrieval_preserves_page_provenance_and_skips_pending_sources(self):
        server.atomic_json(self.data/'library'/'manual.json',{'id':'manual','title':'Corvette manual',
                           'source':'https://example.com/manual.pdf','kind':'manual','status':'imported',
                           'text':'Front anti-roll bar adjustment direction.',
                           'sections':[{'text':'Front anti-roll bar adjustment direction.','locator':'Page 18','source':'https://example.com/manual.pdf#page=18'}]})
        server.atomic_json(self.data/'library'/'pending.json',{'id':'pending','title':'Antiroll video',
                           'text':'','status':'pending transcript'})
        source=server.retrieve('front ARB direction')[0]
        self.assertEqual(source['locator'],'Page 18')
        self.assertTrue(source['source'].endswith('#page=18'))
        self.assertEqual(len(server.retrieve('front ARB direction')),1)

    def test_indy_profile_applies_only_to_corvette_imports(self):
        profile={'title':'Indy 8h','car_match':['corvette','z06'],'objective':'endurance','vision':'Stable stint balance'}
        server.atomic_json(self.data/'project.json',profile)
        path=Path(self.temp.name)/'test.ibt'
        make_log(path)
        run=server.import_run(path)
        self.assertEqual(run['notes']['vision'],'')
        from analysis import analyze
        corvette=analyze(path)
        corvette['metadata']['car']='Chevrolet Corvette Z06 GT3.R'
        with patch.object(server,'analyze',return_value=corvette):
            run=server.import_run(path)
        self.assertEqual(run['notes']['vision'],'Stable stint balance')
        self.assertEqual(run['project_title'],'Indy 8h')

    def test_project_briefs_match_car_and_exact_track_configuration(self):
        profile={'title':'Indy 8h','car_match':['corvette','z06'],
                 'track':'Indianapolis Motor Speedway · road course',
                 'objective':'endurance','vision':'Indy endurance brief'}
        server.atomic_json(self.data/'project.json',profile)
        path=Path(self.temp.name)/'test.ibt'
        make_log(path)
        from analysis import analyze
        corvette=analyze(path)
        corvette['metadata'].update(car='Chevrolet Corvette Z06 GT3.R', track='Watkins Glen International',
                                    track_id=434, configuration='Boot')
        self.assertFalse(server.project_matches_run(profile,corvette))
        with patch.object(server,'analyze',return_value=corvette):
            run=server.import_run(path)
        self.assertIn('Watkins Glen',run['project_title'])
        self.assertIn('Bus Stop',run['notes']['vision'])
        self.assertEqual(server.project_profile(run)['track_id'],'434')
        for key,value in [('track_id',435),('configuration','Cup'),('car','Another car')]:
            other=copy.deepcopy(run);other['metadata'][key]=value
            self.assertIsNone(server.project_profile(other))
        indy=copy.deepcopy(run)
        indy['metadata'].update(track_id=448,track='Indianapolis Motor Speedway',configuration='Road Course')
        self.assertEqual(server.project_profile(indy)['title'],'Indy 8h')

    def test_retrieval_diversifies_relevant_sources_and_filters_video_search(self):
        manual={'id':'manual','title':'Corvette manual','kind':'manual','status':'imported','car':'Chevrolet Corvette Z06 GT3.R',
            'text':'Front roll stiffness understeer','sections':[{'text':'Corvette Z06 GT3.R front roll stiffness understeer '*5,'locator':f'Page {i}','source':'https://example.com/manual'} for i in range(1,7)]}
        video={'id':'video','title':'Setup video','kind':'video transcript','status':'imported','text':'Front roll stiffness understeer',
            'sections':[{'text':'Front roll stiffness understeer reasoning.','locator':'05:20–06:10','source':'https://youtube.com/watch?v=test&t=320'}]}
        irrelevant={'id':'other','title':'Unrelated video','kind':'video transcript','status':'imported','text':'Bananas oranges apples.'}
        for item in [manual,video,irrelevant]:server.atomic_json(self.data/'library'/(item['id']+'.json'),item)
        results=server.retrieve('Corvette Z06 GT3.R front roll stiffness understeer',count=3,car='Chevrolet Corvette Z06 GT3.R')
        self.assertIn('video',[s['id'] for s in results]);self.assertNotIn('other',[s['id'] for s in results])
        videos=server.retrieve('front roll stiffness',kind='video transcript')
        self.assertEqual([s['id'] for s in videos],['video']);self.assertEqual(videos[0]['locator'],'05:20–06:10')
        self.assertEqual(server.retrieve('Corvette Z06 GT3.R',car='Chevrolet Corvette Z06 GT3.R'),[])

    def test_chatgpt_route_never_calls_billed_api(self):
        path=Path(self.temp.name)/'test.ibt'
        make_log(path)
        run=server.import_run(path)
        class FakeCodex:
            def answer(self,instructions,prompt,model,**kwargs):return {'text':'Source-grounded experiment','model':'codex-test'}
        with patch.object(server,'AI_PROVIDER','chatgpt'),patch.object(server,'get_codex',return_value=FakeCodex()),patch.object(server.request,'urlopen') as api:
            result=server.ai_reply(run,'What should I test?')
            api.assert_not_called()
        self.assertEqual(result['provider'],'chatgpt')
        self.assertEqual(result['model'],'codex-test')


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.bridge=CodexBridge(self.temp.name)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def test_api_authenticated_codex_cannot_silently_use_api_billing(self):
        with patch.object(self.bridge,'status',return_value={'connected':False,'auth_type':'apiKey'}):
            with self.assertRaisesRegex(CodexError,'does not use'):
                self.bridge.answer('instructions','prompt')

    def test_unsafe_login_url_rejected(self):
        with patch.object(self.bridge,'request',return_value={'loginId':'123','authUrl':'https://attacker.example/login'}):
            with self.assertRaisesRegex(CodexError,'unexpected'):
                self.bridge.login()

    def test_ephemeral_read_only_turn_and_final_answer(self):
        calls=[]
        class FakeProcess:
            def poll(self):return None
        self.bridge.process=FakeProcess()
        def request(method,params,timeout=20):
            calls.append((method,params))
            if method=='thread/start':return {'thread':{'id':'thread-1'},'model':'test-model'}
            if method=='turn/start':
                watcher=self.bridge.watchers['thread-1']
                watcher.put({'method':'item/completed','params':{'threadId':'thread-1','item':{'id':'a','type':'agentMessage','phase':'commentary','text':'Thinking…'}}})
                watcher.put({'method':'item/completed','params':{'threadId':'thread-1','item':{'id':'b','type':'agentMessage','phase':'final_answer','text':'Repeat a matched baseline.'}}})
                watcher.put({'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}})
                return {'turn':{'id':'turn-1'}}
            return {}
        try:
            with patch.object(self.bridge,'status',return_value={'connected':True}),patch.object(self.bridge,'request',side_effect=request):
                answer=self.bridge.answer('Engineer instructions','Telemetry evidence')
            self.assertEqual(answer['text'],'Repeat a matched baseline.')
            start=next(params for method,params in calls if method=='thread/start')
            self.assertTrue(start['ephemeral'])
            self.assertEqual(start['permissions'],'stint')
            self.assertNotIn('sandbox',start)
            self.assertEqual(start['approvalPolicy'],'never')
            self.assertIn('No tools are permitted',start['developerInstructions'])
            turn=next(params for method,params in calls if method=='turn/start')
            self.assertEqual(turn['permissions'],'stint')
            self.assertNotIn('sandboxPolicy',turn)
        finally:
            self.bridge.process=None


if __name__=='__main__':unittest.main()
