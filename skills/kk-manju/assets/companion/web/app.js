const token = document.querySelector('meta[name="local-token"]').content;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const labels = {running:'运行中',completed:'执行完成',failed:'执行失败',unknown:'状态待核对',open:'待复核',needs_assistant:'等待助手执行',awaiting_readback:'等待平台回读',verified:'已回读确认',pending:'待审查',superseded:'已由本次整理替代'};
const names = {native_arrange:'原生画布整理',organize_selection:'所选成组',sync_snapshot:'同步画布记录',organize_batches:'按批次整理',native_group_layout:'组内排版'};
const pill = (v,t) => `<span class="pill ${esc(v)}">${esc(t||labels[v]||v)}</span>`;
const clock = t => new Date(t*1000).toLocaleTimeString('zh-CN',{hour12:false});
let state, view='overview', project='', runFilter='', busy=false, batchFilter='全部';
function batchKind(name){if(/V3/.test(name))return 'V3';if(/人物|造型|四视图/.test(name))return '人物';if(/场景|母图|美术/.test(name))return '场景';if(/试片|试做|未采用/.test(name))return '试做';return '其他';}
function batchPanel(snap){const groups=(snap.batches||[]).slice().sort((a,b)=>a.name.localeCompare(b.name,'zh-CN',{numeric:true}));const shown=groups.filter(b=>batchFilter==='全部'||batchKind(b.name)===batchFilter);return `<div class="batch-filters">${['全部','V3','人物','场景','试做','其他'].map(v=>`<button data-batch-filter="${v}" class="${v===batchFilter?'active':''}">${v}</button>`).join('')}</div><div class="batch-grid">${shown.map(b=>`<article class="batch-card"><div class="batch-top"><span class="batch-symbol">▦</span><span>${b.count} 项</span></div><strong>${esc(b.name)}</strong><code>${esc(b.id)}</code></article>`).join('')||empty('暂无对应批次')}</div>`;}
function card(title, body, caption=''){return `<section class="card"><div class="card-title">${esc(title)}<small>${esc(caption)}</small></div>${body}</section>`;}
function empty(message){return `<div class="empty">${esc(message)}</div>`;}
function logs(){const compact=view==='overview';let rows=state.events.filter(e=>(!runFilter||e.run===runFilter)&&(!compact||e.kind!=='log'));rows=rows.slice(compact?-8:-120);if(compact)rows.reverse();return `<div class="log-scroll">${rows.map(e=>`<div class="log ${esc(e.kind)}"><time>${clock(e.time)}</time><pre>${esc(e.message)}</pre></div>`).join('')||empty('暂无执行日志')}</div>`;}
function runRows(limit=100){return state.runs.slice(0,limit).map(r=>`<div class="row"><div class="row-top"><span class="row-title">${esc(r.label)}</span>${pill(r.status)}</div><p>${esc(r.script)}.py · ${clock(r.started)} · ${r.ended?Math.max(0,r.ended-r.started).toFixed(1)+' 秒':'心跳 '+clock(r.heartbeat)}</p><code>${esc(r.id.slice(0,8))} · 版本指纹 ${esc(r.script_hash.slice(0,12))}${r.exit_code!==null?' · 退出码 '+r.exit_code:''}</code><div><button class="link-button" data-run="${esc(r.id)}">查看执行日志 →</button></div>${r.artifacts.map(a=>`<div class="artifact"><div>${esc(a.path.split('/').pop())} ${pill('pending')}</div><code>${esc(a.path)}</code><p>SHA256 ${esc(a.sha256.slice(0,16))}… · ${(a.bytes/1024).toFixed(0)} KB</p></div>`).join('')}</div>`).join('')||empty('尚未通过统一入口执行脚本');}
function issueRows(limit=200){return state.issues.slice(0,limit).map(i=>`<div class="row"><div class="row-top"><span>${esc(i.category)}${i.timecode!==null?' · '+i.timecode.toFixed(2)+'s':''}</span>${pill(i.status)}</div><p>${esc(i.description)}</p><code>${esc(i.node||'项目级问题')} · ${i.basis==='user_report'?'用户报告，尚未验证':'来源 '+esc(i.basis)}</code></div>`).join('')||empty('当前没有待复核问题');}
function render(){
  const active=state.projects.find(p=>p.id===state.project);project=state.project;
  const select=$('#project');
  if([...select.options].map(x=>x.value+'|'+x.textContent).join()!==state.projects.map(x=>x.id+'|'+x.name).join())select.innerHTML=state.projects.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')||'<option value="">尚未登记项目</option>';
  select.value=project;$('#project-name').textContent=active?.name||'等待制作任务';
  const snap=active?.snapshot||{};
  $('#project-note').textContent=snap.nodes!==undefined?`画布记录：${snap.nodes} 个节点 · ${snap.groups} 个分组 · ${snap.edges} 条连线${snap.observedAt?' · 采集于 '+new Date(snap.observedAt).toLocaleString('zh-CN'):''}`:'脚本执行、产物与审查分别记录';
  $('#canvas').hidden=!project;$('#canvas').href=`https://www.liblib.tv/canvas?projectId=${encodeURIComponent(project)}`;
  const live=state.contexts.find(c=>state.serverTime-c.observedAt<30);
  $('#browser-state').textContent=live?`浏览器已连接 · 已选 ${live.selected.length} 项`:'浏览器未接入或已离线';$('#browser-state').className='pill '+(live?'online':'');
  if(live){const version=live.extension?.version,available=state.extensionRelease;$('#browser-state').textContent+=' · 扩展 '+(version||'旧版');if(available?.ready&&live.extension?.buildId!==available.buildId)$('#browser-state').textContent+=version?' · 待自动更新':' · 首次启用需重载';}
  const count=s=>state.runs.filter(r=>r.status===s).length;
  $('#stats').innerHTML=[[count('running'),'正在执行','真实进程心跳'],[count('completed'),'执行完成','仍需检查产物'],[state.issues.filter(i=>i.status==='open').length,'待复核问题','保留来源与时间点'],[state.commands.filter(c=>!['verified','superseded'].includes(c.status)).length,'待处理请求','等待执行或回读']].map(([n,t,d])=>`<div class="stat"><label>${t}</label><strong>${n.toString().padStart(2,'0')}</strong><small>${d}</small></div>`).join('');
  if(view==='overview')$('#content').innerHTML=`<div class="grid"><div>${card('最近执行',runRows(5),'只显示实际调用')}${card('浏览器操作请求',state.commands.slice(0,8).map(c=>`<div class="row"><div class="row-top">${esc(names[c.kind])}${pill(c.status)}</div><p>${clock(c.created)} · ${esc((JSON.parse(c.context||'{}')).batch||c.id.slice(0,8))}</p></div>`).join('')||empty('暂无浏览器操作请求'))}</div><div>${card('需要关注',issueRows(5),'用户反馈')}${card('最近日志',logs())}</div></div>`;
  else if(view==='batches')$('#content').innerHTML=card('画布批次',batchPanel(snap),'平台回读记录');
  else if(view==='scripts')$('#content').innerHTML=`<div class="script-grid">${state.scripts.map(s=>card(s.name,`<div class="row"><div class="row-top"><code>${esc(s.id)}.py</code>${pill(s.running?'running':s.available?'completed':'unknown',s.running?s.running+' 个运行中':s.available?'已安装':'未找到')}</div><p>${esc(s.description)}</p></div>`)).join('')}</div>`;
  else if(view==='issues')$('#content').innerHTML=card('待复核问题',issueRows(),'修复后需重新观看与核对');
  else $('#content').innerHTML=card('执行日志',`${runFilter?'<div class="row"><button class="link-button" id="all-logs">显示全部日志</button></div>':''}${logs()}`,'保留最近记录 · 脱敏显示');
  document.querySelectorAll('[data-batch-filter]').forEach(b=>b.onclick=()=>{batchFilter=b.dataset.batchFilter;render();});
  document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>{runFilter=b.dataset.run;setView('logs');});
  if($('#all-logs'))$('#all-logs').onclick=()=>{runFilter='';render();};
  $('#updated').textContent='更新于 '+clock(state.serverTime);
}
function setView(next){view=next;document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x.dataset.view===next));render();}
document.querySelectorAll('[data-view]').forEach(x=>x.onclick=()=>{runFilter='';setView(x.dataset.view);});
$('#project').onchange=()=>{project=$('#project').value;runFilter='';poll();};
$('#copy-pair').onclick=async()=>{try{await navigator.clipboard.writeText(token);$('#pair-result').textContent='已复制，仅用于本机扩展配对';}catch{$('#pair-result').textContent='复制失败，请在本地 pairing-token 文件读取';}};
async function poll(){if(busy)return;busy=true;try{const res=await fetch('/api/state'+(project?'?project='+encodeURIComponent(project):''),{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(5000)});if(!res.ok)throw new Error('服务返回 '+res.status);state=await res.json();$('#connection').textContent='本地服务在线';$('#connection').className='connection';$('#error').hidden=true;render();}catch(e){$('#connection').textContent='本地服务离线';$('#connection').className='connection offline';$('#error').textContent='连接中断，当前显示的是上次记录。请用 companion.py start 恢复。';$('#error').hidden=false;}finally{busy=false;}}
async function loop(){await poll();setTimeout(loop,document.hidden?15000:2500);}loop();
