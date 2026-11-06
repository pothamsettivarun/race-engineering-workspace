"""Independent fuel arithmetic, evidence exclusions, and durable bounded aggregates."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live import LiveService
from live_fuel import bases, totals_from, session_totals
from live_strategy import DEFAULT_PLAN, calculate, validate_plan
from test_live import sample


def lap(number, burn=1.15, pace=60, eligible=True, reasons=None):
    return {'number': number, 'fuel_used': burn, 'duration': pace,
            'eligible': eligible, 'reasons': reasons or []}


class FuelTests(unittest.TestCase):
    def setUp(self):
        self.plan = copy.deepcopy(DEFAULT_PLAN)
        self.state = {'status': 'connected', 'player': {'FuelLevel': 51.8, 'LapCompleted': 10, 'LapDistPct': .25},
                      'session': {'session_type': 'Practice', 'fuel_capacity_litres': 83.3, 'elapsed_seconds': 625},
                      'laps': [lap(10)]}

    def rows(self, result):
        return {row['id']: row for row in result['fuel_statistics']}

    def test_one_lap_range_works_in_testing_without_target(self):
        result = calculate(self.state, self.plan)
        row = self.rows(result)['last_lap']
        self.assertEqual(result['burn_litres'], 1.15)
        self.assertAlmostEqual(row['laps_to_empty'], 45.04347826087)
        self.assertAlmostEqual(result['laps_available'], 44.04347826087)
        self.assertAlmostEqual(result['seconds_available'], 2642.6086956522)
        self.assertEqual(result['latest_safe_pit_lap'], 54)
        self.assertIsNone(row['fuel_to_finish']); self.assertIsNone(row['pit_loads'])
        self.assertIsNone(self.rows(result)['rolling']['burn_litres'])
        self.assertIn('target', result['message'])

    def test_finish_fuel_reserve_fractional_loads_and_minimum(self):
        self.plan['race_laps'] = 200
        result = calculate(self.state, self.plan)
        row = self.rows(result)['last_lap']
        # 189.75 laps * 1.15 + 1.15 reserve = 219.3625 L; 167.5625 L to add.
        self.assertAlmostEqual(result['fuel_to_finish'], 219.3625)
        self.assertAlmostEqual(row['additional_fuel_to_finish'], 167.5625)
        self.assertAlmostEqual(row['pit_loads'], 2.03971393791844)  # / (83.3 - 1.15)
        self.assertEqual(row['minimum_stops'], 3)
        self.state['player']['FuelLevel'] = 300
        self.assertEqual(self.rows(calculate(self.state, self.plan))['last_lap']['minimum_stops'], 0)

    def test_each_basis_drives_full_strategy(self):
        self.state['laps'] = [lap(8, 1, 60), lap(9, 2, 70), lap(10, 3, 80)]
        self.plan['race_laps'] = 20
        for basis, burn, pace, required in [('last_lap', 3, 80, 32.25), ('session', 2, 70, 21.5), ('rolling', 2, 70, 21.5)]:
            self.plan['fuel_basis'] = basis
            result = calculate(self.state, self.plan)
            self.assertEqual(result['fuel_basis'], basis)
            self.assertEqual(result['burn_litres'], burn); self.assertEqual(result['pace_seconds'], pace)
            self.assertAlmostEqual(result['fuel_to_finish'], required)
        self.state['laps'] = [lap(i, burn, 60) for i, burn in enumerate([9, 1, 1, 1, 1, 3], 1)]
        rows = self.rows(calculate(self.state, self.plan))
        self.assertAlmostEqual(rows['session']['burn_litres'], 16/6)
        self.assertEqual(rows['rolling']['burn_litres'], 1); self.assertEqual(rows['last_lap']['burn_litres'], 3)

    def test_latest_excluded_lap_is_not_replaced_with_older_lap(self):
        self.state['laps'].extend([lap(11, -20, eligible=False, reasons=['refueled']),
                                   lap(12, 2, eligible=False, reasons=['pit lane'])])
        result = calculate(self.state, self.plan); rows = self.rows(result)
        self.assertIsNone(rows['last_lap']['burn_litres']); self.assertIsNone(result['laps_available'])
        self.assertIn('pit lane', rows['last_lap']['note'])
        self.assertEqual(rows['session']['sample_count'], 1)
        self.assertEqual(rows['session']['burn_litres'], 1.15)
        for reason in ['off track', 'incident', 'discontinuity', 'replay', 'partial initial lap']:
            self.assertEqual(bases([lap(1), lap(2, 20, eligible=False, reasons=[reason])])[1]['sample_count'], 1)

    def test_timed_race_finishes_have_extra_lap_for_each_basis(self):
        self.state['session'].update(session_type='Race', time_remaining_seconds=125, laps_total=32767)
        row = self.rows(calculate(self.state, self.plan))['last_lap']
        self.assertEqual(row['remaining_laps'], 4)  # ceil(125/60) + finish lap
        self.assertAlmostEqual(row['fuel_to_finish'], 5.75)
        self.state['session']['session_type'] = 'Practice'; self.plan['race_minutes'] = 15
        row = self.rows(calculate(self.state, self.plan))['last_lap']
        self.assertEqual(row['remaining_laps'], 6)  # ceil((900 - 625)/60) + finish lap
        self.assertAlmostEqual(row['fuel_to_finish'], 8.05)

    def test_predictions_pause_but_consumption_evidence_remains(self):
        self.plan['race_laps'] = 200
        for mode in ['stale', 'waiting', 'replay', 'electric']:
            state = copy.deepcopy(self.state)
            if mode in ('stale', 'waiting'): state['status'] = mode
            else: state['session'][mode] = True
            result = calculate(state, self.plan)
            for row in result['fuel_statistics']:
                for key in ['laps_to_empty', 'laps_available', 'seconds_available', 'fuel_to_finish', 'pit_loads', 'minimum_stops']:
                    self.assertIsNone(row[key], (mode, key))
            self.assertEqual(self.rows(result)['last_lap']['burn_litres'], 1.15)
            self.assertFalse(result['scenarios'])
        state = copy.deepcopy(self.state); state['session']['replay'] = True
        self.assertIn('Replay', calculate(state, self.plan)['message'])

    def test_manual_overrides_remain_explicit_and_preserve_observations(self):
        self.plan.update(burn_litres=2, pace_seconds=90, race_laps=20)
        result = calculate(self.state, self.plan); rows = self.rows(result)
        self.assertEqual(result['fuel_basis'], 'manual')
        self.assertEqual(rows['manual']['burn_litres'], 2)
        self.assertAlmostEqual(rows['manual']['laps_to_empty'], 25.9)
        self.assertEqual(rows['last_lap']['burn_litres'], 1.15)
        self.assertAlmostEqual(result['fuel_to_finish'], 21.5)

    def test_missing_capacity_progress_fuel_and_reached_target(self):
        self.plan['race_laps'] = 200
        self.state['session']['fuel_capacity_litres'] = None
        row = self.rows(calculate(self.state, self.plan))['last_lap']
        self.assertIsNotNone(row['laps_to_empty']); self.assertIsNone(row['pit_loads'])
        self.state['player']['LapDistPct'] = -1
        row = self.rows(calculate(self.state, self.plan))['last_lap']
        self.assertIsNotNone(row['laps_to_empty']); self.assertIsNone(row['fuel_to_finish'])
        self.state['player'].update(LapDistPct=.25, FuelLevel=None)
        self.assertIsNone(self.rows(calculate(self.state, self.plan))['last_lap']['laps_to_empty'])
        self.state['player']['FuelLevel'] = 51.8; self.plan['race_laps'] = 10
        self.assertIsNone(self.rows(calculate(self.state, self.plan))['last_lap']['fuel_to_finish'])

    def test_impossible_reserve_and_sdk_capacity_limit(self):
        self.plan.update(race_laps=200, capacity_litres=100)
        row = self.rows(calculate(self.state, self.plan))['last_lap']
        self.assertAlmostEqual(row['pit_loads'], 2.03971393791844)
        self.state['session']['fuel_capacity_litres'] = 1
        self.assertIsNone(self.rows(calculate(self.state, self.plan))['last_lap']['pit_loads'])

    def test_basis_validation_and_invalid_evidence(self):
        self.assertEqual(validate_plan({})['fuel_basis'], 'last_lap')
        for basis in ['last_lap', 'session', 'rolling']:
            self.assertEqual(validate_plan({'fuel_basis': basis})['fuel_basis'], basis)
        for basis in [None, 'unknown', 5]:
            with self.assertRaises(ValueError): validate_plan({'fuel_basis': basis})
        for burn, pace in [(0, 60), (-1, 60), (float('nan'), 60), (1, float('inf')), (True, 60)]:
            self.assertIsNone(bases([lap(1, burn, pace)])[0]['burn_litres'])
        self.assertEqual(session_totals([lap(1)], {'sample_count': 5, 'fuel_litres': 0, 'duration_seconds': 0}), totals_from([lap(1)]))


class FuelCollectorTests(unittest.TestCase):
    def test_observed_session_average_survives_retention_restart_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [0.]
            live = LiveService(Path(directory)/'data', clock=lambda: clock[0])
            try:
                # The first lap is partial. An early 4 L lap then 2,050 1 L laps.
                live.ingest(sample())
                with patch.object(live, '_journal'):
                    for i in range(1, 2053):
                        clock[0] = i*60; live.last_time = clock[0]
                        live._player_lap(sample(clock[0], lap=i, fuel=5000-i-(3 if i >= 2 else 0))['values'])
                self.assertEqual(len(live.laps), 2048)
                self.assertEqual(live.fuel_totals['sample_count'], 2051)
                self.assertEqual(live.fuel_totals['fuel_litres'], 2054)
                row = next(r for r in live.snapshot()['strategy']['fuel_statistics'] if r['id'] == 'session')
                self.assertAlmostEqual(row['burn_litres'], 2054/2051)
                ident = live.record['id']; live.close()
                resumed = LiveService(live.data, clock=lambda: clock[0]+1)
                try:
                    resumed.ingest(sample(clock[0]+1, lap=2052, fuel=2945))
                    self.assertEqual(resumed.record['id'], ident)
                    self.assertEqual(resumed.fuel_totals, live.fuel_totals)
                    resumed.ingest(sample(clock[0]+2, lap=0, session=2))
                    self.assertEqual(resumed.fuel_totals['sample_count'], 0)
                finally: resumed.close()
            finally: live.close()

    def test_refueling_and_duplicate_frames_do_not_change_session_average(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [0.]; live = LiveService(directory, clock=lambda: clock[0])
            try:
                for tick in range(301):
                    t = tick/20; clock[0] = t
                    fuel = 30-.4*t if t < 12 else 50-.4*(t-12)
                    live.ingest(sample(t, lap=int(t/5), pct=t%5/5, fuel=fuel))
                totals = copy.deepcopy(live.fuel_totals)
                self.assertEqual(totals['sample_count'], 1); self.assertEqual(totals['fuel_litres'], 2)
                self.assertFalse(live.laps[-1]['eligible'])
                live.ingest(sample(15, lap=3, fuel=48.8))
                self.assertEqual(live.fuel_totals, totals)
            finally: live.close()


if __name__ == '__main__': unittest.main()
