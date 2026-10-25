"""Structured bounded-phase evidence for the existing single-worker pipeline.

Each actor has its own atomic file. Old child completions cannot overwrite a
new token. No arguments, source text, URLs or environment values are recorded.
"""
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from publication_evidence import EvidenceError, decode
from publication_evidence import digest

ACTORS = {'worker', 'scheduler', 'hook'}
PHASES = {'poll', 'drive_poll', 'monitor', 'publish_settings', 'build_data',
          'ib_adapter', 'media-mirror', 'enrich_tools', 'translate_posts',
          'git', 'command'}


def _path(root, actor):
    if actor not in ACTORS:
        raise EvidenceError('invalid_progress_actor')
    return Path(root) / ('.pipeline-progress-'+actor+'.json')


def _write(path, value):
    from state_guard import atomic_json_write
    atomic_json_write(path, value)


def _read(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 16384:
        raise EvidenceError('invalid_progress_file')
    value = decode(path.read_bytes())
    if not isinstance(value, dict) or value.get('version') != 1:
        raise EvidenceError('invalid_progress_record')
    if value.get('actor') not in ACTORS or value.get('phase') not in PHASES or value.get('status') not in ('active', 'ok', 'failed', 'backlog_wait'):
        raise EvidenceError('invalid_progress_record')
    if any(not isinstance(value.get(k), str) or not re.fullmatch('[0-9a-f]{32}', value[k])
           for k in ('cycle', 'token')):
        raise EvidenceError('invalid_progress_identity')
    if any(type(value.get(k)) not in (int, float) or not math.isfinite(value[k])
           for k in ('started_at', 'budget_seconds')) or value['started_at'] < 0 or not 0 < value['budget_seconds'] <= 10000:
        raise EvidenceError('invalid_progress_bounds')
    return value


def start_phase(root, actor, phase, budget, *, cycle=None, now=None, writer=_write):
    now = time.time() if now is None else now
    if phase not in PHASES or type(budget) not in (int, float) or not 0 < budget <= 10000:
        raise EvidenceError('invalid_progress_phase')
    if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
        raise EvidenceError('invalid_progress_time')
    if cycle is None:
        cycle = os.environ.get('ARCHIVE_POLL_ID') or uuid.uuid4().hex
    if not isinstance(cycle, str) or not re.fullmatch('[0-9a-f]{32}', cycle):
        raise EvidenceError('invalid_progress_cycle')
    event = {'version': 1, 'actor': actor, 'phase': phase, 'budget_seconds': budget,
             'cycle': cycle, 'token': uuid.uuid4().hex, 'started_at': now,
             'status': 'active', 'pid': os.getpid()}
    if actor == 'worker':
        try:
            previous = _read(_path(root, actor))
        except FileNotFoundError:
            previous = {}
        if 'last_success_at' in previous:
            event['last_success_at'] = previous['last_success_at']
        if 'failure_since' in previous:
            event['failure_since'] = previous['failure_since']
            event['failure_count'] = previous.get('failure_count', 0)
    writer(_path(root, actor), event)
    return event


def finish_phase(root, event, *, rc=None, now=None, writer=_write, status=None):
    path = _path(root, event['actor'])
    current = _read(path)
    if current.get('token') != event['token']:
        return False
    now = time.time() if now is None else now
    if not isinstance(now, (int, float)) or not math.isfinite(now) or now < event['started_at']:
        raise EvidenceError('invalid_progress_time')
    if rc is not None and type(rc) is not int:
        raise EvidenceError('invalid_progress_result')
    if status is not None and not (status == 'backlog_wait' and rc == 10
                                   and event['actor'] == 'scheduler' and event['phase'] == 'monitor'):
        raise EvidenceError('invalid_progress_status')
    updated = dict(current, status=status or ('ok' if rc == 0 else 'failed'), completed_at=now, rc=rc)
    if event['actor'] == 'worker':
        if rc == 0:
            updated.pop('failure_since', None); updated.pop('failure_count', None)
            updated['last_success_at'] = now
        else:
            updated['failure_since'] = current.get('failure_since', current['started_at'])
            updated['failure_count'] = current.get('failure_count', 0) + 1
    writer(path, updated)
    return True


def progress_health(root, *, now=None, poll_interval=300, idle_grace=600, overrun_grace=180):
    now = time.time() if now is None else now
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
           for v in (now, poll_interval, idle_grace, overrun_grace)):
        raise EvidenceError('invalid_progress_time')
    try:
        worker = _read(_path(root, 'worker'))
    except FileNotFoundError:
        return {'status': 'phase_evidence_unavailable', 'alert': False}
    if worker.get('actor') != 'worker' or worker.get('phase') != 'poll':
        raise EvidenceError('invalid_worker_progress')
    for key in ('started_at', 'budget_seconds'):
        if type(worker.get(key)) not in (float, int) or not math.isfinite(worker[key]):
            raise EvidenceError('invalid_worker_progress')
    repeated_failure = False
    failure_age = 0
    if 'failure_since' in worker:
        since = worker['failure_since']
        if type(since) not in (int, float) or not math.isfinite(since) or not 0 <= since <= now:
            raise EvidenceError('invalid_worker_failure_time')
        if type(worker.get('failure_count')) is not int or worker['failure_count'] < 0:
            raise EvidenceError('invalid_worker_failure_count')
        failure_age = now - since
        repeated_failure = worker['failure_count'] > 0 and failure_age >= idle_grace
    if worker['status'] != 'active':
        completed = worker.get('completed_at')
        if type(completed) not in (int, float) or not math.isfinite(completed) or completed > now:
            raise EvidenceError('invalid_worker_completion')
        age = now - completed
        if repeated_failure and age <= poll_interval + idle_grace:
            return {'status': 'poll_repeated_failure', 'alert': True,
                    'token': digest(['poll_failure', worker['failure_since']]),
                    'age_seconds': int(failure_age), 'actor': 'worker', 'phase': 'poll'}
        return {'status': 'poll_completion_stale' if age > poll_interval + idle_grace else 'between_polls',
                'alert': age > poll_interval + idle_grace, 'token': worker['token'],
                'age_seconds': int(age), 'actor': 'worker', 'phase': 'poll'}
    active = [worker]
    for actor in ('scheduler', 'hook'):
        try:
            child = _read(_path(root, actor))
        except FileNotFoundError:
            continue
        if child.get('cycle') != worker.get('cycle') or child.get('status') != 'active':
            continue
        if child.get('actor') != actor or child.get('phase') not in PHASES:
            raise EvidenceError('invalid_child_progress')
        if any(type(child.get(k)) not in (int, float) or not math.isfinite(child[k])
               for k in ('started_at', 'budget_seconds')):
            raise EvidenceError('invalid_child_progress')
        if child['started_at'] < worker['started_at']:
            raise EvidenceError('invalid_child_phase_order')
        active.append(child)
    if any(p['started_at'] > now for p in active):
        raise EvidenceError('future_progress_time')
    # Check every active bound: a child starting later cannot hide an overrun
    # of the enclosing poll/monitor budget.
    overdue = [p for p in active if now - p['started_at'] > p['budget_seconds'] + overrun_grace]
    if repeated_failure and not overdue:
        return {'status': 'poll_repeated_failure', 'alert': True,
                'token': digest(['poll_failure', worker['failure_since']]),
                'age_seconds': int(failure_age), 'actor': 'worker', 'phase': 'poll'}
    selected = max(overdue or active, key=lambda p: now - p['started_at'] - p['budget_seconds'])
    if selected['started_at'] > now:
        raise EvidenceError('future_progress_time')
    return {'status': 'phase_overrun' if overdue else 'bounded_phase_active',
            'alert': bool(overdue), 'token': selected['token'], 'actor': selected['actor'],
            'phase': selected['phase'], 'age_seconds': int(now - selected['started_at']),
            'budget_seconds': selected['budget_seconds']}
