import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import server
from telemetry import IBT
from plot_stats import calculate
from test_workspace import make_log

class PlotStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'sample.ibt';make_log(self.path)
        self.data=self.root/'data'
        with patch.object(server,'DATA',self.data):self.run=server.import_run(self.path)
        self.log=IBT(self.path)
    def tearDown(self):self.temp.cleanup()
    def request(self,**kwargs):
        return calculate(self.data,self.run,{'lap_ids':[0,1],'channels':['Speed','Throttle','Brake'],**kwargs})
    def test_original_range_precision_and_both_laps(self):
        rows=self.request(distance_range=[20,35])['rows']
        for row in rows:
            lap=self.run['laps'][row['lap_id']];start,stop=lap['sample_start'],lap['sample_stop']
            distances=self.log.channels['LapDistPct'][start:stop].astype(np.float64)*100
            mask=(distances>=20)&(distances<=35)
            factor=3.6 if row['channel']=='Speed' else 100
            original=self.log.channels[row['channel']][start:stop].astype(np.float64)[mask]*factor
            self.assertEqual(row['valid_sample_count'],len(original))
            self.assertEqual(row['min'],float(original.min()))
            self.assertEqual(row['max'],float(original.max()))
            self.assertEqual(row['mean'],float(original.mean()))
            self.assertFalse(row['provenance']['synthetic_finish_samples'])
        self.assertEqual(len(rows),6)
    def test_finish_is_not_synthetic_and_empty_range(self):
        full=self.request(lap_ids=[0],channels=['Speed'])['rows'][0]
        self.assertEqual(full['valid_sample_count'],120)
        empty=self.request(lap_ids=[0],channels=['Speed'],distance_range=[99.99,100])['rows'][0]
        self.assertFalse(empty['available']);self.assertIsNone(empty['mean'])
    def test_signed_angle_units_missing_channel_and_exclusion(self):
        self.run['notes']['excluded_laps']=[0]
        angle,missing=self.request(lap_ids=[0],channels=['SteeringWheelAngle','YawRate'])['rows']
        values=self.log.channels['SteeringWheelAngle'][:120].astype(np.float64)*(180/math.pi)
        self.assertEqual(angle['mean'],float(values.mean()));self.assertFalse(angle['eligible'])
        self.assertFalse(missing['available'])
    def test_native_delta_and_categorical_condition(self):
        row=self.request(lap_ids=[0],channels=['TimeDelta'],reference_lap_id=1)['rows'][0]
        self.assertEqual(row['min'],0);self.assertEqual(row['max'],0);self.assertEqual(row['mean'],0)
        self.assertEqual(row['valid_sample_count'],120);self.assertIn('interpolation',row['provenance'])
        wet=self.request(scope='session',channels=['TrackWetness'])['rows'][0]
        self.assertEqual(wet['valid_sample_count'],361);self.assertIsNone(wet['mean'])
        self.assertEqual(wet['min'],1)
    def test_stint_exclusions_and_no_original_fallback_for_channels(self):
        self.run['notes']['excluded_laps']=[0]
        row=self.request(scope='stint',channels=['LapTime'])['rows'][0]
        self.assertEqual(row['valid_sample_count'],2);self.assertEqual(row['mean'],60)
        self.run.pop('recording')
        row=self.request(lap_ids=[0],channels=['Speed'])['rows'][0]
        self.assertFalse(row['available']);self.assertIn('summary-only',row['reason'])
    def test_time_delta_accepts_repeated_reference_timestamps(self):
        make_log(self.path, repeat_time=True)
        with patch.object(server, 'DATA', self.data):self.run=server.import_run(self.path)
        row=self.request(lap_ids=[0],channels=['TimeDelta'],reference_lap_id=1)['rows'][0]
        self.assertTrue(row['available']);self.assertEqual(row['valid_sample_count'],120)
        self.assertAlmostEqual(row['max'],.5)
        self.assertAlmostEqual(row['min'],-.5)
    def test_invalid_requests_and_nonfinite_aggregation(self):
        from plot_stats import aggregate
        self.assertEqual(aggregate([1,np.nan,np.inf,3]),{'min':1.0,'max':3.0,'mean':2.0,'valid_sample_count':2,'invalid_sample_count':2})
        for body in [{'distance_range':[50,20]},{'distance_range':[0,float('nan')]},{'lap_ids':[99]},{'channels':['SecretFile']},{'reference_lap_id':False}]:
            with self.assertRaises(ValueError):self.request(**body)

if __name__=='__main__':unittest.main()
