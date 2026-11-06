import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import track_imagery as imagery
from corner_map import profile_key


class ImageryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.run = {'metadata': {'track_id': 448, 'configuration': 'Road Course'}}
        self.points = [{'lat': 39.79, 'lon': -86.24}, {'lat': 39.81, 'lon': -86.23}]
        imagery._failures.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_bounds_include_padding_and_unsupported_locations(self):
        bounds = imagery.bounds_for(self.points)
        self.assertLess(bounds[0], -86.24)
        self.assertGreater(bounds[3], 39.81)
        for points in ([], [{'lat': 50.5, 'lon': 5.9}], [{'lat': 30, 'lon': -120}, {'lat': 40, 'lon': -90}]):
            with self.assertRaises(ValueError):
                imagery.bounds_for(points)

    def test_download_preserves_returned_georeferencing(self):
        bounds = [-86.24, 39.79, -86.23, 39.81]
        info = {'href': 'https://imagery.nationalmap.gov/arcgis/rest/directories/image.jpg',
                'extent': dict(zip(('xmin', 'ymin', 'xmax', 'ymax'), bounds), spatialReference={'wkid': 4326})}
        with patch.object(imagery, 'fetch_json', return_value=info) as metadata, patch.object(imagery, 'urlopen', return_value=io.BytesIO(b'\xff\xd8aerial')):
            content, extent = imagery.download(bounds)
        self.assertEqual(content, b'\xff\xd8aerial')
        self.assertEqual(extent, bounds)
        self.assertIn('imageSR=4326', metadata.call_args.args[0])

    def test_invalid_downloads_are_rejected(self):
        bounds = [-86.24, 39.79, -86.23, 39.81]
        for info in ({'href': 'https://untrusted.example/image.jpg'},
                     {'href': 'https://imagery.nationalmap.gov/image.jpg', 'extent': {}}):
            with patch.object(imagery, 'fetch_json', return_value=info), self.assertRaises(ValueError):
                imagery.download(bounds)

    def test_track_cache_reuses_image_without_network(self):
        extent = [-86.24, 39.79, -86.23, 39.81]
        with patch.object(imagery, 'get_map', return_value={'points': self.points}), patch.object(imagery, 'download', return_value=(b'\xff\xd8aerial', extent)) as download:
            first = imagery.get_for(self.data, self.run, server.atomic_json)
            second = imagery.get_for(self.data, self.run, server.atomic_json)
        self.assertEqual(first, second)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(first['bounds'], extent)
        self.assertTrue(imagery.image_file(self.data, profile_key(self.run)).exists())
        self.assertIn('USGS', first['attribution'])
        with self.assertRaises(ValueError):
            imagery.image_file(self.data, '../outside')

    def test_connection_failure_does_not_block_analysis_and_retry_recovers(self):
        with patch.object(imagery, 'get_map', return_value={'points': self.points}), patch.object(imagery, 'download', side_effect=OSError('offline')):
            self.assertEqual(imagery.get_for(self.data, self.run, server.atomic_json)['status'], 'unavailable')
        with patch.object(imagery, 'get_map', return_value={'points': self.points}), patch.object(imagery, 'download', return_value=(b'\xff\xd8aerial', [-86.24, 39.79, -86.23, 39.81])) as download:
            self.assertEqual(imagery.get_for(self.data, self.run, server.atomic_json)['status'], 'unavailable')
            download.assert_not_called()
            self.assertEqual(imagery.get_for(self.data, self.run, server.atomic_json, force=True)['status'], 'available')

    def test_local_image_and_metadata_endpoints(self):
        with patch.object(server, 'DATA', self.data), patch.object(server, 'load_run', return_value=self.run), \
                patch.object(imagery, 'get_map', return_value={'points': self.points}), \
                patch.object(imagery, 'download', return_value=(b'\xff\xd8aerial', [-86.24, 39.79, -86.23, 39.81])):
            http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{http.server_port}'
            try:
                req = Request(base + '/api/track-imagery', data=json.dumps({'run_id': '0' * 32}).encode())
                with urlopen(req) as response:
                    meta = json.load(response)
                self.assertEqual(meta['status'], 'available')
                with urlopen(base + meta['image_url']) as response:
                    self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
                    self.assertEqual(response.read(), b'\xff\xd8aerial')
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + '/api/track-imagery/image?key=../outside')
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
            finally:
                http.shutdown()
                http.server_close()


if __name__ == '__main__':
    unittest.main()
