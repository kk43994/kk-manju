'use strict';
const $=s=>document.querySelector(s),isExtension=location.protocol==='chrome-extension:',BASE=isExtension?'http://127.0.0.1:18765':'';let token=$('meta[name="local-token"]').content;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const views={library:'漫剧库',episodes:'选集',shots:'整集分镜',player:'单镜播放',compare:'版本比较',editing:'自动剪辑',assets:'参考资产',media:'画布素材',progress:'制作进度',issues:'问题返修',canvas:'画布整理'};
let project=new URLSearchParams(location.search).get('project'),data,view=new URLSearchParams(location.search).get('view')||'library',episodeId=new URLSearchParams(location.search).get('episode')||'',versionId=new URLSearchParams(location.search).get('version')||'',shotId='',previewId='',assetFilter='全部',busy=false,generation=0,controller,urls=[],issueKey=null;
let editJobs=[],editBriefs={},editRequest=null;
let activity=null,activityPolling=false,activityQueued=false,activityOffline=false,activityDirty=false;
let activityFilter='recent',activityKind='all',activityQuery='',activityLimit=12,activityExpanded=new Set();
let loadSequence=0,catalogProblems=[];
let remote={projects:[],media:[],assets:[],folders:[]},links=null,lastSyncId=null,remoteFilter='全部',assetSource='项目参考',remoteController,remotePreview=null;
let issueTarget=null,issueDrafts=new Map(),reviewTargets=new Map();
const embedded=parent!==window;
let panelPort=null,panelClosed=false,panelSafety={action:'safe',safe:false,reasons:['panel_loading']},draftStamp='',draftProjects=new Set();
const linkedCanvas=()=>embedded||(isExtension&&new URLSearchParams(location.search).has('tab'));
const sendParent=(action,extra={})=>{
 if(embedded)parent.postMessage({source:'kk-production',action,...extra},'*');
 else if(isExtension){
  if(action==='safe'){panelSafety={action,...extra};try{panelPort?.postMessage(panelSafety);}catch{}}
  else if(['tools','locate'].includes(action))chrome.runtime.sendMessage({action:'panel_action',kind:action,...extra}).then(r=>{if(!r?.ok)note(r?.error||'画布助手暂未连接');}).catch(e=>note(e.message));
 }
};
function connectPanel(){
 if(!isExtension||embedded||panelClosed)return;
 try{const port=chrome.runtime.connect({name:'kk-production'});panelPort=port;port.postMessage(panelSafety);port.onDisconnect.addListener(()=>{if(panelPort===port)panelPort=null;if(!panelClosed)setTimeout(connectPanel,500);});}catch{if(!panelClosed)setTimeout(connectPanel,1500);}
}
function persistDrafts(){
 try{const value=JSON.stringify([...issueDrafts.values()]);if(value===draftStamp)return;const projects=new Set([project,...issueDrafts.values()].map(x=>typeof x==='string'?x:x?.target?.project).filter(Boolean));for(const p of projects)draftProjects.add(p);for(const p of draftProjects)localStorage.setItem('kk-issue-drafts:'+p,JSON.stringify([...issueDrafts.values()].filter(d=>d.target?.project===p)));draftStamp=value;}catch{}
}
function restoreDrafts(){
 try{const saved=JSON.parse(localStorage.getItem('kk-issue-drafts:'+project)||'[]');for(const d of saved)if(d.target?.project===project&&typeof d.target?.previewId==='string'&&typeof d.description==='string')issueDrafts.set(d.target.previewId,d);}catch{}
 paintIssueDrafts();
}
const stateSafe=()=>{
 const reasons=[];
 if(busy)reasons.push('canvas_action');
 if(issueDrafts.size||Object.values(editBriefs).some(x=>x.trim()))reasons.push('issue_draft');
 if(document.querySelector('dialog[open]'))reasons.push('dialog_open');
 if((document.hasFocus?.()??true)&&document.querySelector('video:not([data-idle])')?.matches(':focus'))reasons.push('editing');
 if([...document.querySelectorAll('video,audio')].some(v=>!v.paused))reasons.push('media_playing');
 persistDrafts();sendParent('safe',{safe:!reasons.length,reasons});
 if(!busy&&!document.querySelector('dialog[open]')){if(activityDirty)paintActivity();if(catalogDirty&&!refreshing&&canRefresh())refreshLive();}
};
async function api(path,body){const r=await fetch(BASE+path,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});const d=await r.json();if(!r.ok)throw Error(d.error||'连接失败');return d;}
function note(message){$('#notice').textContent=message;$('#notice').hidden=!message;}
function selection(){const ep=data?.episodes.find(x=>x.id===episodeId)||data?.episodes[0];const ver=ep?.versions.find(x=>x.id===versionId)||ep?.versions[0];const shot=ver?.shots.find(x=>x.id===shotId)||ver?.shots[0];if(ep)episodeId=ep.id;if(ver)versionId=ver.id;if(shot)shotId=shot.id;return {ep,ver,shot};}
function chosen(s){return s.candidates.find(x=>x.id===s.adopted)||s.candidates.find(x=>x.id===s.preferredCandidate)||s.candidates.find(x=>x.id===s.editCandidate)||s.candidates.find(x=>!(x.namedShots?.length>1))||s.candidates[0];}
function preview(s){return s.candidates.find(x=>x.id===previewId)||chosen(s);}
function image(c,cls=''){if(c?.remoteNode&&!c.poster)return `<img class="${cls}" data-remote-thumb="${esc(c.remoteNode)}" alt="${esc(c.label||'镜头画面')}">`;return c?.poster?`<img class="${cls}" src="${esc(c.poster)}" alt="${esc(c.label||c.name||'镜头画面')}" loading="lazy">`:'<div class="media-placeholder">待生成</div>';}
function chip(text,type=''){return `<span class="chip ${type}">${esc(text)}</span>`;}
const jobLabels={pending:'待生成',claimed:'已领取',submitted:'已提交',running:'生成中',succeeded:'已完成',failed:'失败',unknown:'待核对'};
function durationText(c,s){return c?.duration?c.duration.toFixed(1)+'s':s?.plannedDuration?'计划 '+s.plannedDuration+'s':'时长待读取';}
function status(c,s){if(!c)return chip(jobLabels[s.jobStatus]||'待生成','warn');return [chip(c.label),c.namedShots?.length>1?chip('多镜号片段 · '+c.namedShots.join(' / '),'warn'):'',['claimed','submitted','running','unknown'].includes(s.jobStatus)?chip('新候选 · '+jobLabels[s.jobStatus],'warn'):'',s.adopted===c.id?chip('正式采用','adopted'):'',s.preferredCandidate===c.id?chip('用户偏好'): '',s.editCandidate===c.id?chip('剪辑使用'):'',c.creative==='failed'?chip('需返修','warn'):''].join(' ');}
function filmstrip(ver,shot,compare=false){return `<div class="filmstrip" aria-label="镜头导航">${ver.shots.map(s=>`<button ${compare?'data-compare':'data-shot'}="${s.id}" class="${s.id===shot.id?'active':''}" aria-current="${s.id===shot.id?'true':'false'}" aria-label="${compare?'比较':'播放'} ${s.id}">${image(chosen(s))}<span>${s.id}</span></button>`).join('')}</div>`;}
const flowStatus={pending:'等待领取',claimed:'已领取 · 待外部回执',submitted:'平台已受理',running:'平台生成中',unknown:'结果未知 · 恢复原任务',succeeded:'平台生成完成',failed:'平台生成失败',unverified:'执行证据待核对'};
function flowCards(){return (data.flows||[]).filter(f=>f.episode===episodeId&&f.version===versionId).map(f=>`<article class="flow-card"><div class="actions"><b>${esc(f.shot+' · '+f.label)}</b>${chip(f.taskKind==='local_edit'?(f.status==='succeeded'?'修复完成 · 已挂画布':f.status==='running'?'修复处理及平台审核中':flowStatus[f.status]||f.status):(flowStatus[f.status]||f.status),f.status==='failed'||f.status==='unknown'?'warn':'')}${chip(f.review==='passed'?'助手已审':f.review==='failed'?'审核发现问题':'待审查',f.review==='failed'?'warn':'')}</div><p>${esc(f.reason||'')}</p><div class="flow-stages">${chip(f.receiptVerified?'生成回执已核对':'等待生成回执')}${chip(f.review==='pending'?'待媒体审查':'审查记录已保存')}${chip(f.delivery?.includes('canvas')?'画布已回读':'画布待核对')}${chip(f.delivery?.includes('chat')?'已展示候选':'待展示候选')}</div>${f.reviewNote?`<details><summary>审核与返修依据</summary><p>${esc(f.reviewNote)}</p></details>`:''}${f.taskId?`<small>平台任务 ${esc(f.taskId)}</small>`:''}<div class="actions"><button data-compare="${f.shot}">比较版本</button></div></article>`).join('');}
function libraryItems(){const map=new Map(data.library.map(x=>[x.project,{...x,local:true}]));for(const p of remote.projects||[])map.set(p.uuid,{...(map.get(p.uuid)||{project:p.uuid,episodes:0,local:false}),title:p.name});return [...map.values()];}
const typeLabel={image:'图片',video:'视频',audio:'音频',text:'文本',script:'剧本',storyboard:'分镜'};
function remoteCard(m){return `<button class="tile asset remote-tile" data-remote="${esc(m.id)}"><div class="remote-thumb">${m.hasMedia&&['image','video'].includes(m.type)?`<img data-remote-thumb="${esc(m.id)}" alt="${esc(m.name)}">`:''}<span>${esc(typeLabel[m.type]||'素材')}</span></div><b>${esc(m.name)}</b><small>${esc(m.group||typeLabel[m.type]||'LibTV 素材')}</small></button>`;}
function watchThumbnails(g){const observer=new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;observer.unobserve(entry.target);const img=entry.target;fetch(BASE+'/api/libtv/thumbnail?project='+encodeURIComponent(project)+'&id='+encodeURIComponent(img.dataset.remoteThumb),{headers:{Authorization:'Bearer '+token},signal:controller.signal}).then(r=>{if(!r.ok)throw Error();return r.blob();}).then(blob=>{if(g!==generation)return;const url=URL.createObjectURL(blob);urls.push(url);img.src=url;img.classList.add('ready');}).catch(()=>{});}}, {root:document.querySelector('main'),rootMargin:'100px'});document.querySelectorAll('[data-remote-thumb]').forEach(img=>observer.observe(img));controller.signal.addEventListener('abort',()=>observer.disconnect(),{once:true});}
const empty=t=>`<div class="empty">${esc(t)}</div>`;
function go(next){view=next;note('');$('main').scrollTop=0;render();$('#content').focus?.({preventScroll:true});}
function release(){controller?.abort();document.querySelectorAll('video').forEach(v=>{v.pause();v.removeAttribute('src');v.load();});urls.forEach(URL.revokeObjectURL);urls=[];}
async function videoLoad(video,c,g){
 video.poster=c.poster||'';video.dataset.previewId='';
 if($('#mark-issue'))$('#mark-issue').disabled=true;
 if(!c?.available){note('此候选暂无可播放媒体');return;}
 const requestedProject=project,{ep,ver,shot}=selection();
 try{
  const target=await api('/api/production/preview',{project:requestedProject,media:c.id,episode:ep?.id,version:ver?.id,shot:shot?.id});
  if(g!==generation||requestedProject!==project)return;
  const r=await fetch(BASE+'/api/production/review-media?project='+encodeURIComponent(requestedProject)+'&id='+encodeURIComponent(target.previewId),{headers:{Authorization:'Bearer '+token},signal:controller.signal});
  if(!r.ok)throw Error('原候选暂时不可用');const blob=await r.blob();if(g!==generation||requestedProject!==project)return;
  const url=URL.createObjectURL(blob);urls.push(url);video.src=url;video.dataset.previewId=target.previewId;reviewTargets.set(target.previewId,target);
  if($('#mark-issue'))$('#mark-issue').disabled=false;
  video.addEventListener('play',stateSafe);video.addEventListener('pause',stateSafe);video.addEventListener('ended',stateSafe);
 }catch(e){if(e.name!=='AbortError')note(e.message);}
}
function versionLabel(v){return v?.id==='CANVAS'?'画布镜头':v?.id||'';}
function issueCards(){return (data.issues||[]).map(i=>{
 const t=i.target,scope=t?[t.episode,t.version==='CANVAS'?'画布镜头':t.version,t.shot].filter(Boolean).join(' / '):'节点记录 · 候选未固定';
 return `<article class="issue-row"><div>${chip('待复核','warn')} <b>${esc(i.category)}</b><p>${esc(i.description)}</p><span class="muted">${i.timecode!=null?esc(i.timecode)+'s · ':''}${esc(scope)}</span>${t?`<p>${esc(t.label)}</p><div class="actions"><button data-issue-preview="${esc(i.id)}">打开原审看版本</button></div>`:''}</div></article>`;
}).join('')||empty('没有新增问题标记');}

