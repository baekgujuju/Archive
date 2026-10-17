#!/usr/bin/env python3
"""
v2 데이터 빌더
posts.json → data/*.json + media/ 이미지 파일

사용법:
    python3 build_data.py              # 전체 빌드
    python3 build_data.py --check      # 변경 감지 (exit 0=변경없음, 1=변경있음, 2=에러)

종료 코드:
    0: 성공 (또는 --check에서 변경 없음)
    1: --check에서 변경 감지됨
    2: 에러 발생
"""
import argparse
import importlib.util
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from pipeline_lock import pipeline_lock
from datetime import datetime, timezone
from urllib.parse import urlparse

# 경로 설정
V2_ROOT = Path(__file__).parent
DATA_DIR = V2_ROOT / "data"
MEDIA_THUMB = V2_ROOT / "media" / "thumb"
MEDIA_FULL = V2_ROOT / "media" / "full"

# 소스 데이터
XBOOKMARKS = Path.home() / "workspace" / "x-bookmarks"
POSTS_JSON = XBOOKMARKS / "posts.json"
# v1 웹 글 소스 (index.json, 예: Oliver Kell article-20261011-1048)
ARCHIVE_DIR = Path.home() / "workspace" / "article-archive"
WEB_INDEX_JSON = ARCHIVE_DIR / "index.json"
IB_LIBRARY_JSON = V2_ROOT / "ib-library.json"

# 빌더 버전 (로직 변경 시 증분 → --check가 재빌드 감지)
BUILDER_VERSION = "1.2.0"

# 빌드 시 data/ 교체에서 보존해야 할 파일 (스테이징에 복사).
# ai-tools 4종이 빠지면 매 빌드마다 enrich 상태가 소실됨 (2026-10-17 사고).
# media-map.json은 빌드가 재생성하지 않고 미디어 미러가 채우므로,
# 미러가 실패해도 이전 매핑이 사라지지 않게 보존한다.
ENRICH_STATE_FILES = ['ai-tools.json', 'ai-tools-cache.json',
                      'ai-tools-vision-cache.json', 'ai-tools-progress.json']
PRESERVED_FILES = ['translations.json', 'shared-state.json', 'media-map.json'] + ENRICH_STATE_FILES


def copy_preserved_files(src_dir, dst_dir, names):
    """Copy existing files from src_dir to dst_dir; returns copied names."""
    copied = []
    for name in names:
        src = Path(src_dir) / name
        if src.is_file():
            shutil.copy2(src, Path(dst_dir) / name)
            copied.append(name)
    return copied


def verify_preserved_files(data_dir, backup_dir, names):
    """After a data/ swap, restore missing preserved files from backup.

    Raises RuntimeError (backup kept) if a file is unrecoverable —
    the caller must NOT delete the backup on this path (fail-closed).
    is_file() (not exists()): a directory named like a state file is
    not a preserved file (copy2 would otherwise "succeed" into it).
    """
    for name in names:
        new_file = Path(data_dir) / name
        bak_file = Path(backup_dir) / name
        if not new_file.is_file() and bak_file.is_file():
            try:
                shutil.copy2(bak_file, new_file)
            except OSError as e:
                raise RuntimeError(f"보존 파일 복원 실패: {name}: {e}")
    for name in names:
        if (Path(backup_dir) / name).is_file() and not (Path(data_dir) / name).is_file():
            raise RuntimeError(f"보존 파일 소실: {name} (백업 유지됨)")


# P2 배치 1: 보존 세대 (rollback generation) 관리.
# 성공한 빌드도 이전 세대를 삭제하지 않고 data.backup.<stamp>로 보존한다.
# 세대 이름은 타임스탬프(UTC, 초 단위) + 충돌 시 일련번호.
# 정렬은 사전식이 아니라 (타임스탬프, 숫자 접미사) 순서이다 —
# 사전식 정렬은 .10을 .2보다 앞으로 두어 최신 세대를 지울 수 있다 (차단 7).
_GENERATION_RE = re.compile(r"^data\.backup\.(\d{8}T\d{6})(?:\.(\d+))?$")
RETAINED_GENERATIONS = 2


def _generation_sort_key(name):
    """세대 디렉터리 이름 → (타임스탬프, 숫자 접미사)."""
    m = _GENERATION_RE.match(name)
    if not m:
        return ("", 0)
    return (m.group(1), int(m.group(2)) if m.group(2) else 0)


