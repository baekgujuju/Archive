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


def test_ingester_wrong_source_link_corrected_by_normalization(tmp_path):
    """HTML의 메인 링크가 인용글을 가리켜도, 검증된 메인 permalink로
    sourceUrl을 정규화해 생성한다 (첫 status 링크를 믿지 않음)."""
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    (indir / "x.html").write_text(
        _capture_html(
            meta=_capture_meta(quote_status="extracted"),
            quote={"tid": "456", "handle": "Author2", "text": "quoted"},
            source_href="https://x.com/Author2/status/456"), encoding="utf-8")
    rc, out = _run_ingester(indir, outdir)
    assert rc == 0, out
    posts = json.loads((outdir / "posts.json").read_text())
    assert posts[0]["sourceUrl"] == "https://x.com/Author1/status/123"


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


def test_do_push_non_fast_forward_blocked(tmp_path, monkeypatch):
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(bare), env=env)
    _git(bare, "config", "receive.denyNonFastForwards", "true", env=env)
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
    _git(work2, "fetch", "origin", env=env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) == sha_a2


def test_do_push_receive_hook_rejection_blocked(push_env):
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    hook_path = bare / "hooks" / "pre-receive"
    hook_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook_path.chmod(0o755)
    chunks = {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}
    _write_valid_commit(work, chunks, "B", env)
    assert hook.do_push() is False
    assert _remote_ref(bare, env) == push_env["sha_a"]


def test_do_push_head_move_race_pushes_verified_sha(push_env, monkeypatch):
    """HEAD 이동 경쟁: push 대기 중 로컬 HEAD를 C로 이동해도
    remote에는 검증된 B가 도착해야 한다."""
    env, bare, work = push_env["env"], push_env["bare"], push_env["work"]
    started = push_env["hooks_dir"] / "hook-started"
    signal = push_env["hooks_dir"] / "hook-signal"
    hook_path = bare / "hooks" / "pre-receive"
    hook_path.write_text(
        "#!/bin/sh\n"
        f'touch "{started}"\n'
        "for i in $(seq 1 100); do "
        f'[ -f "{signal}" ] && exit 0; sleep 0.1; done\n'
        "exit 1\n", encoding="utf-8")
    hook_path.chmod(0o755)
    chunks_b = {"x": [{"id": "1"}, {"id": "2"}], "web": [], "ib": []}
    sha_b = _write_valid_commit(work, chunks_b, "B", env)
    result = {}

    def _push():
        result["ok"] = hook.do_push()

    t = threading.Thread(target=_push, daemon=True)
    t.start()
    deadline = __import__("time").monotonic() + 20
    while not started.exists():
        if __import__("time").monotonic() > deadline:
            raise AssertionError("pre-receive hook이 시작되지 않음")
        __import__("time").sleep(0.05)
    # push 대기 중 로컬 HEAD를 C로 이동한다.
    chunks_c = {"x": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "web": [], "ib": []}
    _write_valid_commit(work, chunks_c, "C", env)
    assert _git(work, "rev-parse", "HEAD", env=env).stdout.strip() != sha_b
    signal.touch()
    t.join(timeout=30)
    assert not t.is_alive()
    assert result.get("ok") is True
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
  const art = {
    textContent: spec.text,
    innerText: spec.text,
    closest: () => null, parentElement: null,
    getAttribute: () => null,
    compareDocumentPosition: () => 0,
    contains: () => false,
    querySelectorAll: (sel) => {
      if (sel === 'a[href*="/status/"]') return statusLinks;
      if (sel === 'a[href]') return [...profileLinks, ...statusLinks];
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
  if (spec.quoteLink != null) {
    const qLink = makeLink(spec.quoteLink, '');
    qLink.querySelectorAll = (sel) => {
      if (sel === 'div[dir="auto"]') return quoteEls;
      if (sel === 'img[src*="pbs.twimg.com/media"]') return [];
      return [];
    };
    statusLinks.push(qLink);
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
