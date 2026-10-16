/* Text quote anchors: no saved HTML is executed. */
var ArchiveHighlights = (function () {
  'use strict';
  var key='archive-highlights', active=null, toolbar, scheduled=false;
  function state(){try{return JSON.parse(localStorage.getItem(key)||'{}');}catch(e){return {};}}
  function validAnchor(x){return x&&typeof x.exact==='string'&&x.exact.length>0&&x.exact.length<=10000&&typeof x.id==='string'&&x.id.length>0&&x.id.length<=100&&Number.isSafeInteger(x.start)&&x.start>=0&&typeof x.prefix==='string'&&x.prefix.length<=32&&typeof x.suffix==='string'&&x.suffix.length<=32;}
  function anchors(id){try{var a=JSON.parse(state()[id]||'[]');return Array.isArray(a)?a.filter(function(x){return validAnchor(x);}):[];}catch(e){return [];}}
  function locate(text,a){
    if(text.slice(a.start,a.start+a.exact.length)===a.exact&&text.slice(Math.max(0,a.start-32),a.start)===(a.prefix||'')&&text.slice(a.start+a.exact.length,a.start+a.exact.length+32)===(a.suffix||''))return a.start;
    var hits=[],p=text.indexOf(a.exact);while(p>=0){if((!a.prefix||text.slice(Math.max(0,p-a.prefix.length),p)===a.prefix)&&(!a.suffix||text.slice(p+a.exact.length,p+a.exact.length+a.suffix.length)===a.suffix))hits.push(p);p=text.indexOf(a.exact,p+1);}return hits.length===1?hits[0]:-1;
  }
  function nodes(root){var w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),out=[],n;while((n=w.nextNode()))out.push(n);return out;}
  function paint(root){
    root.querySelectorAll('mark[data-archive-highlight]').forEach(function(m){m.replaceWith(document.createTextNode(m.textContent));});root.normalize();
    if(!anchors(root.dataset.highlightPost).length)return;var text=root.textContent, ranges=anchors(root.dataset.highlightPost).map(function(a){var p=locate(text,a);return {start:p,end:p+a.exact.length,id:a.id};}).filter(function(a){return a.start>=0;});
    var offset=0;nodes(root).forEach(function(n){var begin=offset,end=offset+n.length;offset=end;var hits=ranges.filter(function(a){return a.start<end&&a.end>begin;});if(!hits.length)return;
      var cuts=[0,n.length];hits.forEach(function(a){cuts.push(Math.max(0,a.start-begin),Math.min(n.length,a.end-begin));});cuts=Array.from(new Set(cuts)).sort(function(a,b){return a-b;});var frag=document.createDocumentFragment();
      for(var i=0;i<cuts.length-1;i++){var s=cuts[i],e=cuts[i+1],hit=hits.find(function(a){return a.start<begin+e&&a.end>begin+s;}),part=document.createTextNode(n.data.slice(s,e));if(hit){var mark=document.createElement('mark');mark.dataset.archiveHighlight=hit.id;mark.appendChild(part);frag.appendChild(mark);}else frag.appendChild(part);}n.replaceWith(frag);
    });
  }
  function refresh(){document.querySelectorAll('[data-highlight-post]').forEach(paint);}
  function save(id,a){var d=state();d[id]=JSON.stringify(a);localStorage.setItem(key,JSON.stringify(d));refresh();toolbar.hidden=true;active=null;}
  function selection(){var sel=window.getSelection();if(!sel||sel.isCollapsed||!sel.rangeCount)return;var r=sel.getRangeAt(0),el=r.commonAncestorContainer.nodeType===1?r.commonAncestorContainer:r.commonAncestorContainer.parentElement,root=el.closest('[data-highlight-post]');if(!root||!root.contains(r.startContainer)||!root.contains(r.endContainer))return;var before=r.cloneRange();before.selectNodeContents(root);before.setEnd(r.startContainer,r.startOffset);var start=before.toString().length,text=root.textContent,exact=r.toString();if(!exact.trim()||exact.length>10000)return;
    active={root:root,anchor:{id:Date.now().toString(36)+Math.random().toString(36).slice(2,6),start:start,exact:exact,prefix:text.slice(Math.max(0,start-32),start),suffix:text.slice(start+exact.length,start+exact.length+32)}};var host=root.closest('dialog[open]')||document.body;if(toolbar.parentElement!==host)host.appendChild(toolbar);toolbar.hidden=false;
  }
  function init(){toolbar=document.createElement('div');toolbar.className='archive-highlight-toolbar';toolbar.hidden=true;toolbar.innerHTML='<button type="button" data-highlight-add>🖍 형광펜</button><button type="button" data-highlight-remove>표시 지우기</button>';document.body.appendChild(toolbar);
    toolbar.addEventListener('mousedown',function(e){e.preventDefault();});toolbar.addEventListener('click',function(e){if(!active)return;var id=active.root.dataset.highlightPost,a=anchors(id),b=active.anchor;if(e.target.closest('[data-highlight-add]')){a.push(b);save(id,a);}else if(e.target.closest('[data-highlight-remove]'))save(id,a.filter(function(x){var p=locate(active.root.textContent,x);return p<0||p>=b.start+b.exact.length||p+x.exact.length<=b.start;}));window.getSelection().removeAllRanges();});
    document.addEventListener('selectionchange',function(){if(!scheduled){scheduled=true;setTimeout(function(){scheduled=false;selection();},120);}});
    document.addEventListener('pointerdown',function(e){if(!toolbar.contains(e.target)){toolbar.hidden=true;active=null;}});
    refresh();
  }
  if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',init);
  return {locate:locate,refresh:refresh,validAnchor:validAnchor};
})();
