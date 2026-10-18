/**
 * bootstrap.js — 아카이브 v2 부트스트랩 모듈
 *
 * 데이터 로딩 → localStorage 캐시 → 렌더링의 전체 흐름을 담당.
 * 카드 HTML 생성은 render.js의 `articleCard()`에 위임한다.
 *
 * 흐름:
 *   1. manifest.json fetch (no-cache) → 최신 버전 확인
 *   2. localStorage 캐시와 비교 → 변경된 청크만 다시 fetch
 *   3. 청크 JSON 파싱 → type/_idx 부여 → articleCard() 렌더링
 *   4. #articles에 삽입 → 로딩 UI 숨김 → main 표시
 *
 * 오프라인 대응:
 *   - 네트워크 실패 + 캐시 있음 → 캐시로 렌더링 + 오프라인 배지
 *   - 네트워크 실패 + 캐시 없음 → 에러 화면 + 재시도 버튼
 *
 * 필수 DOM (index.html 셸이 제공):
 *   #loading          — 로딩 컨테이너
 *   #loading-detail   — 단계별 메시지 <p>
 *   #articles         — 카드 렌더링 대상 컨테이너
 *   main              — 본문 (초기 hidden)
 *
 * 선택 DOM (있으면 사용, 없어도 동작):
 *   #error, #error-message, #retry-btn — 에러 UI
 *   #offline-badge    — 오프라인 표시 배지
 *   #article-count    — 전체 글 수 표시
 *
 * 사용법 (ES 모듈):
 *   import { boot } from './bootstrap.js';
 *   // DOMContentLoaded 시 자동 실행됨. 수동 재시작: boot()
 *
 * 사용법 (일반 스크립트):
 *   <script src="js/render.js"></script>
 *   <script src="js/bootstrap.js"></script>
 *   <!-- window.ArchiveBootstrap.boot() 로 접근 -->
 */

 'use strict';

function showContentUpdated(manifest, offline) {
  const value=manifest&&(manifest.content_updated_at||manifest.version);
  const date=new Date(value||'');
  const el=document.getElementById('archive-latest-update');
  if(!el)return;
  if(!Number.isFinite(date.getTime())){el.textContent='latest up —';el.removeAttribute&&el.removeAttribute('datetime');return;}
  const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date);
  const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
  el.textContent='latest up '+p.year.slice(-2)+'.'+p.month+'.'+p.day+' '+p.hour+':'+p.minute+(offline?' · 저장본':'');
  if(el.setAttribute){el.setAttribute('datetime',date.toISOString());el.setAttribute('title','자료 반영 시각 · 한국 시간');}
}


/* ------------------------------------------------------------------ */
/* 0. 상수                                                            */
/* ------------------------------------------------------------------ */

/** localStorage 키 prefix */
const LS_PREFIX = 'archive_v2_';
/** manifest 캐시 키 */
const LS_MANIFEST_KEY = LS_PREFIX + 'manifest';
/** 청크 캐시 키 prefix (뒤에 청크명을 붙임) */
const LS_CHUNK_PREFIX = LS_PREFIX + 'data_';

/**
 * manifest.chunks에서 동적으로 청크 목록 생성.
 * 하드코딩된 CHUNK_ORDER 대신 manifest를 single source of truth로 사용.
 * (빌더가 청크 분할 방식을 바꿔도 manifest만 맞으면 동작)
 *
 * @param {object} manifest — manifest.json 객체
 * @returns {Array<{name: string, type: string, label: string}>} 청크 목록
 */
function chunkOrderFromManifest(manifest) {
  const chunks = (manifest && manifest.chunks) || {};
  const order = [];
  for (const name of Object.keys(chunks)) {
    let type = 'x';
    let label = name;
    if (name === 'posts-web' || name.startsWith('posts-web')) { type = 'web'; label = '웹글'; }
    else if (name === 'posts-ib' || name.startsWith('posts-ib')) { type = 'ib'; label = 'IB 자료'; }
    else if (name.startsWith('posts-x')) { type = 'x'; label = 'X 게시글'; }
    order.push({ name, type, label });
  }
  // manifest 키 순서를 유지 (빌더가 생성한 순서: x 청크들, web, ib)
  // localeCompare numeric 정렬은 'posts-x'와 'posts-x1' 혼합 시 순서를 깨뜨림
  return order;
}

