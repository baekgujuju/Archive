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
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

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
    종료시키고 후크를 끝낸다. (SIGKILL만으로는 자손이 고아로 남는다.)"""
    terminate_current()
    raise SystemExit(143)


def _on_sigterm(signum, frame):
    """P1-7: monitor가 후크 그룹에 SIGTERM을 보내면, 현재 자식 그룹부터
    종료시키고 후크를 끝낸다. (SIGKILL만으로는 자손이 고아로 남는다.)"""
    terminate_current()
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


def current_branch():
    r = git(["rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def changed_files():
    """porcelain v1 정확 파싱 (data/, media/ 한정)."""
    r = git(["status", "--porcelain=v1", "-z", "--", "data/", "media/", "ib-library.json"])
    if r.returncode != 0:
        return []
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


def unpushed_commits():
    branch = PUBLISH_BRANCH or current_branch()
    if not branch:
        return 0
    r = git(["rev-parse", "--verify", f"origin/{branch}"])
    if r.returncode != 0:
        return 0
    r = git(["rev-list", "--count", f"origin/{branch}..HEAD"])
    try:
        return int(r.stdout.strip()) if r.returncode == 0 else 0
    except ValueError:
        return 0


def do_push():
    checked_out = current_branch()
    branch = PUBLISH_BRANCH or checked_out
    if PUBLISH_BRANCH and checked_out != PUBLISH_BRANCH:
        log(f"게시 차단: 현재 브랜치 {checked_out!r}, 게시 브랜치 {PUBLISH_BRANCH!r} — 검토 브랜치 자동 게시 금지")
        return False
    if not branch:
        log("브랜치 확인 불가 — 게시 중단")
        return False
    log(f"게시: origin/{branch} 로 push")
    r = git(["push", "origin", f"HEAD:refs/heads/{branch}"], timeout=300)
    return r.returncode == 0


def update_manifest_translation_hash():
    """번역 후 manifest.translations.sha256 업데이트 (P1-4: 공유 락 하에서).

    락 없이 읽은 오래된 manifest에 번역 해시를 붙이고, 그 사이 빌드가
    교체한 새 manifest 위에 덮으면 빌드 세대의 청크 해시가 사라진다.
    """
    if not MANIFEST.exists() or not TRANSLATIONS.exists():
        return
    try:
        with pipeline_lock():
            m = json.loads(MANIFEST.read_text(encoding="utf-8"))
            m["translations"] = {
                "file": "data/translations.json",
                "sha256": sha256_file(TRANSLATIONS),
                "count": len(json.loads(TRANSLATIONS.read_text(encoding="utf-8"))),
            }
            tmp = MANIFEST.with_name(f".{MANIFEST.name}.tmp-{os.getpid()}")
            tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(MANIFEST)
            log(f"manifest.translations.sha256 업데이트: {m['translations']['sha256'][:16]}...")
    except PipelineLockTimeout as e:
        log(f"manifest 번역 해시 업데이트 건너뜀 (락 타임아웃): {e}")
    except (OSError, ValueError) as e:
        log(f"manifest 번역 해시 업데이트 실패: {e}")


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
        # 0. 미푸시 커밋/Dirty 처리 (소스 해시와 무관)
        pending = unpushed_commits()
        dirty = changed_files()
        if pending > 0:
            log(f"미푸시 커밋 {pending}개 — 푸시 재시도")
            if not PUBLISH_ENABLED:
                log("게시 비활성 — 푸시 건너뜀")
            elif do_push():
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
            backup = None
            if TRANSLATIONS.exists():
                backup = Path(tempfile.mkdtemp(prefix="v2_trans_", dir=HOOKS_DIR)) / "translations.json"
                try:
                    with pipeline_lock():
                        shutil.copy2(str(TRANSLATIONS), str(backup))
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
        if backup and backup.exists():
            try:
                with pipeline_lock():
                    try:
                        bak = json.loads(backup.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        bak = None
                    if bak is None or not isinstance(bak, dict):
                        # 손상된 백업을 현재 파일 위에 덮지 않는다 (예전 버그).
                        # translate 로드 시 격리·복구 경로가 처리한다.
                        log("번역 백업 손상 — 복원 건너뜀 (translate 로드 시 복구)")
                    else:
                        cur = {}
                        if TRANSLATIONS.exists():
                            try:
                                cur = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
                            except (OSError, ValueError):
                                cur = {}
                        if not isinstance(cur, dict):
                            cur = {}
                        merged = dict(cur)
                        restored = sum(1 for k, v in bak.items() if k not in merged)
                        for k, v in bak.items():
                            if k not in merged:
                                merged[k] = v
                        if restored or len(merged) != len(cur):
                            tmp = TRANSLATIONS.with_name(
                                f".{TRANSLATIONS.name}.tmp-{os.getpid()}")
                            tmp.write_text(
                                json.dumps(merged, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                            tmp.replace(TRANSLATIONS)
                            log(f"번역 캐시 병합: {len(cur)}→{len(merged)}개 (복원 {restored}개)")
            except PipelineLockTimeout as e:
                log(f"번역 캐시 병합 건너뜀 (락 타임아웃): {e}")
            finally:
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
        update_manifest_translation_hash()

        # 3. 커밋 (명시적 pathspec)
        files = changed_files()
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
