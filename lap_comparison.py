"""Cross-recording lap comparison; measurements always use retained native samples."""
import copy
import json
import math
import uuid
from pathlib import Path

import numpy as np

from analysis import TRACE_CHANNELS, corners
from corner_map import key_for, map_for, profile_key
from live_maps import get_map
from recordings import open_recording


def validate_corners(items):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('Enter at most 100 corner zones.')
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Each corner needs a label, start, and end.')
        start, end = item.get('start'), item.get('end')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)):
            raise ValueError('Corner boundaries must be finite numbers.')
        if not 0 <= start < 100 or not 0 < end <= 100 or start == end:
            raise ValueError('Corner boundaries must be within 0–100%; start > end crosses the finish.')
        label = item.get('label', '')
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError('Corner labels need 1–80 characters.')
        result.append({'label': label.strip(), 'start': float(start), 'end': float(end)})
    occupied = []
    for item in result:
        for a, b in ranges(item):
            if any(max(a, x) < min(b, y) for x, y in occupied):
                raise ValueError('Corner zones must not overlap. Adjust their boundaries first.')
            occupied.append((a, b))
    return sorted(result, key=lambda c: c['start'])


def ranges(corner):
    a, b = corner['start'], corner['end']
    return [[a, b]] if a < b else [[a, 100.0], [0.0, b]]


def partition(items):
    """A full-lap partition, with finish-crossing pieces grouped into one row."""
    items = validate_corners(items)
    segments, occupied = [], []
    for i, c in enumerate(items):
        pieces = ranges(c)
        segments.append({'id': f'corner-{i}', 'kind': 'corner', 'label': c['label'], 'ranges': pieces})
        occupied.extend(pieces)
    cursor, gaps = 0.0, []
    for a, b in sorted(occupied):
        if a > cursor:
            gaps.append([cursor, a])
        cursor = b
    if cursor < 100:
        gaps.append([cursor, 100.0])
    if len(gaps) > 1 and gaps[0][0] == 0 and gaps[-1][1] == 100:
        gaps = [[*gaps[-1], *gaps[0]], *gaps[1:-1]]
    for i, g in enumerate(gaps):
        pieces = [g[:2], g[2:]] if len(g) == 4 else [g]
        segments.append({'id': f'straight-{i}', 'kind': 'straight', 'label': f'Straight {i + 1}', 'ranges': pieces})
    return sorted(segments, key=lambda s: s['ranges'][0][0])


def confirmed_corners(profile):
    # Named complexes retain the circuit's official turn numbering and still
    # receive their own measured comparison row (for example the Bus Stop).
    items = sorted(copy.deepcopy(profile['corners'] + profile.get('comparison_sections', [])),
                   key=lambda c: c['reference'])
    for left, right in zip(items, items[1:]):
        if left['end'] > right['start']:
            boundary = (left['reference'] + right['reference']) / 2
            left['end'] = right['start'] = boundary
    return validate_corners([{'label': c['label'], 'start': c['start'], 'end': c['end']} for c in items])


def estimated_corners(run, lap, samples):
    # Reuse the established detection rules, but supply all original points.
    traces = {'distance': (samples['LapDistPct'] * 100).tolist(),
              'Speed': (samples['Speed'] * 3.6).tolist()}
    for name, factor in [('Brake', 100), ('SteeringWheelAngle', 180 / math.pi)]:
        if name in samples:
            traces[name] = np.where(np.isfinite(samples[name]), samples[name] * factor, 0).tolist()
    detected = corners({'laps': [dict(lap, traces=traces)], 'notes': run.get('notes', {})}, lap['id'])
    merged = []
    for c in detected:
        if merged and c['start'] <= merged[-1]['end']:
            merged[-1]['end'] = max(merged[-1]['end'], c['end'])
        else:
            merged.append({'start': c['start'], 'end': c['end']})
    if len(merged) > 1 and merged[0]['start'] == 0 and merged[-1]['end'] == 100:
        merged = [{'start': merged[-1]['start'], 'end': merged[0]['end']}, *merged[1:-1]]
    for i, c in enumerate(merged):
        c['label'] = f'Estimated corner {i + 1}'
    return validate_corners(merged)


def segment_path(data, run):
    return Path(data) / 'comparison-segments' / (profile_key(run) + '.json')


def save_segments(data, run, body, write):
    path = segment_path(data, run)
    if body.get('reset'):
        path.unlink(missing_ok=True)
        return {'reset': True}
    record = {'track_id': key_for(run)[0], 'configuration': key_for(run)[1],
              'revision': 'comparison-' + uuid.uuid4().hex[:12],
              'corners': validate_corners(body.get('corners'))}
    write(path.parent / (record['revision'] + '.json'), record)
    write(path, record)
    return record


