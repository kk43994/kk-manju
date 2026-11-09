(function(root){
  function readContext(doc, href){
    const u=new URL(href), project=u.searchParams.get('projectId');
    if(!project||u.protocol!=='https:'||!['www.liblib.tv','liblib.tv'].includes(u.hostname)||u.pathname!=='/canvas')return null;
    const selected=[...doc.querySelectorAll('.react-flow__node.selected, .react-flow__node[aria-selected="true"]')];
    const arrange=[...doc.querySelectorAll('button')].find(b=>(b.getAttribute('aria-label')||'').startsWith('整理画布'));
    const videos=selected.length===1?[...selected[0].querySelectorAll('video')].filter(v=>v.getBoundingClientRect().width>0):[];
    return {project,name:doc.querySelector('input[aria-label="项目名称"]')?.value||project,
      url:u.origin+'/canvas?projectId='+encodeURIComponent(project),
      selected:[...new Set(selected.map(n=>n.getAttribute('data-id')).filter(Boolean))],
      hasGroupSelection:selected.some(n=>n.classList?.contains('react-flow__node-group')),
      arrangeAvailable:!!arrange&&!arrange.disabled,
      timecode:videos.length===1&&Number.isFinite(videos[0].currentTime)?videos[0].currentTime:null};
  }
  function arrangeButton(doc){return [...doc.querySelectorAll('button')].find(b=>(b.getAttribute('aria-label')||'').startsWith('整理画布'));}
  function positions(doc){return [...doc.querySelectorAll('.react-flow__node')].map(n=>[n.getAttribute('data-id'),n.style.transform]).sort((a,b)=>String(a[0]).localeCompare(String(b[0])));}
  function namedButton(doc,name){return [...doc.querySelectorAll('button')].find(b=>(b.getAttribute('aria-label')||b.textContent||'').trim()===name);}
  function arrangePending(doc){return [...doc.querySelectorAll('[role="dialog"]')].some(d=>d.textContent.includes('是否保留此次整理结果'));}
  root.KKLibAdapter={readContext,arrangeButton,positions,namedButton,arrangePending};
  if(typeof module!=='undefined')module.exports=root.KKLibAdapter;
})(typeof globalThis!=='undefined'?globalThis:this);
