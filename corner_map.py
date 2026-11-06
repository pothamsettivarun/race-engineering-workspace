"""Versioned, configuration-specific corner references; GPS is display/navigation."""
import copy
import json
import math
import re
import uuid
from pathlib import Path
import numpy as np
from analysis import eligible
from recordings import open_recording

ROOT = Path(__file__).resolve().parent


def key_for(run):
    return str(run['metadata'].get('track_id')), str(run['metadata'].get('configuration', ''))


def profile_key(run):
    import hashlib
    return hashlib.sha256(json.dumps(key_for(run)).encode()).hexdigest()[:24]


def load_profile(data, run, builtin=False):
    index = Path(data) / 'track-profiles' / (profile_key(run) + '.json')
    if not builtin and index.exists():
        return json.loads(index.read_text(encoding='utf-8'))
    for path in (ROOT / 'profiles').glob('*.json'):
        profile = json.loads(path.read_text(encoding='utf-8'))
        if (str(profile['track_id']), profile['configuration']) == key_for(run):
            return profile
    return None


def geo_distance(lat, lon, lat2, lon2):
    return np.hypot((np.asarray(lat)-lat2)*111320,
                    (np.asarray(lon)-lon2)*111320*math.cos(math.radians(lat2)))


def outline(log, run):
    if not {'Lat','Lon'}.issubset(log.headers):
        return None
    candidates = sorted(eligible(run), key=lambda l: l['duration'])
    for lap in candidates:
        names = ['Lat','Lon','LapDistPct','SessionTime']
        if 'OnPitRoad' in log.headers:names.append('OnPitRoad')
        a = log.read_samples(names, lap['sample_start'], lap['sample_stop'])
        valid = np.isfinite(a['Lat']) & np.isfinite(a['Lon']) & (abs(a['Lat']) > 1) & (abs(a['Lon']) > 1)
        valid &= (abs(a['Lat']) <= 90) & (abs(a['Lon']) <= 180)
        if 'OnPitRoad' in a:valid &= ~a['OnPitRoad'].astype(bool)
        ids = np.flatnonzero(valid)
        if len(ids) < 30 or len(ids) < .95*len(valid):continue
        dt = np.diff(a['SessionTime'][ids])
        meters = np.hypot(np.diff(a['Lat'][ids])*111320,
            np.diff(a['Lon'][ids])*111320*math.cos(math.radians(float(np.median(a['Lat'][ids])))))
        if np.any(dt < 0) or np.any(dt > .5) or np.any(meters > dt*150+3):continue
        if a['LapDistPct'][ids[0]] > .03 or a['LapDistPct'][ids[-1]] < .97:continue
        # Reduced map geometry is for UI only; evidence is always read from IBT.
        shown = ids[np.unique(np.linspace(0,len(ids)-1,min(600,len(ids)),dtype=int))]
        return {'lap_id':lap['id'],'display_only':True,'points':[
            {'distance':float(a['LapDistPct'][i]*100),'lat':float(a['Lat'][i]),'lon':float(a['Lon'][i])} for i in shown]}
    return None


def map_for(data, run):
    profile = load_profile(data, run)
    result = {'profile':profile,'outline':None,'status':'unmapped','message':'No numbered profile for this configuration.'}
    if run.get('recording'):
        result['outline'] = outline(open_recording(data, run), run)
    if not profile:return result
    if not profile.get('validated'):
        result.update(status='unconfirmed',message='Confirm this corner profile before using numbered analysis.')
        return result
    if result['outline'] and profile.get('geometry'):
        points = result['outline']['points']
        distances = np.array([p['distance'] for p in points])
        errors=[]
        for p in profile['geometry']:
            q=points[int(np.argmin(abs(distances-p['distance'])))]
            errors.append(float(geo_distance(q['lat'],q['lon'],p['lat'],p['lon'])))
        if np.percentile(errors,95) > 65:
            result.update(status='mismatch',message='Recorded geometry differs from this numbered profile. Review the configuration/map.')
            return result
    elif run.get('recording'):
        log=open_recording(data,run)
        if {'Lat','Lon'}.issubset(log.headers):
            result.update(status='invalid_position',message='Position samples are invalid or discontinuous; numbered map needs review.')
            return result
    result.update(status='validated',message=('Validated numbered profile · '+profile['revision']+
        (' · recorded path' if result['outline'] else ' · lap-distance mapping; position unavailable')))
    return result