function render(){
 if(!data)return;rememberActivityState();release();const g=++generation;controller=new AbortController();const {ep,ver,shot}=selection();const c=shot&&preview(shot);
 $('nav').innerHTML=Object.entries(views).filter(([v])=>v!=='player').map(([v,t])=>`<button data-nav="${v}" class="${view===v?'active':''}">${t}</button>`).join('');
 $('#title').textContent=views[view];$('#breadcrumb').textContent=view==='library'?'KK 漫剧':`${data.title||'当前漫剧'}${ep?' / '+ep.name:''}${ver&&view!=='episodes'?' / '+versionLabel(ver):''}`;
 let html='';
 if(view==='library')html=`<div class="filters"><input id="library-search" placeholder="搜索漫剧 / LibTV 项目" aria-label="搜索项目"><span class="muted">${libraryItems().length} 个项目</span></div><div class="grid poster-grid">${libraryItems().map(show=>`<button class="tile poster" data-open-show="${show.project}" data-search="${esc(show.title.toLowerCase())}">${show.poster?image(show):'<div class="project-placeholder">LibTV 项目</div>'}<b>${esc(show.title)}</b><small>${show.local?show.episodes+' 集 · 本地制作记录':'LibTV 项目 · 点击查看素材'}</small></button>`).join('')||empty('点击同步 LibTV 读取项目库')}</div>`;
 else if(view==='episodes')html=`<div class="grid">${data.episodes.map(e=>`<button class="tile" data-episode="${e.id}">${image(chosen(e.versions[0].shots[0]))}<b>${esc(e.name)}</b><small>${e.versions.length} 个制作版本 · ${e.versions[0].shots.length} 个镜头 · ${e.versions[0].shots.filter(x=>x.candidates.length).length} 镜可播放</small></button>`).join('')||empty('此漫剧尚未导入集数记录')}</div>`;
 else if(view==='shots')html=!ver?empty('请先选择剧集'):`<div class="actions"><button data-nav="editing" class="primary">自动剪辑到 CapCut</button></div><div class="filters">${ep.versions.map(v=>`<button data-version="${v.id}" class="${v.id===versionId?'active':''}">${esc(v.id==='CANVAS'?'画布镜头':v.id+' '+v.name)}</button>`).join('')}</div>${ver.shots.map(s=>{const v=chosen(s);return `<article class="shot-row"><button data-shot="${s.id}" aria-label="播放 ${s.id}">${image(v)}<span class="duration">${durationText(v,s)}</span></button><div class="shot-info"><h2>${esc(s.id)} · ${esc(s.title)}</h2>${status(v,s)}<p>${s.candidates.length?s.candidates.length+' 个候选版本':esc(jobLabels[s.jobStatus]||'待生成')}${s.reuseNote?' · '+esc(s.reuseNote):''}</p><div class="actions"><button data-shot="${s.id}">播放审看</button><button data-compare="${s.id}">比较版本</button></div></div></article>`;}).join('')}`;
 else if(view==='player'&&shot&&c)html=`<div class="player"><video controls playsinline preload="metadata" id="main-video" aria-label="${esc(shot.id+' '+c.label)}"></video></div><div class="player-meta"><div><b>${esc(shot.id+' · '+shot.title)}</b><div>${status(c,shot)}</div></div><div class="actions"><button id="mark-issue">标记问题</button><button data-compare="${shot.id}">比较版本</button><button class="primary" id="adopt" ${shot.adopted===c.id?'disabled':''}>${shot.adopted===c.id?'已采用':'采用 '+esc(c.label)}</button></div></div><div class="filters">${shot.candidates.map(x=>`<button data-candidate="${x.id}" class="${x.id===c.id?'active':''}">预览 ${esc(x.label)}</button>`).join('')}</div>${filmstrip(ver,shot)}<details class="detail"><summary>审查依据与制作记录</summary><p>${esc(c.reviewNote||'尚无创意审查记录')}</p><p>采用仅更新本地制作记录；画布同步另行执行。</p></details>`;
 else if(view==='compare'&&shot&&shot.candidates.length){html=`<div class="compare">${[0,1].map((side)=>`<section><select id="compare-${side}" aria-label="${side?'右':'左'}侧候选">${shot.candidates.map((x,i)=>`<option value="${x.id}" ${i===(side?Math.max(0,shot.candidates.length-1):0)?'selected':''}>${esc(x.label)}${x.namedShots?.length>1?' · 多镜号片段':''}${shot.adopted===x.id?' · 已采用':shot.preferredCandidate===x.id?' · 用户偏好':''}</option>`).join('')}</select><div class="player"><video id="video-${side}" controls playsinline preload="metadata"></video></div><div class="actions player-meta"><button data-mark-side="${side}">标记问题</button><button data-adopt-side="${side}" class="primary">采用此候选</button></div></section>`).join('')}</div><p class="muted">${esc(shot.id+' · '+shot.title)} · 左右切换只改变预览</p><button data-shot="${shot.id}">返回单镜审看</button>${filmstrip(ver,shot,true)}`;}
 else if(['player','compare'].includes(view)&&shot)html=empty(shot.id+' · '+(jobLabels[shot.jobStatus]||'待生成')+'，暂无可播放候选')+filmstrip(ver,shot,view==='compare');
 else if(view==='editing')html=editingView(ep,ver);
 else if(view==='assets')html=`<div class="filters">${['项目参考','LibTV资产库'].map(x=>`<button data-asset-source="${x}" class="${assetSource===x?'active':''}">${x}</button>`).join('')}</div>`+(assetSource==='LibTV资产库'?`<p class="muted">${remote.assetCount||0} 个个人资产</p><div class="grid">${remote.assets.map(remoteCard).join('')||empty('尚未同步个人资产库')}</div>`:`<div class="filters">${['全部','人物','场景','道具','色卡','声音'].map(x=>`<button data-filter="${x}" class="${assetFilter===x?'active':''}">${x}</button>`).join('')}</div><div class="grid">${data.assets.filter(a=>assetFilter==='全部'||a.category===assetFilter).map(a=>`<article class="tile asset">${image(a)}<b>${esc(a.name)}</b><small>${esc(a.category+' · '+a.status)}</small></article>`).join('')||empty('此分类尚未接入本地素材')}</div>`);
 else if(view==='media')html=`<div class="filters">${['全部','图片','视频','音频','文本','剧本','分镜'].map(x=>`<button data-remote-filter="${x}" class="${remoteFilter===x?'active':''}">${x}</button>`).join('')}<span class="muted">${remote.media.length} 项 · 当前项目</span></div><div class="grid">${remote.media.filter(m=>remoteFilter==='全部'||typeLabel[m.type]===remoteFilter).map(remoteCard).join('')||empty('此项目尚无已同步素材，点击上方同步 LibTV')}</div>`;
 else if(view==='progress')html=activityShell()+(ver?`<details class="detail"><summary>当前集数镜头 · ${esc(ep.name+' / '+versionLabel(ver))}</summary>${flowCards()}<div class="progress-row header"><span>镜头</span><span>平台生成</span><span>技术检查</span><span>创意审核</span><span>正式采用</span></div>${ver.shots.map(s=>{const x=chosen(s);return `<div class="progress-row"><span>${image(x)}${s.id}</span><span>${chip(['claimed','submitted','running','unknown'].includes(s.jobStatus)?'新候选 · '+jobLabels[s.jobStatus]:x?.platform==='succeeded'?'已完成':(jobLabels[s.jobStatus]||'待核对'))}</span><span>${chip(x?.technical==='decode_passed'?'完整解码通过':x?.technical==='metadata_checked'?'规格已读':'待检查')}</span><span>${chip(['passed','user_accepted'].includes(x?.creative)?'历史已审':x?.creative==='failed'?'需返修':'待审',x?.creative==='failed'?'warn':'')}</span><span>${chip(s.adopted?'已采用':'待采用',s.adopted?'adopted':'')}</span></div>`;}).join('')}</details>`:'');
 else if(view==='issues')html=flowCards()+issueCards();
 else if(view==='canvas')html=`<div class="filters"><button class="active">${esc(data.title||'漫剧')}</button>${ep?`<button>${esc(ep.name)}</button><button>${esc(ver.id)}</button>`:''}</div><p class="muted">原画布分组 · 仅定位，保留原件与引用</p>${data.batches.map(b=>`<div class="group-row"><div>${esc(b.name)}<small>${b.count} 项</small></div><button data-locate="${esc(b.id)}" ${!embedded?'disabled':''}>定位</button></div>`).join('')||empty('尚无已同步分组')}<div class="actions player-meta"><button id="open-tools">打开原生整理工具</button></div>`;
 else html=empty('请先选择剧集和镜头');
 $('#content').innerHTML=html;
 document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>go(b.dataset.nav));
 document.querySelectorAll('[data-open-show]').forEach(b=>b.onclick=async()=>{project=b.dataset.openShow;episodeId='';versionId='';shotId='';previewId='';activityFilter='recent';activityKind='all';activityQuery='';activityLimit=12;activityExpanded.clear();await load(false);go(data?.episodes.length?'episodes':'media');pollActivity();updateConnections();});
 document.querySelectorAll('[data-episode]').forEach(b=>b.onclick=()=>{episodeId=b.dataset.episode;versionId='';shotId='';go('shots');});
 document.querySelectorAll('[data-version]').forEach(b=>b.onclick=()=>{versionId=b.dataset.version;shotId='';previewId='';go(view==='editing'?'editing':'shots');});
 document.querySelectorAll('[data-shot]').forEach(b=>b.onclick=()=>{shotId=b.dataset.shot;previewId='';go('player');});
 document.querySelectorAll('[data-issue-preview]').forEach(b=>b.onclick=()=>{const i=data.issues.find(x=>x.id===b.dataset.issuePreview);if(i?.target)openRemote(i.target.previewId,null,i.timecode);});
 document.querySelectorAll('[data-mark-side]').forEach(b=>b.onclick=()=>markVideo($('#video-'+b.dataset.markSide)));
 document.querySelectorAll('[data-compare]').forEach(b=>b.onclick=()=>{shotId=b.dataset.compare;go('compare');});
 document.querySelectorAll('[data-asset-source]').forEach(b=>b.onclick=()=>{assetSource=b.dataset.assetSource;render();});
 document.querySelectorAll('[data-remote-filter]').forEach(b=>b.onclick=()=>{remoteFilter=b.dataset.remoteFilter;render();});
 document.querySelectorAll('[data-remote]').forEach(b=>b.onclick=()=>openRemote(b.dataset.remote));
 if($('#library-search'))$('#library-search').oninput=e=>document.querySelectorAll('[data-search]').forEach(b=>b.hidden=!b.dataset.search.includes(e.target.value.toLowerCase()));
 watchThumbnails(g);
 document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{assetFilter=b.dataset.filter;render();});
 document.querySelectorAll('[data-candidate]').forEach(b=>b.onclick=()=>{previewId=b.dataset.candidate;render();});
 document.querySelectorAll('[data-locate]').forEach(b=>b.onclick=()=>sendParent('locate',{batch:data.batches.find(x=>x.id===b.dataset.locate),project}));
 if($('#open-tools'))$('#open-tools').onclick=()=>linkedCanvas()?sendParent('tools',{project}):note('请在 LibTV 扩展中使用原生整理工具');
 if(view==='player'&&shot&&c){videoLoad($('#main-video'),c,g);$('#adopt').onclick=()=>adopt(c);$('#mark-issue').onclick=()=>markVideo($('#main-video'));}
 if(view==='compare'&&shot&&shot.candidates.length){for(let i=0;i<2;i++){const load=()=>{const x=shot.candidates.find(x=>x.id===$('#compare-'+i).value);videoLoad($('#video-'+i),x,g);};load();$('#compare-'+i).onchange=()=>{goCompareReload(i);};}document.querySelectorAll('[data-adopt-side]').forEach(b=>b.onclick=()=>adopt(shot.candidates.find(x=>x.id===$('#compare-'+b.dataset.adoptSide).value)));}
 if(view==='compare'||view==='player')requestAnimationFrame(()=>{const strip=document.querySelector('.filmstrip'),active=strip?.querySelector('.active');if(active)strip.scrollLeft=active.offsetLeft-strip.offsetLeft-(strip.clientWidth-active.clientWidth)/2;});
 if($('#edit-brief'))$('#edit-brief').oninput=e=>{editBriefs[editScope()]=e.target.value;try{localStorage.setItem('kk-edit-brief:'+editScope(),e.target.value);}catch{}stateSafe();};
 if($('#start-edit'))$('#start-edit').onclick=startEdit;
 if($('#activity-search'))$('#activity-search').oninput=e=>{activityQuery=e.target.value;activityLimit=12;paintActivity();};
 if($('#activity-kind'))$('#activity-kind').onchange=e=>{activityKind=e.target.value;activityLimit=12;paintActivity();};
 paintActivity();stateSafe();
}
function goCompareReload(side){const ids=[$('#compare-0').value,$('#compare-1').value];release();controller=new AbortController();const g=++generation;const {shot}=selection();ids.forEach((id,i)=>videoLoad($('#video-'+i),shot.candidates.find(x=>x.id===id),g));}
async function adopt(c){if(busy)return;busy=true;stateSafe();const {ep,ver,shot}=selection();try{await api('/api/production/adopt',{id:crypto.randomUUID(),project,episode:ep.id,version:ver.id,shot:shot.id,candidate:c.id,expectedRevision:shot.choiceRevision});await load(false);render();note('已采用 '+c.label+' · 本地制作记录已保存');}catch(e){note(e.message);}finally{busy=false;stateSafe();}}
function markVideo(video){const target=reviewTargets.get(video?.dataset.previewId);if(!target){note('请等待候选加载后再标记问题');return;}video.pause();openIssue(target,video.currentTime);}
function issueDraft(){const f=$('#issue-form');return {id:issueKey,target:issueTarget,timecode:f.elements.timecode.value,category:f.elements.category.value,description:f.elements.description.value};}
function paintIssueDrafts(){
 const el=$('#review-drafts');el.hidden=!issueDrafts.size;
 el.innerHTML=issueDrafts.size?'<b>未保存的问题草稿</b> '+[...issueDrafts.values()].map(d=>`<button data-resume-issue="${esc(d.target.previewId)}">继续 ${esc(d.target.label)}</button>`).join(''):'';
 el.querySelectorAll('[data-resume-issue]').forEach(b=>b.onclick=()=>{const d=issueDrafts.get(b.dataset.resumeIssue);if(d)openIssue(d.target);});
}
function rememberIssue(){if(issueTarget&&$('#issue-form').elements.description.value.trim())issueDrafts.set(issueTarget.previewId,issueDraft());else if(issueTarget)issueDrafts.delete(issueTarget.previewId);paintIssueDrafts();stateSafe();}
function openIssue(target,timecode){
 issueTarget={...target};const d=issueDrafts.get(target.previewId),f=$('#issue-form');f.reset();issueKey=d?.id||crypto.randomUUID();
 f.elements.timecode.value=d?.timecode??(Number.isFinite(timecode)?timecode.toFixed(2):'');f.elements.description.value=d?.description||'';if(d)f.elements.category.value=d.category;
 $('#issue-context').textContent=[target.episode,target.shot,target.label].filter(Boolean).join(' · ');$('#issue-error').textContent='';$('#issue-dialog').showModal();stateSafe();
}
function closeIssue(discard=false){if(busy)return;if(discard&&issueTarget)issueDrafts.delete(issueTarget.previewId);else rememberIssue();$('#issue-dialog').close();issueTarget=null;paintIssueDrafts();stateSafe();}
$('#cancel-issue').onclick=()=>closeIssue();$('#discard-issue').onclick=()=>closeIssue(true);
$('#issue-dialog').addEventListener('cancel',e=>{e.preventDefault();closeIssue();});$('#issue-form').oninput=rememberIssue;
window.addEventListener('beforeunload',e=>{if(issueDrafts.size||Object.values(editBriefs).some(x=>x.trim())||busy){e.preventDefault();e.returnValue='';}});
$('#issue-form').onsubmit=async e=>{
 e.preventDefault();if(busy||!issueTarget)return;rememberIssue();
 const target={...issueTarget},f=e.currentTarget,payload={id:issueKey,project:target.project,previewId:target.previewId,timecode:f.elements.timecode.value===''?null:Number(f.elements.timecode.value),category:f.elements.category.value,description:f.elements.description.value.trim()};
 busy=true;f.querySelectorAll('input,select,textarea,button').forEach(x=>x.disabled=true);stateSafe();
 try{await api('/api/production/issues',payload);issueDrafts.delete(target.previewId);paintIssueDrafts();$('#issue-dialog').close();issueTarget=null;f.reset();await load(false);note('问题已保存到原审看版本 · 待复核');}
 catch(e){$('#issue-error').textContent=e.message;}
 finally{busy=false;f.querySelectorAll('input,select,textarea,button').forEach(x=>x.disabled=false);stateSafe();}
};

