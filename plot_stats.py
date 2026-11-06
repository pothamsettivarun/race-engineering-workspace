"""Display statistics from retained samples, never from reduced chart traces."""
import math
import numpy as np
from analysis import CONDITIONS, TRACE_CHANNELS, eligible
from corner_map import key_for
from recordings import open_recording

DISPLAY = {
    'Speed': (3.6, 'km/h'), 'Throttle': (100, '%'), 'Brake': (100, '%'),
    'SteeringWheelAngle': (180 / math.pi, 'degrees'), 'YawRate': (180 / math.pi, 'degrees/s'),
    'LatAccel': (1, 'm/s²'), 'LongAccel': (1, 'm/s²'), 'FuelLevel': (1, 'L'),
    **{side + 'rideHeight': (1000, 'mm') for side in ['LF', 'RF', 'LR', 'RR']},
    **{name: (100 if name in ['RelativeHumidity', 'Precipitation'] else 1, unit)
       for name, (_, unit) in CONDITIONS.items()}, 'TimeDelta': (1, 's'), 'LapTime': (1, 's')}


def aggregate(values, *, categorical=False):
    values = np.asarray(values, dtype=np.float64)
    valid = values[np.isfinite(values)]
    return {'min': float(valid.min()) if len(valid) else None,
            'max': float(valid.max()) if len(valid) else None,
            'mean': float(valid.mean()) if len(valid) and not categorical else None,
            'valid_sample_count': int(len(valid)), 'invalid_sample_count': int(len(values) - len(valid))}


