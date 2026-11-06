'use strict';
// Standalone Live requires no recording, AI bootstrap, or workspace administration.
window.REMOTE_PITWALL=true;
window.RemoteIdentity={role:'viewer'};
const worksheet='live';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function number(value,digits=1){if(value==null)return '—';const text=Number(value).toFixed(digits);return Number(text)===0?text.replace(/^-/,''):text;}
function lapTime(value){return value==null?'—':`${Math.floor(value/60)}:${(value%60).toFixed(3).padStart(6,'0')}`;}
async function api(path,options={}){const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed.');return result;}
const post=(path,body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
document.getElementById('tab-live').hidden=false;
document.getElementById('live-remote-settings').hidden=true;
document.getElementById('live-map-import').hidden=true;
async function refreshRemoteIdentity(){
 try{window.RemoteIdentity=await api('/api/remote/me');document.getElementById('remote-identity').textContent=`${RemoteIdentity.name} · ${RemoteIdentity.role==='engineer'?'Engineer':'Viewer'}`;}
 catch(e){window.RemoteIdentity={role:'viewer'};document.getElementById('remote-identity').textContent=e.message;}
 window.LiveControls?.render();
}
refreshRemoteIdentity();
addEventListener('live-stream-open',refreshRemoteIdentity);
addEventListener('live-stream-error',refreshRemoteIdentity);
