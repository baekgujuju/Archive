#!/usr/bin/env python3
"""Tests for the P1 fixes (2026-10-17 cross-verification).

Covers: shared pipeline lock (fail-closed), translate corrupt recovery +
merge-touched saves, ingester canonical-path refusal + corrupt fail-closed,
build TOCTOU abort, enrich _merge_save corrupt/lock fail-closed,
proc_group whole-group kill on timeout, legacy writer disable.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import pipeline_lock as pl
from pipeline_lock import PipelineLockTimeout, pipeline_lock


def _check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    if not cond:
        raise AssertionError(name)


def test_lock_exclusive_and_timeout():
    # Two contenders: second one times out (fail-closed), first one keeps it.
    with pipeline_lock(timeout=5):
        try:
            with pipeline_lock(timeout=0.5):
                _check("lock timeout raises (fail-closed)", False)
        except PipelineLockTimeout:
            _check("lock timeout raises (fail-closed)", True)
    # After release, it is acquirable again.
    with pipeline_lock(timeout=5):
        _check("lock released after holder exits", True)
    # Exception inside the block releases the lock.
    try:
        with pipeline_lock(timeout=5):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with pipeline_lock(timeout=5):
        _check("lock released on exception", True)


def test_translate_corrupt_recovery_and_merge():
    import translate_posts as tp

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        tp.CACHE_FILE = td / "translations.json"
        tp.RECOVERY_FILE = td / ".translations-recovery.json"
        tp.QUARANTINE_DIR = td / ".quarantine"
        # Valid recovery copy present; main file corrupt.
        tp._atomic_write_json(tp.RECOVERY_FILE, {"good": {"status": "ok"}})
        tp.CACHE_FILE.write_text("{corrupt", encoding="utf-8")
        cache = tp.load_cache()
        _check("corrupt main -> recovery fallback", cache == {"good": {"status": "ok"}})
        _check("corrupt file quarantined outside data/",
               len(list((td / ".quarantine").glob("translations.json.corrupt-*"))) == 1)
        _check("corrupt main file moved away", not tp.CACHE_FILE.exists() or
               json.loads(tp.CACHE_FILE.read_text()) == {"good": {"status": "ok"}})
        _check("recovery restored as main file even with no work",
               json.loads(tp.CACHE_FILE.read_text(encoding="utf-8")) ==
               {"good": {"status": "ok"}})
        # save_cache merges touched keys only: a concurrent writer's entry
        # added after our load must survive our save.
        tp._atomic_write_json(tp.CACHE_FILE, {"other": {"status": "ok"}})
        cache = {"good": {"status": "ok"}, "mine": {"status": "ok"}}
        tp.save_cache(cache, touched={"mine"})
        saved = json.loads(tp.CACHE_FILE.read_text(encoding="utf-8"))
        _check("save merges touched only (concurrent entry preserved)",
               saved.get("other") == {"status": "ok"} and saved.get("mine") == {"status": "ok"})
        _check("recovery copy refreshed", json.loads(tp.RECOVERY_FILE.read_text()) == saved)
        # Empty pending set must NOT clobber a concurrent correction to an
        # already-saved key (the pending set is cleared after each save).
        tp._atomic_write_json(tp.CACHE_FILE, {"mine": {"status": "corrected"}})
        tp.save_cache(cache, touched=set())
        _check("empty pending does not overwrite concurrent correction",
               json.loads(tp.CACHE_FILE.read_text())["mine"] == {"status": "corrected"})
        # save_cache with a corrupt current file raises (fail-closed).
        tp.CACHE_FILE.write_text("{corrupt", encoding="utf-8")
        try:
            tp.save_cache(cache, touched={"mine"})
            _check("save refuses corrupt current file", False)
        except RuntimeError:
            _check("save refuses corrupt current file", True)


def test_ingester_guards():
    import subprocess as sp
    xb = Path.home() / "workspace" / "x-bookmarks"
    with tempfile.TemporaryDirectory() as td:
        # Canonical output path refused.
        r = sp.run([sys.executable, str(xb / "tools" / "ingest_post_html.py"),
                    td, "-o", str(xb)], capture_output=True, text=True)
        _check("ingester refuses canonical -o", r.returncode == 2)
        # Corrupt previous output -> exit 2, no overwrite.
        outdir = Path(td) / "out"
        outdir.mkdir()
        (outdir / "posts.json").write_text("{corrupt", encoding="utf-8")
        (Path(td) / "x.html").write_text("<html></html>", encoding="utf-8")
        r = sp.run([sys.executable, str(xb / "tools" / "ingest_post_html.py"),
                    td, "-o", str(outdir)], capture_output=True, text=True)
        _check("ingester fails closed on corrupt prev", r.returncode == 2)
        _check("corrupt prev not overwritten",
               (outdir / "posts.json").read_text(encoding="utf-8") == "{corrupt")


def test_build_toctou_abort():
    import build_data as bd

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Minimal sandbox: point the module's paths at the temp dir.
        bd.V2_ROOT = td
        bd.DATA_DIR = td / "data"
        bd.POSTS_JSON = td / "posts.json"
        bd.WEB_INDEX_JSON = td / "index.json"
        bd.IB_LIBRARY_JSON = td / "ib-library.json"
        bd.MEDIA_THUMB = td / "media" / "thumb"
        bd.MEDIA_FULL = td / "media" / "full"
        posts = [{"tweetId": "1", "fullText": "hello", "type": "x",
                  "sourceUrl": "https://x.com/a/status/1"}]
        bd.POSTS_JSON.write_text(json.dumps(posts), encoding="utf-8")
        # release.json: release_app_version() reads V2_ROOT/release.json.
        (td / "release.json").write_text(
            json.dumps({"app_version": "test"}), encoding="utf-8")
        # First build succeeds and records the snapshot hash.
        bd.build_data()
        first_manifest = json.loads((bd.DATA_DIR / "manifest.json").read_text())
        # Change the source AFTER load_posts but BEFORE the swap: simulate by
        # monkeypatching _current_source_hashes to report a change at swap time.
        real_current = bd._current_source_hashes
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            h = real_current()
            if calls["n"] >= 2:  # the pre-swap re-verification call
                h = dict(h)
                h["posts"] = "deadbeef"
            return h

        bd._current_source_hashes = flaky
        try:
            bd.build_data()
            _check("build aborts on mid-build source change", False)
        except SystemExit as e:
            _check("build aborts on mid-build source change", e.code == 2)
        finally:
            bd._current_source_hashes = real_current
        # Old generation still live, manifest unchanged.
        live = json.loads((bd.DATA_DIR / "manifest.json").read_text())
        _check("old generation stays live after abort",
               live["source_hash"] == first_manifest["source_hash"])


def test_enrich_merge_save_fail_closed():
    import enrich_tools as et

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        qdir = td / "hooks" / ".quarantine"
        real_v2 = et.V2_ROOT
        et.V2_ROOT = td  # quarantine lands in the sandbox, not the real tree
        try:
            target = td / "cache.json"
            # Corrupt file -> quarantine + raise, never overwrite with our keys.
            target.write_text("{corrupt", encoding="utf-8")
            try:
                et._merge_save(target, {"k": {"v": 1}})
                _check("enrich refuses corrupt file", False)
            except RuntimeError:
                _check("enrich refuses corrupt file", True)
            _check("corrupt file quarantined outside data/",
                   len(list(qdir.glob("cache.json.corrupt-*"))) == 1)
            _check("quarantine bytes preserved",
                   (list(qdir.glob("cache.json.corrupt-*"))[0].read_text()
                    == "{corrupt"))
            # Lock timeout -> raise, never write unlocked.
            import fcntl
            lock_path = pl.LOCK_PATH
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                target2 = td / "cache2.json"
                target2.write_text(json.dumps({"base": 1}), encoding="utf-8")
                try:
                    et._merge_save(target2, {"k": 2})
                    _check("enrich lock timeout raises", False)
                except PipelineLockTimeout:
                    _check("enrich lock timeout raises", True)
                _check("no unlocked write happened",
                       json.loads(target2.read_text()) == {"base": 1})
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            # Normal merge: concurrent update preserved.
            target3 = td / "cache3.json"
            target3.write_text(json.dumps({"a": 1}), encoding="utf-8")
            et._merge_save(target3, {"b": 2})
            _check("enrich merge preserves disk state",
                   json.loads(target3.read_text()) == {"a": 1, "b": 2})
        finally:
            et.V2_ROOT = real_v2


def test_proc_group_kills_grandchildren():
    from proc_group import run_grouped
    # Child spawns a grandchild that sleeps; timeout must kill both.
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "time.sleep(30)"
    )
    grandchild_pids = []
    try:
        run_grouped([sys.executable, "-c", code], timeout=2, grace=2,
                    capture_output=True, text=True)
        _check("proc_group timeout raises", False)
    except subprocess.TimeoutExpired:
        _check("proc_group timeout raises", True)
    # No stray `time.sleep(30)` python processes from this test remain.
    time.sleep(0.5)
    r = subprocess.run(["pgrep", "-f", "time.sleep(30)"],
                       capture_output=True, text=True)
    _check("grandchild reaped after group kill", r.stdout.strip() == "")


def test_legacy_writer_disabled():
    xb = Path.home() / "workspace" / "x-bookmarks"
    r = subprocess.run(
        [sys.executable, str(xb / "tools" / "monitor_drive_inbox.py")],
        capture_output=True, text=True)
    _check("legacy monitor writes disabled (exit 2)", r.returncode == 2)
    r = subprocess.run(
        [sys.executable, str(xb / "tools" / "monitor_drive_inbox.py"), "--dry-run"],
        capture_output=True, text=True)
    _check("legacy monitor --dry-run still allowed", r.returncode != 2)


def test_proc_group_kills_sigterm_ignoring_grandchild():
    from proc_group import run_grouped
    # Grandchild ignores SIGTERM; the unconditional group SIGKILL must reap it.
    code = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)']); "
        "time.sleep(30)"
    )
    try:
        run_grouped([sys.executable, "-c", code], timeout=2, grace=2,
                    capture_output=True, text=True)
        _check("proc_group timeout raises (SIGTERM-ignoring)", False)
    except subprocess.TimeoutExpired:
        _check("proc_group timeout raises (SIGTERM-ignoring)", True)
    time.sleep(0.5)
    r = subprocess.run(["pgrep", "-f", "time.sleep(30)"],
                       capture_output=True, text=True)
    _check("SIGTERM-ignoring grandchild reaped by group SIGKILL",
           r.stdout.strip() == "")


def test_quarantine_survives_build():
    import build_data as bd
    import enrich_tools as et

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        real_v2 = et.V2_ROOT
        et.V2_ROOT = td
        real_bd_v2 = bd.V2_ROOT
        real_bd_data = bd.DATA_DIR
        real_bd_posts = bd.POSTS_JSON
        real_bd_web = bd.WEB_INDEX_JSON
        real_bd_ib = bd.IB_LIBRARY_JSON
        real_bd_thumb = bd.MEDIA_THUMB
        real_bd_full = bd.MEDIA_FULL
        try:
            # Quarantine a corrupt file (goes to hooks/.quarantine).
            target = td / "data" / "cache.json"
            target.parent.mkdir(parents=True)
            target.write_text("{corrupt", encoding="utf-8")
            try:
                et._merge_save(target, {"k": 1})
            except RuntimeError:
                pass
            qfiles = list((td / "hooks" / ".quarantine").glob("cache.json.corrupt-*"))
            _check("quarantine created before build", len(qfiles) == 1)
            # Run a real build (replaces data/) and check the quarantine survives.
            bd.V2_ROOT = td
            bd.DATA_DIR = td / "data"
            bd.POSTS_JSON = td / "posts.json"
            bd.WEB_INDEX_JSON = td / "index.json"
            bd.IB_LIBRARY_JSON = td / "ib-library.json"
            bd.MEDIA_THUMB = td / "media" / "thumb"
            bd.MEDIA_FULL = td / "media" / "full"
            bd.POSTS_JSON.write_text(json.dumps(
                [{"tweetId": "1", "fullText": "hello", "type": "x",
                  "sourceUrl": "https://x.com/a/status/1"}]), encoding="utf-8")
            (td / "release.json").write_text(
                json.dumps({"app_version": "test"}), encoding="utf-8")
            bd.build_data()
            _check("quarantine survives data/ replacement",
                   len(list((td / "hooks" / ".quarantine").glob(
                       "cache.json.corrupt-*"))) == 1)
        finally:
            et.V2_ROOT = real_v2
            bd.V2_ROOT = real_bd_v2
            bd.DATA_DIR = real_bd_data
            bd.POSTS_JSON = real_bd_posts
            bd.WEB_INDEX_JSON = real_bd_web
            bd.IB_LIBRARY_JSON = real_bd_ib
            bd.MEDIA_THUMB = real_bd_thumb
            bd.MEDIA_FULL = real_bd_full


def test_launcher_matching():
    import launcher
    # A python process whose argv contains an exact pipeline script path
    # is matched (the script need not actually run — argv match only).
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)",
         str(launcher.HOOKS_DIR / "monitor.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        _check("launcher matches exact pipeline script argv",
               proc.pid in launcher._pipeline_pids())
    finally:
        proc.terminate()
        proc.wait()
    # A shell whose args merely CONTAIN the hooks path + a script name is
    # NOT matched (no substring matching).
    shell = subprocess.Popen(
        ["bash", "-c", f"echo {launcher.HOOKS_DIR}/monitor.py; sleep 5"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        _check("launcher does not match non-python processes",
               shell.pid not in launcher._pipeline_pids())
    finally:
        shell.terminate()
        shell.wait()


def test_ib_adapter_input_stability():
    import ib_adapter as ia
    real_lib = ia.IB_LIBRARY_JSON
    before = real_lib.read_bytes() if real_lib.exists() else None
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return "fp-first" if calls["n"] == 1 else "fp-changed"

    real_fp = ia.canonical_fingerprint
    ia.canonical_fingerprint = flaky
    try:
        ia.main()
        _check("adapter aborts when inputs change mid-adaptation", False)
    except SystemExit as e:
        _check("adapter aborts when inputs change mid-adaptation", e.code == 1)
    finally:
        ia.canonical_fingerprint = real_fp
    after = real_lib.read_bytes() if real_lib.exists() else None
    _check("adapter leaves library untouched on abort", before == after)


if __name__ == "__main__":
    test_lock_exclusive_and_timeout()
    test_translate_corrupt_recovery_and_merge()
    test_ingester_guards()
    test_build_toctou_abort()
    test_enrich_merge_save_fail_closed()
    test_proc_group_kills_grandchildren()
    test_proc_group_kills_sigterm_ignoring_grandchild()
    test_quarantine_survives_build()
    test_launcher_matching()
    test_ib_adapter_input_stability()
    test_legacy_writer_disabled()
    print("ALL P1 TESTS PASSED")