/* ------------------------------------------------------------------ */
/* 1. 렌더러 접근                                                     */
/* ------------------------------------------------------------------ */

/**
 * render.js의 렌더 함수들을 가져온다.
 * 일반 <script> 로드 시 window.ArchiveRender에 설정되어 있어야 한다.
 *
 * @returns {object|null} ArchiveRender 네임스페이스, 없으면 null
 */
function getRenderer() {
  if (typeof window !== 'undefined' && window.ArchiveRender) {
    return window.ArchiveRender;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/* 2. DOM 헬퍼                                                        */
/* ------------------------------------------------------------------ */

/**
 * @param {string} id — 요소 id
 * @returns {HTMLElement|null}
 */
function $(id) {
  return document.getElementById(id);
}

/**
 * 로딩 상세 메시지를 업데이트한다.
 *
 * @param {string} msg — 표시할 메시지
 */
function updateLoading(msg) {
  const el = $('loading-detail');
  if (el) el.textContent = msg;
}

/**
 * 로딩 UI를 숨기고 본문을 표시한다.
 */
function showMain() {
  const loading = $('loading');
  if (loading) loading.hidden = true;
  const main = document.querySelector('main');
  if (main) main.hidden = false;
}

/**
 * 에러 UI를 표시한다. #error 요소가 없으면 alert로 폴백하지 않고
 * 로딩 영역에 메시지를 직접 표시한다 (조용한 실패 방지).
 *
 * @param {string} message — 사용자에게 보여줄 메시지
 */
function showError(message) {
  const loading = $('loading');
  if (loading) loading.hidden = true;

  const errorBox = $('error');
  const errorMsg = $('error-message');
  if (errorBox && errorMsg) {
    errorMsg.textContent = message;
    errorBox.hidden = false;
  } else if (loading) {
    // 폴백: 로딩 영역을 에러 표시로 재사용
    loading.hidden = false;
    loading.innerHTML =
      '<p style="color:#c62828">⚠️ ' +
      escapeForFallback(message) +
      '</p>' +
      '<button id="retry-btn-fallback" type="button">다시 시도</button>';
    const btn = $('retry-btn-fallback');
    if (btn) btn.addEventListener('click', () => boot());
  }
}

/**
 * showError 폴백 경로에서만 사용하는 최소 이스케이프.
 * (render.js가 로드되지 않은 상황에서도 동작해야 하므로 자체 구현)
 *
 * @param {*} value
 * @returns {string}
 */
function escapeForFallback(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * 에러 UI를 숨긴다.
 */
function hideError() {
  const errorBox = $('error');
  if (errorBox) errorBox.hidden = true;
}

/* ------------------------------------------------------------------ */
/* 3. localStorage 캐시                                                */
/* ------------------------------------------------------------------ */

/**
 * localStorage 쓰기. 용량 초과(QuotaExceededError) 시 false를 반환하고
 * 이후 캐시 쓰기를 건너뛸 수 있도록 호출자가 판단한다.
 *
 * @param {string} key
 * @param {string} value
 * @returns {boolean} 성공 여부
 */
function lsSet(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (e) {
    // QuotaExceededError (iOS Safari ~5MB) 또는 비공개 모드 등
    if (e && (e.name === 'QuotaExceededError' || e.code === 22)) {
      console.warn('[bootstrap] localStorage 용량 초과, 캐시 없이 진행:', key);
    } else {
      console.warn('[bootstrap] localStorage 쓰기 실패:', e);
    }
    return false;
  }
}

/**
 * localStorage 읽기.
 *
 * @param {string} key
 * @returns {string|null}
 */
function lsGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    console.warn('[bootstrap] localStorage 읽기 실패:', e);
    return null;
  }
}

/**
 * 캐시된 manifest를 파싱해서 반환한다.
 *
 * @returns {object|null} manifest 객체, 없거나 깨졌으면 null
 */
function loadCachedManifest() {
  const raw = lsGet(LS_MANIFEST_KEY);
  if (!raw) return null;
  try {
    const m = JSON.parse(raw);
    if (m && typeof m === 'object' && m.chunks && typeof m.chunks === 'object') {
      return m;
    }
    return null;
  } catch (e) {
    console.warn('[bootstrap] 캐시된 manifest 파싱 실패:', e);
    return null;
  }
}

/**
 * 캐시된 청크 데이터를 파싱해서 반환한다.
 *
 * @param {string} chunkName — 예: 'posts-x'
 * @returns {object[]|null} 글 배열, 없거나 깨졌으면 null
 */
function loadCachedChunk(chunkName) {
  const raw = lsGet(LS_CHUNK_PREFIX + chunkName);
  if (!raw) return null;
  try {
    const d = JSON.parse(raw);
    if (d && Array.isArray(d.posts)) {
      return d.posts;
    }
    return null;
  } catch (e) {
    console.warn('[bootstrap] 캐시된 청크 파싱 실패:', chunkName, e);
    return null;
  }
}

/**
 * v2 캐시 전체 삭제 (디버그/문제 해결용).
 */
function clearCache() {
  try {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(LS_PREFIX)) keys.push(k);
    }
    keys.forEach((k) => localStorage.removeItem(k));
    console.log('[bootstrap] 캐시 삭제됨:', keys.length, '개');
  } catch (e) {
    console.warn('[bootstrap] 캐시 삭제 실패:', e);
  }
}

