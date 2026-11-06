"""Deterministic, explicitly conditional fuel and race-plan calculations."""
from __future__ import annotations

import copy
import math
import statistics
from live_fuel import bases as fuel_bases, project as project_fuel, race_target


DEFAULT_PLAN = {'reserve_laps': 1.0, 'fuel_basis': 'last_lap', 'race_laps': None, 'race_minutes': None,
                'pace_seconds': None, 'burn_litres': None, 'capacity_litres': None,
                'pit_loss_seconds': None, 'refuel_lps': None, 'tire_seconds': None,
                'driver_change_seconds': None, 'service_mode': 'concurrent',
                'drivers': [], 'scenarios': [], 'active_scenario': None}
LIMITS = {'reserve_laps': (0, 10), 'race_laps': (1, 10000), 'race_minutes': (1, 1440),
          'pace_seconds': (1, 3600), 'burn_litres': (.001, 200), 'capacity_litres': (.01, 500),
          'pit_loss_seconds': (0, 3600), 'refuel_lps': (.001, 100),
          'tire_seconds': (0, 3600), 'driver_change_seconds': (0, 3600)}


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def validate_plan(value):
    if not isinstance(value, dict):
        raise ValueError('Race plan must be an object.')
    result = copy.deepcopy(DEFAULT_PLAN)
    for key, (low, high) in LIMITS.items():
        v = value.get(key, result[key])
        if v is not None and (number(v) is None or not low <= v <= high):
            raise ValueError(f'{key.replace("_", " ")} must be between {low} and {high}.')
        if key == 'race_laps' and v is not None and int(v) != v:
            raise ValueError('Race laps must be a whole number.')
        result[key] = v
    if result['reserve_laps'] is None:
        raise ValueError('Enter a fuel reserve, including zero if appropriate.')
    if result['race_laps'] is not None and result['race_minutes'] is not None:
        raise ValueError('Choose a lap target or a time target, not both.')
    result['service_mode'] = value.get('service_mode', 'concurrent')
    if result['service_mode'] not in ('concurrent', 'sequential'):
        raise ValueError('Choose concurrent or sequential pit service.')
    result['fuel_basis'] = value.get('fuel_basis', 'last_lap')
    if result['fuel_basis'] not in ('last_lap', 'session', 'rolling'):
        raise ValueError('Choose last lap, session average, or last 5 median for fuel calculations.')
    drivers = value.get('drivers', [])
    if not isinstance(drivers, list) or len(drivers) > 20:
        raise ValueError('Enter at most 20 drivers.')
    ids = set()
    for d in drivers:
        if not isinstance(d, dict) or not isinstance(d.get('id'), str) or not 1 <= len(d['id']) <= 80 or d['id'] in ids:
            raise ValueError('Driver identifiers must be unique text.')
        ids.add(d['id'])
        clean = {'id': d['id'], 'name': str(d.get('name') or d['id'])[:120]}
        for key in ('available_from_minutes', 'available_until_minutes', 'max_stint_minutes', 'min_drive_minutes'):
            v = d.get(key)
            if v is not None and (number(v) is None or not 0 <= v <= 1440):
                raise ValueError('Driver times must be between 0 and 1440 minutes after race start.')
            clean[key] = v
        if clean['available_from_minutes'] is not None and clean['available_until_minutes'] is not None and clean['available_from_minutes'] > clean['available_until_minutes']:
            raise ValueError('Driver availability ends before it starts.')
        result['drivers'].append(clean)
    scenarios = value.get('scenarios', [])
    if not isinstance(scenarios, list) or len(scenarios) > 12:
        raise ValueError('Enter at most 12 scenarios.')
    scenario_ids = set()
    for s in scenarios:
        if not isinstance(s, dict) or not isinstance(s.get('id'), str) or not 1 <= len(s['id']) <= 80 or s['id'] in scenario_ids:
            raise ValueError('Scenario identifiers must be unique text.')
        scenario_ids.add(s['id'])
        stops = s.get('stops', [])
        if not isinstance(stops, list) or len(stops) > 100:
            raise ValueError('Enter at most 100 stops per scenario.')
        clean = {'id': s['id'], 'name': str(s.get('name') or s['id'])[:120], 'stops': []}
        for stop in stops:
            if not isinstance(stop, dict):
                raise ValueError('Each stop must be an object.')
            lap, fuel = stop.get('lap'), stop.get('fuel_litres')
            if number(lap) is None or lap != int(lap) or not 1 <= lap <= 10000:
                raise ValueError('Stop laps must be whole numbers from 1 to 10000.')
            if number(fuel) is None or not 0 <= fuel <= 500:
                raise ValueError('Stop fuel must be between 0 and 500 litres.')
            if type(stop.get('tires', False)) is not bool:
                raise ValueError('Tire service must be true or false.')
            driver = stop.get('driver')
            if driver is not None and driver not in ids:
                raise ValueError('A stop references a driver outside the roster.')
            clean['stops'].append({'lap': int(lap), 'fuel_litres': fuel, 'tires': stop.get('tires', False), 'driver': driver})
        result['scenarios'].append(clean)
    active = value.get('active_scenario')
    if active is not None and (not isinstance(active, str) or len(active) > 80):
        raise ValueError('Invalid active scenario.')
    result['active_scenario'] = active
    return result


