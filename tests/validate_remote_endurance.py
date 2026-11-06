"""Accelerated eight-hour feed with eight concurrently consuming remote streams."""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib import request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live import LiveService
from live_strategy import measurements
from remote import RemoteGateway
from test_live import sample
from test_remote import FakeSDK,ENGINEER,ORIGIN


def main():
 with tempfile.TemporaryDirectory() as directory:
  clock=[0.];sdk=FakeSDK();live=LiveService(directory,sdk,clock=lambda:clock[0])
  gateway=RemoteGateway(directory,Path(__file__).resolve().parents[1],live,port=0)
  gateway.configure({'enabled':True,'origin':ORIGIN,'users':[{'login':ENGINEER,'role':'engineer'}]})
  stopped=threading.Event();counts=[0]*8;errors=[];responses=[];threads=[]
  frame=sample(car_count=1);v=frame['values'];v.update(SessionLapsTotal=1000,PitSvFlags=112,PitSvFuel=12,PitstopActive=False)
  live.ingest(frame)
  def reader(index,response):
   try:
    while not stopped.is_set():
     line=response.readline()
     if not line:break
     if line.startswith(b'data: '):
      snapshot=json.loads(line[6:]);assert len(snapshot['trace'])<=3;assert len(snapshot['pit_control']['commands'])<=12
      counts[index]+=1
   except (OSError,ValueError,AssertionError) as exc:
    if not stopped.is_set():errors.append(str(exc))
  start=time.perf_counter()
  try:
   base=f'http://127.0.0.1:{gateway.http.server_port}'
   for i in range(8):
    response=request.urlopen(request.Request(base+'/api/live/events',headers={'Tailscale-User-Login':ENGINEER}),timeout=10)
    responses.append(response);thread=threading.Thread(target=reader,args=(i,response),daemon=True);thread.start();threads.append(thread)
   assert len(gateway.streams)==8
   for tick in range(8*3600*20+1):
    t=tick/20;clock[0]=t;lap=int(t/60);pct=t%60/60
    v.update(SessionTime=t,SessionTick=tick,LapCompleted=lap,Lap=lap+1,LapDistPct=pct,FuelLevel=80-2*(lap%35+pct))
    v['CarIdxLapCompleted']=[lap];v['CarIdxLapDistPct']=[pct];v['CarIdxEstTime']=[pct*60]
    pit=lap%35==0 and lap>0 and pct<.08;v['CarIdxOnPitRoad']=[pit];v['OnPitRoad']=pit
    live.ingest(frame)
    if tick%120000==0 and tick:print(f'Simulated {t/3600:.1f} hours; eight stream counts {counts}; trace {len(live.trace)}.',flush=True)
   assert len(live.trace)==1200 and len(live.laps)==480
   m=measurements(live.laps);assert m['sample_count']==5 and abs(m['burn_litres']-2)<1e-6
   assert len(live.events)<=1000 and len(live.pits)<=1000 and len(live.pit.observations)<=80
   assert len(gateway.clients)==1 and all(c>10 for c in counts) and not errors,(counts,errors)
   frame=sample(28800.05,tick=576001,lap=480,pct=.001,car_count=64)
   v=frame['values'];v.update(PitSvFlags=112,PitSvFuel=12,PitstopActive=False)
   start_load=time.perf_counter()
   for i in range(100):
    clock[0]=28800.05+i*.05;v.update(SessionTime=clock[0],SessionTick=576001+i)
    live.ingest(frame);snapshot=live.snapshot();assert len(snapshot['cars'])==64
   average=(time.perf_counter()-start_load)/100
   assert average<.05,average
   print(f'Passed: 576,001 samples, 480 laps, eight remote streams, bounded trace/observations/client state; 64-car ingest + snapshot {average*1000:.2f} ms average; wall time {time.perf_counter()-start:.1f}s.',flush=True)
  finally:
   stopped.set();gateway.close();live.close()
   for r in responses:r.close()
   for thread in threads:thread.join(timeout=2)


if __name__=='__main__':main()
