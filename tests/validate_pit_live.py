"""Opt-in parked Corvette Test-session check; restore the original pit selections."""
import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live import LiveService,SDKSource


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if not args.apply:raise SystemExit('Use --apply only for an authorized, parked Corvette Test session.')
    with urlopen('http://127.0.0.1:8765/api/live/snapshot',timeout=3) as f:running=json.load(f)
    if running['pit_control']['enabled']:raise SystemExit('Disable Stint pit control before this standalone collector check.')
    with tempfile.TemporaryDirectory() as directory:
        source=SDKSource();live=LiveService(Path(directory),source=source)
        def feed():
            frame=source.read()
            if not frame:raise RuntimeError(source.error)
            live.ingest(frame)
            return frame
        frame=feed();s=live.state['session']
        if (frame['metadata']['WeekendInfo'].get('EventType')!='Test' or s['car']!='Chevrolet Corvette Z06 GT3.R'
            or (live.state['player'].get('Speed') or 0)>1 or live.state['player'].get('PlayerCarInPitStall') is not False
            or live.state['player'].get('PitstopActive') is not False):
            source.close();raise SystemExit('This check requires a parked Corvette in a Test session.')
        original=live.pit.status()['actual'];session=live.pit._binding();report={'before':original,'results':[]}
        output=Path('test-results/pit-live-acceptance.json')
        output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        live.pit.authorize(True,'stint-test',engineer_name='Stint test')
        def apply(selections):
            feed()
            if live.pit._binding()!=session:raise RuntimeError('Test session or driver changed.')
            body={'id':uuid.uuid4().hex,'collector_id':live.collector_id,'session_id':s['id'],
                  'driver_id':s['driver_id'],'player_car_idx':s['player_car_idx'],'revision':live.pit.revision,
                  'sequence':live.sequence,**selections}
            live.pit.submit(body,'stint-test',local=True,actor_name='Stint test')
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                feed();live.pit.process()
                result=live.pit.status()['commands'][-1]
                if result['status']!='pending':
                    live.pit.process();return live.pit.status()['commands'][-1]
                time.sleep(.05)
            raise RuntimeError('Live check did not resolve.')
        try:
            pressures={c:round(v) for c,v in original['pressures_kpa'].items()}
            test={ 'fuel_litres':2,'fuel_enabled':True,'auto_fuel':False,'tear_off':True,'fast_repair':False,
                   'tires':{'lf':True,'rf':True,'lr':True,'rr':True},
                   'pressures_kpa':{**pressures,'lf':pressures['lf']+3},'tire_compound':1}
            report['results'].append(apply(test))
        finally:
            try:
                if live.pit._binding()==session:
                    restore={k:original[k] for k in ['fuel_enabled','auto_fuel','tear_off','fast_repair','tires','tire_compound']}
                    restore['fuel_litres']=round(original['fuel_litres']);restore['pressures_kpa']={c:round(v) for c,v in original['pressures_kpa'].items()}
                    report['results'].append(apply({**restore,'tires':{c:True for c in restore['tires']}}))
                    report['results'].append(apply(restore))
                    report['after']=live.pit.status()['actual']
            finally:
                source.close();live.close();output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'results':[{'status':r['status'],'confirmed_fields':r['confirmed_fields'],'chat':r.get('private_confirmation'),'message':r['message']} for r in report['results']],
                          'before':report['before'],'after':report.get('after')},indent=2))


if __name__=='__main__':main()
