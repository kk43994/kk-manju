(function(root){
  const patterns=['https://www.liblib.tv/canvas*','https://liblib.tv/canvas*'];
  function canvas(url){try{const u=new URL(url);return u.protocol==='https:'&&['www.liblib.tv','liblib.tv'].includes(u.hostname)&&u.pathname==='/canvas'&&!!u.searchParams.get('projectId');}catch{return false;}}
  function identity(x){return x?.schema===1&&/^\d+(\.\d+){0,3}$/.test(x.version)&&/^[a-f0-9]{64}$/.test(x.buildId);}
  const reasons=new Set(['panel_unavailable','panel_loading','panel_busy','issue_draft','tool_draft','editing','native_preview','canvas_action','dialog_open','media_playing']);
  function create({chrome,fetch,build,now=Date.now,tabTimeoutMs=2000,panelBlockers=async()=>[]}){
    let checking=false,lastCheck=0;
    const status=async(state,extra={})=>{const value={state,currentVersion:build.version,currentBuild:build.buildId,checkedAt:now(),...extra};await chrome.storage.local.set({updateStatus:value});return value;};
    async function tabs(){return (await chrome.tabs.query({url:patterns})).filter(t=>canvas(t.url));}
    async function blockedTab(tab){
      let timer,response;
      try{response=await Promise.race([chrome.tabs.sendMessage(tab.id,{action:'kk_prepare_update'}),new Promise(resolve=>{timer=setTimeout(()=>resolve(null),tabTimeoutMs);})]);}catch{}finally{clearTimeout(timer);}
      if(response?.safe===true)return null;
      const reported=Array.isArray(response?.reasons)?response.reasons.filter(x=>reasons.has(x)):[];
      return {tabId:tab.id,project:new URL(tab.url).searchParams.get('projectId'),reasons:[...new Set(reported.length?reported:[response?'panel_busy':'panel_unavailable'])]};
    }
    async function installPanels(){
      const result=[];
      for(const t of await tabs()){
        try{await chrome.scripting.executeScript({target:{tabId:t.id},files:['build-info.js','adapter.js','content.js']});result.push({tab:t.id,ok:true});}
        catch{result.push({tab:t.id,ok:false});}
      }
      await chrome.storage.local.set({panelInstall:result});return result;
    }
    async function check(force=false){
      if(checking||(!force&&now()-lastCheck<30000))return;
      checking=true;lastCheck=now();
      try{
        const {pairToken,updateAttempt}=await chrome.storage.local.get(['pairToken','updateAttempt']);
        if(!pairToken)return await status('unpaired');
        if(force){
          // Older local services can still check their local package. A check
          // never accepts remote URLs or executable instructions from the page.
          try{await fetch('http://127.0.0.1:18765/api/cloud-update/check',{method:'POST',headers:{Authorization:'Bearer '+pairToken,'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(5000)});}catch{}
        }
        const res=await fetch('http://127.0.0.1:18765/api/extension-release',{headers:{Authorization:'Bearer '+pairToken},cache:'no-store',signal:AbortSignal.timeout(5000)});
        if(!res.ok)throw new Error('本地更新服务不可用');
        const release=await res.json();
        if(!release.ready||!identity(release))return await status('not_ready');
        const extra={availableVersion:release.version,availableBuild:release.buildId,cloud:release.cloud};
        if(build.buildId===release.buildId){await chrome.storage.local.remove('updateAttempt');return await status('current',extra);}
        // Never reload another copy or a partially copied package. This is bundled JSON, not remote JS.
        const diskRes=await fetch(chrome.runtime.getURL('release.json')+'?build='+release.buildId,{cache:'no-store'});
        if(!diskRes.ok)return await status('wrong_directory',extra);
        const disk=await diskRes.json();
        if(!identity(disk)||disk.buildId!==release.buildId||disk.version!==release.version)return await status('wrong_directory',extra);
        if(updateAttempt?.buildId===release.buildId&&updateAttempt.count>=2&&now()-updateAttempt.at<600000)return await status('reload_failed',extra);
        const blocked=(await Promise.all((await tabs()).map(blockedTab))).filter(Boolean);
        blocked.push(...await panelBlockers());
        if(blocked.length)return await status('deferred',{...extra,blockedCount:blocked.length,blockers:blocked.slice(0,20)});
        const count=updateAttempt?.buildId===release.buildId&&now()-updateAttempt.at<600000?updateAttempt.count+1:1;
        await chrome.storage.local.set({updateAttempt:{buildId:release.buildId,count,at:now()}});
        await status('reloading',extra);
        chrome.runtime.reload();
        return {state:'reloading',...extra};
      }catch{return await status('offline');}finally{checking=false;}
    }
    return {check,installPanels};
  }
  root.KKUpdater={create,canvas,identity};
  if(typeof module!=='undefined')module.exports=root.KKUpdater;
})(typeof globalThis!=='undefined'?globalThis:this);
