"""SDK collector, bounded live state, explicit pit commands, and durable race summaries."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from live_strategy import DEFAULT_PLAN, calculate, number, rejoin, validate_plan
from live_fuel import empty_totals, add_lap, session_totals
from live_maps import backfill, get_map
from pit_control import PitControl, PitError

PLAYER_CHANNELS = '''Lap LapCompleted LapDistPct LapCurrentLapTime LapLastLapTime LapBestLapTime
 Speed Throttle Brake SteeringWheelAngle Gear RPM FuelLevel FuelUsePerHour
 LapDeltaToBestLap LapDeltaToBestLap_OK EngineWarnings OilTemp WaterTemp OilPress FuelPress
 dcBrakeBias dcTractionControl dcABS dcPitSpeedLimiterToggle
 OnPitRoad PlayerTrackSurface PlayerCarMyIncidentCount PlayerCarTeamIncidentCount
 PlayerCarDriverIncidentCount PlayerCarInPitStall PlayerCarPitSvStatus PlayerCarTowTime
 PitstopActive PitsOpen PitSvFuel PitSvFlags PitSvTireCompound PitSvLFP PitSvRFP PitSvLRP PitSvRRP
 dpFuelAutoFillEnabled dpFuelAutoFillActive FastRepairAvailable FastRepairUsed PitRepairLeft PitOptRepairLeft
 IsOnTrack IsOnTrackCar'''.split()
CONDITION_CHANNELS = 'TrackTemp TrackTempCrew AirTemp WindVel WindDir RelativeHumidity Precipitation TrackWetness WeatherDeclaredWet'.split()
ARRAY_CHANNELS = '''CarIdxLap CarIdxLapCompleted CarIdxLapDistPct CarIdxPosition CarIdxClassPosition
 CarIdxF2Time CarIdxEstTime CarIdxLastLapTime CarIdxBestLapTime CarIdxOnPitRoad CarIdxTrackSurface
 CarIdxSessionFlags CarIdxTireCompound'''.split()
SESSION_CHANNELS = '''SessionTime SessionTick SessionUniqueID SessionNum SessionState SessionFlags
 SessionTimeRemain SessionLapsRemainEx SessionTimeTotal SessionLapsTotal PlayerCarIdx
 IsReplayPlaying ReplaySessionTime ReplaySessionNum ReplayPlaySpeed'''.split()
TIRE_CHANNELS = [f'{corner}{suffix}' for corner in ('LF', 'RF', 'LR', 'RR')
                 for suffix in ('tempCL', 'tempCM', 'tempCR', 'wearL', 'wearM', 'wearR', 'coldPressure')]
ALL_CHANNELS = PLAYER_CHANNELS+CONDITION_CHANNELS+ARRAY_CHANNELS+SESSION_CHANNELS+TIRE_CHANNELS
FLAGS = {1: 'Checkered', 2: 'White', 4: 'Green', 8: 'Yellow', 16: 'Red', 32: 'Blue',
         64: 'Debris', 128: 'Crossed', 256: 'Yellow waving', 512: 'One lap to green',
         1024: 'Green held', 2048: 'Ten to go', 4096: 'Five to go', 8192: 'Random waving',
         16384: 'Caution', 32768: 'Caution waving', 65536: 'Black', 131072: 'Disqualified',
         262144: 'Serviceable black', 524288: 'Furled black', 1048576: 'Repair required',
         268435456: 'Start hidden', 536870912: 'Start ready', 1073741824: 'Start set',
         2147483648: 'Start go'}
ENGINE_WARNINGS = {1: 'Water temperature', 2: 'Fuel pressure', 4: 'Oil pressure',
                   8: 'Engine stalled', 64: 'Oil temperature'}


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, deque)): return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(clean(value), ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(temp, path)
    finally: temp.unlink(missing_ok=True)


class SDKSource:
    """SDK reads and the narrow, collector-owned pit selection command set."""
    def __init__(self):
        self.ir = None
        self.error = ''
        self.metadata_revision = None
        self.metadata = {}
        self.capabilities = {}

    def read(self):
        if os.name != 'nt': self.error = 'Live iRacing telemetry requires Windows.'; return None
        if self.ir is None:
            try:
                import irsdk
                self.ir = irsdk.IRSDK()
            except ImportError:
                self.error = 'Live SDK missing. Run python -m pip install -r requirements.txt and restart Stint.'
                return None
        if not self.ir.is_initialized and not self.ir.startup():
            self.error = 'Waiting for an iRacing session.'; return None
        if not self.ir.is_connected:
            self.close(); self.error = 'Waiting for iRacing to reconnect.'; return None
        ir = self.ir
        try:
            ir.freeze_var_buffer_latest()
            names = set(ir.var_headers_names)
            if self.metadata_revision != ir.session_info_update:
                self.metadata = {key: copy.deepcopy(ir[key] or {}) for key in ('WeekendInfo', 'DriverInfo', 'SessionInfo')}
                self.metadata_revision = ir.session_info_update
                self.capabilities = {h.name: {'unit': h.unit, 'description': h.desc, 'count': h.count} for h in ir._var_headers if h.name in ALL_CHANNELS}
            values = {n: ir[n] for n in ALL_CHANNELS if n in names}
            self.error = ''
            return {'values': clean(values), 'metadata': self.metadata, 'capabilities': self.capabilities}
        finally: ir.unfreeze_var_buffer_latest()

    def pit_command(self, mode, value=0):
        if mode not in ('clear_tires', 'lf', 'rf', 'lr', 'rr', 'fuel', 'clear_fuel', 'ws', 'clear_ws', 'fr', 'clear_fr', 'tc'):
            raise ValueError('Unsupported pit command.')
        if not self.ir or not self.ir.is_connected: return False
        import irsdk
        # Compound command 12 is documented by the current SDK; pyirsdk 1.3.5
        # predates its enum name, but its broadcast transport supports it.
        number = 12 if mode == 'tc' else getattr(irsdk.PitCommandMode, mode)
        return bool(self.ir.pit_command(number, value))

    def auto_fuel_command(self, enabled, margin):
        from sim_chat import SimChat
        return SimChat(self.ir).auto_fuel(enabled, margin)

    def private_confirmation(self, car_number, engineer, summary):
        from sim_chat import SimChat
        return SimChat(self.ir).private_confirmation(car_number, engineer, summary)

    def zero_fuel_amount(self, current):
        from sim_chat import SimChat
        return SimChat(self.ir).zero_fuel_amount(current)

    def close(self):
        if self.ir:
            handle = getattr(self.ir, '_data_valid_event', None)
            self.ir.shutdown()
            # pyirsdk 1.3.5 opens this Windows event but does not release its handle.
            if handle and os.name == 'nt':
                import ctypes
                close_handle = ctypes.windll.kernel32.CloseHandle
                close_handle.argtypes = [ctypes.c_void_p]
                close_handle.restype = ctypes.c_int
                close_handle(handle)
        self.ir = None
        self.metadata_revision = None


class LiveService:
    def __init__(self, data, source=None, clock=time.monotonic):
        self.data = Path(data)
        self.root = self.data/'live-sessions'
        self.source = source if source is not None else SDKSource()
        self.clock = clock
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.thread = None
        self.map_thread = None
        self.started = False
        self.last_received = None
        self.last_tick = None
        self.last_fingerprint = None
        self.last_time = None
        self.sequence = 0
        self.collector_id = uuid.uuid4().hex
        self.trace = deque(maxlen=1200)
        self.events = deque(maxlen=1000)
        self.laps = deque(maxlen=2048)
        self.fuel_totals = empty_totals()
        self.pits = deque(maxlen=1000)
        self.stints = deque(maxlen=1000)
        self.car_states = {}
        self.alert_conditions = {}
        self.record = None
        self.state = {'session': {}, 'cars': [], 'player': {}, 'conditions': {}, 'tires': {}, 'capabilities': {}, 'map': None}
        self.plan = copy.deepcopy(DEFAULT_PLAN)
        self.error = 'Waiting for the live collector.'
        self.last_checkpoint = 0
        self.last_conditions = -10
        self.last_strategy = -1
        self.strategy = None
        self.mapping_key = None
        self.tire_stamp = None
        self.previous_tires = None
        self.lap_anchor = None
        self.lap_reasons = set()
        self.previous_incidents = None
        self.previous_fuel = None
        self.driver_times = {}
        self.driver_start = 0
        self.current_driver = None
        self.driver_time_last = None
        self.player_stint_start = None
        self.disconnect_start = None
        self.io_error = None
        self.player_refueled = False
        self.pit = PitControl(self)

    def start(self):
        if self.started: return
        self.started = True
        self.thread = threading.Thread(target=self._collect, name='stint-live', daemon=True)
        self.thread.start()
        self.map_thread = threading.Thread(target=backfill, args=(self.data, write_json, self.stopped), name='stint-live-maps', daemon=True)
        self.map_thread.start()

    def _collect(self):
        while not self.stopped.is_set():
            start = self.clock()
            try:
                sample = self.source.read()
                if sample: self.ingest(sample)
                else:
                    with self.lock:
                        self.error = getattr(self.source, 'error', '') or 'Waiting for iRacing.'
                        self._mark_disconnect()
                self.pit.process()
            except Exception as exc:
                with self.lock:
                    self.error = f'Live connection interrupted: {type(exc).__name__}. Reconnecting.'
                    self._mark_disconnect()
                    self.pit.process()  # Cancel before a reconnect can replace the error with fresh data.
                try: self.source.close()
                except Exception: pass
            wait = .05 if self.last_received is not None and self.clock()-self.last_received < 2 else 1
            self.stopped.wait(max(.001, wait-(self.clock()-start)))

    def _mark_disconnect(self):
        if self.record and self.disconnect_start is None:
            self.disconnect_start = self.last_time
            self.lap_reasons.add('discontinuity')
        if self.record and self.clock()-self.last_checkpoint >= 15: self._checkpoint()

    def _journal(self, kind, payload):
        if not self.record: return
        entry = {'kind': kind, 'session_time': self.last_time, **clean(payload)}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root/(self.record['id']+'.jsonl')).open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False, allow_nan=False)+'\n')
        except OSError:
            self.io_error = 'Race history could not be saved. Check available disk space and folder access.'
        return entry

    def _event(self, key, message, severity='info'):
        entry = {'id': uuid.uuid4().hex, 'key': key, 'message': message, 'severity': severity, 'session_time': self.last_time}
        self.events.append(entry)
        self._journal('alert', entry)

    def _alert(self, key, active, message, severity='warning'):
        if active and not self.alert_conditions.get(key): self._event(key, message, severity)
        self.alert_conditions[key] = bool(active)

    def _new_session(self, fingerprint, session_time):
        self.pit.reset('The session changed; pit control is disarmed.')
        if self.record:
            self.record['status'] = 'completed' if self.state['session'].get('finished') else 'ended'
            self._checkpoint()
        for buffer in (self.trace, self.events, self.laps, self.pits, self.stints): buffer.clear()
        self.fuel_totals = empty_totals()
        self.car_states.clear(); self.alert_conditions.clear()
        self.plan = copy.deepcopy(DEFAULT_PLAN)
        self.driver_times = {}; self.driver_time_last = None
        self.current_driver = None; self.driver_start = session_time
        self.lap_anchor = None; self.lap_reasons = set()
        self.previous_incidents = None; self.previous_fuel = None
        self.previous_tires = None; self.tire_stamp = None
        self.player_stint_start = session_time
        self.strategy = None; self.last_strategy = -1; self.mapping_key = None
        self.disconnect_start = None; self.last_conditions = session_time-10
        self.player_refueled = False
        identity = list(fingerprint)
        resumed = None
        if self.record is None and self.root.exists():
            # Only resume a matching, unfinished session with forward-moving simulator time.
            for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
                try:
                    old = json.loads(path.read_text(encoding='utf-8'))
                    if old.get('identity') == identity and old.get('status') == 'active' and old.get('last_session_time', math.inf) <= session_time:
                        resumed = old; break
                except (ValueError, OSError): continue
        self.record = resumed or {'id': uuid.uuid4().hex, 'identity': identity, 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'active', 'gaps': [], 'plan_revisions': 0}
        if resumed:
            self.plan = validate_plan(resumed.get('plan', {}))
            self.laps.extend(resumed.get('laps', [])); self.pits.extend(resumed.get('pits', []))
            self.fuel_totals = session_totals(self.laps, resumed.get('fuel_totals'))
            self.stints.extend(resumed.get('stints', [])); self.events.extend(resumed.get('alerts', []))
            self.driver_times = resumed.get('driver_times', {})
            self.record['gaps'].append({'from': resumed['last_session_time'], 'to': session_time, 'reason': 'server restart'})
            self._event('restart', 'Collector resumed after an interruption; the uncovered interval is marked.')
        self.last_fingerprint = fingerprint
        self.last_tick = None
        self.last_time = session_time

    def ingest(self, sample):
        """Injected snapshots use the same path as real SDK frames."""
        now = self.clock()
        v, meta = clean(sample.get('values', {})), sample.get('metadata', {})
        session_time = number(v.get('SessionTime'))
        if session_time is None: return
        weekend, driver_info, session_info = [meta.get(k) or {} for k in ('WeekendInfo', 'DriverInfo', 'SessionInfo')]
        current_num = v.get('SessionNum', 0)
        current = next((s for s in (session_info.get('Sessions') or []) if isinstance(s, dict) and s.get('SessionNum') == current_num), {})
        fingerprint = (weekend.get('SessionID'), weekend.get('SubSessionID'), v.get('SessionUniqueID'), current_num,
                       str(weekend.get('TrackID')), weekend.get('TrackConfigName', ''))
        tick = v.get('SessionTick')
        if number(tick) is None: tick = session_time
        with self.lock:
            new_session = self.last_fingerprint != fingerprint or (self.last_time is not None and session_time < self.last_time-.5)
            if new_session: self._new_session(fingerprint, session_time)
            if self.last_tick == tick: return
            if self.last_received is not None and now-self.last_received > 2:
                self._mark_disconnect()
                self._alert('stale', True, 'Live data became stale; predictions were suspended.')
            if self.last_time is not None and session_time-self.last_time > .5: self.lap_reasons.add('discontinuity')
            if self.disconnect_start is not None:
                self.record['gaps'].append({'from': self.disconnect_start, 'to': session_time, 'reason': 'connection gap'})
                self.record['gaps'] = self.record['gaps'][-1000:]
                self._journal('gap', self.record['gaps'][-1]); self.disconnect_start = None
                self._event('reconnected', 'Live data reconnected; the uncovered interval is marked.')
            self.last_received = now; self.last_tick = tick; self.last_time = session_time
            self.sequence += 1; self.error = ''; self.alert_conditions['stale'] = False
            player_idx = v.get('PlayerCarIdx', driver_info.get('DriverCarIdx'))
            entries = [d for d in (driver_info.get('Drivers') or []) if isinstance(d, dict)]
            own = next((d for d in entries if d.get('CarIdx') == player_idx), {})
            driver_id = str(own.get('UserID')) if own.get('UserID') is not None else None
            driver_name = own.get('UserName') or 'Unknown driver'
            replay_time = number(v.get('ReplaySessionTime'))
            replay_num = number(v.get('ReplaySessionNum'))
            historical_replay = (v.get('IsOnTrack') is False and replay_num is not None and replay_num >= 0 and
                                 replay_time is not None and (replay_num != current_num or replay_time < session_time-2))
            replay = bool(v.get('IsReplayPlaying')) or historical_replay
            capacity = number(driver_info.get('DriverCarFuelMaxLtr'))
            max_pct = number(driver_info.get('DriverCarMaxFuelPct'))
            if capacity is not None and max_pct is not None: capacity *= max_pct/100 if max_pct > 1 else max_pct
            session = {'id': self.record['id'], 'track_id': str(weekend.get('TrackID')), 'configuration': weekend.get('TrackConfigName', ''),
                       'track': weekend.get('TrackDisplayName') or weekend.get('TrackName') or 'Unknown track',
                       'car': own.get('CarScreenName', 'Unknown car'), 'session_type': current.get('SessionType', 'Unknown'),
                       'player_car_idx': player_idx, 'driver_id': driver_id, 'driver_name': driver_name,
                       'car_number': str(own.get('CarNumber', '')),
                       'tire_compounds': copy.deepcopy(driver_info.get('DriverTires') or []),
                       'elapsed_seconds': session_time, 'time_remaining_seconds': v.get('SessionTimeRemain') if number(v.get('SessionTimeRemain')) is not None and 0 <= v['SessionTimeRemain'] < 86400 else None,
                       'laps_total': v.get('SessionLapsTotal'), 'laps_remaining': v.get('SessionLapsRemainEx'),
                       'replay': replay, 'finished': v.get('SessionState') in (5, 6),
                       'flags': v.get('SessionFlags'), 'flag_labels': [name for bit, name in FLAGS.items() if bit != 268435456 and (v.get('SessionFlags') or 0)&bit],
                       'fuel_capacity_litres': capacity, 'electric': bool(driver_info.get('DriverCarIsElectric')),
                       'team_racing': bool(weekend.get('TeamRacing'))}
            significant = new_session
            if driver_id != self.current_driver:
                if self.current_driver is not None:
                    self._event('driver-change', f'Driver changed to {driver_name}.')
                    self._journal('driver_change', {'from_driver': self.current_driver, 'to_driver': driver_id})
                    significant = True
                self.current_driver = driver_id; self.driver_start = session_time
            if self.driver_time_last is not None and not replay and bool(v.get('IsOnTrack')) and not v.get('OnPitRoad'):
                dt = session_time-self.driver_time_last
                if 0 <= dt <= .5 and driver_id: self.driver_times[driver_id] = self.driver_times.get(driver_id, 0)+dt
            self.driver_time_last = session_time
            session['driver_stint_start_seconds'] = self.driver_start
            session['stint_seconds'] = session_time-self.player_stint_start
            player = {k: v.get(k) for k in PLAYER_CHANNELS}
            incidents = number(v.get('PlayerCarMyIncidentCount'))
            if self.previous_incidents is not None and incidents is not None and incidents > self.previous_incidents:
                self.lap_reasons.add('incident'); self._event('incident', f'Incident count increased to {int(incidents)}.', 'warning'); significant = True
            self.previous_incidents = incidents
            if v.get('OnPitRoad'): self.lap_reasons.add('pit lane')
            if any(v.get(k) is None for k in ('OnPitRoad', 'PlayerTrackSurface', 'PlayerCarMyIncidentCount', 'SessionFlags')):
                self.lap_reasons.add('eligibility channels unavailable')
            if v.get('PlayerTrackSurface') == 0: self.lap_reasons.add('off track')
            if replay: self.lap_reasons.add('replay')
            if (v.get('SessionFlags') or 0)&(8|16|256|16384|32768): self.lap_reasons.add('caution or red flag')
            fuel = number(v.get('FuelLevel'))
            if fuel is not None and self.previous_fuel is not None and fuel-self.previous_fuel > .05:
                self.lap_reasons.add('refueling')
                self.player_refueled = True
            self.previous_fuel = fuel
            significant |= self._player_lap(v, replay)
            cars = []
            results = {r.get('CarIdx'): r for r in (current.get('ResultsPositions') or []) if isinstance(r, dict)}
            def arr(key, idx):
                values = v.get(key)
                return values[idx] if isinstance(values, list) and 0 <= idx < len(values) else None
            for d in entries[:128]:
                idx = d.get('CarIdx')
                if type(idx) is not int or idx < 0 or d.get('IsSpectator') or d.get('CarIsPaceCar'): continue
                pct = number(arr('CarIdxLapDistPct', idx))
                completed = number(arr('CarIdxLapCompleted', idx))
                progress = completed+pct if completed is not None and pct is not None and 0 <= pct < 1 else None
                surface = arr('CarIdxTrackSurface', idx)
                old = self.car_states.get(idx)
                pit = arr('CarIdxOnPitRoad', idx)
                last_lap = number(arr('CarIdxLastLapTime', idx))
                if last_lap is not None and last_lap <= 0: last_lap = None
                if not old:
                    old = {'completed': completed, 'on_pit_road': pit, 'pit_start': session_time if pit else None,
                           'observed_pits': 0, 'pace': deque(maxlen=5), 'laps': deque(maxlen=20)}
                    self.car_states[idx] = old
                elif not replay:
                    if completed is not None and old['completed'] is not None and completed > old['completed']:
                        lap = {'car_idx': idx, 'number': d.get('CarNumber', str(idx)), 'lap': int(completed), 'time': last_lap, 'session_time': session_time}
                        old['laps'].append(lap); self._journal('car_lap', lap)
                        if last_lap and not pit and surface is not None and surface >= 0: old['pace'].append(last_lap)
                        significant = True
                    if pit and old['on_pit_road'] is False:
                        old['pit_start'] = session_time; old['partial_pit'] = False
                    if pit is False and old['on_pit_road'] and old['pit_start'] is not None:
                        old['observed_pits'] += 1
                        visit = {'car_idx': idx, 'number': d.get('CarNumber', str(idx)), 'entry_seconds': old['pit_start'],
                                 'exit_seconds': session_time, 'duration': session_time-old['pit_start'],
                                 'observed_since_connect': True, 'partial': old.get('partial_pit', False)}
                        self.pits.append(visit); self._journal('pit_visit', visit); old['pit_start'] = None
                        if idx == player_idx:
                            if self.player_refueled:
                                stint = {'start_seconds': self.player_stint_start, 'end_seconds': session_time, 'driver_id': driver_id, 'refueled': True}
                                self.stints.append(stint); self._journal('stint', stint)
                                self.player_stint_start = session_time
                            self._event('pit-exit', 'Player car left pit road.'+(' A new fuel stint has started.' if self.player_refueled else ''))
                            self.player_refueled = False
                        significant = True
                if old['pit_start'] == session_time and pit and old['completed'] == completed: old['partial_pit'] = old.get('partial_pit', old['on_pit_road'] is True)
                old['completed'] = completed; old['on_pit_road'] = pit
                r = results.get(idx, {})
                position = arr('CarIdxPosition', idx)
                class_position = arr('CarIdxClassPosition', idx)
                best = number(arr('CarIdxBestLapTime', idx))
                car = {'car_idx': idx, 'number': str(d.get('CarNumber', idx)), 'driver': d.get('UserName', ''),
                       'team': d.get('TeamName', ''), 'class_id': d.get('CarClassID'), 'class_name': d.get('CarClassShortName', ''),
                       'class_color': d.get('CarClassColor'), 'position': position if position and position > 0 else None,
                       'class_position': class_position if class_position and class_position > 0 else None,
                       'lap_completed': completed, 'lap_dist_pct': pct if pct is not None and 0 <= pct < 1 and surface is not None and surface >= 0 else None,
                       'progress': progress if surface is not None and surface >= 0 else None, 'track_surface': surface,
                       'last_lap_seconds': last_lap, 'best_lap_seconds': best if best and best > 0 else None,
                       'sdk_gap_seconds': number(arr('CarIdxF2Time', idx)) if session['session_type'].lower() == 'race' else None,
                       'estimated_track_time': number(arr('CarIdxEstTime', idx)), 'on_pit_road': pit,
                       'flags': arr('CarIdxSessionFlags', idx), 'observed_pits': old['observed_pits'], 'lap_history': list(old['laps']),
                       'observed_pace_seconds': sorted(old['pace'])[len(old['pace'])//2] if old['pace'] else None,
                       'reason_out': r.get('ReasonOutStr')}
                cars.append(car)
            self._timing(cars, player_idx, session['session_type'])
            cars.sort(key=lambda c: (c['position'] is None, c['position'] or 10000, c['best_lap_seconds'] or math.inf, c['car_idx']))
            key = (session['track_id'], session['configuration'])
            if self.mapping_key != key or now-self.state.get('map_checked', -10) >= 5:
                self.state['map'] = get_map(self.data, *key); self.mapping_key = key; self.state['map_checked'] = now
            tires = {k: v.get(k) for k in TIRE_CHANNELS}
            if tires != self.previous_tires:
                if self.previous_tires is not None: self.tire_stamp = session_time
                self.previous_tires = tires
            self.state.update(session=session, player=player, cars=cars, conditions={k: v.get(k) for k in CONDITION_CHANNELS},
                              tires={'values': tires, 'updated_seconds': self.tire_stamp, 'source': 'pit-updated readings; cold pressure is a garage setting'},
                              capabilities=sample.get('capabilities', {}))
            if not replay:
                self.trace.append({'sequence': self.sequence, 'time': session_time, **{k: v.get(k) for k in ('Speed', 'Throttle', 'Brake', 'SteeringWheelAngle', 'Gear', 'RPM', 'FuelLevel')}})
            if session_time-self.last_conditions >= 10 and not replay:
                self._journal('conditions', self.state['conditions']); self.last_conditions = session_time
            if now-self.last_strategy >= 1 or significant or replay != self.alert_conditions.get('replay_active'):
                self._calculate_strategy('connected'); self.last_strategy = now
                self.alert_conditions['replay_active'] = replay
            self._alerts(v)
            self.pit.observe()
            if significant or now-self.last_checkpoint >= 15: self._checkpoint()

    def _player_lap(self, v, replay=False):
        completed = number(v.get('LapCompleted'))
        pct = number(v.get('LapDistPct'))
        fuel = number(v.get('FuelLevel'))
        if completed is None or pct is None: self.lap_reasons.add('missing lap data'); return False
        if self.lap_anchor is None:
            self.lap_anchor = {'completed': completed, 'time': self.last_time, 'fuel': fuel, 'pct': pct}
            self.lap_reasons.add('partial initial lap'); return False
        a = self.lap_anchor
        if completed == a['completed']: return False
        if completed < a['completed']:
            self.lap_anchor = None; self.lap_reasons.add('lap reset'); return False
        duration = self.last_time-a['time']
        used = a['fuel']-fuel if a['fuel'] is not None and fuel is not None else None
        reasons = set(self.lap_reasons)
        if completed-a['completed'] != 1: reasons.add('missing laps')
        if used is None or used <= 0: reasons.add('missing or invalid fuel evidence')
        if duration <= 0: reasons.add('invalid duration')
        lap = {'number': int(completed), 'duration': duration, 'fuel_used': used,
               'fuel_end': fuel, 'eligible': not reasons, 'reasons': sorted(reasons), 'session_time': self.last_time}
        self.laps.append(lap); add_lap(self.fuel_totals, lap); self._journal('player_lap', lap)
        self.lap_anchor = {'completed': completed, 'time': self.last_time, 'fuel': fuel, 'pct': pct}
        self.lap_reasons = set()
        if v.get('OnPitRoad'): self.lap_reasons.add('pit lane')
        if replay: self.lap_reasons.add('replay')
        if v.get('PlayerTrackSurface') == 0: self.lap_reasons.add('off track')
        if (v.get('SessionFlags') or 0)&(8|16|256|16384|32768): self.lap_reasons.add('caution or red flag')
        if any(v.get(k) is None for k in ('OnPitRoad', 'PlayerTrackSurface', 'PlayerCarMyIncidentCount', 'SessionFlags')):
            self.lap_reasons.add('eligibility channels unavailable')
        return True

    @staticmethod
    def _timing(cars, player_idx, session_type):
        race = session_type.lower() == 'race'
        own = next((c for c in cars if c['car_idx'] == player_idx), None)
        leader = next((c for c in cars if c['position'] == 1), None)
        for c in cars:
            c['gap_laps'] = max(0, math.floor(leader['progress']-c['progress']+1e-6)) if race and leader and leader['progress'] is not None and c['progress'] is not None else None
            c['relative_seconds'] = None
            c['lap_difference_to_player'] = None
            if own and c['progress'] is not None and own['progress'] is not None:
                c['lap_difference_to_player'] = math.floor(c['progress'])-math.floor(own['progress'])
                t, own_t = c['estimated_track_time'], own['estimated_track_time']
                lap_time = own.get('observed_pace_seconds') or own.get('best_lap_seconds')
                if t is not None and own_t is not None and lap_time and 0 <= t <= lap_time and 0 <= own_t <= lap_time:
                    c['relative_seconds'] = ((t-own_t+lap_time/2)%lap_time)-lap_time/2
            # Classification gaps originate in the SDK; relative gaps are a separate estimate.
            c['gap_source'] = 'SDK race classification' if race else 'best lap'

    def _calculate_strategy(self, status):
        model = {**self.state, 'status': status, 'laps': list(self.laps),
                 'fuel_totals': self.fuel_totals, 'driver_times': self.driver_times}
        self.strategy = calculate(model, self.plan)
        for scenario in self.strategy['scenarios']:
            scenario['rejoin'] = rejoin({**self.state, 'strategy': self.strategy}, scenario)

    def _alerts(self, v):
        flags = v.get('SessionFlags')
        if flags is not None and flags != self.alert_conditions.get('flag_value'):
            if self.alert_conditions.get('flag_value') is not None: self._event('flags', 'Flags: '+(', '.join(self.state['session']['flag_labels']) or 'none'), 'warning')
            self.alert_conditions['flag_value'] = flags
        for key, channel, label in [('rain', 'Precipitation', 'Rain level changed'), ('wetness', 'TrackWetness', 'Track wetness changed')]:
            value = v.get(channel)
            bucket = (value > 0, round(value, 1)) if key == 'rain' and number(value) is not None else value
            if bucket is not None and bucket != self.alert_conditions.get(key+'_value'):
                if self.alert_conditions.get(key+'_value') is not None:
                    shown = f'{value*100:.1f}%' if key == 'rain' else value
                    self._event(key, f'{label}: {shown}.', 'warning')
                self.alert_conditions[key+'_value'] = bucket
        warnings = v.get('EngineWarnings') or 0
        critical = warnings & sum(ENGINE_WARNINGS)
        previous = self.alert_conditions.get('engine_bits', 0)
        if critical & ~previous:
            labels = [label for bit, label in ENGINE_WARNINGS.items() if critical & bit]
            self._event('engine', 'Engine warning: '+', '.join(labels)+'.', 'critical')
        self.alert_conditions['engine_bits'] = critical
        if not self.state['session'].get('replay') and self.strategy:
            reserve, fuel = self.strategy.get('reserve_litres'), number(v.get('FuelLevel'))
            self._alert('fuel-reserve', reserve is not None and fuel is not None and fuel <= reserve, 'Fuel is at or below the selected reserve.', 'critical')
            chosen = next((s for s in self.strategy['scenarios'] if s['id'] == self.plan.get('active_scenario')), None)
            next_stop = chosen['timeline'][0] if chosen and chosen['timeline'] else None
            if next_stop:
                pct, lap = number(v.get('LapDistPct')), number(v.get('LapCompleted'))
                progress = lap+pct if lap is not None and pct is not None else None
                approaching = progress is not None and 0 < next_stop['lap']-progress <= 1
                self._alert(f'planned-stop-{next_stop["lap"]}', approaching, f'Planned pit stop after lap {next_stop["lap"]}.')
                if next_stop['driver'] and next_stop['driver'] != self.current_driver:
                    self._alert(f'planned-driver-{next_stop["lap"]}', approaching, f'Driver change planned at lap {next_stop["lap"]}.')

    def snapshot(self, include_trace=False):
        with self.lock:
            age = self.clock()-self.last_received if self.last_received is not None else None
            status = 'connected' if age is not None and age <= 2 else 'stale' if self.record else 'waiting'
            if status == 'stale':
                self._mark_disconnect()
                self._alert('stale', True, 'Live data is stale; predictions are suspended.', 'critical')
            if self.strategy is None or status != 'connected' or self.state['session'].get('replay'): self._calculate_strategy(status)
            result = {k: copy.deepcopy(v) for k, v in self.state.items() if k != 'map_checked'}
            result.update(version=1, collector_id=self.collector_id, sequence=self.sequence, status=status, update_age_seconds=age,
                          message=self.error or ('Live telemetry connected.' if status == 'connected' else 'Waiting for fresh iRacing data.'),
                          strategy=copy.deepcopy(self.strategy), plan=copy.deepcopy(self.plan),
                          laps=list(self.laps)[-20:], pits=list(self.pits)[-30:], stints=list(self.stints)[-20:],
                          alerts=list(self.events)[-40:], driver_times=copy.deepcopy(self.driver_times),
                          history_error=self.io_error, trace=list(self.trace) if include_trace else list(self.trace)[-3:])
            result['plan_revision'] = self.record['plan_revisions'] if self.record else 0
            result['pit_control'] = self.pit.status()
            return clean(result)

    def update_plan(self, value, session_id, revision=None, actor='Local driver'):
        plan = validate_plan(value)
        with self.lock:
            if not self.record or session_id != self.record['id']:
                raise ValueError('The live session changed. Refresh before saving this plan.')
            if revision is not None and (type(revision) is not int or revision != self.record['plan_revisions']):
                raise PitError('The race plan changed on another browser. Review the latest plan before saving.')
            self.plan = plan
            self.record['plan_revisions'] += 1
            self._journal('plan', {'revision': self.record['plan_revisions'], 'plan': plan, 'actor': actor})
            age = self.clock()-self.last_received if self.last_received is not None else math.inf
            self._calculate_strategy('connected' if age <= 2 else 'stale')
            self._checkpoint()
            return self.snapshot()

    def _checkpoint(self):
        if not self.record: return
        self.record.update(session=copy.deepcopy(self.state['session']), last_session_time=self.last_time,
                           saved_at=datetime.now(timezone.utc).isoformat(), plan=copy.deepcopy(self.plan),
                           laps=list(self.laps), pits=list(self.pits), stints=list(self.stints), alerts=list(self.events),
                           fuel_totals=copy.deepcopy(self.fuel_totals),
                           driver_times=copy.deepcopy(self.driver_times))
        try:
            write_json(self.root/(self.record['id']+'.json'), self.record)
        except OSError:
            self.io_error = 'Race history could not be saved. Check available disk space and folder access.'
        self.last_checkpoint = self.clock()

    def history(self, session_id=None, kind=None, before=None):
        if session_id:
            if not re.fullmatch('[a-f0-9]{32}', session_id): raise ValueError('Invalid live session identifier.')
            path = self.root/(session_id+'.json')
            if not path.exists(): raise ValueError('Saved live session not found.')
            record = json.loads(path.read_text(encoding='utf-8'))
            entries = deque(maxlen=500)
            journal = self.root/(session_id+'.jsonl')
            if journal.exists():
                with journal.open(encoding='utf-8') as f:
                    for index, line in enumerate(f):
                        if before is not None and index >= before: break
                        try: item = json.loads(line)
                        except ValueError: continue
                        if kind is None or item.get('kind') == kind: entries.append({'index': index, **item})
            record['journal'] = list(entries)
            record['before'] = entries[0]['index'] if entries else None
            if record.get('status') == 'active' and (not self.record or self.record['id'] != session_id): record['status'] = 'interrupted'
            return record
        result = []
        for path in self.root.glob('*.json'):
            try:
                r = json.loads(path.read_text(encoding='utf-8'))
                item = {k: r.get(k) for k in ('id', 'started_at', 'saved_at', 'status', 'session', 'gaps')}
                if item['status'] == 'active' and (not self.record or self.record['id'] != item['id']): item['status'] = 'interrupted'
                result.append(item)
            except (ValueError, OSError): continue
        return sorted(result, key=lambda r: r.get('saved_at') or '', reverse=True)

    def close(self):
        self.stopped.set()
        if self.thread: self.thread.join(timeout=3)
        if self.map_thread: self.map_thread.join(timeout=1)
        with self.lock:
            self.pit.reset('The collector stopped; pit control is disarmed.')
            if self.record and self.state['session'].get('finished'): self.record['status'] = 'completed'
            self._checkpoint()
        if not self.thread or not self.thread.is_alive(): self.source.close()
