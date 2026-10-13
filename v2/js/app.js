/* Archive v2 interactions. Classic script: keep implementation names private. */
var ArchiveApp = (function () {
  'use strict';
  // v1 호환 hex 스키마: archive-bookmarks에 hex 직접 저장 (덮어쓰기 금지)
  var colors = { '#e74c3c': '빨강', '#e67e22': '주황', '#f1c40f': '노랑', '#27ae60': '초록', '#3498db': '파랑', '#34495e': '남색', '#9b59b6': '보라' };
  var records = [], pins = [], hidden = [], bookmarks = Object.create(null), controls, list, pinned, readDialog, memoDialog, bookmarkDialog;
  var current, memoId, bookmarkRecord, mode = 'OR', selection, opener, bound = false, imageDialog, aiList;
  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function load(key, fallback) { try { var v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch (e) { return fallback; } }
  function memoValue(id) { try { return localStorage.getItem('archive_memo_' + id) || ''; } catch (e) { return ''; } }
  // 북마크 커스텀 이름 (v1 archive-bookmark-names 호환)
  function getBookmarkName(hex) {
    var names = load('archive-bookmark-names', {});
    return (names && names[hex]) || colors[hex] || hex;
  }
  function persist(key, value) {
    try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); return true; }
    catch (e) { window.alert('저장 공간을 사용할 수 없습니다. 브라우저 저장 설정을 확인해 주세요.'); return false; }
  }
  function safeUrl(value) {
    if (!String(value || '').trim()) return '';
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
      if (tag === 'img') { var raw = node.getAttribute('src') || ''; var src = /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(raw) ? raw : safeUrl(raw); return src ? '<img src="' + esc(src) + '" alt="' + esc(node.getAttribute('alt') || '') + '" loading="lazy">' : ''; }
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
      var raw = Array.isArray(pair) ? pair[1] || pair[0] : typeof pair === 'string' ? pair : pair && (pair.full || pair.url || pair.src || pair.thumb);
      var url = safeUrl(raw);
      return url ? '<img src="' + esc(url) + '" alt="게시물 이미지" loading="lazy">' : '';
    }).join('');
  }
  function translatedBody(a){var tr=a.translation,original=bodyText(a),base=a.fullHtml?cleanHtml(a.fullHtml):'<p class="aa-text">'+esc(original)+'</p>';if(!tr||tr.status!=='ok'||!Array.isArray(tr.paragraphs)||!Array.isArray(tr.translated)||tr.paragraphs.length!==tr.translated.length)return base;var compact=function(t){return String(t).replace(/\s+/g,' ').trim();};if(compact(original)!==compact(tr.paragraphs.join(' ')))return base;if(!a.fullHtml)return tr.paragraphs.map(function(t,i){return '<section class="translation-pair"><p class="translation-original">'+esc(t)+'</p><p class="translation-ko"><span class="translation-label">한국어 번역</span><br>'+esc(tr.translated[i])+'</p></section>';}).join('');var doc=new DOMParser().parseFromString(base,'text/html');tr.paragraphs.forEach(function(t,i){var nodes=Array.from(doc.body.querySelectorAll('p,h1,h2,h3,li,blockquote'));var node=nodes.find(function(n){return compact(n.textContent)===compact(t);});if(node){var ko=doc.createElement('p');ko.className='translation-ko';ko.textContent=tr.translated[i];node.after(ko);} });return doc.body.innerHTML;}
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
        var rid = String(r.tweetId || r.id || r.sourceUrl || '');
        var deleted = load('archive-deleted-replies', []);
        return '<div class="reply" data-reply-id="' + esc(rid) + '"' + (deleted.indexOf(rid) >= 0 ? ' hidden' : '') + '><div class="reply-head"><span class="reply-author">' + esc(rname) +
          '</span><span class="reply-time">' + esc(r.createdAt || '') + '</span>' + badge + '</div>' +
          '<p class="reply-text">' + esc(r.fullText || '') + '</p>' + images(r.mediaImages || r.media) + (rid ? '<button type="button" data-hide-reply="' + esc(rid) + '">댓글 숨기기</button>' : '') + '</div>';
      }).join('');
      repliesHtml = '<details class="replies" open><summary>💬 댓글 ' + replies.length + '개</summary><div class="reply-list">' + items + '</div></details>';
    }
    // URL 인라인 내용
    var urlHtml = '';
    var urlContents = Array.isArray(a.urlContents) ? a.urlContents : [];
    if (urlContents.length) {
      var uitems = urlContents.map(function (uc) {
        var u = safeUrl(uc.url), t = String(uc.title || u).trim(), b = String(uc.content || '').trim();
        if (!b) return '';
        if (b.length > 1200) b = b.slice(0, 1200) + '…';
        return '<div class="url-inline-item"><div class="url-inline-title">📄 <a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(t) + '</a></div><div class="url-inline-content">' + esc(b) + '</div></div>';
      }).join('');
      if (uitems) urlHtml = '<div class="url-inline"><div class="url-inline-head">🔗 본문 링크 내용</div>' + uitems + '</div>';
    }
    return '<h1>' + esc(title(a)) + '</h1><p class="meta">' + esc(metadata(a)) + '</p><div class="aa-body">' +
      translatedBody(a) + '</div>' + images(a.mediaImages) +
      (q && typeof q === 'object' ? '<blockquote><p>' + esc(author(q)) + '</p><p class="aa-text">' + esc(q.fullText || '') + '</p>' + images(q.media || q.mediaImages) + '</blockquote>' : '') +
      repliesHtml + urlHtml +
      '<p>' + esc((a.topics || []).join(' · ')) + '</p>' + (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>' : '');
  }
  function button(text, group, value) { return '<button type="button" data-filter="' + group + '" data-value="' + esc(value) + '" aria-pressed="false">' + esc(text) + '</button>'; }
  function toggle(array, value) { var i = array.indexOf(value); if (i < 0) array.push(value); else array.splice(i, 1); }
  function status(message) { var el = controls.querySelector('[data-results-status]'); if (el) el.textContent = message; }
  function update() {
    var words = controls.querySelector('input').value.toLowerCase().split(',').map(function (s) { return s.trim(); }).filter(Boolean), count = 0, pinCount = 0;
    var defaultView=!words.length&&!selection.hidden&&!selection.type.length&&!selection.topic.length&&!selection.author.length&&!selection.bookmark.length;
    if(aiList)aiList.hidden=!defaultView;
    records.forEach(function (r) {
      var isHidden = hidden.indexOf(r.id) >= 0, color = bookmarks[r.id], isPinned = pins.indexOf(r.id) >= 0;
      var match = !words.length || (mode === 'AND' ? words.every(function (w) { return r.search.indexOf(w) >= 0; }) : words.some(function (w) { return r.search.indexOf(w) >= 0; }));
      var visible = match && (selection.hidden ? isHidden : !isHidden) && (!selection.type.length || selection.type.indexOf(r.type) >= 0) &&
        (!selection.topic.length || selection.topic.some(function (t) { return r.topics.indexOf(t) >= 0; })) && (!selection.author.length || selection.author.indexOf(r.author) >= 0) &&
        (!selection.subject.length || selection.subject[0]===r.article.subject) && (!selection.category.length || selection.category[0]===r.article.category) && (!selection.bookmark.length || (color && (selection.bookmark.indexOf('all') >= 0 || selection.bookmark.indexOf(color) >= 0)));
      if(!selection.type.length&&r.type==='ib')visible=false;
      if(defaultView&&r.topics.indexOf('AI툴')>=0&&!isPinned)visible=false;
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
    controls.querySelectorAll('[data-filter]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.value ? selection[b.dataset.filter].indexOf(b.dataset.value) >= 0 : !selection[b.dataset.filter].length)); });
    controls.querySelector('[data-action="hidden"]').setAttribute('aria-pressed', String(selection.hidden));
    var isIB=selection.type[0]==='ib';controls.querySelector('.aa-ib-nav').hidden=!isIB;controls.querySelectorAll('[data-ordinary-filter]').forEach(function(el){el.hidden=isIB;});
    // 작성자 선택 표시기 업데이트
    var ind = controls.querySelector('[data-author-indicator]');
    if (ind) ind.textContent = selection.author.length ? '(' + selection.author.length + '명 선택)' : '';
    status(count ? count + '개 글 표시' : '조건에 맞는 글이 없습니다.');
    if(aiList)aiList.querySelectorAll('[data-read-id]').forEach(function(b){b.closest('li').hidden=hidden.indexOf(b.dataset.readId)>=0;});
  }
  function dialog(id, label, html) {
    var d = document.createElement('dialog'); d.id = id; d.className = 'aa-dialog'; d.setAttribute('aria-label', label); d.innerHTML = html; document.body.appendChild(d);
    d.addEventListener('click', function (e) { if (e.target.closest('[data-close]')) close(d); });
    d.addEventListener('cancel', function (e) { e.preventDefault(); close(d); });
    d.addEventListener('close', function () { document.body.classList.toggle('aa-modal-open', !!document.querySelector('dialog[open]')); if (d._opener && d._opener.isConnected) d._opener.focus(); });
    return d;
  }
  function show(d) { opener = document.activeElement; d._opener = opener; if (typeof d.showModal === 'function') d.showModal(); else { d.setAttribute('open', ''); d.setAttribute('aria-modal', 'true'); } document.body.classList.add('aa-modal-open'); var f = d.querySelector('textarea,button'); if (f) f.focus(); }
  function close(d) { if (typeof d.close === 'function') d.close(); else { d.removeAttribute('open'); document.body.classList.remove('aa-modal-open'); if (opener) opener.focus(); } }
  var readerHistory=[];
  function openRead(r,back) {if(current&&readDialog.open&&!back&&current!==r)readerHistory.push(current);if(!readDialog.open)readerHistory=[];current=r;var related=(r.related||[]).filter(function(other){return hidden.indexOf(other.id)<0;});readDialog.querySelector('.aa-content').innerHTML=articleHtml(r.article)+(related.length?'<section class="reader-related"><h2>연관 게시글</h2>'+related.map(function(other){return '<button type="button" data-related-id="'+esc(other.id)+'">'+esc(title(other.article))+'<small> · '+esc(other.author)+'</small></button>';}).join('')+'</section>':'');readDialog.querySelector('[data-reader-title]').textContent=title(r.article);readDialog.querySelector('[data-reader-back]').hidden=!readerHistory.length;if(!readDialog.open)show(readDialog);readDialog.querySelector('.aa-content').scrollTop=0; }
  function resetFilters(){Object.keys(selection).forEach(function(k){selection[k]=k==='hidden'?false:[];});mode='OR';controls.querySelector('input').value='';controls.querySelector('[data-action="mode"]').textContent='OR';update();}

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
  function init(articles) {
    var previous=controls?{selection:selection,mode:mode,query:controls.querySelector('input').value,scroll:window.scrollY}:null;
    list = document.getElementById('articles'); if (!list) return;
    document.body.classList.remove('aa-modal-open');
    [controls, pinned, readDialog, memoDialog, bookmarkDialog, imageDialog, aiList].forEach(function (el) { if (el) el.remove(); });
    selection = { type: [], topic: [], author: [], bookmark: [], subject: [], category: [], hidden: false }; mode = 'OR'; current = null;
    pins = load('archive-pinned', []); hidden = load('archive-deleted-posts', []);
    pins = Array.isArray(pins) ? pins.map(String) : []; hidden = Array.isArray(hidden) ? hidden.map(String) : [];
    var saved = load('archive-bookmarks', {}); bookmarks = Object.create(null);
    // v1 호환: 저장된 hex 값을 그대로 보존 (사전 정의되지 않은 custom 색상도 유지)
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) Object.keys(saved).forEach(function (id) { if (typeof saved[id] === 'string' && saved[id]) bookmarks[id] = saved[id]; });
    var byIdx = Object.create(null); (articles || []).forEach(function (a, i) { byIdx[String(a._idx == null ? i : a._idx)] = window.ArchiveRender.normalizeArticle(a); });
    records = Array.prototype.map.call(list.querySelectorAll('.article'), function (card) {
      var a = byIdx[card.dataset.idx] || {}, topics = Array.isArray(a.topics) ? a.topics.map(String) : (card.dataset.topics || '').split('\n').filter(Boolean);
      return { card: card, id: card.dataset.pinId || card.dataset.idx, article: a, author: card.dataset.author || author(a), type: card.dataset.type || a.type, topics: topics,
        search: [title(a), bodyText(a), a.summary, author(a), a.handle, topics.join(' ')].join(' ').toLowerCase() };
    });
    if (!localStorage.getItem('archive-ib-pins-migrated')) {
      records.forEach(function (r) { if (r.type === 'ib' && localStorage.getItem(r.id) === '1' && pins.indexOf(r.id) < 0) pins.push(r.id); });
      if (persist('archive-pinned', pins)) persist('archive-ib-pins-migrated', '1');
    }
    controls = document.createElement('section'); controls.className = 'aa-controls'; controls.id = 'archive-controls';
    controls.innerHTML = ArchiveUI.controls(records, colors, getBookmarkName);
    ArchiveUI.overview(records); ArchiveUI.related(records);
    var ibRecords=records.filter(function(r){return r.type==='ib';});
    ['subject','category'].forEach(function(field,n){var values=Array.from(new Set(ibRecords.map(function(r){return r.article[field]||'';}).filter(Boolean)));controls.querySelectorAll('.aa-ib-nav .aa-chips')[n].innerHTML=button('전체',field,'')+values.map(function(v){return button(v,field,v);}).join('');});
    list.parentNode.insertBefore(controls, list); pinned = document.createElement('section'); pinned.className = 'aa-pinned'; pinned.innerHTML = '<h2>📌 고정한 글</h2><div class="aa-grid"></div>'; list.parentNode.insertBefore(pinned, list);
    aiList=document.createElement('section');aiList.className='ai-tools-section';var tools=records.filter(function(r){return r.topics.indexOf('AI툴')>=0;});aiList.innerHTML='<h2>🛠️ AI 도구 모음 ('+tools.length+'건)</h2><ul class="ai-tools-list">'+tools.map(function(r){var url=safeUrl(r.article.sourceUrl||r.article.source_url);return '<li><button type="button" data-read-id="'+esc(r.id)+'">'+esc(title(r.article))+'</button> <span class="ai-author">— '+esc(r.author)+'</span> '+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">원문 ↗</a>':'')+'</li>';}).join('')+'</ul>';list.after(aiList);
    controls.addEventListener('input', update); controls.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if (b.dataset.filter) {var field=b.dataset.filter;selection[field]=b.dataset.value&&selection[field][0]!==b.dataset.value?[b.dataset.value]:[];if(field==='type'){selection.topic=[];selection.author=[];selection.subject=[];selection.category=[];}}
      if(b.dataset.action==='reset') resetFilters();
      if (b.dataset.action === 'mode') { mode = mode === 'OR' ? 'AND' : 'OR'; b.textContent = mode; b.setAttribute('aria-label', '검색 조건 ' + mode + ', 클릭하여 변경'); }
      if (b.dataset.action === 'hidden') selection.hidden = !selection.hidden;
      if (b.dataset.action === 'drive') { DriveSync.connect(); return; }
      update();
    });
    var pressTimer;
    function renameBookmark(b){var hex=b.dataset.value;if(!colors[hex])return;var name=window.prompt('북마크 이름',getBookmarkName(hex));if(name===null)return;var names=load('archive-bookmark-names',{});names[hex]=name.trim()||colors[hex];if(persist('archive-bookmark-names',names)){b.textContent=names[hex];b.setAttribute('aria-label',names[hex]);}}
    controls.addEventListener('contextmenu',function(e){var b=e.target.closest('[data-filter="bookmark"]');if(b&&colors[b.dataset.value]){e.preventDefault();renameBookmark(b);}});
    controls.addEventListener('pointerdown',function(e){var b=e.target.closest('[data-filter="bookmark"]');if(b&&colors[b.dataset.value])pressTimer=setTimeout(function(){renameBookmark(b);},650);});
    ['pointerup','pointercancel','pointerleave'].forEach(function(event){controls.addEventListener(event,function(){clearTimeout(pressTimer);});});
    // Drive 동기화 초기화
    DriveSync.init(controls.querySelector('[data-drive-status]'));
    var backupButton = document.createElement('button'); backupButton.type = 'button'; backupButton.textContent = '저장 데이터 백업';
    backupButton.onclick = function () { download(new Blob([JSON.stringify(DriveSync._exportState(), null, 2)], {type:'application/json'}), 'archive-state.json'); };
    controls.querySelector('.archive-settings').appendChild(backupButton);
    var restoreReplies=document.createElement('button');restoreReplies.textContent='숨긴 댓글 모두 복원';restoreReplies.onclick=function(){persist('archive-deleted-replies',[]);document.querySelectorAll('[data-reply-id]').forEach(function(r){r.hidden=false;});status('숨긴 댓글을 복원했습니다.');};controls.querySelector('.archive-settings').appendChild(restoreReplies);
    var importLabel=document.createElement('label');importLabel.textContent=' 백업 복원 ';var importInput=document.createElement('input');importInput.type='file';importInput.accept='.json,application/json';importInput.setAttribute('aria-label','백업 파일 복원');importLabel.appendChild(importInput);controls.querySelector('.archive-settings').appendChild(importLabel);
    importInput.onchange=async function(){var file=importInput.files[0];if(!file)return;try{var state=JSON.parse(await file.text());if(!DriveSync._validateRemote(state))throw new Error('올바른 아카이브 백업이 아닙니다.');if(!window.confirm('현재 저장 내용을 백업한 후 선택한 파일로 복원할까요?'))return;localStorage.setItem('archive-before-import',JSON.stringify(DriveSync._exportState()));DriveSync.importState(state);refreshSavedState();status('백업 복원 완료');}catch(e){window.alert(e.message);}finally{importInput.value='';}};

    readDialog = dialog('archive-read-dialog', '글 읽기', '<header class="aa-reader-header"><button type="button" data-reader-back hidden>← 이전 글</button><strong data-reader-title></strong><button type="button" data-close>닫기 ×</button><div class="aa-toolbar"><button type="button" data-share="text">텍스트 복사</button><button type="button" data-share="image">이미지로 공유</button><button type="button" data-share="html">HTML 다운로드</button><button type="button" data-share="print">인쇄</button></div></header><article class="aa-content"></article>');
    var swipeY;readDialog.querySelector('header').addEventListener('touchstart',function(e){swipeY=e.touches[0].clientY;},{passive:true});readDialog.querySelector('header').addEventListener('touchend',function(e){if(e.changedTouches[0].clientY-swipeY>80)close(readDialog);},{passive:true});
    readDialog.addEventListener('click', function (e) {
      var memoButton=e.target.closest('[data-reader-memo]');if(memoButton&&current){memoId=current.article.memoId||current.id;memoDialog.querySelector('textarea').value=memoValue(memoId);show(memoDialog);return;}
      if(e.target.closest('[data-reader-back]')){var previous=readerHistory.pop();if(previous)openRead(previous,true);return;}
      if (e.target.tagName === 'IMG') { e.preventDefault(); ArchiveImages.open(e.target,readDialog.querySelector('.aa-content')); return; }
      var b = e.target.closest('[data-share]'); if (!b || !current) return; var a = current.article;
      if (b.dataset.share === 'print') window.print();
      if (b.dataset.share === 'text') { var text = title(a) + '\n\n' + bodyText(a) + '\n\n' + (safeUrl(a.sourceUrl || a.source_url) || ''); navigator.clipboard.writeText(text).then(function () { b.textContent = '복사 완료'; }).catch(function () { download(new Blob([text], {type:'text/plain;charset=utf-8'}), 'archive.txt'); }); }
      if (b.dataset.share === 'image') shareImage(a);
      if (b.dataset.share === 'html') download(new Blob(['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + esc(title(a)) + '</title><style>body{max-width:850px;margin:32px auto;padding:16px;font:16px/1.7 sans-serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}.aa-text{white-space:pre-wrap}table{display:block;overflow:auto}[hidden]{display:none!important}button{display:none}</style><body>' + articleHtml(a) + '</body></html>'], { type: 'text/html' }), 'archive.html');
    });
    var readerMemo=document.createElement('button');readerMemo.type='button';readerMemo.dataset.readerMemo='';readerMemo.textContent='메모';readDialog.querySelector('.aa-toolbar').prepend(readerMemo);
    memoDialog = dialog('archive-memo-dialog', '메모 편집', '<h2>메모</h2><label for="archive-memo-text">내용</label><textarea id="archive-memo-text"></textarea><div class="aa-chips"><button type="button" data-memo="save">저장</button><button type="button" data-memo="delete">삭제</button><button type="button" data-close>닫기</button></div>');
    memoDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-memo]'); if (!b) return; var value = b.dataset.memo === 'delete' ? null : memoDialog.querySelector('textarea').value; if (persist('archive_memo_' + memoId, value)) { close(memoDialog); update(); } });
    bookmarkDialog = dialog('archive-bookmark-dialog', '북마크 색상 선택', '<h2>북마크 색상</h2><div class="aa-chips">' + Object.keys(colors).map(function (c) { return '<button type="button" data-color="' + c + '" style="border-color:' + c + '">' + (colors[c] || c) + '</button>'; }).join('') + '<button type="button" data-color="">해제</button><button type="button" data-close>닫기</button></div>');
    bookmarkDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-color]'); if (!b || !bookmarkRecord) return; var next = Object.assign(Object.create(null), bookmarks); if (b.dataset.color) next[bookmarkRecord.id] = b.dataset.color; else delete next[bookmarkRecord.id]; if (persist('archive-bookmarks', next)) { bookmarks = next; close(bookmarkDialog); update(); } });
    if (!bound) {
      document.addEventListener('click', cardClick);
      document.addEventListener('keydown', function (e) { var d = [imageDialog, memoDialog, bookmarkDialog, readDialog].filter(function (item) { return item && item.hasAttribute('open'); })[0]; if (!d) return;
        if (e.key === 'Escape') { e.preventDefault(); close(d); }
        if (e.key === 'Tab' && typeof d.showModal !== 'function') { var els = d.querySelectorAll('button,textarea,a[href]'), first = els[0], last = els[els.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
      });
      var top=document.getElementById('scroll-top-btn');window.addEventListener('scroll',function(){top.hidden=window.scrollY<600;},{passive:true});top.onclick=function(){window.scrollTo({top:0,behavior:'smooth'});};document.getElementById('archive-home').onclick=function(){resetFilters();window.scrollTo({top:0,behavior:'smooth'});};
      bound = true;
    }
    if(previous){selection=previous.selection;mode=previous.mode;controls.querySelector('input').value=previous.query;controls.querySelector('[data-action="mode"]').textContent=mode;}
    update();if(previous)window.scrollTo(0,previous.scroll);
  }
  function cardClick(e) {
    var toolButton=e.target.closest('[data-read-id]');if(toolButton){var tool=records.find(function(r){return r.id===toolButton.dataset.readId;});if(tool)openRead(tool);return;}
    var relatedButton=e.target.closest('[data-related-id]');if(relatedButton){e.preventDefault();var next=records.find(function(r){return r.id===relatedButton.dataset.relatedId;});if(next){ArchiveImages.close();openRead(next);}return;}
    var replyButton = e.target.closest('.reply-del,[data-hide-reply]');
    if (replyButton) {
      e.preventDefault(); e.stopPropagation();
      var rid = replyButton.dataset.hideReply || replyButton.dataset.replyId;
      var removed = load('archive-deleted-replies', []);
      if (rid && removed.indexOf(rid) < 0 && persist('archive-deleted-replies', removed.concat(rid))) document.querySelectorAll('[data-reply-id]').forEach(function (r) { if (r.dataset.replyId === rid) r.hidden = true; });
      return;
    }
    var card = e.target.closest('.article'); if (!card) return; var r = records.filter(function (item) { return item.card === card; })[0]; if (!r) return;
    var b = e.target.closest('button');
    if (b) {
      e.preventDefault();
      if (b.hasAttribute('data-memo-id')) { memoId = b.dataset.memoId; memoDialog.querySelector('textarea').value = memoValue(memoId); show(memoDialog); }
      else if (b.classList.contains('bookmark-btn')) { bookmarkRecord = r; show(bookmarkDialog); }
      else if (b.classList.contains('pin-btn')) { var nextPins = pins.slice(); toggle(nextPins, r.id); if (persist('archive-pinned', nextPins)) { pins = nextPins; update(); } }
      else if (b.classList.contains('delete-btn')) { var nextHidden = hidden.slice(); toggle(nextHidden, r.id); if (persist('archive-deleted-posts', nextHidden)) { hidden = nextHidden; update(); } }
      else if (b.classList.contains('read-window-btn')) openRead(r);
      return;
    }
    var media=e.target.closest('.post-media a');if(media){e.preventDefault();openRead(r);var imgs=readDialog.querySelectorAll('.aa-content img');var n=Array.from(card.querySelectorAll('.post-media a')).indexOf(media);if(imgs[n])ArchiveImages.open(imgs[n],readDialog.querySelector('.aa-content'));return;}
    if (e.target.closest('a,input,textarea,select,label')) return;
    e.preventDefault(); openRead(r);
  }
  function refreshSavedState() {
    pins = load('archive-pinned', []); hidden = load('archive-deleted-posts', []);
    bookmarks = load('archive-bookmarks', {});
    if (!Array.isArray(pins)) pins = [];
    if (!Array.isArray(hidden)) hidden = [];
    if (!bookmarks || typeof bookmarks !== 'object' || Array.isArray(bookmarks)) bookmarks = {};
    if (controls) update();
  }
  return { init: init, refreshSavedState: refreshSavedState, sanitizeHtml: cleanHtml };
}());


window.ArchiveApp = ArchiveApp;
