import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
const action=source.slice(source.indexOf('  async function runArticleAction'),source.indexOf('  async function cardClick'));
async function check(failure,inline=true){
 let prints=0,removed=false,alerts=[],events={},classes=new Set();
 const listeners={}; const img={complete:false,naturalWidth:0,addEventListener:(n,f)=>listeners[n]=f,removeEventListener:n=>delete listeners[n]};
 const area={querySelectorAll:()=>[img],remove(){removed=true;}};
 const button={disabled:false,closest:()=>null};
 const ctx=vm.createContext({Promise,Array,Error,setTimeout,clearTimeout,articleHtml:()=>'<p>actual article</p>',formatContent:()=>{},document:{getElementById:()=>null,createElement:()=>area,body:{appendChild:()=>{},classList:{add:n=>classes.add(n),remove:n=>classes.delete(n)}}},window:{print:()=>prints++,alert:m=>alerts.push(m),addEventListener:(n,f)=>events[n]=f,removeEventListener:n=>delete events[n]}});
 vm.runInContext(action+'\nthis.action=runArticleAction;',ctx);
 const promise=ctx.action('print',{article:{}},button,inline);
 assert.equal(prints,0,'print cannot start before an attachment loads'); assert.equal(img.loading,'eager');assert.equal(button.disabled,true);
 if(failure)listeners.error();else{img.complete=true;img.naturalWidth=100;listeners.load();}
 await promise;
 assert.equal(button.disabled,false);
 if(failure){assert.equal(prints,0);assert.equal(alerts.length,1);assert.equal(removed,true);assert.equal(classes.size,0);}
 else{assert.equal(prints,1);assert.equal(removed,false);events.afterprint();assert.equal(removed,true);assert.equal(classes.size,0);assert.equal(events.afterprint,undefined);}
}
await check(false);await check(true);await check(false,false);await check(true,false);
console.log('PASS: inline and reader print wait for all images, failed images block print, afterprint cleans target and screen state.');
