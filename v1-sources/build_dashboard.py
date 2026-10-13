#!/usr/bin/env python3
"""Render a self-contained offline archive dashboard in Python."""
import argparse
import base64
import hashlib
import html
from collections import Counter
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_POSTS = Path.home() / "workspace/x-bookmarks/posts.json"
DEFAULT_OUTPUT = Path.home() / "workspace/your_files/웹글-아카이브.html"
from ib_section import build_ib_section, IB_CSS, IB_JS
from memo_feature import MEMO_CSS, MEMO_JS, MEMO_MODAL_HTML, memo_button, memo_button_full

HTML = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>아카이브 인사이트</title>
<link href="https://spoqa.github.io/spoqa-han-sans/css/SpoqaHanSansNeo.css" rel="stylesheet">
<script src="https://accounts.google.com/gsi/client" async defer></script>
<style>

:root{color-scheme:light;--paper:#f6f2e9;--ink:#222b3b;--muted:#716e65;--line:#ddd6ca;--accent:#c34127;--soft:#f1e6da;--navy:#18243c}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:10.5pt/1.55 "Spoqa Han Sans Neo","Noto Sans KR",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}button,input{font:inherit}button{cursor:pointer}a{color:inherit;text-decoration:none}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.wrap{max-width:1160px;margin:auto;padding:0 28px}h1,h2,h3{font-family:'Spoqa Han Sans Neo','Noto Sans KR',sans-serif}h1,h2,h3,p{margin:0}header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:22px 0;border-bottom:1px solid var(--line)}.brand{font-weight:800;font-size:19px}.edition,.eyebrow{font-size:10px;letter-spacing:.16em}.edition{color:var(--muted)}.hero{padding:38px 0 28px}.eyebrow{color:var(--accent);font-weight:800}h1{font-size:clamp(38px,6vw,68px);line-height:1.15;letter-spacing:-.05em;margin-top:15px}.intro{color:var(--muted);margin:14px 0 24px}.data-band{padding:26px;border-radius:14px;background:var(--navy);color:white}.stats{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:14px}.stat strong{display:block;font-size:42px;line-height:1.2;font-variant-numeric:tabular-nums;letter-spacing:-.04em}.stat:first-child strong{color:#e7785f}.stat span,.top-authors{font-size:11px;color:#c3ccde}.top-authors{margin-top:18px;overflow-wrap:anywhere}.distribution{margin-top:22px;padding-top:18px;border-top:1px solid #ffffff26;min-width:0}.distribution .edition{color:#c3ccde}.bar{display:flex;height:10px;overflow:hidden;border-radius:8px;margin:14px 0}.legend{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:10px;color:#dce3ed}.legend-item:before{content:"";display:inline-block;width:7px;height:7px;background:var(--color);border-radius:50%;margin-right:5px}.distribution-note{font-size:10px;color:#c3ccde;margin-top:12px}.issue-mark{font:italic 17px Georgia,"Batang","AppleMyungjo",serif;color:var(--accent);margin-top:19px}
.search-bar{position:sticky;top:0;z-index:10;background:#f6f2e9f5;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:12px 0}.search{position:relative}.search span{position:absolute;left:15px;top:7px;font-size:24px;color:var(--muted)}.search-row{display:flex;gap:8px;align-items:center}.search-input{position:relative;flex:1;min-width:0}.search-mode-btn{flex:none;border:1px solid var(--line);border-radius:8px;padding:11px 13px;background:#fffdf8;color:var(--accent);font-size:12px;font-weight:700;min-height:44px;cursor:pointer}.search-mode-btn[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}input{width:100%;border:1px solid var(--line);border-radius:8px;padding:12px 16px 12px 44px;background:#fffdf8;color:var(--ink)}main{padding-top:24px!important;padding-bottom:40px!important}.filters{background:#fffdf8;border:1px solid var(--line);padding:18px;border-radius:12px}.filter-line{display:flex;align-items:baseline;gap:12px}.filter-line+.filter-line{margin-top:14px}.label{width:30px;flex:none;font-size:11px;color:var(--muted)}.chips,.type-tabs{display:flex;flex-wrap:wrap;gap:7px}.chip,.type-tab{border:1px solid var(--line);border-radius:30px;padding:6px 12px;background:transparent;color:var(--ink);font-size:11px}.chip strong{font-weight:500}.chip small{margin-left:6px;color:var(--muted)}.chip[aria-pressed=true],.type-tab[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:white}.chip[aria-pressed=true] small{color:#fff}.spotlight{margin-top:26px}h2{font-size:22px;letter-spacing:-.03em}.spotlight h2{font-size:18px;margin-bottom:12px}.rail{display:flex;gap:10px;overflow-x:auto;padding:4px 4px 12px;scrollbar-width:thin}.rail::-webkit-scrollbar{height:8px}.rail::-webkit-scrollbar-track{background:#efe9dc;border-radius:4px}.rail::-webkit-scrollbar-thumb{background:#c9c2b4;border-radius:4px}.rail::-webkit-scrollbar-thumb:hover{background:#b3ab99}.filter-card{flex:0 0 190px;display:flex;align-items:center;gap:12px;text-align:left;padding:15px;border:1px solid var(--line);border-radius:12px;background:#fffdf8;color:var(--ink)}.filter-card strong{display:block;font-size:12px}.filter-card small{display:block;font-size:10px;color:var(--muted);margin-top:4px}.filter-card[aria-pressed=true]{border-color:var(--accent);background:var(--soft)}.avatar{width:36px;height:36px;flex:none;display:grid;place-items:center;border-radius:50%;background:var(--soft);color:var(--accent);font-size:12px;font-weight:800}.list-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:25px 0 8px}.reset{border:0;background:none;color:var(--accent);padding:7px;text-decoration:underline;text-underline-offset:4px}.status{font-size:11px;color:var(--muted);margin-bottom:18px}.grid{display:grid;gap:14px}.article{display:flex;flex-direction:column;min-width:0;padding:23px;border:1px solid var(--line);border-radius:12px;background:#fffdf8;overflow-wrap:anywhere;transition:transform .18s,border-color .18s}.card-toggle{display:block;cursor:pointer;list-style:none;position:relative}.card-toggle::-webkit-details-marker{display:none}.card-toggle:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.full-text{display:none;white-space:pre-wrap;margin:0 0 18px;padding-top:14px;border-top:1px solid var(--line)}.article[open] .full-text{display:block}.article[open] .summary{display:none}.article[open]{align-self:start}.rail .chip{flex:none;align-self:center}.rail.people-expanded{flex-wrap:wrap;overflow:visible}.article:hover{transform:translateY(-3px);border-color:var(--accent)}.type-badge{font-size:10px;font-weight:800;color:var(--accent);background:var(--soft);padding:4px 8px;border-radius:5px}
.card-header{display:flex;justify-content:space-between;align-items:center;gap:8px}
.card-actions{display:flex;align-items:center;gap:6px}
.card-actions .pin-btn,.card-actions .bookmark-btn,.card-actions .memo-icon-btn{position:static;opacity:.4;padding:2px}
.card-actions .pin-btn:hover,.card-actions .bookmark-btn:hover,.card-actions .memo-icon-btn:hover{opacity:1}
.card-actions .memo-icon-btn.memo-hidden{display:none}.article h3{font-size:17px;line-height:1.45;margin:10px 0 8px;letter-spacing:-.02em;font-weight:600}.handle{display:block;font:11px/1.55 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;color:var(--muted);margin-top:5px}.meta{font-size:10px;color:var(--muted)}.summary{margin:14px 0 18px;color:var(--muted);white-space:pre-line;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:16px}.tag{font-size:10px;padding:3px 8px;color:var(--tag-ink);background:var(--tag-paper);border-radius:4px}.byline{display:flex;justify-content:space-between;align-items:baseline;gap:12px;border-top:1px solid var(--line);padding-top:14px;margin-top:auto;font-size:10px}.author{font-weight:700}.read-link{color:var(--accent);flex:none}
.read-window-btn{border:1px solid var(--line);background:transparent;color:var(--accent);border-radius:6px;padding:4px 10px;font-size:11px;cursor:pointer;margin-right:8px}
.read-window-btn:hover{background:var(--soft)}
/* 글 읽기 모달 */
#article-reader-modal{display:none;position:fixed;inset:0;z-index:10000}
.reader-overlay{position:absolute;inset:0;background:rgba(10,15,24,.7);display:flex;align-items:center;justify-content:center;padding:20px}
.reader-content{position:relative;background:#fffdf8;border-radius:12px;max-width:800px;width:100%;max-height:90vh;overflow-y:auto;padding:40px 32px}
.reader-close{position:absolute;top:12px;right:16px;border:none;background:none;font-size:32px;cursor:pointer;color:#666}
/* 우측 플로팅 홈(맨 위로) 버튼 */
#scroll-top-btn{position:fixed;right:24px;bottom:32px;width:52px;height:52px;border-radius:50%;border:1px solid var(--line);background:var(--paper);color:var(--accent);font-size:22px;cursor:pointer;box-shadow:0 2px 12px rgba(0,0,0,.15);z-index:999;display:none;align-items:center;justify-content:center}
#scroll-top-btn:hover{background:var(--soft)}
#scroll-top-btn.show{display:flex}
/* 우측 플로팅 백업 버튼 (홈 버튼 위) */
#export-float-btn{position:fixed;right:24px;bottom:96px;width:52px;height:52px;border-radius:50%;border:1px solid var(--line);background:var(--paper);color:var(--ink);font-size:22px;cursor:pointer;box-shadow:0 2px 12px rgba(0,0,0,.15);z-index:999;display:none;align-items:center;justify-content:center}
#export-float-btn:hover{background:var(--soft)}
#export-float-btn.show{display:flex}
#import-float-btn{position:fixed;right:24px;bottom:160px;width:52px;height:52px;border-radius:50%;border:1px solid var(--line);background:var(--paper);color:var(--ink);font-size:22px;cursor:pointer;box-shadow:0 2px 12px rgba(0,0,0,.15);z-index:999;display:none;align-items:center;justify-content:center}
#import-float-btn:hover{background:var(--soft)}
#import-float-btn.show{display:flex}
#drive-float-btn{position:fixed;right:24px;bottom:224px;width:52px;height:52px;border-radius:50%;border:1px solid var(--line);background:var(--paper);color:var(--ink);font-size:22px;cursor:pointer;box-shadow:0 2px 12px rgba(0,0,0,.15);z-index:999;display:none;align-items:center;justify-content:center}
#drive-float-btn:hover{background:var(--soft)}
#drive-float-btn.show{display:flex}
.reader-body h1{font-size:26px;margin-bottom:8px}
.reader-meta{color:#888;font-size:13px;margin-bottom:24px}
.reader-text{white-space:pre-wrap;font-size:16px;line-height:1.8;margin-bottom:24px}
.reader-text a{color:#1a73e8;text-decoration:none;overflow-wrap:anywhere}.reader-text a:hover{text-decoration:underline;text-underline-offset:3px}
/* 모달 본문 내 인라인 이미지: .full-text 래퍼가 .reader-text로 바뀌므로 별도 규칙 필요 */
.reader-text figure{max-width:100%;margin:16px auto}
.reader-text img{display:block;max-width:100%;height:auto;margin:16px auto;border-radius:8px}
#article-reader-modal.img-expanded .reader-text-panel .reader-text figure{max-width:100%;margin:12px auto}
#article-reader-modal.img-expanded .reader-text-panel .reader-text img{display:block;max-width:100%;height:auto;margin:10px auto;border-radius:6px}
.full-text{white-space:pre-wrap;margin:0 0 18px;padding-top:14px;border-top:1px solid var(--line);overflow-wrap:anywhere;max-width:100%;overflow:hidden}
.full-text a{color:#1a73e8;text-decoration:none;overflow-wrap:anywhere}.full-text a:hover{text-decoration:underline;text-underline-offset:3px}
.full-text img{display:block;max-width:100%!important;width:100%!important;height:auto!important;margin:16px auto;border-radius:8px;object-fit:contain}
.full-text figure{max-width:100%!important;width:100%!important;margin:16px auto;overflow:hidden}
.full-text figure img{width:100%!important}
/* 웹글 서식 (모바일 v17과 동일): 제목-저자 상하 배치, 번호 항목 서식, 자연스러운 문단 흐름 */
.full-text.web-html{white-space:normal;overflow-wrap:break-word}
.full-text.web-html img{cursor:zoom-in}
.full-text.web-html header.hero{display:block;padding:0 0 10px}
.full-text.web-html header.hero .badge{display:inline-block;font-size:11px;font-weight:800;color:var(--accent);background:var(--soft);padding:4px 10px;border-radius:6px;margin-bottom:10px}
.full-text.web-html header.hero h1{font-size:26px;line-height:1.35;letter-spacing:-.02em;margin:0 0 10px}
.full-text.web-html header.hero p.sub{font-size:13px;color:var(--muted);line-height:1.7;margin:0}
.full-text.web-html h2.sec{font-size:20px;margin:1.8em 0 .7em}
.full-text.web-html p{margin:0 0 1em}
.full-text.web-html h3.stage{font-size:18px;line-height:1.5;margin:1.4em 0 .6em}
.full-text.web-html .stage-num::after{content:". "}
.full-text.web-html .phase-tag{display:block;font-size:12px;color:var(--muted);font-weight:400;margin-top:4px}
.full-text.web-html ul.points{margin:.6em 0 1em;padding-left:1.3em}
.full-text.web-html ul.points li{margin:.35em 0}
.full-text.web-html blockquote.original{margin:.9em 0;padding:.7em 1em;border-left:3px solid var(--line);color:var(--muted);font-size:.92em}
.full-text.web-html blockquote.original .org-label{display:inline-block;font-size:11px;font-weight:800;color:var(--accent);margin-right:.5em}
.full-text.web-html figure.chart{margin:1.2em 0}
.full-text.web-html figcaption{font-size:12px;color:var(--muted);text-align:center;margin-top:6px}
/* 웹글 표: 얇은 회색 테두리 */
.full-text.web-html table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.92em}
.full-text.web-html th,.full-text.web-html td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
.full-text.web-html th{background:var(--soft);font-weight:800}
.full-text.web-html thead th{border-bottom:2px solid var(--line)}
.full-text.web-html .flow{margin:1em 0}
.full-text.web-html .phase-head{display:block;font-size:12px;font-weight:800;color:var(--accent);letter-spacing:.1em;margin:1.2em 0 .4em}
.full-text.web-html .flow-step{display:block;margin:10px 0}
.full-text.web-html .flow-dot{display:inline}
.full-text.web-html .flow-dot>span{font-weight:800}
.full-text.web-html .flow-dot>span::after{content:". "}
.full-text.web-html .flow-body{display:inline}
.full-text.web-html .flow-body b{margin-right:.25em}
.full-text.web-html .flow-body small{display:block;margin-top:3px;color:var(--muted);font-size:.92em}
/* PC 모달(.reader-text)용 웹글 서식: .full-text.web-html 규칙을 미러링 (간격은 더 compact하게) */
.reader-text.web-html{white-space:normal;overflow-wrap:break-word;line-height:1.7}
.reader-text.web-html header.hero{display:block;padding:0 0 10px}
.reader-text.web-html header.hero .badge{display:inline-block;font-size:11px;font-weight:800;color:var(--accent);background:var(--soft);padding:4px 10px;border-radius:6px;margin-bottom:10px}
.reader-text.web-html header.hero h1{font-size:26px;line-height:1.35;letter-spacing:-.02em;margin:0 0 10px}
.reader-text.web-html header.hero p.sub{font-size:13px;color:var(--muted);line-height:1.7;margin:0}
.reader-text.web-html h2.sec{font-size:20px;margin:1.2em 0 .5em}
.reader-text.web-html p{margin:0 0 .6em}
.reader-text.web-html h3.stage{font-size:18px;line-height:1.5;margin:1em 0 .4em}
.reader-text.web-html .stage-num::after{content:". "}
.reader-text.web-html .phase-tag{display:block;font-size:12px;color:var(--muted);font-weight:400;margin-top:4px}
.reader-text.web-html ul.points{margin:.4em 0 .8em;padding-left:1.3em}
.reader-text.web-html ul.points li{margin:.25em 0}
.reader-text.web-html blockquote.original{margin:.7em 0;padding:.6em 1em;border-left:3px solid var(--line);color:var(--muted);font-size:.92em}
.reader-text.web-html blockquote.original .org-label{display:inline-block;font-size:11px;font-weight:800;color:var(--accent);margin-right:.5em}
.reader-text.web-html figure.chart{margin:.8em 0}
.reader-text.web-html figcaption{font-size:12px;color:var(--muted);text-align:center;margin-top:6px}
/* 웹글 표: 얇은 회색 테두리 (모달) */
.reader-text.web-html table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.92em}
.reader-text.web-html th,.reader-text.web-html td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
.reader-text.web-html th{background:var(--soft);font-weight:800}
.reader-text.web-html thead th{border-bottom:2px solid var(--line)}
.reader-text.web-html .flow{margin:.6em 0}
.reader-text.web-html .phase-head{display:block;font-size:12px;font-weight:800;color:var(--accent);letter-spacing:.1em;margin:.8em 0 .3em}
.reader-text.web-html .flow-step{display:block;margin:3px 0}
.reader-text.web-html .flow-line{margin:0;line-height:1.4}
.reader-text.web-html .flow-dot{display:inline}
.reader-text.web-html .flow-dot>span{font-weight:800}
.reader-text.web-html .flow-dot>span::after{content:". "}
.reader-text.web-html .flow-body{display:inline}
.reader-text.web-html .flow-body b{margin-right:.25em}
.reader-text.web-html .flow-body small{display:block;margin-top:2px;color:var(--muted);font-size:.92em}
.reader-images img{display:block;max-width:100%;height:auto;margin:20px auto;border-radius:8px;cursor:zoom-in}
.reader-images{overflow:auto;max-width:100%}
/* 이미지 확대: 모달이 거의 전체화면으로 커지면서 좌측 글 + 우측 큰 이미지 */
#article-reader-modal.img-expanded .reader-content{max-width:96vw;width:96vw;height:94vh;max-height:94vh;padding:0;display:flex;overflow:hidden}
#article-reader-modal.img-expanded .reader-body{display:flex;width:100%;height:100%;min-height:0}
#article-reader-modal.img-expanded .reader-text-panel{width:400px;flex:none;overflow-y:auto;padding:32px 24px;border-left:1px solid #e0d8c8;background:#fffdf8;order:2}
#article-reader-modal.img-expanded .reader-text-panel h1{font-size:20px;margin-bottom:8px}
#article-reader-modal.img-expanded .reader-text-panel .reader-meta{font-size:12px;margin-bottom:16px}
#article-reader-modal.img-expanded .reader-text-panel .reader-text{font-size:14px;line-height:1.7;margin-bottom:16px}
#article-reader-modal.img-expanded .reader-text-panel .reader-images img{max-width:100%;margin:10px auto;cursor:pointer;border:2px solid transparent}
#article-reader-modal.img-expanded .reader-text-panel .reader-images img.active{border-color:var(--accent)}
#article-reader-modal.img-expanded .reader-image-panel{flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;padding:24px;background:#141414;position:relative}
#article-reader-modal.img-expanded .reader-image-panel img{width:100%;height:100%;object-fit:contain;border-radius:4px;cursor:zoom-out}
#article-reader-modal.img-expanded .reader-close{z-index:10;background:#fffdf8;border-radius:50%;width:40px;height:40px;display:flex;align-items:center;justify-content:center}
.reader-author{font-weight:700;margin-top:24px;padding-top:16px;border-top:1px solid #ddd}
/* 연관 게시물 */
.url-inline{margin-top:16px;padding:14px 16px;background:#f4f7f4;border:1px solid var(--line);border-radius:8px}.url-inline-head{font-size:12px;font-weight:800;color:var(--muted);margin-bottom:10px}.url-inline-item{padding:10px 0;border-top:1px solid var(--line)}.url-inline-item:first-of-type{border-top:none;padding-top:0}.url-inline-title{font-size:13px;font-weight:700}.url-inline-title a{color:var(--ink);text-decoration:none}.url-inline-title a:hover{color:var(--accent);text-decoration:underline}.url-inline-url{font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin-top:2px}.url-inline-content{font-size:12.5px;color:#3c4043;margin-top:8px;white-space:pre-wrap;line-height:1.65;overflow-wrap:break-word}.related-section{margin-top:20px;padding:16px;background:#f8f6f0;border-radius:8px;border:1px solid var(--line)}
.related-section h4{font-size:14px;margin:0 0 10px}
.related-list{list-style:none;padding:0;margin:0}
.related-list li{padding:6px 0;font-size:13px;border-bottom:1px solid #e8e0d0}
.related-list li:last-child{border-bottom:none}
.related-list a{color:var(--ink);text-decoration:none}
.related-list a:hover{color:var(--accent);text-decoration:underline}
/* 링크된 외부 URL 내용 */
/* 상단 고정 핀 */
.pin-btn{position:absolute;top:22px;right:16px;border:none;background:none;cursor:pointer;opacity:.4;padding:4px;z-index:5}
.pin-btn:hover{opacity:1}
.pin-btn.pinned{opacity:1}
.pin-btn svg{width:18px;height:18px;fill:none;stroke:#888;stroke-width:2}
.pin-btn.pinned svg{fill:#e74c3c;stroke:#e74c3c}
details.article{position:relative}
#pinned-section{margin-bottom:24px}
#pinned-section h2{font-size:16px;margin:0 0 12px}
#pinned-section:empty{display:none}
#pinned-section .pinned-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:20px}
#bookmarked-section{margin-bottom:0}
#bookmarked-section h2{display:none}
#bookmarked-section:empty{display:none}
/* 컬러 북마크 */
.bookmark-btn{position:absolute;top:22px;right:44px;border:none;background:none;cursor:pointer;opacity:.4;padding:4px;z-index:5}
.bookmark-btn:hover{opacity:1}
.bookmark-btn.bookmarked{opacity:1}
.bookmark-btn svg{width:18px;height:18px;fill:none;stroke:#888;stroke-width:2}
.bookmark-btn.bookmarked svg{fill:var(--bkm-color,#888);stroke:var(--bkm-color,#888)}
.delete-btn{position:absolute;top:22px;right:72px;border:none;background:none;cursor:pointer;opacity:.4;padding:4px;z-index:5}
.delete-btn:hover{opacity:1}
.delete-btn svg{width:18px;height:18px;fill:none;stroke:#888;stroke-width:2}
.bookmark-colors{position:absolute;display:flex;gap:8px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px;z-index:10;box-shadow:0 2px 12px rgba(0,0,0,.15)}
.bookmark-colors button{border:none;background:none;cursor:pointer;padding:4px}
.bookmark-colors button svg{width:28px;height:28px;fill:var(--c);stroke:var(--c);stroke-width:2}
.bookmark-colors button:hover{transform:scale(1.15)}
.bookmark-colors button.selected{outline:2px solid #333;outline-offset:2px;border-radius:4px}
.bookmark-colors .clear-btn{width:auto;height:32px;border-radius:14px;padding:0 12px;font-size:12px;background:#f0f0f0;border:1px solid #ddd}
details.article[data-bookmark-color]{border-left:4px solid var(--bkm-color,transparent)}
#bookmarked-section{margin-bottom:24px}
#bookmarked-section h2{font-size:16px;margin:0 0 12px}
#bookmarked-section:empty{display:none}
.bookmark-filter{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.bookmark-filter button{width:32px;height:32px;border-radius:50%;border:2px solid transparent;cursor:pointer;opacity:.5}
.bookmark-filter button.active{opacity:1;border-color:#333}
.bookmark-filter button.all{width:auto;border-radius:16px;padding:0 12px;font-size:13px;background:#f0f0f0;border:1px solid #ddd;opacity:1}
/* AI 도구 목록 */
.ai-tools-section{margin-top:40px;padding:24px;background:#f8f6f0;border-radius:12px;border:1px solid var(--line)}
.ai-tools-section h2{font-size:18px;margin-bottom:16px}
.ai-tools-list{list-style:none;padding:0;margin:0}
.ai-tools-list li{padding:8px 0;border-bottom:1px solid #e8e0d0;font-size:14px}
.ai-tools-list li:last-child{border-bottom:none}
.ai-tools-list a{color:var(--ink);text-decoration:none}
.ai-tools-list a:hover{color:var(--accent);text-decoration:underline}
.ai-author{color:var(--muted);font-size:12px}.featured{border-top:3px solid var(--accent)}.featured h3{font-size:17px}.spotlight-label{font-size:9px;letter-spacing:.15em;color:var(--accent);margin-bottom:12px}.empty{text-align:center;padding:40px;border:1px dashed var(--line);color:var(--muted)}footer{display:flex;justify-content:space-between;gap:16px;margin-top:36px;border-top:1px solid var(--line);padding-top:20px;font-size:10px;color:var(--muted)}[hidden]{display:none!important}
@media(min-width:760px){.wrap{padding-left:40px;padding-right:40px}.data-band{display:grid;grid-template-columns:1.2fr 1fr;gap:32px}.distribution{margin:0;border-top:0;border-left:1px solid #ffffff26;padding:0 0 0 28px}.grid{grid-template-columns:repeat(3,minmax(0,1fr))}.featured{grid-column:span 1}}
@media(max-width:540px){.wrap{padding-left:18px;padding-right:18px}.hero{padding-top:28px}.data-band{padding:20px}.stat strong{font-size:30px}.stat span{font-size:10px}.article{padding:20px}.featured h3{font-size:26px}footer{flex-direction:column}.byline{flex-wrap:wrap}}
@media(prefers-reduced-motion:reduce){.article{transition:none}.article:hover{transform:none}}
.post-media{display:none;margin:0 0 18px}.article[open] .post-media{display:grid;gap:12px}.post-media img{display:block;max-width:100%;height:auto;margin:0 auto;border-radius:6px}
/* Body-level targets avoid transformed cards and work without JavaScript. */
.media-link{display:block;cursor:zoom-in}
.css-lightbox{display:none;position:fixed;inset:0;z-index:9999;width:100%;height:100%;height:100dvh;background:rgba(10,15,24,.95);color:white;padding:64px 12px 48px;overflow:hidden}
.css-lightbox:target{display:block}
.css-lightbox-stage{height:100%;overflow:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;touch-action:pan-x pan-y pinch-zoom}
.css-lightbox img{display:block;width:auto;height:auto;max-width:100%;max-height:100%;object-fit:contain;margin:auto}
.css-lightbox-close{position:absolute;top:max(12px,env(safe-area-inset-top));right:20px;color:white;font-size:32px;line-height:40px;min-width:44px;text-align:center}
.css-lightbox-toggle{position:absolute;top:24px;left:20px}
.css-lightbox label{position:absolute;top:20px;left:48px;cursor:pointer}
.css-lightbox-toggle:checked~.css-lightbox-stage img{max-width:none;max-height:none;margin:0}
.css-lightbox-help{position:absolute;bottom:max(12px,env(safe-area-inset-bottom));left:12px;right:12px;text-align:center;font-size:11px}
@media print{.css-lightbox{display:none!important}}
/* Image lightbox: added only when JavaScript is available. */
.post-media img[data-lightbox]{cursor:zoom-in}
.lightbox{position:fixed;inset:0;width:100vw;height:100vh;height:100dvh;max-width:none;max-height:none;margin:0;padding:0;border:0;background:rgba(10,15,24,.94);color:white;overflow:hidden;overscroll-behavior:contain}
.lightbox::backdrop{background:rgba(10,15,24,.94)}
.lightbox-stage{position:absolute;inset:56px 16px 44px;display:flex;align-items:center;justify-content:center;overflow:hidden;touch-action:none;user-select:none}
.lightbox-image{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;transform-origin:center;touch-action:none;cursor:grab;user-select:none;-webkit-user-drag:none}
.lightbox-image:active{cursor:grabbing}
.lightbox-close{position:absolute;top:10px;right:16px;width:40px;height:40px;border:1px solid #ffffff66;border-radius:50%;background:var(--navy);color:white;font-size:28px;line-height:1;z-index:1}
.lightbox-help{position:absolute;bottom:12px;left:16px;right:16px;text-align:center;color:#c3ccde;font-size:11px;pointer-events:none}
@media print{.lightbox,.lightbox::backdrop,.lightbox-stage,.lightbox-image,.lightbox-close,.lightbox-help{display:none!important}}
.reader-actions{display:flex;gap:8px;margin-bottom:12px}.reader-actions button{border:1px solid var(--line);border-radius:6px;padding:8px 14px;background:#fffdf8;font-size:13px;cursor:pointer}.reader-actions button:hover{background:var(--soft)}@media print{#article-reader-modal{display:block!important;position:static}#article-reader-modal .reader-overlay{background:none}#article-reader-modal .reader-close,#article-reader-modal .reader-actions{display:none!important}}
@page { margin: 14mm 12mm; }
@media print { body { zoom: 0.5; } body{background:white;font-size:10.5pt;line-height:1.55;print-color-adjust:exact;-webkit-print-color-adjust:exact}.wrap{max-width:none}.search-bar,.filters,.reset{display:none}.rail{overflow:visible;flex-wrap:wrap}.grid{grid-template-columns:repeat(3,minmax(0,1fr))}.featured{grid-column:span 1}.article{break-inside:avoid;transform:none;box-shadow:none}.article[open] .summary,.summary{display:block;overflow:visible}.full-text{display:none!important}.post-media,.article[open] .post-media{display:block}.post-media img{max-width:60%;max-height:250px;object-fit:contain;margin:12px auto;break-inside:avoid}.data-band{break-inside:avoid}header,footer{position:static}}
/* 인용글 섹션 */
.quoted{margin-top:16px;border:1px solid var(--line);border-radius:8px;background:#f4f6fa;border-left:3px solid var(--accent)}
.quoted summary{padding:12px 16px;font-weight:700;font-size:13px;cursor:pointer;list-style:none}
.quoted summary::-webkit-details-marker{display:none}
.quoted-body{padding:4px 16px 12px}
.quoted-author{font-weight:700;font-size:12px}
.quoted-time{color:var(--muted);font-size:11px;margin-left:8px}
.quoted-text{font-size:13px;margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.quoted-media{margin-top:8px;display:grid;gap:8px}
.quoted-media img{max-width:100%;height:auto;border-radius:6px}
/* IB 주원 섹션 */
.ib-juwon-section{margin:20px 0;padding:20px;border:1px solid var(--line);border-radius:12px;background:#fffdf8}
.ib-juwon-section h2{font-size:20px;margin:0 0 8px}
.ib-intro{font-size:13px;color:var(--muted);margin:0 0 16px}
.ib-subject{margin-bottom:12px;border:1px solid var(--line);border-radius:8px;background:#fff}
.ib-subject summary{padding:12px 16px;cursor:pointer;font-weight:700;display:flex;justify-content:space-between;align-items:center;gap:8px}
.ib-subject-title{font-size:15px}
.ib-subject-sub{font-size:11px;color:var(--muted);font-weight:400}
.ib-link-list{list-style:none;margin:0;padding:0 16px 12px}
.ib-link-item{padding:8px 0;border-top:1px solid var(--line);font-size:13px;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:baseline}
.ib-link-item:first-child{border-top:0}
.ib-link-item a{color:#1a73e8;text-decoration:none;font-weight:600}
.ib-link-item a:hover{text-decoration:underline}
.ib-link-desc{color:var(--muted);font-size:12px}
.ib-exam-tag{display:inline-block;font-size:10px;font-weight:700;color:#fff;background:var(--accent);padding:2px 8px;border-radius:10px;margin-right:4px}
.ib-doc-name{font-weight:600}
__IB_CSS__
__MEMO_CSS__
</style>
<script>__IB_JS__</script>
<script>__MEMO_JS__</script>
</head>
<body>
<div class="wrap">
<section class="hero" aria-label="아카이브 통계"><p class="eyebrow">THE ARCHIVE / VOL. 001</p><h1>아카이브 인사이트</h1><p class="intro">시장의 흐름부터 새로운 도구까지. 다시 꺼내 읽을 가치가 있는 기록들.</p>
<div class="data-band"><div><div class="stats" id="stats">__STATS__</div><p class="top-authors" id="top-authors">__TOP_AUTHORS__</p></div><div class="distribution"><div class="edition">TOPIC DISTRIBUTION</div><div class="bar" id="distribution-bar" role="img" aria-label="__DISTRIBUTION_LABEL__">__DISTRIBUTION_BAR__</div><div class="legend" id="legend">__LEGEND__</div><p class="distribution-note">모든 기록의 주제 태그 기준 · 복수 주제 포함</p></div></div><p class="issue-mark">Collected with curiosity.</p></section></div>
<div class="search-bar"><div class="wrap"><div class="search search-row"><div class="search-input"><span aria-hidden="true">⌕</span><input id="search" type="search" aria-label="제목·본문·저자·주제 검색" placeholder="제목, 본문, 저자, 주제로 검색" autocomplete="off"></div><button type="button" id="search-mode" class="search-mode-btn" aria-pressed="false" title="검색 방식 전환: OR/AND">OR</button></div></div></div>
<main class="wrap"><section class="filters" aria-label="아카이브 필터"><div class="filter-line"><span class="label" id="types-label">유형</span><div class="type-tabs" id="types" role="group" aria-labelledby="types-label">__TYPES__</div></div><div class="filter-line"><span class="label" id="topics-label">주제</span><div class="chips" id="topics" role="group" aria-labelledby="topics-label">__TOPICS__</div></div><div class="filter-line"><span class="label" id="people-label">인물</span><div class="chips" id="people" role="group" aria-labelledby="people-label">__PEOPLE__</div></div><div class="filter-line"><span class="label" id="bookmark-label">마크</span><div class="chips" id="bookmark-names" role="group" aria-labelledby="bookmark-label"></div></div></section>
<section class="spotlight" aria-labelledby="spotlight-title"><h2 id="spotlight-title">인물 스포트라이트</h2><div class="rail" id="spotlight">__SPOTLIGHT__</div></section>
<section aria-labelledby="latest-title"><div class="list-heading"><h2 id="latest-title">아카이브 탐색</h2><button class="reset" id="reset" type="button">초기화 · 전체 보기</button></div><p class="status" id="status" role="status" aria-live="polite">__STATUS__</p><div id="pinned-section"></div><div class="grid" id="articles">__ARTICLES__</div>__IB_JUWON__<div class="empty" id="empty" hidden>일치하는 글이 없습니다. 검색어나 필터를 바꿔보세요.</div></section>
__AI_TOOLS__
<div class="sync-settings" style="margin-top:24px;padding:16px;border:1px solid var(--line);border-radius:8px;background:#fffdf8"><details><summary style="cursor:pointer;font-weight:700">백업 · 복원 ▾</summary><p style="font-size:12px;color:var(--muted);margin:8px 0">핀·북마크·이름·숨긴 댓글을 JSON 파일로 내보내거나 가져옵니다. 모바일 아카이브와 호환됩니다.</p><button id="export-btn" type="button" style="margin-right:8px;padding:8px 16px;border:1px solid var(--line);border-radius:6px;background:#fffdf8">내보내기</button><button id="import-btn" type="button" style="padding:8px 16px;border:1px solid var(--line);border-radius:6px;background:#fffdf8">가져오기</button><input id="import-file" type="file" accept="application/json,.json" hidden></details></div>
<footer><span>스크랩 아카이브 · 나의 작은 서재</span><span>읽고, 모으고, 다시 발견하는 곳.</span></footer><noscript>모든 글을 펼쳐 읽을 수 있습니다. 검색과 필터는 JavaScript를 활성화하면 사용할 수 있습니다.</noscript></main>
__LIGHTBOXES__
<script>
const state = {type:null, topic:null, author:null, query:'', searchMode:'OR'};
const el = id => document.getElementById(id);
const articles = Array.from(document.querySelectorAll('details.article'));
let deletedPosts;try{deletedPosts=JSON.parse(localStorage.getItem('archive-deleted-posts')||'[]');}catch(e){deletedPosts=[];}
if(!Array.isArray(deletedPosts)||deletedPosts.some(x=>typeof x!=='string'))deletedPosts=[];
document.addEventListener('click',event=>{const del=event.target.closest('.delete-btn');if(!del)return;event.preventDefault();event.stopPropagation();const card=del.closest('details.article');if(!card)return;const pid=card.dataset.pinId;if(!pid||deletedPosts.includes(pid))return;if(!confirm('이 게시글을 삭제하시겠습니까? 복원할 수 없습니다.'))return;deletedPosts.push(pid);try{localStorage.setItem('archive-deleted-posts',JSON.stringify(deletedPosts));}catch(e){}render();document.dispatchEvent(new Event('archive-post-deleted'));});
function render(){
 const query=state.query.trim();
 const keywords=query.split(',').map(k=>k.trim().toLocaleLowerCase()).filter(Boolean);
 const andMode=state.searchMode==='AND';
 const isIb=state.type==='ib';
 const ibSection=el('ib-juwon');
 let ibCount=0;
 if(ibSection){
  ibSection.hidden=!isIb;
  if(isIb){
   for(const subj of ibSection.querySelectorAll('.ib-subject')){
    let subjCount=0;
    const qfilter=node=>{
     const text=node.textContent.toLocaleLowerCase();
     const qmatch=!keywords.length||(andMode?keywords.every(k=>text.includes(k)):keywords.some(k=>text.includes(k)));
     node.hidden=!qmatch;
     if(qmatch){subjCount++;ibCount++;}
    };
    subj.querySelectorAll('.ib-card').forEach(qfilter);
    subj.querySelectorAll('.ib-exam-item').forEach(qfilter);
    subj.hidden=subjCount===0;
    if(subjCount>0&&keywords.length)subj.open=true;
   }
  }
 }
 let count=0;
 for(const card of articles){
  const text=card.textContent.toLocaleLowerCase();
  const qmatch=!keywords.length||(andMode?keywords.every(k=>text.includes(k)):keywords.some(k=>text.includes(k)));
  const matches=!deletedPosts.includes(card.dataset.pinId)&&!isIb&&(!state.type||card.dataset.type===state.type)&&
   (!state.topic||card.dataset.topics.split('\n').includes(state.topic))&&
   (!state.author||card.dataset.author===state.author)&&
   qmatch;
  card.hidden=!matches;
  card.classList.toggle('featured',matches&&count===0);
  card.querySelector('.spotlight-label').hidden=!(matches&&count===0);
  if(matches)count++;
 }
 for(const button of document.querySelectorAll('button[data-kind]'))
  button.setAttribute('aria-pressed',String((state[button.dataset.kind]||'')===button.dataset.value));
 const active=[state.type==='x'?'X 게시물':state.type==='web'?'웹글':state.type==='ib'?'IB 주원':null,state.topic,state.author].filter(Boolean);
 el('status').textContent=isIb?`IB 주원 · 학업 자료실 · ${ibCount}건${query?' · 검색: '+state.query:''}`:`${count}건${active.length?' · '+active.join(' / '):''}${query?' · 검색: '+state.query:''}`;
 el('empty').hidden=isIb||count!==0;
}
for(const button of document.querySelectorAll('button[data-kind]'))button.addEventListener('click',()=>{
 const {kind,value}=button.dataset;
 state[kind]=state[kind]===value?null:value||null;
 render();
});
for(const toggle of document.querySelectorAll('[data-people-toggle]'))toggle.addEventListener('click',()=>{
 const expanded=toggle.getAttribute('aria-expanded')!=='true';
 toggle.setAttribute('aria-expanded',String(expanded));
 const container=toggle.parentElement;
 container.querySelectorAll('[data-extra-person]').forEach(button=>{button.hidden=!expanded;});
 container.classList.toggle('people-expanded',expanded);
});
el('search').addEventListener('input',event=>{state.query=event.target.value;render();});
el('search-mode').addEventListener('click',()=>{state.searchMode=state.searchMode==='AND'?'OR':'AND';const b=el('search-mode');b.textContent=state.searchMode;b.setAttribute('aria-pressed',String(state.searchMode==='AND'));render();});
el('reset').addEventListener('click',()=>{state.type=null;state.topic=null;state.author=null;state.query='';state.searchMode='OR';el('search').value='';const mb=el('search-mode');if(mb){mb.textContent='OR';mb.setAttribute('aria-pressed','false');}render();});
// Keep SSR images intact; create the modal only as a progressive enhancement.
(() => {
 if(!window.HTMLDialogElement||!HTMLDialogElement.prototype.showModal||!window.PointerEvent)return;
 const modal=document.createElement('dialog');
 modal.className='lightbox';
 modal.setAttribute('aria-label','게시물 이미지 확대');
 modal.innerHTML='<button type="button" class="lightbox-close" aria-label="이미지 닫기">×</button><div class="lightbox-stage"><img class="lightbox-image" alt="" draggable="false"></div><p class="lightbox-help">핀치 또는 휠로 확대 · 드래그로 이동 · ESC로 닫기</p>';
 document.body.append(modal);
 const stage=modal.querySelector('.lightbox-stage');
 const image=modal.querySelector('.lightbox-image');
 const close=modal.querySelector('.lightbox-close');
 const pointers=new Map();
 let scale=1,x=0,y=0,gesture=null,opener=null,previousOverflow='';
 let dragged=false,startPoint=null,startedOnBackground=false;
 function paint(){image.style.transform=`translate(${x}px,${y}px) scale(${scale})`;}
 function zoom(next,point){
  next=Math.min(12,Math.max(1,next));
  const rect=stage.getBoundingClientRect();
  const px=point.x-(rect.left+rect.width/2),py=point.y-(rect.top+rect.height/2);
  const ratio=next/scale;
  x=px-(px-x)*ratio;y=py-(py-y)*ratio;scale=next;
  if(scale===1){x=0;y=0;}
  paint();
 }
 function sample(){
  const points=Array.from(pointers.values());
  if(!points.length)return null;
  const a=points[0],b=points[1]||a;
  return {x:(a.x+b.x)/2,y:(a.y+b.y)/2,distance:Math.hypot(a.x-b.x,a.y-b.y)};
 }
 function open(source){
  opener=source;scale=1;x=0;y=0;dragged=false;pointers.clear();gesture=null;
  image.src=document.querySelector(source.hash+' img').src;image.alt=source.querySelector('img').alt;paint();
  previousOverflow=document.body.style.overflow;
  modal.showModal();document.body.style.overflow='hidden';close.focus();
 }
 function dismiss(){modal.close();}
 modal.addEventListener('close',()=>{
  document.body.style.overflow=previousOverflow;
  pointers.clear();gesture=null;image.removeAttribute('src');
  if(opener&&opener.isConnected)opener.focus({preventScroll:true});
 });
 close.addEventListener('click',dismiss);
 // Native dialog handles ESC and traps focus; keep cleanup on its close event.
 modal.addEventListener('click',event=>{
  if(!dragged&&(event.target===modal||(event.target===stage&&startedOnBackground)))dismiss();
 });
 stage.addEventListener('wheel',event=>{
  event.preventDefault();
  const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?stage.clientHeight:1);
  zoom(scale*Math.exp(-Math.max(-100,Math.min(100,delta))*.002),{x:event.clientX,y:event.clientY});
 },{passive:false});
 stage.addEventListener('pointerdown',event=>{
  if(event.pointerType==='mouse'&&event.button!==0)return;
  dragged=false;startedOnBackground=event.target===stage;
  startPoint={x:event.clientX,y:event.clientY};
  pointers.set(event.pointerId,startPoint);gesture=sample();
  stage.setPointerCapture(event.pointerId);
 });
 stage.addEventListener('pointermove',event=>{
  if(!pointers.has(event.pointerId))return;
  const point={x:event.clientX,y:event.clientY};
  pointers.set(event.pointerId,point);
  if(pointers.size>1||Math.hypot(point.x-startPoint.x,point.y-startPoint.y)>5)dragged=true;
  const next=sample();
  if(gesture){
   if(pointers.size>1&&gesture.distance>0)zoom(scale*next.distance/gesture.distance,gesture);
   if(scale>1){x+=next.x-gesture.x;y+=next.y-gesture.y;paint();}
  }
  gesture=next;
 });
 function release(event){pointers.delete(event.pointerId);gesture=sample();}
 stage.addEventListener('pointerup',release);
 stage.addEventListener('pointercancel',release);
 stage.addEventListener('lostpointercapture',release);
 /* CSS :target lightbox handles .media-link clicks; JS interception disabled */
})();
/* 글 클릭 시 자동으로 읽기 모달 열기 (PC 전용) */
document.querySelectorAll('details.article summary.card-toggle').forEach(summary=>{
 summary.addEventListener('click',event=>{
  // 핀/북마크 처리 직후(700ms 이내)의 클릭은 무시 (DOM 이동 시 이벤트 재지정 방지)
  if(typeof lastPinBkmAt!=='undefined'&&Date.now()-lastPinBkmAt<700)return;
  // 핀/북마크 버튼 클릭은 모달 열지 않음
  if(event.target.closest('.pin-btn,.bookmark-btn,.bookmark-colors,.delete-btn'))return;
  event.preventDefault();
  const card=summary.closest('details.article');
  const btn=card.querySelector('.read-window-btn');
  if(btn)btn.click();
 });
});
/* 연관 게시물 클릭: 읽기 모달로 바로 열기 */
document.addEventListener('click',event=>{
  const link=event.target.closest('.related-link');
  if(!link)return;
  event.preventDefault();event.stopPropagation();
  const targetIdx=link.getAttribute('data-target-idx');
  const target=document.querySelector(`details.article[data-idx="${targetIdx}"]`);
  if(target){
   const btn=target.querySelector('.read-window-btn');
   if(btn)btn.click();
  }
});
/* 새 창에서 읽기: 페이지 내 모달로 정상 비율 표시 (아티팩트 뷰어 호환) */
document.querySelectorAll('.read-window-btn').forEach(btn=>{
 btn.addEventListener('click',event=>{
  event.stopPropagation();
  const card=btn.closest('details.article');
  if(!card)return;
  const title=card.querySelector('h3')?.textContent||'';
  const memoBtn=card.querySelector('.memo-btn');
  const memoId=memoBtn?.dataset.memoId||card.dataset.pinId||'';
  const meta=card.querySelector('.meta')?.textContent||'';
  const author=card.querySelector('.byline .author')?.textContent||'';
  const fullTextEl=card.querySelector('.full-text');
  const fullText=fullTextEl?.innerHTML||'';
  const isWebHtml=fullTextEl?.classList.contains('web-html')?' web-html':'';
  const mediaSrc=card.querySelector('.post-media')?.innerHTML||'';
  const relatedSrc=card.querySelector('.related-section')?.outerHTML||'';
  // 썸네일 대신 라이트박스의 원본 이미지를 사용하도록 교체
  const mediaTmp=document.createElement('div');
  mediaTmp.innerHTML=mediaSrc;
  mediaTmp.querySelectorAll('a.media-link').forEach(a=>{
   const lbId=a.getAttribute('href')?.slice(1);
   const lb=lbId?document.getElementById(lbId):null;
   const fullImg=lb?.querySelector('.css-lightbox-stage img');
   const thumb=a.querySelector('img');
   if(fullImg&&thumb){
    thumb.src=fullImg.src;
    // 링크 래퍼 제거하고 이미지만 남김 (클릭은 확대 토글용)
    a.replaceWith(thumb);
   }
  });
  const media=mediaTmp.innerHTML;
  let modal=document.getElementById('article-reader-modal');
  if(!modal){
   modal=document.createElement('div');
   modal.id='article-reader-modal';
   modal.innerHTML=`<div class="reader-overlay"><div class="reader-content"><button class="reader-close" aria-label="닫기">×</button><div class="reader-body"></div></div></div>`;
   document.body.appendChild(modal);
   modal.querySelector('.reader-close').addEventListener('click',()=>{modal.style.display='none';document.body.style.overflow='';});
   modal.querySelector('.reader-overlay').addEventListener('click',e=>{if(e.target.classList.contains('reader-overlay')){modal.style.display='none';document.body.style.overflow='';}});
   document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modal.style.display==='block'){modal.style.display='none';document.body.style.overflow='';}});
  }
  const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const memoBtnHtml=memoId?`<button type="button" class="memo-btn memo-text-btn" data-memo-id="${esc(memoId)}" data-memo-title="${esc(title.slice(0,50))}">📝 메모</button>`:'';
  modal.querySelector('.reader-body').innerHTML=`<div class="reader-actions"><button type="button" class="reader-print" aria-label="인쇄" title="인쇄">🖨️ 인쇄</button><button type="button" class="reader-share-image" aria-label="이미지로 공유" title="이미지로 공유">🖼️ 이미지</button><button type="button" class="reader-share-text" aria-label="HTML로 공유" title="HTML로 공유">🌐 HTML</button></div><h1>${esc(title)}</h1>${memoBtnHtml}<div class="reader-meta">${esc(meta)}</div><div class="reader-text${isWebHtml}">${fullText}</div><div class="reader-images">${media}</div>${relatedSrc}<div class="reader-author">${esc(author)}</div>`;
  if(memoId)updateMemoBtn(memoId);
  /* 읽기 화면: 인쇄/내보내기/공유 */
  (function(){
   const body=modal.querySelector('.reader-body');
   const getData=()=>({title:title,author:author,date:(body.querySelector('.reader-meta')||{}).innerText||'',text:(body.querySelector('.reader-text')||{}).innerText||'',imgs:Array.from(body.querySelectorAll('img')).map(i=>i.src).filter(Boolean)});
   const escHtmlPc=(s)=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
   const shareAsHtmlPc=(d)=>{const meta=[d.author,d.date].filter(Boolean).join(' · ');const metaHtml=meta?'<div class="meta">'+escHtmlPc(meta)+'</div>':'';const html='<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escHtmlPc(d.title)+'</title><style>body{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;max-width:700px;margin:0 auto;padding:20px;line-height:1.7;color:#222;}h1{font-size:1.4em;border-bottom:2px solid #222;padding-bottom:10px;}img{max-width:100%;height:auto;margin:10px 0;}.meta{color:#888;font-size:0.85em;margin-bottom:20px;}</style></head><body><h1>'+escHtmlPc(d.title)+'</h1>'+metaHtml+'<div>'+escHtmlPc(d.text).replace(/\n/g,'<br>')+'</div>'+d.imgs.map(u=>'<img src="'+escHtmlPc(u)+'">').join('')+'<hr><p><a href="'+escHtmlPc(location.href)+'">원본 보기</a></p></body></html>';const blob=new Blob([html],{type:'text/html'});const fname=d.title.slice(0,30).replace(/[\\/:*?"<>|]/g,'')+'.html';const file=new File([blob],fname,{type:'text/html'});if(navigator.share&&navigator.canShare&&navigator.canShare({files:[file]})){navigator.share({files:[file],title:d.title}).catch(()=>{});}else{const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),5000);toastPc('HTML로 저장했습니다');}};
   const wrapTextPc=(ctx,text,maxW)=>{const out=[];const paras=text.trim().split('\n');let prevEmpty=false;paras.forEach((para)=>{const isEmpty=!para.trim();if(isEmpty){if(!prevEmpty&&out.length>0)out.push('');prevEmpty=true;return;}prevEmpty=false;const words=para.split(/\s+/);let line='';const pushLine=()=>{if(line){out.push(line);line='';}};words.forEach(w=>{if(!w)return;if(ctx.measureText(w).width>maxW){pushLine();let part='';for(const ch of w){const t=part+ch;if(ctx.measureText(t).width>maxW&&part){out.push(part);part=ch;}else{part=t;}}if(part)out.push(part);return;}const t=line?line+' '+w:w;if(ctx.measureText(t).width>maxW){pushLine();line=w;}else{line=t;}});pushLine();});return out;};
   const shareAsImagePc=(d)=>{toastPc('이미지 생성 중...');const canvas=document.createElement('canvas');const ctx=canvas.getContext('2d');const W=800;const pad=40;const MAX_BODY_LINES=60;ctx.font='bold 28px -apple-system,"Apple SD Gothic Neo",sans-serif';const titleLines=wrapTextPc(ctx,d.title,W-pad*2);ctx.font='20px -apple-system,"Apple SD Gothic Neo",sans-serif';let bodyLines=wrapTextPc(ctx,d.text,W-pad*2);if(bodyLines.length>MAX_BODY_LINES){bodyLines=bodyLines.slice(0,MAX_BODY_LINES);bodyLines.push('... (이하 생략)');}const lineH=34;canvas.width=W;canvas.height=pad*2+titleLines.length*40+20+bodyLines.length*lineH+60;ctx.fillStyle='#f6f2e9';ctx.fillRect(0,0,canvas.width,canvas.height);let y=pad+30;ctx.fillStyle='#222b3b';ctx.font='bold 28px -apple-system,"Apple SD Gothic Neo",sans-serif';titleLines.forEach(l=>{ctx.fillText(l,pad,y);y+=40;});y+=10;ctx.strokeStyle='#ddd6ca';ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();y+=30;ctx.font='20px -apple-system,"Apple SD Gothic Neo",sans-serif';bodyLines.forEach(l=>{ctx.fillText(l,pad,y);y+=lineH;});canvas.toBlob(blob=>{if(!blob){toastPc('이미지 생성 실패');return;}const file=new File([blob],'archive-post.png',{type:'image/png'});if(navigator.share&&navigator.canShare&&navigator.canShare({files:[file]})){navigator.share({files:[file],title:d.title}).catch(()=>{});}else{const fname=d.title.slice(0,30).replace(/[\\/:*?"<>|]/g,'')+'.png';const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),5000);toastPc('이미지로 저장했습니다');}},'image/png');};
   const toastPc=m=>{let t=document.getElementById('pc-toast');if(!t){t=document.createElement('div');t.id='pc-toast';t.style.cssText='position:fixed;bottom:24px;left:50%;transform:translateX(-50%);padding:10px 18px;background:#18243c;color:#fff;border-radius:8px;z-index:100000;font-size:14px';document.body.appendChild(t);}t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',2000);};
   const bp=body.querySelector('.reader-print');if(bp)bp.onclick=()=>window.print();
   const bs=body.querySelector('.reader-share-text');if(bs)bs.onclick=()=>{const d=getData();if(!d.title)return;shareAsHtmlPc(d);};
   const bsi=body.querySelector('.reader-share-image');if(bsi)bsi.onclick=()=>{const d=getData();if(!d.title)return;shareAsImagePc(d);};  })();
  modal.classList.remove('img-expanded');
  // 이미지 클릭: 분할 화면으로 확대 (좌측 큰 이미지 + 우측 글 패널)
  // 주의: .reader-image-panel 안의 큰 이미지는 제외 (복귀 핸들러가 덮어써지지 않도록)
  const attachImgHandlers=()=>{
   modal.querySelectorAll('.reader-body img:not(.reader-image-panel img)').forEach(img=>{
    img.style.cursor='zoom-in';
    img.onclick=(event)=>{
     event.preventDefault();event.stopPropagation();
     if(modal.classList.contains('img-expanded')){
      // 이미 확대 상태: 썸네일 클릭이면 우측 이미지 교체
      const panel=modal.querySelector('.reader-image-panel img');
      if(panel&&img!==panel){
       panel.src=img.src;
       modal.querySelectorAll('.reader-text-panel .reader-images img').forEach(t=>t.classList.remove('active'));
       img.classList.add('active');
      }
      return;
     }
     // 분할 화면으로 전환
     const body=modal.querySelector('.reader-body');
     const origHTML=body.innerHTML;
     body.dataset.orig=origHTML;
     const clickedSrc=img.getAttribute('src')||img.src;
     // 모든 이미지 수집 (인라인 + 별도)
     const allImgs=[...body.querySelectorAll('img')].map(i=>i.getAttribute('src')||i.src);
     const thumbs=allImgs.map(s=>`<img src="${esc(s)}" class="${s===clickedSrc?'active':''}">`).join('');
     body.innerHTML=`<div class="reader-image-panel"></div><div class="reader-text-panel"><h1>${esc(title)}</h1><div class="reader-meta">${esc(meta)}</div><div class="reader-text${isWebHtml}">${fullText}</div><div class="reader-images">${thumbs}</div>${relatedSrc}<div class="reader-author">${esc(author)}</div></div>`;
     // 이미지 패널에 클릭한 이미지를 복제해 직접 추가 (src 문자열 문제 원천 차단)
     const panelImg=img.cloneNode(false);
     panelImg.removeAttribute('style');
     panelImg.removeAttribute('class');
     panelImg.style.cssText='width:100%;height:100%;object-fit:contain;border-radius:4px;cursor:zoom-out';
     modal.querySelector('.reader-image-panel').appendChild(panelImg);
     modal.classList.add('img-expanded');
     // 우측 큰 이미지 클릭하면 원위치
     modal.querySelector('.reader-image-panel img').onclick=(e)=>{e.stopPropagation();restoreModal();};
     // 좌측 썸네일 클릭 핸들러
     attachImgHandlers();
    };
   });
  };
  const restoreModal=()=>{
   const body=modal.querySelector('.reader-body');
   if(body.dataset.orig){body.innerHTML=body.dataset.orig;delete body.dataset.orig;}
   modal.classList.remove('img-expanded');
   attachImgHandlers();
  };
  // ESC로도 원위치 (확대 상태에서)
  document.addEventListener('keydown',e=>{
   if(e.key==='Escape'&&modal.classList.contains('img-expanded')){restoreModal();}
  });
  attachImgHandlers();
  modal.style.display='block';
  document.body.style.overflow='hidden';
 });
});
/* 플로팅 홈 버튼: 스크롤 시 표시, 클릭 시 맨 위로 */
(function(){
 const btn=document.createElement('button');
 btn.id='scroll-top-btn';btn.setAttribute('aria-label','맨 위로');btn.innerHTML='↑';
 document.body.appendChild(btn);
 btn.addEventListener('click',()=>{window.scrollTo({top:0,behavior:'smooth'});});
 window.addEventListener('scroll',()=>{
  btn.classList.toggle('show',window.scrollY>600);
 },{passive:true});
})();
/* 상단 고정 핀: localStorage에 저장, 고정된 글은 맨 위 섹션으로 이동 */
(function(){
 const KEY='archive-pinned';
 const getPinned=()=>{try{return JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){return[]}};
 const setPinned=v=>{try{localStorage.setItem(KEY,JSON.stringify(v))}catch(e){}};
 const pinnedSection=document.getElementById('pinned-section');
 const articlesGrid=document.getElementById('articles');
 const origParent=new Map(), origNext=new Map();
 const renderPinned=()=>{
  const pinned=getPinned().filter(pid=>!deletedPosts.includes(pid));
  pinnedSection.querySelectorAll('details.article').forEach(card=>{
   const pid=card.getAttribute('data-pin-id');
   const parent=origParent.get(pid), next=origNext.get(pid);
   if(parent){parent.insertBefore(card,next)}
  });
  pinnedSection.innerHTML='';
  if(!pinned.length)return;
  const h=document.createElement('h2');h.textContent='📌 고정된 글';
  pinnedSection.appendChild(h);
  const grid=document.createElement('div');grid.className='pinned-grid';
  pinnedSection.appendChild(grid);
  pinned.forEach(pid=>{
   const card=articlesGrid.querySelector(`details.article[data-pin-id="${CSS.escape(pid)}"]`);
   if(card){
    if(!origParent.has(pid)){origParent.set(pid,card.parentNode);origNext.set(pid,card.nextSibling)}
    grid.appendChild(card);
    card.querySelector('.pin-btn')?.classList.add('pinned');
   }
  });
  articlesGrid.querySelectorAll('details.article .pin-btn').forEach(b=>{
   const pid=b.closest('details.article').getAttribute('data-pin-id');
   b.classList.toggle('pinned',pinned.includes(pid));
  });
 };
 let lastPinBkmAt=0; // 핀/북마크 처리 후 700ms 이내 카드 클릭 무시 (모바일 v16과 동일)
 document.addEventListener('click',e=>{
  const pinBtn=e.target.closest('.pin-btn');
  if(!pinBtn)return;
  e.preventDefault();e.stopPropagation();lastPinBkmAt=Date.now();
  const card=pinBtn.closest('details.article');
  const pid=card.getAttribute('data-pin-id');
  let pinned=getPinned();
  if(pinned.includes(pid)){pinned=pinned.filter(x=>x!==pid)}
  else{pinned.push(pid)}
  setPinned(pinned);
  renderPinned();
 });
 document.addEventListener('archive-post-deleted',renderPinned);
 renderPinned();
})();
/* 컬러 북마크: 7색 선택, localStorage 저장, 색상별 필터 */
(function(){
 const KEY='archive-bookmarks';
 const COLORS=[
  {name:'빨강',code:'#e74c3c'},
  {name:'주황',code:'#e67e22'},
  {name:'노랑',code:'#f1c40f'},
  {name:'초록',code:'#27ae60'},
  {name:'파랑',code:'#3498db'},
  {name:'남색',code:'#34495e'},
  {name:'보라',code:'#9b59b6'}
 ];
 const getBM=()=>{try{return JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){return{}}};
 const setBM=v=>{try{localStorage.setItem(KEY,JSON.stringify(v))}catch(e){}};
 const articlesGrid=document.getElementById('articles');
 const origParent=new Map(), origNext=new Map();
 const renderBM=()=>{
  const bm=getBM();
  // 모든 카드의 북마크 표시 업데이트 (이동하지 않음)
  document.querySelectorAll('details.article').forEach(card=>{
   const pid=card.getAttribute('data-pin-id');
   const color=bm[pid];
   const btn=card.querySelector('.bookmark-btn');
   if(color){
    card.setAttribute('data-bookmark-color',color);
    card.style.setProperty('--bkm-color',color);
    if(btn){btn.classList.add('bookmarked');btn.style.setProperty('--bkm-color',color)}
   }else{
    card.removeAttribute('data-bookmark-color');
    card.style.removeProperty('--bkm-color');
    if(btn){btn.classList.remove('bookmarked');btn.style.removeProperty('--bkm-color')}
   }
  });
  const ids=Object.keys(bm);
 };
 // 북마크 버튼 클릭 → 색상 선택 팝업
 let openPopup=null;
 document.addEventListener('click',e=>{
  // 팝업 외부 클릭 시 닫기
  if(openPopup&&!openPopup.hidden&&!openPopup.contains(e.target)&&!e.target.closest('.bookmark-btn')){
   openPopup.hidden=true;openPopup=null;
  }
  const bkmBtn=e.target.closest('.bookmark-btn');
  if(!bkmBtn)return;
  e.preventDefault();e.stopPropagation();lastPinBkmAt=Date.now();
  const card=bkmBtn.closest('details.article');
  const pid=card.getAttribute('data-pin-id');
  // 기존 팝업 제거
  document.querySelectorAll('.bookmark-colors').forEach(p=>p.remove());
  // 새 팝업 생성 (body에 고정 위치)
  const popup=document.createElement('div');
  popup.className='bookmark-colors';popup.hidden=false;
  const rect=bkmBtn.getBoundingClientRect();
  popup.style.position='fixed';
  popup.style.top=(rect.bottom+6)+'px';
  popup.style.left=Math.max(8,(rect.right-220))+'px';
  popup.style.zIndex='10001';
  COLORS.forEach(c=>{
   const b=document.createElement('button');
   b.style.setProperty('--c',c.code);b.title=c.name;b.dataset.color=c.code;
   b.setAttribute('aria-label',c.name);
   b.innerHTML='<svg viewBox="0 0 24 24"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg>';
   const bm=getBM();
   if(bm[pid]===c.code)b.classList.add('selected');
   popup.appendChild(b);
  });
  const clear=document.createElement('button');
  clear.className='clear-btn';clear.textContent='해제';
  popup.appendChild(clear);
  document.body.appendChild(popup);
  openPopup=popup;
  popup.addEventListener('click',ev=>{
   ev.stopPropagation();
   const cb=ev.target.closest('button[data-color]');
   const clr=ev.target.closest('.clear-btn');
   let cur=getBM();
   if(cb){cur[pid]=cb.dataset.color}
   else if(clr){delete cur[pid]}
   else return;
   setBM(cur);popup.remove();openPopup=null;renderBM();
  });
 });
 /* 차트 이미지 자동 초록 북마크 (사용자 지정 색상이 없을 때만) */
 (function(){const GREEN='#27ae60';const bm=getBM();let changed=false;document.querySelectorAll('details.article[data-chart="1"]').forEach(card=>{const pid=card.getAttribute('data-pin-id');if(pid&&!bm[pid]){bm[pid]=GREEN;changed=true;}});if(changed)setBM(bm);})();
 renderBM();
})();
/* 북마크 색상 이름 지정 UI (필터 영역) */
(function(){
 const KEY='archive-bookmark-names';
 const COLORS=[
  {name:'빨강',code:'#e74c3c'},
  {name:'주황',code:'#e67e22'},
  {name:'노랑',code:'#f1c40f'},
  {name:'초록',code:'#27ae60'},
  {name:'파랑',code:'#3498db'},
  {name:'남색',code:'#34495e'},
  {name:'보라',code:'#9b59b6'}
 ];
 const getNames=()=>{try{return JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){return{}}};
 const setNames=v=>{try{localStorage.setItem(KEY,JSON.stringify(v))}catch(e){}};
 const container=document.getElementById('bookmark-names');
 if(!container)return;
 const sortByBookmark=(color)=>{
  const grid=document.getElementById('articles');
  if(!grid)return;
  if(!grid._origOrder){
   grid._origOrder=Array.from(grid.querySelectorAll('details.article'));
  }
  // 이전 숨김 방식의 잔재 제거
  document.querySelectorAll('details.article').forEach(card=>{card.style.display='';});
  if(!color){
   const current=Array.from(grid.querySelectorAll('details.article'));
   const orig=grid._origOrder.filter(card=>current.includes(card));
   const rest=current.filter(card=>!orig.includes(card));
   orig.concat(rest).forEach(card=>grid.appendChild(card));
  }else{
   const cards=Array.from(grid.querySelectorAll('details.article'));
   const matching=cards.filter(card=>(card.getAttribute('data-bookmark-color')||'')===color);
   const others=cards.filter(card=>(card.getAttribute('data-bookmark-color')||'')!==color);
   matching.forEach(card=>grid.appendChild(card));
   others.forEach(card=>grid.appendChild(card));
  }
 };
 const render=()=>{
  const names=getNames();
  container.innerHTML='';
  // "전체" 버튼: 원래 정렬로 복귀
  const allChip=document.createElement('button');
  allChip.className='chip';allChip.type='button';
  allChip.dataset.color='';
  allChip.setAttribute('aria-pressed','true');
  allChip.innerHTML=`<svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:#888;stroke:#888;stroke-width:2;vertical-align:middle;margin-right:6px"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg><strong>전체</strong>`;
  allChip.title='클릭: 원래 정렬로 복귀';
  allChip.addEventListener('click',()=>{
   container.querySelectorAll('.chip').forEach(x=>x.setAttribute('aria-pressed','false'));
   allChip.setAttribute('aria-pressed','true');
   sortByBookmark('');
  });
  container.appendChild(allChip);
  COLORS.forEach(c=>{
   const chip=document.createElement('button');
   chip.className='chip';chip.type='button';
   chip.dataset.color=c.code;
   const label=names[c.code]||c.name;
   const escLabel=label.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
   chip.innerHTML=`<svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:${c.code};stroke:${c.code};stroke-width:2;vertical-align:middle;margin-right:6px"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg><strong>${escLabel}</strong>`;
   chip.title='클릭: 해당 색상 먼저 정렬, 더블클릭: 이름 변경';
   chip.addEventListener('click',()=>{
    // 정렬 토글 (숨기지 않음)
    const active=chip.getAttribute('aria-pressed')==='true';
    container.querySelectorAll('.chip').forEach(x=>x.setAttribute('aria-pressed','false'));
    if(active){
     allChip.setAttribute('aria-pressed','true');
     sortByBookmark('');
    }else{
     chip.setAttribute('aria-pressed','true');
     sortByBookmark(c.code);
    }
   });
   chip.addEventListener('dblclick',e=>{
    e.preventDefault();
    const cur=names[c.code]||c.name;
    const nv=prompt(`"${c.name}" 북마크의 이름을 정해주세요:`,cur);
    if(nv!==null&&nv.trim()){
     const n=getNames();n[c.code]=nv.trim();setNames(n);render();
    }
   });
   container.appendChild(chip);
  });
 };
 render();
})();
/* 백업 · 복원 (모바일과 호환) */
(function(){
 const COLORS=['#e74c3c','#e67e22','#f1c40f','#27ae60','#3498db','#34495e','#9b59b6'];
 const get=(k,d)=>{try{const v=JSON.parse(localStorage.getItem(k));return v==null?d:v}catch(e){return d}};
 const set=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){alert('저장 실패: 브라우저 설정을 확인하세요')}};
 const exBtn=document.getElementById('export-btn');
 const imBtn=document.getElementById('import-btn');
 const imFile=document.getElementById('import-file');
 if(!exBtn||!imBtn||!imFile)return;
 exBtn.addEventListener('click',()=>{
  const data={
   pinned:get('archive-pinned',[]),
   bookmarks:get('archive-bookmarks',{}),
   names:get('archive-bookmark-names',{}),
   deletedReplies:get('archive-deleted-replies',[]),deletedPosts:get('archive-deleted-posts',[]),
   exportedAt:new Date().toISOString()
  };
  const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download='archive-bookmarks.json';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
 });
 imBtn.addEventListener('click',()=>imFile.click());
 imFile.addEventListener('change',async e=>{
  try{
   const f=e.target.files[0];if(!f)return;
   const d=JSON.parse(await f.text());
   const nms=d.names||d.bookmarkNames;
   if(!Array.isArray(d.pinned)||d.pinned.some(x=>typeof x!=='string'))throw Error('pinned 형식 오류');
   if(!d.bookmarks||typeof d.bookmarks!=='object'||Array.isArray(d.bookmarks))throw Error('bookmarks 형식 오류');
   if(Object.values(d.bookmarks).some(x=>!COLORS.includes(x)))throw Error('북마크 색상 코드 오류');
   if(!nms||typeof nms!=='object'||Array.isArray(nms))throw Error('names 형식 오류');
   if(Object.entries(nms).some(([k,v])=>!COLORS.includes(k)||typeof v!=='string'))throw Error('이름 형식 오류');
   const dr=d.deletedReplies||[];
   const dp=d.deletedPosts||[];
   if(!Array.isArray(dp)||dp.some(x=>typeof x!=='string'))throw Error('deletedPosts 형식 오류');
   if(!Array.isArray(dr)||dr.some(x=>typeof x!=='string'))throw Error('deletedReplies 형식 오류');
   if(!confirm('현재 핀·북마크·이름·삭제된 댓글/게시글을 가져온 파일로 덮어쓸까요?'))return;
   set('archive-pinned',[...new Set(d.pinned)]);
   set('archive-bookmarks',d.bookmarks);
   set('archive-bookmark-names',nms);
   set('archive-deleted-replies',[...new Set(dr)]);
   set('archive-deleted-posts',[...new Set(dp)]);
   alert('가져오기 완료. 페이지를 새로고침합니다.');
   location.reload();
  }catch(err){alert('가져오기 실패: '+err.message)}
  finally{e.target.value=''}
 });
 // 플로팅 백업 버튼: 내보내기/가져오기 두 개, 마우스 오버 시 설명, 클릭 시 바로 동작
 const expFloat=document.createElement('button');
 expFloat.id='export-float-btn';expFloat.type='button';
 expFloat.innerHTML='💾';expFloat.title='북마크·핀 백업 내보내기';
 expFloat.setAttribute('aria-label','북마크·핀 백업 내보내기');
 document.body.appendChild(expFloat);
 expFloat.addEventListener('click',()=>exBtn.click());
 const impFloat=document.createElement('button');
 impFloat.id='import-float-btn';impFloat.type='button';
 impFloat.innerHTML='📥';impFloat.title='백업 파일 가져오기';
 impFloat.setAttribute('aria-label','백업 파일 가져오기');
 document.body.appendChild(impFloat);
 impFloat.addEventListener('click',()=>imFile.click());
 window.addEventListener('scroll',()=>{
  const show=window.scrollY>600;
  expFloat.classList.toggle('show',show);
  impFloat.classList.toggle('show',show);
 },{passive:true});
})();
/* Google Drive 자동 동기화 (기존 기능과 격리: 실패해도 메인 스크립트에 영향 없음) */
try{(function(){
 window.addEventListener('error',e=>{const _em=e.message||'';if(_em==='Script error.'||_em==='Script error')return;});
 const DRIVE_CLIENT_ID='43687402502-rlid8pjoakcigrn6tpcs58elo357vtfr.apps.googleusercontent.com';
 const DRIVE_SCOPE='https://www.googleapis.com/auth/drive.appdata';
 const STATE_KEYS=['archive-pinned','archive-bookmarks','archive-bookmark-names','archive-deleted-replies','archive-deleted-posts'];
 const TS_KEY='archive-sync-updatedAt';
 const get=(k,d)=>{try{const v=JSON.parse(localStorage.getItem(k));return v==null?d:v}catch(e){return d}};
 const set=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}};
 const getTs=()=>localStorage.getItem(TS_KEY)||'';
 const setTs=v=>{try{localStorage.setItem(TS_KEY,v)}catch(e){}};
 let tokenClient=null,accessToken=null,fileId=null,connected=false,lastHash='';
 const stateHash=()=>{try{return STATE_KEYS.map(k=>localStorage.getItem(k)||'').join('|')}catch(e){return ''}};
 const collectState=()=>({pinned:get('archive-pinned',[]),bookmarks:get('archive-bookmarks',{}),names:get('archive-bookmark-names',{}),deletedReplies:get('archive-deleted-replies',[]),deletedPosts:get('archive-deleted-posts',[]),updatedAt:new Date().toISOString()});
 const applyState=d=>{
  if(Array.isArray(d.pinned))set('archive-pinned',d.pinned);
  if(d.bookmarks&&typeof d.bookmarks==='object')set('archive-bookmarks',d.bookmarks);
  const nms=d.names||d.bookmarkNames;
  if(nms&&typeof nms==='object')set('archive-bookmark-names',nms);
  if(Array.isArray(d.deletedPosts)&&d.deletedPosts.every(x=>typeof x==='string'))set('archive-deleted-posts',[...new Set(d.deletedPosts)]);
  if(Array.isArray(d.deletedReplies))set('archive-deleted-replies',d.deletedReplies);
  if(d.updatedAt)setTs(d.updatedAt);
 };
 const statusEls=[];
 const setStatus=(txt,ok)=>{statusEls.forEach(elm=>{elm.textContent=txt;elm.dataset.connected=ok?'1':'0'})};
 const api=async(path,opts={})=>{
  const r=await fetch('https://www.googleapis.com'+path,{...opts,headers:{...(opts.headers||{}),Authorization:'Bearer '+accessToken}});
  if(!r.ok)throw new Error('Drive API '+r.status);
  return r;
 };
 const findFile=async()=>{
  const r=await api("/drive/v3/files?q="+encodeURIComponent("name='archive-state.json' and 'appDataFolder' in parents")+"&spaces=appDataFolder&fields=files(id,modifiedTime)");
  const j=await r.json();
  return (j.files&&j.files[0])||null;
 };
 const downloadState=async()=>{
  const f=await findFile();
  if(!f)return null;
  fileId=f.id;
  const r=await api('/drive/v3/files/'+f.id+'?alt=media');
  return await r.json();
 };
 const uploadState=async(data)=>{
  const body=JSON.stringify(data);
  if(fileId){
   await api('/upload/drive/v3/files/'+fileId+'?uploadType=media',{method:'PATCH',headers:{'Content-Type':'application/json'},body});
  }else{
   const metadata={name:'archive-state.json',parents:['appDataFolder']};
   const boundary='-------drive-sync-'+Date.now();
   const multipart='--'+boundary+'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+JSON.stringify(metadata)+'\r\n--'+boundary+'\r\nContent-Type: application/json\r\n\r\n'+body+'\r\n--'+boundary+'--';
   const r=await api('/upload/drive/v3/files?uploadType=multipart',{method:'POST',headers:{'Content-Type':'multipart/related; boundary='+boundary},body:multipart});
   const j=await r.json();
   fileId=j.id;
  }
  setTs(data.updatedAt);
  lastHash=stateHash();
 };
 const doSync=async()=>{
  if(!connected||!accessToken)return;
  try{
   const remote=await downloadState();
   const localTs=getTs();
   if(remote&&remote.updatedAt&&(!localTs||remote.updatedAt>localTs)){
    applyState(remote);
    lastHash=stateHash();
    setStatus('☁️ Drive 동기화됨',true);
    location.reload();
   }else if(!remote||(localTs&&(!remote.updatedAt||localTs>remote.updatedAt))){
    const h=stateHash();
    if(h!==lastHash){await uploadState(collectState());setStatus('☁️ Drive 동기화됨',true)}
   }
  }catch(e){setStatus('☁️ Drive 오류',false)}
 };
 const autoSave=async()=>{
  if(!connected||!accessToken)return;
  const h=stateHash();
  if(h!==lastHash){
   try{await uploadState(collectState());setStatus('☁️ Drive 동기화됨',true)}catch(e){setStatus('☁️ Drive 오류',false)}
  }
 };
 window.DriveSync={
  _initTries:0,
  _inited:false,
  init(onReady){
   const self=window.DriveSync;
   if(self._inited){if(onReady)try{onReady()}catch(e){}return true}
   if(typeof google==='undefined'||!google.accounts||!google.accounts.oauth2){
    if(self._initTries<20){self._initTries++;setTimeout(()=>self.init(onReady),500);}
    else{setStatus('🔗 Drive 연결',false);}
    return false;
   }
   self._inited=true;
   tokenClient=google.accounts.oauth2.initTokenClient({client_id:DRIVE_CLIENT_ID,scope:DRIVE_SCOPE,callback:(resp)=>{
    if(resp&&resp.access_token){
     accessToken=resp.access_token;connected=true;
     setStatus('☁️ Drive 연결됨',true);
     doSync();
     setInterval(autoSave,2000);
    }else{setStatus('🔗 Drive 연결',false)}
   }});
   const savedTs=getTs();
   if(savedTs){connected=false;setStatus('🔗 Drive 연결',false)}
   if(onReady)try{onReady()}catch(e){}
  },
  connect(){
   if(!tokenClient){
    window.DriveSync.init(()=>{try{tokenClient.requestAccessToken({prompt:'consent'})}catch(e){}});
    return;
   }
   if(connected&&accessToken){doSync();return}
   tokenClient.requestAccessToken({prompt:'consent'});
  },
  statusEl(elm){statusEls.push(elm)}
 };
 // PC 우측 플로팅 버튼
 const drvBtn=document.createElement('button');
 drvBtn.id='drive-float-btn';drvBtn.type='button';
 drvBtn.innerHTML='🔗';drvBtn.title='Google Drive 연결';
 drvBtn.setAttribute('aria-label','Google Drive 연결');
 document.body.appendChild(drvBtn);
 drvBtn.addEventListener('click',()=>window.DriveSync.connect());
 window.DriveSync.statusEl(drvBtn);
 window.addEventListener('scroll',()=>{drvBtn.classList.toggle('show',window.scrollY>600)},{passive:true});
 /* init은 "Drive 연결" 버튼 클릭 시에만 실행 (페이지 로드 시 자동 실행 안 함) */
})();}catch(driveErr){try{console.warn('DriveSync disabled:',driveErr&&driveErr.message)}catch(e){}}
</script>
__MEMO_MODAL_HTML__
</body>
</html>
'''



def escape(value):
    return html.escape(str(value or ""), quote=True)


_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')


def linkify_html(html_content):
    """fullHtml 내 텍스트 노드의 bare URL을 하이퍼링크로 변환.

    기존 <a> 태그 안의 URL과 태그 속성(href/src 등) 안의 URL은 건드리지 않음.
    """
    if not html_content:
        return html_content or ""
    # 1) 기존 <a>...</a> 블록 통째로 보호
    anchors = []

    def _ap(m):
        anchors.append(m.group(0))
        return "\x00A%d\x00" % (len(anchors) - 1)

    tmp = re.sub(r'<a\b[^>]*>.*?</a>', _ap, html_content, flags=re.S | re.I)
    # 2) 나머지 모든 태그 보호 (속성값의 URL이 링크화되지 않도록)
    tags = []

    def _tp(m):
        tags.append(m.group(0))
        return "\x00T%d\x00" % (len(tags) - 1)

    tmp = re.sub(r'<[^>]+>', _tp, tmp)
    # 3) 텍스트 부분의 bare URL만 링크로 변환
    #    (\x00 플레이스홀더가 URL에 포함되지 않도록 \x00 제외)
    urls = []

    def _up(m):
        urls.append(m.group(0))
        return "\x00URL%d\x00" % (len(urls) - 1)

    tmp = re.sub(r'https?://[^\s<>"\')\]\x00]+', _up, tmp)
    for i, u in enumerate(urls):
        trail = ""
        while u and u[-1] in ".,;:!?…":
            trail = u[-1] + trail
            u = u[:-1]
        eu = escape(u)
        tmp = tmp.replace("\x00URL%d\x00" % i,
                          '<a href="%s" target="_blank" rel="noopener">%s</a>%s'
                          % (eu, eu, escape(trail)))
    # 4) 태그·앵커 복원
    for i, t in enumerate(tags):
        tmp = tmp.replace("\x00T%d\x00" % i, t)
    for i, a in enumerate(anchors):
        tmp = tmp.replace("\x00A%d\x00" % i, a)
    return tmp


def linkify(text):
    """일반 텍스트(이스케이프 전) 내 bare URL을 하이퍼링크로 변환.

    XSS 안전: URL을 플레이스홀더로 치환한 뒤 escape, 마지막에 앵커 복원.
    URL 끝의 문장부호(.,;:!?…)는 링크에서 제외하고 뒤에 붙임.
    """
    urls = []

    def _ph(m):
        urls.append(m.group(0))
        return "\x00URL%d\x00" % (len(urls) - 1)

    tmp = _URL_RE.sub(_ph, text or "")
    tmp = escape(tmp)
    for i, u in enumerate(urls):
        eu = escape(u)
        trail = ""
        while eu and eu[-1] in ".,;:!?…":
            trail = eu[-1] + trail
            eu = eu[:-1]
        tmp = tmp.replace("\x00URL%d\x00" % i,
                          '<a href="%s" target="_blank" rel="noopener">%s</a>%s'
                          % (eu, eu, escape(trail)))
    return tmp


def counts(values):
    return sorted(Counter(value for value in values if value).items(), key=lambda item: (-item[1], item[0]))


def topics_of(article):
    return article.get("topics") if isinstance(article.get("topics"), list) else []


def filter_button(name, count, kind, spotlight=False, extra=False):
    avatar = ""
    if spotlight:
        initials = "".join(word[0] for word in name.split()[:2]).upper()
        avatar = f'<span class="avatar" aria-hidden="true">{escape(initials)}</span>'
    label = f'<span><strong>{escape(name)}</strong></span>' if spotlight else (f'<span><strong>{escape(name)}</strong><small>{count}건</small></span>' if count is not None else escape(name))
    value = name if count is not None else ""
    return (f'<button type="button" class="{"filter-card" if spotlight else "chip"}" '
            f'data-kind="{kind}" data-value="{escape(value)}" aria-pressed="{str(not value).lower()}"'
            f'{" data-extra-person hidden" if extra else ""}>{avatar}{label}</button>')


def people_filters(entries, spotlight=False):
    result = "" if spotlight else filter_button("모든 인물", None, "author")
    for index, (name, count) in enumerate(entries):
        if index == 5:
            result += '<button type="button" class="chip" data-people-toggle aria-expanded="false">전체인물</button>'
        result += filter_button(name, count, "author", spotlight, index >= 5)
    return result



def url_inline_html(article):
    """본문 URL 스크랩 내용을 인라인으로 표시 — 2026-10-12 대표님 지시.
    url_contents.json에서 스크랩된 title/content를 본문 아래에 바로 보여줌."""
    items = []
    for uc in (article.get("urlContents") or []):
        url = uc.get("url", "")
        title = (uc.get("title") or url).strip()
        body = (uc.get("content") or "").strip()
        status = uc.get("status", "")
        if not body and status == "pending":
            continue
        if not body:
            continue
        # 마크다운 기호 가볍게 정리 (이미지/링크는 텍스트로)
        body = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'[이미지: \1]', body)
        body = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', body)
        body = re.sub(r'^#{1,6}\s+', '', body, flags=re.M)
        body = body.strip()
        if len(body) > 1200:
            body = body[:1200].rstrip() + "…"
        items.append(
            '<div class="url-inline-item">'
            f'<div class="url-inline-title">\U0001F4C4 <a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(title)}</a></div>'
            f'<div class="url-inline-url">{escape(url)}</div>'
            f'<div class="url-inline-content">{escape(body)}</div>'
            '</div>'
        )
    if not items:
        return ""
    return '<div class="url-inline"><div class="url-inline-head">\U0001F517 본문 링크 내용</div>' + "".join(items) + '</div>'


def linked_section_html(article):
    """🔗 링크된 내용 섹션 — 2026-10-12 대표님 지시로 완전 제거. 본문 하이퍼링크로 대체."""
    return ""


def article_html(article, featured=False, idx=0, related=None):
    is_x = article["type"] == "x"
    author = article.get("author", "")
    text = article.get("fullText") or ""
    missing = "본문이 수집되지 않은 게시물입니다. X 원문에서 확인하세요."
    title = (text.strip().split("\n")[0][:90] or author) if is_x else article.get("title", "")
    summary = (text[:150] + ("…" if len(text) > 150 else "") if text else missing) if is_x else article.get("summary", "")
    full_html = article.get("fullHtml")  # 인라인 이미지가 포함된 HTML (이스케이프 안 함)
    full_text = (text or missing) if is_x else (text or article.get("summary", ""))
    # 연관 게시물 HTML
    related_html = ""
    if related:
        items = []
        for r in related[:5]:
            r_title = ((r.get("fullText") or "").strip().split("\n")[0][:60] or r.get("author", "")) if r["type"] == "x" else r.get("title", "")
            r_author = r.get("author", "")
            # data-idx로 해당 카드 찾기 (JS에서 스크롤 이동)
            items.append(f'<li><a href="#" class="related-link" data-target-idx="{r.get("_idx", "")}">{escape(r_title)}</a> <span class="ai-author">— {escape(r_author)}</span></li>')
        if items:
            related_html = f'<div class="related-section"><h4>🔗 연관 게시물</h4><ul class="related-list">{"".join(items)}</ul></div>'
    topics = topics_of(article)
    tag_colors = [("#17675c", "#e4efe8"), ("#974728", "#fae8dc"), ("#64528b", "#eee7f5"), ("#766019", "#f5edce")]
    tags = []
    for topic in topics:
        ink, paper = tag_colors[sum(map(ord, topic)) % len(tag_colors)]
        tags.append(f'<span class="tag" style="--tag-ink:{ink};--tag-paper:{paper}">{escape(topic)}</span>')
    handle = f'<span class="handle">{escape(article.get("handle", ""))}</span>' if is_x else ""
    meta = f'작성 {article.get("createdAt") or "날짜 미확인"}' if is_x else f'{article.get("source_domain", "")} · 저장 {article.get("date_saved", "")}'
    imgs = []
    for index, (thumb, full) in enumerate(article.get("mediaImages", [])):
        lid = lightbox_id(article, index)
        imgs.append(f'<a class="media-link" href="#{lid}" aria-label="이미지 {index + 1} 확대 보기">'
                    f'<img src="{escape(thumb)}" alt="{escape(author)} 게시물 이미지 {index + 1}" loading="lazy"></a>')
    images = "".join(imgs)
    media = f'<div class="post-media">{images}</div>' if images else ""
    # 인용글 섹션 (quoted) — 기본 표시(open), 접기 가능
    quoted_html = ""
    q = article.get("quoted")
    if isinstance(q, dict):
        qtext = (q.get("fullText") or "").strip()
        qauthor = q.get("author") or {}
        qname = qauthor.get("name") or qauthor.get("handle") or ""
        qwhen = q.get("createdAt") or ""
        qimgs = []
        for m in (q.get("media") or []):
            mu = m if isinstance(m, str) else (m.get("url") or m.get("src") or "" if isinstance(m, dict) else "")
            if mu:
                qimgs.append(f'<img src="{escape(mu)}" loading="lazy" alt="인용글 이미지">')
        qmedia_html = f'<div class="quoted-media">{"".join(qimgs)}</div>' if qimgs else ""
        if qtext or qimgs:
            qlabel = f'🔗 인용글 · {escape(qname)}' if qname else '🔗 인용글'
            quoted_html = (f'<details class="quoted" open><summary>{qlabel}</summary>'
                           f'<div class="quoted-body">'
                           f'<div><span class="quoted-author">{escape(qname)}</span>'
                           f'<span class="quoted-time">{escape(qwhen)}</span></div>'
                           + (f'<p class="quoted-text">{escape(qtext)}</p>' if qtext else '')
                           + qmedia_html + '</div></details>')
    url = article.get("sourceUrl" if is_x else "source_url", "")
    url_inline = url_inline_html(article)
    return (f'<details class="article{" featured" if featured else ""}{" x-card" if is_x else ""}" '
            f'data-type="{escape(article["type"])}" data-author="{escape(author)}" data-topics="{escape(chr(10).join(topics))}" data-idx="{article.get("_idx", "")}" data-pin-id="{escape(str(article.get("id") or article.get("tweetId") or article.get("sourceUrl") or ""))}"{" data-chart=\"1\"" if article.get("has_chart") else ""}>'
            '<summary class="card-toggle">'
            f'<div class="bookmark-colors" hidden></div>'
            f'<div class="spotlight-label"{"" if featured else " hidden"}>LATEST PICK · 가장 최근 글</div>'
            f'<div class="card-header">'
            f'<div class="type-badge">{"𝕏 · X 게시물" if is_x else "웹글 · WEB ARTICLE"}</div>'
            f'<div class="card-actions">'
            f'{memo_button(str(article.get("id") or article.get("tweetId") or article.get("sourceUrl") or idx), title)}'
            f'<button type="button" class="bookmark-btn" aria-label="북마크" title="북마크"><svg viewBox="0 0 24 24"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg></button>'
            f'<button type="button" class="pin-btn" aria-label="상단 고정" title="상단 고정"><svg viewBox="0 0 24 24"><path d="M9 4h6l-1 7 3 3v2H7v-2l3-3-1-7z"/><path d="M12 14v7"/></svg></button>'
            f'<button type="button" class="delete-btn" aria-label="삭제" title="삭제"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg></button>'
            f'</div></div>'
            f'<h3>{escape(title)}{handle}</h3><div class="meta">{escape(meta)}</div>'
            f'<p class="summary">{escape(summary)}</p><div class="tags">{"".join(tags)}</div>'
            '</summary>'
            f'<div class="full-text{" web-html" if full_html else ""}">{linkify_html(full_html) if full_html else linkify(full_text)}</div>{url_inline}{media}{quoted_html}{related_html}'
            f'<div class="byline"><span class="author">{escape(author)}</span>'
            f'<button type="button" class="read-window-btn" data-idx="{idx}">새 창에서 읽기 ⧉</button>'
            f'<a class="read-link" href="{escape(url)}" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a></div>'
            '</details>')


def lightbox_id(article, index):
    key = str(article.get("id") or article.get("sourceUrl") or article.get("source_url") or "")
    return "lb-" + hashlib.sha256(key.encode()).hexdigest()[:16] + f"-{index}"


def lightboxes_html(articles):
    overlays = []
    for article in articles:
        for index, (_, full) in enumerate(article.get("mediaImages", [])):
            lid = lightbox_id(article, index)
            overlays.append(
                f'<section class="css-lightbox" id="{lid}" role="dialog" aria-label="이미지 확대">'
                '<a href="#articles" class="css-lightbox-close" aria-label="이미지 닫기">×</a>'
                f'<input type="checkbox" class="css-lightbox-toggle" id="{lid}-size">'
                f'<label for="{lid}-size">원래 크기로 보기</label>'
                f'<div class="css-lightbox-stage"><img src="{escape(full)}" '
                f'alt="{escape(article.get("author", ""))} 게시물 이미지 {index + 1} 확대" loading="lazy"></div>'
                '<p class="css-lightbox-help">원래 크기 선택 후 스크롤하여 작은 글자를 읽을 수 있습니다.</p></section>')
    return "".join(overlays)


def image_data(path):
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


_chart_cache = {}

def is_chart_image(path):
    """차트 이미지 판정: 직선(격자/축/캔들) 성분이 많고 채도가 낮은 이미지.
    보수적 임계값 — false positive보다 false negative를 허용."""
    key = str(path)
    if key in _chart_cache:
        return _chart_cache[key]
    result = False
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(path).convert("RGB")
        img.thumbnail((200, 200))
        a = np.asarray(img)
        # 채도: 차트는 흰/회색 배경 위주라 채도가 낮음
        mx = a.max(axis=2).astype(np.float32)
        mn = a.min(axis=2).astype(np.float32)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0), 0.0)
        mean_sat = float(sat.mean())
        # 엣지 (그레이스케일 차분)
        g = a.mean(axis=2)
        gx = np.abs(np.diff(g, axis=1))
        gy = np.abs(np.diff(g, axis=0))
        h, w = g.shape
        edge = np.zeros((h, w), dtype=bool)
        edge[:, :-1] |= gx > 28
        edge[:-1, :] |= gy > 28
        total_edge = int(edge.sum())
        if total_edge > 200:
            # 수평 직선: 행별 12px 이상 연속 엣지
            h_run = 0
            for row in edge:
                run = 0
                for v in row:
                    if v:
                        run += 1
                    elif run >= 12:
                        h_run += run
                        run = 0
                    else:
                        run = 0
                if run >= 12:
                    h_run += run
            # 수직 직선: 열별 12px 이상 연속 엣지
            v_run = 0
            for col in edge.T:
                run = 0
                for v in col:
                    if v:
                        run += 1
                    elif run >= 12:
                        v_run += run
                        run = 0
                    else:
                        run = 0
                if run >= 12:
                    v_run += run
            straight_ratio = (h_run + v_run) / total_edge
            result = straight_ratio > 0.35 and mean_sat < 0.25
    except Exception:
        result = False
    _chart_cache[key] = result
    return result


def full_image(cache_key, originals):
    """Build a separate offline zoom tier; never upscale a thumbnail."""
    from PIL import Image, ImageOps

    source = next((p for p in sorted(originals.glob(f"{cache_key}.*"))
                   if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if source is None:
        raise FileNotFoundError(f"확대용 원본 이미지가 없습니다: {originals / cache_key}")
    cache = ROOT / "media-large" / f"{cache_key}-1600-q85.jpg"
    if not cache.is_file() or cache.stat().st_mtime_ns < source.stat().st_mtime_ns:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            image.save(cache, "JPEG", quality=85, optimize=True, subsampling=2)
    return image_data(cache)


def compute_related(articles, top_n=5):
    """연관 게시물 계산: 주제·저자·키워드 겹침 기반 (토큰 사용 없음)."""
    import re
    from collections import Counter
    stop = {'the','and','for','with','this','that','from','have','있는','하는','되는','통해','대한','관련','것을','수있'}
    def kw(text):
        words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{3,}', (text or '').lower())
        return Counter(w for w in words if w not in stop)
    # 미리 계산
    kws = [kw((a.get('fullText') or a.get('summary') or '') + ' ' + (a.get('title') or '')) for a in articles]
    topics = [{topic for topic in topics_of(a) if topic} for a in articles]
    authors = [a.get('author', '') for a in articles]
    related = {}
    for i, a in enumerate(articles):
        scores = []
        for j, b in enumerate(articles):
            if i == j:
                continue
            score = 0
            # 주제 겹침 (가중치 높음)
            overlap_topics = topics[i] & topics[j]
            score += len(overlap_topics) * 10
            # 저자 일치
            if authors[i] and authors[i] == authors[j]:
                score += 8
            # 키워드 겹침
            common = sum((kws[i] & kws[j]).values())
            score += min(common, 15)
            if score > 5:
                scores.append((score, j))
        scores.sort(reverse=True)
        related[i] = [j for _, j in scores[:top_n]]
    return related


def ai_tools_html(articles):
    """AI툴 목록: 카드가 아닌 간단한 제목 목록."""
    if not articles:
        return ""
    items = []
    for article in articles:
        title = article.get("fullText", "").strip().split("\n")[0][:80] if article["type"] == "x" else article.get("title", "")
        author = article.get("author", "")
        url = article.get("sourceUrl" if article["type"] == "x" else "source_url", "")
        items.append(f'<li><a href="{escape(url)}" target="_blank" rel="noopener">{escape(title)}</a> <span class="ai-author">— {escape(author)}</span></li>')
    return (f'<section class="ai-tools-section"><h2>🛠️ AI 도구 모음 ({len(articles)}건)</h2>'
            f'<ul class="ai-tools-list">{"".join(items)}</ul></section>')


def render_html(articles):
    articles = sorted(articles, key=lambda article: article["sort_time"], reverse=True)
    # AI툴은 카드가 아닌 하단 목록으로 분리
    ai_tools = [a for a in articles if "AI툴" in topics_of(a)]
    main_articles = [a for a in articles if "AI툴" not in topics_of(a)]
    # 카드가 있는 게시물만 추천하여 모든 연관 링크에 타겟을 보장
    related_map = compute_related(main_articles)
    # main_articles의 인덱스를 전체 인덱스로 매핑
    id_to_idx = {id(a): i for i, a in enumerate(articles)}
    # 각 article에 전체 인덱스 부여 (연관 링크 타겟용)
    for a in articles:
        a["_idx"] = id_to_idx[id(a)]
    people = counts(article.get("author") for article in articles)
    topics = counts(topic for article in articles for topic in set(topics_of(article)))
    total = sum(count for _, count in topics)
    colors = ["#d98069", "#c9b591", "#8da9a4", "#64758f"]
    bar, legend = [], []
    for index, (name, count) in enumerate(topics):
        color = colors[index % len(colors)]
        bar.append(f'<span style="width:{count / total * 100:.6f}%;background:{color}" title="{escape(name)} {count}건"></span>')
        legend.append(f'<span class="legend-item" style="--color:{color}">{escape(name)} {count}</span>')
    # IB 주원 섹션 생성
    try:
        ib_juwon_html = build_ib_section(
            Path.home() / "workspace/x-bookmarks/study_links.json",
            Path.home() / "workspace/x-bookmarks/resource_library/ib_juwon.json",
            Path.home() / "workspace/x-bookmarks/resource_library/index.json",
        )
    except Exception:
        ib_juwon_html = ""
    values = {
        "LIGHTBOXES": lightboxes_html(articles),
        "TOTAL": f"전체 {len(articles)}건",
        "STATUS": f"{len(articles)}건",
        "STATS": "".join(f'<div class="stat"><strong>{count}건</strong><span>{label}</span></div>' for label, count in [("전체", len(articles)), ("웹글", sum(a["type"] == "web" for a in articles)), ("X 게시물", sum(a["type"] == "x" for a in articles))]),
        "TOP_AUTHORS": escape("많이 모은 저자 · " + " · ".join(f"{name} {count}건" for name, count in people[:3])),
        "DISTRIBUTION_LABEL": escape(", ".join(f"{name} {count}건" for name, count in topics) or "주제 없음"),
        "DISTRIBUTION_BAR": "".join(bar), "LEGEND": "".join(legend),
        "TYPES": "".join(f'<button type="button" class="type-tab" data-kind="type" data-value="{value}" aria-pressed="{str(not value).lower()}">{label}</button>' for label, value in [("전체", ""), ("웹글", "web"), ("X 게시물", "x"), ("IB 주원", "ib")]),
        "TOPICS": filter_button("전체 주제", None, "topic") + "".join(filter_button(name, count, "topic") for name, count in topics),
        "PEOPLE": people_filters(people), "SPOTLIGHT": people_filters(people, True),
        "ARTICLES": "\n".join(article_html(article, index == 0, index, [main_articles[j] for j in related_map.get(index, [])]) for index, article in enumerate(main_articles)),
        "AI_TOOLS": ai_tools_html(ai_tools),
        "IB_JUWON": ib_juwon_html,
        "IB_CSS": IB_CSS,
        "IB_JS": IB_JS,
        "MEMO_CSS": MEMO_CSS,
        "MEMO_JS": MEMO_JS,
        "MEMO_MODAL_HTML": MEMO_MODAL_HTML,
        "IB_JS": IB_JS,
        "MEMO_CSS": MEMO_CSS,
        "MEMO_JS": MEMO_JS,
        "MEMO_MODAL_HTML": MEMO_MODAL_HTML,
    }
    # Substitute once so article text containing a template marker stays literal.
    return re.sub(r"__([A-Z_]+)__", lambda match: values[match[1]], HTML)


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError):
        return 0


def build(index_path: Path, output: Path, posts_path: Path = DEFAULT_POSTS) -> None:
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data.get("articles"), list):
        raise ValueError("index.json에는 articles 배열이 필요합니다.")
    posts = json.loads(posts_path.read_text(encoding="utf-8"))
    if not isinstance(posts, list):
        raise ValueError("posts.json에는 게시물 배열이 필요합니다.")
    web_count = len(data["articles"])
    for article in data["articles"]:
        article["type"] = "web"
        article["sort_time"] = timestamp(article.get("date_saved", ""))
    # 외부 URL 스크랩 결과 로드 (없으면 빈 dict)
    url_contents_path = posts_path.parent / "url_contents.json"
    url_contents_map = {}
    if url_contents_path.is_file():
        try:
            url_contents_map = json.loads(url_contents_path.read_text(encoding="utf-8"))
        except Exception:
            url_contents_map = {}
    url_pat = re.compile(r'https?://[^\s\)\]]+')
    def external_urls(text):
        found = []
        for u in url_pat.findall(text or ""):
            u = u.rstrip('.,;:!?…')
            if any(d in u for d in ['x.com', 'twitter.com', 'pbs.twimg.com']):
                continue
            if u not in found:
                found.append(u)
        return found
    for post in posts:
        category = (post.get("category") or "").strip()
        if category in ("", "미수집(빈캡처)"):
            category = "기타"
        author = post.get("author") or {}
        handle = author.get("handle") or ""
        name = (author.get("name") or handle or "저자 미확인").strip()
        if handle and name.endswith(f"({handle})"):
            name = name[:-len(handle)-2].strip()
        url = post.get("sourceUrl", "")
        if not url.startswith(("https://x.com/", "https://twitter.com/")):
            raise ValueError(f"잘못된 X 원문 URL: {url}")
        # Snowflake IDs preserve publication order even for relative createdAt labels.
        tweet_id = str(post.get("tweetId", ""))
        sort_time = ((int(tweet_id) >> 22) + 1288834974657) / 1000 if tweet_id.isdigit() else timestamp(post.get("capturedAt", ""))
        media_images = []
        has_chart = False
        for media in post.get("media") or []:
            media_url = media.get("url")
            if not media_url:
                continue
            cache_key = hashlib.md5(media_url.encode("utf-8")).hexdigest()[:12]
            thumb_file = ROOT / "media-mid" / f"{cache_key}.jpg"
            if thumb_file.is_file():
                thumb = image_data(thumb_file)
                full = full_image(cache_key, posts_path.parent / "media")
                media_images.append((thumb, full))
                if not has_chart and is_chart_image(thumb_file):
                    has_chart = True
        data["articles"].append({"type": "x", "id": tweet_id, "sourceUrl": url,
            "author": name, "handle": handle, "fullText": post.get("fullText") or "",
            "createdAt": post.get("createdAt") or "", "topics": [category], "sort_time": sort_time,
            "mediaImages": media_images, "has_chart": has_chart, "quoted": post.get("quoted") or None,
            "urlContents": [{"url": u, **url_contents_map.get(u, {"title": u, "content": "", "status": "pending"})}
                            for u in external_urls(post.get("fullText") or "")]})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(data["articles"]), encoding="utf-8")
    print(f"생성 완료: {output} (전체 {len(data['articles'])}건 · 웹글 {web_count}건 · X {len(posts)}건)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "index.json")
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.index.expanduser().resolve(), args.output.expanduser().resolve(), args.posts.expanduser().resolve())
