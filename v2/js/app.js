if(typeof ArchiveTopics==='undefined'){
/* Owner topic overrides are preferences; source articles are never rewritten. */
(function(root,factory){var api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else{root.ArchiveTopics=api;if(typeof globalThis==='object')globalThis.ArchiveTopics=api;}}(typeof window==='object'?window:this,function(){
  'use strict';
  var KEY='archive-topic-overrides', forbidden=['__proto__','constructor','prototype'];
  function list(value){
    if(!Array.isArray(value)||value.length>20)throw Error('주제는 최대 20개까지 지정할 수 있습니다.');
    var result=[];
    value.forEach(function(s){
      if(typeof s!=='string')throw Error('주제 형식이 올바르지 않습니다.');
      s=s.normalize('NFC').replace(/\s+/g,' ').trim();
      if(!s||s.length>50||/[\u0000-\u001f\u007f]/.test(s))throw Error('주제는 1~50자로 입력해 주세요.');
      if(result.indexOf(s)<0)result.push(s);
    });
    return result.sort(function(a,b){return a<b?-1:a>b?1:0;});
  }
  function validMap(value){
    return value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length<=20000&&Object.keys(value).every(function(id){
      if(!id||id.length>1000||forbidden.indexOf(id)>=0||typeof value[id]!=='string'||value[id].length>8000)return false;
      try{list(JSON.parse(value[id]));return true;}catch(e){return false;}
    });
  }
  function read(){try{var d=JSON.parse(localStorage.getItem(KEY)||'{}');return validMap(d)?d:{};}catch(e){return {};}}
  function effective(id,original,map){map=map||read();if(Object.prototype.hasOwnProperty.call(map,id)){try{return list(JSON.parse(map[id]));}catch(e){}}
    return Array.isArray(original)?original.slice():[];
  }
  function unclassified(topics){return !topics.length||topics.every(function(t){return /^(기타|미분류)$/.test(String(t).trim());});}
  function changed(id,topics,restore){
    if(!id||id.length>1000||forbidden.indexOf(id)>=0)throw Error('자료 식별자를 확인할 수 없습니다.');
    var map=Object.assign(Object.create(null),read());
    if(restore)delete map[id];else map[id]=JSON.stringify(list(topics));
    if(!validMap(map))throw Error('주제 설정이 너무 많습니다.');
    return map;
  }
  return {key:KEY,list:list,validMap:validMap,read:read,effective:effective,unclassified:unclassified,changed:changed};
}));

}
/* Archive v2 interactions. Classic script: keep implementation names private. */
var ArchiveApp = (function () {
  'use strict';
  // v1 호환 hex 스키마: archive-bookmarks에 hex 직접 저장 (덮어쓰기 금지)
  var colors = { '#e74c3c': '빨강', '#e67e22': '주황', '#f1c40f': '노랑', '#27ae60': '초록', '#3498db': '파랑', '#34495e': '남색', '#9b59b6': '보라' };
  var records = [], pins = [], hidden = [], bookmarks = Object.create(null), controls, list, pinned, readDialog, memoDialog, bookmarkDialog, renameDialog, renameHex, confirmDialog, confirmResolve, exportDialog, exportDoneDialog;
  var topicDialog, topicRecord;
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
      if (/^(td|th)$/.test(tag)) ['colspan','rowspan'].forEach(function(k){var v=node.getAttribute(k)||'';if(/^[1-9][0-9]{0,2}$/.test(v))attrs+=' '+k+'="'+v+'"';});
      if (tag === 'ol') {var start=node.getAttribute('start')||'';if(/^-?[0-9]{1,6}$/.test(start))attrs+=' start="'+start+'"';}
      if (tag === 'li') {var value=node.getAttribute('value')||'';if(/^-?[0-9]{1,6}$/.test(value))attrs+=' value="'+value+'"';}
      if (tag === 'a') { var href = safeUrl(node.getAttribute('href')); if (href) attrs += ' href="' + esc(href) + '" target="_self" rel="noopener noreferrer"'; }
      if (tag === 'img') { var raw = node.getAttribute('src') || ''; var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];if(mapped)raw=new URL(mapped,document.baseURI).href; var src = /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(raw) ? raw : safeUrl(raw); return src ? '<img tabindex="0" title="이미지 확대" src="' + esc(src) + '" alt="' + esc(node.getAttribute('alt') || '') + '"' + ['width','height'].map(function(k){var v=node.getAttribute(k)||'';return /^[1-9][0-9]{0,4}$/.test(v)?' '+k+'="'+v+'"':'';}).join('') + ' loading="lazy">' : ''; }
      return '<' + tag + attrs + '>' + content + (/^(br|hr)$/.test(tag) ? '' : '</' + tag + '>');
    }
    return Array.prototype.map.call(doc.body.childNodes, walk).join('');
  }
  function linkText(value) {
    var text=String(value||''),re=/https?:\/\/[^\s<>]+|\b(?:www\.)?[a-z0-9-]+\.(?:com|org|net|io)\/[^\s<>]+/gi,out='',last=0,m;
    while((m=re.exec(text))){var raw=m[0].replace(/[.,;!?)]*$/,'');out+=esc(text.slice(last,m.index));var url=safeUrl(/^https?:/i.test(raw)?raw:'https://'+raw);out+=url&&!/[…⋯]/.test(raw)?'<a href="'+esc(url)+'" target="_self">'+esc(raw)+'</a>':esc(raw);out+=esc(m[0].slice(raw.length));last=m.index+m[0].length;}return out+esc(text.slice(last));
  }
  function linkProse(container){var walker=document.createTreeWalker(container,NodeFilter.SHOW_TEXT),nodes=[],n;while((n=walker.nextNode()))if(!n.parentElement.closest('a,code,pre,script,style')&&/(https?:\/\/|\b[a-z0-9-]+\.(com|org|net|io)\/)/i.test(n.nodeValue))nodes.push(n);nodes.forEach(function(node){var span=document.createElement('span');span.innerHTML=linkText(node.nodeValue);node.replaceWith(span);});}
  // Article media links: verified mediaId -> preserved file map (window.ArchiveArticleMediaMap,
  // data/article-media-map.json, generated from operating original HTMLs — see extract_mapping.py).
  // The body keeps its original link text in a hidden span so textContent and highlight
  // offsets are unchanged; the visible figure adds no text nodes.
  var ARTICLE_MEDIA_HREF = /^https:\/\/x\.com\/[^/]+\/article\/(\d+)\/media\/(\d+)$/;
  function articleMediaMapFor(a){
    var map = window.ArchiveArticleMediaMap;
    if(!map) return null;
    var id = String(a.id ?? a.tweetId ?? '');
    return (id && map[id]) || null;
  }
  function galleryMediaFor(a,placed){
    var media = a.media && a.media.some(function(m){return m&&m.kind==='video_thumbnail';}) ? a.media : a.mediaImages;
    placed = placed || new Set();
    if(!placed.size) return media;
    return (Array.isArray(media) ? media : []).filter(function(pair){
      if(pair && !Array.isArray(pair) && pair.kind === 'video_thumbnail') return true;
      var raw = Array.isArray(pair) ? pair[1] || pair[0] : typeof pair === 'string' ? pair : pair && (pair.full || pair.url || pair.src || pair.thumb);
      if(!raw) return true;
      var mapped = window.ArchiveMediaMap && window.ArchiveMediaMap[raw];
      var resolved = mapped ? new URL(mapped, document.baseURI).href : raw;
      return !placed.has(raw) && !placed.has(resolved);
    });
  }
  function hideMediaParens(anchor){
    var before=anchor.previousSibling,after=anchor.nextSibling;
    if(!before||!after||before.nodeType!==3||after.nodeType!==3)return;
    var left=before.nodeValue.match(/(?:^|\n)([ \t]*\([ \t]*)$/),right=after.nodeValue.match(/^([ \t]*\)[ \t]*)(?=\n|$)/);
    if(!left||!right)return;
    var open=before.splitText(before.nodeValue.length-left[1].length);after.splitText(right[1].length);
    [open,after].forEach(function(text){var keep=anchor.ownerDocument.createElement('span');keep.hidden=true;keep.textContent=text.nodeValue;text.replaceWith(keep);});
  }
  function restoreInlineArticleMedia(container, a){
    var map = articleMediaMapFor(a);
    if(!map || !container) return new Set();
    var placed=new Set();
    var id = String(a.id ?? a.tweetId ?? '');
    var doc = container.ownerDocument || document;
    Array.prototype.slice.call(container.querySelectorAll('a[href]')).forEach(function(anchor){
      if(anchor.closest('.aa-inline-media,[hidden],pre,code'))return;
      var href = anchor.getAttribute('href') || '';
      var m = ARTICLE_MEDIA_HREF.exec(href);
      if(!m || m[1] !== id) return;
      var embedded = map[m[2]];
      if(!embedded || anchor.closest('.translation-ko') || anchor.querySelector('img')) return;
      var mapped = window.ArchiveMediaMap && window.ArchiveMediaMap[embedded];
      var src = mapped ? new URL(mapped, document.baseURI).href : embedded;
      hideMediaParens(anchor);
      var keep = doc.createElement('span');
      keep.hidden = true; keep.setAttribute('aria-hidden', 'true');
      keep.textContent = anchor.textContent;
      var figure = doc.createElement('figure');
      figure.className = 'aa-inline-media';
      var link = doc.createElement('a');
      link.href = href; link.target = '_self'; link.rel = 'noopener noreferrer'; link.title = href;
      var img = doc.createElement('img');
      img.src = src; img.alt = ''; img.loading = 'lazy'; img.tabIndex = 0; img.title = '이미지 확대';
      link.appendChild(img); figure.appendChild(link);
      var frag = doc.createDocumentFragment();
      frag.appendChild(keep); frag.appendChild(figure);
      anchor.parentNode.replaceChild(frag, anchor);
      placed.add(embedded);placed.add(src);
    });
    return placed;
  }

  function formatContent(container, article){container.querySelectorAll('img').forEach(function(img){var raw=img.getAttribute('src'),mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];if(mapped)img.src=new URL(mapped,document.baseURI).href;});linkProse(container);container.querySelectorAll('.stage,.stage-title').forEach(function(el){var num=el.querySelector('.stage-num');if(num&&!/\.\s*$/.test(num.textContent))num.textContent=num.textContent.trim()+'. ';});container.querySelectorAll('a[href]').forEach(function(a){a.target='_self';a.title=a.href;});}
  function canonicalSource(a){var u=safeUrl(a.sourceUrl||a.source_url);if(a.fullHtml&&u){var parsed=new URL(u);if(parsed.pathname==='/'||parsed.pathname===''){var doc=new DOMParser().parseFromString(a.fullHtml,'text/html');var link=Array.from(doc.querySelectorAll('a[href]')).find(function(el){try{var v=new URL(el.href);return v.hostname.replace(/^www\./,'')===parsed.hostname.replace(/^www\./,'')&&v.pathname.length>1;}catch(e){return false;}});if(link)u=safeUrl(link.href);}}return u;}
  function bodyText(a) {
    if (a.fullText) return String(a.fullText);
    if (a.fullHtml) { var d = new DOMParser().parseFromString(cleanHtml(a.fullHtml), 'text/html'); return d.body.textContent || ''; }
    return String(a.summary || '본문이 없습니다.');
  }
  function title(a) { return String(a.title || bodyText(a).split('\n')[0].slice(0, 90) || a.author || '글'); }
  function author(a) { return String(a.author && typeof a.author === 'object' ? a.author.name || a.author.handle || '' : a.author || ''); }
  function metadata(a) { var date=ArchiveMetadata.describe(a).published;return [author(a),a.handle,(date.status==='recorded'||date.status==='edited')?(date.status==='edited'?'수정 ':'게시 ')+date.label:'게시 날짜 미확인',a.source_domain,a.date_saved?'기존 저장 '+a.date_saved:''].filter(Boolean).join(' · '); }
  function images(media, sourceUrl) {
    return (Array.isArray(media) ? media : []).map(function (pair) {
      var raw = Array.isArray(pair) ? pair[1] || pair[0] : typeof pair === 'string' ? pair : pair && (pair.full || pair.url || pair.src || pair.thumb);
      var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];var url = safeUrl(mapped?new URL(mapped,document.baseURI).href:raw);
      var video = pair && !Array.isArray(pair) && pair.kind === 'video_thumbnail';
      var origin = video && safeUrl(pair.sourceUrl || sourceUrl);
      var image = url ? '<img tabindex="0" title="이미지 확대" src="' + esc(url) + '" alt="'+(video?'동영상 썸네일':'게시물 이미지')+'" loading="lazy">' : '';
      return video ? '<figure class="video-thumbnail">'+image+(origin?'<figcaption><a href="'+esc(origin)+'" target="_self" rel="noopener noreferrer">동영상 원문 보기 ↗</a></figcaption>':'<figcaption>영상 원문 주소 미확인</figcaption>')+'</figure>' : image;
    }).join('');
  }
  function articleBodyHtml(a){
    var html=cleanHtml(a.fullHtml),source=canonicalSource(a);
    if(!source)return html;
    var doc=new DOMParser().parseFromString(html,'text/html');
    doc.body.querySelectorAll('a[href]').forEach(function(link){
      if(link.classList.contains('ib-card-link')||(/^원문\s*보기\s*[↗→]?\s*$/.test(link.textContent.trim())&&safeUrl(link.getAttribute('href'))===source))link.remove();
    });
    return doc.body.innerHTML;
  }
  function translatedBody(a){
    var tr=a.translation,original=bodyText(a),base=a.fullHtml?articleBodyHtml(a):'<p class="aa-text">'+linkText(original)+'</p>';
    if(!tr||tr.status!=='ok'||!Array.isArray(tr.paragraphs)||!Array.isArray(tr.translated)||tr.paragraphs.length!==tr.translated.length)return base;
    var compact=function(t){return String(t).replace(/\s+/g,' ').trim();};
    if(!a.fullHtml){
      if(compact(original)!==compact(tr.paragraphs.join(' ')))return base;
      return tr.paragraphs.map(function(t,i){var ko=String(tr.translated[i]||'').trim();return '<section class="translation-pair"><p class="translation-original">'+esc(t)+'</p>'+(ko&&compact(ko)!==compact(t)?'<p class="translation-ko"><span class="translation-label">한국어 번역</span><br>'+esc(ko)+'</p>':'')+'</section>';}).join('');
    }
    var doc=new DOMParser().parseFromString(base,'text/html'),used=new Set(),matched=0,needed=0;
    tr.paragraphs.forEach(function(t,i){
      var translated=String(tr.translated[i]||'').trim();if(!translated||compact(translated)===compact(t))return;needed++;
      var nodes=Array.from(doc.body.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,blockquote,figcaption,td,th')).filter(function(n){return !n.closest('.translation-ko')&&!used.has(n)&&compact(n.textContent)===compact(t);});
      var node=nodes.find(function(n){return !nodes.some(function(child){return child!==n&&n.contains(child);});});
      if(node){var ko=doc.createElement('p');ko.className='translation-ko';ko.textContent=translated;if(/^(TD|TH)$/.test(node.tagName))node.appendChild(ko);else node.after(ko);used.add(node);matched++;}
    });
    if(matched<needed){var notice=doc.createElement('p');notice.className='translation-notice';notice.textContent='일부 문단의 번역을 다시 확인해야 합니다. 원문은 그대로 표시합니다.';doc.body.appendChild(notice);}
    return doc.body.innerHTML;
  }
  function replyParagraphsMatch(text,cachedParas){
    // 문단 배열을 순서대로 대조한다. 이어 붙인 문자열 대조는
    // 문단 경계 변경(2문단→1문단 합침 등)을 탐지하지 못한다.
    // 공백 차이만 있는 변경은 허용한다 (compact 대조).
    var compact=function(t){return String(t).replace(/\s+/g,' ').trim();};
    var cur=String(text).split('\n\n').map(function(p){return p.trim();}).filter(Boolean);
    if(cur.length!==cachedParas.length)return false;
    for(var i=0;i<cur.length;i++)if(compact(cur[i])!==compact(cachedParas[i]))return false;
    return true;
  }
  function replyBody(r){
    // 답글 원문 + 문단 정렬 한국어 번역 (부모 translatedBody와 동일 패턴).
    // 번역이 없거나 원문과 캐시 문단이 어긋나면(수정/재정렬/문단 경계 변경)
    // 원문만 표시.
    var tr=r.translation,text=String(r.fullText||r.text||'');
    if(!tr||tr.status!=='ok'||!Array.isArray(tr.paragraphs)||!Array.isArray(tr.translated)||tr.paragraphs.length!==tr.translated.length)return '<p class="reply-text">'+linkText(text)+'</p>';
    var compact=function(t){return String(t).replace(/\s+/g,' ').trim();};
    if(!replyParagraphsMatch(text,tr.paragraphs))return '<p class="reply-text">'+linkText(text)+'</p>';
    return tr.paragraphs.map(function(t,i){var ko=String(tr.translated[i]||'').trim();return '<p class="reply-text">'+linkText(t)+'</p>'+(ko&&compact(ko)!==compact(t)?'<p class="translation-ko"><span class="translation-label">한국어 번역</span><br>'+esc(ko)+'</p>':'');}).join('');
  }
  function renderQuoted(q, depth, ancestors) {
    depth=depth||0; ancestors=ancestors||new Set();
    if(!q||typeof q!=='object'||Array.isArray(q)||depth>8||ancestors.has(q))return '';
    var seen=new Set(ancestors);seen.add(q);
    return '<details class="quoted" open><summary>인용 게시글</summary><blockquote><p>'+esc(author(q))+'</p>'+replyBody(q)+images(q.media||q.mediaImages,q.sourceUrl)+renderQuoted(q.quoted,depth+1,seen)+'</blockquote></details>';
  }
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
          replyBody(r) + images(r.media || r.mediaImages,r.sourceUrl) + renderQuoted(r.quoted) + (rid ? '<button type="button" data-hide-reply="' + esc(rid) + '">댓글 숨기기</button><button type="button" data-delete-reply="' + esc(rid) + '">댓글 삭제</button>' : '') + '</div>';
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
  function toolInfo(a) {
    return (a.toolDescription?'<p class="tool-description">'+esc(a.toolCategory||'AI 도구')+' · '+esc(a.toolDescription)+'</p>':'')+(a.toolLinks||[]).map(function(t){var u=safeUrl(t.url);return u&&/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+/.test(u)?'<p class="tool-shortcut"><a href="'+esc(u)+'" target="_self" rel="noopener noreferrer">↗ '+esc(t.title||'GitHub 바로가기')+'</a></p>':'';}).join('');
  }
  function articleHtml(a) {
    var url = canonicalSource(a), q = a.quoted;
    var body=document.createElement('div');body.innerHTML=translatedBody(a);linkProse(body);
    var placed=restoreInlineArticleMedia(body,a);
    var repliesHtml=renderReplies(a);
    var urlHtml=renderUrlContents(a);
    return '<h1>' + esc(title(a)) + '</h1>'+toolInfo(a)+'<p class="meta">' + esc(metadata(a)) + '</p><div class="aa-body" data-highlight-post="'+esc(String(a.id??a.tweetId??a.sourceUrl??''))+'">' +
      body.innerHTML + '</div>' + images(galleryMediaFor(a,placed),a.sourceUrl) +
      renderQuoted(q) +
      repliesHtml + urlHtml +
      recordInformation(a) + '<p>' + esc((a.topics || []).join(' · ')) + '</p>' + (url ? '<a href="' + esc(url) + '" target="_self" rel="noopener noreferrer">원문 보기 ↗</a>' : '');
  }
  // Index the reader's sanitized output, including only translations it displays.
  function duplicateCandidates(r) {
    return (r.sameSource || []).filter(function (other) { return hidden.indexOf(other.id) < 0; });
  }
  function recordInformation(a) {
    var info = ArchiveMetadata.describe(a), r = records.find(function (item) { return item.article === a; });
    var rows = [[info.published.status === 'edited' ? '수정 시각(원문)' : '게시 시각', info.published], ['수집 시각', info.captured], ['아카이브 등록 시각', info.archived]];
    if (info.legacySaved.status !== 'missing') rows.push(['기존 저장일', info.legacySaved]);
    var html = rows.map(function (row) { return '<dt>' + esc(row[0]) + '</dt><dd>' + esc(row[1].label) + (row[1].status === 'unverified' ? ' <small>' + esc(row[1].raw.slice(0, 160)) + '</small>' : '') + '</dd>'; }).join('');
    html += '<dt>작성자 식별</dt><dd>' + esc(info.author.handle ? '@' + info.author.handle : info.author.label) + '</dd>';
    if (info.source.key) html += '<dt>원문 식별</dt><dd>' + esc(info.source.key) + '</dd>';
    var warnings = [info.source.warning, info.author.warning].filter(Boolean);
    var others = r ? duplicateCandidates(r) : [];
    return '<details class="aa-record-info"><summary>자료 정보' + (others.length ? ' · 같은 원문 ' + (others.length + 1) + '건' : '') + '</summary><dl>' + html + '</dl>' +
      (warnings.length ? '<p class="aa-record-warning">' + esc(warnings.join(' · ')) + '</p>' : '') +
      (others.length ? '<p>같은 원문의 다른 저장본입니다. 자료와 메모·북마크는 각각 유지합니다.</p>' + others.map(function (other) { return '<button type="button" data-read-id="' + esc(other.id) + '">' + esc(title(other.article)) + '</button>'; }).join('') : '') + '</details>';
  }
  function applyTopicView(r) {
    // The normalized article is a view copy; source data and text anchors stay intact.
    r.topics=r.type==='ib'?r.originalTopics.slice():ArchiveTopics.effective(r.id,r.originalTopics);
    r.article.topics=r.topics.slice();r.unclassified=r.type!=='ib'&&ArchiveTopics.unclassified(r.topics);
    r.card.dataset.topics=r.topics.join('\n');r.searchCache=null;
    var tags=r.card.querySelector('.tags');if(tags)tags.innerHTML=ArchiveRender.topicsHtml(r.topics);
  }
  function refreshTopicViews() {
    records.forEach(applyTopicView);
    if(!controls)return;
    var group=controls.querySelector('[data-filter="topic"]')?.parentElement;
    if(group){var values=Array.from(new Set(records.filter(function(r){return r.type!=='ib';}).flatMap(function(r){return r.topics;}))).sort();
      group.innerHTML=button('전체 주제','topic','')+values.map(function(t){return button(t,'topic',t);}).join('');}
    ArchiveUI.overview(records);ArchiveUI.related(records);
    if(aiList){var tools=records.filter(function(r){return r.topics.indexOf('AI툴')>=0;});
      aiList.innerHTML='<h2>AI 스킬 · 도구 모음</h2><ul class="ai-tools-list">'+tools.map(function(r){var url=safeUrl(r.article.sourceUrl||r.article.source_url);return '<li><button type="button" data-read-id="'+esc(r.id)+'">'+esc(title(r.article))+'</button> <span class="ai-author">— '+esc(r.author)+'</span> '+(url?'<a href="'+esc(url)+'" target="_self" rel="noopener noreferrer">원문 ↗</a>':'')+toolInfo(r.article)+'</li>';}).join('')+'</ul>';}
    if(current&&readDialog&&readDialog.open){var scroll=readDialog.querySelector('.aa-content').scrollTop;openRead(current,true);readDialog.querySelector('.aa-content').scrollTop=scroll;}
  }
  function openTopicEditor(r) {
    if(r.type==='ib')return;
    topicRecord=r;
    var choices=Array.from(new Set(records.filter(function(x){return x.type!=='ib';}).flatMap(function(x){return x.topics.concat(x.originalTopics);}))).sort();
    topicDialog.querySelector('[data-topic-options]').innerHTML=choices.map(function(t){return '<label><input type="checkbox" value="'+esc(t)+'"'+(r.topics.indexOf(t)>=0?' checked':'')+'><span>'+esc(t)+'</span></label>';}).join('');
    topicDialog.querySelector('[data-topic-new]').value='';topicDialog.querySelector('[data-topic-error]').textContent='';show(topicDialog);
  }

  function prepareRecordMetadata() {
    var labels = ArchiveMetadata.authorLabels(records.map(function (r) { return r.article; }));
    records.forEach(function (r) {
      var info = ArchiveMetadata.describe(r.article);
      r.metadataReview = !!(info.source.warning || info.author.warning || [info.published,info.captured,info.archived,info.legacySaved].some(function(d){return d.status==='unverified';}));
      r.authorKey = info.author.key || 'unverified:' + r.id;
      r.author = labels.get(info.author.key) || info.author.label;
      r.card.dataset.author = r.author;
      var byline = r.card.querySelector('.byline .author'); if (byline) byline.textContent = r.author;
      r.sameSource = [];
    });
    ArchiveMetadata.duplicateGroups(records.map(function (r) { return r.article; })).forEach(function (group) {
      group.indices.forEach(function (index) { records[index].sameSource = group.indices.filter(function (other) { return other !== index; }).map(function (other) { return records[other]; }); });
    });
  }
  function duplicateBadge(r) {
    var previous = r.card.querySelector('.aa-duplicate-source'); if (previous) previous.remove();
    var others = duplicateCandidates(r); if (!others.length) return;
    var button = document.createElement('button'); button.type = 'button'; button.className = 'aa-duplicate-source'; button.dataset.duplicates = r.id;
    button.textContent = '같은 원문 ' + (others.length + 1) + '건 · 별도 보관';
    (r.card.querySelector('summary.card-toggle') || r.card).appendChild(button);
  }
  function refreshRecordInformation() {
    if (!current || !readDialog || !readDialog.open) return;
    var previous = readDialog.querySelector('.aa-record-info');
    if (!previous) return;
    var wasOpen = previous.open;
    previous.outerHTML = recordInformation(current.article);
    readDialog.querySelector('.aa-record-info').open = wasOpen;
  }

  function searchSegments(r) {
    var signature = JSON.stringify([load('archive-removed-replies', []), load('archive-deleted-replies', [])]);
    // Bootstrap data refresh recreates records. Any future in-place article/translation update must clear searchCache.
    if (r.searchCache && r.searchCache.signature === signature) return r.searchCache.segments;
    var doc = new DOMParser().parseFromString(articleHtml(r.article), 'text/html'), segments = [];
    var replies = Array.from(doc.querySelectorAll('.reply')), quotes = Array.from(doc.querySelectorAll('.quoted'));
    function visible(node) { return node && !node.closest('[hidden]'); }
    function content(node, omit) {
      var clone = node.cloneNode(true);
      clone.querySelectorAll(omit || 'button,summary,.translation-label').forEach(function (el) { el.remove(); });
      function walk(el) {
        if (el.nodeType === 3) return el.nodeValue;
        var value = Array.from(el.childNodes).map(walk).join('');
        return /^(P|DIV|SECTION|LI|BLOCKQUOTE|H[1-6]|BR)$/.test(el.tagName || '') ? '\n' + value + '\n' : value;
      }
      return walk(clone).replace(/\s+/g, ' ').trim();
    }
    function add(node, label, kind, index, omit) {
      if (!visible(node)) return;
      var value = content(node, omit);
      if (value) segments.push({ text: value, label: label, kind: kind, index: index || 0 });
    }
    add(doc.querySelector('h1'), '제목', 'body');
    add(doc.querySelector('p.meta'), '작성자·날짜', 'body');
    add(doc.querySelector('.aa-body'), '본문', 'body', 0, '.translation-ko,.translation-label,button,summary');
    Array.from(doc.querySelectorAll('.aa-body .translation-ko')).forEach(function (node, i) { add(node, '본문 번역', 'translation', i); });
    replies.forEach(function (node, i) {
      add(node, '댓글 ' + (i + 1), 'reply', i, '.quoted,.translation-ko,button,summary');
      Array.from(node.querySelectorAll('.translation-ko')).filter(function (el) { return !el.closest('.quoted'); }).forEach(function (el) { add(el, '댓글 ' + (i + 1) + ' 번역', 'reply', i); });
    });
    quotes.forEach(function (node, i) {
      add(node, node.closest('.reply') ? '댓글의 인용문' : '인용문', 'quote', i, '.quoted,.translation-ko,button,summary');
      Array.from(node.querySelectorAll('.translation-ko')).filter(function (el) { return el.closest('.quoted') === node; }).forEach(function (el) { add(el, '인용문 번역', 'quote', i); });
    });
    Array.from(doc.querySelectorAll('.url-inline-item')).forEach(function (node, i) { add(node, '링크 내용', 'link', i); });
    Array.from(doc.querySelectorAll('a[href]')).forEach(function (node) {
      if (!visible(node)) return;
      var reply = node.closest('.reply'), quote = node.closest('.quoted');
      var kind = quote ? 'quote' : reply ? 'reply' : node.closest('.url-inline-item') ? 'link' : 'body';
      var index = quote ? quotes.indexOf(quote) : reply ? replies.indexOf(reply) : 0;
      segments.push({ text: node.getAttribute('href'), label: quote ? '인용문 링크' : reply ? '댓글 링크' : '본문·원문 링크', kind: kind, index: index });
    });
    segments.push({ text: [r.article.summary, r.article.toolDescription, r.article.toolCategory, r.topics.join(' ')].filter(Boolean).join(' '), label: '주제·요약', kind: 'body', index: 0 });
    r.searchCache = { signature: signature, segments: segments };
    return segments;
  }
  function searchRecord(r, words) {
    var segments = searchSegments(r).slice(), memo = memoValue(r.card.querySelector('[data-memo-id]')?.dataset.memoId || r.id);
    if (memo) segments.push({ text: memo, label: '메모', kind: 'memo', index: 0 });
    return ArchiveSearch.match(segments, words, mode);
  }
  function searchResult(r, result, words) {
    var previous = r.card.querySelector('.aa-search-hit');
    if (previous) previous.remove();
    r.searchMatch = null;
    if (!words.length || !result.matched || !result.hits.length) return;
    var hit = result.hits[0], panel = document.createElement('button');
    r.searchMatch = hit.segment;
    panel.type = 'button'; panel.className = 'aa-search-hit'; panel.dataset.searchResult = r.id;
    var label = document.createElement('strong'), excerpt = document.createElement('span');
    label.textContent = result.hits.slice(0, 3).map(function (item) { return item.segment.label; }).filter(function (v, i, all) { return all.indexOf(v) === i; }).join(' · ');
    excerpt.textContent = ArchiveSearch.excerpt(hit.segment.text, hit.words);
    panel.append(label, excerpt); (r.card.querySelector('summary.card-toggle') || r.card).appendChild(panel);
  }
  function revealSearchMatch(r) {
    var hit = r.searchMatch;
    if (!hit) return;
    if (hit.kind === 'memo') {
      memoId = r.card.querySelector('[data-memo-id]')?.dataset.memoId || r.id;
      memoDialog.querySelector('textarea').value = memoValue(memoId); show(memoDialog); return;
    }
    var selectors = { reply: '.reply', quote: '.quoted', translation: '.aa-body .translation-ko', link: '.url-inline-item', body: '.aa-body' };
    var target = readDialog.querySelectorAll(selectors[hit.kind] || '.aa-body')[hit.index || 0];
    if (!target || target.closest('[hidden]')) return;
    for (var node = target; node && node !== readDialog; node = node.parentElement) if (node.tagName === 'DETAILS') node.open = true;
    window.requestAnimationFrame(function () { if (readDialog.open && target.isConnected) target.scrollIntoView({ block: 'center' }); });
  }

  function button(text, group, value) { return '<button type="button" data-filter="' + group + '" data-value="' + esc(value) + '" aria-pressed="false">' + esc(text) + '</button>'; }
  function toggle(array, value) { var i = array.indexOf(value); if (i < 0) array.push(value); else array.splice(i, 1); }
  function status(message) { var el = controls.querySelector('[data-results-status]'); if (el) el.textContent = message; }
  function update() {
    var modeButton=controls.querySelector('[data-action="mode"]');modeButton.textContent=mode;modeButton.setAttribute('aria-label','검색 조건 '+mode+', 클릭하여 변경');modeButton.setAttribute('aria-pressed',String(mode==='AND'));
    var words = ArchiveSearch.terms(controls.querySelector('input').value), count = 0, pinCount = 0;
    var defaultView=!words.length&&!selection.review&&!selection.memo&&!selection.hidden&&!selection.type.length&&!selection.topic.length&&!selection.author.length&&!selection.bookmark.length;
    if(aiList)aiList.hidden=!(defaultView||selection.type[0]==='ai');
    records.forEach(function (r) {
      var isHidden = hidden.indexOf(r.id) >= 0, color = bookmarks[r.id], isPinned = pins.indexOf(r.id) >= 0;
      var result = words.length ? searchRecord(r, words) : { matched: true, hits: [] };
      var match = result.matched;
      searchResult(r, result, words);
      duplicateBadge(r);
      var visible = match && (!selection.memo || !!memoValue(r.card.querySelector('[data-memo-id]')?.dataset.memoId || r.id).trim()) && (!selection.review || r.unclassified) && (selection.hidden ? isHidden : !isHidden) && (!selection.type.length || selection.type.indexOf(r.type) >= 0 || (selection.type[0]==='ai'&&r.topics.indexOf('AI툴')>=0)) &&
        (!selection.topic.length || selection.topic.some(function (t) { return r.topics.indexOf(t) >= 0; })) && (!selection.author.length || selection.author.indexOf(r.authorKey) >= 0) &&
        (!selection.subject.length || selection.subject[0]===r.article.subject) && (!selection.category.length || selection.category[0]===r.article.category) && (!selection.bookmark.length || (color && (selection.bookmark.indexOf('all') >= 0 || selection.bookmark.indexOf(color) >= 0)));
      if(!selection.type.length&&r.type==='ib'&&!selection.review&&!selection.memo)visible=false;
      if(defaultView&&r.topics.indexOf('AI툴')>=0&&!isPinned)visible=false;
      r.card.hidden = !visible;
      r.card.style.borderLeftColor = color || '';
      r.card.style.borderTopColor = color || '';
      r.card.style.borderTopWidth = color ? '3px' : ''; 
      r.card.classList.toggle('aa-bookmarked', !!color);
      var mb = r.card.querySelector('[data-memo-id]'), pb = r.card.querySelector('.pin-btn'), bb = r.card.querySelector('.bookmark-btn'), db = r.card.querySelector('.delete-btn');
      if (mb) { var hasMemo = !!memoValue(mb.dataset.memoId).trim(); mb.classList.remove('memo-hidden'); mb.classList.toggle('aa-active', hasMemo); mb.setAttribute('aria-label', hasMemo ? '메모 편집 (메모 있음)' : '메모 추가'); }
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
    var reviewButton=controls.querySelector('[data-action=metadata-review]');if(reviewButton){reviewButton.setAttribute('aria-pressed',String(selection.review));reviewButton.textContent=selection.review?'미분류자료 해제':'미분류자료 보기';}
    var memoFilter=controls.querySelector('[data-action=memo-filter]');memoFilter.setAttribute('aria-pressed',String(!!selection.memo));memoFilter.textContent=selection.memo?'메모된 게시물 · 해제':'메모된 게시물';
    status((selection.memo?'메모된 게시물 · ':'')+(selection.review?'미분류자료 · ':'')+(count ? count + '개 글 표시' : '조건에 맞는 글이 없습니다.'));
    if(aiList)aiList.querySelectorAll('[data-read-id]').forEach(function(b){var record=records.find(function(r){return r.id===b.dataset.readId;});b.closest('li').hidden=hidden.indexOf(b.dataset.readId)>=0||(selection.type[0]==='ai'&&record&&record.card.hidden);});
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
  function openRead(r,back) {if(current&&readDialog.open&&!back&&current!==r)readerHistory.push(current);if(!readDialog.open)readerHistory=[];current=r;readDialog.querySelector('[data-topic-edit]').hidden=r.type==='ib';readDialog.querySelector('[data-share=text]').textContent='텍스트 복사';var related=(r.related||[]).filter(function(other){return hidden.indexOf(other.id)<0;});readDialog.querySelector('.aa-content').innerHTML=articleHtml(r.article)+(related.length?'<section class="reader-related"><h2>연관 게시글</h2>'+related.map(function(other){return '<button type="button" data-related-id="'+esc(other.id)+'">'+'– '+esc(title(other.article))+'<small> · '+esc(other.author)+'</small></button>';}).join('')+'</section>':'');readDialog.querySelector('[data-reader-title]').textContent=title(r.article);readDialog.querySelector('[data-reader-back]').hidden=!readerHistory.length;if(!readDialog.open)show(readDialog);formatContent(readDialog.querySelector('.aa-content'),r.article);if(window.ArchiveHighlights)window.ArchiveHighlights.refresh();readDialog.querySelector('.aa-content').scrollTop=0; }
  function resetFilters(){Object.keys(selection).forEach(function(k){selection[k]=(k==='hidden'||k==='review'||k==='memo')?false:[];});mode='OR';controls.querySelector('input').value='';controls.querySelector('[data-action="mode"]').textContent='OR';update();}

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
    var doc=new DOMParser().parseFromString(articleHtml(a),'text/html');var urls=Array.from(new Set(Array.from(doc.querySelectorAll('img')).map(function(i){return i.src;}))),failed=[];
    await Promise.all(urls.map(async function(url,i){try{var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[url];var response=await fetch(mapped?new URL(mapped,document.baseURI).href:url);if(!response.ok)throw new Error('image');var blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('image');files.push(new File([blob],'attachment-'+(i+1)+'.'+(blob.type.split('/')[1]||'png'),{type:blob.type}));}catch(e){failed.push(url);}}));
    if(failed.length){window.alert('첨부 이미지 '+failed.length+'개를 가져오지 못했습니다. 이미지가 빠진 채로 공유하지 않습니다. 원문 또는 HTML 다운로드를 이용해 주세요.');return;}
    if(navigator.share&&navigator.canShare&&navigator.canShare({files:files})){try{await navigator.share({files:files});}catch(e){if(e.name!=='AbortError')files.forEach(function(f){download(f,f.name);});}}else files.forEach(function(f){download(f,f.name);});
  }
  async function shareHtml(a,downloadOnly){
    var container=document.createElement('article');container.innerHTML=articleHtml(a);formatContent(container,a);
    try{await Promise.all(Array.from(container.querySelectorAll('img')).map(async function(img){var response=await fetch(img.src);if(!response.ok)throw new Error('image');var blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('image');img.src=await new Promise(function(resolve,reject){var reader=new FileReader();reader.onload=function(){resolve(reader.result);};reader.onerror=reject;reader.readAsDataURL(blob);});}));}catch(e){window.alert('첨부 이미지를 포함한 HTML을 만들지 못했습니다. 이미지가 빠진 파일은 공유하지 않습니다.');return;}
    var css='body{max-width:850px;margin:24px auto;padding:16px;font:15px/1.75 system-ui,sans-serif;background:#fffdf8;color:#222b3b;overflow-wrap:anywhere}h1{font-size:24px;line-height:1.4;margin:0 0 18px}h2{font-size:20px;margin:28px 0 14px}h3{font-size:17px;margin:24px 0 12px}p{margin:0 0 16px}img{display:block;max-width:100%;height:auto;margin:16px auto}.aa-text,.translation-original,.translation-ko{white-space:pre-wrap}.stage-card{margin:28px 0;padding-top:18px;border-top:1px solid #ddd6ca}.stage{display:flex;gap:6px;flex-wrap:wrap}.phase-tag{display:block;flex-basis:100%;font-size:12px;font-weight:400}.flow-step{display:flex;gap:12px;padding:10px 0}.flow-dot{flex:none;min-width:24px}.flow-body b,.flow-body small{display:block}.flow-body small{font-size:12px}.org-label{display:block;font-size:11px;color:#716e65}.original,.translation-ko{padding:12px 16px;margin:16px 0;background:#eee9df}a{color:#675a47;text-decoration:underline}table{display:block;overflow:auto;border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd6ca}[hidden],button{display:none!important}@page{margin:15mm}@media print{body,.original,.translation-ko{background:white!important}h1{font-size:18pt}h2{font-size:14pt}h3{font-size:12pt}}';
    var htmlBlob=new Blob(['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title(a))+'</title><style>'+css+'</style></head><body>'+container.innerHTML+'</body></html>'],{type:'text/html'});if(downloadOnly)download(htmlBlob,'archive.html');else shareFile(htmlBlob,'archive.html',title(a));
  }
  function init(articles) {
    var previous=controls?{selection:selection,mode:mode,query:controls.querySelector('input').value,scroll:window.scrollY}:null;
    list = document.getElementById('articles'); if (!list) return;
    document.body.classList.remove('aa-modal-open');
    [topicDialog, controls, pinned, readDialog, memoDialog, bookmarkDialog, renameDialog, confirmDialog, exportDialog, exportDoneDialog, imageDialog, aiList].forEach(function (el) { if (el) el.remove(); });
    selection = { type: [], topic: [], author: [], bookmark: [], subject: [], category: [], hidden: false, review: false, memo: false }; mode = 'OR'; current = null;
    pins = load('archive-pinned', []); hidden = load('archive-deleted-posts', []);
    pins = Array.isArray(pins) ? pins.map(String) : []; hidden = Array.isArray(hidden) ? hidden.map(String) : [];
    var saved = load('archive-bookmarks', {}); bookmarks = Object.create(null);
    // v1 호환: 저장된 hex 값을 그대로 보존 (사전 정의되지 않은 custom 색상도 유지)
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) Object.keys(saved).forEach(function (id) { if (typeof saved[id] === 'string' && saved[id]) bookmarks[id] = saved[id]; });
    var byIdx = Object.create(null); (articles || []).forEach(function (a, i) { byIdx[String(a._idx == null ? i : a._idx)] = window.ArchiveRender.normalizeArticle(a); });
    records = Array.prototype.map.call(list.querySelectorAll('.article'), function (card) {
      var a = byIdx[card.dataset.idx] || {}, topics = Array.isArray(a.topics) ? a.topics.map(String) : (card.dataset.topics || '').split('\n').filter(Boolean);
      return { card: card, id: card.dataset.pinId || card.dataset.idx, article: a, author: card.dataset.author || author(a), type: card.dataset.type || a.type, topics: topics,
        search: [title(a), bodyText(a), a.summary, a.toolDescription, a.toolCategory, author(a), a.handle, topics.join(' ')].join(' ').toLowerCase() };
    });
    records.forEach(function(r){r.originalTopics=r.topics.slice();applyTopicView(r);});
    prepareRecordMetadata();
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
    aiList=document.createElement('section');aiList.className='ai-tools-section';var tools=records.filter(function(r){return r.topics.indexOf('AI툴')>=0;});aiList.innerHTML='<h2>AI 스킬 · 도구 모음</h2><ul class="ai-tools-list">'+tools.map(function(r){var url=safeUrl(r.article.sourceUrl||r.article.source_url);return '<li><button type="button" data-read-id="'+esc(r.id)+'">'+esc(title(r.article))+'</button> <span class="ai-author">— '+esc(r.author)+'</span> '+(url?'<a href="'+esc(url)+'" target="_self" rel="noopener noreferrer">원문 ↗</a>':'')+toolInfo(r.article)+'</li>';}).join('')+'</ul>';list.after(aiList);
    controls.addEventListener('input', update); controls.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      if(b.dataset.filter==='bookmark'){if(suppressBookmarkClick){suppressBookmarkClick=false;return;}clearTimeout(bookmarkClickTimer);if(e.detail<2)bookmarkClickTimer=setTimeout(function(){selection.bookmark=b.dataset.value&&selection.bookmark[0]!==b.dataset.value?[b.dataset.value]:[];update();},250);return;}if (b.dataset.filter) {var field=b.dataset.filter;selection[field]=b.dataset.value&&selection[field][0]!==b.dataset.value?[b.dataset.value]:[];if(field==='type'){selection.topic=[];selection.author=[];selection.subject=[];selection.category=[];}}
      if(b.dataset.action==='filter-close'){var filters=b.closest('details.filters');filters.open=false;filters.querySelector('summary').focus({preventScroll:true});return;}
      if(b.dataset.action==='memo-filter')selection.memo=!selection.memo;
      if(b.dataset.action==='metadata-review')selection.review=!selection.review;
      if(b.dataset.action==='reset') resetFilters();
      if (b.dataset.action === 'mode') { mode = mode === 'OR' ? 'AND' : 'OR'; b.textContent = mode; b.setAttribute('aria-label', '검색 조건 ' + mode + ', 클릭하여 변경'); }
      if (b.dataset.action === 'hidden') selection.hidden = !selection.hidden;
      if (b.dataset.action === 'drive-export') { openExport(); return; }
      if (b.dataset.action === 'drive') { DriveSync.connect().catch(function(e){window.alert(e.message);}); return; }
      if (b.dataset.action === 'drive-download') { DriveSync.download().catch(function(e){window.alert(e.message);});return; }
      update();
    });
    var pressTimer,pressOrigin,bookmarkClickTimer,suppressBookmarkClick=false;
    function renameBookmark(b){clearTimeout(bookmarkClickTimer);renameHex=b.dataset.value;if(!colors[renameHex])return;renameDialog.querySelector('input').value=getBookmarkName(renameHex);show(renameDialog);var input=renameDialog.querySelector('input');var selection=window.getSelection();if(selection)selection.removeAllRanges();input.focus({preventScroll:true});input.setSelectionRange(0,input.value.length);}
    controls.addEventListener('pointermove',function(e){if(pressOrigin&&Math.hypot(e.clientX-pressOrigin.x,e.clientY-pressOrigin.y)>12)clearTimeout(pressTimer);});
    controls.addEventListener('dblclick',function(e){var b=e.target.closest('[data-filter=bookmark]');if(b&&colors[b.dataset.value]){e.preventDefault();renameBookmark(b);}});
    controls.addEventListener('contextmenu',function(e){var b=e.target.closest('[data-filter="bookmark"]');if(b&&colors[b.dataset.value]){e.preventDefault();renameBookmark(b);}});
    controls.addEventListener('pointerdown',function(e){var b=e.target.closest('[data-filter="bookmark"]');if(b&&colors[b.dataset.value]){pressOrigin={x:e.clientX,y:e.clientY};pressTimer=setTimeout(function(){suppressBookmarkClick=true;document.addEventListener('pointerup',function(){setTimeout(function(){suppressBookmarkClick=false;},0);},{once:true,capture:true});renameBookmark(b);},550);}});
    ['pointerup','pointercancel'].forEach(function(event){controls.addEventListener(event,function(){clearTimeout(pressTimer);if(event==='pointerup'&&renameDialog&&renameDialog.open){var range=window.getSelection();if(range)range.removeAllRanges();var input=renameDialog.querySelector('input');input.focus({preventScroll:true});input.setSelectionRange(0,input.value.length);}});});
    // Drive 동기화 초기화
    DriveSync.init(controls.querySelector('[data-drive-status]'));
    var exportOpening=false;
    async function openExport(){if(exportOpening)return;exportOpening=true;try{await DriveSync.connect();show(exportDialog);}catch(e){window.alert(e.message);}finally{exportOpening=false;}}
    var driveQuick=document.getElementById('archive-export-quick');if(driveQuick)driveQuick.onclick=function(){openExport();};
    var backupButton = document.createElement('button'); backupButton.type = 'button'; backupButton.textContent = '저장 데이터 백업';
    backupButton.onclick = function () { download(new Blob([JSON.stringify(DriveSync._exportState(), null, 2)], {type:'application/json'}), 'archive-state.json'); };
    controls.querySelector('[data-settings-backup]').appendChild(backupButton);
    var restoreReplies=document.createElement('button');restoreReplies.textContent='숨긴 댓글 모두 복원';restoreReplies.onclick=function(){persist('archive-deleted-replies',[]);document.querySelectorAll('[data-reply-id]').forEach(function(r){r.hidden=false;});status('숨긴 댓글을 복원했습니다.');};controls.querySelector('[data-settings-hidden]').appendChild(restoreReplies);
    var importLabel=document.createElement('label');importLabel.className='backup-file';importLabel.textContent='백업 파일로 복원';var importInput=document.createElement('input');importInput.type='file';importInput.accept='.json,application/json';importInput.setAttribute('aria-label','백업 파일 복원');importLabel.appendChild(importInput);controls.querySelector('[data-settings-backup]').appendChild(importLabel);
    importInput.onchange=async function(){var file=importInput.files[0];if(!file)return;try{var state=JSON.parse(await file.text());if(!DriveSync._validateRemote(state))throw new Error('올바른 아카이브 백업이 아닙니다.');if(!await askConfirm('현재 저장 내용을 백업한 후 선택한 파일로 복원할까요?'))return;localStorage.setItem('archive-before-import',JSON.stringify(DriveSync._exportState()));DriveSync.importState(state);refreshSavedState();status('백업 복원 완료');}catch(e){window.alert(e.message);}finally{importInput.value='';}};

    readDialog = dialog('archive-read-dialog', '글 읽기', '<header class="aa-reader-header"><button type="button" data-reader-back hidden>← 이전 글</button><strong data-reader-title></strong><button type="button" data-close>닫기 ×</button><div class="aa-toolbar"><button type="button" data-share="text">텍스트 복사</button><details class="html-actions"><summary>공유</summary><div><button type="button" data-share="image">이미지로 공유</button><button type="button" data-share="html">HTML 공유</button><button type="button" data-share="html-download">HTML 다운로드</button></div></details><button type="button" data-share="print">인쇄</button></div></header><article class="aa-content"></article>');
    readDialog.addEventListener('keydown',function(e){if(e.target.tagName==='IMG'&&(e.key==='Enter'||e.key===' ')){e.preventDefault();ArchiveImages.open(e.target,readDialog.querySelector('.aa-content'));}});
    // Keep vertical reading, text selection, media and horizontal scrollers native.
    var readerSwipe=null;
    function clearReaderSwipe(){readerSwipe=null;}
    readDialog.addEventListener('touchstart',function(e){
      clearReaderSwipe();
      if(!window.matchMedia('(max-width: 767px)').matches||e.touches.length!==1||!readDialog.open)return;
      var target=e.target.nodeType===1?e.target:e.target.parentElement;
      if(!target||target.closest('a,button,input,textarea,select,summary,[contenteditable],img,video,audio,canvas,table,.archive-highlight-toolbar'))return;
      if(String(window.getSelection()||'').trim())return;
      for(var node=target;node&&node!==readDialog;node=node.parentElement){
        if(node.scrollWidth>node.clientWidth+2&&/auto|scroll/.test(getComputedStyle(node).overflowX))return;
      }
      var t=e.touches[0];readerSwipe={id:t.identifier,x:t.clientX,y:t.clientY,time:Date.now(),scrollX:window.scrollX,scrollY:window.scrollY};
    },{passive:true});
    readDialog.addEventListener('touchmove',function(e){
      if(!readerSwipe)return;
      if(e.touches.length!==1){clearReaderSwipe();return;}
      var t=e.touches[0],dx=t.clientX-readerSwipe.x,dy=t.clientY-readerSwipe.y;
      if(t.identifier!==readerSwipe.id||Math.abs(dy)>24&&Math.abs(dy)>Math.abs(dx)||dx< -24)clearReaderSwipe();
    },{passive:true});
    readDialog.addEventListener('touchend',function(e){
      var start=readerSwipe;clearReaderSwipe();
      if(!start||e.touches.length||e.changedTouches.length!==1||!readDialog.open||String(window.getSelection()||'').trim())return;
      var t=e.changedTouches[0],dx=t.clientX-start.x,dy=Math.abs(t.clientY-start.y);
      if(t.identifier!==start.id||dx<90||dx<dy*2.5||dy>60||Date.now()-start.time>900)return;
      close(readDialog);
      window.requestAnimationFrame(function(){window.scrollTo(start.scrollX,start.scrollY);});
    },{passive:true});
    readDialog.addEventListener('touchcancel',clearReaderSwipe,{passive:true});
    readDialog.addEventListener('close',clearReaderSwipe);
    var swipeY;readDialog.querySelector('header').addEventListener('touchstart',function(e){swipeY=e.touches[0].clientY;},{passive:true});readDialog.querySelector('header').addEventListener('touchend',function(e){if(e.changedTouches[0].clientY-swipeY>80)close(readDialog);},{passive:true});
    readDialog.addEventListener('click', function (e) {
      if(e.target.closest('[data-topic-edit]')&&current){openTopicEditor(current);return;}
      var memoButton=e.target.closest('[data-reader-memo]');if(memoButton&&current){memoId=current.article.memoId||current.id;memoDialog.querySelector('textarea').value=memoValue(memoId);show(memoDialog);return;}
      if(e.target.closest('[data-reader-back]')){var previous=readerHistory.pop();if(previous)openRead(previous,true);return;}
      if (e.target.tagName === 'IMG') { e.preventDefault(); ArchiveImages.open(e.target,readDialog.querySelector('.aa-content')); return; }
      var b = e.target.closest('[data-share]'); if (!b || !current) return; var a = current.article;var menu=b.closest('.html-actions');if(menu)menu.open=false;
      runArticleAction(b.dataset.share,current,b,false);
    });
    var readerTopic=document.createElement('button');readerTopic.type='button';readerTopic.dataset.topicEdit='';readerTopic.textContent='주제 변경';readDialog.querySelector('.aa-toolbar').appendChild(readerTopic);
    var readerMemo=document.createElement('button');readerMemo.type='button';readerMemo.dataset.readerMemo='';readerMemo.textContent='메모';readDialog.querySelector('.aa-toolbar').prepend(readerMemo);
    records.forEach(function(r){r.card.querySelectorAll('.aa-inline-toolbar').forEach(function(el){el.remove();});var toolbar=readDialog.querySelector('.aa-toolbar').cloneNode(true);toolbar.classList.add('aa-inline-toolbar');toolbar.querySelector('[data-topic-edit]').hidden=r.type==='ib';toolbar.setAttribute('aria-label','게시글 도구');r.card.querySelector('summary.card-toggle').after(toolbar);});
    topicDialog=dialog('archive-topic-dialog','주제 변경','<h2>주제 변경</h2><p>이 글에 지정할 주제를 선택하세요. 여러 개를 선택할 수 있습니다.</p><div class="archive-topic-options" data-topic-options></div><label for="archive-topic-new">새 주제 추가 (쉼표로 구분)</label><input type="text" id="archive-topic-new" data-topic-new maxlength="1000" autocomplete="off"><p class="aa-topic-note">저장하면 이 기기에 즉시 적용됩니다. 다른 기기에도 반영하려면 내보내기를 해주세요.</p><p data-topic-error role="status"></p><div class="aa-chips"><button type="button" data-topic-save>저장</button><button type="button" data-topic-restore>원래 주제로 복원</button><button type="button" data-close>취소</button></div>');
    topicDialog.classList.add('archive-topic-dialog');
    topicDialog.addEventListener('click',function(e){
      var save=e.target.closest('[data-topic-save]'),restore=e.target.closest('[data-topic-restore]');if((!save&&!restore)||!topicRecord)return;
      try{
        var selected=Array.from(topicDialog.querySelectorAll('[data-topic-options] input:checked')).map(function(el){return el.value;});
        var extra=topicDialog.querySelector('[data-topic-new]').value.split(/[,，\n]/).map(function(t){return t.trim();}).filter(Boolean);
        var next=ArchiveTopics.changed(topicRecord.id,selected.concat(extra),!!restore);
        if(!persist(ArchiveTopics.key,next))return;
        close(topicDialog);refreshTopicViews();update();
        status('주제를 변경했습니다. 다른 기기에도 반영하려면 내보내기를 해주세요.');
      }catch(error){topicDialog.querySelector('[data-topic-error]').textContent=error.message;}
    });

    memoDialog = dialog('archive-memo-dialog', '메모 편집', '<h2>메모</h2><label for="archive-memo-text">내용</label><textarea id="archive-memo-text"></textarea><div class="aa-chips"><button type="button" data-memo="save">저장</button><button type="button" data-memo="delete">삭제</button><button type="button" data-close>닫기</button></div>');
    memoDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-memo]'); if (!b) return; var value = b.dataset.memo === 'delete' ? null : memoDialog.querySelector('textarea').value; if (persist('archive_memo_' + memoId, value)) { close(memoDialog); update(); } });
    bookmarkDialog = dialog('archive-bookmark-dialog', '북마크 색상 선택', '<h2>북마크 색상</h2><div class="aa-chips">' + Object.keys(colors).map(function (c) { return '<button type="button" data-color="' + c + '" style="border-color:' + c + '">' + (getBookmarkName(c) || c) + '</button>'; }).join('') + '<button type="button" data-color="">해제</button><button type="button" data-close>닫기</button></div>');
    bookmarkDialog.addEventListener('click', function (e) { var b = e.target.closest('[data-color]'); if (!b || !bookmarkRecord) return; var next = Object.assign(Object.create(null), bookmarks); if (b.dataset.color) next[bookmarkRecord.id] = b.dataset.color; else delete next[bookmarkRecord.id]; if (persist('archive-bookmarks', next)) { bookmarks = next; close(bookmarkDialog); update(); } });
    renameDialog=dialog('archive-rename-dialog','북마크 이름 변경','<h2>북마크 이름</h2><label for="archive-color-name">이름</label><input id="archive-color-name" maxlength="30"><div class="aa-chips"><button type="button" data-save-name>저장</button><button type="button" data-close>취소</button></div>');
    function saveName(){var name=renameDialog.querySelector('input').value.trim()||colors[renameHex],names=load('archive-bookmark-names',{});names[renameHex]=name;if(persist('archive-bookmark-names',names)){controls.querySelectorAll('[data-filter=bookmark]').forEach(function(b){if(b.dataset.value===renameHex){b.textContent=name;b.setAttribute('aria-label',name);}});close(renameDialog);}}
    renameDialog.addEventListener('click',function(e){if(e.target.closest('[data-save-name]'))saveName();});renameDialog.addEventListener('pointerup',function(e){if(e.target.tagName!=='INPUT'){var input=renameDialog.querySelector('input');input.focus({preventScroll:true});input.setSelectionRange(0,input.value.length);}});renameDialog.querySelector('input').addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();saveName();}});
    exportDialog=dialog('archive-export-dialog','Drive 내보내기 확인','<h2>Drive로 내보내기</h2><p>이 기기의 메모·북마크·삭제 상태를 Drive에 반영합니다. 내보낼 때마다 비밀번호가 필요합니다.</p><label for="archive-export-password">비밀번호</label><input type="password" id="archive-export-password" autocomplete="off" maxlength="128"><p data-export-error role="status"></p><div class="aa-chips"><button type="button" data-export-submit>확인 · 내보내기</button><button type="button" data-close>취소</button></div>');
    exportDoneDialog=dialog('archive-export-done-dialog','내보내기 완료','<h2>내보내기가 완료되었습니다.</h2><p>Drive에 저장했습니다. 다른 기기에는 공통 설정 발행 후 반영됩니다. 보통 약 5분이 걸릴 수 있습니다.</p><div class="aa-chips"><button type="button" data-close>확인</button></div>');
    var exportPending=false;
    async function submitExport(){if(exportPending)return;exportPending=true;var input=exportDialog.querySelector('input'),button=exportDialog.querySelector('[data-export-submit]'),password=input.value;input.value='';button.disabled=true;button.textContent='전송 중…';exportDialog.setAttribute('aria-busy','true');exportDialog.querySelector('[data-export-error]').textContent='Drive로 전송 중입니다…';try{await DriveSync.exportWithPassword(password);close(exportDialog);show(exportDoneDialog);status('내보내기 접수됨 · 공통 설정 자동 반영을 기다려 주세요');}catch(e){exportDialog.querySelector('[data-export-error]').textContent=e.message;input.focus();}finally{password='';exportPending=false;button.disabled=false;button.textContent='확인 · 내보내기';exportDialog.removeAttribute('aria-busy');}}
    exportDialog.addEventListener('click',function(e){if(e.target.closest('[data-export-submit]'))submitExport();});
    exportDialog.querySelector('input').addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();submitExport();}});
    exportDialog.addEventListener('close',function(){exportDialog.querySelector('input').value='';exportDialog.querySelector('[data-export-error]').textContent='';});
    confirmDialog=dialog('archive-confirm-dialog','작업 확인','<h2>확인</h2><p></p><div class="aa-chips"><button type="button" data-confirm-no>아니오</button><button type="button" data-confirm-yes>예</button></div>');
    confirmDialog.addEventListener('click',function(e){if(e.target.closest('[data-confirm-no],[data-confirm-yes]')){var resolve=confirmResolve;confirmResolve=null;var yes=!!e.target.closest('[data-confirm-yes]');close(confirmDialog);if(resolve)resolve(yes);}});confirmDialog.addEventListener('close',function(){if(confirmResolve){var resolve=confirmResolve;confirmResolve=null;resolve(false);}});
    if (!bound) {
      document.addEventListener('pointerdown',function(){document.body.classList.remove('using-keyboard');},true);document.addEventListener('keydown',function(e){if(e.key==='Tab')document.body.classList.add('using-keyboard');},true);document.addEventListener('click',function(e){document.querySelectorAll('.html-actions[open]').forEach(function(el){if(!el.contains(e.target))el.open=false;});});document.addEventListener('click',function(e){if(e.target.closest('.archive-highlight-toolbar,dialog')||document.querySelector('dialog[open]'))return;document.querySelectorAll('.article[open]').forEach(function(card){if(!card.contains(e.target))card.open=false;});});document.addEventListener('click', cardClick);
      document.addEventListener('keydown', function (e) { var d = [topicDialog,exportDoneDialog,exportDialog,confirmDialog,renameDialog,imageDialog, memoDialog, bookmarkDialog, readDialog].filter(function (item) { return item && item.hasAttribute('open'); })[0]; if (!d) return;
        if (e.key === 'Escape') { e.preventDefault(); close(d); }
        if (e.key === 'Tab' && typeof d.showModal !== 'function') { var els = d.querySelectorAll('button,textarea,a[href]'), first = els[0], last = els[els.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } }
      });
      var top=document.getElementById('scroll-top-btn'),manage=document.getElementById('archive-manage-btn');manage.onclick=function(){var settings=controls.querySelector('.archive-settings');settings.open=true;settings.scrollIntoView({behavior:'smooth',block:'center'});};window.addEventListener('scroll',function(){top.hidden=window.scrollY<600;manage.hidden=top.hidden;},{passive:true});top.onclick=function(){resetFilters();window.scrollTo({top:0,behavior:'smooth'});};document.getElementById('archive-home').onclick=function(){resetFilters();window.scrollTo({top:0,behavior:'smooth'});};
      bound = true;
    }
    records.forEach(function(r){syncReplies(r.card,r.article);var oldLinks=r.card.querySelector('.url-inline'),newLinks=renderUrlContents(r.article);if(oldLinks){if(newLinks)oldLinks.outerHTML=newLinks;else oldLinks.remove();}else if(newLinks)r.card.insertAdjacentHTML('beforeend',newLinks);r.card.querySelectorAll('.post-media img').forEach(function(img,n){var pair=(r.article.mediaImages||[])[n],raw=Array.isArray(pair)?pair[1]||pair[0]:pair&&(pair.full||pair.url||pair.src||pair.thumb);var mapped=window.ArchiveMediaMap&&window.ArchiveMediaMap[raw];if(raw)img.dataset.full=safeUrl(mapped?new URL(mapped,document.baseURI).href:raw);});var full=r.card.querySelector('.full-text');if(full)full.innerHTML=translatedBody(r.article);formatContent(r.card);var a=r.card.querySelector('.read-link');if(a){var url=canonicalSource(r.article);if(url){a.href=url;a.title=url;a.hidden=false;}else{a.removeAttribute('href');a.hidden=true;}}});
    if(previous){selection=previous.selection;mode=previous.mode;controls.querySelector('input').value=previous.query;controls.querySelector('[data-action="mode"]').textContent=mode;}
    update();if(window.ArchiveHighlights)window.ArchiveHighlights.refresh();if(previous)window.scrollTo(0,previous.scroll);
  }
  function mobilePrintUsesDownload(){
    return !!(navigator.userAgentData&&navigator.userAgentData.mobile)||/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)||(/Macintosh|MacIntel/i.test(navigator.userAgent+' '+navigator.platform)&&navigator.maxTouchPoints>1);
  }
  var desktopPrintCleanup=null;
  async function printDesktopArticle(a,button){
    if(desktopPrintCleanup)desktopPrintCleanup();
    var area=document.createElement('article');area.id='archive-inline-print';area.className='aa-content';area.innerHTML=articleHtml(a);formatContent(area,a);
    document.body.appendChild(area);document.body.classList.add('aa-inline-print');
    var originalLabel=button.textContent;button.disabled=true;button.textContent='인쇄 준비 중…';
    function cleanup(){area.remove();window.removeEventListener('afterprint',cleanup);if(desktopPrintCleanup===cleanup){document.body.classList.remove('aa-inline-print');desktopPrintCleanup=null;}}
    desktopPrintCleanup=cleanup;window.addEventListener('afterprint',cleanup,{once:true});
    try{
      await Promise.all(Array.from(area.querySelectorAll('img')).map(function(img){
        img.loading='eager';
        return new Promise(function(resolve,reject){
          var timer;function done(error){clearTimeout(timer);img.removeEventListener('load',loaded);img.removeEventListener('error',failed);if(error)reject(error);else resolve();}
          function loaded(){done(img.naturalWidth?null:new Error('이미지를 불러오지 못했습니다'));}
          function failed(){done(new Error('이미지를 불러오지 못했습니다'));}
          img.addEventListener('load',loaded,{once:true});img.addEventListener('error',failed,{once:true});timer=setTimeout(function(){done(new Error('이미지 로딩 시간 초과'));},15000);
          if(img.complete)loaded();
        });
      }));
      if(desktopPrintCleanup!==cleanup||!area.isConnected)return;
      window.print();
    }catch(e){var active=desktopPrintCleanup===cleanup;cleanup();if(active)window.alert('인쇄 준비 실패: '+e.message+'. 다시 시도해 주세요.');}
    finally{button.disabled=false;button.textContent=originalLabel;}
  }
  async function runArticleAction(action,r,button,inline){
    var a=r.article,menu=button.closest('.html-actions');if(menu)menu.open=false;
    if(action==='text'){var text=title(a)+'\n\n'+bodyText(a)+'\n\n'+(canonicalSource(a)||'');navigator.clipboard.writeText(text).then(function(){button.textContent='복사 완료';setTimeout(function(){if(button.isConnected)button.textContent='텍스트 복사';},2000);}).catch(function(){download(new Blob([text],{type:'text/plain;charset=utf-8'}),'archive.txt');});}
    if(action==='image')shareImage(a);
    if(action==='html'||action==='html-download')shareHtml(a,action==='html-download');
    if(action==='print'){if(mobilePrintUsesDownload())shareHtml(a,true);else printDesktopArticle(a,button);}
  }
  async function cardClick(e) {
    var duplicateButton=e.target.closest('[data-duplicates]');if(duplicateButton){e.preventDefault();var duplicate=records.find(function(r){return r.id===duplicateButton.dataset.duplicates;});if(duplicate){openRead(duplicate);var info=readDialog.querySelector('.aa-record-info');if(info){info.open=true;info.scrollIntoView({block:'center'});}}return;}
    var searchButton=e.target.closest('[data-search-result]');if(searchButton){e.preventDefault();var found=records.find(function(r){return r.id===searchButton.dataset.searchResult;});if(found){if(found.searchMatch?.kind!=='memo')openRead(found);revealSearchMatch(found);}return;}
    var toolButton=e.target.closest('[data-read-id]');if(toolButton){var tool=records.find(function(r){return r.id===toolButton.dataset.readId;});if(tool)openRead(tool);return;}
    var relatedButton=e.target.closest('[data-related-id]');if(relatedButton){e.preventDefault();var next=records.find(function(r){return r.id===relatedButton.dataset.relatedId;});if(next){ArchiveImages.close();openRead(next);}return;}
    var deleteReply=e.target.closest('[data-delete-reply]');if(deleteReply){e.preventDefault();if(!await askConfirm('이 아카이브에서 댓글을 삭제하시겠습니까? 원본 서비스의 댓글은 삭제되지 않습니다.'))return;var rid=deleteReply.dataset.deleteReply,removed=load('archive-removed-replies',[]);if(removed.indexOf(rid)<0&&persist('archive-removed-replies',removed.concat(rid)))refreshSavedState();return;}
    var replyButton = e.target.closest('.reply-del,[data-hide-reply]');
    if (replyButton) {
      e.preventDefault(); e.stopPropagation();
      var rid = replyButton.dataset.hideReply || replyButton.dataset.replyId;
      var removed = load('archive-deleted-replies', []);
      if (rid && removed.indexOf(rid) < 0 && persist('archive-deleted-replies', removed.concat(rid))) {document.querySelectorAll('[data-reply-id]').forEach(function (r) { if (r.dataset.replyId === rid) r.hidden = true; });update();}
      return;
    }
    var card = e.target.closest('.article'); if (!card) return; var r = records.filter(function (item) { return item.card === card; })[0]; if (!r) return;
    var b = e.target.closest('button');
    if(e.target.closest('.aa-inline-toolbar')&&!b)return;
    if (b) {
      e.preventDefault();
      if(b.hasAttribute('data-topic-edit')){openTopicEditor(r);return;}
      if(b.hasAttribute('data-share')){runArticleAction(b.dataset.share,r,b,true);return;}
      if(b.hasAttribute('data-reader-memo')){memoId=r.card.querySelector('[data-memo-id]').dataset.memoId;memoDialog.querySelector('textarea').value=memoValue(memoId);show(memoDialog);return;}
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
    refreshTopicViews();
    records.forEach(function(r){syncReplies(r.card,r.article);});if(current&&readDialog&&readDialog.open){syncReplies(readDialog.querySelector('.aa-content'),current.article);var panel=document.querySelector('.viewer-text-panel');if(panel)syncReplies(panel,current.article);}

    pins = load('archive-pinned', []); hidden = load('archive-deleted-posts', []);
    bookmarks = load('archive-bookmarks', {});
    if (!Array.isArray(pins)) pins = [];
    if (!Array.isArray(hidden)) hidden = [];
    if (!bookmarks || typeof bookmarks !== 'object' || Array.isArray(bookmarks)) bookmarks = {};
    if (controls) update();
    refreshRecordInformation();
  }
  // replyBody: 테스트용 노출 (text 폴백 검증 — render 테스트에서 사용).
  return { init: init, refreshSavedState: refreshSavedState, sanitizeHtml: cleanHtml, replyBody: replyBody, searchSegments: searchSegments, articleHtml: articleHtml };
}());


window.ArchiveApp = ArchiveApp;




