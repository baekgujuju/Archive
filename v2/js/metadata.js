/* Evidence-bound display metadata. Never mutates a source record or annotation. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ArchiveMetadata = api;
}(typeof window === 'object' ? window : this, function () {
  'use strict';
  function clean(value) { return typeof value === 'string' ? value.normalize('NFC').replace(/\s+/g, ' ').trim() : ''; }
  function handle(value) { var s = clean(value).replace(/^@/, ''); return /^[A-Za-z0-9_]{1,15}$/.test(s) ? s.toLowerCase() : ''; }
  function source(article) {
    var raw = clean(article.sourceUrl || article.source_url || article.canonical_url), url;
    try { url = new URL(raw); } catch (_) { return { key: '', url: '', platform: '', warning: '' }; }
    if (!/^https?:$/.test(url.protocol) || url.username || url.password) return { key: '', url: '', platform: '', warning: '' };
    var host = url.hostname.toLowerCase(), match;
    if (/(^|\.)(x|twitter)\.com$/.test(host)) {
      match = url.pathname.match(/^\/(?:i\/web|([A-Za-z0-9_]+))\/status\/(\d+)(?:\/(?:(?:photo|video)\/\d+|analytics|history))?\/?$/);
      if (!match) return { key: '', url: url.href, platform: 'x', warning: '게시물 주소 확인 필요' };
      var id = clean(String(article.tweetId || ''));
      if (id && id !== match[2]) return { key: '', url: url.href, platform: 'x', warning: '게시물 ID와 원문 주소 불일치' };
      return { key: 'x:' + match[2], url: 'https://x.com/' + (match[1] || 'i/web') + '/status/' + match[2], platform: 'x', handle: handle(match[1]), warning: '' };
    }
    if (/^(?:m\.)?blog\.naver\.com$/.test(host)) {
      var blog = url.searchParams.get('blogId'), log = url.searchParams.get('logNo');
      match = url.pathname.match(/^\/([A-Za-z0-9_-]+)\/(\d+)\/?$/);
      if (match) { blog = match[1]; log = match[2]; }
      if (/^[A-Za-z0-9_-]+$/.test(blog || '') && /^\d+$/.test(log || '')) return { key: 'naver-blog:' + blog + ':' + log, url: url.href, platform: 'naver', warning: '' };
      return { key: '', url: url.href, platform: host, warning: '글 단위 원문 주소 미확인' };
    }
    if (/^(?:m\.)?cafe\.naver\.com$/.test(host)) {
      var club = url.searchParams.get('clubid'), articleId = url.searchParams.get('articleid');
      match = url.pathname.match(/^\/ca-fe\/web\/cafes\/(\d+)\/articles\/(\d+)\/?$/);
      if (match) { club = match[1]; articleId = match[2]; }
      if (/^\d+$/.test(club || '') && /^\d+$/.test(articleId || '')) return { key: 'naver-cafe:' + club + ':' + articleId, url: url.href, platform: 'naver', warning: '' };
      match = url.pathname.match(/^\/([A-Za-z0-9_-]+)\/(\d+)\/?$/);
      if (match) return { key: 'naver-cafe-slug:' + match[1] + ':' + match[2], url: url.href, platform: host, warning: '' };
      return { key: '', url: url.href, platform: host, warning: '글 단위 원문 주소 미확인' };
    }
    // Only well-known tracking parameters are discarded. Meaningful query
    // parameters, fragments and slash distinctions remain distinct evidence.
    Array.from(url.searchParams.keys()).forEach(function (key) { if (/^utm_/i.test(key) || /^(fbclid|gclid)$/i.test(key)) url.searchParams.delete(key); });
    if (url.pathname === '/' && !url.hash) return { key: '', url: url.href, platform: host, warning: '글 단위 원문 주소 미확인' };
    return { key: 'url:' + url.href, url: url.href, platform: host, warning: '' };
  }
  function author(article) {
    var src = source(article), raw = article.author, name = clean(raw && typeof raw === 'object' ? raw.name : raw);
    var claimed = handle(raw && typeof raw === 'object' ? raw.handle || article.handle : article.handle);
    if (!claimed && /^@[A-Za-z0-9_]{1,15}$/.test(name)) claimed = handle(name);
    var verified = src.platform === 'x' ? src.handle || claimed : '';
    if (src.platform === 'x' && src.handle && claimed && src.handle !== claimed) {
      return { key: '', label: name || '@' + claimed, handle: claimed, warning: '작성자 계정과 원문 계정 불일치' };
    }
    if (verified) return { key: 'x-author:' + verified, label: name && handle(name) !== verified ? name : '@' + verified, handle: verified, warning: '' };
    return { key: name && src.platform ? 'author:' + src.platform + ':' + name : '', label: name || '작성자 미확인', handle: '', warning: '' };
  }
  var TOPIC_ALIASES = { 'ai툴': 'AI툴', 'ai 툴': 'AI툴', 'ai도구': 'AI툴', 'ai 도구': 'AI툴', 's&p500': 'S&P 500', 's&p 500': 'S&P 500', 's & p 500': 'S&P 500', 'ib': 'IB' };
  function topic(value) { var s = clean(value), key = s.toLowerCase(); return Object.prototype.hasOwnProperty.call(TOPIC_ALIASES,key) ? TOPIC_ALIASES[key] : s; }
  function topics(values) { return Array.from(new Set((Array.isArray(values) ? values : []).map(topic).filter(Boolean))); }
  function validDay(year, month, day) { var d = new Date(Date.UTC(year, month - 1, day)); return year >= 1000 && month >= 1 && month <= 12 && d.getUTCFullYear() === year && d.getUTCMonth() === month - 1 && d.getUTCDate() === day; }
  function date(value) {
    var s = clean(value), m;
    if (!s) return { status: 'missing', label: '미기록', raw: '' };
    if (/^Last edited\s*/i.test(s)) {
      var edited = date(s.replace(/^Last edited\s*(?:Opens edit history)?\s*/i, ''));
      if (edited.status === 'recorded') return { status: 'edited', label: edited.label + ' (수정 표기)', raw: s };
      return { status: 'unverified', label: '확인 필요', raw: s };
    }
    m = s.match(/^(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$/);
    var zoneValid = !m || !m[7] || m[7] === 'Z' || ((+m[7].slice(1,3) < 14 || (+m[7].slice(1,3) === 14 && +m[7].slice(4) === 0)) && +m[7].slice(4) < 60);
    if (m && zoneValid && validDay(+m[1], +m[2], +m[3]) && (!m[4] || (+m[4] < 24 && +m[5] < 60 && (!m[6] || +m[6] < 60)))) {
      var label = m[1] + '-' + m[2].padStart(2, '0') + '-' + m[3].padStart(2, '0');
      if (m[4]) label += ' ' + m[4] + ':' + m[5] + (m[7] === 'Z' ? ' UTC' : m[7] ? ' ' + m[7] : ' (시간대 미기록)');
      return { status: 'recorded', label: label, raw: s };
    }
    var months = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
    m = s.match(/^(?:(\d{1,2}):(\d{2})\s*[AP]M\s*[·-]\s*)?([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$/i);
    if (m && /^(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)$/i.test(m[3]) && (!m[1] || (+m[1] >= 1 && +m[1] <= 12 && +m[2] < 60)) && validDay(+m[5], months.indexOf(m[3].slice(0,3).toLowerCase()) + 1, +m[4])) return { status: 'recorded', label: s + ' (원문 표기)', raw: s };
    return { status: 'unverified', label: '확인 필요', raw: s };
  }
  function describe(article) {
    return { source: source(article), author: author(article), topics: topics(article.topics),
      published: date(article.published_at || article.createdAt),
      captured: date(article.capturedAt || article.savedAt),
      archived: date(article.archivedAt || article.archived_at),
      legacySaved: date(article.date_saved) };
  }
  function authorLabels(articles) {
    var groups = new Map(), result = new Map();
    articles.forEach(function (a) { var item = author(a); if (!item.key) return; if (!groups.has(item.key)) groups.set(item.key, new Map()); var names = groups.get(item.key); names.set(item.label, (names.get(item.label) || 0) + 1); });
    groups.forEach(function (names, key) { var name = Array.from(names).sort(function (a,b) { return b[1]-a[1] || a[0].localeCompare(b[0]); })[0][0]; result.set(key, name + (key.startsWith('x-author:') && !name.startsWith('@') ? ' · @' + key.slice(9) : '')); });
    return result;
  }
  function duplicateGroups(articles) {
    var groups = new Map();
    articles.forEach(function (a, index) { var key = source(a).key; if (!key) return; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(index); });
    return Array.from(groups).filter(function (pair) { return pair[1].length > 1; }).map(function (pair) { return { sourceKey: pair[0], indices: pair[1] }; });
  }
  return { source: source, author: author, topic: topic, topics: topics, date: date, describe: describe, authorLabels: authorLabels, duplicateGroups: duplicateGroups };
}));
