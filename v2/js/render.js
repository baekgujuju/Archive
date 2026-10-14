/**
 * render.js — 아카이브 v2 카드 렌더링 모듈
 *
 * `build_dashboard.py`의 아래 함수들을 JavaScript로 포팅:
 *   - escape()            → escapeHtml()
 *   - topics_of()         → (articleCard 내부, topicsHtml()로 분리)
 *   - linkify()           → linkify()
 *   - linkify_html()      → linkifyHtml()
 *   - url_inline_html()   → urlInlineHtml()
 *   - memo_button()       → memoButtonHtml()
 *   - lightbox_id()       → lightboxId()
 *   - lightboxes_html()   → lightboxesHtml()
 *   - article_html()      → articleCard()
 *
 * 보안 원칙: 모든 사용자 데이터는 innerHTML 삽입 전 반드시 escapeHtml()로
 * 이스케이프한다. (fullHtml은 빌드 파이프라인에서 정제한 신뢰 HTML로 취급)
 *
 * 사용법 (ES 모듈):
 *   import { articleCard } from './render.js';
 *   container.innerHTML = articles.map((a, i) => articleCard(a, { idx: i })).join('');
 *
 * 사용법 (일반 스크립트):
 *   <script src="js/render.js"></script>
 *   <!-- window.ArchiveRender.articleCard(...) 로 접근 -->
 */

'use strict';

/* ------------------------------------------------------------------ */
/* 1. escapeHtml — Python html.escape(s, quote=True) 포팅             */
/* ------------------------------------------------------------------ */

/**
 * HTML 이스케이프. Python `html.escape(str(value or ""), quote=True)`와 동일:
 * `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`, `"` → `&quot;`, `'` → `&#x27;`
 *
 * @param {*} value — 이스케이프할 값 (null/undefined → "")
 * @returns {string} 이스케이프된 문자열
 */
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

/* ------------------------------------------------------------------ */
/* 1b. sliceCp — 코드포인트 기준 문자열 자르기                       */
/* ------------------------------------------------------------------ */

/**
 * Python의 str[:n]과 동일하게 코드포인트 단위로 자름.
 * (JS String.slice는 UTF-16 코드유닛 단위라 이모지 등 astral 문자에서
 *  Python과 글자 수가 달라짐)
 *
 * @param {string} str — 입력 문자열
 * @param {number} n — 자를 코드포인트 수
 * @returns {string} 앞에서 n 코드포인트까지의 문자열
 */
function sliceCp(str, n) {
  return [...String(str ?? '')].slice(0, n).join('');
}

/* ------------------------------------------------------------------ */
/* 2. linkify — Python linkify() 포팅                                 */
/* ------------------------------------------------------------------ */

