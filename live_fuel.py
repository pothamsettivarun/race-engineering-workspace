"""Observed lap fuel evidence and fuel-only projections for each calculation basis."""
from __future__ import annotations

import math
import statistics


def finite(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def eligible(lap):
    return bool(lap.get('eligible') and (finite(lap.get('duration')) or 0) > 0
                and (finite(lap.get('fuel_used')) or 0) > 0)


def empty_totals():
    return {'sample_count': 0, 'fuel_litres': 0.0, 'duration_seconds': 0.0}


def add_lap(totals, lap):
    if eligible(lap):
        totals['sample_count'] += 1
        totals['fuel_litres'] += lap['fuel_used']
        totals['duration_seconds'] += lap['duration']


def totals_from(laps):
    totals = empty_totals()
    for lap in laps: add_lap(totals, lap)
    return totals


def session_totals(laps, totals=None):
    observed = totals_from(laps)
    if (isinstance(totals, dict) and type(totals.get('sample_count')) is int
            and totals['sample_count'] >= observed['sample_count']
            and all(finite(totals.get(key)) is not None and totals[key] >= observed[key]-1e-7
                    for key in ('fuel_litres', 'duration_seconds'))
            and (totals['sample_count'] == 0 and totals['fuel_litres'] == totals['duration_seconds'] == 0
                 or totals['sample_count'] > 0 and totals['fuel_litres'] > 0 and totals['duration_seconds'] > 0)):
        return {key: totals[key] for key in observed}
    return observed


def bases(laps, totals=None):
    valid = [lap for lap in laps if eligible(lap)][-5:]
    latest = laps[-1] if laps else None
    last = latest if latest and eligible(latest) else None
    totals = session_totals(laps, totals)
    count = totals['sample_count']
    return [
        {'id': 'last_lap', 'label': 'Last lap', 'sample_count': int(last is not None),
         'lap_number': latest.get('number') if latest else None,
         'burn_litres': last['fuel_used'] if last else None, 'pace_seconds': last['duration'] if last else None,
         'note': ('Lap '+str(last.get('number', '')) if last else
                  'Last lap excluded: '+', '.join(latest.get('reasons') or ['invalid fuel evidence']) if latest else
                  'Waiting for one clean complete lap.')},
        {'id': 'session', 'label': 'Session average', 'sample_count': count,
         'burn_litres': totals['fuel_litres']/count if count else None,
         'pace_seconds': totals['duration_seconds']/count if count else None,
         'note': f'{count} eligible observed lap'+('' if count == 1 else 's')},
        {'id': 'rolling', 'label': 'Last 5 median', 'sample_count': len(valid),
         'burn_litres': statistics.median(l['fuel_used'] for l in valid) if len(valid) >= 3 else None,
         'pace_seconds': statistics.median(l['duration'] for l in valid) if len(valid) >= 3 else None,
         'note': f'{len(valid)}/5 eligible laps; at least 3 required.'},
    ]


def race_target(session, plan, progress, pace):
    if progress is None: return None, ''
    if plan['race_laps'] is not None: return plan['race_laps'], 'Manual total race-lap target.'
    elapsed = finite(session.get('elapsed_seconds')) or 0
    if plan['race_minutes'] is not None:
        return ((progress+math.ceil(max(0, plan['race_minutes']*60-elapsed)/pace)+1) if pace else None,
                'Manual race duration plus one finish lap; future pit loss is not credited as fuel saving.')
    finish = None
    if session.get('session_type', '').lower() == 'race':
        laps = finite(session.get('laps_total'))
        remaining = finite(session.get('time_remaining_seconds'))
        if laps and 0 < laps < 10000: finish = laps
        if pace and remaining is not None and 0 <= remaining < 86400:
            timed = progress+math.ceil(remaining/pace)+1
            return min(finish, timed) if finish is not None else timed, 'Timed finish includes one additional lap; pit loss is not credited as fuel saving.'
    return finish, ''


def project(row, state, plan, capacity):
    player, session = state.get('player', {}), state.get('session', {})
    fuel = finite(player.get('FuelLevel'))
    completed, pct = finite(player.get('LapCompleted')), finite(player.get('LapDistPct'))
    progress = completed+pct if completed is not None and pct is not None and 0 <= pct <= 1 else None
    burn, pace = row['burn_litres'], row['pace_seconds']
    result = {**row, 'laps_to_empty': None, 'laps_available': None, 'seconds_available': None,
              'reserve_litres': None, 'latest_safe_pit_lap': None, 'remaining_laps': None,
              'fuel_to_finish': None, 'additional_fuel_to_finish': None,
              'pit_loads': None, 'minimum_stops': None}
    if state.get('status') != 'connected' or session.get('replay') or session.get('electric'): return result
    if not burn or burn <= 0 or fuel is None or fuel < 0: return result
    reserve = burn*plan['reserve_laps']
    available = max(0, fuel/burn-plan['reserve_laps'])
    result.update(laps_to_empty=fuel/burn, laps_available=available, reserve_litres=reserve,
                  seconds_available=available*pace if pace else None,
                  latest_safe_pit_lap=math.floor(progress+available) if progress is not None else None)
    finish, _ = race_target(session, plan, progress, pace)
    if finish is None or finish <= progress or session.get('finished'): return result
    remaining = max(0, finish-progress)
    required = remaining*burn+reserve
    addition = max(0, required-fuel)
    result.update(remaining_laps=remaining, fuel_to_finish=required, additional_fuel_to_finish=addition)
    usable = capacity-reserve if capacity is not None else None
    if addition == 0: result.update(pit_loads=0.0, minimum_stops=0)
    elif usable is not None and usable > 0:
        loads = addition/usable
        result.update(pit_loads=loads, minimum_stops=math.ceil(loads-1e-9))
    return result
