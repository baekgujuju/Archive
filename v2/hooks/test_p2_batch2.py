"""P2 배치 2 테스트.

- capture_contract: status URL 파싱·검증 계약.
- merge_policy: 필드 단위 병합 (누락 보충만, 충돌은 제안).
- media_select: 공용 셀렉터 + 로컬 매핑 조회.
- ingest_post_html (비레포 도구): P2-3 입력 수집 + P2-7 수신 검증.
- drive_poll: P2-1 실패 격리 + P2-2 legacy 재검증 + P3 상태 가드.
- scheduler_runner: run_step 결과 종류 + drive_poll rc=3 처리.
- monitor: P3 워터마크 손상 격리·원자적 저장.
- 배치 1 잔여: 세대 전체 순서 (120 순열) + do_push 종단 테스트.
- ios-bookmarklet.js: node fake-DOM 하네스 (정확 일치·인용 실패·미확인 URL).

실제 Drive·실제 origin·프로덕션 state 파일을 건드리지 않는다.
"""
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent
V2_ROOT = HOOKS_DIR.parent
TOOLS_DIR = Path.home() / "workspace" / "x-bookmarks" / "tools"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(V2_ROOT))
sys.path.insert(0, str(TOOLS_DIR))

import capture_contract
import merge_policy
import media_select
import state_guard
import drive_poll
import monitor as monitor_mod
import scheduler_runner
import build_data
import post_ingest_hook as hook
from merge_policy import (
    CanonicalDuplicateError,
    merge_proposal_fingerprint,
    merge_records,
)


# ---------- capture_contract ----------

def test_parse_status_url():
    assert capture_contract.parse_status_url(
        "https://x.com/Trader_Jesse_/status/123") == ("Trader_Jesse_", "123")
    assert capture_contract.parse_status_url(
        "https://twitter.com/a/status/456?foo=bar#frag") == ("a", "456")
    assert capture_contract.parse_status_url(
        "https://www.x.com/a/status/789") == ("a", "789")
    # ID는 정확 일치 — 123이 1234와 다르다.
    assert capture_contract.parse_status_url(
        "https://x.com/a/status/1234") == ("a", "1234")
    assert capture_contract.parse_status_url("https://x.com/a/status/abc") is None
    assert capture_contract.parse_status_url("https://x.com/a/123") is None
    assert capture_contract.parse_status_url("https://example.com/a/status/123") is None
    assert capture_contract.parse_status_url("https://x.com/a/status/") is None
    assert capture_contract.parse_status_url("not a url") is None
    assert capture_contract.parse_status_url("") is None
    assert capture_contract.parse_status_url(None) is None


def test_normalize_status_url():
    assert capture_contract.normalize_status_url("A", "123") == "https://x.com/A/status/123"


def _base_record():
    return {
        "tweetId": "123",
        "sourceUrl": "https://x.com/Author1/status/123",
        "fullText": "hello world",
        "author": {"handle": "@Author1", "name": "Author One"},
        "media": [],
        "quoted": None,
        "replies": [],
    }


def test_verify_record_valid():
    ok, errors = capture_contract.verify_record(
        _base_record(), requested_url="https://x.com/Author1/status/123")
    assert ok, errors


def test_verify_record_requested_id_mismatch():
    rec = _base_record()
    ok, errors = capture_contract.verify_record(
        rec, requested_url="https://x.com/Author1/status/1234")
    assert not ok
    assert any("요청 ID" in e for e in errors)


def test_verify_record_sourceurl_mismatch():
    rec = _base_record()
    rec["sourceUrl"] = "https://x.com/Author1/status/999"
    ok, errors = capture_contract.verify_record(rec)
    assert not ok
    assert any("sourceUrl" in e for e in errors)


def test_verify_record_handle_mismatch():
    rec = _base_record()
    rec["author"]["handle"] = "@Other"
    ok, errors = capture_contract.verify_record(rec)
    assert not ok
    assert any("핸들" in e for e in errors)


def test_verify_record_blank_body_rejected():
    rec = _base_record()
    rec["fullText"] = "   "
    ok, errors = capture_contract.verify_record(rec)
    assert not ok
    assert any("공백 본문" in e for e in errors)


def test_verify_record_media_only_exception():
    rec = _base_record()
    rec["fullText"] = ""
    rec["captureType"] = "media-only"
    rec["media"] = [{"type": "photo", "url": "https://pbs.twimg.com/media/x.jpg"}]
    ok, errors = capture_contract.verify_record(rec)
    assert ok, errors
    # 미디어 없으면 예외 아님.
    rec["media"] = []
    ok, _ = capture_contract.verify_record(rec)
    assert not ok


def test_verify_record_quote_failed_blocked():
    rec = _base_record()
    rec["quoteStatus"] = "failed"
    ok, errors = capture_contract.verify_record(rec)
    assert not ok
    rec["quoteStatus"] = "indeterminate"
    ok, _ = capture_contract.verify_record(rec)
    assert not ok
    rec["quoteStatus"] = "extracted"
    rec["quoted"] = {"tweetId": "456", "sourceUrl": "https://x.com/B/status/456"}
    ok, errors = capture_contract.verify_record(rec)
    assert ok, errors


def test_verify_record_quoted_source_mismatch():
    rec = _base_record()
    rec["quoted"] = {"tweetId": "456", "sourceUrl": "https://x.com/B/status/777"}
    ok, _ = capture_contract.verify_record(rec)
    assert not ok


def test_verify_record_bad_reply():
    rec = _base_record()
    rec["replies"] = [{"tweetId": "abc"}]
    ok, _ = capture_contract.verify_record(rec)
    assert not ok


# ---------- merge_policy ----------

def test_merge_new_tweet_appended():
    result = merge_records([], [_base_record()])
    assert result.added == 1
    assert len(result.merged) == 1
    assert result.proposals == []


def test_merge_same_value_no_change():
    result = merge_records([_base_record()], [_base_record()])
    assert not result.changed
    assert result.proposals == []


def test_merge_fill_missing():
    old = _base_record()
    old["fullText"] = ""
    result = merge_records([old], [_base_record()])
    assert result.updated == 1
    assert result.merged[0]["fullText"] == "hello world"
    assert result.proposals == []


def test_merge_conflict_keeps_existing_and_proposes():
    old = _base_record()
    new = _base_record()
    new["fullText"] = "different text"
    result = merge_records([old], [new])
    assert result.merged[0]["fullText"] == "hello world"
    assert len(result.proposals) == 1
    assert result.proposals[0]["path"] == "fullText"
    assert result.proposals[0]["existing"] == "hello world"
    assert result.proposals[0]["proposed"] == "different text"


def test_merge_protected_category_never_written():
    old = _base_record()
    old["category"] = "투자"
    new = _base_record()
    new["category"] = "기타"
    result = merge_records([old], [new])
    assert result.merged[0]["category"] == "투자"
    assert result.proposals == []
    # 기존이 비어 있어도 수집기가 분류를 채우지 않는다.
    old2 = _base_record()
    old2["category"] = ""
    result2 = merge_records([old2], [new])
    assert result2.merged[0]["category"] == ""


def test_merge_captured_at_silent_existing():
    old = _base_record()
    old["capturedAt"] = "2026-01-01T00:00:00"
    new = _base_record()
    new["capturedAt"] = "2026-10-17T00:00:00"
    result = merge_records([old], [new])
    assert result.merged[0]["capturedAt"] == "2026-01-01T00:00:00"
    assert result.proposals == []


def test_merge_false_zero_not_missing():
    # False/0은 실제 값이다 — 누락으로 취급해 덮지 않는다.
    old = _base_record()
    old["stats"] = {"pinned": True, "count": 5}
    new = _base_record()
    new["stats"] = {"pinned": False, "count": 0}
    result = merge_records([old], [new])
    assert result.merged[0]["stats"] == {"pinned": True, "count": 5}
    assert len(result.proposals) == 2
    # 기존이 None이면 False/0을 보충한다.
    old2 = _base_record()
    old2["stats"] = {"pinned": None, "count": None}
    result2 = merge_records([old2], [new])
    assert result2.merged[0]["stats"] == {"pinned": False, "count": 0}


def test_merge_media_set_compare():
    old = _base_record()
    old["media"] = [{"type": "photo", "url": "https://x.com/a.jpg"},
                    {"type": "photo", "url": "https://x.com/b.jpg"}]
    new = _base_record()
    new["media"] = [{"type": "photo", "url": "https://x.com/b.jpg"},
                    {"type": "photo", "url": "https://x.com/a.jpg"}]
    result = merge_records([old], [new])
    assert not result.changed  # 순서만 다름 — 동일 집합.
    new["media"] = [{"type": "photo", "url": "https://x.com/c.jpg"}]
    result = merge_records([old], [new])
    assert result.merged[0]["media"] == old["media"]
    assert len(result.proposals) == 1
    assert result.proposals[0]["path"] == "media"


def test_merge_quoted_recursive():
    old = _base_record()
    old["quoted"] = {"tweetId": "456", "fullText": "quoted old",
                     "sourceUrl": "https://x.com/B/status/456"}
    new = _base_record()
    new["quoted"] = {"tweetId": "456", "fullText": "quoted new",
                     "sourceUrl": "https://x.com/B/status/456"}
    result = merge_records([old], [new])
    assert result.merged[0]["quoted"]["fullText"] == "quoted old"
    assert len(result.proposals) == 1
    assert result.proposals[0]["path"] == "quoted.fullText"
    # 기존에 인용 없음 + 수신에 인용 있음 → 보충.
    old2 = _base_record()
    old2["quoted"] = None
    result2 = merge_records([old2], [new])
    assert result2.merged[0]["quoted"]["fullText"] == "quoted new"


def test_merge_replies_union():
    old = _base_record()
    old["replies"] = [{"tweetId": "1", "fullText": "r1"}]
    new = _base_record()
    new["replies"] = [{"tweetId": "1", "fullText": "r1"},
                      {"tweetId": "2", "fullText": "r2"}]
    result = merge_records([old], [new])
    assert len(result.merged[0]["replies"]) == 2
    assert result.proposals == []
    # 동일 답글 내용 충돌 → 제안, 기존 보존.
    new["replies"] = [{"tweetId": "1", "fullText": "r1 CHANGED"}]
    result = merge_records([old], [new])
    assert result.merged[0]["replies"][0]["fullText"] == "r1"
    assert len(result.proposals) == 1


def test_merge_canonical_duplicate_blocked():
    with pytest.raises(CanonicalDuplicateError):
        merge_records([_base_record(), _base_record()], [_base_record()])


def test_merge_incoming_duplicate_blocked():
    with pytest.raises(ValueError, match="수신 파일 내 중복"):
        merge_records([], [_base_record(), _base_record()])


def test_merge_sourceurl_equivalence():
    old = _base_record()
    old["sourceUrl"] = "https://x.com/Author1/status/123"
    new = _base_record()
    new["sourceUrl"] = "https://twitter.com/Author1/status/123?x=1"
    result = merge_records([old], [new])
    assert result.merged[0]["sourceUrl"] == "https://x.com/Author1/status/123"
    assert result.proposals == []
    new["sourceUrl"] = "https://x.com/Author1/status/999"
    result = merge_records([old], [new])
    assert len(result.proposals) == 1


def test_merge_non_collector_field_ignored():
    old = _base_record()
    old["urlContents"] = "existing analysis"
    new = _base_record()
    new["urlContents"] = "incoming analysis"
    result = merge_records([old], [new])
    assert result.merged[0]["urlContents"] == "existing analysis"
    assert result.proposals == []


def test_merge_proposal_fingerprint_stable():
    p1 = {"tweetId": "1", "path": "fullText", "proposed": "x"}
    p2 = {"tweetId": "1", "path": "fullText", "proposed": "x", "fileId": "other"}
    assert merge_proposal_fingerprint(p1) == merge_proposal_fingerprint(p2)


# ---------- media_select ----------

def test_select_media_url():
    s = media_select.select_media_url
    assert s("https://x.com/a.jpg") == "https://x.com/a.jpg"
    assert s("not-a-url") == ""
    assert s("") == ""
    assert s(123) == ""
    # [thumb, full] — full 우선, 없으면 thumb.
    assert s(["https://x.com/thumb.jpg", "https://x.com/full.jpg"]) == "https://x.com/full.jpg"
    assert s(["https://x.com/thumb.jpg", ""]) == "https://x.com/thumb.jpg"
    assert s(["https://x.com/only.jpg"]) == "https://x.com/only.jpg"
    assert s([]) == ""
    # dict 체인: full → thumb → url → src → media_url → image_url → mediaUrl.
    assert s({"thumb": "https://x.com/t.jpg", "full": "https://x.com/f.jpg"}) == "https://x.com/f.jpg"
    assert s({"thumb": "https://x.com/t.jpg"}) == "https://x.com/t.jpg"
    assert s({"url": "https://x.com/u.jpg", "src": "https://x.com/s.jpg"}) == "https://x.com/u.jpg"
    assert s({"mediaUrl": "https://x.com/m.jpg"}) == "https://x.com/m.jpg"
    # fullHtml: URL이면 허용, HTML이면 거부.
    assert s({"fullHtml": "https://x.com/fh.jpg"}) == "https://x.com/fh.jpg"
    assert s({"fullHtml": "<img src=\"https://x.com/fh.jpg\">"}) == ""
    # 빈 full은 건너뛰고 다음 키를 쓴다.
    assert s({"full": "", "thumb": "https://x.com/t.jpg"}) == "https://x.com/t.jpg"


