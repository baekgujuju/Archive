"""Pure publication evidence checks; no network, file writes or canonical edits.

This candidate is not connected to the worker. A verified public observation
must come from a fresh manifest and hash-verified chunks, never an ID-only list.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

FIELDS = ('sourceUrl', 'createdAt', 'fullText', 'fullHtml', 'author',
          'media', 'quoted', 'replies', 'links')
SHA = re.compile(r'[0-9a-f]{64}')
CHUNKS = {'posts-x': 'data/posts-x.json', 'posts-web': 'data/posts-web.json',
          'posts-ib': 'data/posts-ib.json'}
MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 50000
UNRESOLVED = {'publication_pending', 'public_observation_unavailable',
              'canonical_missing', 'canonical_changed_unverified'}


class EvidenceError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError('duplicate_json_key')
        result[key] = value
    return result


def decode(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise EvidenceError('invalid_response_size')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              EvidenceError('nonfinite_json')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise EvidenceError('invalid_json') from exc


def digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as exc:
        raise EvidenceError('invalid_digest_input') from exc
    return hashlib.sha256(raw).hexdigest()


def identity(record):
    if not isinstance(record, dict):
        raise EvidenceError('record_not_object')
    key = record.get('tweetId', record.get('id'))
    if not isinstance(key, str) or not key or len(key) > 256:
        raise EvidenceError('invalid_record_id')
    return key


def content_projection(record, depth=0):
    """Collector content only; classification/translation are separate work.

    Empty and absent fields stay different. Replies keep order and nested
    collector fields, so a missing final paragraph/link/quote cannot pass.
    """
    if depth > 24:
        raise EvidenceError('nested_record_limit')
    if not isinstance(record, dict):
        raise EvidenceError('record_not_object')
    # Legacy nested quotes/replies can explicitly lack IDs. Keep that missing
    # identity in the hash instead of inventing an ID or blocking every post.
    result = {'id': identity(record) if depth == 0 else record.get('tweetId', record.get('id'))}
    for key in FIELDS:
        if key not in record:
            continue
        value = record[key]
        if key == 'quoted' and value not in (None, {}):
            value = content_projection(value, depth + 1)
        elif key == 'replies':
            if not isinstance(value, list) or len(value) > MAX_RECORDS:
                raise EvidenceError('invalid_replies')
            value = [content_projection(item, depth + 1) for item in value]
        result[key] = value
    return result


def content_hash(record):
    return digest(content_projection(record))


def index_records(records):
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise EvidenceError('invalid_records')
    result = {}
    for record in records:
        key = identity(record)
        if key in result:
            raise EvidenceError('duplicate_record_id')
        result[key] = record
    return result


def verify_public(manifest_raw, chunk_bytes):
    """Validate all three post chunks of one observed manifest.

    Caller supplies bytes fetched at fixed approved paths. Manifest paths are
    validated but never followed. This function does not certify media bytes.
    """
    manifest = decode(manifest_raw)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('chunks'), dict):
        raise EvidenceError('invalid_manifest')
    if set(manifest['chunks']) != set(CHUNKS) or set(chunk_bytes) != set(CHUNKS):
        raise EvidenceError('incomplete_chunk_set')
    records = []
    for name, path in CHUNKS.items():
        entry = manifest['chunks'][name]
        if not isinstance(entry, dict) or entry.get('file') != path:
            raise EvidenceError('invalid_chunk_path')
        sha = entry.get('sha256')
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise EvidenceError('invalid_chunk_sha')
        raw = chunk_bytes[name]
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
            raise EvidenceError('invalid_chunk_size')
        if hashlib.sha256(raw).hexdigest() != sha:
            raise EvidenceError('chunk_sha_mismatch')
        data = decode(raw)
        if not isinstance(data, dict) or not isinstance(data.get('posts'), list):
            raise EvidenceError('invalid_chunk_posts')
        count = entry.get('count')
        if type(count) is not int or count != len(data['posts']):
            raise EvidenceError('chunk_count_mismatch')
        records.extend(data['posts'])
    indexed = index_records(records)
    source_hash = manifest.get('source_hash')
    if source_hash is not None and (not isinstance(source_hash, str) or not SHA.fullmatch(source_hash)):
        raise EvidenceError('invalid_manifest_source_hash')
    return {'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
            'records': indexed, 'source_hash': source_hash}


def capture_targets(merged, produced_ids):
    """Snapshot actual merge output, not incoming values rejected as conflicts."""
    indexed = index_records(merged)
    if not isinstance(produced_ids, list) or not produced_ids or len(set(produced_ids)) != len(produced_ids):
        raise EvidenceError('invalid_produced_ids')
    if any(key not in indexed for key in produced_ids):
        raise EvidenceError('merged_record_missing')
    return {key: content_hash(indexed[key]) for key in produced_ids}


def receipt_key(file_id, receipt):
    return digest([file_id, receipt.get('modifiedTime'), receipt.get('size'),
                   receipt.get('content_sha256'), receipt.get('publication_targets')])


def reconcile(processed, canonical, public, previous, *, now, grace=600):
    """Return private version ledger and due incident keys, without side effects.

    public=None means unavailable observation. Previous success is retained as
    historical evidence, but never reported as a fresh current observation.
    Acknowledgement is deliberately a separate operation after delivery.
    """
    if not isinstance(processed, dict) or not isinstance(previous, dict):
        raise EvidenceError('invalid_state')
    if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
        raise EvidenceError('invalid_time')
    if not isinstance(grace, (int, float)) or not math.isfinite(grace) or grace < 0:
        raise EvidenceError('invalid_grace')
    local = index_records(canonical)
    local_hash = {key: content_hash(item) for key, item in local.items()}
    if public is not None and (not isinstance(public, dict) or
            not isinstance(public.get('records'), dict) or any(
                key != identity(value) for key, value in public['records'].items())):
        raise EvidenceError('invalid_public_observation')
    public_hash = None if public is None else {
        key: content_hash(item) for key, item in public['records'].items()}
    known_versions = {}
    for receipt in processed.values():
        if isinstance(receipt, dict) and receipt.get('verification') == 'verified':
            targets = receipt.get('publication_targets')
            if isinstance(targets, dict):
                for tid, value in targets.items():
                    if isinstance(value, str):
                        known_versions.setdefault(tid, set()).add(value)
    ledger, due = {}, []
    for file_id, receipt in processed.items():
        if not isinstance(receipt, dict):
            raise EvidenceError('invalid_receipt')
        targets = receipt.get('publication_targets')
        if receipt.get('verification') != 'verified' or targets is None:
            continue  # Legacy/unaccepted input is not a fabricated publication target.
        if not isinstance(targets, dict) or not targets or any(
                not isinstance(key, str) or not isinstance(value, str) or not SHA.fullmatch(value)
                for key, value in targets.items()):
            raise EvidenceError('invalid_publication_targets')
        key = receipt_key(file_id, receipt)
        old = previous.get(key, {})
        first = old.get('first_observed_at', now)
        if not isinstance(first, (int, float)) or not math.isfinite(first) or first < 0 or first > now:
            raise EvidenceError('invalid_first_observation')
        if any(tid not in local_hash for tid in targets):
            status = 'canonical_missing'
        elif any(local_hash[tid] not in known_versions.get(tid, set()) for tid in targets):
            status = 'canonical_changed_unverified'
        elif any(local_hash[tid] != expected for tid, expected in targets.items()):
            status = 'superseded_by_known_receipt'
        elif public_hash is None:
            status = 'public_observation_unavailable'
        elif all(public_hash.get(tid) == expected for tid, expected in targets.items()):
            status = 'published'
        else:
            status = 'publication_pending'
        item = {'first_observed_at': first, 'checked_at': now, 'status': status,
                'record_count': len(targets),
                'review_required': bool(receipt.get('publication_review_required')),
                'capture_completeness': receipt.get('conversion_audit', {}).get('capture_completeness', 'unknown')}
        unresolved = status in UNRESOLVED
        since = old.get('unresolved_since', now) if old.get('status') in UNRESOLVED else now
        if not isinstance(since, (int, float)) or not math.isfinite(since) or since < 0 or since > now:
            raise EvidenceError('invalid_unresolved_time')
        if unresolved:
            item['unresolved_since'] = since
        if old.get('published_at') is not None:
            item['published_at'] = old['published_at']
        if status == 'published':
            item.setdefault('published_at', now)
        # One uninterrupted unresolved episode, even if the network flaps.
        incident = digest([key, 'unresolved', since])
        if unresolved:
            item['incident'] = incident
        if old.get('acknowledged_incident') == incident:
            item['acknowledged_incident'] = incident
        if unresolved and now - since >= grace:
            if item.get('acknowledged_incident') != incident:
                due.append({'key': key, 'incident': incident, 'status': status,
                            'age_seconds': int(now - since)})
        ledger[key] = item
    due.sort(key=lambda item: (-item['age_seconds'], item['key']))
    return {'version': 1, 'checked_at': now, 'receipts': ledger, 'due': due}


def acknowledge(ledger, key, incident):
    """Call only after existing notification transport confirms delivery."""
    item = ledger['receipts'].get(key)
    if item is None or item.get('status') not in UNRESOLVED or item.get('incident') != incident:
        raise EvidenceError('incident_changed')
    item['acknowledged_incident'] = incident
