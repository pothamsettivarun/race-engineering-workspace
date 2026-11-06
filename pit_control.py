"""Bounded, explicit pit selections. Only the collector dispatches SDK commands."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import OrderedDict, deque


class PitError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class PitControl:
    def __init__(self, live):
        self.live = live
        self.enabled = False
        self.engineer = None
        self.engineer_name = None
        self.binding = None
        self.revision = 0
        self.actual_key = None
        self.observations = deque(maxlen=80)
        self.commands = OrderedDict()
        self.pending = None
        self.notifications = deque(maxlen=32)

    def _binding(self):
        s = self.live.state['session']
        return (self.live.collector_id, s.get('id'), s.get('driver_id'), s.get('player_car_idx'))

    def _actual(self):
        p = self.live.state['player']
        flags = p.get('PitSvFlags')
        fuel = p.get('PitSvFuel')
        if type(flags) is not int or flags < 0: flags = None
        if type(fuel) not in (int, float) or not math.isfinite(fuel) or fuel < 0: fuel = None
        def measured(name):
            value = p.get(name)
            return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        auto = measured('dpFuelAutoFillActive')
        available = measured('dpFuelAutoFillEnabled')
        return {'fuel_litres': fuel, 'fuel_enabled': bool(flags & 16) if flags is not None else None,
                'tires': {c: bool(flags & (1 << i)) if flags is not None else None
                          for i, c in enumerate(('lf', 'rf', 'lr', 'rr'))}, 'flags': flags,
                'pressures_kpa': {c: measured(f'PitSv{c.upper()}P') for c in ('lf', 'rf', 'lr', 'rr')},
                'tire_compound': measured('PitSvTireCompound'),
                'auto_fuel': bool(auto) if auto is not None else None,
                'auto_fuel_available': bool(available) if available is not None else None,
                'auto_fuel_active': bool(p['dpFuelAutoFillActive']) if measured('dpFuelAutoFillActive') is not None else None,
                'tear_off': bool(flags & 32) if flags is not None else None,
                'fast_repair': bool(flags & 64) if flags is not None else None,
                'fast_repairs_available': measured('FastRepairAvailable')}

    def observe(self):
        """Called under the Live lock for each advancing SDK frame."""
        binding = self._binding()
        if binding != self.binding:
            self.reset('Session or driver changed; pit control is disarmed.')
            self.binding = binding
            self.actual_key = None
            self.observations.clear()
        actual = self._actual()
        key = json.dumps(actual, sort_keys=True)
        if key != self.actual_key:
            self.actual_key = key
            self.revision += 1
        self.observations.append((self.live.sequence, self.live.clock()))
        self._resolve()

    def _blocked(self, *, allow_stall=False):
        l, s, p = self.live, self.live.state['session'], self.live.state['player']
        if not s.get('id') or l.last_received is None: return 'Waiting for a live session.'
        if l.clock()-l.last_received > 2 or l.error: return 'Fresh simulator data is required.'
        if s.get('replay'): return 'Pit control is unavailable during replay.'
        if p.get('IsOnTrack') is not True: return 'The driver must be in the car.'
        if p.get('PitstopActive') is not False: return 'Pit service is active or its status is unavailable.'
        if not allow_stall and p.get('PlayerCarInPitStall') is True: return 'Leave the pit stall before applying next-stop selections.'
        if not s.get('driver_id') or s.get('player_car_idx') is None: return 'The active driver is unavailable.'
        if self._actual()['flags'] is None: return 'Pit-selection telemetry is unavailable.'
        if not callable(getattr(l.source, 'pit_command', None)): return 'This connection does not support pit commands.'
        return None

    def status(self):
        with self.live.lock:
            self._resolve()
            s = self.live.state['session']
            capacity = s.get('fuel_capacity_litres')
            capacity = math.floor(capacity) if type(capacity) in (int, float) and math.isfinite(capacity) and capacity >= 1 else None
            actual = self._actual()
            compounds = [{'index': t['TireIndex'], 'name': ('Dry' if s.get('car')=='Chevrolet Corvette Z06 GT3.R' and
                          t.get('TireCompoundType')=='Hard' else str(t.get('TireCompoundType', 'Unknown')))}
                         for t in s.get('tire_compounds', []) if isinstance(t, dict) and
                         type(t.get('TireIndex')) is int and 0 <= t['TireIndex'] <= 31]
            return {'enabled': self.enabled, 'engineer': self.engineer, 'engineer_name': self.engineer_name, 'revision': self.revision,
                    'blocked_reason': self._blocked(), 'actual': actual, 'capacity_litres': capacity,
                    'fuel_supported': not s.get('electric') and capacity is not None and actual['fuel_litres'] is not None,
                    'pressure_supported': {c: v is not None for c, v in actual['pressures_kpa'].items()},
                    'compounds': compounds if actual['tire_compound'] is not None else [],
                    'auto_fuel_supported': (not s.get('electric') and capacity is not None and
                        str(s.get('session_type','')).casefold()=='race' and actual['auto_fuel_available'] is True and
                        actual['auto_fuel'] is not None and callable(getattr(self.live.source, 'auto_fuel_command', None))),
                    'auto_fuel_note': 'Auto Fuel requires a race session and fuel data from iRacing.',
                    'private_confirmation_supported': callable(getattr(self.live.source, 'private_confirmation', None)) and bool(re.fullmatch(r'\d{1,4}', s.get('car_number', ''))),
                    'pending': self.pending['id'] if self.pending else None,
                    'commands': [self._public(c) for c in list(self.commands.values())[-12:]]}

    @staticmethod
    def _public(command):
        return {k: copy.deepcopy(v) for k, v in command.items() if not k.startswith('_')}

    def authorize(self, enabled, engineer, actor='Local driver', *, engineer_name=None):
        with self.live.lock:
            if enabled:
                if not engineer: raise PitError('Designate an engineer before enabling pit control.', 400)
                reason = self._blocked(allow_stall=True)
                if reason: raise PitError(reason)
                self.enabled, self.engineer, self.binding = True, engineer, self._binding()
                self.engineer_name = engineer_name or engineer
                self.live._journal('pit_authority', {'enabled': True, 'engineer': engineer, 'actor': actor})
            else:
                self.reset('Pit control was disabled locally.', actor)
            self.live._checkpoint()
            return self.status()

    def reset(self, reason, actor='System'):
        if self.pending: self._finish('failed', reason)
        if self.enabled:
            self.live._journal('pit_authority', {'enabled': False, 'engineer': self.engineer, 'actor': actor, 'reason': reason})
        self.enabled, self.engineer = False, None
        self.engineer_name = None

    def submit(self, body, actor, *, local=False, actor_name=None):
        if not isinstance(body, dict): raise PitError('Pit selections must be an object.', 400)
        command_id = body.get('id')
        if not isinstance(command_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,64}', command_id):
            raise PitError('A unique command ID is required.', 400)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self.live.lock:
            if not self.enabled or (not local and actor != self.engineer): raise PitError('Pit control is not enabled for this engineer.', 403)
            existing = self.commands.get(command_id)
            if existing:
                if existing['_digest'] != digest or existing['actor'] != actor: raise PitError('This command ID was already used for different selections.')
                return self._public(existing)
            reason = self._blocked()
            if reason: raise PitError(reason)
            s = self.live.state['session']
            if (body.get('collector_id'), body.get('session_id'), body.get('driver_id'), body.get('player_car_idx')) != self._binding():
                raise PitError('The collector, session, car, or driver changed. Refresh before applying.')
            if type(body.get('revision')) is not int or body['revision'] != self.revision:
                raise PitError('Simulator selections changed. Review the actual selections before applying.')
            observed = next((stamp for seq, stamp in reversed(self.observations) if seq == body.get('sequence')), None)
            if observed is None or self.live.clock()-observed > 2: raise PitError('These selections were based on an expired snapshot. Refresh before applying.')
            if self.pending: raise PitError('Another pit-selection request is still pending.')
            fuel, tires = body.get('fuel_litres'), body.get('tires')
            support = self.status()
            if fuel is not None:
                if not support['fuel_supported']: raise PitError('Fuel control is unavailable for this car.', 400)
                if type(fuel) is not int or not 0 <= fuel <= support['capacity_litres']:
                    raise PitError('Fuel to add must be whole liters within the permitted tank capacity.', 400)
            if not isinstance(tires, dict) or set(tires) != {'lf', 'rf', 'lr', 'rr'} or any(type(v) is not bool for v in tires.values()):
                raise PitError('Choose whether to change each of the four tires.', 400)
            requested = {'fuel_litres': fuel, 'tires': copy.deepcopy(tires)}
            for key in ('fuel_enabled', 'tear_off', 'fast_repair', 'auto_fuel'):
                if key in body:
                    if type(body[key]) is not bool: raise PitError(f'{key} must be a checkbox selection.', 400)
                    if key == 'fuel_enabled' and (fuel is None and not body.get('auto_fuel') or (body[key] and fuel == 0 and not body.get('auto_fuel'))):
                        raise PitError('Begin fueling requires a positive fuel amount or Auto Fuel.', 400)
                    if key == 'auto_fuel' and not support['auto_fuel_supported']:
                        raise PitError('Auto Fuel control is unavailable in this session.', 400)
                    requested[key] = body[key]
            if 'auto_fuel_margin_laps' in body:
                margin = body['auto_fuel_margin_laps']
                if type(margin) not in (int, float) or not math.isfinite(margin) or not -10 <= margin <= 10 or abs(margin*10-round(margin*10)) > .0001:
                    raise PitError('Auto Fuel margin must be -10 to 10 laps in 0.1-lap steps.', 400)
                if 'auto_fuel' not in requested: raise PitError('Choose Auto Fuel before setting its margin.', 400)
                requested['auto_fuel_margin_laps'] = margin
            if 'pressures_kpa' in body:
                values = body['pressures_kpa']
                if not isinstance(values, dict) or set(values)-set(tires): raise PitError('Invalid tire pressure selection.', 400)
                for c, value in values.items():
                    if not support['pressure_supported'][c] or type(value) is not int or not 1 <= value <= 1000:
                        raise PitError('Pressures must be whole kPa for available tires; iRacing enforces car limits.', 400)
                requested['pressures_kpa'] = copy.deepcopy(values)
            if 'tire_compound' in body:
                compound = body['tire_compound']
                if type(compound) is not int or compound not in [t['index'] for t in support['compounds']]:
                    raise PitError('Choose an available tire compound for this car.', 400)
                if compound != support['actual']['tire_compound'] and not all(tires.values()):
                    raise PitError('Changing compound requires selecting all four tires.', 400)
                requested['tire_compound'] = compound
            name = actor_name or (self.engineer_name if actor == self.engineer else actor)
            unverified = ['auto_fuel_margin_laps'] if requested.get('auto_fuel') and 'auto_fuel_margin_laps' in requested else []
            command = {'id': command_id, 'actor': actor, 'actor_name': name, 'status': 'pending', 'message': 'Waiting for the collector.',
                       'session_id': s['id'], 'driver_id': s['driver_id'], 'requested': requested,
                       'unverified_fields': unverified,
                       'session_time': self.live.last_time, 'actual': support['actual'], 'confirmed_fields': [],
                       '_digest': digest, '_expires': self.live.clock()+2, '_binding': self._binding(),
                       '_revision': self.revision, '_sent_sequence': None, '_failure': None}
            self.commands[command_id] = command
            while len(self.commands) > 128: self.commands.popitem(last=False)
            self.pending = command
            self.live._journal('pit_command_requested', self._public(command))
            self.live._checkpoint()
            return self._public(command)

    def _matches(self, command):
        actual, req = self._actual(), command['requested']
        fields = [c for c, value in req['tires'].items() if actual['tires'][c] is value]
        fuel = req['fuel_litres']
        if fuel is not None and ((fuel == 0 and actual['fuel_enabled'] is False and
            ('fuel_enabled' not in req or actual['fuel_litres'] is not None and actual['fuel_litres'] < .01)) or
            (fuel > 0 and actual['fuel_enabled'] is req.get('fuel_enabled', True) and actual['fuel_litres'] is not None and abs(actual['fuel_litres']-fuel) < .01) or
            req.get('auto_fuel') is True):
            fields.append('fuel')
        for key in ('fuel_enabled', 'auto_fuel', 'tear_off', 'fast_repair', 'tire_compound'):
            if key in req and actual[key] == req[key]: fields.append(key)
        for c, pressure in req.get('pressures_kpa', {}).items():
            value = actual['pressures_kpa'][c]
            # Integer-kPa SDK requests can be reported as a fractional kPa after
            # simulator unit conversion. Larger clamping is not confirmation.
            if value is not None and abs(value-pressure) <= .6: fields.append('pressure_'+c)
        return fields

    @staticmethod
    def _expected(command):
        req = command['requested']
        return 4+(req['fuel_litres'] is not None)+sum(k in req for k in (
            'fuel_enabled', 'auto_fuel', 'tear_off', 'fast_repair', 'tire_compound'))+len(req.get('pressures_kpa', {}))

    def _resolve(self):
        c = self.pending
        if not c: return
        if c['_binding'] != self._binding(): self._finish('failed', 'Session or driver changed.'); return
        reason = self._blocked()
        if reason: self._finish('unconfirmed' if c['_sent_sequence'] is not None else 'failed', reason); return
        sent = c['_sent_sequence']
        if self.live.clock() >= c['_expires']:
            status = 'partial' if c['confirmed_fields'] else 'unconfirmed' if sent is not None else 'failed'
            self._finish(status, c['_failure'] or 'Request expired without complete simulator confirmation.')
            return
        if sent is not None and self.live.sequence > sent:
            fields = self._matches(c)
            c['confirmed_fields'] = fields
            if len(fields) == self._expected(c) and not c['_failure']:
                self._finish('confirmed', 'Requested selections confirmed by simulator telemetry.'); return

    def _finish(self, status, message):
        c = self.pending
        if not c: return
        c.update(status=status, message=message, actual=self._actual(), completed_session_time=self.live.last_time)
        if status == 'confirmed':
            if c['unverified_fields']:
                c['message'] = 'Selections confirmed; Auto Fuel margin was sent but must be checked in iRacing.'
            c['private_confirmation'] = {'status': 'pending', 'message': 'Waiting for private in-car confirmation.'}
            self.notifications.append(c)
        self.pending = None
        self.live._journal('pit_command_result', self._public(c))
        self.live._checkpoint()

    def process(self):
        """Run exclusively on the collector thread after reading its latest frame."""
        with self.live.lock:
            self._resolve()
            while self.notifications:
                notice = self.notifications.popleft()
                sender = getattr(self.live.source, 'private_confirmation', None)
                result = {'status': 'unavailable', 'message': 'Private in-car chat is unavailable.'}
                if not self.enabled or notice['_binding'] != self._binding() or self._blocked():
                    result = {'status': 'skipped', 'message': 'The live car or session changed before chat delivery.'}
                elif callable(sender):
                    req = notice['requested']
                    fuel_text = 'Auto Fuel' if req.get('auto_fuel') else f"Fuel {req['fuel_litres']} L" if req['fuel_litres'] is not None else 'Fuel unchanged'
                    if req.get('fuel_enabled') is False: fuel_text += ' (refueling off)'
                    parts = [fuel_text,
                             'tires '+(','.join(c.upper() for c, selected in req['tires'].items() if selected) or 'none')]
                    if req.get('pressures_kpa'): parts.append('pressures '+','.join(f'{c.upper()} {v}' for c,v in req['pressures_kpa'].items())+' kPa')
                    for key, label in [('tear_off','tear-off'),('fast_repair','fast repair')]:
                        if key in req: parts.append(label+(' on' if req[key] else ' off'))
                    if 'tire_compound' in req:
                        compound = next((t['name'] for t in self.status()['compounds'] if t['index']==req['tire_compound']), str(req['tire_compound']))
                        parts.append(compound+' tires')
                    if notice['unverified_fields']: parts.insert(0, 'verify Auto Fuel margin in black box')
                    try:
                        sent = sender(self.live.state['session'].get('car_number',''), notice['actor_name'], '; '.join(parts))
                        result = {'status': 'sent' if sent else 'failed', 'message': 'Private confirmation sent to iRacing.' if sent else 'Pit selections are confirmed; private chat delivery failed.'}
                    except Exception:
                        result = {'status': 'failed', 'message': 'Pit selections are confirmed; private chat delivery failed.'}
                notice['private_confirmation'] = result
                self.live._journal('pit_private_confirmation', {'id': notice['id'], 'actor': notice['actor'], 'actor_name': notice['actor_name'], **result})
                self.live._checkpoint()
            c = self.pending
            if not c: return
            if c['_sent_sequence'] is not None:
                # A zero-amount macro enables the refueling checkbox. Clear it
                # only after a subsequent SDK frame reports the zero amount.
                if c.get('_clear_zero_fuel') and self._actual()['fuel_litres'] is not None and self._actual()['fuel_litres'] < .01:
                    c['_clear_zero_fuel'] = False
                    try:
                        if not self.live.source.pit_command('clear_fuel',0):raise RuntimeError()
                    except Exception:c['_failure']='Zero fuel amount was set, but clearing the refueling checkbox failed.'
                return
            if c['_revision'] != self.revision: self._finish('failed', 'Simulator selections changed before dispatch.'); return
            if not self.enabled: self._finish('failed', 'Pit control was disabled.'); return
            c['_sent_sequence'] = self.live.sequence
            c['message'] = 'Sent; waiting for simulator telemetry.'
            req = c['requested']
            operations = [('tc',req['tire_compound'])] if 'tire_compound' in req and req['tire_compound'] != c['actual']['tire_compound'] else []
            operations += [(corner, value) for corner,value in req.get('pressures_kpa',{}).items()
                           if abs(value-c['actual']['pressures_kpa'][corner]) > .6]
            operations += [('clear_tires', 0)] + [(corner, 0) for corner, selected in req['tires'].items() if selected]
            fuel = req['fuel_litres']
            if fuel is not None and not req.get('auto_fuel'):
                if fuel: operations.append(('fuel', fuel))
                if req.get('fuel_enabled', bool(fuel)) is False: operations.append(('clear_fuel', 0))
            for key, mode, clear in [('tear_off','ws','clear_ws'),('fast_repair','fr','clear_fr')]:
                if key in req: operations.append((mode if req[key] else clear,0))
            try:
                for mode, value in operations:
                    if self.live.clock() >= c['_expires']: raise RuntimeError('Request expired during dispatch.')
                    if not self.live.source.pit_command(mode, value): raise RuntimeError('SDK delivery failed; selections may have partially changed.')
                if 'auto_fuel' in req and (req['auto_fuel'] != c['actual']['auto_fuel'] or req['auto_fuel'] and 'auto_fuel_margin_laps' in req):
                    if not self.live.source.auto_fuel_command(req['auto_fuel'], req.get('auto_fuel_margin_laps')):
                        raise RuntimeError('Auto Fuel macro delivery failed.')
                if fuel == 0 and 'fuel_enabled' in req and c['actual']['fuel_litres'] > .01:
                    setter = getattr(self.live.source, 'zero_fuel_amount', None)
                    if not callable(setter) or not setter(c['actual']['fuel_litres']):raise RuntimeError('Zero fuel amount delivery failed.')
                    c['_clear_zero_fuel'] = True
            except Exception:
                c['_failure'] = 'SDK delivery was interrupted; review the actual simulator selections.'
