const test=require('node:test'),assert=require('node:assert/strict');
const {create,canvas}=require('../assets/companion/extension/updater.js');
const old={schema:1,version:'0.3.0',buildId:'a'.repeat(64)};
const next={schema:1,version:'0.3.1',buildId:'b'.repeat(64),ready:true};
function fixture(options={}){
  const saved={pairToken:'local-test-token',...options.saved};let reloads=0,calls=0,time=1000000;const injections=[];
  const tabs=options.tabs||[{id:1,url:'https://www.liblib.tv/canvas?projectId=p1'}];
  const chrome={storage:{local:{get:async keys=>saved,set:async x=>Object.assign(saved,x),remove:async k=>delete saved[k]}},
    runtime:{getURL:p=>'chrome-extension://fixture/'+p,reload:()=>reloads++},
    tabs:{query:async()=>tabs,sendMessage:async()=>{if(options.missing)throw Error('no content');return {safe:options.safe!==false};}},
    scripting:{executeScript:async x=>injections.push(x)}};
  const fetch=async url=>{calls++;if(options.offline)throw Error('offline');return {ok:true,json:async()=>url.startsWith('http:')?(options.release||next):(options.disk||next)};};
  return {api:create({chrome,fetch,build:options.build||old,now:()=>time}),saved,injections,reloads:()=>reloads,calls:()=>calls,tick:()=>time+=30001};
}
test('complete matching local package reloads once and throttles checks',async()=>{const f=fixture();assert.equal((await f.api.check()).state,'reloading');assert.equal(f.reloads(),1);await f.api.check();assert.equal(f.reloads(),1);assert.equal(f.saved.updateAttempt.count,1);});
test('wrong loaded directory or incomplete release never reloads',async()=>{for(const o of [{disk:old},{release:{ready:false}}]){const f=fixture(o);await f.api.check(true);assert.equal(f.reloads(),0);}});
test('unsaved work or unresponsive tab defers reload',async()=>{for(const o of [{safe:false},{missing:true}]){const f=fixture(o);assert.equal((await f.api.check(true)).state,'deferred');assert.equal(f.reloads(),0);}});
test('reload failure has a persistent retry ceiling',async()=>{const f=fixture({saved:{updateAttempt:{buildId:next.buildId,count:2,at:1000000}}});assert.equal((await f.api.check(true)).state,'reload_failed');assert.equal(f.reloads(),0);});
test('current build clears retry guard and does not reload',async()=>{const f=fixture({build:next,saved:{updateAttempt:{buildId:next.buildId,count:2}}});assert.equal((await f.api.check(true)).state,'current');assert.equal(f.saved.updateAttempt,undefined);assert.equal(f.reloads(),0);});
test('offline service leaves extension running and retries later',async()=>{const f=fixture({offline:true});assert.equal((await f.api.check(true)).state,'offline');assert.equal(f.reloads(),0);});
test('reinjects only packaged files in exact LibTV canvas tabs without page reload',async()=>{const f=fixture({tabs:[{id:1,url:'https://www.liblib.tv/canvas?projectId=p1'},{id:2,url:'https://evil.test/canvas?projectId=p1'},{id:3,url:'https://www.liblib.tv/canvas-extra?projectId=p1'}]});await f.api.installPanels();assert.deepEqual(f.injections,[{target:{tabId:1},files:['build-info.js','adapter.js','content.js']}]);assert.equal(canvas('https://www.liblib.tv/home'),false);});
test('same version with new build fingerprint still reloads',async()=>{const f=fixture({release:{...next,version:old.version},disk:{...next,version:old.version}});assert.equal((await f.api.check(true)).state,'reloading');});