def test_normalize_media_key_preserves_query():
    key = media_select.normalize_media_key(
        "https://pbs.twimg.com/media/abc.jpg?format=jpg&amp;name=large")
    assert key == "https://pbs.twimg.com/media/abc.jpg?format=jpg&name=large"
    # 스킴/호스트 소문자화, 경로는 보존.
    assert media_select.normalize_media_key(
        "HTTPS://PBS.Twimg.COM/media/AbC.jpg?format=jpg") == \
        "https://pbs.twimg.com/media/AbC.jpg?format=jpg"
    # name=small과 name=large는 별도 URL (배치 2 결정).
    assert (media_select.normalize_media_key("https://x.com/a.jpg?name=small")
            != media_select.normalize_media_key("https://x.com/a.jpg?name=large"))


def test_find_local_exact_and_normalized(tmp_path):
    media_root = tmp_path
    media_dir = media_root / "media"
    media_dir.mkdir()
    (media_dir / "a.jpg").write_bytes(b"x")
    (media_dir / "b.jpg").write_bytes(b"y")
    media_map = {
        "https://x.com/a.jpg?format=jpg&name=large": "media/a.jpg",
        "https://x.com/b.jpg": "media/b.jpg",
    }
    # 원래 키 정확 조회 (entity 포함).
    local, status = media_select.find_local_in_map(
        "https://x.com/a.jpg?format=jpg&amp;name=large", media_map, media_root)
    assert (local, status) == ("media/a.jpg", "found")
    # 정규화 키 조회 (스킴 대소문자).
    local, status = media_select.find_local_in_map(
        "HTTPS://x.com/b.jpg", media_map, media_root)
    assert (local, status) == ("media/b.jpg", "found")
    # 없는 URL → missing (thumb로 조용히 바꾸지 않음).
    local, status = media_select.find_local_in_map(
        "https://x.com/c.jpg", media_map, media_root)
    assert (local, status) == (None, "missing")
    # query 차이 → 다른 URL (query 전체 제거 금지).
    local, status = media_select.find_local_in_map(
        "https://x.com/a.jpg?format=jpg&name=small", media_map, media_root)
    assert (local, status) == (None, "missing")
    # URL 아님.
    assert media_select.find_local_in_map("not-a-url", media_map, media_root)[1] == "not_url"


def test_find_local_ambiguous(tmp_path):
    media_root = tmp_path
    (media_root / "media").mkdir()
    (media_root / "media" / "a.jpg").write_bytes(b"x")
    (media_root / "media" / "b.jpg").write_bytes(b"y")
    # 두 원본 키가 하나의 정규화 키로 모이고 서로 다른 파일 → 모호성 오류.
    # 두 원본 키(스킴/호스트 대소문자만 다름)가 하나의 정규화 키로 모이고
    # 서로 다른 파일을 가리키면, 정규화 조회에서 모호성 오류.
    # 조회 키 자체는 어느 원본 키와도 정확 일치하지 않는다.
    media_map = {
        "HTTPS://x.com/a.jpg": "media/a.jpg",
        "https://X.COM/a.jpg": "media/b.jpg",
    }
    _, status = media_select.find_local_in_map(
        "https://x.com/a.jpg", media_map, media_root)
    assert status == "ambiguous"


def test_find_local_path_escape(tmp_path):
    media_root = tmp_path
    (media_root / "media").mkdir()
    outside = media_root / "secret.jpg"
    outside.write_bytes(b"s")
    media_map = {"https://x.com/evil.jpg": "../secret.jpg"}
    _, status = media_select.find_local_in_map(
        "https://x.com/evil.jpg", media_map, media_root)
    assert status == "invalid"
    # 절대 경로 탈출.
    media_map = {"https://x.com/evil2.jpg": str(outside)}
    _, status = media_select.find_local_in_map(
        "https://x.com/evil2.jpg", media_map, media_root)
    assert status == "invalid"
    # 파일 없음 → missing.
    media_map = {"https://x.com/gone.jpg": "media/gone.jpg"}
    _, status = media_select.find_local_in_map(
        "https://x.com/gone.jpg", media_map, media_root)
    assert status == "missing"


def test_mirror_enrich_selector_contract():
    """미러와 enrich가 같은 URL을 선택한다 (모든 입력 형태)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "media_mirror_mod", str(V2_ROOT / "media-mirror.py"))
    mirror = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mirror)
    cases = [
        # media 배열의 원소 1개가 [thumb, full] pair인 경우.
        ({"media": [["https://x.com/t.jpg", "https://x.com/f.jpg"]]},
         "https://x.com/f.jpg"),
        ({"media": [{"full": "https://x.com/f.jpg", "thumb": "https://x.com/t.jpg"}]},
         "https://x.com/f.jpg"),
        ({"media": [{"mediaUrl": "https://x.com/m.jpg"}]},
         "https://x.com/m.jpg"),
        ({"media": [{"fullHtml": "https://x.com/fh.jpg"}]},
         "https://x.com/fh.jpg"),
        ({"mediaImages": [["https://x.com/t.jpg", "https://x.com/f2.jpg"]]},
         "https://x.com/f2.jpg"),
        ({"media": ["https://x.com/s.jpg"]}, "https://x.com/s.jpg"),
    ]
    for post, expected in cases:
        urls = set()
        mirror.collect_post_urls(post, urls)
        assert urls == {expected}, (post, urls)
        # enrich 셀렉터도 동일.
        for field in ("media", "mediaImages"):
            for item in post.get(field, []):
                assert media_select.select_media_url(item) == expected


# ---------- ingest_post_html (P2-3 + P2-7) ----------

import ingest_post_html as ingester


def _capture_meta(**over):
    meta = {
        "version": "1",
        "validator_version": "p2-batch2-1",
        "requested_url": "https://x.com/Author1/status/123",
        "observed_main_url": "https://x.com/Author1/status/123",
        "author_handle": "Author1",
        "body_status": "text",
        "quote_status": "none",
        "replies_scope": {"collected": 0, "max": 100},
        "capture_type": "text",
    }
    meta.update(over)
    return meta


def _capture_html(tid="123", handle="Author1", text="hello world",
                  meta=None, quote=None, replies=None, source_href=None):
    """유효한 캡처 HTML. meta=None이면 메타데이터 없음 (구형)."""
    import html as htmlmod
    head = '<meta charset="utf-8">'
    if meta is not None:
        head += ('<meta name="x-capture" content="'
                 + htmlmod.escape(json.dumps(meta, ensure_ascii=False), quote=True) + '">')
    href = source_href or f"https://x.com/{handle}/status/{tid}"
    quote_html = ""
    if quote:
        quote_html = (
            f'<blockquote class="quoted-tweet" data-tweet-id="{quote["tid"]}"'
            f' data-author-handle="{quote["handle"]}" data-created-at="">'
            f'<p class="meta">인용: @{quote["handle"]}</p>'
            f'<p class="meta"><a href="https://x.com/{quote["handle"]}/status/{quote["tid"]}">'
            f'https://x.com/{quote["handle"]}/status/{quote["tid"]}</a></p>'
            f'<div class="tweet-text"><p>{quote["text"]}</p></div>'
            f'</blockquote>')
    replies_html = ""
    if replies:
        parts = []
        for r in replies:
            parts.append(
                f'<article class="reply" data-tweet-id="{r["tid"]}"'
                f' data-author-handle="{r["handle"]}" data-created-at=""'
                f' data-is-thread="false">'
                f'<p class="meta">{r["handle"]}</p>'
                f'<p class="meta"><a href="https://x.com/{r["handle"]}/status/{r["tid"]}">link</a></p>'
                f'<div class="tweet-text"><p>{r["text"]}</p></div>'
                f'</article>')
        replies_html = '<section class="replies">' + "".join(parts) + '</section>'
    return f"""<!DOCTYPE html>
<html><head>{head}<title>X {tid}</title></head><body>
<article data-tweet-id="{tid}" data-author-handle="{handle}" data-created-at="">
  <p class="meta">{handle} (@{handle})</p>
  <p class="meta"><a href="{href}">{href}</a></p>
  <div class="tweet-text"><p>{text}</p></div>
  {quote_html}{replies_html}
