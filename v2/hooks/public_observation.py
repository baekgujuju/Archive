"""Bounded read-only observations of the existing public GitHub Pages archive.

No credentials, canonical files or private Drive data are sent. No URL provided
by a manifest is followed. Not connected to the operating watchdog yet.
"""
import hashlib
import time
import urllib.error
import urllib.request

from publication_evidence import CHUNKS, EvidenceError, MAX_BYTES, decode, verify_public

BASE = 'https://baekgujuju.github.io/Archive/v2/'
PATHS = {'data/manifest.json', *CHUNKS.values()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EvidenceError('public_redirect_refused')


def fetch_public_bytes(path, *, deadline):
    if path not in PATHS:
        raise EvidenceError('public_path_refused')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EvidenceError('public_deadline')
    url = BASE + path + '?archive_audit=' + str(time.time_ns())
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Archive-publication-audit/1', 'Cache-Control': 'no-cache',
        'Accept': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=min(10, remaining)) as response:
            if response.status != 200 or response.geturl() != url:
                raise EvidenceError('public_response_refused')
            raw = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise EvidenceError('public_deadline')
                piece = response.read(min(65536, MAX_BYTES + 1 - len(raw)))
                if not piece:
                    break
                raw.extend(piece)
                if len(raw) > MAX_BYTES:
                    raise EvidenceError('public_response_too_large')
            return bytes(raw)
    except (OSError, urllib.error.URLError) as exc:
        # Do not leak environment/proxy values, URLs or returned response bodies.
        raise EvidenceError('public_network_unavailable') from exc


def observe_public(fetch, cached_chunks=None):
    """Fetch manifest afresh; reuse only hash-proven bytes for named chunks.

    Cache is an optimization, never a substitute for a current manifest read.
    A second manifest read refuses a generation change during observation.
    """
    cache = cached_chunks or {}
    manifest_raw = fetch('data/manifest.json')
    manifest = decode(manifest_raw)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('chunks'), dict):
        raise EvidenceError('invalid_manifest')
    chunks = {}
    for name, path in CHUNKS.items():
        entry = manifest['chunks'].get(name)
        if not isinstance(entry, dict) or entry.get('file') != path:
            raise EvidenceError('invalid_chunk_path')
        previous = cache.get(name)
        if isinstance(previous, bytes) and hashlib.sha256(previous).hexdigest() == entry.get('sha256'):
            chunks[name] = previous
        else:
            chunks[name] = fetch(path)
    observed = verify_public(manifest_raw, chunks)
    if fetch('data/manifest.json') != manifest_raw:
        raise EvidenceError('public_generation_changed')
    return observed, chunks


def current_public(cached_chunks=None, budget=45):
    if not isinstance(budget, (int, float)) or not 1 <= budget <= 60:
        raise EvidenceError('invalid_public_budget')
    deadline = time.monotonic() + budget
    return observe_public(lambda path: fetch_public_bytes(path, deadline=deadline), cached_chunks)
