#!/usr/bin/env python3
"""Shared pipeline-write lock (P1-2/4/6).

One flock file serializes every pipeline write: build (whole build),
enrich saves, translate saves, drive_poll merges, hook translation
backup/restore, publish_settings.

The lock file lives in v2/hooks/ (NOT data/) so a build's data/
replacement can never delete it.

Fail-closed: if the lock cannot be acquired within `timeout`, raise
PipelineLockTimeout. No writer may proceed without the lock. If the
holder dies, flock releases automatically.

Reentrant within one process: the same process may nest pipeline_lock()
(e.g. enrich persist() -> _merge_save() -> pipeline_lock()). The nested
acquire is a no-op (a counter), so nesting can never deadlock. Across
processes, flock still serializes. Thread-safe via a threading lock +
owner-thread tracking.
"""

import fcntl
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / "hooks" / ".pipeline-write.lock"
DEFAULT_TIMEOUT = 30.0

_state = threading.RLock()
_owner = None
_depth = 0


class PipelineLockTimeout(TimeoutError):
    """Raised when the shared pipeline-write lock cannot be acquired."""


def quarantine_file(path, quarantine_dir, label):
    """Move a corrupt file into an exclusively-reserved quarantine dir.

    The destination directory is created by tempfile.mkdtemp(), which
    retries on collision and never returns an existing directory — so
    the move can never overwrite earlier quarantined evidence (os.replace
    onto a fixed name would). Returns the quarantined path.

    Failure semantics (so callers can distinguish them):
    - FileNotFoundError: the SOURCE genuinely disappeared (another writer
      restored/removed it) — the reserved dir is removed.
    - Other OSError: the destination or move failed while the source
      remains — the reserved dir is removed; the caller must NOT
      overwrite the original (fail-closed).
    """
    path = Path(path)
    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = Path(tempfile.mkdtemp(
        prefix=f"{path.name}.corrupt-", dir=str(quarantine_dir)))
    dest = dest_dir / path.name
    try:
        os.replace(path, dest)
    except FileNotFoundError:
        # Source absence vs destination failure: os.replace raises the
        # same error for both. Distinguish by re-checking the source.
        try:
            dest_dir.rmdir()
        except OSError:
            pass
        if path.exists():
            raise OSError(
                f"격리 이동 실패 — 소스({path})가 남아 있음, 덮어쓰지 않고 중단"
            )
        raise
    except OSError:
        try:
            dest_dir.rmdir()
        except OSError:
            pass
        raise
    return dest


@contextmanager
def pipeline_lock(timeout=DEFAULT_TIMEOUT):
    """Acquire the shared pipeline-write lock (fail-closed on timeout).

    Reentrant within one process: the same thread may nest pipeline_lock()
    (e.g. enrich persist() -> _merge_save() -> pipeline_lock()); the nested
    acquire is a no-op (a counter), so nesting can never deadlock.
    Across threads/processes, flock serializes.

    _state only guards the owner/depth bookkeeping (short sections); it is
    never held across the flock wait or the yield, so a contending thread
    can always reach its flock timeout instead of wedging on the mutex.
    The owner is set ONLY after flock succeeds — never reserved before
    (a reservation that leaks on open/flock/exception would let a later
    call enter unlocked). Release clears the owner BEFORE unlocking the
    flock, so a stale owner can never fake a nested acquire. The fd is
    consumed (closed=True) BEFORE os.close() runs, so a close exception
    can never cause a double close of a possibly-reused fd number.
    """
    global _owner, _depth
    me = threading.get_ident()
    with _state:
        if _owner == me:
            _depth += 1
            nested = True
        else:
            nested = False
    if nested:
        try:
            yield
        finally:
            with _state:
                _depth -= 1
        return
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    closed = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PipelineLockTimeout(
                        f"공유 파이프라인 락 획득 실패 ({timeout}초) — "
                        "락 없이 쓰지 않고 중단 (fail-closed)"
                    )
                time.sleep(0.05)
        with _state:
            # Owner is set only after flock succeeds.
            _owner = me
            _depth = 1
        try:
            yield
        finally:
            with _state:
                # Clear the owner BEFORE unlocking, so a stale owner
                # can never fake a nested acquire.
                _owner = None
                _depth = 0
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                # Consume ownership BEFORE close: if close() raises, the
                # outer finally must not retry it (the fd number may
                # already be reused by another thread).
                closed = True
                os.close(fd)
    finally:
        # Only close if the flock loop raised before the inner finally
        # ran (timeout path). Never close twice.
        if not closed:
            os.close(fd)
