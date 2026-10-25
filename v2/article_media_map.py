"""Compile X Article positions from captured DOM and preserved bytes, offline.

Never infer positions from media order. An unavailable source retains only
previously verified entries that still belong to the current record. All other
evidence failures preserve the complete previous map and return nonzero.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from PIL import Image
from hooks.preservation_audit import CaptureTree, Node, MAX_HTML_BYTES, _text, _owner

PUBLIC = 'https://baekgujuju.github.io/Archive/v2/media/full/'
LINK = re.compile(r'/article/(\d+)/media/(\d+)(?:[?#].*)?$')
FILE = re.compile(r'embedded-([0-9a-f]{64})\.(jpg|jpeg|png|gif|webp|avif)$')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=1).encode('utf-8')


def descendants(node):
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from descendants(child)


def expected(record):
    result = set()
    for match in re.finditer(r'/article/(\d+)/media/(\d+)', record.get('fullText') or ''):
        result.add(match.group(2))
    return result


def local_image(url, media_dir):
    if not isinstance(url, str) or not url.startswith(PUBLIC):
        raise ValueError('nonlocal_media_url')
    name = url[len(PUBLIC):]
    match = FILE.fullmatch(name)
    if not match:
        raise ValueError('invalid_media_filename')
    path = media_dir / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing_media_file')
    raw = path.read_bytes()
    if sha(raw) != match[1]:
        raise ValueError('media_sha_mismatch')
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('image_pixel_limit')
        image.load()
        size = image.size
    return raw, size


def compile_capture(path, tid, record, media_dir):
    raw = path.read_bytes()
    if len(raw) > MAX_HTML_BYTES:
        raise ValueError('capture_size_limit')
    tree = CaptureTree(raw.decode('utf-8'))
    mains = [n for n in tree.nodes if n.tag == 'article'
             and n.attrs.get('data-tweet-id') == tid and 'reply' not in n.classes()
             and _owner(n) is None]
    if len(mains) != 1:
        raise ValueError('main_identity_ambiguous')
    main = mains[0]
    scripts = [n for n in tree.nodes if n.tag == 'script'
               and n.attrs.get('id') == 'archive-capture-manifest']
    if len(scripts) != 1:
        raise ValueError('manifest_missing_or_ambiguous')
    manifest = json.loads(_text(scripts[0]))
    images = manifest.get('images')
    if not isinstance(images, list):
        raise ValueError('manifest_images_invalid')
    owned = {m.get('url') for m in record.get('media', []) if isinstance(m, dict)}
    out = {}
    for anchor in descendants(main):
        if anchor.tag != 'a' or _owner(anchor) is not main:
            continue
        href = anchor.attrs.get('href', '')
        parsed = urlsplit(href)
        match = LINK.search(parsed.path)
        if not match or parsed.hostname not in {'x.com', 'twitter.com', 'www.x.com', 'www.twitter.com'}:
            continue
        if match[1] != tid:
            raise ValueError('article_identity_mismatch')
        # Only semantic body anchors; exclude metadata, quotes and replies.
        parent, in_body, excluded = anchor.parent, False, False
        while parent and parent is not main:
            in_body |= bool(parent.classes() & {'tweet-text', 'article-body'})
            excluded |= bool(parent.classes() & {'translation-ko', 'meta', 'replies'})
            parent = parent.parent
        if not in_body or excluded:
            continue
        imgs = [n for n in descendants(anchor) if n.tag == 'img']
        if len(imgs) != 1:
            raise ValueError('anchor_image_count')
        img = imgs[0]
        data = re.fullmatch(r'data:image/(?:png|jpeg|gif|webp|avif);base64,([A-Za-z0-9+/]*={0,2})', img.attrs.get('src', ''))
        if not data:
            raise ValueError('anchor_image_not_embedded')
        content = base64.b64decode(data[1], validate=True)
        digest = sha(content)
        urls = [url for url in owned if isinstance(url, str)
                and url.startswith(PUBLIC + 'embedded-' + digest + '.')]
        if len(urls) != 1:
            raise ValueError('media_ownership_ambiguous')
        url = urls[0]
        stored, size = local_image(url, media_dir)
        if stored != content:
            raise ValueError('capture_store_bytes_mismatch')
        media_id = img.attrs.get('data-archive-media')
        entries = [m for m in images if isinstance(m, dict)
                   and (m.get('id') == media_id if media_id else m.get('bytes') == len(content))]
        if len(entries) != 1:
            raise ValueError('manifest_identity_ambiguous')
        entry = entries[0]
        if (entry.get('status') != 'embedded' or 'body' not in entry.get('roles', [])
                or entry.get('bytes') != len(content)
                or (entry.get('width'), entry.get('height')) != size
                or entry.get('sha256', digest) != digest):
            raise ValueError('manifest_image_mismatch')
        mid = match[2]
        if mid in out and out[mid] != url:
            raise ValueError('conflicting_media_id')
        out[mid] = url
    if set(out) != expected(record):
        raise ValueError('body_link_coverage_mismatch')
    return dict(sorted(out.items())), sha(raw)


def generate(records, inbox, media_dir, previous, require_originals=False):
    targets = {str(r.get('tweetId')): r for r in records if expected(r)}
    out, evidence = {}, {}
    if not isinstance(previous, dict):
        raise ValueError('invalid_previous_map')
    # Parse source IDs, not filenames (Drive originals may have hashed names).
    sources = {}
    for path in sorted(inbox.glob('*.html')):
        if path.is_symlink() or path.stat().st_size > MAX_HTML_BYTES:
            continue
        # Bounded scan; identity is strictly checked again in compile_capture.
        raw = path.read_bytes()
        ids = re.findall(rb'<article\b[^>]*\bdata-tweet-id=["\'](\d+)["\']', raw)
        possible = {value.decode('ascii') for value in ids} & targets.keys()
        if not possible:
            continue
        tree = CaptureTree(raw.decode('utf-8'))
        # A later unrelated capture may contain this post as a reply. It is not
        # a new original for that post and must not displace its real source.
        main_ids = {node.attrs.get('data-tweet-id') for node in tree.nodes
                    if node.tag == 'article' and _owner(node) is None
                    and 'reply' not in node.classes()}
        for tid in possible & main_ids:
            sources.setdefault(tid, []).append(path)
    for tid, record in sorted(targets.items()):
        candidates = sources.get(tid, [])
        prior = previous.get(tid, {})
        if not isinstance(prior, dict):
            raise ValueError('invalid_previous_article')
        if any(not isinstance(mid, str) or not mid.isdigit() for mid in prior):
            raise ValueError('invalid_previous_media_id')
        prior = {mid: url for mid, url in prior.items()
                 if mid in expected(record) and url in {m.get('url') for m in record.get('media', [])}}
        for url in prior.values():
            local_image(url, media_dir)
        if not candidates:
            if require_originals:
                raise ValueError('original_missing:' + tid)
            if prior:
                out[tid] = dict(sorted(prior.items()))
            evidence[tid] = {'status': 'retained' if prior else 'unmapped', 'reason': 'original_missing'}
            continue
        # Most recent capture must match current record; never silently use stale
        # evidence after a new capture fails validation.
        source = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
        mapping, source_hash = compile_capture(source, tid, record, media_dir)
        out[tid] = mapping
        evidence[tid] = {'status': 'verified', 'source_sha256': source_hash, 'links': len(mapping)}
    return out, evidence


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=root / 'data')
    parser.add_argument('--media-dir', type=Path, default=root / 'media' / 'full')
    parser.add_argument('--inbox', type=Path)
    parser.add_argument('--report', type=Path, default=root / 'hooks' / '.article-media-map-report.json')
    parser.add_argument('--check', action='store_true', help='validate without writing; exit 2 means map would change')
    parser.add_argument('--require-originals', action='store_true')
    args = parser.parse_args(argv)
    path = args.data_dir / 'article-media-map.json'
    old = path.read_bytes() if path.exists() else b'{}'
    map_changed = False
    try:
        inbox = args.inbox
        if inbox is None:
            home = Path.home() / 'workspace' / 'x-bookmarks'
            cfg = load(home / 'drive_archive_config.json')
            inbox = Path(cfg.get('local_inbox', str(home / 'drive_inbox'))).expanduser()
        if not inbox.is_dir():
            raise ValueError('inbox_unavailable')
        chunk = load(args.data_dir / 'posts-x.json')
        records = chunk['posts'] if isinstance(chunk, dict) else chunk
        result, evidence = generate(records, inbox, args.media_dir, json.loads(old), args.require_originals)
        new = encoded(result)
        manifest_path = args.data_dir / 'manifest.json'
        manifest_bytes = None
        if manifest_path.exists():
            manifest = load(manifest_path)
            manifest['article_media_map'] = {'file': 'data/article-media-map.json',
                                             'sha256': sha(new), 'count': len(result)}
            manifest_bytes = encoded(manifest)
        report = {'version': 1, 'status': 'ok', 'map_sha256': sha(new), 'articles': evidence}
        if args.check:
            print(json.dumps(report, ensure_ascii=False))
            return 0 if old == new else 2
        # The private evidence is durable before publishing the matching map.
        atomic(args.report, encoded(report))
        if old != new:
            atomic(path, new)
            map_changed = True
        if manifest_bytes is not None:
            atomic(manifest_path, manifest_bytes)
        print('article media map: articles=%d links=%d sha256=%s' % (len(result), sum(map(len, result.values())), sha(new)))
        return 0
    except Exception as exc:
        # A later manifest write must not leave a partially published generation.
        # Process interruption is also safe: hook publication runs only on rc=0.
        if map_changed:
            atomic(path, old)
        if not args.check:
            atomic(args.report, encoded({'version': 1, 'status': 'needs_review',
                                        'previous_map_sha256': sha(old), 'reason': type(exc).__name__ + ':' + str(exc)[:300]}))
        print('article media map: validation failed; previous map retained')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
