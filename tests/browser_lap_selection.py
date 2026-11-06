"""Lap picker colors, eligibility, keyboard navigation, and overlay ordering."""
from pathlib import Path
import sys, threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import server
class LocalStatus:
 def status(self, force=False): return {'available':True,'connected':False,'login_pending':False}
 def close(self): pass
server.CODEX=LocalStatus()
http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
threading.Thread(target=http.serve_forever,daemon=True).start()
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='msedge',headless=True)
  page=browser.new_page(viewport={'width':1366,'height':768}); errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(f'http://127.0.0.1:{http.server_port}');page.wait_for_selector('#workspace:visible')
  page.click('[data-tab=telemetry]')
  laps=page.evaluate('() => ({selected:state.lap,reference:state.reference,fastest:usable(state.run).sort((a,b)=>a.duration-b.duration)[0].id})')
  def color(selector): return page.locator(selector).first.evaluate('(el)=>getComputedStyle(el).backgroundColor')
  assert color('#browser-laps .lap-number-selected')=='rgb(0, 128, 255)'
  assert color('#browser-laps .lap-number-reference')=='rgb(255, 255, 255)'
  assert page.locator(f'[data-browser-lap="{laps["fastest"]}"] .lap-time-fastest').count()==1
  assert page.locator('#browser-laps .lap-time-fastest').first.evaluate('(el)=>getComputedStyle(el).backgroundColor')=='rgb(255, 128, 0)'
  for chart in page.locator('#charts .chart').all()[:4]:
   strokes=chart.locator('.trace').evaluate_all('(els)=>els.map(el=>el.getAttribute("stroke"))')
   assert strokes[0]=='#ffffff' and strokes[-1]!='#ffffff',strokes
  page.click('#lap-select-picker')
  assert page.locator('#lap-select-options .lap-time-fastest').count()>=1
  assert color('#lap-select-options .lap-number-selected')=='rgb(0, 128, 255)'
  assert color('#lap-select-options .lap-number-reference')=='rgb(255, 255, 255)'
  page.screenshot(path='test-results/lap-picker-highlights.png')
  page.keyboard.press('Home');page.keyboard.press('ArrowDown');page.keyboard.press('Enter')
  assert page.locator('#lap-select').input_value()==page.evaluate('String(state.run.laps[1].id)')
  assert not page.locator('.lap-picker-popup').count()
  page.click('#reference-select-picker');page.keyboard.press('Home');page.keyboard.press('Enter')
  assert page.locator('#reference-select').input_value()==''
  assert page.locator('#browser-laps .lap-number-reference').count()==0
  page.click('#reference-select-picker');page.locator('#reference-select-options [role=option]').nth(4).click()
  assert page.locator('#reference-select').input_value()==page.evaluate('String(state.run.laps[3].id)')
  page.click('#lap-select-picker');page.keyboard.press('Escape');assert not page.locator('.lap-picker-popup').count()
  page.click('#reference-select-picker');page.click('[data-tab=stint]');assert not page.locator('.lap-picker-popup').count()
  assert color('#lap-table .lap-number-selected')=='rgb(0, 128, 255)'
  assert color('#lap-table .lap-number-reference')=='rgb(255, 255, 255)'
  # Change only in-memory eligibility to test exclusion and ties, without saving user notes.
  result=page.evaluate('''() => {
   const ordered=usable(state.run).filter(l=>Number.isFinite(l.duration)).sort((a,b)=>a.duration-b.duration);
   state.run.notes.excluded_laps.push(ordered[0].id);renderTelemetry();
   return {excluded:ordered[0].id,next:ordered[1].id};
  }''')
  assert page.locator(f'[data-browser-lap="{result["excluded"]}"] .lap-time-fastest').count()==0
  assert page.locator(f'[data-browser-lap="{result["next"]}"] .lap-time-fastest').count()==1
  page.click('#lap-select-picker');assert page.locator('.lap-picker-popup').inner_text().count('excluded')>=1
  page.keyboard.press('Escape')
  page.set_viewport_size({'width':390,'height':844});page.click('#lap-select-picker')
  box=page.locator('.lap-picker-popup').bounding_box();assert box['x']>=0 and box['x']+box['width']<=390
  page.keyboard.press('End');page.keyboard.press('Enter');assert page.locator('#lap-select').input_value()==page.evaluate('String(state.run.laps.at(-1).id)')
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  assert not errors,errors
  browser.close();print('Passed: selected trace ordering, valid fastest times, lap number colors, both dropdowns, keyboard/outside dismissal, eligibility updates and mobile bounds.')
finally:
 http.shutdown();http.server_close();server.CODEX=None
