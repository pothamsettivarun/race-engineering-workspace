'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Render the engineer's Markdown without accepting source HTML or executable URLs.
function markdownInline(value){
  const tokens=[];
  const token=html=>`\u0000${tokens.push(html)-1}\u0000`;
  const link=(label,url)=>{
    try{const parsed=new URL(url);if(!['https:','http:'].includes(parsed.protocol))return esc(label);}
    catch{return esc(label);}
    return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
  };
  let text=String(value).replace(/\u0000/g,'');
  text=text.replace(/`([^`\n]+)`|\[([^\]\n]+)\]\(([^\s)]+)\)|<(https?:\/\/[^>\s]+)>/g,
    (all,code,label,url,auto)=>token(code!==undefined?`<code>${esc(code)}</code>`:link(label||auto,url||auto)));
  text=esc(text).replace(/\*\*([^\n]+?)\*\*|__([^\n]+?)__/g,(_,a,b)=>`<strong>${a||b}</strong>`)
    .replace(/(^|[\s(])\*([^*\n]+)\*/g,'$1<em>$2</em>')
    .replace(/(^|[\s(])_([^_\n]+)_/g,'$1<em>$2</em>');
  return text.replace(/\u0000(\d+)\u0000/g,(_,id)=>tokens[Number(id)]);
}
function renderMarkdown(value){
  const lines=String(value??'').replace(/\r\n?/g,'\n').split('\n');
  const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(x=>x.trim().replace(/\\\|/g,'|'));
  const divider=line=>line.includes('|')&&cells(line).every(x=>/^:?-{3,}:?$/.test(x));
  const list=line=>/^(\s*)([-+*]|\d+[.)])\s+(.+)$/.exec(line);
  const special=(line,next)=>/^\s*(```|~~~|#{1,6}\s|>\s?|(?:-{3,}|\*{3,}|_{3,})\s*$)/.test(line)||list(line)||!!(next&&divider(next));
  let output='',i=0;
  while(i<lines.length){
    const line=lines[i];
    if(!line.trim()){i++;continue;}
    const fence=/^\s*(`{3,}|~{3,})(.*)$/.exec(line);
    if(fence){const content=[];i++;while(i<lines.length&&!lines[i].trim().startsWith(fence[1]))content.push(lines[i++]);i++;output+=`<pre><code>${esc(content.join('\n'))}</code></pre>`;continue;}
    const heading=/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if(heading){const level=Math.min(heading[1].length+1,6);output+=`<h${level}>${markdownInline(heading[2])}</h${level}>`;i++;continue;}
    if(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)){output+='<hr>';i++;continue;}
    if(i+1<lines.length&&divider(lines[i+1])){
      const headers=cells(line),align=cells(lines[i+1]).map(x=>x.startsWith(':')&&x.endsWith(':')?'center':x.endsWith(':')?'right':'left');
      const cell=(x,j,tag)=>`<${tag} style="text-align:${align[j]||'left'}">${markdownInline(x)}</${tag}>`;
      output+=`<div class="markdown-table" role="region" aria-label="Engineering comparison table" tabindex="0"><table><thead><tr>${headers.map((x,j)=>cell(x,j,'th')).join('')}</tr></thead><tbody>`;i+=2;
      while(i<lines.length&&lines[i].trim()&&lines[i].includes('|')){const row=cells(lines[i++]);output+=`<tr>${headers.map((_,j)=>cell(row[j]||'',j,'td')).join('')}</tr>`;}
      output+='</tbody></table></div>';continue;
    }
    if(/^\s*>/.test(line)){const quote=[];while(i<lines.length&&/^\s*>/.test(lines[i]))quote.push(lines[i++].replace(/^\s*>\s?/,''));output+=`<blockquote>${renderMarkdown(quote.join('\n'))}</blockquote>`;continue;}
    const first=list(line);
    if(first){
      const ordered=/\d/.test(first[2]),indent=first[1].length,tag=ordered?'ol':'ul';
      output+=`<${tag}${ordered?` start="${parseInt(first[2],10)}"`:''}>`;
      while(i<lines.length){
        const current=list(lines[i]);if(!current||current[1].length!==indent||/\d/.test(current[2])!==ordered)break;
        const content=[current[3]],padding=current[1].length+current[2].length+1;i++;
        while(i<lines.length){
          if(!lines[i].trim()){if(i+1<lines.length&&/^\s/.test(lines[i+1])&&lines[i+1].search(/\S/)>indent){content.push('');i++;continue;}break;}
          if(lines[i].search(/\S/)<=indent)break;
          content.push(lines[i++].slice(padding));
        }
        output+=`<li>${renderMarkdown(content.join('\n'))}</li>`;
        if(i<lines.length&&!lines[i].trim()&&list(lines[i+1]||''))i++;
      }
      output+=`</${tag}>`;continue;
    }
    const paragraph=[line];i++;
    while(i<lines.length&&lines[i].trim()&&!special(lines[i],lines[i+1]))paragraph.push(lines[i++]);
    output+=`<p>${paragraph.map(markdownInline).join('\n')}</p>`;
  }
  return output;
}
const state = {runs:[], run:null, lap:null, reference:null, baseline:null, zoom:[0,100], charts:[], status:{}, importBusy:false, loginTimer:null};
const colors = {selected:'#0080ff',reference:'#ffffff',brake:'#ff0000',throttle:'#00ff00',additional:'#00ffff',delta:'#ffffff',lf:'#ff0000',rf:'#00ff00',lr:'#0080ff',rr:'#ffff00'};
function lapTime(value){if(value == null)return '—';return `${Math.floor(value/60)}:${(value%60).toFixed(3).padStart(6,'0')}`;}
function number(value,digits=1){if(value==null)return '—';const text=Number(value).toFixed(digits);return Number(text)===0?text.replace(/^-/, ''):text;}
function delta(value){return value == null ? '—' : `${value>=0?'+':''}${value.toFixed(3)} s`;}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);$('notice').hidden=!message;}
async function api(path,options={}){const response=await fetch(path,options);const result=await response.json();if(!response.ok){if(response.status===404&&path==='/api/remove-run')throw new Error('The running Stint server needs to be restarted to enable session removal. Close the Stint server process, then launch Start Stint.cmd.');throw new Error(result.error||'Request failed.');}return result;}
const post=(path,body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function usable(run){return run.laps.filter(l=>l.eligible&&!run.notes.excluded_laps.includes(l.id));}
function lapOptions(run){return run.laps.map(l=>`<option value="${l.id}">Lap ${l.number} · ${lapTime(l.duration)}${!l.eligible?' · flagged':run.notes.excluded_laps.includes(l.id)?' · excluded':''}</option>`).join('');}

function lapNumberMarkup(l){
 const role=l.id===state.lap?'selected':l.id===state.reference?'reference':'other';
 return `<span class="lap-number lap-number-${role}">${esc(l.number)}</span>`;
}
function lapTimeMarkup(l){
 const times=usable(state.run).map(x=>x.duration).filter(Number.isFinite);
 const fastest=times.length&&l.eligible&&!state.run.notes.excluded_laps.includes(l.id)&&l.duration===Math.min(...times);
 return `<span class="lap-time${fastest?' lap-time-fastest':''}"${fastest?' title="Fastest valid lap"':''}>${lapTime(l.duration)}</span>`;
}
const lapPicker={id:null,index:0,popup:null};
function pickerValues(id){return (id==='reference-select'?[null]:[]).concat(state.run?.laps||[]);}
function pickerLabel(l){return l?`Lap ${lapNumberMarkup(l)} <span class="lap-divider">·</span> ${lapTimeMarkup(l)}`:'No reference';}
function closeLapPicker(){
 if(lapPicker.id){const button=$(lapPicker.id+'-picker');button.setAttribute('aria-expanded','false');button.removeAttribute('aria-activedescendant');}
 lapPicker.popup?.remove();lapPicker.popup=null;lapPicker.id=null;
}
function renderLapPickers(){
 for(const id of ['lap-select','reference-select']){
  const button=$(id+'-picker');if(!button)continue;
  const value=$(id).value,l=state.run?.laps.find(x=>String(x.id)===value);
  button.innerHTML=`<span class="lap-picker-value">${pickerLabel(l)}</span><span class="lap-picker-arrow" aria-hidden="true">⌄</span>`;
  button.disabled=!state.run;
 }
 // Existing selections may change through sidebar navigation or evidence links.
 if(lapPicker.id)closeLapPicker();
}
function highlightPickerOption(){
 const options=lapPicker.popup.querySelectorAll('[role=option]');
 options.forEach((option,i)=>option.classList.toggle('picker-active',i===lapPicker.index));
 const active=options[lapPicker.index];
 $(lapPicker.id+'-picker').setAttribute('aria-activedescendant',active.id);
 active.scrollIntoView({block:'nearest'});
}
function choosePickerOption(index){
 const id=lapPicker.id,lap=pickerValues(id)[index];
 closeLapPicker();$(id).value=lap?.id??'';$(id).dispatchEvent(new Event('change'));$(id+'-picker').focus({preventScroll:true});
}
function openLapPicker(id){
 closeLapPicker();if(!state.run)return;
 const button=$(id+'-picker'),values=pickerValues(id);
 lapPicker.id=id;lapPicker.index=Math.max(0,values.findIndex(l=>String(l?.id??'')===$(id).value));
 const popup=document.createElement('div');lapPicker.popup=popup;popup.className='lap-picker-popup';popup.id=id+'-options';popup.setAttribute('role','listbox');popup.setAttribute('aria-label',id==='lap-select'?'Selected lap':'Reference lap');
 popup.innerHTML=values.map((l,i)=>{
  const flag=l?(!l.eligible?'flagged':state.run.notes.excluded_laps.includes(l.id)?'excluded':''):'';
  return `<div role="option" id="${id}-option-${i}" aria-selected="${i===lapPicker.index}" data-picker-index="${i}">${pickerLabel(l)}${flag?` <span class="lap-picker-flag">· ${flag}</span>`:''}</div>`;
 }).join('');
 document.body.append(popup);
 const rect=button.getBoundingClientRect(),width=Math.min(innerWidth-8,Math.max(rect.width,215));
 const below=innerHeight-rect.bottom-4,above=rect.top-4,height=Math.min(380,Math.max(below,above));
 popup.style.width=width+'px';popup.style.left=Math.max(4,Math.min(rect.left,innerWidth-width-4))+'px';popup.style.maxHeight=height+'px';
 if(below>=Math.min(380,above))popup.style.top=rect.bottom+'px';else popup.style.bottom=(innerHeight-rect.top)+'px';
 button.setAttribute('aria-expanded','true');highlightPickerOption();
 popup.addEventListener('pointerdown',event=>event.preventDefault());
 popup.addEventListener('click',event=>{const option=event.target.closest('[data-picker-index]');if(option)choosePickerOption(Number(option.dataset.pickerIndex));});
}
function initLapPickers(){
 for(const id of ['lap-select','reference-select']){
  const select=$(id);select.hidden=true;select.setAttribute('aria-hidden','true');select.tabIndex=-1;
  const button=document.createElement('button');button.id=id+'-picker';button.type='button';button.className='lap-picker';button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls',id+'-options');button.setAttribute('aria-label',id==='lap-select'?'Selected lap':'Reference lap');select.after(button);
  button.addEventListener('click',event=>{event.preventDefault();if(lapPicker.id===id)closeLapPicker();else openLapPicker(id);});
  button.addEventListener('keydown',event=>{
   const open=lapPicker.id===id;
   if(event.key==='Escape'){closeLapPicker();event.preventDefault();return;}
   if(event.key==='Tab'){closeLapPicker();return;}
   if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
    event.preventDefault();if(!open){openLapPicker(id);return;}
    const last=pickerValues(id).length-1;
    lapPicker.index=event.key==='Home'?0:event.key==='End'?last:Math.max(0,Math.min(last,lapPicker.index+(event.key==='ArrowDown'?1:-1)));highlightPickerOption();
   }else if(open&&['Enter',' '].includes(event.key)){event.preventDefault();choosePickerOption(lapPicker.index);}
  });
 }
 document.addEventListener('pointerdown',event=>{if(lapPicker.id&&!lapPicker.popup.contains(event.target)&&!event.target.closest('.lap-picker'))closeLapPicker();});
 window.addEventListener('resize',closeLapPicker);renderLapPickers();
}

function runLabel(run){return run.notes.setup_label||run.metadata.setup_name||run.filename;}

async function refreshRuns(){state.runs=await api('/api/runs');renderRuns();if(!state.run&&state.runs.length)document.querySelectorAll('[data-tab]').forEach(b=>b.disabled=false);window.LapComparison?.refreshRuns();}
function renderRuns(){
  $('runs').innerHTML=state.runs.length?state.runs.map(r=>`<div class="run-entry ${state.run?.id===r.id?'active':''}"><button class="run-button ${state.run?.id===r.id?'active':''}" data-run="${r.id}" title="${esc(r.filename)} · Imported ${esc(new Date(r.imported_at).toLocaleString())}"><strong>${esc(r.metadata.track)}</strong><small>${esc(runLabel(r))}</small><small>${lapTime(r.summary.best)} · ${r.summary.usable_laps} usable laps</small></button><button class="remove-run" data-remove-run="${r.id}" aria-label="Remove session ${esc(r.filename)}" title="Remove session">×</button></div>`).join(''):'<p class="quiet">Import a run to begin.</p>';
  $('runs').querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>loadRun(b.dataset.run));
  $('runs').querySelectorAll('[data-remove-run]').forEach(b=>b.onclick=()=>{
    const run=state.runs.find(r=>r.id===b.dataset.removeRun);
    $('remove-run-dialog').dataset.run=run.id;
    $('remove-run-name').textContent=run.filename;
    $('remove-run-details').textContent=`${run.metadata.track} · Imported ${new Date(run.imported_at).toLocaleString()}`;
    $('remove-run-error').textContent='';$('remove-run-dialog').showModal();
  });
}
function clearRunWorkspace(){
  closeLapPicker();state.run=null;state.lap=null;state.reference=null;state.baseline=null;state.charts=[];
  localStorage.removeItem('stint.lastRun');
  for(const id of ['browser-laps','channel-browser','charts','stint-summary','stint-chart','lap-table','condition-cards','condition-charts','condition-metadata','comparison','setup-snapshot','findings','ai-history','track-map','zones','summary','lap-select','reference-select','baseline-select'])$(id).innerHTML='';
  $('notes-form').reset();$('question-form').reset();$('manual-conditions').value='';$('verdict-notes').value='';
  $('export-button').hidden=true;$('session-caption').textContent='';$('session-caption').title='';$('workspace-status').textContent='Open an .ibt recording';
  $('info-dialog').close();$('map-dialog').close();setRunControls(false);renderLapPickers();activateWorksheet('live');
}
function setRunControls(enabled){
  for(const id of ['info-button','attach-recording','edit-map-button','save-conditions','save-verdict','reference-select','lap-select','reset-zoom'])$(id).disabled=!enabled;
  document.querySelectorAll('[data-tab]').forEach(b=>b.disabled=!enabled&&!['live','library','lap-comparison'].includes(b.dataset.tab));
}
$('confirm-remove-run').onclick=async()=>{
  const button=$('confirm-remove-run'),id=$('remove-run-dialog').dataset.run;button.disabled=true;
  try{
    await post('/api/remove-run',{id});$('remove-run-dialog').close();
    const wasCurrent=state.run?.id===id,previousWorksheet=worksheet;
    if(wasCurrent)clearRunWorkspace();
    await refreshRuns();
    if(wasCurrent&&state.runs.length){await loadRun(state.runs[0].id);activateWorksheet(previousWorksheet);}
    else if(state.run){const baseline=state.baseline;state.baseline=null;renderWorkspace();renderRuns();if(baseline&&baseline!==id){$('baseline-select').value=baseline;await updateComparison();}}
    notice('Session removed from Stint.');
  }catch(error){$('remove-run-error').textContent=error.message;notice(error.message,true);}
  finally{button.disabled=false;}
};
async function loadRun(id){
  try{
    notice('Loading run…');state.run=await api(`/api/run?id=${id}`);state.lap=state.run.summary.best_lap_id??state.run.laps[0]?.id;
    const candidates=usable(state.run).filter(l=>l.id!==state.lap).sort((a,b)=>a.duration-b.duration);
    state.reference=candidates[0]?.id??state.lap;state.zoom=[0,100];state.baseline=null;
    localStorage.setItem('stint.lastRun',id);renderWorkspace();renderRuns();notice('');
  }catch(error){notice(error.message,true);}
}
function renderWorkspace(){
  const r=state.run;if(!r)return;
  setRunControls(true);
  renderProject();
  $('empty').hidden=true;$('workspace').hidden=false;$('export-button').hidden=false;
  $('run-file').textContent=r.filename;$('run-title').textContent=r.metadata.track;
  $('run-subtitle').textContent=`${r.metadata.car} · ${r.metadata.configuration||'Recorded session'} · ${new Date(r.metadata.recorded_at).toLocaleString()}`;
  const s=r.summary;
  const summaryHtml=[['Best eligible lap',lapTime(s.best)],['Median pace',lapTime(s.median)],['Lap variation',s.stdev==null?'—':`${s.stdev.toFixed(3)} s`],['Usable laps / recorded segments',`${s.usable_laps} / ${s.total_laps}`],['Track temperature',`${number(r.conditions.track_temp?.start)} °C`]].map(([label,value])=>`<div class="stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join('');
  for(const id of ['summary','stint-summary'])$(id).innerHTML=summaryHtml;
  $('lap-select').innerHTML=lapOptions(r);$('lap-select').value=state.lap;
  $('reference-select').innerHTML='<option value="">No reference</option>'+lapOptions(r);$('reference-select').value=state.reference??'';
  const bind={'setup-label':'setup_label',objective:'objective',vision:'vision',feedback:'feedback','setup-notes':'setup_notes',changes:'changes','manual-conditions':'manual_conditions',verdict:'verdict','verdict-notes':'verdict_notes'};
  Object.entries(bind).forEach(([id,key])=>$(id).value=r.notes[key]||'');
  $('baseline-select').innerHTML='<option value="">Choose a baseline run</option>'+state.runs.filter(x=>x.id!==r.id).map(x=>`<option value="${x.id}">${esc(x.metadata.track)} · ${esc(runLabel(x))}</option>`).join('');
  $('comparison').innerHTML='<p class="quiet">Import a second run, then select your baseline here to inspect pace, conditions, and recorded setup differences.</p>';
  renderTelemetry();renderConditions();renderSetup();renderFindings();renderHistory();loadLibrary();
  if(r.metadata.warnings.length)notice(r.metadata.warnings.join('\n'),true);
}
function series(lap,key,label,color){return lap&&lap.traces[key]?{x:lap.traces.distance,y:lap.traces[key],label,color,isReference:color===colors.reference,statChannel:key,statLap:lap.id}:null;}
function renderTelemetry(){
  const r=state.run,lap=r.laps.find(l=>l.id===state.lap),ref=r.laps.find(l=>l.id===state.reference);
  state.charts=[];$('charts').innerHTML='';
  $('zoom-label').textContent=`${number(state.zoom[0])}–${number(state.zoom[1])}% lap distance`;
  const selected=lap?`Lap ${lap.number}`:'Selected',reference=ref?`Ref ${ref.number}`:'Reference';
  const statChannels=['Speed','Throttle','Brake',...($('channel-select').value==='rideHeight'?['LFrideHeight','RFrideHeight','LRrideHeight','RRrideHeight']:[$('channel-select').value]),'TimeDelta'];
  const statRequest={run_id:r.id,lap_ids:[...new Set([state.lap,state.reference].filter(x=>x!=null))],channels:statChannels,distance_range:[...state.zoom],reference_lap_id:state.reference};
  const statsPromise=requestPlotStats(statRequest);
  const add=(title,unit,seriesList,options={})=>{const host=document.createElement('div');$('charts').append(host);makeChart(host,title,unit,seriesList.filter(Boolean),{range:state.zoom,linked:true,statsPromise,...options});};
  add('Speed','km/h',[series(lap,'Speed',selected,colors.selected),series(ref,'Speed',reference,colors.reference)]);
  add('Throttle','%',[series(lap,'Throttle',selected,colors.throttle),series(ref,'Throttle',reference,colors.reference)],{fixedY:[0,100]});
  add('Brake','%',[series(lap,'Brake',selected,colors.brake),series(ref,'Brake',reference,colors.reference)],{fixedY:[0,100]});
  const channel=$('channel-select').value;
  if(channel==='rideHeight'){
    add('Ride heights · selected lap','mm',['LF','RF','LR','RR'].map((side,i)=>series(lap,`${side}rideHeight`,side,[colors.lf,colors.rf,colors.lr,colors.rr][i])));
  }else{
    const labels={SteeringWheelAngle:['Steering wheel','degrees'],YawRate:['Yaw rate','degrees/s'],LatAccel:['Lateral acceleration','m/s²'],LongAccel:['Longitudinal acceleration','m/s²'],FuelLevel:['Fuel','L']};
    add(...labels[channel],[series(lap,channel,selected,colors.additional),series(ref,channel,reference,colors.reference)],{zeroLine:['SteeringWheelAngle','YawRate','LatAccel','LongAccel'].includes(channel)});
  }
  if(lap&&ref&&lap.complete&&ref.complete){
    const x=[],y=[];
    const lo=Math.max(lap.traces.distance[0],ref.traces.distance[0]),hi=Math.min(lap.traces.distance.at(-1),ref.traces.distance.at(-1));
    for(let d=lo;d<=hi;d+=.1){x.push(d);y.push(interpolate(lap.traces.distance,lap.traces.time,d)-interpolate(ref.traces.distance,ref.traces.time,d));}
    add('Elapsed time delta · positive is slower','s',[{x,y,label:'Selected − reference',color:colors.delta,statChannel:'TimeDelta',statLap:lap.id}],{zeroLine:true});
  }
  $('zones').innerHTML=r.zones.length?r.zones.map((z,i)=>`<button data-zone="${i}" class="${state.zoom[0]===z.start&&state.zoom[1]===z.end?'active':''}" title="${z.start}–${z.end}% · min ${number(z.min_speed)} km/h">${esc(z.label)}</button>`).join(''):'<span class="quiet">No driving zones detected in an eligible lap.</span>';
  $('zones').querySelectorAll('[data-zone]').forEach(b=>b.onclick=()=>zoomZone(r.zones[Number(b.dataset.zone)]));
  if($('charts').children.length<5){const host=document.createElement('div');host.className='chart';host.innerHTML='<div class="chart-heading">Elapsed time delta · seconds</div><p>Select two complete laps to compare time delta.</p>';$('charts').append(host);}renderLapTable();renderStint();renderCornerMap();renderBrowserLaps();renderLapPickers();renderChannelBrowser();updateWorkspaceStatus();
}
function zoomZone(zone){state.zoom=[zone.start,zone.end];renderTelemetry();document.querySelector('[data-tab="telemetry"]').click();$('charts').focus({preventScroll:true});}
function interpolate(x,y,target){let lo=0,hi=x.length-1;while(lo<hi){let mid=Math.floor((lo+hi)/2);if(x[mid]<target)lo=mid+1;else hi=mid;}let j=lo,i=Math.max(0,j-1);if(y[i]==null||y[j]==null)return null;return x[j]===x[i]?y[j]:y[i]+(y[j]-y[i])*(target-x[i])/(x[j]-x[i]);}
function makeChart(host,title,unit,seriesList,options={}){
  host.className='chart';host.innerHTML=`<div class="chart-heading"><span>${esc(title)} <span class="quiet">${esc(unit)}</span></span><div class="plot-statistics" aria-label="Visible range statistics"></div></div><div class="readout"></div>`;
  if(!seriesList.length){host.querySelector('.plot-statistics').textContent='Statistics unavailable';host.innerHTML+='<p class="quiet">This channel is not available in this log.</p>';return;}
  if(options.noStats)host.querySelector('.plot-statistics').textContent='Original-sample measurements below';else populatePlotStats(host,seriesList,options);
  const fitted=host.closest('#charts');const width=Math.max(180,host.clientWidth-10)||850,height=fitted?Math.max(45,host.clientHeight-host.querySelector('.chart-heading').offsetHeight-16):180,left=43,right=18,top=8,bottom=22,plotW=width-left-right,plotH=height-top-bottom;
  const minX=options.range?.[0]??Math.min(...seriesList.map(s=>s.x[0])),maxX=options.range?.[1]??Math.max(...seriesList.map(s=>s.x.at(-1)));
  const values=[];seriesList.forEach(s=>s.x.forEach((x,i)=>{if(x>=minX&&x<=maxX&&s.y[i]!=null&&Number.isFinite(s.y[i]))values.push(s.y[i]);}));
  let minY=options.fixedY?.[0]??(values.length?Math.min(...values):0),maxY=options.fixedY?.[1]??(values.length?Math.max(...values):1);
  if(options.zeroLine&&!options.fixedY){minY=Math.min(0,minY);maxY=Math.max(0,maxY);}
  if(minY===maxY){minY-=1;maxY+=1;}else if(!options.fixedY){const pad=(maxY-minY)*.08;minY-=pad;maxY+=pad;}
  const sx=x=>left+(x-minX)/(maxX-minX||1)*plotW,sy=y=>top+(maxY-y)/(maxY-minY)*plotH;
  let svg=`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${esc(title)} chart"><title>${esc(title)} in ${esc(unit)}</title>`;
  for(let i=0;i<=4;i++){const y=top+plotH*i/4,value=maxY-(maxY-minY)*i/4;if(options.zeroLine&&Math.abs(y-sy(0))<9)continue;svg+=`<line class="grid" x1="${left}" y1="${y}" x2="${width-right}" y2="${y}"/><text x="${left-7}" y="${y+3}" text-anchor="end">${value.toFixed(Math.abs(maxY-minY)<5?2:0)}</text>`;}
  if(options.zeroLine)svg+=`<line class="zero-line" x1="${left}" y1="${sy(0)}" x2="${width-right}" y2="${sy(0)}"/><text class="zero-label" x="${left-7}" y="${sy(0)+3}" text-anchor="end">0</text>`;
  for(let i=0;i<=5;i++){const x=minX+(maxX-minX)*i/5;svg+=`<text x="${sx(x)}" y="${height-8}" text-anchor="middle">${x.toFixed(options.linked?1:0)}${options.linked?'%':''}</text>`;}
  // Paint reference overlays first; selected traces remain visible at intersections.
  [...seriesList].sort((a,b)=>Number(!!b.isReference)-Number(!!a.isReference)).forEach(s=>{let path='',started=false;s.x.forEach((x,i)=>{const y=s.y[i];if(x<minX||x>maxX||y==null||!Number.isFinite(y)){started=false;return;}path+=`${started?'L':'M'}${sx(x).toFixed(2)},${sy(y).toFixed(2)} `;started=true;});svg+=`<path class="trace" stroke="${s.color}" d="${path}"/>`;if(options.dots)s.x.forEach((x,i)=>{if(s.y[i]!=null)svg+=`<circle cx="${sx(x)}" cy="${sy(s.y[i])}" r="3.5" fill="${s.color}"/>`;});});
  svg+=`<line class="crosshair" x1="${left}" x2="${left}" y1="${top}" y2="${top+plotH}" visibility="hidden"/></svg>`;host.insertAdjacentHTML('beforeend',svg);
  const element=host.querySelector('svg'),cross=host.querySelector('.crosshair'),readout=host.querySelector('.readout');
  readout.textContent=seriesList.map(s=>s.label).join(' / ');
  const update=x=>{const px=sx(x);cross.setAttribute('x1',px);cross.setAttribute('x2',px);cross.setAttribute('visibility','visible');readout.textContent=seriesList.map(s=>`${s.label}: ${number(interpolate(s.x,s.y,x),unit==='s'?3:1)}`).join(' · ');};
  const hide=()=>{cross.setAttribute('visibility','hidden');readout.textContent=seriesList.map(s=>s.label).join(' / ');};
  element.addEventListener('pointermove',event=>{const rect=element.getBoundingClientRect();const px=(event.clientX-rect.left)/rect.width*width;const x=Math.max(minX,Math.min(maxX,minX+(px-left)/plotW*(maxX-minX)));if(options.onCursor)options.onCursor(x);else if(options.linked){state.charts.forEach(c=>c.update(x));highlightMapPosition(x);}else update(x);});
  element.addEventListener('pointerleave',()=>{if(options.onLeave)options.onLeave();else if(options.linked)state.charts.forEach(c=>c.hide());else hide();});
  if(options.linkGroup)options.linkGroup.push({update,hide});else if(options.linked)state.charts.push({update,hide});
}
function renderLapTable(){
  const r=state.run;
  const visible=r.laps.filter(l=>$('show-flagged').checked||l.eligible||l.id===state.lap);
  $('lap-table').innerHTML=visible.map(l=>{const excluded=r.notes.excluded_laps.includes(l.id);return `<tr class="${state.lap===l.id?'selected':''} ${!l.eligible||excluded?'excluded':''}"><td><input type="checkbox" data-use="${l.id}" aria-label="Include lap ${l.number}" ${l.eligible&&!excluded?'checked':''} ${!l.eligible?'disabled':''}></td><td><button class="lap-link" data-lap="${l.id}">${lapNumberMarkup(l)}</button></td><td>${lapTimeMarkup(l)}${!l.complete?' <small>partial</small>':''}</td><td>${number(l.metrics.fuel_start_l)} L</td><td>${number(l.metrics.TrackTemp)} °C</td><td>${esc(l.reasons.join('; ')|| (excluded?'Excluded by you':'Eligible · review traffic / tires'))}</td></tr>`;}).join('');
  $('lap-table').querySelectorAll('[data-lap]').forEach(b=>b.onclick=()=>{state.lap=Number(b.dataset.lap);$('lap-select').value=state.lap;renderTelemetry();});
  $('lap-table').querySelectorAll('[data-use]').forEach(box=>box.onchange=async()=>{const id=Number(box.dataset.use),excluded=new Set(r.notes.excluded_laps);box.checked?excluded.delete(id):excluded.add(id);try{await post('/api/notes',{id:r.id,notes:{excluded_laps:[...excluded]}});state.run=await api(`/api/run?id=${r.id}`);await refreshRuns();renderSummaryOnly();renderTelemetry();renderFindings();if(state.baseline)await updateComparison();}catch(error){notice(error.message,true);renderLapTable();}});
}
function renderSummaryOnly(){const s=state.run.summary;const values=[lapTime(s.best),lapTime(s.median),s.stdev==null?'—':`${s.stdev.toFixed(3)} s`,`${s.usable_laps} / ${s.total_laps}`,`${number(state.run.conditions.track_temp?.start)} °C`];for(const id of ['summary','stint-summary'])$(id).querySelectorAll('strong').forEach((e,i)=>e.textContent=values[i]);}
function renderStint(){const r=state.run,ls=usable(r);$('stint-chart').innerHTML='';if(!ls.length){$('stint-chart').innerHTML='<p class="quiet">No eligible complete laps for a stint chart.</p>';return;}makeChart($('stint-chart'),'Eligible lap progression · recorded segment index','s',[{x:ls.map(l=>l.id+1),y:ls.map(l=>l.duration),label:'Lap time',color:colors.selected,statChannel:'LapTime',statLap:null}],{dots:true,statsScope:'stint'});}
function renderConditions(){
  const r=state.run,names={air_temp:'Air temperature',track_temp:'Track temperature',track_temp_crew:'Crew track temperature',wind_speed:'Wind speed',wind_direction:'Wind direction',humidity:'Relative humidity',precipitation:'Precipitation',wetness:'Track wetness'};
  const wetNames=['Unknown','Dry','Mostly dry','Very lightly wet','Lightly wet','Moderately wet','Very wet','Extremely wet'];
  $('condition-cards').innerHTML=Object.entries(names).map(([key,label])=>{const c=r.conditions[key];return `<div class="condition-card"><span class="quiet">${label}</span><strong>${c?key==='wetness'?esc(wetNames[Math.round(c.start)]||'Unknown'):`${number(c.start)} ${esc(c.unit)}`:'Unavailable'}</strong><span class="quiet">${c?`Range ${number(c.min)}–${number(c.max)} · ${esc(c.source)}`:'No recorded channel'}</span></div>`;}).join('');
  $('condition-charts').innerHTML='';
  for(const [label,unit,keys] of [['Temperature','°C',['track_temp','air_temp','track_temp_crew']],['Wind speed','m/s',['wind_speed']],['Track wetness','category',['wetness']]]){const host=document.createElement('div');$('condition-charts').append(host);makeChart(host,`${label} · elapsed seconds`,unit,keys.filter(k=>r.conditions[k]).map((k,i)=>({x:r.conditions[k].time,y:r.conditions[k].values,label:names[k],color:[colors.selected,'#ff8000',colors.throttle][i],statChannel:r.conditions[k].source.split(': ')[1],statLap:null})),{statsScope:'session'});}
  $('condition-metadata').innerHTML='<div class="metadata-list">'+Object.entries(r.metadata.conditions).map(([key,c])=>`<div class="metadata-row"><span>${esc(key.replaceAll('_',' '))}</span><span>${esc(c.value)} <small class="quiet">${esc(c.source)}</small></span></div>`).join('')+`<div class="metadata-row"><span>Simulator build</span><span>${esc(r.metadata.build)}</span></div></div>`;
}
function renderSetup(){const flat=state.run.metadata.setup_flat;let group='';$('setup-snapshot').innerHTML=Object.keys(flat).length?Object.entries(flat).map(([key,value])=>{const parts=key.split(' / '),heading=parts.slice(0,-1).join(' / ');let html='';if(heading!==group){group=heading;html=`<h3 class="setup-group">${esc(heading)}</h3>`;}return html+`<div class="setup-row"><span>${esc(parts.at(-1))}</span><span>${esc(value)}</span></div>`;}).join(''):'<p>No setup snapshot was decoded. Record the actual garage settings in the engineering brief.</p>';}
function renderFindings(){const r=state.run;$('findings').innerHTML=r.findings.map((f,i)=>`<article class="finding"><span class="kind">${esc(f.kind)}</span><h3>${esc(f.title)}</h3><p><strong>Evidence.</strong> ${esc(f.evidence)}</p><p><strong>Interpretation.</strong> ${esc(f.hypothesis)}</p><p><strong>Next experiment.</strong> ${esc(f.experiment)}</p><p><strong>Success.</strong> ${esc(f.success)}</p>${f.zone?`<button class="text-button" data-finding="${i}">Inspect ${esc(f.zone.label)}</button>`:''}</article>`).join('');$('findings').querySelectorAll('[data-finding]').forEach(b=>b.onclick=()=>zoomZone(r.findings[Number(b.dataset.finding)].zone));}
async function saveNotes(extra={}){
  if(!state.run)return;const values={setup_label:$('setup-label').value,objective:$('objective').value,vision:$('vision').value,feedback:$('feedback').value,setup_notes:$('setup-notes').value,changes:$('changes').value,...extra};
  $('save-status').textContent='Saving…';await post('/api/notes',{id:state.run.id,notes:values});state.run=await api(`/api/run?id=${state.run.id}`);await refreshRuns();renderFindings();renderSummaryOnly();$('save-status').textContent='Saved locally';
}
async function updateComparison(){
  const id=$('baseline-select').value;state.baseline=id||null;
  if(!id){$('comparison').innerHTML='<p class="quiet">Choose a baseline run to compare.</p>';return;}
  $('comparison').textContent='Comparing runs…';
  try{const c=await api(`/api/compare?current=${state.run.id}&baseline=${id}`);
    $('comparison').innerHTML=`<div class="comparison-result"><span class="quiet">Current minus baseline · negative is faster</span><strong class="delta">${delta(c.median_delta)}</strong><p>Median ${lapTime(c.baseline.median)} → ${lapTime(c.current.median)} · best-lap difference ${delta(c.best_delta)}</p><p><strong>${esc(c.verdict)}</strong></p></div><h3>Comparison constraints</h3><ul class="warning-list">${c.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul><h3>Recorded setup differences</h3><p class="quiet">Garage readings and calculated values are separated from candidate adjustable settings.</p>${renderChanges(c.setup_changes)}<h3>Cross-run traces</h3><p class="quiet">Best eligible laps from each run. Confirm similar fuel and tire state before interpreting.</p><div id="cross-run-chart"></div>`;
    const baseline=await api(`/api/run?id=${id}`),a=state.run.laps.find(l=>l.id===state.run.summary.best_lap_id),b=baseline.laps.find(l=>l.id===baseline.summary.best_lap_id);
    makeChart($('cross-run-chart'),'Best eligible lap speed','km/h',[series(a,'Speed','Current',colors.selected),{...series(b,'Speed','Baseline',colors.reference),statsRunId:baseline.id,statsRecording:baseline.recording?.sha256}].filter(x=>x&&x.statChannel));
  }catch(error){$('comparison').textContent=error.message;notice(error.message,true);}
}
function renderChanges(changes){
  if(!changes.length)return '<p class="quiet">No decoded setting differences. Review manually recorded changes in the brief.</p>';
  const readout=key=>/LastHot|LastTemps|TreadRemaining|UpdateCount|CornerWeight|ShockDefl|SpringDefl|SliderDefl|DamperDefl|TorsionBarDefl|RideHeight|AeroCalculator|CrossWeight/.test(key);
  const settings=changes.filter(c=>!readout(c.setting)),readouts=changes.filter(c=>readout(c.setting));
  const rows=items=>items.map(c=>`<div class="setup-row"><span>${esc(c.setting)}</span><span>${esc(c.before??'—')} → ${esc(c.after??'—')}</span></div>`).join('');
  return (settings.length?rows(settings):'<p class="quiet">No candidate adjustable-setting differences detected.</p>')+(readouts.length?`<details><summary class="quiet">${readouts.length} garage / calculated readout differences</summary>${rows(readouts)}</details>`:'');
}
async function loadLibrary(){try{const items=await api('/api/library');$('library-list').innerHTML=items.length?items.map(x=>`<div class="library-entry"><strong>${esc(x.title)}</strong><small>${esc(x.source||'Private note')} · ${x.characters.toLocaleString()} characters${x.pages?` · ${x.pages} pages`:''}${x.generated_captions?' · auto captions':''} · ${esc(x.status||'imported')}</small>${x.caveat?`<p class="source-caveat">${esc(x.caveat)}</p>`:''}${x.characters?`<details data-source="${x.id}"><summary>Inspect imported text</summary><div class="source-content">Loading…</div></details>`:''}</div>`).join(''):'<p class="quiet">No private sources yet. Add your manuals, notes, or transcripts.</p>';$('library-list').querySelectorAll('details[data-source]').forEach(element=>element.ontoggle=async()=>{if(!element.open||element.dataset.loaded)return;try{const item=await api(`/api/library-source?id=${element.dataset.source}`);element.querySelector('.source-content').textContent=item.text;element.dataset.loaded='true';}catch(error){element.querySelector('.source-content').textContent=error.message;}});}catch(error){notice(error.message,true);}}
function renderHistory(){const history=state.run.ai_history||[];$('ai-history').innerHTML=history.map(x=>`<article class="answer"><p class="question">${esc(x.question)}</p><div class="answer-text">${renderMarkdown(x.answer)}</div>${renderEvidenceManifest(x.evidence_manifest)}<span class="quiet">${esc(x.model)} · ${new Date(x.created_at).toLocaleString()}</span>${x.sources.length?`<details><summary>Retrieved source excerpts</summary>${x.sources.map(s=>`<h3>${esc(s.title)}</h3><p>${esc(s.excerpt)}</p><small>${esc(s.source)}</small>`).join('')}</details>`:''}</article>`).join('');bindEvidenceLinks();}
function renderProject(){const p=state.run?state.run.development_project:state.status.project;if(!p){$('project-banner').hidden=true;return;}$('project-banner').hidden=false;$('project-title').textContent=p.title;$('project-vision').textContent=p.vision;$('project-run-note').textContent=state.run?`This run matches the development car and ${state.run.metadata.track} · ${state.run.metadata.configuration||'default configuration'}. Confirm your test conditions.`:'Matching car and track imports start with this objective and vision.';}
function updateAIStatus(){const s=state.status,c=s.codex||{},provider=s.provider||'api';$('ai-status').textContent=s.ai_connected?(provider==='chatgpt'?`ChatGPT connected${c.plan_type?' · '+c.plan_type:''}`:'API connected · '+s.model):'Local analysis · AI not connected';$('ask-button').disabled=!s.ai_connected;$('ai-description').textContent=s.ai_connected?(provider==='chatgpt'?'Using your ChatGPT plan through local Codex. Questions send measured summaries and source excerpts; save your brief first.':'Using the OpenAI API. Questions send measured summaries and source excerpts; save your brief first.'):'Sign in with ChatGPT in Connect AI to ask questions using your plan’s Codex access. Local findings require no connection.';$('codex-auth-status').textContent=c.connected?`Signed in with ChatGPT${c.plan_type?' · '+c.plan_type+' plan':''}.`:c.login_pending?'Waiting for you to complete browser sign-in…':c.error||'Not signed in. Complete the official browser sign-in to continue.';$('chatgpt-login').disabled=!!c.login_pending||c.available===false;$('chatgpt-cancel').hidden=!c.login_pending;renderProject();}
function providerOptions(){const chatgpt=$('ai-provider').value==='chatgpt';$('chatgpt-options').hidden=!chatgpt;$('api-options').hidden=chatgpt;}
async function loadModels(){if(!state.status.codex?.connected)return;try{const models=await api('/api/codex/models');const selected=state.status.provider==='chatgpt'?state.status.model:'auto';$('codex-model').innerHTML='<option value="auto">Use Codex default model</option>'+models.map(m=>`<option value="${esc(m.id)}">${esc(m.name||m.id)}</option>`).join('');if(selected!=='auto'&&!models.some(m=>m.id===selected))$('codex-model').add(new Option(selected,selected));$('codex-model').value=selected;}catch(error){$('codex-auth-status').textContent=error.message;}}
async function refreshAIStatus(){state.status=await api('/api/status');updateAIStatus();if(state.status.codex?.connected){if(state.loginTimer){clearInterval(state.loginTimer);state.loginTimer=null;}$('chatgpt-login-link').hidden=true;await loadModels();}}
async function refreshFiles(){try{const files=await api('/api/files');$('local-files').innerHTML=files.length?files.map(f=>`<button class="local-file" data-token="${f.token}"><strong>${esc(f.name)}</strong><small>${(f.size/1024/1024).toFixed(1)} MB · ${new Date(f.modified*1000).toLocaleString()}</small></button>`).join(''):'<p>No telemetry files found in the default iRacing folders. Use the file picker above.</p>';$('local-files').querySelectorAll('[data-token]').forEach(b=>b.onclick=()=>doImport(()=>post('/api/import-local',{token:b.dataset.token,attach:state.attachId||undefined}),b.textContent));}catch(error){$('import-progress').textContent=error.message;}}
async function openImport(){state.attachId=null;$('import-dialog').showModal();$('import-progress').textContent='';await refreshFiles();}
async function doImport(action,label){if(state.importBusy)return;const comparisonImport=!!window.LapComparison?.importTarget;state.importBusy=true;$('import-progress').textContent='Reading telemetry and calculating laps… Large logs can take a little longer.';$('upload-file').disabled=true;$('local-files').querySelectorAll('button').forEach(b=>b.disabled=true);try{const result=await action();await refreshRuns();if(window.LapComparison?.importTarget){await window.LapComparison.imported(result.id);}else{await loadRun(result.id);activateWorksheet('telemetry');}$('import-dialog').close();notice(state.attachId?'Original recording attached; full-resolution evidence is available.':comparisonImport?'Recording ready in Lap comparison. Select a lap in each slot.':'Run imported. Confirm the setup snapshot and save your engineering brief.');state.attachId=null;}catch(error){$('import-progress').textContent=error.message;}finally{state.importBusy=false;$('upload-file').disabled=false;$('upload-file').value='';$('local-files').querySelectorAll('button').forEach(b=>b.disabled=false);}}

$('import-button').onclick=openImport;$('empty-import').onclick=openImport;$('refresh-files').onclick=refreshFiles;$('refresh-runs').onclick=()=>refreshRuns().catch(e=>notice(e.message,true));
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('upload-file').onchange=()=>{const file=$('upload-file').files[0];if(file)doImport(()=>api(`/api/upload?name=${encodeURIComponent(file.name)}${state.attachId?'&attach='+state.attachId:''}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file}),file.name);};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>activateWorksheet(b.dataset.tab));
$('lap-select').onchange=()=>{state.lap=Number($('lap-select').value);renderTelemetry();};
$('reference-select').onchange=()=>{state.reference=$('reference-select').value===''?null:Number($('reference-select').value);renderTelemetry();};
$('channel-select').onchange=renderTelemetry;$('reset-zoom').onclick=()=>{state.zoom=[0,100];renderTelemetry();};
$('show-flagged').onchange=renderLapTable;
$('baseline-select').onchange=updateComparison;
$('notes-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await saveNotes();notice('Engineering brief saved. Findings updated.');}catch(error){notice(error.message,true);$('save-status').textContent='Not saved';}finally{button.disabled=false;}};
$('notes-form').addEventListener('input',()=>$('save-status').textContent='Unsaved changes');
$('save-conditions').onclick=async()=>{try{await saveNotes({manual_conditions:$('manual-conditions').value});notice('Condition notes saved.');}catch(e){notice(e.message,true);}};
$('save-verdict').onclick=async()=>{try{await saveNotes({verdict:$('verdict').value,verdict_notes:$('verdict-notes').value});notice('Experiment verdict saved.');}catch(e){notice(e.message,true);}};
$('export-button').onclick=()=>window.open(`/api/export?id=${state.run.id}`,'_blank','noopener');
$('library-file').onchange=async()=>{const file=$('library-file').files[0];if(file){if(file.size>500000){notice('Source text must be under 500 KB.',true);return;}$('library-text').value=await file.text();if(!$('library-title').value)$('library-title').value=file.name;}};
$('library-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await post('/api/library',{title:$('library-title').value,source:$('library-source').value,text:$('library-text').value});event.target.reset();await loadLibrary();notice('Private source added. Connected AI can retrieve matching excerpts.');}catch(error){notice(error.message,true);}finally{button.disabled=false;}};
$('connect-button').onclick=async()=>{$('ai-provider').value=state.status.provider||'chatgpt';$('api-model').value=state.status.api_model||'gpt-4.1-mini';providerOptions();$('ai-dialog').showModal();try{await refreshAIStatus();}catch(error){$('codex-auth-status').textContent=error.message;}};
$('ai-provider').onchange=providerOptions;
$('chatgpt-refresh').onclick=()=>refreshAIStatus().catch(e=>$('codex-auth-status').textContent=e.message);
$('chatgpt-login').onclick=async()=>{const popup=window.open('about:blank','_blank');if(popup)popup.opener=null;$('chatgpt-login').disabled=true;try{const result=await post('/api/codex/login',{});$('chatgpt-login-link').href=result.auth_url;$('chatgpt-login-link').hidden=false;if(popup)popup.location.replace(result.auth_url);await refreshAIStatus();if(state.loginTimer)clearInterval(state.loginTimer);state.loginTimer=setInterval(()=>refreshAIStatus().catch(e=>$('codex-auth-status').textContent=e.message),2500);}catch(error){if(popup)popup.close();$('codex-auth-status').textContent=error.message;$('chatgpt-login').disabled=false;}};
$('chatgpt-cancel').onclick=async()=>{try{await post('/api/codex/login-cancel',{});if(state.loginTimer){clearInterval(state.loginTimer);state.loginTimer=null;}await refreshAIStatus();$('chatgpt-login-link').hidden=true;}catch(e){$('codex-auth-status').textContent=e.message;}};
$('ai-config-form').onsubmit=async event=>{event.preventDefault();try{const previousProject=state.status.project;state.status={...await post('/api/ai-config',{provider:$('ai-provider').value,key:$('api-key').value,model:$('api-model').value,codex_model:$('codex-model').value}),project:previousProject};$('api-key').value='';updateAIStatus();$('ai-dialog').close();notice(state.status.ai_connected?'AI connection selected. Your engineering questions will use this connection.':'Connection selected. Complete ChatGPT sign-in or configure an API key before asking the engineer.');}catch(error){notice(error.message,true);}};
$('question-form').onsubmit=submitInvestigation;
async function boot(){
 const statusTask=api('/api/status').then(status=>{state.status=status;updateAIStatus();if(state.run)renderProject();}).catch(()=>{$('ai-status').textContent='AI status unavailable';});
 try{await refreshRuns();const last=localStorage.getItem('stint.lastRun');if(last&&state.runs.some(r=>r.id===last))await loadRun(last);else if(state.runs.length)await loadRun(state.runs[0].id);if(!state.run){$('empty').hidden=true;$('workspace').hidden=false;clearRunWorkspace();}if(location.hash==='#live')activateWorksheet('live');}
 catch(error){$('empty').hidden=true;$('workspace').hidden=false;activateWorksheet('live');notice(error.message,true);}
 await statusTask;
}

initLapPickers();
boot().then(recoverInvestigation);
let editingProfile=null,editingRun=null,editorCorner='T1',mapCoordinates=[],mapDrag=null,activeJob=null,pollTimer=null;
function safeSource(url){try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch{return null;}}
function profileForEditing(){
  if(editingRun!==state.run.id){editingRun=state.run.id;editingProfile=structuredClone(state.run.corner_map?.profile||{track_id:String(state.run.metadata.track_id),configuration:state.run.metadata.configuration,validated:false,direction:'recorded travel direction',start_finish_distance_pct:0,source:{title:'',url:''},corners:[],geometry:state.run.corner_map?.outline?.points||[]});editorCorner=editingProfile.corners[0]?.label||'T1';}
  return editingProfile;
}
// Shared circuit orientation and fit for Telemetry and Lap comparison.
function trackMapProjection(points,fitPoints=points){
  const lat=points.reduce((sum,p)=>sum+p.lat,0)/points.length,lon=points.reduce((sum,p)=>sum+p.lon,0)/points.length;
  const raw=p=>({x:-(p.lat-lat)*111320,y:-(p.lon-lon)*111320*Math.cos(lat*Math.PI/180)});
  let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
  for(const p of fitPoints){const q=raw(p);minX=Math.min(minX,q.x);maxX=Math.max(maxX,q.x);minY=Math.min(minY,q.y);maxY=Math.max(maxY,q.y);}
  const scale=Math.min(500/(maxX-minX||1),235/(maxY-minY||1));
  return p=>{const q=raw(p);return {...p,x:30+(q.x-minX)*scale,y:40+(q.y-minY)*scale};};
}
function mapPoint(distance){return mapCoordinates.reduce((best,p)=>Math.abs(p.distance-distance)<Math.abs(best.distance-distance)?p:best,mapCoordinates[0]);}
function renderCornerMap(){
  const r=state.run,m=r.corner_map||{},profile=profileForEditing();
  $('recording-status').textContent=r.recording?`Original recording retained · ${r.recording.sample_count.toLocaleString()} samples · ${r.recording.tick_rate} Hz · SHA-256 ${r.recording.sha256.slice(0,12)}…`:'Summary-only run · attach the matching original .ibt for full-resolution AI evidence.';
  $('attach-recording').hidden=!!r.recording;$('map-status').textContent=m.message||'Corner map unavailable.';
  const points=m.outline?.points||profile.geometry||[];
  if(points.length>1){
    mapCoordinates=points.map(trackMapProjection(points));
    const controls=$('map-editor').open?profile.corners:((m.status==='validated')?m.profile.corners:[]);
    let html=`<svg id="track-map-svg" viewBox="0 0 560 320" role="img" aria-label="Track map with numbered corners"><title>Recorded driving path; corner reference points</title><polyline class="map-path" points="${mapCoordinates.map(p=>`${p.x},${p.y}`).join(' ')}"/><text x="20" y="18">N ← · travel direction shown by increasing lap distance</text>`;
    const sf=mapPoint(0);html+=`<rect x="${sf.x-3}" y="${sf.y-7}" width="6" height="14" class="map-finish"/><text x="${sf.x+7}" y="${sf.y+17}">S/F</text>`;
    controls.forEach(c=>{const p=mapPoint(c.reference);html+=`<g data-map-corner="${esc(c.label)}" class="map-corner ${state.zoom[0]===c.start&&state.zoom[1]===c.end?'active':''}" tabindex="0" role="button" aria-label="Select ${esc(c.label)}"><circle cx="${p.x}" cy="${p.y}" r="5"/><text x="${p.x+8}" y="${p.y-8}">${esc(c.label)}</text></g>`;});
    if($('map-editor').open){const c=profile.corners.find(c=>c.label===editorCorner);if(c)for(const [k,color] of [['start','#00ff00'],['reference','#0080ff'],['end','#ff8000']]){const p=mapPoint(c[k]);html+=`<circle data-map-handle="${k}" cx="${p.x}" cy="${p.y}" r="8" fill="${color}" stroke="white" stroke-width="2"/><text x="${p.x+10}" y="${p.y+16}">${k}</text>`;}}
    html+='<circle id="map-position" pointer-events="none" r="6" fill="#ff0000" stroke="white" stroke-width="2" visibility="hidden"/></svg>';
    $('track-map').innerHTML=html;
    $('track-map').querySelectorAll('[data-map-corner]').forEach(el=>{const select=()=>{editorCorner=el.dataset.mapCorner;if($('map-editor').open){renderCornerMap();}else{const c=r.zones.find(z=>z.label===editorCorner);if(c){$('question-corners').value=c.label;zoomZone(c);}}};el.onclick=select;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select();}};});
    $('track-map').querySelectorAll('[data-map-handle]').forEach(el=>el.onpointerdown=e=>{e.preventDefault();mapDrag=el.dataset.mapHandle;});
  }else{$('track-map').innerHTML='<p class="quiet">No usable recorded position path. Corner distances can still be edited below.</p>';mapCoordinates=[];}
  $('edit-corner').innerHTML=profile.corners.map(c=>`<option>${esc(c.label)}</option>`).join('');$('edit-corner').value=editorCorner;
  const selected=profile.corners.find(c=>c.label===editorCorner);
  $('corner-editor-fields').innerHTML=selected?['start','reference','end'].map(k=>`<label>${k} · % lap distance<input data-corner-field="${k}" type="number" min="0" max="100" step="0.01" value="${selected[k]}"></label>`).join(''):'';
  $('corner-editor-fields').querySelectorAll('input').forEach(el=>el.onchange=()=>{selected[el.dataset.cornerField]=Number(el.value);renderCornerMap();});
  $('map-source-title').value=profile.source?.title||'';$('map-source-url').value=profile.source?.url||'';$('map-confirmed').checked=!!profile.validated;
  const url=safeSource(profile.source?.url);$('map-source-link').hidden=!url;if(url)$('map-source-link').href=url;
  const existing=[...$('question-corners').selectedOptions].map(o=>o.value);
  $('question-corners').innerHTML=(m.status==='validated'?m.profile.corners:[]).map(c=>`<option value="${esc(c.label)}" ${existing.includes(c.label)?'selected':''}>${esc(c.label)}</option>`).join('');
  updateScopeHint();
}
function highlightMapPosition(distance){const marker=$('map-position');if(!marker||!mapCoordinates.length)return;const p=mapPoint(distance);marker.setAttribute('cx',p.x);marker.setAttribute('cy',p.y);marker.setAttribute('visibility','visible');const labels=state.run.zones.filter(z=>distance>=z.start&&distance<=z.end).map(z=>z.label);$('map-status').textContent=`${distance.toFixed(2)}% lap distance · ${labels.join(' / ')||'between mapped corners'}`;document.querySelectorAll('[data-map-corner]').forEach(el=>el.classList.toggle('hovered',labels.includes(el.dataset.mapCorner)));}
window.addEventListener('pointermove',event=>{if(!mapDrag||!mapCoordinates.length)return;const svg=$('track-map-svg'),pointer=svg.createSVGPoint();pointer.x=event.clientX;pointer.y=event.clientY;const local=pointer.matrixTransform(svg.getScreenCTM().inverse()),x=local.x,y=local.y;const p=mapCoordinates.reduce((best,p)=>Math.hypot(p.x-x,p.y-y)<Math.hypot(best.x-x,best.y-y)?p:best,mapCoordinates[0]);const c=editingProfile.corners.find(c=>c.label===editorCorner);if(!c)return;let value=p.distance;if(mapDrag==='start')value=Math.min(value,c.reference);if(mapDrag==='end')value=Math.max(value,c.reference);if(mapDrag==='reference')value=Math.max(c.start,Math.min(c.end,value));c[mapDrag]=Number(value.toFixed(3));renderCornerMap();});
window.addEventListener('pointerup',()=>mapDrag=null);
$('map-editor').ontoggle=()=>{if(state.run)renderCornerMap();};$('edit-corner').onchange=()=>{editorCorner=$('edit-corner').value;renderCornerMap();};
$('map-source-title').oninput=()=>{profileForEditing().source.title=$('map-source-title').value;};$('map-source-url').oninput=()=>{profileForEditing().source.url=$('map-source-url').value;};$('map-confirmed').onchange=()=>profileForEditing().validated=$('map-confirmed').checked;
$('add-map-corner').onclick=()=>{const p=profileForEditing(),previous=p.corners.at(-1)?.reference||0,ref=Math.min(99,previous+5);if(ref<=previous){notice('No room after the final corner. Adjust its reference first.',true);return;}p.corners.push({label:`T${p.corners.length+1}`,start:Math.max(0,ref-2),reference:ref,end:Math.min(100,ref+2)});editorCorner=p.corners.at(-1).label;renderCornerMap();};
async function saveCornerProfile(reset=false){try{await post('/api/corner-profile',{id:state.run.id,profile:profileForEditing(),reset});editingRun=null;state.run=await api(`/api/run?id=${state.run.id}`);renderTelemetry();notice('Corner profile revision saved. Previous answers retain their original mapping.');}catch(e){notice(e.message,true);}}
$('save-corner-map').onclick=()=>saveCornerProfile();$('reset-corner-map').onclick=()=>saveCornerProfile(true);
$('attach-recording').onclick=async()=>{state.attachId=state.run.id;$('import-dialog').showModal();$('import-progress').textContent='Choose the original recording for this run. Notes and conversation will be preserved after verification.';await refreshFiles();};
function updateScopeHint(){const scope=$('question-scope').value;$('question-corners-label').hidden=scope!=='corners';$('scope-hint').textContent=scope==='region'?`Focus: lap ${state.run?.laps.find(l=>l.id===state.lap)?.number??'—'}, ${number(state.zoom[0])}–${number(state.zoom[1])}% distance.`:scope==='corners'?'Select numbered corners. Explicit corner references in your question must agree with this selection.':'The engineer chooses relevant sections. Corner references in your question take priority.';}
$('question-scope').onchange=updateScopeHint;
function selectedScope(){const kind=$('question-scope').value;return {kind,lap_id:state.lap,reference_lap_id:state.reference,...(kind==='corners'?{corners:[...$('question-corners').selectedOptions].map(o=>o.value)}:{}),...(kind==='region'?{distance_range:state.zoom}:{} )};}
async function submitInvestigation(event){event.preventDefault();const question=$('question').value.trim();if(!question)return;if(activeJob)return;const scope=selectedScope();try{validateQuestionScope(question,scope);}catch(e){notice(e.message,true);return;}if(scope.kind==='corners'&&!scope.corners.length){notice('Select at least one numbered corner.',true);return;}$('ask-button').disabled=true;try{await saveNotes();const job=await post('/api/ai-jobs',{id:state.run.id,baseline:state.baseline,question,scope});activeJob={id:job.id,run_id:job.run_id};localStorage.setItem('stint.activeJob',JSON.stringify(activeJob));$('ai-progress').hidden=false;$('cancel-investigation').hidden=false;notice('Investigation started. Original samples are inspected on request.');pollInvestigation();}catch(error){notice(error.message,true);$('ask-button').disabled=!state.status.ai_connected;}}
async function pollInvestigation(){if(!activeJob)return;try{const job=await api(`/api/ai-jobs/${activeJob.id}`);$('ai-progress').hidden=false;$('ai-progress').textContent=job.progress;$('cancel-investigation').hidden=!['pending','running','cancelling'].includes(job.status);$('ask-button').disabled=true;if(['completed','failed','cancelled','interrupted'].includes(job.status)){const runId=activeJob.run_id;activeJob=null;localStorage.removeItem('stint.activeJob');$('cancel-investigation').hidden=true;$('ask-button').disabled=!state.status.ai_connected;if(job.status==='completed'){if(state.run?.id===runId){state.run=await api(`/api/run?id=${runId}`);renderHistory();$('question').value='';}notice('Engineering response saved with inspection coverage.');}else{notice(job.error||job.progress,true);$('ai-progress').innerHTML=`${esc(job.progress)}${renderEvidenceManifest(job.evidence_manifest)}`;bindEvidenceLinks();}return;}pollTimer=setTimeout(pollInvestigation,1500);}catch(error){$('ai-progress').textContent=error.message+' Retrying status…';pollTimer=setTimeout(pollInvestigation,3000);}}
$('cancel-investigation').onclick=async()=>{if(activeJob){try{await post(`/api/ai-jobs/${activeJob.id}/cancel`,{});$('ai-progress').textContent='Stopping investigation…';}catch(e){notice(e.message,true);}}};
async function recoverInvestigation(){try{const stored=localStorage.getItem('stint.activeJob');if(stored){activeJob=JSON.parse(stored);pollInvestigation();}}catch{localStorage.removeItem('stint.activeJob');}}
function renderEvidenceManifest(manifest){if(!manifest)return '<p class="quiet">Saved before detailed evidence tracking was added.</p>';const sections=manifest.sections||[],measurements=manifest.supplied_measurements||[],calculated=manifest.calculated||[];const button=(s,label)=>`<button type="button" class="text-button evidence-link" data-evidence-run="${esc(s.run_id)}" data-evidence-lap="${s.lap_id}" data-evidence-range="${esc(JSON.stringify(s.distance_range))}" data-evidence-profile="${esc(s.profile_revision||manifest.profiles?.[s.run_id]?.revision||'unmapped')}">${esc(label)} · View supporting telemetry</button>`;return `<details class="evidence-coverage"><summary>Inspection coverage · ${sections.length} original-sample pages supplied</summary><p>Calculated across ${calculated.reduce((n,c)=>n+c.lap_ids.length,0)} eligible laps; ${measurements.length} corner measurements supplied to AI. ${sections.reduce((n,s)=>n+s.sample_count,0)} sample rows supplied across these pages (ranges can overlap).</p>${sections.length?sections.map(s=>`<p>${button(s,`${s.evidence_id} · ${s.corner||'range'} · ${s.sample_count} rows`)}<small>${s.distance_range.map(x=>Number(x).toFixed(3)).join('–')}% · native channels: ${esc(s.channels.join(', '))} · ${s.complete?'requested range complete':'partial page; see subsequent coverage'} · profile ${esc(s.profile_revision||'unmapped')}</small></p>`).join(''):'<p>No original traces were supplied to the AI. This response used summaries or calculated measurements.</p>'}${measurements.length?`<details><summary>Calculated evidence supplied</summary>${measurements.map(m=>`<p>${button(m,`${m.evidence_id} · ${m.corner}`)}</p>`).join('')}</details>`:''}${(manifest.limits||[]).map(l=>`<p class="source-caveat">${esc(l)}</p>`).join('')}<p class="quiet">Numbered ranges reflect the saved profile revision. Graphs use reduced display traces; original samples supplied to AI retain their native precision.</p></details>`;}
function bindEvidenceLinks(){document.querySelectorAll('.evidence-link').forEach(button=>button.onclick=async()=>{try{if(state.run.id!==button.dataset.evidenceRun)await loadRun(button.dataset.evidenceRun);state.lap=Number(button.dataset.evidenceLap);$('lap-select').value=state.lap;const range=JSON.parse(button.dataset.evidenceRange);zoomZone({start:range[0],end:range[1]});highlightMapPosition((range[0]+range[1])/2);const saved=button.dataset.evidenceProfile,current=state.run.corner_map?.profile?.revision;if(saved&&saved!==current)notice(`Showing the saved distance range from profile ${saved}. The current map uses ${current||'unmapped'}.`);}catch(e){notice(e.message,true);}});}

function validateQuestionScope(question,scope){
  const available=state.run.corner_map?.status==='validated'?state.run.corner_map.profile.corners:[],explicit=new Set();
  for(const match of question.matchAll(/\b(?:T|turns?\s*)(\d{1,2})(?:\s*[–—-]\s*(?:T|turns?\s*)?(\d{1,2}))?\b/gi)){
    const first=Number(match[1]),last=Number(match[2]||first);if(first<1||last<first||last>99)throw new Error('Invalid corner range in your question.');
    for(let i=first;i<=last;i++)explicit.add(`T${i}`);
  }
  for(const label of explicit)if(!available.some(c=>c.label===label))throw new Error(`No validated mapping for ${label}. Confirm the corner map before asking about numbered turns.`);
  if(explicit.size&&scope.kind==='corners'&&(explicit.size!==scope.corners.length||scope.corners.some(c=>!explicit.has(c))))throw new Error('The corners in your question differ from your selection. Select those corners or choose Whole run.');
  if(explicit.size&&scope.kind==='region')for(const c of available.filter(c=>explicit.has(c.label)))if(c.start<scope.distance_range[0]||c.end>scope.distance_range[1])throw new Error('The chart range does not cover the corners named in your question. Choose those corners or Whole run.');
}


// Desktop worksheet shell. Persist only presentation preferences in local storage.
let worksheet='telemetry',browserTab='sessions',layoutPrefs={browser:220,map:240,browserCollapsed:false,mapCollapsed:false},shellResizeTimer;
try{layoutPrefs={...layoutPrefs,...JSON.parse(localStorage.getItem('stint.layout')||'{}')};worksheet=localStorage.getItem('stint.worksheet')||'telemetry';}catch{}
function persistLayout(){localStorage.setItem('stint.layout',JSON.stringify(layoutPrefs));}
function applyLayout(){
 document.body.classList.toggle('lap-comparison-mode',worksheet==='lap-comparison');
 document.body.classList.toggle('live-mode',worksheet==='live');
 const mobile=innerWidth<=700;
 const available=Math.max(180,innerWidth-450);
 const bw=Math.max(160,Math.min(Number(layoutPrefs.browser)||220,Math.min(360,available*.5)));
 const mw=Math.max(180,Math.min(Number(layoutPrefs.map)||240,Math.min(400,available*.5)));
 document.documentElement.style.setProperty('--browser-width',bw+'px');document.documentElement.style.setProperty('--map-width',mw+'px');
 document.body.classList.toggle('browser-collapsed',mobile?!layoutPrefs.mobileBrowser:!!layoutPrefs.browserCollapsed);
 document.body.classList.toggle('map-collapsed',mobile?!layoutPrefs.mobileMap:!!layoutPrefs.mapCollapsed);
 $('toggle-browser').setAttribute('aria-expanded',String(!document.body.classList.contains('browser-collapsed')));
 $('toggle-map').setAttribute('aria-expanded',String(!document.body.classList.contains('map-collapsed')));
 $('toggle-map').disabled=worksheet!=='telemetry';
 for(const [id,value] of [['browser-splitter',bw],['map-splitter',mw]]){$(id).setAttribute('aria-valuenow',Math.round(value));$(id).setAttribute('aria-valuemin',id==='browser-splitter'?160:180);$(id).setAttribute('aria-valuemax',id==='browser-splitter'?360:400);}
}
function activateWorksheet(name){
 if(!state.run&&state.runs.length&&!['live','library','lap-comparison'].includes(name)){loadRun(state.runs[0].id).then(()=>{if(state.run)activateWorksheet(name);});return;}
 if(!document.querySelector(`[data-tab="${name}"]`))name='telemetry';worksheet=name;
 document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
 document.querySelectorAll('.tab-panel').forEach(p=>p.hidden=p.id!==`tab-${name}`);
 localStorage.setItem('stint.worksheet',name);applyLayout();if(state.run){if(name==='telemetry')renderTelemetry();else if(name==='stint')renderStint();else if(name==='conditions')renderConditions();}scheduleCharts();
 if(window.LivePitwall)window.LivePitwall.activate(name==='live');
 window.LapComparison?.activate(name==='lap-comparison');
 if(!state.run&&name!=='live'&&name!=='library'&&name!=='lap-comparison')notice('Import a completed .ibt recording to use this worksheet.');else if(name==='live')notice('');
}
function scheduleCharts(){clearTimeout(shellResizeTimer);shellResizeTimer=setTimeout(()=>{if(worksheet==='lap-comparison'){window.LapComparison?.resize();return;}if(!state.run)return;if(worksheet==='telemetry')renderTelemetry();else if(worksheet==='stint')renderStint();else if(worksheet==='conditions')renderConditions();else if(worksheet==='compare'&&state.baseline)updateComparison();},80);}
function updateWorkspaceStatus(){
 const r=state.run;if(!r)return;const l=r.laps.find(l=>l.id===state.lap);
 $('session-caption').textContent=`${r.metadata.track} · ${runLabel(r)}`;
 $('session-caption').title=`${r.metadata.car} · ${r.filename}`;
 $('workspace-status').textContent=`${runLabel(r)} | Lap ${l?.number??'—'} · ${lapTime(l?.duration)} | ${state.zoom.map(x=>number(x)).join('–')}% | ${r.recording?'Original recording':'See recording info'}`;
 $('job-status').textContent=typeof activeJob!=='undefined'&&activeJob?$('ai-progress').textContent.split('\n')[0]:'AI idle';
}
function renderBrowserLaps(){
 if(!state.run)return;const r=state.run;
 $('browser-laps').innerHTML=r.laps.map(l=>{const eligible=l.eligible&&!r.notes.excluded_laps.includes(l.id);return `<button class="browser-lap ${state.lap===l.id?'active':''} ${eligible?'':'excluded'}" data-browser-lap="${l.id}" title="${esc(l.reasons.join('; ')||(eligible?'Eligible · traffic / tires need review':'Manually excluded'))}">${lapNumberMarkup(l)}${lapTimeMarkup(l)}<span>${eligible?'✓':'×'}</span></button>`;}).join('');
 $('browser-laps').querySelectorAll('[data-browser-lap]').forEach(b=>b.onclick=()=>{state.lap=Number(b.dataset.browserLap);$('lap-select').value=state.lap;renderTelemetry();if(innerWidth<=700){layoutPrefs.mobileBrowser=false;applyLayout();}});
}
function renderChannelBrowser(){
 if(!state.run)return;const lap=state.run.laps.find(l=>l.id===state.lap),traces=lap?.traces||{};
 const choices=[['Speed','Speed','km/h',colors.selected],['Throttle','Throttle','%',colors.throttle],['Brake','Brake','%',colors.brake],['SteeringWheelAngle','Steering','deg',colors.additional],['YawRate','Yaw rate','deg/s',colors.additional],['LatAccel','Lateral acceleration','m/s²',colors.additional],['LongAccel','Longitudinal acceleration','m/s²',colors.additional],['FuelLevel','Fuel','L',colors.additional],['rideHeight','Ride heights','mm',colors.lf]];
 $('channel-browser').innerHTML=choices.map(([key,label,unit,color])=>{const available=key==='rideHeight'?['LF','RF','LR','RR'].some(x=>traces[x+'rideHeight']):!!traces[key];return `<button class="channel-row ${$('channel-select').value===key?'active':''}" data-display-channel="${key}" ${available?'':'disabled'} title="${available?'Show '+label:'Unavailable in selected lap'}"><span class="channel-swatch" style="background:${color}"></span><span>${label}</span><small>${unit}</small></button>`;}).join('');
 $('channel-browser').querySelectorAll('[data-display-channel]').forEach(b=>b.onclick=()=>{const key=b.dataset.displayChannel;if(['Speed','Brake','Throttle'].includes(key)){activateWorksheet('telemetry');$('charts').children[key==='Speed'?0:key==='Throttle'?1:2]?.focus({preventScroll:true});}else{$('channel-select').value=key;activateWorksheet('telemetry');renderTelemetry();}if(innerWidth<=700){layoutPrefs.mobileBrowser=false;applyLayout();}});
}
for(const b of document.querySelectorAll('[data-browser-tab]'))b.onclick=()=>{browserTab=b.dataset.browserTab;document.querySelectorAll('[data-browser-tab]').forEach(x=>x.classList.toggle('active',x===b));$('browser-sessions').hidden=browserTab!=='sessions';$('browser-channels').hidden=browserTab!=='channels';};
$('toggle-browser').onclick=()=>{if(innerWidth<=700)layoutPrefs.mobileBrowser=!layoutPrefs.mobileBrowser;else layoutPrefs.browserCollapsed=!layoutPrefs.browserCollapsed;applyLayout();persistLayout();scheduleCharts();};
$('toggle-map').onclick=()=>{if(innerWidth<=700)layoutPrefs.mobileMap=!layoutPrefs.mobileMap;else layoutPrefs.mapCollapsed=!layoutPrefs.mapCollapsed;applyLayout();persistLayout();scheduleCharts();};
$('info-button').onclick=()=>$('info-dialog').showModal();
$('edit-map-button').onclick=()=>{$('map-editor-host').append($('corner-map-panel'));$('map-editor').hidden=false;$('map-editor').open=true;$('map-dialog').showModal();renderCornerMap();};
$('map-dialog').addEventListener('close',()=>{$('map-editor').open=false;$('map-editor').hidden=true;$('map-dock').insertBefore($('corner-map-panel'),$('zones'));if(state.run)renderCornerMap();});
for(const [id,key,direction] of [['browser-splitter','browser',1],['map-splitter','map',-1]]){
 const el=$(id);let drag=null;
 el.onpointerdown=e=>{drag={x:e.clientX,width:key==='browser'?$('data-browser').getBoundingClientRect().width:$('map-dock').getBoundingClientRect().width};el.setPointerCapture(e.pointerId);e.preventDefault();};
 el.onpointermove=e=>{if(!drag)return;layoutPrefs[key]=Math.max(key==='browser'?160:180,Math.min(key==='browser'?360:400,drag.width+(e.clientX-drag.x)*direction));applyLayout();scheduleCharts();};
 el.onpointerup=()=>{drag=null;persistLayout();scheduleCharts();};el.onpointercancel=()=>drag=null;
 el.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();layoutPrefs[key]=e.key==='Home'?(key==='browser'?160:180):e.key==='End'?(key==='browser'?360:400):Number(layoutPrefs[key])+(e.key==='ArrowRight'?10:-10)*direction;layoutPrefs[key]=Math.max(key==='browser'?160:180,Math.min(key==='browser'?360:400,layoutPrefs[key]));applyLayout();persistLayout();scheduleCharts();};
}
window.addEventListener('resize',()=>{applyLayout();scheduleCharts();});
new ResizeObserver(scheduleCharts).observe($('worksheet-host'));
new MutationObserver(updateWorkspaceStatus).observe($('ai-progress'),{childList:true,subtree:true,characterData:true});
activateWorksheet(worksheet);


// Original-sample statistics are independent of cursor interpolation and display traces.
const plotStatsCache=new Map();
function requestPlotStats(body,recording){
 const key=JSON.stringify([body,recording??state.run?.recording?.sha256,body.run_id===state.run?.id?state.run?.notes.excluded_laps:[]]);
 const cached=plotStatsCache.get(key);if(cached&&Date.now()-cached.time<10000)return cached.promise;
 const promise=post('/api/plot-stats',body);plotStatsCache.set(key,{promise,time:Date.now()});
 if(plotStatsCache.size>24)plotStatsCache.delete(plotStatsCache.keys().next().value);
 promise.catch(()=>plotStatsCache.delete(key));return promise;
}
function populatePlotStats(host,seriesList,options){
 const token=Symbol();host.statsToken=token;const box=host.querySelector('.plot-statistics');
 const specs=seriesList.map(s=>({series:s,run:s.statsRunId||state.run?.id}));
 box.innerHTML=specs.map(({series:s})=>`<div class="stat-readout" data-trace="${esc(s.label)}"><span class="stat-series">${esc(s.label)}</span> <span class="stat-pending">…</span></div>`).join('');
 const groups=new Map();for(const spec of specs){if(!groups.has(spec.run))groups.set(spec.run,[]);groups.get(spec.run).push(spec);}
 const jobs=[...groups].map(async([run,items])=>{
  const scope=options.statsScope||'laps';
  const body={run_id:run,scope,channels:[...new Set(items.map(x=>x.series.statChannel))],...(scope==='laps'?{lap_ids:[...new Set(items.map(x=>x.series.statLap))],distance_range:options.range||[0,100]}:{})};
  try{const result=options.statsPromises?.[run]?await options.statsPromises[run]:options.statsPromise&&run===state.run?.id?await options.statsPromise:await requestPlotStats(body,items[0].series.statsRecording);return items.map(spec=>({...spec,stats:result.rows.find(r=>r.channel===spec.series.statChannel&&r.lap_id===spec.series.statLap)}));}
  catch(error){return items.map(spec=>({...spec,stats:{available:false,reason:error.message}}));}
 });
 Promise.all(jobs).then(groups=>{
  if(!host.isConnected||host.statsToken!==token)return;const result=groups.flat();
  box.innerHTML=specs.map(spec=>{
   const row=result.find(x=>x.series===spec.series)?.stats,s=spec.series;
   const label=`<span class="stat-series">${esc(s.label)}</span>`;
   if(!row?.available)return `<div class="stat-readout" title="${esc(row?.reason||'No original statistics available')}">${label} <span class="quiet">unavailable</span></div>`;
   const digits=row.unit==='s'?3:row.unit==='category'?0:1;
   const title=`${row.valid_sample_count} valid samples · ${row.invalid_sample_count} invalid · ${row.provenance?.method||''}${row.provenance?.interpolation?' · '+row.provenance.interpolation:''} · ${row.unit}${row.eligible===false?' · flagged/excluded lap':''}`;
   return `<div class="stat-readout" data-channel="${esc(row.channel)}" data-lap="${row.lap_id??''}" data-valid-count="${row.valid_sample_count}" title="${esc(title)}">${label} <span class="stat-min" aria-label="Minimum"><i>▼</i> ${number(row.min,digits)}</span> <span class="stat-max" aria-label="Maximum"><i>▲</i> ${number(row.max,digits)}</span> <span class="stat-mean" aria-label="Average"><i>◆</i> ${number(row.mean,digits)}</span></div>`;
  }).join('');
 });
}
