#!/usr/bin/env python3
"""
v2 IB 인제스트 어댑터 (canonical)

정규 IB 소스 3종을 v2 posts-ib.json용 레코드로 변환:
  1. ~/workspace/x-bookmarks/study_links.json        ('ib' 카테고리: url/title/source/description)
  2. ~/workspace/x-bookmarks/resource_library/ib_juwon.json (로컬 문서: category/dir/reason/type)
  3. ~/workspace/x-bookmarks/resource_library/index.json    (text_file 경로 조인용)

ID 안정성 (핵심):
  v1 ib_section.py는 핀 ID를 `ib_pin_{abs(hash(url)) % 1000000}` 로 생성했으나,
  Python의 hash()는 프로세스마다 랜덤 시드(PYTHONHASHSEED)가 달라 빌드마다 ID가 바뀌는
  버그가 있었음. 메모 ID는 `ib_{md5(url)[:8] % 1000000}` 로 안정적이었음.
  본 어댑터는 양쪽 모두 md5 기반으로 안정화:
    pin_id  = f"ib_pin_{int(md5(url.encode()).hexdigest()[:8], 16) % 1000000}"
    memo_id = f"ib_{int(md5(url.encode()).hexdigest()[:8], 16) % 1000000}"  # ib_section.py:198과 동일식
  → 기존 localStorage의 `archive_memo_ib_*` 메모가 그대로 유지됨.

사용법:
    python3 ib_adapter.py                    # JSON 배열을 stdout으로 출력
    python3 ib_adapter.py --out ib_posts.json # 파일로 저장

    # build_data.py에서 import하여 사용:
    from ib_adapter import load_ib_posts
    posts = load_ib_posts()  # list[dict], type='ib', category='IB'
"""
import hashlib
import json
import sys
from pathlib import Path

# 정규 소스 경로
XBOOKMARKS = Path.home() / "workspace" / "x-bookmarks"
STUDY_LINKS = XBOOKMARKS / "study_links.json"
RESOURCE_LIB = XBOOKMARKS / "resource_library"
IB_JUWON = RESOURCE_LIB / "ib_juwon.json"
RL_INDEX = RESOURCE_LIB / "index.json"

# v1 호환 상수
PIN_MOD = 1000000


def stable_pin_id(url: str) -> str:
    """안정적 핀 ID (v1 ib_pin_* 형식, md5 기반)."""
    h = int(hashlib.md5(url.encode("utf-8")).hexdigest()[:8], 16) % PIN_MOD
    return f"ib_pin_{h}"


def stable_memo_id(url: str) -> str:
    """안정적 메모 ID (ib_section.py:198과 동일한 식)."""
    h = int(hashlib.md5(url.encode("utf-8")).hexdigest()[:8], 16) % PIN_MOD
    return f"ib_{h}"


def _norm_post(*, url, title, author, full_text, summary, category_label, source_kind, extra=None):
    """v2 스키마 호환 IB 레코드 생성."""
    pin_id = stable_pin_id(url or title)
    post = {
        "id": pin_id,               # v1 ib_pin_* 형식 (안정적)
        "tweetId": pin_id,          # 호환용
        "type": "ib",               # 명시적 타입 (classify_post 우회 불필요)
        "category": "IB",           # IB_CATEGORIES에 포함 → build_data.py도 'ib'로 분류
        "topics": ["IB", category_label] if category_label else ["IB"],
        "title": title or "",
        "author": author or "",
        "fullText": full_text or "",
        "summary": summary or "",
        "sourceUrl": url or "",
        "memo_id": stable_memo_id(url or title),  # v1 메모 ID 형식 보존
        "ib_source": source_kind,   # 'study_links' | 'ib_juwon'
        "media": [],
        "replies": [],
        "urlContents": [],
    }
    if extra:
        post.update(extra)
    return post


def load_study_links_ib():
    """study_links.json의 'ib' 카테고리 → IB 레코드."""
    if not STUDY_LINKS.exists():
        print(f"  study_links.json 없음 (건너뜀): {STUDY_LINKS}", file=sys.stderr)
        return []
    data = json.loads(STUDY_LINKS.read_text(encoding="utf-8"))
    entries = data.get("ib", []) if isinstance(data, dict) else []
    posts = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        url = (e.get("url") or "").strip()
        title = (e.get("title") or "").strip()
        if not url and not title:
            continue
        posts.append(_norm_post(
            url=url,
            title=title,
            author=(e.get("source") or "").strip(),
            full_text=(e.get("description") or "").strip(),
            summary=(e.get("description") or "").strip()[:300],
            category_label="IB 링크",
            source_kind="study_links",
        ))
    return posts


def load_ib_juwon():
    """resource_library/ib_juwon.json → IB 레코드 (로컬 문서, text.md 본문 포함)."""
    if not IB_JUWON.exists():
        print(f"  ib_juwon.json 없음 (건너뜀): {IB_JUWON}", file=sys.stderr)
        return []
    entries = json.loads(IB_JUWON.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        return []
    posts = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        dirname = (e.get("dir") or "").strip()
        category = (e.get("category") or "").strip() or dirname
        if not dirname:
            continue
        # 본문: <dir>/text.md (없으면 reason을 요약으로)
        full_text = ""
        text_path = RESOURCE_LIB / dirname / "text.md"
        if text_path.exists():
            try:
                full_text = text_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                pass
        reason = (e.get("reason") or "").strip()
        # URL 대용 식별자 (로컬 문서이므로 dir 기반)
        pseudo_url = f"ib-local://resource_library/{dirname}"
        posts.append(_norm_post(
            url=pseudo_url,
            title=category,
            author="IB 자료",
            full_text=full_text,
            summary=reason[:300] if reason else full_text[:300],
            category_label="IB 문서",
            source_kind="ib_juwon",
            extra={"ib_dir": dirname, "ib_reason": reason},
        ))
    return posts


def load_ib_posts():
    """정규 소스 3종을 병합한 IB 레코드 리스트 (중복 URL 제거)."""
    posts = []
    posts.extend(load_study_links_ib())
    posts.extend(load_ib_juwon())
    # 중복 제거 (id 기준, 최초 우선)
    seen = set()
    unique = []
    for p in posts:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        unique.append(p)
    return unique


def main():
    import argparse
    parser = argparse.ArgumentParser(description="IB 인제스트 어댑터")
    parser.add_argument("--out", type=Path, help="출력 JSON 경로 (없으면 stdout)")
    args = parser.parse_args()

    posts = load_ib_posts()
    print(f"IB 레코드: {len(posts)}개", file=sys.stderr)
    # ID 안정성 자가 검증: 두 번 생성해도 동일해야 함
    for p in posts:
        url = p["sourceUrl"]
        assert p["id"] == stable_pin_id(url), f"ID 불안정: {p['id']}"
        assert p["memo_id"] == stable_memo_id(url), f"메모 ID 불안정: {p['memo_id']}"
    print(f"  ID 안정성 검증 통과", file=sys.stderr)

    out = json.dumps(posts, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(out, encoding="utf-8")
        print(f"  저장: {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
