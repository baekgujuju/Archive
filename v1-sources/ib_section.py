"""IB 주원 섹션 HTML 생성기 — build_mobile.py / build_dashboard.py 공용.

v25: 실제 제목 표시 + 클릭 시 본문 인라인 펼침 + 검색 연동.
"""

import json
import re
from html import escape
from pathlib import Path
from urllib.parse import urlparse
from memo_feature import MEMO_CSS as MEMO_CSS_SHARED, memo_button

SUBJECT_NAMES = {
    "math": "수학", "science": "과학", "english": "영어",
    "chinese": "중국어", "ib": "IB", "admissions": "입시", "other": "기타",
}

SUBJECT_META = {
    "math": ("📐 수학", "경시대회·개념 학습"),
    "science": ("🔬 과학", "시뮬레이션·과학 뉴스"),
    "english": ("📖 영어", "레벨별 리딩"),
    "chinese": ("🈳 중국어", "HSK 대비"),
    "ib": ("🎓 IB", "IB 과정·카페·블로그"),
    "admissions": ("🏫 입시", "ESAT·PAT·미국대학"),
    "other": ("📎 기타", "기타 자료"),
}

# 기출문제 관련 URL 패턴
EXAM_PATTERNS = {
    "AMC": ["amc", "artofproblemsolving"],
    "UKMT": ["ukmt"],
    "싱가포르 수학": ["sgtestpaper"],
    "MOEMS": ["moems"],
    "IB 기출": ["pirateib"],
}


def _clean_url(url):
    """HTML 엔티티 정리."""
    return url.replace("&quot;", "").replace("&amp;", "&").rstrip("}").strip()


def _is_valid_url(url):
    """유효한 http URL인지 확인."""
    url = _clean_url(url)
    if not url.startswith(("http://", "https://")):
        return False
    if len(url) < 12:
        return False
    if url in ("https://www.jbessay", "https://www.dogonews.com/"):
        return False
    return True


def _post_id(url):
    """네이버 카페/블로그 글번호 추출."""
    m = re.search(r"/(\d+)(?:\?|$)", url)
    return m.group(1) if m else None


def _load_content_maps(base_dir):
    """본문 데이터 로드: {clean_url_prefix: (title, content)}."""
    base = Path(base_dir)
    content_map = {}  # normalized url -> (title, content)

    # 1. study_content.json (일반 사이트)
    sc_path = base / "study_content.json"
    if sc_path.exists():
        try:
            sc = json.loads(sc_path.read_text(encoding="utf-8"))
            for k, v in sc.items():
                nk = k.lower().rstrip("/")
                content_map[nk] = (v.get("title", ""), v.get("content", ""))
        except Exception:
            pass

    # 2. naver_cafe_scraped.json (네이버 카페, 글번호 기준)
    cafe_by_id = {}
    nc_path = base / "resource_library" / "naver_cafe_scraped.json"
    if nc_path.exists():
        try:
            nc = json.loads(nc_path.read_text(encoding="utf-8"))
            for x in nc.get("results", []):
                pid = _post_id(x.get("url", ""))
                if pid and x.get("status") == "ok":
                    meta = []
                    if x.get("author"):
                        meta.append(f"작성자: {x['author']}")
                    if x.get("date"):
                        meta.append(f"작성일: {x['date']}")
                    cafe_by_id[pid] = (x.get("title", ""), x.get("content", ""), " · ".join(meta))
        except Exception:
            pass

    return content_map, cafe_by_id


def _find_content(url, content_map, cafe_by_id):
    """URL에 대한 (title_override, content, meta) 반환. 없으면 (None, '', '')."""
    # 1. 네이버 카페: 글번호 매칭
    pid = _post_id(url)
    if pid and pid in cafe_by_id:
        t, c, m = cafe_by_id[pid]
        return t, c, m
    # 2. 일반 사이트: prefix 매칭 (study_content 키가 잘린 경우가 있음)
    url_n = url.lower().split("?")[0].rstrip("/")
    best = None
    best_len = 0
    for ck, (ct, cc) in content_map.items():
        ck_n = ck.split("?")[0]
        # 공통 prefix 길이
        common = 0
        for a, b in zip(url_n, ck_n):
            if a == b:
                common += 1
            else:
                break
        # 짧은 URL(naver.me 등)은 정확히 일치하면 바로 채택
        shorter = min(len(url_n), len(ck_n))
        if url_n == ck_n or (shorter < 40 and common == shorter):
            return ct, cc, ""
        # 긴 URL은 최소 40자 이상 공통이고, 둘 중 하나가 거의 포함 관계
        if common >= 40 and common > best_len:
            if common >= shorter * 0.8:
                best = (ct, cc, "")
                best_len = common
    if best:
        return best
    return None, "", ""


