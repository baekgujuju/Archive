/* Pure query matching; all result rendering uses DOM text nodes. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ArchiveSearch = api;
}(typeof window === 'object' ? window : this, function () {
  'use strict';
  function text(value) { return String(value == null ? '' : value).normalize('NFC'); }
  function terms(query) { return text(query).toLowerCase().split(',').map(function (v) { return v.trim(); }).filter(Boolean); }
  function match(segments, words, mode) {
    if (!words.length) return { matched: true, hits: [] };
    var hits = [], found = new Set();
    segments.forEach(function (segment) {
      var lower = text(segment.text).toLowerCase(), matches = [];
      words.forEach(function (word) { if (lower.indexOf(word) >= 0) { found.add(word); matches.push(word); } });
      if (matches.length) hits.push({ segment: segment, words: matches });
    });
    return { matched: mode === 'AND' ? words.every(function (w) { return found.has(w); }) : found.size > 0, hits: hits };
  }
  function excerpt(value, words, size) {
    var source = text(value).replace(/\s+/g, ' ').trim(), lower = source.toLowerCase();
    var at = -1;
    words.forEach(function (word) { var pos = lower.indexOf(word); if (pos >= 0 && (at < 0 || pos < at)) at = pos; });
    var start = Math.max(0, at - 45), end = Math.min(source.length, start + (size || 170));
    return (start ? '…' : '') + source.slice(start, end) + (end < source.length ? '…' : '');
  }
  return { terms: terms, match: match, excerpt: excerpt };
}));
