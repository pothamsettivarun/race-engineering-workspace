"""Content-addressed original recordings. No lossy trace cache is read here."""
import hashlib
import os
import shutil
import uuid
from pathlib import Path
from telemetry import IBT


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def retain(data, path):
    root = Path(data) / 'recordings'
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (uuid.uuid4().hex + '.tmp')
    try:
        shutil.copyfile(path, temporary)
        log = IBT(temporary, False)
        sha = digest(temporary)
        target = root / (sha + '.ibt')
        if target.exists():
            if digest(target) != sha:
                raise ValueError('Retained recording failed its checksum; restore the original.')
        else:
            os.replace(temporary, target)
        return {'sha256': sha, 'bytes': target.stat().st_size, 'sample_count': log.count,
                'tick_rate': log.tick_rate, 'format': 'original IBT', 'precision': 'native recorded values'}
    finally:
        temporary.unlink(missing_ok=True)


_verified = {}


def open_recording(data, run):
    recording = run.get('recording')
    if not recording:
        raise ValueError('This run is summary-only. Attach its original recording first.')
    sha = recording['sha256']
    if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid recording identifier.')
    path = Path(data) / 'recordings' / (sha + '.ibt')
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    key = str(path.resolve())
    if _verified.get(key) != signature:
        if digest(path) != sha:
            raise ValueError('Original recording was changed. Evidence access is disabled.')
        _verified[key] = signature
    return IBT(path, False)


def validate_attachment(old, candidate):
    for key in ['car', 'car_id', 'track_id', 'configuration', 'recorded_at', 'tick_rate', 'setup', 'setup_name', 'build', 'available_channels']:
        if old['metadata'].get(key) != candidate['metadata'].get(key):
            raise ValueError(f'Recording does not match this run ({key}). Import it as a new run.')
    if len(old['laps']) != len(candidate['laps']):
        raise ValueError('Recording lap boundaries do not match. Import as a new run.')
    for a, b in zip(old['laps'], candidate['laps']):
        for key in ['number', 'session', 'duration', 'complete', 'eligible', 'traces']:
            if a.get(key) != b.get(key):
                raise ValueError(f'Recording evidence does not match ({key}). Import as a new run.')