const URL_RE = /https?:\/\/[^\s<>"')\]]+/g;
const URL_TRAIL_CHARS = '.,;:!?…';

/**
 * 일반 텍스트 안의 bare URL을 하이퍼링크로 변환.
 * XSS 안전: URL을 플레이스홀더로 치환 → 전체 escape → 앵커 복원.
 * URL 끝의 문장부호(.,;:!?…)는 링크에서 제외하고 뒤에 붙임.
 * (Python linkify()와 동일한 알고리즘)
 *
 * @param {string} text — 일반 텍스트
 * @returns {string} URL이 링크로 변환된 HTML
 */
function linkify(text) {
  const urls = [];
  const tmp = String(text ?? '').replace(URL_RE, (m) => {
    urls.push(m);
    return `\u0000URL${urls.length - 1}\u0000`;
  });
  let out = escapeHtml(tmp);
  urls.forEach((u, i) => {
    let eu = escapeHtml(u);
    let trail = '';
    while (eu.length > 0 && URL_TRAIL_CHARS.includes(eu[eu.length - 1])) {
      trail = eu[eu.length - 1] + trail;
      eu = eu.slice(0, -1);
    }
    out = out.split(`\u0000URL${i}\u0000`).join(
      `<a href="${eu}" target="_self" rel="noopener">${eu}</a>${escapeHtml(trail)}`
    );
  });
  return out;
}

/* ------------------------------------------------------------------ */
/* 3. linkifyHtml — Python linkify_html() 포팅 (단순화)                */
/* ------------------------------------------------------------------ */

/**
 * 신뢰 HTML 안의 bare URL을 하이퍼링크로 변환.
 * 기존 <a>…</a> 블록은 보호하고, 태그 바깥 텍스트 노드에만 linkify 적용.
 * (Python linkify_html()의 단순화 버전 — 빌드 시점에 이미 정제된 HTML 가정)
 *
 * @param {string} htmlContent — 신뢰할 수 있는 HTML
 * @returns {string} bare URL이 링크로 변환된 HTML
 */
function linkifyHtml(htmlContent) {
  if (!htmlContent) return '';
  const anchors = [];
  // 1) 기존 <a>…</a> 블록 통째로 보호
  let tmp = String(htmlContent).replace(/<a\b[^>]*>[\s\S]*?<\/a>/gi, (m) => {
    anchors.push(m);
    return `\u0001A${anchors.length - 1}\u0001`;
  });
  // 2) 태그 바깥(짝수 인덱스 세그먼트)의 텍스트에만 linkify 적용
  tmp = tmp
    .split(/(<[^>]*>)/g)
    .map((seg, i) => (i % 2 === 0 ? linkify(seg) : seg))
    .join('');
  // 3) 보호했던 <a> 블록 복원
  anchors.forEach((a, i) => {
    tmp = tmp.split(`\u0001A${i}\u0001`).join(a);
  });
  return tmp;
}

/* ------------------------------------------------------------------ */
/* 4. topicsHtml — 태그 렌더링 (Python article_html() 내 tags 부분)    */
/* ------------------------------------------------------------------ */

const TAG_COLORS = [
  ['#17675c', '#e4efe8'],
  ['#974728', '#fae8dc'],
  ['#64528b', '#eee7f5'],
  ['#766019', '#f5edce'],
];

/**
 * 주제 문자열에서 태그 색상 인덱스를 계산.
 * Python: tag_colors[sum(map(ord, topic)) % len(tag_colors)] 와 동일.
 * (for…of + codePointAt으로 Python의 ord()와 같은 코드포인트 합산)
 *
 * @param {string} topic — 주제 문자열
 * @returns {number} TAG_COLORS 인덱스
 */
function tagColorIndex(topic) {
  let sum = 0;
  for (const ch of String(topic)) sum += ch.codePointAt(0);
  return sum % TAG_COLORS.length;
}

/**
 * 주제 목록을 태그 HTML로 렌더링.
 *
 * @param {string[]} [topics] — 주제 문자열 배열 (없으면 빈 문자열)
 * @returns {string} `<span class="tag">…</span>` 들을 이어붙인 HTML
 */
function topicsHtml(topics) {
  const list = Array.isArray(topics) ? topics : [];
  return list
    .map((topic) => {
      const [ink, paper] = TAG_COLORS[tagColorIndex(topic)];
      return `<span class="tag" style="--tag-ink:${ink};--tag-paper:${paper}">${escapeHtml(topic)}</span>`;
    })
    .join('');
}

/* ------------------------------------------------------------------ */
/* 5. memoButtonHtml — Python memo_feature.memo_button() 포팅         */
/* ------------------------------------------------------------------ */

/**
 * 메모 버튼 HTML (북마크 옆 아이콘, 메모 있을 때만 표시).
 *
 * @param {string} memoId — 메모 식별자 (pin-id와 동일)
 * @param {string} [title=""] — data-memo-title용 (50자 제한)
 * @returns {string} 메모 버튼 HTML
 */
function memoButtonHtml(memoId, title = '') {
  const safeId = escapeHtml(String(memoId ?? ''));
  const safeTitle = escapeHtml(sliceCp(title ?? '', 50));
  return (
    `<button type="button" class="memo-btn memo-icon-btn memo-hidden" ` +
    `data-memo-id="${safeId}" data-memo-title="${safeTitle}" title="메모">` +
    `<svg viewBox="0 0 24 24"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5z"/>` +
    `<path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></svg></button>`
  );
}

/* ------------------------------------------------------------------ */
/* 6. lightboxId / lightboxesHtml                                     */
/* ------------------------------------------------------------------ */

/**
 * FNV-1a 32비트 해시 → 8자리 hex.
 *
 * @param {string} str — 입력 문자열
 * @param {number} [seed=0x811c9dc5] — 시드
 * @returns {string} 8자리 hex 문자열
 */
function fnv1a(str, seed = 0x811c9dc5) {
  let h = seed >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

/**
 * 라이트박스 ID 생성. Python lightbox_id()와 동일한 형식
 * `lb-<16hex>-<index>` (해시 알고리즘은 다름 — v2 내부 일관성만 보장).
 *
 * @param {object} article — 글 객체
 * @param {number} index — 이미지 인덱스
 * @returns {string} 라이트박스 ID
 */
function lightboxId(article, index) {
  const key = String(article?.id ?? article?.sourceUrl ?? article?.source_url ?? '');
  return `lb-${fnv1a(key)}${fnv1a(key, 0x01000193)}-${index}`;
}

/**
 * 이미지 확대용 라이트박스 오버레이 HTML 생성.
 * mediaImages 항목은 [thumb, full] 배열 또는 {thumb, full} 객체를 지원.
 *
 * @param {object[]} [articles] — 글 객체 배열
 * @returns {string} `<section class="css-lightbox">…</section>` 들을 이어붙인 HTML
 */
function lightboxesHtml(articles) {
  const out = [];
  for (const article of articles || []) {
    const media = article?.mediaImages || [];
    const author = article?.author || '';
    media.forEach((pair, index) => {
      const full = Array.isArray(pair) ? pair[1] : pair?.full;
      if (!full) return;
      const lid = lightboxId(article, index);
      out.push(
        `<section class="css-lightbox" id="${lid}" role="dialog" aria-label="이미지 확대">` +
          `<a href="#articles" class="css-lightbox-close" aria-label="이미지 닫기">×</a>` +
          `<input type="checkbox" class="css-lightbox-toggle" id="${lid}-size">` +
          `<label for="${lid}-size">원래 크기로 보기</label>` +
          `<div class="css-lightbox-stage"><img src="${escapeHtml(full)}" ` +
          `alt="${escapeHtml(author)} 게시물 이미지 ${index + 1} 확대" loading="lazy"></div>` +
          `<p class="css-lightbox-help">원래 크기 선택 후 스크롤하여 작은 글자를 읽을 수 있습니다.</p></section>`
      );
    });
  }
  return out.join('');
}

/* ------------------------------------------------------------------ */
/* 7. urlInlineHtml — Python url_inline_html() 포팅                   */
/* ------------------------------------------------------------------ */

/**
 * 본문 URL 스크랩 내용을 인라인으로 표시 (Python url_inline_html() 포팅).
 * 마크다운 기호 가볍게 정리, 본문 1200자 제한.
 *
 * @param {object} article — 글 객체 (urlContents 배열 포함 가능)
 * @returns {string} 인라인 URL 섹션 HTML (없으면 "")
 */
function urlInlineHtml(article) {
  const items = [];
  for (const uc of article?.urlContents || []) {
    const url = uc?.url || '';
    const title = String(uc?.title || url).trim();
    let body = String(uc?.content || '').trim();
    const status = uc?.status || '';
    if (!body && status === 'pending') continue;
    if (!body) continue;
    // 마크다운 기호 가볍게 정리 (이미지/링크는 텍스트로)
    body = body.replace(/!\[([^\]]*)\]\([^)]*\)/g, '[이미지: $1]');
    body = body.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1');
    body = body.replace(/^#{1,6}\s+/gm, '');
    body = body.trim();
    if (body.length > 1200) body = sliceCp(body, 1200).trimEnd() + '…';
    items.push(
      '<div class="url-inline-item">' +
        `<div class="url-inline-title">📄 <a href="${escapeHtml(url)}" target="_self" rel="noopener noreferrer">${escapeHtml(title)}</a></div>` +
        `<div class="url-inline-url">${escapeHtml(url)}</div>` +
        `<div class="url-inline-content">${escapeHtml(body)}</div>` +
        '</div>'
    );
  }
  if (!items.length) return '';
  return '<div class="url-inline"><div class="url-inline-head">🔗 본문 링크 내용</div>' + items.join('') + '</div>';
}

/* ------------------------------------------------------------------ */
/* 7b. normalizeArticle — 빌드 파이프라인 정규화의 JS 미러            */
/* ------------------------------------------------------------------ */

/**
 * v1 빌드 파이프라인(build_dashboard.py 1477~1482행)의 author 정규화를
 * JS에서 미러링. 파이프라인이 이미 정규화했다면 no-op이다.
 *
 *  - author가 {name, handle} 객체면 name/handle 문자열로 분리
 *  - name이 "(handle)"로 끝나면 해당 부분 제거
 *  - topics가 없으면 빈 배열
 *
 * @param {object} article — 원본 글 객체 (수정하지 않고 복사본 반환)
 * @returns {object} 정규화된 글 객체 복사본
 */
function normalizeArticle(article) {
  const a = Object.assign({}, article || {});
  let author = a.author;
  let handle = a.handle || '';
  if (author && typeof author === 'object' && !Array.isArray(author)) {
    handle = author.handle || handle;
    let name = author.name || handle || '저자 미확인';
    name = String(name);
    if (handle && name.endsWith(`(${handle})`)) {
      name = name.slice(0, -(handle.length + 2)).trim();
    }
    author = name.trim();
  } else {
    author = author || '';
  }
  a.author = author;
  a.handle = handle;
  if (!Array.isArray(a.topics)) a.topics = [];
  if(!a.topics.length && !a.category && (a.fullText || a.fullHtml || a.summary)) a.topics=['기타'];
  // 카테고리 → 주제 매핑 (raw category가 topics로 매핑되지 않는 문제 수정)
  // 예: archify 글의 category "AI툴" → topics ["AI툴"]
  if (!a.topics.length && a.category && typeof a.category === 'string') {
    a.topics = [a.category === '미수집(빈캡처)' ? '기타' : a.category];
  }
  if(a.type==='ib'){a.subject=a.subject||(a.topics[0]||'기타');a.category=a.category||(a.topics[1]||'기타');}
  // 미디어 정규화: raw JSON의 media[] ({type, url}) → mediaImages[] ([thumb, full])
  // render.js/articleCard/lightboxesHtml은 mediaImages[]를 읽음
  if (!Array.isArray(a.mediaImages) || !a.mediaImages.length) {
    const rawMedia = a.media;
    if (Array.isArray(rawMedia) && rawMedia.length) {
      const pairs = [];
      for (const m of rawMedia) {
        const url = typeof m === 'string' ? m : m && (m.url || m.src);
        if (url) pairs.push([url, url]); // thumb/full 동일 (원격 URL)
      }
      if (pairs.length) a.mediaImages = pairs;
    }
  }
  return a;
}

/**
 * 답글(댓글) HTML 생성. v1 build_mobile.py 598~617행 패턴의 JS 포팅.
 * `<details class="replies">` 접기/펼치기 구조, 삭제 버튼 포함.
 *
 * @param {object} article — 글 객체 (replies 배열 포함 가능)
 * @returns {string} 답글 섹션 HTML (없으면 "")
 */
function repliesHtml(article) {
  const replies = Array.isArray(article?.replies)
    ? article.replies.filter((r) => r && typeof r === 'object')
    : [];
  if (!replies.length) return '';
  const items = [];
  for (const r of replies) {
    const ra = r.author || {};
    const rname = ra.name || ra.handle || '';
    const rtext = String(r.fullText || '');
    const rwhen = String(r.createdAt || '');
    const rtid = String(r.tweetId || r.id || '');
    const badge = r.isThread ? ' <span class="thread-badge">스레드</span>' : '';
    items.push(
      `<div class="reply" data-reply-id="${escapeHtml(rtid)}">` +
        `<div class="reply-head"><span class="reply-author">${escapeHtml(rname)}</span>` +
        `<span class="reply-time">${escapeHtml(rwhen)}</span>${badge}` +
        `<button type="button" class="reply-del" data-reply-id="${escapeHtml(rtid)}" aria-label="댓글 삭제" title="댓글 삭제">×</button></div>` +
        `<p class="reply-text">${escapeHtml(rtext)}</p></div>`
    );
  }
  return (
    `<details class="replies"><summary>💬 댓글 ${replies.length}개</summary>` +
    `<div class="reply-list">${items.join('')}</div></details>`
  );
}

/* ------------------------------------------------------------------ */
/* 8. articleCard — Python article_html() 포팅 (핵심)                 */
/* ------------------------------------------------------------------ */

/**
 * 글 카드 HTML 생성. Python `article_html(article, featured, idx, related)`의
 * JavaScript 포팅. 동일한 클래스명·data 속성을 사용하여 기존 CSS와 호환.
 *
 * v2 JSON 필드 (posts-x.json / posts-web.json):
 *   type("x"|"web"), author, handle, fullText, fullHtml, title, summary,
 *   createdAt, sourceUrl/source_url, source_domain, date_saved,
 *   topics[], mediaImages[[thumb,full]…], quoted{}, urlContents[],
 *   has_chart, id/tweetId, _idx
 *
 * @param {object} article — 글 객체
 * @param {object} [options] — 렌더 옵션
 * @param {boolean} [options.featured=false] — LATEST PICK 강조 표시
 * @param {number} [options.idx=0] — 카드 인덱스 (read-window-btn data-idx)
 * @param {object[]} [options.related=[]] — 연관 게시물 (최대 5개 표시)
 * @returns {string} `<details class="article">…</details>` 카드 HTML
 */
function articleCard(article, options = {}) {
  const { featured = false, idx = 0, related = [] } = options;
  const a = normalizeArticle(article);
  const isX = a.type === 'x';
  const author = a.author || '';
  const text = a.fullText || '';
  const MISSING = '본문이 수집되지 않은 게시물입니다. X 원문에서 확인하세요.';

  // 제목: X 글은 첫 줄(90자), 웹글은 title 필드 → 없으면 fullText 첫 줄에서 유도
  const webTitleFallback = !isX && !a.title && text ? sliceCp(text.trim().split('\n')[0], 90) : '';
  const title = isX ? sliceCp(text.trim().split('\n')[0], 90) || author : a.title || webTitleFallback || author;
  // 요약: X 글은 150자 미리보기, 웹글은 summary 필드
  const summary = isX ? (text ? sliceCp(text, 150) + (text.length > 150 ? '…' : '') : MISSING) : a.summary || sliceCp(text, 150);
  const fullHtml = a.fullHtml && typeof window !== 'undefined' && window.ArchiveApp?.sanitizeHtml
    ? window.ArchiveApp.sanitizeHtml(a.fullHtml) : a.fullHtml;
  const fullText = isX ? text || MISSING : text || a.summary || '';

  // 연관 게시물 HTML (최대 5개)
  let relatedHtml = '';
  if (related && related.length) {
    const items = [];
    for (const r of related.slice(0, 5)) {
      const rTitle =
        r.type === 'x' ? sliceCp(String(r.fullText || '').trim().split('\n')[0], 60) || r.author || '' : r.title || '';
      const rAuthor = r.author || '';
      // data-target-idx로 해당 카드 찾기 (JS에서 스크롤 이동)
      items.push(
        `<li><a href="#" class="related-link" data-target-idx="${escapeHtml(String(r._idx ?? ''))}">${escapeHtml(rTitle)}</a> ` +
          `<span class="ai-author">— ${escapeHtml(rAuthor)}</span></li>`
      );
    }
    if (items.length) {
      relatedHtml = `<div class="related-section"><h4>🔗 연관 게시물</h4><ul class="related-list">${items.join('')}</ul></div>`;
    }
  }

  const topics = Array.isArray(a.topics) ? a.topics : [];
  const handle = isX ? `<span class="handle">${escapeHtml(a.handle || '')}</span>` : '';
  const meta = isX ? `작성 ${a.createdAt || '날짜 미확인'}` : `${a.source_domain || ''} · 저장 ${a.date_saved || ''}`;

  // 이미지 (썸네일 → 라이트박스 링크)
  const imgs = [];
  const mediaImages = a.mediaImages || [];
  mediaImages.forEach((pair, index) => {
    const thumb = Array.isArray(pair) ? pair[0] : pair?.thumb;
    if (!thumb) return;
    const lid = lightboxId(a, index);
    imgs.push(
      `<a class="media-link" href="#${lid}" aria-label="이미지 ${index + 1} 확대 보기">` +
        `<img src="${escapeHtml(thumb)}" alt="${escapeHtml(author)} 게시물 이미지 ${index + 1}" loading="lazy"></a>`
    );
  });
  const media = imgs.length ? `<div class="post-media">${imgs.join('')}</div>` : '';

  // 인용글 섹션 (quoted) — 기본 표시(open), 접기 가능
  let quotedHtml = '';
  const q = a.quoted;
  if (q && typeof q === 'object' && !Array.isArray(q)) {
    const qtext = String(q.fullText || '').trim();
    const qauthor = q.author || {};
    const qname = qauthor.name || qauthor.handle || '';
    const qwhen = q.createdAt || '';
    const qimgs = [];
    for (const m of q.media || []) {
      const mu = typeof m === 'string' ? m : m?.url || m?.src || '';
      if (mu) qimgs.push(`<img src="${escapeHtml(mu)}" loading="lazy" alt="인용글 이미지">`);
    }
    const qmediaHtml = qimgs.length ? `<div class="quoted-media">${qimgs.join('')}</div>` : '';
    if (qtext || qimgs.length) {
      const qlabel = qname ? `🔗 인용글 · ${escapeHtml(qname)}` : '🔗 인용글';
      quotedHtml =
        `<details class="quoted" open><summary>${qlabel}</summary>` +
        `<div class="quoted-body">` +
        `<div><span class="quoted-author">${escapeHtml(qname)}</span>` +
        `<span class="quoted-time">${escapeHtml(qwhen)}</span></div>` +
        (qtext ? `<p class="quoted-text">${escapeHtml(qtext)}</p>` : '') +
        qmediaHtml +
        `</div></details>`;
    }
  }

  const rawUrl = a.sourceUrl || a.source_url || '';
  const url = /^https?:\/\//i.test(rawUrl) ? rawUrl : '';
  const urlInline = urlInlineHtml(a);
  const pinId = String(a.id ?? a.tweetId ?? a.sourceUrl ?? '');
  const memoId = a.memoId || pinId || String(idx);
  const dataIdx = a._idx ?? idx;

  return (
    `<details class="article${featured ? ' featured' : ''}${isX ? ' x-card' : ''}" ` +
    `data-type="${escapeHtml(a.type)}" data-author="${escapeHtml(author)}" ` +
    `data-topics="${escapeHtml(topics.join('\n'))}" data-idx="${escapeHtml(String(dataIdx))}" ` +
    `data-pin-id="${escapeHtml(pinId)}"${a.has_chart ? ' data-chart="1"' : ''}>` +
    '<summary class="card-toggle">' +
    '<div class="bookmark-colors" hidden></div>' +
    `<div class="spotlight-label"${featured ? '' : ' hidden'}>LATEST PICK · 가장 최근 글</div>` +
    '<div class="card-header">' +
    `<div class="type-badge">${isX ? '𝕏 · X 게시물' : a.type === 'ib' ? '🎓 IB 자료' : '웹글 · WEB ARTICLE'}</div>` +
    '<div class="card-actions">' +
    memoButtonHtml(memoId, title) +
    '<button type="button" class="bookmark-btn" aria-label="북마크" title="북마크">' +
    '<svg viewBox="0 0 24 24"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg></button>' +
    '<button type="button" class="pin-btn" aria-label="상단 고정" title="상단 고정">' +
    '<svg viewBox="0 0 24 24"><path d="M8 3h8l-1 7 4 4v2H5v-2l4-4-1-7z"/><path d="M12 16v5"/></svg></button>' +
    '<button type="button" class="delete-btn" aria-label="삭제" title="삭제">' +
    '<svg viewBox="0 0 24 24"><path d="M5 7h14M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg></button>' +
    '</div></div>' +
    `<h3>${escapeHtml(title)}${handle}</h3><div class="meta">${escapeHtml(meta)}</div>` +
    `<p class="summary">${escapeHtml(summary)}</p><div class="tags">${topicsHtml(topics)}</div>` +
    '</summary>' +
    `<div class="full-text${fullHtml ? ' web-html' : ''}">${fullHtml ? linkifyHtml(fullHtml) : linkify(fullText)}</div>` +
    `${urlInline}${media}${quotedHtml}${repliesHtml(a)}${relatedHtml}` +
    `<div class="byline"><span class="author">${escapeHtml(author)}</span>` +
    `<button type="button" class="read-window-btn" data-idx="${idx}">새 창에서 읽기 ⧉</button>` +
    `<a class="read-link" href="${escapeHtml(url)}" target="_self" rel="noopener noreferrer">원문 보기 ↗</a></div>` +
    '</details>'
  );
}

/* ------------------------------------------------------------------ */
/* 내보내기                                                            */
/* ------------------------------------------------------------------ */

const ArchiveRender = {
  escapeHtml,
  sliceCp,
  linkify,
  linkifyHtml,
  topicsHtml,
  tagColorIndex,
  memoButtonHtml,
  fnv1a,
  lightboxId,
  lightboxesHtml,
  urlInlineHtml,
  normalizeArticle,
  repliesHtml,
  articleCard,
};

// 일반 <script> 로드 시 전역 접근용
// (ES 모듈 export는 classic script에서 SyntaxError를 일으키므로 사용하지 않음)
if (typeof window !== 'undefined') {
  window.ArchiveRender = ArchiveRender;
}
