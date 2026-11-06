"""Full-resolution calculations and scoped, paginated read-only AI evidence."""
import copy
import json
import math
import secrets
import threading
import time
import numpy as np
from analysis import eligible, summary, corners
from recordings import open_recording
from corner_map import map_for, mapped_corners, references


def encode(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def native(value):
    if isinstance(value,(np.bool_,bool)):return bool(value)
    if isinstance(value,(np.integer,int)):return int(value)
    value=float(value)
    return value if math.isfinite(value) else None


def channel_info(name, header):
    result={'unit':header['unit'],'type':header['type']}
    if name in ['Brake','Throttle']:
        result['native_encoding']='fraction 0–1; SDK unit label may say %. Multiply by 100 only for displayed percentages.'
    return result


def events(mask,t,dist,offset,values):
    edges=np.diff(np.r_[False,mask,False].astype(int))
    result=[]
    for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
        duration=float(t[min(b,len(t)-1)]-t[a])
        result.append({'sample_start':int(offset+a),'sample_stop':int(offset+b),
                       'distance_start_pct':float(dist[a]*100),'distance_end_pct':float(dist[b-1]*100),
                       'session_time':float(t[a]),'duration_s':duration,'sustained':duration>=.1-1e-8,
                       'peak_native':native(np.max(values[a:b]))})
    return result


def measure(log,lap,zone):
    names=['SessionTime','LapDistPct','Speed']+[n for n in ['Brake','Throttle','SteeringWheelAngle','YawRate','LatAccel','LongAccel','FuelLevel'] if n in log.headers]
    data=log.read_samples(names,lap['sample_start'],lap['sample_stop'])
    ids=np.flatnonzero((data['LapDistPct']*100>=zone['start']) & (data['LapDistPct']*100<=zone['end']))
    if len(ids)<2:return None
    first,last=int(ids[0]),int(ids[-1])+1
    a={k:v[first:last] for k,v in data.items()};t=a['SessionTime'];d=a['LapDistPct'];offset=lap['sample_start']+first
    point=lambda i:{'sample':int(offset+i),'session_time':float(t[i]),'distance_pct':float(d[i]*100),
                    'values':{k:native(v[i]) for k,v in a.items() if k not in ['SessionTime','LapDistPct']}}
    speed=np.where(np.isfinite(a['Speed']),a['Speed'],np.inf);minimum=int(np.argmin(speed))
    out={'lap_id':lap['id'],'lap_number':lap['number'],'corner':zone['label'],'range_pct':[zone['start'],zone['end']],
         'sample_start':offset,'sample_stop':offset+len(t),'sample_count':len(t),'elapsed_s':float(t[-1]-t[0]),
         'entry':point(0),'minimum_speed_location':point(minimum),'exit':point(len(t)-1),
         'event_rules':{'brake_above':.05,'throttle_above':.10,'near_full_throttle_above':.95,'persistence_s':.1},
         'events':{},'measurements':{},'warnings':[]}
    for channel,threshold,name in [('Brake',.05,'braking'),('Throttle',.10,'throttle'),('Throttle',.95,'near_full_throttle')]:
        if channel in a:
            out['events'][name]=events(np.isfinite(a[channel]) & (a[channel]>threshold),t,d,offset,a[channel])
    dt=np.r_[np.diff(t),0]
    if 'Brake' in a and 'Throttle' in a:
        out['measurements']['pedal_overlap_s']=float(np.sum(dt[(a['Brake']>.05)&(a['Throttle']>.10)]))
    if 'Brake' in a:
        valid=np.isfinite(a['Brake'])
        if valid.any():
            peak=int(np.argmax(np.where(valid,a['Brake'],-np.inf)))
            out['peak_braking']=point(peak)
            released=events(valid & (a['Brake']<=.05),t,d,offset,a['Brake'])
            release=next((e for e in released if e['sample_start']>=offset+peak and e['sustained']),None)
            out['brake_release']=point(release['sample_start']-offset) if release else None
            # Preserve every original value throughout the release via read_samples; no fitted curve.
            out['measurements']['peak_to_release_s']=float(release['session_time']-t[peak]) if release else None
            pickups=[e for e in out['events'].get('throttle',[]) if e['sample_start']>=offset+peak and e['sustained']]
            out['throttle_pickup_after_brake_peak']=point(pickups[0]['sample_start']-offset) if pickups else None
            braking=next((e for e in out['events'].get('braking',[]) if e['sustained']),None)
            out['braking_onset']=point(braking['sample_start']-offset) if braking else None
    for channel in ['SteeringWheelAngle','YawRate','LatAccel','LongAccel']:
        if channel in a:
            values=a[channel][np.isfinite(a[channel])]
            out['measurements'][channel+'_abs_p95_native']=float(np.percentile(abs(values),95)) if len(values) else None
    if 'FuelLevel' in a:out['fuel_start_l']=native(a['FuelLevel'][0]);out['fuel_end_l']=native(a['FuelLevel'][-1])
    if np.any(np.diff(t)>.5):out['warnings'].append('Telemetry gap inside the range')
    out['warnings'] += [n+' has invalid values' for n,v in a.items() if not np.isfinite(v).all()]
    return out


def representative(run,selected=None):
    laps=eligible(run)
    if not laps:return []
    median=summary(run)['median']
    result=laps[:2]+laps[-2:]+[min(laps,key=lambda l:l['duration']),min(laps,key=lambda l:abs(l['duration']-median))]
    result += [l for l in laps if l['id']==selected]
    return list(dict.fromkeys(l['id'] for l in result))


TOOLS=[
    {'type':'function','name':'list_evidence','description':'List scoped runs/laps, native channel units and full-resolution corner measurements. Continue pages using the returned cursor.',
     'inputSchema':{'type':'object','properties':{'run_id':{'type':'string'},'cursor':{'type':'string'}},'additionalProperties':False}},
    {'type':'function','name':'read_samples','description':'Read consecutive ORIGINAL telemetry samples. No downsampling, interpolation or rounding. Corner labels require a validated profile. Use continuation cursor until complete; returned ranges document inspection coverage.',
     'inputSchema':{'type':'object','properties':{'run_id':{'type':'string'},'lap_id':{'type':'integer'},'channels':{'type':'array','items':{'type':'string'},'maxItems':16},'corner':{'type':'string'},'distance_range':{'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2},'time_range':{'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2},'cursor':{'type':'string'}},'additionalProperties':False}},
    {'type':'function','name':'read_knowledge','description':'Retrieve cited engineering source excerpts for this measured car.',
     'inputSchema':{'type':'object','properties':{'query':{'type':'string'},'kind':{'type':'string','enum':['manual','video transcript','private note']}},'required':['query'],'additionalProperties':False}}
]


class Investigation:
    def __init__(self,data,run,baseline=None,scope=None,retrieve=None,progress=None,cancel=None):
        self.data=data;self.runs={r['id']:copy.deepcopy(r) for r in [run,baseline] if r}
        self.primary=run['id'];self.retrieve=retrieve;self.progress=progress or (lambda x:None)
        self.cancel=cancel or threading.Event();self.calls=0;self.bytes=0;self.cursors={};self.logs={};self.maps={};self.measures={}
        self.manifest={'mode':'targeted inspection','calculated':[],'supplied_measurements':[],'sections':[],'knowledge':[],
                       'calls':[],'limits':[],'recordings':{},'profiles':{},'rules':'Original native-unit samples. Chart display traces are not AI evidence.'}
        self.scope=scope or {'kind':'run'}
        for r in self.runs.values():
            self.maps[r['id']]=map_for(data,r)
            self.manifest['profiles'][r['id']]=copy.deepcopy(self.maps[r['id']].get('profile'))
            self.manifest['recordings'][r['id']]=copy.deepcopy(r.get('recording'))
            if r.get('recording'):self.logs[r['id']]=open_recording(data,r)
        self.zone_selection=[]

    def resolve_scope(self,question):
        available=mapped_corners(self.maps[self.primary])
        explicit=references(question,available)
        chosen=self.scope.get('corners',[]) if self.scope.get('kind')=='corners' else []
        if self.scope.get('kind')=='corners' and (not isinstance(chosen,list) or not chosen):raise ValueError('Select at least one numbered corner.')
        if any(c not in [z['label'] for z in available] for c in chosen):raise ValueError('Selected corners have no validated map.')
        if explicit and self.scope.get('kind')!='run':
            if self.scope.get('kind')=='corners' and set(chosen)!=set(explicit):raise ValueError('Question corner references differ from the selected scope. Select '+', '.join(explicit)+' or Whole run.')
            if self.scope.get('kind')=='region':
                lo,hi=self.scope['distance_range']
                if any(z['start']<lo or z['end']>hi for z in available if z['label'] in explicit):raise ValueError('The selected chart range does not cover the corners named in the question. Choose Whole run or those corners.')
        self.zone_selection=explicit or chosen
        self.scope['resolved_corners']=self.zone_selection
        if self.scope.get('kind')=='region':
            lo,hi=self.scope.get('distance_range',[0,100])
            if not 0<=lo<hi<=100:raise ValueError('Chart range must be within 0–100% lap distance.')
        self.manifest['scope']=copy.deepcopy(self.scope)
        return self.scope

    def calculate(self):
        for rid,run in self.runs.items():
            self.progress('Calculating original-sample measurements · '+run['metadata']['track'])
            if rid not in self.logs:continue
            zones=mapped_corners(self.maps[rid])
            if not zones:
                # Detect regions at full resolution instead of consulting display traces.
                log=self.logs[rid];ls=eligible(run)
                if ls:
                    lap=min(ls,key=lambda l:l['duration']);names=['LapDistPct']+[n for n in ['SteeringWheelAngle','Brake'] if n in log.headers]
                    a=log.read_samples(names,lap['sample_start'],lap['sample_stop']);active=np.zeros(len(a['LapDistPct']),bool)
                    if 'SteeringWheelAngle' in a:active|=np.abs(a['SteeringWheelAngle'])>math.radians(18)
                    if 'Brake' in a:active|=a['Brake']>.1
                    ids=np.flatnonzero(active);groups=np.split(ids,np.flatnonzero(np.diff(a['LapDistPct'][ids])>.008)+1) if len(ids) else []
                    zones=[{'label':f'Zone {i+1}','start':max(0,float(a['LapDistPct'][g[0]]*100)-.4),'end':min(100,float(a['LapDistPct'][g[-1]]*100)+.8)} for i,g in enumerate(groups) if a['LapDistPct'][g[-1]]-a['LapDistPct'][g[0]]>.005]
            self.measures[rid]=[]
            for lap in eligible(run):
                if self.cancel.is_set():raise ValueError('Investigation cancelled.')
                for zone in zones:
                    m=measure(self.logs[rid],lap,zone)
                    if m:self.measures[rid].append(m)
            self.manifest['calculated'].append({'run_id':rid,'lap_ids':[l['id'] for l in eligible(run)],
                'corners':[z['label'] for z in zones],'measurement_count':len(self.measures[rid]),'all_original_samples':True})

    def overview(self,rid):
        run=self.runs[rid];log=self.logs.get(rid);mapping=self.maps[rid]
        return {'run_id':rid,'access':'full-resolution' if log else 'summary-only',
                'recording':run.get('recording'),'metadata':{k:v for k,v in run['metadata'].items() if k not in ['available_channels','setup']},
                'notes':run['notes'],'summary':summary(run),'corner_map_status':mapping['status'],
                'corner_map_revision':mapping['profile']['revision'] if mapping['profile'] else None,
                'corners':mapped_corners(mapping),'representative_lap_ids':representative(run,self.scope.get('lap_id')),
                'laps':[{'id':l['id'],'number':l['number'],'session':l['session'],'duration':l['duration'],'eligible':l in eligible(run),'reasons':l['reasons'],'metrics':l['metrics']} for l in run['laps']],
                'channels':{n:channel_info(n,h) for n,h in log.headers.items() if h['count']==1 and h['type']!=0} if log else {},
                'conditions':{k:{a:b for a,b in v.items() if a not in ['time','values']} for k,v in run['conditions'].items()}}

    def initial(self):
        return {'runs':[self.overview(rid) for rid in self.runs],'scope':self.scope,
                'inspection':'Measurements are calculated across eligible laps. Original traces have NOT yet been supplied: use read_samples.',
                'limits':{'tool_calls':24,'returned_bytes':131072,'page_rows':256,'page_bytes':16384}}

    def _run(self,rid):
        rid=rid or self.primary
        if rid not in self.runs:raise ValueError('Run is outside this investigation.')
        return rid,self.runs[rid]

    def _cursor(self,state):
        token=secrets.token_urlsafe(18);self.cursors[token]=state;return token

    def call(self,name,args):
        if self.cancel.is_set():return {'error':'Investigation cancelled.'}
        self.calls+=1
        if self.calls>24 or self.bytes>=131072:
            self.manifest['limits'].append('Investigation evidence budget reached; inspection is incomplete.')
            return {'error':self.manifest['limits'][-1],'stop_requesting':True}
        self.manifest['calls'].append({'tool':name,'arguments':copy.deepcopy(args)})
        previous={k:len(self.manifest[k]) for k in ['sections','supplied_measurements','knowledge']}
        try:
            if not isinstance(args,dict):raise ValueError('Tool arguments must be an object.')
            if name=='read_samples':result=self.samples(args)
            elif name=='list_evidence':result=self.listing(args)
            elif name=='read_knowledge':
                if set(args)-{'query','kind'} or args.get('kind') not in [None,'manual','video transcript','private note']:raise ValueError('Choose a supported knowledge source kind.')
                if not isinstance(args.get('query'),str) or not 0<len(args['query'])<=5000:raise ValueError('Enter a knowledge query under 5000 characters.')
                result={'sources':self.retrieve(args['query'],count=3,car=self.runs[self.primary]['metadata']['car'],kind=args.get('kind')) if self.retrieve else []}
                self.manifest['knowledge'].extend(copy.deepcopy(result['sources']))
            else:raise ValueError('Unknown read-only tool.')
            size=len(encode(result).encode())
            if size>16384 or self.bytes+size>131072:
                for k,n in previous.items():del self.manifest[k][n:]
                self.manifest['limits'].append('Returned evidence would exceed the page/investigation budget; request a narrower range or fewer channels.')
                return {'error':self.manifest['limits'][-1]}
            self.bytes+=size
            return result
        except (ValueError,OSError,KeyError,TypeError) as exc:
            for k,n in previous.items():del self.manifest[k][n:]
            return {'error':str(exc)}

    def listing(self,args):
        if set(args)-{'run_id','cursor'}:raise ValueError('Unsupported evidence argument.')
        state=self.cursors.get(args.get('cursor')) if args.get('cursor') else None
        if args.get('cursor') and (not state or state['kind']!='measurements'):raise ValueError('Invalid evidence cursor.')
        rid,_=self._run(state['run_id'] if state else args.get('run_id'));index=state['index'] if state else 0
        items=self.measures.get(rid,[])
        if self.zone_selection:items=[m for m in items if m['corner'] in self.zone_selection]
        if self.scope.get('kind')=='region':
            lo,hi=self.scope['distance_range'];items=[m for m in items if m['range_pct'][1]>=lo and m['range_pct'][0]<=hi]
        result={'run_id':rid,'measurements':[],'total':len(items),'next_cursor':None}
        while index<len(items):
            if len(encode(result))+len(encode(items[index]))>14000:break
            m=copy.deepcopy(items[index]);m['evidence_id']=f'M-{rid[:6]}-{m["lap_id"]}-{m["corner"]}'
            result['measurements'].append(m);index+=1
        if not result['measurements'] and index<len(items):return {'error':'One measurement is too large; use read_samples for this corner.'}
        if index<len(items):result['next_cursor']=self._cursor({'kind':'measurements','run_id':rid,'index':index})
        self.manifest['supplied_measurements'].extend([{'evidence_id':m['evidence_id'],'run_id':rid,'lap_id':m['lap_id'],'corner':m['corner'],'distance_range':m['range_pct']} for m in result['measurements']])
        self.progress(f'Inspecting calculated corner measurements · {len(result["measurements"])} entries')
        return result

    def samples(self,args):
        allowed={'run_id','lap_id','channels','corner','distance_range','time_range','cursor'}
        if set(args)-allowed:raise ValueError('Unsupported sample argument.')
        if args.get('cursor'):
            if set(args)!={'cursor'}:raise ValueError('A continuation cursor cannot be combined with a new sample request.')
            state=self.cursors.get(args['cursor'])
            if not state or state['kind']!='samples':raise ValueError('Invalid sample cursor.')
            rid,run=self._run(state['run_id']);start,stop=state['start'],state['stop'];names=state['channels'];lap_id=state['lap_id'];corner=state['corner'];corner_range=state.get('corner_range')
        else:
            rid,run=self._run(args.get('run_id'));lap_id=args.get('lap_id')
            if isinstance(lap_id,bool) or not isinstance(lap_id,int):raise ValueError('A numeric lap_id is required.')
            lap=next((l for l in eligible(run) if l['id']==lap_id),None)
            if not lap:raise ValueError('Lap is flagged, excluded, or outside this run.')
            names=args.get('channels',['Speed','Brake','Throttle','SteeringWheelAngle','YawRate'])
            if not isinstance(names,list) or not 1<=len(names)<=16 or any(not isinstance(n,str) for n in names):raise ValueError('Choose 1–16 scalar channels.')
            names=list(dict.fromkeys(['SessionTime','LapDistPct']+names));corner=args.get('corner');corner_range=None
            if sum(bool(args.get(k)) for k in ['corner','distance_range','time_range'])>1:raise ValueError('Specify one corner, distance range, or time range.')
            log=self.logs.get(rid)
            if not log:raise ValueError('Summary-only run: attach the original recording.')
            start,stop=lap['sample_start'],lap['sample_stop']
            a=log.read_samples(['LapDistPct','SessionTime'],start,stop)
            if corner:
                zone=next((z for z in mapped_corners(self.maps[rid]) if z['label']==corner),None)
                if not zone:raise ValueError('Corner has no validated mapping for this configuration.')
                bounds=[zone['start'],zone['end']];corner_range=bounds.copy();axis=a['LapDistPct']*100
            elif args.get('time_range'):
                bounds=args['time_range'];axis=a['SessionTime'] # absolute recorded session seconds
            else:
                bounds=args.get('distance_range',[0,100]);axis=a['LapDistPct']*100
            if not isinstance(bounds,list) or len(bounds)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in bounds) or bounds[0]>=bounds[1]:raise ValueError('Range needs two increasing finite numbers.')
            if not args.get('time_range') and not 0<=bounds[0]<bounds[1]<=100:raise ValueError('Distance range must be within 0–100%.')
            ids=np.flatnonzero((axis>=bounds[0])&(axis<=bounds[1]))
            if not len(ids):raise ValueError('No original samples in this range.')
            # Include two seconds of recorded approach/exit context for named corners.
            # The requested corner boundaries remain distinct from this context.
            if corner:
                lo=max(float(a['SessionTime'][0]),float(a['SessionTime'][ids[0]])-2)
                hi=min(float(a['SessionTime'][-1]),float(a['SessionTime'][ids[-1]])+2)
                ids=np.flatnonzero((a['SessionTime']>=lo)&(a['SessionTime']<=hi))
            stop=start+int(ids[-1])+1;start+=int(ids[0])
        log=open_recording(self.data,run) if rid in self.logs else None
        if not log:raise ValueError('Original recording is unavailable.')
        data=log.read_samples(names,start,min(stop,start+256))
        result={'evidence_id':f'S{len(self.manifest["sections"])+1}','run_id':rid,'lap_id':lap_id,'corner':corner,
                'recording_sha256':run['recording']['sha256'],'profile_revision':self.maps[rid]['profile']['revision'] if self.maps[rid]['profile'] else None,
                'columns':['sample_index']+names,'units':{n:log.headers[n]['unit'] for n in names},'rows':[],'next_cursor':None,
                'channel_info':{n:channel_info(n,log.headers[n]) for n in names},
                'invalid_values':[],'original_samples':True,'complete':False,'corner_definition_range':corner_range,'context_seconds':2 if corner else 0}
        for j in range(len(data['SessionTime'])):
            row=[start+j]+[native(data[n][j]) for n in names]
            if len(encode(result))+len(encode(row))>13500:break
            result['rows'].append(row)
        end=start+len(result['rows'])
        if end==start:raise ValueError('A sample exceeds the page budget; choose fewer channels.')
        result['sample_start']=start;result['sample_stop']=end;result['requested_sample_stop']=stop
        result['distance_range']=[float(data['LapDistPct'][0]*100),float(data['LapDistPct'][len(result['rows'])-1]*100)]
        result['time_range']=[float(data['SessionTime'][0]),float(data['SessionTime'][len(result['rows'])-1])]
        result['complete']=end==stop
        if end<stop:result['next_cursor']=self._cursor({'kind':'samples','run_id':rid,'start':end,'stop':stop,'channels':names,'lap_id':lap_id,'corner':corner,'corner_range':corner_range})
        for n in names:
            invalid=np.flatnonzero(~np.isfinite(data[n][:len(result['rows'])]))
            if len(invalid):result['invalid_values'].append({'channel':n,'sample_indices':[start+int(i) for i in invalid]})
        self.manifest['sections'].append({k:copy.deepcopy(v) for k,v in result.items() if k not in ['rows','columns','units','invalid_values']})
        self.manifest['sections'][-1]['channels']=names;self.manifest['sections'][-1]['sample_count']=len(result['rows'])
        self.progress(f'Inspecting original samples · lap {next(l["number"] for l in run["laps"] if l["id"]==lap_id)} · {corner or "selected range"} · {start}–{end-1}')
        return result
