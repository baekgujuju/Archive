#!/usr/bin/env python3
"""Shared pipeline-write lock (P1-4 / P1-6 / P1-7 backstop).

Every process that writes pipeline state MUST hold this lock for the whole
read-modify-write transaction:

  - build_data.py: the whole build (preservation -> swap -> backup cleanup)
  - enrich_tools.py: each _merge_save
  - translate_posts.py: each cache save
  - drive_poll.py: the posts.json merge
  - post_ingest_hook.py: the translation backup/restore steps
  - publish_settings.py: the shared-state write

The lock file lives in v2/hooks/ (OUTSIDE data/), so build_data.py's
data/ replacement can never wipe it.

Fail-closed: on timeout, PipelineLockTimeout is raised and the caller MUST
NOT write. Writing unlocked (the old enrich behaviour: lock fail-open after
~10s) silently loses another writer's updates. A timeout means: defer the
save and let the next poll retry via the retry marker.

flock is released automatically if the holder dies, so a crashed holder can
never wedge the pipeline permanently.
"""

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / "hooks" / ".pipeline-write.lock"


class PipelineLockTimeout(TimeoutError):
    """Raised when the shared pipeline-write lock cannot be acquired in time.

    The caller must NOT write. Defer and retry later.
    """


@contextmanager
def pipeline_lock(timeout: float = 30.0):
    """Acquire the shared pipeline-write lock (exclusive).

    Blocks up to ``timeout`` seconds, then raises PipelineLockTimeout.
    The lock is released on exit, including on exception.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PipelineLockTimeout(
                        f"pipeline-write lock not acquired within {timeout}s "
                        f"({LOCK_PATH}); refusing to write unlocked"
                    )
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
