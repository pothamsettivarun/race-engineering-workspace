"""Fuel UI acceptance using synthetic telemetry, isolated from the racing PC's SDK."""
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright, expect
import server
from live_fuel import totals_from
from test_live import sample
from test_live_fuel import lap


class FuelSource:
    error = ''
    def __init__(self): self.tick = 0; self.paused = False; self.replay = False
    def read(self):
        if self.paused: return None
        self.tick += 1
        frame = sample(625+self.tick*.05, tick=self.tick, lap=10, pct=.25, fuel=51.8, race=False, replay=self.replay)
        frame['metadata']['DriverInfo'].update(DriverCarFuelMaxLtr=83.3, DriverCarMaxFuelPct=1)
        return frame
    def close(self): pass


def main():
    class FakeCodex:
        def status(self, **kwargs): return {'available': False, 'connected': False, 'login_pending': False}
        def close(self): pass
    with tempfile.TemporaryDirectory() as directory, patch.object(server, 'DATA', Path(directory)/'data'), patch.object(server, 'CODEX', FakeCodex()):
        source = FuelSource()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler, live_source=source, remote_port=0)
        http.live.start(); threading.Thread(target=http.serve_forever, daemon=True).start()
        output = Path('test-results'); output.mkdir(exist_ok=True)
        def observed(laps):
            with http.live.lock:
                http.live.laps.clear(); http.live.laps.extend(laps)
                http.live.fuel_totals = totals_from(laps); http.live.strategy = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1920, 'height': 1080}); errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script('window.EventSource=class extends EventSource{constructor(...args){super(...args);window.testFuelStream=this;}}')
                page.goto(f'http://127.0.0.1:{http.server_port}')
                expect(page.locator('#live-connection')).to_have_text('Live connected')
                observed([lap(10)])
                last = page.locator('[data-fuel-basis="last_lap"]')
                session = page.locator('[data-fuel-basis="session"]')
                rolling = page.locator('[data-fuel-basis="rolling"]')
                expect(last.locator('td').nth(0)).to_have_text('1.150')
                expect(last.locator('[data-fuel-empty]')).to_have_text('45.04')
                expect(last.locator('[data-fuel-pits]')).to_have_text('—')
                expect(rolling.locator('td').nth(0)).to_have_text('—')
                expect(page.locator('#live-fuel')).to_contain_text('44.04 laps')
                expect(page.locator('#live-fuel')).to_contain_text('target for practice/testing')
                page.locator('#live-open-strategy').click()
                expect(page.locator('[data-plan-field="fuel_basis"]')).to_have_value('last_lap')
                page.locator('[data-plan-field="race_laps"]').fill('200')
                page.locator('#live-save-plan').click()
                expect(page.locator('#live-plan-status')).to_have_text('Saved locally')
                page.locator('#live-view-pitwall').click()
                expect(last.locator('[data-fuel-pits]')).to_contain_text('2.04')
                expect(last.locator('[data-fuel-pits]')).to_contain_text('3+ stops')
                expect(page.locator('#live-fuel')).to_contain_text('219.36 L')
                page.locator('.live-fuel-help summary').click()
                sequence = page.evaluate('LivePitwall.getSnapshot().sequence')
                page.wait_for_function('sequence=>LivePitwall.getSnapshot().sequence>sequence+8', arg=sequence)
                assert page.locator('.live-fuel-help').evaluate('(e)=>e.open')
                page.locator('.live-fuel-help summary').click()
                observed([lap(8, 3, 80), lap(9, 1, 60), lap(10)])
                expect(session.locator('td').nth(0)).to_have_text('1.717')
                expect(rolling.locator('td').nth(0)).to_have_text('1.150')
                page.locator('#live-open-strategy').click()
                page.locator('[data-plan-field="fuel_basis"]').select_option('session')
                page.locator('#live-save-plan').click()
                expect(page.locator('#live-plan-status')).to_have_text('Saved locally')
                page.locator('#live-view-pitwall').click()
                expect(session).to_have_class('fuel-selected')
                expect(page.locator('#live-fuel')).to_contain_text('29.17 laps')
                for width, height in [(1920, 1080), (1366, 768), (390, 844)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    page.locator('#live-fuel').scroll_into_view_if_needed()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert page.locator('.live-fuel-table').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
                    page.screenshot(path=str(output/f'fuel-pitwall-{width}.png'))
                    page.locator('#live-fuel').screenshot(path=str(output/f'fuel-stats-{width}.png'))
                page.set_viewport_size({'width': 1366, 'height': 768})
                page.locator('#live-open-strategy').click()
                page.locator('[data-plan-field="burn_litres"]').fill('2')
                page.locator('#live-save-plan').click()
                expect(page.locator('#live-plan-status')).to_have_text('Saved locally')
                page.locator('#live-view-pitwall').click()
                expect(page.locator('[data-fuel-basis="manual"]')).to_have_class('fuel-selected')
                expect(page.locator('[data-fuel-basis="manual"] [data-fuel-empty]')).to_have_text('25.90')
                source.replay = True
                expect(page.locator('#live-connection')).to_contain_text('Replay')
                for row in [last, session, rolling, page.locator('[data-fuel-basis="manual"]')]:
                    expect(row.locator('[data-fuel-empty]')).to_have_text('—')
                    expect(row.locator('[data-fuel-pits]')).to_have_text('—')
                expect(last.locator('td').nth(0)).to_have_text('1.150')
                source.replay = False
                expect(session.locator('[data-fuel-empty]')).to_have_text('30.17')
                page.evaluate('window.testFuelStream.close()')
                expect(page.locator('#live-connection')).to_have_text('Data stale', timeout=6000)
                expect(page.locator('#live-fuel')).to_contain_text('browser reconnects')
                expect(last.locator('[data-fuel-empty]')).to_have_text('—')
                page.reload()
                expect(page.locator('#live-connection')).to_have_text('Live connected', timeout=10000)
                source.paused = True
                expect(page.locator('#live-connection')).to_have_text('Data stale', timeout=6000)
                expect(last.locator('[data-fuel-empty]')).to_have_text('—')
                source.paused = False
                expect(page.locator('#live-connection')).to_have_text('Live connected', timeout=6000)
                observed([lap(8, 3, 80), lap(9, 1, 60), lap(10), lap(11, 5, eligible=False, reasons=['pit lane'])])
                expect(last.locator('td').nth(0)).to_have_text('—')
                expect(last).to_contain_text('Lap 11 excluded')
                expect(session.locator('td').nth(0)).to_have_text('1.717')
                assert not errors, errors
                browser.close()
                print('Fuel UI passed: one-lap testing range, per-basis finish estimates, saved basis, manual overrides, exclusions, replay, browser disconnect, stale/reconnect, 1920/1366/mobile.')
        finally: http.shutdown(); http.server_close()


if __name__ == '__main__': main()
