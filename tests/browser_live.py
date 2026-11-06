"""Pitwall UI acceptance with a deterministic multiclass feed and no user files."""
import tempfile
import threading
import time
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
import server
from test_live import sample


class FakeSource:
    error=''
    def __init__(self):self.tick=0;self.paused=False;self.track=448;self.replay=False
    def read(self):
        if self.paused:return None
        self.tick+=1
        t=100+self.tick*.05
        frame=sample(t,tick=self.tick,lap=5,pct=.4+self.tick*.0001,fuel=30-self.tick*.001,replay=self.replay,track=self.track)
        frame['values']['CarIdxLapDistPct']=[.4+self.tick*.0001,.48,.1]
        frame['values']['CarIdxEstTime']=[24+self.tick*.006,29,6]
        return frame
    def close(self):pass


def main():
 with tempfile.TemporaryDirectory() as directory:
  with patch.object(server,'DATA',Path(directory)/'data'):
   class FakeCodex:
    def status(self,force=False):return {'available':True,'connected':False,'login_pending':False}
    def close(self):pass
   with patch.object(server,'CODEX',FakeCodex()):
    source=FakeSource();http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler,live_source=source)
    http.live.start();threading.Thread(target=http.serve_forever,daemon=True).start()
    output=Path('test-results');output.mkdir(exist_ok=True)
    try:
     with sync_playwright() as p:
      browser=p.chromium.launch(channel='msedge',headless=True)
      page=browser.new_page(viewport={'width':1920,'height':1080});errors=[]
      page.on('pageerror',lambda e:errors.append(str(e)))
      page.add_init_script("window.liveSequences=[];setInterval(()=>{if(window.LivePitwall?.getSnapshot())window.liveSequences.push(LivePitwall.getSnapshot().sequence)},20)")
      snapshot_requests=[0]
      def delayed_snapshot(route):
       snapshot_requests[0]+=1
       response=route.fetch()
       if snapshot_requests[0]==2:time.sleep(.4)
       route.fulfill(response=response)
      page.route('**/api/live/snapshot',delayed_snapshot)
      page.route('**/api/status',lambda route:route.fulfill(status=503,json={'error':'AI unavailable for live test'}))
      page.goto(f'http://127.0.0.1:{http.server_port}')
      page.wait_for_selector('#tab-live:visible');page.wait_for_selector('#live-timing-rows tr')
      assert page.locator('#live-connection').inner_text()=='Live connected'
      assert page.locator('#ai-status').inner_text()=='AI status unavailable'
      assert page.locator('#live-timing-rows tr').count()==3
      assert page.locator('.live-car-marker').count()==3
      assert page.locator('#live-sound').get_attribute('aria-pressed')=='false'
      page.wait_for_timeout(600)
      sequences=page.evaluate('window.liveSequences')
      assert sequences==sorted(sequences),'A delayed snapshot rewound the live state.'
      for width,height in [(1920,1080),(1366,768)]:
       page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(150)
       assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
       assert page.evaluate('document.documentElement.scrollHeight<=innerHeight')
       for canvas in page.locator('.live-chart-grid canvas').all():
        box=canvas.bounding_box();assert box['height']>60 and box['y']+box['height']<=height
       page.screenshot(path=str(output/f'live-pitwall-{width}.png'))
      page.locator('#live-timing-rows [data-car-idx="1"] button').click()
      assert 'Driver 1' in page.locator('#live-focus').inner_text()
      assert 'live-selected' in page.locator('.live-car-marker[data-car-idx="1"]').get_attribute('class')
      page.locator('.live-car-marker[data-car-idx="2"]').click()
      assert 'Driver 2' in page.locator('#live-focus').inner_text()
      page.select_option('#live-class','1');assert page.locator('#live-timing-rows tr:visible').count()==2
      page.select_option('#live-class','all')
      page.click('#live-sound');assert page.locator('#live-sound').get_attribute('aria-pressed')=='true';page.click('#live-sound')
      page.click('#live-view-strategy')
      for field,value in [('pace_seconds','60'),('burn_litres','2'),('pit_loss_seconds','20'),('refuel_lps','2'),('tire_seconds','10'),('driver_change_seconds','5')]:
       page.fill(f'[data-plan-field="{field}"]',value)
      page.wait_for_timeout(250)
      assert page.locator('[data-plan-field="pace_seconds"]').input_value()=='60'
      page.click('#live-add-current-driver');page.fill('[data-driver="0"][data-key="name"]','Current endurance driver')
      page.click('#live-save-plan');page.wait_for_function("() => document.querySelector('#live-plan-status').textContent==='Saved locally'")
      page.wait_for_selector('.live-scenario')
      assert 'Feasible' in page.locator('#live-scenarios').inner_text()
      page.locator('[data-copy]').first.click()
      page.fill('#live-scenario-name','My race plan')
      page.locator('[data-stop="0"][data-key="tires"]').check()
      page.click('#live-save-plan');page.wait_for_function("() => document.querySelector('#live-plan-status').textContent==='Saved locally'")
      page.wait_for_function("() => document.querySelector('#live-scenarios').textContent.includes('My race plan')")
      page.screenshot(path=str(output/'live-strategy.png'))
      page.click('[data-tab=library]');seq=page.evaluate('LivePitwall.getSnapshot().sequence');page.wait_for_timeout(250)
      assert page.evaluate('LivePitwall.getSnapshot().sequence')>seq
      page.click('[data-tab=live]');page.click('#live-view-pitwall')
      source.replay=True;page.wait_for_function("() => document.querySelector('#live-connection').textContent.includes('Replay')")
      assert not page.evaluate('LivePitwall.getSnapshot().strategy.scenarios.length')
      source.replay=False;source.paused=True
      page.wait_for_function("() => document.querySelector('#live-connection').textContent==='Data stale'",timeout=6000)
      assert 'suspended' in page.locator('#live-fuel').inner_text()
      source.paused=False;page.wait_for_function("() => document.querySelector('#live-connection').textContent==='Live connected'",timeout=6000)
      source.track=999;page.wait_for_selector('#live-map-import:visible')
      assert 'Lap progress' in page.locator('#live-map-source').inner_text()
      page.click('#live-view-history');page.wait_for_selector('#live-history-content h2')
      assert page.locator('#live-history-select option').count()>=2
      page.screenshot(path=str(output/'live-history.png'))
      page.click('#live-view-pitwall');page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
      assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
      page.screenshot(path=str(output/'live-mobile.png'))
      page.reload();page.wait_for_selector('#tab-live:visible');page.wait_for_selector('#live-timing-rows tr')
      assert not errors,errors
      browser.close()
      print('Live browser acceptance passed: no recordings, timing/map selection, classes, sounds, strategy editing, background updates, replay/stale/reconnect, unknown map, history, desktop/mobile, refresh.')
    finally:http.shutdown();http.server_close()


if __name__=='__main__':main()
