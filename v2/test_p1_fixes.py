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
import unittest
import unittest.mock
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
    import threading
    # Same-thread nesting is reentrant (no deadlock, no timeout).
    with pipeline_lock(timeout=5):
        with pipeline_lock(timeout=5):
            _check("same-thread nesting is reentrant", True)
    # A DIFFERENT thread contending times out (fail-closed).
    with pipeline_lock(timeout=5):
        result = []

        def contend():
            try:
                with pipeline_lock(timeout=0.5):
                    result.append("acquired")
            except PipelineLockTimeout:
                result.append("timeout")

        t = threading.Thread(target=contend)
        t.start()
        t.join()
        _check("other-thread timeout raises (fail-closed)",
               result == ["timeout"])
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
            # 격리는 독점 예약 디렉터리(mkdtemp) 안에 원본 이름 그대로 들어간다.
            qfile = list(qdir.glob("cache.json.corrupt-*/cache.json"))
            _check("quarantine holds the original bytes",
                   len(qfile) == 1 and qfile[0].read_text() == "{corrupt")
            # 두 번째 격리도 첫 번째 증거를 덮지 않는다 (독점 예약).
            target.write_text("{corrupt2", encoding="utf-8")
            try:
                et._merge_save(target, {"k": 2})
            except RuntimeError:
                pass
            _check("second quarantine does not overwrite the first",
                   len(list(qdir.glob("cache.json.corrupt-*/cache.json"))) == 2)
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
    from unittest.mock import patch

    class FakePart:
        def __init__(self, entry, kind):
            self.entry, self.kind = entry, kind

        def read_bytes(self):
            return self.entry.cmdline

    class FakeEntry:
        def __init__(self, cmdline, exe="/usr/bin/python3", cwd="/home/hatch"):
            self.name = "123456"
            self.cmdline, self.exe, self.cwd = cmdline, exe, cwd

        def __truediv__(self, kind):
            return FakePart(self, kind)

    def pids_for(entry):
        def fake_readlink(path):
            if isinstance(path, FakePart):
                return path.entry.exe if path.kind == "exe" else path.entry.cwd
            raise OSError("unexpected")

        with patch.object(Path, "iterdir", return_value=[entry]), \
                patch.object(os, "readlink", side_effect=fake_readlink):
            return launcher._pipeline_pids()

    hooks = str(launcher.HOOKS_DIR)
    script = (hooks + "/monitor.py").encode()
    # Exact absolute script operand -> matched.
    _check("launcher matches exact script operand",
           pids_for(FakeEntry(b"python3\x00" + script + b"\x00")) == [123456])
    # -c payload with the script path as a data argument -> NOT matched.
    _check("launcher rejects -c payload arguments",
           pids_for(FakeEntry(b"python3\x00-c\x00print(1)\x00" + script + b"\x00")) == [])
    # Relative script resolved against /proc/PID/cwd -> matched.
    _check("launcher resolves relative script via cwd",
           pids_for(FakeEntry(b"python3\x00monitor.py\x00", cwd=hooks)) == [123456])
    _check("launcher rejects relative script from wrong cwd",
           pids_for(FakeEntry(b"python3\x00monitor.py\x00", cwd="/tmp")) == [])
    # Non-python exe -> NOT matched even with a script operand.
    _check("launcher rejects non-python exe",
           pids_for(FakeEntry(b"vim\x00" + script + b"\x00",
                              exe="/usr/bin/vim")) == [])
    # Attached -m: `python -mtimeit /…/monitor.py` runs the timeit MODULE,
    # not monitor.py -> NOT matched.
    _check("launcher rejects attached -m module",
           pids_for(FakeEntry(b"python3\x00-mtimeit\x00" + script + b"\x00")) == [])
    # Bundled short options: `-uW ignore monitor.py` -> script is monitor.py.
    _check("launcher parses bundled -uW with value",
           pids_for(FakeEntry(b"python3\x00-uW\x00ignore\x00monitor.py\x00",
                              cwd=hooks)) == [123456])
    # Unparseable option: do not guess -> NOT matched.
    _check("launcher rejects unparseable options",
           pids_for(FakeEntry(b"python3\x00-Z\x00monitor.py\x00",
                              cwd=hooks)) == [])
    # Long option with a value: `--check-hash-based-pycs always monitor.py`
    # -> the value is consumed, monitor.py is the script.
    _check("launcher consumes long-option value",
           pids_for(FakeEntry(b"python3\x00--check-hash-based-pycs\x00always\x00monitor.py\x00",
                              cwd=hooks)) == [123456])
    # Attached --opt=val is REJECTED by this environment's interpreter
    # (verified by probe) -> do not guess.
    _check("launcher rejects attached long-option value",
           pids_for(FakeEntry(b"python3\x00--check-hash-based-pycs=always\x00monitor.py\x00",
                              cwd=hooks)) == [])
    _check("launcher rejects --quiet=x",
           pids_for(FakeEntry(b"python3\x00--quiet=x\x00monitor.py\x00",
                              cwd=hooks)) == [])
    _check("launcher rejects --isolated (unsupported here)",
           pids_for(FakeEntry(b"python3\x00--isolated\x00monitor.py\x00",
                              cwd=hooks)) == [])
    # -I / -S are valid flags (no value).
    _check("launcher parses -I flag",
           pids_for(FakeEntry(b"python3\x00-I\x00monitor.py\x00",
                              cwd=hooks)) == [123456])
    _check("launcher parses -S flag",
           pids_for(FakeEntry(b"python3\x00-S\x00monitor.py\x00",
                              cwd=hooks)) == [123456])
    # --help / -h / -V terminate before running any script.
    _check("launcher rejects --help",
           pids_for(FakeEntry(b"python3\x00--help\x00monitor.py\x00",
                              cwd=hooks)) == [])
    _check("launcher rejects --help-env/--help-xoptions/--help-all",
           pids_for(FakeEntry(b"python3\x00--help-env\x00monitor.py\x00",
                              cwd=hooks)) == [] and
           pids_for(FakeEntry(b"python3\x00--help-xoptions\x00monitor.py\x00",
                              cwd=hooks)) == [] and
           pids_for(FakeEntry(b"python3\x00--help-all\x00monitor.py\x00",
                              cwd=hooks)) == [])
    # Empty argv element is preserved: `python -W '' monitor.py`.
    _check("launcher preserves empty argv element",
           pids_for(FakeEntry(b"python3\x00-W\x00\x00monitor.py\x00",
                              cwd=hooks)) == [123456])
    _check("launcher rejects -h",
           pids_for(FakeEntry(b"python3\x00-h\x00monitor.py\x00",
                              cwd=hooks)) == [])
    _check("launcher rejects -V",
           pids_for(FakeEntry(b"python3\x00-V\x00monitor.py\x00",
                              cwd=hooks)) == [])
    # Unknown long option -> do not guess.
    _check("launcher rejects unknown long option",
           pids_for(FakeEntry(b"python3\x00--bogus\x00monitor.py\x00",
                              cwd=hooks)) == [])
    # Every long flag option is rejected by this environment's interpreter
    # (full probe 2026-10-17) -> _UNPARSEABLE, never a script match.
    _rejected_long_flags = [
        b"--inspect", b"--interactive", b"--optimize",
        b"--dont-write-bytecode", b"--no-user-site", b"--no-site",
        b"--ignore-environment", b"--bytes-warning", b"--quiet",
        b"--verbose", b"--unbuffered", b"--warn-default-encoding",
        b"--safe-path", b"--isolated",
    ]
    _check("launcher rejects all unsupported long flag options",
           all(pids_for(FakeEntry(b"python3\x00" + opt + b"\x00monitor.py\x00",
                                  cwd=hooks)) == []
               for opt in _rejected_long_flags))


