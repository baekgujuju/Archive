/* Archive v2 interactions. Classic script: keep implementation names private. */
var ArchiveApp = (function () {
  'use strict';
  var colors = { red: '#dc3545', orange: '#ed861c', yellow: '#c5a300', green: '#25854a', blue: '#2878ce', purple: '#8554b8' };
  var labels = { red: '빨강', orange: '주황', yellow: '노랑', green: '초록', blue: '파랑', purple: '보라' };
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
    return '<h1>' + esc(title(a)) + '</h1><p class="meta">' + esc(metadata(a)) + '</p><div class="aa-body">' +
      (a.fullHtml ? cleanHtml(a.fullHtml) : '<p class="aa-text">' + esc(bodyText(a)) + '</p>') + '</div>' + images(a.mediaImages) +
      (q && typeof q === 'object' ? '<blockquote><p>' + esc(author(q)) + '</p><p class="aa-text">' + esc(q.fullText || '') + '</p>' + images(q.media || q.mediaImages) + '</blockquote>' : '') +
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
      r.card.style.borderLeftColor = colors[color] || '';
      r.card.classList.toggle('aa-bookmarked', !!color);
      var mb = r.card.querySelector('[data-memo-id]'), pb = r.card.querySelector('.pin-btn'), bb = r.card.querySelector('.bookmark-btn'), db = r.card.querySelector('.delete-btn');
      if (mb) { var hasMemo = !!memoValue(mb.dataset.memoId); mb.classList.remove('memo-hidden'); mb.classList.toggle('aa-active', hasMemo); mb.setAttribute('aria-label', hasMemo ? '메모 편집 (메모 있음)' : '메모 추가'); }
      if (pb) { pb.classList.toggle('aa-active', isPinned); pb.setAttribute('aria-pressed', String(isPinned)); pb.title = isPinned ? '상단 고정 해제' : '상단 고정'; }
      if (bb) { bb.style.color = colors[color] || ''; bb.setAttribute('aria-label', color ? '북마크: ' + labels[color] : '북마크'); }
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
      '.wrap{max-width:1280px}#articles,.aa-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}.aa-controls{margin-bottom:20px}.aa-controls input{width:100%;min-height:44px;font-size:16px;padding:10px}.aa-search{display:flex;gap:8px}.aa-chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.aa-controls button,.aa-dialog button,.article button{min-width:44px;min-height:44px;cursor:pointer;font:inherit;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);padding:8px}.aa-controls button[aria-pressed=true],.aa-active{background:#ddd6ca!important;font-weight:bold}.article{min-width:0;border:1px solid var(--line);border-left:5px solid var(--line);border-radius:10px;background:#fff;padding:14px}.article[hidden],.aa-pinned[hidden]{display:none!important}.card-toggle{cursor:pointer;list-style:none}.card-toggle::-webkit-details-marker{display:none}.card-header,.card-actions,.byline{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.card-header{justify-content:space-between}.article svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.7}.memo-btn{display:inline-flex!important;align-items:center;justify-content:center}.article h3{overflow-wrap:anywhere}.meta,.handle{color:var(--muted);font-size:.85em}.summary{white-space:pre-wrap}.tags{display:flex;gap:6px;flex-wrap:wrap}.tag{background:var(--tag-paper);color:var(--tag-ink);padding:3px 7px;border-radius:6px}.article img{max-width:100%}.full-text{white-space:pre-wrap;overflow-wrap:anywhere}.aa-pinned{margin-bottom:20px}.aa-dialog{border:0;background:var(--paper);color:var(--ink);padding:24px;max-width:680px;width:calc(100% - 24px);max-height:90vh;overflow:auto}.aa-dialog::backdrop{background:rgba(0,0,0,.55)}#archive-read-dialog{position:fixed;inset:0;margin:0;width:100%;height:100%;max-width:none;max-height:none;padding: max(16px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(16px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left))}.aa-toolbar{display:flex;flex-wrap:wrap;gap:8px;max-width:850px;margin:0 auto 20px}.aa-content{max-width:850px;margin:auto;overflow-wrap:anywhere}.aa-content img{max-width:100%;height:auto;display:block;margin:16px auto}.aa-content table{display:block;overflow:auto}.aa-text{white-space:pre-wrap}.aa-dialog textarea{width:100%;min-height:240px;font-size:16px;padding:12px}.aa-modal-open{overflow:hidden}.aa-dialog:not([open]){display:none}.aa-dialog[open]{display:block}@media(min-width:768px){#articles,.aa-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(min-width:1024px){#articles,.aa-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media print{body.aa-modal-open>.wrap,body.aa-modal-open>.aa-dialog:not(#archive-read-dialog),.aa-toolbar{display:none!important}body.aa-modal-open{overflow:visible;zoom:1}#archive-read-dialog[open]{position:static;height:auto;overflow:visible;padding:0}.aa-content{max-width:none}.aa-content img{break-inside:avoid}}';
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
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) Object.keys(saved).forEach(function (id) { if (Object.prototype.hasOwnProperty.call(colors, saved[id])) bookmarks[id] = saved[id]; });
    var byIdx = Object.create(null); (articles || []).forEach(function (a, i) { byIdx[String(a._idx == null ? i : a._idx)] = a; });
    records = Array.prototype.map.call(list.querySelectorAll('.article'), function (card) {
      var a = byIdx[card.dataset.idx] || {}, topics = Array.isArray(a.topics) ? a.topics.map(String) : (card.dataset.topics || '').split('\n').filter(Boolean);
      return { card: card, id: card.dataset.pinId || card.dataset.idx, article: a, author: card.dataset.author || author(a), type: card.dataset.type || a.type, topics: topics,
        search: [title(a), bodyText(a), a.summary, author(a), a.handle, topics.join(' ')].join(' ').toLowerCase() };
    });
    styles(); controls = document.createElement('section'); controls.className = 'aa-controls'; controls.id = 'archive-controls';
    var topics = [], authors = []; records.forEach(function (r) { r.topics.forEach(function (t) { if (topics.indexOf(t) < 0) topics.push(t); }); if (r.author && authors.indexOf(r.author) < 0) authors.push(r.author); });
    controls.innerHTML = '<div class="aa-search"><input type="search" aria-label="글 검색" placeholder="제목·본문·저자·주제 검색 (쉼표로 구분)"><button type="button" data-action="mode" aria-label="검색 조건 OR, 클릭하여 AND로 변경">OR</button></div><div class="aa-chips" role="group" aria-label="유형">' + button('X 게시물', 'type', 'x') + button('웹글', 'type', 'web') + button('IB', 'type', 'ib') + '</div><div class="aa-chips" role="group" aria-label="주제">' + topics.sort().map(function (t) { return button(t, 'topic', t); }).join('') + '</div><div class="aa-chips" role="group" aria-label="저자">' + authors.sort().map(function (a) { return button(a, 'author', a); }).join('') + '</div><div class="aa-chips" role="group" aria-label="북마크 필터">' + button('북마크 전체', 'bookmark', 'all') + Object.keys(colors).map(function (c) { return button(labels[c], 'bookmark', c); }).join('') + '</div><details><summary>설정 · 숨긴 글 관리</summary><button type="button" data-action="hidden" aria-pressed="false">숨긴 글 보기 / 복원</button></details><p role="status" aria-live="polite"></p>';
    list.parentNode.insertBefore(controls, list); pinned = document.createElement('section'); pinned.className = 'aa-pinned'; pinned.innerHTML = '<h2>📌 고정한 글</h2><div class="aa-grid"></div>'; list.parentNode.insertBefore(pinned, list);
    controls.addEventListener('input', update); controls.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.dataset.filter) toggle(selection[b.dataset.filter], b.dataset.value);
      if (b.dataset.action === 'mode') { mode = mode === 'OR' ? 'AND' : 'OR'; b.textContent = mode; b.setAttribute('aria-label', '검색 조건 ' + mode + ', 클릭하여 변경'); }
      if (b.dataset.action === 'hidden') selection.hidden = !selection.hidden;
      update();
    });
    readDialog = dialog('archive-read-dialog', '글 읽기', '<div class="aa-toolbar"><button type="button" data-close>닫기 ×</button><button type="button" data-share="image">🖼️ 이미지로 공유</button><button type="button" data-share="html">🌐 HTML로 공유</button><button type="button" data-share="print">🖨️ 인쇄</button></div><article class="aa-content"></article>');
    readDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-share]'); if (!b || !current) return; var a = current.article;
      if (b.dataset.share === 'print') window.print();
      if (b.dataset.share === 'image') shareImage(a);
      if (b.dataset.share === 'html') shareFile(new Blob(['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(title(a)) + '</title><style>body{max-width:850px;margin:32px auto;padding:16px;font:16px/1.7 sans-serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}.aa-text{white-space:pre-wrap}table{display:block;overflow:auto}</style><body>' + articleHtml(a) + '</body></html>'], { type: 'text/html' }), 'archive.html', title(a));
    });
    memoDialog = dialog('archive-memo-dialog', '메모 편집', '<h2>메모</h2><label for="archive-memo-text">내용</label><textarea id="archive-memo-text"></textarea><div class="aa-chips"><button type="button" data-memo="save">저장</button><button type="button" data-memo="delete">삭제</button><button type="button" data-close>닫기</button></div>');
    memoDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-memo]'); if (!b) return; var value = b.dataset.memo === 'delete' ? null : memoDialog.querySelector('textarea').value; if (persist('archive_memo_' + memoId, value)) { close(memoDialog); update(); } });
    bookmarkDialog = dialog('archive-bookmark-dialog', '북마크 색상 선택', '<h2>북마크 색상</h2><div class="aa-chips">' + Object.keys(colors).map(function (c) { return '<button type="button" data-color="' + c + '" style="border-color:' + colors[c] + '">' + labels[c] + '</button>'; }).join('') + '<button type="button" data-color="">해제</button><button type="button" data-close>닫기</button></div>');
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
  return { init: init };
}());
window.ArchiveApp = ArchiveApp;