</article></body></html>
"""


def _run_ingester(indir, outdir):
    """ingester를 subprocess로 실행 (실제 진입점). Returns (rc, stdout)."""
    r = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "ingest_post_html.py"), str(indir),
         "-o", str(outdir)],
        capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_ingester_valid_capture(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "123.html").write_text(
        _capture_html(meta=_capture_meta()), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert len(posts) == 1
    assert posts[0]["tweetId"] == "123"
    assert posts[0]["sourceUrl"] == "https://x.com/Author1/status/123"
    assert posts[0]["quoteStatus"] == "none"
    review = json.loads((outdir / "review.json").read_text())
    assert review == []


def test_ingester_htm_and_case_variants(tmp_path):
    """P2-3: .htm/.HTML/.HTM도 동일 레코드를 만든다."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "a.htm").write_text(
        _capture_html(tid="111", meta=_capture_meta(
            requested_url="https://x.com/Author1/status/111",
            observed_main_url="https://x.com/Author1/status/111")), encoding="utf-8")
    (indir / "b.HTML").write_text(
        _capture_html(tid="222", meta=_capture_meta(
            requested_url="https://x.com/Author1/status/222",
            observed_main_url="https://x.com/Author1/status/222")), encoding="utf-8")
    (indir / "c.HtM").write_text(
        _capture_html(tid="333", meta=_capture_meta(
            requested_url="https://x.com/Author1/status/333",
            observed_main_url="https://x.com/Author1/status/333")), encoding="utf-8")
    (indir / "d.txt").write_text("not html", encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert sorted(p["tweetId"] for p in posts) == ["111", "222", "333"]


def test_ingester_excludes_symlink_and_subdir(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    real = indir / "real.html"
    real.write_text(_capture_html(meta=_capture_meta()), encoding="utf-8")
    (indir / "link.html").symlink_to(real)
    sub = indir / "sub"
    sub.mkdir()
    (sub / "nested.html").write_text(
        _capture_html(tid="999", meta=_capture_meta(
            requested_url="https://x.com/Author1/status/999",
            observed_main_url="https://x.com/Author1/status/999")), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert len(posts) == 1
    assert posts[0]["tweetId"] == "123"


def test_ingester_same_bytes_dedupe(tmp_path):
    """확장자만 다른 동일 바이트는 같은 레코드 (1건)."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    content = _capture_html(meta=_capture_meta())
    (indir / "a.html").write_text(content, encoding="utf-8")
    (indir / "a.htm").write_text(content, encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert len(posts) == 1


def test_ingester_same_tweet_different_bytes_to_review(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "a.html").write_text(
        _capture_html(text="version one", meta=_capture_meta()), encoding="utf-8")
    (indir / "b.html").write_text(
        _capture_html(text="version two", meta=_capture_meta()), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert len(posts) == 1
    assert posts[0]["fullText"] == "version one"
    review = json.loads((outdir / "review.json").read_text())
    assert len(review) == 1
    assert "동일 tweetId 충돌" in review[0]["reason"]


def test_ingester_failed_input_returns_nonzero(tmp_path):
    """입력 1건 실패 시 빈 성공 결과를 내보내지 않는다."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "bad.html").write_text("<html><body>no article</body></html>", encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert posts == []


def test_ingester_legacy_no_metadata_to_review(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "old.html").write_text(_capture_html(meta=None), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert posts == []
    review = json.loads((outdir / "review.json").read_text())
    assert len(review) == 1
    assert "legacy" in review[0]["reason"]


def test_ingester_metadata_mismatch_to_review(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    # 요청 ID(999)와 기록 ID(123) 불일치.
    (indir / "x.html").write_text(
        _capture_html(meta=_capture_meta(
            requested_url="https://x.com/Author1/status/999",
            observed_main_url="https://x.com/Author1/status/999")), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "요청 ID" in review[0]["reason"]


def test_ingester_wrong_source_link_to_review(tmp_path):
    """HTML의 메인 링크가 다른 글의 ID를 가리키면 검수 대상이다
    (메타데이터로 조용히 보정하지 않음 — Codex 1차 리뷰)."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(quote_status="extracted"),
            quote={"tid": "456", "handle": "Author2", "text": "quoted"},
            source_href="https://x.com/Author2/status/456"), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "HTML 메인 permalink" in review[0]["reason"]


def test_ingester_source_link_normalization_allowed(tmp_path):
    """호스트·query 차이만 있는 HTML 링크는 정규화해 통과한다."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(),
            source_href="https://twitter.com/Author1/status/123?s=20"),
        encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert posts[0]["sourceUrl"] == "https://x.com/Author1/status/123"


def test_ingester_photo_subpath_other_id_to_review(tmp_path):
    """다른 글 ID의 /photo/ 하위 링크도 검수 대상이다
    (파서 하위 경로 허용 후에도 ID 불일치는 검수 — Codex 2차 리뷰 ④)."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(quote_status="extracted"),
            quote={"tid": "456", "handle": "Author2", "text": "quoted"},
            source_href="https://x.com/Other/status/999/photo/1"), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "HTML 메인 permalink" in review[0]["reason"]


def test_ingester_photo_subpath_same_id_ok(tmp_path):
    """같은 글의 /photo/ 하위 링크는 정규화해 통과한다."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(),
            source_href="https://x.com/Author1/status/123/photo/1"),
        encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert posts[0]["sourceUrl"] == "https://x.com/Author1/status/123"


def test_ingester_unparsable_source_link_to_review(tmp_path):
    """파싱 불가 HTML permalink는 비교를 건너뛰지 않고 검수한다
    (파싱 실패를 정상화 허가로 취급하는 우회 방지 — Codex 2차 리뷰 ④)."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(),
            source_href="https://x.com/Author1/status/123/unknown/1"),
        encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "파싱 불가" in review[0]["reason"]


def test_ingester_quote_failed_to_review(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(meta=_capture_meta(quote_status="failed")), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "quote 상태 failed" in review[0]["reason"]


def test_ingester_quote_extracted_but_no_blockquote(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(meta=_capture_meta(quote_status="extracted")), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "인용 블록 없음" in review[0]["reason"]


def test_ingester_quote_present_but_meta_none(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(quote_status="none"),
            quote={"tid": "456", "handle": "Author2", "text": "quoted"}),
        encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "인용 블록 있음" in review[0]["reason"]


def test_ingester_blank_body_to_review(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(text="", meta=_capture_meta()), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 2, out
    review = json.loads((outdir / "review.json").read_text())
    assert "공백 본문" in review[0]["reason"]


def test_ingester_media_only_with_media_ok(tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    html = _capture_html(
        text="", meta=_capture_meta(body_status="media-only", capture_type="media-only"))
    # 미디어 <img>를 본문에 삽입한다.
    html = html.replace(
        '<div class="tweet-text"><p></p></div>',
        '<div class="tweet-text"><p></p></div>'
        '<figure><img src="https://pbs.twimg.com/media/x.jpg"></figure>')
    (indir / "x.html").write_text(html, encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert posts[0]["captureType"] == "media-only"


def test_ingester_sort_order_casefold(tmp_path):
    """정렬은 (name.casefold(), name) — 대소문자 혼합 파일 순서 고정."""
    indir = tmp_path / "in"
    indir.mkdir()
    for name in ["B.html", "a.html", "A.html", "b.html"]:
        (indir / name).write_text("x", encoding="utf-8")
    names = [p.name for p in ingester.collect_inputs(indir)]
    assert names == ["A.html", "a.html", "B.html", "b.html"]


# ---------- state_guard (P3) ----------

def test_validate_drive_state():
    ok, _ = state_guard.validate_drive_state({"processed": {}, "last_poll": "x"})
    assert ok
    ok, _ = state_guard.validate_drive_state([])
    assert not ok
    ok, _ = state_guard.validate_drive_state({"processed": []})
    assert not ok
    ok, _ = state_guard.validate_drive_state({"processed": {"a": "not-dict"}})
    assert not ok
    ok, _ = state_guard.validate_drive_state(
        {"processed": {"a": {"modifiedTime": 123}}})
    assert not ok
    ok, _ = state_guard.validate_drive_state(
        {"processed": {"a": {"modifiedTime": "t", "size": "1", "verification": "bogus"}}})
    assert not ok


def test_validate_monitor_state():
    ok, _ = state_guard.validate_monitor_state({"hashes": {"a": "x", "b": None}})
    assert ok
    ok, _ = state_guard.validate_monitor_state({"hashes": []})
    assert not ok
    ok, _ = state_guard.validate_monitor_state({})
    assert not ok
    ok, _ = state_guard.validate_monitor_state({"hashes": {"a": 1}})
    assert not ok


def test_load_state_guarded_drive_corrupt(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".drive_poll_state.json"
    raw = b'{"processed": {"a": '
    state_path.write_bytes(raw)
    with pytest.raises(state_guard.StateBlockedError):
        state_guard.load_state_guarded(state_path, kind="drive_poll_state")
    # 원본은 격리되고 (이동됨), 복구 마커가 남는다.
    assert not state_path.exists()
    marker = state_guard.read_recovery_marker(state_path)
    assert marker is not None
    assert marker["kind"] == "drive_poll_state"
    assert marker["byte_sha256"] == hashlib.sha256(raw).hexdigest()
    # 복구 마커가 있으면 다음 실행도 fail-closed (신규 초기화 아님).
    with pytest.raises(state_guard.StateBlockedError):
        state_guard.load_state_guarded(state_path, kind="drive_poll_state")
    # 격리본 + 메타데이터가 있다.
    quarantine = hooks / ".quarantine"
    dirs = list(quarantine.iterdir())
    assert len(dirs) == 1
    assert (dirs[0] / ".drive_poll_state.json").exists()
    assert (dirs[0] / "metadata.json").exists()


def test_load_state_guarded_drive_non_utf8(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".drive_poll_state.json"
    state_path.write_bytes(b"\xff\xfe{bad")
    with pytest.raises(state_guard.StateBlockedError):
        state_guard.load_state_guarded(state_path, kind="drive_poll_state")


def test_load_state_guarded_monitor_visible_init(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".monitor_state.json"
    state_path.write_text('{"hashes": "not-a-dict"}', encoding="utf-8")
    data, status = state_guard.load_state_guarded(state_path, kind="monitor_state")
    assert data == {}
    assert status == "post_quarantine_init"
    assert not state_path.exists()
    assert state_guard.read_recovery_marker(state_path) is not None
    # 마커가 있어도 모니터는 보이는 초기화로 진행한다.
    data, status = state_guard.load_state_guarded(state_path, kind="monitor_state")
    assert status == "post_quarantine_init"


def test_load_state_guarded_fresh(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".drive_poll_state.json"
    data, status = state_guard.load_state_guarded(state_path, kind="drive_poll_state")
    assert (data, status) == ({}, "fresh")


def test_load_state_guarded_directory_is_read_error(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".monitor_state.json"
    state_path.mkdir()
    with pytest.raises(state_guard.StateReadError):
        state_guard.load_state_guarded(state_path, kind="monitor_state")


def test_atomic_json_write_fsync_and_replace(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")
    state_guard.atomic_json_write(target, {"new": 1})
    assert json.loads(target.read_text()) == {"new": 1}
    # 임시 파일이 남지 않는다.
    assert list(tmp_path.iterdir()) == [target]


def test_append_review_entry_dedup_and_corrupt(tmp_path):
    marker = tmp_path / ".needs-review"
    assert state_guard.append_review_entry(marker, "", "reason A")
    assert state_guard.append_review_entry(marker, "", "reason A")  # 중복 제거
    assert state_guard.append_review_entry(marker, "", "reason B", extra={"fileId": "f1"})
    entries = json.loads(marker.read_text())
    assert len(entries) == 2
    assert entries[1]["fileId"] == "f1"
    # 손상된 마커는 격리하고 새로 시작한다.
    marker.write_text("{broken", encoding="utf-8")
    assert state_guard.append_review_entry(marker, "", "reason C")
    entries = json.loads(marker.read_text())
    assert len(entries) == 1
    assert entries[0]["reason"] == "reason C"


# ---------- drive_poll (P2-1 + P2-2 + P3) ----------

@contextmanager
def _noop_lock(*args, **kwargs):
    yield


@pytest.fixture
def poll_env(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    xbm = tmp_path / "xbm"
    xbm.mkdir()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "inbox_folder_id": "inbox-1",
        "local_inbox": str(inbox),
        "state_file": str(tmp_path / "legacy.json"),
    }), encoding="utf-8")
    (xbm / "posts.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(drive_poll, "HOOKS_DIR", hooks)
    monkeypatch.setattr(drive_poll, "SHARED_LOCK", hooks / ".drive_poll.lock")
    monkeypatch.setattr(drive_poll, "CONFIG_PATH", config)
    monkeypatch.setattr(drive_poll, "POSTS_JSON", xbm / "posts.json")
    monkeypatch.setattr(drive_poll, "TOOLS_DIR", TOOLS_DIR)
    monkeypatch.setattr(drive_poll, "INGEST_SCRIPT", TOOLS_DIR / "ingest_post_html.py")
    monkeypatch.setattr(drive_poll, "FAILURES_FILE", hooks / ".drive_poll_failures.json")
    monkeypatch.setattr(drive_poll, "PROPOSALS_FILE", hooks / ".merge_proposals.json")
    monkeypatch.setattr(drive_poll, "NEEDS_REVIEW", hooks / ".needs-review")
    monkeypatch.setattr(drive_poll, "QUARANTINE_DRIVE_DIR", hooks / ".quarantine" / "drive-files")
    monkeypatch.setattr(drive_poll, "STAGING_DIR", hooks / ".drive_poll_staging")
    monkeypatch.setattr(drive_poll, "XBM", xbm)
    monkeypatch.setattr(drive_poll, "pipeline_lock", _noop_lock)
    return {"hooks": hooks, "xbm": xbm, "inbox": inbox,
            "config": config, "tmp": tmp_path}


class FakeRun:
    """hatch_gws_cli 다운로드는 시뮬레이션, 나머지는 실제 실행."""

    def __init__(self, downloads):
        self.downloads = downloads
        self.download_calls = []

    def __call__(self, cmd, **kw):
        if cmd[0] == "hatch_gws_cli":
            params = json.loads(cmd[cmd.index("--params") + 1])
            fid = params["fileId"]
            out = cmd[cmd.index("-o") + 1]
            self.download_calls.append(fid)
            spec = self.downloads.get(fid)
            if spec == "timeout":
                raise subprocess.TimeoutExpired(cmd, timeout=300)
            if isinstance(spec, tuple) and spec[0] == "rc":
                return subprocess.CompletedProcess(cmd, spec[1], "", spec[2])
            Path(out).write_bytes(spec)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=kw.pop("timeout", 300), **kw)


def _drive_file(fid, name, content):
    return {"id": fid, "name": name,
            "modifiedTime": "2026-10-17T00:00:00Z", "size": str(len(content))}


def _valid_html(tid, text="body"):
    return _capture_html(
        tid=tid, text=text, meta=_capture_meta(
            requested_url=f"https://x.com/Author1/status/{tid}",
            observed_main_url=f"https://x.com/Author1/status/{tid}")).encode("utf-8")


def test_drive_poll_ok_and_file_failure(poll_env, monkeypatch):
    a = _valid_html("111", "A body")
    b = _capture_html(meta=None).encode("utf-8")  # legacy → 검수 대상
    c = _valid_html("333", "C body")
    files = [_drive_file("fid-a", "a.html", a),
             _drive_file("fid-b", "b.html", b),
             _drive_file("fid-c", "c.html", c)]
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)
    fake = FakeRun({"fid-a": a, "fid-b": b, "fid-c": c})
    monkeypatch.setattr(drive_poll, "run", fake)
    rc = drive_poll.main()
    assert rc == 3  # 파일 실패 존재 + 실패 기록 영속화.
    posts = json.loads((poll_env["xbm"] / "posts.json").read_text())
    assert sorted(p["tweetId"] for p in posts) == ["111", "333"]
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    assert len(failures["records"]) == 1
    rec = next(iter(failures["records"].values()))
    assert rec["fileId"] == "fid-b"
    assert rec["stage"] == "validation"
    assert rec["status"] == "quarantined"
    assert rec["evidence_path"] is not None
    assert (Path(rec["evidence_path"]) / "download.bin").exists()
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    assert state["processed"]["fid-a"]["verification"] == "verified"
    assert state["processed"]["fid-c"]["verification"] == "verified"
    assert state["processed"]["fid-b"]["verification"] == "quarantined"
    # 영속 보관: fileId + 콘텐츠 해시가 파일명에 있다.
    archived = list(poll_env["inbox"].iterdir())
    assert len(archived) == 2
    assert all("fid-" in p.name for p in archived)
    # .needs-review에 격리 알림이 있다 (기존 스키마 유지).
    review = json.loads((poll_env["hooks"] / ".needs-review").read_text())
    assert any(r.get("fileId") == "fid-b" for r in review)


def test_drive_poll_download_timeout_backoff_then_quarantine(poll_env, monkeypatch):
    content = _valid_html("111")
    files = [_drive_file("fid-d", "d.html", content)]
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)
    fake = FakeRun({"fid-d": "timeout"})
    monkeypatch.setattr(drive_poll, "run", fake)
    rc = drive_poll.main()
    assert rc == 3
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    rec = next(iter(failures["records"].values()))
    assert rec["stage"] == "download"
    assert rec["status"] == "retry_pending"
    assert rec["attempts"] == 1
    # 백오프 대기 중이면 재다운로드하지 않는다.
    fake.download_calls.clear()
    rc = drive_poll.main()
    assert rc == 0
    assert fake.download_calls == []
    # 백오프 만료 후 같은 방식 실패 2회 → 격리.
    failures["records"][rec["key"]]["next_retry_at"] = "2020-01-01T00:00:00+00:00"
    (poll_env["hooks"] / ".drive_poll_failures.json").write_text(
        json.dumps(failures), encoding="utf-8")
    rc = drive_poll.main()
    assert rc == 3
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    rec = next(iter(failures["records"].values()))
    assert rec["attempts"] == 2
    assert rec["status"] == "quarantined"


def test_drive_poll_canonical_duplicate_blocked(poll_env, monkeypatch):
    dup = {
        "tweetId": "111", "sourceUrl": "https://x.com/Author1/status/111",
        "fullText": "x", "author": {"handle": "@A", "name": "A"},
        "media": [], "quoted": None, "replies": [],
    }
    (poll_env["xbm"] / "posts.json").write_text(
        json.dumps([dup, dict(dup)]), encoding="utf-8")
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: [])
    rc = drive_poll.main()
    assert rc == 1


def test_drive_poll_state_corrupt_fail_closed(poll_env, monkeypatch):
    state_path = poll_env["hooks"] / ".drive_poll_state.json"
    raw = b'{"processed": {"a": '
    state_path.write_bytes(raw)
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: [])
    rc = drive_poll.main()
    assert rc == 1
    # 격리 + 복구 마커, 신규 초기화 아님.
    assert not state_path.exists()
    assert state_guard.read_recovery_marker(state_path) is not None
    # 다음 실행도 여전히 차단된다.
    rc = drive_poll.main()
    assert rc == 1


def test_drive_poll_legacy_revalidation_batch(poll_env, monkeypatch):
    legacy = {"processed": {}}
    files = []
    downloads = {}
    for i in range(7):
        fid = f"legacy-{i}"
        tid = str(1000 + i)
        content = _valid_html(tid, f"legacy {i}")
        legacy["processed"][fid] = {"modifiedTime": "2026-10-17T00:00:00Z",
                                    "size": str(len(content))}
        files.append(_drive_file(fid, f"legacy-{i}.html", content))
        downloads[fid] = content
    # Drive에서 사라진 legacy 파일 1건 → unavailable.
    legacy["processed"]["legacy-gone"] = {"modifiedTime": "2026-10-01T00:00:00Z",
                                          "size": "10"}
    (poll_env["tmp"] / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)
    fake = FakeRun(downloads)
    monkeypatch.setattr(drive_poll, "run", fake)
    rc = drive_poll.main()
    assert rc == 0
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    verified = [f for f, e in state["processed"].items()
                if e.get("verification") == "verified"]
    unverified = [f for f, e in state["processed"].items()
                  if e.get("verification") == "unverified"]
    assert len(verified) == 5  # 배치 제한.
    assert len(unverified) == 2
    assert state["processed"]["legacy-gone"]["verification"] == "unavailable"
    assert not any(e.get("legacyBaseline") for e in state["processed"].values())
    # 두 번째 폴에서 나머지 2건을 재검증한다.
    rc = drive_poll.main()
    assert rc == 0
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    assert all(e.get("verification") == "verified"
               for f, e in state["processed"].items() if f.startswith("legacy-")
               and f != "legacy-gone")
    posts = json.loads((poll_env["xbm"] / "posts.json").read_text())
    assert len(posts) == 7


def test_drive_poll_merge_conflict_becomes_proposal(poll_env, monkeypatch):
    old = {
        "tweetId": "111", "sourceUrl": "https://x.com/Author1/status/111",
        "fullText": "old text",
        # 인제스터가 같은 HTML에서 만드는 author와 동일 — fullText만 충돌.
        "author": {"handle": "@Author1", "name": "Author1 (@Author1)"},
        "media": [], "quoted": None, "replies": [], "category": "투자",
    }
    (poll_env["xbm"] / "posts.json").write_text(json.dumps([old]), encoding="utf-8")
    content = _valid_html("111", "new text")
    files = [_drive_file("fid-new", "new.html", content)]
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)
    monkeypatch.setattr(drive_poll, "run", FakeRun({"fid-new": content}))
    rc = drive_poll.main()
    assert rc == 0  # 충돌은 실패가 아니라 제안이다.
    posts = json.loads((poll_env["xbm"] / "posts.json").read_text())
    assert posts[0]["fullText"] == "old text"  # 기존 값 보존.
    assert posts[0]["category"] == "투자"
    proposals = json.loads((poll_env["hooks"] / ".merge_proposals.json").read_text())
    assert len(proposals["proposals"]) == 1
    assert proposals["proposals"][0]["path"] == "fullText"
    assert proposals["proposals"][0]["proposed"] == "new text"
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    assert state["processed"]["fid-new"]["verification"] == "verified"


# ---------- scheduler_runner (P2-1) ----------

def test_run_step_returns_kind_and_rc(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler_runner, "LOG_FILE", tmp_path / "sched.log")
    monkeypatch.setattr(scheduler_runner, "HOOKS_DIR", tmp_path)

    class FakeCompleted:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = "out"
            self.stderr = ""

    monkeypatch.setattr(scheduler_runner, "run_grouped",
                        lambda *a, **k: FakeCompleted(0))
    result = scheduler_runner.run_step("x", tmp_path / "x.py", 10)
    assert result == scheduler_runner.StepResult("ok", 0)
    monkeypatch.setattr(scheduler_runner, "run_grouped",
                        lambda *a, **k: FakeCompleted(3))
    result = scheduler_runner.run_step("x", tmp_path / "x.py", 10)
    assert result == scheduler_runner.StepResult("failed", 3)
    monkeypatch.setattr(scheduler_runner, "run_grouped",
                        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 10)))
    result = scheduler_runner.run_step("x", tmp_path / "x.py", 10)
    assert result.kind == "timeout"


def _scheduler_main_with(monkeypatch, tmp_path, drive_rc, monitor_rc, publish_rc):
    monkeypatch.setattr(scheduler_runner, "LOG_FILE", tmp_path / "sched.log")
    monkeypatch.setattr(scheduler_runner, "HOOKS_DIR", tmp_path)
    monkeypatch.setattr(scheduler_runner, "LOCK_FILE", tmp_path / ".sched.lock")
    calls = []

    class FakeCompleted:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def fake_grouped(cmd, **kw):
        # 스크립트 basename으로 판정 — tmp_path가 테스트 이름을 포함해
        # "drive_poll" 부분 문자열이 들어갈 수 있으므로 substring 금지.
        script = Path(cmd[1]).name
        calls.append(script)
        if script == "drive_poll.py":
            return FakeCompleted(drive_rc)
        if script == "monitor.py":
            return FakeCompleted(monitor_rc)
        return FakeCompleted(publish_rc)

    monkeypatch.setattr(scheduler_runner, "run_grouped", fake_grouped)
    monkeypatch.setattr(sys, "argv", ["scheduler_runner.py"])
    return scheduler_runner.main(), calls


def test_scheduler_drive_poll_rc3_proceeds_and_preserves_warning(monkeypatch, tmp_path):
    rc, calls = _scheduler_main_with(monkeypatch, tmp_path, 3, 0, 0)
    assert rc == 3  # monitor 성공으로 3의 경고가 지워지지 않는다.
    assert any("monitor" in c for c in calls)
    assert any("publish_settings" in c for c in calls)


def test_scheduler_drive_poll_rc1_stops(monkeypatch, tmp_path):
    rc, calls = _scheduler_main_with(monkeypatch, tmp_path, 1, 0, 0)
    assert rc == 1
    assert not any("monitor" in c for c in calls)


def test_scheduler_drive_poll_rc2_skips(monkeypatch, tmp_path):
    rc, calls = _scheduler_main_with(monkeypatch, tmp_path, 2, 0, 0)
    assert rc == 2
    assert not any("monitor" in c for c in calls)


def test_scheduler_monitor_failure_returns_2(monkeypatch, tmp_path):
    rc, _ = _scheduler_main_with(monkeypatch, tmp_path, 0, 1, 0)
    assert rc == 2


# ---------- monitor (P3) ----------

def test_monitor_corrupt_state_visible_init(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_file = hooks / ".monitor_state.json"
    state_file.write_text('{"hashes": "bad"}', encoding="utf-8")
    monkeypatch.setattr(monitor_mod, "HOOKS_DIR", hooks)
    monkeypatch.setattr(monitor_mod, "STATE_FILE", state_file)
    data = monitor_mod.load_state()
    assert data == {}
    assert not state_file.exists()
    assert state_guard.read_recovery_marker(state_file) is not None
    review = json.loads((hooks / ".needs-review").read_text())
    assert any("monitor" in r.get("reason", "") for r in review)


def test_monitor_save_state_atomic_and_clears_marker(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_file = hooks / ".monitor_state.json"
    monkeypatch.setattr(monitor_mod, "HOOKS_DIR", hooks)
    monkeypatch.setattr(monitor_mod, "STATE_FILE", state_file)
    # 복구 마커가 있어도 새 워터마크 저장 후 제거된다.
    marker = state_guard.recovery_marker_path(state_file)
    marker.write_text("{}", encoding="utf-8")
    monitor_mod.save_state({"a": "x"})
    assert json.loads(state_file.read_text())["hashes"] == {"a": "x"}
    assert not marker.exists()
    assert list(hooks.iterdir()) == [state_file]


def test_monitor_main_corrupt_state_runs_hook_and_saves(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    src = tmp_path / "posts.json"
    src.write_text("hello", encoding="utf-8")
    state_file = hooks / ".monitor_state.json"
    state_file.write_text('{"hashes": ', encoding="utf-8")  # 잘린 JSON
    monkeypatch.setattr(monitor_mod, "HOOKS_DIR", hooks)
    monkeypatch.setattr(monitor_mod, "STATE_FILE", state_file)
    monkeypatch.setattr(monitor_mod, "LOCK_FILE", hooks / ".monitor.lock")
    monkeypatch.setattr(monitor_mod, "SOURCES", {"posts": src})
    monkeypatch.setattr(sys, "argv", ["monitor.py"])

    class FakeCompleted:
        returncode = 0
        stdout = "hook ok"
        stderr = ""

    monkeypatch.setattr(monitor_mod, "run_grouped",
                        lambda *a, **k: FakeCompleted())
    monkeypatch.setattr(monitor_mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    rc = monitor_mod.main()
    assert rc == 0
    # 격리된 워터마크 대신 새 워터마크가 저장되고 마커가 제거된다.
    saved = json.loads(state_file.read_text())
    assert saved["hashes"]["posts"] == hashlib.sha256(b"hello").hexdigest()
    assert not state_guard.recovery_marker_path(state_file).exists()


# ---------- 배치 1 잔여 1: 세대 전체 순서 ----------

_GENERATION_NAMES = [
    "data.backup.20261017T120000",
    "data.backup.20261017T120000.0",
    "data.backup.20261017T120000.01",
    "data.backup.20261017T120000.1",
    "data.backup.20261017T120000.2",
]
# 기대 순서 — 구현의 sort key로 생성하지 않은 리터럴 상수.
_EXPECTED_GENERATION_ORDER = [
    "data.backup.20261017T120000",
    "data.backup.20261017T120000.0",
    "data.backup.20261017T120000.01",
    "data.backup.20261017T120000.1",
    "data.backup.20261017T120000.2",
]


def test_generation_sort_key_total_order_all_permutations():
    keys = {name: build_data._generation_sort_key(name)
            for name in _GENERATION_NAMES}
    # 키 유일성 (bare/.0, .01/.1 동률 금지).
    assert len(set(keys.values())) == len(_GENERATION_NAMES)
    # 모든 쌍 비교가 기대 순서와 일치한다.
    for a, b in itertools.combinations(_GENERATION_NAMES, 2):
        ia, ib = _EXPECTED_GENERATION_ORDER.index(a), _EXPECTED_GENERATION_ORDER.index(b)
        assert (keys[a] < keys[b]) == (ia < ib), (a, b)
    # 120개 순열 모두 동일 순서로 정렬된다.
    for perm in itertools.permutations(_GENERATION_NAMES):
        assert sorted(perm, key=build_data._generation_sort_key) == _EXPECTED_GENERATION_ORDER


def test_generation_prune_keeps_two_newest(tmp_path, monkeypatch):
    root = tmp_path / "v2"
    root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", root)
    for name in _GENERATION_NAMES:
        (root / name).mkdir()
    (root / "data.backup").mkdir()  # 패턴 불일치 — 건드리지 않는다.
    (root / "other").mkdir()
    build_data._prune_generations(keep=2)
    remaining = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert remaining == [
        "data.backup",
        "data.backup.20261017T120000.1",
        "data.backup.20261017T120000.2",
        "other",
    ]


# ---------- 배치 1 잔여 2: do_push 종단 테스트 ----------

def _git_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    })
    return env


def _git(repo, *args, env=None, check=True):
    r = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} 실패: {r.stderr}")
    return r


def _chunk_bytes(posts):
    return json.dumps({"posts": posts}, ensure_ascii=False).encode("utf-8")


def _manifest_for(chunks):
    m = {"source_hash": "s1", "chunks": {}}
    for name, posts in chunks.items():
        raw = _chunk_bytes(posts)
        m["chunks"][f"posts-{name}"] = {
            "file": f"data/posts-{name}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "count": len(posts),
        }
    return m


def _write_valid_commit(repo, chunks, msg, env):
    data = repo / "v2" / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name, posts in chunks.items():
        (data / f"posts-{name}.json").write_bytes(_chunk_bytes(posts))
    (data / "manifest.json").write_text(
        json.dumps(_manifest_for(chunks), ensure_ascii=False), encoding="utf-8")
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "-m", msg, env=env)
    return _git(repo, "rev-parse", "HEAD", env=env).stdout.strip()


def _remote_ref(bare, env):
    r = _git(bare, "rev-parse", "refs/heads/main", env=env, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def _remote_manifest_matches(bare, sha, chunks, env):
    """remote에서 B의 manifest와 참조 blob 해시를 다시 확인한다."""
    raw = _git(bare, "show", f"{sha}:v2/data/manifest.json", env=env).stdout
    m = json.loads(raw)
    for name in ("x", "web", "ib"):
        entry = m["chunks"][f"posts-{name}"]
        blob = _git(bare, "show", f"{sha}:v2/data/posts-{name}.json", env=env).stdout
        assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == entry["sha256"]
        assert len(json.loads(blob)["posts"]) == entry["count"]


@pytest.fixture
def push_env(tmp_path, monkeypatch):
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(bare), env=env)
    _git(bare, "config", "receive.denyNonFastForwards", "true", env=env)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main", env=env)
    _git(work, "config", "user.email", "t@t", env=env)
    _git(work, "config", "user.name", "t", env=env)
    _git(work, "remote", "add", "origin", str(bare), env=env)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", work)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "main")
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", True)
    # do_push 자식 프로세스에도 임시 Git 환경을 적용한다 (전역 설정·
    # 자격증명 접근 금지 — do_push은 env를 상속하므로 setenv로 격리).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    chunks = {"x": [{"id": "1"}], "web": [], "ib": []}
    sha_a = _write_valid_commit(work, chunks, "A", env)
    _git(work, "push", "origin", "main", env=env)
    _git(work, "fetch", "origin", env=env)
    return {"env": env, "bare": bare, "work": work,
            "hooks_dir": hooks_dir, "chunks": chunks, "sha_a": sha_a}


def test_do_push_end_to_end(push_env):
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    chunks = {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}
    sha_b = _write_valid_commit(work, chunks, "B", env)
    assert hook.do_push() is True
    assert _remote_ref(bare, env) == sha_b
    _remote_manifest_matches(bare, sha_b, chunks, env)


def test_do_push_invalid_commit_blocked(push_env):
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    data = work / "v2" / "data"
    (data / "manifest.json").write_text('{"chunks": {}}', encoding="utf-8")
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-m", "bad", env=env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) == push_env["sha_a"]
    # 검수 마커에 기록된다.
    review = json.loads((push_env["hooks_dir"] / ".needs-review").read_text())
    assert len(review) == 1


def test_do_push_no_baseline_blocked(tmp_path, monkeypatch):
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(bare), env=env)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main", env=env)
    _git(work, "config", "user.email", "t@t", env=env)
    _git(work, "config", "user.name", "t", env=env)
    _git(work, "remote", "add", "origin", str(bare), env=env)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", work)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "main")
    # do_push 자식에도 임시 Git 환경을 적용한다 (실제 HOME의
    # core.hooksPath·URL 재작성 설정이 do_push에만 적용되는 것 방지).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    _write_valid_commit(work, {"x": [{"id": "1"}], "web": [], "ib": []}, "A", env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) is None


def test_do_push_branch_mismatch_blocked(push_env):
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    _git(work, "checkout", "-b", "feature", env=env)
    chunks = {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}
    _write_valid_commit(work, chunks, "B-feature", env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) == push_env["sha_a"]


def test_do_push_non_fast_forward_blocked(tmp_path, monkeypatch, capsys):
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(bare), env=env)
    _git(bare, "config", "receive.denyNonFastForwards", "true", env=env)
    # bare HEAD를 main으로 지정한다 — clone이 main을 체크아웃해야
    # 브랜치 불일치가 아니라 non-fast-forward로 거부됨을 증명한다.
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main", env=env)
    # work1: A를 remote에 게시.
    work1 = tmp_path / "work1"
    work1.mkdir()
    _git(work1, "init", "-b", "main", env=env)
    _git(work1, "config", "user.email", "t@t", env=env)
    _git(work1, "config", "user.name", "t", env=env)
    _git(work1, "remote", "add", "origin", str(bare), env=env)
    sha_a = _write_valid_commit(work1, {"x": [{"id": "1"}], "web": [], "ib": []}, "A", env)
    _git(work1, "push", "origin", "main", env=env)
    # work2: A에서 분기한 B (remote의 A2와 무관).
    work2 = tmp_path / "work2"
    _git(tmp_path, "clone", str(bare), str(work2), env=env)
    _git(work2, "config", "user.email", "t@t", env=env)
    _git(work2, "config", "user.name", "t", env=env)
    assert _git(work2, "branch", "--show-current", env=env).stdout.strip() == "main"
    sha_b = _write_valid_commit(work2, {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}, "B", env)
    # work3: A와 무관한 A2를 remote에 강제 게시 (다른 히스토리).
    work3 = tmp_path / "work3"
    work3.mkdir()
    _git(work3, "init", "-b", "main", env=env)
    _git(work3, "config", "user.email", "t@t", env=env)
    _git(work3, "config", "user.name", "t", env=env)
    _git(work3, "remote", "add", "origin", str(bare), env=env)
    sha_a2 = _write_valid_commit(work3, {"x": [{"id": "9"}], "web": [], "ib": []}, "A2", env)
    # 설정용 force push는 denyNonFastForwards를 잠시 해제한다.
    _git(bare, "config", "receive.denyNonFastForwards", "false", env=env)
    try:
        _git(work3, "push", "--force", "origin", "main", env=env)
    finally:
        _git(bare, "config", "receive.denyNonFastForwards", "true", env=env)
    # work2에서 B를 push하면 non-fast-forward로 거부된다.
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", work2)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "main")
    # do_push 자식에도 임시 Git 환경을 적용한다 (실제 HOME의
    # core.hooksPath·URL 재작성 설정이 do_push에만 적용되는 것 방지).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    _git(work2, "fetch", "origin", env=env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) == sha_a2
    # 거부 원인이 브랜치 불일치가 아니라 non-fast-forward인지 do_push
    # 자체의 로그(자식 git push의 stderr)로 확인한다. 별도 수동 push
    # 호출은 do_push의 거부 원인을 증명하지 못한다.
    captured = capsys.readouterr().out
    assert "non-fast-forward" in captured


def test_do_push_receive_hook_rejection_blocked(push_env):
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    hook_path = bare / "hooks" / "pre-receive"
    hook_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook_path.chmod(0o755)
    chunks = {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}
    _write_valid_commit(work, chunks, "B", env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) == push_env["sha_a"]


def test_do_push_head_move_race_pushes_verified_sha(push_env, monkeypatch, tmp_path):
    """HEAD 이동 경쟁: 검증 완료 후 push 프로세스 시작 전에 HEAD를
    이동시켜도 remote에는 검증된 B가 도착해야 한다.

    동기화 지점은 PATH상의 git shim이다 — do_push가 `git push`를
    호출하는 순간 (검증 완료 후, push 시작 전) shim이 HEAD를 C로
    이동시킨 뒤 실제 git push를 실행한다. push·검증 함수는 모킹하지
    않고 실제 Git을 쓴다.
    """
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    chunks_b = {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}
    sha_b = _write_valid_commit(work, chunks_b, "B", env)
    # C를 미리 만들어 두되 HEAD는 B에 둔다.
    sha_c = _write_valid_commit(work, {"x": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
                                "web": [], "ib": []}, "C", env)
    _git(work, "reset", "--hard", sha_b, env=env)
    assert _git(work, "rev-parse", "HEAD", env=env).stdout.strip() == sha_b
    # git shim: 첫 `git push` 호출 시 HEAD를 C로 이동시킨다.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real_git = shutil.which("git")
    shim = bindir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        "for _a in \"$@\"; do\n"
        "  if [ \"$_a\" = \"push\" ] && [ -n \"$GIT_SHIM_MOVE_HEAD_TO\" ] "
        "&& [ ! -f \"$GIT_SHIM_FIRED\" ]; then\n"
        "    touch \"$GIT_SHIM_FIRED\"\n"
        "    \"$REAL_GIT\" -C \"$GIT_SHIM_REPO\" update-ref HEAD \"$GIT_SHIM_MOVE_HEAD_TO\"\n"
        "    break\n"
        "  fi\n"
        "done\n"
        "exec \"$REAL_GIT\" \"$@\"\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("REAL_GIT", real_git)
    monkeypatch.setenv("GIT_SHIM_REPO", str(work))
    monkeypatch.setenv("GIT_SHIM_MOVE_HEAD_TO", sha_c)
    monkeypatch.setenv("GIT_SHIM_FIRED", str(tmp_path / "shim-fired"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    # monkeypatch이 env를 복원하고, shim-fired 파일은 pytest tmp에 있어
    # 별도 정리가 필요 없다.
    assert hook.do_push() is True
    assert _git(work, "rev-parse", "HEAD", env=env).stdout.strip() == sha_c
    assert _remote_ref(bare, env) == sha_b  # C가 아니라 검증된 B.


# ---------- ios-bookmarklet.js (P2-7) ----------

BOOKMARKLET_JS = TOOLS_DIR / "ios-bookmarklet.js"


def _bookmarklet_js(articles, pathname):
    """fake DOM + 북마크릿 소스 + 결과 출력을 합친 node 모듈 스크립트.

    articles: [{handle, tid, text, profile: [...], status_links: [...],
                quote_text, quote_link, images: [...]}]
    - profile: 프로필 링크 핸들 목록 (작성자 후보).
    - status_links: [{href}] — 순서대로 메인 permalink, 인용 링크 등.
    - quote_text: 인용 카드 div[dir=auto] 텍스트 (없으면 인용 카드 없음).
    - quote_link: 인용 카드의 /status/ 링크 href.
    - images: 메인 글의 pbs.twimg.com/media 이미지 src 목록.
    """
    def esc(s):
        return json.dumps(s, ensure_ascii=False)
    art_js = []
    for a in articles:
        status_links = a.get("status_links") or [
            {"href": f"/{a['handle']}/status/{a['tid']}"}]
        profile = a.get("profile") or [a["handle"]]
        images = a.get("images") or []
        quote_text = a.get("quote_text")
        quote_link = a.get("quote_link")
        art_js.append(f"""{{
            handle: {esc(a['handle'])}, tid: {esc(a['tid'])},
            text: {esc(a.get('text', ''))},
            statusLinks: {esc(status_links)},
            profile: {esc(profile)},
            images: {esc(images)},
            quoteText: {esc(quote_text)},
            quoteLink: {esc(quote_link)},
            quoteContainsLink: {json.dumps(bool(a.get('quote_contains_link')))},
            chain: {json.dumps(bool(a.get('chain')))},
            headerText: {esc(a.get('header_text'))},
            bodyLinks: {esc(a.get('body_links') or [])},
            bodyInQuote: {json.dumps(bool(a.get('body_in_quote')))},
            bodyClosestHref: {esc(a.get('body_closest_href'))},
        }}""")
    return r"""
const downloads = [];
const alerts = [];
global.alert = (msg) => alerts.push(String(msg));
global.Blob = class {
  constructor(parts, opts) { this.parts = parts; this.type = opts && opts.type; }
};
global.URL = {
  createObjectURL: (blob) => { downloads.push({ type: blob.type, text: blob.parts.join('') }); return 'blob:fake'; },
  revokeObjectURL: () => {},
};
global.scrollTo = () => {};
global.getComputedStyle = () => ({ overflowY: 'visible' });
global.Node = { DOCUMENT_POSITION_FOLLOWING: 4 };
// HTMLCollection-like (실제 DOM: length + 인덱스 + iterable, indexOf 없음).
function toCollection(arr) {
  const o = { length: arr.length };
  arr.forEach((el, i) => { o[i] = el; });
  o[Symbol.iterator] = function* () { for (let i = 0; i < this.length; i++) yield this[i]; };
  return o;
}
function makeTextEl(text) {
  return {
    textContent: text, innerText: text,
    closest: () => null, parentElement: null,
    getAttribute: () => null, querySelector: () => null,
    querySelectorAll: () => [],
  };
}
function makeLink(href, text) {
  return {
    href, textContent: text || '',
    getAttribute: (n) => (n === 'href' ? href : null),
    closest: () => null, parentElement: null,
    querySelector: () => null, querySelectorAll: () => [],
    contains: () => false,
  };
}
function makeImg(src) {
  return {
    getAttribute: (n) => (n === 'src' ? src : (n === 'alt' ? '' : null)),
    closest: () => null, parentElement: null,
    querySelector: () => null, querySelectorAll: () => [],
    contains: () => false,
  };
}
function makeArticle(spec) {
  const bodyEl = makeTextEl(spec.text);
  const quoteEls = spec.quoteText != null ? [makeTextEl(spec.quoteText)] : [];
  const statusLinks = spec.statusLinks.map(l => makeLink(l.href, ''));
  const profileLinks = spec.profile.map(h => makeLink('/' + h, h));
  const imgs = spec.images.map(s => makeImg(s));
  // 본문 안에 붙여넣은 링크 — 본문 div 안에 있으므로 인용 후보가 아니다.
  const bodyLinkObjs = (spec.bodyLinks || []).map(href => makeLink(href, ''));
  bodyEl.contains = (el) => bodyLinkObjs.includes(el);
  if (spec.bodyClosestHref) {
    const _a = makeLink(spec.bodyClosestHref, '');
    bodyEl.closest = (sel) => (sel === 'a' ? _a : null);
  }
  const art = {
    textContent: spec.text,
    innerText: spec.text,
    closest: () => null, parentElement: null,
    getAttribute: () => null,
    compareDocumentPosition: () => 0,
    contains: () => false,
    querySelectorAll: (sel) => {
      if (sel === 'a[href*="/status/"]') return [...statusLinks, ...bodyLinkObjs];
      if (sel === 'a[href]') return [...profileLinks, ...statusLinks, ...bodyLinkObjs];
      if (sel.startsWith('a[href="/')) return profileLinks.filter(
        l => l.href === sel.slice(8, -2));
      if (sel === 'div[data-testid="tweetText"]') return [];
      if (sel === 'div[lang]') return [bodyEl];
      if (sel === 'div[dir="auto"]') return [bodyEl, ...quoteEls];
      if (sel === 'div') return [bodyEl, ...quoteEls];
      if (sel === 'img[src*="pbs.twimg.com/media"]') return imgs;
      if (sel === 'span, div' || sel === 'span') return [];
      return [];
    },
    querySelector: (sel) => {
      if (sel === 'a[href*="/status/"]') return statusLinks[0] || null;
      return null;
    },
  };
  // 인용 카드: 인용 링크가 있으면 그 앵커가 인용 카드 역할을 한다.
  // chain 모드는 아래 chain 블록에서 처리한다 (중복 추가 방지).
  if (spec.quoteLink != null && !spec.chain) {
    const qLink = makeLink(spec.quoteLink, '');
    if (spec.quoteContainsLink) {
      // 링크-포함 구조 (Codex 2차 리뷰 ② 재현): 인용 div 안에 인용 링크가
      // 있고 permalink보다 앞에 있다. 체인(parentElement/children)을
      // 모델링해 pre-pass·isAfter가 동작하게 한다.
      const quoteDiv = {
        tagName: 'DIV',
        textContent: spec.quoteText || '', innerText: spec.quoteText || '',
        children: toCollection([qLink]), parentElement: art,
        getAttribute: (n) => (n === 'dir' ? 'auto' : null),
        closest: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        contains: (el) => el === qLink || el === quoteDiv,
      };
      qLink.parentElement = quoteDiv;
      bodyEl.parentElement = art;
      bodyEl.tagName = 'DIV';
      const _bodyGetAttr = bodyEl.getAttribute;
      bodyEl.getAttribute = (n) => (n === 'dir' ? 'auto' : _bodyGetAttr(n));
      const permalinkEl = statusLinks[0] || null;
      if (permalinkEl) permalinkEl.parentElement = art;
      const chainChildren = [quoteDiv, ...(permalinkEl ? [permalinkEl] : []), bodyEl];
      art.children = toCollection(chainChildren);
      art.contains = (el) => el === art || chainChildren.includes(el) || el === qLink;
      const _artQSA = art.querySelectorAll;
      art.querySelectorAll = (sel) => {
        if (sel === 'div[dir="auto"]') return [quoteDiv, bodyEl];
        if (sel === 'div') return [quoteDiv, bodyEl];
        if (sel === 'a[href*="/status/"]') return [qLink, ...statusLinks];
        return _artQSA(sel);
      };
      statusLinks.push(qLink);
    } else {
      qLink.querySelectorAll = (sel) => {
        if (sel === 'div[dir="auto"]') return quoteEls;
        if (sel === 'img[src*="pbs.twimg.com/media"]') return [];
        return [];
      };
      // 본문 요소가 인용 카드 안에 있는 경우를 모델링한다.
      qLink.contains = (el) => (spec.bodyInQuote ? el === bodyEl : false);
      statusLinks.push(qLink);
    }
  }
  // chain 모드 (Codex 4차 리뷰): permalink → 본문(붙여넣은 링크) →
  // 실제 인용 카드(형제 구조) 순서의 체인을 HTMLCollection-like로
  // 모델링해 isAfter의 실제 DOM 경로를 증명한다. headerText가 있으면
  // permalink 앞 헤더 div를 추가한다 (헤더-only 테스트용).
  if (spec.chain) {
    const permalinkEl = statusLinks[0] || null;
    if (permalinkEl) permalinkEl.parentElement = art;
    bodyEl.parentElement = art;
    bodyEl.tagName = 'DIV';
    const _bg = bodyEl.getAttribute;
    bodyEl.getAttribute = (n) => (n === 'dir' ? 'auto' : _bg(n));
    bodyEl.children = toCollection(bodyLinkObjs);
    // 붙여넣은 링크의 부모를 본문 div로 연결한다 — pre-pass walk-up이
    // 본문 div에서 isAfter로 중단되는 경로를 검증하기 위함이다
    // (Codex 5차 리뷰). 부모가 null이면 walk-up이 시작도 하지 않는다.
    for (const _bl of bodyLinkObjs) _bl.parentElement = bodyEl;
    const chainEls = [];
    let headerDiv = null;
    if (spec.headerText != null) {
      headerDiv = {
        tagName: 'DIV', textContent: spec.headerText, innerText: spec.headerText,
        children: toCollection([]), parentElement: art,
        getAttribute: (n) => (n === 'dir' ? 'auto' : null),
        closest: () => null, querySelector: () => null, querySelectorAll: () => [],
        contains: (el) => el === headerDiv,
      };
      chainEls.push(headerDiv);
    }
    if (permalinkEl) chainEls.push(permalinkEl);
    chainEls.push(bodyEl);
    let chainQLink = null;
    if (spec.quoteLink != null) {
      chainQLink = makeLink(spec.quoteLink, '');
      const quoteCardEl = {
        tagName: 'DIV', textContent: '', innerText: '',
        children: toCollection([chainQLink, ...quoteEls]), parentElement: art,
        getAttribute: () => null, closest: () => null,
        // 실제 DOM: querySelector는 자손 중 첫 일치 요소를 반환한다.
        // (기존 () => null은 walk-up이 카드로 올라가지 못하게 하는 우회였다.)
        querySelector: (sel) => (sel === 'div[dir="auto"]' ? (quoteEls[0] || null) : null),
        querySelectorAll: (sel) => {
          if (sel === 'div[dir="auto"]') return quoteEls;
          if (sel === 'img[src*="pbs.twimg.com/media"]') return [];
          return [];
        },
        contains: (el) => el === quoteCardEl || el === chainQLink || quoteEls.includes(el),
      };
      chainQLink.parentElement = quoteCardEl;
      // 실제 DOM: 링크의 자손에는 형제 인용 div가 없다. 인용문은
      // walk-up으로 찾은 카드에서만 얻어야 한다 (기존 quoteEls 반환은 우회).
      chainQLink.querySelectorAll = () => [];
      chainQLink.querySelector = () => null;
      chainEls.push(quoteCardEl);
      statusLinks.push(chainQLink);
    }
    art.children = toCollection(chainEls);
    art.contains = (el) => {
      if (el === art) return true;
      if (chainEls.includes(el)) return true;
      if (el === chainQLink) return true;
      if (bodyLinkObjs.includes(el)) return true;
      if (quoteEls.includes(el)) return true;
      return false;
    };
    const _artQSA3 = art.querySelectorAll;
    art.querySelectorAll = (sel) => {
      if (sel === 'div[dir="auto"]') {
        const out = [];
        if (headerDiv) out.push(headerDiv);
        out.push(bodyEl, ...quoteEls);
        return out;
      }
      if (sel === 'div') {
        const out = [];
        if (headerDiv) out.push(headerDiv);
        out.push(bodyEl, ...quoteEls);
        return out;
      }
      if (sel === 'a[href*="/status/"]') {
        // DOM 순서: permalink → 본문 붙여넣은 링크 → 인용 링크
        const out = [];
        if (permalinkEl) out.push(permalinkEl);
        out.push(...bodyLinkObjs);
        if (chainQLink) out.push(chainQLink);
        return out;
      }
      return _artQSA3(sel);
    };
  }
  return art;
}
const articles = [""" + ",\n".join(art_js) + r"""].map(makeArticle);
global.document = {
  title: 'X',
  querySelectorAll: (sel) => {
    if (sel === 'article') return articles;
    return [];
  },
  querySelector: (sel) => null,
  createElement: (tag) => ({
    tagName: tag.toUpperCase(), href: '', download: '', style: {},
    click: () => {}, remove: () => {},
  }),
  body: { appendChild: () => {}, removeChild: () => {}, scrollHeight: 0 },
  head: { appendChild: () => {} },
  documentElement: {},
};
global.location = {
  href: 'https://x.com""" + pathname + r"""',
  pathname: '""" + pathname + r"""',
  origin: 'https://x.com',
};
global.window = global;
""" + BOOKMARKLET_JS.read_text(encoding="utf-8") + r"""
await new Promise(r => setTimeout(r, 9000));
console.log(JSON.stringify({
  downloads: downloads.map(d => ({ type: d.type, text: d.text })),
  alerts,
}));
"""


def _run_bookmarklet(articles, pathname):
    script = _bookmarklet_js(articles, pathname)
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError(f"북마크릿 실행 실패:\n{r.stderr}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_bookmarklet_valid_capture_downloads_once():
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world"}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1
    text = result["downloads"][0]["text"]
    assert 'data-tweet-id="123"' in text
    assert '<meta name="x-capture"' in text
    # 메타데이터가 HTML 이스케이프된 JSON으로 들어간다.
    assert "version" in text and "p2-batch2-1" in text
    assert result["alerts"] == []


def test_bookmarklet_unknown_url_no_download():
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world"}],
        "/home")
    assert result["downloads"] == []
    assert any("찾지 못했습니다" in a for a in result["alerts"])


def test_bookmarklet_quoted_article_handle_mismatch_no_download():
    """목표 글을 인용한 다른 글의 article만 있으면 저장하지 않는다."""
    result = _run_bookmarklet(
        [{"handle": "Other", "tid": "999", "text": "quoting Author1",
          "profile": ["Other"],
          "status_links": [{"href": "/Other/status/999"}],
          "quote_text": "hello world",
          "quote_link": "/Author1/status/123"}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("다른 작성자" in a for a in result["alerts"])


def test_bookmarklet_quote_failed_no_download():
    """인용 링크는 있으나 인용 텍스트·이미지 없으면 저장하지 않는다."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"}],
          "quote_text": None,
          "quote_link": "/Author2/status/456"}],
        "/Author1/status/123")
    # 인용 카드가 비어 있으면 quoteStatus=failed → 다운로드 0회.
    # (이 픽스처에서는 인용 앵커의 div[dir=auto]가 없으므로 failed.)
    assert result["downloads"] == []
    assert any("인용글" in a for a in result["alerts"])


