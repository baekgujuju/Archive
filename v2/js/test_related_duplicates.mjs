import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const ctx={URL};vm.createContext(ctx);vm.runInContext(fs.readFileSync(new URL('./ui.js',import.meta.url),'utf8'),ctx);
const record=(id,title,type='ib',text='')=>({id,type,author:'자료실',topics:['IB'],article:{title,fullText:text}});
const rows=[record('a','IB 공부방법'),record('b','IB DP 개인적 고찰'),record('c',' IB  DP 개인적 고찰 '),record('d','외국인학교 교육과정'),record('e','외국인학교 교육과정'),record('f','IB 공부방법')];
ctx.ArchiveUI.related(rows);assert.deepEqual(Array.from(rows[0].related,r=>r.id),['b','d']);
const x=[record('x','', 'x','첫번째 투자 글'),record('y','', 'x','두번째 투자 글'),record('z','', 'x','세번째 투자 글')];ctx.ArchiveUI.related(x);assert.equal(x[0].related.length,2);
console.log('PASS: duplicate IB related titles/self entries omitted; distinct X posts without explicit title retained');
const urls=[record('a','IB 원문'),record('b','교육과정'),record('c','교육과정_2'),record('d','교육과정')];urls[1].article.source_url='https://m.blog.naver.com/PostView.naver?blogId=example&logNo=123&noTrackingCode=true';urls[2].article.source_url='https://m.blog.naver.com/PostView.naver?blogId=example&logNo=123&tc=shared';urls[3].article.source_url='https://m.blog.naver.com/PostView.naver?blogId=example&logNo=456';ctx.ArchiveUI.related(urls);assert.deepEqual(Array.from(urls[0].related,r=>r.id),['b','d']);
console.log('PASS: same Naver article with tracking/title variations deduplicated; distinct article IDs retained');