/* ------------------------------------------------------------------ */
/* 4. 네트워크                                                         */
/* ------------------------------------------------------------------ */

/**
 * manifest.json을 네트워크에서 가져온다. 항상 최신 버전을 확인해야
 * 하므로 HTTP 캐시를 사용하지 않는다.
 *
 * @returns {Promise<object>} manifest 객체
 * @throws {Error} 네트워크 실패·HTTP 오류·JSON 파싱 실패 시
 */
async function fetchManifest() {
  let res;
  try {
    res = await fetch('data/manifest.json', { cache: 'no-cache' });
  } catch (e) {
    throw new Error('네트워크 오류: manifest를 불러오지 못했습니다. (' + e.message + ')');
  }
  if (!res.ok) {
    throw new Error('manifest 로드 실패: HTTP ' + res.status);
  }
  let manifest;
  try {
    manifest = await res.json();
  } catch (e) {
    throw new Error('manifest JSON 파싱 실패: ' + e.message);
  }
  if (!manifest || typeof manifest !== 'object' || !manifest.chunks) {
    throw new Error('manifest 구조가 올바르지 않습니다.');
  }
  return manifest;
}

/**
 * 청크 JSON을 네트워크에서 가져온다.
 *
 * @param {string} file — manifest에 기록된 파일 경로 (예: 'data/posts-x.json')
 * @param {string} label — 로딩 UI용 표시명
 * @returns {Promise<object[]>} 글 배열
 * @throws {Error} 네트워크 실패·HTTP 오류·구조 이상 시
 */
async function fetchChunk(file, label) {
  let res;
  try {
    // 청크는 sha 변경 시에만 다시 받으므로 HTTP 캐시 무효화로 신선도 보장
    res = await fetch(file, { cache: 'no-cache' });
  } catch (e) {
    throw new Error('네트워크 오류: ' + label + '을(를) 불러오지 못했습니다.');
  }
  if (!res.ok) {
    throw new Error(label + ' 로드 실패: HTTP ' + res.status);
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error(label + ' JSON 파싱 실패: ' + e.message);
  }
  if (!data || !Array.isArray(data.posts)) {
    throw new Error(label + ' 데이터 구조가 올바르지 않습니다.');
  }
  return data.posts;
}

/* ------------------------------------------------------------------ */
/* 5. 부트스트랩 본체                                                  */
/* ------------------------------------------------------------------ */

let booted = false; // 중복 실행 방지
let cacheWritable = true; // localStorage 쓰기 가능 여부 (용량 초과 시 false)