def test_bookmarklet_quote_extracted_downloads_with_quote():
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"}],
          "quote_text": "quoted text",
          "quote_link": "/Author2/status/456"}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1
    text = result["downloads"][0]["text"]
    assert 'data-tweet-id="456"' in text
    assert "quoted text" in text


def test_bookmarklet_i_status_quote_textful_indeterminate():
    """텍스트 있는 인용 카드의 링크가 /i/status/면 indeterminate로 저장 중단
    (핸들 'i'로 오인해 extracted가 되면 안 된다 — Codex 2차 리뷰 ③)."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"}],
          "quote_text": "quoted text",
          "quote_link": "/i/status/456"}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("인용글" in a for a in result["alerts"])


def test_bookmarklet_quote_div_contains_link():
    """인용 div 안에 인용 링크가 있고 permalink보다 앞에 있어도
    인용은 추출되고 본문은 메인 글이다 (Codex 2차 리뷰 ② 재현 구조)."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"}],
          "quote_text": "quoted text",
          "quote_link": "/Other/status/456",
          "quote_contains_link": True}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1
    text = result["downloads"][0]["text"]
    # 메인 본문과 인용 블록을 분리해 각각의 내용·ID를 검증한다
    # (존재만 검사하면 역할 뒤바뀜을 검출하지 못함 — Codex 3차 비차단).
    assert "<blockquote" in text
    main_part, quote_part = text.split("<blockquote", 1)
    assert "hello world" in main_part
    assert "quoted text" not in main_part
    assert 'data-tweet-id="456"' in quote_part
    assert "quoted text" in quote_part


