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
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
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

# 빌더 버전 (로직 변경 시 증분 → --check가 재빌드 감지)
BUILDER_VERSION = "1.0.0"

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


def load_posts():
    """
    posts.json 로드 및 검증

    Returns:
        list: 검증된 포스트 리스트

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
        with open(POSTS_JSON, encoding='utf-8') as f:
            posts = json.load(f)
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

    return valid_posts


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


def build_data():
    """
    메인 빌드 로직 (원자적 배포)

    1. 스테이징 디렉토리에 전체 생성
    2. 검증 (JSON 파싱 가능, manifest 해시 일치)
    3. 원자적 교체 (기존 → 백업, 스테이징 → 정식)
    4. 실패 시 이전 세대 유지
    """
    print("=== v2 데이터 빌드 시작 ===")

    # 1. posts.json 로드 및 검증
    print(f"\n[1/5] posts.json 읽기 및 검증...")
    try:
        posts = load_posts()
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

    print(f"  X: {len(posts_x)}개")
    print(f"  Web: {len(posts_web)}개")
    print(f"  IB: {len(posts_ib)}개")

    # 3. 스테이징 디렉토리에 생성
    print(f"\n[3/5] 스테이징 디렉토리에 생성...")
    staging_dir = None
    backup_dir = None  # try 블록 밖에서 초기화 (except 핸들러에서 안전하게 참조)
    try:
        staging_dir = Path(tempfile.mkdtemp(prefix="v2_build_"))
        staging_data = staging_dir / "data"
        staging_data.mkdir(parents=True)

        print(f"  스테이징: {staging_dir}")

        x_path = process_posts(posts_x, 'x', staging_data)
        web_path = process_posts(posts_web, 'web', staging_data)
        ib_path = process_posts(posts_ib, 'ib', staging_data)

        # 4. manifest 생성 (스테이징에)
        print(f"\n[4/5] manifest.json 생성...")

        # 소스 콘텐츠 해시 (변경 감지용)
        # posts.json 전체를 읽어서 해시 (결정적)
        with open(POSTS_JSON, 'rb') as f:
            source_hash = hashlib.sha256(f.read()).hexdigest()

        manifest = {
            "version": datetime.now(timezone.utc).isoformat(),
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
            "app_version": "2.0.0"
        }

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

        # 기존 백업 (있을 경우)
        if DATA_DIR.exists():
            backup_dir = V2_ROOT / "data.backup"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.move(str(DATA_DIR), str(backup_dir))
            print(f"  기존 data/ → data.backup/")

        # 스테이징 → 정식
        shutil.move(str(staging_data), str(DATA_DIR))
        print(f"  스테이징 → data/")

        # 미디어 디렉토리 생성 (비어 있음, Phase 2에서 사용)
        MEDIA_THUMB.mkdir(parents=True, exist_ok=True)
        MEDIA_FULL.mkdir(parents=True, exist_ok=True)

        # 백업 정리 (성공 시)
        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir)
            print(f"  백업 정리됨")

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

    with open(POSTS_JSON, 'rb') as f:
        current_hash = hashlib.sha256(f.read()).hexdigest()

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
