"""Materialize captured raster bytes before canonical merge. Caller owns pipeline lock."""
import base64
import copy
import hashlib
import io
import os
import re
import tempfile
import warnings
from pathlib import Path

PUBLIC_MEDIA = 'https://baekgujuju.github.io/Archive/v2/media/full/'
LIMIT = 15 * 1024 * 1024
FORMATS = {'png': ('PNG', '.png'), 'jpeg': ('JPEG', '.jpg'),
           'gif': ('GIF', '.gif'), 'webp': ('WEBP', '.webp')}


class EmbeddedMediaError(ValueError):
    pass


def decode_image(value):
    if len(value) > 21 * 1024 * 1024:
        raise EmbeddedMediaError('Embedded image exceeds limit')
    match = re.fullmatch(r'data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/]*={0,2})', value)
    if not match:
        raise EmbeddedMediaError('Unsupported embedded raster encoding; AVIF must be converted by client')
    try:
        raw = base64.b64decode(match[2], validate=True)
        if not raw or len(raw) > LIMIT:
            raise ValueError('size')
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != FORMATS[match[1]][0]:
                    raise ValueError('format mismatch')
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                for frame in range(getattr(image, 'n_frames', 1)):
                    image.seek(frame)
                    image.load()
        return raw, FORMATS[match[1]][1]
    except Exception as exc:
        raise EmbeddedMediaError('Embedded image validation failed: ' + type(exc).__name__) from exc


def materialize_records(records, media_dir):
    """Return a copy; validation completes before any write. Never mutate caller records."""
    result = copy.deepcopy(records)
    staged = {}

    def replace(value):
        if not isinstance(value, str) or not value.startswith('data:'):
            return value
        raw, ext = decode_image(value)
        name = 'embedded-' + hashlib.sha256(raw).hexdigest() + ext
        staged[name] = raw
        return PUBLIC_MEDIA + name

    def media(value):
        if isinstance(value, str):
            return replace(value)
        if isinstance(value, list):
            return [media(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) if key in ('url', 'src', 'full', 'thumb', 'media_url', 'image_url', 'mediaUrl') else item
                    for key, item in value.items()}
        return value

    def visit(record):
        if not isinstance(record, dict):
            return
        for key in ('media', 'mediaImages'):
            if key in record:
                record[key] = media(record[key])
        if isinstance(record.get('fullHtml'), str):
            record['fullHtml'] = re.sub(r'(<img\b[^>]*?\bsrc=["\'])(data:[^"\']+)(["\'])',
                                        lambda m: m[1] + replace(m[2]) + m[3], record['fullHtml'], flags=re.I)
        for child in record.get('replies', []) or []:
            visit(child)
        visit(record.get('quoted'))

    for record in result:
        visit(record)
    root = Path(media_dir)
    if staged:
        root.mkdir(parents=True, exist_ok=True)
    for name, raw in staged.items():
        dest = root / name
        if dest.is_file() and dest.read_bytes() == raw:
            continue
        fd, temporary = tempfile.mkstemp(prefix='.embedded-', suffix='.tmp', dir=root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, dest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return result


def published_asset_path(url, media_dir):
    if not isinstance(url, str) or not url.startswith(PUBLIC_MEDIA):
        return None
    name = url[len(PUBLIC_MEDIA):]
    if not re.fullmatch(r'embedded-[0-9a-f]{64}\.(png|jpg|gif|webp)', name):
        return None
    return Path(media_dir) / name