def _archive_backup(backup_dir):
    """data.backup/을 타임스탬프 세대 디렉터리로 이동한다. Returns the new path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archived = V2_ROOT / f"data.backup.{stamp}"
    n = 0
    while archived.exists():
        n += 1
        archived = V2_ROOT / f"data.backup.{stamp}.{n}"
    shutil.move(str(backup_dir), str(archived))
    print(f"  이전 세대 보존: {archived.name} (삭제 아님)")
    return archived


def _prune_generations(keep=RETAINED_GENERATIONS):
    """보존 세대 중 최신 `keep`개만 남기고 나머지를 삭제한다.

    패턴에 정확히 일치하는 디렉터리만 대상으로 한다 (data.backup 본체,
    다른 이름의 디렉터리는 건드리지 않는다). 삭제 실패는 경고만 남기고
    빌드를 실패시키지 않는다 — 오래된 세대가 남는 것은 안전한 방향이다.
    """
    gens = sorted(
        (p for p in V2_ROOT.iterdir()
         if p.is_dir() and _GENERATION_RE.match(p.name)),
        key=lambda p: _generation_sort_key(p.name),
    )
    for old in gens[: max(0, len(gens) - keep)]:
        try:
            shutil.rmtree(old)
            print(f"  오래된 세대 정리: {old.name}")
        except OSError as e:
            print(f"  경고: 오래된 세대 정리 실패 ({old.name}): {e}", file=sys.stderr)


def source_fingerprint():
    """Include both article sources so web-only edits trigger a rebuild.

    IB 정본 소스(study_links, resource_library)도 포함 — 신규 IB 업로드가
    빌드를 트리거하도록 함. (ib_adapter.canonical_fingerprint)
    """
    hashes = {}
    for name, source in (("posts", POSTS_JSON), ("web", WEB_INDEX_JSON), ("ib", IB_LIBRARY_JSON)):
        hashes[name] = sha256_file(source) if source.exists() else None
    # 정본 IB 입력 해시 (텍스트 에셋 포함)
    try:
        from ib_adapter import canonical_fingerprint
        hashes["ib_canonical"] = canonical_fingerprint()
    except Exception as e:
        import sys
        print(f"  경고: IB 정본 해시 실패: {e}", file=sys.stderr)
        hashes["ib_canonical"] = None
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()

# 타입 판정용 상수
X_HOSTS = {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com'}
IB_CATEGORIES = {'IB', 'IB 자료', 'IB 주원'}


def sha256_file(path):
    """파일의 SHA-256 해시"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _read_json_bytes(path):
    """Read a JSON file once, returning (parsed, sha256 of the exact bytes).

    P1-5: the manifest's source hash and the pre-swap re-verification must
    describe the exact bytes that were parsed, not a later re-read.
    """
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return json.loads(raw.decode("utf-8")), digest


def load_posts():
    """
    posts.json 로드 및 검증

    Returns:
        (list, str): 검증된 포스트 리스트, 파싱한 바이트의 SHA-256

    Raises:
        FileNotFoundError: posts.json이 없을 때
        ValueError: JSON 파싱 실패 또는 구조 이상 시
    """
    if not POSTS_JSON.exists():
        raise FileNotFoundError(
            f"posts.json을 찾을 수 없습니다: {POSTS_JSON}\n"
            f"조치: x-bookmarks/posts.json 파일이 존재하는지 확인하세요."
        )

    try:
        posts, posts_hash = _read_json_bytes(POSTS_JSON)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"posts.json 파싱 실패 (line {e.lineno}, col {e.colno}): {e.msg}\n"
            f"조치: JSON 파일이 깨졌는지 확인하세요."
        )
    except UnicodeDecodeError as e:
        raise ValueError(
            f"posts.json 인코딩 오류: {e}\n"
            f"조치: 파일이 UTF-8로 저장되었는지 확인하세요."
        )

    if not isinstance(posts, list):
        raise ValueError(
            f"posts.json 루트가 list가 아닙니다 (실제: {type(posts).__name__})\n"
            f"조치: posts.json 구조를 확인하세요."
        )

    # 각 항목 검증
    valid_posts = []
    errors = []
    for i, post in enumerate(posts):
        if not isinstance(post, dict):
            errors.append(f"  [{i}] dict가 아님 (실제: {type(post).__name__})")
            continue

        # sourceUrl, category 타입 검증 (None 허용)
        source = post.get('sourceUrl')
        if source is not None and not isinstance(source, str):
            errors.append(f"  [{i}] sourceUrl이 문자열이 아님: {type(source).__name__}")
            continue

        category = post.get('category')
        if category is not None and not isinstance(category, str):
            errors.append(f"  [{i}] category가 문자열이 아님: {type(category).__name__}")
            continue

        valid_posts.append(post)

    if errors:
        print("경고: 일부 항목이 유효하지 않아 제외됨:", file=sys.stderr)
        for e in errors[:10]:  # 최대 10개만 표시
            print(e, file=sys.stderr)
        if len(errors) > 10:
            print(f"  ... 외 {len(errors) - 10}개", file=sys.stderr)

    return valid_posts, posts_hash


