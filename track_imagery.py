"""Locally cached, georeferenced aerial photographs; never infer edges from a lap."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from corner_map import key_for, profile_key
from live_maps import get_map

SERVICE = 'https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer'
ATTRIBUTION = 'USGS, USDA, The National Map · NAIP aerial imagery'
_lock = threading.Lock()
_failures = {}


def unavailable(message):
    return {'status': 'unavailable', 'message': message}


def bounds_for(points):
    if not points:
        raise ValueError('A recorded GPS outline is needed to locate the aerial map.')
    lat = [p['lat'] for p in points]
    lon = [p['lon'] for p in points]
    latitude = sum(lat) / len(lat)
    padding_lat = 80 / 111320
    padding_lon = 80 / (111320 * math.cos(math.radians(latitude)))
    bounds = [min(lon) - padding_lon, min(lat) - padding_lat,
              max(lon) + padding_lon, max(lat) + padding_lat]
    west, south, east, north = bounds
    if not all(math.isfinite(v) for v in bounds) or not -125 <= west < east <= -66 or not 24 <= south < north <= 50:
        raise ValueError('The current aerial source covers the continental US. Aerial imagery is unavailable for this location.')
    if east - west > .3 or north - south > .3:
        raise ValueError('The recorded outline is too large for a circuit aerial map.')
    return bounds


def fetch_json(url):
    with urlopen(Request(url, headers={'User-Agent': 'Stint local telemetry workspace'}), timeout=15) as response:
        return json.loads(response.read(1_000_000))


def download(bounds):
    west, south, east, north = bounds
    size = 4000 / max(east - west, north - south)
    width, height = max(1, round((east - west) * size)), max(1, round((north - south) * size))
    params = {'bbox': ','.join(str(v) for v in bounds), 'bboxSR': 4326, 'imageSR': 4326,
              'size': f'{width},{height}', 'format': 'jpg', 'f': 'json', 'compressionQuality': 95,
              'renderingRule': json.dumps({'rasterFunction': 'NaturalColor'}),
              'interpolation': 'RSP_BilinearInterpolation'}
    info = fetch_json(SERVICE + '/exportImage?' + urlencode(params))
    url = info.get('href', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'imagery.nationalmap.gov':
        raise ValueError('The aerial source did not return an image.')
    extent = info.get('extent', {})
    values = [extent.get(k) for k in ('xmin', 'ymin', 'xmax', 'ymax')]
    if (any(type(v) not in (float, int) or not math.isfinite(v) for v in values)
            or extent.get('spatialReference', {}).get('wkid') != 4326
            or any(abs(a - b) > .01 for a, b in zip(bounds, values))
            or not values[0] < values[2] or not values[1] < values[3]):
        raise ValueError('The aerial image has an unsupported geographic extent.')
    with urlopen(Request(url, headers={'User-Agent': 'Stint local telemetry workspace'}), timeout=15) as response:
        image = response.read(32_000_001)
    if not image.startswith(b'\xff\xd8') or len(image) > 32_000_000:
        raise ValueError('The aerial source returned an invalid image.')
    return image, values


def get_for(data, run, write, force=False):
    key = profile_key(run)
    root = Path(data) / 'track-imagery' / key
    with _lock:
        if not force and (root / 'metadata.json').exists() and (root / 'aerial.jpg').exists():
            saved = json.loads((root / 'metadata.json').read_text(encoding='utf-8'))
            if (saved.get('track_id'), saved.get('configuration')) == key_for(run) and saved.get('schema') == 1:
                return saved
        failure = _failures.get(key)
        if not force and failure and time.monotonic() - failure[0] < 300:
            return unavailable(failure[1])
        try:
            mapping = get_map(data, *key_for(run))
            if not mapping['points']:
                from corner_map import map_for
                mapping['points'] = (map_for(data, run).get('outline') or {}).get('points', [])
            bounds = bounds_for(mapping['points'])
            image, extent = download(bounds)
            sha = hashlib.sha256(image).hexdigest()
            metadata = {'schema': 1, 'status': 'available', 'track_id': key_for(run)[0],
                        'configuration': key_for(run)[1], 'bounds': extent,
                        'image_url': f'/api/track-imagery/image?key={key}&v={sha[:16]}',
                        'source_url': SERVICE, 'attribution': ATTRIBUTION, 'sha256': sha,
                        'message': 'Real-world aerial imagery; simulator layout and GPS alignment may differ.',
                        'retrieved_at': datetime.now(timezone.utc).isoformat()}
            root.mkdir(parents=True, exist_ok=True)
            temporary = root / 'aerial.tmp'
            temporary.write_bytes(image)
            temporary.replace(root / 'aerial.jpg')
            write(root / 'metadata.json', metadata)
            _failures.pop(key, None)
            return metadata
        except (OSError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else 'Aerial imagery could not be loaded. Check your connection and retry.'
            _failures[key] = (time.monotonic(), message)
            return unavailable(message)


def image_file(data, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{24}', key):
        raise ValueError('Invalid aerial image identifier.')
    return Path(data) / 'track-imagery' / key / 'aerial.jpg'
