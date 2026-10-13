#!/usr/bin/env python3
"""
v2 미디어 미러 (media-mirror.py)

v2/data/posts*.json에서 이미지 URL을 스캔하고,
공개 아카이브 이미지를 v2/media/full/<sha256>.<ext>에 다운로드.
원본 URL → same-origin 상대 경로 매핑을 생성.

특성:
  - HTTPS 이미지 콘텐츠만 (리다이렉트 후에도 HTTPS 확인)
  - 래스터 MIME만 허용 (png/jpeg/webp/gif)
  - 크기 제한 (10MB), 시간 제한 (30초/파일)
  - 8 병렬 워커
  - 결정적: 이미 존재하고 nonempty면 스킵
  - 실패 보고 (실패한 URL 목록 출력)
  - 매핑을 원자적으로 기록 (js + data/media-map.json)

사용법:
    python3 v2/media-mirror.py
    python3 v2/media-mirror.py --dry-run  # 다운로드 없이 URL 목록만

출력:
    v2/media/full/<sha256>.<ext>  — 미러된 이미지
    v2/js/media-map.js             — window.ArchiveMediaMap
    v2/data/media-map.json         — 발행 스테이징용 매핑
"""
import hashlib
import html
import json
import re
import sys
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


def extract_image_urls():
    """posts*.json에서 이미지 URL 추출 (html.unescape 적용)."""
    urls = set()

    for posts_file in DATA_DIR.glob("posts*.json"):
        try:
            data = json.loads(posts_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"경고: {posts_file.name} 읽기 실패: {e}", file=sys.stderr)
            continue

        posts = data.get("posts", []) if isinstance(data, dict) else data

        for post in posts:
            if not isinstance(post, dict):
                continue
            collect_post_urls(post, urls)

    # HTTPS만 필터
    https_urls = {u for u in urls if u.startswith("https://")}
    return https_urls


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
            # [full, thumb] 형태 — full 우선
            urls.add(html.unescape(str(pair[0])))
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
    # 파일명 사전 계산 (스킵 확인용)
    tmp_name = url_to_filename(url)
    tmp_dest = MEDIA_DIR / tmp_name
    if tmp_dest.exists() and tmp_dest.stat().st_size > 0:
        return f"media/full/{tmp_name}", "cached"

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
            filename = url_to_filename(url, ext)
            dest = MEDIA_DIR / filename
            rel_path = f"media/full/{filename}"

            # 이미 있으면 스킵 (nonempty 확인)
            if dest.exists() and dest.stat().st_size > 0:
                return rel_path, "cached"

            dest.write_bytes(data)
            return rel_path, len(data)
    except Exception as e:
        return None, str(e)[:100]


def main():
    dry_run = "--dry-run" in sys.argv

    print("이미지 URL 스캔 중...")
    urls = extract_image_urls()
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
    tmp_js.rename(MAP_JS)

    # 2. data/media-map.json (발행 스테이징용)
    tmp_json = MAP_JSON.with_suffix(".tmp")
    tmp_json.write_text(map_data, encoding="utf-8")
    tmp_json.rename(MAP_JSON)

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
