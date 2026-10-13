#!/usr/bin/env python3
"""
v2 영속 워커 (5분 폴, 모델 호출 없음)

Hatch agent cron 대신 사용하는 결정적 호스트 워커.
subprocess로 분리 실행되며 exec 완료 후에도 생존.

특성:
  - 300초마다 scheduler_runner.py 실행
  - singleton flock + PID 파일 (중복 실행 방지)
  - 로그 크기 제한 (1MB, 로테이션)
  - V2_PUBLISH=1, V2_PUBLISH_BRANCH=main을 자식 프로세스에 프로그래밍 방식으로 설정
  - 모델 호출 없음 (순수 Python)

사용법:
    # 시작 (백그라운드, 세션 분리)
    nohup python3 v2/hooks/persistent_worker.py > /dev/null 2>&1 &
    # 또는
    python3 v2/hooks/launcher.py start

    # 상태 확인
    python3 v2/hooks/launcher.py status

    # 중지
    python3 v2/hooks/launcher.py stop
"""
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
V2_ROOT = HOOKS_DIR.parent
PID_FILE = HOOKS_DIR / ".worker.pid"
LOCK_FILE = HOOKS_DIR / ".worker.lock"
LOG_FILE = HOOKS_DIR / "worker.log"
MAX_LOG_SIZE = 1 * 1024 * 1024  # 1MB

POLL_INTERVAL = 300  # 5분


def log(msg):
    """로그 기록 (크기 제한)."""
    # 로그 로테이션
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE:
        # 오래된 로그를 .old로 이동
        old = LOG_FILE.with_suffix(".log.old")
        if old.exists():
            old.unlink()
        LOG_FILE.rename(old)

    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {msg}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def is_running():
    """워커가 실행 중인지 확인."""
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
        # 프로세스 존재 확인
        os.kill(pid, 0)
        return True, pid
    except (ValueError, OSError, ProcessLookupError):
        return False, None


def run_poll():
    """scheduler_runner.py 실행 (환경변수 프로그래밍 방식 설정)."""
    # 자식 프로세스에 환경변수 설정 (os.environ을 통한 프로그래밍 방식)
    env = os.environ.copy()
    env["V2_PUBLISH"] = "1"
    env["V2_PUBLISH_BRANCH"] = "main"

    log("폴 시작")
    try:
        r = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "scheduler_runner.py")],
            capture_output=True, text=True, timeout=290, env=env)
        # scheduler_runner의 출력을 워커 로그에 기록
        if r.stdout:
            for line in r.stdout.strip().split("\n")[-10:]:  # 마지막 10줄만
                log(f"  [poll] {line}")
        if r.returncode != 0:
            log(f"폴 실패 (rc={r.returncode})")
            if r.stderr:
                log(f"  오류: {r.stderr[:300]}")
        else:
            log("폴 완료")
    except subprocess.TimeoutExpired:
        log("폴 타임아웃 (290초)")
    except Exception as e:
        log(f"폴 예외: {e}")


def main():
    """워커 메인 루프."""
    # PID 파일에 기록
    PID_FILE.write_text(str(os.getpid()))

    # singleton flock
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("다른 워커가 실행 중 — 종료")
        PID_FILE.unlink(missing_ok=True)
        return 1

    log(f"워커 시작 (PID {os.getpid()})")

    try:
        while True:
            run_poll()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log("워커 중지 (인터럽트)")
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()
        PID_FILE.unlink(missing_ok=True)
        log("워커 종료")

    return 0


if __name__ == "__main__":
    sys.exit(main())
