/* Archive v2 interactions. Classic script: keep implementation names private. */
var ArchiveApp = (function () {
  'use strict';
  // v1 호환 hex 스키마: archive-bookmarks에 hex 직접 저장 (덮어쓰기 금지)
  var colors = { '#e74c3c': '빨강', '#e67e22': '주황', '#f1c40f': '노랑', '#27ae60': '초록', '#3498db': '파랑', '#34495e': '남색', '#9b59b6': '보라' };
  var records = [], pins = [], hidden = [], bookmarks = Object.create(null), controls, list, pinned, readDialog, memoDialog, bookmarkDialog;
  var current, memoId, bookmarkRecord, mode = 'OR', selection, opener, bound = false;
  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function load(key, fallback) { try { var v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch (e) { return fallback; } }
  function memoValue(id) { try { return localStorage.getItem('archive_memo_' + id) || ''; } catch (e) { return ''; } }
  function persist(key, value) {
    try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); return true; }
    catch (e) { window.alert('저장 공간을 사용할 수 없습니다. 브라우저 저장 설정을 확인해 주세요.'); return false; }
  }
  function safeUrl(value) {
    try { var u = new URL(String(value || ''), document.baseURI); return /^https?:$/.test(u.protocol) ? u.href : ''; } catch (e) { return ''; }
  }
  // Rebuild HTML rather than trusting fullHtml or copying potentially unsafe cards.
  function cleanHtml(value) {
    var doc = new DOMParser().parseFromString(String(value || ''), 'text/html');
    var allowed = /^(P|BR|DIV|SPAN|H1|H2|H3|H4|H5|H6|STRONG|B|EM|I|U|S|UL|OL|LI|BLOCKQUOTE|PRE|CODE|TABLE|THEAD|TBODY|TR|TH|TD|HR|FIGURE|FIGCAPTION|A|IMG)$/;
    function walk(node) {
      if (node.nodeType === 3) return esc(node.nodeValue);
      if (node.nodeType !== 1 || /^(SCRIPT|STYLE|IFRAME|OBJECT|EMBED|SVG|MATH|FORM|INPUT|BUTTON|TEXTAREA|TEMPLATE)$/.test(node.tagName)) return '';
      var content = Array.prototype.map.call(node.childNodes, walk).join('');
      if (!allowed.test(node.tagName)) return content;
      var tag = node.tagName.toLowerCase(), attrs = '';
      if (tag === 'a') { var href = safeUrl(node.getAttribute('href')); if (href) attrs = ' href="' + esc(href) + '" target="_blank" rel="noopener noreferrer"'; }
      if (tag === 'img') { var src = safeUrl(node.getAttribute('src')); return src ? '<img src="' + esc(src) + '" alt="' + esc(node.getAttribute('alt') || '') + '" loading="lazy">' : ''; }
      return '<' + tag + attrs + '>' + content + (/^(br|hr)$/.test(tag) ? '' : '</' + tag + '>');
    }
    return Array.prototype.map.call(doc.body.childNodes, walk).join('');
  }
  function bodyText(a) {
    if (a.fullText) return String(a.fullText);
    if (a.fullHtml) { var d = new DOMParser().parseFromString(cleanHtml(a.fullHtml), 'text/html'); return d.body.textContent || ''; }
    return String(a.summary || '본문이 없습니다.');
  }
  function title(a) { return String(a.title || bodyText(a).split('\n')[0].slice(0, 90) || a.author || '글'); }
  function author(a) { return String(a.author && typeof a.author === 'object' ? a.author.name || a.author.handle || '' : a.author || ''); }
  function metadata(a) { return [author(a), a.handle, a.createdAt, a.source_domain, a.date_saved].filter(Boolean).join(' · '); }
  function images(media) {
    return (Array.isArray(media) ? media : []).map(function (pair) {
      var url = safeUrl(Array.isArray(pair) ? pair[1] || pair[0] : typeof pair === 'string' ? pair : pair && (pair.full || pair.url || pair.src || pair.thumb));
      return url ? '<img src="' + esc(url) + '" alt="게시물 이미지" loading="lazy">' : '';
    }).join('');
  }
  function articleHtml(a) {
    var url = safeUrl(a.sourceUrl || a.source_url), q = a.quoted;
    // 답글 HTML (v1 패턴)
    var repliesHtml = '';
    var replies = Array.isArray(a.replies) ? a.replies.filter(function (r) { return r && typeof r === 'object'; }) : [];
    if (replies.length) {
      var items = replies.map(function (r) {
        var ra = r.author || {};
        var rname = ra.name || ra.handle || '';
        var badge = r.isThread ? ' <span class="thread-badge">스레드</span>' : '';
        return '<div class="reply"><div class="reply-head"><span class="reply-author">' + esc(rname) +
          '</span><span class="reply-time">' + esc(r.createdAt || '') + '</span>' + badge + '</div>' +
          '<p class="reply-text">' + esc(r.fullText || '') + '</p></div>';
      }).join('');
      repliesHtml = '<details class="replies" open><summary>💬 댓글 ' + replies.length + '개</summary><div class="reply-list">' + items + '</div></details>';
    }
    // URL 인라인 내용
    var urlHtml = '';
    var urlContents = Array.isArray(a.urlContents) ? a.urlContents : [];
    if (urlContents.length) {
      var uitems = urlContents.map(function (uc) {
        var u = uc.url || '', t = String(uc.title || u).trim(), b = String(uc.content || '').trim();
        if (!b || b.length > 1200) b = b.slice(0, 1200) + '…';
        return '<div class="url-inline-item"><div class="url-inline-title">📄 <a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(t) + '</a></div><div class="url-inline-content">' + esc(b) + '</div></div>';
      }).join('');
      if (uitems) urlHtml = '<div class="url-inline"><div class="url-inline-head">🔗 본문 링크 내용</div>' + uitems + '</div>';
    }
    return '<h1>' + esc(title(a)) + '</h1><p class="meta">' + esc(metadata(a)) + '</p><div class="aa-body">' +
      (a.fullHtml ? cleanHtml(a.fullHtml) : '<p class="aa-text">' + esc(bodyText(a)) + '</p>') + '</div>' + images(a.mediaImages) +
      (q && typeof q === 'object' ? '<blockquote><p>' + esc(author(q)) + '</p><p class="aa-text">' + esc(q.fullText || '') + '</p>' + images(q.media || q.mediaImages) + '</blockquote>' : '') +
      repliesHtml + urlHtml +
      '<p>' + esc((a.topics || []).join(' · ')) + '</p>' + (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>' : '');
  }
  function button(text, group, value) { return '<button type="button" data-filter="' + group + '" data-value="' + esc(value) + '" aria-pressed="false">' + esc(text) + '</button>'; }
  function toggle(array, value) { var i = array.indexOf(value); if (i < 0) array.push(value); else array.splice(i, 1); }
  function status(message) { controls.querySelector('[role="status"]').textContent = message; }
  function update() {
    var words = controls.querySelector('input').value.toLowerCase().split(',').map(function (s) { return s.trim(); }).filter(Boolean), count = 0, pinCount = 0;
    records.forEach(function (r) {
      var isHidden = hidden.indexOf(r.id) >= 0, color = bookmarks[r.id], isPinned = pins.indexOf(r.id) >= 0;
      var match = !words.length || (mode === 'AND' ? words.every(function (w) { return r.search.indexOf(w) >= 0; }) : words.some(function (w) { return r.search.indexOf(w) >= 0; }));
      var visible = match && (selection.hidden ? isHidden : !isHidden) && (!selection.type.length || selection.type.indexOf(r.type) >= 0) &&
        (!selection.topic.length || selection.topic.some(function (t) { return r.topics.indexOf(t) >= 0; })) && (!selection.author.length || selection.author.indexOf(r.author) >= 0) &&
        (!selection.bookmark.length || (color && (selection.bookmark.indexOf('all') >= 0 || selection.bookmark.indexOf(color) >= 0)));
      r.card.hidden = !visible;
      r.card.style.borderLeftColor = color || '';
      r.card.classList.toggle('aa-bookmarked', !!color);
      var mb = r.card.querySelector('[data-memo-id]'), pb = r.card.querySelector('.pin-btn'), bb = r.card.querySelector('.bookmark-btn'), db = r.card.querySelector('.delete-btn');
      if (mb) { var hasMemo = !!memoValue(mb.dataset.memoId); mb.classList.remove('memo-hidden'); mb.classList.toggle('aa-active', hasMemo); mb.setAttribute('aria-label', hasMemo ? '메모 편집 (메모 있음)' : '메모 추가'); }
      if (pb) { pb.classList.toggle('aa-active', isPinned); pb.setAttribute('aria-pressed', String(isPinned)); pb.title = isPinned ? '상단 고정 해제' : '상단 고정'; }
      if (bb) { bb.style.color = color || ''; bb.setAttribute('aria-label', color ? '북마크: ' + (colors[color] || color) : '북마크'); }
      if (db) { db.title = isHidden ? '복원' : '숨기기'; db.setAttribute('aria-label', db.title); }
      var parent = isPinned ? pinned.querySelector('.aa-grid') : list;
      if (r.card.parentNode !== parent) parent.appendChild(r.card);
      if (visible) { count++; if (isPinned) pinCount++; }
    });
    // Keep original order after unpinning, and saved pin order in the pinned section.
    records.forEach(function (r) { if (pins.indexOf(r.id) < 0) list.appendChild(r.card); });
    pins.forEach(function (id) { records.forEach(function (r) { if (r.id === id) pinned.querySelector('.aa-grid').appendChild(r.card); }); });
    pinned.hidden = !pinCount;
    controls.querySelectorAll('[data-filter]').forEach(function (b) { b.setAttribute('aria-pressed', String(selection[b.dataset.filter].indexOf(b.dataset.value) >= 0)); });
    controls.querySelector('[data-action="hidden"]').setAttribute('aria-pressed', String(selection.hidden));
    // 작성자 선택 표시기 업데이트
    var ind = controls.querySelector('[data-author-indicator]');
    if (ind) ind.textContent = selection.author.length ? '(' + selection.author.length + '명 선택)' : '';
    status(count ? count + '개 글 표시' : '조건에 맞는 글이 없습니다.');
  }
  function dialog(id, label, html) {
    var d = document.createElement('dialog'); d.id = id; d.className = 'aa-dialog'; d.setAttribute('aria-label', label); d.innerHTML = html; document.body.appendChild(d);
    d.addEventListener('click', function (e) { if (e.target.closest('[data-close]')) close(d); });
    d.addEventListener('cancel', function (e) { e.preventDefault(); close(d); });
    d.addEventListener('close', function () { document.body.classList.remove('aa-modal-open'); if (opener && opener.isConnected) opener.focus(); });
    return d;
  }
  function show(d) { opener = document.activeElement; if (typeof d.showModal === 'function') d.showModal(); else { d.setAttribute('open', ''); d.setAttribute('aria-modal', 'true'); } document.body.classList.add('aa-modal-open'); var f = d.querySelector('textarea,button'); if (f) f.focus(); }
  function close(d) { if (typeof d.close === 'function') d.close(); else { d.removeAttribute('open'); document.body.classList.remove('aa-modal-open'); if (opener) opener.focus(); } }
  function openRead(r) { current = r; readDialog.querySelector('.aa-content').innerHTML = articleHtml(r.article); show(readDialog); readDialog.scrollTop = 0; }
  function download(blob, name) { var url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(function () { URL.revokeObjectURL(url); }, 60000); }
  function shareFile(blob, name, heading) {
    var file;
    try { file = new File([blob], name, { type: blob.type }); } catch (e) { download(blob, name); return; }
    if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
      navigator.share({ files: [file], title: heading }).catch(function (e) { if (e.name !== 'AbortError') download(blob, name); });
    } else download(blob, name);
  }
  function shareImage(a) {
    var canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
    if (!ctx) { window.alert('이미지를 생성할 수 없습니다.'); return; }
    var width = 1080, lines = [];
    function wrap(text, font, height, color) {
      ctx.font = font;
      String(text).split('\n').forEach(function (p) {
        var line = '';
        Array.from(p).forEach(function (ch) { if (line && ctx.measureText(line + ch).width > width - 96) { lines.push({ text: line, font: font, height: height, color: color }); line = ''; } line += ch; });
        lines.push({ text: line, font: font, height: height, color: color });
      });
    }
    wrap(title(a), 'bold 36px sans-serif', 50, '#222b3b'); wrap(metadata(a), '24px sans-serif', 36, '#716e65'); wrap(bodyText(a), '28px sans-serif', 42, '#222b3b');
    // Paginate long articles so mobile canvas limits do not truncate the body.
    var pages = [], page = [], height = 96;
    lines.forEach(function (line) { if (height + line.height > 4000) { pages.push(page); page = []; height = 96; } page.push(line); height += line.height; }); if (page.length) pages.push(page);
    var blobs = [], index = 0;
    function next() {
      if (index >= pages.length) { if (blobs.length === 1) shareFile(blobs[0], 'archive.png', title(a)); else blobs.forEach(function (blob, i) { download(blob, 'archive-' + (i + 1) + '.png'); }); return; }
      var rows = pages[index++]; canvas.width = width; canvas.height = 96 + rows.reduce(function (sum, row) { return sum + row.height; }, 0); ctx.fillStyle = '#f6f2e9'; ctx.fillRect(0, 0, canvas.width, canvas.height); var y = 48;
      rows.forEach(function (row) { ctx.font = row.font; ctx.fillStyle = row.color; ctx.textBaseline = 'top'; ctx.fillText(row.text, 48, y); y += row.height; });
      canvas.toBlob(function (blob) { if (!blob) { window.alert('PNG 생성에 실패했습니다.'); return; } blobs.push(blob); next(); }, 'image/png');
    }
    next();
  }
  function styles() {
    if (document.getElementById('archive-app-style')) return;
    var s = document.createElement('style'); s.id = 'archive-app-style'; s.textContent = '\n' +
      '.wrap{max-width:1280px}#articles,.aa-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}.aa-controls{margin-bottom:20px}.aa-controls input{width:100%;min-height:44px;font-size:16px;padding:10px}.aa-search{display:flex;gap:8px}.aa-search input{min-width:0;flex:1}.aa-chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;max-width:100%}.aa-chips button{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.aa-authors{margin:8px 0}.aa-authors summary{cursor:pointer;padding:8px;font-weight:700;list-style:none}.aa-authors summary::-webkit-details-marker{display:none}.aa-authors summary [data-author-indicator]{font-weight:400;color:var(--accent)}.aa-controls button,.aa-dialog button,.article button{min-width:44px;min-height:44px;cursor:pointer;font:inherit;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);padding:8px}.aa-controls button[aria-pressed=true],.aa-active{background:#ddd6ca!important;font-weight:bold}.article{min-width:0;border:1px solid var(--line);border-left:5px solid var(--line);border-radius:10px;background:#fff;padding:14px}.article[hidden],.aa-pinned[hidden]{display:none!important}.card-toggle{cursor:pointer;list-style:none}.card-toggle::-webkit-details-marker{display:none}.card-header,.card-actions,.byline{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.card-header{justify-content:space-between}.article svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.7}.memo-btn{display:inline-flex!important;align-items:center;justify-content:center}.article h3{overflow-wrap:anywhere}.meta,.handle{color:var(--muted);font-size:.85em}.summary{white-space:pre-wrap}.tags{display:flex;gap:6px;flex-wrap:wrap}.tag{background:var(--tag-paper);color:var(--tag-ink);padding:3px 7px;border-radius:6px}.article img{max-width:100%}.full-text{white-space:pre-wrap;overflow-wrap:anywhere}.aa-pinned{margin-bottom:20px}.aa-dialog{border:0;background:var(--paper);color:var(--ink);padding:24px;max-width:680px;width:calc(100% - 24px);max-height:90vh;overflow:auto}.aa-dialog::backdrop{background:rgba(0,0,0,.55)}#archive-read-dialog{position:fixed;inset:0;margin:0;width:100%;height:100%;max-width:none;max-height:none;padding: max(16px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(16px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left))}.aa-toolbar{display:flex;flex-wrap:wrap;gap:8px;max-width:850px;margin:0 auto 20px}.aa-content{max-width:850px;margin:auto;overflow-wrap:anywhere}.aa-content img{max-width:100%;height:auto;display:block;margin:16px auto}.aa-content table{display:block;overflow:auto}.aa-text{white-space:pre-wrap}.aa-dialog textarea{width:100%;min-height:240px;font-size:16px;padding:12px}.aa-modal-open{overflow:hidden}.aa-dialog:not([open]){display:none}.aa-dialog[open]{display:block}@media(min-width:768px){#articles,.aa-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(min-width:1024px){#articles,.aa-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media print{body.aa-modal-open>.wrap,body.aa-modal-open>.aa-dialog:not(#archive-read-dialog),.aa-toolbar{display:none!important}body.aa-modal-open{overflow:visible;zoom:1}#archive-read-dialog[open]{position:static;height:auto;overflow:visible;padding:0}.aa-content{max-width:none}.aa-content img{break-inside:avoid}}';
    document.head.appendChild(s);
  }
  function init(articles) {
    list = document.getElementById('articles'); if (!list) return;
    if (pinned) pinned.querySelectorAll('.article').forEach(function (card) { list.appendChild(card); });
    document.body.classList.remove('aa-modal-open');
    [controls, pinned, readDialog, memoDialog, bookmarkDialog].forEach(function (el) { if (el) el.remove(); });
    selection = { type: [], topic: [], author: [], bookmark: [], hidden: false }; mode = 'OR'; current = null;
    pins = load('archive-pinned', []); hidden = load('archive-deleted-posts', []);
    pins = Array.isArray(pins) ? pins.map(String) : []; hidden = Array.isArray(hidden) ? hidden.map(String) : [];
    var saved = load('archive-bookmarks', {}); bookmarks = Object.create(null);
    // v1 호환: 저장된 hex 값을 그대로 보존 (사전 정의되지 않은 custom 색상도 유지)
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) Object.keys(saved).forEach(function (id) { if (typeof saved[id] === 'string' && saved[id]) bookmarks[id] = saved[id]; });
    var byIdx = Object.create(null); (articles || []).forEach(function (a, i) { byIdx[String(a._idx == null ? i : a._idx)] = a; });
    records = Array.prototype.map.call(list.querySelectorAll('.article'), function (card) {
      var a = byIdx[card.dataset.idx] || {}, topics = Array.isArray(a.topics) ? a.topics.map(String) : (card.dataset.topics || '').split('\n').filter(Boolean);
      return { card: card, id: card.dataset.pinId || card.dataset.idx, article: a, author: card.dataset.author || author(a), type: card.dataset.type || a.type, topics: topics,
        search: [title(a), bodyText(a), a.summary, author(a), a.handle, topics.join(' ')].join(' ').toLowerCase() };
    });
    styles(); controls = document.createElement('section'); controls.className = 'aa-controls'; controls.id = 'archive-controls';
    var topics = [], authors = []; records.forEach(function (r) { r.topics.forEach(function (t) { if (topics.indexOf(t) < 0) topics.push(t); }); if (r.author && authors.indexOf(r.author) < 0) authors.push(r.author); });
    controls.innerHTML = '<div class="aa-search"><input type="search" aria-label="글 검색" placeholder="제목·본문·저자·주제 검색 (쉼표로 구분)"><button type="button" data-action="mode" aria-label="검색 조건 OR, 클릭하여 AND로 변경">OR</button></div><div class="aa-chips" role="group" aria-label="유형">' + button('X 게시물', 'type', 'x') + button('웹글', 'type', 'web') + button('IB', 'type', 'ib') + '</div><div class="aa-chips" role="group" aria-label="주제">' + topics.sort().map(function (t) { return button(t, 'topic', t); }).join('') + '</div><details class="aa-authors"><summary>저자 <span data-author-indicator></span> ▾</summary><div class="aa-chips" role="group" aria-label="저자">' + authors.sort().map(function (a) { return button(a, 'author', a); }).join('') + '</div></details><div class="aa-chips" role="group" aria-label="북마크 필터">' + button('북마크 전체', 'bookmark', 'all') + Object.keys(colors).map(function (c) { return button(colors[c] || c, 'bookmark', c); }).join('') + '</div><details><summary>설정 · 숨긴 글 관리</summary><button type="button" data-action="hidden" aria-pressed="false">숨긴 글 보기 / 복원</button><button type="button" data-action="drive">🔗 Drive 연결</button><span data-drive-status role="status"></span></details><p role="status" aria-live="polite"></p>';
    list.parentNode.insertBefore(controls, list); pinned = document.createElement('section'); pinned.className = 'aa-pinned'; pinned.innerHTML = '<h2>📌 고정한 글</h2><div class="aa-grid"></div>'; list.parentNode.insertBefore(pinned, list);
    controls.addEventListener('input', update); controls.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.dataset.filter) toggle(selection[b.dataset.filter], b.dataset.value);
      if (b.dataset.action === 'mode') { mode = mode === 'OR' ? 'AND' : 'OR'; b.textContent = mode; b.setAttribute('aria-label', '검색 조건 ' + mode + ', 클릭하여 변경'); }
      if (b.dataset.action === 'hidden') selection.hidden = !selection.hidden;
      if (b.dataset.action === 'drive') { DriveSync.connect(); return; }
      update();
    });
    // Drive 동기화 초기화
    DriveSync.init(controls.querySelector('[data-drive-status]'));
    readDialog = dialog('archive-read-dialog', '글 읽기', '<div class="aa-toolbar"><button type="button" data-close>닫기 ×</button><button type="button" data-share="image">🖼️ 이미지로 공유</button><button type="button" data-share="html">🌐 HTML로 공유</button><button type="button" data-share="print">🖨️ 인쇄</button></div><article class="aa-content"></article>');
    readDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-share]'); if (!b || !current) return; var a = current.article;
      if (b.dataset.share === 'print') window.print();
      if (b.dataset.share === 'image') shareImage(a);
      if (b.dataset.share === 'html') shareFile(new Blob(['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(title(a)) + '</title><style>body{max-width:850px;margin:32px auto;padding:16px;font:16px/1.7 sans-serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}.aa-text{white-space:pre-wrap}table{display:block;overflow:auto}</style><body>' + articleHtml(a) + '</body></html>'], { type: 'text/html' }), 'archive.html', title(a));
    });
    memoDialog = dialog('archive-memo-dialog', '메모 편집', '<h2>메모</h2><label for="archive-memo-text">내용</label><textarea id="archive-memo-text"></textarea><div class="aa-chips"><button type="button" data-memo="save">저장</button><button type="button" data-memo="delete">삭제</button><button type="button" data-close>닫기</button></div>');
    memoDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-memo]'); if (!b) return; var value = b.dataset.memo === 'delete' ? null : memoDialog.querySelector('textarea').value; if (persist('archive_memo_' + memoId, value)) { close(memoDialog); update(); } });
    bookmarkDialog = dialog('archive-bookmark-dialog', '북마크 색상 선택', '<h2>북마크 색상</h2><div class="aa-chips">' + Object.keys(colors).map(function (c) { return '<button type="button" data-color="' + c + '" style="border-color:' + c + '">' + (colors[c] || c) + '</button>'; }).join('') + '<button type="button" data-color="">해제</button><button type="button" data-close>닫기</button></div>');
    bookmarkDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-color]'); if (!b || !bookmarkRecord) return; var next = Object.assign(Object.create(null), bookmarks); if (b.dataset.color) next[bookmarkRecord.id] = b.dataset.color; else delete next[bookmarkRecord.id]; if (persist('archive-bookmarks', next)) { bookmarks = next; close(bookmarkDialog); update(); } });
    if (!bound) {
      document.addEventListener('click', cardClick);
      document.addEventListener('keydown', function (e) { var d = [memoDialog, bookmarkDialog, readDialog].filter(function (item) { return item && item.hasAttribute('open'); })[0]; if (!d) return;
        if (e.key === 'Escape') { e.preventDefault(); close(d); }
        if (e.key === 'Tab' && typeof d.showModal !== 'function') { var els = d.querySelectorAll('button,textarea,a[href]'), first = els[0], last = els[els.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
      });
      bound = true;
    }
    update();
  }
  function cardClick(e) {
    var card = e.target.closest('.article'); if (!card) return; var r = records.filter(function (item) { return item.card === card; })[0]; if (!r) return;
    var b = e.target.closest('button');
    if (b) {
      e.preventDefault();
      if (b.hasAttribute('data-memo-id')) { memoId = b.dataset.memoId; memoDialog.querySelector('textarea').value = memoValue(memoId); show(memoDialog); }
      else if (b.classList.contains('bookmark-btn')) { bookmarkRecord = r; show(bookmarkDialog); }
      else if (b.classList.contains('pin-btn')) { var nextPins = pins.slice(); toggle(nextPins, r.id); if (persist('archive-pinned', nextPins)) { pins = nextPins; update(); } }
      else if (b.classList.contains('delete-btn')) { var restoring = hidden.indexOf(r.id) >= 0; if (restoring || window.confirm('이 글을 숨기시겠습니까? 설정에서 복원할 수 있습니다.')) { var nextHidden = hidden.slice(); toggle(nextHidden, r.id); if (persist('archive-deleted-posts', nextHidden)) { hidden = nextHidden; update(); } } }
      else if (b.classList.contains('read-window-btn')) openRead(r);
      return;
    }
    if (e.target.closest('a,input,textarea,select,label')) return;
    e.preventDefault(); openRead(r);
  }
  return { init: init, DriveSync: DriveSync };
}());

