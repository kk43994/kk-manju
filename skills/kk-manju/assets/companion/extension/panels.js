(function(root){
  const projectOf=url=>{try{const u=new URL(url);return u.protocol==='https:'&&['www.liblib.tv','liblib.tv'].includes(u.hostname)&&u.pathname==='/canvas'?u.searchParams.get('projectId'):null;}catch{return null;}};
  function create(chrome){
    const configured=new Map(),ports=new Map();
    const ownPage=url=>{try{const u=new URL(url),base=new URL(chrome.runtime.getURL('production.html'));return u.origin===base.origin&&u.pathname===base.pathname;}catch{return false;}};
    const pathFor=(tabId,project)=>'production.html?panel=1&view=progress&tab='+tabId+'&project='+encodeURIComponent(project);
    async function configure(tab){
      const project=projectOf(tab.url);if(!project||!chrome.sidePanel)return;
      const path=pathFor(tab.id,project);if(configured.get(tab.id)===path)return;
      // Changing the path destroys the old document. Retain a busy editor until
      // it reports safe; never retarget its draft to the new canvas project.
      if([...ports.values()].some(p=>p.tabId===tab.id&&!p.safe))return;
      if(chrome.runtime.getContexts){
        const contexts=await chrome.runtime.getContexts({contextTypes:['SIDE_PANEL','TAB']});
        if(contexts.some(c=>ownPage(c.documentUrl)&&Number(new URL(c.documentUrl).searchParams.get('tab'))===tab.id&&!ports.get(c.documentId)?.safe&&new URL(c.documentUrl).searchParams.get('project')!==project))return;
      }
      await chrome.sidePanel.setOptions({tabId:tab.id,path,enabled:true});configured.set(tab.id,path);
    }
    async function open(tab){
      if(!projectOf(tab.url))throw Error('请在 LibTV 画布打开制作侧栏');
      if(!chrome.sidePanel?.open)throw Error('当前浏览器不支持侧栏，可打开完整制作面板');
      // Invoke open in the incoming user gesture, before storage/network awaits.
      // A default panel handles the rare first click before tab setup completes.
      await chrome.sidePanel.open({tabId:tab.id});
      if(!configured.has(tab.id))await configure(tab);
      return {ok:true};
    }
    chrome.runtime.onConnect.addListener(port=>{
      if(port.name!=='kk-production'||!ownPage(port.sender?.url)||!port.sender?.documentId)return;
      const url=new URL(port.sender.url),id=port.sender.documentId;
      const state={project:url.searchParams.get('project'),tabId:Number(url.searchParams.get('tab'))||null,safe:false,reasons:['panel_loading']};
      ports.set(id,state);
      port.onMessage.addListener(m=>{if(m?.action!=='safe')return;state.safe=m.safe===true;state.reasons=Array.isArray(m.reasons)?m.reasons.filter(x=>['issue_draft','dialog_open','media_playing','canvas_action','editing'].includes(x)):[];if(state.safe&&state.tabId)chrome.tabs.get(state.tabId).then(configure).catch(()=>{});});
      port.onDisconnect.addListener(()=>{if(ports.get(id)===state)ports.delete(id);});
    });
    async function blockers(){
      // Context inventory survives service-worker restarts; a disconnected open
      // editor stays protected, while a panel never opened cannot block updates.
      if(!chrome.runtime.getContexts)return [{project:'unknown',reasons:['panel_unavailable']}];
      const contexts=await chrome.runtime.getContexts({contextTypes:['SIDE_PANEL','TAB']});
      return contexts.filter(c=>ownPage(c.documentUrl)).flatMap(c=>{
        const p=ports.get(c.documentId);if(p?.safe)return [];
        return [{project:p?.project||new URL(c.documentUrl).searchParams.get('project')||'unknown',reasons:p?.reasons?.length?p.reasons:['panel_unavailable']}];
      });
    }
    async function action(message,sender){
      if(!ownPage(sender.url)||!['tools','locate'].includes(message.kind))throw Error('面板操作来源无效');
      const tabId=Number(new URL(sender.url).searchParams.get('tab'));
      if(!Number.isSafeInteger(tabId)||tabId<=0)throw Error('请从对应的 LibTV 画布打开侧栏');
      const tab=await chrome.tabs.get(tabId);
      if(projectOf(tab.url)!==message.project)throw Error('画布项目已切换，请返回此面板对应的项目');
      const response=await chrome.tabs.sendMessage(tabId,{action:'kk_panel_action',kind:message.kind,project:message.project,batch:message.batch});
      if(!response?.ok)throw Error(response?.error||'画布助手暂未连接');
      return response;
    }
    return {configure,open,blockers,action,ownPage};
  }
  root.KKPanels={create,projectOf};
  if(typeof module!=='undefined')module.exports=root.KKPanels;
})(typeof globalThis!=='undefined'?globalThis:this);
