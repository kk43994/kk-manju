const input=document.querySelector('#token'),result=document.querySelector('#result');
chrome.storage.local.get('pairToken').then(x=>{input.value=x.pairToken||'';});
document.querySelector('#save').onclick=async()=>{
  const token=input.value.trim();if(token.length<32){result.textContent='请填写完整的本机配对码';return;}
  try{const res=await fetch('http://127.0.0.1:18765/api/state',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(5000)});if(!res.ok)throw new Error('配对码不匹配或本地服务未就绪');await chrome.storage.local.set({pairToken:token});result.textContent='已连接。回到 LibTV 画布展开右下角 KK 面板。';}catch(e){result.textContent='未连接：'+e.message;}
};

const updateLabels={current:'已是本机最新版',reloading:'正在更新扩展',deferred:'更新已就绪 · 等待结束当前编辑或操作',wrong_directory:'当前加载目录与发布目录不同',reload_failed:'自动重载未成功 · 请手动重新加载一次',offline:'本地服务暂不可用',not_ready:'暂无完整发布包',unpaired:'请先配对本机服务'};
async function showUpdate(){const {updateStatus:s}=await chrome.storage.local.get('updateStatus');document.querySelector('#update-state').textContent='当前 '+chrome.runtime.getManifest().version+(s?' · '+(updateLabels[s.state]||s.state):'');}
document.querySelector('#check-update').onclick=async()=>{await chrome.runtime.sendMessage({action:'check_update'});await showUpdate();};
chrome.storage.onChanged.addListener(showUpdate);showUpdate();