/**
 * 부트스트랩 진입점. manifest 확인 → 청크 로드(캐시 활용) → 렌더링.
 * 재시도 버튼에서도 호출되므로 여러 번 호출돼도 안전해야 한다.
 */
async function boot() {
  if(window.DriveSync&&window.DriveSync.prepareView)window.DriveSync.prepareView();
  if (booted) {
    console.log('[bootstrap] 이미 실행 중이거나 완료됨, 중복 호출 무시');
    return;
  }
  booted = true;

  const renderer = getRenderer();
  if (!renderer || typeof renderer.articleCard !== 'function') {
    showError('렌더링 모듈(render.js)을 불러오지 못했습니다. 페이지를 새로고침해 주세요.');
    booted = false;
    return;
  }

  hideError();
  const loading = $('loading');
  if (loading) loading.hidden = false;

  const container = $('articles');
  if (!container) {
    showError('화면 구조(#articles)를 찾지 못했습니다. 페이지를 새로고침해 주세요.');
    booted = false;
    return;
  }

  // 1. manifest 확인
  updateLoading('버전 확인 중...');
  let manifest = null;
  let manifestFromNetwork = false;
  const cachedManifest = loadCachedManifest();

  try {
    manifest = await fetchManifest();
    manifestFromNetwork = true;
  } catch (e) {
    console.warn('[bootstrap]', e.message);
    // 네트워크 실패 → 캐시 폴백 시도
    if (cachedManifest) {
      const cached = loadAllCachedChunks();
      if (cached) {
        renderArticles(cached.articles, renderer, container, { offline: true });
        showContentUpdated(cachedManifest,true);
        showOfflineBadge(true);
        showMain();
        return;
      }
    }
    showError(
      '데이터를 불러오지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.'
    );
    booted = false;
    return;
  }

  // 2. 청크별: 캐시 유효하면 사용, 아니면 fetch
  /** @type {Array<{posts: object[], type: string}>} */
  const chunkResults = [];
  let usedCacheCount = 0;

  for (const chunk of chunkOrderFromManifest(manifest)) {
    const info = manifest.chunks[chunk.name];
    if (!info || !info.file) {
      console.warn('[bootstrap] manifest에 청크 정보 없음:', chunk.name);
      continue;
    }

    // 캐시 유효성: 이전 manifest의 sha256과 동일 + 데이터 존재
    const cachedInfo = cachedManifest && cachedManifest.chunks
      ? cachedManifest.chunks[chunk.name]
      : null;
    let posts = null;
    if (
      cachedInfo &&
      cachedInfo.sha256 &&
      info.sha256 &&
      cachedInfo.sha256 === info.sha256
    ) {
      posts = loadCachedChunk(chunk.name);
      if (posts) usedCacheCount++;
    }

    if (!posts) {
      const count = info.count ? ' (' + info.count + '개)' : '';
      updateLoading(chunk.label + ' 로딩 중...' + count);
      try {
        posts = await fetchChunk(info.file, chunk.label);
      } catch (e) {
        console.warn('[bootstrap]', e.message);
        // 개별 청크 실패 → 캐시 폴백, 캐시도 없으면 해당 청크 건너뜀
        posts = loadCachedChunk(chunk.name);
        if (!posts) {
          console.error('[bootstrap] 청크 로드 실패, 건너뜀:', chunk.name);
          continue;
        }
      }
      // 새로 받은 청크는 캐시에 저장 시도
      if (cacheWritable) {
        const ok = lsSet(LS_CHUNK_PREFIX + chunk.name, JSON.stringify({ posts }));
        if (!ok) cacheWritable = false; // 용량 초과 시 이후 쓰기 생략
      }
    }

    chunkResults.push({ posts, type: chunk.type });
  }

  // 3. manifest 캐시 저장 (청크 sha 비교 기준점)
  if (cacheWritable) {
    const ok = lsSet(LS_MANIFEST_KEY, JSON.stringify(manifest));
    if (!ok) cacheWritable = false;
  }

  if (chunkResults.length === 0) {
    showError('불러온 데이터가 없습니다. 다시 시도해 주세요.');
    booted = false;
    return;
  }

  // 4. 합치기 + type/_idx 부여
  /** @type {object[]} */
  const articles = [];
  for (const cr of chunkResults) {
    for (const p of cr.posts) {
      if (p && typeof p === 'object') {
        // 캐시/원본 객체를 직접 수정하지 않도록 얕은 복사
        const a = Object.assign({}, p);
        if (!a.type) a.type = cr.type;
        a._idx = articles.length;
        articles.push(a);
      }
    }
  }

  console.log(
    '[bootstrap] 로드 완료: ' + articles.length + '개 (캐시 ' + usedCacheCount + '청크 사용)'
  );

  // 5. 렌더링
  updateLoading('화면 그리는 중...');
  // 큰 HTML 문자열 생성을 다음 프레임으로 양보 (로딩 UI가 먼저 그려지도록)
  await new Promise((resolve) => setTimeout(resolve, 0));
  try {const mapResponse=await fetch('data/media-map.json',{cache:'no-cache'});if(!mapResponse.ok)throw new Error('Media map unavailable');if(mapResponse.ok){window.ArchiveMediaMap=await mapResponse.json();localStorage.setItem('archive-media-map',JSON.stringify(window.ArchiveMediaMap));}}catch(e){try{window.ArchiveMediaMap=JSON.parse(localStorage.getItem('archive-media-map')||'{}');}catch(ignore){}}
  try {if(!manifest.translations)throw new Error('No translations');const response=await fetch('data/translations.json',{cache:'no-cache'});if(response.ok){const translations=await response.json();localStorage.setItem('archive-translations',JSON.stringify(translations));articles.forEach(a=>{a.translation=translations[a.tweetId||a.id||a.pinId];attachReplyTranslations(a,translations);});}}catch(e){try{const translations=JSON.parse(localStorage.getItem('archive-translations')||'{}');articles.forEach(a=>{a.translation=translations[a.tweetId||a.id||a.pinId];attachReplyTranslations(a,translations);});}catch(ignore){}}
  try{const response=await fetch('data/ai-tools.json',{cache:'no-cache'});if(response.ok){window.ArchiveToolMap=await response.json();localStorage.setItem('archive-ai-tools',JSON.stringify(window.ArchiveToolMap));}}catch(e){}
  renderArticles(articles, renderer, container, { offline: false });
  if(typeof window!=='undefined' && window.dispatchEvent) window.dispatchEvent(new Event('archive-data-loaded'));
  showOfflineBadge(false);
  showMain();
  showContentUpdated(manifest,false);
  const updateStatus=document.getElementById('archive-update-status');
  if(updateStatus)updateStatus.textContent='자료 변경 자동 확인 · 60초 간격 · '+new Date(manifest.version).toLocaleString('ko-KR');

}

