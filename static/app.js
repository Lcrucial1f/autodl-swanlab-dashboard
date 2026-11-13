const $ = (s) => document.querySelector(s);
const icons = {
 grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
 server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3M15 17.5h3"/>',
 chip:'<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 3v3m6-3v3m-6 12v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
 pulse:'<path d="M3 12h4l3-7 4 14 3-7h4"/>',
 leaf:'<path d="M20 4C8 2 3 7 5 14s15 7 15-10Z"/><path d="m4 21 10-10"/>',
 available:'<path d="m9 12 2 2 5-5"/><circle cx="12" cy="12" r="9"/>',
 search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
 refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
 settings:'<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z"/><path d="m9 3-.5 2-2 1-2-.4-2 3.4 1.5 1.5v3L2.5 15l2 3.4 2-.4 2 1 .5 2h4l.5-2 2-1 2 .4 2-3.4-1.5-1.5v-3L20 9l-2-3.4-2 .4-2-1-.5-2Z"/>',
 moon:'<path d="M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10Z"/>',
 sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
 pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.5"/>',
 terminal:'<path d="m5 7 5 5-5 5m8 0h6"/>',
 edit:'<path d="m16 3 5 5L8 21H3v-5L16 3Zm-3 3 5 5"/>',
 arrow:'<path d="M6 18 18 6M6 6h12v12"/>',
 chevron:'<path d="m7 10 5 5 5-5"/>',
 history:'<path d="M3 5v5h5M3 10a9 9 0 1 1 1 7"/><path d="M12 7v5l3 2"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]||icons.chip}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
function applyTheme(theme) {
  document.documentElement.dataset.theme=theme;
  const dark=theme==='dark',button=$('#theme-button');
  button.innerHTML=icon(dark?'sun':'moon')+`<span class="theme-label">${dark?'浅色主题':'深色主题'}</span><span class="theme-shortcut">◐</span>`;
  button.setAttribute('aria-label',dark?'切换浅色主题':'切换深色主题');
}
try { applyTheme(localStorage.getItem('autodl-theme')==='dark'?'dark':'light'); } catch {applyTheme('light');}
$('#theme-button').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='light'?'dark':'light';applyTheme(theme);try{localStorage.setItem('autodl-theme',theme);}catch{}});
const token = $('meta[name="autodl-token"]').content;
let state = null, filter = 'all', query = '', draft = null, fetching = false;
let stateRevision = 0, renameTarget = null, renameSaving = false;
let historyRevision, completedItems=[], completedTotal=0, completedQuery='', completedLoaded=false, completedLoading=false, completedRequest=0, completedController, completedTimer, historyReturnPath='';
const historyPanels=new Map();
const collapsed = new Set(JSON.parse(localStorage.getItem('autodl-collapsed') || '[]'));
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gib = (n) => n == null ? '—' : (n / 1024).toFixed(1);
const fresh = (i) => i.online === true && !i.stale;
const count = (i, status) => fresh(i) ? i.gpus.filter(g => g.state === status).length : 0;
const safeUrl = (url) => { try { const u = new URL(url); return u.protocol === 'https:' && u.hostname === 'swanlab.cn' ? u.href : ''; } catch { return ''; } };
function toast(message) { const el=$('#toast'); el.textContent=message; el.hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.hidden=true,2600); }
async function post(path, data={}) { const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Autodl-Token':token},body:JSON.stringify(data)}); const result=await res.json(); if(!res.ok){const error=new Error(result.error||'操作失败');error.status=res.status;error.result=result;throw error;} return result; }
const hasHistory=()=>state && Object.prototype.hasOwnProperty.call(state,'history_revision');
function historyTime(value) { if(!value)return '时间未记录';const date=new Date(value*1000);return Number.isNaN(date.getTime())?'时间未记录':date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); }
async function readHistoryApi(path,signal) { const response=await fetch(path,{signal:signal||AbortSignal.timeout(8000)});const result=await response.json();if(!response.ok)throw Error(result.error||'读取失败');return result; }
function renderCompleted() {
  const focus=document.activeElement?.closest('[data-history]')?.dataset.history;
  $('#completed-count').textContent=completedTotal;
  $('#completed-list').innerHTML=completedItems.map(item=>{
    const url=safeUrl(item.url),instances=(item.instances||[]).map(i=>i.name||`实例 ${i.id}`).join(' / ');
    return `<article class="completed-item" data-completed="${esc(item.path)}"><div class="completed-main"><div class="completed-label"><span class="completed-badge">${icon('available')}已完成</span><span class="completed-project" title="${esc(item.workspace)} / ${esc(item.project)}">${esc(item.project||'项目未记录')}</span></div><h3>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(item.name)}</a>`:esc(item.name)}</h3><div class="completed-meta"><span title="${esc(instances)}">${icon('server')}${esc(instances||'实例未记录')}</span><span>${item.finished_at?'完成于':'确认完成于'} ${esc(historyTime(item.finished_at||item.completed_at))}</span></div></div><div class="completed-actions"><button type="button" class="rename-button" data-history="${esc(item.path)}" aria-label="查看 ${esc(item.name)} 的名称历史">${icon('history')}<span>名称历史${item.history_count?` <small>${Number(item.history_count)||0}</small>`:''}</span></button>${url?`<a class="completed-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="打开已完成的 SwanLab 实验 ${esc(item.name)}">SwanLab ${icon('arrow')}</a>`:''}</div></article>`;
  }).join('');
  if(focus)document.querySelector(`[data-history="${CSS.escape(focus)}"]`)?.focus({preventScroll:true});
  $('#completed-status').textContent=completedLoading?(completedItems.length?'正在更新历史…':'正在读取历史…'):!$('#completed-error').hidden?'':!completedItems.length?(completedQuery?'没有找到匹配的实验，试试旧名称、项目或实例。':hasHistory()?'暂无已完成实验。启用后自动记录，由 SwanLab 确认完成后显示在这里。':'历史服务尚未启用，请更新并重启本地服务。'):'';
  $('#completed-summary').textContent=completedItems.length?`已显示 ${completedItems.length} / ${completedTotal} 个${completedQuery?'匹配实验':'实验 · 最近完成优先'}`:'';
  $('#completed-more').hidden=completedItems.length>=completedTotal;
  $('#completed-more').disabled=completedLoading;
  $('#completed').setAttribute('aria-busy',String(completedLoading));
}
async function loadCompleted(append=false) {
  if(!hasHistory()){renderCompleted();return;}
  const request=++completedRequest,search=completedQuery,target=append?5:Math.max(5,completedItems.length);
  completedController?.abort();completedController=new AbortController();
  completedLoading=true;$('#completed-error').hidden=true;renderCompleted();
  try {
    let items=append?[...completedItems]:[],total=0;
    do {
      const params=new URLSearchParams({q:search,limit:'5',offset:String(items.length)});
      const result=await readHistoryApi('/api/experiments/completed?'+params,AbortSignal.any([completedController.signal,AbortSignal.timeout(8000)]));
      if(request!==completedRequest)return;
      if(!Array.isArray(result.items))throw Error('历史数据格式不正确');
      items.push(...result.items);total=Number(result.total)||0;
      if(append||!result.has_more||!result.items.length)break;
    } while(items.length<target);
    completedItems=items;completedTotal=total;completedLoaded=true;
  } catch(error) {
    if(request!==completedRequest)return;
    $('#completed-error-text').textContent='无法读取已完成实验：'+(error.message||'请稍后重试');$('#completed-error').hidden=false;
  } finally {if(request===completedRequest){completedLoading=false;renderCompleted();}}
}
function syncHistory() {
  if(!hasHistory()){renderCompleted();return;}
  if((!completedLoaded&&!completedLoading)||historyRevision!==state.history_revision){historyRevision=state.history_revision;clearTimeout(completedTimer);loadCompleted();}
  if(state.history_error){$('#completed-error-text').textContent='实验历史暂不可用：'+state.history_error;$('#completed-error').hidden=false;}
}
$('#completed-search').addEventListener('input',e=>{
  completedQuery=e.target.value.trim();completedItems=[];completedTotal=0;completedLoaded=false;
  ++completedRequest;completedController?.abort();clearTimeout(completedTimer);completedLoading=true;$('#completed-error').hidden=true;renderCompleted();
  completedTimer=setTimeout(()=>loadCompleted(),250);
});
$('#completed-more').addEventListener('click',()=>loadCompleted(true));
$('#completed-retry').addEventListener('click',()=>loadCompleted());
function renderNameHistory(selector) {
  const panel=historyPanels.get(selector);if(!panel)return;
  const root=$(selector),experiment=panel.experiment;
  if(panel.loading){root.innerHTML='<h3>重命名历史</h3><p class="history-notice" role="status">正在读取名称历史…</p>';return;}
  if(panel.error){root.innerHTML=`<h3>重命名历史</h3><p class="history-notice error" role="alert">${esc(panel.error)}</p>${hasHistory()?'<button type="button" class="text-button" data-history-retry>重新读取</button>':''}`;return;}
  const records=[...(experiment?.rename_history||[])].sort((a,b)=>(b.changed_at||0)-(a.changed_at||0));
  const visible=panel.expanded?records:records.slice(0,5);
  root.innerHTML=`<div class="history-heading"><h3>重命名历史 <span>${records.length}</span></h3><span>最近在前</span></div><div class="history-original"><span>日志初始名称</span><strong>${esc(experiment?.original_name||'尚未记录')}</strong></div>${visible.length?`<ol class="history-timeline">${visible.map(item=>`<li><div class="history-names"><span class="history-old">${esc(item.old_name)}</span><span class="history-arrow" aria-label="改为">→</span><strong>${esc(item.new_name)}</strong></div><div class="history-meta"><span>${item.source==='cloud'?'云端变化 · 发现于':'看板改名 ·'} ${esc(historyTime(item.changed_at))}</span></div></li>`).join('')}</ol>`:'<p class="history-notice">暂无重命名记录，之后的名称变化会保留在这里。</p>'}${records.length>5?`<button type="button" class="text-button history-expand" data-history-expand aria-expanded="${panel.expanded}">${panel.expanded?'收起历史':`查看全部 ${records.length} 次改名`}</button>`:''}`;
}
async function loadNameHistory(selector,path) {
  const previous=historyPanels.get(selector),request=(previous?.request||0)+1;
  historyPanels.set(selector,{path,request,loading:true,expanded:false});renderNameHistory(selector);
  if(!hasHistory()){historyPanels.set(selector,{path,request,error:'历史服务尚未启用。更新服务后会开始记录；当前仍可修改名称。'});renderNameHistory(selector);return;}
  try {
    const result=await readHistoryApi('/api/experiments/history?'+new URLSearchParams({path}));
    if(historyPanels.get(selector)?.request!==request||historyPanels.get(selector)?.path!==path)return;
    if(!result.experiment||!Array.isArray(result.experiment.rename_history))throw Error('名称历史格式不正确');
    historyPanels.set(selector,{path,request,experiment:result.experiment,expanded:false});
    if(selector==='#completed-history'){$('#history-current').textContent=result.experiment.name;$('#history-project').textContent=result.experiment.project||'—';}
  } catch(error){if(historyPanels.get(selector)?.request!==request)return;historyPanels.set(selector,{path,request,error:'无法读取名称历史：'+error.message+(selector==='#rename-history'?'。仍可修改名称。':'')});}
  renderNameHistory(selector);
}
for(const selector of ['#rename-history','#completed-history'])$(selector).addEventListener('click',e=>{
  const panel=historyPanels.get(selector);if(!panel)return;
  if(e.target.closest('[data-history-expand]')){panel.expanded=!panel.expanded;renderNameHistory(selector);$(selector).querySelector('[data-history-expand]')?.focus({preventScroll:true});}
  if(e.target.closest('[data-history-retry]'))loadNameHistory(selector,panel.path);
});
$('#completed-list').addEventListener('click',e=>{
  const button=e.target.closest('[data-history]');if(!button)return;
  const item=completedItems.find(item=>item.path===button.dataset.history);if(!item)return;
  historyReturnPath=item.path;$('#history-project').textContent=item.project||'—';$('#history-current').textContent=item.name;
  loadNameHistory('#completed-history',item.path);$('#history-dialog').showModal();
});
for(const id of ['close-history','finish-history'])$('#'+id).addEventListener('click',()=>$('#history-dialog').close());
function invalidateHistory(selector) { const panel=historyPanels.get(selector);if(panel)panel.request++; }
$('#history-dialog').addEventListener('close',()=>{invalidateHistory('#completed-history');const button=document.querySelector(`[data-history="${CSS.escape(historyReturnPath)}"]`);(button||$('#completed-search')).focus({preventScroll:true});});
function matches(i) {
  const text=[i.name,i.display_name,i.host,i.port,...i.tasks.flatMap(t=>t.experiments.flatMap(e=>[e.name,e.project,e.run_id]))].join(' ').toLowerCase();
  return text.includes(query) && (filter==='all' || filter==='busy' && count(i,'busy')>0 || filter==='idle' && count(i,'idle')>0 || filter==='offline' && i.online===false);
}
function stats() {
  const items=state.instances, online=items.filter(fresh), gpus=online.flatMap(i=>i.gpus);
  const pending=!items.some(i=>i.online!==null), busy=gpus.filter(g=>g.state==='busy').length, idle=gpus.filter(g=>g.state==='idle').length;
  const cells=[['实例总数',items.length,'台',`${online.length} 台在线`,'server'],['GPU 总数',gpus.length,'张','在线实例可见','chip'],['运行中 GPU',busy,'张','当前运行 / 占用','pulse'],['空闲 GPU',idle,'张','等待下一个实验','leaf'],['可用机器',online.filter(i=>count(i,'idle')>0).length,'台','至少一张空闲 GPU','available']];
  $('#stats').innerHTML=cells.map(([label,value,unit,sub,ic],n)=>`<div class="stat stat-${n}"><div class="stat-label">${label}<span class="stat-icon">${icon(ic)}</span></div><div class="stat-value">${pending&&n>0?'—':value}<span>${unit}</span></div><div class="stat-sub">${n===0?'<i class="online-dot"></i>':''}${sub}</div></div>`).join('');
  $('#fleet-count').textContent=items.length+' 台实例';
}
function gpuTile(g) {
  const util=g.util==null?'—':Math.round(g.util), percent=g.util==null?0:Math.min(100,Math.max(0,g.util));
  const label=g.state==='busy'?'运行':g.state==='idle'?'空闲':'未知';
  return `<div class="gpu ${esc(g.state)}" title="GPU ${g.index} · ${esc(g.name)} · 利用率 ${util}% · 显存 ${gib(g.used)} / ${gib(g.total)} GiB"><div class="gpu-top"><span class="gpu-index">GPU ${g.index}</span><span class="gpu-state"><i></i>${label}</span></div><div class="gpu-use">${util}<small>%</small></div><div class="bar"><span style="width:${percent}%"></span></div><div class="memory"><span>余 ${gib(g.free)}</span><small> GiB</small></div></div>`;
}
function taskRows(i) {
  if(!fresh(i))return `<div class="task-empty">${icon('pulse')}<div>${i.online===null?'等待首次采样':i.stale?'数据已过期':'实例暂时离线'}<small>恢复连接后自动更新</small></div></div>`;
  if(!i.tasks.length)return `<div class="task-empty ${count(i,'busy')?'unmatched':'idle-task'}">${icon(count(i,'busy')?'search':'leaf')}<div>${count(i,'busy')?'实验待识别':i.gpus.length?'算力就绪':'暂无 GPU 信息'}<small>${count(i,'busy')?'GPU 已占用，未匹配到实验日志':i.gpus.length?'当前没有 GPU 任务':'等待下一次采样'}</small></div></div>`;
  return i.tasks.map(t=> {
    const experiments=t.experiments.length?t.experiments:[{project:'运行中的 GPU 任务',name:t.process_visible?'SwanLab 实验未识别':'GPU 进程不可见',url:''}];
    return experiments.map(e=>{const url=safeUrl(e.url),title=esc(e.evidence||'暂未找到与当前进程匹配的 SwanLab 日志');
      const syncLabel=e.path?(e.name_source==='cloud'?'已同步':e.name_source==='cache'?'上次同步':'等待同步'):'';
      return `<div class="task"><div class="task-caption"><span class="swan-mark">S</span>SwanLab ${syncLabel?`<span class="name-sync ${e.name_sync_error?'sync-warning':''}" title="${esc(e.name_sync_error||'名称与 SwanLab 同步')}">${syncLabel}</span>`:''}<span class="task-gpus" title="PID ${t.pid}">GPU ${t.gpu_indices.join(' / ')}</span></div><div class="task-project" title="项目：${esc(e.project||'项目待识别')}">${esc(e.project||'项目待识别').replaceAll('_','_<wbr>')}</div><div class="experiment-row">${url?`<a class="task-name" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(e.name)} · ${title}">${esc(e.name).replaceAll('_','_<wbr>')}</a>`:`<div class="task-name" title="${title}">${esc(e.name)}</div>`}</div>${url?`<div class="task-actions"><a class="task-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="打开 SwanLab 实验 ${esc(e.name)}"><span>打开 SwanLab</span>${icon('arrow')}</a>${e.path?`<button type="button" class="rename-button" data-rename="${esc(e.path)}" aria-label="修改实验名称 ${esc(e.name)}">${icon('edit')}<span>修改名称</span></button>`:''}</div>`:''}</div>`;
    }).join('');
  }).join('');
}
function card(i) {
  let status=i.online===null?'连接中':i.online?'在线':'离线', cls=i.online?'':'offline';
  if(i.stale){status='数据过期';cls='stale';}
  const models=[...new Set(i.gpus.map(g=>g.name.replace('NVIDIA GeForce ','').replace('NVIDIA ','')))].join(' / ');
  const idle=count(i,'idle'),busy=count(i,'busy');
  const label=busy?`${busy} 张运行${idle?' · '+idle+' 张空闲':''}`:idle?`${idle} 张空闲`:'';
  return `<article class="card ${cls} ${idle===i.gpus.length&&idle?'available-card':''}" data-instance="${i.id}"><div class="machine"><div class="machine-kicker">${icon('server')}<span class="status ${cls}"><i></i>${status}</span></div><div class="card-title" title="${esc(i.name)} · ${esc(i.host)}:${i.port}">${esc(i.display_name)}</div><div class="model"><strong>${esc(models||'GPU 状态待更新')}</strong>${i.gpus.length?' <span>× '+i.gpus.length+'</span>':''}</div><div class="busy-label ${busy?'':'idle-label'}">${label}</div></div><div class="telemetry">${i.gpus.length?`<div class="gpu-grid">${i.gpus.map(gpuTile).join('')}</div>`:`<div class="card-error">${esc(i.error||i.gpu_error||'正在读取 GPU 与 SwanLab 日志…')}</div>`}</div><div class="tasks">${taskRows(i)}</div><button class="copy-button" data-copy="${i.id}" title="复制连接命令：autodl ${i.id}" aria-label="复制实例 ${i.port} 的连接命令">${icon('terminal')}<span>SSH</span></button></article>`;
}
function captureFocus() {
  const a=document.activeElement;
  if(a?.dataset.rename)return ['rename',a.dataset.rename,a.closest('.card')?.dataset.instance];
  if(a?.dataset.copy)return ['copy',a.dataset.copy];
  if(a?.dataset.region)return ['region',a.dataset.region];
  if(a?.dataset.jump)return ['jump',a.dataset.jump];
  if(a?.matches('.task-name,.task-link'))return ['link',a.closest('.card').dataset.instance,a.className,a.getAttribute('href')];
  return null;
}
function restoreFocus(focus) {
  if(!focus)return;
  let el;
  if(focus[0]==='rename')el=document.querySelector(`[data-instance="${CSS.escape(focus[2])}"] [data-rename="${CSS.escape(focus[1])}"]`);
  else if(focus[0]==='link')el=[...document.querySelectorAll(`[data-instance="${CSS.escape(focus[1])}"] a`)].find(a=>a.className===focus[2]&&a.getAttribute('href')===focus[3]);
  else el=document.querySelector(`[data-${focus[0]}="${CSS.escape(focus[1])}"]`);
  el?.focus({preventScroll:true});
}
function render() {
  if(!state)return;
  const focused=captureFocus();
  stats();
  $('#welcome').hidden=state.instances.length>0;
  $('#regions').hidden=state.instances.length===0;
  $('.toolbar').hidden=state.instances.length===0;
  $('#region-nav').innerHTML=state.regions.map(r=>`<button data-jump="${esc(r.id)}">${icon('pin')}<span>${esc(r.name)}</span><small>${state.instances.filter(i=>i.region===r.id).length}</small></button>`).join('');
  const regions=state.regions.map((r,n)=>{
    const all=state.instances.filter(i=>i.region===r.id),items=all.filter(matches),busy=all.reduce((s,i)=>s+count(i,'busy'),0),idle=all.reduce((s,i)=>s+count(i,'idle'),0);
    if(!items.length && (filter!=='all'||query))return '';
    const isCollapsed=collapsed.has(r.id);
    return `<section id="region-${esc(r.id)}" class="region ${isCollapsed?'collapsed':''}"><button class="region-heading" data-region="${esc(r.id)}" aria-expanded="${!isCollapsed}"><span class="region-emblem">${icon('pin')}</span><span class="region-title">${esc(r.name)}</span><span class="region-count">${all.length} 台</span><span class="region-line"></span><span class="region-meta"><span>${all.filter(fresh).length} 台在线</span><span class="region-running">${busy} 卡运行</span><span class="region-idle">${idle} 卡空闲</span></span><span class="chevron">${icon('chevron')}</span></button>${isCollapsed?'':`<div class="cards">${items.map(card).join('')}</div>${!items.length?'<div class="empty">该区域还没有实例，可以在分区设置中调整归属。</div>':''}`}</section>`;
  }).join('');
  $('#regions').innerHTML=regions||'<div class="empty">没有匹配的实例，试试其他筛选条件。</div>';
  restoreFocus(focused);
  const known=state.instances.filter(i=>i.checked_at);
  $('#update-time').textContent=state.updated_at?`最后更新 ${new Date(state.updated_at*1000).toLocaleTimeString('zh-CN',{hour12:false})}${state.refreshing?' · 更新中':''}`:known.length?`已连接 ${known.length} / ${state.instances.length} 台`:'正在连接实例…';
  $('#banner').hidden=!state.error;
  $('#banner').textContent=state.error||'';
}
async function load() {
  if(fetching)return;
  fetching=true;
  const requestRevision=stateRevision;
  try {const res=await fetch('/api/state',{signal:AbortSignal.timeout(6000)}); if(!res.ok)throw Error('读取失败'); const next=await res.json(); if(requestRevision===stateRevision){state=next;render();syncHistory();}}
  catch {$('#banner').hidden=false;$('#banner').textContent='本地服务连接中断，当前显示的是上次采样。运行 autodl ui 可以重新打开服务。';$('#update-time').textContent='连接已中断';}
  finally {fetching=false;}
}
$('#search').addEventListener('input',e=>{query=e.target.value.trim().toLowerCase();render();});
$('.filters').addEventListener('click',e=>{const b=e.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('selected',el===b));render();});
$('#regions').addEventListener('click',async e=>{
  const rename=e.target.closest('[data-rename]');
  if(rename){openRename(rename);return;}
  const region=e.target.closest('[data-region]');
  if(region){const id=region.dataset.region;collapsed.has(id)?collapsed.delete(id):collapsed.add(id);localStorage.setItem('autodl-collapsed',JSON.stringify([...collapsed]));render();return;}
  const copy=e.target.closest('[data-copy]');if(copy){try{await navigator.clipboard.writeText('autodl '+copy.dataset.copy);toast('已复制：autodl '+copy.dataset.copy);}catch{toast('终端输入：autodl '+copy.dataset.copy);}}
});
$('#refresh-button').addEventListener('click',async()=>{try{await post('/api/refresh');toast('正在刷新实例状态');setTimeout(load,500);}catch(e){toast(e.message);}});
function readDraftInputs() {
  document.querySelectorAll('[data-region-name]').forEach(el=>{const r=draft.regions.find(r=>r.id===el.dataset.regionName);if(r)r.name=el.value;});
  document.querySelectorAll('[data-instance-name]').forEach(el=>draft.instances[el.dataset.instanceName].name=el.value);
  document.querySelectorAll('[data-instance-region]').forEach(el=>draft.instances[el.dataset.instanceRegion].region=el.value);
}
function draftRender() {
  $('#region-fields').innerHTML=draft.regions.map((r,n)=>`<div class="region-field"><span>${String(n+1).padStart(2,'0')}</span><input aria-label="区域 ${n+1} 名称" data-region-name="${esc(r.id)}" maxlength="30" required value="${esc(r.name)}"><button type="button" data-move="${n}" data-direction="-1" ${n===0?'disabled':''} aria-label="上移 ${esc(r.name)}">↑</button><button type="button" data-move="${n}" data-direction="1" ${n===draft.regions.length-1?'disabled':''} aria-label="下移 ${esc(r.name)}">↓</button></div>`).join('');
  $('#instance-fields').innerHTML=state.instances.map(i=>{const item=draft.instances[String(i.id)];return `<label class="instance-field"><span>#${i.id} · ${i.port}</span><input aria-label="实例 ${i.port} 名称" data-instance-name="${i.id}" maxlength="60" value="${esc(item.name)}"><select aria-label="实例 ${i.port} 区域" data-instance-region="${i.id}">${draft.regions.map(r=>`<option value="${esc(r.id)}" ${item.region===r.id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></label>`;}).join('');
}
$('#settings-button').addEventListener('click',()=>{if(!state)return;draft={regions:structuredClone(state.regions),instances:Object.fromEntries(state.instances.map(i=>[String(i.id),{name:i.display_name,region:i.region}]))};draftRender();$('#settings-error').textContent='';$('#settings-dialog').showModal();});
$('#add-region').addEventListener('click',()=>{readDraftInputs();draft.regions.push({id:'custom-'+Date.now(),name:'新区域'});draftRender();});
$('#region-fields').addEventListener('click',e=>{const b=e.target.closest('[data-move]');if(!b)return;readDraftInputs();const n=Number(b.dataset.move),other=n+Number(b.dataset.direction);[draft.regions[n],draft.regions[other]]=[draft.regions[other],draft.regions[n]];draftRender();});
for(const id of ['close-settings','cancel-settings'])$('#'+id).addEventListener('click',()=>$('#settings-dialog').close());
$('#settings-form').addEventListener('submit',async e=>{e.preventDefault();readDraftInputs();draft.regions.forEach(r=>r.name=r.name.trim());$('#save-settings').disabled=true;try{await post('/api/settings',draft);$('#settings-dialog').close();await load();toast('分区设置已保存');}catch(error){$('#settings-error').textContent=error.message;}finally{$('#save-settings').disabled=false;}});
load();setInterval(load,3000);

$('#region-nav').addEventListener('click',e=>{const b=e.target.closest('[data-jump]');if(!b)return;query='';$('#search').value='';filter='all';document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('selected',el.dataset.filter==='all'));collapsed.delete(b.dataset.jump);render();document.getElementById('region-'+b.dataset.jump)?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!document.querySelector('dialog[open]')){e.preventDefault();$('#search').focus();}});

