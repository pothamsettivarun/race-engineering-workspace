"""Measured findings and conservative, testable engineering hypotheses."""
from __future__ import annotations
import math
import statistics
import numpy as np
from telemetry import IBT, finite, flatten_setup

TRACE_CHANNELS = ['Speed', 'Throttle', 'Brake', 'SteeringWheelAngle', 'YawRate', 'LatAccel', 'LongAccel',
                  'FuelLevel', 'LFrideHeight', 'RFrideHeight', 'LRrideHeight', 'RRrideHeight']
CONDITIONS = {'AirTemp': ('air_temp', '°C'), 'TrackTemp': ('track_temp', '°C'),
              'TrackTempCrew': ('track_temp_crew', '°C'), 'WindVel': ('wind_speed', 'm/s'),
              'WindDir': ('wind_direction', 'rad'), 'RelativeHumidity': ('humidity', '%'),
              'Precipitation': ('precipitation', '%'), 'TrackWetness': ('wetness', 'category')}


def percentile(values, p):
    values = np.asarray(values)
    values = values[np.isfinite(values)]
    return finite(np.percentile(values, p)) if len(values) else None


def lap_metrics(ch, start, stop):
    speed = ch['Speed'][start:stop]
    dt = np.diff(ch['SessionTime'][start:stop], append=ch['SessionTime'][stop])
    dt = np.clip(dt, 0, 1)
    moving = speed > 5
    result = {'max_speed_kph': percentile(speed * 3.6, 100)}
    for name, label, factor in [('SteeringWheelAngle', 'steering_p95_deg', 180 / math.pi),
                                 ('LatAccel', 'lateral_p95_g', 1 / 9.80665),
                                 ('YawRate', 'yaw_p95_deg_s', 180 / math.pi)]:
        if name in ch:
            result[label] = percentile(np.abs(ch[name][start:stop][moving]) * factor, 95)
    if 'Brake' in ch and 'Throttle' in ch:
        brake, throttle = ch['Brake'][start:stop], ch['Throttle'][start:stop]
        coast = (brake < .02) & (throttle < .02) & moving
        result['coast_seconds'] = finite(np.sum(dt[coast]))
        result['brake_seconds'] = finite(np.sum(dt[(brake > .05) & moving]))
        result['overlap_seconds'] = finite(np.sum(dt[(brake > .1) & (throttle > .1) & moving]))
        result['full_throttle_pct'] = finite(np.mean(throttle[moving] > .95) * 100) if moving.any() else None
    if 'FuelLevel' in ch:
        result['fuel_start_l'] = finite(ch['FuelLevel'][start])
        result['fuel_end_l'] = finite(ch['FuelLevel'][stop - 1])
        result['fuel_used_l'] = finite(ch['FuelLevel'][start] - ch['FuelLevel'][stop - 1])
    for name in ['AirTemp', 'TrackTemp', 'TrackTempCrew', 'WindVel', 'TrackWetness']:
        if name in ch:
            result[name] = percentile(ch[name][start:stop], 50)
    return result


