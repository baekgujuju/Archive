"""P2 배치 1 미러 테스트 (Codex 테스트 게이트).

- 누락/손상/형식 오류 청크 → 양쪽 맵 바이트 불변, rc=2, 다운로드 없음.
- data 디렉터리 없음 / 청크 형상 오류 / 유효한 빈 아카이브 구분.
- 손상 캐시·헤더-only/잘린 PNG/JPEG/GIF/WebP 거부, 유효 이미지는 캐시 히트.
- 유효하지 않은 다운로드가 유효한 캐시 바이트를 덮지 않음.
- 일반 다운로드 실패 → rc=1, 맵은 성공 URL만 포함 (부분 게시 가능).
"""
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

MIRROR_PATH = Path(__file__).resolve().parent / "media-mirror.py"


def load_mirror():
    spec = importlib.util.spec_from_file_location("media_mirror", MIRROR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- 이미지 바이트 픽스처 (전체 프레임) ---

def png_bytes(valid=True):
    sig = bytes.fromhex("89504e470d0a1a0a")
    iend = bytes.fromhex("0000000049454e44ae426082")
    if valid:
        return sig + b"\x00" * 20 + iend
    return sig + b"\x00" * 20  # IEND 없음 (잘림)


def jpeg_bytes(valid=True):
    if valid:
        return b"\xff\xd8\xff" + b"\x00" * 20 + b"\xff\xd9"
    return b"\xff\xd8\xff" + b"\x00" * 20  # EOI 없음


def gif_bytes(valid=True):
    if valid:
        return b"GIF89a" + b"\x00" * 20 + b"\x3b"
    return b"GIF89a" + b"\x00" * 20  # 트레일러 없음


def webp_bytes(valid=True):
    payload = b"VP8 " + b"\x00" * 10
    size = 4 + len(payload)
    data = b"RIFF" + struct.pack("<I", size) + b"WEBP" + payload
    if valid:
        return data
    # RIFF 크기를 실제보다 크게 (잘림).
    return b"RIFF" + struct.pack("<I", size + 100) + b"WEBP" + payload


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    mod = load_mirror()
    v2 = tmp_path / "v2"
    data = v2 / "data"
    media = v2 / "media" / "full"
    js = v2 / "js"
    for d in (data, media, js):
        d.mkdir(parents=True)
    monkeypatch.setattr(mod, "V2_ROOT", v2)
    monkeypatch.setattr(mod, "DATA_DIR", data)
    monkeypatch.setattr(mod, "MEDIA_DIR", media)
    monkeypatch.setattr(mod, "MAP_JS", js / "media-map.js")
    monkeypatch.setattr(mod, "MAP_JSON", data / "media-map.json")
    return mod, v2, data, media


def _write_chunk_set(mirror_mod, data, posts_by_chunk):
    """청크 파일 + 매니페스트를 쓴다. posts_by_chunk: {name: [posts]}."""
    chunks = {}
    for name, posts in posts_by_chunk.items():
        chunk = {"posts": posts}
        raw = json.dumps(chunk, ensure_ascii=False).encode("utf-8")
        (data / f"posts-{name}.json").write_bytes(raw)
        chunks[f"posts-{name}"] = {
            "file": f"data/posts-{name}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "count": len(posts),
        }
    (data / "manifest.json").write_text(
        json.dumps({"chunks": chunks}, ensure_ascii=False), encoding="utf-8")
    return chunks


def _post_with_image(url):
    return {"id": "1", "fullHtml": f'<img src="{url}">', "mediaImages": [], "media": []}


# --- 전체 프레임 검증 ---

@pytest.mark.parametrize("data,ext,expected", [
    (png_bytes(True), ".png", True),
    (png_bytes(False), ".png", False),
    (bytes.fromhex("89504e470d0a1a0a"), ".png", False),  # 헤더-only
    (jpeg_bytes(True), ".jpg", True),
    (jpeg_bytes(False), ".jpg", False),
    (b"\xff\xd8\xff", ".jpg", False),
    (gif_bytes(True), ".gif", True),
    (gif_bytes(False), ".gif", False),
    (b"GIF89a", ".gif", False),
    (webp_bytes(True), ".webp", True),
    (webp_bytes(False), ".webp", False),
    (b"RIFF", ".webp", False),
    (b"not-an-image", ".png", False),
    (b"", ".jpg", False),
])
def test_is_complete_image(mirror, data, ext, expected):
    mod, _, _, _ = mirror
    assert mod.is_complete_image(data, ext) is expected


# --- 청크 실패 시 맵 불변 + rc=2 + 다운로드 없음 ---

def test_missing_chunk_leaves_maps_untouched(mirror, monkeypatch):
    mod, v2, data, media = mirror
    _write_chunk_set(mod, data, {"x": [], "web": [], "ib": []})
    (mod.MAP_JS).write_text("OLD-JS")
    (mod.MAP_JSON).write_text("OLD-JSON")
    # posts-ib 청크 삭제 (매니페스트는 여전히 참조).
    (data / "posts-ib.json").unlink()
    calls = []
    monkeypatch.setattr(mod, "download_image", lambda u: calls.append(u) or ("x", 1))
    assert mod.main() == 2
    assert mod.MAP_JS.read_text() == "OLD-JS"
    assert mod.MAP_JSON.read_text() == "OLD-JSON"
    assert calls == []


def test_malformed_chunk_leaves_maps_untouched(mirror, monkeypatch):
    mod, v2, data, media = mirror
    _write_chunk_set(mod, data, {"x": [], "web": [], "ib": []})
    (mod.MAP_JS).write_text("OLD-JS")
    (mod.MAP_JSON).write_text("OLD-JSON")
    # posts-x를 손상된 JSON으로 교체 — 매니페스트 해시도 어긋난다.
    (data / "posts-x.json").write_text("{broken")
    calls = []
    monkeypatch.setattr(mod, "download_image", lambda u: calls.append(u) or ("x", 1))
    assert mod.main() == 2
    assert mod.MAP_JS.read_text() == "OLD-JS"
    assert mod.MAP_JSON.read_text() == "OLD-JSON"
    assert calls == []


def test_missing_manifest_returns_2(mirror, monkeypatch):
    mod, v2, data, media = mirror
    # manifest.json 없음.
    (mod.MAP_JS).write_text("OLD-JS")
    calls = []
    monkeypatch.setattr(mod, "download_image", lambda u: calls.append(u) or ("x", 1))
    assert mod.main() == 2
    assert mod.MAP_JS.read_text() == "OLD-JS"
    assert calls == []


def test_missing_data_dir_returns_2(mirror, monkeypatch):
    mod, v2, data, media = mirror
    import shutil
    shutil.rmtree(data)
    monkeypatch.setattr(mod, "DATA_DIR", data)  # 존재하지 않는 경로
    assert mod.main() == 2


def test_invalid_chunk_shape_returns_2(mirror, monkeypatch):
    mod, v2, data, media = mirror
    # 청크가 {posts: [...]}가 아니라 리스트.
    raw = json.dumps([{"id": "1"}]).encode("utf-8")
    (data / "posts-x.json").write_bytes(raw)
    (data / "posts-web.json").write_bytes(json.dumps({"posts": []}).encode())
    (data / "posts-ib.json").write_bytes(json.dumps({"posts": []}).encode())
    chunks = {
        "posts-x": {"file": "data/posts-x.json",
                    "sha256": hashlib.sha256(raw).hexdigest(), "count": 1},
        "posts-web": {"file": "data/posts-web.json",
                      "sha256": hashlib.sha256(json.dumps({"posts": []}).encode()).hexdigest(),
                      "count": 0},
        "posts-ib": {"file": "data/posts-ib.json",
                     "sha256": hashlib.sha256(json.dumps({"posts": []}).encode()).hexdigest(),
                     "count": 0},
    }
    (data / "manifest.json").write_text(json.dumps({"chunks": chunks}))
    assert mod.main() == 2


def test_valid_empty_archive_returns_0(mirror, monkeypatch):
    mod, v2, data, media = mirror
    _write_chunk_set(mod, data, {"x": [], "web": [], "ib": []})
    monkeypatch.setattr(sys, "argv", ["media-mirror.py"])
    assert mod.main() == 0
    assert json.loads(mod.MAP_JSON.read_text()) == {}


# --- 캐시 검증 ---

def test_corrupt_cache_is_rejected_and_redownloaded(mirror, monkeypatch):
    mod, v2, data, media = mirror
    url = "https://example.com/a.png"
    # 손상된 캐시 (헤더-only PNG).
    cached = media / mod.url_to_filename(url, ".png")
    cached.write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    assert mod.cached_image_ok(cached, ".png") is False
    # 유효한 캐시는 히트.
    cached.write_bytes(png_bytes(True))
    assert mod.cached_image_ok(cached, ".png") is True


def test_invalid_download_does_not_overwrite_cache(mirror, monkeypatch):
    mod, v2, data, media = mirror
    url = "https://example.com/b.png"
    dest = media / mod.url_to_filename(url, ".png")
    original = b"garbage-not-an-image"
    dest.write_bytes(original)

    class FakeResp:
        def geturl(self):
            return url

        @property
        def headers(self):
            return {"Content-Type": "image/png"}

        def read(self, n=-1):
            return png_bytes(False)  # 잘린 PNG (유효하지 않음)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    rel, info = mod.download_image(url)
    assert rel is None
    assert dest.read_bytes() == original  # 덮지 않음


def test_ordinary_download_failure_returns_1_and_writes_partial_map(mirror, monkeypatch):
    mod, v2, data, media = mirror
    good = "https://example.com/good.png"
    bad = "https://example.com/bad.png"
    _write_chunk_set(mod, data, {"x": [_post_with_image(good), _post_with_image(bad)],
                                 "web": [], "ib": []})

    class FakeResp:
        def __init__(self, url):
            self._url = url

        def geturl(self):
            return self._url

        @property
        def headers(self):
            return {"Content-Type": "image/png"}

        def read(self, n=-1):
            return png_bytes(True)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        if "bad" in req.full_url:
            raise OSError("connection reset")
        return FakeResp(req.full_url)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", ["media-mirror.py"])
    assert mod.main() == 1
    m = json.loads(mod.MAP_JSON.read_text())
    assert good in m
    assert bad not in m
