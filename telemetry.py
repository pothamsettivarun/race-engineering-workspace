"""Read iRacing's disk SDK layout without changing the source file."""
from __future__ import annotations

import math
import re
import struct
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

TYPES = {0: 'S1', 1: '?', 2: '<i4', 3: '<u4', 4: '<f4', 5: '<f8'}
CHANNELS = (
    'SessionTime Lap LapDistPct LapCurrentLapTime SessionNum Speed Throttle Brake '
    'SteeringWheelAngle Gear RPM Yaw YawRate LatAccel LongAccel FuelLevel OnPitRoad '
    'PlayerTrackSurface PlayerCarMyIncidentCount TrackTemp TrackTempCrew AirTemp '
    'WindVel WindDir RelativeHumidity Precipitation TrackWetness '
    'LFrideHeight RFrideHeight LRrideHeight RRrideHeight '
    'LFshockDefl RFshockDefl LRshockDefl RRshockDefl '
    'LFshockVel RFshockVel LRshockVel RRshockVel'
).split()


class TelemetryError(ValueError):
    pass


class IBT:
    def __init__(self, path, load_channels=True):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        if self.size < 144:
            raise TelemetryError('This file is too short to be an iRacing .ibt log.')
        with self.path.open('rb') as f:
            header = f.read(144)
            values = struct.unpack_from('<10i', header)
            self.version, _, self.tick_rate, _, info_len, info_offset, num_vars, var_offset, num_buf, self.row_size = values
            self.data_offset = struct.unpack_from('<i', header, 52)[0]
            self.recorded_at, _, _, _, declared = struct.unpack_from('<Qddii', header, 112)
            if not (1 <= self.version <= 10 and 1 <= self.tick_rate <= 1000 and
                    0 < num_vars <= 10000 and 1 <= num_buf <= 4 and 0 < self.row_size <= 1000000):
                raise TelemetryError('Unrecognized iRacing telemetry header. Import a completed .ibt file.')
            if not (144 <= var_offset and var_offset + num_vars * 144 <= self.size and
                    0 < info_len <= 10_000_000 and 144 <= info_offset and
                    info_offset + info_len <= self.size and self.data_offset >= 144):
                raise TelemetryError('Invalid or incomplete telemetry offsets. Stop logging and try again.')
            if self.data_offset < max(var_offset + num_vars * 144, info_offset + info_len):
                raise TelemetryError('Telemetry samples overlap the file header.')
            available = max(0, (self.size - self.data_offset) // self.row_size)
            if declared <= 0 or declared > available:
                raise TelemetryError('The log is unfinished or truncated. Stop telemetry logging before importing.')
            self.count = declared
            self.headers = {}
            f.seek(var_offset)
            for _ in range(num_vars):
                raw = f.read(144)
                kind, offset, count = struct.unpack_from('<iii', raw)
                name = raw[16:48].split(b'\0')[0].decode('latin-1')
                unit = raw[112:144].split(b'\0')[0].decode('latin-1')
                if kind not in TYPES or count < 1 or offset < 0 or offset + np.dtype(TYPES[kind]).itemsize * count > self.row_size:
                    raise TelemetryError(f'Invalid channel layout: {name}.')
                self.headers[name] = {'type': kind, 'offset': offset, 'count': count, 'unit': unit}
            f.seek(info_offset)
            raw = f.read(info_len).rstrip(b'\0')
            encoding = 'utf-8' if b'Encoding: UTF8' in raw[:150] else 'latin-1'
            self.session_text = raw.decode(encoding, errors='replace')
            try:
                self.session = yaml.safe_load(self.session_text) or {}
            except yaml.YAMLError:
                # Some SDK strings contain unescaped ':' characters. Keep scalar
                # metadata available; never silently fabricate a parsed setup.
                self.session = {}
            if not isinstance(self.session, dict):
                self.session = {}
        required = {'SessionTime', 'Lap', 'LapDistPct', 'Speed'}
        if not required.issubset(self.headers):
            raise TelemetryError('Required time, lap, distance, or speed channels are missing.')
        self.warnings = [] if self.session else ['Session YAML could not be decoded; setup metadata may be unavailable.']
        self.channels = {}
        if not load_channels:
            return
        mm = np.memmap(self.path, dtype='u1', mode='r')
        try:
            for name in CHANNELS:
                v = self.headers.get(name)
                if v and v['count'] == 1:
                    self.channels[name] = np.ndarray((self.count,), dtype=TYPES[v['type']], buffer=mm,
                                                     offset=self.data_offset + v['offset'], strides=(self.row_size,)).astype(float)
        finally:
            mm._mmap.close()
        for required_name in required:
            if not np.isfinite(self.channels[required_name]).all():
                raise TelemetryError(f'{required_name} contains invalid values; this log cannot be aligned safely.')

    def read_samples(self, names, start=0, stop=None):
        """Copy original typed samples, without unit conversion or rounding."""
        stop = self.count if stop is None else stop
        if not 0 <= start <= stop <= self.count:
            raise TelemetryError('Sample range is outside the recording.')
        result = {}
        mm = np.memmap(self.path, dtype='u1', mode='r')
        try:
            for name in names:
                h = self.headers.get(name)
                if not h or h['count'] != 1 or h['type'] == 0:
                    raise TelemetryError(f'Channel is missing or is not a supported scalar: {name}')
                result[name] = np.ndarray((self.count,), dtype=TYPES[h['type']], buffer=mm,
                    offset=self.data_offset + h['offset'], strides=(self.row_size,))[start:stop].copy()
        finally:
            mm._mmap.close()
        return result

    def metadata(self):
        weekend = self.session.get('WeekendInfo') or {}
        drivers = self.session.get('DriverInfo') or {}
        player = next((d for d in drivers.get('Drivers', []) or []
                       if d.get('CarIdx') == drivers.get('DriverCarIdx')), {})

        def scalar(key, default=None):
            if key in weekend:
                return weekend[key]
            match = re.search(r'^\s+' + re.escape(key) + r':\s*(.*?)\s*$', self.session_text, re.M)
            return match.group(1) if match else default

        length = scalar('TrackLength', '')
        match = re.search(r'[\d.]+', str(length))
        length_m = float(match.group()) * 1000 if match else None
        sessions = (self.session.get('SessionInfo') or {}).get('Sessions') or []
        current_num = int(self.channels.get('SessionNum', np.array([0]))[0])
        current = next((s for s in sessions if s.get('SessionNum') == current_num), {})
        conditions = {}
        mappings = {'air_temp': 'TrackAirTemp', 'track_temp': 'TrackSurfaceTemp',
                    'track_temp_crew': 'TrackSurfaceTempCrew', 'wind_speed': 'TrackWindVel',
                    'wind_direction': 'TrackWindDir', 'humidity': 'TrackRelativeHumidity',
                    'precipitation': 'TrackPrecipitation', 'skies': 'TrackSkies'}
        for key, source in mappings.items():
            value = scalar(source)
            if value is not None:
                conditions[key] = {'value': value, 'source': 'session metadata'}
        if current.get('SessionTrackRubberState'):
            conditions['rubber'] = {'value': current['SessionTrackRubberState'], 'source': 'session metadata'}
        return {'car': player.get('CarScreenName') or player.get('CarPath') or 'Unknown car',
                'car_id': player.get('CarID'), 'track': scalar('TrackDisplayName', scalar('TrackName', 'Unknown track')),
                'track_id': scalar('TrackID'), 'configuration': scalar('TrackConfigName', ''),
                'track_length_m': length_m, 'build': scalar('BuildVersion', 'Unknown'),
                'recorded_at': datetime.fromtimestamp(self.recorded_at, timezone.utc).isoformat(),
                'session_type': current.get('SessionType', scalar('EventType', 'Unknown')),
                'fixed_setup': (weekend.get('WeekendOptions') or {}).get('IsFixedSetup') == 1,
                'setup_name': drivers.get('DriverSetupName', ''), 'setup': self.session.get('CarSetup') or {},
                'conditions': conditions, 'tick_rate': self.tick_rate,
                'duration': round(float(self.channels['SessionTime'][-1] - self.channels['SessionTime'][0]), 2),
                'available_channels': {k: v['unit'] for k, v in self.headers.items()}, 'warnings': self.warnings}


def finite(value):
    value = float(value)
    return round(value, 5) if math.isfinite(value) else None


def flatten_setup(value, prefix=''):
    result = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result.update(flatten_setup(item, f'{prefix} / {key}' if prefix else str(key)))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            result.update(flatten_setup(item, f'{prefix} / {i}'))
    else:
        result[prefix] = str(value)
    return result
