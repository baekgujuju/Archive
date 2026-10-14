var ArchiveUI = (function () {
  'use strict';
  var esc = function (s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); };
  function count(records, field) {
    var out = new Map(); records.forEach(function (r) { var values = Array.isArray(r[field]) ? r[field] : [r[field]]; values.filter(Boolean).forEach(function (v) { out.set(v,(out.get(v)||0)+1); }); });
    return Array.from(out).sort(function (a,b) { return b[1]-a[1] || a[0].localeCompare(b[0],'ko'); });
  }
  function chip(label, field, value, number) { return '<button type="button" class="' + (field==='type'?'type-tab':'chip') + '" data-filter="'+field+'" data-value="'+esc(value)+'" aria-label="'+esc(label)+'" aria-pressed="false"><strong>'+esc(label)+'</strong>'+'</button>'; }
  function controls(records, colors, nameOf) {
    var ordinary=records.filter(function(r){return r.type!=='ib';}),topics=count(ordinary,'topics'),people=count(ordinary,'author');
    var morePeople='';
    var peopleHtml=people.slice(0,3).map(function(p){return chip(p[0],'author',p[0],p[1]);}).join('');
    if(people.length>3)morePeople='<details class="all-authors"><summary>전체 인물 ▾</summary><div class="aa-chips">'+people.slice(3).map(function(p){return chip(p[0],'author',p[0],p[1]);}).join('')+'</div></details>';
    return '<div class="search-bar"><div class="aa-search"><input type="search" aria-label="글 검색" placeholder="제목, 본문, 저자, 주제로 검색 (쉼표로 구분)"><button class="search-mode-btn" type="button" data-action="mode" aria-label="검색 조건 OR, 클릭하여 AND로 변경">OR</button><button type="button" class="reset" data-action="reset">전체 보기</button></div></div>'+
      '<details class="filters" aria-label="아카이브 필터"><summary>필터 ▾</summary><div class="filter-line"><span class="label">유형</span><div class="type-tabs aa-chips" role="group" aria-label="유형">'+chip('전체','type','',records.length)+chip('웹글','type','web',records.filter(function(r){return r.type==='web';}).length)+chip('X 게시물','type','x',records.filter(function(r){return r.type==='x';}).length)+chip('IB 주원','type','ib',records.filter(function(r){return r.type==='ib';}).length)+'</div></div>'+
      '<div class="filter-line" data-ordinary-filter><span class="label">주제</span><div class="chips aa-chips" role="group" aria-label="주제">'+chip('전체 주제','topic','')+topics.map(function(t){return chip(t[0],'topic',t[0],t[1]);}).join('')+'</div></div>'+
      '<div class="filter-line" data-ordinary-filter><span class="label">인물</span><div class="author-options"><div class="chips aa-chips" role="group" aria-label="저자">'+chip('전체 인물','author','')+peopleHtml+'</div>'+morePeople+'</div></div>'+
      '<div class="filter-line"><span class="label">마크</span><div class="chips aa-chips" role="group" aria-label="북마크 필터">'+chip('전체','bookmark','')+Object.keys(colors).map(function(c){return '<button type="button" class="chip" data-filter="bookmark" data-value="'+c+'" aria-label="'+esc(nameOf(c))+'" aria-pressed="false" title="길게 눌러 이름 변경" style="border-color:'+c+'">'+esc(nameOf(c))+'</button>';}).join('')+'</div></div></details>'+
      '<section class="spotlight" data-ordinary-filter><h2>인물 스포트라이트</h2><div class="rail">'+people.slice(0,5).map(function(p){return '<button type="button" class="filter-card" data-filter="author" data-value="'+esc(p[0])+'" aria-pressed="false"><span class="avatar">'+esc(p[0].slice(0,2))+'</span><span><strong>'+esc(p[0])+'</strong><small>'+p[1]+'건</small></span></button>';}).join('')+'</div></section>'+
      '<section class="aa-ib-nav" hidden><h2>🎓 IB 주원</h2><div class="aa-chips" role="group" aria-label="IB 과목"></div><div class="aa-chips" role="group" aria-label="IB 분류"></div></section>'+
      '<div class="list-heading archive-reset"><h2>아카이브 탐색</h2><button type="button" class="reset" data-action="reset">초기화 · 전체 보기</button></div><p class="status" data-results-status role="status" aria-live="polite"></p>'+
      '<details class="archive-settings"><summary>보관함 관리</summary><div class="settings-sections"><section class="settings-section"><h3>Drive 동기화</h3><p>내려받기는 자동으로, 내보내기는 비밀번호로 확인합니다.</p><div class="settings-actions"><button type="button" data-action="drive">Drive 연결</button><button type="button" data-action="drive-export">내보내기 🔒</button></div><span data-drive-status role="status"></span></section><section class="settings-section" data-settings-backup><h3>백업 · 복원</h3><p>이 기기의 저장 내용을 파일로 보관하거나 복원합니다.</p></section><section class="settings-section" data-settings-hidden><h3>숨긴 항목</h3><button type="button" data-action="hidden" aria-pressed="false">숨긴 글 보기 / 복원</button></section></div></details>';
  }
  function overview(records) {
    var stats=document.getElementById('archive-stats'); if(!stats)return;
    stats.innerHTML=[['전체',records.length],['웹글',records.filter(function(r){return r.type==='web';}).length],['X 게시물',records.filter(function(r){return r.type==='x';}).length]].map(function(p){return '<div class="stat"><strong>'+p[1]+'건</strong><span>'+p[0]+'</span></div>';}).join('');
    var people=count(records.filter(function(r){return r.type!=='ib';}),'author');document.getElementById('archive-authors').textContent='많이 모은 저자 · '+people.slice(0,3).map(function(p){return p[0]+' '+p[1]+'건';}).join(' · ');
    var topics=count(records.filter(function(r){return r.type!=='ib';}),'topics'),sum=topics.reduce(function(n,t){return n+t[1];},0),palette=['#d98069','#c9b591','#8da9a4','#64758f'];
    document.getElementById('archive-distribution').innerHTML=topics.map(function(t,i){return '<span style="width:'+(t[1]/(sum||1)*100)+'%;background:'+palette[i%4]+'" title="'+esc(t[0])+' '+t[1]+'건"></span>';}).join('');
    document.getElementById('archive-legend').innerHTML=topics.map(function(t,i){return '<span class="legend-item" style="--color:'+palette[i%4]+'">'+esc(t[0])+' '+t[1]+'</span>';}).join('');
  }
  function related(records) {
    var stop=new Set(['the','and','for','with','this','that','from','have','있는','하는','되는','통해','대한','관련','것을','수있']);
    var prepared=records.map(function(r){var words=new Map();((r.article.fullText||r.article.summary||'')+' '+(r.article.title||'')).toLowerCase().match(/[가-힣]{2,}|[a-z]{3,}/g)?.forEach(function(w){if(!stop.has(w))words.set(w,(words.get(w)||0)+1);});return {topics:new Set(r.topics),words:words};});
    records.forEach(function(r,i){r.related=records.map(function(other,j){if(i===j||(r.type==='ib')!==(other.type==='ib'))return null;var p=prepared[i],q=prepared[j],overlap=0,common=0;p.topics.forEach(function(t){if(q.topics.has(t))overlap++;});p.words.forEach(function(n,w){common+=Math.min(n,q.words.get(w)||0);});var score=overlap*10+(r.author&&r.author===other.author?8:0)+Math.min(15,common);return score>5?{record:other,score:score}:null;}).filter(Boolean).sort(function(a,b){return b.score-a.score;}).slice(0,5).map(function(x){return x.record;});});
  }
  return {controls:controls,overview:overview,related:related};
}());

