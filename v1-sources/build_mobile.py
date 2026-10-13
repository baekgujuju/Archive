#!/usr/bin/env python3
"""Self-contained mobile archive; existing dashboard functions and cached image tiers."""
import argparse, base64, hashlib, json, re
from pathlib import Path
from PIL import Image
import build_dashboard as bd
from build_dashboard import (escape, counts, topics_of, filter_button, people_filters,
    article_html, lightbox_id, image_data, full_image, is_chart_image,
    compute_related, ai_tools_html, timestamp, ROOT, DEFAULT_POSTS, linkify_html, linkify, url_inline_html)
from ib_section import build_ib_section, IB_CSS, IB_JS
from memo_feature import MEMO_CSS, MEMO_JS, MEMO_MODAL_HTML, memo_button, memo_button_full

def webp_thumb(cache_key, originals):
    """550px WebP 썸네일 생성."""
    source = next((p for p in sorted(originals.glob(f"{cache_key}.*"))
                   if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if source is None:
        return None, None
    cache = bd.ROOT / "media-webp" / f"{cache_key}-550.webp"
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as img:
            img = img.convert("RGB")
            img.thumbnail((550, 550), Image.LANCZOS)
            img.save(cache, "WEBP", quality=75, method=6)
    data = base64.b64encode(cache.read_bytes()).decode("ascii")
    return f"data:image/webp;base64,{data}", f"data:image/webp;base64,{data}"

def webp_full(cache_key, originals):
    """1200px WebP 확대 이미지 생성 (기존 1600px JPEG q85 대체)."""
    source = next((p for p in sorted(originals.glob(f"{cache_key}.*"))
                   if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if source is None:
        return None
    cache = bd.ROOT / "media-full-webp" / f"{cache_key}-1200-q80.webp"
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as img:
            img = img.convert("RGB")
            img.thumbnail((1200, 1200), Image.LANCZOS)
            img.save(cache, "WEBP", quality=80, method=6)
    data = base64.b64encode(cache.read_bytes()).decode("ascii")
    return f"data:image/webp;base64,{data}"

MOBILE_CSS = r'''
@page{margin:14mm 12mm}
@media print{body{zoom:0.5}}
:root{color-scheme:light;--paper:#f6f2e9;--ink:#222b3b;--muted:#716e65;--line:#ddd6ca;--accent:#c34127;--soft:#f1e6da;--navy:#18243c}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;text-size-adjust:100%}body{margin:0;background:var(--paper);color:var(--ink);font:10.5pt/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}button,input{font:inherit}button{cursor:pointer}a{color:inherit;text-decoration:none}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.wrap{max-width:1160px;margin:auto;padding:0 28px}h1,h2,h3{font-family:'Spoqa Han Sans Neo','Noto Sans KR',sans-serif}h1,h2,h3,p{margin:0}header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:22px 0;border-bottom:1px solid var(--line)}.brand{font-weight:800;font-size:19px}.edition,.eyebrow{font-size:10px;letter-spacing:.16em}.edition{color:var(--muted)}.hero{padding:38px 0 28px}.eyebrow{color:var(--accent);font-weight:800}h1{font-size:clamp(38px,6vw,68px);line-height:1.15;letter-spacing:-.05em;margin-top:15px}.intro{color:var(--muted);margin:14px 0 24px}.data-band{padding:26px;border-radius:14px;background:var(--navy);color:white}.stats{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:14px}.stat strong{display:block;font-size:42px;line-height:1.2;font-variant-numeric:tabular-nums;letter-spacing:-.04em}.stat:first-child strong{color:#e7785f}.stat span,.top-authors{font-size:11px;color:#c3ccde}.top-authors{margin-top:18px;overflow-wrap:anywhere}.distribution{margin-top:22px;padding-top:18px;border-top:1px solid #ffffff26;min-width:0}.distribution .edition{color:#c3ccde}.bar{display:flex;height:10px;overflow:hidden;border-radius:8px;margin:14px 0}.legend{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:10px;color:#dce3ed}.legend-item:before{content:"";display:inline-block;width:7px;height:7px;background:var(--color);border-radius:50%;margin-right:5px}.distribution-note{font-size:10px;color:#c3ccde;margin-top:12px}.issue-mark{font:italic 17px Georgia,"Batang","AppleMyungjo",serif;color:var(--accent);margin-top:19px}
.search-bar{position:sticky;top:0;z-index:10;background:#f6f2e9f5;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:12px 0}.search{position:relative}.search span{position:absolute;left:15px;top:7px;font-size:24px;color:var(--muted)}.search-row{display:flex;gap:8px;align-items:center}.search-input{position:relative;flex:1;min-width:0}.search-mode-btn{flex:none;border:1px solid var(--line);border-radius:8px;padding:11px 13px;background:#fffdf8;color:var(--accent);font-size:12px;font-weight:700;min-height:44px;cursor:pointer}.search-mode-btn[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}input{width:100%;border:1px solid var(--line);border-radius:8px;padding:12px 16px 12px 44px;background:#fffdf8;color:var(--ink)}main{padding-top:24px!important;padding-bottom:40px!important}.filters{background:#fffdf8;border:1px solid var(--line);padding:18px;border-radius:12px}.filter-line{display:flex;align-items:baseline;gap:12px}.filter-line+.filter-line{margin-top:14px}.label{width:30px;flex:none;font-size:11px;color:var(--muted)}.chips,.type-tabs{display:flex;flex-wrap:wrap;gap:7px}.chip,.type-tab{border:1px solid var(--line);border-radius:30px;padding:6px 12px;background:transparent;color:var(--ink);font-size:11px}.chip strong{font-weight:500}.chip small{margin-left:6px;color:var(--muted)}.chip[aria-pressed=true],.type-tab[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:white}.chip[aria-pressed=true] small{color:#fff}.spotlight{margin-top:26px}h2{font-size:22px;letter-spacing:-.03em}.spotlight h2{font-size:18px;margin-bottom:12px}.rail{display:flex;gap:10px;overflow-x:auto;padding:4px 4px 12px;scrollbar-width:thin}.filter-card{flex:0 0 190px;display:flex;align-items:center;gap:12px;text-align:left;padding:15px;border:1px solid var(--line);border-radius:12px;background:#fffdf8;color:var(--ink)}.filter-card strong{display:block;font-size:12px}.filter-card small{display:block;font-size:10px;color:var(--muted);margin-top:4px}.filter-card[aria-pressed=true]{border-color:var(--accent);background:var(--soft)}.avatar{width:36px;height:36px;flex:none;display:grid;place-items:center;border-radius:50%;background:var(--soft);color:var(--accent);font-size:12px;font-weight:800}.list-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:25px 0 8px}.reset{border:0;background:none;color:var(--accent);padding:7px;text-decoration:underline;text-underline-offset:4px}.status{font-size:11px;color:var(--muted);margin-bottom:18px}.grid{display:grid;gap:14px}.article{display:flex;flex-direction:column;min-width:0;padding:23px;border:1px solid var(--line);border-radius:12px;background:#fffdf8;overflow-wrap:anywhere;transition:transform .18s,border-color .18s}.card-toggle{display:block;cursor:pointer;list-style:none}.card-toggle::-webkit-details-marker{display:none}.card-toggle:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.full-text{display:none;white-space:pre-wrap;margin:0 0 18px;padding-top:14px;border-top:1px solid var(--line)}.full-text a{color:#1a73e8;text-decoration:none;overflow-wrap:anywhere}.full-text a:hover{text-decoration:underline;text-underline-offset:3px}.article[open] .full-text{display:block}.article[open] .summary{display:none}.article[open]{align-self:start}.rail .chip{flex:none;align-self:center}.rail.people-expanded{flex-wrap:wrap;overflow:visible}.article:hover{transform:translateY(-3px);border-color:var(--accent)}.card-header{display:flex;justify-content:space-between;align-items:center;gap:8px}.card-actions{display:flex;align-items:center;gap:6px}.card-actions .pin-btn,.card-actions .bookmark-btn,.card-actions .memo-icon-btn{position:static;opacity:.4;padding:2px}.type-badge{display:inline-block;font-size:10px;font-weight:800;color:var(--accent);background:var(--soft);padding:4px 8px;border-radius:5px}.article h3{font-size:14px;line-height:1.4;margin:13px 0 10px;letter-spacing:-.03em;font-weight:600}.handle{display:block;font:11px/1.55 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;color:var(--muted);margin-top:5px}.meta{font-size:10px;color:var(--muted)}.summary{margin:14px 0 18px;color:var(--muted);white-space:pre-line;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:16px}.tag{font-size:10px;padding:3px 8px;color:var(--tag-ink);background:var(--tag-paper);border-radius:4px}.byline{display:flex;justify-content:space-between;align-items:baseline;gap:12px;border-top:1px solid var(--line);padding-top:14px;margin-top:auto;font-size:10px}.author{font-weight:700}.read-link{color:var(--accent);flex:none}
.read-window-btn{border:1px solid var(--line);background:transparent;color:var(--accent);border-radius:6px;padding:4px 10px;font-size:11px;cursor:pointer;margin-right:8px}
.read-window-btn:hover{background:var(--soft)}

.url-inline{margin-top:16px;padding:14px 16px;background:#f4f7f4;border:1px solid var(--line);border-radius:8px}.url-inline-head{font-size:12px;font-weight:800;color:var(--muted);margin-bottom:10px}.url-inline-item{padding:10px 0;border-top:1px solid var(--line)}.url-inline-item:first-of-type{border-top:none;padding-top:0}.url-inline-title{font-size:13px;font-weight:700}.url-inline-title a{color:var(--ink);text-decoration:none}.url-inline-title a:hover{color:var(--accent);text-decoration:underline}.url-inline-url{font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin-top:2px}.url-inline-content{font-size:12.5px;color:#3c4043;margin-top:8px;white-space:pre-wrap;line-height:1.65;overflow-wrap:break-word}.related-section{margin-top:20px;padding:16px;background:#f8f6f0;border-radius:8px;border:1px solid var(--line)}
.related-section h4{font-size:14px;margin:0 0 10px}
.related-list{list-style:none;padding:0;margin:0}
.related-list li{padding:6px 0;font-size:13px;border-bottom:1px solid #e8e0d0}
.related-list li:last-child{border-bottom:none}
.related-list a{color:var(--ink);text-decoration:none}
.related-list a:hover{color:var(--accent);text-decoration:underline}
/* 링크된 외부 URL 내용 */
.linked-item .linked-url a{color:#1a73e8;text-decoration:underline}
/* 상단 고정 핀 */
.pin-btn{position:absolute;top:10px;right:12px;border:none;background:none;cursor:pointer;opacity:.4;padding:4px;z-index:5}
.pin-btn:hover{opacity:1}
.pin-btn.pinned{opacity:1}
.pin-btn svg{width:20px;height:20px;fill:none;stroke:#888;stroke-width:2}
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
.bookmark-btn{position:absolute;top:10px;right:52px;border:none;background:none;cursor:pointer;opacity:.4;padding:4px;z-index:5}
.bookmark-btn:hover{opacity:1}
.bookmark-btn.bookmarked{opacity:1}
.bookmark-btn svg{width:22px;height:22px;fill:none;stroke:#888;stroke-width:2}
.bookmark-btn.bookmarked svg{fill:var(--bkm-color,#888);stroke:var(--bkm-color,#888)}
.delete-btn{position:absolute;top:10px;right:122px;border:none;background:none;cursor:pointer;opacity:.4;padding:4px;z-index:5}
.delete-btn:hover{opacity:1}
.delete-btn svg{width:22px;height:22px;fill:none;stroke:#888;stroke-width:2}
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
.linked-item{padding:10px 0;border-bottom:1px solid #dfe5df}
.linked-item:last-child{border-bottom:none}
.linked-item .linked-title{font-weight:700;font-size:13px}
.linked-item .linked-title a{color:var(--ink);text-decoration:none}
.linked-item .linked-title a:hover{color:var(--accent);text-decoration:underline}
.linked-item .linked-url{font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.linked-item .linked-snippet{font-size:12px;color:#444;margin-top:6px;white-space:pre-wrap}
.linked-item .linked-failed{font-size:12px;color:var(--muted)}
/* AI 도구 목록 */
.ai-tools-section{margin-top:40px;padding:24px;background:#f8f6f0;border-radius:12px;border:1px solid var(--line)}
.ai-tools-section h2{font-size:18px;margin-bottom:16px}
.ai-tools-list{list-style:none;padding:0;margin:0}
.ai-tools-list li{padding:8px 0;border-bottom:1px solid #e8e0d0;font-size:14px}
.ai-tools-list li:last-child{border-bottom:none}
.ai-tools-list a{color:var(--ink);text-decoration:none}
.ai-tools-list a:hover{color:var(--accent);text-decoration:underline}
.ai-author{color:var(--muted);font-size:12px}.featured{border-top:3px solid var(--accent)}.featured h3{font-size:30px}.spotlight-label{font-size:9px;letter-spacing:.15em;color:var(--accent);margin-bottom:12px}.empty{text-align:center;padding:40px;border:1px dashed var(--line);color:var(--muted)}footer{display:flex;justify-content:space-between;gap:16px;margin-top:36px;border-top:1px solid var(--line);padding-top:20px;font-size:10px;color:var(--muted)}[hidden]{display:none!important}'''
MOBILE_CSS += r'''
.spotlight{margin-top:6px}.filter-card{flex:0 0 auto;padding:6px 8px;gap:5px;white-space:nowrap}.avatar{width:22px;height:22px;font-size:9px}.rail{gap:5px}.filter-card strong{font-size:10px}.spotlight h2{font-size:15px;margin-bottom:4px}.list-heading{margin:12px 0 6px}
.media-full-link{display:block;cursor:zoom-in}details.article:target{border-color:var(--accent);box-shadow:0 0 0 3px #c3412733}
.reader-back{flex:none;min-height:44px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--muted);font-weight:700}
#rename-dialog{width:calc(100% - 36px);max-width:400px;border:1px solid var(--line);border-radius:12px;background:var(--paper);color:var(--ink);padding:18px}#rename-dialog input{margin:12px 0;padding:12px}#rename-dialog button{min-height:44px;padding:10px 18px;background:var(--paper);border:1px solid var(--line);border-radius:6px}

.wrap{max-width:760px;padding:0 18px}.hero{padding:8px 0}.hero>summary{color:var(--accent);padding:8px 0}.hero h1{font-size:32px}.data-band{display:block;padding:8px}.stats{gap:6px}.stat strong{font-size:20px}.stat span{font-size:10px}.distribution{margin-top:8px;padding-top:8px}.bar{margin:10px 0}.distribution-note{margin-top:8px}.search-bar{padding-top:calc(8px + env(safe-area-inset-top))}.filters{padding:6px 0;border:0;background:transparent}.filters summary{display:inline-block;background:#fffdf8;border:1px solid var(--line);border-radius:10px;padding:8px 14px;font-weight:700;color:var(--accent);box-shadow:0 2px 6px rgba(0,0,0,.08);cursor:pointer;list-style:none}.filters summary::-webkit-details-marker{display:none}.filter-line{align-items:center;margin-top:8px!important}.chips,.type-tabs{flex-wrap:nowrap;overflow-x:auto;min-width:0}.chip,.type-tab{flex:none;min-height:44px}.rail.people-expanded{flex-wrap:nowrap}.grid,.pinned-grid{display:grid!important;grid-template-columns:minmax(0,1fr)!important;gap:14px}.article{padding:18px;content-visibility:auto;contain-intrinsic-size:auto 340px}.article h3,.featured h3{font-size:14px}.article:hover{transform:none}.type-badge{display:inline-block;margin-right:0}.spotlight-label{margin-right:82px}.byline{flex-wrap:wrap}.pin-btn,.bookmark-btn,.delete-btn{opacity:1;padding:10px;width:44px;height:44px;top:8px}.pin-btn{right:8px}.bookmark-btn{right:46px}.delete-btn{right:128px}.card-actions{gap:3px!important}.memo-icon-btn{margin-right:2px}.article .memo-icon-btn{position:absolute;top:8px;right:84px;width:44px;height:44px;display:flex;align-items:center;justify-content:center}.article .memo-icon-btn.memo-hidden{display:none}.article .memo-icon-btn svg{width:24px;height:24px}.pin-btn svg,.bookmark-btn svg{width:24px;height:24px}.post-media{display:none}.article[open] .post-media{display:grid;gap:12px}.post-media img,.full-text img{max-width:100%;height:auto}.read-window-btn{min-height:44px}.bookmark-filter{display:flex;flex-wrap:nowrap;overflow-x:auto;gap:6px;margin:6px 0}.bookmark-filter button{flex:none;width:44px;height:44px;border:2px solid transparent;border-radius:50%;position:relative}.bookmark-filter button[aria-pressed=true]{border-color:var(--ink);opacity:1}.bookmark-filter button.zero{opacity:.35}.bookmark-filter small{background:var(--paper);color:var(--ink);border-radius:10px;padding:0 4px;position:absolute;right:0;bottom:0}.bkm-mark{display:flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:30px;padding:6px 12px;background:transparent;color:var(--ink);font-size:11px;flex:none;min-height:44px;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}.bkm-mark svg{width:18px;height:18px;flex:none}.bkm-mark[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}.bkm-mark[aria-pressed=true] small{color:#fff}.bkm-mark.zero{opacity:.35}.bkm-mark small{margin-left:2px;color:var(--muted)}.bookmark-colors{position:fixed;inset:auto 8px calc(16px + env(safe-area-inset-bottom));flex-wrap:wrap;z-index:10001}.bookmark-colors button{min-width:44px;min-height:44px}.bookmark-colors svg{width:24px;height:24px}.file-notice{font-size:11px;padding:6px 18px;border-bottom:1px solid var(--line)}button,.chip,summary,a{touch-action:manipulation}.reader-sheet{position:fixed;inset:0;width:100vw;width:100dvw;height:100vh;height:100dvh;margin:0;max-width:none;max-height:none;border:0;border-radius:0;padding:0;background:#fffdf8;color:var(--ink);overscroll-behavior:contain}.reader-header{display:flex;align-items:center;gap:10px;margin:0;padding:env(safe-area-inset-top) max(12px,env(safe-area-inset-right)) 0 max(18px,env(safe-area-inset-left));touch-action:pan-x;background:var(--paper);border-bottom:1px solid var(--line);height:calc(56px + env(safe-area-inset-top))}.reader-header strong{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.reader-body{height:calc(100% - 56px - env(safe-area-inset-top));overflow-y:auto;overscroll-behavior:contain;touch-action:pan-y;padding:18px max(18px,env(safe-area-inset-right)) calc(24px + env(safe-area-inset-bottom)) max(18px,env(safe-area-inset-left))}.reader-body h3{font-size:16px;font-weight:700}.reader-body .full-text{display:block;border:0;padding-top:18px;font-size:16px;line-height:1.8}.reader-body .post-media{display:grid;gap:12px}.reader-body img{max-width:100%;height:auto}.reader-body .summary,.reader-body .pin-btn,.reader-body .bookmark-btn,.reader-body .spotlight-label,.reader-body .read-window-btn{display:none}.reader-body .card-toggle{pointer-events:none}.lightbox{position:fixed;inset:0;width:100vw;width:100dvw;height:100vh;height:100dvh;max-width:none;max-height:none;margin:0;padding:0;border:0;background:rgba(10,15,24,.94);color:white;overflow:hidden;overscroll-behavior:contain}.lightbox::backdrop{background:rgba(10,15,24,.94)}.lightbox-stage{position:absolute;inset:calc(56px + env(safe-area-inset-top)) 12px calc(104px + env(safe-area-inset-bottom));display:flex;align-items:center;justify-content:center;overflow:hidden;user-select:none;touch-action:none}.lightbox-image{display:block;max-width:100%;max-height:100%;object-fit:contain;transform-origin:center;user-select:none;-webkit-user-drag:none}.lightbox-close{position:absolute;right:max(12px,env(safe-area-inset-right));top:calc(8px + env(safe-area-inset-top));width:44px;height:44px;border:0;background:none;color:#888;font-size:28px}.lightbox-strip{position:absolute;bottom:calc(32px + env(safe-area-inset-bottom));left:12px;right:12px;display:flex;gap:8px;overflow-x:auto;touch-action:manipulation}.lightbox-strip button{width:56px;height:56px;flex:none;padding:2px;border:2px solid transparent;background:var(--navy)}.lightbox-strip button[aria-pressed=true]{border-color:var(--accent)}.lightbox-strip img{width:100%;height:100%;object-fit:contain}.lightbox-help{position:absolute;bottom:env(safe-area-inset-bottom);width:100%;text-align:center;font-size:11px;color:#c3ccde;pointer-events:none}#scroll-top-btn{position:fixed;right:calc(18px + env(safe-area-inset-right));bottom:calc(18px + env(safe-area-inset-bottom));width:56px;height:56px;border-radius:50%;border:1px solid var(--line);background:var(--paper);color:var(--accent);font-size:24px;z-index:20}#toast{position:fixed;bottom:calc(88px + env(safe-area-inset-bottom));left:18px;right:18px;padding:12px;background:var(--navy);color:white;border-radius:8px;z-index:20000;text-align:center}.settings{padding:8px 0}.settings button{min-height:44px;border:1px solid var(--line);background:var(--paper);border-radius:6px}.ai-tools-section{padding:18px}.ai-tools-section summary{font-family:Georgia,"Batang","AppleMyungjo",serif;font-size:18px}.related-link{display:inline-block;padding:8px 0}.card-toggle:focus,.card-toggle:focus-visible{outline:none}.lightbox-close:focus,.lightbox-close:focus-visible{outline:none}.reader-back:focus,.reader-back:focus-visible{outline:none}.reader-actions{display:flex;gap:4px;flex:none}.reader-actions button{min-width:44px;min-height:44px;border:1px solid var(--line);border-radius:8px;background:var(--paper);font-size:18px;cursor:pointer}@media print{#reader-sheet[open]{position:static;height:auto;overflow:visible}#reader-sheet .reader-header .reader-back,#reader-sheet .reader-actions,#scroll-top-btn,#toast{display:none!important}}[hidden]{display:none!important}
/* 댓글 섹션 */
.replies{margin-top:16px;border:1px solid var(--line);border-radius:8px;background:#faf8f2}
.replies summary{padding:12px 16px;font-weight:700;font-size:13px;cursor:pointer;list-style:none;min-height:44px}
.replies summary::-webkit-details-marker{display:none}
.reply-list{padding:4px 16px 12px}
.reply{padding:10px 0;border-top:1px solid var(--line)}
.reply-head{display:flex;align-items:center;gap:8px;font-size:12px}
.reply-author{font-weight:700}
.reply-time{color:var(--muted);font-size:11px}
.thread-badge{font-size:10px;background:var(--soft);color:var(--accent);border-radius:4px;padding:2px 6px;font-weight:700}
.reply-del{margin-left:auto;width:44px;height:44px;border:0;background:none;font-size:24px;color:var(--muted);flex:none}
.reply-del:hover{color:#c0392b}
.reply-text{font-size:13px;margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.reply.deleted .reply-text{color:var(--muted);font-style:italic}
.reply[hidden]{display:none}
.reader-body .replies{margin-top:20px}
/* 인용글 섹션 */
.quoted{margin-top:16px;border:1px solid var(--line);border-radius:8px;background:#f4f6fa;border-left:3px solid var(--accent)}
.quoted summary{padding:12px 16px;font-weight:700;font-size:13px;cursor:pointer;list-style:none;min-height:44px}
.quoted summary::-webkit-details-marker{display:none}
.quoted-body{padding:4px 16px 12px}
.quoted-author{font-weight:700;font-size:12px}
.quoted-time{color:var(--muted);font-size:11px;margin-left:8px}
.quoted-text{font-size:13px;margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.quoted-media{margin-top:8px;display:grid;gap:8px}
.quoted-media img{max-width:100%;height:auto;border-radius:6px;cursor:pointer}
.quoted-link{color:inherit;text-decoration:underline;text-underline-offset:3px}
.quoted-link-out{margin-top:8px;font-size:12px}
.quoted-link-out a{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.reader-body .quoted{margin-top:20px}
.full-text.web-html{white-space:normal;overflow-wrap:break-word}
.full-text.web-html img{cursor:zoom-in}
/* 웹글 히어로: 아카이브 전역 header flex를 무력화하고 제목-저자 상하 배치 */
.full-text.web-html header.hero{display:block;padding:0 0 10px}
.full-text.web-html header.hero .badge{display:inline-block;font-size:11px;font-weight:800;color:var(--accent);background:var(--soft);padding:4px 10px;border-radius:6px;margin-bottom:10px}
.full-text.web-html header.hero h1{font-size:26px;line-height:1.35;letter-spacing:-.02em;margin:0 0 10px}
.full-text.web-html header.hero p.sub{font-size:13px;color:var(--muted);line-height:1.7;margin:0}
/* 웹글 본문: 자연스러운 문단 흐름 */
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
/* 흐름도 단계: 번호+제목을 한 줄에, 세부 설명은 아래에 */
.full-text.web-html .flow{margin:1em 0}
.full-text.web-html .phase-head{display:block;font-size:12px;font-weight:800;color:var(--accent);letter-spacing:.1em;margin:1.2em 0 .4em}
.full-text.web-html .flow-step{display:block;margin:10px 0}
.full-text.web-html .flow-dot{display:inline}
.full-text.web-html .flow-dot>span{font-weight:800}
.full-text.web-html .flow-dot>span::after{content:". "}
.full-text.web-html .flow-body{display:inline}
.full-text.web-html .flow-body b{margin-right:.25em}
.full-text.web-html .flow-body small{display:block;margin-top:3px;color:var(--muted);font-size:.92em}
/* 반응형 레이아웃: 태블릿/폴더블/데스크탑 대응 */
@media(min-width:768px){.grid,.pinned-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:18px}.wrap{max-width:960px}}
@media(min-width:1024px){.grid,.pinned-grid{grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:20px}.wrap{max-width:1200px}}
'''
MOBILE_HTML = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>아카이브 인사이트 · 모바일</title>
<link rel="apple-touch-icon" sizes="180x180" href="icon-180.png"><link rel="icon" type="image/png" sizes="192x192" href="icon-192.png"><link rel="icon" type="image/png" sizes="512x512" href="icon-512.png"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="My archive"><meta name="theme-color" content="#1a1a2e">
<link href="https://spoqa.github.io/spoqa-han-sans/css/SpoqaHanSansNeo.css" rel="stylesheet"><script src="https://accounts.google.com/gsi/client" async defer></script><style>__CSS__</style></head><body>
<div id="js-debug" hidden style="background:#b42318;color:#fff;position:fixed;top:0;left:0;right:0;z-index:30000;font-size:12px;padding:10px"></div>
<div id="file-notice" class="file-notice" hidden>파일로 직접 열람 중: 북마크 저장이 안 될 수 있어요 → 내보내기/가져오기 사용</div>
<div class="wrap"><details class="hero"><summary>아카이브 인사이트 ▾</summary><p class="eyebrow">THE ARCHIVE / VOL. 001</p><h1>아카이브 인사이트</h1><p class="intro">시장의 흐름부터 새로운 도구까지. 다시 꺼내 읽을 가치가 있는 기록들.</p><p class="top-authors">__TOP_AUTHORS__</p><div class="legend">__LEGEND__</div></details><div class="data-band"><div class="stats">__STATS__</div><div class="bar" role="img" aria-label="__DISTRIBUTION_LABEL__">__DISTRIBUTION_BAR__</div></div></div>
<div class="search-bar"><div class="wrap"><div class="search search-row"><div class="search-input"><span aria-hidden="true">⌕</span><input id="search" type="search" placeholder="제목, 본문, 저자, 주제로 검색" aria-label="아카이브 검색"></div><button type="button" id="search-mode" class="search-mode-btn" aria-pressed="false" title="검색 방식 전환: OR/AND">OR</button></div><section class="filters"><details><summary>필터 ▾</summary><div class="filter-line"><span class="label">유형</span><div class="type-tabs">__TYPES__</div></div><div class="filter-line"><span class="label">주제</span><div class="chips">__TOPICS__</div></div><div class="filter-line"><span class="label">인물</span><div class="chips">__PEOPLE__</div></div><div class="filter-line"><span class="label">마크</span><div id="bookmark-names" class="chips">__BOOKMARK_NAMES__</div></div><div class="filter-line"><span class="label">백업</span><div class="chips"><button type="button" class="chip" id="export-shortcut">💾 내보내기</button><button type="button" class="chip" id="import-shortcut">📥 가져오기</button><button type="button" class="chip" id="drive-connect">🔗 Drive 연결</button></div></div></details></section></div></div>
<main class="wrap"><section class="spotlight"><h2>인물 스포트라이트</h2><div class="rail">__SPOTLIGHT__</div></section><div class="list-heading"><h2>아카이브 탐색</h2><button id="reset" class="reset">초기화 · 전체 보기</button></div><p id="status" class="status" role="status">__STATUS__</p><div id="pinned-section"></div><div class="grid" id="articles">__ARTICLES__</div>__IB_JUWON__<div id="empty" class="empty" hidden>일치하는 글이 없습니다.</div>__AI_TOOLS__<details class="settings"><summary>설정 ▾</summary><p id="js-caps">JavaScript가 실행되지 않았습니다. Safari에서 열어주세요.</p><noscript>JavaScript가 실행되지 않았습니다. Safari에서 열어주세요.</noscript><button id="export">북마크 내보내기</button><button id="import">가져오기</button><input id="import-file" type="file" accept="application/json,.json" hidden></details><footer>스크랩 아카이브 · 나의 작은 서재</footer><noscript>카드를 펼쳐 본문을 읽을 수 있습니다. 연관글 링크로 이동하거나 이미지를 눌러 원본을 볼 수 있습니다. 검색·필터·라이트박스는 JavaScript가 필요합니다.</noscript></main>
<dialog id="reader-sheet" class="reader-sheet" aria-labelledby="reader-title"><header class="reader-header"><button type="button" class="reader-back">← 목록</button><strong id="reader-title"></strong><div class="reader-actions"><button type="button" class="reader-print" aria-label="인쇄" title="인쇄">🖨️</button><button type="button" class="reader-share-image" aria-label="이미지로 공유" title="이미지로 공유">🖼️</button><button type="button" class="reader-share-text" aria-label="HTML로 공유" title="HTML로 공유">🌐</button></div></header><div class="reader-body"></div></dialog><dialog id="rename-dialog" aria-labelledby="rename-label"><form method="dialog"><label id="rename-label" for="rename-input">북마크 이름</label><input id="rename-input" maxlength="80" autocomplete="off"><button value="cancel">취소</button><button value="save">저장</button></form></dialog><button id="scroll-top-btn" aria-label="맨 위로" hidden>↑</button><div id="toast" role="status" hidden></div><script>__JS__</script>__MEMO_MODAL_HTML__
</body></html>'''
MOBILE_JS = r'''
window.addEventListener('error',e=>{const _em=e.message||'';if(_em==='Script error.'||_em==='Script error')return;const d=document.getElementById('js-debug');if(d){d.hidden=false;d.textContent='JS 오류: '+(e.message||e.error);}});
window.addEventListener('unhandledrejection',e=>{const d=document.getElementById('js-debug');if(d){d.hidden=false;d.textContent='JS Promise 오류: '+(e.reason&&e.reason.message||e.reason);}});
let storageOK=true;try{localStorage.setItem('__t','1');localStorage.removeItem('__t');}catch(e){storageOK=false;}
const state = {type:null, topic:null, author:null, query:'', bookmarkColor:null, searchMode:'OR'};
const el = id => document.getElementById(id);
const fullOf = img => (img.closest('a.media-full-link')||{}).href || img.dataset.full || img.src;
const articles = Array.from(document.querySelectorAll('details.article'));
function render(){
 applyDeletedPosts();
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
   (!state.bookmarkColor||bookmarks[card.dataset.pinId]===state.bookmarkColor)&&qmatch;
  card.hidden=!matches;
  card.classList.toggle('featured',matches&&count===0);
  card.querySelector('.spotlight-label').hidden=!(matches&&count===0);
  if(matches)count++;
 }
 for(const button of document.querySelectorAll('button[data-kind]'))
  button.setAttribute('aria-pressed',String((state[button.dataset.kind]||'')===button.dataset.value));
 const active=[state.type==='x'?'X 게시물':state.type==='web'?'웹글':state.type==='ib'?'IB 주원':null,state.topic,state.author].filter(Boolean);
 el('status').textContent=isIb?`IB 주원 · 학업 자료실 · ${ibCount}건${query?' · 검색: '+state.query:''}`:`${count}건${active.length?' · '+active.join(' / '):''}${query?' · 검색: '+state.query:''}`;
 el('empty').hidden=isIb||count!==0;updateColorButtons();
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
 
});
el('search').addEventListener('input',event=>{state.query=event.target.value;render();});
el('search-mode').addEventListener('click',()=>{state.searchMode=state.searchMode==='AND'?'OR':'AND';const b=el('search-mode');b.textContent=state.searchMode;b.setAttribute('aria-pressed',String(state.searchMode==='AND'));render();});
el('reset').addEventListener('click',()=>{state.type=null;state.topic=null;state.author=null;state.query='';state.bookmarkColor=null;state.searchMode='OR';el('search').value='';const mb=el('search-mode');if(mb){mb.textContent='OR';mb.setAttribute('aria-pressed','false');}render();});
// Create the modal only as a progressive enhancement.
(() => {
 if(!window.HTMLDialogElement||!HTMLDialogElement.prototype.showModal||!window.PointerEvent)return;
 const modal=document.createElement('dialog');
 modal.className='lightbox';
 modal.setAttribute('aria-label','게시물 이미지 확대');
 modal.innerHTML='<button type="button" class="lightbox-close" aria-label="이미지 닫기">×</button><div class="lightbox-stage"><img class="lightbox-image" alt="" draggable="false"></div><div class="lightbox-strip"></div><p class="lightbox-help">핀치 또는 휠로 확대 · 드래그로 이동 · ESC로 닫기</p>';
 document.body.append(modal);
 const stage=modal.querySelector('.lightbox-stage');
 const image=modal.querySelector('.lightbox-image');
 const close=modal.querySelector('.lightbox-close');
 const pointers=new Map();
 let scale=1,x=0,y=0,gesture=null,opener=null,previousOverflow='';
 let dragged=false,startPoint=null,startedOnBackground=false; let gallery=[],current=0,lastTap=0,multi=false;
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
  gallery=[...source.closest('.reader-body,details.article').querySelectorAll('.media-full-link img,.quoted-media img,.full-text.web-html img')].filter(img=>!img.closest('a')||img.closest('a.media-full-link'));current=gallery.indexOf(source);select(current);paint();
  previousOverflow=document.body.style.overflow;
  modal.showModal();close.focus();
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
  dragged=false;startedOnBackground=event.target===stage;multi=pointers.size>0;
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
 function release(event){
  if(event.type==='pointerup'&&pointers.has(event.pointerId)&&!multi&&startPoint){
   const dx=event.clientX-startPoint.x,dy=event.clientY-startPoint.y;
   if(scale===1&&Math.abs(dx)>60&&Math.abs(dx)>Math.abs(dy)*1.5){select(current+(dx<0?1:-1));}
   else if(scale===1&&dy>90&&Math.abs(dy)>Math.abs(dx)*1.5){dismiss();}
   else if(!dragged&&!startedOnBackground){const now=Date.now();if(now-lastTap<300){zoom(scale===1?2.5:1,{x:event.clientX,y:event.clientY});lastTap=0;}else lastTap=now;}
  }
  pointers.delete(event.pointerId);gesture=sample();
 }
 stage.addEventListener('pointerup',release);
 stage.addEventListener('pointercancel',release);
 stage.addEventListener('lostpointercapture',release);
 function select(index){
  current=(index+gallery.length)%gallery.length;scale=1;x=0;y=0;lastTap=0;
  image.src=fullOf(gallery[current]);image.alt=gallery[current].alt;paint();
  const strip=modal.querySelector('.lightbox-strip');strip.replaceChildren();
  gallery.forEach((item,i)=>{const button=document.createElement('button');button.type='button';button.setAttribute('aria-label',`이미지 ${i+1}`);button.setAttribute('aria-pressed',String(i===current));const thumb=document.createElement('img');thumb.src=item.dataset.thumb||item.src;thumb.alt='';button.append(thumb);button.onclick=()=>select(i);strip.append(button);});
 }
 modal.addEventListener('close',()=>{modal.querySelector('.lightbox-strip').replaceChildren();gallery=[];});
 document.addEventListener('click',event=>{let source=event.target.closest('.media-full-link img')||event.target.closest('.media-full-link')?.querySelector('img')||event.target.closest('.quoted-media img');if(!source){const webImg=event.target.closest('.full-text.web-html img');if(webImg&&!webImg.closest('a'))source=webImg;}if(!source)return;event.preventDefault();event.stopPropagation();open(source);});
 document.addEventListener('keydown',event=>{const t=event.target;if(t.matches('.media-full-link img,.quoted-media img,.full-text.web-html img')&&(!t.closest('a')||t.closest('a.media-full-link'))&&(event.key==='Enter'||event.key===' ')){event.preventDefault();open(t);}});
})();

// Shared PC storage schema; keep in-memory state when file-origin storage fails.
const COLORS=[{name:'빨강',code:'#e74c3c'},{name:'주황',code:'#e67e22'},{name:'노랑',code:'#f1c40f'},{name:'초록',code:'#27ae60'},{name:'파랑',code:'#3498db'},{name:'남색',code:'#34495e'},{name:'보라',code:'#9b59b6'}];
function load(key,fallback){try{return JSON.parse(localStorage.getItem(key)||JSON.stringify(fallback));}catch(e){return fallback;}}
let pinned=load('archive-pinned',[]),bookmarks=load('archive-bookmarks',{}),names=load('archive-bookmark-names',{});
/* 차트 이미지 자동 초록 북마크 (사용자 지정 색상이 없을 때만) */
(function(){const GREEN='#27ae60';let changed=false;document.querySelectorAll('details.article[data-chart="1"]').forEach(card=>{const pid=card.dataset.pinId;if(pid&&!bookmarks[pid]){bookmarks[pid]=GREEN;changed=true;}});if(changed)save('archive-bookmarks',bookmarks);})();
let deletedReplies=load('archive-deleted-replies',[]);
if(!Array.isArray(deletedReplies))deletedReplies=[];
let deletedPosts=load('archive-deleted-posts',[]);
if(!Array.isArray(deletedPosts)||deletedPosts.some(x=>typeof x!=='string'))deletedPosts=[];
if(!Array.isArray(pinned))pinned=[];if(!bookmarks||typeof bookmarks!=='object'||Array.isArray(bookmarks))bookmarks={};if(!names||typeof names!=='object'||Array.isArray(names))names={};
let toastTimer;function toast(message){el('toast').textContent=message;el('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el('toast').hidden=true,4000);}
let saveWarned=false;
function updateStorageNotice(){const notice=el('file-notice');notice.hidden=storageOK&&location.protocol!=='file:';if(!storageOK)notice.textContent='파일로 직접 열람하는 경우 내보내기/가져오기를 사용하세요. 이 뷰어에서는 핀·북마크가 기기에 저장되지 않습니다(임시 저장만 됨). Safari에서 열면 정상 저장됩니다.';}
function save(key,value){try{if(!storageOK)throw Error('storage unavailable');localStorage.setItem(key,JSON.stringify(value));}catch(e){storageOK=false;updateStorageNotice();if(!saveWarned){saveWarned=true;toast('저장 실패: 브라우저 설정을 확인하세요');}}}
function updateColorButtons(){const allBtn=document.getElementById('bkm-all');if(allBtn)allBtn.setAttribute('aria-pressed',String(!state.bookmarkColor));document.querySelectorAll('[data-color-filter]').forEach(button=>{const code=button.dataset.colorFilter;const count=articles.filter(card=>bookmarks[card.dataset.pinId]===code).length;button.classList.toggle('zero',count===0);button.setAttribute('aria-disabled',String(count===0));button.setAttribute('aria-pressed',String(state.bookmarkColor===code));button.querySelector('small').textContent=count;const label=names[code]||COLORS.find(c=>c.code===code).name;button.title=label+' · 길게 눌러 이름 변경';button.setAttribute('aria-label',label+' 북마크 '+count+'건');const strong=button.querySelector('strong');if(strong)strong.textContent=label;});}
function renameColor(color){const dialog=el('rename-dialog'),input=el('rename-input');input.value=names[color.code]||color.name;dialog.returnValue='';dialog.onclose=()=>{if(dialog.returnValue==='save'&&input.value.trim()){names[color.code]=input.value.trim();save('archive-bookmark-names',names);updateColorButtons();}};dialog.showModal();input.focus();}
function colorButtons(){const container=el('bookmark-names');if(!container.querySelector('[data-color-filter]'))COLORS.forEach(c=>{const button=document.createElement('button');button.type='button';button.dataset.colorFilter=c.code;button.className='bkm-mark';button.setAttribute('aria-label',c.name+' 북마크');button.innerHTML='<svg viewBox="0 0 24 24" style="fill:'+c.code+'" aria-hidden="true"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg>';const label=document.createElement('strong');label.textContent=c.name;button.append(label);button.append(document.createElement('small'));container.append(button);});container.querySelectorAll('[data-color-filter]').forEach(button=>{const c=COLORS.find(color=>color.code===button.dataset.colorFilter);let timer,long=false,start;button.onpointerdown=e=>{start={x:e.clientX,y:e.clientY};long=false;timer=setTimeout(()=>{long=true;navigator.vibrate?.(10);renameColor(c);},500);};const cancel=()=>clearTimeout(timer);button.onpointerup=cancel;button.onpointercancel=cancel;button.onpointerleave=cancel;button.onpointermove=e=>{if(start&&Math.hypot(e.clientX-start.x,e.clientY-start.y)>8)cancel();};button.onclick=()=>{if(long){long=false;return;}if(!articles.some(card=>bookmarks[card.dataset.pinId]===c.code))return;state.bookmarkColor=state.bookmarkColor===c.code?null:c.code;render();};});const allBtn=document.getElementById('bkm-all');if(allBtn&&!allBtn.dataset.bound){allBtn.dataset.bound='1';allBtn.onclick=()=>{state.bookmarkColor=null;render();};}updateColorButtons();}
function renderSaved(){applyDeletedPosts();const section=el('pinned-section');articles.forEach(card=>el('articles').append(card));section.replaceChildren();const valid=pinned.filter(pid=>!deletedPosts.includes(pid)&&articles.some(card=>card.dataset.pinId===pid));if(valid.length){const h=document.createElement('h2');h.textContent='📌 고정된 글';const grid=document.createElement('div');grid.className='pinned-grid';section.append(h,grid);valid.forEach(pid=>grid.append(articles.find(card=>card.dataset.pinId===pid)));}articles.forEach(card=>{const pid=card.dataset.pinId,color=bookmarks[pid];card.querySelector('.pin-btn').classList.toggle('pinned',pinned.includes(pid));card.querySelector('.pin-btn').setAttribute('aria-pressed',String(pinned.includes(pid)));const button=card.querySelector('.bookmark-btn');button.classList.toggle('bookmarked',!!color);if(color){card.dataset.bookmarkColor=color;card.style.setProperty('--bkm-color',color);button.style.setProperty('--bkm-color',color);}else{delete card.dataset.bookmarkColor;card.style.removeProperty('--bkm-color');button.style.removeProperty('--bkm-color');}});render();}
function applyDeletedPosts(){articles.forEach(card=>{card.hidden=deletedPosts.includes(card.dataset.pinId);});}
document.addEventListener('click',event=>{const del=event.target.closest('.delete-btn');if(!del)return;event.preventDefault();event.stopPropagation();const card=del.closest('details.article');if(!card)return;const pid=card.dataset.pinId;if(!pid||deletedPosts.includes(pid))return;if(!confirm('이 게시글을 삭제하시겠습니까? 복원할 수 없습니다.'))return;deletedPosts.push(pid);save('archive-deleted-posts',deletedPosts);renderSaved();toast('게시글을 삭제했습니다 (복원 불가)');});
function applyDeletedReplies(scope){(scope||document).querySelectorAll('.reply[data-reply-id]').forEach(node=>{if(!deletedReplies.includes(node.dataset.replyId))return;node.hidden=false;node.classList.add('deleted');const text=node.querySelector('.reply-text');if(text)text.textContent='삭제된 댓글입니다.';node.querySelector('.reply-del')?.remove();});}
document.addEventListener('click',event=>{const del=event.target.closest('.reply-del');if(!del)return;event.preventDefault();event.stopPropagation();const rid=del.dataset.replyId;if(!rid||deletedReplies.includes(rid))return;if(!confirm('이 댓글을 삭제하시겠습니까? 복원할 수 없습니다.'))return;deletedReplies.push(rid);save('archive-deleted-replies',deletedReplies);applyDeletedReplies();toast('댓글을 삭제했습니다 (복원 불가)');});
let popup=null,lastHandled=null,lastHandledAt=0;
function handleSaved(event){const button=event.target.closest('.pin-btn,.bookmark-btn');if(!button)return;event.preventDefault();event.stopPropagation();if(event.type==='click'&&lastHandled===button&&Date.now()-lastHandledAt<700)return;lastHandled=button;lastHandledAt=Date.now();const card=button.closest('details.article'),pid=card.dataset.pinId;popup?.remove();popup=null;if(button.matches('.pin-btn')){pinned=pinned.includes(pid)?pinned.filter(x=>x!==pid):[...pinned,pid];save('archive-pinned',pinned);renderSaved();return;}popup=document.createElement('div');popup.className='bookmark-colors';COLORS.forEach(c=>{const b=document.createElement('button');b.style.setProperty('--c',c.code);b.setAttribute('aria-label',names[c.code]||c.name);b.innerHTML='<svg viewBox="0 0 24 24"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg>';b.onclick=()=>{bookmarks[pid]=c.code;save('archive-bookmarks',bookmarks);popup.remove();popup=null;renderSaved();};popup.append(b);});const clear=document.createElement('button');clear.className='clear-btn';clear.textContent='해제';clear.onclick=()=>{delete bookmarks[pid];save('archive-bookmarks',bookmarks);popup.remove();popup=null;renderSaved();};popup.append(clear);document.body.append(popup);}
document.addEventListener('pointerup',event=>{if(event.pointerType!=='mouse')handleSaved(event);});document.addEventListener('click',handleSaved);
document.addEventListener('click',event=>{if(popup&&!event.target.closest('.bookmark-colors,.bookmark-btn')){popup.remove();popup=null;}});
const sheet=el('reader-sheet'),body=sheet.querySelector('.reader-body');let readerOpener=null;
function openReader(card){readerOpener=card;body.querySelectorAll('img').forEach(img=>img.removeAttribute('src'));body.replaceChildren();const clone=card.cloneNode(true);clone.querySelector('summary').replaceWith(...clone.querySelector('summary').childNodes);const _mb=clone.querySelector('.memo-btn');const _mid=_mb?_mb.dataset.memoId:'';const _mt=_mb?_mb.dataset.memoTitle:'';clone.querySelectorAll('.pin-btn,.bookmark-btn,.bookmark-colors,.delete-btn,.read-window-btn,.summary,.spotlight-label,.memo-btn').forEach(node=>node.remove());body.append(...clone.childNodes);if(_mid){const _nb=document.createElement('button');_nb.type='button';_nb.className='memo-btn memo-text-btn';_nb.dataset.memoId=_mid;_nb.dataset.memoTitle=_mt;_nb.textContent='📝 메모';const _h3=body.querySelector('h3');if(_h3){_h3.after(_nb);}else{body.prepend(_nb);}updateMemoBtn(_mid);}applyDeletedReplies(body);body.querySelectorAll('img').forEach(img=>{img.dataset.thumb=img.src;img.dataset.lightbox=img.dataset.lightbox||'inline';img.src=fullOf(img);img.classList.add('lightbox-trigger');img.tabIndex=0;img.setAttribute('role','button');});el('reader-title').textContent=card.querySelector('h3').textContent;if(!sheet.open)sheet.showModal();body.scrollTop=0;sheet.querySelector('.reader-back').focus({preventScroll:true});body.querySelectorAll('img').forEach(img=>{img.addEventListener('load',()=>{body.scrollTop=0;},{once:true});});}
function closeReader(){sheet.close();}
sheet.querySelector('.reader-back').onclick=closeReader;
/* 읽기 화면: 인쇄/내보내기/공유 */
function readerArticleData(){const card=readerOpener;if(!card)return null;const title=((card.querySelector('h3')||{}).textContent||'').trim();const author=((card.querySelector('.author')||{}).textContent||'').trim();const date=((card.querySelector('.meta')||{}).textContent||'').trim();const bodyEl=sheet.querySelector('.reader-body');const text=((bodyEl.querySelector('.full-text')||{}).innerText||'').trim();const imgs=Array.from(bodyEl.querySelectorAll('img')).map(i=>i.src).filter(Boolean);return{title:title,author:author,date:date,text:text,imgs:imgs};}
function shareAsImage(d){toast('이미지 생성 중...');const canvas=document.createElement('canvas');const ctx=canvas.getContext('2d');const W=800;const pad=40;const MAX_BODY_LINES=60;ctx.font='bold 28px -apple-system,"Apple SD Gothic Neo",sans-serif';const titleLines=wrapText(ctx,d.title,W-pad*2);ctx.font='20px -apple-system,"Apple SD Gothic Neo",sans-serif';let bodyLines=wrapText(ctx,d.text,W-pad*2);let truncated=false;if(bodyLines.length>MAX_BODY_LINES){bodyLines=bodyLines.slice(0,MAX_BODY_LINES);bodyLines.push('... (이하 생략)');truncated=true;}const lineH=34;const titleH=titleLines.length*40;const bodyH=bodyLines.length*lineH;canvas.width=W;canvas.height=pad*2+titleH+20+bodyH+60;ctx.fillStyle='#f6f2e9';ctx.fillRect(0,0,canvas.width,canvas.height);let y=pad+30;ctx.fillStyle='#222b3b';ctx.font='bold 28px -apple-system,"Apple SD Gothic Neo",sans-serif';titleLines.forEach(l=>{ctx.fillText(l,pad,y);y+=40;});y+=10;ctx.strokeStyle='#ddd6ca';ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(W-pad,y);ctx.stroke();y+=30;ctx.font='20px -apple-system,"Apple SD Gothic Neo",sans-serif';ctx.fillStyle='#222b3b';bodyLines.forEach(l=>{ctx.fillText(l,pad,y);y+=lineH;});canvas.toBlob(blob=>{if(!blob){toast('이미지 생성 실패');return;}const file=new File([blob],'archive-post.png',{type:'image/png'});const fname=d.title.slice(0,30).replace(/[\\/:*?"<>|]/g,'')+'.png';if(navigator.share&&navigator.canShare&&navigator.canShare({files:[file]})){navigator.share({files:[file],title:d.title}).catch(()=>{});}else{const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),5000);toast('이미지로 저장했습니다');}},'image/png');}
function wrapText(ctx,text,maxW){const out=[];const paras=text.trim().split('\n');let prevEmpty=false;paras.forEach((para)=>{const isEmpty=!para.trim();if(isEmpty){if(!prevEmpty&&out.length>0)out.push('');prevEmpty=true;return;}prevEmpty=false;const words=para.split(/\s+/);let line='';const pushLine=()=>{if(line){out.push(line);line='';}};words.forEach(w=>{if(!w)return;if(ctx.measureText(w).width>maxW){pushLine();let part='';for(const ch of w){const t=part+ch;if(ctx.measureText(t).width>maxW&&part){out.push(part);part=ch;}else{part=t;}}if(part)out.push(part);return;}const t=line?line+' '+w:w;if(ctx.measureText(t).width>maxW){pushLine();line=w;}else{line=t;}});pushLine();});return out;}
function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function shareAsHtml(d){const meta=[d.author,d.date].filter(Boolean).join(' · ');const metaHtml=meta?'<div class="meta">'+escHtml(meta)+'</div>':'';const html='<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escHtml(d.title)+'</title><style>body{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;max-width:700px;margin:0 auto;padding:20px;line-height:1.7;color:#222;}h1{font-size:1.4em;border-bottom:2px solid #222;padding-bottom:10px;}img{max-width:100%;height:auto;margin:10px 0;}.meta{color:#888;font-size:0.85em;margin-bottom:20px;}</style></head><body><h1>'+escHtml(d.title)+'</h1>'+metaHtml+'<div>'+escHtml(d.text).replace(/\n/g,'<br>')+'</div>'+d.imgs.map(u=>'<img src="'+escHtml(u)+'">').join('')+'<hr><p><a href="'+escHtml(location.href)+'">원본 보기</a></p></body></html>';const blob=new Blob([html],{type:'text/html'});const fname=d.title.slice(0,30).replace(/[\\/:*?"<>|]/g,'')+'.html';const file=new File([blob],fname,{type:'text/html'});if(navigator.share&&navigator.canShare&&navigator.canShare({files:[file]})){navigator.share({files:[file],title:d.title}).catch(()=>{});}else{const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),5000);toast('HTML로 저장했습니다');}}
sheet.querySelector('.reader-print').onclick=()=>{window.print();};

sheet.querySelector('.reader-share-text').onclick=()=>{const d=readerArticleData();if(!d||!d.title)return;shareAsHtml(d);};
sheet.querySelector('.reader-share-image').onclick=()=>{const d=readerArticleData();if(!d||!d.title)return;shareAsImage(d);};sheet.addEventListener('close',()=>{body.querySelectorAll('img').forEach(img=>img.removeAttribute('src'));body.replaceChildren();readerOpener?.querySelector('summary').focus({preventScroll:true});});
document.addEventListener('click',event=>{if(Date.now()-lastHandledAt<700)return;const related=event.target.closest('.related-link,.quoted-link');if(related){event.preventDefault();const target=articles.find(card=>card.dataset.idx===related.dataset.targetIdx);if(target)openReader(target);return;}const trigger=event.target.closest('.card-toggle,.read-window-btn');if(!trigger||event.target.closest('.pin-btn,.bookmark-btn,.bookmark-colors,.delete-btn'))return;event.preventDefault();openReader(trigger.closest('details.article'));});
let headerStart=null;const header=sheet.querySelector('.reader-header');header.onpointerdown=e=>{if(e.target.closest('button'))return;headerStart={id:e.pointerId,x:e.clientX,y:e.clientY};header.setPointerCapture(e.pointerId);};header.onpointerup=e=>{if(headerStart&&e.clientY-headerStart.y>80&&Math.abs(e.clientX-headerStart.x)<80)closeReader();headerStart=null;};header.onpointercancel=()=>headerStart=null;
updateStorageNotice();el('js-caps').textContent=`dialog:${window.HTMLDialogElement&&HTMLDialogElement.prototype.showModal?'OK':'NG'} · pointer:${window.PointerEvent?'OK':'NG'} · 저장:${storageOK?'OK':'NG'} · 프로토콜:${location.protocol}`;colorButtons();applyDeletedReplies();renderSaved();
el('scroll-top-btn').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});window.addEventListener('scroll',()=>el('scroll-top-btn').hidden=window.scrollY<=600,{passive:true});
el('export').onclick=()=>{const blob=new Blob([JSON.stringify({pinned,bookmarks,names,deletedReplies,deletedPosts,exportedAt:new Date().toISOString()},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='archive-bookmarks.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);};
el('import').onclick=()=>el('import-file').click();el('import-file').onchange=async event=>{try{const file=event.target.files[0];if(!file)return;const data=JSON.parse(await file.text()),codes=COLORS.map(c=>c.code),dr=data.deletedReplies||[],dp=data.deletedPosts||[];if(!Array.isArray(data.pinned)||data.pinned.some(x=>typeof x!=='string')||!data.bookmarks||Array.isArray(data.bookmarks)||typeof data.bookmarks!=='object'||Object.values(data.bookmarks).some(x=>!codes.includes(x))||!data.names||Array.isArray(data.names)||typeof data.names!=='object'||Object.entries(data.names).some(([k,v])=>!codes.includes(k)||typeof v!=='string')||!Array.isArray(dr)||dr.some(x=>typeof x!=='string')||!Array.isArray(dp)||dp.some(x=>typeof x!=='string'))throw Error('북마크 JSON 형식을 확인하세요');if(!confirm('현재 핀·북마크·이름·삭제된 댓글/게시글을 가져온 파일로 덮어쓸까요?'))return;const refreshReplies=deletedReplies.some(rid=>!dr.includes(rid));pinned=[...new Set(data.pinned)];bookmarks=data.bookmarks;names=data.names;deletedReplies=[...new Set(dr)];deletedPosts=[...new Set(dp)];save('archive-pinned',pinned);save('archive-bookmarks',bookmarks);save('archive-bookmark-names',names);save('archive-deleted-replies',deletedReplies);save('archive-deleted-posts',deletedPosts);applyDeletedReplies();applyDeletedPosts();renderSaved();if(refreshReplies&&storageOK)location.reload();}catch(error){toast('가져오기 실패: '+error.message);}finally{event.target.value='';}};
// 필터 메뉴의 백업 바로가기 → 하단 설정의 내보내기/가져오기 실행
el('export-shortcut').onclick=()=>el('export').click();
el('import-shortcut').onclick=()=>el('import').click();
/* Google Drive 자동 동기화 (기존 기능과 격리: 실패해도 메인 스크립트에 영향 없음) */
try{(function(){
 const DRIVE_CLIENT_ID='43687402502-rlid8pjoakcigrn6tpcs58elo357vtfr.apps.googleusercontent.com';
 const DRIVE_SCOPE='https://www.googleapis.com/auth/drive.appdata';
 const STATE_KEYS=['archive-pinned','archive-bookmarks','archive-bookmark-names','archive-deleted-replies','archive-deleted-posts'];
 const TS_KEY='archive-sync-updatedAt';
 const get=(k,d)=>{try{const v=JSON.parse(localStorage.getItem(k));return v==null?d:v}catch(e){return d}};
 const getTs=()=>{try{return localStorage.getItem(TS_KEY)||''}catch(e){return ''}};
 const setTs=v=>{try{localStorage.setItem(TS_KEY,v)}catch(e){}};
 let tokenClient=null,accessToken=null,fileId=null,connected=false,lastHash='';
 const stateHash=()=>{try{return STATE_KEYS.map(k=>localStorage.getItem(k)||'').join('|')}catch(e){return ''}};
 const collectState=()=>({pinned:get('archive-pinned',[]),bookmarks:get('archive-bookmarks',{}),names:get('archive-bookmark-names',{}),deletedReplies:get('archive-deleted-replies',[]),deletedPosts:get('archive-deleted-posts',[]),updatedAt:new Date().toISOString()});
 const applyState=d=>{
  try{
   if(Array.isArray(d.pinned))localStorage.setItem('archive-pinned',JSON.stringify(d.pinned));
   if(d.bookmarks&&typeof d.bookmarks==='object')localStorage.setItem('archive-bookmarks',JSON.stringify(d.bookmarks));
   const nms=d.names||d.bookmarkNames;
   if(nms&&typeof nms==='object')localStorage.setItem('archive-bookmark-names',JSON.stringify(nms));
   if(Array.isArray(d.deletedPosts)&&d.deletedPosts.every(x=>typeof x==='string'))localStorage.setItem('archive-deleted-posts',JSON.stringify([...new Set(d.deletedPosts)]));
   if(Array.isArray(d.deletedReplies))localStorage.setItem('archive-deleted-replies',JSON.stringify(d.deletedReplies));
   if(d.updatedAt)setTs(d.updatedAt);
  }catch(e){}
 };
 const drvBtn=el('drive-connect');
 const setStatus=t=>{if(drvBtn)drvBtn.textContent=t};
 const api=async(path,opts={})=>{
  const r=await fetch('https://www.googleapis.com'+path,{...opts,headers:{...(opts.headers||{}),Authorization:'Bearer '+accessToken}});
  if(!r.ok)throw new Error('Drive API '+r.status);
  return r;
 };
 const findFile=async()=>{
  const r=await api("/drive/v3/files?q="+encodeURIComponent("name='archive-state.json' and 'appDataFolder' in parents")+"&spaces=appDataFolder&fields=files(id)");
  const j=await r.json();
  return (j.files&&j.files[0])||null;
 };
 const uploadState=async data=>{
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
 window.DriveSync={
  _initTries:0,
  _inited:false,
  init(onReady){
   const self=window.DriveSync;
   if(self._inited){if(onReady)try{onReady()}catch(e){}return true}
   if(typeof google==='undefined'||!google.accounts||!google.accounts.oauth2){
    if(self._initTries<20){self._initTries++;setTimeout(()=>self.init(onReady),500);}
    else{toast('Google 로그인을 불러오지 못했습니다. 네트워크를 확인한 뒤 다시 시도하세요.');}
    return false;
   }
   self._inited=true;
   tokenClient=google.accounts.oauth2.initTokenClient({client_id:DRIVE_CLIENT_ID,scope:DRIVE_SCOPE,callback:async resp=>{
    if(resp&&resp.access_token){
     accessToken=resp.access_token;connected=true;
     setStatus('☁️ Drive 연결됨');
     try{
      const f=await findFile();
      if(f){
       fileId=f.id;
       const r=await api('/drive/v3/files/'+f.id+'?alt=media');
       const remote=await r.json();
       const localTs=getTs();
       if(remote&&remote.updatedAt&&(!localTs||remote.updatedAt>localTs)){
        applyState(remote);lastHash=stateHash();location.reload();return;
       }
      }
      lastHash=stateHash();
      setInterval(async()=>{
       if(!connected)return;
       const h=stateHash();
       if(h!==lastHash){try{await uploadState(collectState())}catch(e){}}
      },2000);
     }catch(e){setStatus('☁️ Drive 오류')}
    }
   }});
   if(onReady)try{onReady()}catch(e){}
  },
  connect(){
   if(!tokenClient){
    toast('Google 로그인을 불러오는 중입니다…');
    window.DriveSync.init(()=>{try{tokenClient.requestAccessToken({prompt:'consent'})}catch(e){toast('Drive 연결 실패: '+(e&&e.message||e))}});
    return;
   }
   tokenClient.requestAccessToken({prompt:'consent'});
  }
 };
 if(drvBtn)drvBtn.onclick=()=>window.DriveSync.connect();
 /* init은 "Drive 연결" 버튼 클릭 시에만 실행 (페이지 로드 시 자동 실행 안 함) */
})();}catch(driveErr){try{console.warn('DriveSync disabled:',driveErr&&driveErr.message)}catch(e){}}
'''

def article_html_mobile(article, featured=False, idx=0, related=None, quoted_map=None):
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
            items.append(f'<li><a href="#card-{r.get("_idx", "")}" class="related-link" data-target-idx="{r.get("_idx", "")}">{escape(r_title)}</a> <span class="ai-author">— {escape(r_author)}</span></li>')
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
        imgs.append(f'<a class="media-full-link" href="{escape(full)}" target="_blank" rel="noopener">'
                    f'<img src="{escape(thumb)}" data-lightbox="{lid}" class="lightbox-trigger" tabindex="0" role="button" alt="{escape(author)} 게시물 이미지 {index + 1}" loading="lazy"></a>')
    images = "".join(imgs)
    media = f'<div class="post-media">{images}</div>' if images else ""
    # 인용글 섹션 (quoted) — 기본 표시(open), 접기 가능
    quoted_html = ""
    q = article.get("quoted")
    if isinstance(q, dict):
        qtext = (q.get("fullText") or "").strip()
        qauthor = q.get("author") or {}
        qhandle = (qauthor.get("handle") or "").strip()
        qname_raw = qauthor.get("name") or qhandle or ""
        qwhen = q.get("createdAt") or ""
        qtid = str(q.get("tweetId") or "").strip()
        qurl = (q.get("sourceUrl") or "").strip()
        # 본문 텍스트에서 x.com/status/{id} 추출 시도 (URL 미확인 케이스 대응)
        if not qtid:
            _qm = re.search(r'x\.com/\w+/status/(\d+)', qtext)
            if _qm:
                qtid = _qm.group(1)
        # URL 구성: sourceUrl > handle+tid > tid
        if not qurl and qtid:
            _qh = qhandle.lstrip("@")
            qurl = f"https://x.com/{_qh}/status/{qtid}" if _qh else f"https://x.com/i/status/{qtid}"
        # 작성자명 정리: "(URL 미확인)"이면 handle 또는 기본값으로
        if "(URL 미확인)" in qname_raw:
            qname = qhandle if qhandle else ""
        else:
            qname = qname_raw
        qimgs = []
        for m in (q.get("media") or []):
            mu = m if isinstance(m, str) else (m.get("url") or m.get("src") or "" if isinstance(m, dict) else "")
            if mu:
                qimgs.append(f'<img src="{escape(mu)}" loading="lazy" alt="인용글 이미지" class="lightbox-trigger" data-lightbox="quoted" tabindex="0" role="button">')
        qmedia_html = f'<div class="quoted-media">{"".join(qimgs)}</div>' if qimgs else ""
        # 텍스트도 이미지도 없으면 렌더링 안 함
        if qtext or qimgs:
            qlabel_text = f'🔗 인용글 · {escape(qname)}' if qname else '🔗 인용글'
            # 스크랩된 글이면 내부 링크로 연결
            _qidx = (quoted_map or {}).get(qtid) if qtid else None
            if _qidx is not None:
                qlabel = f'<a href="#card-{_qidx}" class="quoted-link" data-target-idx="{_qidx}">{qlabel_text}</a>'
            else:
                qlabel = qlabel_text
            qlink_html = (f'<p class="quoted-link-out"><a href="{escape(qurl)}" target="_blank" rel="noopener">원문 보기</a></p>' if qurl else '')
            quoted_html = (f'<details class="quoted" open><summary>{qlabel}</summary>'
                           f'<div class="quoted-body">'
                           f'<div><span class="quoted-author">{escape(qname)}</span>'
                           f'<span class="quoted-time">{escape(qwhen)}</span></div>'
                           + (f'<p class="quoted-text">{escape(qtext)}</p>' if qtext else '')
                           + qmedia_html + qlink_html + '</div></details>')
    # 댓글 섹션 (replies)
    replies_html = ""
    replies = [r for r in (article.get("replies") or []) if isinstance(r, dict)]
    if replies:
        items = []
        for r in replies:
            ra = r.get("author") or {}
            rname = ra.get("name") or ra.get("handle") or ""
            rtext = r.get("fullText") or ""
            rwhen = r.get("createdAt") or ""
            rtid = str(r.get("tweetId") or "")
            badge = ' <span class="thread-badge">스레드</span>' if r.get("isThread") else ""
            items.append(
                f'<div class="reply" data-reply-id="{escape(rtid)}">'
                f'<div class="reply-head"><span class="reply-author">{escape(rname)}</span>'
                f'<span class="reply-time">{escape(rwhen)}</span>{badge}'
                f'<button type="button" class="reply-del" data-reply-id="{escape(rtid)}" aria-label="댓글 삭제" title="댓글 삭제">×</button></div>'
                f'<p class="reply-text">{escape(rtext)}</p></div>')
        replies_html = (f'<details class="replies"><summary>💬 댓글 {len(replies)}개</summary>'
                        f'<div class="reply-list">{"".join(items)}</div></details>')
    url = article.get("sourceUrl" if is_x else "source_url", "")
    return (f'<details id="card-{idx}" class="article{" featured" if featured else ""}{" x-card" if is_x else ""}" '
            f'data-type="{escape(article["type"])}" data-author="{escape(author)}" data-topics="{escape(chr(10).join(topics))}" data-idx="{article.get("_idx", "")}" data-pin-id="{escape(str(article.get("id") or article.get("tweetId") or article.get("sourceUrl") or ""))}"{" data-chart=\"1\"" if article.get("has_chart") else ""}>'
            '<summary class="card-toggle">'
            f'<button type="button" class="pin-btn" aria-label="상단 고정" title="상단 고정"><svg viewBox="0 0 24 24"><path d="M9 4h6l-1 7 3 3v2H7v-2l3-3-1-7z"/><path d="M12 14v7"/></svg></button>'
            f'<button type="button" class="bookmark-btn" aria-label="북마크" title="북마크"><svg viewBox="0 0 24 24"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg></button>'
            f'<button type="button" class="delete-btn" aria-label="삭제" title="삭제"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg></button>'
            f'{memo_button(str(article.get("id") or article.get("tweetId") or article.get("sourceUrl") or idx), title)}'
            f'{memo_button(str(article.get("id") or article.get("tweetId") or article.get("sourceUrl") or ""), title)}'
            f'<div class="bookmark-colors" hidden></div>'
            f'<div class="spotlight-label"{"" if featured else " hidden"}>LATEST PICK · 가장 최근 글</div>'
            f'<div class="type-badge">{"𝕏 · X 게시물" if is_x else "웹글 · WEB ARTICLE"}</div>'
            f'<h3>{escape(title)}{handle}</h3><div class="meta">{escape(meta)}</div>'
            f'<p class="summary">{escape(summary)}</p><div class="tags">{"".join(tags)}</div>'
            '</summary>'
            f'<div class="full-text{" web-html" if full_html else ""}">{linkify_html(full_html) if full_html else linkify(full_text)}</div>{url_inline_html(article)}{media}{quoted_html}{replies_html}{related_html}'
            f'<div class="byline"><span class="author">{escape(author)}</span>'
            f'<button type="button" class="read-window-btn" data-idx="{idx}">새 창에서 읽기 ⧉</button>'
            f'<a class="read-link" href="{escape(url)}" target="_blank" rel="noopener noreferrer">원문 보기 ↗</a></div>'
            '</details>')

def render_html_mobile(articles):
    articles = sorted(articles, key=lambda article: article["sort_time"], reverse=True)
    # AI툴은 카드가 아닌 하단 목록으로 분리
    ai_tools = [a for a in articles if "AI툴" in topics_of(a)]
    main_articles = articles
    # 카드가 있는 게시물만 추천하여 모든 연관 링크에 타겟을 보장
    related_map = compute_related(main_articles)
    # main_articles의 인덱스를 전체 인덱스로 매핑
    id_to_idx = {id(a): i for i, a in enumerate(articles)}
    # 각 article에 전체 인덱스 부여 (연관 링크 타겟용)
    for a in articles:
        a["_idx"] = id_to_idx[id(a)]
    # 인용글 tweetId → _idx 매핑 (인용글 내부 연동용)
    quoted_map = {}
    for a in articles:
        _tid = str(a.get("id") or "").strip()
        if _tid and _tid not in quoted_map:
            quoted_map[_tid] = a["_idx"]
    people = counts(article.get("author") for article in articles)
    topics = counts(topic for article in articles for topic in set(topics_of(article)))
    total = sum(count for _, count in topics)
    colors = ["#d98069", "#c9b591", "#8da9a4", "#64758f"]
    bar, legend = [], []
    for index, (name, count) in enumerate(topics):
        color = colors[index % len(colors)]
        bar.append(f'<span style="width:{count / total * 100:.6f}%;background:{color}" title="{escape(name)} {count}건"></span>')
        legend.append(f'<span class="legend-item" style="--color:{color}">{escape(name)} {count}</span>')
    bookmark_colors = [("빨강", "#e74c3c"), ("주황", "#e67e22"), ("노랑", "#f1c40f"),
                       ("초록", "#27ae60"), ("파랑", "#3498db"), ("남색", "#34495e"), ("보라", "#9b59b6")]
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
                "BOOKMARK_NAMES": '<button type="button" class="bkm-mark" id="bkm-all" aria-label="전체 북마크" aria-pressed="true"><svg viewBox="0 0 24 24" style="fill:#888" aria-hidden="true"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg><strong>전체</strong></button>' + "".join(f'<button type="button" class="bkm-mark" data-color-filter="{code}" aria-label="{name} 북마크"><svg viewBox="0 0 24 24" style="fill:{code}" aria-hidden="true"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/></svg><strong>{name}</strong><small></small></button>' for name, code in bookmark_colors),
        
        "TOTAL": f"전체 {len(articles)}건",
        "STATUS": f"{len(articles)}건",
        "STATS": "".join(f'<div class="stat"><strong>{count}건</strong><span>{label}</span></div>' for label, count in [("전체", len(articles)), ("웹글", sum(a["type"] == "web" for a in articles)), ("X 게시물", sum(a["type"] == "x" for a in articles))]),
        "TOP_AUTHORS": escape("많이 모은 저자 · " + " · ".join(f"{name} {count}건" for name, count in people[:3])),
        "DISTRIBUTION_LABEL": escape(", ".join(f"{name} {count}건" for name, count in topics) or "주제 없음"),
        "DISTRIBUTION_BAR": "".join(bar), "LEGEND": "".join(legend),
        "TYPES": "".join(f'<button type="button" class="type-tab" data-kind="type" data-value="{value}" aria-pressed="{str(not value).lower()}">{label}</button>' for label, value in [("전체", ""), ("웹글", "web"), ("X 게시물", "x"), ("IB 주원", "ib")]),
        "TOPICS": filter_button("전체 주제", None, "topic") + "".join(filter_button(name, count, "topic") for name, count in topics),
        "PEOPLE": people_filters(people), "SPOTLIGHT": people_filters(people, True),
        "ARTICLES": "\n".join(article_html_mobile(article, index == 0, index, [main_articles[j] for j in related_map.get(index, [])], quoted_map) for index, article in enumerate(main_articles)),
        "AI_TOOLS": ai_tools_html(ai_tools).replace('<section class="ai-tools-section"><h2>', '<details class="ai-tools-section"><summary>').replace("</h2>", "</summary>").replace("</section>", "</details>"),
        "IB_JUWON": ib_juwon_html,
        "MEMO_CSS": MEMO_CSS,
        "MEMO_JS": MEMO_JS,
        "MEMO_MODAL_HTML": MEMO_MODAL_HTML,
    }
    # Substitute once so article text containing a template marker stays literal.
    return re.sub(r"__([A-Z_]+)__", lambda match: values[match[1]], MOBILE_HTML.replace("__CSS__", MOBILE_CSS + IB_CSS + MEMO_CSS).replace("__JS__", MOBILE_JS + IB_JS + MEMO_JS))

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
                if not has_chart and is_chart_image(thumb_file):
                    has_chart = True
                try:
                    thumb, _ = webp_thumb(cache_key, posts_path.parent / "media")
                except Exception as error:
                    print(f"경고: WebP 폴백 {cache_key}: {error}")
                    thumb = None
                if not thumb:
                    print(f"경고: JPEG 썸네일 폴백 {cache_key}")
                    thumb = image_data(thumb_file)
                full = None
                try:
                    full = webp_full(cache_key, posts_path.parent / "media")
                except Exception as error:
                    print(f"경고: WebP 확대 실패 {cache_key}: {error}")
                if not full:
                    cache = ROOT / "media-large" / f"{cache_key}-1600-q85.jpg"
                    if cache.is_file():
                        print(f"경고: 구 확대 캐시 폴백 {cache_key}")
                        full = image_data(cache)
                    else:
                        full = thumb
                media_images.append((thumb, full))
        data["articles"].append({"type": "x", "id": tweet_id, "sourceUrl": url,
            "author": name, "handle": handle, "fullText": post.get("fullText") or "",
            "createdAt": post.get("createdAt") or "", "topics": [category], "sort_time": sort_time,
            "mediaImages": media_images, "has_chart": has_chart, "replies": post.get("replies") or [],
            "quoted": post.get("quoted") or None,
            "urlContents": [{"url": u, **url_contents_map.get(u, {"title": u, "content": "", "status": "pending"})}
                            for u in external_urls(post.get("fullText") or "")]})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html_mobile(data["articles"]), encoding="utf-8")
    print(f"생성 완료: {output} (전체 {len(data['articles'])}건 · 웹글 {web_count}건 · X {len(posts)}건)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "index.json")
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    parser.add_argument("--output", type=Path, default=ROOT / "웹글-아카이브-모바일.html")
    args = parser.parse_args()
    build(args.index.expanduser().resolve(), args.output.expanduser().resolve(), args.posts.expanduser().resolve())
