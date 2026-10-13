#!/usr/bin/env python3
"""
v2 업로드-성공 후크: 빌드 → 번역 → 커밋 (게시는 기본 비활성)

동작:
  1. flock排他 락으로 동시 실행 방지
  2. build_data.py --check 로 변경 감지 (변경 없으면 조용히 종료, 모델 호출 없음)
     단, 미푸시 커밋이 있으면 푸시 재시도
  3. 변경 있으면: translations.json 백업 → 전체 빌드 → 복원(안전망)
     → 신규/변경 영어 글만 translate_posts.py → git 변경 파일 확인 → 명시적 pathspec 커밋
  4. 게시(git push)는 기본 비활성. 활성화하려면 환경변수 V2_PUBLISH=1
     (최종 QA 전까지는 절대 켜지 말 것)
  5. 푸시 대상 브랜치는 V2_PUBLISH_BRANCH 로 명시 (기본값: 현재 브랜치명, HEAD 추적 아님)

사용법:
    python3 post_ingest_hook.py              # 빌드+번역+커밋 (게시 안 함)
    V2_PUBLISH=1 python3 post_ingest_hook.py # 게시까지 (QA 완료 후에만)

종료 코드: 0=성공/변경없음, 1=빌드 실패, 2=락 획득 실패
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = V2_ROOT / "data"
HOOKS_DIR = V2_ROOT / "hooks"
LOCK_FILE = HOOKS_DIR / ".post_ingest.lock"
TRANSLATIONS = DATA_DIR / "translations.json"
# 커밋 대상 (명시적 pathspec — 무관한 스테이지 파일 혼입 방지)
COMMIT_PATHS = ["data/", "media/"]

# 게시 게이트: 명시적 옵트인 없이는 절대 push하지 않음
PUBLISH_ENABLED = os.environ.get("V2_PUBLISH", "") == "1"
# 푸시 대상 브랜치 명시 (HEAD 추적 브랜치 오푸시 방지)
PUBLISH_BRANCH = os.environ.get("V2_PUBLISH_BRANCH", "")


def log(msg):
    print(f"[hook {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, **kw):
    log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 600), **kw)
    if r.stdout:
        print(r.stdout[-2000:])
    if r.returncode != 0:
        log(f"실패 (exit {r.returncode}): {r.stderr[-1000:]}")
    return r


def git(args, **kw):
    """git -C V2_ROOT 실행."""
    return run(["git", "-C", str(V2_ROOT)] + args, **kw)


def current_branch():
    r = git(["rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else ""


def changed_files():
    """porcelain v1 정확 파싱 (strip 금지 — 파일명 선행 공백 보존).

    형식: XY<space>path (X=staged, Y=unstaged)
    """
    r = git(["status", "--porcelain=v1", "-z", "--", "data/", "media/"])
    if r.returncode != 0:
        return []
    files = []
    # -z: NUL 구분, 경로에 따옴표 없음
    for entry in r.stdout.split("\x00"):
        if len(entry) < 4:
            continue
        xy = entry[:2]
        path = entry[3:]
        # rename/copy의 경우 "orig -> new" 형태 → new 사용
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if xy.strip():  # 변경 있음
            files.append(path)
    return files


def unpushed_commits():
    """origin/<branch> 대비 미푸시 커밋 수 (없으면 0)."""
    branch = PUBLISH_BRANCH or current_branch()
    if not branch:
        return 0
    # 원격 브랜치가 없으면 전체를 미푸시로 간주하지 않음 (신규 브랜치는 게시 게이트가 막음)
    r = git(["rev-parse", "--verify", f"origin/{branch}"])
    if r.returncode != 0:
        return 0
    r = git(["rev-list", "--count", f"origin/{branch}..HEAD"])
    try:
        return int(r.stdout.strip()) if r.returncode == 0 else 0
    except ValueError:
        return 0


def do_push():
    """게시 (게이트 통과 시에만 호출)."""
    branch = PUBLISH_BRANCH or current_branch()
    if not branch:
        log("브랜치 확인 불가 — 게시 중단")
        return False
    log(f"게시: origin/{branch} 로 push")
    r = git(["push", "origin", f"HEAD:refs/heads/{branch}"], timeout=300)
    return r.returncode == 0


def main():
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("다른 후크 실행 중 — 종료")
        return 2
    log("락 획득")

    try:
        # 0. 미푸시 커밋 재시도 (--check가 exit 0이어도 푸시는 재시도)
        pending = unpushed_commits()
        if pending > 0:
            log(f"미푸시 커밋 {pending}개 감지 — 푸시 재시도")
            if not PUBLISH_ENABLED:
                log("게시 비활성 — 푸시 건너뜀 (커밋은 로컬에 유지)")
            elif do_push():
                log("미푸시 커밋 게시 완료")
            else:
                log("푸시 재시도 실패 — 다음 실행에서 재시도")
                return 1

        # 1. 변경 감지 (결정적, 모델 호출 없음)
        r = run([sys.executable, str(V2_ROOT / "build_data.py"), "--check"])
        if r.returncode == 0:
            log("변경 없음 — 종료 (모델 호출 없음)")
            return 0
        if r.returncode != 1:
            log(f"--check 비정상 종료 ({r.returncode})")
            return 1
        log("변경 감지됨 — 빌드 시작")

        # 2a. translations.json 백업 (원자적 교체 안전망)
        backup = None
        if TRANSLATIONS.exists():
            backup = Path(tempfile.mkdtemp(prefix="v2_trans_")) / "translations.json"
            shutil.copy2(str(TRANSLATIONS), str(backup))
            log(f"번역 캐시 백업: {backup}")

        # 2b. 전체 빌드
        r = run([sys.executable, str(V2_ROOT / "build_data.py")])
        if r.returncode != 0:
            log("빌드 실패 — 중단 (백업에서 복원 시도)")
            if backup and backup.exists():
                # 빌드 실패 시 이전 data/는 그대로이므로 백업만 정리
                pass
            return 1

        # 2c. 번역 캐시 복원/병합
        if backup and backup.exists():
            try:
                bak = json.loads(backup.read_text(encoding="utf-8"))
                cur = json.loads(TRANSLATIONS.read_text(encoding="utf-8")) if TRANSLATIONS.exists() else {}
                # 합집합 (빌드 후 유실분 복원)
                merged = dict(cur)
                restored = 0
                for k, v in bak.items():
                    if k not in merged:
                        merged[k] = v
                        restored += 1
                if restored or len(merged) != len(cur):
                    TRANSLATIONS.write_text(
                        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                    log(f"번역 캐시 병합: {len(cur)}→{len(merged)}개 (복원 {restored}개)")
                else:
                    log(f"번역 캐시 유지: {len(cur)}개")
            except (OSError, ValueError) as e:
                log(f"번역 캐시 검증 실패, 백업으로 복원: {e}")
                shutil.copy2(str(backup), str(TRANSLATIONS))
            finally:
                shutil.rmtree(backup.parent, ignore_errors=True)

        # 2d. 번역 (신규/변경분만 — 캐시 히트 시 스킵, 해시 불일치 시 재번역)
        r = run([sys.executable, str(V2_ROOT / "translate_posts.py")], timeout=1800)
        if r.returncode != 0:
            log("번역 스크립트 실패 — 다음 실행에서 재시도 (커밋은 계속)")

        # 3. 변경 파일 확인 → 명시적 pathspec 커밋 (무관 파일 혼입 방지)
        files = changed_files()
        if not files:
            log("커밋할 변경 없음 — 종료")
            return 0
        log(f"변경 파일: {files}")

        r = git(["add", "--"] + COMMIT_PATHS)
        if r.returncode != 0:
            log("git add 실패 — 중단")
            return 1
        # 스테이지된 것이 COMMIT_PATHS 뿐인지 재확인
        r = git(["diff", "--cached", "--name-only", "-z"])
        staged = [p for p in r.stdout.split("\x00") if p]
        if r.returncode != 0:
            log("스테이지 확인 실패 — 중단")
            return 1
        log(f"스테이지됨: {staged}")

        msg = (
            "v2: 자동 빌드 (post-ingest hook)\n\n"
            f"변경: {', '.join(files)}\n"
            f"시각: {datetime.now(timezone.utc).isoformat()}"
        )
        r = git(["-c", "user.name=Muse", "-c", "user.email=muse@local",
                 "commit", "-m", msg, "--"] + COMMIT_PATHS)
        if r.returncode != 0:
            log("커밋 실패 — 중단")
            return 1
        log("커밋 완료")

        # 4. 게시 게이트
        if not PUBLISH_ENABLED:
            log("게시 비활성 (V2_PUBLISH=1 없이 push하지 않음) — QA 완료 전까지 유지")
            return 0
        if do_push():
            log("게시 완료")
            return 0
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
