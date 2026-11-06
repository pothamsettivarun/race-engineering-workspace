"""Accelerated eight-hour feed at 20 Hz, followed by a 64-car load check."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live import LiveService
from live_strategy import measurements
from test_live import sample


def main():
 with tempfile.TemporaryDirectory() as directory:
  tick_time=[0.0]
  live=LiveService(Path(directory)/'data',clock=lambda:tick_time[0])
  start=time.perf_counter()
  frame=sample(car_count=1);v=frame['values'];v['SessionLapsTotal']=1000
  try:
   for tick in range(8*3600*20+1):
    t=tick/20;tick_time[0]=t;lap=int(t/60);pct=t%60/60
    v.update(SessionTime=t,SessionTick=tick,LapCompleted=lap,Lap=lap+1,LapDistPct=pct,FuelLevel=80-2*(lap%35+pct))
    v['CarIdxLapCompleted']=[lap];v['CarIdxLapDistPct']=[pct];v['CarIdxEstTime']=[pct*60]
    pit=lap%35==0 and lap>0 and pct<.08
    v['CarIdxOnPitRoad']=[pit];v['OnPitRoad']=pit
    live.ingest(frame)
    if tick%120000==0 and tick:print(f'Simulated {t/3600:.1f} hours; trace {len(live.trace)}; laps {len(live.laps)}.',flush=True)
   assert len(live.trace)==1200
   assert len(live.laps)==480
   m=measurements(live.laps);assert m['sample_count']==5 and abs(m['pace_seconds']-60)<1e-6 and abs(m['burn_litres']-2)<1e-6,m
   assert len(live.events)<=1000 and len(live.pits)<=1000 and len(live.car_states)<=128
   own_count=len(live.laps)
   frame=sample(28800.05,tick=576001,lap=480,pct=.001,fuel=30,car_count=64)
   load_start=time.perf_counter()
   for i in range(100):
    tick_time[0]=28800.05+i*.05;frame['values']['SessionTime']=tick_time[0];frame['values']['SessionTick']=576001+i
    live.ingest(frame)
    snap=live.snapshot()
    assert len(snap['cars'])==64 and len(snap['trace'])<=3
   load_elapsed=time.perf_counter()-load_start
   assert load_elapsed/100<.05,load_elapsed
   live._checkpoint()
   record=live.history(live.record['id']);assert len(record['laps'])==own_count
   print(f'Passed eight hours at 20 Hz: 576,001 samples, 480 laps, bounded 1,200-sample trace. 64-car ingest + snapshot: {load_elapsed/100*1000:.2f} ms average. Wall time {time.perf_counter()-start:.1f} s.',flush=True)
  finally:live.close()


if __name__=='__main__':main()
