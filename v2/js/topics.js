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
