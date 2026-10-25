"""Durable retry queues and per-invocation evidence. No worker side effects."""
import hashlib
import fcntl
import os
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from state_guard import atomic_json_write

_ATTEMPT = ContextVar('retry_attempt', default=None)


def begin_attempt(run_id):
    _ATTEMPT.set(run_id)


def attempt_id():
    return _ATTEMPT.get() or os.environ.get('V2_RETRY_ATTEMPT_ID') or uuid.uuid4().hex


MARKERS = ('.retry_translation', '.retry_media', '.retry-enrich',
           '.retry_commit', '.retry_media_required')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(path):
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw)
        if (isinstance(value, dict) and all(k in value for k in (
                'first_seen_at', 'last_attempt_at', 'attempts', 'reason_code', 'last_error', 'next_retry_at'))
                and type(value['attempts']) is int and value['attempts'] >= 0):
            return value
    except ValueError:
        pass
    now = datetime.now(timezone.utc).isoformat()
    return dict(first_seen_at=now, last_attempt_at=None, attempts=0,
                reason_code='legacy_unknown', last_error=raw, next_retry_at=now,
                queue_ref=None, count=None, manual_review=True, legacy_raw=raw)


def snapshot_queue(root, data_dir):
    # Immutable input snapshot: a queue reference remains usable after data/ rebuilds.
    files = {}
    for path in sorted(data_dir.glob('*.json')):
        if not (path.name.startswith('posts-') or path.name == 'media-map.json'):
            continue
        files[path.name] = json.loads(path.read_text(encoding='utf-8'))
    value = {'files': files, 'items': source_items(files)}
    target = root / '.retry-queues' / (digest(value) + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(target, value)
    return str(target)


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with open(root / '.retry-state.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def mark(path, reason, error='', queue_ref=None, count=None):
    with locked(path.parent):
        return _mark(path, reason, error, queue_ref, count)


def _mark(path, reason, error='', queue_ref=None, count=None):
    now = datetime.now(timezone.utc)
    old = read(path) or {}
    if queue_ref is None:
        queue_ref = snapshot_queue(path.parent, path.parent.parent / 'data')
    token = attempt_id()
    attempts = old.get('attempts', 0) + (old.get('attempt_id') != token)
    rec = dict(old, first_seen_at=old.get('first_seen_at', now.isoformat()),
               last_attempt_at=now.isoformat(), attempts=attempts, attempt_id=token,
               reason_code='legacy_unknown' if old.get('legacy_raw') is not None else reason,
               last_error=str(error), next_retry_at=(now + timedelta(
                   seconds=min(86400, 300 * 2 ** min(attempts - 1, 9)))).isoformat(),
               queue_ref=queue_ref or old.get('queue_ref'),
               count=count if count is not None else old.get('count', 1),
               count_unit='items' if count is not None else old.get('count_unit', 'queue'))
    rec['queue_refs'] = sorted(set(old.get('queue_refs', []) + ([old['queue_ref']] if old.get('queue_ref') else []) + [queue_ref]))
    if reason == 'source_generation_changed':
        rec['manual_review'] = True
    if attempts >= 10 or now - datetime.fromisoformat(rec['first_seen_at']) > timedelta(days=7):
        rec['manual_review'] = True
        print(f'[retry] 장기 정체 — 수동 검토 필요: {path.name}', flush=True)
    atomic_json_write(path, rec)
    return rec


def source_items(files):
    items = {}
    for name, data in files.items():
        if not name.startswith('posts-') or not isinstance(data, dict):
            continue
        for post in data.get('posts', []):
            key = post.get('id') or post.get('tweetId')
            if key:
                items[name + ':' + str(key)] = digest(post)
    return items


def clear_completed(path, expected):
    """Remove only a completed, unchanged queue whose inputs are still present."""
    with locked(path.parent):
        current = read(path)
        if current is None or current != expected:
            return
        if 'legacy_raw' in current:
            _mark(path, 'legacy_unknown', current.get('last_error', ''))
            return
        files = {p.name: json.loads(p.read_text(encoding='utf-8'))
                 for p in (path.parent.parent / 'data').glob('posts-*.json')}
        present = source_items(files)
        for ref in current.get('queue_refs', [current.get('queue_ref')]):
            if not ref:
                continue
            content = json.loads(Path(ref).read_text(encoding='utf-8'))
            if Path(ref).stem != digest(content):
                raise OSError('retry queue fingerprint mismatch')
            queued = source_items(content['files'])
            if any(present.get(key) != fingerprint for key, fingerprint in queued.items()):
                _mark(path, 'source_generation_changed', '이전 입력 큐 보존 — 수동 검토 필요')
                return
        path.unlink()
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def due(path):
    rec = read(path)
    if rec is None:
        return False
    try:
        return datetime.fromisoformat(rec['next_retry_at']) <= datetime.now(timezone.utc)
    except (KeyError, ValueError, TypeError):
        return True


def preserve(root, data_dir):
    with locked(root):
        return _preserve(root, data_dir)


def _preserve(root, data_dir):
    backlog = []
    for name in MARKERS:
        path = root / name
        rec = read(path)
        if rec is None:
            continue
        if not rec.get('queue_ref'):
            rec['queue_ref'] = snapshot_queue(root, data_dir)
        atomic_json_write(path, rec)
        if read(path) != rec:
            raise OSError('retry marker preservation unconfirmed')
        for ref in rec.get('queue_refs', [rec['queue_ref']]):
            queue = Path(ref)
            if not queue.is_file() or queue.stem != digest(json.loads(queue.read_text(encoding='utf-8'))):
                raise OSError('retry queue preservation unconfirmed')
        backlog.append(dict(kind=name, count=rec.get('count'), count_unit=rec.get('count_unit', 'unknown'), reason_code=rec['reason_code'],
                            queue_ref=rec['queue_ref'], fingerprint=digest(rec),
                            queue_refs=rec.get('queue_refs', [rec['queue_ref']]),
                            manual_review=rec.get('manual_review', False)))
    return backlog


def valid_result(path, run_id, fingerprint, rc=10, publish_enabled=None):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not (value['run_id'] == run_id and value['input_fingerprint'] == fingerprint
                and value['exit_code'] == rc and value['required_status'] == 'complete'
                and value['publication_status'] in ('published', 'disabled', 'unchanged')
                and value['backlog_preserved'] is True and value['backlog']):
            return None
        if digest(value['input_hashes']) != fingerprint or (publish_enabled is True and value['publication_status'] == 'disabled'):
            return None
        for entry in value['backlog']:
            marker = read(path.parent / entry['kind'])
            if marker is None or digest(marker) != entry['fingerprint']:
                return None
            for ref in entry.get('queue_refs', [entry['queue_ref']]):
                queue = Path(ref)
                content = json.loads(queue.read_text(encoding='utf-8'))
                if queue.stem != digest(content):
                    return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None
