"""Optional end-to-end browser check, using temporary runs rather than user data."""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import server
from test_workspace import make_log


def main():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        server.DATA=root/'data'
        server.API_KEY=''
        class FakeCodex:
            def status(self,force=False):return {'available':True,'connected':False,'login_pending':False}
            def close(self):pass
        server.CODEX=FakeCodex()
        server.AI_PROVIDER='chatgpt'
        baseline=root/'baseline.ibt'
        experiment=root/'experiment.ibt'
        make_log(baseline)
        make_log(experiment,track_temp=36,arb=3)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{http.server_port}'
        screenshots=Path(__file__).resolve().parents[1]/'test-results'
        screenshots.mkdir(exist_ok=True)
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                page=browser.new_page(viewport={'width':1500,'height':1050})
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base)
                page.wait_for_selector('#tab-live:visible')
                page.click('#import-button')
                page.set_input_files('#upload-file',str(baseline))
                page.wait_for_selector('#workspace:visible')
                page.wait_for_selector('#charts svg')
                assert page.locator('#charts svg').count()==5
                first_id=page.locator('.run-button.active').get_attribute('data-run')
                page.locator('.run-entry.active').screenshot(path=str(screenshots/'session-row-border.png'))
                page.click('[data-tab=engineer]')
                page.fill('#setup-label','Baseline A')
                page.fill('#vision','Endurance confidence through a full fuel stint')
                page.fill('#feedback','Sustained midcorner understeer in fast corners')
                page.click('#notes-form button[type=submit]')
                page.wait_for_function("() => document.querySelector('#save-status').textContent==='Saved locally'")
                assert 'Driver reports understeer' in page.locator('#findings').inner_text()
                page.click('[data-tab=telemetry]')
                page.locator('#charts svg').first.hover()
                assert ':' in page.locator('#charts .readout').first.inner_text()
                page.locator('#zones button').first.click()
                assert page.locator('#zoom-label').inner_text()!='0.0–100.0% lap distance'
                page.click('#reset-zoom')
                page.click('[data-browser-tab=channels]')
                page.select_option('#channel-select','rideHeight')
                assert 'not available' in page.locator('#charts').inner_text()
                page.select_option('#channel-select','SteeringWheelAngle')
                page.click('[data-tab=conditions]')
                assert '30.0 °C' in page.locator('#condition-cards').inner_text()
                page.fill('#manual-conditions','New tires; no traffic')
                page.click('#save-conditions')
                page.wait_for_function("() => document.querySelector('#notice').textContent==='Condition notes saved.'")
                page.click('[data-tab=library]')
                page.fill('#library-title','Front ARB <script>test</script>')
                page.fill('#library-source','Private test manual')
                page.fill('#library-text','Front anti-roll bar stiffness: verify adjustment direction in the garage.')
                page.click('#library-form button[type=submit]')
                page.wait_for_selector('.library-entry')
                assert page.locator('.library-entry strong').inner_text()=='Front ARB <script>test</script>'
                assert page.locator('.library-entry script').count()==0
                page.click('#import-button')
                page.set_input_files('#upload-file',str(experiment))
                page.wait_for_function("() => document.querySelector('#run-file').textContent==='experiment.ibt'")
                page.click('[data-tab=compare]')
                page.select_option('#baseline-select',first_id)
                page.wait_for_selector('.comparison-result')
                assert 'Track temp differs' in page.locator('#comparison').inner_text()
                assert '4 → 3' in page.locator('#comparison').inner_text()
                page.select_option('#verdict','inconclusive')
                page.fill('#verdict-notes','Temperature changed; repeat in matched conditions')
                page.click('#save-verdict')
                page.wait_for_function("() => document.querySelector('#notice').textContent==='Experiment verdict saved.'")
                page.reload()
                page.wait_for_selector('#workspace:visible')
                assert page.locator('#verdict').input_value()=='inconclusive'
                page.click('[data-tab=stint]')
                page.locator('[data-use="0"]').uncheck()
                page.wait_for_function("() => document.querySelectorAll('#summary strong')[3].textContent==='2 / 3'")
                assert page.locator('#ask-button').is_disabled()
                page.screenshot(path=str(screenshots/'desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(screenshots/'mobile.png'),full_page=True)
                page.set_viewport_size({'width':1500,'height':1050})
                page.click('[data-tab=compare]')
                page.select_option('#baseline-select',first_id)
                page.wait_for_selector('.comparison-result')
                page.click(f'[data-remove-run="{first_id}"]')
                assert page.locator('#remove-run-name').inner_text()=='baseline.ibt'
                page.locator('#remove-run-dialog button').filter(has_text='Cancel').click()
                assert page.locator('#runs [data-run]').count()==2
                page.route('**/api/remove-run',lambda route:route.fulfill(status=404,content_type='application/json',body='{"error":"Not found."}'))
                page.click(f'[data-remove-run="{first_id}"]');page.click('#confirm-remove-run')
                page.wait_for_function("() => document.querySelector('#remove-run-error').textContent.includes('server needs to be restarted')")
                assert page.locator('#remove-run-dialog').is_visible()
                assert page.locator('#runs [data-run]').count()==2
                page.unroute('**/api/remove-run')
                page.click('#confirm-remove-run')
                page.wait_for_function('() => state.runs.length===1 && state.baseline===null')
                assert page.locator('#baseline-select').input_value()==''
                assert page.evaluate('state.run.filename')=='experiment.ibt'
                page.click('#import-button');page.set_input_files('#upload-file',str(baseline))
                page.wait_for_function("() => state.run.filename==='baseline.ibt' && state.runs.length===2")
                current=page.evaluate('state.run.id')
                page.click('[data-tab=stint]');page.click(f'[data-remove-run="{current}"]')
                page.click('#confirm-remove-run')
                page.wait_for_function("() => state.run?.filename==='experiment.ibt' && state.runs.length===1")
                assert page.locator('[data-tab=stint]').get_attribute('class')=='active'
                current=page.evaluate('state.run.id')
                page.click(f'[data-remove-run="{current}"]');page.click('#confirm-remove-run')
                page.wait_for_function('() => state.run===null && state.runs.length===0')
                assert page.locator('#browser-laps').inner_text()==''
                assert page.locator('#charts').inner_text()==''
                assert page.locator('#info-button').is_disabled()
                assert page.evaluate("localStorage.getItem('stint.lastRun')") is None
                page.reload();page.wait_for_function('() => state.runs.length===0 && state.run===null')
                assert page.locator('#runs [data-run]').count()==0
                assert page.locator('[data-tab=stint]').is_disabled()
                assert not errors,errors
                browser.close()
                print('Browser smoke passed: import, charts, notes, comparisons, mobile layout, removal confirmation/cancel, baseline cleanup, current-session fallback, final-session cleanup and persistence. No JavaScript errors.')
        finally:
            http.shutdown()
            http.server_close()


if __name__=='__main__':
    main()