def test_lock_reentrant():
    # Nested acquisition in the same process must not deadlock.
    import time as _time
    start = _time.monotonic()
    with pipeline_lock(timeout=5):
        with pipeline_lock(timeout=5):
            _check("reentrant lock nests without deadlock", True)
    _check("reentrant lock releases promptly", _time.monotonic() - start < 5)


def test_build_rejects_unstamped_ib():
    import build_data as bd

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        real = {k: getattr(bd, k) for k in
                ("V2_ROOT", "DATA_DIR", "POSTS_JSON", "WEB_INDEX_JSON",
                 "IB_LIBRARY_JSON", "MEDIA_THUMB", "MEDIA_FULL")}
        try:
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
            # Unstamped library (no canonical_fingerprint): fail closed.
            bd.IB_LIBRARY_JSON.write_text(
                json.dumps({"posts": []}), encoding="utf-8")
            try:
                bd.build_data()
                _check("build rejects unstamped IB library", False)
            except SystemExit as e:
                _check("build rejects unstamped IB library", e.code == 2)
            _check("no data/ swap on unstamped IB",
                   not (td / "data" / "manifest.json").exists())
            # Stamped library: build succeeds.
            bd.IB_LIBRARY_JSON.write_text(
                json.dumps({"posts": [], "canonical_fingerprint": "fp-abc"}),
                encoding="utf-8")
            bd.build_data()
            manifest = json.loads((td / "data" / "manifest.json").read_text())
            _check("stamped IB library builds",
                   manifest["source_hash"] is not None)
        finally:
            for k, v in real.items():
                setattr(bd, k, v)


