#!/usr/bin/env python3
"""
v2 업로드-성공 후크: 빌드 → 번역 → 커밋 (게시는 기본 비활성)

동작:
  0. 미푸시 커밋/Dirty data/ 확인 (소스 해시와 무관하게 처리)
  1. flock排他 락으로 동시 실행 방지
  2. build_data.py --check 로 변경 감지
  3. 변경 있으면: ib_adapter 병합 → translations.json 백업 → 전체 빌드
     → translate_posts.py → manifest.translations.sha256 업데이트
     → git 변경 파일 확인 → 명시적 pathspec 커밋
  4. 번역 실패 시 재시도 마커 생성 (다음 실행에서 재시도)
  5. 게시(git push)는 기본 비활성. V2_PUBLISH=1 일 때만.

종료 코드: 0=성공/변경없음, 1=실패(재시도 필요), 2=락 획득 실패
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from proc_group import run_grouped, terminate_current

# pipeline_lock lives in v2/ (parent dir), not hooks/ — same bootstrap as drive_poll.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_lock import PipelineLockTimeout, pipeline_lock  # noqa: E402

V2_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = V2_ROOT / "data"
HOOKS_DIR = V2_ROOT / "hooks"
LOCK_FILE = HOOKS_DIR / ".post_ingest.lock"
TRANSLATIONS = DATA_DIR / "translations.json"
MANIFEST = DATA_DIR / "manifest.json"
RETRY_TRANSLATION = HOOKS_DIR / ".retry_translation"
RETRY_COMMIT = HOOKS_DIR / ".retry_commit"
RETRY_MEDIA = HOOKS_DIR / ".retry_media"
RETRY_ENRICH = HOOKS_DIR / ".retry-enrich"
COMMIT_PATHS = ["data/", "media/", "ib-library.json"]

PUBLISH_ENABLED = os.environ.get("V2_PUBLISH", "") == "1"
PUBLISH_BRANCH = os.environ.get("V2_PUBLISH_BRANCH", "")


def log(msg):
    print(f"[hook {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def _on_sigterm(signum, frame):
    """P1-7: monitor가 후크 그룹에 SIGTERM을 보내면, 현재 자식 그룹부터
    종료시키고 후크를 끝낸다. (SIGKILL만으로는 자손이 고아로 남는다.)
    진입 즉시 SIGTERM을 무시해 정리 도중 재진입을 막는다."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    terminate_current(grace=15)
    raise SystemExit(143)


try:
    # import 시점(테스트 등 비-메인 스레드)에서는 설치 불가 — 그 경우 스킵.
    signal.signal(signal.SIGTERM, _on_sigterm)
except ValueError:
    pass


def run(cmd, **kw):
    log("$ " + " ".join(str(c) for c in cmd))
    # P1-7: 자식은 별도 프로세스 그룹으로 실행하고, 타임아웃 시 그룹 전체를
    # 종료한다 (subprocess.run은 직접 자식만 죽여 손자를 고아로 남긴다).
    r = run_grouped(cmd, capture_output=True, text=True,
                    timeout=kw.pop("timeout", 600), **kw)
    if r.stdout:
        print(r.stdout[-2000:])
    if r.returncode != 0:
        log(f"실패 (exit {r.returncode}): {r.stderr[-1000:]}")
    return r


def git(args, **kw):
    return run(["git", "-C", str(V2_ROOT)] + args, **kw)


def git_bytes(args, **kw):
    """git 출력을 바이트로 반환 (해시 검증용 — text 디코딩 왜곡 방지)."""
    r = run_grouped(["git", "-C", str(V2_ROOT)] + args,
                     capture_output=True, text=False,
                     timeout=kw.pop("timeout", 600), **kw)
    return r


