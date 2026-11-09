importScripts('build-info.js','updater.js');
const updates=KKUpdater.create({chrome,fetch,build:KK_BUILD});
chrome.runtime.onInstalled.addListener(()=>{updates.installPanels().then(()=>updates.check(true)).catch(()=>{});});
chrome.runtime.onStartup.addListener(()=>{updates.installPanels().then(()=>updates.check(true)).catch(()=>{});});
const BASE='http://127.0.0.1:18765';
const routes=new Set(['/api/context','/api/batches','/api/issues','/api/commands','/api/commands/observed']);
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  (async()=>{
    if(message.action==='check_update'&&sender.url===chrome.runtime.getURL('options.html'))return {ok:true,data:await updates.check(true)};
    const url=new URL(sender.url||'https://invalid.local');
    if(url.protocol!=='https:'||!['www.liblib.tv','liblib.tv'].includes(url.hostname)||url.pathname!=='/canvas'||!sender.tab)throw new Error('消息来源不支持');
    if(message.action==='open_settings'){await chrome.runtime.openOptionsPage();return {ok:true};}
    if(!routes.has(message.path))throw new Error('操作不支持');
    const {pairToken}=await chrome.storage.local.get('pairToken');
    if(!pairToken)throw new Error('请先在扩展设置中填写本机配对码');
    const payload={...message.data,client:'tab-'+sender.tab.id};
    if(message.path==='/api/context')payload.extension={version:KK_BUILD.version,buildId:KK_BUILD.buildId};
    if(payload.project&&payload.project!==url.searchParams.get('projectId'))throw new Error('项目已切换，请刷新上下文');
    const res=await fetch(BASE+message.path,{method:'POST',headers:{Authorization:'Bearer '+pairToken,'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(6000)});
    const data=await res.json();if(!res.ok)throw new Error(data.error||'本地服务未就绪');
    if(message.path==='/api/context')await updates.check();
    return {ok:true,data};
  })().then(reply).catch(e=>reply({ok:false,error:e.message}));
  return true;
});
