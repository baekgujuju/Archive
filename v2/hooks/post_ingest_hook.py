#!/usr/bin/env python3
"""
v2 업로드-성공 후크: 빌드 → 번역 → 커밋 (게시는 기본 비활성)

동작:
  1. flock排他 락으로 동시 실행 방지
  2. build_data.py --check 로 변경 감지 (변경 없으면 조용히 종료, 모델 호출 없음)
  3. 변경 있으면: translations.json 백업 → 전체 빌드 → 복원(안전망)
     → 신규 영어 글만 translate_posts.py → git changed-file 확인 → 커밋
  4. 게시(git push)는 기본 비활성. 활성화하려면 환경변수 V2_PUBLISH=1
     (최종 QA 전까지는 절대 켜지 말 것)

사용법:
    python3 post_ingest_hook.py              # 빌드+번역+커밋 (게시 안 함)
    V2_PUBLISH=1 python3 post_ingest_hook.py # 게시까지 (QA 완료 후에만)

종료 코드: 0=성공/변경없음, 1=빌드 실패, 2=락 획득 실패
"""
import fcntl
import hashlib
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

# 게시 게이트: 명시적 옵트인 없이는 절대 push하지 않음
PUBLISH_ENABLED = os.environ.get("V2_PUBLISH", "") == "1"


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


def git_changed_files():
    """v2/data/ 이하 변경 파일 목록 (git status porcelain)."""
    r = run(["git", "-C", str(V2_ROOT), "status", "--porcelain", "--", "data/"])
    files = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if len(line) > 3:
            files.append(line[3:])
    return files


def main():
    # 1. 동시 실행 방지 락
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("다른 후크 실행 중 — 종료")
        return 2
    log("락 획득")

    try:
        # 2. 변경 감지 (결정적, 모델 호출 없음)
        r = run([sys.executable, str(V2_ROOT / "build_data.py"), "--check"])
        if r.returncode == 0:
            log("변경 없음 — 종료 (모델 호출 없음)")
            return 0
        if r.returncode not in (1,):
            log(f"--check 비정상 종료 ({r.returncode})")
            return 1
        log("변경 감지됨 — 빌드 시작")

        # 3a. translations.json 백업 (원자적 교체 시 삭제되는 문제의 안전망)
        backup = None
        if TRANSLATIONS.exists():
            backup = Path(tempfile.mkdtemp(prefix="v2_trans_")) / "translations.json"
            shutil.copy2(str(TRANSLATIONS), str(backup))
            log(f"번역 캐시 백업: {backup}")

        # 3b. 전체 빌드 (스테이징→원자적 교체)
        r = run([sys.executable, str(V2_ROOT / "build_data.py")])
        if r.returncode != 0:
            log("빌드 실패 — 중단")
            return 1

        # 3c. 번역 캐시 복원 (빌드가 보존하지 못한 경우)
        if backup and not TRANSLATIONS.exists():
            shutil.copy2(str(backup), str(TRANSLATIONS))
            log("번역 캐시 복원됨 (빌드에서 유실)")
        elif backup:
            # 해시 비교: 빌드가 보존했으면 그대로, 비었으면 백업으로
            try:
                cur = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
                bak = json.loads(backup.read_text(encoding="utf-8"))
                if len(cur) < len(bak):
                    shutil.copy2(str(backup), str(TRANSLATIONS))
                    log(f"번역 캐시 병합 복원 ({len(cur)}→{len(bak)}개)")
            except (OSError, ValueError) as e:
                log(f"번역 캐시 검증 실패, 백업으로 복원: {e}")
                shutil.copy2(str(backup), str(TRANSLATIONS))
        if backup:
            shutil.rmtree(backup.parent, ignore_errors=True)

        # 3d. 신규 영어 글만 번역 (캐시된 것은 스킵 → 모델 호출 최소화)
        r = run([sys.executable, str(V2_ROOT / "translate_posts.py")], timeout=900)
        if r.returncode != 0:
            log("번역 스크립트 실패 — 커밋은 계속 진행 (번역은 다음 기회에)")

        # 4. git changed-file 확인 후 커밋
        changed = git_changed_files()
        # translations.json도 포함 (data/ 이하이므로 위에서 잡힘)
        if not changed:
            log("커밋할 변경 없음 — 종료")
            return 0
        log(f"변경 파일: {changed}")
        run(["git", "-C", str(V2_ROOT), "add", "--", "data/"])
        msg = (
            "v2: 자동 빌드 (post-ingest hook)\n\n"
            f"변경: {', '.join(changed)}\n"
            f"시각: {datetime.now(timezone.utc).isoformat()}"
        )
        r = run(["git", "-C", str(V2_ROOT), "-c", "user.name=Muse",
                 "-c", "user.email=muse@local", "commit", "-m", msg])
        if r.returncode != 0:
            log("커밋 실패")
            return 1
        log("커밋 완료")

        # 5. 게시 게이트
        if not PUBLISH_ENABLED:
            log("게시 비활성 (V2_PUBLISH=1 없이 push하지 않음) — QA 완료 전까지 유지")
            return 0
        log("⚠️ 게시 활성화됨 — push 실행")
        r = run(["git", "-C", str(V2_ROOT), "push", "origin", "HEAD"], timeout=300)
        return 0 if r.returncode == 0 else 1

    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()
        log("락 해제")


if __name__ == "__main__":
    sys.exit(main())
