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
    Release clears the owner BEFORE unlocking the flock, so a stale owner
    can never make a later acquire look "nested" while unlocked.
    """
    global _owner, _depth
    me = threading.get_ident()
    with _state:
        if _owner == me:
            _depth += 1
            nested = True
            reserved = False
        elif _owner is None:
            # Reserve ownership before flocking, so a nested call from
            # this thread (after the outer yield) sees us as the owner.
            _owner = me
            _depth = 1
            nested = False
            reserved = True
        else:
            nested = False
            reserved = False
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
                    if reserved:
                        with _state:
                            if _owner == me:
                                _owner = None
                                _depth = 0
                    raise PipelineLockTimeout(
                        f"공유 파이프라인 락 획득 실패 ({timeout}초) — "
                        "락 없이 쓰지 않고 중단 (fail-closed)"
                    )
                time.sleep(0.05)
        with _state:
            # (Re)assert ownership: another thread may have overwritten
            # the reservation while we waited on the flock.
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
                os.close(fd)
                closed = True
    finally:
        # Only close if the flock loop raised before the inner finally
        # ran (timeout path). Never close twice: the fd number may have
        # been reused by another thread by now.
        if not closed:
            os.close(fd)