def measurements(laps):
    valid = [l for l in laps if l.get('eligible') and number(l.get('duration')) and number(l.get('fuel_used')) and l['fuel_used'] > 0][-5:]
    return {'sample_count': len(valid),
            'pace_seconds': statistics.median(l['duration'] for l in valid) if len(valid) >= 3 else None,
            'burn_litres': statistics.median(l['fuel_used'] for l in valid) if len(valid) >= 3 else None}


def service_time(stop, changed_driver, plan):
    missing = []
    lane = number(plan['pit_loss_seconds'])
    if lane is None: missing.append('pit-lane time loss')
    fuel = 0.0
    if stop['fuel_litres'] > 0:
        if plan['refuel_lps'] is None: missing.append('refueling rate')
        else: fuel = stop['fuel_litres'] / plan['refuel_lps']
    tires = 0.0
    if stop['tires']:
        if plan['tire_seconds'] is None: missing.append('tire-service duration')
        else: tires = plan['tire_seconds']
    driver = 0.0
    if changed_driver:
        if plan['driver_change_seconds'] is None: missing.append('driver-change duration')
        else: driver = plan['driver_change_seconds']
    if missing: return None, missing
    service = max(fuel, tires, driver) if plan['service_mode'] == 'concurrent' else fuel + tires + driver
    return lane + service, []


