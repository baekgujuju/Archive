import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('./bootstrap.js',import.meta.url),'utf8');const fn=source.slice(source.indexOf('function applyToolMetadata('),source.indexOf('function renderArticles('));const ctx={};vm.createContext(ctx);vm.runInContext(fn,ctx);
const a={tweetId:'1',fullText:'Click to Follow Demo',topics:['기타']},valid={status:'verified',title:'REA',summary_ko:'앱과 바이너리 구조를 분석하는 도구',category:'역공학',repo_url:'https://github.com/morluto/rea'};
ctx.applyToolMetadata([a],{'1':valid});assert.equal(a.title,'REA');assert.deepEqual([...a.topics],['AI툴']);assert.equal(a.toolLinks[0].url,valid.repo_url);
ctx.applyToolMetadata([a],{'1':valid});assert.equal(a.topics.length,1);
for(const url of ['javascript:alert(1)','https://github.com.evil.test/foo/bar','https://evil.test/morluto/rea']){const b={id:'x',topics:['투자']};ctx.applyToolMetadata([b],{x:{...valid,repo_url:url}});assert.equal(b.toolDescription,undefined);}
const b={id:'2',title:'원래 제목',topics:['투자']};ctx.applyToolMetadata([b],{'2':{...valid,status:'pending'}});assert.equal(b.toolDescription,undefined);ctx.applyToolMetadata([b],{'2':valid});assert.equal(b.title,'원래 제목');assert.deepEqual([...b.topics],['투자','AI툴']);
console.log('PASS: verified metadata, stable IDs, duplicate prevention, pending omission, unsafe URL rejection, original titles/topics preservation');
