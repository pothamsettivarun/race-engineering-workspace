"""Boot layout browser acceptance using copied real recordings and isolated data."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from analysis import eligible
import server


def main():
    original = server.DATA
    runs = [server.load_run(json.loads(p.read_text(encoding='utf-8'))['id'])
            for p in (original / 'index').glob('*.json')]
    runs = [r for r in runs if str(r['metadata'].get('track_id')) == '434'
            and r['metadata'].get('configuration') == 'Boot' and eligible(r)]
    pair = next(([a['id'], b['id']] for i, a in enumerate(runs) for b in runs[i + 1:]
                 if a['metadata']['car_id'] == b['metadata']['car_id']), None)
    assert pair, 'Import two Watkins Glen Boot recordings in the same car with clean laps first.'
    with tempfile.TemporaryDirectory() as tmp:
        server.DATA = Path(tmp) / 'data'
        for name in ('runs', 'index', 'recordings'):
            (server.DATA / name).mkdir(parents=True)
        for id in pair:
            for folder, suffix in [('runs', '.json.gz'), ('index', '.json')]:
                shutil.copy2(original / folder / (id + suffix), server.DATA / folder / (id + suffix))
            run = server.load_run(id)
            name = run['recording']['sha256'] + '.ibt'
            shutil.copy2(original / 'recordings' / name, server.DATA / 'recordings' / name)
        class FakeCodex:
            def status(self, force=False):
                return {'available': True, 'connected': False, 'login_pending': False}
        server.CODEX = FakeCodex()
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.route('**/api/track-imagery', lambda route: route.fulfill(json={
                    'status': 'unavailable', 'message': 'Offline acceptance check'}))
                page.goto(f'http://127.0.0.1:{http.server_port}')
                page.click('[data-tab=lap-comparison]')
                for side, id in zip(('selected', 'reference'), pair):
                    page.select_option(f'#lc-run-{side}', id)
                page.wait_for_selector('#lc-results:visible')
                assert 'confirmed corner profile' in page.locator('#lc-provenance').inner_text()
                assert page.locator('#lc-map [data-map-corner]').count() == 12
                assert page.locator('#lc-charts svg').count() == 5
                page.locator('#lc-map-zones button', has_text='Bus Stop').click()
                assert '32.70–39.30%' in page.locator('#lc-range').inner_text()
                assert page.locator('#lc-line-detail .lc-line-path').count() == 2
                assert 'Bus Stop' in page.locator('#lc-detail').inner_text()
                page.locator('#lc-map').screenshot(path=str(server.ROOT / 'test-results' / 'watkins-comparison-map.png'))
                page.locator('#lc-line-detail').screenshot(path=str(server.ROOT / 'test-results' / 'watkins-comparison-line.png'))
                page.click('#lc-edit-toggle')
                assert page.locator('#lc-corner-inputs [data-field=label]').count() == 12
                page.click('#lc-save')
                page.wait_for_function("() => document.querySelector('#lc-edit-status').textContent.includes('Saved for')")
                page.click('#lc-reset')
                page.wait_for_function("() => document.querySelector('#lc-provenance').textContent.includes('confirmed corner profile')")
                page.screenshot(path=str(server.ROOT / 'test-results' / 'watkins-comparison-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(server.ROOT / 'test-results' / 'watkins-comparison-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1500, 'height': 1000})
                page.click('[data-tab=telemetry]')
                page.wait_for_selector('#track-map-svg')
                assert page.locator('#track-map [data-map-corner]').count() == 11
                assert 'Watkins Glen' in page.locator('#project-title').inner_text()
                page.click('#edit-map-button')
                assert page.locator('#reset-corner-map').inner_text() == 'Reset to built-in track map'
                page.screenshot(path=str(server.ROOT / 'test-results' / 'watkins-telemetry-desktop.png'), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            http.shutdown()
            http.server_close()
            server.DATA = original
    print('Watkins browser acceptance passed: two recordings, 12 sections, Bus Stop zoom, GPS paths, save/reset, mobile layout and numbered Telemetry map.')


if __name__ == '__main__':
    main()
