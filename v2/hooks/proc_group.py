#!/usr/bin/env python3
"""Process-group-aware subprocess runner (P1-7).

Problem: subprocess.run(timeout=...) kills only the DIRECT child. The
grandchildren (hook -> translate -> gemini/codex, hook -> enrich -> codex)
survive as orphans, keep writing, and can race the next poll.

Fix: every child is started in its OWN process group (start_new_session).
On timeout the whole group gets SIGTERM (grace period, so a SIGTERM
handler upstream can forward the shutdown), then SIGKILL. The caller
sees subprocess.TimeoutExpired, as with subprocess.run.

The hook installs a SIGTERM handler (see post_ingest_hook.py) that kills
its current child group via terminate_current(), so a SIGTERM sent to
the hook's group cascades down one level.

CURRENT tracks the active child group for the signal handler. It is
cleared on every exit path (success, timeout, exception).
"""

import os
import signal
import subprocess
import time

CURRENT = {"pgid": None}


def _kill_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def terminate_current():
    """SIGTERM the currently-running child group (for signal handlers)."""
    pgid = CURRENT.get("pgid")
    if pgid:
        _kill_group(pgid, signal.SIGTERM)


def run_grouped(cmd, timeout=None, grace=15, **kw):
    """subprocess.run with process-group kill on timeout.

    The child is a group leader (start_new_session=True). On timeout:
    SIGTERM the group, wait up to `grace` seconds, SIGKILL the group,
    reap, then raise subprocess.TimeoutExpired.
    """
    if kw.pop("capture_output", False):
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    proc = subprocess.Popen(
        cmd, start_new_session=True, **kw,
    )
    CURRENT["pgid"] = proc.pid
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid, signal.SIGKILL)
                proc.wait()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        CURRENT["pgid"] = None
