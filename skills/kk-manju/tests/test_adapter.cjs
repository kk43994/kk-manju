const test=require('node:test'),assert=require('node:assert/strict');
const {readContext,arrangeButton,namedButton,arrangePending}=require('../assets/companion/extension/adapter.js');
function fixture({selection=true,video=true,disabled=false}={}){
 const button={disabled,getAttribute:n=>n==='aria-label'?'整理画布，Option+Shift+F':null};
 const node={getAttribute:()=> 'v-123',querySelectorAll:()=>video?[{currentTime:4.25,getBoundingClientRect:()=>({width:200})}]:[]};
 return {querySelector:()=>({value:'大师兄'}),querySelectorAll:selector=>selector==='button'?[button]:selection?[node]:[]};
}
test('reads actual selection and selected video time',()=>{const c=readContext(fixture(),'https://www.liblib.tv/canvas?projectId=p1');assert.deepEqual(c.selected,['v-123']);assert.equal(c.timecode,4.25);assert.equal(c.arrangeAvailable,true);});
test('does not invent time when media is not visible',()=>{assert.equal(readContext(fixture({video:false}),'https://www.liblib.tv/canvas?projectId=p1').timecode,null);});
test('honors disabled native arrange and empty selection',()=>{const c=readContext(fixture({selection:false,disabled:true}),'https://www.liblib.tv/canvas?projectId=p1');assert.equal(c.arrangeAvailable,false);assert.equal(c.timecode,null);assert.deepEqual(c.selected,[]);});
test('rejects noncanvas and unrelated hosts',()=>{for(const u of ['https://evil.test/canvas?projectId=p1','https://www.liblib.tv/home','http://www.liblib.tv/canvas?projectId=p1'])assert.equal(readContext(fixture(),u),null);});
test('detects pending native preview without treating it as saved',()=>{assert.equal(arrangePending({querySelectorAll:()=>[{textContent:'是否保留此次整理结果？还原保留'}]}),true);assert.equal(arrangePending({querySelectorAll:()=>[]}),false);});
test('uses exact native group locator rather than similar node labels',()=>{const target={getAttribute:()=> '定位到分组 V3 R2'};const doc={querySelectorAll:()=>[{getAttribute:()=> '定位到节点 V3 R2'},target]};assert.equal(namedButton(doc,'定位到分组 V3 R2'),target);assert.equal(namedButton(doc,'V3'),undefined);});