def simulate(scenario, ctx, plan):
    """Stop lap means service after completing that absolute race lap."""
    fuel, burn, pace, capacity = ctx['fuel'], ctx['burn'], ctx['pace'], ctx['capacity']
    progress, finish = ctx['progress'], ctx['finish']
    reserve = burn * plan['reserve_laps']
    errors, missing, timeline, drive_times = [], [], [], {}
    t = ctx['elapsed']
    pit_loss = 0.0
    current_driver = ctx.get('driver')
    stint_start = ctx.get('driver_stint_start', t)
    roster = {d['id']: d for d in plan['drivers']}
    drive_times.update(ctx.get('driver_times', {}))
    if plan['capacity_litres'] is not None and ctx.get('sdk_capacity') is not None and capacity > ctx['sdk_capacity'] + .01:
        errors.append('Entered fuel capacity exceeds the simulator limit.')
    if fuel > capacity + .01: errors.append('Current fuel exceeds the entered tank capacity.')
    minimum_margin = fuel - reserve
    remaining_stops = [s for s in scenario['stops'] if s['lap'] > progress]
    if [s['lap'] for s in scenario['stops']] != sorted(set(s['lap'] for s in scenario['stops'])):
        errors.append('Stop laps must be strictly increasing.')

    def drive(end):
        nonlocal t, fuel, progress, minimum_margin
        duration = max(0, end - progress) * pace
        start = t
        t += duration
        fuel -= max(0, end - progress) * burn
        minimum_margin = min(minimum_margin, fuel - reserve)
        if fuel < reserve - 1e-6: errors.append(f'Fuel reserve breached by lap {end:g}.')
        if current_driver:
            drive_times[current_driver] = drive_times.get(current_driver, 0) + duration
            d = roster.get(current_driver)
            if d:
                if d['available_from_minutes'] is not None and start < d['available_from_minutes'] * 60: errors.append(f'{d["name"]} is not available at stint start.')
                if d['available_until_minutes'] is not None and t > d['available_until_minutes'] * 60: errors.append(f'{d["name"]} is unavailable before stint end.')
                if d['max_stint_minutes'] is not None and t - stint_start > d['max_stint_minutes'] * 60 + 1e-6: errors.append(f'{d["name"]} exceeds the maximum continuous stint.')
            elif roster:
                errors.append('Assign the current driver to the roster before validating driver rules.')
        progress = end

    for stop in remaining_stops:
        if stop['lap'] >= finish:
            errors.append(f'Stop on lap {stop["lap"]} is at or after the finish.')
            continue
        drive(stop['lap'])
        before = fuel
        fuel += stop['fuel_litres']
        if fuel > capacity + 1e-6: errors.append(f'Stop on lap {stop["lap"]} exceeds fuel capacity.')
        changed = bool(stop.get('driver') and stop['driver'] != current_driver)
        loss, needed = service_time(stop, changed, plan)
        missing.extend(needed)
        entry = {'lap': stop['lap'], 'arrival_seconds': t, 'fuel_before': before,
                 'fuel_after': fuel, 'loss_seconds': loss, 'driver': stop.get('driver') or current_driver,
                 'tires': stop['tires'], 'fuel_litres': stop['fuel_litres']}
        timeline.append(entry)
        if loss is not None: t += loss; pit_loss += loss
        if changed: current_driver = stop['driver']; stint_start = t
    drive(finish)
    for d in roster.values():
        if d['min_drive_minutes'] is not None and drive_times.get(d['id'], 0) < d['min_drive_minutes'] * 60:
            errors.append(f'{d["name"]} has insufficient total driving time.')
    # Driver availability also depends on missing pit timing; never claim it was validated.
    if missing and roster: missing.append('complete pit timing to validate driver windows')
    return {**copy.deepcopy(scenario), 'feasible': not errors, 'errors': list(dict.fromkeys(errors)),
            'missing_inputs': list(dict.fromkeys(missing)), 'timeline': timeline,
            'finish_fuel': fuel, 'minimum_margin_litres': minimum_margin,
            'pit_loss_seconds': pit_loss if not missing else None,
            'projected_completion_seconds': t if not missing else None,
            'driver_times': drive_times, 'rank': None}


def suggestions(ctx, plan):
    """Bounded integer-lap candidates; feasibility is checked by the simulator."""
    burn, capacity = ctx['burn'], ctx['capacity']
    reserve = burn * plan['reserve_laps']
    progress, finish = ctx['progress'], ctx['finish']
    first = math.floor(progress + max(0, (ctx['fuel'] - reserve) / burn) + 1e-8)
    full = math.floor((capacity - reserve) / burn + 1e-8)
    if full < 1 or first <= progress: return []
    stops, lap = [], first
    while lap < finish and len(stops) < 100:
        stops.append(lap)
        lap += full
    if lap < finish: return []
    minimum = len(stops)
    candidates = [('auto-long', 'Longest fuel stints', stops)]
    for count, label, ident in [(minimum, 'Evenly spaced stops', 'auto-even'), (minimum + 1, 'One additional stop', 'auto-extra')]:
        if count > 100: continue
        points = [math.floor(progress + (finish-progress)*i/(count+1)) for i in range(1, count+1)]
        if len(set(points)) != len(points) or any(p <= progress or p >= finish for p in points): continue
        candidates.append((ident, label, points))
    result = []
    for ident, label, points in candidates:
        fuel, previous, entries = ctx['fuel'], progress, []
        for i, lap in enumerate(points):
            fuel -= (lap-previous)*burn
            next_lap = points[i+1] if i+1 < len(points) else finish
            addition = max(0, (next_lap-lap)*burn + reserve - fuel)
            entries.append({'lap': lap, 'fuel_litres': addition, 'tires': False, 'driver': None})
            fuel += addition; previous = lap
        result.append({'id': ident, 'name': label, 'stops': entries, 'suggested': True})
    return result