def load_web_articles():
    """
    v1 웹 글 소스 (article-archive/index.json) 로드.

    v1의 웹 카드(예: Oliver Kell article-20261011-1048)는 posts.json이 아닌
    별도 index.json에 있음. v2에서 누락되지 않도록 병합.

    Returns:
        (list, str|None): 정규화된 웹 글 리스트 (v2 스키마 호환),
        파싱한 바이트의 SHA-256 (인덱스 없음 시 None)
    """
    if not WEB_INDEX_JSON.exists():
        print(f"  웹 인덱스 없음 (건너뜀): {WEB_INDEX_JSON}", file=sys.stderr)
        return [], None

    data, web_hash = _read_json_bytes(WEB_INDEX_JSON)

    articles = data.get('articles', []) if isinstance(data, dict) else []
    normalized = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        # v2 스키마로 정규화 (원본 ID 보존)
        normalized.append({
            'id': a.get('id'),  # 원본 ID 보존 (예: article-20261011-1048)
            'tweetId': a.get('id'),  # 호환용
            'type': 'web',
            'title': a.get('title', ''),
            'author': a.get('author', ''),
            'source_url': a.get('source_url', ''),  # render.js는 웹글에 source_url 사용
            'sourceUrl': a.get('source_url', ''),  # 호환용
            'source_domain': a.get('source_domain', ''),
            'date_saved': a.get('date_saved', ''),
            'topics': a.get('topics', []) if isinstance(a.get('topics'), list) else [],
            'summary': a.get('summary', ''),
            'fullText': a.get('fullText', ''),
            'fullHtml': a.get('fullHtml', ''),
            'category': 'web',
        })
    return normalized, web_hash


def classify_post(post):
    """
    포스트 타입 판정

    규칙 (우선순위 순):
    1. sourceUrl 호스트가 X_HOSTS에 있으면 → 'x'
    2. category가 IB_CATEGORIES에 정확히 일치하면 → 'ib'
    3. 그 외 → 'web'

    Args:
        post: 검증된 포스트 dict

    Returns:
        str: 'x', 'web', 또는 'ib'
    """
    source = post.get('sourceUrl') or ''
    category = post.get('category') or ''

    # 1. X 판정: 호스트명 정확히 비교 (urlparse 사용)
    try:
        host = urlparse(source).netloc.lower()
        # 포트 번호 제거
        host = host.split(':')[0]
        if host in X_HOSTS:
            return 'x'
    except Exception:
        pass  # URL 파싱 실패 시 다음 규칙으로

    # 2. IB 판정: 카테고리 정확히 일치
    if category in IB_CATEGORIES:
        return 'ib'

    # 3. 기본값: 웹글
    return 'web'