def test_quarantine_setup_failure_is_not_source_gone():
    from pipeline_lock import quarantine_file
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src.json"
        src.write_text("{corrupt", encoding="utf-8")
        # 목적지 생성(mkdtemp)이 FileNotFoundError를 내도, 소스 소실로
        # 오인되지 않고 OSError로 정규화돼야 한다.
        with unittest.mock.patch("pipeline_lock.tempfile.mkdtemp",
                                 side_effect=FileNotFoundError("setup gone")):
            try:
                quarantine_file(src, td / ".quarantine", "src.json")
                _check("quarantine setup failure is OSError", False)
            except FileNotFoundError:
                _check("quarantine setup failure is OSError", False)
            except OSError:
                _check("quarantine setup failure is OSError", True)
        _check("quarantine setup failure leaves source intact",
               src.read_text() == "{corrupt")


def test_translate_source_gone_then_reappears_aborts():
    import translate_posts as tp
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        main = td / "translations.json"
        recovery = td / "translations.json.recovery"
        main.write_text("{corrupt", encoding="utf-8")
        recovery.write_text(json.dumps({"a": "A"}), encoding="utf-8")
        qdir = td / ".quarantine"

        def fake_quarantine(path, qd, label):
            # 소스가 사라졌다가 손상 상태로 다시 나타나는 TOCTOU.
            main.write_text("{corrupt-again", encoding="utf-8")
            raise FileNotFoundError("source gone")

        with unittest.mock.patch.object(tp, "CACHE_FILE", main), \
             unittest.mock.patch.object(tp, "RECOVERY_FILE", recovery), \
             unittest.mock.patch.object(tp, "QUARANTINE_DIR", qdir), \
             unittest.mock.patch.object(tp, "quarantine_file",
                                         side_effect=fake_quarantine):
            try:
                tp.load_cache()
                _check("reappeared corrupt file aborts before restore", False)
            except RuntimeError:
                _check("reappeared corrupt file aborts before restore", True)
        _check("reappeared corrupt file is not overwritten",
               main.read_text() == "{corrupt-again")


def test_translate_quarantine_source_gone_uses_recovery():
    import translate_posts as tp
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        main = td / "translations.json"
        recovery = td / "translations.json.recovery"
        main.write_text("{corrupt", encoding="utf-8")
        recovery.write_text(json.dumps({"a": "A"}), encoding="utf-8")
        qdir = td / ".quarantine"
        # 소스가 격리 도중 사라진 경우(FileNotFoundError) -> 복구본 경로로
        # 계속해야지 {}를 즉시 반환하면 안 된다.
        # (mock은 소스가 실제로 사라지는 것을 시뮬레이션한다.)
        def fake_quarantine(path, qd, label):
            main.unlink()
            raise FileNotFoundError("source gone")

        with unittest.mock.patch.object(tp, "CACHE_FILE", main), \
             unittest.mock.patch.object(tp, "RECOVERY_FILE", recovery), \
             unittest.mock.patch.object(tp, "QUARANTINE_DIR", qdir), \
             unittest.mock.patch.object(tp, "quarantine_file",
                                         side_effect=fake_quarantine):
            cache = tp.load_cache()
        _check("quarantine source-gone continues to recovery",
               cache == {"a": "A"})


def test_quarantine_file_distinguishes_source_vs_destination():
    from pipeline_lock import quarantine_file
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        qdir = td / ".quarantine"
        # (a) 소스가 진짜 사라짐 -> FileNotFoundError, 빈 디렉터리 정리.
        try:
            quarantine_file(td / "missing.json", qdir, "missing.json")
            _check("quarantine missing source raises FileNotFoundError", False)
        except FileNotFoundError:
            _check("quarantine missing source raises FileNotFoundError", True)
        _check("quarantine failure removes reserved empty dir",
               len(list(qdir.iterdir())) == 0)
        # (b) 소스가 남아있는데 이동 실패 -> OSError
        # (FileNotFoundError가 아님), 빈 디렉터리 정리, 소스 보존.
        src = td / "src.json"
        src.write_text("{corrupt", encoding="utf-8")
        with unittest.mock.patch("pipeline_lock.os.replace",
                                 side_effect=FileNotFoundError("dest gone")):
            try:
                quarantine_file(src, qdir, "src.json")
                _check("quarantine dest failure raises OSError", False)
            except FileNotFoundError:
                _check("quarantine dest failure raises OSError", False)
            except OSError:
                _check("quarantine dest failure raises OSError", True)
        _check("quarantine dest failure removes reserved empty dir",
               len(list(qdir.iterdir())) == 0)
        _check("quarantine dest failure leaves source intact",
               src.read_text() == "{corrupt")