async function load(repaint=true){
 const sequence=++loadSequence;
 try{
  if(!project){const r=await fetch(BASE+'/api/state',{headers:{Authorization:'Bearer '+token}});const s=await r.json();if(sequence!==loadSequence)return;project=s.project;}
  if(activity?.project!==project){activity=null;paintActivity();}
  const requestedProject=project;
  if(isExtension){$('#full-panel').hidden=false;$('#full-panel').href=BASE+'/production?project='+encodeURIComponent(project)+'&view='+encodeURIComponent(view);}
  if(data?.project!==project){data={project,episodes:[],assets:[],batches:[],issues:[],flows:[],runs:[],library:[]};remote={projects:[],media:[],assets:[],folders:[]};editJobs=[];restoreDrafts();if(repaint)render();}
  connectChanges();
  const results=await Promise.allSettled([api('/api/production',{project,includePending:true}),api('/api/libtv/catalog',{project}),api('/api/capcut/jobs',{project})].map((request,i)=>request.then(value=>{
   if(project!==requestedProject||sequence!==loadSequence)return value;
   if(i===0)data=value;else if(i===1)remote=value;else editJobs=value.jobs;
   // Each useful section paints as soon as it arrives. A slow optional endpoint
   // never holds the task list or project navigation behind Promise.all.
   if(repaint&&canRefresh())renderKeepingPosition();
   return value;
  })));
  if(project!==requestedProject||sequence!==loadSequence)return;
  catalogProblems=[];
  results.forEach((r,i)=>{if(r.status==='fulfilled'){if(i===0)data=r.value;else if(i===1)remote=r.value;else editJobs=r.value.jobs;}else catalogProblems.push(['集数目录','画布素材','剪辑记录'][i]+'暂未更新');});
  data.title=remote.projects.find(x=>x.uuid===project)?.name||data.title||'当前项目';
  if(catalogProblems.length)note(catalogProblems.join('；')+' · 已有记录保留，自动重试');
  else if($('#notice').textContent.includes('暂未更新'))note('');
  if(repaint&&canRefresh())renderKeepingPosition();
 }catch(e){note('制作目录暂未更新，将自动重试 · '+e.message);}
}
$('#refresh').onclick=()=>{if(!busy&&!document.querySelector('dialog[open]')){load();pollActivity();}};$('#collapse').hidden=!embedded;$('#collapse').onclick=()=>{release();sendParent('collapse');};$('#canvas-tools').onclick=()=>linkedCanvas()?sendParent('tools',{project}):go('canvas');
window.addEventListener('message',e=>{if(e.source===parent&&['https://www.liblib.tv','https://liblib.tv',location.origin].includes(e.origin)&&e.data?.action==='kk-pause'){document.querySelectorAll('video,audio').forEach(v=>v.pause());stateSafe();}});
setInterval(()=>{if(isExtension&&!embedded)stateSafe();},20000);
for(const event of ['focusin','focusout','focus','blur'])window.addEventListener(event,()=>setTimeout(stateSafe,0));
window.addEventListener('beforeunload',()=>{panelClosed=true;feedController?.abort();persistDrafts();release();});async function boot(){await Promise.resolve();try{if(isExtension){const settings=await chrome.storage.local.get('pairToken');token=settings.pairToken||'';$('#extension-settings').hidden=false;$('#extension-settings').onclick=()=>chrome.runtime.sendMessage({action:'open_settings'});$('#monitor-link').href=BASE+'/';if(!project){const [tab]=await chrome.tabs.query({active:true,lastFocusedWindow:true});const u=new URL(tab?.url||'https://invalid.local');if(u.protocol==='https:'&&['www.liblib.tv','liblib.tv'].includes(u.hostname)&&u.pathname==='/canvas'){project=u.searchParams.get('projectId');const here=new URL(location.href);here.searchParams.set('project',project);here.searchParams.set('tab',tab.id);history.replaceState(null,'',here);}}connectPanel();}sendParent('ready');await Promise.all([load(),pollActivity(),updateConnections()]);}catch(e){note('制作面板未就绪 · '+e.message);sendParent('ready');}finally{stateSafe();}}boot();

