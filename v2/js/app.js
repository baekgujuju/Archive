/* Archive v2 interactions. Classic script: keep implementation names private. */
var ArchiveApp = (function () {
  'use strict';
  // v1 호환 hex 스키마: archive-bookmarks에 hex 직접 저장 (덮어쓰기 금지)
  var colors = { '#e74c3c': '빨강', '#e67e22': '주황', '#f1c40f': '노랑', '#27ae60': '초록', '#3498db': '파랑', '#34495e': '남색', '#9b59b6': '보라' };
  var records = [], pins = [], hidden = [], bookmarks = Object.create(null), controls, list, pinned, readDialog, memoDialog, bookmarkDialog, renameDialog, renameHex, confirmDialog, confirmResolve, exportDialog;
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
    var allowed = /^(P|BR|DIV|SPAN|H1|H2|H3|H4|H5|H6|SMALL|STRONG|B|EM|I|U|S|UL|OL|LI|BLOCKQUOTE|PRE|CODE|TABLE|THEAD|TBODY|TR|TH|TD|HR|FIGURE|FIGCAPTION|A|IMG)$/;
    function walk(node) {
      if (node.nodeType === 3) return esc(node.nodeValue.replace(/&quot;/g,'\"').replace(/&#39;/g,"'"));
      if (node.nodeType !== 1 || /^(SCRIPT|STYLE|IFRAME|OBJECT|EMBED|SVG|MATH|FORM|INPUT|BUTTON|TEXTAREA|TEMPLATE)$/.test(node.tagName)) return '';
      var content = Array.prototype.map.call(node.childNodes, walk).join('');
      if (!allowed.test(node.tagName)) return content;
      var tag = node.tagName.toLowerCase(), attrs = ''; var cls=node.getAttribute('class');if(cls&&/^[a-z0-9 _-]+$/i.test(cls))attrs=' class="'+esc(cls)+'"';
      if (tag === 'a') { var href = safeUrl(node.getAttribute('href')); if (href) attrs += ' href="' + esc(href) + '" target="_self" rel="noopener noreferrer"'; }
      if (tag === 'img') { var raw = node.getAttribute('src') || ''; var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];if(mapped)raw=new URL(mapped,document.baseURI).href; var src = /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(raw) ? raw : safeUrl(raw); return src ? '<img tabindex="0" title="이미지 확대" src="' + esc(src) + '" alt="' + esc(node.getAttribute('alt') || '') + '" loading="lazy">' : ''; }
      return '<' + tag + attrs + '>' + content + (/^(br|hr)$/.test(tag) ? '' : '</' + tag + '>');
    }
    return Array.prototype.map.call(doc.body.childNodes, walk).join('');
  }
  function linkText(value) {
    var text=String(value||''),re=/https?:\/\/[^\s<>]+|\b(?:www\.)?[a-z0-9-]+\.(?:com|org|net|io)\/[^\s<>]+/gi,out='',last=0,m;
    while((m=re.exec(text))){var raw=m[0].replace(/[.,;!?)]*$/,'');out+=esc(text.slice(last,m.index));var url=safeUrl(/^https?:/i.test(raw)?raw:'https://'+raw);out+=url&&!/[…⋯]/.test(raw)?'<a href="'+esc(url)+'" target="_self">'+esc(raw)+'</a>':esc(raw);out+=esc(m[0].slice(raw.length));last=m.index+m[0].length;}return out+esc(text.slice(last));
  }
  function linkProse(container){var walker=document.createTreeWalker(container,NodeFilter.SHOW_TEXT),nodes=[],n;while((n=walker.nextNode()))if(!n.parentElement.closest('a,code,pre,script,style')&&/(https?:\/\/|\b[a-z0-9-]+\.(com|org|net|io)\/)/i.test(n.nodeValue))nodes.push(n);nodes.forEach(function(node){var span=document.createElement('span');span.innerHTML=linkText(node.nodeValue);node.replaceWith(span);});}
  function formatContent(container){container.querySelectorAll('img').forEach(function(img){var raw=img.getAttribute('src'),mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];if(mapped)img.src=new URL(mapped,document.baseURI).href;});linkProse(container);container.querySelectorAll('.stage,.stage-title').forEach(function(el){var num=el.querySelector('.stage-num');if(num&&!/\.\s*$/.test(num.textContent))num.textContent=num.textContent.trim()+'. ';});container.querySelectorAll('a[href]').forEach(function(a){a.target='_self';a.title=a.href;});}
  function canonicalSource(a){var u=safeUrl(a.sourceUrl||a.source_url);if(a.fullHtml&&u){var parsed=new URL(u);if(parsed.pathname==='/'||parsed.pathname===''){var doc=new DOMParser().parseFromString(a.fullHtml,'text/html');var link=Array.from(doc.querySelectorAll('a[href]')).find(function(el){try{var v=new URL(el.href);return v.hostname.replace(/^www\./,'')===parsed.hostname.replace(/^www\./,'')&&v.pathname.length>1;}catch(e){return false;}});if(link)u=safeUrl(link.href);}}return u;}
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
      var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];var url = safeUrl(mapped?new URL(mapped,document.baseURI).href:raw);
      return url ? '<img tabindex="0" title="이미지 확대" src="' + esc(url) + '" alt="게시물 이미지" loading="lazy">' : '';
    }).join('');
  }
  function translatedBody(a){var tr=a.translation,original=bodyText(a),base=a.fullHtml?cleanHtml(a.fullHtml):'<p class="aa-text">'+linkText(original)+'</p>';if(!tr||tr.status!=='ok'||!Array.isArray(tr.paragraphs)||!Array.isArray(tr.translated)||tr.paragraphs.length!==tr.translated.length)return base;var compact=function(t){return String(t).replace(/\s+/g,' ').trim();};if(compact(original)!==compact(tr.paragraphs.join(' ')))return base;if(!a.fullHtml)return tr.paragraphs.map(function(t,i){return '<section class="translation-pair"><p class="translation-original">'+esc(t)+'</p><p class="translation-ko"><span class="translation-label">한국어 번역</span><br>'+esc(tr.translated[i])+'</p></section>';}).join('');var doc=new DOMParser().parseFromString(base,'text/html');tr.paragraphs.forEach(function(t,i){var nodes=Array.from(doc.body.querySelectorAll('p,h1,h2,h3,li,blockquote'));var node=nodes.find(function(n){return compact(n.textContent)===compact(t);});if(node){var ko=doc.createElement('p');ko.className='translation-ko';ko.textContent=tr.translated[i];node.after(ko);} });return doc.body.innerHTML;}
  function renderReplies(a){
    // 답글 HTML (v1 패턴)
    var repliesHtml = '';
    var replies = Array.isArray(a.replies) ? a.replies.filter(function (r) { return r && typeof r === 'object' && load('archive-removed-replies',[]).indexOf(String(r.tweetId||r.id||r.sourceUrl||''))<0; }) : [];
    if (replies.length) {
      var items = replies.map(function (r) {
        var ra = r.author || {};
        var rname = ra.name || ra.handle || '';
        var badge = r.isThread ? ' <span class="thread-badge">스레드</span>' : '';
        var rid = String(r.tweetId || r.id || r.sourceUrl || '');
        var deleted = load('archive-deleted-replies', []);
        return '<div class="reply" data-reply-id="' + esc(rid) + '"' + (deleted.indexOf(rid) >= 0 ? ' hidden' : '') + '><div class="reply-head"><span class="reply-author">' + esc(rname) +
          '</span><span class="reply-time">' + esc(r.createdAt || '') + '</span>' + badge + '</div>' +
          '<p class="reply-text">' + linkText(r.fullText || '') + '</p>' + images(r.mediaImages || r.media) + (rid ? '<button type="button" data-hide-reply="' + esc(rid) + '">댓글 숨기기</button><button type="button" data-delete-reply="' + esc(rid) + '">댓글 삭제</button>' : '') + '</div>';
      }).join('');
      repliesHtml = '<details class="replies"><summary>💬 댓글 ' + replies.length + '개</summary><div class="reply-list">' + items + '</div></details>';
    }
    return repliesHtml;
  }
  function syncReplies(scope,a){var old=scope.querySelector('.replies'),open=old&&old.open,html=renderReplies(a);if(old){if(html)old.outerHTML=html;else old.remove();}else if(html)scope.insertAdjacentHTML('beforeend',html);var next=scope.querySelector('.replies');if(next)next.open=!!open;}
  function renderUrlContents(a){
    // URL 인라인 내용
    var urlHtml = '';
    var urlContents = Array.isArray(a.urlContents) ? a.urlContents : [];
    if (urlContents.length) {
      var uitems = urlContents.map(function (uc) {
        var u = safeUrl(uc.url), t = String(uc.title || u).trim(), b = String(uc.content || '').trim();
        if (!b) return ''; var local=records.find(function(r){return title(r.article)===t||title(r.article).indexOf(t.replace(/ \(.*\)$/,''))===0;});if(!u&&local)return '<div class="url-inline-item"><button type="button" class="content-link" data-read-id="'+esc(local.id)+'">↗ '+esc(t)+'</button><p>'+esc(b)+'</p></div>';if(!u)return '';
        if (b.length > 1200) b = b.slice(0, 1200) + '…';
        return '<div class="url-inline-item"><div class="url-inline-title">📄 <a href="' + esc(u) + '" target="_self" rel="noopener noreferrer">' + esc(t) + '</a></div><div class="url-inline-content">' + esc(b) + '</div></div>';
      }).join('');
      if (uitems) urlHtml = '<div class="url-inline"><div class="url-inline-head">🔗 본문 링크 내용</div>' + uitems + '</div>';
    }
    return urlHtml;
  }
  function articleHtml(a) {
    var url = canonicalSource(a), q = a.quoted;
    var repliesHtml=renderReplies(a);
    var urlHtml=renderUrlContents(a);
    return '<h1>' + esc(title(a)) + '</h1><p class="meta">' + esc(metadata(a)) + '</p><div class="aa-body">' +
      translatedBody(a) + '</div>' + images(a.mediaImages) +
      (q && typeof q === 'object' ? '<details class="quoted" open><summary>인용 게시글</summary><blockquote><p>' + esc(author(q)) + '</p><p class="aa-text">' + esc(q.fullText || '') + '</p>' + images(q.media || q.mediaImages) + '</blockquote></details>' : '') +
      repliesHtml + urlHtml +
      '<p>' + esc((a.topics || []).join(' · ')) + '</p>' + (url ? '<a href="' + esc(url) + '" target="_self" rel="noopener noreferrer">원문 보기 ↗</a>' : '');
  }
  function button(text, group, value) { return '<button type="button" data-filter="' + group + '" data-value="' + esc(value) + '" aria-pressed="false">' + esc(text) + '</button>'; }
  function toggle(array, value) { var i = array.indexOf(value); if (i < 0) array.push(value); else array.splice(i, 1); }
  function status(message) { var el = controls.querySelector('[data-results-status]'); if (el) el.textContent = message; }
  function update() {
    var modeButton=controls.querySelector('[data-action="mode"]');modeButton.textContent=mode;modeButton.setAttribute('aria-label','검색 조건 '+mode+', 클릭하여 변경');modeButton.setAttribute('aria-pressed',String(mode==='AND'));
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
      if (bb) { bb.style.color = color || '';var icon=bb.querySelector('svg');if(icon)icon.style.fill=color||'none'; bb.setAttribute('aria-label', color ? '북마크: ' + getBookmarkName(color) : '북마크'); }
      if (db) { db.title = isHidden ? '복원' : '삭제'; db.setAttribute('aria-label', db.title); }
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
    d.addEventListener('click', function (e) { if (e.target.closest('[data-close]')) close(d);else if(e.target===d){var rect=d.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)close(d);} });
    d.addEventListener('cancel', function (e) { e.preventDefault(); close(d); });
    d.addEventListener('close', function () { document.body.classList.toggle('aa-modal-open', !!document.querySelector('dialog[open]')); if (d._opener && d._opener.isConnected) d._opener.focus(); });
    return d;
  }
  function askConfirm(message){return new Promise(function(resolve){confirmResolve=resolve;confirmDialog.querySelector('p').textContent=message;show(confirmDialog);});}
  function show(d) { opener = document.activeElement; d._opener = opener; if (typeof d.showModal === 'function') d.showModal(); else { d.setAttribute('open', ''); d.setAttribute('aria-modal', 'true'); } document.body.classList.add('aa-modal-open'); var f = d.querySelector('textarea,input,button'); if (f) f.focus(); }
  function close(d) { if (typeof d.close === 'function') d.close(); else { d.removeAttribute('open'); document.body.classList.remove('aa-modal-open'); if (opener) opener.focus(); } }
  var readerHistory=[];
  function openRead(r,back) {if(current&&readDialog.open&&!back&&current!==r)readerHistory.push(current);if(!readDialog.open)readerHistory=[];current=r;readDialog.querySelector('[data-share=text]').textContent='텍스트 복사';var related=(r.related||[]).filter(function(other){return hidden.indexOf(other.id)<0;});readDialog.querySelector('.aa-content').innerHTML=articleHtml(r.article)+(related.length?'<section class="reader-related"><h2>연관 게시글</h2>'+related.map(function(other){return '<button type="button" data-related-id="'+esc(other.id)+'">'+'– '+esc(title(other.article))+'<small> · '+esc(other.author)+'</small></button>';}).join('')+'</section>':'');readDialog.querySelector('[data-reader-title]').textContent=title(r.article);readDialog.querySelector('[data-reader-back]').hidden=!readerHistory.length;if(!readDialog.open)show(readDialog);formatContent(readDialog.querySelector('.aa-content'));readDialog.querySelector('.aa-content').scrollTop=0; }
  function resetFilters(){Object.keys(selection).forEach(function(k){selection[k]=k==='hidden'?false:[];});mode='OR';controls.querySelector('input').value='';controls.querySelector('[data-action="mode"]').textContent='OR';update();}

  function download(blob, name) { var url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(function () { URL.revokeObjectURL(url); }, 60000); }
  function shareFile(blob, name, heading) {
    var file;
    try { file = new File([blob], name, { type: blob.type }); } catch (e) { download(blob, name); return; }
    if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
      navigator.share({ files: [file] }).catch(function (e) { if (e.name !== 'AbortError') download(blob, name); });
    } else download(blob, name);
  }
  async function shareImage(a) {
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
      if (index >= pages.length) { finishImageShare(a,blobs); return; }
      var rows = pages[index++]; canvas.width = width; canvas.height = 96 + rows.reduce(function (sum, row) { return sum + row.height; }, 0); ctx.fillStyle = '#f6f2e9'; ctx.fillRect(0, 0, canvas.width, canvas.height); var y = 48;
      rows.forEach(function (row) { ctx.font = row.font; ctx.fillStyle = row.color; ctx.textBaseline = 'top'; ctx.fillText(row.text, 48, y); y += row.height; });
      canvas.toBlob(function (blob) { if (!blob) { window.alert('PNG 생성에 실패했습니다.'); return; } blobs.push(blob); next(); }, 'image/png');
    }
    next();
  }
  async function finishImageShare(a,blobs){
    var files=blobs.map(function(b,i){return new File([b],'archive-'+(i+1)+'.png',{type:'image/png'});});
    var doc=new DOMParser().parseFromString(articleHtml(a),'text/html'),urls=Array.from(new Set(Array.from(doc.querySelectorAll('img')).map(function(i){return i.src;}))),failed=[];
    await Promise.all(urls.map(async function(url,i){try{var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[url];var response=await fetch(mapped?new URL(mapped,document.baseURI).href:url);if(!response.ok)throw new Error('image');var blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('image');files.push(new File([blob],'attachment-'+(i+1)+'.'+(blob.type.split('/')[1]||'png'),{type:blob.type}));}catch(e){failed.push(url);}}));
    if(failed.length){window.alert('첨부 이미지 '+failed.length+'개를 가져오지 못했습니다. 이미지가 빠진 채로 공유하지 않습니다. 원문 또는 HTML 다운로드를 이용해 주세요.');return;}
    if(navigator.share&&navigator.canShare&&navigator.canShare({files:files})){try{await navigator.share({files:files});}catch(e){if(e.name!=='AbortError')files.forEach(function(f){download(f,f.name);});}}else files.forEach(function(f){download(f,f.name);});
  }
  async function shareHtml(a,downloadOnly){
    var container=document.createElement('article');container.innerHTML=articleHtml(a);formatContent(container);
    try{await Promise.all(Array.from(container.querySelectorAll('img')).map(async function(img){var response=await fetch(img.src);if(!response.ok)throw new Error('image');var blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('image');img.src=await new Promise(function(resolve,reject){var reader=new FileReader();reader.onload=function(){resolve(reader.result);};reader.onerror=reject;reader.readAsDataURL(blob);});}));}catch(e){window.alert('첨부 이미지를 포함한 HTML을 만들지 못했습니다. 이미지가 빠진 파일은 공유하지 않습니다.');return;}
    var css='body{max-width:850px;margin:24px auto;padding:16px;font:15px/1.75 system-ui,sans-serif;background:#fffdf8;color:#222b3b;overflow-wrap:anywhere}h1{font-size:24px;line-height:1.4;margin:0 0 18px}h2{font-size:20px;margin:28px 0 14px}h3{font-size:17px;margin:24px 0 12px}p{margin:0 0 16px}img{display:block;max-width:100%;height:auto;margin:16px auto}.aa-text,.translation-original,.translation-ko{white-space:pre-wrap}.stage-card{margin:28px 0;padding-top:18px;border-top:1px solid #ddd6ca}.stage{display:flex;gap:6px;flex-wrap:wrap}.phase-tag{display:block;flex-basis:100%;font-size:12px;font-weight:400}.flow-step{display:flex;gap:12px;padding:10px 0}.flow-dot{flex:none;min-width:24px}.flow-body b,.flow-body small{display:block}.flow-body small{font-size:12px}.org-label{display:block;font-size:11px;color:#716e65}.original,.translation-ko{padding:12px 16px;margin:16px 0;background:#eee9df}a{color:#675a47;text-decoration:underline}table{display:block;overflow:auto;border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd6ca}[hidden],button{display:none!important}@page{margin:15mm}@media print{body,.original,.translation-ko{background:white!important}h1{font-size:18pt}h2{font-size:14pt}h3{font-size:12pt}}';
    var htmlBlob=new Blob(['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title(a))+'</title><style>'+css+'</style></head><body>'+container.innerHTML+'</body></html>'],{type:'text/html'});if(downloadOnly)download(htmlBlob,'archive.html');else shareFile(htmlBlob,'archive.html',title(a));
  }
  function init(articles) {
    var previous=controls?{selection:selection,mode:mode,query:controls.querySelector('input').value,scroll:window.scrollY}:null;
    list = document.getElementById('articles'); if (!list) return;
    document.body.classList.remove('aa-modal-open');
    [controls, pinned, readDialog, memoDialog, bookmarkDialog, renameDialog, confirmDialog, exportDialog, imageDialog, aiList].forEach(function (el) { if (el) el.remove(); });
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
    var lead=records.find(function(r){return r.type!=='ib'&&r.topics.indexOf('AI툴')<0;});if(lead){lead.card.classList.add('featured');var pick=lead.card.querySelector('.spotlight-label');if(pick)pick.hidden=false;}
    records.forEach(function(r){if(!r.related.length)return;var section=document.createElement('section');section.className='related-section';section.innerHTML='<h4>연관 게시글</h4>'+r.related.map(function(other){return '<button type="button" data-related-id="'+esc(other.id)+'">'+'– '+esc(title(other.article))+'</button>';}).join('');r.card.appendChild(section);});
    var ibRecords=records.filter(function(r){return r.type==='ib';});
    ['subject','category'].forEach(function(field,n){var values=Array.from(new Set(ibRecords.map(function(r){return r.article[field]||'';}).filter(Boolean)));controls.querySelectorAll('.aa-ib-nav .aa-chips')[n].innerHTML=button('전체',field,'')+values.map(function(v){return button(v,field,v);}).join('');});
    list.parentNode.insertBefore(controls, list); pinned = document.createElement('section'); pinned.className = 'aa-pinned'; pinned.innerHTML = '<h2>📌 고정한 글</h2><div class="aa-grid"></div>'; list.parentNode.insertBefore(pinned, list);
    aiList=document.createElement('section');aiList.className='ai-tools-section';var tools=records.filter(function(r){return r.topics.indexOf('AI툴')>=0;});aiList.innerHTML='<h2>🛠️ AI 도구 모음 ('+tools.length+'건)</h2><ul class="ai-tools-list">'+tools.map(function(r){var url=safeUrl(r.article.sourceUrl||r.article.source_url);return '<li><button type="button" data-read-id="'+esc(r.id)+'">'+esc(title(r.article))+'</button> <span class="ai-author">— '+esc(r.author)+'</span> '+(url?'<a href="'+esc(url)+'" target="_self" rel="noopener noreferrer">원문 ↗</a>':'')+'</li>';}).join('')+'</ul>';list.after(aiList);
    controls.addEventListener('input', update); controls.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if(b.dataset.filter==='bookmark'){if(suppressBookmarkClick){suppressBookmarkClick=false;return;}clearTimeout(bookmarkClickTimer);if(e.detail<2)bookmarkClickTimer=setTimeout(function(){selection.bookmark=b.dataset.value&&selection.bookmark[0]!==b.dataset.value?[b.dataset.value]:[];update();},250);return;}if (b.dataset.filter) {var field=b.dataset.filter;selection[field]=b.dataset.value&&selection[field][0]!==b.dataset.value?[b.dataset.value]:[];if(field==='type'){selection.topic=[];selection.author=[];selection.subject=[];selection.category=[];}}
      if(b.dataset.action==='reset') resetFilters();
      if (b.dataset.action === 'mode') { mode = mode === 'OR' ? 'AND' : 'OR'; b.textContent = mode; b.setAttribute('aria-label', '검색 조건 ' + mode + ', 클릭하여 변경'); }
      if (b.dataset.action === 'hidden') selection.hidden = !selection.hidden;
      if (b.dataset.action === 'drive-export') { if(!DriveSync.isConnected()){window.alert('먼저 Drive를 연결해 주세요.');return;} show(exportDialog); return; }
      if (b.dataset.action === 'drive') { DriveSync.connect(); return; }
      update();
    });
    var pressTimer,pressOrigin,bookmarkClickTimer,suppressBookmarkClick=false;
    function renameBookmark(b){clearTimeout(bookmarkClickTimer);renameHex=b.dataset.value;if(!colors[renameHex])return;renameDialog.querySelector('input').value=getBookmarkName(renameHex);show(renameDialog);}
    controls.addEventListener('pointermove',function(e){if(pressOrigin&&Math.hypot(e.clientX-pressOrigin.x,e.clientY-pressOrigin.y)>12)clearTimeout(pressTimer);});
    controls.addEventListener('dblclick',function(e){var b=e.target.closest('[data-filter=bookmark]');if(b&&colors[b.dataset.value]){e.preventDefault();renameBookmark(b);}});
    controls.addEventListener('contextmenu',function(e){var b=e.target.closest('[data-filter="bookmark"]');if(b&&colors[b.dataset.value]){e.preventDefault();renameBookmark(b);}});
    controls.addEventListener('pointerdown',function(e){var b=e.target.closest('[data-filter="bookmark"]');if(b&&colors[b.dataset.value]){pressOrigin={x:e.clientX,y:e.clientY};pressTimer=setTimeout(function(){suppressBookmarkClick=true;document.addEventListener('pointerup',function(){setTimeout(function(){suppressBookmarkClick=false;},0);},{once:true,capture:true});renameBookmark(b);},550);}});
    ['pointerup','pointercancel'].forEach(function(event){controls.addEventListener(event,function(){clearTimeout(pressTimer);});});
    // Drive 동기화 초기화
    DriveSync.init(controls.querySelector('[data-drive-status]'));
    var backupButton = document.createElement('button'); backupButton.type = 'button'; backupButton.textContent = '저장 데이터 백업';
    backupButton.onclick = function () { download(new Blob([JSON.stringify(DriveSync._exportState(), null, 2)], {type:'application/json'}), 'archive-state.json'); };
    controls.querySelector('.archive-settings').appendChild(backupButton);
    var restoreReplies=document.createElement('button');restoreReplies.textContent='숨긴 댓글 모두 복원';restoreReplies.onclick=function(){persist('archive-deleted-replies',[]);document.querySelectorAll('[data-reply-id]').forEach(function(r){r.hidden=false;});status('숨긴 댓글을 복원했습니다.');};controls.querySelector('.archive-settings').appendChild(restoreReplies);
    var importLabel=document.createElement('label');importLabel.textContent=' 백업 복원 ';var importInput=document.createElement('input');importInput.type='file';importInput.accept='.json,application/json';importInput.setAttribute('aria-label','백업 파일 복원');importLabel.appendChild(importInput);controls.querySelector('.archive-settings').appendChild(importLabel);
    importInput.onchange=async function(){var file=importInput.files[0];if(!file)return;try{var state=JSON.parse(await file.text());if(!DriveSync._validateRemote(state))throw new Error('올바른 아카이브 백업이 아닙니다.');if(!await askConfirm('현재 저장 내용을 백업한 후 선택한 파일로 복원할까요?'))return;localStorage.setItem('archive-before-import',JSON.stringify(DriveSync._exportState()));DriveSync.importState(state);refreshSavedState();status('백업 복원 완료');}catch(e){window.alert(e.message);}finally{importInput.value='';}};

    readDialog = dialog('archive-read-dialog', '글 읽기', '<header class="aa-reader-header"><button type="button" data-reader-back hidden>← 이전 글</button><strong data-reader-title></strong><button type="button" data-close>닫기 ×</button><div class="aa-toolbar"><button type="button" data-share="text">텍스트 복사</button><button type="button" data-share="image">이미지로 공유</button><details class="html-actions"><summary>HTML ▾</summary><div><button type="button" data-share="html">HTML 공유</button><button type="button" data-share="html-download">HTML 다운로드</button></div></details><button type="button" data-share="print">인쇄</button></div></header><article class="aa-content"></article>');
    readDialog.addEventListener('keydown',function(e){if(e.target.tagName==='IMG'&&(e.key==='Enter'||e.key===' ')){e.preventDefault();ArchiveImages.open(e.target,readDialog.querySelector('.aa-content'));}});
    var swipeY;readDialog.querySelector('header').addEventListener('touchstart',function(e){swipeY=e.touches[0].clientY;},{passive:true});readDialog.querySelector('header').addEventListener('touchend',function(e){if(e.changedTouches[0].clientY-swipeY>80)close(readDialog);},{passive:true});
    readDialog.addEventListener('click', function (e) {
      var memoButton=e.target.closest('[data-reader-memo]');if(memoButton&&current){memoId=current.article.memoId||current.id;memoDialog.querySelector('textarea').value=memoValue(memoId);show(memoDialog);return;}
      if(e.target.closest('[data-reader-back]')){var previous=readerHistory.pop();if(previous)openRead(previous,true);return;}
      if (e.target.tagName === 'IMG') { e.preventDefault(); ArchiveImages.open(e.target,readDialog.querySelector('.aa-content')); return; }
      var b = e.target.closest('[data-share]'); if (!b || !current) return; var a = current.article;var menu=b.closest('.html-actions');if(menu)menu.open=false;
      if (b.dataset.share === 'print') window.print();
      if (b.dataset.share === 'text') { var text = title(a) + '\n\n' + bodyText(a) + '\n\n' + (canonicalSource(a) || ''); navigator.clipboard.writeText(text).then(function () { b.textContent = '복사 완료'; }).catch(function () { download(new Blob([text], {type:'text/plain;charset=utf-8'}), 'archive.txt'); }); }
      if (b.dataset.share === 'image') shareImage(a);
      if (b.dataset.share === 'html'||b.dataset.share==='html-download') shareHtml(a,b.dataset.share==='html-download');
    });
    var readerMemo=document.createElement('button');readerMemo.type='button';readerMemo.dataset.readerMemo='';readerMemo.textContent='메모';readDialog.querySelector('.aa-toolbar').prepend(readerMemo);
    memoDialog = dialog('archive-memo-dialog', '메모 편집', '<h2>메모</h2><label for="archive-memo-text">내용</label><textarea id="archive-memo-text"></textarea><div class="aa-chips"><button type="button" data-memo="save">저장</button><button type="button" data-memo="delete">삭제</button><button type="button" data-close>닫기</button></div>');
    memoDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-memo]'); if (!b) return; var value = b.dataset.memo === 'delete' ? null : memoDialog.querySelector('textarea').value; if (persist('archive_memo_' + memoId, value)) { close(memoDialog); update(); } });
    bookmarkDialog = dialog('archive-bookmark-dialog', '북마크 색상 선택', '<h2>북마크 색상</h2><div class="aa-chips">' + Object.keys(colors).map(function (c) { return '<button type="button" data-color="' + c + '" style="border-color:' + c + '">' + (getBookmarkName(c) || c) + '</button>'; }).join('') + '<button type="button" data-color="">해제</button><button type="button" data-close>닫기</button></div>');
    bookmarkDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-color]'); if (!b || !bookmarkRecord) return; var next = Object.assign(Object.create(null), bookmarks); if (b.dataset.color) next[bookmarkRecord.id] = b.dataset.color; else delete next[bookmarkRecord.id]; if (persist('archive-bookmarks', next)) { bookmarks = next; close(bookmarkDialog); update(); } });
    renameDialog=dialog('archive-rename-dialog','북마크 이름 변경','<h2>북마크 이름</h2><label for="archive-color-name">이름</label><input id="archive-color-name" maxlength="30"><div class="aa-chips"><button type="button" data-save-name>저장</button><button type="button" data-close>취소</button></div>');
    function saveName(){var name=renameDialog.querySelector('input').value.trim()||colors[renameHex],names=load('archive-bookmark-names',{});names[renameHex]=name;if(persist('archive-bookmark-names',names)){controls.querySelectorAll('[data-filter=bookmark]').forEach(function(b){if(b.dataset.value===renameHex){b.textContent=name;b.setAttribute('aria-label',name);}});close(renameDialog);}}
    renameDialog.addEventListener('click',function(e){if(e.target.closest('[data-save-name]'))saveName();});renameDialog.querySelector('input').addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();saveName();}});
    exportDialog=dialog('archive-export-dialog','Drive 내보내기 확인','<h2>Drive로 내보내기</h2><p>이 기기의 메모·북마크·삭제 상태를 Drive에 반영합니다. 내보낼 때마다 비밀번호가 필요합니다.</p><label for="archive-export-password">비밀번호</label><input type="password" id="archive-export-password" autocomplete="off" maxlength="128"><p data-export-error role="status"></p><div class="aa-chips"><button type="button" data-export-submit>확인 · 내보내기</button><button type="button" data-close>취소</button></div>');
    var exportPending=false;
    async function submitExport(){if(exportPending)return;exportPending=true;var input=exportDialog.querySelector('input'),button=exportDialog.querySelector('[data-export-submit]'),password=input.value;input.value='';button.disabled=true;exportDialog.querySelector('[data-export-error]').textContent='';try{await DriveSync.exportWithPassword(password);close(exportDialog);}catch(e){exportDialog.querySelector('[data-export-error]').textContent=e.message;input.focus();}finally{password='';exportPending=false;button.disabled=false;}}
    exportDialog.addEventListener('click',function(e){if(e.target.closest('[data-export-submit]'))submitExport();});
    exportDialog.querySelector('input').addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();submitExport();}});
    exportDialog.addEventListener('close',function(){exportDialog.querySelector('input').value='';exportDialog.querySelector('[data-export-error]').textContent='';});
    confirmDialog=dialog('archive-confirm-dialog','작업 확인','<h2>확인</h2><p></p><div class="aa-chips"><button type="button" data-confirm-no>아니오</button><button type="button" data-confirm-yes>예</button></div>');
    confirmDialog.addEventListener('click',function(e){if(e.target.closest('[data-confirm-no],[data-confirm-yes]')){var resolve=confirmResolve;confirmResolve=null;var yes=!!e.target.closest('[data-confirm-yes]');close(confirmDialog);if(resolve)resolve(yes);}});confirmDialog.addEventListener('close',function(){if(confirmResolve){var resolve=confirmResolve;confirmResolve=null;resolve(false);}});
    if (!bound) {
      document.addEventListener('pointerdown',function(){document.body.classList.remove('using-keyboard');},true);document.addEventListener('keydown',function(e){if(e.key==='Tab')document.body.classList.add('using-keyboard');},true);document.addEventListener('click',function(e){document.querySelectorAll('.html-actions[open]').forEach(function(el){if(!el.contains(e.target))el.open=false;});});document.addEventListener('click', cardClick);
      document.addEventListener('keydown', function (e) { var d = [exportDialog,confirmDialog,renameDialog,imageDialog, memoDialog, bookmarkDialog, readDialog].filter(function (item) { return item && item.hasAttribute('open'); })[0]; if (!d) return;
        if (e.key === 'Escape') { e.preventDefault(); close(d); }
        if (e.key === 'Tab' && typeof d.showModal !== 'function') { var els = d.querySelectorAll('button,textarea,a[href]'), first = els[0], last = els[els.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
      });
      var top=document.getElementById('scroll-top-btn'),manage=document.getElementById('archive-manage-btn');manage.onclick=function(){var settings=controls.querySelector('.archive-settings');settings.open=true;settings.scrollIntoView({behavior:'smooth',block:'center'});};window.addEventListener('scroll',function(){top.hidden=window.scrollY<600;manage.hidden=top.hidden;},{passive:true});top.onclick=function(){resetFilters();window.scrollTo({top:0,behavior:'smooth'});};document.getElementById('archive-home').onclick=function(){resetFilters();window.scrollTo({top:0,behavior:'smooth'});};
      bound = true;
    }
    records.forEach(function(r){syncReplies(r.card,r.article);var oldLinks=r.card.querySelector('.url-inline'),newLinks=renderUrlContents(r.article);if(oldLinks){if(newLinks)oldLinks.outerHTML=newLinks;else oldLinks.remove();}else if(newLinks)r.card.insertAdjacentHTML('beforeend',newLinks);r.card.querySelectorAll('.post-media img').forEach(function(img,n){var pair=(r.article.mediaImages||[])[n],raw=Array.isArray(pair)?pair[1]||pair[0]:pair&&(pair.full||pair.url||pair.src||pair.thumb);var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];if(raw)img.dataset.full=safeUrl(mapped?new URL(mapped,document.baseURI).href:raw);});var full=r.card.querySelector('.full-text');if(full)full.innerHTML=translatedBody(r.article);formatContent(r.card);var a=r.card.querySelector('.read-link');if(a){var url=canonicalSource(r.article);if(url){a.href=url;a.title=url;a.hidden=false;}else{a.removeAttribute('href');a.hidden=true;}}});
    if(previous){selection=previous.selection;mode=previous.mode;controls.querySelector('input').value=previous.query;controls.querySelector('[data-action="mode"]').textContent=mode;}
    update();if(previous)window.scrollTo(0,previous.scroll);
  }
  async function cardClick(e) {
    var toolButton=e.target.closest('[data-read-id]');if(toolButton){var tool=records.find(function(r){return r.id===toolButton.dataset.readId;});if(tool)openRead(tool);return;}
    var relatedButton=e.target.closest('[data-related-id]');if(relatedButton){e.preventDefault();var next=records.find(function(r){return r.id===relatedButton.dataset.relatedId;});if(next){ArchiveImages.close();openRead(next);}return;}
    var deleteReply=e.target.closest('[data-delete-reply]');if(deleteReply){e.preventDefault();if(!await askConfirm('이 아카이브에서 댓글을 삭제하시겠습니까? 원본 서비스의 댓글은 삭제되지 않습니다.'))return;var rid=deleteReply.dataset.deleteReply,removed=load('archive-removed-replies',[]);if(removed.indexOf(rid)<0&&persist('archive-removed-replies',removed.concat(rid)))refreshSavedState();return;}
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
      else if (b.classList.contains('bookmark-btn')) { bookmarkRecord = r;bookmarkDialog.querySelectorAll('[data-color]').forEach(function(el){if(el.dataset.color)el.textContent=getBookmarkName(el.dataset.color);}); show(bookmarkDialog); }
      else if (b.classList.contains('pin-btn')) { var nextPins = pins.slice(); toggle(nextPins, r.id); if (persist('archive-pinned', nextPins)) { pins = nextPins; update(); } }
      else if (b.classList.contains('delete-btn')) {if(hidden.indexOf(r.id)<0&&!await askConfirm('게시글을 삭제하시겠습니까? 삭제한 글은 관리 메뉴에서 복원할 수 있습니다.'))return; var nextHidden = hidden.slice(); toggle(nextHidden, r.id); if (persist('archive-deleted-posts', nextHidden)) { hidden = nextHidden; update(); } }
      else if (b.classList.contains('read-window-btn')) openRead(r);
      return;
    }
    if(e.target.tagName==='IMG'){e.preventDefault();var source=e.target.dataset.full||e.target.src;openRead(r);var inlineImage=Array.from(readDialog.querySelectorAll('.aa-content img')).find(function(img){return img.src===source;});if(inlineImage)ArchiveImages.open(inlineImage,readDialog.querySelector('.aa-content'));return;}
    if(e.target.closest('a')&&!e.target.closest('.post-media'))return;
    var media=e.target.closest('.post-media a');if(media){e.preventDefault();openRead(r);var imgs=readDialog.querySelectorAll('.aa-content img');var n=Array.from(card.querySelectorAll('.post-media a')).indexOf(media);if(imgs[n])ArchiveImages.open(imgs[n],readDialog.querySelector('.aa-content'));return;}
    if (e.target.closest('a,input,textarea,select,label')) return;
    // Preserve v1 desktop inline expansion; mobile continues to open the full reader.
    if(window.matchMedia('(min-width:768px)').matches&&e.target.closest('.card-toggle'))return;
    
    e.preventDefault(); openRead(r);
  }
  function refreshSavedState() {
    records.forEach(function(r){syncReplies(r.card,r.article);});if(current&&readDialog&&readDialog.open){syncReplies(readDialog.querySelector('.aa-content'),current.article);var panel=document.querySelector('.viewer-text-panel');if(panel)syncReplies(panel,current.article);}

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