def analyze(path):
    log = IBT(path)
    ch = log.channels
    time, dist, lap = ch['SessionTime'], ch['LapDistPct'], ch['Lap']
    session = ch.get('SessionNum', np.zeros(len(time)))
    # The SDK can record distinct samples with the same SessionTime. Keep their
    # original indices and values; only a backwards clock is a discontinuity.
    splits = np.flatnonzero((np.diff(lap) != 0) | (np.diff(session) != 0) |
                            (np.diff(time) < 0) | (np.diff(dist) < -.5)) + 1
    bounds = [0, *splits.tolist(), len(time)]
    laps = []
    for start, stop in zip(bounds[:-1], bounds[1:]):
        if stop - start < 3:
            continue
        reasons = []
        p = dist[start:stop]
        ended = stop < len(time) and lap[stop] == lap[start] + 1 and session[stop] == session[start]
        started = 0 <= p[0] < .03 and (start == 0 or (
            dist[start - 1] > .97 and lap[start] == lap[start - 1] + 1 and
            session[start] == session[start - 1] and time[start] >= time[start - 1]))
        complete = (started and ended and 0 <= dist[stop] < .03 and
                    p[-1] > .97 and np.all(np.diff(p) >= -.002) and
                    np.all(np.diff(p) <= .5) and time[stop] >= time[stop - 1])
        duration = float(time[stop] - time[start]) if ended else float(time[stop - 1] - time[start])
        if not complete:
            reasons.append('Partial lap / outlap / discontinuity')
        if duration < 15:
            reasons.append('Too short for a representative lap')
        if np.any(np.diff(time[start:stop]) > max(.5, 3 / log.tick_rate)):
            reasons.append('Telemetry gap')
        if 'OnPitRoad' in ch and np.any(ch['OnPitRoad'][start:stop] > 0):
            reasons.append('Pit lane')
        if 'PlayerTrackSurface' in ch and np.any(ch['PlayerTrackSurface'][start:stop] <= 0):
            reasons.append('Off track / not in world')
        if 'PlayerCarMyIncidentCount' in ch:
            incidents = ch['PlayerCarMyIncidentCount'][max(0, start - 1): min(len(time), stop + 1)]
            if np.any(np.diff(incidents) > 0):
                reasons.append('Incident counter increased')
        indexes = np.unique(np.linspace(start, stop - 1, min(1400, stop - start), dtype=int))
        traces = {'distance': [finite(v * 100) for v in dist[indexes]],
                  'time': [finite(v - time[start]) for v in time[indexes]]}
        for name in TRACE_CHANNELS:
            if name in ch:
                factor = 3.6 if name == 'Speed' else 100 if name in ['Throttle', 'Brake'] else 180 / math.pi if name in ['SteeringWheelAngle', 'YawRate'] else 1000 if 'rideHeight' in name else 1
                traces[name] = [finite(v * factor) for v in ch[name][indexes]]
        # Append the measured boundary for complete laps so time deltas reach the finish.
        if ended:
            traces['distance'].append(100.0)
            traces['time'].append(finite(duration))
            for name in TRACE_CHANNELS:
                if name in traces:
                    traces[name].append(traces[name][-1])
        laps.append({'id': len(laps), 'number': int(lap[start]), 'session': int(session[start]),
                     'sample_start': start, 'sample_stop': stop,
                     'duration': round(duration, 4), 'complete': bool(complete), 'eligible': not reasons,
                     'reasons': reasons, 'metrics': lap_metrics(ch, start, stop) if ended else {}, 'traces': traces})
    condition_data = {}
    indexes = np.unique(np.linspace(0, len(time) - 1, min(500, len(time)), dtype=int))
    for channel, (key, unit) in CONDITIONS.items():
        if channel in ch:
            factor = 100 if channel in ['RelativeHumidity', 'Precipitation'] else 1
            vals = ch[channel] * factor
            condition_data[key] = {'source': f'telemetry: {channel}', 'unit': unit,
                                   'start': finite(vals[0]), 'end': finite(vals[-1]),
                                   'min': percentile(vals, 0), 'max': percentile(vals, 100),
                                   'time': [finite(v - time[0]) for v in time[indexes]],
                                   'values': [finite(v) for v in vals[indexes]]}
    result = {'metadata': log.metadata(), 'laps': laps, 'conditions': condition_data,
              'notes': {'objective': 'endurance', 'vision': '', 'feedback': '', 'setup_label': '',
                        'setup_notes': '', 'changes': '', 'manual_conditions': '', 'excluded_laps': [],
                        'verdict': 'untested', 'verdict_notes': ''}}
    result['metadata']['setup_flat'] = flatten_setup(result['metadata']['setup'])
    result['summary'] = summary(result)
    return result


def eligible(run):
    excluded = run.get('notes', {}).get('excluded_laps', [])
    return [lap for lap in run['laps'] if lap['eligible'] and lap['id'] not in excluded]


def summary(run):
    laps = eligible(run)
    times = [l['duration'] for l in laps]
    return {'usable_laps': len(laps), 'total_laps': len(run['laps']),
            'best': min(times) if times else None, 'median': statistics.median(times) if times else None,
            'spread': max(times) - min(times) if times else None,
            'stdev': statistics.stdev(times) if len(times) > 1 else None,
            'best_lap_id': min(laps, key=lambda l: l['duration'])['id'] if laps else None}