const activityKinds={image:'图片',video:'视频',audio:'音频',voice:'配音',local_edit:'本地处理'};
const activityStatuses={pending:'待领取',claimed:'已领取 · 等待提交',submitted:'平台已受理',running:'平台执行中',succeeded:'生成完成',failed:'执行失败',unknown:'结果未知 · 待恢复核对',unverified:'回执待核对'};
const activityFilterNames={recent:'最近更新',active:'执行中',review:'待审查',delivery:'待交付',attention:'待核对',all:'全部'};
const activityActive=j=>['claimed','submitted','running'].includes(j.status);
const evidenceTime=j=>Math.max(j.updatedAt||0,j.canvas?.checkedAt||0);
const activityRecent=j=>activityActive(j)&&!j.stale&&Date.now()/1000-evidenceTime(j)<86400;
const activityCheck=j=>j.stale||['failed','unknown','unverified'].includes(j.status)||j.review==='failed'||(activityActive(j)&&!activityRecent(j));
const activityDelivery=j=>j.status==='succeeded'&&j.review==='passed'&&!j.stale&&(j.requiredDelivery||[]).some(k=>!(j.delivery||[]).includes(k));
const activityMatch=(j,filter)=>filter==='active'?activityRecent(j):filter==='review'?j.status==='succeeded'&&j.review==='pending'&&!j.stale:filter==='delivery'?activityDelivery(j):filter==='attention'?activityCheck(j):true;
function activityResults(){
 const query=activityQuery.trim().toLowerCase();
 return (activity?.jobs||[]).filter(j=>activityMatch(j,activityFilter)&&(activityKind==='all'||j.kind===activityKind)&&(!query||[j.label,j.job,j.plan,j.revision,j.taskId,...(j.canvas?.nodes||[]).map(n=>n.name)].join(' ').toLowerCase().includes(query))).sort((a,b)=>(activityFilter==='recent'?(Number(activityRecent(b))-Number(activityRecent(a))):0)||b.updatedAt-a.updatedAt||a.id.localeCompare(b.id));
}
const activityDate=value=>value?new Date(value*1000).toLocaleString('zh-CN'):'时间未知';
function rememberActivityState(){document.querySelectorAll('details[data-activity-id]').forEach(d=>d.open?activityExpanded.add(d.dataset.activityId):activityExpanded.delete(d.dataset.activityId));}
function activityShell(){return `<section id="browser-requests" aria-label="画布请求"></section><section id="activity-jobs"><h2>项目任务</h2><p class="muted">本地计划与 LibTV 画布按平台任务对齐。生成、审查、交付和正式采用分别记录。</p><div id="activity-filters" class="filters" aria-label="任务状态筛选"></div><div class="activity-search"><input id="activity-search" type="search" value="${esc(activityQuery)}" placeholder="搜索镜号、任务名、批次或任务 ID" aria-label="搜索制作任务"><select id="activity-kind" aria-label="制作任务类型">${[['all','全部类型'],...Object.entries(activityKinds)].map(([k,v])=>`<option value="${k}" ${activityKind===k?'selected':''}>${v}</option>`).join('')}</select></div><p id="activity-count" class="muted"></p><div id="activity-list"></div><details class="detail" data-activity-id="scripts"><summary>最近脚本执行</summary><div id="activity-runs"></div></details></section>`;}
function activityCards(jobs){return jobs.map(j=>{
 const old=activityActive(j)&&!activityRecent(j),origin=j.source==='canvas'?'LibTV 画布回读':j.statusSource==='canvas'?'本地计划 · 画布结果已对齐':'本地制作计划';
 const preview=(j.canvas?.nodes||[]).find(n=>n.hasMedia);
 return `<article class="activity-card"><div class="actions"><b>${esc(j.label)}</b>${chip(activityKinds[j.kind]||j.kind)}${chip(j.stale?'上次状态 · 待核对':old?'长期未核对 · '+(activityStatuses[j.status]||j.status):activityStatuses[j.status]||j.status,activityCheck(j)?'warn':'')}${j.status==='succeeded'?chip(j.review==='passed'?'助手已审':j.review==='failed'?'需返修':j.source==='canvas'?'尚无本地审查记录':'待审查',j.review==='failed'?'warn':''):''}</div><p class="muted">${esc(j.plan)}${j.revision?' · '+esc(j.revision):''}<br>${esc(origin)} · 任务更新 ${esc(activityDate(j.updatedAt))}${j.canvas?' · 画布核对 '+esc(activityDate(j.canvas.checkedAt)):''}</p>${old?'<p class="muted">超过 24 小时没有新证据，保留原任务状态，等待核对结果。</p>':''}${j.reason?`<p>${esc(j.reason)}</p>`:''}${j.statusSource==='canvas'&&j.recordedStatus!==j.status?`<p class="muted">本地原记录：${esc(activityStatuses[j.recordedStatus]||j.recordedStatus)}；同一平台任务已回读更新。</p>`:''}${j.status==='succeeded'?`<div class="flow-stages">${(j.requiredDelivery||[]).map(k=>chip(({canvas:'画布',chat:'结果卡'}[k]||k)+(j.delivery.includes(k)?'交付已记录':'待交付'))).join('')}</div>`:''}${j.reviewNote?`<details data-activity-id="${esc(j.id)}" ${activityExpanded.has(j.id)?'open':''}><summary>审查记录</summary><p>${esc(j.reviewNote)}</p></details>`:''}${j.taskId?`<small>平台任务 ${esc(j.taskId)}</small>`:''}${preview?`<div class="actions"><button data-activity-preview="${esc(j.id)}">${preview.kind==='image'?'查看图片':preview.kind==='audio'?'试听音频':'播放审看'}</button></div>`:''}</article>`;
 }).join('');}
