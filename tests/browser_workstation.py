"""Read-only workstation layout acceptance against saved local recordings."""
from pathlib import Path
import sys,threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import server
class LocalStatus:
 def status(self,force=False):return {'available':True,'connected':False,'login_pending':False}
 def close(self):pass
server.CODEX=LocalStatus()
http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
threading.Thread(target=http.serve_forever,daemon=True).start()
output=Path('test-results');output.mkdir(exist_ok=True)
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='msedge',headless=True)
  page=browser.new_page(viewport={'width':1920,'height':1080});errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(f'http://127.0.0.1:{http.server_port}');page.wait_for_selector('#workspace:visible')
  for width,height in [(1920,1080),(1366,768)]:
   page.set_viewport_size({'width':width,'height':height});page.click('[data-tab=telemetry]');page.wait_for_selector('#charts .stat-readout[data-channel=Speed]');page.wait_for_timeout(200)
   assert page.locator('#charts .chart').count()==5
   assert page.locator('#charts .stat-readout[data-channel=Throttle]').count()==2
   assert page.locator('#charts .stat-readout[data-channel=Brake]').count()==2
   assert page.locator('#charts .plot-statistics').first.inner_text().count('▼')==2
   assert page.evaluate("getComputedStyle(document.getElementById('toolbar')).backgroundColor === 'rgb(0, 0, 0)'")
   assert page.evaluate("getComputedStyle(document.querySelector('svg text')).fontFamily.includes('Tahoma')")
   assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
   for chart in page.locator('#charts .chart').all():
    box=chart.bounding_box();assert box['y']+box['height']<=height-24
   stats_text=page.locator('#charts .plot-statistics').first.inner_text();page.locator('#charts svg').first.hover();assert page.locator('#charts .plot-statistics').first.inner_text()==stats_text;assert page.locator('#charts .crosshair[visibility=visible]').count()==page.locator('#charts svg').count()
   page.screenshot(path=str(output/f'workstation-{width}.png'))
  # Deliberately let a superseded range finish after the latest selection.
  page.evaluate("""() => {window.originalStatsAPI=api;api=async function(path,options){const result=await window.originalStatsAPI(path,options);if(path==='/api/plot-stats'&&JSON.parse(options.body).distance_range?.[0]===12)await new Promise(resolve=>setTimeout(resolve,500));return result;};zoomZone({start:12,end:20});zoomZone({start:60,end:70});}""")
  page.wait_for_selector('#charts .stat-readout[data-channel=Speed]');page.wait_for_timeout(650)
  expected=page.evaluate("""async () => {const response=await window.originalStatsAPI('/api/plot-stats',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:state.run.id,lap_ids:[state.lap],channels:['Speed'],distance_range:[60,70]})});return response.rows[0];}""")
  first=page.locator('#charts .stat-readout[data-channel=Speed]').first
  assert first.get_attribute('data-valid-count')==str(expected['valid_sample_count'])
  assert first.locator('.stat-mean').inner_text().split()[-1]==f"{expected['mean']:.1f}"
  page.evaluate('() => {api=window.originalStatsAPI;}');page.click('#reset-zoom');page.wait_for_selector('#charts .stat-readout[data-channel=Speed]')
  old=page.locator('#data-browser').bounding_box()['width'];page.locator('#browser-splitter').focus();page.keyboard.press('ArrowRight');page.wait_for_timeout(150);assert page.locator('#data-browser').bounding_box()['width']>old
  split=page.locator('#map-splitter').bounding_box();page.mouse.move(split['x']+2,split['y']+30);page.mouse.down();page.mouse.move(split['x']-25,split['y']+30,steps=5);page.mouse.up();page.wait_for_timeout(150)
  saved_browser=page.locator('#data-browser').bounding_box()['width'];saved_map=page.locator('#map-dock').bounding_box()['width']
  page.click('#toggle-map');assert not page.locator('#map-dock').is_visible();page.click('#toggle-map')
  page.click('[data-browser-tab=channels]');assert page.locator('.channel-row').count()==9
  page.locator('[data-display-channel=Brake]').click();assert page.locator('#charts .chart-heading').nth(2).inner_text().startswith('Brake');page.locator('[data-display-channel=Throttle]').click();assert page.locator('#charts .chart-heading').nth(1).inner_text().startswith('Throttle');page.locator('[data-display-channel=FuelLevel]').click();assert page.locator('#channel-select').input_value()=='FuelLevel'
  page.click('[data-tab=engineer]');page.reload();page.wait_for_selector('#workspace:visible');page.wait_for_timeout(150)
  assert page.locator('#tab-engineer').is_visible();assert abs(page.locator('#data-browser').bounding_box()['width']-saved_browser)<1
  page.screenshot(path=str(output/'workstation-engineer.png'))
  page.click('[data-tab=telemetry]');assert abs(page.locator('#map-dock').bounding_box()['width']-saved_map)<1
  for tab in ['stint','conditions','compare','setup','library']:
   page.click(f'[data-tab={tab}]');page.screenshot(path=str(output/f'workstation-{tab}.png'))
  for button,dialog in [('info-button','info-dialog'),('import-button','import-dialog'),('connect-button','ai-dialog')]:
   page.click('#'+button);assert page.locator('#'+dialog).is_visible();page.screenshot(path=str(output/f'workstation-{dialog}.png'));page.locator('#'+dialog+' .close-dialog').click()
  page.click('[data-tab=telemetry]');page.click('#edit-map-button');assert page.locator('#map-dialog').is_visible();assert page.locator('#map-editor').is_visible();page.screenshot(path=str(output/'workstation-map-editor.png'));page.locator('#map-dialog .close-dialog').click()
  page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
  assert not page.locator('#data-browser').is_visible();assert not page.locator('#map-dock').is_visible()
  page.click('#toggle-browser');assert page.locator('#data-browser').is_visible();page.click('#toggle-browser')
  page.click('#toggle-map');assert page.locator('#map-dock').is_visible();page.click('#toggle-map')
  for tab in ['telemetry','stint','conditions','compare','setup','engineer','library']:
   page.click(f'[data-tab={tab}]');assert page.evaluate('document.documentElement.scrollWidth <= innerWidth');page.screenshot(path=str(output/f'workstation-mobile-{tab}.png'))
  page.set_viewport_size({'width':1366,'height':768});page.click('[data-tab=telemetry]');page.wait_for_timeout(200);assert page.locator('#data-browser').is_visible();assert page.locator('#map-dock').is_visible()
  assert not errors,errors
  browser.close();print('Passed: fitted five-plot desktop layouts, linked cursors, splitters/keyboard/persistence, channel browser, all worksheets/dialogs, responsive toggles and recovery; no JS errors.')
finally:
 http.shutdown();http.server_close();server.CODEX=None