def calculate(state, plan):
    measured = measurements(state.get('laps', []))
    bases = fuel_bases(state.get('laps', []), state.get('fuel_totals'))
    basis = next(b for b in bases if b['id'] == plan.get('fuel_basis', 'last_lap'))
    pace = plan['pace_seconds'] or basis['pace_seconds']
    burn = plan['burn_litres'] or basis['burn_litres']
    player, session = state.get('player', {}), state.get('session', {})
    fuel = number(player.get('FuelLevel'))
    sdk_capacity = number(session.get('fuel_capacity_litres'))
    capacity = plan['capacity_litres'] or sdk_capacity
    permitted_capacity = min(capacity, sdk_capacity) if sdk_capacity is not None and capacity is not None else capacity
    if plan['pace_seconds'] or plan['burn_litres']:
        basis = {**basis, 'id': 'manual', 'label': 'Plan override', 'pace_seconds': pace, 'burn_litres': burn,
                 'note': 'Manual plan values override the selected measured basis.'}
        bases.append(basis)
    rows = [project_fuel(b, state, plan, permitted_capacity) for b in bases]
    selected = next(r for r in rows if r['id'] == basis['id'])
    pct = number(player.get('LapDistPct'))
    completed = number(player.get('LapCompleted'))
    progress = completed + pct if completed is not None and pct is not None and 0 <= pct <= 1 else None
    elapsed = number(session.get('elapsed_seconds')) or 0
    result = {'measurements': measured, 'pace_seconds': pace, 'burn_litres': burn,
              'pace_source': 'manual' if plan['pace_seconds'] else basis['label'],
              'burn_source': 'manual' if plan['burn_litres'] else basis['label'],
              'fuel_basis': basis['id'], 'fuel_basis_label': basis['label'], 'fuel_statistics': rows,
              'capacity_litres': capacity, 'permitted_capacity_litres': permitted_capacity,
              'reserve_litres': burn*plan['reserve_laps'] if burn else None,
              'laps_available': selected['laps_available'],
              'seconds_available': None, 'latest_safe_pit_lap': None, 'remaining_laps': None,
              'fuel_to_finish': None, 'additional_fuel_to_finish': None, 'scenarios': [],
              'message': '', 'assumptions': ['Constant representative pace and fuel burn; no weather, tire degradation, or rival-strategy model.',
                                           'Driver durations cover observed running only. Uncovered intervals and driving before collection are not credited.',
                                           'Suggested fuel plans retain the current driver; edit assignments to satisfy roster constraints.']}
    if result['laps_available'] is not None and pace:
        result['seconds_available'] = result['laps_available']*pace
        if progress is not None: result['latest_safe_pit_lap'] = math.floor(progress+result['laps_available'])
    if state.get('status') != 'connected' or session.get('replay'):
        result['message'] = ('Replay: predictions suspended. Return to live driving to calculate fuel range.'
                             if session.get('replay') else 'Predictions suspended until fresh live telemetry returns.')
        for key in ('laps_available', 'seconds_available', 'latest_safe_pit_lap'): result[key] = None
        return result
    if session.get('electric'):
        result['message'] = 'Fuel strategy in litres is unavailable for electric cars.'
        for key in ('burn_litres', 'reserve_litres', 'laps_available', 'seconds_available', 'latest_safe_pit_lap'): result[key] = None
        return result
    if not pace or not burn or fuel is None or progress is None:
        result['message'] = ('Collect three eligible complete laps for the last 5 median, or choose last lap / session average.'
                             if basis['id'] == 'rolling' else basis['note']+' Enter manual pace and fuel burn, or choose another basis in Edit race plan.')
        return result
    finish, target_note = race_target(session, plan, progress, pace)
    if target_note: result['assumptions'].append(target_note)
    if finish is None:
        result['message'] = 'Enter a race-lap or duration target for practice/testing.'
        return result
    if finish <= progress or session.get('finished'):
        result['message'] = 'Race target reached.'
        return result
    result['remaining_laps'] = finish-progress
    result['fuel_to_finish'] = (finish-progress)*burn + burn*plan['reserve_laps']
    result['additional_fuel_to_finish'] = max(0, result['fuel_to_finish']-fuel)
    if not capacity:
        result['message'] = 'Enter the permitted fuel capacity to compare stops.'
        return result
    ctx = {'fuel': fuel, 'burn': burn, 'pace': pace, 'capacity': capacity, 'sdk_capacity': sdk_capacity,
           'progress': progress, 'finish': finish, 'elapsed': elapsed,
           'driver': session.get('driver_id'), 'driver_times': state.get('driver_times', {}),
           'driver_stint_start': session.get('driver_stint_start_seconds', elapsed)}
    candidates = suggestions(ctx, plan) + plan['scenarios']
    result['scenarios'] = [simulate(s, ctx, plan) for s in candidates]
    ranked = sorted((s for s in result['scenarios'] if s['feasible'] and not s['missing_inputs']), key=lambda s: s['projected_completion_seconds'])
    for rank, s in enumerate(ranked, 1): s['rank'] = rank
    result['message'] = 'Estimated strategies; review the inputs before making a race call.'
    if not result['scenarios']: result['message'] = 'No suggested fuel plan is feasible. Enter a manual scenario or review fuel and reserve.'
    return result