def test_bookmarklet_chain_pasted_link_and_quote():
    """체인 모드: permalink → 본문(붙여넣은 링크) → 실제 인용 카드.
    붙여넣은 링크는 인용이 아니고, 실제 인용이 추출되며, 본문·인용의
    ID·내용이 각각 올바르다 (Codex 4차 리뷰 ②-1)."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"}],
          "body_links": ["/Other/status/789"],
          "quote_text": "quoted text",
          "quote_link": "/Author2/status/456",
          "chain": True}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1
    text = result["downloads"][0]["text"]
    assert "<blockquote" in text
    main_part, quote_part = text.split("<blockquote", 1)
    assert "hello world" in main_part
    assert "quoted text" not in main_part
    assert 'data-tweet-id="456"' in quote_part
    assert "quoted text" in quote_part
    assert 'data-tweet-id="789"' not in text


def test_bookmarklet_header_only_no_download():
    """헤더 div만 있고 실제 본문·미디어가 없으면 저장 중단한다
    (findBodyEl null → 다운로드 0회 + 본문 오류 안내 — Codex 4차 리뷰 ②-3)."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "",
          "status_links": [{"href": "/Author1/status/123"}],
          "header_text": "header text",
          "chain": True}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("본문을 찾지 못했습니다" in a for a in result["alerts"])