def calculate(data, run, args, reference_run=None):
    scope = args.get('scope', 'laps')
    if scope not in ['laps', 'session', 'stint']:
        raise ValueError('Choose laps, session, or stint statistics.')
    channels = args.get('channels', ['Speed'])
    allowed = set(TRACE_CHANNELS) | set(CONDITIONS) | {'TimeDelta', 'LapTime'}
    if not isinstance(channels, list) or not 1 <= len(channels) <= 16 or any(not isinstance(c, str) or c not in allowed for c in channels):
        raise ValueError('Choose 1–16 supported display channels.')
    distance = args.get('distance_range', [0, 100])
    if not isinstance(distance, list) or len(distance) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in distance) or not 0 <= distance[0] < distance[1] <= 100:
        raise ValueError('Distance range must increase within 0–100%.')
    ids = args.get('lap_ids', [])
    if not isinstance(ids, list) or len(ids) > 16 or any(isinstance(x, bool) or not isinstance(x, int) for x in ids):
        raise ValueError('Choose at most 16 lap IDs.')
    laps = {lap['id']: lap for lap in run['laps']}
    if any(i not in laps for i in ids):
        raise ValueError('Lap is not in this run.')
    if scope == 'laps' and not ids:
        raise ValueError('Choose at least one lap.')
    reference = args.get('reference_lap_id')
    reference_run = run if reference_run is None else reference_run
    reference_laps = {lap['id']: lap for lap in reference_run['laps']}
    if reference is not None and (isinstance(reference, bool) or not isinstance(reference, int) or reference not in reference_laps):
        raise ValueError('Reference lap is not in this run.')
    if 'TimeDelta' in channels and reference_run['id'] != run['id'] and (key_for(run)[0] == 'None' or key_for(run) != key_for(reference_run)):
        raise ValueError('Time delta requires the same track ID and configuration.')
    try:
        log = open_recording(data, run)
        recording_error = None
    except (ValueError, OSError) as exc:
        log, recording_error = None, str(exc)
    arrays = log.read_samples([name for name in dict.fromkeys(['LapDistPct', 'SessionTime', *channels])
                               if name in log.headers and log.headers[name]['count'] == 1]) if log else {}
    rows = []

    def row(channel, lap=None):
        result = {'channel': channel, 'lap_id': lap['id'] if lap else None,
                  'lap_number': lap['number'] if lap else None, 'unit': DISPLAY[channel][1],
                  'distance_range': distance if scope == 'laps' else None,
                  'min': None, 'max': None, 'mean': None, 'valid_sample_count': 0,
                  'invalid_sample_count': 0, 'available': False, 'provenance': None}
        if lap:
            result['eligible'] = lap['eligible'] and lap['id'] not in run['notes'].get('excluded_laps', [])
        rows.append(result)
        return result

    if scope == 'stint':
        if channels != ['LapTime']:
            raise ValueError('Stint statistics support LapTime only.')
        result = row('LapTime')
        selected = eligible(run)
        durations = []
        for lap in selected:
            if log:
                start, stop = lap['sample_start'], lap['sample_stop']
                durations.append(float(arrays['SessionTime'][stop] - arrays['SessionTime'][start]))
            else:
                durations.append(lap['duration'])
        result.update(aggregate(durations), available=bool(durations),
                      provenance={'method': 'eligible lap elapsed-time measurements',
                                  'original_samples': bool(log), 'lap_ids': [l['id'] for l in selected],
                                  'recording_sha256': run.get('recording', {}).get('sha256') if log else None})
        return {'run_id': run['id'], 'scope': scope, 'rows': rows}

    for lap in ([laps[i] for i in dict.fromkeys(ids)] if scope == 'laps' else [None]):
        for channel in dict.fromkeys(channels):
            result = row(channel, lap)
            if not log:
                result['reason'] = recording_error
                continue
            if channel == 'LapTime' or (scope == 'session' and channel == 'TimeDelta'):
                result['reason'] = 'Channel is not supported in this scope.'
                continue
            start, stop = (lap['sample_start'], lap['sample_stop']) if lap else (0, log.count)
            positions = np.arange(start, stop)
            if lap:
                d = arrays['LapDistPct'][start:stop].astype(np.float64) * 100
                positions = positions[(d >= distance[0]) & (d <= distance[1])]
            result['provenance'] = {'method': 'finite original samples; arithmetic mean',
                                    'original_samples': True, 'recording_sha256': run['recording']['sha256'],
                                    'sample_start': start, 'sample_stop': stop, 'synthetic_finish_samples': False}
            if channel == 'TimeDelta':
                ref = reference_laps.get(reference)
                if not lap or not lap['complete'] or not ref or not ref['complete']:
                    result['reason'] = 'Select two complete laps for time delta.'
                    continue
                rs, re = ref['sample_start'], ref['sample_stop']
                try:
                    ref_log = log if reference_run['id'] == run['id'] else open_recording(data, reference_run)
                    ref_arrays = ref_log.read_samples(['LapDistPct', 'SessionTime'], rs, re)
                except (ValueError, OSError) as exc:
                    result['reason'] = str(exc)
                    continue
                rd = ref_arrays['LapDistPct'].astype(np.float64)
                rt = ref_arrays['SessionTime'].astype(np.float64) - float(ref_arrays['SessionTime'][0])
                if len(rd) < 2 or np.any(np.diff(rd) < 0) or np.any(np.diff(rt) < 0):
                    result['reason'] = 'Reference distance/time must be monotonic for interpolation.'
                    continue
                rd, unique = np.unique(rd, return_index=True)
                rt = rt[unique]
                d = arrays['LapDistPct'][positions].astype(np.float64)
                in_range = (d >= rd[0]) & (d <= rd[-1])
                positions, d = positions[in_range], d[in_range]
                j = np.searchsorted(rd, d, side='left')
                left = np.maximum(0, j - 1)
                # Exact recorded reference points remain usable; do not bridge telemetry gaps.
                gap = (rt[j] - rt[left] > max(.5, 3 / ref_log.tick_rate)) & (rd[j] != d)
                values = arrays['SessionTime'][positions].astype(np.float64) - arrays['SessionTime'][start] - np.interp(d, rd, rt)
                values[gap] = np.nan
                result['provenance'].update(method='selected original timestamps minus distance-aligned reference elapsed time',
                                            reference_lap_id=reference, reference_run_id=reference_run['id'],
                                            reference_recording_sha256=reference_run['recording']['sha256'],
                                            interpolation='linear reference distance interpolation, no extrapolation; gaps excluded')
            else:
                if channel not in arrays or log.headers[channel]['count'] != 1:
                    result['reason'] = 'Channel unavailable in original recording.'
                    continue
                values = arrays[channel][positions].astype(np.float64) * DISPLAY[channel][0]
            result.update(aggregate(values, categorical=channel == 'TrackWetness'))
            result['available'] = result['valid_sample_count'] > 0
            if not result['available']:
                result['reason'] = 'No finite original samples in this range.'
    return {'run_id': run['id'], 'scope': scope, 'rows': rows}
