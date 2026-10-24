var ArchiveUI = (function () {
  'use strict';
  var esc = function (s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); };
  function count(records, field) {
    var out = new Map(); records.forEach(function (r) { var values = Array.isArray(r[field]) ? r[field] : [r[field]]; values.filter(Boolean).forEach(function (v) { out.set(v,(out.get(v)||0)+1); }); });
    return Array.from(out).sort(function (a,b) { return b[1]-a[1] || a[0].localeCompare(b[0],'ko'); });
  }
  function chip(label, field, value, number) { return '<button type="button" class="' + (field==='type'?'type-tab':'chip') + '" data-filter="'+field+'" data-value="'+esc(value)+'" aria-label="'+esc(label)+'" aria-pressed="false"><strong>'+esc(label)+'</strong>'+'</button>'; }
  function controls(records, colors, nameOf) {
    var ordinary=records.filter(function(r){return r.type!=='ib';}),topics=count(ordinary,'topics'),people=count(ordinary,'authorKey');
    function personLabel(key){var r=ordinary.find(function(r){return r.authorKey===key;});return r?r.author:key;}
    var morePeople='';
    var peopleHtml=people.slice(0,3).map(function(p){return chip(personLabel(p[0]),'author',p[0],p[1]);}).join('');
    if(people.length>3)morePeople='<details class="all-authors"><summary>전체 인물 ▾</summary><div class="aa-chips">'+people.slice(3).map(function(p){return chip(personLabel(p[0]),'author',p[0],p[1]);}).join('')+'</div></details>';
    return '<div class="search-bar"><div class="aa-search"><input type="search" aria-label="글 검색" placeholder="본문·댓글·번역·메모 검색 (쉼표로 구분)"><button class="search-mode-btn" type="button" data-action="mode" aria-label="검색 조건 OR, 클릭하여 AND로 변경">OR</button><button type="button" class="reset" data-action="reset">전체 보기</button></div></div>'+
      '<details class="filters" aria-label="아카이브 필터"><summary>필터 ▾</summary><div class="filter-line"><span class="label">자료</span><div class="type-tabs aa-chips" role="group" aria-label="유형">'+chip('아카이브','type','')+chip('IB 주원','type','ib')+chip('AI 스킬','type','ai')+'</div></div>'+
      '<div class="filter-line" data-ordinary-filter><span class="label">주제</span><div class="chips aa-chips" role="group" aria-label="주제">'+chip('전체 주제','topic','')+topics.map(function(t){return chip(t[0],'topic',t[0],t[1]);}).join('')+'</div></div>'+
      '<div class="filter-line" data-ordinary-filter><span class="label">인물</span><div class="author-options"><div class="chips aa-chips" role="group" aria-label="저자">'+chip('전체 인물','author','')+peopleHtml+'</div>'+morePeople+'</div></div>'+
      '<div class="filter-line"><span class="label">마크</span><div class="chips aa-chips" role="group" aria-label="북마크 필터">'+chip('전체','bookmark','')+Object.keys(colors).map(function(c){return '<button type="button" class="chip" data-filter="bookmark" data-value="'+c+'" aria-label="'+esc(nameOf(c))+'" aria-pressed="false" title="길게 눌러 이름 변경" style="border-color:'+c+'">'+esc(nameOf(c))+'</button>';}).join('')+'</div></div>'+(records.some(function(r){return r.metadataReview;})?'<div class="aa-quality-notice"><button type="button" class="reset" data-action="metadata-review" aria-pressed="false" aria-describedby="metadata-review-help">미분류자료 보기</button><p id="metadata-review-help">출처·저자·날짜 정보의 확인이 필요한 자료입니다.</p></div>':'')+'<div class="filter-footer"><button type="button" class="reset filter-close" data-action="filter-close">필터 닫기</button></div></details>'+
      '<section class="spotlight" data-ordinary-filter><h2>인물 스포트라이트</h2><div class="rail">'+people.slice(0,5).map(function(p){return '<button type="button" class="filter-card" data-filter="author" data-value="'+esc(p[0])+'" aria-pressed="false"><span class="avatar">'+esc(personLabel(p[0]).slice(0,2))+'</span><span><strong>'+esc(personLabel(p[0]))+'</strong><small>'+p[1]+'건</small></span></button>';}).join('')+'</div></section>'+
      '<section class="aa-ib-nav" hidden><h2>🎓 IB 주원</h2><div class="aa-chips" role="group" aria-label="IB 과목"></div><div class="aa-chips" role="group" aria-label="IB 분류"></div></section>'+
      '<div class="list-heading archive-reset"><h2>아카이브 탐색</h2><button type="button" class="reset" data-action="reset">초기화 · 전체 보기</button></div><p class="status" data-results-status role="status" aria-live="polite"></p>'+

      '<details class="archive-settings"><summary>삭제한 항목 관리</summary><div class="settings-sections"><section class="settings-section" data-settings-backup hidden><span data-drive-status role="status"></span><small data-drive-detail></small></section><section class="settings-section" data-settings-hidden><h3>글·댓글 복원</h3><button type="button" data-action="hidden" aria-pressed="false">삭제한 글 보기 / 복원</button></section></div></details>';
  }
  function overview(records) {
    var stats=document.getElementById('archive-stats'); if(!stats)return;
    stats.innerHTML=[['전체 자료',records.length]].map(function(p){return '<div class="stat"><strong>'+p[1]+'건</strong><span>'+p[0]+'</span></div>';}).join('');
    var people=count(records.filter(function(r){return r.type!=='ib';}),'author');document.getElementById('archive-authors').textContent='많이 모은 저자 · '+people.slice(0,3).map(function(p){return p[0]+' '+p[1]+'건';}).join(' · ');
    var topics=count(records.filter(function(r){return r.type!=='ib';}),'topics'),sum=topics.reduce(function(n,t){return n+t[1];},0),palette=['#d98069','#c9b591','#8da9a4','#64758f'];
    document.getElementById('archive-distribution').innerHTML=topics.map(function(t,i){return '<span style="width:'+(t[1]/(sum||1)*100)+'%;background:'+palette[i%4]+'" title="'+esc(t[0])+' '+t[1]+'건"></span>';}).join('');
    document.getElementById('archive-legend').innerHTML=topics.map(function(t,i){return '<span class="legend-item" style="--color:'+palette[i%4]+'">'+esc(t[0])+' '+t[1]+'</span>';}).join('');
  }
  function related(records) {
    var stop=new Set(['the','and','for','with','this','that','from','have','있는','하는','되는','통해','대한','관련','것을','수있']);
    var prepared=records.map(function(r){var words=new Map();((r.article.fullText||r.article.summary||'')+' '+(r.article.title||'')).toLowerCase().match(/[가-힣]{2,}|[a-z]{3,}/g)?.forEach(function(w){if(!stop.has(w))words.set(w,(words.get(w)||0)+1);});return {topics:new Set(r.topics),words:words};});
    records.forEach(function(r,i){var seen=new Set();var key=function(other){var source=other.article.sourceUrl||other.article.source_url;if(source){try{var u=new URL(source);if(/(^|\.)blog\.naver\.com$/.test(u.hostname)&&u.searchParams.get('logNo'))return 'naver-blog:'+u.searchParams.get('blogId')+':'+u.searchParams.get('logNo');var cafe=u.pathname.match(/^\/ca-fe\/web\/cafes\/([^/]+)\/articles\/(\d+)/)||u.pathname.match(/^\/([^/]+)\/(\d+)\/?$/);if(/(^|\.)cafe\.naver\.com$/.test(u.hostname)&&cafe)return 'naver-cafe:'+cafe[1]+':'+cafe[2];u.hash='';Array.from(u.searchParams.keys()).forEach(function(k){if(/^(utm_|tc$|si$|proxyReferer$|noTracking)/i.test(k))u.searchParams.delete(k);});return u.href;}catch(e){}}return String(other.article.title||String(other.article.fullText||other.article.summary||'').split('\n')[0].slice(0,90)).replace(/\s+/g,' ').trim().toLowerCase()+'|'+String(other.author||'').trim().toLowerCase();};seen.add(key(r));r.related=records.map(function(other,j){if(i===j||(r.type==='ib')!==(other.type==='ib'))return null;var p=prepared[i],q=prepared[j],overlap=0,common=0;p.topics.forEach(function(t){if(q.topics.has(t))overlap++;});p.words.forEach(function(n,w){common+=Math.min(n,q.words.get(w)||0);});var score=overlap*10+(r.author&&r.author===other.author?8:0)+Math.min(15,common);return score>5?{record:other,score:score}:null;}).filter(Boolean).sort(function(a,b){return b.score-a.score;}).filter(function(x){var k=key(x.record);if(k==='|'||seen.has(k))return false;seen.add(k);return true;}).slice(0,5).map(function(x){return x.record;});});
  }
  return {controls:controls,overview:overview,related:related};
}());

