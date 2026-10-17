#!/usr/bin/env python3
"""Process-group-aware subprocess runner (P1-7).

Problem: subprocess.run(timeout=...) kills only the DIRECT child. The
grandchildren (hook -> translate -> gemini/codex, hook -> enrich -> codex)
survive as orphans, keep writing, and can race the next poll.

Fix: every child is started in its OWN process group (start_new_session).
On timeout the whole group gets SIGTERM (grace period, so a SIGTERM
handler upstream can forward the shutdown), then SIGKILL — UNCONDITIONALLY,
even if the group leader already exited, because a SIGTERM-ignoring
grandchild may still be alive. Pipes are drained with a BOUND; an escaped
descendant holding stdout/stderr can never wedge the reaper forever.

Termination forwarding: each stage (worker -> scheduler -> monitor ->
hook) installs a SIGTERM handler that ignores further SIGTERM (no
re-entrancy), calls terminate_current() with its stage budget, then
exits. Budgets are staggered outward from ONE total-cleanup deadline:
a stage's worst-case cleanup is grace + 5s SIGKILL wait + 10s drain,
so each parent's grace exceeds the child's total:
hook 15 (total 30) < monitor 45 (total 60) < scheduler 75 (total 90) <
worker 105 (total 120). An outer stage never SIGKILLs an inner stage
before that stage finishes cleaning its own session.

CURRENT tracks the active child group for the signal handlers. It is
cleared on every exit path (success, timeout, exception).
"""

import os
import signal
import subprocess
import time

CURRENT = {"pgid": None}
_TERMINATING_PGID = None
# pgids whose group was already terminated by terminate_current(). _cleanup()
# skips re-escalation for these (the handler and the exception path must
# not duplicate escalation — the parent's budget must cover ONE cleanup,
# not two). Cleared when a new child group starts.
_TERMINATED_PGIDS = set()


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

    Idempotent: a second call for the same pgid returns immediately, so
    repeated SIGTERM handlers cannot restart escalation. Full escalation:
    SIGTERM -> grace -> SIGKILL. The caller then exits.
    """
    global _TERMINATING_PGID
    pgid = CURRENT.get("pgid")
    if not pgid or _TERMINATING_PGID == pgid:
        return
    _TERMINATING_PGID = pgid
    try:
        _escalate(pgid, grace)
        _TERMINATED_PGIDS.add(pgid)
    finally:
        if _TERMINATING_PGID == pgid:
            _TERMINATING_PGID = None


def _cleanup(proc, grace, drain_timeout=10):
    """Terminate the child group and reap the leader (bounded).

    Used by both the timeout path and the BaseException path. The group is
    ALWAYS escalated, even if the leader already exited: a grandchild may
    still hold stdout/stderr, and skipping escalation would abandon it
    (and wedge the drain). If terminate_current() already escalated this
    group (SIGTERM handler path), escalation is skipped — one cleanup,
    not two. After the group is killed, drains the pipes with a bound: an
    escaped descendant holding stdout/stderr cannot wedge the reaper. If
    the drain times out, closes our pipe ends and reaps the leader.
    """
    if proc.pid not in _TERMINATED_PGIDS:
        _escalate(proc.pid, grace)
    try:
        out, err = proc.communicate(timeout=drain_timeout)
    except subprocess.TimeoutExpired:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        proc.wait()
        out, err = b"", b""
    return out, err


def run_grouped(cmd, timeout=None, grace=15, **kw):
    """subprocess.run with process-group kill on timeout / exception.

    The child is a group leader (start_new_session=True). On timeout or
    BaseException (KeyboardInterrupt / SystemExit from a SIGTERM handler):
    kill the whole group, drain the pipes with a bound, reap the leader,
    then re-raise (TimeoutExpired for timeouts).
    """
    if kw.pop("capture_output", False):
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    proc = subprocess.Popen(
        cmd, start_new_session=True, **kw,
    )
    CURRENT["pgid"] = proc.pid
    # A new group: its pgid must not inherit a stale "already terminated"
    # mark (pgids can be reused by the OS).
    _TERMINATED_PGIDS.discard(proc.pid)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            out, err = _cleanup(proc, grace)
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
        except BaseException:
            _cleanup(proc, grace)
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        CURRENT["pgid"] = None
