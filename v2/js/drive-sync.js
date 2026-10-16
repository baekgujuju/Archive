/* Public baseline reads without login; owner export uses short-lived Drive authorization. */
var DriveSync = (function () {
  'use strict';
  var CLIENT_ID = '43687402502-rlid8pjoakcigrn6tpcs58elo357vtfr.apps.googleusercontent.com';
  var KEYS = {highlights:'archive-highlights',pinned:'archive-pinned',bookmarks:'archive-bookmarks',names:'archive-bookmark-names',deletedRemovedReplies:'archive-removed-replies',deletedReplies:'archive-deleted-replies',deletedPosts:'archive-deleted-posts'};
  var MEMO = 'archive_memo_', BASE = 'archive-sync-base', BACKUP = 'archive-sync-conflicts';
  var WRITE_PIN_HASH = '2ffb3371badee194e734205ebe9e870bb9e1857feb1e8d1984ba72231894faa6';
  var writePermit = {}, passwordBusy = false;
  var viewPrepared=false, VIEW_BASE='archive-settings-view-base';
  var SHARED_CACHE='archive-published-settings-v1', PENDING='archive-settings-pending-v1', publicStarted=false, publicBusy=false, firstPublic=true;
  var SESSION='archive-drive-session-v2', expiresAt=0, lastRead='', remoteUpdated='', warning='';
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
  function timeLabel(value) { var date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'미확인'; }
  function message(s) {
    if(statusEl&&statusEl.textContent!==s)statusEl.textContent=s;
    var quick=document.getElementById&&document.getElementById('archive-export-quick');
    if(quick){quick.textContent='내보내기 🔒';quick.setAttribute('title',s);}
    var detail=statusEl&&statusEl.parentElement&&statusEl.parentElement.querySelector('[data-drive-detail]');
    if(detail)detail.textContent=(lastRead?'이 기기에서 불러온 시각 '+timeLabel(lastRead):'아직 이 창에서 불러오지 않았습니다.')+(remoteUpdated?' · 다른 기기 내보내기 '+timeLabel(remoteUpdated):'')+(warning?' · '+warning:'');
  }
  // Only the current tab/app session survives reload; never persist tokens in localStorage or backups.
  function clearSession(){try{if(typeof sessionStorage!=='undefined')sessionStorage.removeItem(SESSION);}catch(e){}expiresAt=0;}
  function saveSession(resp){
    expiresAt=Date.now()+Math.min(3600,Math.max(1,Number(resp.expires_in)||3600))*1000;
    try{if(typeof sessionStorage!=='undefined')sessionStorage.setItem(SESSION,JSON.stringify({access_token:token,expiresAt:expiresAt,sharedWrite:true}));}catch(e){}
  }
  function restoreSession(){
    try{if(typeof sessionStorage==='undefined')return false;var saved=JSON.parse(sessionStorage.getItem(SESSION)||'null');
      if(saved&&saved.sharedWrite===true&&typeof saved.access_token==='string'&&saved.expiresAt>Date.now()+30000&&saved.expiresAt<=Date.now()+3600000){token=saved.access_token;expiresAt=saved.expiresAt;connected=true;return true;}
    }catch(e){}clearSession();return false;
  }
  function startPolling(){if(!timer)timer=setInterval(tick,15000);}
  function expire(){connected=false;token=null;fileId=null;clearSession();message('내보내기 인증 만료 · 다시 내보낼 때 연결합니다');}

  function dictionary(d) { return d !== null && typeof d === 'object' && !Array.isArray(d); }
  function valid(d) {
    if (!dictionary(d)) return false;
    return ['pinned','deletedReplies','deletedPosts','deletedRemovedReplies'].every(function (k) { return d[k] === undefined || (Array.isArray(d[k]) && d[k].every(function (x) { return typeof x === 'string'; })); }) &&
      ['highlights','bookmarks','names','bookmarkNames','memos'].every(function (k) { return d[k] === undefined || (dictionary(d[k]) && Object.keys(d[k]).every(function (id) { return !['__proto__','constructor','prototype'].includes(id) && typeof d[k][id] === 'string'; })); });
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
    if(window.ArchiveHighlights)window.ArchiveHighlights.refresh();
    if (window.ArchiveApp && window.ArchiveApp.refreshSavedState) window.ArchiveApp.refreshSavedState();
  }
  async function api(path, options) {
    var res = await fetch('https://www.googleapis.com' + path, Object.assign({}, options, {headers:Object.assign({}, options && options.headers, {Authorization:'Bearer ' + token})}));
    if (!res.ok) { var e = new Error('Drive ' + res.status); e.auth = res.status === 401 || res.status === 403; throw e; } return res;
  }
  async function remoteState() {
    if(!fileId){
      var query="name='archive-shared-state.json' and 'me' in owners and trashed=false and properties has { key='archive' and value='baekgujuju/Archive' } and properties has { key='purpose' and value='shared-state' }";
      var list=await(await api('/drive/v3/files?q='+encodeURIComponent(query)+'&spaces=drive&fields=files(id,modifiedTime)&orderBy=modifiedTime%20desc&pageSize=100')).json();
      fileId=list.files&&list.files[0]&&list.files[0].id;
    }
    if(!fileId)return null;
    var d=await(await api('/drive/v3/files/'+encodeURIComponent(fileId)+'?alt=media')).json();
    if(!valid(d)||d.schema_version!==1||typeof d.revision!=='string')throw new Error('공통 설정 형식이 다릅니다. 기존 파일은 변경하지 않았습니다.');
    return d;
  }
  function published(d){return valid(d)&&d.schema_version===1&&typeof d.revision==='string'&&Number.isFinite(new Date(d.updatedAt).getTime());}
  function pending(){var d=get(PENDING,null);return published(d)?d:null;}
  function viewBaseline(d){
    if(!localStorage.getItem('archive-before-shared-migration'))put('archive-before-shared-migration',collect());
    put(VIEW_BASE,d);var normalized=normalize(d);if(hash(normalized)!==hash(collect()))apply(normalized);
  }
  function prepareView(){
    if(viewPrepared)return;viewPrepared=true;
    var cached=get(SHARED_CACHE,null),waiting=pending();
    if(waiting&&(!published(cached)||new Date(waiting.updatedAt)>new Date(cached.updatedAt))){viewBaseline(waiting);return;}
    if(published(cached))viewBaseline(cached);
  }
  async function readPublic(){
    if(publicBusy)return false;publicBusy=true;
    try{
      var res=await fetch('data/shared-state.json',{cache:'no-store'});
      if(res.status===404){message('공통 설정 준비 중 · 기존 설정은 보존됩니다');return false;}
      if(!res.ok)throw new Error('공통 설정을 확인하지 못했습니다');
      var d=await res.json();if(!published(d))throw new Error('공통 설정 형식 오류 · 이전 설정을 유지합니다');
      var cached=get(SHARED_CACHE,null),waiting=pending(),local=collect();
      var displayed=get(VIEW_BASE,null);var dirty=published(displayed)&&hash(local)!==hash(normalize(displayed));
      var waitingIsNewer=waiting&&new Date(waiting.updatedAt)>new Date(d.updatedAt);
      if(firstPublic){if(!waitingIsNewer&&!dirty)viewBaseline(d);firstPublic=false;}
      else if(!dirty&&!waitingIsNewer)viewBaseline(d);
      else if(hash(local)===hash(normalize(d)))localStorage.removeItem(PENDING);
      put(SHARED_CACHE,d);lastRead=new Date().toISOString();remoteUpdated=d.updatedAt;
      if(waiting&&!waitingIsNewer&&hash(normalize(waiting))===hash(normalize(d)))localStorage.removeItem(PENDING);
      message(pending()?'내보내기 접수됨 · 공통 설정 반영 대기':'공통 설정 자동 업데이트됨');return true;
    }catch(e){message(e.message+' · 저장된 설정은 유지됩니다');return false;}
    finally{publicBusy=false;}
  }
  async function upload(d, permit) {
    if (permit !== writePermit) throw new Error('Drive 내보내기는 비밀번호 확인이 필요합니다.');
    if (fileId) await api('/upload/drive/v3/files/' + encodeURIComponent(fileId) + '?uploadType=media', {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
    else {
      var boundary = 'archive-' + Date.now();
      var body = '--' + boundary + '\r\nContent-Type: application/json\r\n\r\n' + JSON.stringify({name:'archive-shared-state.json',mimeType:'application/json',properties:{archive:'baekgujuju/Archive',purpose:'shared-state'}}) + '\r\n--' + boundary + '\r\nContent-Type: application/json\r\n\r\n' + JSON.stringify(d) + '\r\n--' + boundary + '--';
      var result = await (await api('/upload/drive/v3/files?uploadType=multipart',{method:'POST',headers:{'Content-Type':'multipart/related; boundary=' + boundary},body:body})).json();
      if (!result.id) throw new Error('Drive 파일 생성 실패'); fileId = result.id;
    }
  }
  async function sync(permit) {
    if(permit!==writePermit)return readPublic();
    if (!connected) throw new Error('내보내기를 위해 Google 연결을 완료해 주세요.');
    if (busy) throw new Error('동기화 중입니다. 잠시 후 다시 시도해 주세요.');
    busy = true; var writing = permit === writePermit;
    message(writing ? '☁️ Drive로 내보내는 중…' : '☁️ 내려받기 확인 중…');
    try {
      if(expiresAt&&Date.now()>=expiresAt){expire();throw new Error('Drive 연결이 만료되었습니다. 다시 연결해 주세요.');}
      var remote = await remoteState(), local = collect(), base = get(BASE, null);
      var baseFile=localStorage.getItem('archive-sync-base-file');if(baseFile&&baseFile!==fileId)base=null;
      var sharedBase=get(VIEW_BASE,null);if(published(sharedBase))base=sharedBase;
      var merged = remote ? merge(local, remote, valid(base) ? base : null) : merge(local, {}, null);
      merged.schema_version=1;merged.revision=merged.updatedAt+'-'+Math.random().toString(36).slice(2,10);
      if (merged._conflicts.length) put(BACKUP, {remote:remote,local:local,fields:merged._conflicts,at:merged.updatedAt});
      // Reading never writes to Drive. Preserve local edits and baseline the actual remote.
      // A password authorizes this single transaction, never a timer or future edit.
      if (writing) {await upload(merged, permit);var verified=await remoteState();if(!verified||hash(normalize(verified))!==hash(normalize(merged)))throw new Error('내보낸 상태를 다시 확인하지 못했습니다. 기존 로컬 상태는 유지됩니다.');}
      if(hash(normalize(merged))!==hash(normalize(local)))apply(merged);
      put(BASE, normalize(writing ? merged : remote || {}));
      if(fileId)localStorage.setItem('archive-sync-base-file',fileId);
      lastRead=new Date().toISOString();remoteUpdated=writing?merged.updatedAt:(remote&&remote.updatedAt)||'';
      warning=merged._conflicts.length?'서로 다른 변경 '+merged._conflicts.length+'건은 이 기기 값을 보존했습니다':(!remote?'이 계정에 내보낸 저장 내용이 없습니다':'');
      if (writing) {localStorage.setItem('archive-sync-updatedAt', merged.updatedAt);put(PENDING,merged);put(VIEW_BASE,merged);}
      lastHash = hash(normalize(merged));
      message(writing ? '내보내기 접수됨 · 자동 반영 대기 · 다시 잠김' : '☁️ 내려받기 완료 · 북마크 '+Object.keys(merged.bookmarks).length+' · 고정 '+merged.pinned.length+' · 내보내기 잠김');
      return true;
    } catch (e) {
      message(e.auth ? 'Drive 인증 만료 · 다시 연결해 주세요' : e.message + ' · 로컬 데이터는 유지됩니다');
      if (e.auth) { expire(); }
      if (writing) throw e;
      return false;
    } finally { busy = false; }
  }
  async function exportWithPassword(password) {
    if (passwordBusy) throw new Error('비밀번호 확인 중입니다.');
    passwordBusy = true;
    try {
      if (!connected) throw new Error('먼저 Drive를 연결해 주세요.');
      if (!window.crypto || !window.crypto.subtle) throw new Error('HTTPS에서 다시 열어 주세요.');
      var bytes = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(String(password)));
      var digest = Array.from(new Uint8Array(bytes)).map(function(b){return b.toString(16).padStart(2,'0');}).join('');
      if (digest !== WRITE_PIN_HASH) throw new Error('비밀번호가 맞지 않습니다. 내보내지 않았습니다.');
      return await sync(writePermit);
    } finally { passwordBusy = false; }
  }
  function tick(){if(!publicBusy&&!(typeof document.hidden==='boolean'&&document.hidden))readPublic().catch(function(){});}
  function init(el) {
    statusEl = el;
    // 재호출 시 교체된 statusEl에도 현재 상태 표시 (early return 전)
    message('공통 설정은 자동으로 불러옵니다 · 내보내기 잠김');
    if (initialized) return;
    initialized = true;
    restoreSession();startPolling();if(!publicStarted){publicStarted=true;readPublic();}
    if(window.addEventListener)window.addEventListener('focus',tick);
    if(document.addEventListener)document.addEventListener('visibilitychange',function(){if(!document.hidden)tick();});
    if (!document.querySelector('script[src*="accounts.google.com"]')) { var script = document.createElement('script'); script.src = 'https://accounts.google.com/gsi/client'; script.async = true; document.head.appendChild(script); }
    var tries = 0;
    function ready() {
      if (typeof google !== 'undefined' && google.accounts && google.accounts.oauth2) {
        tokenClient = google.accounts.oauth2.initTokenClient({client_id:CLIENT_ID,scope:'https://www.googleapis.com/auth/drive.file',error_callback:function () { message('Google 연결이 취소되었거나 차단되었습니다'); },callback:function (resp) {
          if (!resp.access_token) { message('Drive 연결 실패 · 다시 연결해 주세요'); return; }
          token = resp.access_token; connected = true;fileId=null;saveSession(resp);message('Google 연결됨 · 비밀번호 확인 후 내보냅니다');
        }});
      } else if (tries++ < 20) setTimeout(ready, 500);
      else { initialized = false; message('Google 연결을 불러오지 못했습니다 · 다시 시도해 주세요'); }
    }
    ready();
  }
  function connect() { if (tokenClient) tokenClient.requestAccessToken({prompt:''}); else { initialized = false; init(statusEl); message('Google 연결 준비 중 · 잠시 후 다시 눌러 주세요'); } }
  return {prepareView:prepareView,readPublic:readPublic,init:init,connect:connect,download:function(){if(!connected){connect();return Promise.resolve(false);}return sync();},exportWithPassword:exportWithPassword,importState:function(state){if(!valid(state))throw new Error("올바른 백업이 아닙니다");apply(normalize(state));},isConnected:function () { return connected; },_validateRemote:valid,_mergeState:merge,_collectMemos:memoState,_exportState:function () { var state = collect(); state.exportedAt = new Date().toISOString(); state.conflicts = get(BACKUP, null); return state; }};
}());
window.DriveSync = DriveSync;
