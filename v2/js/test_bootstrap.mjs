/**
 * test_bootstrap.mjs — bootstrap.js 로직 단위 테스트
 *
 * DOM/fetch/localStorage를 모킹해서 핵심 흐름을 검증:
 *  1. manifest fetch 성공 → 청크 fetch → 렌더링
 *  2. manifest fetch 실패 + 캐시 있음 → 오프라인 렌더링
 *  3. manifest fetch 실패 + 캐시 없음 → 에러 UI
 *  4. 변경된 청크만 다시 fetch (sha256 비교)
 *  5. localStorage 용량 초과 → 캐시 없이 진행
 *
 * 실행: node test_bootstrap.mjs
 */
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const bootstrapSrc = readFileSync(join(__dirname, 'bootstrap.js'), 'utf-8');

// ES 모듈 export 문을 제거하고 평가 (브라우저 전역 패턴 테스트)
const srcNoExport = bootstrapSrc
  .replace(/^export\s*\{[^}]*\};?\s*$/m, '')
  .replace(/^import\s+.*$/gm, '');

let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { passed++; console.log('PASS:', name); }
  else { failed++; console.log('FAIL:', name); }
}

/**
 * 테스트용 샌드박스 환경 생성
 */
function makeEnv({ manifestRes, chunkRes, cachedManifest, cachedChunks, quotaFail }) {
  // localStorage mock
  const store = new Map();
  if (cachedManifest) store.set('archive_v2_manifest', JSON.stringify(cachedManifest));
  if (cachedChunks) {
    for (const [k, v] of Object.entries(cachedChunks)) {
      store.set('archive_v2_data_' + k, JSON.stringify(v));
    }
  }
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => {
      if (quotaFail) {
        const e = new Error('QuotaExceededError');
        e.name = 'QuotaExceededError';
        throw e;
      }
      store.set(k, String(v));
    },
    removeItem: (k) => store.delete(k),
    key: (i) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };

  // DOM mock (필요한 최소 요소)
  const elements = {};
  function makeEl(id) {
    const el = {
      id,
      hidden: id === 'error' || id === 'offline-badge' ? true : false,
      textContent: '',
      innerHTML: '',
      _listeners: {},
      addEventListener: (ev, fn) => { el._listeners[ev] = fn; },
    };
    // main은 querySelector로 찾음
    return el;
  }
  for (const id of ['loading', 'loading-detail', 'articles', 'error', 'error-message', 'retry-btn', 'offline-badge', 'article-count']) {
    elements[id] = makeEl(id);
  }
  const mainEl = makeEl('main');
  mainEl.hidden = true;

  const document = {
    getElementById: (id) => elements[id] || null,
    querySelector: (sel) => (sel === 'main' ? mainEl : null),
    readyState: 'complete',
    addEventListener: () => {},
  };

  // fetch mock
  const fetchedUrls = [];
  async function fetch(url, opts) {
    fetchedUrls.push({ url, cache: opts && opts.cache });
    if (url === 'data/manifest.json') {
      if (manifestRes instanceof Error) throw manifestRes;
      return { ok: true, json: async () => manifestRes };
    }
    const cr = chunkRes[url];
    if (cr instanceof Error) throw cr;
    if (!cr) return { ok: false, status: 404 };
    return { ok: true, json: async () => cr };
  }

  // window mock (render.js 모킹)
  const renderedCards = [];
  const window = {
    ArchiveRender: {
      articleCard: (a, opts) => {
        renderedCards.push({ article: a, opts });
        return `<details data-idx="${opts.idx}">card</details>`;
      },
    },
    ArchiveApp: null,
  };

  return { localStorage, document, fetch, window, elements, mainEl, fetchedUrls, renderedCards, store };
}

const MANIFEST_V1 = {
  version: '2026-10-13T00:00:00+00:00',
  chunks: {
    'posts-x': { file: 'data/posts-x.json', sha256: 'aaa', count: 2 },
    'posts-web': { file: 'data/posts-web.json', sha256: 'bbb', count: 1 },
    'posts-ib': { file: 'data/posts-ib.json', sha256: 'ccc', count: 0 },
  },
};

