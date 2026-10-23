"""Private receipt ledger adapter for the existing watchdog (candidate).

No source/configuration leaves this process. Only fixed public Pages resources
are fetched by public_observation. No canonical or intake state is written.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time
import tempfile

from publication_evidence import CHUNKS, MAX_BYTES, EvidenceError, acknowledge, decode, reconcile
from public_observation import current_public
from input_publication import reconcile_inputs


@contextmanager
def tracker_lock(root):
    with (Path(root) / '.publication-tracker.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise EvidenceError('tracker_busy') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _read(path):
    path = Path(path)
    if path.is_symlink():
        raise EvidenceError('tracker_symlink_refused')
    if path.stat().st_size > 64 * 1024 * 1024:
        raise EvidenceError('tracker_input_too_large')
    return decode(path.read_bytes())


def _write(path, value):
    from state_guard import atomic_json_write
    atomic_json_write(path, value)


def _cache(root):
    directory = Path(root)/'.publication-observation-cache'
    if directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)():
        raise EvidenceError('public_cache_link_refused')
    directory.mkdir(mode=0o700, exist_ok=True)
    cache = {}
    for name in CHUNKS:
        path = directory/(name+'.json')
        if path.is_symlink():
            raise EvidenceError('public_cache_link_refused')
        if path.is_file() and path.stat().st_size <= MAX_BYTES:
            cache[name] = path.read_bytes()
    return directory, cache


def _save_cache(directory, current, previous):
    for name, raw in current.items():
        if name not in CHUNKS or not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise EvidenceError('invalid_public_cache')
        if previous.get(name) == raw:
            continue
        fd, temp = tempfile.mkstemp(prefix='.public-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, directory/(name+'.json'))
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def check_receipts(root, canonical_path, *, now=None, grace=600,
                   acknowledge_oldest='', observe=current_public, writer=_write, input_source=None):
    root = Path(root)
    now = time.time() if now is None else now
    with tracker_lock(root):
        intake = _read(root / '.drive_poll_state.json')
        if not isinstance(intake, dict) or not isinstance(intake.get('processed'), dict):
            raise EvidenceError('invalid_intake_state')
        processed = intake['processed']
        target_count = sum(isinstance(r, dict) and r.get('publication_targets') is not None
                           and r.get('verification') == 'verified' for r in processed.values())
        inputs = input_source() if input_source is not None else None
        if not target_count and input_source is None:
            if acknowledge_oldest:
                raise EvidenceError('incident_changed')
            return {'status': 'no_versioned_receipts', 'tracked_receipts': 0, 'alert': False}
        state_path = root / '.publication-receipts.json'
        try:
            previous = _read(state_path)
        except FileNotFoundError:
            previous = {'version': 1, 'receipts': {}}
        if not isinstance(previous, dict) or previous.get('version') != 1 or not isinstance(previous.get('receipts'), dict):
            raise EvidenceError('invalid_tracker_state')
        try:
            canonical = _read(canonical_path)
        except FileNotFoundError:
            if input_source is None:
                raise
            canonical = []
        observation_error = False
        directory, cached = _cache(root)
        try:
            observed, current = observe(cached_chunks=cached)
        except EvidenceError:
            observed = None
            observation_error = True
        else:
            _save_cache(directory, current, cached)
        # Intake/canonical can change while HTTPS is in flight. A fresh local
        # snapshot, not the stale one above, establishes the desired versions.
        intake2 = _read(root / '.drive_poll_state.json')
        try:
            canonical2 = _read(canonical_path)
        except FileNotFoundError:
            if input_source is None:
                raise
            canonical2 = []
        if intake2 != intake or canonical2 != canonical:
            raise EvidenceError('local_snapshot_changed')
        if input_source is not None and input_source() != inputs:
            raise EvidenceError('local_snapshot_changed')
        result = reconcile(processed, canonical, observed, previous['receipts'], now=now, grace=grace)
        input_summary = None
        input_ack = acknowledge_oldest.startswith('input:')
        if inputs is not None:
            result['inputs'], input_summary = reconcile_inputs(inputs, observed, previous.get('inputs', {}),
                now=now, grace=grace, acknowledge_oldest=acknowledge_oldest if input_ack else '')
        elif input_ack:
            raise EvidenceError('input_incident_changed')
        if acknowledge_oldest and not input_ack:
            bits = acknowledge_oldest.split(':')
            if len(bits) != 3 or bits[0] != 'receipt':
                raise EvidenceError('invalid_receipt_ack')
            acknowledge(result, bits[1], bits[2])
            result['due'] = [item for item in result['due'] if item['key'] != bits[1] or item['incident'] != bits[2]]
        # The durable ledger must be written before a success or alert is
        # returned. A failed write never acknowledges notification delivery.
        writer(state_path, result)
        statuses = {}
        for item in result['receipts'].values():
            statuses[item['status']] = statuses.get(item['status'], 0) + 1
        summary = {'status': 'receipt_publication_checked', 'tracked_receipts': len(result['receipts']),
                   'counts': statuses, 'observation_error': observation_error, 'alert': bool(result['due'])}
        if result['due']:
            event = result['due'][0]
            summary.update(status=event['status'], age_seconds=event['age_seconds'],
                           oldest='receipt:'+event['key']+':'+event['incident'])
        if input_summary is not None:
            summary['input_status'] = input_summary['status']
            if input_summary['status'] != 'input_version_published' or input_ack:
                # One input-version incident covers a stale whole publication.
                # Per-record alarms remain queued for loss within a current build.
                summary.update(input_summary)
        return summary