function allExperiments() { return state?.instances.flatMap(i=>i.tasks.flatMap(t=>t.experiments)) || []; }
function openRename(button) {
  if(renameSaving)return;
  const experiment=allExperiments().find(e=>e.path===button.dataset.rename);
  if(!experiment){toast('实验信息正在更新，请稍后重试');return;}
  renameTarget={path:experiment.path,expected_name:experiment.name,instance:button.closest('.card').dataset.instance};
  $('#rename-project').textContent=experiment.project || '—';
  $('#rename-current').textContent=experiment.name;
  $('#rename-name').value=experiment.name;
  $('#rename-error').textContent='';
  $('#rename-progress').textContent=experiment.name_sync_error?'当前显示上次记录的名称。保存前会核对 SwanLab 最新名称。':'';
  loadNameHistory('#rename-history',experiment.path);
  $('#rename-dialog').showModal();
  $('#rename-name').focus({preventScroll:true});
  $('#rename-name').select();
  $('.rename-body').scrollTop=0;
}
function setRenameBusy(busy) {
  renameSaving=busy;
  $('#rename-form').setAttribute('aria-busy',String(busy));
  for(const id of ['rename-name','save-rename','cancel-rename','close-rename'])$('#'+id).disabled=busy;
  $('#save-rename').textContent=busy?'正在同步…':'保存并同步';
}
function cancelRename() { if(!renameSaving)$('#rename-dialog').close(); }
for(const id of ['cancel-rename','close-rename'])$('#'+id).addEventListener('click',cancelRename);
$('#rename-dialog').addEventListener('cancel',e=>{if(renameSaving)e.preventDefault();});
$('#rename-dialog').addEventListener('close',()=>{
  invalidateHistory('#rename-history');
  const target=renameTarget;
  renameTarget=null;
  if(target)requestAnimationFrame(()=>{
    const control=document.querySelector(`[data-instance="${CSS.escape(target.instance)}"] [data-rename="${CSS.escape(target.path)}"]`);
    (control || $('#refresh-button')).focus({preventScroll:true});
  });
});
$('#rename-form').addEventListener('submit',async e=>{
  e.preventDefault();
  if(renameSaving||!renameTarget)return;
  const name=$('#rename-name').value.trim();
  if(!name||[...name].length>100){$('#rename-error').textContent='请输入 1–100 个字符的实验名称';return;}
  const target={...renameTarget};
  $('#rename-error').textContent='';
  $('#rename-progress').textContent='正在保存到 SwanLab，请稍候…';
  setRenameBusy(true);
  try {
    const result=await post('/api/experiments/rename',{path:target.path,name,expected_name:target.expected_name});
    if(!result.ok||!result.experiment||result.experiment.path!==target.path||typeof result.experiment.name!=='string')throw Error('同步结果无法确认，请刷新后查看 SwanLab 名称');
    stateRevision++;
    for(const experiment of allExperiments())if(experiment.path===target.path){Object.assign(experiment,result.experiment);experiment.name_source='cloud';delete experiment.name_sync_error;}
    render();
    setRenameBusy(false);
    $('#rename-dialog').close();
    toast(result.warning?'名称已同步；'+result.warning:'实验名称已同步到 SwanLab');
    await load();
  } catch(error) {
    $('#rename-error').textContent=error.message || '同步失败，请稍后重试';
    $('#rename-progress').textContent=error.status===409?'输入内容已保留。请关闭窗口并刷新，再使用最新名称重试。':'输入内容已保留。';
    setRenameBusy(false);
    $('#rename-name').focus();
  }
});

