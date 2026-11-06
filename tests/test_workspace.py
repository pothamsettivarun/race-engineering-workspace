import copy
import contextlib
import io
import json
import math
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import request, error

import yaml

import server
from analysis import analyze, compare, findings, summary
from telemetry import IBT, TelemetryError


def make_log(path, *, incident=False, pit=False, gap=False, track_temp=30, arb=4,
             repeat_time=False, clock_reset=False):
    names = [('SessionTime', 5), ('Lap', 2), ('LapDistPct', 4), ('Speed', 4),
             ('Throttle', 4), ('Brake', 4), ('SteeringWheelAngle', 4), ('FuelLevel', 4),
             ('PlayerTrackSurface', 2), ('PlayerCarMyIncidentCount', 2), ('OnPitRoad', 1),
             ('TrackTemp', 4), ('AirTemp', 4), ('WindVel', 4), ('TrackWetness', 2)]
    fmt = {1: '?', 2: 'i', 4: 'f', 5: 'd'}
    offsets, row_size = {}, 0
    for name, kind in names:
        offsets[name] = row_size
        row_size += struct.calcsize('<' + fmt[kind])
    session = {'WeekendInfo': {'TrackName': 'test', 'TrackDisplayName': 'Test circuit', 'TrackID': 1,
                              'TrackLength': '3.0 km', 'BuildVersion': 'test',
                              'WeekendOptions': {'IsFixedSetup': 0}},
               'DriverInfo': {'DriverCarIdx': 0, 'DriverSetupName': 'baseline.sto',
                              'Drivers': [{'CarIdx': 0, 'CarScreenName': 'Test car', 'CarID': 12}]},
               'CarSetup': {'Chassis': {'Front': {'ArbSetting': arb}}, 'Tires': {'LastHotPressure': '180 kPa'}}}
    info = yaml.safe_dump(session).encode()
    info_offset = 144 + len(names) * 144
    data_offset = info_offset + len(info)
    # Three complete 60-second laps plus the next start-line sample.
    records = 361
    header = bytearray(144)
    struct.pack_into('<10i', header, 0, 2, 1, 2, 0, len(info), info_offset, len(names), 144, 1, row_size)
    struct.pack_into('<4i', header, 48, 1, data_offset, 0, 0)
    struct.pack_into('<Qddii', header, 112, 1700000000, 0, 180, 3, records)
    with Path(path).open('wb') as f:
        f.write(header)
        for name, kind in names:
            v = bytearray(144)
            struct.pack_into('<iii', v, 0, kind, offsets[name], 1)
            v[16:16+len(name)] = name.encode()
            f.write(v)
        f.write(info)
        for i in range(records):
            sample_time = i*.5 + (2 if gap and i>=150 else 0)
            if repeat_time and i in (10, 150, 240):
                sample_time -= .5
            if clock_reset and i >= 150:
                sample_time -= 10
            values = {'SessionTime': sample_time, 'Lap': i//120+1,
                      'LapDistPct': (i%120)/120, 'Speed': 40+5*math.sin(i/10),
                      'Throttle': .8, 'Brake': 0, 'SteeringWheelAngle': .6*math.sin(i/10),
                      'FuelLevel': 50-i/100, 'PlayerTrackSurface': 3,
                      'PlayerCarMyIncidentCount': int(incident and i>=150),
                      'OnPitRoad': pit and 130<=i<=140, 'TrackTemp': track_temp,
                      'AirTemp': 20, 'WindVel': 2, 'TrackWetness': 1}
            row = bytearray(row_size)
            for name, kind in names:
                struct.pack_into('<'+fmt[kind], row, offsets[name], values[name])
            f.write(row)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.ibt'
        make_log(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_disk_layout_and_complete_laps(self):
        run = analyze(self.path)
        self.assertEqual(run['metadata']['car'], 'Test car')
        self.assertEqual(run['summary']['usable_laps'], 3)
        self.assertEqual(run['summary']['median'], 60)
        self.assertEqual(run['metadata']['setup_flat']['Chassis / Front / ArbSetting'], '4')
        self.assertEqual(run['conditions']['track_temp']['source'], 'telemetry: TrackTemp')
        self.assertEqual(run['laps'][0]['traces']['distance'][-1], 100)
        self.assertEqual(run['laps'][0]['traces']['time'][-1], 60)

    def test_truncated_log_is_rejected(self):
        content = self.path.read_bytes()
        self.path.write_bytes(content[:-10])
        with self.assertRaises(TelemetryError):
            IBT(self.path)

    def test_repeated_timestamps_keep_complete_laps_and_original_samples(self):
        make_log(self.path, repeat_time=True)
        run = analyze(self.path)
        self.assertEqual(run['summary']['usable_laps'], 3)
        self.assertEqual([(l['sample_start'], l['sample_stop']) for l in run['laps']],
                         [(0, 120), (120, 240), (240, 360)])
        self.assertEqual([l['duration'] for l in run['laps']], [60, 59.5, 60.5])
        self.assertEqual(len(run['laps'][0]['traces']['time']), 121)
        self.assertEqual(run['laps'][0]['traces']['time'][9:11], [4.5, 4.5])

    def test_backwards_clock_still_splits_and_excludes_lap(self):
        make_log(self.path, clock_reset=True)
        run = analyze(self.path)
        self.assertEqual(run['summary']['usable_laps'], 2)
        self.assertEqual(len(run['laps']), 4)
        self.assertFalse(run['laps'][1]['complete'])
        self.assertFalse(run['laps'][2]['complete'])

    def test_near_start_line_counter_reset_is_not_a_complete_lap(self):
        data = bytearray(self.path.read_bytes())
        log = IBT(self.path)
        # A formation-lap counter correction near the line, followed by driving
        # the rest of the circuit, must not masquerade as a full timed lap.
        for i in range(2, 120):
            struct.pack_into('<i', data, log.data_offset+i*log.row_size+log.headers['Lap']['offset'], 0)
        for i in range(120, log.count):
            struct.pack_into('<i', data, log.data_offset+i*log.row_size+log.headers['Lap']['offset'], i//120)
        struct.pack_into('<i', data, log.data_offset+log.headers['Lap']['offset'], 0)
        self.path.write_bytes(data)
        run = analyze(self.path)
        self.assertFalse(run['laps'][0]['complete'])
        self.assertEqual(run['summary']['usable_laps'], 2)

    def test_spawn_near_finish_does_not_count_as_a_complete_lap(self):
        log = IBT(self.path)
        data = bytearray(self.path.read_bytes())
        for i in range(1, 120):
            struct.pack_into('<f', data, log.data_offset+i*log.row_size+log.headers['LapDistPct']['offset'], .99+.009*i/120)
        self.path.write_bytes(data)
        run = analyze(self.path)
        self.assertFalse(run['laps'][0]['complete'])
        self.assertEqual(run['summary']['usable_laps'], 2)

    def test_invalid_offsets_rejected(self):
        data = bytearray(self.path.read_bytes())
        struct.pack_into('<i', data, 52, 0)
        self.path.write_bytes(data)
        with self.assertRaises(TelemetryError):
            IBT(self.path)

    def test_incident_pit_and_gap_laps_excluded(self):
        for kwargs, reason in [({'incident': True}, 'Incident counter increased'),
                               ({'pit': True}, 'Pit lane'), ({'gap': True}, 'Telemetry gap')]:
            with self.subTest(reason=reason):
                make_log(self.path, **kwargs)
                run = analyze(self.path)
                self.assertEqual(run['summary']['usable_laps'], 2)
                self.assertIn(reason, run['laps'][1]['reasons'])

    def test_manual_exclusion_changes_summary(self):
        run = analyze(self.path)
        run['notes']['excluded_laps'] = [0, 1]
        self.assertEqual(summary(run)['usable_laps'], 1)
        self.assertIsNone(summary(run)['stdev'])

    def test_conditions_warn_without_weather_correction(self):
        a = analyze(self.path)
        a['id'] = 'a'*32
        make_log(self.path, track_temp=36)
        b = analyze(self.path)
        b['id'] = 'b'*32
        result = compare(b, a)
        self.assertEqual(result['median_delta'], 0)
        self.assertTrue(any('Track temp differs' in w for w in result['warnings']))

    def test_feedback_never_becomes_measured_understeer(self):
        run = analyze(self.path)
        run['notes']['feedback'] = 'Sustained midcorner understeer in fast corners'
        item = next(v for v in findings(run) if v['kind'] == 'driver hypothesis')
        self.assertIn('do not establish', item['hypothesis'])
        self.assertIn('one increment softer', item['experiment'])
        run['metadata']['fixed_setup'] = True
        item = next(v for v in findings(run) if v['kind'] == 'driver hypothesis')
        self.assertNotIn('one increment softer', item['experiment'])


class StartupTests(unittest.TestCase):
    def test_running_server_revision_controls_reuse(self):
        for status, expected, opens in [
            ({'product': 'stint', 'code_revision': server.SERVER_CODE_REVISION}, 0, True),
            ({'product': 'stint'}, 1, False),
            ({'product': 'stint', 'code_revision': 'old', 'server_pid': 1234}, 1, False),
            ({'product': 'another-app'}, None, False),
        ]:
            with self.subTest(status=status):
                response = io.BytesIO(json.dumps(status).encode())
                with patch.object(server.request, 'urlopen', return_value=response), \
                     patch.object(server.webbrowser, 'open') as browser, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(server.check_existing_server(8765, True), expected)
                    self.assertEqual(browser.called, opens)
                    if expected == 1:
                        self.assertIn('older Stint server', output.getvalue())

    def test_second_launch_does_not_interrupt_jobs_or_start_another_server(self):
        with patch('sys.argv', ['server.py', '--open']), \
             patch.object(server, 'check_existing_server', return_value=1), \
             patch.object(server, 'get_jobs') as jobs, \
             patch.object(server, 'ThreadingHTTPServer') as http:
            self.assertEqual(server.main(), 1)
            jobs.assert_not_called()
            http.assert_not_called()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.patch = patch.object(server, 'DATA', Path(cls.temp.name) / 'data')
        cls.patch.start()
        cls.key_patch = patch.object(server, 'API_KEY', '')
        cls.key_patch.start()
        cls.provider_patch = patch.object(server, 'AI_PROVIDER', 'api')
        cls.provider_patch.start()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.log = Path(cls.temp.name) / 'test.ibt'
        make_log(cls.log)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.patch.stop()
        cls.key_patch.stop()
        cls.provider_patch.stop()
        cls.temp.cleanup()

    def call(self, path, body=None, raw=None, headers=None):
        if body is not None:
            raw=json.dumps(body).encode()
        req = request.Request(self.base+path, data=raw, headers=headers or {})
        with request.urlopen(req) as response:
            return json.load(response)

    def test_full_import_save_reload_compare_export_loop(self):
        first=self.call('/api/upload?name=baseline.ibt', raw=self.log.read_bytes())['id']
        second=self.call('/api/upload?name=experiment.ibt', raw=self.log.read_bytes())['id']
        self.call('/api/notes', {'id':second, 'notes':{'feedback':'More confidence', 'objective':'hotlap', 'changes':'ARB 4 → 3'}})
        reloaded=self.call('/api/run?id='+second)
        self.assertEqual(reloaded['notes']['changes'], 'ARB 4 → 3')
        self.assertEqual(reloaded['summary']['usable_laps'],3)
        comparison=self.call(f'/api/compare?current={second}&baseline={first}')
        self.assertEqual(comparison['median_delta'],0)
        report=self.call('/api/export?id='+second)
        self.assertNotIn('traces',report['laps'][0])

    def test_origin_check(self):
        with self.assertRaises(error.HTTPError) as exc:
            self.call('/api/notes', {}, headers={'Origin':'https://hostile.example'})
        self.assertEqual(exc.exception.code,403)
        exc.exception.close()

    def test_remove_import_preserves_shared_recording_and_other_session(self):
        first = self.call('/api/upload?name=first.ibt', raw=self.log.read_bytes())['id']
        second = self.call('/api/upload?name=second.ibt', raw=self.log.read_bytes())['id']
        run = server.load_run(first)
        self.call('/api/notes', {'id': first, 'notes': {'feedback': 'Saved feedback'}})
        self.assertTrue(self.call('/api/remove-run', {'id': first})['removed'])
        self.assertNotIn(first, [r['id'] for r in self.call('/api/runs')])
        with self.assertRaisesRegex(ValueError, 'Run not found'):
            server.load_run(first)
        self.assertEqual(server.load_run(second)['summary']['usable_laps'], 3)
        archive = server.DATA/'removed-runs'/first
        with server.gzip.open(archive/(first+'.json.gz'), 'rt', encoding='utf-8') as f:
            self.assertEqual(json.load(f)['notes']['feedback'], 'Saved feedback')
        self.assertTrue((archive/(first+'.json')).exists())
        retained = server.DATA/'recordings'/(run['recording']['sha256']+'.ibt')
        self.assertEqual(retained.read_bytes(), self.log.read_bytes())

    def test_remove_rejects_bad_ids_and_active_investigation_dependencies(self):
        id = self.call('/api/upload?name=active.ibt', raw=self.log.read_bytes())['id']
        for bad in ['../index', None, 'f'*32]:
            with self.assertRaises(error.HTTPError) as exc:
                self.call('/api/remove-run', {'id': bad})
            self.assertEqual(exc.exception.code, 400)
            exc.exception.close()
        jobs = server.get_jobs()
        job_id = 'e'*32
        server.atomic_json(jobs._path(job_id), {'run_id': 'a'*32, 'status': 'running',
                                             'evidence_manifest': {'recordings': {id: {}}}})
        jobs.active[job_id] = threading.Event()
        try:
            with self.assertRaisesRegex(ValueError, 'active investigation'):
                server.remove_run(id)
            self.assertTrue(server.run_path(id).exists())
        finally:
            jobs.active.pop(job_id)
        self.assertTrue(server.remove_run(id)['removed'])

    def test_repeated_timestamps_import_through_both_paths(self):
        repeat = Path(self.temp.name)/'repeated.ibt'
        make_log(repeat, repeat_time=True)
        uploaded = self.call('/api/upload?name=repeated.ibt', raw=repeat.read_bytes())['id']
        with patch.object(server, 'resolve_local', return_value=repeat), patch.object(server.time, 'time', return_value=repeat.stat().st_mtime+10):
            local = self.call('/api/import-local', {'token': 'test'})['id']
        self.assertEqual(server.load_run(uploaded)['laps'], server.load_run(local)['laps'])
        self.assertEqual(server.load_run(local)['summary']['usable_laps'], 3)

    def test_library_retrieval_and_no_key_error(self):
        self.call('/api/library', {'title':'Front ARB', 'text':'Front antiroll setting affects roll stiffness.', 'source':'Private manual'})
        self.assertTrue(server.retrieve('front roll stiffness'))
        with self.assertRaisesRegex(ValueError,'not connected'):
            server.ai_reply({}, 'test')

    def test_ai_payload_uses_evidence_not_raw_traces_and_persists(self):
        id=self.call('/api/upload?name=ai.ibt',raw=self.log.read_bytes())['id']
        captured={}
        class FakeResponse:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'output':[{'content':[{'type':'output_text','text':'Measured evidence; repeat the baseline.'}]}]}).encode()
        def fake_urlopen(req, **kwargs):
            captured.update(json.loads(req.data))
            return FakeResponse()
        with patch.object(server,'API_KEY','test-key'), patch.object(server.request,'urlopen',fake_urlopen):
            reply=server.ai_reply(server.load_run(id),'What should I test?')
        self.assertIn('Measured',reply['answer'])
        self.assertFalse(captured['store'])
        self.assertNotIn('"traces"',captured['input'])
        self.assertEqual(len(server.load_run(id)['ai_history']),1)


if __name__ == '__main__':
    unittest.main()