/* ------------------------------------------------------------------ */
/* Drive 동기화 (v1 포팅): drive.appdata 스코프, 2초 간격 로컬 변경 업로드 */
/* 실제 동작: 연결 후 2초마다 로컬 변경사항만 업로드. 지속적인 원격 다운로드는 없음. */
/* ------------------------------------------------------------------ */
var DriveSync = (function () {
  'use strict';
  var CLIENT_ID = '43687402502-rlid8pjoakcigrn6tpcs58elo357vtfr.apps.googleusercontent.com';
  var SCOPE = 'https://www.googleapis.com/auth/drive.appdata';
  var STATE_KEYS = ['archive-pinned', 'archive-bookmarks', 'archive-bookmark-names', 'archive-deleted-replies', 'archive-deleted-posts'];
  var MEMO_PREFIX = 'archive_memo_';
  var TS_KEY = 'archive-sync-updatedAt';
  var tokenClient = null, accessToken = null, fileId = null, connected = false, lastHash = '';
  var statusEl = null, timerId = null, inflight = false;

  function get(k, d) { try { var v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } }
  function set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function getTs() { try { return localStorage.getItem(TS_KEY) || ''; } catch (e) { return ''; } }
  function setTs(v) { try { localStorage.setItem(TS_KEY, v); } catch (e) {} }
  function collectMemos() {
    var memos = {};
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(MEMO_PREFIX) === 0) memos[k.slice(MEMO_PREFIX.length)] = localStorage.getItem(k);
      }
    } catch (e) {}
    return memos;
  }
  function stateHash() {
    try {
      var parts = STATE_KEYS.map(function (k) { return localStorage.getItem(k) || ''; });
      parts.push(JSON.stringify(collectMemos()));
      return parts.join('|');
    } catch (e) { return ''; }
  }
  function collectState() {
    return {
      pinned: get('archive-pinned', []),
      bookmarks: get('archive-bookmarks', {}),
      names: get('archive-bookmark-names', {}),
      deletedReplies: get('archive-deleted-replies', []),
      deletedPosts: get('archive-deleted-posts', []),
      memos: collectMemos(),
      updatedAt: new Date().toISOString()
    };
  }
  // 원격 데이터 유효성 검증
  function validateRemote(d) {
    if (!d || typeof d !== 'object') return false;
    if (d.pinned !== undefined && !Array.isArray(d.pinned)) return false;
    if (d.bookmarks !== undefined && (typeof d.bookmarks !== 'object' || Array.isArray(d.bookmarks))) return false;
    if (d.deletedPosts !== undefined && !Array.isArray(d.deletedPosts)) return false;
    if (d.deletedReplies !== undefined && !Array.isArray(d.deletedReplies)) return false;
    if (d.memos !== undefined && (typeof d.memos !== 'object' || Array.isArray(d.memos))) return false;
    return true;
  }
  // 명시적 병합 정책: 로컬+원격 모두 보존 (union)
  function mergeState(local, remote) {
    var merged = {
      pinned: unionArr(local.pinned, remote.pinned),
      bookmarks: Object.assign({}, remote.bookmarks, local.bookmarks),
      names: Object.assign({}, remote.names, local.names),
      deletedReplies: unionArr(local.deletedReplies, remote.deletedReplies),
      deletedPosts: unionArr(local.deletedPosts, remote.deletedPosts),
      memos: Object.assign({}, remote.memos, local.memos),
      updatedAt: new Date().toISOString()
    };
    return merged;
  }
  function unionArr(a, b) {
    var seen = {}, out = [];
    (a || []).concat(b || []).forEach(function (x) {
      var k = typeof x === 'string' ? x : JSON.stringify(x);
      if (!seen[k]) { seen[k] = 1; out.push(x); }
    });
    return out;
  }
  function applyState(d) {
    if (Array.isArray(d.pinned)) set('archive-pinned', d.pinned);
    if (d.bookmarks && typeof d.bookmarks === 'object') set('archive-bookmarks', d.bookmarks);
    var nms = d.names || d.bookmarkNames;
    if (nms && typeof nms === 'object') set('archive-bookmark-names', nms);
    if (Array.isArray(d.deletedPosts)) set('archive-deleted-posts', unionArr(get('archive-deleted-posts', []), d.deletedPosts));
    if (Array.isArray(d.deletedReplies)) set('archive-deleted-replies', d.deletedReplies);
    if (d.memos && typeof d.memos === 'object') {
      Object.keys(d.memos).forEach(function (id) {
        try {
          var key = MEMO_PREFIX + id;
          if (!localStorage.getItem(key) && d.memos[id]) localStorage.setItem(key, d.memos[id]);
        } catch (e) {}
      });
    }
    if (d.updatedAt) setTs(d.updatedAt);
  }
  function setStatus(txt, isError) {
    if (statusEl) {
      statusEl.textContent = txt;
      statusEl.style.color = isError ? '#c0392b' : '';
    }
  }
  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    headers.Authorization = 'Bearer ' + accessToken;
    opts.headers = headers;
    return fetch('https://www.googleapis.com' + path, opts).then(function (r) {
      if (r.status === 401 || r.status === 403) throw new Error('AUTH:' + r.status);
      if (!r.ok) throw new Error('Drive API ' + r.status);
      return r;
    });
  }
  // 기존 appData 파일 찾기 (v1 호환)
  function findFile() {
    return api('/drive/v3/files?q=' + encodeURIComponent("name='archive-state.json' and 'appDataFolder' in parents") + '&spaces=appDataFolder&fields=files(id,modifiedTime)')
      .then(function (r) { return r.json(); })
      .then(function (j) { return (j.files && j.files[0]) || null; });
  }
  function downloadState() {
    return findFile().then(function (f) {
      if (!f) return null;
      fileId = f.id;
      return api('/drive/v3/files/' + f.id + '?alt=media')
        .then(function (r) { return r.json(); });
    });
  }
  function uploadState(data) {
    var body = JSON.stringify(data);
    var done = function () { setTs(data.updatedAt); lastHash = stateHash(); setStatus('☁️ Drive 동기화됨'); };
    if (fileId) {
      return api('/upload/drive/v3/files/' + fileId + '?uploadType=media', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: body
      }).then(done);
    }
    var metadata = { name: 'archive-state.json', parents: ['appDataFolder'] };
    var boundary = '-------drive-sync-' + Date.now();
    var multipart = '--' + boundary + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' +
      JSON.stringify(metadata) + '\r\n--' + boundary + '\r\nContent-Type: application/json\r\n\r\n' +
      body + '\r\n--' + boundary + '--';
    return api('/upload/drive/v3/files?uploadType=multipart', {
      method: 'POST', headers: { 'Content-Type': 'multipart/related; boundary=' + boundary }, body: multipart
    }).then(function (r) { return r.json(); }).then(function (j) { fileId = j.id; done(); });
  }
  // 초기 동기화: 원격 상태 다운로드 → 검증 → 병합
  function doSync() {
    if (!connected || !accessToken || inflight) return;
    inflight = true;
    setStatus('☁️ Drive 동기화 중...');
    downloadState().then(function (remote) {
      if (remote) {
        if (!validateRemote(remote)) {
          setStatus('☁️ Drive 데이터 형식 오류', true);
          inflight = false;
          return;
        }
        var localTs = getTs();
        if (remote.updatedAt && (!localTs || remote.updatedAt > localTs)) {
          // 원격이 newer → 병합 후 적용
          var merged = mergeState(collectState(), remote);
          applyState(merged);
          lastHash = stateHash();
          setStatus('☁️ Drive 동기화됨 (병합)');
          if (typeof location !== 'undefined') location.reload();
        } else {
          setStatus('☁️ Drive 연결됨');
          lastHash = stateHash();
        }
      } else {
        // 원격 파일 없음 → 로컬 업로드
        setStatus('☁️ Drive 연결됨');
        lastHash = stateHash();
      }
      inflight = false;
    }).catch(function (e) {
      inflight = false;
      if (String(e.message).indexOf('AUTH:') === 0) {
        setStatus('🔗 Drive 인증 필요 (다시 연결)', true);
        connected = false; accessToken = null;
      } else {
        setStatus('☁️ Drive 오류', true);
      }
    });
  }
  function autoSave() {
    if (!connected || !accessToken || inflight) return;
    var h = stateHash();
    if (h !== lastHash) {
      inflight = true;
      // 업로드 전 기존 파일 확인 (fileId가 없으면)
      var ensure = fileId ? Promise.resolve() : findFile().then(function (f) { if (f) fileId = f.id; });
      ensure.then(function () {
        return uploadState(collectState());
      }).then(function () { inflight = false; })
      .catch(function (e) {
        inflight = false;
        if (String(e.message).indexOf('AUTH:') === 0) {
          setStatus('🔗 Drive 인증 필요 (다시 연결)', true);
          connected = false; accessToken = null;
        } else {
          setStatus('☁️ Drive 오류', true);
        }
      });
    }
  }
  return {
    init: function (el) {
      statusEl = el;
      if (!document.querySelector('script[src*="accounts.google.com"]')) {
        var s = document.createElement('script');
        s.src = 'https://accounts.google.com/gsi/client';
        s.async = true; s.defer = true;
        document.head.appendChild(s);
      }
      var tries = 0;
      var wait = function () {
        if (typeof google !== 'undefined' && google.accounts && google.accounts.oauth2) {
          tokenClient = google.accounts.oauth2.initTokenClient({
            client_id: CLIENT_ID, scope: SCOPE,
            callback: function (resp) {
              if (resp && resp.access_token) {
                accessToken = resp.access_token; connected = true;
                doSync(); // 연결 시 원격 상태 확인 및 병합
                if (!timerId) timerId = setInterval(autoSave, 2000); // 단일 타이머
              } else {
                setStatus('🔗 Drive 연결 실패', true);
              }
            }
          });
          setStatus('🔗 Drive 연결');
        } else if (tries++ < 20) { setTimeout(wait, 500); }
        else { setStatus('🔗 Drive 연결 불가', true); }
      };
      wait();
    },
    connect: function () {
      if (tokenClient) tokenClient.requestAccessToken({ prompt: 'consent' });
      else setStatus('🔗 Drive 초기화 중...', true);
    },
    isConnected: function () { return connected; },
    // 테스트용 노출
    _validateRemote: validateRemote,
    _mergeState: mergeState,
    _collectMemos: collectMemos
  };
})();

window.ArchiveApp = ArchiveApp;
