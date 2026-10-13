#!/usr/bin/env python3
"""
v2 IB 정본 병합 어댑터 (canonical)

63개 스냅샷(v2/ib-library.json, 원본 pin/memo ID 보존)을 기준으로
정본 소스 3종을 병합:
  1. ~/workspace/x-bookmarks/study_links.json
  2. ~/workspace/x-bookmarks/resource_library/index.json
  3. ~/workspace/x-bookmarks/resource_library/ib_juwon.json

규칙:
  - 스냅샷 63건의 id/memoId/subject/category는 절대 변경하지 않음
  - 정본 URL/제목으로 매칭 → 기존 레코드 유지 (본문이 더 풍부하면 업데이트)
  - 신규 항목만 추가 (안정적 md5 기반 ID)
  - 정본 입력 해시 (텍스트 에셋 포함) → build_data.source_fingerprint()에서 사용

사용법:
    python3 ib_adapter.py                    # v2/ib-library.json 업데이트
    python3 ib_adapter.py --fingerprint      # 정본 입력 해시만 출력
    python3 ib_adapter.py --stats            # 병합 통계만 출력
"""
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

V2_ROOT = Path(__file__).parent
IB_LIBRARY_JSON = V2_ROOT / "ib-library.json"

XBOOKMARKS = Path.home() / "workspace" / "x-bookmarks"
STUDY_LINKS = XBOOKMARKS / "study_links.json"
RESOURCE_LIB = XBOOKMARKS / "resource_library"
IB_JUWON = RESOURCE_LIB / "ib_juwon.json"
RL_INDEX = RESOURCE_LIB / "index.json"

