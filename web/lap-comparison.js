'use strict';
// Independent worksheet state: never changes the main Telemetry run or lap selection.
(() => {
  const sides=['selected','reference'], slots={selected:null,reference:null};
  let preferences={selected:null,reference:null}, result=null, draft=[], active=false, requestVersion=0;
  let selectionVersion={selected:0,reference:0}, range=[0,100], selectedSegment=null, charts=[], mapPoints=[];
  let mapView=null, mapBase=null, drag=null, importTarget=null, ready=false, resizeTimer;
  let lineDistance=null,lineSpan=1.6,lineFrame=0;
  let aerial=null,aerialTrack=null,aerialVersion=0;
  try{preferences={...preferences,...JSON.parse(localStorage.getItem('stint.lapComparison')||'{}')};}catch{}
  const signed=value=>value==null?'—':`${value>=0?'+':''}${value.toFixed(3)} s`;
  const value=(v,digits=1)=>v==null||!Number.isFinite(v)?'—':v.toFixed(digits);
  const status=(message,error=false)=>{$('lc-status').textContent=message;$('lc-status').classList.toggle('error',error);};
  const persist=()=>{for(const side of sides)preferences[side]=slots[side]?{run:slots[side].run.id,lap:slots[side].lap}:null;try{localStorage.setItem('stint.lapComparison',JSON.stringify(preferences));}catch{}};
  const flags=(run,lap)=>[...(lap.reasons||[]),...(run.notes?.excluded_laps?.includes(lap.id)?['Manually excluded']:[])];
  const body=()=>({selected_run_id:slots.selected.run.id,selected_lap_id:slots.selected.lap,reference_run_id:slots.reference.run.id,reference_lap_id:slots.reference.lap});
  function refresh(){
    for(const side of sides){
      const select=$(`lc-run-${side}`), id=slots[side]?.run.id||'';
      select.innerHTML='<option value="">Choose a recording</option>'+state.runs.map(run=>`<option value="${esc(run.id)}">${esc(run.filename||runLabel(run))} · ${esc(run.metadata.track)} · ${esc(run.metadata.car)}</option>`).join('');
      if(id&&!state.runs.some(r=>r.id===id)){selectionVersion[side]++;slots[side]=null;requestVersion++;result=null;$('lc-results').hidden=true;renderSlot(side);status('A selected recording was removed. Choose another recording.');}
      select.value=slots[side]?.run.id||'';
    }
    if(active&&!ready)init();
  }
  function renderSlot(side){
    const slot=slots[side], select=$(`lc-lap-${side}`);
    $(`lc-attach-${side}`).hidden=!slot||!!slot.run.recording;
    if(!slot){select.innerHTML='<option value="">Choose a recording first</option>';select.disabled=true;$(`lc-info-${side}`).textContent='';return;}
    const run=slot.run;
    select.innerHTML='<option value="">Choose a complete lap</option>'+run.laps.map(lap=>`<option value="${lap.id}" ${lap.complete?'':'disabled'}>Lap ${lap.number} · ${lapTime(lap.duration)}${flags(run,lap).length?' · '+esc(flags(run,lap).join(', ')):''}${lap.complete?'':' · incomplete'}</option>`).join('');
    select.value=slot.lap??'';select.disabled=false;
    const lap=run.laps.find(l=>l.id===slot.lap), fuel=lap?.metrics?.fuel_start_l;
    $(`lc-info-${side}`).textContent=`${run.metadata.car} · ${run.metadata.configuration||run.metadata.track} · ${new Date(run.metadata.recorded_at).toLocaleString()}${fuel!=null?' · fuel '+value(fuel)+' L':''}${lap?.metrics?.TrackTemp!=null?' · track '+value(lap.metrics.TrackTemp)+' °C':''}${lap?.metrics?.AirTemp!=null?' · air '+value(lap.metrics.AirTemp)+' °C':''}${flags(run,lap||{}).length?' · '+flags(run,lap||{}).join(', '):''}`;
  }
  async function choose(side,id,lapId){
    const token=++selectionVersion[side];++requestVersion;result=null;slots[side]=null;$('lc-results').hidden=true;renderSlot(side);
    if(!id){persist();status('Choose or import two recordings to compare their laps.');return;}
    status('Loading recording…');
    try{
      const run=await api('/api/run?id='+encodeURIComponent(id));if(token!==selectionVersion[side])return;
      const valid=run.laps.filter(l=>l.complete), clean=valid.filter(l=>!flags(run,l).length).sort((a,b)=>a.duration-b.duration);
      const lap=valid.find(l=>l.id===lapId)||clean[0]||valid[0];
      slots[side]={run,lap:lap?.id??null};$(`lc-run-${side}`).value=id;renderSlot(side);persist();range=[0,100];selectedSegment=null;mapView=null;lineDistance=null;lineSpan=1.6;
      await compare();
    }catch(error){if(token===selectionVersion[side])status(error.message,true);}
  }
  async function init(){
    if(ready||!state.runs.length)return;ready=true;
    await Promise.all(sides.map(side=>preferences[side]&&state.runs.some(r=>r.id===preferences[side].run)?choose(side,preferences[side].run,preferences[side].lap):Promise.resolve()));
  }
  async function compare(useDraft=false){
    const token=++requestVersion;
    if(!sides.every(s=>slots[s]&&slots[s].lap!=null)){result=null;$('lc-results').hidden=true;status('Choose a complete lap from each recording.');return false;}
    status('Calculating from original telemetry…');if(!useDraft){result=null;$('lc-results').hidden=true;}
    try{
      const data=await post('/api/lap-comparison',{...body(),...(useDraft?{corners:draft}:{})});if(token!==requestVersion)return false;
      result=data;draft=structuredClone(data.corners);if(!data.segments.some(s=>s.id===selectedSegment))selectedSegment=null;
      $('lc-results').hidden=false;status('Comparison ready. Select a segment to inspect its traces and measurements.');loadAerial();render();return true;
    }catch(error){if(token===requestVersion){status(error.message,true);$('lc-edit-status').textContent=error.message;if(useDraft&&result)$('lc-results').hidden=false;}return false;}
  }
  function render(){
    if(!result)return;
    if(active){$('session-caption').textContent='Lap comparison';$('workspace-status').textContent=`Selected lap ${result.selected.number} / reference lap ${result.reference.number} | ${signed(result.delta_s)} | Original recordings`;}
    $('lc-total').textContent=`${signed(result.delta_s)} total`;
    $('lc-warnings').innerHTML=result.warnings.map(w=>`<li>${esc(w)}</li>`).join('');$('lc-method').textContent=result.timing_method;
    $('lc-provenance').textContent=result.provenance.source+(result.provenance.revision?' · '+result.provenance.revision:'');
    renderCharts();renderRows();renderDetails();renderEditor();renderMap();
  }
  function cursor(x){
    for(const chart of charts)chart.update(x);
    for(const side of sides){const points=mapPoints.filter(p=>p.side===side);const p=points.reduce((best,p)=>!best||Math.abs(p.distance-x)<Math.abs(best.distance-x)?p:best,null),el=$(`lc-cursor-${side}`);if(p&&el){el.setAttribute('cx',p.x);el.setAttribute('cy',p.y);el.removeAttribute('visibility');}}
    const corners=result.segments.filter(s=>s.kind==='corner'&&inSegment(x,s));
    $('lc-map-position').textContent=`${value(x,2)}% lap distance · ${corners.map(c=>c.label).join(' / ')||'between mapped corners'}`;
    $('lc-map').querySelectorAll('[data-map-corner]').forEach(el=>el.classList.toggle('hovered',corners.some(c=>c.id===el.dataset.segment)));
    lineDistance=x;
    if(!lineFrame)lineFrame=requestAnimationFrame(()=>{lineFrame=0;renderLineDetail();});
  }
  function leave(){charts.forEach(c=>c.hide());sides.forEach(s=>$(`lc-cursor-${s}`)?.setAttribute('visibility','hidden'));$('lc-map').querySelectorAll('.hovered').forEach(el=>el.classList.remove('hovered'));}
  function renderCharts(){
    charts=[];$('lc-charts').innerHTML='';if(!result)return;
    $('lc-range').textContent=range.map(x=>value(x,2)).join('–')+'%';$('lc-from').value=range[0];$('lc-to').value=range[1];
    const requests={},statsPromises={};
    for(const side of sides){const lap=result[side];if(!requests[lap.run_id])requests[lap.run_id]={run_id:lap.run_id,lap_ids:[],channels:['Speed','Throttle','Brake','SteeringWheelAngle'],distance_range:[...range]};if(!requests[lap.run_id].lap_ids.includes(lap.lap_id))requests[lap.run_id].lap_ids.push(lap.lap_id);}
    for(const [run,body] of Object.entries(requests))statsPromises[run]=requestPlotStats(body,sides.map(s=>result[s]).find(l=>l.run_id===run).sha256);
    const deltaStats=requestPlotStats({run_id:result.selected.run_id,lap_ids:[result.selected.lap_id],channels:['TimeDelta'],distance_range:[...range],reference_run_id:result.reference.run_id,reference_lap_id:result.reference.lap_id},`${result.selected.sha256}:${result.reference.sha256}`);
    const add=(title,unit,series,opts={})=>{const host=document.createElement('div');$('lc-charts').append(host);makeChart(host,title,unit,series,{range,linked:true,linkGroup:charts,statsPromises,onCursor:cursor,onLeave:leave,...opts});};
    for(const [channel,title,unit,color] of [['Speed','Speed','km/h',colors.selected],['Throttle','Throttle','%',colors.throttle],['Brake','Brake','%',colors.brake],['SteeringWheelAngle','Steering wheel','degrees',colors.additional]]){
      const traces=sides.filter(s=>result[s].traces[channel]).map(side=>({x:result[side].traces.distance,y:result[side].traces[channel],label:side==='selected'?`Lap ${result[side].number}`:`Ref ${result[side].number}`,color:side==='selected'?color:colors.reference,isReference:side==='reference',statChannel:channel,statLap:result[side].lap_id,statsRunId:result[side].run_id,statsRecording:result[side].sha256}));
      add(title,unit,traces,['Brake','Throttle'].includes(channel)?{fixedY:[0,100]}:channel==='SteeringWheelAngle'?{zeroLine:true}:{});
    }
    add('Elapsed time delta · positive is slower','s',[{x:result.delta_trace.distance,y:result.delta_trace.values,label:'Selected − reference',color:colors.delta,statChannel:'TimeDelta',statLap:result.selected.lap_id,statsRunId:result.selected.run_id}],{zeroLine:true,statsPromises:{[result.selected.run_id]:deltaStats}});
  }
  function sortedRows(){const rows=[...result.segments], mode=$('lc-sort').value;return mode==='track'?rows:rows.sort((a,b)=>mode==='gain'?a.delta_s-b.delta_s:mode==='magnitude'?Math.abs(b.delta_s)-Math.abs(a.delta_s):b.delta_s-a.delta_s);}
  function renderRows(){
    $('lc-segments').innerHTML=sortedRows().map(row=>`<tr class="${row.id===selectedSegment?'lc-active':''}"><td><button type="button" data-lc-segment="${esc(row.id)}">${esc(row.label)}</button></td><td>${row.ranges.map(r=>r.map(x=>value(x,2)).join('–')).join(' / ')}</td><td>${value(row.selected.elapsed_s,3)} s</td><td>${value(row.reference.elapsed_s,3)} s</td><td class="${row.delta_s>0?'lc-loss':'lc-gain'}">${signed(row.delta_s)}</td><td>${value(row.selected.min_speed_kph)} / ${value(row.reference.min_speed_kph)} km/h</td><td>${value(row.selected.max_brake_pct)} / ${value(row.reference.max_brake_pct)} %</td></tr>`).join('');
    $('lc-segments').querySelectorAll('[data-lc-segment]').forEach(b=>b.onclick=()=>selectSegment(b.dataset.lcSegment));
  }
  function selectSegment(id){
    const row=result.segments.find(s=>s.id===id);if(!row)return;selectedSegment=id;
    range=row.ranges.length===1?[...row.ranges[0]]:[0,100];
    lineDistance=segmentAnchor(row);lineSpan=Math.min(1.6,row.ranges.reduce((n,[a,b])=>n+b-a,0));
    renderCharts();renderRows();renderDetails();mapView=null;renderMap();
  }
  function renderDetails(){
    const row=result.segments.find(s=>s.id===selectedSegment);
    if(!row){$('lc-detail').innerHTML='<p>Select a segment to see its measurements.</p>';return;}
    const metrics=[['elapsed_s','Segment time','s',3],['entry_speed_kph','Entry speed','km/h',1],['exit_speed_kph','Exit speed','km/h',1],['min_speed_kph',row.kind==='corner'?'Minimum corner speed':'Minimum speed','km/h',1],['max_speed_kph','Maximum speed','km/h',1],['max_brake_pct','Maximum brake','%',1],['braking_s','Braking duration','s',3],['coasting_s','Coasting duration','s',3],['full_throttle_s','Full-throttle duration','s',3]];
    $('lc-detail').innerHTML=`<h3>${esc(row.label)} · ${signed(row.delta_s)}</h3><div class="lc-table-scroll"><table><thead><tr><th>Metric</th><th class="selected-key">Selected</th><th class="reference-key">Reference</th><th>Difference S − R</th></tr></thead><tbody>${metrics.map(([key,label,unit,d])=>`<tr><th>${label} (${unit})</th><td>${value(row.selected[key],d)}</td><td>${value(row.reference[key],d)}</td><td>${row.differences[key]==null?'—':(row.differences[key]>=0?'+':'')+value(row.differences[key],d)}</td></tr>`).join('')}</tbody></table></div><p class="quiet">Original samples: ${row.selected.sample_count} selected / ${row.reference.sample_count} reference. Braking &gt;5%, coasting both pedals &lt;2%, full throttle &gt;95%. Missing measurements show —.</p>`;
    if(row.ranges.length>1){
      const controls=document.createElement('div');controls.className='lc-actions';
      for(const piece of row.ranges){const button=document.createElement('button');button.type='button';button.textContent=`Zoom ${piece.map(x=>value(x,2)).join('–')}%`;button.onclick=()=>{range=[...piece];renderCharts();};controls.append(button);}
      $('lc-detail').append(controls);
    }
  }
  function renderEditor(){
    $('lc-corner-inputs').innerHTML=draft.map((c,i)=>`<div class="lc-corner-row"><label>Name<input data-corner="${i}" data-field="label" value="${esc(c.label)}" maxlength="80"></label><label>Start %<input data-corner="${i}" data-field="start" type="number" min="0" max="100" step="0.01" value="${c.start}"></label><label>End %<input data-corner="${i}" data-field="end" type="number" min="0" max="100" step="0.01" value="${c.end}"></label><button type="button" data-delete="${i}" aria-label="Delete ${esc(c.label)}">×</button></div>`).join('');
    $('lc-corner-inputs').querySelectorAll('input').forEach(input=>input.onchange=()=>{draft[Number(input.dataset.corner)][input.dataset.field]=input.dataset.field==='label'?input.value:Number(input.value);$('lc-edit-status').textContent='Draft changed. Apply to recalculate, or save for this track.';renderMap();});
    $('lc-corner-inputs').querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{draft.splice(Number(b.dataset.delete),1);renderEditor();renderMap();});
  }
  function inSegment(d,row){return row?.ranges.some(([a,b])=>d>=a&&d<=b);}
  function segmentAnchor(row){
    if(!row)return 0;
    const profile=slots.reference?.run.corner_map;
    const ref=profile?.status==='validated'?[...profile.profile.corners,...(profile.profile.comparison_sections||[])].find(c=>c.label===row.label):null;
    if(ref&&inSegment(ref.reference,row))return ref.reference;
    return (row.ranges[0][0]+row.ranges.reduce((n,[a,b])=>n+b-a,0)/2)%100;
  }
  function pathAt(points,distance){
    if(!points?.length)return null;
    let lo=0,hi=points.length-1;
    while(lo<hi){const mid=(lo+hi)>>1;if(points[mid].distance<distance)lo=mid+1;else hi=mid;}
    const b=points[lo],a=points[Math.max(0,lo-1)],fraction=b.distance===a.distance?0:Math.max(0,Math.min(1,(distance-a.distance)/(b.distance-a.distance)));
    return {lat:a.lat+(b.lat-a.lat)*fraction,lon:a.lon+(b.lon-a.lon)*fraction};
  }
  async function loadAerial(refresh=false){
    if(!result||!slots.selected)return;
    const run=slots.selected.run,key=JSON.stringify([run.metadata.track_id,run.metadata.configuration]);
    if(aerialTrack===key&&!refresh)return;
    const token=++aerialVersion;aerialTrack=key;aerial={status:'loading'};renderAerialStatus();
    if(!result.map.selected?.length&&!result.map.reference?.length&&!result.map.outline?.length){aerialTrack=null;aerial={status:'unavailable',message:'A recorded GPS outline is needed to locate an aerial map.'};renderAerialStatus();return;}
    try{
      const response=await post('/api/track-imagery',{run_id:run.id,refresh});
      if(token!==aerialVersion)return;
      if(response.status==='available'){
        const photo=new Image();photo.src=response.image_url;await photo.decode();
        if(token!==aerialVersion)return;
      }
      aerial=response;
    }catch(error){if(token!==aerialVersion)return;aerial={status:'unavailable',message:error.message==='Not found.'?'Restart Stint to enable aerial maps.':'Aerial imagery could not be loaded. Check your connection and retry.'};}
    renderAerialStatus();if(result)renderMap();
  }
  function renderAerialStatus(){
    const available=aerial?.status==='available';
    $('lc-show-aerial').disabled=!available;
    $('lc-aerial-status').textContent=aerial?.status==='loading'?'Loading aerial track image…':available?aerial.message:(aerial?.message||'');
    $('lc-aerial-source').hidden=!available;$('lc-aerial-retry').hidden=aerial?.status!=='unavailable';
    if(available){$('lc-aerial-source').textContent=aerial.attribution;$('lc-aerial-source').href=aerial.source_url;}
  }
  function aerialLayer(project){
    if(aerial?.status!=='available'||!$('lc-show-aerial').checked)return '';
    const [west,south,east,north]=aerial.bounds;
    const nw=project({lat:north,lon:west}),ne=project({lat:north,lon:east}),sw=project({lat:south,lon:west});
    return `<image class="lc-aerial-image" href="${esc(aerial.image_url)}" x="0" y="0" width="1" height="1" preserveAspectRatio="none" transform="matrix(${ne.x-nw.x},${ne.y-nw.y},${sw.x-nw.x},${sw.y-nw.y},${nw.x},${nw.y})"/>`;
  }
  function visibleDrivingPath(points,project){
    // Fit the camera to its local window, but draw the continuing recorded lap.
    // Keep segments crossing the frame and let the SVG viewport clip them.
    let path='',previous=null,drawing=false;
    for(const point of points){
      const current=project(point);
      if(previous){
        const outside=Math.max(previous.x,current.x)<-8||Math.min(previous.x,current.x)>568||Math.max(previous.y,current.y)<-8||Math.min(previous.y,current.y)>348;
        if(outside)drawing=false;
        else{
          if(!drawing)path+=`M${previous.x.toFixed(3)},${previous.y.toFixed(3)} `;
          path+=`L${current.x.toFixed(3)},${current.y.toFixed(3)} `;drawing=true;
        }
      }
      previous=current;
    }
    return path;
  }
  function renderLineDetail(){
    if(!result)return;
    const paths=result.map,originPath=paths.reference||paths.selected;
    $('lc-line-out').disabled=$('lc-line-in').disabled=$('lc-line-fit').disabled=!originPath?.length;
    if(!originPath?.length){$('lc-line-readout').textContent='Recorded GPS paths are unavailable for this pair.';$('lc-line-detail').innerHTML='';return;}
    if(lineDistance==null)lineDistance=segmentAnchor(result.segments.find(s=>s.id===selectedSegment)||result.segments.find(s=>s.kind==='corner')||result.segments[0]);
    const distance=((lineDistance%100)+100)%100,origin=pathAt(originPath,distance),cos=Math.cos(origin.lat*Math.PI/180);
    // Work in meters with one shared origin and scale. No offsets are magnified.
    const meters=p=>({x:-(p.lat-origin.lat)*111320,y:-(p.lon-origin.lon)*111320*cos});
    const focus={},windows={},all=[];
    for(const side of sides){
      if(!paths[side]?.length||!$(`lc-show-${side}`).checked)continue;
      const position=pathAt(paths[side],distance);focus[side]=meters(position);
      const points=paths[side].map(p=>({...p,relative:((p.distance-distance+150)%100)-50})).filter(p=>Math.abs(p.relative)<=lineSpan/2);
      for(const relative of [-lineSpan/2,lineSpan/2])points.push({...pathAt(paths[side],(distance+relative+100)%100),relative});
      windows[side]=points.sort((a,b)=>a.relative-b.relative).map(meters);
      all.push(focus[side]);for(const p of windows[side])all.push(p);
    }
    const row=result.segments.find(s=>inSegment(distance,s));
    let description=`${row?.label||'Lap'} · ${value(distance,2)}% lap distance`;
    if(focus.selected&&focus.reference)description+=` · path separation ${value(Math.hypot(focus.selected.x-focus.reference.x,focus.selected.y-focus.reference.y),2)} m`;
    else description+=' · enable both GPS paths to compare';
    $('lc-line-readout').textContent=description;
    if(!all.length){$('lc-line-detail').innerHTML='<p class="quiet">Enable Selected or Reference above to show its driving line.</p>';return;}
    let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
    for(const p of all){minX=Math.min(minX,p.x);maxX=Math.max(maxX,p.x);minY=Math.min(minY,p.y);maxY=Math.max(maxY,p.y);}
    const width=Math.max(maxX-minX,4),height=Math.max(maxY-minY,4),scale=Math.min(480/width,260/height),cx=(maxX+minX)/2,cy=(maxY+minY)/2;
    const screen=p=>({x:280+(p.x-cx)*scale,y:165+(p.y-cy)*scale});
    const drivingPaths={};
    for(const side of sides)if(windows[side])drivingPaths[side]=visibleDrivingPath(paths[side],p=>screen(meters(p)));
    let svg=`<svg viewBox="0 0 560 340" data-pixels-per-meter="${scale}" data-distance-span="${lineSpan}" role="img" aria-label="Close-up of recorded driving lines at the same geographic scale"><title>Recorded driving lines over georeferenced aerial imagery when available</title>`;
    svg+=aerialLayer(p=>screen(meters(p)));
    for(const side of [...sides].reverse())if(drivingPaths[side])svg+=`<path class="lc-line-shadow" d="${drivingPaths[side]}"/>`;
    for(const side of [...sides].reverse())if(drivingPaths[side])svg+=`<path class="lc-line-path lc-line-${side}" d="${drivingPaths[side]}"/>`;
    if(focus.selected&&focus.reference){const a=screen(focus.selected),b=screen(focus.reference);svg+=`<line class="lc-line-gap" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`;}
    for(const side of sides)if(focus[side]){const p=screen(focus[side]);svg+=`<circle class="lc-line-dot lc-line-${side}" cx="${p.x}" cy="${p.y}" r="4"/>`;}
    const target=80/scale,power=10**Math.floor(Math.log10(target)),bar=[1,2,5,10].map(x=>x*power).filter(x=>x<=target).at(-1)||power;
    svg+=`<path class="lc-line-scale" d="M24 310v6h${(bar*scale).toFixed(2)}v-6"/><text x="24" y="334">${Number(bar.toPrecision(3))} m · actual scale</text></svg>`;
    $('lc-line-detail').innerHTML=svg;
  }
  function sizeMapLabels(svg){
    if(!svg)return;
    const unitsPerPixel=mapView[2]/(svg.getBoundingClientRect().width||560);
    svg.style.setProperty('--lc-map-label-size',`${12*unitsPerPixel}px`);
    svg.style.setProperty('--lc-map-direction-size',`${10*unitsPerPixel}px`);
    svg.style.setProperty('--lc-map-label-stroke',`${1.2*unitsPerPixel}px`);
  }
  function renderMap(){
    if(!result)return;
    const m=result.map, source=m.reference||m.selected||m.outline, geometry=!!source?.length;
    $('lc-map-status').textContent=!geometry?'No position geometry available. Lap-progress diagram.':`Recorded GPS: ${m.selected?'selected':'selected unavailable'} / ${m.reference?'reference':'reference unavailable'}${!m.selected&&!m.reference?' · saved circuit outline':''}`;
    const all=[...(m.selected||[]),...(m.reference||[]),...(m.outline||[])];
    let project;
    if(geometry){
      project=trackMapProjection(source,all);
    }else{project=p=>({distance:p.distance,x:280+115*Math.sin(p.distance/100*2*Math.PI),y:165-115*Math.cos(p.distance/100*2*Math.PI)});}
    mapPoints=[];
    const background=geometry?source.map(project):Array.from({length:401},(_,i)=>project({distance:i/4}));
    for(const side of sides){const path=m[side];if(path)mapPoints=mapPoints.concat(path.map(p=>({...project(p),side})));}
    if(!mapPoints.length)mapPoints=background.map(p=>({...p,side:'outline'}));
    mapBase=[0,0,560,320];
    const row=result.segments.find(s=>s.id===selectedSegment);
    if(!mapView)mapView=[...mapBase];
    const nearest=distance=>background.reduce((best,p)=>Math.abs(p.distance-distance)<Math.abs(best.distance-distance)?p:best,background[0]);
    const corners=result.segments.filter(s=>s.kind==='corner');
    const poly=points=>points.map(p=>`${p.x.toFixed(3)},${p.y.toFixed(3)}`).join(' ');
    let html=`<svg viewBox="${mapView.join(' ')}" role="img" aria-label="${geometry?'Track map with numbered corners and both lap trajectories':'Lap-progress diagram'}"><title>${geometry?'Recorded driving paths; corner reference points':'Lap progress, not circuit geometry'}</title>${geometry?aerialLayer(project):''}<polyline class="lc-outline map-path" points="${poly(background)}"/><text class="lc-map-direction" x="20" y="18">${geometry?'N ← · travel direction shown by increasing lap distance':'Lap progress · not circuit geometry'}</text>`;
    if(row)for(const [lo,hi] of row.ranges){html+=`<polyline class="lc-highlight" points="${poly(background.filter(p=>p.distance>=lo&&p.distance<=hi))}"/>`;}
    for(const side of [...sides].reverse()){if(!$(`lc-show-${side}`).checked)continue;const points=mapPoints.filter(p=>p.side===side);html+=`<polyline class="lc-path lc-path-${side}" points="${poly(points)}"/>`;}
    const sf=nearest(0);html+=`<rect x="${sf.x-3}" y="${sf.y-7}" width="6" height="14" class="map-finish"/><text x="${sf.x+7}" y="${sf.y+17}">S/F</text>`;
    for(const segment of corners){
      const p=nearest(segmentAnchor(segment));
      const rightEdge=p.x>490;
      html+=`<g class="map-corner ${segment.id===selectedSegment?'active':''}" data-map-corner="${esc(segment.label)}" data-segment="${esc(segment.id)}" role="button" tabindex="0" aria-label="Inspect ${esc(segment.label)}"><circle cx="${p.x}" cy="${p.y}" r="5"/><text x="${p.x+(rightEdge?-8:8)}" y="${p.y-8}" text-anchor="${rightEdge?'end':'start'}">${esc(segment.label)}</text><title>${esc(segment.label)}</title></g>`;
    }
    if(!$('lc-editor').hidden)for(const [i,c] of draft.entries())for(const field of ['start','end']){const p=background.reduce((best,p)=>Math.abs(p.distance-c[field])<Math.abs(best.distance-c[field])?p:best,background[0]);html+=`<circle class="lc-handle" data-corner="${i}" data-field="${field}" cx="${p.x}" cy="${p.y}" r="6"><title>${esc(c.label)} ${field}: ${value(c[field],2)}%</title></circle>`;}
    for(const side of sides)if($(`lc-show-${side}`).checked)html+=`<circle id="lc-cursor-${side}" class="lc-marker lc-marker-${side}" r="6" visibility="hidden" pointer-events="none"/>`;
    html+='</svg>';$('lc-map').innerHTML=html;
    $('lc-map-zones').innerHTML=corners.map(c=>`<button type="button" class="${c.id===selectedSegment?'active':''}" data-segment="${esc(c.id)}" aria-pressed="${c.id===selectedSegment}">${esc(c.label)}</button>`).join('');
    $('lc-map-zones').querySelectorAll('[data-segment]').forEach(button=>button.onclick=()=>selectSegment(button.dataset.segment));
    const svg=$('lc-map').querySelector('svg');
    sizeMapLabels(svg);
    svg.querySelectorAll('[data-segment]').forEach(el=>{el.onclick=()=>selectSegment(el.dataset.segment);el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selectSegment(el.dataset.segment);}};});
    const point=e=>{const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());};
    svg.onpointerdown=e=>{if(e.target.closest('[data-segment]'))return;e.preventDefault();const handle=e.target.closest('.lc-handle');drag={start:point(e),view:[...mapView],corner:handle?Number(handle.dataset.corner):null,field:handle?.dataset.field};svg.setPointerCapture(e.pointerId);};
    svg.onpointermove=e=>{if(!drag)return;const p=point(e);if(drag.corner!=null){const nearest=background.reduce((best,q)=>Math.hypot(q.x-p.x,q.y-p.y)<Math.hypot(best.x-p.x,best.y-p.y)?q:best,background[0]);draft[drag.corner][drag.field]=Number(nearest.distance.toFixed(3));const handle=svg.querySelector(`.lc-handle[data-corner="${drag.corner}"][data-field="${drag.field}"]`);handle.setAttribute('cx',nearest.x);handle.setAttribute('cy',nearest.y);}else{mapView=[mapView[0]+drag.start.x-p.x,mapView[1]+drag.start.y-p.y,...mapView.slice(2)];svg.setAttribute('viewBox',mapView.join(' '));}};
    svg.onpointerup=async()=>{const edit=drag?.corner!=null;drag=null;if(edit){renderEditor();await compare(true);}};svg.onpointercancel=()=>{drag=null;renderEditor();renderMap();};
    renderLineDetail();
  }
  function mapZoom(factor){if(!mapView)return;const [x,y,w,h]=mapView;mapView=[x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor];const svg=$('lc-map').querySelector('svg');svg?.setAttribute('viewBox',mapView.join(' '));sizeMapLabels(svg);}
  async function startImport(side,attach=false){importTarget=side;await openImport();if(attach){state.attachId=slots[side].run.id;$('import-progress').textContent='Choose this run’s original IBT to restore full-resolution comparison.';}}
  window.LapComparison={
    get importTarget(){return importTarget;},
    async imported(id){const side=importTarget;importTarget=null;activateWorksheet('lap-comparison');await choose(side,id);},
    refreshRuns:refresh,
    activate(enabled){if(!enabled&&active&&state.run)updateWorkspaceStatus();active=enabled;if(enabled){refresh();if(result){$('lc-results').hidden=false;render();}}},
    resize(){if(active&&result){renderCharts();sizeMapLabels($('lc-map').querySelector('svg'));}},
  };
  $('import-dialog').addEventListener('close',()=>{importTarget=null;});
  for(const side of sides){
    $(`lc-run-${side}`).onchange=e=>choose(side,e.target.value);
    $(`lc-lap-${side}`).onchange=e=>{if(!slots[side])return;slots[side].lap=e.target.value===''?null:Number(e.target.value);persist();renderSlot(side);range=[0,100];selectedSegment=null;mapView=null;lineDistance=null;lineSpan=1.6;compare();};
    $(`lc-import-${side}`).onclick=()=>startImport(side);
    $(`lc-attach-${side}`).onclick=()=>startImport(side,true);
    $(`lc-show-${side}`).onchange=()=>renderMap();
  }
  $('lc-refresh').onclick=async()=>{try{await refreshRuns();for(const side of sides)if(slots[side])await choose(side,slots[side].run.id,slots[side].lap);}catch(e){status(e.message,true);}};
  $('lc-sort').onchange=()=>{if(result)renderRows();};
  $('lc-full').onclick=()=>{range=[0,100];selectedSegment=null;mapView=null;renderCharts();renderRows();renderDetails();renderMap();};
  $('lc-zoom').onclick=()=>{const lo=Number($('lc-from').value),hi=Number($('lc-to').value);if(!Number.isFinite(lo)||!Number.isFinite(hi)||lo<0||hi>100||lo>=hi){status('Enter an increasing range within 0–100%.',true);return;}range=[lo,hi];renderCharts();};
  $('lc-fit').onclick=()=>{mapView=null;renderMap();};$('lc-map-in').onclick=()=>mapZoom(.7);$('lc-map-out').onclick=()=>mapZoom(1/.7);
  $('lc-show-aerial').onchange=()=>renderMap();$('lc-aerial-retry').onclick=()=>loadAerial(true);
  $('lc-line-in').onclick=()=>{lineSpan=Math.max(.05,lineSpan*.5);renderLineDetail();};
  $('lc-line-out').onclick=()=>{lineSpan=Math.min(100,lineSpan*2);renderLineDetail();};
  $('lc-line-fit').onclick=()=>{const row=result?.segments.find(s=>s.id===selectedSegment)||result?.segments.find(s=>inSegment(lineDistance,s));if(row){lineSpan=row.ranges.reduce((n,[a,b])=>n+b-a,0);lineDistance=(row.ranges[0][0]+lineSpan/2)%100;renderLineDetail();}};
  $('lc-edit-toggle').onclick=()=>{$('lc-editor').hidden=!$('lc-editor').hidden;$('lc-edit-toggle').setAttribute('aria-expanded',String(!$('lc-editor').hidden));renderMap();};
  $('lc-add').onclick=()=>{const occupied=result?.segments.filter(s=>s.kind==='straight').flatMap(s=>s.ranges).sort((a,b)=>(b[1]-b[0])-(a[1]-a[0]))[0]||[0,100];draft.push({label:`Corner ${draft.length+1}`,start:Number((occupied[0]+(occupied[1]-occupied[0])*.25).toFixed(3)),end:Number((occupied[0]+(occupied[1]-occupied[0])*.75).toFixed(3))});renderEditor();renderMap();};
  $('lc-apply').onclick=async()=>{if(await compare(true))$('lc-edit-status').textContent='Draft applied. Save for track to reuse these boundaries.';};
  $('lc-save').onclick=async()=>{try{if(!await compare(true))return;await post('/api/comparison-segments',{run_id:slots.selected.run.id,corners:draft});await compare();$('lc-edit-status').textContent='Saved for this track configuration.';}catch(e){$('lc-edit-status').textContent=e.message;}};
  $('lc-reset').onclick=async()=>{try{await post('/api/comparison-segments',{run_id:slots.selected.run.id,reset:true});await compare();$('lc-edit-status').textContent='Reset to confirmed profile or automatic detection.';}catch(e){$('lc-edit-status').textContent=e.message;}};
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>window.LapComparison.resize(),100);});
  refresh();if(worksheet==='lap-comparison')window.LapComparison.activate(true);
})();