def _get_description(url, item):
    """링크 설명 반환. item의 description 필드 우선."""
    d = item.get("description", "").strip()
    if d:
        return d
    clean = _clean_url(url)
    if "cafe.naver.com" in clean:
        return "네이버 카페 글"
    if "blog.naver.com" in clean:
        return "네이버 블로그 글"
    if "youtube.com" in clean or "youtu.be" in clean:
        return "유튜브 영상"
    return "학습 자료"


def _build_card(url, title, desc, content, meta, card_idx):
    """확장형 카드 1개 생성. 클릭하면 본문이 인라인으로 펼쳐짐."""
    safe_url = escape(url, quote=True)
    if content and content.strip():
        # 본문 있음: 인라인 펼침
        body = (
            f'<div class="ib-card-body">'
            + (f'<div class="ib-card-meta">{escape(meta)}</div>' if meta else '')
            + f'<div class="ib-card-content">{escape(content)}</div>'
            + f'<a class="ib-card-link" href="{safe_url}" target="_blank" rel="noopener">원문 보기 ↗</a>'
            + f'</div>'
        )
    else:
        # 본문 없음: 설명 + 원문 링크
        body = (
            f'<div class="ib-card-body">'
            f'<p class="ib-card-nocontent">본문 미리보기가 없습니다. 원문에서 확인하세요.</p>'
            f'<a class="ib-card-link" href="{safe_url}" target="_blank" rel="noopener">원문 보기 ↗</a>'
            f'</div>'
        )
    return (
        f'<details class="ib-card" data-ib-idx="{card_idx}">'
        f'<summary class="ib-card-summary">'
        f'<span class="ib-card-title">{escape(title)}</span>'
        f'<span class="ib-card-desc">{escape(desc)}</span>'
        f'</summary>'
        f'{body}'
        f'</details>'
    )



def _build_card_html(url, title, desc, content, meta, card_idx, pin_key):
    """개별 카드 HTML (계층형용)."""
    safe_url = escape(url, quote=True)
    if content and len(content.strip()) > 50:
        body = (
            f'<div class="ib-card-body">'
            + (f'<div class="ib-card-meta">{escape(meta)}</div>' if meta else '')
            + f'<div class="ib-card-content">{escape(content)}</div>'
            + f'<a class="ib-card-link" href="{safe_url}" target="_blank" rel="noopener">원문 보기 ↗</a>'
            + f'</div>'
        )
    else:
        body = (
            f'<div class="ib-card-body">'
            f'<p class="ib-card-nocontent">본문 미리보기가 없습니다. 원문에서 확인하세요.</p>'
            f'<a class="ib-card-link" href="{safe_url}" target="_blank" rel="noopener">원문 보기 ↗</a>'
            f'</div>'
        )
    import hashlib as _hl
    memo_id = f"ib_{int(_hl.md5(url.encode()).hexdigest()[:8], 16) % 1000000}"
    return (
        f'<details class="ib-card" data-ib-idx="{card_idx}" data-pin-key="{pin_key}">'
        f'<summary class="ib-card-summary">'
        f'<span class="ib-card-title">{escape(title)}</span>'
        f'{memo_button(memo_id, title)}'
        f'<span class="ib-card-desc">{escape(desc)}</span>'
        f'<button class="ib-pin-btn" data-pin="{pin_key}" title="핀 고정">📌</button>'
        f'</summary>'
        f'{body}'
        f'</details>'
    )