PIN_MOD = 1000000
SUBJECT_ORDER = ["ib", "math", "science", "english", "chinese", "admissions", "other"]
SUBJECT_LABELS = {
    "ib": "IB", "math": "수학", "science": "과학", "english": "영어",
    "chinese": "중국어", "admissions": "입시", "other": "기타",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_fingerprint() -> str:
    """정본 입력 전체 해시 (텍스트 에셋 포함).

    build_data.source_fingerprint()에서 호출됨.
    study_links.json, ib_juwon.json, resource_library/index.json +
    index.json이 참조하는 모든 text_file의 해시를 결합.
    """
    h = hashlib.sha256()
    # 1. JSON 소스 3종
    for path in (STUDY_LINKS, IB_JUWON, RL_INDEX):
        h.update(str(path).encode("utf-8"))
        h.update(b"\x00")
        if path.exists():
            h.update(sha256_file(path).encode("utf-8"))
        else:
            h.update(b"<missing>")
        h.update(b"\x00")

    # 2. 텍스트 에셋 (index.json의 text_file)
    text_hashes = []
    if RL_INDEX.exists():
        try:
            idx = json.loads(RL_INDEX.read_text(encoding="utf-8"))
            if isinstance(idx, list):
                for e in idx:
                    if not isinstance(e, dict):
                        continue
                    rel = e.get("text_file", "")
                    if not rel:
                        continue
                    tp = RESOURCE_LIB / rel
                    if tp.exists():
                        text_hashes.append((rel, sha256_file(tp)))
        except (OSError, ValueError):
            pass
    # 결정적 순서
    for rel, th in sorted(text_hashes):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(th.encode("utf-8"))
        h.update(b"\x00")
    h.update(f"text_assets:{len(text_hashes)}".encode("utf-8"))
    return h.hexdigest()


def stable_pin_id(url: str) -> str:
    h = int(hashlib.md5(url.encode("utf-8")).hexdigest()[:8], 16) % PIN_MOD
    return f"ib_pin_{h}"


def stable_memo_id(url: str) -> str:
    h = int(hashlib.md5(url.encode("utf-8")).hexdigest()[:8], 16) % PIN_MOD
    return f"ib_{h}"


def norm_url(url: str) -> str:
    url = (url or "").strip()
    if not url or url.startswith("ib-local://"):
        return url
    try:
        p = urlparse(url)
        return urlunparse((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip("/") or "/",
            "", "", "",
        ))
    except Exception:
        return url.lower().rstrip("/")


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def load_snapshot():
    """63 스냅샷 로드. 없으면 빈 리스트 (최초 생성 시)."""
    if not IB_LIBRARY_JSON.exists():
        return []
    data = json.loads(IB_LIBRARY_JSON.read_text(encoding="utf-8"))
    posts = data.get("posts", []) if isinstance(data, dict) else []
    return posts if isinstance(posts, list) else []


def collect_canonical():
    """정본 소스에서 원시 레코드 수집."""
    raws = []

    # 1. study_links.json (전 과목)
    if STUDY_LINKS.exists():
        data = json.loads(STUDY_LINKS.read_text(encoding="utf-8"))
        for subject in SUBJECT_ORDER:
            entries = data.get(subject, []) if isinstance(data, dict) else []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                url = (e.get("url") or "").strip()
                if not url or not re.match(r"^https?://", url):
                    continue
                raws.append({
                    "url": url,
                    "title": (e.get("title") or "").strip() or url,
                    "description": (e.get("description") or "").strip(),
                    "source": (e.get("source") or "").strip(),
                    "subject": subject,
                    "kind": "study_links",
                })

    # 2+3. ib_juwon.json + resource_library/index.json 조인
    index_map = {}
    if RL_INDEX.exists():
        try:
            idx = json.loads(RL_INDEX.read_text(encoding="utf-8"))
            if isinstance(idx, list):
                for e in idx:
                    if isinstance(e, dict) and e.get("dir"):
                        index_map[e["dir"]] = e
        except (OSError, ValueError):
            pass

    if IB_JUWON.exists():
        entries = json.loads(IB_JUWON.read_text(encoding="utf-8"))
        if isinstance(entries, list):
            for e in entries:
                if not isinstance(e, dict):
                    continue
                dirname = (e.get("dir") or "").strip()
                reason = (e.get("reason") or "").strip()
                source_txt = (e.get("source") or "").strip()

                if not dirname:
                    # 텍스트 파일 기반
                    if not reason and not source_txt:
                        continue
                    pseudo = f"ib-local://text/{source_txt}" if source_txt else f"ib-local://reason/{reason[:40]}"
                    raws.append({
                        "url": pseudo,
                        "title": reason[:80] if reason else source_txt,
                        "description": reason,
                        "source": source_txt or "IB 자료",
                        "subject": "ib",
                        "kind": "ib_juwon_text",
                    })
                    continue

                category = (e.get("category") or "").strip() or dirname
                info = index_map.get(dirname, {})
                text_rel = info.get("text_file") or f"{dirname}/text.md"
                full_text = ""
                tp = RESOURCE_LIB / text_rel
                if tp.exists():
                    try:
                        full_text = tp.read_text(encoding="utf-8").strip()[:20000]
                    except (OSError, UnicodeDecodeError):
                        pass
                raws.append({
                    "url": f"ib-local://resource_library/{dirname}",
                    "title": category,
                    "description": reason,
                    "source": "IB 자료",
                    "subject": "ib",
                    "kind": "ib_juwon",
                    "full_text": full_text,
                    "rl_category": info.get("category", ""),
                })

    return raws


def merge(snapshot, raws):
    """스냅샷 기준 병합.

    Returns:
        (merged_posts, stats)
    """
    by_url = {}
    by_title = {}
    for s in snapshot:
        u = norm_url(s.get("source_url") or s.get("sourceUrl") or "")
        t = norm_title(s.get("title") or "")
        if u and u not in by_url:
            by_url[u] = s
        if t and t not in by_title:
            by_title[t] = s

    merged = []
    seen = set()
    stats = {"snapshot": len(snapshot), "matched": 0, "added": 0,
             "content_updated": 0, "snapshot_only": 0}

    for r in raws:
        u = norm_url(r["url"])
        t = norm_title(r["title"])
        snap = by_url.get(u) or by_title.get(t)

        if snap:
            # 기존: id/memoId/subject/category 유지
            post = dict(snap)  # 전체 보존
            # 본문이 더 풍부하면 업데이트 (ID 불변)
            new_text = r.get("full_text") or r.get("description", "")
            old_text = snap.get("fullText") or snap.get("summary") or ""
            if new_text and len(new_text) > len(old_text):
                post["fullText"] = new_text
                post["summary"] = new_text[:300]
                stats["content_updated"] += 1
            stats["matched"] += 1
        else:
            # 신규: 안정적 ID
            url = r["url"]
            post = {
                "type": "ib",
                "id": stable_pin_id(url),
                "memoId": stable_memo_id(url),
                "title": r["title"],
                "summary": (r.get("full_text") or r.get("description", ""))[:300],
                "fullText": r.get("full_text") or r.get("description", ""),
                "fullHtml": "",
                "author": r.get("source") or "IB 자료",
                "source_url": url,
                "topics": [SUBJECT_LABELS.get(r.get("subject", ""), r.get("subject", ""))],
            }
            stats["added"] += 1

        pid = post.get("id")
        if pid in seen:
            continue
        seen.add(pid)
        merged.append(post)

    # 스냅샷에만 있는 것은 유지
    for s in snapshot:
        if s.get("id") not in seen:
            merged.append(s)
            seen.add(s.get("id"))
            stats["snapshot_only"] += 1

    stats["total"] = len(merged)
    return merged, stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="IB 정본 병합 어댑터")
    parser.add_argument("--fingerprint", action="store_true", help="정본 해시만 출력")
    parser.add_argument("--stats", action="store_true", help="병합 통계만 출력")
    args = parser.parse_args()

    if args.fingerprint:
        print(canonical_fingerprint())
        return

    snapshot = load_snapshot()
    raws = collect_canonical()
    merged, stats = merge(snapshot, raws)

    if args.stats:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    # 스냅샷 63건 보존 검증
    snap_ids = {s.get("id") for s in snapshot}
    merged_ids = {p.get("id") for p in merged}
    missing = snap_ids - merged_ids
    if missing:
        print(f"오류: 스냅샷 ID 유실: {missing}", file=sys.stderr)
        sys.exit(1)

    IB_LIBRARY_JSON.write_text(
        json.dumps({"posts": merged}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"IB 병합 완료: 스냅샷 {stats['snapshot']} → "
          f"매칭 {stats['matched']} + 신규 {stats['added']} + "
          f"스냅샷유지 {stats['snapshot_only']} = 총 {stats['total']}개 "
          f"(본문 업데이트 {stats['content_updated']}개)", file=sys.stderr)
    print(f"정본 해시: {canonical_fingerprint()[:16]}...", file=sys.stderr)


if __name__ == "__main__":
    main()