const CHUNKS_V1 = {
  'data/posts-x.json': { posts: [{ tweetId: '1', fullText: 'hello' }, { tweetId: '2', fullText: 'world' }] },
  'data/posts-web.json': { posts: [{ title: 'web1' }] },
  'data/posts-ib.json': { posts: [] },
};

function runBootstrap(env) {
  const fn = new Function(
    'window', 'document', 'localStorage', 'fetch', 'console', 'setTimeout',
    srcNoExport + '\nreturn ArchiveBootstrap;'
  );
  // autoStart()가 boot()를 호출하는데, boot는 async이므로 완료를 기다림
  return new Promise((resolve) => {
    const bootstrap = fn(env.window, env.document, env.localStorage, env.fetch, console, (f) => f());
    // boot 완료 대기: 여러 틱 양보
    const wait = (n) => {
      if (n <= 0) return resolve(bootstrap);
      setTimeout(() => wait(n - 1), 0);
    };
    // new Function 안의 setTimeout은 주입된 동기 버전을 사용하므로 즉시 진행됨.
    // 그래도 비동기 fetch 체인을 위해 몇 틱 대기
    let ticks = 0;
    const pump = () => {
      ticks++;
      if (ticks > 50) return resolve(bootstrap);
      setImmediate(pump);
    };
    pump();
  });
}

// ---- 테스트 1: 정상 흐름 ----
{
  const env = makeEnv({ manifestRes: MANIFEST_V1, chunkRes: CHUNKS_V1 });
  await runBootstrap(env);
  check('T1: 3개 카드 렌더링', env.renderedCards.length === 3);
  check('T1: type 부여 (x)', env.renderedCards[0].article.type === 'x');
  check('T1: type 부여 (web)', env.renderedCards[2].article.type === 'web');
  check('T1: _idx 부여', env.renderedCards[1].article._idx === 1 && env.renderedCards[1].opts.idx === 1);
  check('T1: main 표시', env.mainEl.hidden === false);
  check('T1: loading 숨김', env.elements['loading'].hidden === true);
  check('T1: manifest 캐시 저장', env.store.has('archive_v2_manifest'));
  check('T1: 청크 캐시 저장', env.store.has('archive_v2_data_posts-x'));
  check('T1: manifest no-cache', env.fetchedUrls[0].cache === 'no-cache');
  check('T1: 글 수 표시', env.elements['article-count'].textContent === '3');
}

// ---- 테스트 1b: 분할 청크 형식 (x1..x8, 실제 manifest 형식) ----
{
  const MANIFEST_SPLIT = {
    version: '2026-10-13T00:00:00+00:00',
    chunks: {
      'posts-x1': { file: 'data/posts-x1.json', sha256: 'a1', count: 2 },
      'posts-x2': { file: 'data/posts-x2.json', sha256: 'a2', count: 1 },
      'posts-web': { file: 'data/posts-web.json', sha256: 'bbb', count: 1 },
    },
  };
  const CHUNKS_SPLIT = {
    'data/posts-x1.json': { posts: [{ tweetId: '1', fullText: 'hello' }, { tweetId: '2', fullText: 'world' }] },
    'data/posts-x2.json': { posts: [{ tweetId: '3', fullText: 'third' }] },
    'data/posts-web.json': { posts: [{ title: 'web1' }] },
  };
  const env = makeEnv({ manifestRes: MANIFEST_SPLIT, chunkRes: CHUNKS_SPLIT });
  await runBootstrap(env);
  check('T1b: 분할 청크 4개 카드 렌더링', env.renderedCards.length === 4);
  check('T1b: x1 타입 부여', env.renderedCards[0].article.type === 'x');
  check('T1b: x2 타입 부여', env.renderedCards[2].article.type === 'x');
  check('T1b: web 타입 부여', env.renderedCards[3].article.type === 'web');
  check('T1b: 글 수 표시', env.elements['article-count'].textContent === '4');
}

