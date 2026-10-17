#!/usr/bin/env python3
"""
v2 미디어 미러 (media-mirror.py)

매니페스트가 지정한 posts 청크에서 이미지 URL을 스캔하고,
공개 아카이브 이미지를 v2/media/full/<sha256>.<ext>에 다운로드.
원본 URL → same-origin 상대 경로 매핑을 생성.

특성:
  - HTTPS 이미지 콘텐츠만 (리다이렉트 후에도 HTTPS 확인)
  - 래스터 MIME만 허용 (png/jpeg/webp/gif)
  - 크기 제한 (10MB), 시간 제한 (30초/파일)
  - 8 병렬 워커
  - 결정적: 완전한 이미지(헤더+트레일러)가 캐시에 있으면 스킵
  - 실패 보고 (실패한 URL 목록 출력)
  - 매핑을 원자적으로 기록 (js + data/media-map.json)
  - P2-9: 청크는 매니페스트에서 기대 목록을 확인하고, 공유 파이프라인
    락 하에서 일관된 스냅샷으로 읽는다. 누락·손상 청크가 있으면
    맵을 건드리지 않고 중단한다 (축소 방지).

사용법:
    python3 v2/media-mirror.py
    python3 v2/media-mirror.py --dry-run  # 다운로드 없이 URL 목록만

종료 코드: 0=맵 완성, 1=일부 URL 미매핑(다운로드 실패),
          2=청크/매니페스트 읽기 실패(맵을 건드리지 않고 중단 — P2-9 축소 방지)
캐시 검증: 매직 바이트(빠른 사전 검사) + 전체 프레임 검증(트레일러·크기)
          — nonempty-only·헤더-only 검사는 잘린 파일을 영구 cache hit으로 만든다.

출력:
    v2/media/full/<sha256>.<ext>  — 미러된 이미지
    v2/js/media-map.js             — window.ArchiveMediaMap
    v2/data/media-map.json         — 발행 스테이징용 매핑
"""
import hashlib
import html
import io
import json
import re
import struct
import sys
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline_lock import PipelineLockTimeout, pipeline_lock

V2_ROOT = Path(__file__).resolve().parent
DATA_DIR = V2_ROOT / "data"
MEDIA_DIR = V2_ROOT / "media" / "full"
MAP_JS = V2_ROOT / "js" / "media-map.js"
MAP_JSON = DATA_DIR / "media-map.json"

MAX_SIZE = 10 * 1024 * 1024  # 10MB
TIMEOUT = 30  # 초
WORKERS = 8

# 래스터 이미지 MIME만 허용
ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# 이미지 매직 바이트 (빠른 사전 검사 — 전체 검증은 is_complete_image).
IMAGE_MAGIC = {
    ".png": [bytes.fromhex("89504e470d0a1a0a")],
    ".jpg": [bytes.fromhex("ffd8ff")],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],
}

# 확장자 → Pillow 포맷 (is_complete_image의 포맷 대조용).
_EXT_FORMAT = {".png": "PNG", ".jpg": "JPEG", ".gif": "GIF", ".webp": "WEBP"}


def _magic_matches(data: bytes, ext: str) -> bool:
    """헤더 매직 바이트 사전 검사 (빠른 실패용 — 완전성 아님)."""
    if not data:
        return False
    for magic in IMAGE_MAGIC.get(ext, []):
        if data.startswith(magic):
            if ext == ".webp":
                return len(data) >= 12 and data[8:12] == b"WEBP"
            return True
    return False


def is_complete_image(data: bytes, ext: str) -> bool:
    """이미지 전체 검증 (P2-9 차단 4 재수정: 트레일러만으로는 부족하다).

    헤더/트레일러/크기 검사만으로는 중간 비트 손상을 통과시킨다
    (트레일러를 유지한 채 중간만 손상된 파일이 통과했음). 그래서
    Pillow로 모든 프레임까지 실제로 디코딩한다 — 잘림·중간 손상·
    손상된 헤더 테이블을 모두 거부한다. 디코딩 실패 = 유효하지 않음.
    포맷도 대조한다 (매직은 맞는데 내용이 다른 포맷이면 거부).
    """
    if not data or not _magic_matches(data[:12], ext):
        return False
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            if im.format != _EXT_FORMAT.get(ext):
                return False
            n_frames = getattr(im, "n_frames", 1)
            for i in range(n_frames):
                im.seek(i)
                im.load()
        return True
    except Exception:
        return False


