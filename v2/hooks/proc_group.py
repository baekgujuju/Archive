#!/usr/bin/env python3
"""Process-group-aware subprocess runner (P1-7).

Problem: subprocess.run(timeout=...) kills only the DIRECT child. The
grandchildren (hook -> translate -> gemini/codex, hook -> enrich -> codex)
survive as orphans, keep writing, and can race the next poll.

Fix: every child is started in its OWN process group (start_new_session).
On timeout the whole group gets SIGTERM (grace period, so a SIGTERM
handler upstream can forward the shutdown), then SIGKILL — UNCONDITIONALLY,
even if the group leader already exited, because a SIGTERM-ignoring
grandchild may still be alive. Pipes are drained via communicate() so a
full pipe can never wedge the reaper.

Termination forwarding: each stage (worker -> scheduler -> monitor ->
hook) installs a SIGTERM handler that calls terminate_current(), which
kills its current child group with the same SIGTERM -> grace -> SIGKILL
escalation. So a SIGTERM to any stage cascades down the whole chain.

CURRENT tracks the active child group for the signal handlers. It is
cleared on every exit path (success, timeout, exception).
"""

import os
import signal
import subprocess
import time

CURRENT = {"pgid": None}


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _escalate(pgid, grace):
    """SIGTERM the group, wait up to `grace` for it to die, then SIGKILL.

    The SIGKILL is unconditional: a leader that exits on SIGTERM may
    leave a SIGTERM-ignoring grandchild alive in the same group.
    """
    _kill_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return
        time.sleep(0.1)
    _kill_group(pgid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return
        time.sleep(0.1)


def terminate_current(grace=10):
    """Kill the currently-running child group (for SIGTERM handlers).

    Full escalation: SIGTERM -> grace -> SIGKILL. The caller then exits.
    """
    pgid = CURRENT.get("pgid")
    if pgid:
        _escalate(pgid, grace)


def run_grouped(cmd, timeout=None, grace=15, **kw):
    """subprocess.run with process-group kill on timeout.

    The child is a group leader (start_new_session=True). On timeout:
    SIGTERM the group, wait up to `grace`, SIGKILL the group
    (unconditionally — see _escalate), then drain the pipes via
    communicate() and reap, then raise subprocess.TimeoutExpired.
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
            _escalate(proc.pid, grace)
            # Drain the pipes (a full pipe must not wedge the reaper) and
            # reap the leader. communicate() returns once the process exits.
            out, err = proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        CURRENT["pgid"] = None
