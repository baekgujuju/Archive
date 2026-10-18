/**
 * test_render.mjs — render.js 자체 테스트 (classic script 실행 방식)
 *
 * render.js는 classic <script>로 로드되므로, 테스트도 node:vm으로
 * classic script로 실행한 뒤 window.ArchiveRender 경유로 접근.
 * (ESM import 방식은 export가 제거되어 동작하지 않음)
 *
 * 실행: node test_render.mjs
 * (v2/js 디렉토리에서 실행)
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// render.js를 classic script로 실행
const ctx = vm.createContext({ window: {}, console });
const src = readFileSync(join(__dirname, 'render.js'), 'utf8');
new vm.Script(src, { filename: 'render.js' }).runInContext(ctx);
const {
  escapeHtml,
  sliceCp,
  linkify,
  linkifyHtml,
  topicsHtml,
  memoButtonHtml,
  lightboxId,
  lightboxesHtml,
  urlInlineHtml,
  normalizeArticle,
  articleCard,
  repliesHtml,
} = ctx.window.ArchiveRender;

// app.js도 classic script로 실행 (replyBody 테스트용 — IIFE는
// 로드 시 DOM을 건드리지 않으므로 최소 컨텍스트로 충분).
const appSrc = readFileSync(join(__dirname, 'app.js'), 'utf8');
new vm.Script(appSrc, { filename: 'app.js' }).runInContext(ctx);
const appReplyBody = ctx.window.ArchiveApp.replyBody;

let pass = 0;
let fail = 0;
function check(name, cond, detail = '') {
  if (cond) {
    pass++;
    console.log(`PASS: ${name}`);
  } else {
    fail++;
    console.log(`FAIL: ${name} ${detail}`);
  }
}

// 1. escapeHtml — Python html.escape(quote=True)와 동일
check('escape &<>"\'', escapeHtml('&<>"\'') === '&amp;&lt;&gt;&quot;&#x27;');
check('escape null', escapeHtml(null) === '');
check('escape undefined', escapeHtml(undefined) === '');
check('escape 한글 유지', escapeHtml('가나다') === '가나다');

// 2. linkify — bare URL 변환 + XSS 안전
const lf = linkify('보세요 https://example.com/a?b=1&c=2 끝.');
check('linkify 앵커 생성', lf.includes('<a href="https://example.com/a?b=1&amp;c=2"'));
check('linkify 텍스트 이스케이프', !lf.includes('<script>'));
const lf2 = linkify('<img src=x onerror=alert(1)> https://x.com.');
check('linkify XSS 차단', lf2.includes('&lt;img') && lf2.includes('<a href="https://x.com"'));
const lf3 = linkify('문장 끝 https://a.com.');
check('linkify trailing dot', lf3.includes('>https://a.com</a>.'));

// 3. linkifyHtml — 기존 <a> 보호
const lh = linkifyHtml('<p>보세요 <a href="https://keep.com">유지</a> 그리고 https://new.com</p>');
check('linkifyHtml 기존 a 유지', lh.includes('<a href="https://keep.com">유지</a>'));
check('linkifyHtml bare URL 변환', lh.includes('<a href="https://new.com"'));

// 4. topicsHtml
const th = topicsHtml(['투자', '차트']);
check('topicsHtml 2개', (th.match(/class="tag"/g) || []).length === 2);
check('topicsHtml 이스케이프', topicsHtml(['<b>']).includes('&lt;b&gt;'));
check('topicsHtml 빈배열', topicsHtml([]) === '');
check('topicsHtml null', topicsHtml(null) === '');

// 5. memoButtonHtml
const mb = memoButtonHtml('abc"123', '제목<title>');
check('memo id 이스케이프', mb.includes('data-memo-id="abc&quot;123"'));
check('memo title 이스케이프+50자', mb.includes('data-memo-title="제목&lt;title&gt;"'));

// 6. lightboxId — 결정적, 형식 lb-<16hex>-<idx>
const art0 = { id: 'post1', sourceUrl: 'https://x.com/a' };
const lid1 = lightboxId(art0, 0);
const lid2 = lightboxId(art0, 0);
const lid3 = lightboxId(art0, 1);
check('lightboxId 결정적', lid1 === lid2);
check('lightboxId 형식', /^lb-[0-9a-f]{16}-0$/.test(lid1), lid1);
check('lightboxId 인덱스 구분', lid1 !== lid3);

// 7. articleCard — X 글
const xArticle = {
  type: 'x',
  author: '트레이더 제씨',
  handle: '@Trader_Jesse_',
  fullText: '첫 줄 제목입니다\n둘째 줄 본문 https://example.com 링크',
  createdAt: '9:36 PM · Oct 2, 2026',
  tweetId: '12345',
  topics: ['투자', '차트'],
  mediaImages: [['thumb1.jpg', 'full1.jpg']],
  has_chart: true,
  sourceUrl: 'https://x.com/Trader_Jesse_/status/12345',
  urlContents: [],
  quoted: null,
};
const card = articleCard(xArticle, { idx: 7 });
check('card details.article', card.includes('<details class="article x-card"'));
check('card data-pin-id', card.includes('data-pin-id="12345"'));
check('card data-chart', card.includes('data-chart="1"'));
check('card data-idx', card.includes('data-idx="7"'));
check('card 제목 90자', card.includes('<h3>첫 줄 제목입니다'));
check('card meta', card.includes('작성 9:36 PM · Oct 2, 2026'));
check('card summary 링크', card.includes('<a href="https://example.com"'));
check('card 삭제 버튼', card.includes('class="delete-btn"'));
check('card 메모 버튼', card.includes('data-memo-id="12345"'));
check('card 라이트박스 링크', card.includes('class="media-link"'));
check('card read-window-btn idx', card.includes('data-idx="7">새 창에서 읽기'));

// 8. articleCard — XSS 글
const xssArticle = {
  type: 'x',
  author: '<img src=x onerror=alert(1)>',
  fullText: '<script>alert(2)</script> 제목',
  tweetId: '999',
  sourceUrl: 'https://x.com/a/999',
};
const xssCard = articleCard(xssArticle, { idx: 0 });
check('card XSS author 차단', !xssCard.includes('<img src=x onerror'));
check('card XSS title 차단', xssCard.includes('&lt;script&gt;'));

// 9. articleCard — 웹글
const webArticle = {
  type: 'web',
  title: '웹글 제목',
  summary: '웹글 요약',
  author: '홍길동',
  source_domain: 'example.com',
  date_saved: '2026-10-13',
  source_url: 'https://example.com/post',
  topics: [],
  id: 'web1',
};
const wcard = articleCard(webArticle, { idx: 0 });
check('web card 출처 유형 배지 생략', !wcard.includes('type-badge'));
check('web card meta', wcard.includes('example.com · 저장 2026-10-13'));
check('web card x-card 없음', !wcard.includes('x-card'));

// 10. articleCard — 인용글
const qArticle = {
  type: 'x',
  author: 'A',
  fullText: '본문',
  tweetId: '111',
  sourceUrl: 'https://x.com/a/111',
  quoted: {
    fullText: '인용 <b>본문</b>',
    author: { name: '인용자<script>' },
    createdAt: '어제',
    media: ['https://img.com/q.jpg'],
  },
};
const qcard = articleCard(qArticle, { idx: 0 });
check('quoted 섹션', qcard.includes('class="quoted"'));
check('quoted XSS 차단', qcard.includes('인용자&lt;script&gt;') && qcard.includes('&lt;b&gt;'));

// 11. urlInlineHtml
const uiArticle = {
  urlContents: [
    { url: 'https://a.com', title: '제목', content: '# 헤더\n본문 **굵게** [링크](https://b.com)', status: 'ok' },
    { url: 'https://b.com', title: '', content: '', status: 'pending' },
  ],
};
const ui = urlInlineHtml(uiArticle);
check('urlInline pending 제외', !ui.includes('https://b.com'));
check('urlInline 마크다운 정리', ui.includes('헤더') && !ui.includes('# 헤더'));

// 12. lightboxesHtml
const lb = lightboxesHtml([xArticle]);
check('lightboxes 섹션', lb.includes('class="css-lightbox"'));
check('lightboxes id 일치', lb.includes(`id="${lightboxId(xArticle, 0)}"`));

// 13. normalizeArticle — author 객체 정규화
const na1 = normalizeArticle({ author: { handle: '@foo', name: '홍길동 (@foo)' } });
check('normalize handle 제거', na1.author === '홍길동' && na1.handle === '@foo');
const na2 = normalizeArticle({ author: { handle: '@bar', name: '김철수' } });
check('normalize 일반', na2.author === '김철수' && na2.handle === '@bar');
const na3 = normalizeArticle({ author: '문자열저자' });
check('normalize 문자열 통과', na3.author === '문자열저자');
const na4 = normalizeArticle({});
check('normalize 빈 객체', na4.author === '' && na4.topics.length === 0);

// 14. sliceCp — 이모지 코드포인트 단위 자르기
check('sliceCp 이모지', sliceCp('🧠가나다', 2) === '🧠가');
check('sliceCp 일반', sliceCp('abcdef', 3) === 'abc');

// 15. articleCard — author 객체 입력도 처리
const dictAuthorCard = articleCard({
  type: 'x',
  author: { handle: '@Trader_Jesse_', name: '트레이더 제씨 (@Trader_Jesse_)' },
  fullText: '제목줄\n본문',
  tweetId: '555',
  sourceUrl: 'https://x.com/a/555',
}, { idx: 0 });
check('card author 객체 정규화', dictAuthorCard.includes('트레이더 제씨') && !dictAuthorCard.includes('[object Object]'));

// 16. repliesHtml — 답글 번역 표시 (원문 → 문단 정렬 번역)
const replyBase = {
  author: { handle: '@u', name: 'User' },
  tweetId: '222',
  createdAt: '2026-10-18',
};
const rh1 = repliesHtml({
  replies: [{ ...replyBase, fullText: 'This is a great point.',
    translation: { status: 'ok', paragraphs: ['This is a great point.'],
      translated: ['훌륭한 지적이다.'] } }],
});
check('답글 번역 표시', rh1.includes('This is a great point.') && rh1.includes('훌륭한 지적이다.') && rh1.includes('한국어 번역'));
check('답글 원문이 번역보다 먼저', rh1.indexOf('This is a great point.') < rh1.indexOf('훌륭한 지적이다.'));
const rh2 = repliesHtml({ replies: [{ ...replyBase, fullText: '한국어 답글입니다.' }] });
check('답글 번역 없으면 원문만', rh2.includes('한국어 답글입니다.') && !rh2.includes('translation-ko'));
const rh3 = repliesHtml({
  replies: [{ ...replyBase, fullText: 'Hello world here.',
    translation: { status: 'ok', paragraphs: ['Hello world here.'],
      translated: ['<script>alert(1)</script>안녕'] } }],
});
check('답글 번역 이스케이프', rh3.includes('&lt;script&gt;') && !rh3.includes('<script>alert(1)'));
const rh4 = repliesHtml({
  replies: [{ ...replyBase, fullText: 'Edited reply text here.',
    translation: { status: 'ok', paragraphs: ['Original reply text here.'],
      translated: ['원본 답글 번역'] } }],
});
check('답글 원문·캐시 불일치 시 번역 미표시', rh4.includes('Edited reply text here.') && !rh4.includes('원본 답글 번역'));
const rh5 = repliesHtml({
  replies: [{ ...replyBase, fullText: 'Same text here.',
    translation: { status: 'ok', paragraphs: ['Same text here.'],
      translated: ['Same text here.'] } }],
});
check('답글 번역이 원문과 같으면 미표시', !rh5.includes('translation-ko'));
const rh6 = repliesHtml({
  replies: [{ ...replyBase, fullText: 'First para here.\n\nSecond para here.',
    translation: { status: 'ok', paragraphs: ['First para here.', 'Second para here.'],
      translated: ['첫 번째 문단.', '두 번째 문단.'] } }],
});
check('답글 문단 정렬', rh6.indexOf('First para here.') < rh6.indexOf('첫 번째 문단.') && rh6.indexOf('첫 번째 문단.') < rh6.indexOf('Second para here.') && rh6.indexOf('Second para here.') < rh6.indexOf('두 번째 문단.'));
const rh7 = repliesHtml({
  replies: [{ ...replyBase, fullText: 'First para here. Second para here.',
    translation: { status: 'ok', paragraphs: ['First para here.', 'Second para here.'],
      translated: ['첫 번째 문단.', '두 번째 문단.'] } }],
});
check('답글 문단 경계 변경 시 번역 미표시', rh7.includes('First para here. Second para here.') && !rh7.includes('첫 번째 문단.'));
// 17. text 전용 답글 (fullText 없음) — 렌더도 text 폴백으로 원문/번역 표시.
const rh8 = repliesHtml({
  replies: [{ ...replyBase, text: 'This is a great point.',
    translation: { status: 'ok', paragraphs: ['This is a great point.'],
      translated: ['훌륭한 지적이다.'] } }],
});
check('render.js replyBodyHtml text 폴백', rh8.includes('This is a great point.') && rh8.includes('훌륭한 지적이다.') && rh8.includes('한국어 번역'));
const appRh = appReplyBody({ text: 'This is a great point.',
  translation: { status: 'ok', paragraphs: ['This is a great point.'],
    translated: ['훌륭한 지적이다.'] } });
check('app.js replyBody text 폴백', appRh.includes('This is a great point.') && appRh.includes('훌륭한 지적이다.') && appRh.includes('한국어 번역'));

console.log(`\n결과: ${pass} 통과, ${fail} 실패`);
process.exit(fail ? 1 : 0);
