'use strict';
(() => {
 const el=id=>document.getElementById(id), remote=!!window.REMOTE_PITWALL;
 const corners=['lf','rf','lr','rr'], labels={lf:'Left front',rf:'Right front',lr:'Left rear',rr:'Right rear'};
 const host=el('live-pit-controls');
 let snapshot=null,binding='',pressureBaseline={},draftRevision=null,pitDirty=false,marginDirty=false,lastPacket=0,lastCommand=null,awaitingConfirmation=false,lastSubmitError='',settings=null,settingsDirty=false;
 host.innerHTML='<p id="live-pit-authority" role="status"></p><div id="live-pit-actual"></div><form id="live-pit-form"><fieldset class="pit-fieldset"><legend>Fuel · next pit stop</legend><label class="pit-check"><input id="live-pit-auto" type="checkbox">Auto Fuel</label><p id="live-pit-auto-note" class="quiet"></p><label>Margin (laps) · draft<input id="live-pit-margin" type="number" min="-10" max="10" step="0.1" value="1"></label><p class="quiet">Edit the margin to send it. Check its value in iRacing after applying.</p><label>Add fuel (whole L)<input id="live-pit-fuel" type="number" min="0" step="1" inputmode="numeric" required></label><label class="pit-check"><input id="live-pit-begin" type="checkbox">Begin fueling</label><p id="live-pit-fuel-info" class="quiet"></p><label class="pit-check"><input id="live-pit-tear-off" type="checkbox">Windshield tear-off</label><label class="pit-check"><input id="live-pit-fast-repair" type="checkbox">Fast repair <span id="live-pit-repairs-left" class="quiet"></span></label></fieldset><fieldset class="pit-fieldset"><legend>Tires · next pit stop</legend><div class="pit-tires">'+corners.map(c=>`<div><label class="pit-check"><input type="checkbox" id="live-pit-${c}">Replace ${c.toUpperCase()}</label><label>${c.toUpperCase()} pressure (kPa)<input id="live-pit-pressure-${c}" type="number" min="1" max="1000" step="1" inputmode="numeric" aria-describedby="live-pit-pressure-was-${c}" required><span id="live-pit-pressure-was-${c}" class="pit-pressure-was" hidden></span></label></div>`).join('')+'</div><label>Compound<select id="live-pit-compound"></select></label><p class="quiet">iRacing enforces car-specific pressure limits. Review the actual values if a request is clamped.</p></fieldset><p class="quiet">Confirmed selections send a private in-car message naming the person who applied them.</p><div class="live-actions"><button id="live-pit-load" type="button">Load next planned stop</button><button id="live-pit-sync" type="button">Use actual selections</button><button id="live-pit-apply" class="primary" type="submit">Apply to car</button></div><p id="live-pit-draft-note" class="quiet"></p><p id="live-pit-result" role="status"></p></form>';
 const canEdit=()=>!remote||window.RemoteIdentity?.role==='engineer';
 const actualText=p=>`${p.actual.fuel_enabled===null?'Fuel unavailable':p.actual.fuel_enabled?`Add ${number(p.actual.fuel_litres,2)} L`:'No refueling'}${p.actual.auto_fuel?' · Auto Fuel':''} · ${corners.some(c=>p.actual.tires[c]===null)?'Tires unavailable':corners.filter(c=>p.actual.tires[c]).map(c=>c.toUpperCase()).join(', ')||'No tires'} · ${corners.map(c=>`${c.toUpperCase()} ${number(p.actual.pressures_kpa?.[c],0)}`).join(' / ')} kPa · ${p.compounds?.find(c=>c.index===p.actual.tire_compound)?.name||'Compound unavailable'} · Tear-off ${p.actual.tear_off?'on':'off'} · Fast repair ${p.actual.fast_repair?'on':'off'}`;
 function syncDraft(){
  if(!snapshot?.pit_control)return;
  const p=snapshot.pit_control;
  el('live-pit-fuel').value=p.fuel_supported?Math.ceil(p.actual.fuel_litres):'';
  el('live-pit-begin').checked=!!p.actual.fuel_enabled;el('live-pit-auto').checked=!!p.actual.auto_fuel;
  el('live-pit-tear-off').checked=!!p.actual.tear_off;el('live-pit-fast-repair').checked=!!p.actual.fast_repair;
  for(const c of corners){el('live-pit-'+c).checked=!!p.actual.tires[c];el('live-pit-pressure-'+c).value=p.actual.pressures_kpa?.[c]==null?'':Math.round(p.actual.pressures_kpa[c]);}
  el('live-pit-compound').innerHTML=(p.compounds||[]).map(c=>`<option value="${c.index}">${esc(c.name)}</option>`).join('')||'<option value="">Unavailable</option>';
  el('live-pit-compound').value=p.actual.tire_compound??'';marginDirty=false;
  draftRevision=p.revision;pitDirty=false;awaitingConfirmation=false;el('live-pit-draft-note').textContent='Draft matches observed selections, rounded to whole liters. The margin is a draft value; unchanged margins are preserved.';
 }
 function render(){
  if(!snapshot?.pit_control)return;
  const p=snapshot.pit_control,editable=canEdit(),expired=Date.now()-lastPacket>2000;
  el('live-pit-authority').textContent=p.enabled?`Pit control enabled · ${p.engineer_name||p.engineer}`:'Pit control disarmed · enable it on the racing PC';
  if(remote&&!p.enabled)el('live-pit-authority').textContent+=` · Designated engineer: ${window.RemoteIdentity?.engineer_name||window.RemoteIdentity?.engineer||'None'}`;
  el('live-pit-authority').className=p.enabled?'connected':'quiet';
  el('live-pit-actual').textContent='Actual simulator selections: '+actualText(p);
  el('live-pit-fuel').disabled=!editable||!p.fuel_supported||el('live-pit-auto').checked;el('live-pit-fuel').max=p.capacity_litres??'';
  el('live-pit-begin').disabled=!editable||!p.fuel_supported||el('live-pit-auto').checked;
  el('live-pit-auto').disabled=!editable||!p.auto_fuel_supported;el('live-pit-margin').disabled=!editable||!p.auto_fuel_supported||!el('live-pit-auto').checked;
  el('live-pit-auto').title=p.auto_fuel_supported?'iRacing calculates fuel for the next stop':p.auto_fuel_note;el('live-pit-auto-note').textContent=p.auto_fuel_supported?'':p.auto_fuel_note;
  el('live-pit-tear-off').disabled=!editable||p.actual.tear_off==null;el('live-pit-fast-repair').disabled=!editable||p.actual.fast_repair==null;
  el('live-pit-repairs-left').textContent=p.actual.fast_repairs_available==null?'· remaining unavailable':`· ${p.actual.fast_repairs_available} remaining`;
  const burn=snapshot.strategy?.burn_litres,fuel=snapshot.player.FuelLevel;
  el('live-pit-fuel-info').textContent=`Remaining: ${number(fuel)} L · Estimated laps: ${number(fuel!=null&&burn>0?fuel/burn:null)}`;
  for(const c of corners){
   el('live-pit-'+c).disabled=!editable||p.actual.tires[c]===null;
   const input=el('live-pit-pressure-'+c),was=el('live-pit-pressure-was-'+c);
   input.disabled=!editable||!p.pressure_supported?.[c];
   // Compare next-stop pressures with the fitted tires, even after Apply confirms them.
   const fitted=snapshot.tires?.values?.[c.toUpperCase()+'coldPressure'],actual=p.actual.pressures_kpa?.[c];
   if(Number.isFinite(fitted)&&fitted>0)pressureBaseline[c]=Math.round(fitted);
   else if(pressureBaseline[c]==null&&Number.isFinite(actual)&&actual>0)pressureBaseline[c]=Math.round(actual);
   const original=pressureBaseline[c],value=input.valueAsNumber;
   const modified=!!p.pressure_supported?.[c]&&original!=null&&Number.isFinite(value)&&value>0&&Math.round(value)!==original;
   input.classList.toggle('pit-pressure-changed',modified);
   was.hidden=!modified;was.textContent=modified?`Was ${original} kPa`:'';
  }
  el('live-pit-compound').disabled=!editable||!p.compounds?.length;
  el('live-pit-load').disabled=!editable;el('live-pit-sync').disabled=!editable;
  const changed=p.revision!==draftRevision;
  el('live-pit-apply').disabled=!editable||!p.enabled||!!p.blocked_reason||!!p.pending||expired||changed||snapshot.status!=='connected';
  if(changed&&pitDirty)el('live-pit-draft-note').textContent='Simulator selections changed. Use actual selections or load the planned stop again before applying.';
  if(!editable)el('live-pit-authority').textContent+=' · Viewer access';
  const current=p.commands.find(c=>c.id===lastCommand)||p.commands.at(-1);
  if(lastSubmitError){el('live-pit-result').textContent=lastSubmitError;el('live-pit-result').className='error';}
  else if(current){const status={pending:'Pending',confirmed:'Confirmed',partial:'Partially applied',unconfirmed:'Unconfirmed',failed:'Failed'}[current.status];const req=current.requested,changedSince=current.status==='confirmed'&&(corners.some(c=>req.tires[c]!==p.actual.tires[c])||(!req.auto_fuel&&req.fuel_litres!=null&&(req.fuel_litres===0?p.actual.fuel_enabled!==false:p.actual.fuel_enabled!== (req.fuel_enabled??true)||Math.abs(req.fuel_litres-p.actual.fuel_litres)>.01))||['fuel_enabled','tear_off','fast_repair','tire_compound','auto_fuel'].some(k=>k in req&&req[k]!==p.actual[k])||Object.entries(req.pressures_kpa||{}).some(([c,v])=>Math.abs(v-p.actual.pressures_kpa[c])>.6));el('live-pit-result').textContent=`${status}: ${current.message}${changedSince?' Simulator selections have since changed; review the actual values.':''}${current.private_confirmation?' '+current.private_confirmation.message:''}`;el('live-pit-result').className=current.status==='confirmed'&&!changedSince&&!current.unverified_fields?.length?'connected':current.status==='pending'?'quiet':'error';}
  else if(p.blocked_reason||expired)el('live-pit-result').textContent=expired?'Connection stale; applying is suspended.':p.blocked_reason;
  const form=el('live-plan-form');
  if(form)form.querySelectorAll('input,select,button').forEach(input=>input.disabled=!editable&&input.id!=='live-reload-plan');
  el('live-scenarios')?.querySelectorAll('[data-copy],[data-use]').forEach(button=>{if(!editable)button.disabled=true;});
  if(!editable)el('live-plan-status').textContent='Viewer access · race plan is read-only';
 }
 addEventListener('live-snapshot',event=>{
  snapshot=event.detail;lastPacket=Date.now();
  const next=JSON.stringify([snapshot.collector_id,snapshot.session.id,snapshot.session.driver_id]);
  if(binding!==next){binding=next;pressureBaseline={};lastCommand=null;lastSubmitError='';syncDraft();}
  else if(awaitingConfirmation&&snapshot.pit_control.commands.some(c=>c.id===lastCommand&&c.status==='confirmed'))syncDraft();
  else if(awaitingConfirmation&&snapshot.pit_control.commands.some(c=>c.id===lastCommand&&c.status!=='pending'))awaitingConfirmation=false;
  else if(!pitDirty&&draftRevision!==snapshot.pit_control.revision)syncDraft();
  render();
 });
 window.LiveControls={render};
 snapshot=window.LivePitwall?.getSnapshot();
 if(snapshot){lastPacket=Date.now();binding=JSON.stringify([snapshot.collector_id,snapshot.session.id,snapshot.session.driver_id]);syncDraft();render();}
 for(const input of host.querySelectorAll('input,select'))input.addEventListener('input',()=>{if(input.id==='live-pit-margin')marginDirty=true;if(input.id==='live-pit-auto'&&input.checked){el('live-pit-begin').checked=true;marginDirty=true;}if(input.id==='live-pit-fuel')el('live-pit-begin').checked=Number(input.value)>0;const compoundChanged=input.id==='live-pit-compound'&&Number(input.value)!==snapshot.pit_control.actual.tire_compound;if(compoundChanged)for(const c of corners)el('live-pit-'+c).checked=true;pitDirty=true;awaitingConfirmation=false;el('live-pit-draft-note').textContent=compoundChanged?'Compound changed · all four tires selected for replacement.':'Draft selections · Apply to car to request them.';render();});
 el('live-pit-sync').onclick=()=>{lastSubmitError='';syncDraft();render();};
 el('live-pit-load').onclick=()=>{
  const progress=(snapshot?.player.LapCompleted??0)+(snapshot?.player.LapDistPct??0);
  const scenario=snapshot?.strategy?.scenarios?.find(s=>s.id===snapshot.plan.active_scenario);
  const stop=scenario?.timeline?.find(s=>s.lap>progress);
  if(!stop){el('live-pit-draft-note').textContent='Save an active feasible race scenario with a future stop first.';return;}
  syncDraft();const fuel=Math.ceil(stop.fuel_litres);
  if(snapshot.pit_control.fuel_supported){el('live-pit-fuel').value=fuel;el('live-pit-begin').checked=fuel>0;el('live-pit-auto').checked=false;}
  for(const c of corners)el('live-pit-'+c).checked=!!stop.tires;
  pitDirty=true;el('live-pit-draft-note').textContent=`After lap ${stop.lap}: add ${number(stop.fuel_litres,2)} L, rounded up to ${fuel} L. Review, then Apply to car.`;render();
 };
 el('live-pit-form').onsubmit=async event=>{
  event.preventDefault();if(el('live-pit-apply').disabled)return;
  const s=snapshot.session,p=snapshot.pit_control,body={id:crypto.randomUUID(),collector_id:snapshot.collector_id,session_id:s.id,driver_id:s.driver_id,player_car_idx:s.player_car_idx,sequence:snapshot.sequence,revision:draftRevision,fuel_litres:p.fuel_supported?Number(el('live-pit-fuel').value):null,tires:Object.fromEntries(corners.map(c=>[c,el('live-pit-'+c).checked]))};
  if(p.fuel_supported)body.fuel_enabled=el('live-pit-begin').checked;
  if(p.auto_fuel_supported){body.auto_fuel=el('live-pit-auto').checked;if(body.auto_fuel){body.fuel_litres=null;delete body.fuel_enabled;if(marginDirty)body.auto_fuel_margin_laps=Number(el('live-pit-margin').value);}}
  for(const [key,id] of [['tear_off','live-pit-tear-off'],['fast_repair','live-pit-fast-repair']])if(!el(id).disabled)body[key]=el(id).checked;
  body.pressures_kpa=Object.fromEntries(corners.filter(c=>p.pressure_supported?.[c]).map(c=>[c,Number(el('live-pit-pressure-'+c).value)]));
  if(p.compounds?.length)body.tire_compound=Number(el('live-pit-compound').value);
  lastCommand=body.id;awaitingConfirmation=true;lastSubmitError='';el('live-pit-apply').disabled=true;el('live-pit-result').textContent='Pending: sending request to the racing PC.';
  try{const result=await post('/api/live/pit',body);el('live-pit-result').textContent=`${result.status}: ${result.message}`;}
  catch(e){lastSubmitError=e.message+' Review actual selections before trying again.';el('live-pit-result').textContent=lastSubmitError;el('live-pit-result').className='error';lastCommand=null;awaitingConfirmation=false;}
 };
 setInterval(render,500);
 if(remote)return;

 const dialog=document.createElement('dialog');dialog.id='remote-settings-dialog';dialog.className='remote-settings-dialog';
 dialog.innerHTML='<div class="live-pane-heading"><h2>Remote pitwall</h2><button id="remote-close" type="button">Close</button></div><p id="remote-setup-status" role="status"></p><p id="remote-session-warning" class="quiet">Pit control is enabled for the current session and driver only. Restarting Stint or changing drivers disarms it.</p><div class="live-actions"><button id="remote-refresh" type="button">Refresh status</button><button id="remote-enable-control" type="button">Enable pit control</button><button id="remote-disable-control" type="button">Disable pit control</button></div><p id="remote-connected"></p><form id="remote-settings-form"><label class="flag-toggle"><input id="remote-enabled" type="checkbox">Enable private remote access</label><label>HTTPS pitwall address<input id="remote-origin" type="url" placeholder="https://racing-pc.your-tailnet.ts.net:8443"></label><h3>Allowed Tailscale users</h3><p class="quiet">Use each person’s Tailscale login. Designate one engineer; other users are viewers.</p><div id="remote-users"></div><button id="remote-add-user" type="button">Add user</button><div class="live-actions"><button class="primary" type="submit">Save remote settings</button><a id="remote-open-link" target="_blank" rel="noopener noreferrer" hidden>Open remote pitwall</a></div></form><p id="remote-result" role="status"></p><details open><summary>Connection setup</summary><ol><li><a href="https://tailscale.com/download/windows" target="_blank" rel="noopener noreferrer">Install Tailscale</a> on both PCs and sign in.</li><li>In the Tailscale admin console, share the racing PC with your engineer. Restrict their network access to this PC’s TCP port 8443 using the access-policy example in the README.</li><li>Open PowerShell in the Stint folder on the racing PC and run the setup helper:<code id="remote-serve-command"></code>The helper preserves other Serve configurations and stops if port 8443 is already used. Follow Tailscale’s HTTPS consent prompt if shown.</li><li>Refresh status, enter allowed logins, and save with private remote access enabled. Give the engineer the HTTPS address.</li><li>Enter the car, then enable pit control here. The engineer can now apply fuel and tire selections.</li></ol><p class="quiet">Use private Tailscale Serve. Public Funnel connections have no authenticated user identity and cannot access this pitwall.</p></details>';
 document.body.append(dialog);
 function paintUsers(){
  el('remote-users').innerHTML=settings.users.map((u,i)=>`<div class="remote-user-row"><label>Tailscale login<input data-user="${i}" data-key="login" value="${esc(u.login)}" maxlength="200" required></label><label>Name<input data-user="${i}" data-key="name" value="${esc(u.name)}" maxlength="120"></label><label>Role<select data-user="${i}" data-key="role"><option value="viewer" ${u.role==='viewer'?'selected':''}>Viewer</option><option value="engineer" ${u.role==='engineer'?'selected':''}>Engineer</option></select></label><button data-remove="${i}" type="button">Remove</button></div>`).join('')||'<p class="quiet">No users have access yet.</p>';
  el('remote-users').querySelectorAll('[data-user]').forEach(input=>input.oninput=()=>{settings.users[Number(input.dataset.user)][input.dataset.key]=input.value;settingsDirty=true;});
  el('remote-users').querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{settings.users.splice(Number(button.dataset.remove),1);settingsDirty=true;paintUsers();});
 }
 async function refreshSettings(force=false){
  try{
   const data=await api('/api/remote/settings');
   if(!settings||force||!settingsDirty){settings=structuredClone(data);settingsDirty=false;el('remote-enabled').checked=data.enabled;el('remote-origin').value=data.origin||data.transport?.origin||'';paintUsers();}
   el('remote-setup-status').textContent=[data.error,data.transport?.message,data.listener_running?'Pitwall listener running':'Remote access disabled'].filter(Boolean).join(' · ');
   el('remote-enable-control').disabled=!data.engineer||!!data.pit_control.blocked_reason||data.pit_control.enabled;
   el('remote-disable-control').disabled=!data.pit_control.enabled;
   el('remote-connected').textContent=`Pit control ${data.pit_control.enabled?'enabled':'disarmed'}. Connected: ${data.clients.map(c=>`${c.name} (${c.role})`).join(', ')||'No remote viewers'}.`;
   el('remote-serve-command').textContent='powershell -NoProfile -ExecutionPolicy Bypass -File ".\\Setup Remote Pitwall.ps1"';
   const link=el('remote-open-link');link.hidden=!data.origin;link.href=data.origin||'';
  }catch(e){el('remote-result').textContent=e.message;el('remote-result').className='error';}
 }
 el('live-remote-settings').onclick=()=>{dialog.showModal();refreshSettings(true);};
 el('remote-close').onclick=()=>dialog.close();el('remote-refresh').onclick=()=>refreshSettings();
 for(const id of ['remote-enabled','remote-origin'])el(id).oninput=()=>settingsDirty=true;
 el('remote-add-user').onclick=()=>{settings.users.push({login:'',name:'',role:'viewer'});settingsDirty=true;paintUsers();};
 el('remote-settings-form').onsubmit=async event=>{
  event.preventDefault();try{await post('/api/remote/settings',{enabled:el('remote-enabled').checked,origin:el('remote-origin').value,users:settings.users});settingsDirty=false;await refreshSettings(true);el('remote-result').textContent='Remote settings saved.';el('remote-result').className='connected';}catch(e){el('remote-result').textContent=e.message;el('remote-result').className='error';}
 };
 for(const [id,enabled] of [['remote-enable-control',true],['remote-disable-control',false]])el(id).onclick=async()=>{try{await post('/api/remote/control',{enabled});el('remote-result').textContent=enabled?'Pit control enabled for the designated engineer.':'Pit control disabled.';el('remote-result').className='connected';await refreshSettings();}catch(e){el('remote-result').textContent=e.message;el('remote-result').className='error';}};
 setInterval(()=>{if(dialog.open)refreshSettings();},3000);
})();
