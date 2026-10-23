"""Convert existing intake/review evidence to delivery-acknowledged notices.

Does not change failure dispositions, proposal decisions or canonical content.
Only counts and fixed status codes leave this private adapter.
"""
from pathlib import Path
import math
from datetime import datetime

from publication_evidence import EvidenceError, digest
from publication_tracker import _read, _write, tracker_lock


def review_events(processed, proposals):
    if not isinstance(processed, dict) or not isinstance(proposals, list):
        raise EvidenceError('invalid_review_sources')
    events = {}
    for fid, record in processed.items():
        if not isinstance(record, dict):
            raise EvidenceError('invalid_review_receipt')
        state = record.get('verification')
        if state in ('quarantined', 'retry_pending', 'unverified', 'unavailable'):
            key = digest(['intake', fid, record.get('modifiedTime'), record.get('size'),
                          record.get('failure_key'), state])
            events[key] = {'code': 'intake_'+state, 'grace': 600 if state == 'retry_pending' else 0}
        audit = record.get('conversion_audit') or {}
        if not isinstance(audit, dict):
            raise EvidenceError('invalid_review_audit')
        if audit.get('capture_completeness') == 'partial':
            key = digest(['partial', fid, record.get('content_sha256'), record.get('modifiedTime')])
            events[key] = {'code': 'partial_capture_review', 'grace': 0}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise EvidenceError('invalid_review_proposal')
        if proposal.get('status') != 'pending':
            continue
        # Values stay local; hash identifies the exact conflict version without
        # copying private source text into the ledger or notification payload.
        key = digest(['merge_conflict', proposal.get('fileId'), proposal.get('fingerprint'),
                      proposal.get('tweetId'), proposal.get('path'),
                      proposal.get('existing'), proposal.get('proposed')])
        events[key] = {'code': 'merge_conflict_review', 'grace': 0}
    return events


def add_poll_delay(events, last_poll, *, now, maximum_age=900):
    """A long optional phase cannot silently hide overdue intake observation.

    Reports delayed observation, never invents a claim that uploads were lost.
    Uses the existing completed-poll timestamp, not the last upload date.
    """
    if last_poll is None:
        return
    try:
        date = datetime.fromisoformat(last_poll)
        if date.tzinfo is None:
            raise ValueError('timezone required')
        stamp = date.timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise EvidenceError('invalid_last_intake_poll') from exc
    if stamp > now:
        raise EvidenceError('future_last_intake_poll')
    if now - stamp > maximum_age:
        events[digest(['intake_poll_delay', last_poll])] = {'code': 'intake_poll_delayed', 'grace': 0}


def update_notices(events, previous, *, now, acknowledge_oldest=''):
    if not isinstance(previous, dict):
        raise EvidenceError('invalid_review_notice_state')
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise EvidenceError('invalid_review_notice_time')
    records, due = {}, []
    for key, event in events.items():
        old = previous.get(key, {})
        if not isinstance(old, dict):
            raise EvidenceError('invalid_review_notice_record')
        first = old.get('first_observed_at', now)
        if type(first) not in (int, float) or not math.isfinite(first) or first < 0 or first > now:
            raise EvidenceError('invalid_review_notice_time')
        record = dict(event, first_observed_at=first, acknowledged=old.get('acknowledged') is True)
        if not record['acknowledged'] and now - first >= event['grace']:
            due.append(key)
        records[key] = record
    due.sort()
    token = 'review:'+digest(due) if due else ''
    counts = {}
    for key in due:
        code = events[key]['code']; counts[code] = counts.get(code, 0) + 1
    if acknowledge_oldest:
        if not due or acknowledge_oldest != token:
            raise EvidenceError('review_incident_changed')
        for key in due:
            records[key]['acknowledged'] = True
    return ({'version': 1, 'checked_at': now, 'records': records},
            {'status': 'review_required' if due else 'review_notices_checked',
             'alert': bool(due) and not acknowledge_oldest, 'oldest': token,
             'review_counts': counts, 'review_item_count': len(due)})


def check_reviews(root, *, now, acknowledge_oldest='', writer=_write):
    root = Path(root)
    with tracker_lock(root):
        state = _read(root/'.drive_poll_state.json')
        if not isinstance(state, dict) or not isinstance(state.get('processed'), dict):
            raise EvidenceError('invalid_review_intake')
        try:
            proposals = _read(root/'.merge_proposals.json')
        except FileNotFoundError:
            proposals = {'version': 1, 'proposals': []}
        if not isinstance(proposals, dict) or proposals.get('version') != 1 or not isinstance(proposals.get('proposals'), list):
            raise EvidenceError('invalid_review_proposals')
        path = root/'.publication-review-notices.json'
        try:
            previous = _read(path)
        except FileNotFoundError:
            previous = {'version': 1, 'records': {}}
        if not isinstance(previous, dict) or previous.get('version') != 1 or not isinstance(previous.get('records'), dict):
            raise EvidenceError('invalid_review_notice_state')
        events = review_events(state['processed'], proposals['proposals'])
        add_poll_delay(events, state.get('last_poll'), now=now)
        ledger, result = update_notices(events,
                                       previous['records'], now=now, acknowledge_oldest=acknowledge_oldest)
        writer(path, ledger)
        return result
