/**
 * test_drivesync.mjs — DriveSync 로직 테스트 (mock 기반, 실제 OAuth 없음)
 *
 * node:vm으로 app.js를 classic script로 실행한 뒤,
 * DriveSync의 _validateRemote/_mergeState/_collectMemos를 검증.
 *
 * 실행: node test_drivesync.mjs
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
let passed = 0, failed = 0;
function check(name, cond, detail = '') {
  if (cond) { passed++; console.log(`  ✓ ${name}`); }
  else { failed++; console.log(`  ✗ ${name}${detail ? ': ' + detail : ''}`); }
}

// 최소 DOM mock
const store = {};
const ctx = vm.createContext({
  window: {},
  document: {
    querySelector: () => null,
    createElement: () => ({ style: {} }),
    head: { appendChild: () => {} },
    addEventListener: () => {},
  },
  localStorage: {
    getItem: (k) => store[k] ?? null,
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    key: (i) => Object.keys(store)[i] || null,
    get length() { return Object.keys(store).length; },
  },
  console, setTimeout: () => 0, setInterval: () => 0, fetch: () => Promise.reject(new Error('no network')),
});

const src = readFileSync(join(__dirname, 'drive-sync.js'), 'utf8');
try {
  new vm.Script(src, { filename: 'drive-sync.js' }).runInContext(ctx);
} catch (e) {
  console.log('drive-sync.js 로드 실패:', e.message);
  process.exit(2);
}

const src2 = readFileSync(join(__dirname, 'app.js'), 'utf8');
try {
  new vm.Script(src2, { filename: 'app.js' }).runInContext(ctx);
} catch (e) {
  console.log('app.js 로드 실패:', e.message);
  process.exit(2);
}

const DS = ctx.DriveSync;
check('DriveSync 노출', !!DS);
check('_validateRemote 노출', typeof DS._validateRemote === 'function');
check('_mergeState 노출', typeof DS._mergeState === 'function');

console.log('[1] 원격 데이터 유효성 검증');
check('정상 데이터 통과', DS._validateRemote({ pinned: [], bookmarks: {}, updatedAt: 'x' }));
check('null 거부', !DS._validateRemote(null));
check('pinned 비배열 거부', !DS._validateRemote({ pinned: 'x' }));
check('bookmarks 배열 거부', !DS._validateRemote({ bookmarks: [] }));
check('memos 비객체 거부', !DS._validateRemote({ memos: 'x' }));
check('빈 객체 통과', DS._validateRemote({}));

console.log('[2] 병합 정책 (로컬+원격 모두 보존)');
const local = { pinned: ['a'], bookmarks: { x: '#e74c3c' }, names: {}, deletedReplies: [], deletedPosts: ['p1'], memos: { m1: 'local memo' } };
const remote = { pinned: ['b'], bookmarks: { y: '#3498db' }, names: { y: '파랑' }, deletedReplies: ['r1'], deletedPosts: ['p2'], memos: { m2: 'remote memo' } };
const merged = DS._mergeState(local, remote);
check('pinned union', merged.pinned.includes('a') && merged.pinned.includes('b'));
check('bookmarks 병합', merged.bookmarks.x === '#e74c3c' && merged.bookmarks.y === '#3498db');
check('로컬 우선 (충돌 시)', DS._mergeState({ bookmarks: { x: '#111' } }, { bookmarks: { x: '#222' } }).bookmarks.x === '#111');
check('deletedPosts union', merged.deletedPosts.includes('p1') && merged.deletedPosts.includes('p2'));
check('memos 병합', merged.memos.m1 === 'local memo' && merged.memos.m2 === 'remote memo');
check('updatedAt 갱신', !!merged.updatedAt);

console.log('[3] 메모 수집');
store['archive_memo_abc'] = 'test memo';
store['archive-pinned'] = '[]';
const memos = DS._collectMemos();
check('메모 수집됨', memos.abc === 'test memo');
check('비메모 키 제외', !('archive-pinned' in memos));

console.log(`\n결과: ${passed} 통과, ${failed} 실패`);
process.exit(failed ? 1 : 0);