# ---------- Codex 1차 리뷰 ①: 상태 격리 중단 시 fail-closed ----------

def test_state_guard_intent_marker_blocks_after_interruption(tmp_path):
    """격리 중단 (의도 마커만 남고 원본이 남아 있음) 후에도 fail-closed.

    다음 실행은 fresh로 진행하지 않고, 중단된 격리를 완료한 뒤 차단한다.
    """
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".drive_poll_state.json"
    state_path.write_text('{"processed": {"a": ', encoding="utf-8")  # 잘린 JSON
    # 이동 전에 중단된 것처럼 의도 마커만 남긴다.
    state_guard.write_recovery_marker(
        state_path, kind="drive_poll_state", quarantined_path=None,
        original_path=state_path, byte_sha256="abc", error="interrupted",
        status="quarantine_pending")
    # 의도 마커의 복원 절차에 중단 가능성이 안내된다.
    intent = state_guard.read_recovery_marker(state_path)
    assert intent["status"] == "quarantine_pending"
    assert "quarantine_pending" in intent["restore_procedure"]
    with pytest.raises(state_guard.StateBlockedError):
        state_guard.load_state_guarded(state_path, kind="drive_poll_state")
    # 중단된 격리가 완료된다: 원본은 격리되고 마커는 quarantined로 갱신.
    assert not state_path.exists()
    marker = state_guard.read_recovery_marker(state_path)
    assert marker["status"] == "quarantined"
    assert marker["quarantined_path"] is not None


def test_state_guard_valid_state_with_marker_blocked(tmp_path):
    """유효한 state 파일 + 미해결 마커 → 마커를 무시하고 진행하지 않는다."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".drive_poll_state.json"
    state_path.write_text('{"processed": {}}', encoding="utf-8")
    state_guard.write_recovery_marker(
        state_path, kind="drive_poll_state", quarantined_path="/tmp/q",
        original_path=state_path, byte_sha256="abc", error="x")
    with pytest.raises(state_guard.StateBlockedError):
        state_guard.load_state_guarded(state_path, kind="drive_poll_state")
    # monitor는 유효 state를 버리고 보이는 초기화로 진행한다.
    monitor_path = hooks / ".monitor_state.json"
    monitor_path.write_text('{"hashes": {"a": "b"}}', encoding="utf-8")
    state_guard.write_recovery_marker(
        monitor_path, kind="monitor_state", quarantined_path="/tmp/q",
        original_path=monitor_path, byte_sha256="abc", error="x")
    data, status = state_guard.load_state_guarded(
        monitor_path, kind="monitor_state")
    assert (data, status) == ({}, "post_quarantine_init")


def test_state_guard_quarantine_marker_status_complete(tmp_path):
    """격리 완료 후 마커 상태는 quarantined, 격리 경로가 기록된다."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    state_path = hooks / ".drive_poll_state.json"
    state_path.write_text('{"processed": {"a": ', encoding="utf-8")
    with pytest.raises(state_guard.StateBlockedError):
        state_guard.load_state_guarded(state_path, kind="drive_poll_state")
    marker = state_guard.read_recovery_marker(state_path)
    assert marker["status"] == "quarantined"
    assert marker["quarantined_path"] is not None


