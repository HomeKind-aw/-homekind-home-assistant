'use strict';
const byId=id=>document.getElementById(id);
const state={csrf:null,entities:[],selected:new Set(),live:new Set(),paired:false,phase:'setup',loading:true,controlsLoaded:false};
async function api(path,body){
 const response=await fetch('./api/'+path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-HomeKind-Setup':state.csrf}:{},...(body?{body:JSON.stringify(body)}:{})});
 const result=await response.json();if(!response.ok)throw Error(result.error||'The connection is not ready. Try again.');return result;
}
function error(message=''){byId('error').textContent=message;byId('error').hidden=!message;}
function count(){state.live=new Set([...state.live].filter(e=>state.selected.has(e)));byId('selected-count').textContent=state.selected.size+' selected';renderLive();}
function renderLive(){const target=byId('live-cameras');if(!target)return;const rows=state.entities.filter(e=>e.entity_id.startsWith('camera.')&&state.selected.has(e.entity_id));target.replaceChildren();for(const row of rows){const label=document.createElement('label');label.className='control-choice';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=state.live.has(row.entity_id);checkbox.addEventListener('change',()=>{if(checkbox.checked)state.live.add(row.entity_id);else state.live.delete(row.entity_id);});const text=document.createElement('span');text.textContent=row.name;label.append(checkbox,text);target.append(label);}if(!rows.length){const p=document.createElement('p');p.textContent='Select a camera in your readings above to make its live-view choice available.';target.append(p);}}
function recommended(entity){if(/^(media_player|camera|event|weather)\./.test(entity.entity_id))return true;const k=entity.device_class||'',name=(entity.name+' '+entity.entity_id).replace(/[_.-]+/g,' ');return !/\b(crossfade|loudness|autoplay|surround|child lock|do not disturb|auto off enabled|auto update|indicator|led|adaptive|sensitivity|selfidentification|interference|calibration|night sound|speech enhancement)\b/i.test(name)&&!['voltage','current','signal_strength','connectivity','problem'].includes(k);}
function visibleEntities(){const search=byId('search').value.trim().toLocaleLowerCase();return state.entities.filter(entity=>(byId('show-other').checked||recommended(entity)||state.selected.has(entity.entity_id))&&(entity.name+' '+entity.entity_id).toLocaleLowerCase().includes(search));}
function renderEntities(){
 const search=byId('search').value.trim().toLocaleLowerCase(),matched=visibleEntities();
 const fragment=document.createDocumentFragment();
 for(const entity of matched.slice(0,200)){
  const label=document.createElement('label');label.className='entity';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=entity.entity_id;checkbox.checked=state.selected.has(entity.entity_id);
  checkbox.addEventListener('change',()=>{if(checkbox.checked){if(state.selected.size>=250){checkbox.checked=false;error('Choose up to 250 sensors.');return;}state.selected.add(entity.entity_id);}else state.selected.delete(entity.entity_id);count();});
  const text=document.createElement('span'),name=document.createElement('b'),id=document.createElement('small');name.textContent=entity.name;id.textContent=entity.entity_id+(entity.device_class?.startsWith('health_')?' · Personal: connect to yourself in My sharing':entity.entity_id.startsWith('camera.')?' · Status reading; live view has a separate choice below':'');text.append(name,id);label.append(checkbox,text);fragment.append(label);
 }
 if(!matched.length){const p=document.createElement('p');p.textContent=search?'No matching sensors. Try another name.':'Supported sensors will appear once they are available in Home Assistant.';fragment.append(p);}
 byId('entities').replaceChildren(fragment);byId('list-note').textContent=matched.length>200?'Showing the first 200 matches. Search to find another sensor.':'';count();
}
async function loadEntities(initial=false){
 byId('reload').disabled=true;try{const result=await api('entities');state.entities=result.entities;if(initial)state.selected=new Set(result.entities.filter(entity=>entity.selected).map(entity=>entity.entity_id));renderEntities();state.loading=false;byId('save').disabled=!state.csrf;error();}
 catch(failure){error(failure.message);}finally{byId('reload').disabled=false;}
}
async function refreshStatus(){
 try{const s=await api('status');state.paired=s.paired;state.phase=s.phase;if(!state.controlsLoaded){state.live=new Set(s.live_camera_entity_ids||[]);byId('allow-controls').checked=s.read_only===false;state.controlsLoaded=true;}const labels={setup:'Ready to connect',paused:'Sharing paused',starting:'Saving your connection',connecting:'Connecting',connected:'Connected to HomeKind',waiting:'Waiting for an update',pairing_required:'Fresh pairing code needed'};byId('connection').textContent=labels[s.phase]||'Checking connection';byId('status-message').textContent=s.message;byId('pairing').required=!s.paired||s.phase==='pairing_required';byId('pairing-hint').textContent=s.phase==='pairing_required'?'Create a fresh code in HomeKind, then paste it above.':s.paired?'Your household is paired. Leave this blank when updating your sensor selection.':'The code lasts 10 minutes. Create it once this app is installed.';byId('save').textContent=s.paired?'Save selected sensors':'Connect household';}
 catch{byId('connection').textContent='Setup connection interrupted';byId('status-message').textContent='Reload this screen or check that the HomeKind app is running.';}
}
byId('show-other').addEventListener('change',renderEntities);
byId('select-shown').addEventListener('click',()=>{for(const e of visibleEntities())if(state.selected.size<250)state.selected.add(e.entity_id);renderEntities();});
byId('clear-shown').addEventListener('click',()=>{state.selected.clear();renderEntities();});
byId('search').addEventListener('input',renderEntities);
byId('reload').addEventListener('click',()=>{void loadEntities();});
byId('setup').addEventListener('submit',async event=>{
 event.preventDefault();const button=byId('save');button.disabled=true;error();
 try{await api('configure',{pairing_code:byId('pairing').value.trim(),entity_ids:[...state.selected],read_only:!byId('allow-controls').checked,live_camera_entity_ids:[...state.live]});byId('pairing').value='';await refreshStatus();}
 catch(failure){error(failure.message);}finally{button.disabled=false;}
});
(async()=>{try{state.csrf=(await api('setup')).csrf;await refreshStatus();await loadEntities(true);}catch(failure){error(failure.message);}setInterval(()=>{void refreshStatus();},4000);})();
