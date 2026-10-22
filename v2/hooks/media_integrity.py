"""Validate owned media against the exact candidate commit, never the worktree.

read_blob(path) -> bytes | None is supplied by the caller and pinned to one SHA.
Infrastructure errors must propagate so they are retried, not called corruption.
"""
import hashlib
import io
import re
import warnings
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

ORIGIN = 'baekgujuju.github.io'
PREFIX = '/Archive/v2/media/'


class MediaIntegrityError(ValueError):
    pass


class _Sources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('img', 'source', 'video'):
            for key in ('src', 'poster'):
                if attrs.get(key):
                    self.urls.append(attrs[key])


def owned_path(url):
    if not isinstance(url, str):
        return None
    if url.startswith(('media/', './media/')):
        url = 'https://' + ORIGIN + '/Archive/v2/' + url.removeprefix('./')
    elif url.startswith(PREFIX):
        url = 'https://' + ORIGIN + url
    parsed = urlsplit(url)
    if parsed.hostname != ORIGIN or not parsed.path.startswith(PREFIX):
        return None
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise MediaIntegrityError('Invalid owned media origin')
    path = unquote(parsed.path)
    if '%' in path or '\\' in path or any(p in ('', '.', '..') for p in path.lstrip('/').split('/')):
        raise MediaIntegrityError('Unsafe owned media path')
    return 'v2/' + path[len('/Archive/v2/'):]


def media_urls(value):
    """Only media fields/HTML sources; a prose link is not an image requirement."""
    if isinstance(value, list):
        for item in value:
            yield from media_urls(item)
    elif isinstance(value, dict):
        for key in ('media', 'mediaImages'):
            for item in value.get(key, []) or []:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, list):
                    yield from (v for v in item if isinstance(v, str))
                elif isinstance(item, dict):
                    yield from (item[k] for k in ('url', 'src', 'full', 'thumb', 'poster', 'media_url', 'image_url', 'mediaUrl') if isinstance(item.get(k), str))
        if isinstance(value.get('fullHtml'), str):
            parser = _Sources()
            parser.feed(value['fullHtml'])
            yield from parser.urls
        yield from media_urls(value.get('replies'))
        yield from media_urls(value.get('quoted'))


def validate_owned_media(records, read_blob):
    """Return unique validated paths; missing/hash-invalid content blocks publish."""
    paths = sorted({path for url in media_urls(records) if (path := owned_path(url))})
    for path in paths:
        raw = read_blob(path)
        if raw is None:
            raise MediaIntegrityError('Owned media absent from candidate commit: ' + path)
        if not raw:
            raise MediaIntegrityError('Owned media empty: ' + path)
        if len(raw) > 15 * 1024 * 1024:
            raise MediaIntegrityError('Owned image exceeds capture limit: ' + path)
        name = path.rsplit('/', 1)[-1]
        if name.startswith('embedded-'):
            match = re.fullmatch(r'embedded-([0-9a-f]{64})\.(png|jpg|gif|webp)', name)
            if not match or hashlib.sha256(raw).hexdigest() != match[1]:
                raise MediaIntegrityError('Embedded media content hash mismatch: ' + path)
        from PIL import Image
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('PNG', 'JPEG', 'WEBP', 'GIF'):
                        raise ValueError('unsupported raster')
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    for frame in range(getattr(image, 'n_frames', 1)):
                        image.seek(frame)
                        image.load()
        except Exception as exc:
            raise MediaIntegrityError('Owned image is not a complete supported raster: ' + path) from exc
    return paths
