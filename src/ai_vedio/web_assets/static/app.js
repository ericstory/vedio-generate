const $ = (selector) => document.querySelector(selector);
const state = { tasks: [], selected: null, timer: null };
const statusMap = {
  queued: ['排队中', 'pending'], pending: ['排队中', 'pending'], processing: ['生成中', 'running'],
  running: ['生成中', 'running'], succeeded: ['已完成', 'success'], failed: ['失败', 'failed'],
  cancelled: ['已取消', 'failed'], expired: ['已过期', 'failed']
};
const modelNames = {
  'wan-2.2-a14b-adult-v2': '自建 V2 · Wan 2.2 成人 LoRA',
  'pinkcherry-ltx-2.3-v1.8': '自建 · PinkCherry LTX 2.3',
  'seedance-2.5': 'Seedance 2.5',
  'seedance-2-mini': 'Seedance 2.0 Mini',
  'seedance-2-fast': 'Seedance 2.0 Fast',
  'seedance-2.0': 'Seedance 2.0'
};
const selfHostedModels = new Set(['pinkcherry-ltx-2.3-v1.8', 'wan-2.2-a14b-adult-v2']);

function escapeHtml(value='') {
  const node = document.createElement('span'); node.textContent = value; return node.innerHTML;
}
function relativeTime(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return '刚刚'; if (seconds < 3600) return `${Math.floor(seconds/60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds/3600)} 小时前`;
  return new Date(iso).toLocaleDateString('zh-CN', {month:'short', day:'numeric'});
}
function statusInfo(value) { return statusMap[value] || ['处理中', 'running']; }
function showToast(message, kind='') {
  const toast = $('#toast'); toast.textContent = message; toast.className = `toast show ${kind}`;
  clearTimeout(toast._timer); toast._timer = setTimeout(() => toast.className='toast', 3200);
}
async function api(url, options={}) {
  const response = await fetch(url, options);
  if (response.status === 401) { location.href = './login'; throw new Error('请重新登录'); }
  let data = {};
  const contentType = response.headers.get('content-type') || '';
  try {
    data = contentType.includes('application/json') ? await response.json() : {detail: await response.text()};
  } catch (_) { data = {}; }
  if (!response.ok) {
    const error = new Error(data.detail || `请求失败（HTTP ${response.status}）`);
    error.payload = data; error.status = response.status; throw error;
  }
  return data;
}
function showRequestError(error) {
  const data=error.payload || {};
  $('#error-title').textContent=data.detail || '请求未通过';
  $('#error-message').textContent=data.guidance?.length ? '生成服务没有创建任务，请根据下方建议调整后重试。' : error.message;
  $('#error-code').textContent=data.code || `HTTP_${error.status || 'UNKNOWN'}`;
  const guidance=data.guidance || ['检查网络连接和输入参数后重试', '如持续失败，请联系管理员查看服务日志'];
  $('#error-guidance').innerHTML=guidance.map(item=>`<li>${escapeHtml(item)}</li>`).join('');
  $('#error-upstream').textContent=data.upstream_message || error.message;
  $('#error-technical').hidden=!(data.upstream_message || error.message);
  $('#error-dialog').hidden=false;
}
function closeError(){ $('#error-dialog').hidden=true; }
function renderTasks() {
  const list = $('#task-list'); $('#task-count').textContent = state.tasks.length;
  if (!state.tasks.length) {
    list.innerHTML = '<div class="task-empty"><div class="empty-icon">✦</div><p>还没有生成任务</p><small>你的创作记录会出现在这里</small></div>'; return;
  }
  list.innerHTML = state.tasks.map(task => {
    const [label, kind] = statusInfo(task.status);
    return `<button class="task-item ${state.selected===task.id?'selected':''}" data-id="${task.id}">
      <span class="task-thumb ${kind}">${kind==='running'?'<i class="mini-loader"></i>':kind==='success'?'▶':'✦'}</span>
      <span class="task-copy"><b>${escapeHtml(task.prompt)}</b><small>${relativeTime(task.created_at)} · ${escapeHtml(modelNames[task.model] || task.model)} · ${task.duration}s · ${task.ratio}</small></span>
      <span class="task-status ${kind}">${label}</span></button>`;
  }).join('');
  list.querySelectorAll('.task-item').forEach(el => el.addEventListener('click', () => openTask(el.dataset.id)));
}
async function loadTasks(silent=false) {
  try {
    const data = await api('./api/tasks'); state.tasks = data.tasks; renderTasks();
    if (state.selected) { const task=state.tasks.find(item=>item.id===state.selected); if(task) renderDetail(task); }
  } catch(err) { if(!silent) showToast(err.message, 'error'); }
}
function openTask(id) {
  state.selected=id; renderTasks(); renderDetail(state.tasks.find(item=>item.id===id));
  $('#composer-view').hidden=true; $('#detail-view').hidden=false; closeSidebar();
}
function renderDetail(task) {
  if (!task) return; const [label, kind]=statusInfo(task.status);
  $('#detail-status').textContent=label; $('#detail-status').className=`status-pill ${kind}`;
  $('#detail-title').textContent=task.prompt;
  $('#detail-meta').innerHTML=`<span>${escapeHtml(modelNames[task.model] || task.model)}</span><span>${task.ratio}</span><span>${task.resolution}</span><span>${task.duration} 秒</span><span>${task.has_reference?'含参考图':'文生视频'}</span>`;
  $('#detail-error').textContent=task.error || '';
  const vote=$('#quality-vote'); vote.hidden=task.status!=='succeeded';
  vote.querySelectorAll('button').forEach(button=>button.classList.toggle('selected', Number(task.quality_vote)===(button.dataset.vote==='up'?1:-1)));
  const container=$('#result-video');
  if(task.status==='succeeded' && task.video_url) container.innerHTML=`<video controls autoplay muted playsinline src="${escapeHtml(task.video_url)}"></video><a class="download-link" href="${escapeHtml(task.video_url)}" target="_blank" rel="noopener">打开原视频 ↗</a>`;
  else if(kind==='failed') container.innerHTML='<div class="result-placeholder failed-mark"><span>!</span><b>生成未完成</b><small>请检查失败原因后重新尝试</small></div>';
  else container.innerHTML='<div class="result-placeholder"><span class="loader"></span><b>正在创作中</b><small>通常需要几分钟，请稍候</small></div>';
}
function resetComposer() { state.selected=null; renderTasks(); $('#detail-view').hidden=true; $('#composer-view').hidden=false; closeSidebar(); $('#prompt').focus(); }
function openSidebar(){ $('#sidebar').classList.add('open'); $('#sidebar-scrim').classList.add('open'); }
function closeSidebar(){ $('#sidebar').classList.remove('open'); $('#sidebar-scrim').classList.remove('open'); }
function syncModelCapabilities() {
  const model=$('#model').value; const selfHosted=selfHostedModels.has(model); const wan=model==='wan-2.2-a14b-adult-v2';
  const referenceControl=$('#reference-control'); const audio=$('#generate-audio');
  referenceControl.classList.toggle('disabled', selfHosted);
  $('#reference').disabled=selfHosted;
  if(selfHosted){ clearReference(); audio.checked=true; audio.disabled=!wan; }
  else { audio.checked=true; audio.disabled=false; }
  $('#audio-control').hidden=selfHosted && !wan;
  $('#model-hint').hidden=!selfHosted;
  // LTX 2.3 MVP is benchmarked at 480p/720p; keep Seedance's 1080p option independent.
  Array.from($('#resolution').options).forEach(option=>{if(option.textContent==='1080p')option.disabled=selfHosted;});
  if(selfHosted && $('#resolution').value==='1080p') $('#resolution').value='720p';
  Array.from($('#ratio').options).forEach(option=>option.disabled=false);
  Array.from($('#duration').options).forEach(option=>option.disabled=false);
  $('#model-hint').innerHTML=wan
    ? '<b>独立云 GPU 质量链路</b> · Wan 2.2 A14B · 成人双 LoRA · 4–15 秒 · 全画幅 · AI 生成音效'
    : '<b>独立云 GPU 链路</b> · LTX 2.3 Distilled · 提示词直接生成同步音视频，首版不含参考图';
}