/**
 * 캐시된 manifest 기준으로 모든 청크를 캐시에서 로드한다.
 * (오프라인 폴백용)
 *
 * @returns {{articles: object[]}|null} 성공 시 글 배열, 하나라도 없으면 null
 */
function loadAllCachedChunks() {
  const cachedManifest = loadCachedManifest();
  if (!cachedManifest) return null;
  const articles = [];
  for (const chunk of chunkOrderFromManifest(cachedManifest)) {
    const posts = loadCachedChunk(chunk.name);
    if (!posts) return null; // 일부라도 없으면 폴백 불가
    for (const p of posts) {
      if (p && typeof p === 'object') {
        const a = Object.assign({}, p);
        if (!a.type) a.type = chunk.type;
        a._idx = articles.length;
        articles.push(a);
      }
    }
  }
  return articles.length ? { articles } : null;
}

/**
 * 글 배열을 articleCard()로 렌더링해서 컨테이너에 삽입한다.
 *
 * @param {object[]} articles — type/_idx가 부여된 글 배열
 * @param {object} renderer — window.ArchiveRender
 * @param {HTMLElement} container — #articles
 * @param {object} [opts]
 * @param {boolean} [opts.offline=false] — 오프라인 모드 여부
 */
function applyToolMetadata(articles,toolMap){
    try{articles.forEach(a=>{
      const t=toolMap[String(a.tweetId||a.id||a.pinId||'')];
      if(!t||t.status!=='verified'||typeof t.summary_ko!=='string')return;
      const candidates=Array.isArray(t.repo_links)&&t.repo_links.length?t.repo_links:[{url:t.repo_url,title:t.link_title||t.title}];
      const seen=new Set(),links=[];
      candidates.forEach(link=>{
        if(!link||typeof link.url!=='string')return;
        let u;try{u=new URL(link.url);}catch(e){return;}
        if(u.protocol!=='https:'||u.hostname!=='github.com'||u.username||u.password||!/^\/[\w.-]+\/[\w.-]+\/?$/.test(u.pathname)||u.search||u.hash)return;
        const url=u.href.replace(/\/$/,'').replace(/\.git$/,'');
        if(seen.has(url.toLowerCase()))return;seen.add(url.toLowerCase());
        links.push({url,title:String(link.title||t.title||'GitHub 저장소').slice(0,100)});
      });
      if(!links.length)return;
      a.topics=Array.from(new Set([...(Array.isArray(a.topics)?a.topics:[]).filter(t=>t!=='기타'),'AI툴']));
      a.toolDescription=t.summary_ko.slice(0,240);a.toolCategory=String(t.category||'AI 도구').slice(0,40);a.toolLinks=links;
      if(!a.title||/click\s+to\s+(follow|subscribe)/i.test(a.title||a.fullText||''))a.title=String(t.title||'GitHub 도구').slice(0,100);
    });}catch(e){}
}

