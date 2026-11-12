const input=document.querySelector('#token'),result=document.querySelector('#result');
chrome.storage.local.get('pairToken').then(x=>{input.value=x.pairToken||'';});
document.querySelector('#save').onclick=async()=>{
  const token=input.value.trim();if(token.length<32){result.textContent='请填写完整的本机配对码';return;}
  try{const res=await fetch('http://127.0.0.1:18765/api/state',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(5000)});if(!res.ok)throw new Error('配对码不匹配或本地服务未就绪');await chrome.storage.local.set({pairToken:token});result.textContent='已连接。回到 LibTV 画布展开右下角 KK 面板。';}catch(e){result.textContent='未连接：'+e.message;}
};

const updateLabels={current:'已是本机最新版',reloading:'正在更新扩展',deferred:'更新已就绪 · 等待结束当前编辑或操作',wrong_directory:'当前加载目录与发布目录不同',reload_failed:'自动重载未成功 · 请手动重新加载一次',offline:'本地服务暂不可用',not_ready:'暂无完整发布包',unpaired:'请先配对本机服务'};
const cloudLabels={current:'云端自动更新已开启',checking:'正在检查云端新版',downloading:'正在下载新版',deferred:'新版已下载，等待制作任务完成',failed:'更新暂未成功，保留当前版本，将自动重试',rolled_back:'已退回可用版本，等待修复版',not_installed:'需先安装一次自动更新版配套服务',idle:'云端自动更新已开启'};
const updateReasonNames={panel_unavailable:'页面没有响应更新检查',panel_loading:'制作面板尚未就绪',panel_busy:'面板仍有未结束操作',issue_draft:'有未保存的问题草稿',tool_draft:'有未完成的画布工具草稿',editing:'正在输入或调整',native_preview:'原生整理预览等待保留或还原',canvas_action:'画布操作正在处理',dialog_open:'预览或对话框尚未关闭',media_playing:'素材正在播放'};
let updateView=0;
async function showUpdate(){
 const sequence=++updateView,{updateStatus:s}=await chrome.storage.local.get('updateStatus');if(sequence!==updateView)return;
 document.querySelector('#update-state').textContent='当前 '+chrome.runtime.getManifest().version+(s?' · '+(updateLabels[s.state]||s.state):'')+(s?.cloud?' · '+(cloudLabels[s.cloud.state]||s.cloud.state):'');
 const detail=document.querySelector('#update-detail');detail.textContent='';
 if(s?.state==='deferred'){
  let tabs=[];try{tabs=await chrome.tabs.query({url:['https://www.liblib.tv/canvas*','https://liblib.tv/canvas*']});}catch{}if(sequence!==updateView)return;
  detail.style.whiteSpace='pre-line';detail.textContent=(s.blockers||[]).map(b=>(tabs.find(t=>t.id===b.tabId)?.title||'LibTV 页面')+'：'+b.reasons.map(r=>updateReasonNames[r]||'未结束操作').join('；')).join('\n')||'请先保存各页面的草稿、关闭预览或完成原生整理，再自动继续。';
 }else if(s?.state==='wrong_directory')detail.textContent='请核对加载的是当前本机扩展目录；单独刷新 LibTV 页面不会更换扩展副本。';
}

document.querySelector('#check-update').onclick=async()=>{await chrome.runtime.sendMessage({action:'check_update'});await showUpdate();};
chrome.storage.onChanged.addListener(showUpdate);showUpdate();
