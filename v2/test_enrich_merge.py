#!/usr/bin/env python3
"""Tests for the append-only / diff-merge save path in enrich_tools.py.

Run: python3 v2/test_enrich_merge.py
Covers (2026-10-17 data-loss fix):
- prune removes only posts gone from the archive (authoritative posts-x set)
- fingerprint drift keeps the entry with a stale flag; matching clears it
- _merge_save preserves an external writer's changes and applies deletions
- cumulative diffs: a later persist() never resubmits an earlier batch's
  changes over an intervening external correction (baseline advance)
All state lives in a temp dir; the real repo is never touched.
"""

import copy
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import enrich_tools as et

# Original analyze_image_batch captured before test setups mock it.
_ORIG_ANALYZE = et.analyze_image_batch

# _setup()이 바꾸는 et 전역은 테스트 후 복원한다 — 복원하지 않으면
# 뒤에 실행되는 테스트(예: test_build_preserve의 보존 목록 검사)가
# 오염된 경로를 보고 실패한다 (pytest 세션 내 테스트 순서 의존성).
_ET_GLOBALS = ["DATA_DIR", "TOOLS_JSON", "CACHE_JSON", "PROGRESS_JSON",
               "RETRY_MARKER", "MANIFEST_JSON", "collect_screenshots",
               "fingerprint", "analyze_image_batch"]


@pytest.fixture(autouse=True)
def _restore_et_globals():
    saved = {k: getattr(et, k) for k in _ET_GLOBALS}
    yield
    for k, v in saved.items():
        setattr(et, k, v)


def _setup(tmp):
    for n in ("a.png", "b.png", "c.png"):
        (tmp / n).write_bytes(b"fake-image-" + n.encode())
    et.DATA_DIR = tmp
    et.TOOLS_JSON = tmp / "ai-tools.json"
    et.CACHE_JSON = tmp / "ai-tools-cache.json"
    et.PROGRESS_JSON = tmp / "enrich-progress.json"
    et.RETRY_MARKER = tmp / ".retry-enrich"
    et.MANIFEST_JSON = tmp / "manifest.json"
    et.collect_screenshots = lambda: [
        ("tid-A", str(tmp / "a.png"), "https://x.com/a", "text A"),
        ("tid-B", str(tmp / "b.png"), "https://x.com/b", "text B changed"),
        ("tid-C", str(tmp / "c.png"), "https://x.com/c", "text C"),
    ]
    et.fingerprint = lambda image, text, url: {"a": "fp-A", "b": "fp-B"}.get(
        Path(image).name[0], "fp-X")
    (tmp / "posts-x.json").write_text(json.dumps({"posts": [
        {"tweetId": "tid-A"}, {"tweetId": "tid-B"}, {"tweetId": "tid-C"}]}),
        encoding="utf-8")
    (tmp / "posts-web.json").write_text(json.dumps({"posts": []}), encoding="utf-8")
    et.TOOLS_JSON.write_text(json.dumps({
        "tid-A": {"fingerprint": "fp-A", "name": "tool-A", "stale": True},
        "tid-B": {"fingerprint": "fp-B-OLD", "name": "tool-B-stale"},
        "tid-GONE": {"fingerprint": "fp-G", "name": "tool-gone"},
    }), encoding="utf-8")
    et.CACHE_JSON.write_text(json.dumps({}), encoding="utf-8")
    (tmp / "ai-tools-vision-cache.json").write_text(json.dumps({}), encoding="utf-8")
    et.analyze_image_batch = lambda *a, **k: {}
    et.verify_repo_with_readme = lambda *a, **k: None
    et.get_readme = lambda *a, **k: None
    et.log = lambda *a: None


def test_prune_and_merge():
    tmp = Path(tempfile.mkdtemp())
    _setup(tmp)
    et.run_backfill(dry_run=False, max_items=1, batch_size=1)
    saved = json.loads(et.TOOLS_JSON.read_text())
    assert sorted(saved) == ["tid-A", "tid-B"], saved
    assert "stale" not in saved["tid-A"], "matching fingerprint must clear stale"
    assert saved["tid-B"].get("stale") is True, "drifted entry must be kept stale"


def test_merge_save_preserves_external():
    tmp = Path(tempfile.mkdtemp())
    _setup(tmp)
    f = tmp / "merge.json"
    f.write_text(json.dumps({"a": 1, "b": 2, "c": 3}), encoding="utf-8")
    # External writer changes b and adds d before our merge.
    f.write_text(json.dumps({"a": 1, "b": 99, "c": 3, "d": 4}), encoding="utf-8")
    et._merge_save(f, {"b": 20}, ["c"])
    assert json.loads(f.read_text()) == {"a": 1, "b": 20, "d": 4}


def test_cumulative_diff_baseline_advance():
    """An external correction between persist() calls must survive the next one."""
    tmp = Path(tempfile.mkdtemp())
    _setup(tmp)
    real_merge = et._merge_save
    calls = {"tools": 0}

    def wrapped(path, updates, deletions=()):
        real_merge(path, updates, deletions)
        if path == et.TOOLS_JSON:
            calls["tools"] += 1
            if calls["tools"] == 1:
                # Simulate another writer correcting tid-B right after our
                # first persist (which wrote the prune's stale flag).
                data = json.loads(et.TOOLS_JSON.read_text())
                data["tid-B"]["name"] = "CORRECTED-BY-OTHER-WRITER"
                et.save_json_atomic(et.TOOLS_JSON, data)

    et._merge_save = wrapped
    try:
        # batch_size=1, 3 remaining candidates -> persist() runs 4 times.
        et.run_backfill(dry_run=False, max_items=3, batch_size=1)
    finally:
        et._merge_save = real_merge
    saved = json.loads(et.TOOLS_JSON.read_text())
    assert saved["tid-B"]["name"] == "CORRECTED-BY-OTHER-WRITER", saved["tid-B"]