def test_atomic_json_write_dir_fsync_failure_propagates(tmp_path, monkeypatch):
    """디렉터리 fsync용 open 실패를 성공으로 삼키지 않는다."""
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")
    orig_open = os.open

    def _fail_dir(path, *args, **kwargs):
        if path == str(tmp_path):
            raise OSError("injected dir open failure")
        return orig_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", _fail_dir)
    with pytest.raises(OSError):
        state_guard.atomic_json_write(target, {"new": 1})


# ---------- Codex 1차 리뷰 ⑤: 빈 결과 검증 실패 + 증거 + media 유효성 ----------

def test_capture_contract_media_only_requires_valid_url():
    base = {
        "tweetId": "123",
        "sourceUrl": "https://x.com/Author1/status/123",
        "fullText": "",
        "author": {"handle": "@Author1", "name": "A"},
        "captureType": "media-only",
        "quoteStatus": "none",
    }
    # "https://"만으로는 media-only 예외를 통과하지 않는다.
    rec = dict(base, media=["https://"])
    ok, errors = capture_contract.verify_record(rec)
    assert not ok
    assert any("공백 본문" in e for e in errors)
    rec = dict(base, media=[{"full": "https://"}])
    ok, _ = capture_contract.verify_record(rec)
    assert not ok
    # 유효한 미디어 URL이면 예외가 적용된다.
    rec = dict(base, media=["https://x.com/a.jpg"])
    ok, _ = capture_contract.verify_record(rec)
    assert ok


def test_drive_poll_empty_results_is_validation_failure(poll_env, monkeypatch):
    """ingester가 rc=0으로 빈 posts.json을 내면 검증 실패 + 증거 보존."""
    content = _valid_html("111")
    files = [_drive_file("fid-e", "e.html", content)]
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)

    def _run(cmd, **kw):
        if cmd[0] == "hatch_gws_cli":
            out = cmd[cmd.index("-o") + 1]
            Path(out).write_bytes(content)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        # ingester 흉내: rc=0이지만 posts.json이 비어 있음.
        outdir = Path(cmd[cmd.index("-o") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "posts.json").write_text("[]", encoding="utf-8")
        (outdir / "review.json").write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(drive_poll, "run", _run)
    rc = drive_poll.main()
    assert rc == 3
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    rec = next(iter(failures["records"].values()))
    assert rec["stage"] == "validation"
    assert rec["error_code"] == "empty_results"
    assert rec["status"] == "quarantined"  # 검증 실패는 즉시 격리.
    # 증거가 보존된다 (다운로드 바이트 연결).
    assert rec["evidence_path"] is not None
    assert (Path(rec["evidence_path"]) / "download.bin").exists()
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    assert state["processed"]["fid-e"]["verification"] == "quarantined"


# ---------- Codex 1차 리뷰 ⑥: 락 경합 분리 ----------

def _failure_record(fid="fid-x", stage="download", error_code="timeout"):
    return {
        "key": f"{fid}:1:10", "fileId": fid, "fileName": f"{fid}.html",
        "modifiedTime": "1", "size": "10", "content_sha256": "abc",
        "stage": stage, "error_code": error_code, "subprocess_rc": None,
        "stderr_excerpt": "", "stderr_truncated": False, "tweetId": None,
        "evidence_path": None, "review_items": None,
    }


def test_persist_failure_lock_contention_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_poll, "FAILURES_FILE", tmp_path / "failures.json")
    monkeypatch.setattr(drive_poll, "NEEDS_REVIEW", tmp_path / ".needs-review")
    # 콘텐츠 실패 A 1회 → consecutive 1, retry_pending.
    r1 = drive_poll.persist_failure_record(_failure_record())
    assert r1["status"] == "retry_pending"
    assert r1["consecutive"] == 1
    # 락 경합 → consecutive 유지, 격리 아님, 락 표시.
    r2 = drive_poll.persist_failure_record(
        _failure_record(), lock_contention=True)
    assert r2["status"] == "retry_pending"
    assert r2["consecutive"] == 1
    assert r2["lock_contention"] is True
    # 락 경합 2회 더 → 여전히 격리되지 않는다.
    drive_poll.persist_failure_record(_failure_record(), lock_contention=True)
    r4 = drive_poll.persist_failure_record(
        _failure_record(), lock_contention=True)
    assert r4["status"] == "retry_pending"
    assert r4["consecutive"] == 1
    # 락 경합 뒤 같은 콘텐츠 실패 → 연속 2회로 격리 (락이 체인을 끊지 않음).
    r5 = drive_poll.persist_failure_record(_failure_record())
    assert r5["status"] == "quarantined"
    assert r5["consecutive"] == 2
    # .needs-review에는 격리된 콘텐츠 실패만 있다 (락 경합 없음).
    review = json.loads((tmp_path / ".needs-review").read_text())
    assert len(review) == 1
    assert "timeout" in review[0]["reason"]


def test_persist_failure_backoff_uses_consecutive(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_poll, "FAILURES_FILE", tmp_path / "failures.json")
    monkeypatch.setattr(drive_poll, "NEEDS_REVIEW", tmp_path / ".needs-review")
    # A → B → B: 세 번째 기록의 백오프는 누적 3회가 아니라 연속 2회 기준.
    # (연속 2회이므로 격리되지만, 그 전 B 1회째의 백오프는 300초여야 한다.)
    drive_poll.persist_failure_record(_failure_record(error_code="err-a"))
    r_b1 = drive_poll.persist_failure_record(_failure_record(error_code="err-b"))
    assert r_b1["consecutive"] == 1
    assert r_b1["status"] == "retry_pending"
    from datetime import datetime as _dt, timezone as _tz
    nxt = _dt.fromisoformat(r_b1["next_retry_at"])
    now = _dt.now(_tz.utc)
    assert 240 <= (nxt - now).total_seconds() <= 360  # backoff(1)=300초


def test_drive_poll_lock_timeout_retries_without_quarantine(poll_env, monkeypatch):
    content = _valid_html("111")
    files = [_drive_file("fid-l", "l.html", content)]
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)
    monkeypatch.setattr(drive_poll, "run", FakeRun({"fid-l": content}))

    @contextmanager
    def _raising_lock(*args, **kwargs):
        from pipeline_lock import PipelineLockTimeout
        raise PipelineLockTimeout("injected lock contention")
        yield

    monkeypatch.setattr(drive_poll, "pipeline_lock", _raising_lock)
    rc = drive_poll.main()
    assert rc == 3
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    rec = next(iter(failures["records"].values()))
    assert rec["stage"] == "merge"
    assert rec["error_code"] == "lock_timeout"
    assert rec["status"] == "retry_pending"
    assert rec["lock_contention"] is True
    assert rec["evidence_path"] is None  # 락 경합은 증거를 남기지 않음.
    # 백오프 만료 후 다시 락 경합 → 여전히 격리되지 않는다.
    failures["records"][rec["key"]]["next_retry_at"] = "2020-01-01T00:00:00+00:00"
    (poll_env["hooks"] / ".drive_poll_failures.json").write_text(
        json.dumps(failures), encoding="utf-8")
    rc = drive_poll.main()
    assert rc == 3
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    rec = next(iter(failures["records"].values()))
    assert rec["status"] == "retry_pending"
    assert rec["consecutive"] == 0


# ---------- Codex 1차 리뷰 ⑦: 다운로드 지문 재조회 + 배치 제한 ----------

def test_drive_poll_download_drift_modified_time(poll_env, monkeypatch):
    """다운로드 중 modifiedTime 변경 (동일 크기) → fingerprint_drift."""
    content = _valid_html("111")
    original = _drive_file("fid-m", "m.html", content)
    changed = dict(original, modifiedTime="2026-10-18T00:00:00Z")
    calls = {"n": 0}

    def _listing(inbox_id):
        calls["n"] += 1
        return [original] if calls["n"] == 1 else [changed]

    monkeypatch.setattr(drive_poll, "list_drive_files", _listing)
    monkeypatch.setattr(drive_poll, "run", FakeRun({"fid-m": content}))
    rc = drive_poll.main()
    assert rc == 3
    failures = json.loads((poll_env["hooks"] / ".drive_poll_failures.json").read_text())
    rec = next(iter(failures["records"].values()))
    assert rec["stage"] == "download"
    assert rec["error_code"] == "fingerprint_drift"
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    assert state["processed"]["fid-m"]["verification"] != "verified"


def test_drive_poll_legacy_batch_limit_on_fingerprint_change(poll_env, monkeypatch):
    """지문이 바뀐 미검증 legacy도 폴당 5건 제한을 받는다."""
    legacy = {"processed": {}}
    files = []
    downloads = {}
    for i in range(7):
        fid = f"legacy-{i}"
        tid = str(2000 + i)
        content = _valid_html(tid, f"changed {i}")
        # legacy 기록은 옛 지문, Drive 목록은 새 지문 (수정됨).
        legacy["processed"][fid] = {"modifiedTime": "2026-10-01T00:00:00Z",
                                    "size": str(len(content))}
        files.append(_drive_file(fid, f"legacy-{i}.html", content))
        # Drive 목록의 modifiedTime을 바꿔 지문 불일치를 만든다.
        files[-1] = dict(files[-1], modifiedTime="2026-10-17T00:00:00Z")
        downloads[fid] = content
    (poll_env["tmp"] / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(drive_poll, "list_drive_files", lambda inbox_id: files)
    monkeypatch.setattr(drive_poll, "run", FakeRun(downloads))
    rc = drive_poll.main()
    assert rc == 0
    state = json.loads((poll_env["hooks"] / ".drive_poll_state.json").read_text())
    verified = [f for f, e in state["processed"].items()
                if e.get("verification") == "verified"]
    unverified = [f for f, e in state["processed"].items()
                  if e.get("verification") == "unverified"]
    assert len(verified) == 5
    assert len(unverified) == 2


# ---------- Codex 1차 리뷰 ⑧: 누락 미디어 영속 pending ----------

def test_enrich_missing_media_persisted(tmp_path, monkeypatch):
    import enrich_tools as et
    monkeypatch.setattr(et, "DATA_DIR", tmp_path)
    monkeypatch.setattr(et, "V2_ROOT", tmp_path)
    monkeypatch.setattr(et, "MISSING_MEDIA_FILE", tmp_path / ".enrich_missing_media.json")
    monkeypatch.setattr(et, "log", lambda *a: None)
    (tmp_path / "media-map.json").write_text("{}", encoding="utf-8")
    (tmp_path / "posts-x.json").write_text(json.dumps({"posts": [
        {"tweetId": "t1", "fullText": "x", "media": ["https://x.com/missing.jpg"]}]}),
        encoding="utf-8")
    (tmp_path / "posts-web.json").write_text(json.dumps({"posts": []}), encoding="utf-8")
    candidates = et.collect_screenshots()
    assert candidates == []
    missing = json.loads((tmp_path / ".enrich_missing_media.json").read_text())
    assert missing == [{"tweetId": "t1", "url": "https://x.com/missing.jpg",
                        "detected_at": missing[0]["detected_at"]}]


def test_enrich_missing_media_keeps_retry_marker(tmp_path, monkeypatch):
    import enrich_tools as et
    monkeypatch.setattr(et, "DATA_DIR", tmp_path)
    monkeypatch.setattr(et, "V2_ROOT", tmp_path)
    monkeypatch.setattr(et, "TOOLS_JSON", tmp_path / "ai-tools.json")
    monkeypatch.setattr(et, "CACHE_JSON", tmp_path / "ai-tools-cache.json")
    monkeypatch.setattr(et, "PROGRESS_JSON", tmp_path / "progress.json")
    monkeypatch.setattr(et, "RETRY_MARKER", tmp_path / ".retry-enrich")
    monkeypatch.setattr(et, "MANIFEST_JSON", tmp_path / "manifest.json")
    monkeypatch.setattr(et, "MISSING_MEDIA_FILE", tmp_path / ".enrich_missing_media.json")
    monkeypatch.setattr(et, "collect_screenshots", lambda: [])
    monkeypatch.setattr(et, "fingerprint", lambda *a: "fp")
    monkeypatch.setattr(et, "analyze_image_batch", lambda *a, **k: {})
    monkeypatch.setattr(et, "verify_repo_with_readme", lambda *a, **k: None)
    monkeypatch.setattr(et, "get_readme", lambda *a, **k: None)
    monkeypatch.setattr(et, "log", lambda *a: None)
    (tmp_path / "posts-x.json").write_text(json.dumps({"posts": []}), encoding="utf-8")
    (tmp_path / "posts-web.json").write_text(json.dumps({"posts": []}), encoding="utf-8")
    (tmp_path / "ai-tools.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ai-tools-cache.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ai-tools-vision-cache.json").write_text("{}", encoding="utf-8")
    # 누락 미디어만 남은 상태 — 다른 미해결 항목이 없어도 마커가 유지된다.
    (tmp_path / ".enrich_missing_media.json").write_text(json.dumps([
        {"tweetId": "t1", "url": "https://x.com/missing.jpg",
         "detected_at": "2026-10-17T00:00:00+00:00"}]), encoding="utf-8")
    et.run_backfill(dry_run=False, max_items=1)
    assert (tmp_path / ".retry-enrich").exists()
    # 누락이 해소되면 마커가 삭제된다.
    (tmp_path / ".enrich_missing_media.json").write_text("[]", encoding="utf-8")
    et.run_backfill(dry_run=False, max_items=1)
    assert not (tmp_path / ".retry-enrich").exists()


# ---------- Codex 1차 리뷰 ②③: 북마크릿 본문/인용 분리 + indeterminate ----------

def test_bookmarklet_photo_link_not_quote():
    """메인 글 자신의 사진 링크는 인용이 아니다."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"},
                           {"href": "/Author1/status/123/photo/1"}]}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1
    assert "인용" not in " ".join(result["alerts"])


def test_bookmarklet_unparsable_quote_candidate_indeterminate():
    """인용 후보이나 신원 파싱 불가 (/Other/status/abc) → 저장 중단."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"},
                           {"href": "/Other/status/abc"}]}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("인용글" in a for a in result["alerts"])