def process_posts(post_list, name, staging_data_dir):
    """
    포스트 리스트를 JSON 파일로 저장 (스테이징 디렉토리에)

    이미지 처리 전략 (2026-10-13 결정):
    - 현재 posts.json의 media 필드는 원격 URL (pbs.twimg.com 등)
    - v2 Phase 1에서는 원격 URL을 그대로 유지 (오프라인 미지원)
    - v2 Phase 2에서 이미지 다운로드 + 로컬 저장 구현 예정
    - 이유: 98개 이미지 다운로드/변환은 별도 작업으로 분리 (안정성 우선)

    Args:
        post_list: 포스트 리스트
        name: 청크 이름 ('x', 'web', 'ib')
        staging_data_dir: 스테이징 데이터 디렉토리 Path

    Returns:
        Path: 생성된 JSON 파일 경로 (스테이징 내)
    """
    # 깊은 복사 없이 필요한 필드만 유지
    # (현재는 전체 복사, 나중에 이미지 URL → 로컬 경로 변환 시 수정)
    result = []
    for post in post_list:
        # dict 복사 (원본 수정 방지)
        new_post = dict(post)

        # TODO Phase 2: media 필드의 원격 URL → 로컬 파일 다운로드
        # - media: [{"type": "photo", "url": "https://pbs.twimg.com/..."}]
        # - 다운로드 후 media/thumb/, media/full/에 저장
        # - new_post['media'] = [{"thumb": "media/thumb/xxx.webp", "full": "media/full/xxx.webp"}]

        result.append(new_post)

    out_path = staging_data_dir / f"posts-{name}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"posts": result}, f, ensure_ascii=False, indent=2)

    size = out_path.stat().st_size
    print(f"  posts-{name}.json: {len(result)}개, {size/1024:.1f}KB")
    return out_path


def release_app_version():
    # Program release is independent of data rebuilds.
    release = json.loads((V2_ROOT / 'release.json').read_text(encoding='utf-8-sig'))
    value = release.get('app_version')
    if not isinstance(value, str) or not value:
        raise ValueError('release.json app_version missing')
    return value


def content_updated_at(source_hash, now):
    try:
        previous = json.loads((DATA_DIR / 'manifest.json').read_text(encoding='utf-8'))
        if previous.get('source_hash') == source_hash:
            return previous.get('content_updated_at') or previous.get('version') or now
    except (OSError, ValueError):
        pass
    return now


def _current_source_hashes():
    """현재 소스 파일들의 해시 dict (스냅샷과 동일 형식).

    P1-5: 교체 직전 재검증용. source_fingerprint()와 같은 조합이다.
    단, ib_canonical은 어댑션이 내장한 지문을 쓴다 — 어댑션 이후 입력이
    바뀌었다면 그 입력은 이 라이브러리를 만든 입력이 아니므로, 재계산한
    지문을 쓰면 오래된 라이브러리에 새 지문이 붙는다. 내장 지문이 없는
    구 라이브러리는 재계산으로 폴백한다.
    """
    hashes = {}
    for name, source in (("posts", POSTS_JSON), ("web", WEB_INDEX_JSON), ("ib", IB_LIBRARY_JSON)):
        hashes[name] = sha256_file(source) if source.exists() else None
    hashes["ib_canonical"] = _embedded_ib_fingerprint()
    return hashes


