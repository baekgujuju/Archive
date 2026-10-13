import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('./drive-sync.js',import.meta.url),'utf8');
const store={'archive-pinned':'["local"]','archive-bookmarks':'{}','archive_memo_local':'my memo'};
let remote={pinned:['cloud'],bookmarks:{cloud:'#34495e'},names:{},deletedReplies:[],memos:{cloud:'cloud memo'},updatedAt:'2026-10-12'};
let config,intervals=[],calls=[],refreshes=0,authError=false;
const status={textContent:''};
const ctx=vm.createContext({
  window:{ArchiveApp:{refreshSavedState(){refreshes++;}}},
  document:{querySelector(){return true;}},
  localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];},key:i=>Object.keys(store)[i],get length(){return Object.keys(store).length;}},
  google:{accounts:{oauth2:{initTokenClient(c){config=c;return {requestAccessToken(){config.callback({access_token:'mock-token'});}};}}}},
  setTimeout, setInterval(fn){intervals.push(fn);return intervals.length;}, console,
  fetch:async(url,opts={})=>{
    calls.push({url,method:opts.method||'GET'});
    if(authError)return {ok:false,status:401};
    if(url.includes('/upload/')){assert.equal(opts.method,'PATCH','existing cloud file must be updated');remote=JSON.parse(opts.body);return {ok:true,status:200};}
    if(url.includes('?alt=media'))return {ok:true,status:200,json:async()=>remote};
    return {ok:true,status:200,json:async()=>({files:[{id:'existing'}]})};
  }
});
new vm.Script(source).runInContext(ctx);
const drain=()=>new Promise(setImmediate);
ctx.DriveSync.init(status);ctx.DriveSync.connect();await drain();
assert.deepEqual(new Set(remote.pinned),new Set(['local','cloud']));
assert.equal(remote.memos.local,'my memo');assert.equal(remote.memos.cloud,'cloud memo');
assert.equal(refreshes,1);assert.equal(intervals.length,1);
assert.equal(calls.filter(c=>c.method==='POST').length,0);
console.log('PASS: initial restore merges and updates existing cloud file without reload');
store['archive-pinned']='["cloud"]';delete store.archive_memo_local;
intervals[0]();await drain();
assert.deepEqual(remote.pinned,['cloud']);assert.equal(remote.memos.local,undefined);
console.log('PASS: unpin and memo deletion propagate against synchronized baseline');
remote.bookmarks.cloud='#e74c3c';
for(let i=0;i<15;i++)intervals[0]();await drain();
assert.equal(JSON.parse(store['archive-bookmarks']).cloud,'#e74c3c');
console.log('PASS: remote polling applies another device edit');
ctx.DriveSync.connect();await drain();assert.equal(intervals.length,1);
console.log('PASS: reconnect retains one polling timer');
authError=true;store.archive_memo_new='unsynced';intervals[0]();await drain();
assert.equal(ctx.DriveSync.isConnected(),false);assert.equal(store.archive_memo_new,'unsynced');assert.match(status.textContent,/인증/);
console.log('PASS: expired authentication keeps local edits and reports reconnect');
assert.equal(ctx.DriveSync._validateRemote({bookmarks:null}),false);
assert.equal(ctx.DriveSync._validateRemote({pinned:[123]}),false);
console.log('PASS: malformed remote state rejected');