const requestNames={native_arrange:'原生整理画布',native_group_layout:'原生组内排版',organize_selection:'所选节点成组',organize_batches:'按批次整理',sync_snapshot:'同步画布目录'};
const requestStates={needs_assistant:'等待 Codex 接续',awaiting_readback:'等待平台回读',verified:'回读已登记',superseded:'已由后续操作替代',unknown:'请求待核对'};
const requestPending=r=>!['verified','superseded'].includes(r.status);
function requestCards(rows){return rows.map(r=>`<article class="activity-card"><div class="actions"><b>${esc(requestNames[r.kind]||'画布请求')}</b>${chip(requestStates[r.status]||'请求待核对',requestPending(r)?'warn':'')}</div><p>${esc(activityDate(r.createdAt))} · 原始选择 ${r.selectedCount} 项${r.batch?' · '+esc(r.batch):''}</p>${r.observation?`<p class="muted">页面观察：${esc(r.observation)}</p>`:''}${r.hasReadback?`<p class="muted">回读记录已保存${r.evidenceArchived?' · 证据副本已保留':''}</p>`:''}<small>请求 ${esc(r.id)}</small>${requestPending(r)?`<div class="actions"><button data-request-handoff="${esc(r.id)}">交给 Codex 继续</button></div>`:''}</article>`).join('');}
function requestActivity(){
 const requests=activity?.requests||[],pending=requests.filter(requestPending),done=requests.filter(r=>!requestPending(r)),total=Object.values(activity?.requestCounts||{}).reduce((a,b)=>a+b,0);
 return `<details class="detail" data-activity-id="requests-list" ${activityExpanded.has('requests-list')||pending.some(r=>Date.now()/1000-r.createdAt<86400)?'open':''}><summary>画布请求${pending.length?' · '+(activity.summary.requestsPending||pending.length)+' 条待接续':''}</summary><p class="muted">保留原项目和选择范围。需要助手的请求会等待你交给当前 Codex 对话；点击原生控件后，仍须回读确认保存。</p>`+(pending.length?requestCards(pending.slice(0,8)):empty('暂无待接续的画布请求'))+(pending.length>8?`<details class="detail" data-activity-id="more-requests"><summary>其余 ${pending.length-8} 条待接续请求</summary>${requestCards(pending.slice(8))}</details>`:'')+(done.length?`<details class="detail" data-activity-id="request-history"><summary>已处理记录 · ${done.length} 条</summary>${requestCards(done)}</details>`:'')+(total>requests.length?`<p class="muted">显示 ${requests.length} / ${total} 条，未完成请求优先。完整记录保留在本机。</p>`:'')+'</details>';
}
function handoffText(r){
 return `请接续 KK 漫剧的这条浏览器请求。\n项目 ID：${r.project}\n请求 ID：${r.id}\n操作：${requestNames[r.kind]||r.kind}\n当前状态：${requestStates[r.status]||'待核对'}\n\n请先从本机 companion 的 pending 记录读取这个 ID 的原始项目、节点和上下文，并核对该请求尚未处理。${r.status==='awaiting_readback'?'这是已请求原生操作的回读任务，请通过 LibTV 检查现有结果，不要再次执行整理。':'严格沿用原请求的范围；无法确定时保留现状并说明缺少的依据。'}保存实际回读证据后，登记该请求的回执。不要重新提交生成、自动采用素材或处理其他请求。`;
}
function openHandoff(id){const r=activity?.requests?.find(r=>r.id===id&&r.project===project);if(!r||!requestPending(r))return;$('#request-text').value=handoffText(r);$('#request-copy-status').textContent='复制后粘贴到当前 Codex 对话；这里不会自动发送消息。';$('#request-dialog').showModal();stateSafe();}
$('#copy-request').onclick=async()=>{try{await navigator.clipboard.writeText($('#request-text').value);$('#request-copy-status').textContent='说明已复制，请粘贴到当前 Codex 对话。';}catch{$('#request-text').focus();$('#request-text').select();$('#request-copy-status').textContent='自动复制不可用，说明已选中，请手动复制。';}};
$('#close-request').onclick=()=>{$('#request-dialog').close();stateSafe();};$('#request-dialog').addEventListener('cancel',()=>setTimeout(stateSafe,0));
let requestsElement=null,requestsRendered='';
function paintActivity(){
 const banner=$('#activity-summary'),target=$('#activity-list');if(!banner)return;
 if(!activity||activity.project!==project){banner.hidden=!activityOffline;banner.textContent=activityOffline?'进度连接中断 · 正在重试，请检查本机服务':' ';if(target)target.innerHTML=empty(activityOffline?'暂时无法读取项目任务，正在重试':'正在读取项目任务…');return;}
 const s=activity.summary,latest=activity.jobs.reduce((best,j)=>!best||j.updatedAt>best.updatedAt?j:best,null),run=activity.runs[0],counts=Object.fromEntries(Object.keys(activityFilterNames).map(k=>[k,activity.jobs.filter(j=>activityMatch(j,k)).length]));
 banner.hidden=!activity.jobs.length&&!activity.runs.length&&!activity.requests?.length&&!activityOffline;
 banner.innerHTML=`<div><b>${activityOffline?'进度连接中断 · 保留上次记录':`制作进度 · 执行中 ${counts.active} 项 / ${s.scriptsRunning} 个脚本运行${s.requestsPending?' / '+s.requestsPending+' 条画布请求待接续':''}`}</b><p>${esc(latest?latest.label+' · '+(activityStatuses[latest.status]||latest.status):run?run.label:'尚无已登记任务')}${activityOffline?'':' · 本机记录变化自动更新'}</p></div><button id="open-activity">查看制作进度${counts.attention?' · '+counts.attention+' 项待核对':''}</button>`;
 $('#open-activity').onclick=()=>go('progress');
 if(target){
  rememberActivityState();
  const top=$('main').scrollTop,rows=activityResults(),focused=document.activeElement,focusedFilter=focused?.dataset?.activityFilter;
  const requestElement=$('#browser-requests'),requestRevision=JSON.stringify([activity.requests,activity.requestCounts]),requestFocus=focused?.dataset?.requestHandoff;
  if(requestElement!==requestsElement||requestRevision!==requestsRendered){
   requestElement.innerHTML=requestActivity();requestsElement=requestElement;requestsRendered=requestRevision;
   document.querySelectorAll('[data-request-handoff]').forEach(b=>b.onclick=()=>openHandoff(b.dataset.requestHandoff));
   if(requestFocus){const next=document.querySelector(`[data-request-handoff="${requestFocus}"]`);if(next)next.focus({preventScroll:true});else{requestElement.tabIndex=-1;requestElement.focus({preventScroll:true});}}
  }
  $('#activity-filters').innerHTML=Object.entries(activityFilterNames).map(([k,label])=>`<button data-activity-filter="${k}" class="${activityFilter===k?'active':''}" aria-pressed="${activityFilter===k}">${label}${k==='recent'?'':' '+counts[k]}</button>`).join('');
  $('#activity-count').textContent=`显示 ${Math.min(rows.length,activityLimit)} / ${rows.length} 项 · 项目共 ${activity.jobs.length} 项`+(activity.canvasCheckedAt?' · 画布核对 '+activityDate(activity.canvasCheckedAt):' · 画布尚未同步');
  target.innerHTML=activity.warnings.map(w=>`<p class="muted">${esc(w)}</p>`).join('')+(rows.length?activityCards(rows.slice(0,activityLimit)):empty(activityFilter==='active'?'暂无有近期证据的执行记录；较早未结束任务见“待核对”':'没有符合当前筛选的任务'))+(rows.length>activityLimit?'<button id="activity-more">再显示 12 项</button>':'');
  $('#activity-runs').innerHTML=activity.runs.map(r=>`<p>${esc(r.label)} · ${esc({completed:'执行完成',failed:'执行失败',running:'运行中',unknown:'心跳失联 · 待核对'}[r.status]||r.status)} · ${esc(activityDate(r.started))}${r.exit_code!==null?' · 退出码 '+esc(r.exit_code):''}</p>`).join('')||'<p>暂无脚本执行记录</p>';
  document.querySelectorAll('[data-activity-filter]').forEach(b=>b.onclick=()=>{activityFilter=b.dataset.activityFilter;activityLimit=12;paintActivity();});
  document.querySelectorAll('[data-activity-preview]').forEach(b=>b.onclick=()=>{const j=activity.jobs.find(j=>j.id===b.dataset.activityPreview),n=j?.canvas?.nodes.find(n=>n.hasMedia);if(n)openRemote(n.id,{...n,type:n.kind,taskId:j.taskId});});
  document.querySelectorAll('details[data-activity-id]').forEach(d=>{if(activityExpanded.has(d.dataset.activityId))d.open=true;d.ontoggle=()=>{if(d.isConnected)d.open?activityExpanded.add(d.dataset.activityId):activityExpanded.delete(d.dataset.activityId);};});
  if($('#activity-more'))$('#activity-more').onclick=()=>{activityLimit+=12;paintActivity();};
  $('main').scrollTop=top;
  if(focusedFilter)document.querySelector(`[data-activity-filter="${focusedFilter}"]`)?.focus({preventScroll:true});
 }
 activityDirty=false;
}
async function pollActivity(){
 if(!project)return;if(activityPolling){activityQueued=true;return;}activityPolling=true;activityQueued=false;const requestedProject=project;
 try{const next=await api('/api/production/activity',{project:requestedProject});if(project!==requestedProject)return;activityDirty=activityDirty||activityOffline||next.revision!==activity?.revision;activity=next;activityOffline=false;if(activityDirty&&!document.querySelector('dialog[open]'))paintActivity();}
 catch(e){if(project===requestedProject){activityOffline=true;paintActivity();}}
 finally{activityPolling=false;if(activityQueued){activityQueued=false;pollActivity();}}
}
setInterval(()=>{if(document.visibilityState==='visible'&&(activityOffline||Date.now()-lastFeedAt>5000))pollActivity();},5000);

