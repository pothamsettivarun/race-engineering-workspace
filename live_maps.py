"""Configuration-specific outlines shared by imports and the live pitwall."""
import json
import math
from pathlib import Path
from corner_map import outline, profile_key, load_profile
from recordings import open_recording


def valid_points(points):
    if not isinstance(points, list) or len(points) < 30: return False
    previous = -1
    for p in points:
        if not isinstance(p, dict): return False
        values = [p.get(k) for k in ('distance', 'lat', 'lon')]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values): return False
        d, lat, lon = values
        if not 0 <= d <= 100 or d < previous or not -90 <= lat <= 90 or not -180 <= lon <= 180: return False
        previous = d
    return points[0]['distance'] <= 3 and points[-1]['distance'] >= 97


def cache_run(data, run, write):
    if not run.get('recording'): return False
    try:
        path = outline(open_recording(data, run), run)
        if not path or not valid_points(path['points']): return False
        record = {'track_id': str(run['metadata'].get('track_id')),
                  'configuration': run['metadata'].get('configuration', ''),
                  'points': path['points'], 'source_run_id': run['id'], 'display_only': True}
        write(Path(data)/'track-outlines'/(profile_key(run)+'.json'), record)
        return True
    except (ValueError, OSError, KeyError):
        return False


def get_map(data, track_id, configuration):
    run = {'metadata': {'track_id': str(track_id), 'configuration': configuration}}
    try:
        profile = load_profile(data, run)
    except (ValueError, OSError):
        profile = None
    if profile and (str(profile.get('track_id')), profile.get('configuration', '')) != (str(track_id), configuration):
        profile = None
    path = Path(data)/'track-outlines'/(profile_key(run)+'.json')
    cached = None
    try:
        if path.exists(): cached = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError): pass
    points = cached.get('points', []) if cached and (cached.get('track_id'), cached.get('configuration')) == (str(track_id), configuration) else []
    if not valid_points(points): points = profile.get('geometry', []) if profile else []
    if not valid_points(points): points = []
    corners = []
    if profile and profile.get('validated') and points:
        # Confirm imported geometry against profile anchors before applying numbers.
        from corner_map import geo_distance
        errors = []
        for p in profile.get('geometry', []):
            q = min(points, key=lambda q: abs(q['distance']-p['distance']))
            errors.append(float(geo_distance(q['lat'], q['lon'], p['lat'], p['lon'])))
        if not errors or sorted(errors)[int((len(errors)-1)*.95)] <= 65:
            corners = profile.get('corners', [])
    return {'track_id': str(track_id), 'configuration': configuration, 'points': points,
            'corners': corners, 'revision': profile.get('revision') if corners else None,
            'source': 'imported recording' if cached and points == cached.get('points') else 'saved track profile',
            'display_only': True, 'status': 'outline' if points else 'lap_progress'}


def backfill(data, write, stopped):
    import gzip
    for path in (Path(data)/'index').glob('*.json'):
        if stopped.is_set(): break
        try:
            index = json.loads(path.read_text(encoding='utf-8'))
            if (Path(data)/'track-outlines'/(profile_key(index)+'.json')).exists(): continue
            with gzip.open(Path(data)/'runs'/(index['id']+'.json.gz'), 'rt', encoding='utf-8') as f:
                cache_run(data, json.load(f), write)
        except (ValueError, OSError, KeyError): continue