def build_ib_section(study_links_path, ib_juwon_path, resource_index_path):
    """IB 주원 섹션 HTML 생성 (계층형: 과목 → 카테고리 → 글)."""
    study_links = json.loads(Path(study_links_path).read_text(encoding="utf-8"))
    base_dir = Path(study_links_path).parent
    content_map, cafe_by_id = _load_content_maps(base_dir)

    # 카테고리 맵 로드
    cat_map_path = base_dir / "category_map.json"
    try:
        category_map = json.loads(cat_map_path.read_text(encoding="utf-8"))
    except Exception:
        category_map = {}

    # URL → (제목, 설명) 매핑 구축
    url_info = {}
    for cat_key, links in study_links.items():
        for item in links:
            if not isinstance(item, dict):
                continue
            url = _clean_url(item.get("url", ""))
            if not _is_valid_url(url):
                continue
            title = item.get("title", "").strip() or url
            if title.startswith("텍스트 ") or title == url:
                try:
                    title = urlparse(url).netloc.replace("www.", "")
                except Exception:
                    title = url[:40]
            desc = _get_description(url, item)
            c_title, content, meta = _find_content(url, content_map, cafe_by_id)
            if c_title and c_title != "접속 불가" and (not item.get("title", "").strip() or title.startswith("텍스트 ")):
                title = c_title
            url_info[url] = (title, desc, content, meta)

    # 과목 탭 HTML
    subject_order = ["ib", "math", "science", "english", "chinese", "admissions", "other"]
    tabs_html = []
    for skey in subject_order:
        sname = SUBJECT_NAMES.get(skey, skey)
        cats = category_map.get(skey, {})
        total = sum(len(urls) for urls in cats.values())
        if total == 0:
            # 카테고리 맵에 없으면 study_links에서 직접 카운트
            total = len([l for l in study_links.get(skey, []) if isinstance(l, dict)])
        tabs_html.append(
            f'<button class="ib-subject-tab" data-subject="{skey}">'
            f'{sname} <span class="ib-tab-count">{total}</span>'
            f'</button>'
        )

    # 카테고리 및 카드 HTML (과목별로 그룹화)
    content_html = []
    card_idx = 0
    for skey in subject_order:
        sname = SUBJECT_NAMES.get(skey, skey)
        cats = category_map.get(skey, {})
        
        # 카테고리 칩
        chips_html = []
        for cname, urls in cats.items():
            # 유효한 URL만 카운트
            valid_urls = [u for u in urls if _clean_url(u) in url_info or _is_valid_url(_clean_url(u))]
            chips_html.append(
                f'<button class="ib-cat-chip" data-subject="{skey}" data-category="{escape(cname)}">'
                f'{escape(cname)} <span class="ib-chip-count">{len(valid_urls)}</span>'
                f'</button>'
            )
        
        # 각 카테고리의 카드들
        cats_cards_html = []
        for cname, urls in cats.items():
            cards = []
            for url in urls:
                url = _clean_url(url)
                if url not in url_info:
                    continue
                title, desc, content, meta = url_info[url]
                pin_key = f"ib_pin_{abs(hash(url)) % 1000000}"
                cards.append(_build_card_html(url, title, desc, content, meta, card_idx, pin_key))
                card_idx += 1
            cats_cards_html.append(
                f'<div class="ib-cat-cards" data-subject="{skey}" data-category="{escape(cname)}" hidden>'
                f'{"".join(cards)}'
                f'</div>'
            )
        
        content_html.append(
            f'<div class="ib-subject-panel" data-subject="{skey}" hidden>'
            f'<div class="ib-cat-chips">{"".join(chips_html)}</div>'
            f'<div class="ib-cards-area">{"".join(cats_cards_html)}</div>'
            f'</div>'
        )

    return (
        f'<section class="ib-juwon-section" id="ib-juwon" hidden>'
        f'<h2>🎓 IB 주원</h2>'
        f'<p class="ib-intro">과목을 선택하고 카테고리를 눌러보세요. 카드를 클릭하면 본문이 펼쳐집니다.</p>'
        f'<div class="ib-subject-tabs">{"".join(tabs_html)}</div>'
        f'{"".join(content_html)}'
        f'</section>'
    )