// ---- 테스트 2: 캐시 히트 시 네트워크 스킵 ----
{
  const cachedChunks = {
    'posts-x': CHUNKS_V1['data/posts-x.json'],
    'posts-web': CHUNKS_V1['data/posts-web.json'],
    'posts-ib': CHUNKS_V1['data/posts-ib.json'],
  };
  const env = makeEnv({ manifestRes: MANIFEST_V1, chunkRes: {}, cachedManifest: MANIFEST_V1, cachedChunks });
  await runBootstrap(env);
  const chunkFetches = env.fetchedUrls.filter((f) => f.url !== 'data/manifest.json');
  check('T2: 변경 없는 청크는 fetch 안 함', chunkFetches.length === 0);
  check('T2: 캐시로 3개 렌더링', env.renderedCards.length === 3);
}

// ---- 테스트 3: 변경된 청크만 다시 fetch ----
{
  const MANIFEST_V2 = JSON.parse(JSON.stringify(MANIFEST_V1));
  MANIFEST_V2.chunks['posts-x'].sha256 = 'zzz'; // x만 변경
  const cachedChunks = {
    'posts-x': CHUNKS_V1['data/posts-x.json'],
    'posts-web': CHUNKS_V1['data/posts-web.json'],
    'posts-ib': CHUNKS_V1['data/posts-ib.json'],
  };
  const env = makeEnv({ manifestRes: MANIFEST_V2, chunkRes: CHUNKS_V1, cachedManifest: MANIFEST_V1, cachedChunks });
  await runBootstrap(env);
  const chunkUrls = env.fetchedUrls.filter((f) => f.url !== 'data/manifest.json').map((f) => f.url);
  check('T3: 변경된 x 청크만 fetch', chunkUrls.length === 1 && chunkUrls[0] === 'data/posts-x.json');
}

// ---- 테스트 4: 오프라인 + 캐시 있음 → 오프라인 배지 ----
{
  const cachedChunks = {
    'posts-x': CHUNKS_V1['data/posts-x.json'],
    'posts-web': CHUNKS_V1['data/posts-web.json'],
    'posts-ib': CHUNKS_V1['data/posts-ib.json'],
  };
  const env = makeEnv({
    manifestRes: new Error('network down'),
    chunkRes: {},
    cachedManifest: MANIFEST_V1,
    cachedChunks,
  });
  await runBootstrap(env);
  check('T4: 오프라인 렌더링', env.renderedCards.length === 3);
  check('T4: 오프라인 배지 표시', env.elements['offline-badge'].hidden === false);
  check('T4: main 표시', env.mainEl.hidden === false);
}

// ---- 테스트 5: 오프라인 + 캐시 없음 → 에러 UI ----
{
  const env = makeEnv({ manifestRes: new Error('network down'), chunkRes: {} });
  await runBootstrap(env);
  check('T5: 에러 박스 표시', env.elements['error'].hidden === false);
  check('T5: 렌더링 없음', env.renderedCards.length === 0);
  check('T5: 에러 메시지', env.elements['error-message'].textContent.includes('인터넷'));
}

// ---- 테스트 6: QuotaExceededError → 캐시 없이 진행 ----
{
  const env = makeEnv({ manifestRes: MANIFEST_V1, chunkRes: CHUNKS_V1, quotaFail: true });
  await runBootstrap(env);
  check('T6: 용량 초과에도 렌더링', env.renderedCards.length === 3);
  check('T6: 캐시 저장 안 됨', !env.store.has('archive_v2_manifest'));
}

// ---- 테스트 7: render.js 없음 → 에러 ----
{
  const env = makeEnv({ manifestRes: MANIFEST_V1, chunkRes: CHUNKS_V1 });
  env.window.ArchiveRender = null;
  await runBootstrap(env);
  check('T7: 렌더러 없음 에러', env.elements['error'].hidden === false);
}

// ---- 테스트 8: 깨진 청크 JSON → 해당 청크 건너뜀 ----
{
  const badChunks = Object.assign({}, CHUNKS_V1, {
    'data/posts-web.json': { notPosts: [] }, // posts 배열 없음
  });
  const env = makeEnv({ manifestRes: MANIFEST_V1, chunkRes: badChunks });
  await runBootstrap(env);
  check('T8: 깨진 청크 제외하고 렌더링', env.renderedCards.length === 2);
}

console.log(`\n결과: ${passed} 통과, ${failed} 실패`);
process.exit(failed ? 1 : 0);