// Local connection setup
let setupData=null, setupBusy=false, setupRequest=0, pendingRemoval=null;
function selectSetupTab(name) {
  if(setupBusy)return;
  document.querySelectorAll('[data-setup-tab]').forEach(button=>{
    const selected=button.dataset.setupTab===name;
    button.setAttribute('aria-selected',String(selected));
    button.tabIndex=selected?0:-1;
    $('#setup-'+button.dataset.setupTab).hidden=!selected;
  });
  $('.setup-body').scrollTop=0;
}
function setupMessage(id,message,isError=false) {
  const el=$('#'+id);el.textContent=message;el.classList.toggle('error',isError);
}
function setSetupBusy(busy,message='') {
  setupBusy=busy;
  $('#setup-dialog').setAttribute('aria-busy',String(busy));
  $('#setup-dialog').querySelectorAll('button,input,textarea').forEach(el=>el.disabled=busy);
  $('#setup-footer-status').textContent=busy?message:'配置保存在当前电脑';
  if(!busy)$('#copy-public-key').disabled=!setupData?.ssh?.public_key;
}
function renderSwanStatus(account={}) {
  const connected=account.state==='connected';
  const titles={connected:account.username?`已连接 · ${account.username}`:'已连接 SwanLab',not_logged_in:'尚未连接 SwanLab',sdk_missing:'未找到 SwanLab SDK',unavailable:'暂时无法连接',checking:'正在检查连接'};
  $('#swan-account').classList.toggle('connected',connected);
  $('#setup-swan-indicator').classList.toggle('connected',connected);
  $('#swan-account-title').textContent=titles[account.state]||'等待检查连接';
  $('#swan-account-message').textContent=account.message||'读取当前电脑的 SwanLab 登录状态。';
  const sources={tool:'本工具保存的凭据',local:'本工具保存的凭据',saved:'本工具保存的凭据',file:'本工具保存的凭据',system:'系统已有登录',netrc:'系统已有登录',environment:'环境变量',env:'环境变量'};
  $('#swan-account-source').textContent=account.credential_source?`凭据来源：${sources[account.credential_source]||account.credential_source}`:'';
  $('#swan-install-help').hidden=account.state!=='sdk_missing';
  $('#swan-clear-wrap').hidden=!account.configured;
  $('#swan-detected-python').textContent=account.python?`当前使用：${account.python}`:'自动检测本机可用的 Python 环境。';
}
function renderSetup() {
  if(!setupData)return;
  const instances=setupData.instances||[];
  $('#setup-instance-count').textContent=instances.length;
  $('#setup-list-count').textContent=`${instances.length} 台`;
  $('#setup-region-options').innerHTML=(state?.regions||[]).map(r=>`<option value="${esc(r.name)}"></option>`).join('');
  $('#setup-instance-list').innerHTML=instances.length?instances.map(i=>`<article class="setup-instance" data-setup-instance="${esc(i.id)}"><div class="setup-instance-main"><span class="setup-instance-icon">${icon('server')}</span><div><strong>${esc(i.name)}</strong><p>${esc(i.user)}@${esc(i.host)}:${esc(i.port)}</p><small>${esc(i.region)} · 终端连接 <code>autodl ${esc(i.id)}</code></small></div><div class="setup-instance-actions"><button type="button" class="button" data-test-instance="${esc(i.id)}">检测</button><button type="button" class="text-button" data-remove-instance="${esc(i.id)}">移除</button></div></div><p class="setup-feedback" data-instance-feedback="${esc(i.id)}" role="status"></p>${pendingRemoval===String(i.id)?`<div class="setup-confirm"><p>仅从本机列表移除“${esc(i.name)}”？云端实例、文件和训练任务不会被删除或停止。</p><div><button type="button" class="button" data-cancel-remove="${esc(i.id)}">保留</button><button type="button" class="button danger" data-confirm-remove="${esc(i.id)}">确认移除</button></div></div>`:''}</article>`).join(''):'<div class="setup-empty">还没有登记实例。从上方添加第一台机器。</div>';
  $('#setup-registry-path').textContent=setupData.registry_path||'—';
  $('#setup-config-path').textContent=setupData.config_dir||'—';
  const ssh=setupData.ssh||{};
  $('#setup-ssh-status').textContent=!ssh.available?'未找到 OpenSSH，请先在本机安装 SSH 客户端。':!ssh.expect_available?'SSH 已就绪。密码安装公钥需要 expect；也可以使用已授权私钥，或手动添加下方公钥。':'SSH 已就绪，支持密码配置免密登录。';
  $('#setup-ssh-status').classList.toggle('error',!ssh.available);
  $('#setup-public-key-wrap').hidden=!ssh.public_key;
  $('#setup-public-key').value=ssh.public_key||'';
  $('#setup-no-key').hidden=!!ssh.public_key;
  $('#copy-public-key').disabled=setupBusy||!ssh.public_key;
  renderSwanStatus(setupData.swanlab);
}
async function loadSetup() {
  const request=++setupRequest;
  setupMessage('setup-error','');
  setupMessage('setup-loading','正在读取本地配置和账号状态…');
  try {
    const response=await fetch('/api/setup',{signal:AbortSignal.timeout(45000)});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'本地配置读取失败');
    if(request!==setupRequest)return;
    setupData=data;renderSetup();
  } catch(error) {if(request===setupRequest)setupMessage('setup-error',error.message||'读取失败，请重新读取配置。',true);}
  finally {if(request===setupRequest)setupMessage('setup-loading','');}
}
function openSetup() {
  if($('#setup-dialog').open)return;
  $('#instance-password').value='';$('#swan-api-key').value='';
  pendingRemoval=null;
  for(const id of ['setup-error','instance-add-message','swan-login-message'])setupMessage(id,'');
  $('#swan-clear-confirm').hidden=true;
  selectSetupTab('instances');
  if(setupData)renderSetup();
  $('#setup-dialog').showModal();
  $('#setup-tab-instances').focus();
  loadSetup();
}
for(const id of ['setup-button','welcome-setup'])$('#'+id).addEventListener('click',openSetup);
for(const id of ['close-setup','finish-setup'])$('#'+id).addEventListener('click',()=>{if(!setupBusy)$('#setup-dialog').close();});
$('#setup-dialog').addEventListener('cancel',e=>{if(setupBusy)e.preventDefault();});
$('#setup-dialog').addEventListener('close',()=>{
  $('#instance-password').value='';$('#swan-api-key').value='';pendingRemoval=null;
  $('#setup-button').focus({preventScroll:true});
});
$('.setup-tabs').addEventListener('click',e=>{const tab=e.target.closest('[data-setup-tab]');if(tab)selectSetupTab(tab.dataset.setupTab);});
$('.setup-tabs').addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)||setupBusy)return;
  e.preventDefault();
  const name=e.key==='Home'?'instances':e.key==='End'?'swanlab':document.activeElement.dataset.setupTab==='instances'?'swanlab':'instances';
  selectSetupTab(name);$('#setup-tab-'+name).focus();
});
$('#reload-setup').addEventListener('click',loadSetup);
$('#copy-public-key').addEventListener('click',async()=>{
  if(!setupData?.ssh?.public_key)return;
  try {await navigator.clipboard.writeText(setupData.ssh.public_key);toast('SSH 公钥已复制');}
  catch {$('#setup-public-key').focus();$('#setup-public-key').select();toast('请复制已选中的公钥');}
});
$('#instance-add-form').addEventListener('submit',async e=>{
  e.preventDefault();if(setupBusy)return;
  const data={command:$('#instance-command').value.trim(),name:$('#instance-name').value.trim(),region:$('#instance-region').value.trim(),identity_file:$('#instance-key-file').value.trim(),password:$('#instance-password').value};
  $('#instance-password').value='';
  setupMessage('instance-add-message','正在登记实例并验证 SSH 连接…');
  setSetupBusy(true,'正在连接实例，请稍候…');
  try {
    const result=await post('/api/instances/add',data);
    setupMessage('instance-add-message',result.warning||(result.key_installed?'实例已添加，免密连接验证成功。':'实例已登记，可在下方检测连接。'),!!result.warning);
    $('#instance-command').value='';$('#instance-name').value='';
    stateRevision++;
    await loadSetup();await load();
  } catch(error) {setupMessage('instance-add-message',error.message||'添加失败，请检查 SSH 命令后重试。',true);}
  finally {data.password='';setSetupBusy(false);}
});
$('#setup-instance-list').addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button||setupBusy)return;
  if(button.dataset.removeInstance){pendingRemoval=button.dataset.removeInstance;renderSetup();$(`[data-confirm-remove="${CSS.escape(pendingRemoval)}"]`)?.focus();return;}
  if(button.dataset.cancelRemove){const id=button.dataset.cancelRemove;pendingRemoval=null;renderSetup();$(`[data-remove-instance="${CSS.escape(id)}"]`)?.focus();return;}
  const id=button.dataset.testInstance||button.dataset.confirmRemove;if(!id)return;
  const testing=!!button.dataset.testInstance;
  const feedback=()=>document.querySelector(`[data-instance-feedback="${CSS.escape(id)}"]`);
  if(feedback())feedback().textContent=testing?'正在检测免密连接…':'正在从本机列表移除…';
  setSetupBusy(true,testing?'正在检测 SSH 连接…':'正在更新本地实例列表…');
  try {
    const result=await post(testing?'/api/instances/test':'/api/instances/remove',{id:Number(id)});
    if(testing&&feedback()){
      feedback().textContent=result.message||(result.online?'连接成功':'连接失败，请检查实例状态或密钥');
      feedback().classList.toggle('error',!result.online);
    } else {
      pendingRemoval=null;stateRevision++;await loadSetup();await load();toast('已从本机列表移除');
    }
  } catch(error) {if(feedback()){feedback().textContent=error.message;feedback().classList.add('error');}}
  finally {setSetupBusy(false);}
});
async function accountAction(path,data,message) {
  if(setupBusy)return;
  setupRequest++;
  setupMessage('setup-loading','');
  setupMessage('swan-login-message',message);
  setSetupBusy(true,message);
  try {
    const result=await post(path,data);
    if(result.swanlab){setupData={...(setupData||{}),swanlab:result.swanlab};renderSwanStatus(result.swanlab);}
    const connected=result.swanlab?.state==='connected';
    setupMessage('swan-login-message',connected?'SwanLab 连接成功，实验名称将自动同步。':result.swanlab?.message||'配置已更新。',!connected&&result.swanlab?.state==='unavailable');
    $('#swan-clear-confirm').hidden=true;
  } catch(error) {
    if(error.result?.swanlab){setupData={...(setupData||{}),swanlab:error.result.swanlab};renderSwanStatus(error.result.swanlab);}
    setupMessage('swan-login-message',error.message||'连接失败，请重新检查配置。',true);
  } finally {if(data.api_key)data.api_key='';setSetupBusy(false);if(!setupData?.instances)loadSetup();}
}
$('#swan-login-form').addEventListener('submit',async e=>{
  e.preventDefault();if(setupBusy)return;
  const data={api_key:$('#swan-api-key').value.trim(),python:$('#swan-python').value.trim()};
  $('#swan-api-key').value='';
  await accountAction('/api/swanlab/configure',data,'正在验证 SwanLab 账号…');
});
$('#swan-check').addEventListener('click',()=>accountAction('/api/swanlab/check',{},'正在检查 SwanLab 连接…'));
$('#swan-clear').addEventListener('click',()=>{$('#swan-clear-confirm').hidden=false;$('#swan-clear-submit').focus();});
$('#swan-clear-cancel').addEventListener('click',()=>{$('#swan-clear-confirm').hidden=true;$('#swan-clear').focus();});
$('#swan-clear-submit').addEventListener('click',()=>accountAction('/api/swanlab/clear',{},'正在清除本工具保存的凭据…'));
if(location.hash==='#setup')openSetup();