/**
 * 답글(댓글) 번역 연결. 부모 번역과 같은 translations.json에서
 * `reply:{부모 tweetId}:{답글 tweetId}` 키로 조회해 각 답글에 연결한다.
 * 부모 키(순수 tweetId)와 충돌하지 않는다. 번역이 없으면 연결하지 않고
 * 렌더는 원문만 표시한다.
 */
function attachReplyTranslations(article, translations) {
  var pid = article && (article.tweetId || article.id || article.pinId);
  if (!pid || !translations) return;
  function visit(node, path, depth, ancestors) {
    if (!node || typeof node !== 'object' || depth > 8 || ancestors.has(node)) return;
    var seen = new Set(ancestors); seen.add(node);
    var q = node.quoted;
    if (q && typeof q === 'object' && !Array.isArray(q) && !seen.has(q)) {
      var qp = path.concat(['quote', String(q.tweetId || q.id || 'anonymous')]);
      q.translation = translations[['quote', String(pid)].concat(qp).join(':')];
      visit(q, qp, depth + 1, seen);
    }
    (Array.isArray(node.replies) ? node.replies : []).forEach(function(r) {
      if (!r || typeof r !== 'object' || seen.has(r)) return;
      var rid = r.tweetId || r.id;
      if (!rid) return;
      var rp = path.concat(['reply', String(rid)]);
      var key = path.length ? ['quote', String(pid)].concat(rp).join(':') : 'reply:' + pid + ':' + rid;
      r.translation = translations[key];
      visit(r, rp, depth + 1, seen);
    });
  }
  visit(article, [], 0, new Set());
}