def mapped_corners(mapping):
    return copy.deepcopy(mapping['profile']['corners']) if mapping['status']=='validated' else []


def references(text, available):
    found=set()
    # T8–T10, turns 8-10, Turn 13, and T2/T4 all use the same definitions.
    for m in re.finditer(r'\b(?:T|turns?\s*)(\d{1,2})(?:\s*[–—-]\s*(?:T|turns?\s*)?(\d{1,2}))?\b',text,re.I):
        a=int(m[1]);b=int(m[2] or a)
        if not 1<=a<=b<=99:raise ValueError('Invalid corner range in the question.')
        found.update(f'T{i}' for i in range(a,b+1))
    unknown=found-{c['label'] for c in available}
    if unknown:raise ValueError('No validated mapping for '+', '.join(sorted(unknown))+'. Attach the recording or confirm its corner map.')
    return sorted(found,key=lambda x:int(x[1:]))


def save_profile(data, run, profile, atomic_json):
    if not isinstance(profile,dict) or key_for(run)!=(str(profile.get('track_id')),profile.get('configuration')):
        raise ValueError('Profile must match the exact track configuration.')
    corners=profile.get('corners',[])
    if not 1<=len(corners)<=100:raise ValueError('Enter between 1 and 100 corners.')
    previous=-1
    for i,c in enumerate(corners,1):
        if c.get('label')!=f'T{i}':raise ValueError('Corner labels must be sequential T1, T2, …')
        vals=[c.get(k) for k in ['start','reference','end']]
        if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in vals):raise ValueError('Corner distances must be finite numbers.')
        start,ref,end=vals
        if not 0<=start<=ref<=end<=100 or start==end or ref<=previous:raise ValueError('Corner references must increase; each entry ≤ reference ≤ exit within 0–100%.')
        previous=ref
    sections = profile.get('comparison_sections', [])
    if not isinstance(sections, list) or len(corners) + len(sections) > 100:
        raise ValueError('Enter at most 100 corners and comparison sections.')
    labels = {c['label'] for c in corners}
    for section in sections:
        if not isinstance(section, dict):raise ValueError('Each comparison section needs a label and distances.')
        label = section.get('label')
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or label in labels:
            raise ValueError('Comparison section labels must be unique and contain 1–80 characters.')
        labels.add(label)
        vals = [section.get(k) for k in ['start', 'reference', 'end']]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in vals):
            raise ValueError('Comparison section distances must be finite numbers.')
        start, ref, end = vals
        if not 0 <= start <= ref <= end <= 100 or start == end:
            raise ValueError('Each comparison section needs entry ≤ reference ≤ exit within 0–100%.')
    source=profile.get('source',{})
    if profile.get('validated') and (not source.get('url') or not str(source['url']).startswith('https://') or not source.get('title')):
        raise ValueError('Confirmed numbering needs a source title and HTTPS reference URL.')
    saved=copy.deepcopy(profile);saved['revision']='user-'+uuid.uuid4().hex[:12]
    if saved.get('validated') and run.get('recording'):
        path=outline(open_recording(data,run),run)
        if path:
            points=path['points']
            saved['geometry']=[points[int(i)] for i in np.linspace(0,len(points)-1,min(80,len(points)),dtype=int)]
            for c in saved['corners'] + saved.get('comparison_sections', []):
                nearest=min(points,key=lambda p:abs(p['distance']-c['reference']))
                c['lat'],c['lon']=nearest['lat'],nearest['lon']
    root=Path(data)/'track-profiles';root.mkdir(parents=True,exist_ok=True)
    atomic_json(root/(saved['revision']+'.json'),saved)
    atomic_json(root/(profile_key(run)+'.json'),saved)
    return saved
