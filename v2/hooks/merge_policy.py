#!/usr/bin/env python3
"""정본(posts.json) 재수집 병합 정책 (P2-2).

수집기는 기존 값을 절대 덮지 않는다. 규칙:
- 신규 tweetId: 전체 검증 후 그대로 추가.
- 기존 tweetId: 동일 값은 무변경, 검증된 누락 필드만 보충.
- 본문·작성자·URL·media·quote·replies 충돌: 기존 값 보존 + 변경 제안으로 분리.
- 분류·번역·분석·검수 필드는 수집기가 건드리지 않는다
  (수신 레코드에 있어도 병합 대상에서 제외).
- 중첩 객체에도 필드 단위 정책을 재귀 적용한다.
- capturedAt은 첫 캡처 시각 — 기존 값 유지 (충돌 아님, 제안 없음).
- replies는 tweetId 기준 합집합 (신규 답글은 보충, 동일 답글 내용
  충돌은 제안). media는 URL 집합 비교 — 다르면 제안.

충돌은 실패가 아니다. 충돌이 있어도 누락 필드 보충은 적용하고,
충돌은 proposals로 남겨 검수 대상으로 분리한다. 실제 내용 수정까지
자동 반영하지 않는다 (보수적 정책).

정본에 중복 tweetId가 이미 있으면 병합 전 명시적 차단
(CanonicalDuplicateError) — index dict가 중복 중 하나를 조용히
선택하던 기존 동작을 금지한다.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

# 수집기가 병합 대상으로 삼는 필드. 이 밖에 필드(category 포함)는
# 수집기가 절대 쓰지 않는다 (기존 값 보존 + 수신 값 무시).
COLLECTOR_FIELDS = {
    "sourceUrl", "capturedAt", "createdAt", "fullText",
    "author", "media", "stats", "quoted", "replies",
}
# 수집기가 건드리지 않는 필드 (분류·번역·분석·검수).
PROTECTED_FIELDS = {
    "category", "translation", "translations", "analysis",
    "review", "reviewStatus", "verified", "curated", "urlContents",
}


class CanonicalDuplicateError(ValueError):
    """정본에 중복 tweetId가 있음 — 병합 전 명시적 차단."""


class MergeResult:
    def __init__(self, merged, proposals, added, updated):
        self.merged = merged          # 병합 결과 정본 (list)
        self.proposals = proposals    # 변경 제안 목록
        self.added = added            # 신규 추가 tweetId 수
        self.updated = updated        # 누락 보충이 일어난 기존 레코드 수
        self.changed = bool(added or updated)

    def __repr__(self):
        return (f"MergeResult(added={self.added}, updated={self.updated}, "
                f"proposals={len(self.proposals)})")


def _is_missing(value):
    """누락 판정. False/0은 실제 값이다 (누락이 아님)."""
    return value is None or value == "" or value == [] or value == {}


def _media_key(item):
    """media 항목 → 집합 비교용 정규 키."""
    if isinstance(item, str):
        return ("str", item)
    if isinstance(item, dict):
        return ("dict", json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(item, (list, tuple)):
        return ("list", json.dumps(list(item), sort_keys=True, ensure_ascii=False))
    return ("other", repr(item))


def _same_status_url(a, b):
    """두 URL이 같은 status URL인지 (정규화 후 정확 일치).

    x.com/twitter.com, www 접두, query/fragment 차이는 동일 글로 본다.
    """
    try:
        pa, pb = urlparse(a), urlparse(b)
    except ValueError:
        return False
    if pa.scheme not in ("http", "https") or pb.scheme not in ("http", "https"):
        return False
    hosts = {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}
    if pa.netloc.lower() not in hosts or pb.netloc.lower() not in hosts:
        return False
    return pa.path == pb.path


def _merge_value(existing, incoming, path, tweet_id, file_id, fingerprint, proposals):
    """한 필드의 병합. Returns (merged_value, changed).

    - 누락(existing) + 존재(incoming) → 보충.
    - 동일 → 무변경.
    - 다름 → 기존 보존 + 제안.
    - dict → 재귀 (필드 단위). media/replies는 전용 규칙.
    """
    field = path.split(".")[-1]
    if field == "capturedAt":
        # 첫 캡처 시각 — 기존 값 유지, 충돌 아님.
        return existing, False
    if _is_missing(existing):
        if _is_missing(incoming):
            return existing, False
        return incoming, True
    if _is_missing(incoming):
        return existing, False
    if existing == incoming:
        return existing, False
    # 둘 다 존재하고 다름.
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        changed = False
        for key in sorted(set(existing) | set(incoming)):
            if key in PROTECTED_FIELDS:
                continue
            sub_path = f"{path}.{key}"
            old_v = existing.get(key)
            new_v = incoming.get(key)
            if key not in existing:
                if not _is_missing(new_v):
                    merged[key] = new_v
                    changed = True
                continue
            if key not in incoming:
                continue
            merged_v, sub_changed = _merge_value(
                old_v, new_v, sub_path, tweet_id, file_id, fingerprint, proposals)
            merged[key] = merged_v
            changed = changed or sub_changed
        return merged, changed
    if isinstance(existing, list) and isinstance(incoming, list):
        if field == "replies":
            # tweetId 기준 합집합. 동일 답글 내용 충돌은 제안.
            by_id = {}
            for reply in existing:
                if isinstance(reply, dict) and reply.get("tweetId"):
                    by_id[str(reply["tweetId"])] = reply
            merged = list(existing)
            changed = False
            for reply in incoming:
                if not isinstance(reply, dict) or not reply.get("tweetId"):
                    continue
                rid = str(reply["tweetId"])
                if rid not in by_id:
                    merged.append(reply)
                    by_id[rid] = reply
                    changed = True
                elif by_id[rid] != reply:
                    proposals.append({
                        "tweetId": tweet_id, "path": f"{path}[{rid}]",
                        "existing": by_id[rid], "proposed": reply,
                        "fileId": file_id, "fingerprint": fingerprint,
                        "status": "pending",
                    })
            return merged, changed
        if field == "media":
            # URL 집합 비교 — 다르면 제안 (덮지 않음).
            if {_media_key(m) for m in existing} == {_media_key(m) for m in incoming}:
                return existing, False
            proposals.append({
                "tweetId": tweet_id, "path": path,
                "existing": existing, "proposed": incoming,
                "fileId": file_id, "fingerprint": fingerprint,
                "status": "pending",
            })
            return existing, False
        # 그 외 목록: 다르면 제안.
        proposals.append({
            "tweetId": tweet_id, "path": path,
            "existing": existing, "proposed": incoming,
            "fileId": file_id, "fingerprint": fingerprint,
            "status": "pending",
        })
        return existing, False
    # sourceUrl: 정규화 후 동일하면 같은 값 (덮지 않음, 제안 없음).
    if field == "sourceUrl" and isinstance(existing, str) and isinstance(incoming, str):
        if _same_status_url(existing, incoming):
            return existing, False
    proposals.append({
        "tweetId": tweet_id, "path": path,
        "existing": existing, "proposed": incoming,
        "fileId": file_id, "fingerprint": fingerprint,
        "status": "pending",
    })
    return existing, False


def check_canonical_duplicates(canonical):
    """정본의 중복 tweetId 목록. 비어 있으면 중복 없음."""
    seen = {}
    dups = []
    for item in canonical:
        if not isinstance(item, dict):
            continue
        tid = item.get("tweetId")
        if tid is None:
            continue
        tid = str(tid)
        if tid in seen:
            if tid not in dups:
                dups.append(tid)
        else:
            seen[tid] = True
    return dups


def merge_records(canonical, incoming, file_id="", fingerprint=""):
    """정본 + 수신 레코드 병합. Returns MergeResult.

    canonical에 중복 tweetId가 있으면 CanonicalDuplicateError를
    raise한다 (병합 전 명시적 차단). incoming 내 중복 tweetId도
    차단한다 (수신 파일 자체의 모순).
    """
    if not isinstance(canonical, list):
        raise ValueError("정본은 배열이어야 함")
    dups = check_canonical_duplicates(canonical)
    if dups:
        raise CanonicalDuplicateError(f"정본 중복 tweetId: {dups}")
    seen_incoming = set()
    for item in incoming:
        if not isinstance(item, dict) or not item.get("tweetId"):
            raise ValueError("수신 레코드에 tweetId 없음")
        tid = str(item["tweetId"])
        if tid in seen_incoming:
            raise ValueError(f"수신 파일 내 중복 tweetId: {tid}")
        seen_incoming.add(tid)

    merged = [dict(item) if isinstance(item, dict) else item for item in canonical]
    indices = {}
    for i, item in enumerate(merged):
        if isinstance(item, dict) and item.get("tweetId"):
            indices[str(item["tweetId"])] = i
    proposals = []
    added = updated = 0
    for item in incoming:
        tid = str(item["tweetId"])
        if tid not in indices:
            merged.append(dict(item))
            indices[tid] = len(merged) - 1
            added += 1
            continue
        old = merged[indices[tid]]
        new = dict(old)
        changed = False
        for key in sorted(set(old) | set(item)):
            if key == "tweetId":
                continue
            if key in PROTECTED_FIELDS:
                continue
            if key not in COLLECTOR_FIELDS and key not in old:
                # 수집기 스키마 밖의 수신 필드는 무시 (기존 값 보존).
                continue
            if key not in COLLECTOR_FIELDS:
                continue
            old_v = old.get(key)
            new_v = item.get(key)
            if key not in old:
                if not _is_missing(new_v):
                    new[key] = new_v
                    changed = True
                continue
            if key not in item:
                continue
            merged_v, sub_changed = _merge_value(
                old_v, new_v, key, tid, file_id, fingerprint, proposals)
            new[key] = merged_v
            changed = changed or sub_changed
        if changed:
            merged[indices[tid]] = new
            updated += 1
    return MergeResult(merged, proposals, added, updated)


def merge_proposal_fingerprint(proposal):
    """제안 중복 제거용 지문 (tweetId + path + proposed 내용)."""
    raw = json.dumps(
        [proposal.get("tweetId"), proposal.get("path"), proposal.get("proposed")],
        sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
