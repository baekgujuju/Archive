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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import enrich_tools as et


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
    et.TOOLS_JSON.write_text(json.dumps({
        "tid-A": {"fingerprint": "fp-A", "name": "tool-A", "stale": True},
        "tid-B": {"fingerprint": "fp-B-OLD", "name": "tool-B-stale"},
        "tid-GONE": {"fingerprint": "fp-G", "name": "tool-gone"},
    }), encoding="utf-8")
    et.CACHE_JSON.write_text(json.dumps({}), encoding="utf-8")
    (tmp / "ai-tools-vision-cache.json").write_text(json.dumps({}), encoding="utf-8")
    et.analyze_image_batch = lambda images: {}
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


if __name__ == "__main__":
    test_prune_and_merge()
    test_merge_save_preserves_external()
    test_cumulative_diff_baseline_advance()
    print("test_enrich_merge: ALL PASS")