// Repaint changed catalogs only; keep search, scroll, playback and open drafts.
let refreshing=false;
const liveViews=['editing','library','episodes','shots','assets','media','progress','issues'];
function liveRevision(){return JSON.stringify([data?.catalogRevision,data?.flows,data?.issues,data?.runs,data?.library,editJobs,remote.projects,remote.assets,remote.folders,remote.media]);}
function canRefresh(){return !busy&&!document.querySelector('dialog[open]')&&!['edit-brief','activity-search','activity-kind'].includes(document.activeElement?.id)&&liveViews.includes(view);}
function renderKeepingPosition(){const top=$('main').scrollTop,search=$('#library-search')?.value||'',focus=document.activeElement?.id;render();$('main').scrollTop=top;if($('#library-search')){$('#library-search').value=search;document.querySelectorAll('[data-search]').forEach(b=>b.hidden=!b.dataset.search.includes(search.toLowerCase()));if(focus==='library-search')$('#library-search').focus();}}
async function refreshLive(){if(refreshing||!canRefresh())return;refreshing=true;const before=liveRevision(),expected=feedCatalog;try{await load(false);if(canRefresh()&&before!==liveRevision())renderKeepingPosition();if(canRefresh()&&!catalogProblems.length&&expected===feedCatalog)catalogDirty=false;}finally{refreshing=false;if(expected!==feedCatalog&&catalogDirty&&canRefresh())refreshLive();}}
let feedController=null,feedProject='',feedRevision='',feedActivity='',feedCatalog='',lastFeedAt=0,feedRetry=null,feedFailures=0,catalogDirty=false;
function connectChanges(){
 if(!project||panelClosed||typeof TextDecoder==='undefined'||document.visibilityState==='hidden')return;
 if(feedProject===project&&(feedController||feedRetry))return;
 if(feedProject!==project){feedController?.abort();clearTimeout(feedRetry);feedRetry=null;feedRevision='';feedActivity='';feedCatalog='';}
 feedProject=project;const requestedProject=project,request=feedController=new AbortController();
 const deadline=setTimeout(()=>request.abort(),35000);
 (async()=>{
  const response=await fetch(BASE+'/api/production/changes?project='+encodeURIComponent(project)+'&since='+feedRevision,{headers:{Authorization:'Bearer '+token},signal:request.signal});
  if(!response.ok||!response.body)throw Error('更新通道暂不可用');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  try{while(!request.signal.aborted){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});if(buffer.length>16384)throw Error('更新消息过长');let end;while((end=buffer.indexOf('\n'))>=0){const message=JSON.parse(buffer.slice(0,end));buffer=buffer.slice(end+1);if(project!==requestedProject||feedController!==request||message.project!==project)continue;if(!/^[a-f0-9]{64}$/.test(message.revision))throw Error('更新消息无效');lastFeedAt=Date.now();feedFailures=0;if(message.revision!==feedRevision){feedRevision=message.revision;if(message.activity!==feedActivity){feedActivity=message.activity;pollActivity();}if(message.catalog!==feedCatalog){feedCatalog=message.catalog;catalogDirty=true;refreshLive();}}}}}
  finally{await reader.cancel().catch(()=>{});}
 })().catch(()=>{if(feedController===request&&!request.signal.aborted){lastFeedAt=0;feedFailures++;}}).finally(()=>{
  clearTimeout(deadline);if(feedController!==request)return;feedController=null;
  if(!panelClosed&&document.visibilityState!=='hidden')feedRetry=setTimeout(()=>{feedRetry=null;connectChanges();},Math.min(10000,500*2**feedFailures));
 });
}
setInterval(()=>{if(document.visibilityState==='visible'&&(catalogDirty||catalogProblems.length||Date.now()-lastFeedAt>5000))refreshLive();},5000);
window.addEventListener('focus',()=>{connectChanges();pollActivity();});
document.addEventListener?.('visibilitychange',()=>{if(document.visibilityState==='hidden'){feedController?.abort();clearTimeout(feedRetry);feedRetry=null;}else{connectChanges();pollActivity();if(catalogDirty)refreshLive();}});