def corners(run, lap_id=None):
    """Heuristic driving zones, deliberately not official numbered corners."""
    laps = eligible(run)
    selected = next((l for l in run['laps'] if l['id'] == lap_id), None) if lap_id is not None else None
    if selected is None:
        selected = min(laps, key=lambda l: l['duration']) if laps else None
    if not selected:
        return []
    tr = selected['traces']
    d = np.asarray(tr['distance'])
    steer = np.asarray([v or 0 for v in tr.get('SteeringWheelAngle', [0] * len(d))])
    brake = np.asarray([v or 0 for v in tr.get('Brake', [0] * len(d))])
    active = (np.abs(steer) > 18) | (brake > 10)
    ids = np.flatnonzero(active)
    if not len(ids):
        return []
    groups, group = [], [int(ids[0])]
    for i in ids[1:]:
        if i - group[-1] > 1 and d[i] - d[group[-1]] > .8:
            groups.append(group)
            group = []
        group.append(int(i))
    groups.append(group)
    result = []
    for group in groups:
        a, b = group[0], group[-1]
        if d[b] - d[a] < .5:
            continue
        speed = [v for v in tr['Speed'][a:b+1] if v is not None]
        result.append({'label': f'Zone {len(result) + 1}', 'start': round(max(0, float(d[a]) - .4), 2),
                       'end': round(min(100, float(d[b]) + .8), 2), 'min_speed': min(speed) if speed else None,
                       'lap_id': selected['id']})
    return result


def findings(run):
    s = summary(run)
    laps = eligible(run)
    notes = run['notes']
    items = []

    def add(title, evidence, hypothesis, experiment, success, kind='measured', zone=None):
        items.append(dict(title=title, evidence=evidence, hypothesis=hypothesis, experiment=experiment,
                          success=success, kind=kind, zone=zone))

    if not laps:
        add('Record a complete baseline', 'No complete laps passed the pit/off-track/incident checks.',
            'There is not enough clean evidence to evaluate setup performance.',
            'Drive an outlap followed by 4–6 clean timed laps. Stop logging before importing.',
            'At least three complete representative laps with the exact setup recorded.', 'data quality')
        return items
    if len(laps) < 3:
        add('Baseline needs more repetition', f'Only {len(laps)} eligible complete lap(s).',
            'A faster lap may reflect driving variation rather than a setup improvement.',
            'Repeat the same setup for 4–6 timed laps with comparable fuel and conditions.',
            'At least three clean laps and a repeatable feedback pattern.', 'data quality')
    else:
        add('Establish the pace envelope', f"Median {s['median']:.3f}s; sample standard deviation {s['stdev']:.3f}s across {len(laps)} eligible laps.",
            'This spread includes driving, fuel, tire, and condition effects; it is not a pure setup sensitivity measure.',
            'Use this run as baseline. Compare matched runs and repeat A/B/A if the gain is small.',
            'A repeatable gain beyond observed variation, with the intended handling benefit.')
    zones = corners(run)
    worst = max(laps, key=lambda l: l['metrics'].get('coast_seconds') or 0)
    coast = worst['metrics'].get('coast_seconds')
    if coast is not None and coast > 2:
        add('Inspect time with neither pedal applied', f"Lap {worst['number']} has {coast:.2f}s with brake and throttle below 2%, above 18 km/h.",
            'Coasting can be appropriate. Unintended hesitation or balance discomfort is another possible explanation.',
            'Select this lap and inspect pedal traces. Identify one repeated hesitation and repeat the same setup with a deliberate input.',
            'A clearer corner-phase diagnosis before changing chassis settings.')
    if notes.get('feedback'):
        feedback = notes['feedback'].lower()
        setup = run['metadata'].get('setup_flat', {})
        arb = next((k for k in setup if ('arb' in k.lower() or 'antiroll' in k.lower().replace(' ', ''))
                    and ('front' in k.lower() or 'chassis / front' in k.lower())
                    and any(t in k.lower() for t in ['setting', 'diameter', 'stiffness', 'blade', 'size'])), None)
        symptom = 'understeer' if any(v in feedback for v in ['understeer', 'push', 'wash']) else 'oversteer' if any(v in feedback for v in ['oversteer', 'loose', 'unstable', 'snap']) else None
        if symptom:
            change = 'Isolate the corner and phase first; repeat a matched baseline before choosing a chassis adjustment.'
            if arb and symptom == 'understeer' and not run['metadata'].get('fixed_setup'):
                change = (f'If the symptom is sustained midcorner understeer and the garage confirms this is adjustable, '
                          f'test one increment softer on {arb} (recorded: {setup[arb]}). Keep other settings fixed. '
                          'Verify the softer direction in the car manual; monitor platform control and rear stability.')
            add(f'Driver reports {symptom}', notes['feedback'],
                'Driver-reported hypothesis. These channels alone do not establish axle grip balance or a root cause.',
                change, 'Less of the reported symptom, improved confidence, and repeatable pace without a regression.',
                'driver hypothesis')
    if run['metadata'].get('fixed_setup'):
        add('Session reports a fixed setup', 'IsFixedSetup is enabled in session metadata.',
            'Some garage adjustments may be locked in this session.',
            'Use an open-setup test session for chassis experiments; check which controls are available.',
            'Confirm that the proposed adjustment can actually be applied.', 'constraint')
    setup_recorded = bool(run['metadata'].get('setup_flat') or notes.get('setup_notes'))
    if not setup_recorded:
        add('Record the actual setup', 'No decoded CarSetup snapshot or manually recorded settings.',
            'A setup name alone does not prove which settings were used.',
            'Paste the relevant garage settings and record the adjustable values in the engineering brief.',
            'Every A/B run has a traceable setup snapshot.', 'data quality')
    if notes.get('objective') == 'endurance':
        add('Validate across a representative stint', f"This log spans {run['metadata']['duration'] / 60:.1f} minutes.",
            'Short runs cannot establish late-stint tire behavior or fuel-dependent balance.',
            'After the short A/B test, drive a representative stint and report early, middle, and late-run confidence.',
            'Sustained median pace and confidence through the intended stint; do not infer tire wear without measurements.', 'objective')
    return items