def read_lap(data, run, lap_id):
    if type(lap_id) is not int:
        raise ValueError('Select a lap from each recording.')
    lap = next((l for l in run['laps'] if l['id'] == lap_id), None)
    if not lap or not lap['complete']:
        raise ValueError('Select complete laps from both recordings.')
    log = open_recording(data, run)
    start, stop = lap['sample_start'], lap['sample_stop']
    names = ['SessionTime', 'LapDistPct', *TRACE_CHANNELS, 'Lat', 'Lon', 'OnPitRoad']
    names = [n for n in dict.fromkeys(names) if n in log.headers and log.headers[n]['count'] == 1]
    a = log.read_samples(names, start, stop)
    finish = log.read_samples(['SessionTime', 'LapDistPct'], stop, stop + 1)
    t = a['SessionTime'].astype(float) - float(a['SessionTime'][0])
    d = a['LapDistPct'].astype(float) * 100
    duration = float(finish['SessionTime'][0]) - float(a['SessionTime'][0])
    gap = max(.5, 3 / log.tick_rate)
    if (len(t) < 2 or not np.isfinite(t).all() or not np.isfinite(d).all()
            or np.any(np.diff(t) < 0) or np.any(np.diff(t) > gap)
            or np.any(np.diff(d) < 0) or np.any(np.diff(d) > 50)
            or not 0 <= d[0] < 3 or not 97 < d[-1] < 100
            or not 0 <= float(finish['LapDistPct'][0]) * 100 < 3
            or not 0 <= duration - t[-1] <= gap):
        raise ValueError('Lap distance/time is discontinuous or runs backwards; choose another lap.')
    # Importer lap timing starts at the first recorded sample, ending at the next lap sample.
    # Anchor those same timestamps at 0/100% so segment times reconcile to the stored lap time.
    ud, ids = np.unique(d, return_index=True)
    ut = t[ids]
    if ud[0] == 0:
        ud, ut = ud[1:], ut[1:]
    curve_d, curve_t = np.r_[0., ud, 100.], np.r_[0., ut, duration]
    flags = list(lap.get('reasons', []))
    if lap_id in run.get('notes', {}).get('excluded_laps', []):
        flags.append('Manually excluded')
    return {'run_id': run['id'], 'lap_id': lap_id, 'number': lap['number'], 'duration': duration,
            'metadata': run['metadata'], 'flags': flags, 'lap': lap, 'samples': a,
            'distance': curve_d, 'time': curve_t, 'raw_distance': d, 'raw_time': t,
            'gap': gap, 'sha256': run['recording']['sha256']}


def measurement(lap, pieces):
    a, d, t = lap['samples'], lap['raw_distance'], lap['raw_time']
    mask = np.zeros(len(d), dtype=bool)
    weights = np.zeros(len(d), dtype=float)
    interval_end = np.r_[t[1:], lap['duration']]
    elapsed = 0.0
    for lo, hi in pieces:
        begin, end = np.interp([lo, hi], lap['distance'], lap['time'])
        elapsed += end - begin
        mask |= (d >= lo) & (d < hi if hi < 100 else d <= hi)
        weights += np.maximum(0, np.minimum(interval_end, end) - np.maximum(t, begin))
    result = {'elapsed_s': float(elapsed), 'sample_count': int(mask.sum())}
    for key, channel, factor, mode in [('min_speed_kph', 'Speed', 3.6, 'min'),
                                      ('max_speed_kph', 'Speed', 3.6, 'max'),
                                      ('max_brake_pct', 'Brake', 100, 'max')]:
        values = a.get(channel, np.full(len(d), np.nan))[mask].astype(float) * factor
        values = values[np.isfinite(values)]
        result[key] = float(getattr(values, mode)()) if len(values) else None
    for key, target in [('entry_speed_kph', pieces[0][0]), ('exit_speed_kph', pieces[-1][1])]:
        result[key] = float(np.interp(target, np.r_[0, d, 100], np.r_[a['Speed'][0], a['Speed'], a['Speed'][-1]]) * 3.6)
    brake, throttle = a.get('Brake'), a.get('Throttle')
    result['braking_s'] = float(weights[np.isfinite(brake) & (brake > .05)].sum()) if brake is not None and np.isfinite(brake[weights > 0]).all() else None
    result['full_throttle_s'] = float(weights[np.isfinite(throttle) & (throttle > .95)].sum()) if throttle is not None and np.isfinite(throttle[weights > 0]).all() else None
    result['coasting_s'] = (float(weights[(brake < .02) & (throttle < .02)].sum())
                            if brake is not None and throttle is not None and
                            np.isfinite(brake[weights > 0]).all() and np.isfinite(throttle[weights > 0]).all() else None)
    return result


