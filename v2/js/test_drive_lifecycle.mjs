import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto,createHash} from 'node:crypto';
const fixturePin='fixture-only-export-pin';
const source=readFileSync(new URL('./drive-sync.js',import.meta.url),'utf8').replace(/var WRITE_PIN_HASH = '[a-f0-9]+';/,"var WRITE_PIN_HASH = '"+createHash('sha256').update(fixturePin).digest('hex')+"';");
const store={'archive-pinned':'["local"]','archive-bookmarks':'{}','archive_memo_local':'my memo'};
let remote={pinned:['cloud'],bookmarks:{cloud:'#34495e'},names:{},deletedReplies:[],memos:{cloud:'cloud memo'},updatedAt:'2026-10-12'};
let config,intervals=[],calls=[],refreshes=0,authError=false,noCloud=false,uploadFailure=false;
const status={textContent:''};
const ctx=vm.createContext({
  TextEncoder,Uint8Array, window:{crypto:webcrypto,ArchiveApp:{refreshSavedState(){refreshes++;}}},
  document:{querySelector(){return true;}},
  localStorage:{getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];},key:i=>Object.keys(store)[i],get length(){return Object.keys(store).length;}},
  google:{accounts:{oauth2:{initTokenClient(c){config=c;return {requestAccessToken(){config.callback({access_token:'mock-token'});}};}}}},
  setTimeout, setInterval(fn){intervals.push(fn);return intervals.length;}, console,
  fetch:async(url,opts={})=>{
    calls.push({url,method:opts.method||'GET'});
    if(authError)return {ok:false,status:401};
    if(url.includes('/upload/')){if(uploadFailure)return {ok:false,status:500};if(opts.method==='POST'){remote=JSON.parse(opts.body.split('Content-Type: application/json\r\n\r\n')[2].split('\r\n--')[0]);return {ok:true,status:200,json:async()=>({id:'created'})};}assert.equal(opts.method,'PATCH');remote=JSON.parse(opts.body);return {ok:true,status:200};}
    if(url.includes('?alt=media'))return {ok:true,status:200,json:async()=>remote};
    return {ok:true,status:200,json:async()=>({files:noCloud?[]:[{id:'existing'}]})};
  }
});
new vm.Script(source).runInContext(ctx);
const drain=()=>new Promise(setImmediate);
ctx.DriveSync.init(status);ctx.DriveSync.connect();await drain();
assert.deepEqual(remote.pinned,['cloud']);
assert.equal(calls.filter(c=>c.method!=='GET').length,0,'connect must be read-only');
await assert.rejects(ctx.DriveSync.exportWithPassword('wrong'),/비밀번호/);
assert.equal(calls.filter(c=>c.method!=='GET').length,0,'wrong password must never write');
await ctx.DriveSync.exportWithPassword(fixturePin);
assert.deepEqual(new Set(remote.pinned),new Set(['local','cloud']));
assert.equal(remote.memos.local,'my memo');assert.equal(remote.memos.cloud,'cloud memo');
assert.equal(refreshes,2);assert.equal(intervals.length,1);
assert.equal(calls.filter(c=>c.method==='POST').length,0);
console.log('PASS: initial read-only restore, wrong PIN blocked, one authorized export succeeds');
store['archive-pinned']='["cloud"]';delete store.archive_memo_local;
intervals[0]();await drain();
assert.ok(remote.pinned.includes('local'),'timer cannot export local deletion');
await ctx.DriveSync.exportWithPassword(fixturePin);
assert.deepEqual(remote.pinned,['cloud']);assert.equal(remote.memos.local,undefined);
console.log('PASS: local deletions remain private until another password-authorized export');
remote.bookmarks.cloud='#e74c3c';
for(let i=0;i<15;i++)intervals[0]();await drain();
assert.equal(JSON.parse(store['archive-bookmarks']).cloud,'#e74c3c');
console.log('PASS: remote polling applies another device edit');
const writesBefore=calls.filter(c=>c.method!=='GET').length;ctx.DriveSync.connect();await drain();assert.equal(intervals.length,1);assert.equal(calls.filter(c=>c.method!=='GET').length,writesBefore,'reconnect remains locked');
console.log('PASS: reconnect retains one polling timer');
authError=true;store.archive_memo_new='unsynced';intervals[0]();await drain();
assert.equal(ctx.DriveSync.isConnected(),false);assert.equal(store.archive_memo_new,'unsynced');assert.match(status.textContent,/인증/);
console.log('PASS: expired authentication keeps local edits and reports reconnect');
assert.equal(ctx.DriveSync._validateRemote({bookmarks:null}),false);
assert.equal(ctx.DriveSync._validateRemote({pinned:[123]}),false);
console.log('PASS: malformed remote state rejected');

authError=false;noCloud=true;remote=null;
new vm.Script(source).runInContext(ctx);
ctx.DriveSync.init(status);const before=calls.filter(c=>c.method!=='GET').length;ctx.DriveSync.connect();await drain();
assert.equal(calls.filter(c=>c.method!=='GET').length,before,'empty cloud connect must not create file');
await ctx.DriveSync.exportWithPassword(fixturePin);
assert.equal(calls.at(-1).method,'POST');assert.equal(remote.memos.new,'unsynced');
console.log('PASS: absent cloud state stays absent until authorized file creation');
store.archive_memo_failure='keep this';uploadFailure=true;
await assert.rejects(ctx.DriveSync.exportWithPassword(fixturePin),/500/);
assert.equal(store.archive_memo_failure,'keep this');assert.equal(remote.memos.failure,undefined);
uploadFailure=false;const failedWrites=calls.filter(c=>c.method!=='GET').length;intervals.at(-1)();await drain();
assert.equal(calls.filter(c=>c.method!=='GET').length,failedWrites,'failed export must not retry writing automatically');
await ctx.DriveSync.exportWithPassword(fixturePin);assert.equal(remote.memos.failure,'keep this');
console.log('PASS: failed upload retains local edits and retries only with a fresh password');