def _setup_ordering(tmp):
    """6 candidates with distinct capturedAt for ordering tests."""
    import time as _time
    tids = ["tid-A", "tid-B", "tid-C", "tid-D", "tid-E", "tid-F"]
    stamps = {
        "tid-A": "2026-10-10T00:00:00+00:00",
        "tid-B": "2026-10-16T00:00:00+00:00",  # newest
        "tid-C": "2026-10-01T00:00:00+00:00",  # oldest
        "tid-D": "2026-10-15T00:00:00+00:00",
        "tid-E": "2026-10-14T00:00:00+00:00",
        "tid-F": "2026-10-13T00:00:00+00:00",
    }
    for t in tids:
        (tmp / f"{t}.png").write_bytes(b"fake-" + t.encode())
    et.DATA_DIR = tmp
    et.TOOLS_JSON = tmp / "ai-tools.json"
    et.CACHE_JSON = tmp / "ai-tools-cache.json"
    et.PROGRESS_JSON = tmp / "enrich-progress.json"
    et.RETRY_MARKER = tmp / ".retry-enrich"
    et.MANIFEST_JSON = tmp / "manifest.json"
    et.collect_screenshots = lambda: [
        (t, str(tmp / f"{t}.png"), f"https://x.com/{t}", f"text {t}") for t in tids
    ]
    et.fingerprint = lambda image, text, url: "fp-" + Path(image).stem
    (tmp / "posts-x.json").write_text(json.dumps({"posts": [
        {"tweetId": t, "capturedAt": stamps[t]} for t in tids]}), encoding="utf-8")
    (tmp / "posts-web.json").write_text(json.dumps({"posts": []}), encoding="utf-8")
    et.TOOLS_JSON.write_text(json.dumps({}), encoding="utf-8")
    et.CACHE_JSON.write_text(json.dumps({}), encoding="utf-8")
    (tmp / "ai-tools-vision-cache.json").write_text(json.dumps({}), encoding="utf-8")
    et.analyze_image_batch = lambda *a, **k: {}
    et.verify_repo_with_readme = lambda *a, **k: None
    et.get_readme = lambda *a, **k: None
    et.log = lambda *a: None


def test_ordering_newest_first_interleaved():
    """Newest-first with 1 oldest interleaved per 4 newest."""
    tmp = Path(tempfile.mkdtemp())
    _setup_ordering(tmp)
    seen_order = []
    real_enrich_one = et.enrich_one

    def recording(tweet_id, *a, **k):
        seen_order.append(tweet_id)
        return real_enrich_one(tweet_id, *a, **k)

    et.enrich_one = recording
    try:
        et.run_backfill(dry_run=False, batch_size=6, interleave_ratio=4,
                        time_budget=600)
    finally:
        et.enrich_one = real_enrich_one
    # newest desc: B, D, E, F, A, C -> interleave 4 newest, 1 oldest (C), rest (A)
    assert seen_order == ["tid-B", "tid-D", "tid-E", "tid-F", "tid-C", "tid-A"], seen_order


def test_deadline_stops_immediately():
    """A past deadline must stop work at every level, caching nothing."""
    tmp = Path(tempfile.mkdtemp())
    _setup(tmp)
    import time as _time
    past = _time.monotonic() - 1
    # Real analyze_image_batch (not the _setup mock): past deadline must
    # return {} WITHOUT calling any model.
    real_analyze = _ORIG_ANALYZE
    calls = []
    real_single = et._analyze_single_image_gemini
    et._analyze_single_image_gemini = lambda img, timeout=180: calls.append(timeout) or (_ for _ in ()).throw(
        AssertionError("model must not be called after deadline"))
    et.analyze_image_batch = real_analyze
    try:
        assert real_analyze([str(tmp / "a.png")], deadline=past) == {}
        assert calls == [], "no model call after deadline"
        # Timeout capping: a 30s remaining budget caps the per-call timeout.
        et._analyze_single_image_gemini = lambda img, timeout=180: (calls.append(timeout), {"is_github": False})[1]
        out = real_analyze([str(tmp / "a.png")], deadline=_time.monotonic() + 30)
        assert out, "capped call must still return"
        assert 20 < calls[-1] <= 31, f"timeout must be capped to remaining budget, got {calls[-1]}"
    finally:
        et._analyze_single_image_gemini = real_single
        et.analyze_image_batch = lambda *a, **k: {}
    entry, status = et.enrich_one("tid-A", str(tmp / "a.png"), "https://x.com/a",
                                  "text A", {}, {}, [0], deadline=past)
    assert (entry, status) == (None, "temporary_failure"), (entry, status)
    result = et.run_backfill(dry_run=False, batch_size=1, time_budget=0)
    assert result["verified"] == 0 and result["cached"] == 0, result
    assert json.loads(et.CACHE_JSON.read_text()) == {}, "deadline must not cache negatives"


if __name__ == "__main__":
    test_prune_and_merge()
    test_merge_save_preserves_external()
    test_cumulative_diff_baseline_advance()
    test_ordering_newest_first_interleaved()
    test_deadline_stops_immediately()
    print("test_enrich_merge: ALL PASS")