const connectionNames={connected:'已连接',connecting:'连接中',unchecked:'待检测',disconnected:'未连接',error:'异常'};
const updateReasonNames={panel_unavailable:'页面没有响应更新检查',panel_loading:'制作面板尚未就绪',panel_busy:'面板仍有未结束操作',issue_draft:'有未保存的问题草稿',tool_draft:'有未完成的画布工具草稿',editing:'正在输入或调整',native_preview:'原生整理预览等待保留或还原',canvas_action:'画布操作正在处理',dialog_open:'预览或对话框尚未关闭',media_playing:'素材正在播放'};
function updateDetails(browser){
 const report=(browser.updates||[]).slice().sort((a,b)=>b.checkedAt-a.checkedAt)[0];
 if(!report)return browser.updateRequired?'<p>当前页面未回传更新等待原因，可在 KK 漫剧“连接设置”底部查看具体状态。</p>':'';
 const names={deferred:'新版本正在等待以下页面结束操作，完成后会自动继续',wrong_directory:'当前加载目录与本机发布目录不一致，请核对原扩展的加载来源',reload_failed:'自动重载未成功，请保存草稿后从扩展管理页重新加载',offline:'扩展最近一次更新检查未能连接本机服务',not_ready:'本机扩展包尚未通过完整性检查',unpaired:'当前扩展副本尚未配对',reloading:'扩展正在重新加载',current:'扩展已核对本机版本'};
 return `<p class="connection-warning">${esc(names[report.state]||'更新状态待核对')}</p>`+(report.blockers||[]).map(b=>`<p>${esc(b.project===project?'当前项目':(remote.projects||[]).find(p=>p.uuid===b.project)?.name||'其他 LibTV 项目')}：${b.reasons.map(r=>esc(updateReasonNames[r]||'未结束操作')).join('；')}</p>`).join('')+(report.blockedCount>(report.blockers||[]).length?'<p>还有其他页面未就绪，处理后会继续核对。</p>':'');
}
function paintConnections(){
 if(!links)return;
 for(const key of ['browser','codex','libtv']){
  const status=links[key]?.status||'unchecked',outdated=key==='browser'&&status==='connected'&&links.browser.updateRequired;
  document.querySelector(`[data-light="${key}"]`).dataset.status=outdated?'outdated':status;
  document.querySelector(`[data-state="${key}"]`).textContent=outdated?'待刷新':key==='codex'&&status==='connected'?'服务就绪':connectionNames[status]||status;
 }
 const running=links.sync?.status==='running';
 $('#sync-libtv').disabled=running;$('#full-sync').disabled=running;$('#sync-libtv').textContent=running?'同步中…':'同步 LibTV';
 $('#sync-time').textContent=running?links.sync.phase:(links.autoSync?.enabled?'自动更新已开启 · ':'')+(links.lastSuccess?'上次同步 '+new Date(links.lastSuccess*1000).toLocaleTimeString('zh-CN'):'等待首次同步');
 if(!$('#connections-dialog').open)return;
 const browser=links.browser||{},versions=[...new Set((browser.builds||[]).map(x=>x.version))];
 const extensionIds=[...new Set((browser.builds||[]).map(x=>x.originExtensionId).filter(x=>/^[a-p]{32}$/.test(x||'')))];
 const buildInfo=browser.status==='connected'?(versions.length?`<p>扩展后台版本：${esc(versions.join('、'))}${browser.availableVersion?' · 本机扩展包：'+esc(browser.availableVersion):''}</p>`:'')+(extensionIds.length?'<p>扩展来源 ID：'+extensionIds.map(esc).join('、')+'</p>':'')+(browser.updateRequired?'<p class="connection-warning">新版本已就绪，当前页面尚未加载。请先保存草稿，并按下方更新状态处理。</p>':'')+(browser.unknownBuildTabs?`<p>有 ${browser.unknownBuildTabs} 个当前项目页面未报告扩展版本，暂不能核对升级是否生效。</p>`:''):'';
 $('#connections-body').innerHTML=['browser','codex','libtv'].map(k=>`<p><b>${{browser:'浏览器',codex:'本地 Codex 服务',libtv:'LibTV 官方插件'}[k]}</b> · ${connectionNames[links[k]?.status]||'待检测'}</p>`).join('')+buildInfo+(browser.status==='connected'?updateDetails(browser):'')
  +(browser.status==='disconnected'?'<p>当前项目没有新鲜的浏览器回传。'+(browser.otherTabsConnected?'其他项目标签页仍已连接。':'请打开此项目的 LibTV 页面。')+'</p>':'')
  +(links.sync?.status==='running'||links.sync?.status==='completed'?`<p>${esc(links.sync.phase)}</p>`:'')
  +(links.autoSync?.enabled?'<p>自动更新已开启 · 当前项目约 30 秒更新，项目目录和其他已接入项目约 5 分钟更新。</p>':'')
  +(links.autoSync?.error?`<p>${esc(links.autoSync.error)}</p>`:'')+(links.error?`<p class="muted">${esc(links.error)}</p>`:'')+(links.sync?.error?`<p>${esc(links.sync.error)}</p>`:'');
}
async function updateConnections(force=false){if(!project)return;try{const requestedProject=project;const nextLinks=await api('/api/connections',{project,force:force===true});if(project!==requestedProject)return;links=nextLinks;paintConnections();if(links.sync?.id!==lastSyncId&&links.sync?.status==='completed'){lastSyncId=links.sync.id;await refreshLive();if(!catalogProblems.length)note('LibTV 已同步 · '+links.sync.phase);}else if(links.sync?.status==='failed')note('同步失败：'+links.sync.error+' · 上次成功数据仍保留');}catch(e){document.querySelectorAll('[data-light]').forEach(x=>x.dataset.status='error');document.querySelectorAll('[data-state]').forEach(x=>x.textContent='服务离线');}}
async function syncLibrary(full=false){try{const r=await api('/api/libtv/sync',{project,full});note(r.message||(full?'正在完整核对目录和资产，已有素材继续可用':'正在更新当前画布和变化的资产'));await updateConnections();}catch(e){note(e.message);}}$('#sync-libtv').onclick=()=>syncLibrary();$('#full-sync').onclick=()=>syncLibrary(true);
$('#connection-details').onclick=()=>{$('#connections-dialog').showModal();paintConnections();stateSafe();};$('#close-connections').onclick=()=>{$('#connections-dialog').close();stateSafe();if(['library','episodes','shots','assets','media','progress','issues'].includes(view))render();};$('#connections-dialog').addEventListener('cancel',()=>setTimeout(stateSafe,0));$('#recheck-links').onclick=()=>updateConnections(true);
async function openRemote(id,supplied,atTime=null){
 const item=supplied||[...remote.media,...remote.assets].find(x=>x.id===id);
 if(!item&&!id.startsWith('review:'))return;
 remoteController?.abort();const request=remoteController=new AbortController(),requestedProject=project;remotePreview=null;
 $('#remote-issue').disabled=true;$('#remote-title').textContent=item?.name||'原审看版本';$('#remote-body').textContent='正在读取官方素材…';$('#remote-dialog').showModal();stateSafe();
 try{
  const target=await api('/api/production/preview',{project:requestedProject,...(id.startsWith('review:')?{previewId:id}:{media:id,taskId:item?.taskId})});
  if(request.signal.aborted||remoteController!==request)return;
  const r=await fetch(BASE+'/api/production/review-media?project='+encodeURIComponent(requestedProject)+'&id='+encodeURIComponent(target.previewId),{headers:{Authorization:'Bearer '+token},signal:request.signal});
  if(!r.ok)throw Error('原审看素材暂不可用，请保留问题记录后核对');const blob=await r.blob();
  if(request.signal.aborted||remoteController!==request||!$('#remote-dialog').open||project!==requestedProject)return;
  const url=URL.createObjectURL(blob);urls.push(url);remotePreview=target;$('#remote-title').textContent=target.label;
  $('#remote-body').innerHTML=target.kind==='video'?'<video controls playsinline></video>':target.kind==='audio'?'<audio controls></audio>':'<img alt="素材原图">';
  const media=$('#remote-body').firstElementChild;media.src=url;
  if(['video','audio'].includes(target.kind))media.addEventListener('loadedmetadata',()=>{if(Number.isFinite(atTime))media.currentTime=Math.min(atTime,Number.isFinite(media.duration)?media.duration:atTime);},{once:true});
  $('#remote-issue').disabled=false;
 }catch(e){if(e.name!=='AbortError'&&remoteController===request)$('#remote-body').textContent=e.message;}
}
$('#remote-issue').onclick=()=>{if(!remotePreview)return;const m=$('#remote-body').querySelector('video,audio');m?.pause();openIssue(remotePreview,m?.currentTime);};
function closeRemote(){remoteController?.abort();remotePreview=null;$('#remote-body').querySelectorAll('video,audio').forEach(x=>x.pause());$('#remote-body').replaceChildren();$('#remote-dialog').close();stateSafe();}
$('#close-remote').onclick=closeRemote;$('#remote-dialog').addEventListener('cancel',closeRemote);

