/**
 * test_integration.mjs — v2 통합 테스트 (node:vm 기반, 브라우저 테스트 아님)
 *
 * ⚠️ 이것은 실제 브라우저 테스트가 아닙니다.
 * node:vm으로 classic script 파싱과 기본 로직만 검증합니다.
 * 실제 브라우저 테스트는 MANUAL_BROWSER_CHECKLIST.md를 참조하여
 * 데스크톱 Chrome과 iOS Safari에서 수동으로 수행해야 합니다.
 *
 * 검증 내용:
 * 1. render.js / bootstrap.js / app.js가 classic script로 파싱되는지
 *    (top-level import/export 있으면 SyntaxError → 이전 무한로딩 버그 재발 방지)
 * 2. window.ArchiveRender 전역 노출
 * 3. articleCard() 렌더링 결과에 답글/미디어 섹션이 포함되는지
 * 4. normalizeArticle()의 media[] → mediaImages[] 정규화
 *
 * 실행: node js/test_integration.mjs
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    failed++;
    console.log(`  ✗ ${name}: ${e.message}`);
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'assertion failed');
}

// 최소 브라우저 환경 mock
function createContext() {
  const window = {};
  const document = {
    getElementById: () => null,
    createElement: () => ({ style: {}, appendChild: () => {} }),
    body: { appendChild: () => {}, style: {} },
  };
  const ctx = vm.createContext({
    window,
    document,
    localStorage: {
      _s: {},
      getItem(k) { return this._s[k] ?? null; },
      setItem(k, v) { this._s[k] = String(v); },
      removeItem(k) { delete this._s[k]; },
    },
    console,
    setTimeout: (fn) => 0,
    clearTimeout: () => {},
  });
  return { ctx, window };
}

function loadClassic(ctx, filename) {
  const src = readFileSync(join(__dirname, filename), 'utf8');
  // classic script로 파싱: import/export가 있으면 여기서 SyntaxError 발생
  const script = new vm.Script(src, { filename });
  script.runInContext(ctx);
}

console.log('[1] classic script 파싱 검증');
for (const f of ['render.js', 'bootstrap.js', 'app.js']) {
  test(`${f} classic 파싱`, () => {
    const { ctx } = createContext();
    try {
      loadClassic(ctx, f);
    } catch (e) {
      if (e instanceof SyntaxError) {
        throw new Error(`SyntaxError (export/import 잔재 의심): ${e.message}`);
      }
      // ReferenceError 등은 DOM 의존성 때문, 파싱 자체는 성공
      if (!e.message.includes('document') && !e.message.includes('window')) throw e;
    }
  });
}

console.log('[2] 전역 노출 검증');
{
  const { ctx, window } = createContext();
  // bootstrap.js는 DOM에 의존하므로 render.js만 로드
  try { loadClassic(ctx, 'render.js'); } catch (e) { /* DOM 의존 무시 */ }
  test('window.ArchiveRender 노출', () => {
    assert(window.ArchiveRender, 'ArchiveRender 없음');
    assert(typeof window.ArchiveRender.articleCard === 'function', 'articleCard 없음');
    assert(typeof window.ArchiveRender.repliesHtml === 'function', 'repliesHtml 없음');
    assert(typeof window.ArchiveRender.normalizeArticle === 'function', 'normalizeArticle 없음');
  });

  test('media[] → mediaImages[] 정규화', () => {
    const norm = window.ArchiveRender.normalizeArticle({
      type: 'x',
      media: [{ type: 'photo', url: 'https://example.com/img.jpg' }],
    });
    assert(Array.isArray(norm.mediaImages), 'mediaImages 미생성');
    assert(norm.mediaImages.length === 1, 'mediaImages 개수 오류');
    assert(norm.mediaImages[0][0] === 'https://example.com/img.jpg', 'URL 매핑 오류');
  });

  test('articleCard 답글 섹션 포함', () => {
    const html = window.ArchiveRender.articleCard({
      type: 'x',
      author: 'test',
      fullText: '본문',
      id: '123',
      replies: [
        { tweetId: 'r1', fullText: '답글1', author: { name: 'replier' }, createdAt: '1h' },
        { tweetId: 'r2', fullText: '답글2', author: { handle: '@r2' }, createdAt: '2h', isThread: true },
      ],
    }, { idx: 0 });
    assert(html.includes('class="replies"'), 'replies 섹션 없음');
    assert(html.includes('댓글 2개'), '답글 개수 표시 없음');
    assert(html.includes('답글1') && html.includes('답글2'), '답글 본문 없음');
    assert(html.includes('스레드'), '스레드 뱃지 없음');
  });

  test('articleCard 미디어 이미지 포함', () => {
    const html = window.ArchiveRender.articleCard({
      type: 'x',
      author: 'test',
      fullText: '본문',
      id: '456',
      media: [{ type: 'photo', url: 'https://example.com/p.jpg' }],
    }, { idx: 1 });
    assert(html.includes('post-media'), 'post-media 섹션 없음');
    assert(html.includes('https://example.com/p.jpg'), '미디어 URL 없음');
  });

  test('lightboxesHtml 생성', () => {
    const html = window.ArchiveRender.lightboxesHtml([
      { id: 'a1', author: 'test', mediaImages: [['t.jpg', 'f.jpg']] },
    ]);
    assert(html.includes('css-lightbox'), 'lightbox 섹션 없음');
    assert(html.includes('f.jpg'), 'full 이미지 없음');
  });
}

console.log(`\n결과: ${passed} 통과, ${failed} 실패`);
process.exit(failed ? 1 : 0);
