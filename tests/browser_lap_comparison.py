"""Isolated browser coverage for independent imports, laps, segmentation and maps."""
from pathlib import Path
import base64
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from test_lap_comparison import recording, add_gps
import server


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server.DATA = root / 'data'
        class FakeCodex:
            def status(self, force=False):
                return {'available': True, 'connected': False, 'login_pending': False}
            def close(self):
                pass
        server.CODEX = FakeCodex()
        first, second = root / 'selected.ibt', root / 'reference.ibt'
        recording(first, slower=True)
        recording(second)
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1500, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.route('**/api/track-imagery', lambda route: route.fulfill(json={'status': 'unavailable', 'message': 'Offline imagery fixture'}))
                page.goto(f'http://127.0.0.1:{http.server_port}')
                page.click('[data-tab=lap-comparison]')
                page.click('#lc-import-selected')
                page.set_input_files('#upload-file', str(first))
                page.wait_for_selector('#import-dialog:not([open])', state='attached')
                assert page.locator('#tab-lap-comparison').is_visible()
                assert page.locator('#lc-run-selected').input_value()
                assert page.evaluate('state.run === null')
                page.click('#lc-import-reference')
                page.set_input_files('#upload-file', str(second))
                page.wait_for_selector('#lc-results:visible')
                assert '+2.000 s' in page.locator('#lc-total').inner_text()
                assert page.locator('#lc-charts svg').count() == 5
                page.wait_for_selector('#lc-charts .stat-readout[data-channel=TimeDelta]')
                assert page.locator('#lc-charts .stat-readout[data-channel]').count() == 9
                assert page.locator('#lc-charts .stat-min').count() == 9
                assert page.locator('#lc-charts .stat-max').count() == 9
                assert page.locator('#lc-charts .stat-mean').count() == 9
                assert '4.000' in page.locator('#lc-charts .stat-readout[data-channel=TimeDelta] .stat-max').inner_text()
                for i, color in enumerate(['#0080ff', '#00ff00', '#ff0000', '#00ffff', '#ffffff']):
                    assert page.locator('#lc-charts .chart').nth(i).locator('.trace').last.get_attribute('stroke') == color
                assert 'Lap-progress diagram' in page.locator('#lc-map-status').inner_text()
                assert 'unavailable' in page.locator('#lc-line-readout').inner_text()
                assert page.locator('#lc-line-in').is_disabled()
                # Explicitly opening Telemetry chooses a run only when none was open.
                page.click('[data-tab=telemetry]')
                page.wait_for_selector('#charts svg')
                original_run = page.evaluate('state.run.id')
                page.click('[data-tab=lap-comparison]')
                page.select_option('#lc-lap-selected', '1')
                page.wait_for_selector('#lc-results:visible')
                assert page.locator('#lc-lap-reference').input_value() == '0'
                page.wait_for_selector('#lc-charts .stat-readout[data-channel=Brake][data-lap="1"]')
                assert page.locator('#lc-charts .stat-readout[data-channel=Brake][data-lap="1"] .stat-max').inner_text().strip().endswith('0.0')
                assert page.locator('#lc-charts .stat-readout[data-channel=Brake][data-lap="0"] .stat-max').inner_text().strip().endswith('99.0')
                page.click('#lc-edit-toggle')
                # Replace estimated zones with a known half-lap corner.
                page.locator('#lc-corner-inputs [data-delete]').evaluate_all('(els)=>els.reverse().forEach(el=>el.click())')
                page.click('#lc-add')
                page.locator('#lc-corner-inputs [data-field=label]').fill('Test corner')
                page.locator('#lc-corner-inputs [data-field=start]').fill('0')
                page.locator('#lc-corner-inputs [data-field=end]').fill('50')
                page.click('#lc-apply')
                page.wait_for_function("() => document.querySelector('#lc-edit-status').textContent.includes('Draft applied')")
                rows = page.locator('#lc-segments tr')
                assert rows.count() == 2
                assert 'Test corner' in rows.first.inner_text()
                assert '+4.000 s' in rows.first.inner_text()
                page.select_option('#lc-sort', 'gain')
                assert 'Straight' in rows.first.inner_text()
                page.select_option('#lc-sort', 'magnitude')
                assert 'Test corner' in rows.first.inner_text()
                page.select_option('#lc-sort', 'track')
                page.locator('#lc-segments button').first.click()
                assert '0.00–50.00%' in page.locator('#lc-range').inner_text()
                page.wait_for_function("() => [...document.querySelectorAll('#lc-charts .stat-readout[data-channel=Speed]')].every(el=>el.dataset.validCount==='61') && document.querySelectorAll('#lc-charts .stat-readout[data-channel=Speed]').length===2")
                assert 'Minimum corner speed' in page.locator('#lc-detail').inner_text()
                page.click('#lc-full')
                assert '0.00–100.00%' in page.locator('#lc-range').inner_text()
                page.click('#lc-save')
                page.wait_for_function("() => document.querySelector('#lc-edit-status').textContent.includes('Saved for')")
                # Invalid overlapping edits remain visible and fixable.
                page.click('#lc-add')
                page.locator('#lc-corner-inputs [data-field=start]').last.fill('20')
                page.locator('#lc-corner-inputs [data-field=end]').last.fill('30')
                page.click('#lc-apply')
                page.wait_for_function("() => document.querySelector('#lc-edit-status').textContent.includes('overlap')")
                assert page.locator('#lc-editor').is_visible()
                page.locator('#lc-corner-inputs [data-delete]').last.click()
                page.click('#lc-apply')
                page.wait_for_function("() => document.querySelector('#lc-edit-status').textContent.includes('Draft applied')")
                page.reload()
                page.wait_for_selector('#lc-results:visible')
                assert page.locator('#lc-lap-selected').input_value() == '1'
                assert 'saved comparison map' in page.locator('#lc-provenance').inner_text()
                output = Path(__file__).resolve().parents[1] / 'test-results'
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output / 'lap-comparison-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_timeout(250)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / 'lap-comparison-mobile.png'), full_page=True)
                # Existing Telemetry and setup comparison still render independently.
                page.set_viewport_size({'width': 1500, 'height': 1000})
                page.click('[data-tab=telemetry]')
                page.wait_for_selector('#charts svg')
                assert page.locator('#charts svg').count() == 5
                assert page.evaluate('state.run.id') == original_run
                # Native trajectories, shared hover, path visibility, map zoom and editing.
                page.click('[data-tab=lap-comparison]')
                page.unroute('**/api/track-imagery')
                page.route('**/api/track-imagery', lambda route: route.fulfill(json={
                    'status': 'available', 'bounds': [-86.21, 39.79, -86.19, 39.81],
                    'image_url': '/test-aerial.png', 'source_url': 'https://imagery.nationalmap.gov/',
                    'message': 'Aerial imagery fixture', 'attribution': 'USGS imagery fixture'}))
                page.route('**/test-aerial.png', lambda route: route.fulfill(content_type='image/png', body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')))
                gps_first, gps_second = root / 'selected-gps.ibt', root / 'reference-gps.ibt'
                recording(gps_first, slower=True)
                recording(gps_second)
                add_gps(gps_first)
                add_gps(gps_second, .00001)
                for side, path in [('selected', gps_first), ('reference', gps_second)]:
                    page.click(f'#lc-import-{side}')
                    page.set_input_files('#upload-file', str(path))
                    page.wait_for_selector('#import-dialog:not([open])', state='attached')
                page.wait_for_selector('#lc-map .lc-path-selected')
                page.wait_for_selector('#lc-map .lc-aerial-image')
                assert page.locator('#lc-line-detail .lc-aerial-image').count() == 1
                assert page.locator('#lc-line-detail .lc-line-road').count() == 0
                page.uncheck('#lc-show-aerial')
                assert page.locator('#lc-map .lc-aerial-image').count() == 0
                assert page.locator('#lc-line-detail .lc-aerial-image').count() == 0
                page.check('#lc-show-aerial')
                assert page.locator('#lc-map .lc-aerial-image').count() == 1
                assert page.locator('#lc-map svg').get_attribute('viewBox') == '0 0 560 320'
                assert 'S/F' in page.locator('#lc-map').inner_text()
                assert 'N ←' in page.locator('#lc-map').inner_text()
                assert page.locator('#lc-map [data-map-corner="Test corner"]').count() == 1
                assert page.locator('#lc-map-zones button').first.inner_text() == 'Test corner'
                assert page.locator('#lc-line-detail .lc-line-path').count() == 2
                assert page.locator('#lc-line-detail .lc-line-path.lc-line-reference').evaluate("el => getComputedStyle(el).strokeDasharray") == 'none'
                assert page.locator('#lc-line-detail .lc-line-path.lc-line-reference').evaluate("el => getComputedStyle(el).stroke") == 'rgb(255, 255, 255)'
                assert page.locator('#lc-map .lc-path-reference').evaluate("el => getComputedStyle(el).strokeDasharray") == 'none'
                # The camera is local, but both recorded paths continue beyond its frame.
                assert page.locator('#lc-line-detail .lc-line-path').evaluate_all('''paths => paths.every(path => {
                    const outside=p=>p.x<0||p.x>560||p.y<0||p.y>340;
                    return outside(path.getPointAtLength(0))&&outside(path.getPointAtLength(path.getTotalLength()));
                })''')
                assert 'path separation' in page.locator('#lc-line-readout').inner_text()
                # The close-up preserves the actual distance between the two GPS paths.
                measured = page.locator('#lc-line-detail svg').evaluate('''svg => {
                    const gap=svg.querySelector('.lc-line-gap'),scale=Number(svg.dataset.pixelsPerMeter);
                    return Math.hypot(gap.x1.baseVal.value-gap.x2.baseVal.value,gap.y1.baseVal.value-gap.y2.baseVal.value)/scale;
                }''')
                assert .5 < measured < 1.5
                span = float(page.locator('#lc-line-detail svg').get_attribute('data-distance-span'))
                page.click('#lc-line-in')
                assert float(page.locator('#lc-line-detail svg').get_attribute('data-distance-span')) < span
                page.click('#lc-line-out')
                page.locator('#lc-map-zones button').first.click()
                assert page.locator('#lc-map svg').get_attribute('viewBox') == '0 0 560 320'
                assert page.locator('#lc-map .map-corner.active').count() == 1
                assert '0.00–50.00%' in page.locator('#lc-range').inner_text()
                page.click('#lc-full')
                assert page.locator('#lc-map .lc-path-reference').get_attribute('points') != page.locator('#lc-map .lc-path-selected').get_attribute('points')
                page.locator('#lc-charts svg').first.hover()
                assert page.locator('#lc-charts .crosshair[visibility=visible]').count() == 5
                assert page.locator('#lc-cursor-reference').get_attribute('visibility') is None
                page.wait_for_function("() => document.querySelector('#lc-line-readout').textContent.includes(document.querySelector('#lc-map-position').textContent.split('%')[0]+'%')")
                page.uncheck('#lc-show-reference')
                assert page.locator('#lc-map .lc-path-reference').count() == 0
                assert page.locator('#lc-line-detail .lc-line-reference').count() == 0
                page.check('#lc-show-reference')
                before = page.locator('#lc-map svg').get_attribute('viewBox')
                label_size = lambda: page.locator('#lc-map [data-map-corner] text').first.evaluate('el=>parseFloat(getComputedStyle(el).fontSize)*Math.hypot(el.getScreenCTM().a,el.getScreenCTM().b)')
                assert abs(label_size() - 12) < .2
                page.click('#lc-map-in')
                assert page.locator('#lc-map svg').get_attribute('viewBox') != before
                assert abs(label_size() - 12) < .2
                page.click('#lc-fit')
                page.click('#lc-edit-toggle')
                handle = page.locator('#lc-map .lc-handle[data-field=end]').first
                handle.scroll_into_view_if_needed()
                target = page.locator('#lc-map svg').evaluate('''svg => {
                    const path=svg.querySelector('.lc-path-reference'),point=path.points.getItem(35),p=svg.createSVGPoint();
                    p.x=point.x;p.y=point.y;const q=p.matrixTransform(svg.getScreenCTM());return {x:q.x,y:q.y};
                }''')
                box = handle.bounding_box()
                page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                page.mouse.down()
                page.mouse.move(target['x'], target['y'], steps=5)
                page.mouse.up()
                page.wait_for_selector('#lc-results:visible')
                assert 25 < float(page.locator('#lc-corner-inputs [data-field=end]').first.input_value()) < 35
                page.screenshot(path=str(output / 'lap-comparison-gps.png'), full_page=True)
                # An older response arriving last must not replace a newer lap.
                held = []
                def reorder(route):
                    response = route.fetch()
                    if not held:
                        held.append((route, response))
                    else:
                        route.fulfill(response=response)
                        held[0][0].fulfill(response=held[0][1])
                page.route('**/api/lap-comparison', reorder)
                page.select_option('#lc-lap-selected', '1')
                page.wait_for_timeout(150)
                assert held
                page.select_option('#lc-lap-selected', '2')
                page.wait_for_selector('#lc-results:visible')
                page.wait_for_timeout(150)
                assert 'Selected lap 3 /' in page.locator('#workspace-status').inner_text()
                page.unroute('**/api/lap-comparison', reorder)
                assert not errors, errors
                browser.close()
            print('Passed: independent uploads/laps, native delta, rankings, linked segment selection, editor errors/save/reload, responsive layout and Telemetry regression.')
        finally:
            http.shutdown()
            http.server_close()


if __name__ == '__main__':
    main()