function renderArticles(articles, renderer, container, opts = {}) {
  try{applyToolMetadata(articles,(typeof window!=='undefined'&&window.ArchiveToolMap)||JSON.parse(localStorage.getItem('archive-ai-tools')||'{}'));}catch(e){}

  function dateOf(a){if(a.type==='x'&&/^\d+$/.test(String(a.tweetId||a.id||''))){try{return Number((BigInt(a.tweetId||a.id)>>22n)+1288834974657n);}catch(e){}}return Date.parse(a.date_saved||a.createdAt||a.date||'')||0;}
  articles=articles.slice().sort((a,b)=>dateOf(b)-dateOf(a));
  articles = articles.map((article) => renderer.normalizeArticle ? renderer.normalizeArticle(article) : article);
  try{const translations=JSON.parse(localStorage.getItem('archive-translations')||'{}');articles.forEach(a=>{if(!a.translation)a.translation=translations[a.tweetId||a.id||a.pinId];attachReplyTranslations(a,translations);});}catch(e){}
  const offline = !!opts.offline;
  let html;
  try {
    html = articles
      .map((a, i) => {
        try {
          return renderer.articleCard(a, { idx: a._idx != null ? a._idx : i });
        } catch (e) {
          console.warn('[bootstrap] 카드 렌더링 실패 (idx ' + i + '):', e);
          return '';
        }
      })
      .join('');
  } catch (e) {
    showError('화면을 그리는 중 오류가 발생했습니다: ' + e.message);
    booted = false;
    return;
  }

  container.innerHTML = html;

  // 라이트박스 섹션 추가 (이미지 확대용, render.js lightboxesHtml)
  try {
    if (renderer.lightboxesHtml) {
      const lbHtml = renderer.lightboxesHtml(articles);
      if (lbHtml) {
        const lbWrap = document.createElement('div');
        lbWrap.id = 'lightboxes';
        lbWrap.innerHTML = lbHtml;
        container.appendChild(lbWrap);
      }
    }
  } catch (e) {
    console.warn('[bootstrap] 라이트박스 생성 실패:', e);
  }

  const countEl = $('article-count');
  if (countEl) {
    countEl.textContent = String(articles.length);
  }

  if (offline) {
    console.log('[bootstrap] 오프라인 모드: 캐시된 데이터로 렌더링');
  }
  // Every render path, including offline cache fallback, gets working controls.
  if (typeof window !== 'undefined' && window.ArchiveApp) {
    try { window.ArchiveApp.init(articles); }
    catch (e) { console.error('[bootstrap] 인터랙션 초기화 실패:', e); showError('화면 기능을 시작하지 못했습니다. 새로고침해 주세요.'); }
  }
}

/**
 * 오프라인 배지 표시/숨김.
 *
 * @param {boolean} show
 */
function showOfflineBadge(show) {
  const badge = $('offline-badge');
  if (badge) badge.hidden = !show;
}

/**
 * 재시도 버튼 바인딩. 셸에 #retry-btn이 있으면 boot()를 연결한다.
 */
function bindRetry() {
  const btn = $('retry-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      booted = false; // 재시도 허용
      hideError();
      boot();
    });
  }
}

/* ------------------------------------------------------------------ */
/* 6. 시작                                                             */
/* ------------------------------------------------------------------ */

/**
 * DOM 준비 후 자동 시작. 스크립트가 DOMContentLoaded 이후에
 * 로드된 경우를 대비해 readyState도 확인한다.
 */
function autoStart() {
  bindRetry();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => boot());
  } else {
    boot();
  }
}

/* 내보내기                                                            */

const ArchiveBootstrap = {
  boot,
  clearCache,
  // 테스트용 내부 함수 노출
  _loadCachedManifest: loadCachedManifest,
  _loadCachedChunk: loadCachedChunk,
};

// 일반 <script> 로드 시 전역 접근용 (render.js와 동일한 패턴)
// (ES 모듈 export는 classic script에서 SyntaxError를 일으키므로 사용하지 않음)
if (typeof window !== 'undefined') {
  window.ArchiveBootstrap = ArchiveBootstrap;
}

autoStart();

if(typeof window!=='undefined'&&window.addEventListener&&typeof document!=='undefined'){
  let checking=false,lastVersion=null;
  window.addEventListener('archive-data-loaded',()=>{const manifest=loadCachedManifest();lastVersion=manifest&&JSON.stringify([manifest.chunks,manifest.translations,manifest.ai_tools,manifest.shared_state]);});
  async function checkUpdates(){if(checking||document.hidden||document.querySelector('dialog[open]'))return;checking=true;try{const next=await fetchManifest(),version=JSON.stringify([next.chunks,next.translations,next.ai_tools,next.shared_state]);if(lastVersion&&version!==lastVersion){booted=false;await boot();const status=document.getElementById('archive-update-status');if(status)status.textContent='새 자료가 자동으로 업데이트되었습니다.';}lastVersion=version;}catch(e){}finally{checking=false;}}
  setInterval(checkUpdates,60000);window.addEventListener('focus',checkUpdates);document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkUpdates();});
}
