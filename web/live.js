'use strict';
// Live state is independent of the imported recording's state and selections.
(() => {
 const el=id=>document.getElementById(id), h=esc, n=number, lt=lapTime;
 let snapshot=null,active=false,view='pitwall',selected=null,filter='all',stream=null;
 let draft=null,draftSession=null,draftRevision=null,dirty=false,editing=null,mapKey='',mapPoints=[],rows=new Map(),markers=new Map(),lastPacket=Date.now();
 let trace=[],traceSequence=-1,seenAlerts=new Set(),primed=false,sound=false,audioContext=null,lastAlert='',historyCursor=null;
 let lastStrategy='',historyId=null,historyEntries=[];
 const seconds=v=>v==null?'—':`${Math.floor(v/3600)}:${String(Math.floor(v/60)%60).padStart(2,'0')}:${String(Math.floor(v%60)).padStart(2,'0')}`;
 const metric=(label,value)=>`<div class="live-metric"><span>${h(label)}</span><strong>${h(value)}</strong></div>`;
 const flags={1:'Left front tire',2:'Right front tire',4:'Left rear tire',8:'Right rear tire',16:'Fuel',32:'Tearoff',64:'Fast repair'};
 const conditionLabels={TrackTemp:'Track',TrackTempCrew:'Track crew',AirTemp:'Air',WindVel:'Wind',WindDir:'Wind direction',RelativeHumidity:'Humidity',Precipitation:'Rain',TrackWetness:'Wetness'};
 const visibleCars=()=>snapshot?.cars.filter(c=>filter==='all'||String(c.class_id)===filter)||[];
 const own=()=>snapshot?.cars.find(c=>c.car_idx===snapshot.session.player_car_idx);

 function receive(data,full=false){
  if(snapshot&&snapshot.collector_id===data.collector_id&&data.sequence<snapshot.sequence){
   if(full&&snapshot.session.id===data.session.id){
    const merged=new Map([...(data.trace||[]),...trace].map(s=>[s.sequence,s]));
    trace=[...merged.values()].sort((a,b)=>a.sequence-b.sequence).slice(-1200);
   }
   return;
  }
  if(snapshot&&snapshot.collector_id!==data.collector_id){trace=[];traceSequence=-1;}
  const changed=snapshot?.session.id!==data.session.id;
  snapshot=data;
  lastPacket=Date.now();
  if(changed){selected=data.session.player_car_idx;trace=[];traceSequence=-1;mapKey='';draft=null;dirty=false;draftSession=null;lastStrategy='';primed=false;seenAlerts.clear();rows.clear();el('live-timing-rows').replaceChildren();}
  if(full){trace=[];traceSequence=-1;}
  for(const sample of data.trace||[]){if(sample.sequence>traceSequence){trace.push(sample);traceSequence=sample.sequence;}}
  if(trace.length>1200)trace.splice(0,trace.length-1200);
  if(trace.length){const start=trace[trace.length-1].time-60;while(trace.length&&trace[0].time<start)trace.shift();}
  for(const alert of data.alerts||[]){
   if(primed&&!seenAlerts.has(alert.id)&&sound&&alert.severity!=='info')beep(alert.severity);
   seenAlerts.add(alert.id);
  }
  if(seenAlerts.size>1200)seenAlerts=new Set((data.alerts||[]).map(a=>a.id));
  primed=true;
  if(!draft||draftSession!==data.session.id||(!dirty&&draftRevision!==data.plan_revision)){draft=structuredClone(data.plan);draftSession=data.session.id;draftRevision=data.plan_revision;editing=draft.scenarios[0]?.id||null;renderForm();}
  else if(dirty&&draftRevision!==data.plan_revision){el('live-plan-status').textContent='The saved plan changed on another browser. Reload it before saving.';el('live-plan-status').className='error';}
  if(active)render();
  dispatchEvent(new CustomEvent('live-snapshot',{detail:data}));
 }

 function render(){
  if(!snapshot)return;
  const s=snapshot.session,p=snapshot.player;
  el('live-connection').textContent=snapshot.status==='connected'?(s.replay?'Replay · predictions paused':'Live connected'):snapshot.status==='stale'?'Data stale':'Waiting for iRacing';
  el('live-connection').className=snapshot.status==='connected'&&!s.replay?'connected':'stale';
  el('live-connection').title=snapshot.message;
  el('live-session-title').textContent=s.id?`${s.track} · ${s.car} · ${s.session_type}`:snapshot.message;
  el('live-clock').textContent=s.id?`Elapsed ${seconds(s.elapsed_seconds)} / Remaining ${seconds(s.time_remaining_seconds)} · ${snapshot.update_age_seconds==null?'—':n(snapshot.update_age_seconds,1)} s old`:'';
  el('live-flags').textContent=[...(s.flag_labels||[]),snapshot.history_error||''].filter(Boolean).join(' / ');
  el('session-caption').textContent=s.id?`${s.track} · Live`:'Live pitwall';
  el('workspace-status').textContent=`${snapshot.message}${s.replay?' | Replay':''}`;
  if(view==='pitwall'){renderTiming();renderMap();renderFocus();renderOps();drawCharts();}
  if(view==='strategy')renderResults();
  window.LiveControls?.render();
 }

 function renderTiming(){
  const classes=[...new Map(snapshot.cars.map(c=>[String(c.class_id),c.class_name||`Class ${c.class_id??'unknown'}`])).entries()];
  const signature=JSON.stringify(classes);
  if(el('live-class').dataset.classes!==signature){
   el('live-class').innerHTML='<option value="all">All classes</option>'+classes.map(([id,name])=>`<option value="${h(id)}">${h(name)}</option>`).join('');
   el('live-class').dataset.classes=signature;el('live-class').value=filter;
  }
  const race=snapshot.session.session_type?.toLowerCase()==='race';
  el('live-gap-label').textContent=race?'Gap':'To best';
  el('live-timing-note').textContent=race?'Gap: SDK classification. Track-relative estimates appear on selection. Pit counts cover observed visits.':'Practice / qualifying order uses best laps. Pit counts cover observed visits.';
  const fastest=Math.min(...snapshot.cars.map(c=>c.best_lap_seconds).filter(v=>v>0));
  const current=new Set(snapshot.cars.map(c=>c.car_idx));
  for(const [idx,row]of rows){if(!current.has(idx)){row.remove();rows.delete(idx);}}
  let rowIndex=0;
  for(const c of snapshot.cars){
   let row=rows.get(c.car_idx);
   if(!row){row=document.createElement('tr');row.dataset.carIdx=c.car_idx;row.innerHTML='<td></td><td><button class="live-car-button"></button><small></small></td><td></td><td></td><td></td><td></td>';rows.set(c.car_idx,row);row.querySelector('button').onclick=()=>selectCar(c.car_idx);}
   row.hidden=filter!=='all'&&String(c.class_id)!==filter;
   row.classList.toggle('live-own',c.car_idx===snapshot.session.player_car_idx);row.classList.toggle('live-selected',selected===c.car_idx);
   const cells=row.cells;cells[0].textContent=`${c.position??'—'} / ${c.class_position??'—'}`;
   cells[1].querySelector('button').textContent=`#${c.number} ${c.driver}`;
   cells[1].querySelector('small').textContent=c.team||c.class_name||snapshot.session.car;
   cells[2].textContent=race?(c.gap_laps>0?`+${c.gap_laps} L`:c.sdk_gap_seconds==null?'—':`+${n(c.sdk_gap_seconds,1)}`):(c.best_lap_seconds&&Number.isFinite(fastest)?`+${n(c.best_lap_seconds-fastest,3)}`:'—');
   cells[3].textContent=lt(c.last_lap_seconds);cells[4].textContent=lt(c.best_lap_seconds);
   cells[5].textContent=`${c.on_pit_road?'In / ':''}${c.observed_pits}`;
   row.title=`${c.class_name||'Class unknown'}${c.reason_out?' · '+c.reason_out:''}`;
   const expected=el('live-timing-rows').children[rowIndex++];
   if(expected!==row)el('live-timing-rows').insertBefore(row,expected||null);
  }
  if(!snapshot.cars.length)el('live-timing-note').textContent='Timing will populate when the simulator exposes session entries.';
 }

 function selectCar(idx){selected=idx;renderTiming();renderMap();renderFocus();}
 function svg(tag,attrs){const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,v);return node;}
 function project(distance){
  if(!mapPoints.length)return{x:40+distance*5.2,y:140};
  let i=mapPoints.findIndex(p=>p.distance>=distance);
  if(i<0)i=mapPoints.length-1;
  const b=mapPoints[i],a=mapPoints[Math.max(0,i-1)],f=b.distance===a.distance?0:Math.max(0,Math.min(1,(distance-a.distance)/(b.distance-a.distance)));
  return{x:a.x+(b.x-a.x)*f,y:a.y+(b.y-a.y)*f};
 }
 function renderMap(){
  const mapping=snapshot.map||{points:[],corners:[],status:'lap_progress'},points=mapping.points||[];
  const key=JSON.stringify([mapping.track_id,mapping.configuration,mapping.revision,points]);
  if(key!==mapKey){
   mapKey=key;markers.clear();mapPoints=[];
   const canvas=svg('svg',{viewBox:'0 0 600 370',role:'img','aria-label':points.length?'Track outline and car positions':'Lap-progress diagram and car positions'});
   if(points.length){
    const lat=points.reduce((s,p)=>s+p.lat,0)/points.length,lon=points.reduce((s,p)=>s+p.lon,0)/points.length;
    const coords=points.map(p=>({...p,x:(p.lon-lon)*111320*Math.cos(lat*Math.PI/180),y:-(p.lat-lat)*111320}));
    const xs=coords.map(p=>p.x),ys=coords.map(p=>p.y),minX=Math.min(...xs),minY=Math.min(...ys);
    const scale=Math.min(500/(Math.max(...xs)-minX||1),285/(Math.max(...ys)-minY||1));
    mapPoints=coords.map(p=>({...p,x:50+(p.x-minX)*scale,y:40+(p.y-minY)*scale}));
    canvas.append(svg('polyline',{points:mapPoints.map(p=>`${p.x},${p.y}`).join(' '),class:'live-track-outline'}));
    for(const c of mapping.corners||[]){const p=project(c.reference);const label=svg('text',{x:p.x+5,y:p.y-8,class:'live-corner-label'});label.textContent=c.label;canvas.append(label);}
   }else{
    canvas.append(svg('line',{x1:40,x2:560,y1:140,y2:140,class:'live-track-outline'}));
    for(const[d,label]of[[0,'Start / finish'],[50,'50%'],[100,'100%']]){const p=project(d),text=svg('text',{x:p.x,y:175,'text-anchor':d===0?'start':d===100?'end':'middle'});text.textContent=label;canvas.append(text);}
   }
   const sf=project(0);canvas.append(svg('rect',{x:sf.x-3,y:sf.y-8,width:6,height:16,fill:'#fff'}));
   el('live-map').replaceChildren(canvas);
  }
  el('live-map-source').textContent=points.length?mapping.source:'Lap progress';
  el('live-map-note').textContent=points.length?'Positions follow the recorded center path. Pit cars stay on that path; no pit-lane geometry is inferred.':'No matching track outline. Import a completed recording with GPS to add this circuit.';
  el('live-map-import').hidden=!!points.length;
  const canvas=el('live-map').querySelector('svg');
  const ids=new Set(snapshot.cars.map(c=>c.car_idx));
  for(const[idx,node]of markers)if(!ids.has(idx)){node.remove();markers.delete(idx);}
  for(const c of snapshot.cars){
   let marker=markers.get(c.car_idx);
   if(!marker){marker=svg('g',{'data-car-idx':c.car_idx,role:'button',tabindex:0,'aria-label':`Select car ${c.number}`,class:'live-car-marker'});marker.append(svg('circle',{r:8}),svg('text',{x:12,y:4}));marker.onclick=()=>selectCar(c.car_idx);marker.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectCar(c.car_idx);}};canvas.append(marker);markers.set(c.car_idx,marker);}
   const hide=c.lap_dist_pct==null||(filter!=='all'&&String(c.class_id)!==filter);
   marker.setAttribute('visibility',hide?'hidden':'visible');marker.setAttribute('tabindex',hide?'-1':'0');
   if(!hide){const p=project(c.lap_dist_pct*100);marker.setAttribute('transform',`translate(${p.x} ${p.y})`);}
   marker.classList.toggle('live-own',c.car_idx===snapshot.session.player_car_idx);marker.classList.toggle('live-selected',selected===c.car_idx);marker.classList.toggle('live-pit-car',!!c.on_pit_road);
   marker.querySelector('text').textContent=`${c.number}${c.on_pit_road?' P':''}`;
  }
 }
 function renderFocus(){
  const c=snapshot.cars.find(c=>c.car_idx===selected);
  if(!c){el('live-focus').textContent='Select a car in timing or on the map.';return;}
  const visits=snapshot.pits.filter(p=>p.car_idx===c.car_idx).slice(-3);
  el('live-focus').innerHTML=`<h3>#${h(c.number)} ${h(c.driver)}</h3><p>${h(c.team||c.class_name)} · ${c.on_pit_road?'In pit lane':'On track / session entry'} · ${c.lap_completed??'—'} completed laps</p><p>Estimated track-relative gap: ${c.relative_seconds==null?'—':`${c.relative_seconds>=0?'Ahead':'Behind'} ${n(Math.abs(c.relative_seconds),1)} s`} · Lap difference: ${c.lap_difference_to_player??'—'}</p><p class="quiet">Recent observed laps: ${c.lap_history.map(l=>`L${l.lap} ${lt(l.time)}`).slice(-4).join(' / ')||'Waiting for a lap crossing'}</p><p class="quiet">Pit visits: ${visits.map(p=>`${seconds(p.entry_seconds)} · ${n(p.duration,1)} s${p.partial?' (partial)':''}`).join(' / ')||'None observed'}</p>`;
  const nearby=snapshot.cars.filter(car=>car.car_idx!==snapshot.session.player_car_idx&&car.relative_seconds!=null).sort((a,b)=>Math.abs(a.relative_seconds)-Math.abs(b.relative_seconds)).slice(0,6);
  if(nearby.length)el('live-focus').innerHTML+=`<h3>Nearest to player on track</h3><p class="quiet">${nearby.map(car=>`#${h(car.number)} ${car.relative_seconds>=0?'ahead':'behind'} ${n(Math.abs(car.relative_seconds),1)} s${car.on_pit_road?' (pit)':''}`).join('<br>')}</p>`;
 }
 function renderFuel(){
  const p=snapshot.player,s=snapshot.session,m=snapshot.strategy||{},fresh=snapshot.status==='connected'&&!s.replay&&!s.electric;
  const helpOpen=el('live-fuel').querySelector('.live-fuel-help')?.open;
  const rows=m.fuel_statistics||[],selected=rows.find(r=>r.id===m.fuel_basis);
  const capacity=m.permitted_capacity_litres??s.fuel_capacity_litres;
  const table=s.electric?'':`<table class="live-fuel-table" aria-label="Fuel consumption and range by lap basis"><thead><tr><th scope="col">Based on</th><th scope="col">Usage<small>L/lap</small></th><th scope="col">Laps to<small>empty</small></th><th scope="col" title="Additional fuel divided by tank capacity above reserve. Fractional fuel loads; the minimum stop count is a fuel-only estimate.">Pits to<small>end</small></th></tr></thead><tbody>${rows.map(r=>`<tr data-fuel-basis="${h(r.id)}" class="${r.id===m.fuel_basis?'fuel-selected':''}"><th scope="row" title="${h(r.note)}">${h(r.label)}<small>${h(r.id==='last_lap'?(r.lap_number==null?'Waiting for lap':`Lap ${r.lap_number}${r.sample_count?'':' excluded'}`):r.id==='rolling'?`${r.sample_count}/5 clean laps`:r.id==='session'?`${r.sample_count} clean laps`:'Manual estimates')}</small></th><td>${n(r.burn_litres,3)}</td><td data-fuel-empty>${n(fresh?r.laps_to_empty:null,2)}</td><td data-fuel-pits>${n(fresh?r.pit_loads:null,2)}${fresh&&r.minimum_stops!=null?`<small>${r.minimum_stops}${r.minimum_stops?'+':''} stops</small>`:''}</td></tr>`).join('')}</tbody></table>`;
  el('live-fuel').innerHTML=metric(s.electric?'Energy remaining':'Fuel / permitted tank',s.electric?`${n(p.FuelLevel,2)} kWh`:`${n(p.FuelLevel,2)} / ${n(capacity,2)} L`)+table+
   metric('Calculations based on',m.fuel_basis_label||'Last lap')+
   metric('Available above reserve',`${n(fresh?m.laps_available:null,2)} laps / ${seconds(fresh?m.seconds_available:null)}`)+
   metric('Latest safe pit',!fresh||m.latest_safe_pit_lap==null?'—':`After lap ${m.latest_safe_pit_lap}`)+
   metric('Fuel to finish incl. reserve',`${n(fresh?m.fuel_to_finish:null,2)} L`)+
   metric('Additional fuel needed',`${n(fresh?m.additional_fuel_to_finish:null,2)} L`)+
   `<p class="quiet live-fuel-note">Reserve: ${n(snapshot.plan.reserve_laps,1)} lap (${n(m.reserve_litres,2)} L). ${h(selected?.note||'')}</p><p class="quiet">${h(m.message)}</p>`+
   (s.electric?'':`<details class="quiet live-fuel-help" ${helpOpen?'open':''}><summary>How fuel estimates work</summary><p>Laps to empty uses all remaining fuel. Pits to end shows extra fuel as tank loads above reserve; stop counts are fuel-only minimums. Check Strategy for feasible stop laps and service times. Session average covers clean laps observed by Stint. Pit, incident, off-track, refueled, partial and interrupted laps are excluded.</p></details>`);
 }
 function renderOps(){
  const p=snapshot.player,s=snapshot.session,m=snapshot.strategy||{};
  renderFuel();
  const chosen=m.scenarios?.find(c=>c.id===snapshot.plan.active_scenario),stop=chosen?.timeline?.[0];
  if(stop)el('live-fuel').innerHTML+=metric('Planned next stop',`Lap ${stop.lap} · add ${n(stop.fuel_litres,2)} L`)+metric('Stop time loss',`${n(stop.loss_seconds,1)} s`);
  const selectedServices=p.PitSvFlags==null?'Unavailable':Object.entries(flags).filter(([bit])=>p.PitSvFlags&Number(bit)).map(([,name])=>name).join(', ')||'None';
  el('live-operations').innerHTML=metric('Driver',s.driver_name||'—')+metric('Observed fuel / driver stint',`${seconds(s.stint_seconds)} / ${seconds(s.elapsed_seconds-s.driver_stint_start_seconds)}`)+metric('Pits open',p.PitsOpen==null?'—':p.PitsOpen?'Yes':'No')+metric('Player state',p.PlayerCarTowTime>0?`Tow ${seconds(p.PlayerCarTowTime)}`:p.PitstopActive?'Pit service':p.PlayerCarInPitStall?'In stall':p.OnPitRoad?'Pit road':p.IsOnTrack?'Driving':'Out of car')+metric('Incidents: personal / team',`${p.PlayerCarMyIncidentCount??'—'} / ${p.PlayerCarTeamIncidentCount??'—'}`)+metric('Mandatory / optional repair',`${seconds(p.PitRepairLeft)} / ${seconds(p.PitOptRepairLeft)}`)+metric('Service selections',selectedServices)+metric('Pit fuel selection',`${n(p.PitSvFuel,2)} ${s.electric?'kWh':'L'}`);
  el('live-conditions').innerHTML=Object.entries(conditionLabels).map(([key,label])=>{const v=snapshot.conditions[key];let value='—';if(v!=null){value=['TrackTemp','TrackTempCrew','AirTemp'].includes(key)?`${n(v,1)} °C`:key==='WindVel'?`${n(v,1)} m/s`:key==='WindDir'?`${n(v*180/Math.PI,0)}°`:['RelativeHumidity','Precipitation'].includes(key)?`${n(v*100,0)}%`:['Unknown','Dry','Mostly dry','Very light wet','Light wet','Moderately wet','Very wet','Extremely wet'][v]||String(v);}return metric(label,value);}).join('');
  const tires=snapshot.tires||{},values=tires.values||{};
  el('live-tires').innerHTML=`<p class="quiet">${h(tires.source||'Pit-updated readings')}<br>Last observed update: ${seconds(tires.updated_seconds)}</p>`+['LF','RF','LR','RR'].map(c=>metric(c,`${n(values[c+'tempCL'],1)} / ${n(values[c+'tempCM'],1)} / ${n(values[c+'tempCR'],1)} °C · ${n(values[c+'wearL']==null?null:values[c+'wearL']*100,0)}% wear remaining · cold ${n(values[c+'coldPressure'],1)} kPa`)).join('');
  el('live-instruments').textContent=`Gear ${p.Gear===-1?'R':p.Gear===0?'N':p.Gear??'—'} / RPM ${n(p.RPM,0)} / Delta ${p.LapDeltaToBestLap_OK?`${n(p.LapDeltaToBestLap,3)} s`:'—'} / Bias ${n(p.dcBrakeBias,1)} / TC ${p.dcTractionControl??'—'} / ABS ${p.dcABS??'—'} / Oil ${n(p.OilTemp,1)} °C / Water ${n(p.WaterTemp,1)} °C`;
  const alertKey=snapshot.alerts.map(a=>a.id).join();
  if(lastAlert!==alertKey){lastAlert=alertKey;el('live-alerts').innerHTML=[...snapshot.alerts].reverse().map(a=>`<div class="live-alert ${h(a.severity)}"><time>${seconds(a.session_time)}</time> ${h(a.message)}</div>`).join('')||'<p class="quiet">No alerts.</p>';}
 }

 function drawCharts(){
  const p=snapshot.player;
  el('live-speed-readout').textContent=`Speed ${n(p.Speed==null?null:p.Speed*3.6,1)} km/h`;
  el('live-pedals-readout').textContent=`Throttle ${n(p.Throttle==null?null:p.Throttle*100,0)}% / Brake ${n(p.Brake==null?null:p.Brake*100,0)}%`;
  el('live-steering-readout').textContent=`Steering ${n(p.SteeringWheelAngle==null?null:p.SteeringWheelAngle*180/Math.PI,1)}°`;
  draw(el('live-speed-chart'),[['Speed',3.6,'#0080ff']],0,null);
  draw(el('live-pedals-chart'),[['Throttle',100,'#00ff00'],['Brake',100,'#ff0000']],0,100);
  draw(el('live-steering-chart'),[['SteeringWheelAngle',180/Math.PI,'#00ffff']],null,null);
 }
 function draw(canvas,channels,lo,hi){
  const rect=canvas.getBoundingClientRect();if(rect.width<1||rect.height<1)return;
  const ratio=window.devicePixelRatio||1,w=Math.round(rect.width),ht=Math.round(rect.height);
  if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(ht*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(ht*ratio);}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,ht);
  const values=trace.flatMap(s=>channels.map(([key,f])=>s[key]==null?null:s[key]*f)).filter(Number.isFinite);
  const min=lo??Math.min(0,...values),max=hi??Math.max(min+1,...values),range=max-min||1;
  ctx.strokeStyle='#373b40';ctx.lineWidth=1;ctx.font='10px Tahoma';ctx.fillStyle='#969da5';
  const left=32,right=w-8,top=10,bottom=ht-19;
  for(let i=0;i<=2;i++){const y=top+(bottom-top)*i/2;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillText(n(max-range*i/2,0),0,y+3);}
  ctx.fillText('-60 s',left,ht-3);ctx.fillText('Now',right-23,ht-3);
  if(!trace.length)return;
  const end=trace[trace.length-1].time,start=end-60;
  for(const[key,f,color]of channels){ctx.strokeStyle=color;ctx.lineWidth=1.3;ctx.beginPath();let previous=null;for(const sample of trace){const value=sample[key];if(value==null||sample.time<start){previous=null;continue;}const x=left+(sample.time-start)/60*(right-left),y=bottom-(value*f-min)/range*(bottom-top);if(previous==null||sample.time-previous>.25)ctx.moveTo(x,y);else ctx.lineTo(x,y);previous=sample.time;}ctx.stroke();}
 }

 function beep(severity){
  if(!audioContext||audioContext.state!=='running')return;
  const oscillator=audioContext.createOscillator(),gain=audioContext.createGain();oscillator.frequency.value=severity==='critical'?440:880;
  gain.gain.setValueAtTime(.05,audioContext.currentTime);gain.gain.exponentialRampToValueAtTime(.001,audioContext.currentTime+.18);oscillator.connect(gain);gain.connect(audioContext.destination);oscillator.start();oscillator.stop(audioContext.currentTime+.2);
 }
 function setView(value){view=value;el('live-pitwall').hidden=value!=='pitwall';el('live-strategy-workspace').hidden=value!=='strategy';el('live-history-workspace').hidden=value!=='history';for(const v of ['pitwall','strategy','history'])el('live-view-'+v).classList.toggle('active',v===value);if(value==='history')refreshHistory().catch(showError);render();}
 const showError=e=>{el('live-plan-status').textContent=e.message;el('live-plan-status').className='error';};

 const fields=[['reserve_laps','Reserve (laps)',1],['race_laps','Total race laps',1],['race_minutes','Total race minutes',1],['pace_seconds','Override pace (s/lap)',.001],['burn_litres','Override fuel burn (L/lap)',.001],['capacity_litres','Override permitted tank (L)',.01],['pit_loss_seconds','Pit lane loss versus normal lap (s)',.1],['refuel_lps','Refueling rate (L/s)',.001],['tire_seconds','Tire service (s)',.1],['driver_change_seconds','Driver change (s)',.1]];
 function markDirty(){dirty=true;el('live-plan-status').textContent='Unsaved changes';el('live-plan-status').className='';}
 function renderForm(){
  if(!draft)return;
  el('live-plan-fields').innerHTML=`<label>Fuel calculation basis<select data-plan-field="fuel_basis"><option value="last_lap">Last lap</option><option value="session">Session average</option><option value="rolling">Last 5 median (3+ clean laps)</option></select></label>`+fields.map(([key,label,step])=>`<label>${h(label)}<input data-plan-field="${key}" type="number" step="${step}" min="0" value="${draft[key]??''}" placeholder="${['pace_seconds','burn_litres','capacity_litres'].includes(key)?'Use measured / SDK value':'Required if applicable'}"></label>`).join('')+`<label>Service timing<select data-plan-field="service_mode"><option value="concurrent">Concurrent (longest task)</option><option value="sequential">Sequential (sum of tasks)</option></select></label><p class="quiet">Last lap and session average work after one clean complete lap. Manual pace or burn overrides the measured basis. Use a lap target or a time target, or leave both blank for the SDK race limit. Stop times require service inputs.</p>`;
  el('live-plan-fields').querySelector('[data-plan-field=fuel_basis]').value=draft.fuel_basis||'last_lap';
  el('live-plan-fields').querySelector('[data-plan-field=service_mode]').value=draft.service_mode;
  el('live-plan-fields').querySelectorAll('[data-plan-field]').forEach(input=>input.oninput=()=>{draft[input.dataset.planField]=input.tagName==='SELECT'?input.value:input.value===''?null:Number(input.value);markDirty();});
  renderDrivers();renderScenarioEditor();el('live-plan-status').textContent='';
 }
 function renderDrivers(){
  el('live-driver-editor').innerHTML=draft.drivers.map((d,i)=>`<div class="live-driver-row"><div class="live-actions"><strong>${h(d.id===snapshot.session.driver_id?'Current driver':d.name)}</strong><button type="button" data-delete-driver="${i}" aria-label="Remove ${h(d.name)}">Remove</button></div><div class="live-form-grid"><label>Name<input data-driver="${i}" data-key="name" value="${h(d.name)}" maxlength="120"></label>${[['available_from_minutes','Available from'],['available_until_minutes','Available until'],['max_stint_minutes','Maximum continuous stint'],['min_drive_minutes','Minimum total driving']].map(([key,label])=>`<label>${label} (min)<input data-driver="${i}" data-key="${key}" type="number" min="0" max="1440" step=".1" value="${d[key]??''}"></label>`).join('')}</div></div>`).join('')||'<p class="quiet">Add drivers to enforce availability or driving-time constraints. Suggested fuel plans retain the current driver until edited.</p>';
  el('live-driver-editor').querySelectorAll('[data-driver]').forEach(input=>input.oninput=()=>{draft.drivers[Number(input.dataset.driver)][input.dataset.key]=input.dataset.key==='name'?input.value:input.value===''?null:Number(input.value);markDirty();});
  el('live-driver-editor').querySelectorAll('[data-delete-driver]').forEach(button=>button.onclick=()=>{const removed=draft.drivers.splice(Number(button.dataset.deleteDriver),1)[0];for(const s of draft.scenarios)for(const stop of s.stops)if(stop.driver===removed.id)stop.driver=null;markDirty();renderDrivers();renderScenarioEditor();});
 }
 function addDriver(current){
  if(!draft)return;
  const id=current?snapshot.session.driver_id:`driver-${Date.now()}`;
  if(!id)return;
  if(draft.drivers.some(d=>d.id===id)){showError(new Error('The current driver is already in the roster.'));return;}
  draft.drivers.push({id,name:current?snapshot.session.driver_name:'New driver',available_from_minutes:null,available_until_minutes:null,max_stint_minutes:null,min_drive_minutes:null});markDirty();renderDrivers();renderScenarioEditor();
 }
 function renderScenarioEditor(){
  const selector=el('live-scenario-select');selector.innerHTML=draft.scenarios.map(s=>`<option value="${h(s.id)}">${h(s.name)}</option>`).join('')||'<option value="">Create or copy a scenario</option>';
  if(!draft.scenarios.some(s=>s.id===editing))editing=draft.scenarios[0]?.id||null;selector.value=editing||'';
  const scenario=draft.scenarios.find(s=>s.id===editing);
  el('live-delete-scenario').disabled=!scenario;
  if(!scenario){el('live-scenario-editor').innerHTML='<p class="quiet">Copy a suggestion from the comparison pane, or create a scenario.</p>';return;}
  el('live-scenario-editor').innerHTML=`<label>Scenario name<input id="live-scenario-name" value="${h(scenario.name)}" maxlength="120"></label><div class="live-stop-editor">${scenario.stops.map((stop,i)=>`<div class="live-stop-row"><label>Stop after lap<input data-stop="${i}" data-key="lap" type="number" min="1" max="10000" step="1" value="${stop.lap}"></label><label>Add fuel (L)<input data-stop="${i}" data-key="fuel_litres" type="number" min="0" max="500" step="any" value="${n(stop.fuel_litres,3)}"></label><label>Driver<select data-stop="${i}" data-key="driver"><option value="">Keep current</option>${draft.drivers.map(d=>`<option value="${h(d.id)}">${h(d.name)}</option>`).join('')}</select></label><label class="live-check">Tires<input data-stop="${i}" data-key="tires" type="checkbox" ${stop.tires?'checked':''}></label><button type="button" data-remove-stop="${i}" aria-label="Remove stop ${i+1}">Remove</button></div>`).join('')}</div><button id="live-add-stop" type="button">Add stop</button>`;
  el('live-scenario-name').oninput=e=>{scenario.name=e.target.value;markDirty();};
  el('live-scenario-editor').querySelectorAll('[data-stop]').forEach(input=>{const stop=scenario.stops[Number(input.dataset.stop)],key=input.dataset.key;if(key==='driver')input.value=stop.driver||'';input.oninput=()=>{stop[key]=key==='tires'?input.checked:key==='driver'?input.value||null:Number(input.value);markDirty();};});
  el('live-scenario-editor').querySelectorAll('[data-remove-stop]').forEach(button=>button.onclick=()=>{scenario.stops.splice(Number(button.dataset.removeStop),1);markDirty();renderScenarioEditor();});
  el('live-add-stop').onclick=()=>{scenario.stops.push({lap:(scenario.stops.at(-1)?.lap??Math.floor(snapshot.player.LapCompleted||0))+1,fuel_litres:0,tires:false,driver:null});markDirty();renderScenarioEditor();};
 }
 function copyScenario(s){
  const copy={id:`scenario-${Date.now()}`,name:s.name+' (edited)',stops:structuredClone(s.stops)};draft.scenarios.push(copy);editing=copy.id;markDirty();renderScenarioEditor();
 }
 async function savePlan(activeId){
  if(window.REMOTE_PITWALL&&window.RemoteIdentity?.role!=='engineer')throw new Error('Viewer access: race plan is read-only.');
  if(!snapshot?.session.id)throw new Error('Connect to an iRacing session before saving a plan.');
  if(activeId!==undefined)draft.active_scenario=activeId;
  const id=draftSession,submitted=structuredClone(draft);el('live-save-plan').disabled=true;
  try{const data=await post('/api/live/plan',{session_id:id,revision:draftRevision,plan:submitted});if(draftSession!==id)throw new Error('The session changed while saving.');draft=structuredClone(data.plan);draftRevision=data.plan_revision;dirty=false;receive(data);el('live-plan-status').textContent=window.REMOTE_PITWALL?'Saved on racing PC':'Saved locally';el('live-plan-status').className='';lastStrategy='';renderResults();}
  finally{el('live-save-plan').disabled=false;window.LiveControls?.render();}
 }
 function renderResults(){
  const m=snapshot.strategy||{};
  el('live-strategy-message').textContent=m.message||'';
  el('live-model-evidence').innerHTML=metric('Representative pace',`${lt(m.pace_seconds)} (${m.pace_source||'measured'})`)+metric('Fuel burn',`${n(m.burn_litres,3)} L/lap (${m.burn_source||'measured'})`)+metric('Evidence',m.fuel_statistics?.find(r=>r.id===m.fuel_basis)?.note||'No measured laps')+metric('Estimated remaining distance',`${n(m.remaining_laps,2)} laps`)+metric('Permitted tank / reserve',`${n(m.permitted_capacity_litres??m.capacity_litres,2)} / ${n(m.reserve_litres,2)} L`);
  const key=JSON.stringify([m.scenarios,snapshot.plan.active_scenario]);
  if(key===lastStrategy)return;lastStrategy=key;
  el('live-assumptions').innerHTML=(m.assumptions||[]).map(a=>`<p>${h(a)}</p>`).join('');
  el('live-scenarios').innerHTML=(m.scenarios||[]).map(s=>`<article class="live-scenario ${s.feasible?'':'infeasible'}" data-scenario="${h(s.id)}"><div class="live-pane-heading"><h3>${h(s.name)}</h3><strong>${s.feasible?(s.missing_inputs.length?'Timing incomplete':`Feasible${s.rank?' · Rank '+s.rank:''}`):'Infeasible'}</strong></div><div class="live-actions">${s.suggested?'<button data-copy>Copy to edit</button>':''}<button data-use ${!s.feasible?'disabled':''}>${snapshot.plan.active_scenario===s.id?'Active for alerts':'Save & use for alerts'}</button></div><p>${s.timeline.length} remaining stops · completion ${seconds(s.projected_completion_seconds)} · pit loss ${n(s.pit_loss_seconds,1)} s · finish fuel ${n(s.finish_fuel,2)} L · minimum reserve margin ${n(s.minimum_margin_litres,2)} L</p>${s.errors.map(e=>`<p class="error">${h(e)}</p>`).join('')}${s.missing_inputs.length?`<p class="quiet">Needed: ${h(s.missing_inputs.join(', '))}.</p>`:''}<div class="live-table-scroll"><table><thead><tr><th>After lap</th><th>Arrival</th><th>Add L</th><th>Fuel after</th><th>Loss</th><th>Driver / tires</th></tr></thead><tbody>${s.timeline.map(t=>`<tr><td>${t.lap}</td><td>${seconds(t.arrival_seconds)}</td><td>${n(t.fuel_litres,2)}</td><td>${n(t.fuel_after,2)}</td><td>${n(t.loss_seconds,1)} s</td><td>${h(draft.drivers.find(d=>d.id===t.driver)?.name||snapshot.session.driver_name)} / ${t.tires?'Yes':'No'}</td></tr>`).join('')}</tbody></table></div>${s.rejoin?`<p>Estimated rejoin: class P${s.rejoin.class_position} of ${s.rejoin.observed_class_cars} observed cars. Nearby: ${s.rejoin.nearby.map(c=>`#${h(c.number)} ${n(c.gap_seconds,1)} s`).join(', ')}.</p><p class="quiet">${h(s.rejoin.caveat)}</p>`:'<p class="quiet">Rejoin estimate needs complete stop timing and observed field pace.</p>'}</article>`).join('')||'<p class="quiet">Strategies appear when pace, fuel burn, race target, and tank capacity are available.</p>';
  el('live-scenarios').querySelectorAll('[data-scenario]').forEach(card=>{const s=m.scenarios.find(s=>s.id===card.dataset.scenario);card.querySelector('[data-copy]')?.addEventListener('click',()=>copyScenario(s));card.querySelector('[data-use]').onclick=()=>savePlan(s.id).catch(showError);});
 }

 async function refreshHistory(){
  const entries=await api('/api/live/history');
  el('live-history-select').innerHTML=entries.map(r=>`<option value="${h(r.id)}">${h(r.session?.track||'Session')} · ${h(new Date(r.started_at).toLocaleString())} · ${h(r.status)}</option>`).join('')||'<option value="">No saved live sessions</option>';
  if(entries.some(r=>r.id===historyId))el('live-history-select').value=historyId;
  await loadHistory(false);
 }
 async function loadHistory(older){
  const id=el('live-history-select').value,kind=el('live-history-kind').value;
  if(!id){el('live-history-content').textContent='Race history is saved after a live session connects.';el('live-history-older').hidden=true;return;}
  historyId=id;
  const record=await api(`/api/live/history?id=${encodeURIComponent(id)}${kind?'&kind='+encodeURIComponent(kind):''}${older&&historyCursor!=null?'&before='+historyCursor:''}`);
  if(el('live-history-select').value!==id)return;
  historyEntries=older?[...record.journal,...historyEntries].slice(-1500):record.journal;
  historyCursor=record.before;el('live-history-older').hidden=historyCursor==null||historyCursor<=0;
  const laps=record.laps||[];
  el('live-history-content').innerHTML=`<h2>${h(record.session?.track||'Session')} · ${h(record.status)}</h2><p>${h(record.session?.car)} · ${laps.length} saved player laps · ${record.pits?.length??0} observed pit visits · ${record.plan_revisions??0} plan revisions</p>${record.gaps?.map(g=>`<p class="error">Uncovered interval ${seconds(g.from)}–${seconds(g.to)}: ${h(g.reason)}</p>`).join('')||''}<details><summary>Player laps and fuel</summary><table><thead><tr><th>Lap</th><th>Duration</th><th>Fuel used</th><th>Eligibility</th></tr></thead><tbody>${laps.map(l=>`<tr><td>${l.number}</td><td>${lt(l.duration)}</td><td>${n(l.fuel_used,3)} L</td><td>${l.eligible?'Eligible':h(l.reasons.join(', '))}</td></tr>`).join('')}</tbody></table></details><h3>Race journal</h3><div class="live-journal">${[...historyEntries].reverse().map(e=>`<div class="live-journal-entry"><time>${seconds(e.session_time)}</time> ${journalEntry(e)}</div>`).join('')||'<p>No matching journal entries.</p>'}</div>`;
 }
 function journalEntry(e){
  if(e.kind==='alert')return h(e.message);
  if(e.kind==='player_lap')return `Player lap ${e.number}: ${lt(e.duration)}, fuel ${n(e.fuel_used,3)} L. ${e.eligible?'Eligible':h((e.reasons||[]).join(', '))}.`;
  if(e.kind==='car_lap')return `Car #${h(e.number)} completed lap ${e.lap}: ${lt(e.time)}.`;
  if(e.kind==='pit_visit')return `Car #${h(e.number)} pit visit: ${seconds(e.entry_seconds)}–${seconds(e.exit_seconds)}, ${n(e.duration,1)} s${e.partial?' (entry not fully observed)':''}.`;
  if(e.kind==='stint')return `Fuel stint: ${seconds(e.start_seconds)}–${seconds(e.end_seconds)}. Refueling observed.`;
  if(e.kind==='driver_change')return 'Driver change observed.';
  if(e.kind==='gap')return `Uncovered interval ${seconds(e.from)}–${seconds(e.to)}: ${h(e.reason)}.`;
  if(e.kind==='plan')return `Race plan revision ${e.revision}. ${e.plan?.race_laps?e.plan.race_laps+' total laps':e.plan?.race_minutes?e.plan.race_minutes+' total minutes':'SDK race limit'}. Scenarios: ${h(e.plan?.scenarios?.map(s=>s.name).join(', ')||'suggested fuel plans')}.`;
  if(e.kind==='conditions')return `Conditions: track ${n(e.TrackTemp,1)} °C, air ${n(e.AirTemp,1)} °C, wind ${n(e.WindVel,1)} m/s, rain ${n(e.Precipitation==null?null:e.Precipitation*100,1)}%.`;
  if(e.kind==='pit_authority')return `Pit control ${e.enabled?'enabled for '+h(e.engineer):'disabled'} by ${h(e.actor)}. ${h(e.reason||'')}`;
  if(e.kind==='pit_command_requested')return `Pit selections requested by ${h(e.actor)}: ${e.requested.fuel_litres==null?'fuel unchanged':e.requested.fuel_litres+' L to add'}, tires ${h(Object.entries(e.requested.tires).filter(([,v])=>v).map(([c])=>c.toUpperCase()).join(', ')||'none')}.`;
  if(e.kind==='pit_command_result')return `Pit request ${h(e.status)}: ${h(e.message)} (${h(e.actor)}).`;
  return 'Race observation saved.';
 }

 el('live-class').onchange=e=>{filter=e.target.value;renderTiming();renderMap();};
 for(const value of ['pitwall','strategy','history'])el('live-view-'+value).onclick=()=>setView(value);
 el('live-open-strategy').onclick=()=>setView('strategy');el('live-map-import').onclick=()=>el('import-button')?.click();
 el('live-reload-plan').onclick=()=>{draft=structuredClone(snapshot.plan);draftRevision=snapshot.plan_revision;dirty=false;renderForm();lastStrategy='';renderResults();window.LiveControls?.render();};
 el('live-sound').onclick=async()=>{try{if(!sound){audioContext??=new(window.AudioContext||window.webkitAudioContext)();await audioContext.resume();sound=audioContext.state==='running';}else sound=false;el('live-sound').textContent=sound?'Sound on':'Sound off';el('live-sound').setAttribute('aria-pressed',String(sound));}catch{el('live-sound').textContent='Sound unavailable';}};
 el('live-plan-form').onsubmit=e=>{e.preventDefault();savePlan().catch(showError);};
 el('live-add-current-driver').onclick=()=>addDriver(true);el('live-add-driver').onclick=()=>addDriver(false);
 el('live-scenario-select').onchange=e=>{editing=e.target.value;renderScenarioEditor();};
 el('live-new-scenario').onclick=()=>copyScenario({name:'New scenario',stops:[]});
 el('live-delete-scenario').onclick=()=>{draft.scenarios=draft.scenarios.filter(s=>s.id!==editing);if(draft.active_scenario===editing)draft.active_scenario=null;editing=null;markDirty();renderScenarioEditor();};
 el('live-refresh-history').onclick=()=>refreshHistory().catch(showError);el('live-history-select').onchange=()=>loadHistory(false).catch(showError);el('live-history-kind').onchange=()=>loadHistory(false).catch(showError);el('live-history-older').onclick=()=>loadHistory(true).catch(showError);
 window.LivePitwall={activate(value){active=value;if(value)render();},getSnapshot(){return snapshot;}};
 addEventListener('resize',()=>{if(active&&view==='pitwall'&&snapshot)drawCharts();});
 addEventListener('beforeunload',()=>stream?.close());
 async function connect(){
  try{receive(await api('/api/live/snapshot'),true);}catch(e){el('live-connection').textContent=e.message;}
  stream=new EventSource('/api/live/events');
  stream.addEventListener('snapshot',e=>{try{receive(JSON.parse(e.data));}catch(error){el('live-connection').textContent='Live display error: '+error.message;}});
  stream.onopen=()=>{dispatchEvent(new Event('live-stream-open'));api('/api/live/snapshot').then(s=>receive(s,true)).catch(()=>{});};
  stream.onerror=()=>{dispatchEvent(new Event('live-stream-error'));el('live-connection').textContent='Connection lost · reconnecting';if(snapshot){snapshot.status='stale';snapshot.message='Browser connection lost; waiting for fresh data.';snapshot.strategy={...snapshot.strategy,scenarios:[],message:'Predictions suspended until the browser reconnects.',laps_available:null,seconds_available:null,latest_safe_pit_lap:null,fuel_to_finish:null,additional_fuel_to_finish:null};if(active)render();}};
 }
 active=typeof worksheet!=='undefined'&&worksheet==='live';connect();
 setInterval(()=>{if(snapshot?.status==='connected'&&Date.now()-lastPacket>2000){snapshot.status='stale';snapshot.message='Browser connection stale; waiting for fresh data.';snapshot.strategy={...snapshot.strategy,scenarios:[],message:'Predictions suspended until the browser reconnects.',laps_available:null,seconds_available:null,latest_safe_pit_lap:null,fuel_to_finish:null,additional_fuel_to_finish:null};if(active)render();}},500);
})();