def _embedded_ib_fingerprint():
    """ib-library.json에 내장된 canonical fingerprint.

    - 라이브러리 있음 + 내장 있음 → 내장 지문 (출처 증명: 이 라이브러리를
      만든 입력의 지문).
    - 라이브러리 없음 → canonical_fingerprint() 재계산 (변경 감지 토큰.
      라이브러리가 없으면 IB 청크가 없으므로 출처 증명이 필요 없고,
      --check과 일치해야 한다).
    - 라이브러리 있음 + 내장 없음 → None. 현재 지문으로 대체하지 않는다.
      무지문 라이브러리는 어떤 입력으로 만들어졌는지 증명할 수 없으므로,
      현재 지문을 붙이면 오래된 라이브러리에 새 지문이 붙어 다음 --check이
      "변경 없음"으로 stale 청크를 잔존시킨다 (P1-5). 무지문이면 빌드가
      fail-closed 한다.
    """
    if not IB_LIBRARY_JSON.exists():
        try:
            from ib_adapter import canonical_fingerprint
            return canonical_fingerprint()
        except Exception as e:
            print(f"  경고: IB 정본 해시 실패: {e}", file=sys.stderr)
            return None
    try:
        data = json.loads(IB_LIBRARY_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(
                data.get("canonical_fingerprint"), str):
            return data["canonical_fingerprint"]
    except (OSError, ValueError):
        pass
    return None


def _snapshot_hash(snapshot):
    """스냅샷 dict -> manifest source_hash (source_fingerprint()와 동일 조합)."""
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def build_data():
    """메인 빌드 로직 (원자적 배포).

    P1-4: 전체 빌드를 공유 파이프라인 락 하에서 수행한다. 보존 복사부터
    교체·백업 정리까지, 다른 writer(enrich/translate/설정)의 저장이 끼어들어
    스테이징본이 새 갱신을 덮는 일을 막는다.
    """
    with pipeline_lock():
        _build_data_locked()


def _build_data_locked():
    """
    메인 빌드 로직 (원자적 배포)

    1. 스테이징 디렉토리에 전체 생성
    2. 검증 (JSON 파싱 가능, manifest 해시 일치)
    3. 원자적 교체 (기존 → 백업, 스테이징 → 정식)
    4. 실패 시 이전 세대 유지
    """
    print("=== v2 데이터 빌드 시작 ===")

    # P2 배치 1 (Codex 차단 7): 중단된 교체 복구. 이전 빌드가
    # data/ → data.backup/ 이동 후 staging → data/ 이동 전에 죽으면
    # live data/가 없고 data.backup/만 남는다. 다음 빌드가 이 상태로
    # 보존 파일을 복사하면 번역·공유상태·enrich 상태가 빠진 채로
    # 재구축된다. data/가 없고 data.backup/이 있으면, live 상태를
    # 읽기 전에 백업을 복원한다 (명백한 중단 트랜잭션 — 의미론적
    # 롤백 승인이 필요 없는 복구이다).
    _transient_backup = V2_ROOT / "data.backup"
    if not DATA_DIR.exists() and _transient_backup.exists():
        print("  중단된 교체 감지: data.backup/ → data/ 복원")
        shutil.move(str(_transient_backup), str(DATA_DIR))

    # 1. posts.json 로드 및 검증 (+ 파싱한 바이트의 해시를 스냅샷으로 기록)
    print(f"\n[1/5] posts.json 읽기 및 검증...")
    try:
        posts, posts_hash = load_posts()
    except (FileNotFoundError, ValueError) as e:
        print(f"에러: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"  총 {len(posts)}개 포스트 (유효)")

    # 2. 타입별 분리
    print(f"\n[2/5] 타입별 분리...")
    posts_x = []
    posts_web = []
    posts_ib = []

    for post in posts:
        ptype = classify_post(post)
        if ptype == 'x':
            posts_x.append(post)
        elif ptype == 'ib':
            posts_ib.append(post)
        else:
            posts_web.append(post)

    ib_hash = None
    ib_embedded = None
    if IB_LIBRARY_JSON.exists():
        try:
            library, ib_hash = _read_json_bytes(IB_LIBRARY_JSON)
        except ValueError as e:
            print(f"에러: {e}", file=sys.stderr)
            sys.exit(2)
        if isinstance(library, dict) and isinstance(
                library.get("canonical_fingerprint"), str):
            # 파싱한 바로 그 라이브러리의 내장 지문 (재읽기 아님).
            ib_embedded = library["canonical_fingerprint"]
        else:
            # 무지문 라이브러리: 어떤 입력으로 만들어졌는지 증명 불가.
            # 현재 지문으로 대체하지 않고 fail-closed (어댑터를 먼저 실행).
            print("에러: ib-library.json에 canonical_fingerprint 없음 — "
                  "ib_adapter.py를 먼저 실행해 지문을 내장하세요",
                  file=sys.stderr)
            sys.exit(2)
        existing = {str(p.get("id") or p.get("tweetId") or "") for p in posts_ib}
        for record in library.get("posts", []):
            if isinstance(record, dict) and str(record.get("id") or "") not in existing:
                posts_ib.append(record)

    print(f"  X: {len(posts_x)}개")
    print(f"  Web: {len(posts_web)}개 (posts.json)")
    print(f"  IB: {len(posts_ib)}개")

    # 2b. v1 웹 글 병합 (index.json)
    web_articles, web_hash = load_web_articles()
    if web_articles:
        posts_web.extend(web_articles)
        print(f"  Web 추가 (index.json): {len(web_articles)}개")
    print(f"  Web 합계: {len(posts_web)}개")

    # P1-5 스냅샷: 파싱한 정확한 소스 바이트의 해시. manifest source_hash와
    # 교체 직전 재검증이 이 스냅샷을 기준으로 한다 (나중 재읽기 아님).
    snapshot = _current_source_hashes()
    snapshot["posts"] = posts_hash
    snapshot["web"] = web_hash
    snapshot["ib"] = ib_hash
    # ib_canonical은 파싱한 라이브러리의 내장 지문으로 덮는다 (재읽기 아님).
    # 내장이 없으면 _current_source_hashes()의 값(재계산 폴백)을 유지한다.
    if ib_embedded is not None:
        snapshot["ib_canonical"] = ib_embedded

    # 3. 스테이징 디렉토리에 생성
    print(f"\n[3/5] 스테이징 디렉토리에 생성...")
    staging_dir = None
    backup_dir = None  # try 블록 밖에서 초기화 (except 핸들러에서 안전하게 참조)
    swapped = False  # 스테이징 -> data/ 교체 + 보존 검증 완료 여부
    rolled_back = False  # 검증 실패 후 백업 세대로 롤백 완료 여부
    backup_cleaned = False  # 성공 시 백업 삭제 완료 여부
    try:
        staging_dir = Path(tempfile.mkdtemp(prefix="v2_build_", dir=V2_ROOT))
        staging_data = staging_dir / "data"
        staging_data.mkdir(parents=True)

        print(f"  스테이징: {staging_dir}")

        x_path = process_posts(posts_x, 'x', staging_data)
        web_path = process_posts(posts_web, 'web', staging_data)
        ib_path = process_posts(posts_ib, 'ib', staging_data)

        # Keep paragraph translations when the atomic data directory is replaced.
        translations = DATA_DIR / 'translations.json'
        if translations.exists():
            shutil.copy2(translations, staging_data / 'translations.json')
        # Enrich state MUST survive a rebuild: build_data.py replaces the
        # whole data/ directory (data/ -> data.backup/ -> deleted). Until
        # 2026-10-17 the ai-tools files were not carried over, so every
        # build wiped them and enrich rebuilt from scratch (152 -> 40).
        copied = copy_preserved_files(DATA_DIR, staging_data, ENRICH_STATE_FILES)
        for name in copied:
            print(f"  enrich 상태 보존: {name}")
        shared_state = DATA_DIR / 'shared-state.json'
        if shared_state.exists():
            spec = importlib.util.spec_from_file_location('archive_settings_validator', Path(__file__).parent / 'hooks' / 'publish_settings.py')
            validator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(validator)
            validator.validate(json.loads(shared_state.read_text(encoding='utf-8')))
            shutil.copy2(shared_state, staging_data / 'shared-state.json')
        # 4. manifest 생성 (스테이징에)
        print(f"\n[4/5] manifest.json 생성...")

        # 소스 콘텐츠 해시 (변경 감지용)
        # P1-5: 파싱한 스냅샷의 해시를 쓴다. 여기서 다시 읽으면,
        # 로드 이후 바뀐 소스의 해시가 오래된 청크에 붙는 TOCTOU가 된다.
        source_hash = _snapshot_hash(snapshot)

        manifest = {
            "version": datetime.now(timezone.utc).isoformat(),
            "content_updated_at": content_updated_at(source_hash, datetime.now(timezone.utc).isoformat()),
            "build": "v2",
            "source_hash": source_hash,  # 변경 감지용
            "builder_version": BUILDER_VERSION,  # 빌더 버전 (로직 변경 시 증분)
            "chunks": {
                "posts-x": {
                    "file": "data/posts-x.json",
                    "sha256": sha256_file(x_path),
                    "count": len(posts_x)
                },
                "posts-web": {
                    "file": "data/posts-web.json",
                    "sha256": sha256_file(web_path),
                    "count": len(posts_web)
                },
                "posts-ib": {
                    "file": "data/posts-ib.json",
                    "sha256": sha256_file(ib_path),
                    "count": len(posts_ib)
                },
            },
            "app_version": release_app_version()
        }

        if translations.exists():
            manifest['translations'] = {'file': 'data/translations.json', 'sha256': sha256_file(translations)}
        if shared_state.exists():
            shared = json.loads(shared_state.read_text(encoding='utf-8'))
            manifest['shared_state'] = {'file': 'data/shared-state.json', 'sha256': sha256_file(shared_state), 'revision': shared['revision']}
        # Carry over the enrich manifest entry (refreshed by enrich's
        # update_manifest after each run; without this a rebuild drops it).
        old_manifest = DATA_DIR / 'manifest.json'
        if old_manifest.exists():
            try:
                old_ai = json.loads(old_manifest.read_text(encoding='utf-8')).get('ai_tools')
                if isinstance(old_ai, dict):
                    manifest['ai_tools'] = old_ai
            except (OSError, ValueError):
                pass
        manifest_path = staging_data / "manifest.json"
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        print(f"  manifest.json 생성됨")
        print(f"  source_hash: {source_hash[:16]}...")

        # 5. 검증: 생성된 JSON이 파싱 가능한지
        print(f"\n[5/5] 검증...")
        for chunk_file in [x_path, web_path, ib_path, manifest_path]:
            try:
                with open(chunk_file, encoding='utf-8') as f:
                    json.load(f)
                print(f"  ✓ {chunk_file.name} 파싱 OK")
            except Exception as e:
                raise ValueError(f"{chunk_file.name} 검증 실패: {e}")

        # manifest 해시 검증
        with open(manifest_path, encoding='utf-8') as f:
            m = json.load(f)
        for chunk_name, chunk_info in m["chunks"].items():
            chunk_path = staging_data / Path(chunk_info["file"]).name
            actual_hash = sha256_file(chunk_path)
            if actual_hash != chunk_info["sha256"]:
                raise ValueError(
                    f"{chunk_name} 해시 불일치: "
                    f"기대 {chunk_info['sha256'][:16]}..., "
                    f"실제 {actual_hash[:16]}..."
                )
        print(f"  ✓ manifest 해시 일치")

        # 6. 원자적 교체
        print(f"\n[6/6] 원자적 교체...")

        # P1-5: 교체 직전 소스 재검증. 로드 이후 소스가 바뀌었으면
        # 오래된 청크에 새 해시를 붙이지 않고 교체 없이 중단한다.
        # (다음 폴이 새 소스로 처음부터 다시 빌드한다.)
        current_snapshot = _current_source_hashes()
        if current_snapshot != snapshot:
            changed = [k for k in snapshot if snapshot.get(k) != current_snapshot.get(k)]
            raise ValueError(
                "소스가 빌드 중 변경됨 — 교체 없이 중단: "
                f"{', '.join(changed)} (다음 폴이 새 소스로 재빌드)"
            )

        # 기존 백업 (있을 경우)
        if DATA_DIR.exists():
            backup_dir = V2_ROOT / "data.backup"
            if backup_dir.exists():
                # 이전 실행이 남긴 백업은 절대 삭제하지 않는다 — 유일한
                # 복구본일 수 있다. 타임스탬프 붙여 보관하고 진행한다.
                # (P2 배치 1: 세대 보존 — 아래 성공 경로와 동일한 이름 체계,
                # prune_generations가 최근 2세대만 유지한다.)
                _archive_backup(backup_dir)
            shutil.move(str(DATA_DIR), str(backup_dir))
            print(f"  기존 data/ → data.backup/")

        # 스테이징 → 정식
        shutil.move(str(staging_data), str(DATA_DIR))
        swapped = True  # 교체 완료 — 이후 실패는 새 세대 live (검증과 분리)
        print(f"  스테이징 → data/")

        # 교체 후 검증: 보존 대상 파일이 새 data/에 없으면 백업에서 복원.
        # 검증 실패 시 실패한 세대(DATA_DIR)를 버리고 백업을 복원한 뒤
        # 실패 종료 — 실패 세대를 live로 남겨두면 다음 빌드가 백업을
        # 지워버려 유일한 복구본이 사라진다 (fail-closed + rollback).
        if backup_dir and backup_dir.exists():
            try:
                verify_preserved_files(DATA_DIR, backup_dir, PRESERVED_FILES)
            except RuntimeError:
                # 실패 세대 제거 후 백업 복원. rmtree 실패 시 move가
                # 백업을 실패 세대 안으로 넣는 오염을 막기 위해
                # ignore_errors 없이, 실패하면 원본 오류를 그대로 전파.
                try:
                    shutil.rmtree(DATA_DIR)
                    shutil.move(str(backup_dir), str(DATA_DIR))
                    rolled_back = True
                    print("검증 실패 — 백업 세대로 롤백됨", file=sys.stderr)
                except OSError as rollback_e:
                    print(f"롤백 실패 (수동 복구 필요): {rollback_e}", file=sys.stderr)
                    print(f"  live: {DATA_DIR}, 백업: {backup_dir}", file=sys.stderr)
                raise
        # (swapped는 교체 직후 이미 True — 검증 이후 실패도 새 세대 live)

        # 미디어 디렉토리 생성 (비어 있음, Phase 2에서 사용)
        MEDIA_THUMB.mkdir(parents=True, exist_ok=True)
        MEDIA_FULL.mkdir(parents=True, exist_ok=True)

        # 백업 정리 (성공 시) — P2 배치 1: 이전 세대를 삭제하지 않고
        # 타임스탬프 세대 디렉터리로 보존하고 최근 2세대만 유지한다.
        # 검증 통과했지만 의미론적으로 잘못된 콘텐츠가 live가 됐을 때의
        # 롤백 대상이다 (의미론적 롤백 자체는 운영자 승인 필요).
        if backup_dir and backup_dir.exists():
            _archive_backup(backup_dir)
            _prune_generations(keep=2)
            backup_cleaned = True
            print(f"  이전 세대 보존됨 (최근 2세대 유지)")

        # 스테이징 루트 정리
        staging_dir.rmdir()

        print(f"\n=== 빌드 완료 ===")
        print(f"데이터 디렉토리: {DATA_DIR}")
        print(f"미디어 디렉토리: {V2_ROOT / 'media'}")

    except Exception as e:
        print(f"\n에러 발생: {e}", file=sys.stderr)
        # 백업 복원: DATA_DIR 이동 성공 후 스테이징 이동 실패 시
        if backup_dir and backup_dir.exists() and not DATA_DIR.exists():
            try:
                shutil.move(str(backup_dir), str(DATA_DIR))
                print("백업에서 복원됨.", file=sys.stderr)
            except Exception as restore_e:
                print(f"백업 복원 실패: {restore_e}", file=sys.stderr)
                print(f"수동 복원 필요: {backup_dir} → {DATA_DIR}", file=sys.stderr)
        elif rolled_back:
            print("백업 세대로 롤백되어 이전 세대가 live입니다.", file=sys.stderr)
        elif swapped and backup_cleaned:
            print(f"새 세대가 live입니다 (백업 정리됨). 실패 단계: 후처리. data/: {DATA_DIR}", file=sys.stderr)
        elif swapped:
            # 교체 완료 후 실패 — 새 세대가 live. 백업은 있을 때만 유지됨을
            # 명시한다 (최초 빌드는 백업 자체가 없음).
            print(f"새 세대가 live 상태: {DATA_DIR}", file=sys.stderr)
            if backup_dir and backup_dir.exists():
                print(f"백업 유지됨 (수동 확인 필요): {backup_dir}", file=sys.stderr)
            else:
                print("백업 없음 (최초 빌드 또는 백업 미생성).", file=sys.stderr)
        else:
            print("이전 세대를 유지합니다.", file=sys.stderr)
        # 스테이징 정리
        if staging_dir and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        sys.exit(2)


def check_changes():
    """
    변경 감지

    manifest의 source_hash와 현재 posts.json의 해시를 비교

    Returns:
        int: 0=변경없음, 1=변경있음, 2=에러
    """
    manifest_path = DATA_DIR / "manifest.json"

    if not manifest_path.exists():
        print("manifest.json 없음 - 빌드 필요")
        return 1

    try:
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"manifest.json 파싱 실패: {e}", file=sys.stderr)
        return 2

    # manifest 구조 검증
    if "source_hash" not in manifest:
        print("manifest.json에 source_hash 없음 (구버전) - 빌드 필요")
        return 1

    if "builder_version" not in manifest:
        print("manifest.json에 builder_version 없음 (구버전) - 빌드 필요")
        return 1

    # 빌더 버전 비교 (로직 변경 시 재빌드 필요)
    if manifest["builder_version"] != BUILDER_VERSION:
        print(
            f"빌더 버전 변경됨 "
            f"({manifest['builder_version']} → {BUILDER_VERSION}) - 빌드 필요"
        )
        return 1

    # 소스 해시 비교
    if not POSTS_JSON.exists():
        print(f"posts.json 없음: {POSTS_JSON}", file=sys.stderr)
        return 2

    current_hash = source_fingerprint()

    if current_hash == manifest["source_hash"]:
        print("변경 없음")
        return 0
    else:
        print("변경 감지됨 - 빌드 필요")
        print(f"  이전: {manifest['source_hash'][:16]}...")
        print(f"  현재: {current_hash[:16]}...")
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='v2 데이터 빌더 (posts.json → data/*.json)'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='변경 감지 (exit 0=변경없음, 1=변경있음, 2=에러)'
    )
    args = parser.parse_args()

    if args.check:
        sys.exit(check_changes())
    else:
        build_data()
