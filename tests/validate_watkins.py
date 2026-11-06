"""Read-only acceptance check using imported Watkins Glen Boot recordings."""
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from analysis import eligible
from corner_map import geo_distance, load_profile, map_for
from lap_comparison import calculate
from live_maps import get_map
from recordings import digest, open_recording


def main():
    candidates = [server.load_run(json.loads(p.read_text(encoding='utf-8'))['id'])
                  for p in (server.DATA / 'index').glob('*.json')]
    runs = [r for r in candidates if str(r['metadata'].get('track_id')) == '434'
            and r['metadata'].get('configuration') == 'Boot' and eligible(r)]
    assert len(runs) >= 2, 'Import two Watkins Glen Boot recordings with clean laps first.'
    profile = load_profile(server.DATA, runs[0], builtin=True)
    assert len(profile['corners']) == 11
    assert digest(server.DATA / 'sources' / 'watkins-glen-imsa-reference.pdf') == profile['source']['sha256']
    assert get_map(server.DATA, 434, 'Boot')['status'] == 'outline'
    assert get_map(server.DATA, 435, 'Classic Boot')['status'] == 'lap_progress'
    for run in runs:
        assert map_for(server.DATA, run)['status'] == 'validated', run['filename']
        lap = min(eligible(run), key=lambda l: l['duration'])
        a = open_recording(server.DATA, run).read_samples(
            ['LapDistPct', 'Lat', 'Lon', 'SteeringWheelAngle'], lap['sample_start'], lap['sample_stop'])
        distances = a['LapDistPct'] * 100
        for corner in profile['corners']:
            near = abs(distances - corner['reference']) < .2
            expected = 1 if corner['direction'] == 'left' else -1
            assert np.median(a['SteeringWheelAngle'][near]) * expected > 0, (run['filename'], corner['label'])
            i = int(np.argmin(abs(distances - corner['reference'])))
            assert geo_distance(a['Lat'][i], a['Lon'][i], corner['lat'], corner['lon']) < 35
    # Keep the reference in the same car where possible.
    a = runs[0]
    b = next((r for r in runs[1:] if r['metadata']['car_id'] == a['metadata']['car_id']), runs[1])
    body = {f'{side}_lap_id': min(eligible(r), key=lambda l: l['duration'])['id']
            for side, r in [('selected', a), ('reference', b)]}
    c = calculate(server.DATA, a, b, body)
    assert c['provenance']['source'] in ('confirmed corner profile', 'saved comparison map')
    assert any(s['label'] == 'Bus Stop' and s['kind'] == 'corner' for s in c['segments'])
    pieces = sorted(p for row in c['segments'] for p in row['ranges'])
    assert pieces[0][0] == 0 and pieces[-1][1] == 100
    assert all(x[1] == y[0] for x, y in zip(pieces, pieces[1:]))
    for side in ('selected', 'reference'):
        assert abs(sum(s[side]['elapsed_s'] for s in c['segments']) - c[side]['duration']) < 1e-8
        assert c['map'][side]
    assert abs(sum(s['delta_s'] for s in c['segments']) - c['delta_s']) < 1e-8
    print(f"Validated {len(runs)} real Boot recordings: source checksum, T1-T11 GPS/handedness, live outline, "
          f"Bus Stop comparison, full-lap partition and reconciled delta ({c['delta_s']:+.6f} s).")


if __name__ == '__main__':
    main()