$('#quality-vote').addEventListener('click', async event=>{
  const button=event.target.closest('[data-vote]'); if(!button || !state.selected) return;
  try { const result=await api(`./api/tasks/${state.selected}/vote`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vote:button.dataset.vote})});
    const index=state.tasks.findIndex(task=>task.id===state.selected); if(index>=0) state.tasks[index]=result.task; renderDetail(result.task); renderTasks(); showToast('评分已记录');
  } catch(err) { showToast(err.message,'error'); }
});

$('#generate-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const form=event.currentTarget; const button=$('#submit-button'); button.disabled=true; button.innerHTML='<i class="mini-loader"></i> 提交中';
  const data=new FormData(form); data.set('generate_audio', data.has('generate_audio') ? 'true' : 'false');
  try {
    const result=await api('./api/tasks', {method:'POST', body:data}); showToast('任务已提交，正在开始创作');
    form.reset(); clearReference(); syncModelCapabilities(); $('#char-count').textContent='0 / 3000'; await loadTasks(true); openTask(result.task.id);
  } catch(err) { showRequestError(err); }
  finally { button.disabled=false; button.innerHTML='<span>✦</span> 开始生成'; }
});
$('#prompt').addEventListener('input', e => $('#char-count').textContent=`${e.target.value.length} / 3000`);
$('#reference').addEventListener('change', event => {
  const file=event.target.files[0]; if(!file) return; if(file.size>30*1024*1024){showToast('参考图不能超过 30MB','error'); clearReference(); return;}
  const objectUrl=URL.createObjectURL(file); const preview=$('#reference-image');
  preview.onload=()=>{
    const ratio=preview.naturalWidth/preview.naturalHeight;
    if(preview.naturalWidth<300 || preview.naturalHeight<300 || preview.naturalWidth>6000 || preview.naturalHeight>6000 || ratio<0.4 || ratio>2.5){
      showRequestError(Object.assign(new Error('参考图片尺寸不符合要求'),{status:422,payload:{detail:'参考图片尺寸不符合要求',code:'INVALID_IMAGE_DIMENSIONS',guidance:['图片宽高均需为 300–6000px','图片宽高比需处于 0.4–2.5 之间','裁剪或缩放图片后重新上传'],upstream_message:`当前图片：${preview.naturalWidth} × ${preview.naturalHeight}px，宽高比 ${ratio.toFixed(2)}`}}));
      URL.revokeObjectURL(objectUrl); clearReference(); return;
    }
    URL.revokeObjectURL(objectUrl);
  };
  preview.src=objectUrl; $('#reference-name').textContent=file.name; $('#reference-preview').hidden=false; $('#reference-guide').hidden=false;
});
function clearReference(){ $('#reference').value=''; $('#reference-image').removeAttribute('src'); $('#reference-preview').hidden=true; $('#reference-guide').hidden=true; }
$('#remove-reference').addEventListener('click', clearReference);
$('#ratio').addEventListener('change', event => { const ratio=event.target.value; $('#ratio-icon').className=`ratio-icon ${['9:16','3:4'].includes(ratio)?'portrait':ratio==='1:1'?'square':'landscape'}`; });
$('#model').addEventListener('change', syncModelCapabilities);
document.querySelectorAll('[data-prompt]').forEach(card => card.addEventListener('click', () => { $('#prompt').value=card.dataset.prompt; $('#prompt').dispatchEvent(new Event('input')); $('#prompt').focus(); window.scrollTo({top:0,behavior:'smooth'}); }));
$('#new-task').addEventListener('click', resetComposer); $('#back-button').addEventListener('click', resetComposer);
$('#sidebar-open').addEventListener('click', openSidebar); $('#sidebar-close').addEventListener('click', closeSidebar); $('#sidebar-scrim').addEventListener('click', closeSidebar);
$('#error-close').addEventListener('click', closeError); $('#error-confirm').addEventListener('click', closeError); $('#error-dialog').addEventListener('click', event=>{if(event.target===$('#error-dialog'))closeError();});
document.addEventListener('keydown', event=>{if(event.key==='Escape')closeError();});
$('#logout').addEventListener('click', async()=>{ try{await api('./api/logout',{method:'POST'});}finally{location.href='./login';} });
syncModelCapabilities(); loadTasks(); state.timer=setInterval(()=>loadTasks(true), 8000);