def current_branch():
    r = git(["rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode != 0:
        # P2-10: Git 실패를 빈 브랜치로 취급하면 "게시 중단"이 아니라
        # 잘못된 분기로 푸시하거나 미푸시 커밋을 0개로 오판한다.
        raise RuntimeError(f"git rev-parse 실패: {r.stderr[-500:]}")
    branch = r.stdout.strip()
    if branch == "HEAD" or not branch:
        # detached HEAD는 자동 게시 대상이 아니다 (어느 브랜치로
        # 푸시해야 할지 알 수 없고, 검증한 커밋과 다른 커밋이
        # 게시될 수 있다).
        raise RuntimeError("detached HEAD — 자동 게시 불가")
    return branch


def ref_exists(ref):
    """ref 존재 여부 (예: refs/remotes/origin/main).

    Returns True(존재) / False(없음). 그 외의 Git 실패는 raise한다
    (인프라 실패를 "없음"으로 오판하지 않는다).
    show-ref --verify는 없는 원격 ref에 128을 반환하므로,
    rev-parse --verify -q를 사용한다 (없음 → 1, 존재 → 0).
    """
    r = git(["rev-parse", "--verify", "-q", ref])
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    raise RuntimeError(f"git rev-parse --verify 실패 ({ref}): {r.stderr[-500:]}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def changed_files():
    """porcelain v1 정확 파싱 (data/, media/ 한정).

    P2-10: Git 실패 시 []를 반환하면 "변경 없음"으로 오판해 커밋을
    건너뛰고 성공으로 끝난다 — 실패를 raise한다 (fail-closed).
    """
    r = git(["status", "--porcelain=v1", "-z", "--", "data/", "media/", "ib-library.json"])
    if r.returncode != 0:
        raise RuntimeError(f"git status 실패: {r.stderr[-500:]}")
    files = []
    for entry in r.stdout.split("\x00"):
        if len(entry) < 4:
            continue
        xy = entry[:2]
        path = entry[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if xy.strip():
            files.append(path)
    return files


def unpushed_commit_list():
    """origin/<branch>..HEAD 커밋 SHA 목록 (오래된 순).

    게시 기준선(origin/<branch>)이 없으면 fail-closed로 raise한다:
    "상류 없음 = 미푸시 0개"로 취급하면 검증 없이 전체 히스토리를
    게시할 수 있다. 첫 게시의 기준선은 운영자가 명시적으로 만든다
    (예: 해당 브랜치를 한 번 수동 push).
    """
    branch = PUBLISH_BRANCH or current_branch()
    if not ref_exists(f"refs/remotes/origin/{branch}"):
        raise RuntimeError(
            f"게시 기준선 없음: origin/{branch}가 없습니다. "
            f"해당 브랜치를 한 번 수동으로 push해 기준선을 만드세요 "
            f"(예: git push origin HEAD:refs/heads/{branch})"
        )
    r = git(["rev-list", "--reverse", f"origin/{branch}..HEAD"])
    if r.returncode != 0:
        raise RuntimeError(f"git rev-list 실패: {r.stderr[-500:]}")
    return [line for line in r.stdout.splitlines() if line.strip()]


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _manifest_relpath(path):
    """매니페스트 file 경로를 검증하고 Git 경로(v2/ 상대)로 변환한다.

    매니페스트 경로는 v2/ 기준 상대 경로이다. 절대 경로·`..` 탈출·
    빈 경로는 거부한다. Returns `v2/<path>` 또는 None(거부).
    """
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("/") or path.startswith("\\"):
        return None
    parts = PurePosixPath(path).parts
    if not parts or any(p in ("..", ".", "") for p in parts):
        return None
    return "v2/" + path


def _git_show_bytes(commit, git_path):
    """git show <commit>:<path>를 바이트로 반환한다.

    Returns (data, None) 성공 시, (None, 'missing') 해당 경로가 커밋에
    없을 때 (콘텐츠 실패), 그 외 Git 실패는 RuntimeError를 raise한다
    (인프라 실패를 콘텐츠 실패로 오판하지 않는다).
    """
    r = git_bytes(["show", f"{commit}:{git_path}"])
    if r.returncode == 0:
        return r.stdout, None
    err = (r.stderr or b"").decode("utf-8", "replace")
    if "does not exist" in err:
        return None, "missing"
    raise RuntimeError(f"git show 실패 ({commit[:12]}:{git_path}): {err[-300:]}")


def _validate_manifest_dict(m, short):
    """매니페스트 구조 검증 (커밋 검증 + 번역 해시 업데이트 공유).

    validate_commit_artifacts와 update_manifest_translation_hash가
    같은 기준을 쓰지 않으면 한쪽이 통과시킨 비정형 매니페스트를
    다른 쪽이 성공처럼 처리한다. Returns (ok, reason).
    - chunks: posts-x/posts-web/posts-ib 필수, 각 file(유효한 v2-상대
      경로) + sha256(64-hex) + count(정확히 int, bool 거부, >= 0).
    - 선택 섹션 translations/shared_state/ai_tools: 있으면 dict +
      file/sha256 유효 + count가 있으면 정확히 int >= 0.
    """
    if not isinstance(m, dict):
        return False, f"manifest.json이 dict가 아님 ({short})"
    chunks = m.get("chunks")
    if not isinstance(chunks, dict):
        return False, f"manifest.json에 chunks 없음 ({short})"
    for name in ("posts-x", "posts-web", "posts-ib"):
        entry = chunks.get(name)
        if not isinstance(entry, dict):
            return False, f"manifest.json chunks.{name} 없음/형식 오류 ({short})"
        f, h, c = entry.get("file"), entry.get("sha256"), entry.get("count")
        if _manifest_relpath(f) is None:
            return False, f"manifest.json chunks.{name} file 경로 형식 오류 ({short})"
        if not isinstance(h, str) or not _HEX64.match(h):
            return False, f"manifest.json chunks.{name} sha256 형식 오류 ({short})"
        if type(c) is not int or c < 0:  # bool은 int의 서브클래스 — type()으로 거부
            return False, f"manifest.json chunks.{name} count 형식 오류 ({short})"
    for section in ("translations", "shared_state", "ai_tools"):
        entry = m.get(section)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            return False, f"manifest.json {section} 형식 오류 ({short})"
        f, h = entry.get("file"), entry.get("sha256")
        if _manifest_relpath(f) is None:
            return False, f"manifest.json {section} file 경로 형식 오류 ({short})"
        if not isinstance(h, str) or not _HEX64.match(h):
            return False, f"manifest.json {section} sha256 형식 오류 ({short})"
        if "count" in entry and (type(entry["count"]) is not int or entry["count"] < 0):
            return False, f"manifest.json {section} count 형식 오류 ({short})"
    return True, ""


def validate_commit_artifacts(commit):
    """푸시 전 커밋 아티팩트 검증 (P2-10).

    커밋 안의 manifest.json이 파싱되고, 매니페스트 구조가 올바르며,
    기록된 모든 file+sha256이 커밋된 바이트와 일치하는지 확인한다.
    청크는 파싱해 posts 목록 길이와 count도 대조한다.
    Returns (True, '') 또는 (False, reason). Git 인프라 실패는
    RuntimeError를 raise한다 (재시도 대상, 검수 대상이 아님).
    """
    short = commit[:12]
    data, missing = _git_show_bytes(commit, "v2/data/manifest.json")
    if missing:
        return False, f"manifest.json 없음 ({short})"
    try:
        m = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, f"manifest.json 파싱 실패 ({short})"
    ok, reason = _validate_manifest_dict(m, short)
    if not ok:
        return False, reason
    chunks = m["chunks"]
    pairs = []  # (manifest_path, sha256, chunk_name_or_None)
    for name in ("posts-x", "posts-web", "posts-ib"):
        entry = chunks[name]
        pairs.append((entry["file"], entry["sha256"], name))
    for section in ("translations", "shared_state", "ai_tools"):
        entry = m.get(section)
        if entry is None:
            continue
        pairs.append((entry["file"], entry["sha256"], None))

    for path, want, chunk_name in pairs:
        git_path = _manifest_relpath(path)
        if git_path is None:
            return False, f"{path} 경로 형식 오류 ({short})"
        data, missing = _git_show_bytes(commit, git_path)
        if missing:
            return False, f"{path} 없음 ({short})"
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            return False, f"{path} 해시 불일치 ({short})"
        if chunk_name is not None:
            # 청크 내용 구조 + count 대조.
            try:
                chunk = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return False, f"{path} 파싱 실패 ({short})"
            if not isinstance(chunk, dict) or not isinstance(chunk.get("posts"), list):
                return False, f"{path} 구조 오류 (posts 목록 없음) ({short})"
            if not all(isinstance(p, dict) for p in chunk["posts"]):
                return False, f"{path} 구조 오류 (post가 dict가 아님) ({short})"
            want_count = chunks[chunk_name]["count"]
            if len(chunk["posts"]) != want_count:
                return False, f"{path} count 불일치 (manifest {want_count}, 실제 {len(chunk['posts'])}) ({short})"
    return True, ""


def flag_needs_review(commit, reason):
    """검증 실패 커밋을 검수 대상으로 기록한다 (배치 3 검수 큐의 입력).

    이미 커밋된 아티팩트는 재시도로 고쳐지지 않으므로, 푸시를 막고
    운영자 개입을 기다린다. 실패한 커밋을 덮어쓰거나 삭제하지 않는다.
    (commit, reason) 중복은 기록하지 않는다. 마커 파일이 손상됐으면
    격리하고 새로 시작한다 (조용히 교체하지 않는다).
    인프라 실패(Git 명령 오류)는 여기로 오지 않는다 — raise되어
    재시도 대상이 된다.
    """
    marker = HOOKS_DIR / ".needs-review"
    entries = []
    if marker.exists():
        try:
            entries = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("not a list")
        except (OSError, ValueError):
            # 손상된 마커 격리: 고유 목적지를 예약(mkstemp, O_EXCL)하고
            # 원본 바이트를 복사한 뒤 원본을 삭제한다. 초 단위 이름 +
            # replace는 같은 초의 두 격리가 앞 증거를 덮는다.
            # 격리 실패 시 원본 마커를 건드리지 않고 중단한다
            # (격리 실패 후 새 마커로 교체하면 손상 증거가 사라진다).
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            try:
                fd, qpath = tempfile.mkstemp(
                    prefix=f".needs-review.quarantine.{stamp}.", dir=str(HOOKS_DIR))
                try:
                    with os.fdopen(fd, "wb") as dst, open(marker, "rb") as src:
                        shutil.copyfileobj(src, dst)
                except OSError:
                    try:
                        os.unlink(qpath)
                    except OSError:
                        pass
                    raise
                marker.unlink()
                log(f"손상된 검수 마커 격리: {Path(qpath).name}")
            except OSError as e:
                log(f"손상된 검수 마커 격리 실패 — 원본 보존: {e}")
                return
            entries = []
    for e in entries:
        if isinstance(e, dict) and e.get("commit") == commit and e.get("reason") == reason:
            log(f"검수 대상 이미 기록됨: {commit[:12]} — {reason}")
            return
    entries.append({
        "commit": commit,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    tmp = marker.with_name(f".{marker.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(marker)
    log(f"검수 대상 기록: {commit[:12]} — {reason}")


def do_push():
    try:
        checked_out = current_branch()
    except RuntimeError as e:
        log(f"게시 차단: 브랜치 확인 불가 — {e}")
        return False
    branch = PUBLISH_BRANCH or checked_out
    if PUBLISH_BRANCH and checked_out != PUBLISH_BRANCH:
        log(f"게시 차단: 현재 브랜치 {checked_out!r}, 게시 브랜치 {PUBLISH_BRANCH!r} — 검토 브랜치 자동 게시 금지")
        return False
    if not branch:
        log("브랜치 확인 불가 — 게시 중단")
        return False
    # P2-10: 게시 기준선이 없으면 첫 자동 푸시를 하지 않는다
    # (기준선은 운영자가 명시적으로 만든다).
    try:
        if not ref_exists(f"refs/remotes/origin/{branch}"):
            log(f"게시 차단: 게시 기준선 없음 (origin/{branch}) — 수동 push로 기준선을 만드세요")
            return False
    except RuntimeError as e:
        log(f"게시 차단: 기준선 확인 실패 — {e}")
        return False
    # P2-10: HEAD를 SHA로 고정하고 그 SHA를 검증한 뒤 같은 SHA를
    # 푸시한다 (검증한 커밋과 다른 커밋이 게시되는 TOCTOU 방지).
    head = git(["rev-parse", "HEAD"])
    if head.returncode != 0:
        log(f"게시 차단: HEAD 확인 실패 — {head.stderr[-300:]}")
        return False
    sha = head.stdout.strip()
    if not sha or sha == "HEAD":
        log("게시 차단: HEAD 없음 — 게시할 커밋이 없습니다")
        return False
    try:
        ok, reason = validate_commit_artifacts(sha)
    except RuntimeError as e:
        log(f"게시 차단: HEAD 검증 중 Git 실패 — {e}")
        return False
    if not ok:
        log(f"게시 차단: HEAD 아티팩트 검증 실패 — {reason}")
        flag_needs_review(sha, reason)
        return False
    log(f"게시: origin/{branch} 로 push ({sha[:12]})")
    r = git(["push", "origin", f"{sha}:refs/heads/{branch}"], timeout=300)
    return r.returncode == 0


def update_manifest_translation_hash():
    """번역 후 manifest.translations.sha256 업데이트 (P1-4: 공유 락 하에서).

    락 없이 읽은 오래된 manifest에 번역 해시를 붙이고, 그 사이 빌드가
    교체한 새 manifest 위에 덮으면 빌드 세대의 청크 해시가 사라진다.

    P2-10: 실패를 로그만 남기고 계속하면 오래된 매니페스트를 성공처럼
    게시한다. Returns True(성공) / False(실패) — 호출자는 False 시
    커밋을 막고 재시도한다 (fail-closed). 존재·스키마 확인은 락 안에서
    수행한다 (락 밖 확인은 TOCTOU). 매니페스트가 없으면 실패이다.
    번역 파일이 없는데 매니페스트가 번역을 참조하면 실패이다.
    번역 참조가 없는 매니페스트 + 번역 파일 없음은 동기화할 게 없으므로
    성공이다.
    """
    try:
        with pipeline_lock():
            if not MANIFEST.exists():
                log("manifest 번역 해시 업데이트 실패: manifest.json 없음")
                return False
            try:
                m = json.loads(MANIFEST.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log(f"manifest 번역 해시 업데이트 실패: manifest 파싱 오류: {e}")
                return False
            # 구조 검증 (validate_commit_artifacts와 공유) — {}처럼
            # 필수 청크가 없는 매니페스트를 "동기화할 게 없음"으로
            # 성공 처리하면 비정형 매니페스트가 커밋된다.
            ok, reason = _validate_manifest_dict(m, "local")
            if not ok:
                log(f"manifest 번역 해시 업데이트 실패: 구조 오류 — {reason}")
                return False
            if not TRANSLATIONS.exists():
                if "translations" in m:
                    log("manifest 번역 해시 업데이트 실패: translations.json 없음 (매니페스트가 참조 중)")
                    return False
                return True
            try:
                trans = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log(f"manifest 번역 해시 업데이트 실패: translations 파싱 오류: {e}")
                return False
            if not isinstance(trans, dict):
                log("manifest 번역 해시 업데이트 실패: translations가 dict가 아님")
                return False
            m["translations"] = {
                "file": "data/translations.json",
                "sha256": sha256_file(TRANSLATIONS),
                "count": len(trans),
            }
            tmp = MANIFEST.with_name(f".{MANIFEST.name}.tmp-{os.getpid()}")
            tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(MANIFEST)
            log(f"manifest.translations.sha256 업데이트: {m['translations']['sha256'][:16]}...")
        return True
    except PipelineLockTimeout as e:
        log(f"manifest 번역 해시 업데이트 건너뜀 (락 타임아웃): {e}")
        return False
    except (OSError, ValueError, TypeError, AttributeError) as e:
        log(f"manifest 번역 해시 업데이트 실패: {e}")
        return False


def decide_pipeline(force_build, check_rc, dirty, enrich_marker):
    """Pure pipeline decision: (needs_build, enrich_only).

    force_build: a translation/commit/media retry marker forces a rebuild.
    check_rc: build_data --check result (0=no change, 1=changed).
    dirty: working-tree changes exist. enrich_marker: .retry-enrich exists.
    Enrich-only = no rebuild needed but enrich backlog remains: skip the
    build (which replaces data/) and run enrich alone.
    """
    needs_build = bool(force_build)
    if not needs_build:
        needs_build = (check_rc == 1) or bool(dirty)
    enrich_only = (not needs_build) and bool(enrich_marker)
    return needs_build, enrich_only


def _validate_translation_entry(entry):
    """번역 캐시 엔트리 검증 (translate_posts의 dict 캐시 규약).

    엔트리는 dict여야 하며, 번역 결과의 핵심 필드를 가져야 한다.
    형식이 맞지 않는 엔트리는 병합하지 않는다 (오염 전파 방지).
    """
    if not isinstance(entry, dict):
        return False
    # translated는 str, paragraphs는 list여야 한다 (둘 다 없어도 되는
    # 플레이스홀더는 허용하지 않는다 — 번역 캐시의 최소 규약).
    if not isinstance(entry.get("translated"), str):
        return False
    if not isinstance(entry.get("paragraphs"), list):
        return False
    return True


def merge_translation_backup(backup_path):
    """번역 백업을 현재 translations.json에 병합한다 (호출자가 락 보유).

    현재 파일에 없는 키만 추가하고, 있는 키는 절대 덮지 않는다 (2d와
    방치 백업 복구의 공통 로직). 원자적 저장.
    Returns (복원 키 수, 사유). 사유:
      '' = 성공 (복원 0개도 성공 — 백업이 이미 중복),
      'partial' = 일부 엔트리가 유효하지 않아 건너뜀 — 유효 엔트리는
        병합됐지만 무효 엔트리의 증거가 백업에 남아 있으므로
        호출자는 백업을 삭제하면 안 된다 (격리/보존해야 한다).
      '손상' = 백업이 파싱 불가 / dict 아님 / 유효 엔트리 없음,
      'current-corrupt' = 현재 파일이 존재하지만 손상됨 — 병합하지
        않고 둘 다 보존한다 (손상 현재 파일을 오래된 백업으로 덮으면
        격리·복구 증거가 사라진다. translate 로드의 격리 경로가
        현재 파일을 처리한다).
    유효하지 않은 개별 엔트리는 건너뛰고, 유효한 엔트리만 병합한다.
    """
    try:
        bak = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, "손상"
    if not isinstance(bak, dict):
        return 0, "손상"
    cur = None
    if TRANSLATIONS.exists():
        try:
            cur = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cur = None
        if cur is not None and not isinstance(cur, dict):
            cur = None
    if TRANSLATIONS.exists() and cur is None:
        # 현재 파일이 존재하지만 읽을 수 없음 (손상/파싱 불가) —
        # 덮지 않고 보존한다.
        return 0, "current-corrupt"
    if cur is None:
        cur = {}
    merged = dict(cur)
    restored = 0
    skipped = 0
    for k, v in bak.items():
        if k in merged:
            continue
        if not _validate_translation_entry(v):
            skipped += 1
            continue
        merged[k] = v
        restored += 1
    if skipped:
        log(f"번역 백업 병합: 유효하지 않은 엔트리 {skipped}개 건너뜀 ({backup_path})")
    if restored or len(merged) != len(cur):
        tmp = TRANSLATIONS.with_name(f".{TRANSLATIONS.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(TRANSLATIONS)
    if skipped:
        # 유효 엔트리는 병합됐지만 무효 엔트리의 증거가 백업에 있음 —
        # 성공으로 보고해 삭제하면 증거가 사라진다.
        return restored, "partial"
    return restored, ""


# 방치 백업 복구 (P2/P3, Codex 차단 5·6): 나이(24h)가 아니라 트랜잭션
# 출처 + 콘텐츠 적용 가능성으로 자동 복구 여부를 판단한다.
# 2b 백업 시 transaction.json을 함께 쓴다:
#   {transaction_id, created_at, source_hash, translations_sha256}
# 자동 복구 조건 (전부 충족):
#   - transaction.json이 있고 형식이 올바름 (없으면 legacy — 모호하므로 보존)
#   - 백업의 source_hash가 현재 매니페스트의 source_hash와 동일
#     (소스가 바뀌었으면 백업 키가 삭제된 글을 가리킬 수 있어 모호함)
#   - 적용 가능한 백업들 사이에 같은 키에 대한 값 충돌이 없음
# 위 조건을 못 만족하면 자동 병합·삭제하지 않고 운영자 검토를 기다린다.
# 진짜로 빈 디렉터리만 삭제한다 (translations.json이 없다고 비어 있는
# 것이 아니다 — transaction.json 같은 부분 백업도 보존한다).


def _read_backup_transaction(backup_dir):
    """백업의 transaction.json을 읽는다. 없거나 형식이 틀리면 None.

    모든 필드를 요구한다 — source_hash/translations_sha256이
    없거나 임의 문자열이면 백업 바이트와 묶을 수 없어 신뢰할 수
    없다 (Codex 2차 리뷰 차단 4). translations_sha256은 64-hex여야
    백업 바이트 해시와 대조할 수 있다.
    """
    try:
        rec = json.loads((backup_dir / "transaction.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    if not isinstance(rec.get("transaction_id"), str) or not rec.get("transaction_id"):
        return None
    if not isinstance(rec.get("created_at"), str) or not rec.get("created_at"):
        return None
    if not isinstance(rec.get("source_hash"), str) or not rec.get("source_hash"):
        return None
    if not isinstance(rec.get("translations_sha256"), str) or not _HEX64.match(rec["translations_sha256"]):
        return None
    return rec


def _quarantine_backup_dir(backup_dir):
    """백업 디렉터리를 .quarantine/으로 이동해 증거를 보존한다.

    부분 병합처럼 유효 엔트리는 복원됐지만 무효 엔트리의 증거가
    남아 있는 경우 — 제자리 보존하면 매 폴 재시도하고, 삭제하면
    증거가 사라진다. 격리하면 재시도도 없고 증거도 남는다.
    Returns True(이동 성공) / False(실패 — 제자리 보존).
    """
    try:
        dest_root = HOOKS_DIR / ".quarantine"
        dest_root.mkdir(exist_ok=True)
        dest = dest_root / f"{backup_dir.name}.{uuid.uuid4().hex}"
        shutil.move(str(backup_dir), str(dest))
        log(f"방치 백업 격리 (증거 보존): {backup_dir.name} → {dest.name}")
        return True
    except OSError as e:
        log(f"방치 백업 격리 실패 — 제자리 보존: {backup_dir.name}: {e}")
        return False


def _current_source_hash():
    """현재 매니페스트의 source_hash. 없거나 손상됐으면 None."""
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict):
        return None
    h = m.get("source_hash")
    return h if isinstance(h, str) and h else None


def recover_abandoned_backups():
    """이전 실행이 중단하며 남긴 v2_trans_* 백업을 정리한다.

    후크 flock을 획득한 뒤에 호출되므로 다른 후크와 동시에 실행되지
    않는다. 트랜잭션 출처 + 소스 동일성 + 충돌 없음이 확인된 경우만
    자동 복구한다. 그 외는 모두 보존하고 운영자 검토를 기다린다.

    후보 수집은 공유 상태가 아니라 락 밖에서 한다. 공유 상태 판단
    (현재 매니페스트 source_hash, 현재 파일, 중복 대조) + 병합 +
    삭제는 전부 공유 락 안에서 수행한다 — 락 밖에서 하면 현재 파일이
    병합 사이에 바뀌어 중복 판단이 어긋나고, 병합한 백업을
    다른 실행이 다시 병합할 수 있다.
    """
    candidates = []  # (backup_dir, transaction, backup_data)
    try:
        entries = sorted(HOOKS_DIR.glob("v2_trans_*"))
    except OSError as e:
        # 열거 실패 — 어떤 백업이 있는지 알 수 없으므로 모두 보존한다.
        log(f"방치 백업 열거 실패 — 모두 보존: {e}")
        return
    for d in entries:
        if not d.is_dir():
            continue
        backup_file = d / "translations.json"
        if not backup_file.is_file():
            # translations.json이 없다고 빈 디렉터리가 아니다.
            try:
                remaining = [p for p in d.iterdir()]
            except OSError as e:
                log(f"방치 백업 열거 실패 — 보존: {d.name}: {e}")
                continue
            if not remaining:
                log(f"방치 백업 (진짜 빈 디렉터리, 삭제): {d.name}")
                shutil.rmtree(d, ignore_errors=True)
            else:
                log(f"방치 백업 부분본 — 자동 복구하지 않고 운영자 검토 대기: {d.name} ({[p.name for p in remaining]})")
            continue
        rec = _read_backup_transaction(d)
        if rec is None:
            log(f"방치 백업 legacy/형식 오류 (transaction.json 없음·불완전) — 자동 복구하지 않고 운영자 검토 대기: {d.name}")
            continue
        # 백업 바이트를 트랜잭션 메타데이터와 묶는다 — 해시가 다르면
        # 생성 후 백업이 변경된 것이므로 신뢰할 수 없다.
        try:
            backup_hash = sha256_file(backup_file)
        except OSError as e:
            log(f"방치 백업 읽기 실패 — 보존: {d.name}: {e}")
            continue
        if backup_hash != rec["translations_sha256"]:
            log(f"방치 백업 해시 불일치 (백업이 생성 후 변경됨) — 자동 복구하지 않고 운영자 검토 대기: {d.name}")
            continue
        try:
            bak = json.loads(backup_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log(f"방치 백업 손상 — 자동 복구하지 않고 운영자 검토 대기: {d.name}")
            continue
        if not isinstance(bak, dict):
            log(f"방치 백업 손상 (dict 아님) — 자동 복구하지 않고 운영자 검토 대기: {d.name}")
            continue
        candidates.append((d, rec, bak))

    if not candidates:
        return
    try:
        with pipeline_lock():
            _recover_abandoned_backups_locked(candidates)
    except PipelineLockTimeout as e:
        log(f"방치 백업 복구 건너뜀 (락 타임아웃) — 보존: {e}")
    except OSError as e:
        # 쓰기/교체 실패 — 백업을 보존하고 중단한다 (다음 실행 재시도).
        log(f"방치 백업 복구 중단 (쓰기 실패) — 보존: {e}")


def _recover_abandoned_backups_locked(candidates):
    """공유 락 보유 상태에서 실행되는 복구 본체 (candidates: 위 참조)."""
    current_hash = _current_source_hash()
    if current_hash is None:
        log("방치 백업 복구 건너뜀: 현재 매니페스트의 source_hash를 확인할 수 없음 — 모두 보존")
        return
    applicable = []
    for d, rec, bak in candidates:
        if rec["source_hash"] != current_hash:
            log(f"방치 백업 보존 (소스 변경됨 — 백업 세대가 현재와 다름): {d.name}")
            continue
        applicable.append((d, rec, bak))
    if not applicable:
        return

    # 충돌 검출: 적용 가능한 백업들 사이에 같은 키에 다른 값이 있으면
    # 어느 것을 골라도 되돌릴 수 없으므로 자동 병합하지 않고 모두 보존한다.
    seen = {}
    conflicts = set()
    for d, rec, bak in applicable:
        for k, v in bak.items():
            if k in seen and seen[k] != v:
                conflicts.add(k)
            elif k not in seen:
                seen[k] = v
    if conflicts:
        log(f"방치 백업 충돌 ({len(conflicts)}개 키) — 자동 병합하지 않고 모두 보존: {sorted(conflicts)[:5]}")
        return

    # 트랜잭션 시간순(최신 먼저, 결정적)으로 병합한다. 충돌이 없으므로
    # 순서는 결과에 영향을 주지 않는다.
    for d, rec, bak in sorted(applicable, key=lambda x: x[1]["created_at"], reverse=True):
        backup_file = d / "translations.json"
        # 현재 파일과 바이트가 실제로 동일할 때만 중복 — 메타데이터만
        # 대조하면 변경된 백업의 고유 엔트리를 중복으로 오판해 지운다.
        if TRANSLATIONS.exists() and sha256_file(TRANSLATIONS) == sha256_file(backup_file):
            log(f"방치 백업 중복 (현재와 바이트 동일) — 삭제: {d.name}")
            shutil.rmtree(d, ignore_errors=True)
            continue
        restored, reason = merge_translation_backup(backup_file)
        if reason == "partial":
            # 유효 엔트리는 병합됐고 무효 엔트리 증거가 백업에 남음 —
            # 삭제하지 않고 격리한다 (재시도 없음 + 증거 보존).
            log(f"방치 백업 부분 병합 (복원 {restored}개, 무효 엔트리 격리): {d.name}")
            _quarantine_backup_dir(d)
            continue
        if reason:
            # 손상 / 현재 파일 손상 — 둘 다 보존하고 중단한다.
            # (현재 파일 손상은 translate 로드의 격리 경로가 처리한다.)
            log(f"방치 백업 복구 중단 ({reason}) — 보존: {d.name}")
            return
        log(f"방치 백업 자동 복구: {d.name} (복원 {restored}개)")
        shutil.rmtree(d, ignore_errors=True)


def main():
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("다른 후크 실행 중 — 종료")
        return 2
    log("락 획득")

    force_build_markers = RETRY_TRANSLATION.exists() or RETRY_COMMIT.exists() or RETRY_MEDIA.exists()
    has_retry_marker = force_build_markers or RETRY_ENRICH.exists()
    if has_retry_marker:
        log("재시도 마커 감지 — 소스 변경 여부와 무관하게 처리")

    try:
        # P2/P3: 이전 실행이 중단하며 남긴 번역 백업을 먼저 정리한다.
        # (최근 + 유효한 백업만 자동 복구, 나머지는 운영자 검토 대기.)
        # try/finally 안에서 호출해야 예상치 못한 예외 시에도 flock이
        # 해제된다 (밖에서 호출하면 finally를 우회한다).
        recover_abandoned_backups()

        # 0. 미푸시 커밋/Dirty 처리 (소스 해시와 무관)
        # P2-10: 미푸시 커밋 검증은 실제로 푸시할 때만 수행한다.
        # 게시 비활성 시에는 푸시하지 않으므로 기준선 확인이
        # 파이프라인을 막아서는 안 된다.
        try:
            pending_list = unpushed_commit_list() if PUBLISH_ENABLED else []
            pending = len(pending_list)
            dirty = changed_files()
        except RuntimeError as e:
            # P2-10: Git 실패를 "변경 없음/미푸시 0개"로 취급하면
            # 성공처럼 끝나버린다 — 중단하고 다음 폴에서 재시도.
            log(f"Git 상태 확인 실패 — 중단: {e}")
            return 1
        if pending > 0:
            # P2-10: 미푸시 커밋을 검증 없이 푸시하지 않는다. 이미 커밋된
            # 아티팩트는 재시도로 고쳐지지 않으므로, 검증 실패 시 푸시를
            # 막고 검수 대상으로 기록한다. Git 인프라 실패는 재시도 대상이다.
            for commit in pending_list:
                try:
                    ok, reason = validate_commit_artifacts(commit)
                except RuntimeError as e:
                    log(f"미푸시 커밋 검증 중 Git 실패 — 중단: {e}")
                    return 1
                if not ok:
                    log(f"미푸시 커밋 검증 실패 — 푸시 중단: {reason}")
                    flag_needs_review(commit, reason)
                    return 1
            log(f"미푸시 커밋 {pending}개 — 푸시 재시도")
            if do_push():
                log("미푸시 커밋 게시 완료")
            else:
                log("푸시 재시도 실패")
                return 1
        if dirty and not pending:
            # Dirty 파일이 있지만 커밋되지 않음 → 커밋 시도
            log(f"Dirty 파일 감지 (소스 해시 무관): {dirty}")
            # 아래 3단계로 진행하여 커밋함

        # 1. 변경 감지 (번역/커밋/미디어 마커가 있으면 무조건 빌드.
        #    enrich 마커만 있으면 소스 변경이 있을 때만 빌드하고,
        #    없으면 빌드 없이 enrich만 실행 — enrich 미완료(rc=2) 때마다
        #    전체 빌드가 data/를 교체하던 손실 루프 방지.)
        needs_build = force_build_markers
        enrich_only = False
        if not needs_build:
            r = run([sys.executable, str(V2_ROOT / "build_data.py"), "--check"])
            if r.returncode == 0 and not dirty and not RETRY_ENRICH.exists():
                log("변경 없음, Dirty 없음 — 종료 (모델 호출 없음)")
                # 재시도 마커 정리 (성공적으로 처리됨)
                RETRY_TRANSLATION.unlink(missing_ok=True)
                RETRY_COMMIT.unlink(missing_ok=True)
                return 0
            if r.returncode not in (0, 1):
                log(f"--check 비정상 종료 ({r.returncode})")
                return 1
            needs_build, enrich_only = decide_pipeline(
                False, r.returncode, dirty, RETRY_ENRICH.exists())
        if not needs_build and not enrich_only:
            return 0
        log("빌드 필요 — 시작" if needs_build else "소스 변경 없음 — 빌드 없이 enrich만 실행")

        # 2a~2c-2는 빌드 파이프라인 — enrich-only 모드면 건너뜀
        # (소스 변경 없음: 빌드할 게 없고, 빌드 자체가 data/ 교체라
        # 불필요한 위험만 더함. enrich → 번역 → 커밋은 아래에서 계속.)
        if not enrich_only:
            # 2a. IB 정본 병합 (신규 IB 감지)
            r = run([sys.executable, str(V2_ROOT / "ib_adapter.py")])
            if r.returncode != 0:
                log("IB 어댑터 실패 — 중단")
                return 1

            # 2b. translations.json 백업 (공유 락 하에서 읽어 일관된 스냅샷 확보)
            # P2/P3: transaction.json을 함께 쓴다 — 방치 백업 복구가
            # 나이(24h)가 아니라 트랜잭션 출처 + 소스 동일성으로
            # 자동 복구 여부를 판단한다.
            backup = None
            if TRANSLATIONS.exists():
                backup = Path(tempfile.mkdtemp(prefix="v2_trans_", dir=HOOKS_DIR)) / "translations.json"
                try:
                    with pipeline_lock():
                        shutil.copy2(str(TRANSLATIONS), str(backup))
                        txn = {
                            "transaction_id": uuid.uuid4().hex,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "source_hash": None,
                            "translations_sha256": sha256_file(TRANSLATIONS),
                        }
                        try:
                            m = json.loads(MANIFEST.read_text(encoding="utf-8"))
                            if isinstance(m, dict) and isinstance(m.get("source_hash"), str):
                                txn["source_hash"] = m["source_hash"]
                        except (OSError, ValueError):
                            pass
                        (backup.parent / "transaction.json").write_text(
                            json.dumps(txn, ensure_ascii=False, indent=2), encoding="utf-8")
                except PipelineLockTimeout as e:
                    log(f"번역 캐시 백업 건너뜀 (락 타임아웃): {e}")
                    backup = None
                else:
                    log(f"번역 캐시 백업: {backup}")

            # 2c. 전체 빌드
            r = run([sys.executable, str(V2_ROOT / "build_data.py")])
            if r.returncode != 0:
                log("빌드 실패 — 중단")
                return 1


            # 2c-2. 미디어 미러 (best-effort)
            # build_data 성공 후, git 발행 전
            _mirror = V2_ROOT / "media-mirror.py"
            if _mirror.exists():
                log("미디어 미러 시작 (best-effort)...")
                try:
                    _mr = run([sys.executable, str(_mirror)], timeout=600)
                    if _mr.returncode == 0:
                        RETRY_MEDIA.unlink(missing_ok=True)
                        log("미디어 미러 완료")
                    else:
                        RETRY_MEDIA.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
                        log(f"미디어 미러 실패 (rc={_mr.returncode}) — 다음 실행에서 재시도")
                except Exception as _e:
                    RETRY_MEDIA.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
                    log(f"미디어 미러 예외: {_e} — 다음 실행에서 재시도")
            else:
                log("media-mirror.py 없음")
        else:
            backup = None
            log("enrich-only — IB/빌드/미디어미러 건너뜀")
        # 2c-3. GitHub 스크린샷 AI툴 자동 분류 (best-effort, 캐시 기반)
        # 미디어 미러 후, git 커밋 전. 실패해도 전체 흐름은 계속.
        _enrich = V2_ROOT / "enrich_tools.py"
        if _enrich.exists():
            log("AI툴 스크린샷 분류 시작 (best-effort, 최신 우선 + 백로그 인터리브 + 180초 데드라인)...")
            try:
                # 최신 우선 + 백로그 예약분(신규 4:백로그 1) + 180초 데드라인.
                # 데드라인은 연산 내부에서 강제되어 번역/커밋이 900초 타임아웃에
                # 걸리지 않게 한다 (미완료분은 지문으로 다음 폴에 재개).
                _er = run([sys.executable, str(_enrich), "--backfill",
                           "--interleave-ratio", "4",
                           "--time-budget", "180"], timeout=900)
                if _er.returncode == 0:
                    log("AI툴 분류 완료")
                else:
                    RETRY_ENRICH.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
                    log(f"AI툴 분류 실패 (rc={_er.returncode}) — 다음 실행에서 재시도")
            except Exception as _e:
                RETRY_ENRICH.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
                log(f"AI툴 분류 예외: {_e} — 다음 실행에서 재시도")
        else:
            log("enrich_tools.py 없음")
        # 2d. 번역 캐시 복원/병합 (공유 락 하에서, 원자적 저장)
        # P2/P3 (Codex 차단 5): 백업은 병합이 성공적으로 끝나고 나서만
        # 삭제한다. 락 타임아웃·손상 백업·현재 파일 손상·쓰기 실패 시에는
        # 백업을 보존한다 — 다음 실행의 방치 백업 복구가 처리한다.
        if backup and backup.exists():
            merged_ok = False
            try:
                with pipeline_lock():
                    restored, reason = merge_translation_backup(backup)
                    if reason:
                        # 손상된 백업을 현재 파일 위에 덮지 않는다 (예전 버그).
                        # translate 로드 시 격리·복구 경로가 처리한다.
                        log(f"번역 백업 손상/병합 중단 ({reason}) — 백업 보존 (다음 실행 복구 대기)")
                    else:
                        log(f"번역 캐시 병합 완료 (복원 {restored}개)")
                        merged_ok = True
            except PipelineLockTimeout as e:
                log(f"번역 캐시 병합 건너뜀 (락 타임아웃) — 백업 보존: {e}")
            except OSError as e:
                log(f"번역 캐시 병합 중단 (쓰기 실패) — 백업 보존: {e}")
            if merged_ok:
                shutil.rmtree(backup.parent, ignore_errors=True)

        # 2e. 번역 (실패 시 재시도 마커)
        r = run([sys.executable, str(V2_ROOT / "translate_posts.py")], timeout=1800)
        if r.returncode != 0:
            log("번역 실패 — 재시도 마커 생성")
            RETRY_TRANSLATION.write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        else:
            RETRY_TRANSLATION.unlink(missing_ok=True)
            log("번역 완료 — 재시도 마커 제거")

        # 2f. manifest 번역 해시 업데이트 (번역 후)
        # P2-10: 실패 시 커밋을 막는다 — 오래된 매니페스트를 성공처럼
        # 게시하지 않는다. 다음 폴이 재시도한다.
        if not update_manifest_translation_hash():
            log("manifest 번역 해시 업데이트 실패 — 커밋 중단")
            return 1

        # 3. 커밋 (명시적 pathspec)
        try:
            files = changed_files()
        except RuntimeError as e:
            log(f"Git 상태 확인 실패 — 중단: {e}")
            return 1
        if not files:
            log("커밋할 변경 없음")
            # 번역 재시도 마커가 있으면 실패로 반환 (다음에 재시도)
            return 1 if (RETRY_TRANSLATION.exists() or RETRY_MEDIA.exists() or RETRY_ENRICH.exists()) else 0

        log(f"변경 파일: {files}")
        r = git(["add", "--"] + COMMIT_PATHS)
        if r.returncode != 0:
            log("git add 실패")
            RETRY_COMMIT.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
            return 1

        # 명시적 pathspec으로 커밋 — 무관한 스테이지 파일은 자동으로 제외됨
        # (git commit -- <paths>는 지정된 경로만 커밋, 다른 스테이지 작업 보존)
        msg = (
            "v2: 자동 빌드 (post-ingest hook)\n\n"
            f"변경: {', '.join(files)}\n"
            f"시각: {datetime.now(timezone.utc).isoformat()}"
        )
        r = git(["-c", "user.name=Muse", "-c", "user.email=muse@local",
                 "commit", "-m", msg, "--"] + COMMIT_PATHS)
        if r.returncode != 0:
            log("커밋 실패 — 재시도 마커 생성")
            RETRY_COMMIT.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
            return 1
        RETRY_COMMIT.unlink(missing_ok=True)
        log("커밋 완료")

        # 4. 게시 게이트
        if not PUBLISH_ENABLED:
            log("게시 비활성 — QA 완료 전까지 유지")
            return 1 if (RETRY_TRANSLATION.exists() or RETRY_MEDIA.exists() or RETRY_ENRICH.exists()) else 0
        if do_push():
            log("게시 완료")
            return 1 if (RETRY_TRANSLATION.exists() or RETRY_MEDIA.exists() or RETRY_ENRICH.exists()) else 0
        log("푸시 실패 — 다음 실행에서 재시도")
        return 1

    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()
        log("락 해제")


if __name__ == "__main__":
    sys.exit(main())
