"""Read-only validation of the shipped Indy profile against the attached recording."""
import sys,json,numpy as np
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from recordings import open_recording,digest
from corner_map import map_for,mapped_corners,geo_distance
from analysis import eligible
run=server.load_run('01e231604c3547caa9181276574dab55');mapping=map_for(server.DATA,run)
assert mapping['status']=='validated';profile=mapping['profile'];assert len(profile['corners'])==14
reference=server.DATA/'sources'/'indy-numbered-reference.pdf';assert digest(reference)==profile['source']['sha256']
log=open_recording(server.DATA,run);lap=min(eligible(run),key=lambda l:l['duration']);a=log.read_samples(['LapDistPct','Lat','Lon','SteeringWheelAngle'],lap['sample_start'],lap['sample_stop']);distance=a['LapDistPct']*100
for corner in profile['corners']:
    near=abs(distance-corner['reference'])<.2
    steer=float(np.median(a['SteeringWheelAngle'][near]));expected=1 if corner['direction']=='left' else -1
    assert steer*expected>0,(corner['label'],steer)
    i=int(np.argmin(abs(distance-corner['reference'])))
    assert geo_distance(a['Lat'][i],a['Lon'][i],corner['lat'],corner['lon'])<15
    assert corner['start']<=corner['reference']<=corner['end']
print('Validated all T1-T14: source checksum, exact configuration, GPS references, handedness/order, lap-distance windows and start/finish profile.')
