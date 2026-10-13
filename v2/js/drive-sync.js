/* Private Drive appData state. OAuth tokens stay in memory. */
var DriveSync = (function () {
  'use strict';
  var CLIENT_ID = '43687402502-rlid8pjoakcigrn6tpcs58elo357vtfr.apps.googleusercontent.com';
  var KEYS = {pinned:'archive-pinned',bookmarks:'archive-bookmarks',names:'archive-bookmark-names',deletedReplies:'archive-deleted-replies',deletedPosts:'archive-deleted-posts'};
  var MEMO = 'archive_memo_', BASE = 'archive-sync-base', BACKUP = 'archive-sync-conflicts';
  var statusEl, tokenClient, token, fileId, timer, busy = false, initialized = false, connected = false, lastHash = '', ticks = 0;
  function get(k, fallback) { try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch (_) { return fallback; } }
  function put(k, value) { localStorage.setItem(k, JSON.stringify(value)); }
  function memoState() {
    var out = Object.create(null);
    for (var i = 0; i < localStorage.length; i++) { var k = localStorage.key(i); if (k && k.indexOf(MEMO) === 0) out[k.slice(MEMO.length)] = localStorage.getItem(k); }
    return out;
  }
  function collect() {
    var out = {}; Object.keys(KEYS).forEach(function (k) { out[k] = get(KEYS[k], /^(pinned|deleted)/.test(k) ? [] : {}); });
    out.memos = memoState(); return out;
  }
  function hash(d) { return JSON.stringify(d || collect()); }
  function message(s) { if (statusEl) statusEl.textContent = s; }
  function dictionary(d) { return d !== null && typeof d === 'object' && !Array.isArray(d); }
  function valid(d) {
    if (!dictionary(d)) return false;
    return ['pinned','deletedReplies','deletedPosts'].every(function (k) { return d[k] === undefined || (Array.isArray(d[k]) && d[k].every(function (x) { return typeof x === 'string'; })); }) &&
      ['bookmarks','names','bookmarkNames','memos'].every(function (k) { return d[k] === undefined || (dictionary(d[k]) && Object.keys(d[k]).every(function (id) { return !['__proto__','constructor','prototype'].includes(id) && typeof d[k][id] === 'string'; })); });
  }
  function normalize(d) {
    var out = {}; Object.keys(KEYS).forEach(function (k) { out[k] = d[k] || (/^(pinned|deleted)/.test(k) ? [] : {}); });
    out.names = d.names || d.bookmarkNames || {}; out.memos = d.memos || {}; return out;
  }
  // Three-way merge preserves deletions once a synchronized baseline exists.
  // Simultaneous conflicting edits keep the local value and preserve remote data in a backup.
  function merge(local, remote, baseline) {
    local = normalize(local); remote = normalize(remote); var out = {}, conflicts = [];
    Object.keys(local).forEach(function (field) {
      var arrays = Array.isArray(local[field]), l = arrays ? Object.fromEntries(local[field].map(function (id) { return [id, true]; })) : local[field];
      var r = arrays ? Object.fromEntries(remote[field].map(function (id) { return [id, true]; })) : remote[field];
      var b = baseline ? normalize(baseline)[field] : null;
      if (arrays && b) b = Object.fromEntries(b.map(function (id) { return [id, true]; }));
      var values = Object.create(null), ids = new Set(Object.keys(l).concat(Object.keys(r), b ? Object.keys(b) : []));
      ids.forEach(function (id) {
        var lv = l[id], rv = r[id], bv = b ? b[id] : undefined, value;
        if (!b) { value = lv !== undefined ? lv : rv; if (lv !== undefined && rv !== undefined && lv !== rv) conflicts.push(field + ':' + id); }
        else if (lv === bv) value = rv;
        else if (rv === bv || lv === rv) value = lv;
        else { value = lv; conflicts.push(field + ':' + id); }
        if (value !== undefined) values[id] = value;
      });
      out[field] = arrays ? Object.keys(values) : values;
    });
    out.updatedAt = new Date().toISOString();
    Object.defineProperty(out, '_conflicts', {value: conflicts, enumerable:false}); return out;
  }
  function apply(d) {
    // Save a recoverable snapshot before applying cloud changes.
    put('archive-sync-local-backup', collect());
    Object.keys(KEYS).forEach(function (k) { put(KEYS[k], d[k]); });
    Object.keys(memoState()).forEach(function (id) { if (!Object.prototype.hasOwnProperty.call(d.memos, id)) localStorage.removeItem(MEMO + id); });
    Object.keys(d.memos).forEach(function (id) { localStorage.setItem(MEMO + id, d.memos[id]); });
    if (window.ArchiveApp && window.ArchiveApp.refreshSavedState) window.ArchiveApp.refreshSavedState();
  }
  async function api(path, options) {
    var res = await fetch('https://www.googleapis.com' + path, Object.assign({}, options, {headers:Object.assign({}, options && options.headers, {Authorization:'Bearer ' + token})}));
    if (!res.ok) { var e = new Error('Drive ' + res.status); e.auth = res.status === 401 || res.status === 403; throw e; } return res;
  }
  async function remoteState() {
    if (!fileId) {
      var query = "name='archive-state.json' and 'appDataFolder' in parents and trashed=false";
      var list = await (await api('/drive/v3/files?q=' + encodeURIComponent(query) + '&spaces=appDataFolder&fields=files(id,modifiedTime)&orderBy=modifiedTime%20desc&pageSize=100')).json();
      fileId = list.files && list.files[0] && list.files[0].id;
    }
    if (!fileId) return null;
    var d = await (await api('/drive/v3/files/' + encodeURIComponent(fileId) + '?alt=media')).json();
    if (!valid(d)) throw new Error('Drive 데이터 형식 오류: 기존 파일을 변경하지 않았습니다.'); return d;
  }
  async function upload(d) {
    if (fileId) await api('/upload/drive/v3/files/' + encodeURIComponent(fileId) + '?uploadType=media', {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    else {
      var boundary = 'archive-' + Date.now();
      var body = '--' + boundary + '\r\nContent-Type: application/json\r\n\r\n' + JSON.stringify({name:'archive-state.json',parents:['appDataFolder']}) + '\r\n--' + boundary + '\r\nContent-Type: application/json\r\n\r\n' + JSON.stringify(d) + '\r\n--' + boundary + '--';
      var result = await (await api('/upload/drive/v3/files?uploadType=multipart',{method:'POST',headers:{'Content-Type':'multipart/related; boundary=' + boundary},body:body})).json();
      if (!result.id) throw new Error('Drive 파일 생성 실패'); fileId = result.id;
    }
  }
  async function sync() {
    if (!connected || busy) return; busy = true; message('☁️ 동기화 중…');
    try {
      var remote = await remoteState(), local = collect(), base = get(BASE, null);
      var merged = remote ? merge(local, remote, valid(base) ? base : null) : merge(local, {}, null);
      if (merged._conflicts.length) put(BACKUP, {remote:remote,local:local,fields:merged._conflicts,at:merged.updatedAt});
      // Apply before upload; failed uploads retain local edits and retry.
      apply(merged); await upload(merged);
      put(BASE, normalize(merged)); localStorage.setItem('archive-sync-updatedAt', merged.updatedAt);
      lastHash = hash(normalize(merged));
      message(merged._conflicts.length ? '☁️ 동기화 완료 · 충돌 사본 보존' : '☁️ 동기화 완료 · 변경 2초 / 원격 30초');
    } catch (e) {
      message(e.auth ? 'Drive 인증 만료 · 다시 연결해 주세요' : e.message + ' · 로컬 데이터는 유지됩니다');
      if (e.auth) { connected = false; token = null; }
    } finally { busy = false; }
  }
  function tick() { ticks++; if (connected && (hash() !== lastHash || ticks % 15 === 0)) sync(); }
  function init(el) {
    statusEl = el;
    // 재호출 시 교체된 statusEl에도 현재 상태 표시 (early return 전)
    message(connected ? '☁️ 동기화 완료 · 변경 2초 / 원격 30초' : 'Drive 미연결 · 저장은 이 기기에 유지됩니다');
    if (initialized) return;
    initialized = true;
    if (!document.querySelector('script[src*="accounts.google.com"]')) { var script = document.createElement('script'); script.src = 'https://accounts.google.com/gsi/client'; script.async = true; document.head.appendChild(script); }
    var tries = 0;
    function ready() {
      if (typeof google !== 'undefined' && google.accounts && google.accounts.oauth2) {
        tokenClient = google.accounts.oauth2.initTokenClient({client_id:CLIENT_ID,scope:'https://www.googleapis.com/auth/drive.appdata',error_callback:function () { message('Google 연결이 취소되었거나 차단되었습니다'); },callback:function (resp) {
          if (!resp.access_token) { message('Drive 연결 실패 · 다시 연결해 주세요'); return; }
          token = resp.access_token; connected = true; if (!timer) timer = setInterval(tick, 2000); sync();
        }});
      } else if (tries++ < 20) setTimeout(ready, 500);
      else { initialized = false; message('Google 연결을 불러오지 못했습니다 · 다시 시도해 주세요'); }
    }
    ready();
  }
  function connect() { if (tokenClient) tokenClient.requestAccessToken({prompt:''}); else { initialized = false; init(statusEl); message('Google 연결 준비 중 · 잠시 후 다시 눌러 주세요'); } }
  return {init:init,connect:connect,importState:function(state){if(!valid(state))throw new Error("올바른 백업이 아닙니다");apply(normalize(state));},isConnected:function () { return connected; },_validateRemote:valid,_mergeState:merge,_collectMemos:memoState,_exportState:function () { var state = collect(); state.exportedAt = new Date().toISOString(); state.conflicts = get(BACKUP, null); return state; }};
}());
window.DriveSync = DriveSync;
