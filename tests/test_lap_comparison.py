"""Synthetic native IBTs with known sector times and narrow input peaks."""
import copy
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import request, error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workspace import make_log
from analysis import analyze
from recordings import retain
from telemetry import IBT
import server
from lap_comparison import calculate, confirmed_corners, partition, save_segments, validate_corners


def recording(path, *, slower=False, duplicate=False, backwards=False, gap=False):
    make_log(path, gap=gap)
    log = IBT(path, False)
    raw = bytearray(path.read_bytes())
    for i in range(log.count):
        local = (i % 120) * .5
        if slower:
            t = (i // 120) * 62 + (local * 34 / 30 if local <= 30 else 34 + (local - 30) * 28 / 30)
            struct.pack_into('<d', raw, log.data_offset + i * log.row_size + log.headers['SessionTime']['offset'], t)
        if duplicate and i == 20:
            struct.pack_into('<f', raw, log.data_offset + i * log.row_size + log.headers['LapDistPct']['offset'], 19 / 120)
        if backwards and i == 20:
            struct.pack_into('<f', raw, log.data_offset + i * log.row_size + log.headers['LapDistPct']['offset'], 18 / 120)
        if i == 10:
            struct.pack_into('<f', raw, log.data_offset + i * log.row_size + log.headers['Brake']['offset'], .99)
            struct.pack_into('<f', raw, log.data_offset + i * log.row_size + log.headers['Speed']['offset'], 10.)
    path.write_bytes(raw)


def add_gps(path, offset=0):
    """Append native double position channels without changing existing samples."""
    log = IBT(path, False)
    original = path.read_bytes()
    values = struct.unpack_from('<10i', original)
    info = original[values[5]:values[5] + values[4]]
    table = bytearray(original[values[7]:values[7] + values[6] * 144])
    for i, name in enumerate(('Lat', 'Lon')):
        header = bytearray(144)
        struct.pack_into('<iii', header, 0, 5, log.row_size + i * 8, 1)
        header[16:16+len(name)] = name.encode()
        table.extend(header)
    header = bytearray(original[:144])
    info_offset = 144 + len(table)
    data_offset = info_offset + len(info)
    struct.pack_into('<i', header, 20, info_offset)
    struct.pack_into('<i', header, 24, values[6] + 2)
    struct.pack_into('<i', header, 36, log.row_size + 16)
    struct.pack_into('<i', header, 52, data_offset)
    samples = bytearray()
    for i in range(log.count):
        row = original[log.data_offset + i * log.row_size:log.data_offset + (i+1) * log.row_size]
        p = (i % 120) / 120 * 2 * math.pi
        samples.extend(row + struct.pack('<dd', 39.8 + (.004 + offset) * math.cos(p), -86.2 + (.004 + offset) * math.sin(p)))
    path.write_bytes(header + table + info + samples)


class LapComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = []
        for i in range(2):
            path = self.root / f'{i}.ibt'
            recording(path, slower=i == 0)
            run = analyze(path)
            run.update(id=str(i) * 32, recording=retain(self.root, path))
            self.runs.append(run)
        self.body = {'selected_lap_id': 0, 'reference_lap_id': 0,
                     'corners': [{'label': 'Corner', 'start': 0, 'end': 50}]}

    def tearDown(self):
        self.temp.cleanup()

    def compare(self, body=None):
        return calculate(self.root, *self.runs, self.body if body is None else body)

    def test_known_losses_gains_and_native_extrema(self):
        c = self.compare()
        self.assertAlmostEqual(c['delta_s'], 2)
        self.assertAlmostEqual(sum(s['delta_s'] for s in c['segments']), 2)
        self.assertAlmostEqual(c['segments'][0]['delta_s'], 4)
        self.assertAlmostEqual(c['segments'][1]['delta_s'], -2)
        self.assertAlmostEqual(c['segments'][0]['selected']['max_brake_pct'], 99, places=4)
        self.assertEqual(c['segments'][0]['selected']['min_speed_kph'], 36)
        self.assertAlmostEqual(c['delta_trace']['values'][-1], 2)
        self.assertIsNone(c['map']['selected'])
        self.assertTrue(c['selected']['sha256'])

    def test_wrap_partition_and_duration_totals(self):
        self.body['corners'] = [{'label': 'Finish corner', 'start': 90, 'end': 10}, {'label': 'T2', 'start': 30, 'end': 45}]
        c = self.compare()
        pieces = sorted(r for s in c['segments'] for r in s['ranges'])
        self.assertEqual(pieces[0][0], 0)
        self.assertEqual(pieces[-1][1], 100)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(pieces, pieces[1:])))
        for side in ('selected', 'reference'):
            self.assertAlmostEqual(sum(s[side]['elapsed_s'] for s in c['segments']), c[side]['duration'])
        c = self.compare(dict(self.body, corners=[{'label': 'T1', 'start': 10, 'end': 20}]))
        self.assertEqual(len(next(s for s in c['segments'] if s['kind'] == 'straight')['ranges']), 2)

    def test_overlap_profile_conversion_and_validation(self):
        profile = {'corners': [{'label': 'T1', 'start': 5, 'reference': 15, 'end': 25},
                               {'label': 'T2', 'start': 20, 'reference': 30, 'end': 35}]}
        zones = confirmed_corners(profile)
        self.assertEqual(zones[0]['end'], 22.5)
        self.assertEqual(zones[1]['start'], 22.5)
        for items in ([{'label': 'x', 'start': 3, 'end': 3}],
                      [{'label': 'x', 'start': False, 'end': 3}],
                      [{'label': 'x', 'start': 0, 'end': 20}, {'label': 'y', 'start': 10, 'end': 30}]):
            with self.assertRaises(ValueError):
                validate_corners(items)

    def test_watkins_profile_seeds_bus_stop_and_reconciles_both_recordings(self):
        for run in self.runs:
            run['metadata'].update(track_id=434, configuration='Boot')
        c = self.compare({'selected_lap_id': 0, 'reference_lap_id': 0})
        self.assertEqual(c['provenance']['source'], 'confirmed corner profile')
        self.assertEqual([z['label'] for z in c['corners']],
                         ['T1', 'T2', 'T3', 'T4', 'Bus Stop', 'T5', 'T6', 'T7', 'T8', 'T9', 'T10', 'T11'])
        bus_stop = next(s for s in c['segments'] if s['label'] == 'Bus Stop')
        self.assertEqual(bus_stop['kind'], 'corner')
        self.assertGreater(bus_stop['selected']['sample_count'], 0)
        self.assertGreater(len(c['map']['outline']), 30)
        pieces = sorted(r for s in c['segments'] for r in s['ranges'])
        self.assertEqual(pieces[0][0], 0)
        self.assertEqual(pieces[-1][1], 100)
        self.assertTrue(all(a[1] == b[0] for a, b in zip(pieces, pieces[1:])))
        for side in ('selected', 'reference'):
            self.assertAlmostEqual(sum(s[side]['elapsed_s'] for s in c['segments']), c[side]['duration'])
        self.assertAlmostEqual(sum(s['delta_s'] for s in c['segments']), 2)
        saved = save_segments(self.root, self.runs[0], {'corners': c['corners']}, server.atomic_json)
        self.assertEqual(self.compare({'selected_lap_id': 1, 'reference_lap_id': 0})['provenance']['revision'], saved['revision'])
        save_segments(self.root, self.runs[0], {'reset': True}, server.atomic_json)
        self.assertEqual(self.compare({'selected_lap_id': 0, 'reference_lap_id': 0})['corners'], c['corners'])

    def test_watkins_profile_does_not_seed_other_layouts(self):
        from corner_map import load_profile
        from live_maps import get_map
        run = self.runs[0]
        run['metadata'].update(track_id=434, configuration='Boot')
        self.assertEqual(len(load_profile(self.root, run)['corners']), 11)
        self.assertEqual(len(get_map(self.root, 434, 'Boot')['corners']), 11)
        for track_id, config in [(435, 'Classic Boot'), (434, 'Cup'), (56, 'Boot'), (448, 'Boot')]:
            run['metadata'].update(track_id=track_id, configuration=config)
            self.assertIsNone(load_profile(self.root, run))
            self.assertEqual(get_map(self.root, track_id, config)['status'], 'lap_progress')

    def test_saved_map_reused_and_reset(self):
        saved = save_segments(self.root, self.runs[0], {'corners': self.body['corners']}, server.atomic_json)
        c = self.compare({'selected_lap_id': 0, 'reference_lap_id': 1})
        self.assertEqual(c['provenance']['revision'], saved['revision'])
        self.assertEqual(c['corners'], self.body['corners'])
        save_segments(self.root, self.runs[0], {'reset': True}, server.atomic_json)
        c = self.compare({'selected_lap_id': 0, 'reference_lap_id': 0})
        self.assertEqual(c['provenance']['source'], 'automatic estimated zones')
        self.assertTrue(all(z['label'].startswith('Estimated corner') for z in c['corners']))

    def test_flags_cars_track_and_partial(self):
        self.runs[0]['laps'][0]['reasons'] = ['Pit lane']
        self.runs[0]['notes']['excluded_laps'] = [0]
        self.runs[0]['metadata']['car_id'] = 99
        c = self.compare()
        self.assertTrue(any('Different cars' in w for w in c['warnings']))
        self.assertIn('Selected: Pit lane', c['warnings'])
        self.assertIn('Selected: Manually excluded', c['warnings'])
        self.runs[0]['laps'][0]['complete'] = False
        with self.assertRaisesRegex(ValueError, 'complete'):
            self.compare()
        self.runs[0]['laps'][0]['complete'] = True
        self.runs[0]['metadata']['track_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'same track'):
            self.compare()

    def test_duplicate_distance_allowed_backward_and_gaps_rejected(self):
        for mode in ('duplicate', 'backwards', 'gap'):
            path = self.root / 'special.ibt'
            recording(path, **{mode: True})
            run = analyze(path)
            run.update(id='f' * 32, recording=retain(self.root, path))
            args = dict(self.body, selected_lap_id=1 if mode == 'gap' else 0)
            if mode == 'duplicate':
                result = calculate(self.root, run, self.runs[1], args)
                self.assertEqual(result['delta_s'], 0)
            else:
                with self.assertRaisesRegex(ValueError, 'discontinuous|backwards|complete'):
                    calculate(self.root, run, self.runs[1], args)

    def test_missing_original_rejected(self):
        self.runs[0].pop('recording')
        with self.assertRaisesRegex(ValueError, 'Attach'):
            self.compare()

    def test_native_gps_for_each_selected_lap_and_missing_inputs(self):
        for i in range(2):
            path = self.root / f'{i}.ibt'
            add_gps(path, i * .00001)
            run = analyze(path)
            run.update(id=str(i) * 32, recording=retain(self.root, path))
            self.runs[i] = run
        c = self.compare()
        self.assertEqual(len(c['map']['selected']), 120)
        self.assertNotEqual(c['map']['selected'][0]['lat'], c['map']['reference'][0]['lat'])
        self.assertEqual(c['map']['selected'][10]['distance'], c['selected']['traces']['distance'][11])
        path = self.root / 'no-brake.ibt'
        recording(path)
        raw = bytearray(path.read_bytes())
        position = 144 + list(IBT(path, False).headers).index('Brake') * 144 + 16
        raw[position:position+5] = b'Other'
        path.write_bytes(raw)
        run = analyze(path)
        run.update(id='e' * 32, recording=retain(self.root, path))
        c = calculate(self.root, run, self.runs[1], self.body)
        self.assertIsNone(c['segments'][0]['selected']['max_brake_pct'])
        self.assertIsNone(c['segments'][0]['selected']['braking_s'])

    def test_different_sample_rates_and_duplicate_timestamps(self):
        path = self.root / 'lower-rate.ibt'
        recording(path)
        log = IBT(path, False)
        raw = bytearray(path.read_bytes())
        samples = b''.join(raw[log.data_offset+i*log.row_size:log.data_offset+(i+1)*log.row_size] for i in range(0, log.count, 2))
        raw = raw[:log.data_offset] + samples
        struct.pack_into('<i', raw, 8, 1)
        struct.pack_into('<i', raw, 140, len(samples) // log.row_size)
        path.write_bytes(raw)
        run = analyze(path)
        run.update(id='d' * 32, recording=retain(self.root, path))
        c = calculate(self.root, self.runs[0], run, self.body)
        self.assertAlmostEqual(c['delta_s'], 2)
        self.assertAlmostEqual(c['segments'][0]['delta_s'], 4)
        path = self.root / 'repeat-time.ibt'
        make_log(path, repeat_time=True)
        run = analyze(path)
        run.update(id='c' * 32, recording=retain(self.root, path))
        c = calculate(self.root, self.runs[0], run, self.body)
        self.assertAlmostEqual(sum(s['delta_s'] for s in c['segments']), c['delta_s'])

    def test_duration_intervals_clipped_at_fractional_boundaries(self):
        self.body['corners'][0].update(start=8.4, end=8.6)
        c = self.compare()
        row = next(s for s in c['segments'] if s['kind'] == 'corner')
        self.assertLessEqual(row['selected']['braking_s'], row['selected']['elapsed_s'] + 1e-10)
        self.assertAlmostEqual(sum(s['selected']['elapsed_s'] for s in c['segments']), 62)

    def test_cross_recording_plot_statistics_use_native_reference_and_range(self):
        from plot_stats import calculate as stats
        import numpy as np
        a, b = IBT(self.root / '0.ibt'), IBT(self.root / '1.ibt')
        args = {'lap_ids': [0], 'channels': ['TimeDelta'], 'reference_lap_id': 1, 'distance_range': [20, 35]}
        row = stats(self.root, self.runs[0], args, reference_run=self.runs[1])['rows'][0]
        distances = a.channels['LapDistPct'][:120] * 100
        mask = (distances >= 20) & (distances <= 35)
        expected = (a.channels['SessionTime'][:120] - (b.channels['SessionTime'][120:240] - b.channels['SessionTime'][120]))[mask]
        self.assertEqual(row['valid_sample_count'], len(expected))
        self.assertEqual(row['min'], float(np.min(expected)))
        self.assertEqual(row['max'], float(np.max(expected)))
        self.assertEqual(row['mean'], float(np.mean(expected)))
        self.assertEqual(row['provenance']['reference_run_id'], self.runs[1]['id'])
        self.assertEqual(row['provenance']['reference_recording_sha256'], self.runs[1]['recording']['sha256'])
        self.assertFalse(row['provenance']['synthetic_finish_samples'])
        self.runs[1].pop('recording')
        missing = stats(self.root, self.runs[0], args, reference_run=self.runs[1])['rows'][0]
        self.assertFalse(missing['available'])
        self.runs[1]['metadata']['configuration'] = 'Other layout'
        with self.assertRaisesRegex(ValueError, 'same track'):
            stats(self.root, self.runs[0], args, reference_run=self.runs[1])


class ComparisonAPITests(unittest.TestCase):
    def test_api_import_compare_save_and_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DATA', Path(tmp) / 'data'):
            path = Path(tmp) / 'test.ibt'
            recording(path)
            a, b = server.import_run(path), server.import_run(path)
            http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            def call(endpoint, body):
                req = request.Request(f'http://127.0.0.1:{http.server_port}' + endpoint, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
                with request.urlopen(req) as response:
                    return json.load(response)
            try:
                body = {'selected_run_id': a['id'], 'reference_run_id': b['id'], 'selected_lap_id': 0, 'reference_lap_id': 1}
                c = call('/api/lap-comparison', body)
                self.assertEqual(c['reference']['lap_id'], 1)
                stats = call('/api/plot-stats', {'run_id': a['id'], 'lap_ids': [0], 'channels': ['TimeDelta'], 'reference_run_id': b['id'], 'reference_lap_id': 1})['rows'][0]
                self.assertEqual(stats['max'], 0)
                self.assertEqual(stats['provenance']['reference_run_id'], b['id'])
                call('/api/comparison-segments', {'run_id': a['id'], 'corners': [{'label': 'Test', 'start': 20, 'end': 30}]})
                self.assertEqual(call('/api/lap-comparison', body)['provenance']['source'], 'saved comparison map')
                with self.assertRaises(error.HTTPError) as caught:
                    call('/api/lap-comparison', dict(body, selected_lap_id=True))
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()
            finally:
                http.shutdown()
                http.server_close()


if __name__ == '__main__':
    unittest.main()