def trajectory(lap):
    a = lap['samples']
    if not {'Lat', 'Lon'} <= a.keys():
        return None
    lat, lon, t = a['Lat'].astype(float), a['Lon'].astype(float), lap['raw_time']
    valid = np.isfinite(lat) & np.isfinite(lon) & (abs(lat) > 1) & (abs(lat) <= 90) & (abs(lon) > 1) & (abs(lon) <= 180)
    if 'OnPitRoad' in a:
        valid &= ~a['OnPitRoad'].astype(bool)
    ids = np.flatnonzero(valid)
    if len(ids) < 30 or len(ids) < .95 * len(lat):
        return None
    if np.ptp(lat[ids]) * 111320 < 20 and np.ptp(lon[ids]) * 111320 < 20:
        return None
    dt = np.diff(t[ids])
    meters = np.hypot(np.diff(lat[ids]) * 111320, np.diff(lon[ids]) * 111320 * math.cos(math.radians(float(np.median(lat[ids])))))
    if np.any(dt > lap['gap']) or np.any(meters > dt * 150 + 3):
        return None
    # Retain native positions for segment zoom. This geometry is display-only.
    return [{'distance': float(lap['raw_distance'][i]), 'lat': float(lat[i]), 'lon': float(lon[i])} for i in ids]


def display(lap):
    d = lap['raw_distance']
    ids = np.unique(np.linspace(0, len(d) - 1, min(2400, len(d)), dtype=int))
    traces = {'distance': [0., *d[ids].tolist(), 100.],
              'time': [0., *lap['raw_time'][ids].tolist(), lap['duration']]}
    for name in TRACE_CHANNELS:
        if name in lap['samples']:
            factor = 3.6 if name == 'Speed' else 100 if name in ('Brake', 'Throttle') else 180 / math.pi if name in ('SteeringWheelAngle', 'YawRate') else 1000 if 'rideHeight' in name else 1
            v = lap['samples'][name][ids].astype(float) * factor
            vals = [float(x) if math.isfinite(x) else None for x in v]
            traces[name] = [vals[0], *vals, vals[-1]]
    return {**{k: lap[k] for k in ('run_id', 'lap_id', 'number', 'duration', 'flags', 'sha256')},
            'metadata': {k: lap['metadata'].get(k) for k in ('car', 'track', 'configuration')}, 'traces': traces}


def calculate(data, selected, reference, body):
    if not key_for(selected)[0] or key_for(selected)[0] == 'None' or key_for(selected) != key_for(reference):
        raise ValueError('Choose recordings from the same track ID and configuration.')
    a = read_lap(data, selected, body.get('selected_lap_id'))
    b = read_lap(data, reference, body.get('reference_lap_id'))
    warnings = [f'{label}: {flag}' for label, lap in [('Selected', a), ('Reference', b)] for flag in lap['flags']]
    if selected['metadata'].get('car_id') != reference['metadata'].get('car_id') or selected['metadata'].get('car') != reference['metadata'].get('car'):
        warnings.append('Different cars: pace and input differences may reflect car performance.')
    if selected['id'] == reference['id']:
        warnings.append('Both laps are from the same recording.')
    warnings.append('Review fuel, tires, traffic, weather, and track conditions before interpreting differences.')
    path = segment_path(data, selected)
    if 'corners' in body:
        zones, provenance = validate_corners(body['corners']), {'source': 'draft', 'revision': None}
    elif path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if (str(saved.get('track_id')), saved.get('configuration')) != key_for(selected):
            raise ValueError('Saved comparison map does not match this configuration. Reset its map.')
        zones, provenance = validate_corners(saved['corners']), {'source': 'saved comparison map', 'revision': saved['revision']}
    else:
        mapping = map_for(data, reference)
        if mapping['status'] == 'validated':
            zones, provenance = confirmed_corners(mapping['profile']), {'source': 'confirmed corner profile', 'revision': mapping['profile']['revision']}
        else:
            zones = estimated_corners(reference, b['lap'], b['samples'])
            provenance = {'source': 'automatic estimated zones', 'revision': None, 'reference_run_id': reference['id'], 'reference_lap_id': b['lap_id']}
    if not zones:
        warnings.append('No corner zones detected. Add corners in Edit segments; the full lap is currently one straight segment.')
    rows = partition(zones)
    for row in rows:
        row['selected'], row['reference'] = measurement(a, row['ranges']), measurement(b, row['ranges'])
        row['delta_s'] = row['selected']['elapsed_s'] - row['reference']['elapsed_s']
        row['differences'] = {k: row['selected'][k] - row['reference'][k] if row['selected'][k] is not None and row['reference'][k] is not None else None
                              for k in row['selected'] if k != 'sample_count'}
    grid = np.linspace(0, 100, 2001)
    outline = get_map(data, *key_for(selected))
    return {'selected': display(a), 'reference': display(b), 'delta_s': a['duration'] - b['duration'],
            'delta_trace': {'distance': grid.tolist(), 'values': (np.interp(grid, a['distance'], a['time']) - np.interp(grid, b['distance'], b['time'])).tolist()},
            'corners': zones, 'segments': rows, 'provenance': provenance, 'warnings': warnings,
            'map': {'selected': trajectory(a), 'reference': trajectory(b), 'outline': outline['points'], 'display_only': True},
            'timing_method': 'Original timestamps, linearly aligned by lap distance; first/next-lap timestamps anchored at 0/100%.'}