def cached_image_ok(path: Path, ext: str) -> bool:
    """캐시 파일이 완전한 이미지인지 확인한다.

    매직이 틀리면 파일을 읽지 않고 False (빠른 실패). 매직이 맞으면
    전체를 읽어 Pillow로 전 프레임 디코딩까지 검증한다.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    if not _magic_matches(head, ext):
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return is_complete_image(data, ext)


class ChunkError(Exception):
    """청크/매니페스트 읽기 실패 — 맵 교체를 중단한다 (rc=2)."""


def load_expected_chunks():
    """매니페스트에서 기대 청크 목록을 읽고 검증한다.

    Returns {chunk_name: (manifest_relpath, sha256, count)}.
    매니페스트 없음/파싱 불가/구조 오류 시 ChunkError.
    """
    manifest_path = DATA_DIR / "manifest.json"
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ChunkError(f"manifest.json 읽기 실패: {e}")
    if not isinstance(m, dict):
        raise ChunkError("manifest.json이 dict가 아님")
    chunks = m.get("chunks")
    if not isinstance(chunks, dict):
        raise ChunkError("manifest.json에 chunks 없음")
    expected = {}
    for name in ("posts-x", "posts-web", "posts-ib"):
        entry = chunks.get(name)
        if not isinstance(entry, dict):
            raise ChunkError(f"manifest.json chunks.{name} 없음/형식 오류")
        f, h, c = entry.get("file"), entry.get("sha256"), entry.get("count")
        if not isinstance(f, str) or not f or f.startswith("/") or ".." in f.split("/"):
            raise ChunkError(f"manifest.json chunks.{name} file 형식 오류")
        if not isinstance(h, str) or len(h) != 64 or any(ch not in "0123456789abcdef" for ch in h):
            raise ChunkError(f"manifest.json chunks.{name} sha256 형식 오류 (64-hex 아님)")
        if type(c) is not int or c < 0:  # bool은 int의 서브클래스 — type()으로 거부
            raise ChunkError(f"manifest.json chunks.{name} count 형식 오류 (bool/비int)")
        expected[name] = (f, h, c)
    return expected


def read_chunk_snapshot(expected):
    """기대 청크들을 공유 락 하에서 일관된 스냅샷으로 읽는다.

    각 청크: 존재 확인 + 바이트 sha256 대조(매니페스트) + JSON 파싱 +
    {posts: [...]} 구조 + 모든 post가 dict인지 검증.
    하나라도 실패하면 ChunkError (맵을 건드리지 않고 중단).
    Returns [chunk_data, ...] (expected 순서).
    """
    try:
        with pipeline_lock():
            snapshot = []
            for name, (rel, want_hash, want_count) in expected.items():
                # rel은 v2/ 기준 상대 경로 (예: data/posts-x.json).
                path = V2_ROOT / rel
                try:
                    raw = path.read_bytes()
                except OSError as e:
                    raise ChunkError(f"청크 없음/읽기 실패 ({name} {rel}): {e}")
                got = hashlib.sha256(raw).hexdigest()
                if got != want_hash:
                    raise ChunkError(
                        f"청크 해시 불일치 ({name} {rel}): manifest와 다름 — "
                        f"빌드 중 교체됐거나 손상됨")
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    raise ChunkError(f"청크 파싱 실패 ({name} {rel}): {e}")
                if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
                    raise ChunkError(f"청크 구조 오류 ({name} {rel}): posts 목록 없음")
                if len(data["posts"]) != want_count:
                    raise ChunkError(
                        f"청크 count 불일치 ({name} {rel}): "
                        f"manifest {want_count}, 실제 {len(data['posts'])}")
                for post in data["posts"]:
                    if not isinstance(post, dict):
                        raise ChunkError(f"청크 구조 오류 ({name} {rel}): post가 dict가 아님")
                snapshot.append(data)
            return snapshot
    except PipelineLockTimeout as e:
        raise ChunkError(f"청크 스냅샷 락 타임아웃: {e}")


def extract_image_urls(chunks):
    """스냅샷 청크들에서 이미지 URL 추출 (html.unescape 적용)."""
    urls = set()
    for data in chunks:
        for post in data.get("posts", []):
            collect_post_urls(post, urls)
    return {u for u in urls if u.startswith("https://")}


def collect_post_urls(post, urls):
    """단일 post에서 이미지 URL 수집."""
    # 1. fullHtml의 <img src> (html.unescape)
    full_html = post.get("fullHtml", "") or ""
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', full_html):
        urls.add(html.unescape(m.group(1)))

    # 2. mediaImages 쌍 배열 (full 우선)
    for pair in post.get("mediaImages", []) or []:
        if isinstance(pair, dict):
            # full 우선, 없으면 thumb
            for key in ("full", "thumb", "url", "src"):
                if pair.get(key):
                    urls.add(html.unescape(pair[key]))
                    break
        elif isinstance(pair, (list, tuple)) and len(pair) >= 1:
            # [thumb, full] 형태 — full 우선 (ArchiveRender 계약)
            urls.add(html.unescape(str(pair[1] if len(pair)>1 and pair[1] else pair[0])))
        elif isinstance(pair, str):
            urls.add(html.unescape(pair))

    # 3. media 배열 (dict full/thumb 우선)
    for media in post.get("media", []) or []:
        if isinstance(media, dict):
            for key in ("full", "thumb", "url", "src", "media_url", "image_url"):
                if media.get(key):
                    urls.add(html.unescape(media[key]))
                    break
        elif isinstance(media, str):
            urls.add(html.unescape(media))

    # 4. 중첩 replies/quoted (재귀)
    for section in ("replies", "quoted"):
        items = post.get(section, []) or []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if isinstance(item, dict):
                collect_post_urls(item, urls)


def url_to_filename(url, mime_ext=None):
    """URL → <sha256>.<ext> 파일명."""
    sha = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    if mime_ext:
        return f"{sha}{mime_ext}"
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    ext = Path(path).suffix
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".jpg"
    if ext == ".jpeg":
        ext = ".jpg"
    return f"{sha}{ext}"


def download_image(url):
    """이미지 다운로드. (성공시 (rel_path, bytes), 실패시 (None, 이유))."""
    # Query-format URLs often have no extension. Check all MIME extensions before
    # opening the network so an existing PNG is not re-downloaded as a JPEG.
    for ext in set(ALLOWED_MIME.values()):
        cached_name=url_to_filename(url,ext)
        cached_path=MEDIA_DIR/cached_name
        # P2-9: nonempty-only/헤더-only 검사는 잘린 파일을 영구 cache hit으로
        # 만든다. 완전한 이미지(헤더+트레일러)만 캐시로 인정한다.
        if cached_path.exists() and cached_image_ok(cached_path, ext):
            return f"media/full/{cached_name}", "cached"

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Archive-Media-Mirror)",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            # 리다이렉트 후 최종 URL이 HTTPS인지 확인
            final_url = resp.geturl()
            if not final_url.startswith("https://"):
                return None, f"리다이렉트가 비-HTTPS: {final_url[:60]}"

            # MIME 확인 (래스터만)
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype not in ALLOWED_MIME:
                return None, f"미지원 MIME: {ctype}"

            # 크기 제한
            clen = resp.headers.get("Content-Length")
            if clen and int(clen) > MAX_SIZE:
                return None, f"너무 큼: {clen} bytes"

            data = resp.read(MAX_SIZE + 1)
            if len(data) > MAX_SIZE:
                return None, "10MB 초과"
            if len(data) == 0:
                return None, "빈 파일"

            # MIME 기반 확장자로 파일명 확정
            ext = ALLOWED_MIME[ctype]

            # P2-9: 다운로드한 바이트도 전체 프레임으로 검증 — 잘못된
            # 콘텐츠를 캐시에 저장하면 영구 cache hit이 된다.
            if not is_complete_image(data, ext):
                return None, f"이미지 불완전 (헤더/트레일러 불일치, {ctype})"

            filename = url_to_filename(url, ext)
            dest = MEDIA_DIR / filename
            rel_path = f"media/full/{filename}"

            # 이미 있으면 스킵 (전체 프레임 확인 — P2-9)
            if dest.exists() and cached_image_ok(dest, ext):
                return rel_path, "cached"

            temp=dest.with_suffix(dest.suffix+".tmp")
            temp.write_bytes(data)
            temp.replace(dest)
            return rel_path, len(data)
    except Exception as e:
        return None, str(e)[:100]


def main():
    dry_run = "--dry-run" in sys.argv

    # Pillow 없이는 전체 디코딩 검증을 할 수 없다 — 조용히 모든
    # 이미지를 거부하는 대신 맵 교체를 중단한다 (fail-closed).
    # Pillow가 없으면 is_complete_image가 전부 False를 반환해
    # 정상 매핑까지 제거되는 침묵 손상이 생기기 때문이다.
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("오류: Pillow 없음 — 이미지 전체 검증을 할 수 없어 맵 교체를 중단합니다.",
              file=sys.stderr)
        return 2

    print("이미지 URL 스캔 중...")
    try:
        expected = load_expected_chunks()
        chunks = read_chunk_snapshot(expected)
    except ChunkError as e:
        # P2-9: 청크/매니페스트 읽기 실패 시 맵 교체를 중단한다.
        # 실패한 청크의 URL이 빠진 채로 맵을 쓰면 정상 매핑까지
        # 제거되고 rc=0이 될 수 있다. 맵은 건드리지 않는다.
        print(f"오류: {e}", file=sys.stderr)
        print("청크 읽기 실패 — 맵 교체를 중단하고 기존 맵을 유지합니다.", file=sys.stderr)
        return 2
    urls = extract_image_urls(chunks)

    print(f"발견: {len(urls)}개 HTTPS 이미지 URL")

    if dry_run:
        for u in sorted(urls)[:20]:
            print(f"  {u}")
        if len(urls) > 20:
            print(f"  ... 외 {len(urls) - 20}개")
        return 0

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    url_map = {}
    failed = []
    total_bytes = 0
    cached = 0

    # 8 병렬 워커
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        results = list(executor.map(download_image, sorted(urls)))

    for url, (rel_path, info) in zip(sorted(urls), results):
        if rel_path:
            url_map[url] = rel_path
            if info == "cached":
                cached += 1
            else:
                total_bytes += info
        else:
            failed.append((url, info))

    # 원자적 기록: js + data/media-map.json
    map_data = json.dumps(url_map, ensure_ascii=False, indent=2)

    # 1. js 파일
    map_js = "// 자동 생성 (media-mirror.py) — 수정 금지\nwindow.ArchiveMediaMap = " + map_data + ";\n"
    tmp_js = MAP_JS.with_suffix(".tmp")
    tmp_js.write_text(map_js, encoding="utf-8")
    tmp_js.replace(MAP_JS)

    # 2. data/media-map.json (발행 스테이징용)
    tmp_json = MAP_JSON.with_suffix(".tmp")
    tmp_json.write_text(map_data, encoding="utf-8")
    tmp_json.replace(MAP_JSON)

    print(f"\n완료:")
    print(f"  미러됨: {len(url_map) - cached}개 ({total_bytes:,} bytes)")
    print(f"  캐시 (기존): {cached}개")
    print(f"  실패: {len(failed)}개")
    if failed:
        print("\n실패한 URL:")
        for url, msg in failed[:10]:
            print(f"  {url[:60]}... — {msg}")

    print(f"\n매핑 파일:")
    print(f"  {MAP_JS}")
    print(f"  {MAP_JSON}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
