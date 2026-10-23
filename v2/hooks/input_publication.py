"""Reuse the installed builder's provenance to track all three source lanes."""
import importlib.util
import math
from pathlib import Path
import sys

from publication_evidence import EvidenceError, SHA, decode, digest


def operating_inputs(v2_root):
    """Read-only: no build/adapter main, network, config or canonical writes."""
    root = Path(v2_root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('_archive_health_builder', root/'build_data.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    from ib_adapter import canonical_fingerprint
    # Unlike source_fingerprint's warning/fallback, failure here is unknown;
    # do not certify a None replacement as the actual IB source version.
    ib_hash = canonical_fingerprint()
    if not isinstance(ib_hash, str) or not SHA.fullmatch(ib_hash):
        raise EvidenceError('invalid_ib_input_fingerprint')
    snapshot = {name: builder.sha256_file(path) if path.exists() else None
                for name, path in (('posts', builder.POSTS_JSON), ('web', builder.WEB_INDEX_JSON),
                                   ('ib', builder.IB_LIBRARY_JSON))}
    snapshot['ib_canonical'] = ib_hash
    expected = builder._snapshot_hash(snapshot)
    try:
        manifest = decode((root/'data/manifest.json').read_bytes())
        built = manifest.get('source_hash') if isinstance(manifest, dict) else None
    except (FileNotFoundError, EvidenceError):
        built = None
    if built is not None and (not isinstance(built, str) or not SHA.fullmatch(built)):
        built = None
    return {'source_hash': expected, 'built_source_hash': built}


def reconcile_inputs(inputs, observed, previous, *, now, grace=600, acknowledge_oldest=''):
    if not isinstance(inputs, dict) or not isinstance(previous, dict):
        raise EvidenceError('invalid_input_publication_state')
    expected = inputs.get('source_hash')
    if not isinstance(expected, str) or not SHA.fullmatch(expected):
        raise EvidenceError('invalid_input_fingerprint')
    if any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in (now, grace)):
        raise EvidenceError('invalid_input_publication_time')
    if observed is None:
        status = 'input_public_observation_unavailable'
    elif observed.get('source_hash') == expected:
        status = 'input_version_published'
    elif inputs.get('built_source_hash') != expected:
        status = 'input_build_pending'
    else:
        status = 'input_pages_pending'
    result = {'source_hash': expected, 'status': status, 'checked_at': now}
    pending = status != 'input_version_published'
    if pending:
        since = previous.get('unresolved_since', now)
        seed = previous.get('episode_source_hash', expected)
        if type(since) not in (int, float) or not math.isfinite(since) or not 0 <= since <= now:
            raise EvidenceError('invalid_input_wait_time')
        if not isinstance(seed, str) or not SHA.fullmatch(seed):
            raise EvidenceError('invalid_input_episode')
        token = 'input:' + digest([since, seed])
        result.update(unresolved_since=since, episode_source_hash=seed, incident=token,
                      acknowledged=previous.get('incident') == token and previous.get('acknowledged') is True)
        if acknowledge_oldest:
            if acknowledge_oldest != token:
                raise EvidenceError('input_incident_changed')
            result['acknowledged'] = True
        summary = {'status': status, 'alert': now-since >= grace and not result['acknowledged'],
                   'oldest': token, 'age_seconds': int(now-since)}
    else:
        if acknowledge_oldest:
            raise EvidenceError('input_incident_changed')
        result['verified_at'] = now
        summary = {'status': status, 'alert': False}
    return result, summary