def rejoin(snapshot, scenario):
    if not scenario.get('feasible') or scenario.get('missing_inputs') or not scenario.get('timeline'): return None
    stop = scenario['timeline'][0]
    pace = snapshot['strategy'].get('pace_seconds')
    own_idx = snapshot['session'].get('player_car_idx')
    cars = snapshot.get('cars', [])
    own = next((c for c in cars if c['car_idx'] == own_idx), None)
    if not own or not pace or own.get('progress') is None or stop['loss_seconds'] is None: return None
    horizon = stop['arrival_seconds'] - snapshot['session']['elapsed_seconds'] + stop['loss_seconds']
    if horizon < 0: return None
    own_progress = stop['lap']
    projected = []
    for c in cars:
        if c['car_idx'] == own_idx or c.get('progress') is None or c.get('on_pit_road') or c.get('track_surface', -1) < 0: continue
        car_pace = c.get('observed_pace_seconds')
        if not car_pace: continue
        projected.append({**c, 'projected_progress': c['progress']+horizon/car_pace})
    if not projected: return None
    same_class = [c for c in projected if c['class_id'] == own['class_id']]
    nearest = sorted(projected, key=lambda c: abs(((c['projected_progress']-own_progress+.5)%1)-.5))[:4]
    return {'estimated': True, 'lap': stop['lap'], 'horizon_seconds': horizon,
            'class_position': 1+sum(c['projected_progress'] > own_progress for c in same_class),
            'observed_class_cars': len(same_class)+1,
            'nearby': [{'car_idx': c['car_idx'], 'number': c['number'],
                        'gap_seconds': ((c['projected_progress']-own_progress+.5)%1-.5)*pace} for c in nearest],
            'caveat': 'Observed cars with usable pace only; assumes no rival stops, traffic losses, or pace changes.'}
