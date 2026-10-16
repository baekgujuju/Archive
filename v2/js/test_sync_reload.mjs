import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto,createHash} from 'node:crypto';
const pin='fixture-pin';
const source=readFileSync(new URL('./drive-sync.js',import.meta.url),'utf8').replace(/var WRITE_PIN_HASH = '[a-f0-9]+';/,`var WRITE_PIN_HASH = '${createHash('sha256').update(pin).digest('hex')}';`);
const saved={},session={};let writes=0,reads=0,remote={pinned:['phone-pin'],bookmarks:{'phone-mark':'#3498db'},memos:{},updatedAt:'2026-10-16T01:00:00Z'},tamper=false;
const storage=s=>({getItem:k=>s[k]??null,setItem:(k,v)=>s[k]=String(v),removeItem:k=>delete s[k],key:i=>Object.keys(s)[i],get length(){return Object.keys(s).length;}});
const drain=()=>new Promise(setImmediate);
function page(){
 let oauth,callbacks={},intervals=[],status={textContent:''},refreshes=0;
 const c=vm.createContext({TextEncoder,Uint8Array,console,setTimeout,clearTimeout,localStorage:storage(saved),sessionStorage:storage(session),
 window:{crypto:webcrypto,addEventListener:(n,f)=>callbacks[n]=f,ArchiveApp:{refreshSavedState(){refreshes++;}}},
 document:{hidden:false,querySelector:()=>true,addEventListener:(n,f)=>callbacks[n]=f},
 google:{accounts:{oauth2:{initTokenClient(o){oauth=o;return {requestAccessToken(){o.callback({access_token:'fixture-token',expires_in:3600});}};}}}},
 setInterval(f,ms){intervals.push({f,ms});return intervals.length;},
 fetch:async(url,o={})=>{if(o.method==='PATCH'){writes++;if(!tamper)remote=JSON.parse(o.body);return {ok:true};}reads++;return {ok:true,json:async()=>url.includes('?alt=media')?remote:{files:[{id:'fixture-cloud-file'}]}};}});
 new vm.Script(source).runInContext(c);c.DriveSync.init(status);return {c,status,callbacks,intervals,get refreshes(){return refreshes;}};
}
let first=page();first.c.DriveSync.connect();await drain();
assert.equal(writes,0);assert.equal(JSON.parse(saved['archive-bookmarks'])['phone-mark'],'#3498db');
assert.ok(session['archive-drive-session-v1']);assert.equal(Object.values(saved).some(x=>x.includes('fixture-token')),false);
console.log('PASS: connect reads phone marks/pins, token never enters localStorage');
remote.pinned.push('new-phone-pin');remote.bookmarks['new-phone-mark']='#e74c3c';
let reload=page();await drain();assert.equal(reload.c.DriveSync.isConnected(),true);assert.equal(writes,0);
assert.ok(JSON.parse(saved['archive-pinned']).includes('new-phone-pin'));assert.equal(JSON.parse(saved['archive-bookmarks'])['new-phone-mark'],'#e74c3c');
assert.equal(reload.intervals.length,1);assert.equal(reload.intervals[0].ms,5000);
console.log('PASS: same app session reload restores connection and reads newer remote without writes');
const refreshBefore=reload.refreshes;reload.intervals[0].f();await drain();assert.equal(reload.refreshes,refreshBefore);assert.equal(writes,0);
console.log('PASS: unchanged remote polling does not redraw the reading list');
remote.pinned.push('focus-pin');reload.callbacks.focus();await drain();assert.ok(JSON.parse(saved['archive-pinned']).includes('focus-pin'));assert.equal(writes,0);
console.log('PASS: focus reads newer remote, no export');
await reload.c.DriveSync.exportWithPassword(pin);assert.equal(writes,1);assert.ok(reads>0);assert.equal(JSON.stringify(reload.c.DriveSync._exportState()).includes('fixture-token'),false);
console.log('PASS: one PIN export then readback; backup excludes session credential');
saved['archive-bookmarks']=JSON.stringify({...JSON.parse(saved['archive-bookmarks']),test:'#123'});tamper=true;
await assert.rejects(reload.c.DriveSync.exportWithPassword(pin),/再|다시 확인/);assert.equal(JSON.parse(saved['archive-bookmarks']).test,'#123');
console.log('PASS: false successful write is blocked by readback and local changes preserved');
session['archive-drive-session-v1']=JSON.stringify({access_token:'expired',expiresAt:Date.now()-1});let expired=page();await drain();
assert.equal(expired.c.DriveSync.isConnected(),false);assert.equal(session['archive-drive-session-v1'],undefined);
assert.ok(JSON.parse(saved['archive-pinned']).includes('focus-pin'));
console.log('PASS: expired session requires reconnect and preserves marks/pins');