def compare(current, baseline):
    warnings = []
    a, b = summary(current), summary(baseline)
    for field in ['car', 'track', 'configuration', 'build']:
        if current['metadata'].get(field) != baseline['metadata'].get(field):
            warnings.append(f'{field.replace("_", " ").capitalize()} differs.')
    if current['notes'].get('objective') != baseline['notes'].get('objective'):
        warnings.append('Development objectives differ.')
    if current['notes'].get('manual_conditions') or baseline['notes'].get('manual_conditions'):
        warnings.append('Review manually recorded conditions; free-text differences are not normalized.')
    for key, tolerance in [('track_temp', 2), ('air_temp', 2), ('wind_speed', 1), ('wetness', 0)]:
        x, y = current['conditions'].get(key), baseline['conditions'].get(key)
        if not x or not y or x['start'] is None or y['start'] is None:
            warnings.append(f'{key.replace("_", " ").capitalize()} is unavailable in one or both runs.')
        elif abs(x['start'] - y['start']) > tolerance:
            warnings.append(f'{key.replace("_", " ").capitalize()} differs: {y["start"]} → {x["start"]} {x["unit"]}.')
    for run in [baseline, current]:
        for key, tolerance in [('track_temp', 2), ('air_temp', 2), ('wind_speed', 1), ('wetness', 0)]:
            v = run['conditions'].get(key)
            if v and v['min'] is not None and v['max'] is not None and v['max'] - v['min'] > tolerance:
                warnings.append(f'{key.replace("_", " ").capitalize()} changed materially within a run.')
    ca, bl = eligible(current), eligible(baseline)
    fuels = [statistics.median([l['metrics']['fuel_start_l'] for l in ls if l['metrics'].get('fuel_start_l') is not None])
             if any(l['metrics'].get('fuel_start_l') is not None for l in ls) else None for ls in [ca, bl]]
    if None not in fuels and abs(fuels[0] - fuels[1]) > 3:
        warnings.append(f'Starting lap fuel differs: {fuels[1]:.1f} → {fuels[0]:.1f} L.')
    warnings.append('Tire age, traffic, and driver adaptation are not automatically controlled; confirm comparable runs.')
    flat_a, flat_b = current['metadata'].get('setup_flat', {}), baseline['metadata'].get('setup_flat', {})
    changes = [{'setting': k, 'before': flat_b.get(k), 'after': flat_a.get(k)}
               for k in sorted(set(flat_a) | set(flat_b)) if flat_a.get(k) != flat_b.get(k)]
    delta = a['median'] - b['median'] if a['median'] is not None and b['median'] is not None else None
    spread = max(a['stdev'] or 0, b['stdev'] or 0)
    if delta is None:
        verdict = 'Insufficient eligible laps to compare.'
    elif min(a['usable_laps'], b['usable_laps']) < 3:
        verdict = 'Preliminary pace difference only; record more clean laps.'
    elif abs(delta) <= spread:
        verdict = 'Pace difference is within observed lap variation. Repeat the experiment.'
    else:
        verdict = 'Observed pace is faster.' if delta < 0 else 'Observed pace is slower.'
        verdict += ' This does not establish that the setup caused the difference.'
    return {'current': a, 'baseline': b, 'median_delta': delta,
            'best_delta': a['best'] - b['best'] if a['best'] is not None and b['best'] is not None else None,
            'warnings': list(dict.fromkeys(warnings)), 'setup_changes': changes, 'verdict': verdict,
            'baseline_id': baseline['id'], 'current_id': current['id']}