def test_bookmarklet_i_status_other_tweet_indeterminate():
    """/i/status/<다른 ID>는 작성자 불명 인용 → 저장 중단."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"},
                           {"href": "/i/status/456"}]}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("인용글" in a for a in result["alerts"])


def test_bookmarklet_i_status_own_tweet_ok():
    """/i/status/<자신 ID>는 메인 글 자신의 링크 → 정상 저장."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"},
                           {"href": "/i/status/123"}]}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1


def test_bookmarklet_pasted_x_link_in_body_not_quote():
    """본문 안에 붙여넣은 X 링크는 인용이 아니다."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "check this link",
          "status_links": [{"href": "/Author1/status/123"}],
          "body_links": ["/Other/status/456"]}],
        "/Author1/status/123")
    assert len(result["downloads"]) == 1
    assert "data-tweet-id=\"456\"" not in result["downloads"][0]["text"]


def test_bookmarklet_body_inside_quote_card_aborts():
    """본문 요소가 인용 카드 안에 있으면 저장 중단."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "status_links": [{"href": "/Author1/status/123"}],
          "quote_text": "quoted text",
          "quote_link": "/Author2/status/456",
          "body_in_quote": True}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("인용 카드" in a for a in result["alerts"])


def test_bookmarklet_body_inside_other_link_aborts():
    """본문 요소가 다른 글의 링크 안에 있으면 저장 중단."""
    result = _run_bookmarklet(
        [{"handle": "Author1", "tid": "123", "text": "hello world",
          "body_closest_href": "/Other/status/456"}],
        "/Author1/status/123")
    assert result["downloads"] == []
    assert any("다른 글의 링크" in a for a in result["alerts"])


# ---------- x_post_to_html EXTRACT_JS (Codex 1차 리뷰 ②③) ----------

def _extract_x_post_js():
    """x_post_to_html.py에서 EXTRACT_JS의 Python 문자열 값을 추출한다.

    원시 소스가 아니라 Python이 보는 값(파일의 `\\\\` → 값의 `\\`,
    즉 브라우저에 전달되는 JS 소스)을 반환해야 한다.
    ast.literal_eval로 정확히 디코드한다.
    """
    import ast
    src = (TOOLS_DIR / "x_post_to_html.py").read_text(encoding="utf-8")
    m = re.search(r'EXTRACT_JS = (""".*?""")', src, re.DOTALL)
    assert m, "EXTRACT_JS를 찾지 못함"
    return ast.literal_eval(m.group(1))


def _run_extract_js(articles, tid, req_handle):
    """mini-DOM에서 EXTRACT_JS를 실행한다.

    articles: [{handle, tid, text, profile_first: handle|None,
                quote: {handle, tid, text}|None, quote_first: bool,
                status_links: [href], body_links: [href]}]
    """
    def esc(s):
        return json.dumps(s, ensure_ascii=False)
    art_js = []
    for a in articles:
        art_js.append(f"""{{
            handle: {esc(a['handle'])}, tid: {esc(a['tid'])},
            text: {esc(a.get('text', ''))},
            profileFirst: {esc(a.get('profile_first'))},
            quote: {esc(a.get('quote'))},
            quoteFirst: {json.dumps(bool(a.get('quote_first')))},
            quoteHref: {esc(a.get('quote_href'))},
            quoteContainsLink: {json.dumps(bool(a.get('quote_contains_link')))},
            headerText: {esc(a.get('header_text'))},
            statusLinks: {esc(a.get('status_links') or [])},
            bodyLinks: {esc(a.get('body_links') or [])},
        }}""")
    script = r"""
// HTMLCollection-like (실제 DOM: length + 인덱스 + iterable, indexOf 없음).
// isAfter가 Array.prototype.indexOf.call을 써야 함을 증명한다 (Codex 3차 ②-1).
function toCollection(arr) {
  const o = { length: arr.length };
  arr.forEach((el, i) => { o[i] = el; });
  o[Symbol.iterator] = function* () { for (let i = 0; i < this.length; i++) yield this[i]; };
  return o;
}
function matches(e, sel) {
  const parts = sel.split(',').map(s => s.trim());
  return parts.some(p => matchOne(e, p));
}
function matchOne(e, sel) {
  if (/^[a-z][a-z0-9]*$/.test(sel)) return e.tag === sel;  // 'a', 'div' 등 태그만
  if (sel === '[aria-label]') return e.attrs['aria-label'] !== undefined;
  if (sel === '[role="heading"]') return e.attrs['role'] === 'heading';
  if (sel === 'a[href]') return e.tag === 'a' && e.attrs['href'] !== undefined;
  if (sel === 'a[href*="/status/"]') return e.tag === 'a' && (e.attrs['href'] || '').includes('/status/');
  if (sel === 'div[dir="auto"]') return e.tag === 'div' && e.attrs['dir'] === 'auto';
  if (sel === 'img[src*="pbs.twimg.com/media"]') return e.tag === 'img' && (e.attrs['src'] || '').includes('pbs.twimg.com/media');
  const m = sel.match(/^a\[href="([^"]+)"\]$/);
  if (m) return e.tag === 'a' && e.attrs['href'] === m[1];
  throw new Error('unsupported selector: ' + sel);
}
function isDescendant(root, el) {
  if (root === el) return true;
  for (const c of root.children) if (isDescendant(c, el)) return true;
  return false;
}
function queryAll(root, sel) {
  const out = [];
  const walk = (e) => {
    for (const c of e.children) {
      if (matches(c, sel)) out.push(c);
      walk(c);
    }
  };
  walk(root);
  return out;
}
function makeEl(tag, attrs, children, text) {
  const e = {
    tag, tagName: tag.toUpperCase(), attrs: attrs || {}, children: toCollection(children || []),
    textContent: text || '', innerText: text || '',
    parentElement: null,
    getAttribute(n) { return this.attrs[n] !== undefined ? this.attrs[n] : null; },
    querySelectorAll(sel) { return queryAll(this, sel); },
    querySelector(sel) { const r = queryAll(this, sel); return r[0] || null; },
    contains(other) { return isDescendant(this, other); },
    closest(sel) { let cur = this; while (cur) { if (matches(cur, sel)) return cur; cur = cur.parentElement; } return null; },
    compareDocumentPosition() { return 0; },
  };
  for (const c of e.children) c.parentElement = e;
  return e;
}
function makeArticle(spec) {
  const bodyDiv = makeEl('div', {dir: 'auto'}, [], spec.text);
  const bodyLinks = (spec.bodyLinks || []).map(h => makeEl('a', {href: h}, [], ''));
  bodyDiv.children = toCollection(bodyLinks);
  for (const c of bodyDiv.children) c.parentElement = bodyDiv;
  const profileLink = makeEl('a', {href: '/' + spec.handle}, [], spec.handle);
  const permalink = makeEl('a', {href: '/' + spec.handle + '/status/' + spec.tid}, [], '12:00');
  const extraLinks = (spec.statusLinks || []).map(h => makeEl('a', {href: h}, [], ''));
  let quoteCard = null;
  if (spec.quote) {
    const qHref = spec.quoteHref || ('/' + spec.quote.handle + '/status/' + spec.quote.tid);
    const qLink = makeEl('a', {href: qHref}, [], '');
    if (spec.quoteContainsLink) {
      // 링크-포함 구조: 인용 div 안에 인용 링크가 있다 (Codex 2차 리뷰 ② 재현).
      quoteCard = makeEl('div', {dir: 'auto'}, [qLink], spec.quote.text);
    } else {
      const qDiv = makeEl('div', {dir: 'auto'}, [], spec.quote.text);
      quoteCard = makeEl('div', {}, [qLink, qDiv], '');
    }
  }
  const mainProfile = makeEl('a', {href: '/' + spec.handle}, [], spec.handle + ' Name');
  // 인용 작성자 프로필 링크가 먼저 오는 경우 (profileFirst).
  const firstProfile = spec.profileFirst
    ? makeEl('a', {href: '/' + spec.profileFirst}, [], spec.profileFirst)
    : null;
  const kids = [];
  if (spec.quoteFirst && quoteCard) kids.push(quoteCard);
  if (firstProfile) kids.push(firstProfile);
  // 실제 X 순서: 헤더(프로필·permalink) → 본문 → 인용 카드.
  // headerText가 있으면 permalink보다 앞의 헤더 div를 추가한다.
  const headerDiv = spec.headerText != null
    ? makeEl('div', {dir: 'auto'}, [], spec.headerText) : null;
  if (headerDiv) kids.push(headerDiv);
  kids.push(profileLink, mainProfile, permalink, bodyDiv, ...extraLinks);
  if (!spec.quoteFirst && quoteCard) kids.push(quoteCard);
  return makeEl('article', {}, kids, '');
}
const articles = [""" + ",\n".join(art_js) + r"""].map(makeArticle);
const docRoot = makeEl('div', {}, articles, '');
global.document = {
  querySelectorAll: (sel) => queryAll(docRoot, sel),
  querySelector: (sel) => queryAll(docRoot, sel)[0] || null,
};
global.Node = { DOCUMENT_POSITION_FOLLOWING: 4 };
const extract = eval(EXTRACT_JS_PLACEHOLDER);
const result = extract({ tid: TID_PLACEHOLDER, maxReplies: 100, reqHandle: REQHANDLE_PLACEHOLDER });
console.log(JSON.stringify(result));
"""
    script = script.replace("EXTRACT_JS_PLACEHOLDER", json.dumps(_extract_x_post_js()))
    script = script.replace("TID_PLACEHOLDER", json.dumps(tid))
    script = script.replace("REQHANDLE_PLACEHOLDER", json.dumps(req_handle))
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError(f"EXTRACT_JS 실행 실패:\n{r.stderr}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_extract_js_main_body_excludes_quote():
    """인용 div가 본문 div보다 먼저 와도 본문은 메인 글 텍스트이다."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body text",
          "quote": {"handle": "Author2", "tid": "456", "text": "quoted text"},
          "quote_first": True}],
        "123", "Author1")
    assert result["text"] == "main body text"
    assert result["quoteStatus"] == "extracted"
    assert result["quote"]["text"] == "quoted text"


def test_extract_js_main_handle_from_permalink():
    """인용 작성자 프로필 링크가 먼저 와도 작성자는 메인 permalink 핸들이다."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "profile_first": "Author2",
          "quote": {"handle": "Author2", "tid": "456", "text": "quoted"}}],
        "123", "Author1")
    assert result["handle"] == "Author1"


def test_extract_js_photo_link_not_quote():
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "status_links": ["/Author1/status/123/photo/1"]}],
        "123", "Author1")
    assert result["quoteStatus"] == "none"


def test_extract_js_unparsable_quote_candidate_indeterminate():
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "status_links": ["/Other/status/abc"]}],
        "123", "Author1")
    assert result["quoteStatus"] == "indeterminate"


def test_extract_js_pasted_x_link_in_body_not_quote():
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "check this",
          "body_links": ["/Other/status/456"]}],
        "123", "Author1")
    assert result["quoteStatus"] == "none"


def test_extract_js_quote_first_with_pasted_link_in_body():
    """인용 카드가 먼저 + 본문에 붙여넣은 링크: 인용은 실제 인용 카드,
    붙여넣은 링크는 인용이 아니다."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "quote": {"handle": "Author2", "tid": "456", "text": "quoted"},
          "quote_first": True,
          "body_links": ["/Other/status/789"]}],
        "123", "Author1")
    assert result["quoteStatus"] == "extracted"
    assert result["quote"]["id"] == "456"
    assert result["text"] == "main body"


def test_extract_js_i_status_quote_textful_indeterminate():
    """텍스트 있는 인용 카드의 링크가 /i/status/면 indeterminate이다
    (핸들 'i'로 오인해 extracted가 되면 안 된다 — Codex 2차 리뷰 ③)."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "quote": {"handle": "Author2", "tid": "456", "text": "quoted text"},
          "quote_href": "/i/status/456"}],
        "123", "Author1")
    assert result["quoteStatus"] == "indeterminate"


def test_extract_js_quote_div_contains_link():
    """인용 div 안에 인용 링크가 있고 permalink보다 앞에 있어도
    인용은 추출되고 본문은 메인 글이다 (Codex 2차 리뷰 ② 재현 구조)."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "quote": {"handle": "Other", "tid": "456", "text": "quoted text"},
          "quote_first": True,
          "quote_contains_link": True}],
        "123", "Author1")
    assert result["quoteStatus"] == "extracted"
    assert result["quote"]["text"] == "quoted text"
    assert result["text"] == "main body"


def test_extract_js_header_div_before_permalink_not_body():
    """permalink 앞 헤더 div가 있어도 최종 본문은 실제 본문이다
    (main.text가 mainBodyEl을 써야 함 — Codex 3차 리뷰 ②-2)."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "main body",
          "header_text": "header text",
          "quote": {"handle": "Author2", "tid": "456", "text": "quoted"}}],
        "123", "Author1")
    assert result["text"] == "main body"
    assert result["quoteStatus"] == "extracted"


def test_extract_js_only_header_before_permalink_no_fallback():
    """permalink 앞 헤더 div만 있고 실제 본문이 없으면 본문은 빈 값이다
    (폴백으로 헤더를 본문으로 쓰지 않음 — Codex 3차 리뷰 ②-3)."""
    result = _run_extract_js(
        [{"handle": "Author1", "tid": "123", "text": "",
          "header_text": "header text"}],
        "123", "Author1")
    assert result["text"] == ""
