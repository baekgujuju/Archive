#!/usr/bin/env python3
"""공용 미디어 URL 선택 + 로컬 매핑 조회 (P2-8).

미러(media-mirror.py)와 enrich(enrich_tools.py)가 같은 셀렉터를
사용한다. 미러가 선택한 URL과 enrich가 선택한 URL이 같아야 한다.

셀렉터:
- 문자열: 유효한 URL이면 사용.
- [thumb, full]: 유효한 full 우선, 없으면 thumb. 1개면 그 1개.
- dict: full → thumb → url → src → media_url → image_url → mediaUrl.
- fullHtml: 값 자체가 URL인 경우만 허용 (HTML 문자열은 별도
  <img src> 추출 경로 — 이 셀렉터가 HTML을 URL로 반환하지 않는다).

find_local():
- HTML entity 해제 후 원래 키 정확 조회 → 공통 정규화 키 정확 조회.
- query는 기본 보존. 범용 query 제거·정렬·substring 비교 금지.
  (미러 맵 키는 `?format=jpg&name=large`를 보존한다.)
- 여러 원본이 하나의 정규화 키로 모여 서로 다른 파일을 가리키면
  모호성 오류 (ambiguous).
- 로컬 경로는 허용된 미디어 루트 내부 실제 파일인지 확인한다.
- full이 존재하는데 매핑이 없다는 이유로 thumb로 조용히 바꾸지 않는다.
- URL 후보 있으나 로컬 파일 없으면 누락(missing) — 재시도 대상.

X 이미지 name=small/large/orig는 배치 2에서 별도 URL로 유지한다
(미러 검증 alias를 만들지 않음).
"""

from __future__ import annotations

import html as htmlmod
from pathlib import Path
from urllib.parse import urlparse

MEDIA_URL_KEYS = ("full", "thumb", "url", "src", "media_url", "image_url", "mediaUrl")


def _is_valid_url(value):
    # 스킴은 대소문자를 구분하지 않는다 (RFC 3986) — "HTTPS://"도 유효.
    return (
        isinstance(value, str)
        and value.lower().startswith(("http://", "https://"))
        and len(value) > len("https://")
    )


def select_media_url(item):
    """미디어 항목 → 사용할 URL (없으면 "")."""
    if isinstance(item, str):
        return item if _is_valid_url(item) else ""
    if isinstance(item, (list, tuple)):
        if len(item) >= 2:
            # [thumb, full] — full 우선, 없으면 thumb.
            for candidate in (item[1], item[0]):
                if _is_valid_url(candidate):
                    return candidate
            return ""
        if len(item) == 1:
            return item[0] if _is_valid_url(item[0]) else ""
        return ""
    if isinstance(item, dict):
        for key in MEDIA_URL_KEYS:
            value = item.get(key)
            if _is_valid_url(value):
                return value
        # fullHtml: 값 자체가 URL인 경우만.
        value = item.get("fullHtml")
        if _is_valid_url(value) and "<" not in value:
            return value
        return ""
    return ""


def normalize_media_key(url):
    """정규화 키: entity 해제 + 스킴/호스트 소문자. query 보존."""
    text = htmlmod.unescape(url).strip()
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        rebuilt = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
        if parsed.query:
            rebuilt += f"?{parsed.query}"
        if parsed.fragment:
            rebuilt += f"#{parsed.fragment}"
        return rebuilt
    return text


def build_media_index(media_map):
    """정규화 키 → [(원본 키, 로컬 경로), ...] 인덱스."""
    index = {}
    for raw, local in media_map.items():
        if not isinstance(raw, str) or not isinstance(local, str):
            continue
        index.setdefault(normalize_media_key(raw), []).append((raw, local))
    return index


def _check_local(local, media_root):
    """로컬 경로 검증. Returns (상대 경로 또는 None, 상태).

    상태: found (루트 내부 실제 파일) / missing (파일 없음) /
    invalid (루트 밖·탈출·형식 오류).
    """
    if not isinstance(local, str) or not local:
        return None, "invalid"
    try:
        candidate = Path(local)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (Path(media_root) / candidate).resolve()
        root = (Path(media_root) / "media").resolve()
        if resolved != root and root not in resolved.parents:
            return None, "invalid"
        if not resolved.is_file():
            return None, "missing"
    except (OSError, RuntimeError):
        return None, "invalid"
    return local, "found"


def find_local_in_map(url, media_map, media_root, index=None):
    """미디어 URL → (로컬 상대 경로 또는 None, 상태).

    상태: found / missing / ambiguous / invalid / not_url.
    - missing: URL 후보는 있으나 로컬 파일 없음 — 재시도 대상 유지,
      thumb로 조용히 바꾸지 않는다.
    - ambiguous: 여러 원본이 하나의 정규화 키로 모여 서로 다른
      파일을 가리킴 — 모호성 오류.
    """
    if not _is_valid_url(url):
        return None, "not_url"
    raw_key = htmlmod.unescape(url).strip()
    # 1) 원래 키 정확 조회.
    if raw_key in media_map:
        return _check_local(media_map[raw_key], media_root)
    # 2) 공통 정규화 키 정확 조회.
    if index is None:
        index = build_media_index(media_map)
    hits = index.get(normalize_media_key(url), [])
    if not hits:
        return None, "missing"
    locals_seen = {local for _, local in hits}
    if len(locals_seen) > 1:
        return None, "ambiguous"
    return _check_local(hits[0][1], media_root)
