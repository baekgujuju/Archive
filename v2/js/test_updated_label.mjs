import assert from 'node:assert/strict';import vm from 'node:vm';import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('./bootstrap.js',import.meta.url),'utf8').split('/* ------------------------------------------------------------------ */')[0];
const attrs={},el={textContent:'',setAttribute:(k,v)=>attrs[k]=v,removeAttribute:k=>delete attrs[k]};
const c=vm.createContext({document:{getElementById:()=>el}});new vm.Script(source).runInContext(c);
c.showContentUpdated({version:'2026-10-15T22:18:03Z'},false);assert.equal(el.textContent,'최신 업데이트 2026.10.16 07:18');
c.showContentUpdated({version:'2026-10-16T02:00:00Z',content_updated_at:'2026-10-15T22:18:03Z'},true);assert.equal(el.textContent,'최신 업데이트 2026.10.16 07:18 · 저장본');
c.showContentUpdated({version:'broken'},false);assert.equal(el.textContent,'최신 업데이트 시각 미확인');assert.equal(attrs.datetime,undefined);
console.log('PASS: content timestamp priority, KST conversion, cached label and invalid date');
