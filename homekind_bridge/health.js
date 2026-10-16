'use strict';
// Local Ingress only. No credentials, raw reports or readings enter browser storage.
(()=>{
 const el=id=>document.getElementById(id),labels={steps:'Steps',sleep:'Sleep duration',resting_hr:'Resting heart rate'};
 let content='',format='csv',preview=null,profiles=[],ticket=0,epoch=0;
 const message=text=>{el('health-message').textContent=text;};
 function invalidate(){epoch++;preview=null;el('health-preview').replaceChildren();el('health-save').hidden=true;el('health-confirm').checked=false;el('health-confirm-wrap').hidden=true;}
 function option(value,text){const node=document.createElement('option');node.value=value;node.textContent=text;return node;}
 function fillColumns(columns){
  for(const key of ['date','steps','sleep','resting_hr']){const select=el('health-col-'+key);select.replaceChildren(option('',key==='date'?'Choose the date column':'Do not import'));for(const column of columns)select.append(option(column,column));if(key==='date')select.value=columns.find(c=>c.toLowerCase()==='date')||'';}
  el('health-columns').hidden=format==='json';el('health-preview-button').disabled=false;
 }
 async function refresh(){
  const result=await api('health/status');profiles=result.profiles;message(result.message);
  const select=el('health-profile'),chosen=select.value;select.replaceChildren(option('','New personal source'));for(const p of profiles)select.append(option(p.id,p.label));select.value=profiles.some(p=>p.id===chosen)?chosen:'';
  const list=el('health-saved');list.replaceChildren();for(const p of profiles){const row=document.createElement('p'),name=document.createElement('span'),button=document.createElement('button');name.textContent=p.label+' · '+p.readings.map(r=>labels[r.metric]+' '+r.date).join(', ');button.type='button';button.textContent='Remove this source';button.className='small';button.addEventListener('click',async()=>{button.disabled=true;try{await api('health/remove',{profile_id:p.id});invalidate();await refresh();await loadEntities();}catch(error){message(error.message);}finally{button.disabled=false;}});row.append(name,document.createTextNode(' '),button);list.append(row);}
 }
 el('health-file').addEventListener('change',async()=>{
  const id=++ticket;invalidate();content='';el('health-preview-button').disabled=true;const file=el('health-file').files[0];if(!file)return;
  if(file.size>131072||!(/\.(csv|json)$/i.test(file.name))){message('Choose a CSV or HomeKind JSON report up to 128 KB. ZIP and FIT files are not supported.');return;}
  try{const text=await file.text();if(id!==ticket)return;format=/\.json$/i.test(file.name)?'json':'csv';const result=await api('health/inspect',{content:text,format});if(id!==ticket)return;content=text;fillColumns(result.columns);message(result.rows+' report rows ready. Choose the columns and date format, then preview.');}catch(error){message(error.message);}
 });
 el('health-profile').addEventListener('change',()=>{invalidate();const p=profiles.find(p=>p.id===el('health-profile').value);for(const key of ['label','source','timezone'])el('health-'+key).disabled=!!p;if(p){el('health-label').value=p.label;el('health-source').value=p.source;el('health-timezone').value=p.timezone;}});
 el('health-import-form').addEventListener('change',event=>{if(event.target.id!=='health-file')invalidate();});
 el('health-import-form').addEventListener('input',invalidate);
 el('health-import-form').addEventListener('submit',async event=>{
  event.preventDefault();invalidate();const expected=epoch,button=el('health-preview-button');button.disabled=true;
  try{if(!content)throw Error('Choose a report first.');const data={content,format,label:el('health-label').value,source:el('health-source').value,timezone:el('health-timezone').value,profile_id:el('health-profile').value||null,date_format:el('health-date-format').value,sleep_unit:el('health-sleep-unit').value,mapping:Object.fromEntries(['date','steps','sleep','resting_hr'].map(k=>[k,el('health-col-'+k).value]))};
   const p=await api('health/preview',data);if(expected!==epoch)return;preview=p.preview_id;const intro=document.createElement('p');intro.textContent=p.label+' · '+p.source+' · latest selected daily readings';el('health-preview').append(intro);const list=document.createElement('ul');for(const r of p.readings){const li=document.createElement('li');li.textContent=labels[r.metric]+': '+r.value+' '+r.unit+' · source date '+r.date;list.append(li);}el('health-preview').append(list);el('health-save').hidden=false;el('health-confirm-wrap').hidden=false;message(p.older_rows_skipped?p.older_rows_skipped+' older rows excluded. Check the person, readings and dates before saving.':'Check the person, readings and dates before saving.');
  }catch(error){message(error.message);}finally{button.disabled=false;}
 });
 el('health-save').addEventListener('click',async()=>{
  const button=el('health-save');button.disabled=true;
  try{if(!preview||!el('health-confirm').checked)throw Error('Check the preview and confirm storage in this Home Assistant home.');const result=await api('health/commit',{preview_id:preview,publish_to_ha:true});content='';el('health-file').value='';invalidate();el('health-preview-button').disabled=true;await refresh();message(result.message);await loadEntities();}
  catch(error){message(error.message);}finally{button.disabled=false;}
 });
 el('health-template').addEventListener('click',()=>{const parts=new Intl.DateTimeFormat('en-CA',{timeZone:el('health-timezone').value,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()),part=k=>parts.find(p=>p.type===k).value,date=part('year')+'-'+part('month')+'-'+part('day');const blob=new Blob(['date,steps,sleep_minutes,resting_heart_rate\n'+date+',,,\n'],{type:'text/csv'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='HomeKind-health-template.csv';a.click();URL.revokeObjectURL(url);});
 el('health-timezone').value=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
 const start=()=>{void refresh().catch(error=>message(error.message));};if(state.csrf)start();else document.addEventListener('homekind-ready',start,{once:true});
})();