IB_CSS = """
/* IB 주원 섹션 */
.ib-juwon-section{margin:20px 0;padding:20px;border:1px solid var(--line);border-radius:12px;background:#fffdf8}
.ib-juwon-section h2{font-size:20px;margin:0 0 8px}
.ib-intro{font-size:13px;color:var(--muted);margin:0 0 16px}
.ib-subject{margin-bottom:12px;border:1px solid var(--line);border-radius:8px;background:#fff}
.ib-subject summary{padding:12px 16px;cursor:pointer;font-weight:700;display:flex;justify-content:space-between;align-items:center;gap:8px;list-style:none}
.ib-subject summary::-webkit-details-marker{display:none}
.ib-subject-title{font-size:15px}
.ib-subject-sub{font-size:11px;color:var(--muted);font-weight:400}
.ib-card-list{padding:0 12px 12px;display:flex;flex-direction:column;gap:8px}
/* 확장형 카드 */
.ib-card{border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}
.ib-card-summary{padding:10px 14px;cursor:pointer;display:flex;flex-direction:column;gap:2px;list-style:none;text-align:left;align-items:flex-start}
.ib-card-summary::-webkit-details-marker{display:none}
.ib-card-summary:hover{background:#f8fafd}
.ib-card[open] .ib-card-summary{background:#f0f6ff;border-bottom:1px solid var(--line)}
.ib-card-title{font-size:14px;font-weight:700;color:#1a1a1a;text-align:left}
.ib-card[open] .ib-card-title{color:#1a73e8}
.ib-card-desc{font-size:12px;color:var(--muted)}
.ib-card-body{padding:12px 14px;font-size:13px;line-height:1.75}
.ib-card-meta{font-size:11px;color:var(--muted);margin-bottom:8px;padding-bottom:8px;border-bottom:1px dashed var(--line)}
.ib-card-content{white-space:pre-wrap;word-break:break-word;margin-bottom:12px;max-height:480px;overflow-y:auto}
.ib-card-nocontent{color:var(--muted);font-size:12px;margin:0 0 12px}
.ib-card-link{display:inline-block;font-size:13px;font-weight:600;color:#1a73e8;text-decoration:none}
.ib-card-link:hover{text-decoration:underline}
/* 기출문제/관련자료 행 */
.ib-exam-item{padding:8px 4px;border-top:1px solid var(--line);font-size:13px;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:baseline}
.ib-exam-item:first-child{border-top:0}
.ib-exam-item a{color:#1a73e8;text-decoration:none;font-weight:600}
.ib-exam-item a:hover{text-decoration:underline}
.ib-exam-tag{display:inline-block;font-size:10px;font-weight:700;color:#fff;background:var(--accent);padding:2px 8px;border-radius:10px;margin-right:4px}
.ib-doc-name{font-weight:600}
.ib-link-desc{color:var(--muted);font-size:12px}
@media print{
 .ib-card-content{max-height:none;overflow:visible}
 .ib-juwon-section{break-inside:avoid}
}
/* 계층형 UI */
.ib-subject-tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.ib-subject-tab{padding:10px 18px;border:2px solid var(--line);border-radius:20px;background:#fff;font-size:14px;font-weight:600;cursor:pointer;text-align:left}
.ib-subject-tab:hover{border-color:var(--accent);background:#f8fafd}
.ib-subject-tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.ib-tab-count{display:inline-block;font-size:11px;background:rgba(0,0,0,0.1);padding:2px 8px;border-radius:10px;margin-left:4px}
.ib-subject-tab.active .ib-tab-count{background:rgba(255,255,255,0.3)}
.ib-cat-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;padding:12px;background:#f8fafd;border-radius:8px}
.ib-cat-chip{padding:8px 16px;border:1px solid var(--line);border-radius:16px;background:#fff;font-size:13px;cursor:pointer;text-align:left}
.ib-cat-chip:hover{border-color:var(--accent)}
.ib-cat-chip.active{background:#1a73e8;color:#fff;border-color:#1a73e8}
.ib-chip-count{font-size:11px;opacity:0.7;margin-left:4px}
.ib-cards-area{display:flex;flex-direction:column;gap:8px}
.ib-cat-cards{display:flex;flex-direction:column;gap:8px}
.ib-pin-btn{background:none;border:none;cursor:pointer;font-size:16px;padding:4px;margin-left:auto}
.ib-pin-btn.pinned{opacity:1}
.ib-pin-btn:not(.pinned){opacity:0.3}
.ib-card-summary{position:relative}
@media(max-width:540px){
.ib-subject-tab{font-size:12px !important;padding:8px 12px !important}
.ib-subject-tab .ib-count{display:none !important}
.ib-cat-chip{font-size:12px !important;padding:6px 10px !important}
.ib-cat-chip .ib-chip-count{display:none !important}
}
""" + MEMO_CSS_SHARED


IB_JS = """
// IB 계층형 UI
document.addEventListener('DOMContentLoaded', function() {
  const tabs = document.querySelectorAll('.ib-subject-tab');
  const panels = document.querySelectorAll('.ib-subject-panel');
  
  // 핀 상태 로드
  document.querySelectorAll('.ib-pin-btn').forEach(btn => {
    const key = btn.dataset.pin;
    if (localStorage.getItem(key) === '1') btn.classList.add('pinned');
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      const isPinned = btn.classList.toggle('pinned');
      localStorage.setItem(key, isPinned ? '1' : '0');
    });
  });
  
  // 과목 탭 클릭
  tabs.forEach(tab => {
    tab.addEventListener('click', function() {
      const subject = tab.dataset.subject;
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      panels.forEach(p => {
        p.hidden = p.dataset.subject !== subject;
      });
      const panel = document.querySelector('.ib-subject-panel[data-subject="' + subject + '"]');
      if (panel) {
        const firstChip = panel.querySelector('.ib-cat-chip');
        if (firstChip) firstChip.click();
      }
    });
  });
  
  // 카테고리 칩 클릭
  document.querySelectorAll('.ib-cat-chip').forEach(chip => {
    chip.addEventListener('click', function() {
      const subject = chip.dataset.subject;
      const category = chip.dataset.category;
      const panel = chip.closest('.ib-subject-panel');
      panel.querySelectorAll('.ib-cat-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      panel.querySelectorAll('.ib-cat-cards').forEach(cards => {
        cards.hidden = !(cards.dataset.subject === subject && cards.dataset.category === category);
      });
    });
  });
  
  // IB를 기본 선택
  const ibTab = document.querySelector('.ib-subject-tab[data-subject="ib"]');
  if (ibTab) ibTab.click();
  else if (tabs.length > 0) tabs[0].click();
});
"""