def test_translate_quarantine_failure_aborts():
    import translate_posts as tp
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        tp.CACHE_FILE = td / "translations.json"
        tp.RECOVERY_FILE = td / ".translations-recovery.json"
        tp.QUARANTINE_DIR = td / ".quarantine"
        tp._atomic_write_json(tp.RECOVERY_FILE, {"good": {"status": "ok"}})
        tp.CACHE_FILE.write_text("{corrupt", encoding="utf-8")

        def failing_replace(src, dst):
            raise OSError("simulated quarantine failure")

        with patch.object(os, "replace", side_effect=failing_replace):
            try:
                tp.load_cache()
                _check("quarantine failure aborts restore", False)
            except RuntimeError:
                _check("quarantine failure aborts restore", True)
        _check("corrupt file NOT overwritten on quarantine failure",
               tp.CACHE_FILE.read_text(encoding="utf-8") == "{corrupt")


def test_proc_group_kills_grandchild_after_leader_exit():
    from proc_group import run_grouped
    import time as _time
    # 리더는 즉시 종료되지만, 같은 그룹의 손자가 stdout을 보유하고 잔다.
    # 리더 종료 여부와 무관하게 그룹 전체가 정리돼야 한다.
    with tempfile.TemporaryDirectory() as td:
        pidfile = str(Path(td) / "grandchild.pid")
        grandchild = (
            "import os, time; "
            f"open({pidfile!r}, 'w').write(str(os.getpid())); time.sleep(60)"
        )
        child = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])"
        )
        start = _time.monotonic()
        try:
            run_grouped([sys.executable, "-c", child], timeout=2, grace=2,
                        capture_output=True, text=True)
            _check("proc_group timeout raises (leader exited)", False)
        except subprocess.TimeoutExpired:
            _check("proc_group timeout raises (leader exited)", True)
        _check("proc_group cleanup is bounded after leader exit",
               _time.monotonic() - start < 30)
        try:
            gpid = int(Path(pidfile).read_text())
            try:
                os.kill(gpid, 0)
                _check("grandchild reaped after leader exit", False)
            except ProcessLookupError:
                _check("grandchild reaped after leader exit", True)
        except (OSError, ValueError):
            _check("grandchild pid recorded", False)


def test_proc_group_bounded_drain():
    from proc_group import run_grouped
    import time as _time
    # Escaped descendant (new session, outside the group) holds stdout open.
    # The drain must time out (bounded) instead of wedging forever.
    with tempfile.TemporaryDirectory() as td:
        pidfile = str(Path(td) / "grandchild.pid")
        grandchild = (
            "import os, time; "
            f"open({pidfile!r}, 'w').write(str(os.getpid())); time.sleep(60)"
        )
        child = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}], "
            "start_new_session=True); time.sleep(60)"
        )
        start = _time.monotonic()
        try:
            run_grouped([sys.executable, "-c", child], timeout=1, grace=1,
                        capture_output=True, text=True)
            _check("proc_group timeout raises (escaped descendant)", False)
        except subprocess.TimeoutExpired:
            _check("proc_group timeout raises (escaped descendant)", True)
        elapsed = _time.monotonic() - start
        _check("proc_group drain is bounded (not wedged)",
               elapsed < 30)
        # Clean up the escaped grandchild.
        try:
            gpid = int(Path(pidfile).read_text())
            os.kill(gpid, 9)
        except (OSError, ValueError):
            pass


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
    test_lock_reentrant()
    test_translate_corrupt_recovery_and_merge()
    test_translate_quarantine_failure_aborts()
    test_translate_quarantine_source_gone_uses_recovery()
    test_quarantine_setup_failure_is_not_source_gone()
    test_translate_source_gone_then_reappears_aborts()
    test_quarantine_file_distinguishes_source_vs_destination()
    test_ingester_guards()
    test_build_toctou_abort()
    test_build_rejects_unstamped_ib()
    test_enrich_merge_save_fail_closed()
    test_proc_group_kills_grandchildren()
    test_proc_group_kills_sigterm_ignoring_grandchild()
    test_proc_group_kills_grandchild_after_leader_exit()
    test_proc_group_bounded_drain()
    test_quarantine_survives_build()
    test_launcher_matching()
    test_ib_adapter_input_stability()
    test_legacy_writer_disabled()
    print("ALL P1 TESTS PASSED")