let lastConnectionPoll=0;setInterval(()=>{if(document.visibilityState==='visible'&&(links?.sync?.status==='running'||Date.now()-lastConnectionPoll>=5000)){lastConnectionPoll=Date.now();updateConnections();}},1000);

function editScope(){return [project,episodeId,versionId].join('/');}
function editSelections(ver){return ver.shots.filter(s=>s.candidates.some(c=>c.id===s.adopted)).map(s=>{const c=s.candidates.find(c=>c.id===s.adopted);return {shot:s.id,title:s.title||s.id,candidate:c.id,label:c.label||c.id,revision:s.choiceRevision||0};});}
const editStatuses={queued:'等待剪辑',preparing:'准备素材',editing:'Codex 剪辑中',verifying:'核对云端分轨',ready:'可在网页微调',needs_attention:'需要在 Codex 继续核对'};
function editingView(ep,ver){
 if(editBriefs[editScope()]===undefined){try{editBriefs[editScope()]=localStorage.getItem('kk-edit-brief:'+editScope())||'';}catch{}}
 if(!ver)return empty('请先选择有镜头的剧集');
 const selected=editSelections(ver),missing=ver.shots.filter(s=>!s.adopted||!s.candidates.some(c=>c.id===s.adopted));
 const active=editJobs.some(j=>['queued','preparing','editing','verifying'].includes(j.status));
 const jobs=editJobs.filter(j=>j.episode===ep.id&&j.version===ver.id);
 return `<div class="filters">${ep.versions.map(v=>`<button data-version="${esc(v.id)}" class="${v.id===versionId?'active':''}">${esc(v.id==='CANVAS'?'画布镜头':v.id+' '+v.name)}</button>`).join('')}</div><section class="edit-card"><h2>采用版本 → 自动剪辑 → CapCut 网页微调</h2><p>画面按片段保留，声音和字幕独立可调。字幕默认白字、无黑色描边。每轮另建工程，保留已有人工微调版。</p><p>${selected.length} / ${ver.shots.length} 镜已正式采用${missing.length?' · 还需选择 '+esc(missing.map(s=>s.id).join('、')):''}</p><div class="edit-selection">${ver.shots.map(s=>`<div><button data-compare="${esc(s.id)}">${esc(s.id)}</button><span>${esc(s.candidates.find(c=>c.id===s.adopted)?.label||'待采用')}</span></div>`).join('')}</div><label for="edit-brief">本轮剪辑要求（可选）</label><textarea id="edit-brief" maxlength="2000" rows="3" placeholder="例如：保留对白，用听者反应承接，打斗切口干脆">${esc(editBriefs[editScope()]||'')}</textarea><p class="muted">Codex 使用已采用素材设计节奏和切点；转场、特效按需要调用。有独立声源时分开人声、音效与音乐，原片只有混合音轨时保留为混合声音。请在完成后正常速度审看。</p><button id="start-edit" class="primary" ${missing.length||!selected.length||active||busy?'disabled':''}>${active?'已有剪辑任务执行中':'自动剪辑到 CapCut'}</button></section>${jobs.map(j=>`<article class="edit-card"><div class="actions"><b>${esc(new Date(j.created*1000).toLocaleString('zh-CN'))}</b>${chip(editStatuses[j.status]||j.status,j.status==='needs_attention'?'warn':'')}</div><p>${esc(j.message)}</p>${j.editor?.web_editor_url?`<a class="edit-link" target="_blank" rel="noopener noreferrer" href="${esc(j.editor.web_editor_url)}">${esc(j.editor.web_editor_text||'Open in CapCut')}</a><p class="muted">${Object.entries(j.editor.counts||{}).map(([k,v])=>`${{VIDEO:'画面',AUDIO:'声音',TEXT:'字幕'}[k]||k} ${v} 段`).join(' · ')}</p>`:''}<details><summary>本轮采用清单</summary>${j.selections.map(s=>`<p>${esc(s.shot+' · '+s.label)}</p>`).join('')}</details></article>`).join('')}`;
}
async function startEdit(){
 if(busy)return;
 const {ep,ver}=selection();if(!ver)return;
 const payload={project,episode:ep.id,version:ver.id,selections:editSelections(ver),brief:editBriefs[editScope()]||''};
 // Preserve the same id after a timeout: a click may already have been accepted.
 const fingerprint=JSON.stringify(payload);
 if(!editRequest||editRequest.fingerprint!==fingerprint)editRequest={id:crypto.randomUUID(),fingerprint};
 busy=true;stateSafe();$('#start-edit').disabled=true;
 try{const result=await api('/api/capcut/start',{id:editRequest.id,...payload});editRequest=null;delete editBriefs[[payload.project,payload.episode,payload.version].join('/')];try{localStorage.removeItem('kk-edit-brief:'+ [payload.project,payload.episode,payload.version].join('/'));}catch{}await load(false);render();note('已启动 Codex 剪辑 · 可在此查看进度，完成后打开 CapCut 微调');}
 catch(e){note(e.message);}
 finally{busy=false;if($('#start-edit'))$('#start-edit').disabled=editJobs.some(j=>['queued','preparing','editing','verifying'].includes(j.status));stateSafe();}
}
