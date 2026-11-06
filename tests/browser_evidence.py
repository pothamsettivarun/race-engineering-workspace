"""New UI acceptance flow with temporary recordings and an instrumented fake model."""
import sys,tempfile,threading,math,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import server
from test_workspace import make_log

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);server.DATA=root/'data';server.JOBS=None;server.AI_PROVIDER='chatgpt'
    class FakeCodex:
        def status(self,force=False):return {'available':True,'connected':True,'auth_type':'chatgpt','plan_type':'plus','login_pending':False}
        def close(self):pass
        def answer(self,instructions,prompt,model,**kwargs):
            if 'Cancel test' in prompt:
                while not kwargs['cancel'].is_set():time.sleep(.01)
                raise ValueError('Investigation cancelled.')
            context=json.loads(prompt.split('Measured run context:\n')[1].split('\nDriver question:\n')[0])
            rid=context['full_resolution_access']['runs'][0]['run_id']
            kwargs['tool_handler']('list_evidence',{'run_id':rid})
            kwargs['tool_handler']('read_samples',{'run_id':rid,'lap_id':0,'corner':'T13','channels':['Speed','Brake','Throttle','SteeringWheelAngle']})
            return {'text':'## T13 evidence\n\n**S1:** original samples support this observation.\n\n- One controlled experiment.','model':'browser-test'}
    server.CODEX=FakeCodex()
    log=root/'test.ibt';make_log(log)
    http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page(viewport={'width':1500,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{http.server_port}');page.click('#import-button');page.set_input_files('#upload-file',str(log));page.wait_for_selector('#workspace:visible')
            rid=page.locator('.run-button.active').get_attribute('data-run');run=server.load_run(rid)
            profile={'track_id':'1','configuration':'','validated':True,'source':{'title':'Numbered synthetic test map','url':'https://example.com/map'},'corners':[{'label':f'T{i}','start':i*6-2,'reference':i*6,'end':i*6+2} for i in range(1,15)],'geometry':[{'distance':i,'lat':39.8+.002*math.sin(i/100*2*math.pi),'lon':-86.2+.002*math.cos(i/100*2*math.pi)} for i in range(100)]}
            server.save_profile(server.DATA,run,profile,server.atomic_json);page.reload();page.wait_for_selector('[data-map-corner="T13"]')
            assert page.evaluate('document.documentElement.scrollHeight <= innerHeight');assert '361 samples' in page.locator('#recording-status').inner_text()
            page.locator('[data-map-corner="T13"]').click();assert '76.0–80.0%' in page.locator('#zoom-label').inner_text()
            page.locator('#charts svg').first.hover();assert page.locator('#map-position').get_attribute('visibility')=='visible'
            page.click('[data-tab=engineer]');page.select_option('#question-scope','corners');page.select_option('#question-corners',['T13']);page.fill('#question','Inspect T13');page.click('#ask-button')
            page.wait_for_selector('.answer-text h3');assert page.locator('.evidence-coverage').count()==1
            page.locator('.evidence-coverage > summary').click();assert 'original-sample pages' in page.locator('.evidence-coverage').inner_text()
            page.locator('.evidence-link').first.click();assert page.locator('#lap-select').input_value()=='0'
            page.click('#edit-map-button');page.select_option('#edit-corner','T13');old=page.locator('[data-corner-field=reference]').input_value();handle=page.locator('[data-map-handle=reference]').bounding_box();page.mouse.move(handle['x']+handle['width']/2,handle['y']+handle['height']/2);page.mouse.down();page.mouse.move(handle['x']+handle['width']/2+25,handle['y']+handle['height']/2,steps=8);page.mouse.up();assert page.locator('[data-corner-field=reference]').input_value()!=old;page.fill('[data-corner-field=reference]',str(float(old)+.1));page.locator('[data-corner-field=reference]').press('Tab');page.click('#save-corner-map');page.wait_for_function("() => document.getElementById('notice').textContent.includes('revision saved')")
            assert server.load_profile(server.DATA,run)['corners'][12]['reference']==float(old)+.1
            page.locator('#map-dialog .close-dialog').click();page.click('[data-tab=engineer]');page.select_option('#question-scope','run');page.fill('#question','Cancel test');page.click('#ask-button');page.wait_for_selector('#cancel-investigation:visible');page.click('#cancel-investigation');page.wait_for_function("() => document.getElementById('notice').textContent.includes('cancelled')")
            page.click('[data-tab=telemetry]');page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors,errors
            Path('test-results').mkdir(exist_ok=True);page.screenshot(path='test-results/evidence-mobile.png',full_page=True)
            browser.close();print('Passed: retained import, T13 map/zoom/hover, scoped background AI, saved evidence links, map revision edits, cancellation, mobile layout; no JS errors.')
    finally:http.shutdown();http.server_close();server.CODEX=None
