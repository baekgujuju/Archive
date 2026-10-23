#!/usr/bin/env python3
"""
v2 영속 워커 (5분 폴, 모델 호출 없음)

Hatch agent cron 대신 사용하는 결정적 호스트 워커.
subprocess로 분리 실행되며 exec 완료 후에도 생존.

특성:
  - 300초마다 scheduler_runner.py 실행 (완료 후 300초 대기)
  - singleton flock + PID 파일 (중복 실행 방지)
  - 로그 크기 제한 (1MB, 로테이션)
  - V2_PUBLISH=1, V2_PUBLISH_BRANCH=main을 자식 프로세스에 프로그래밍 방식으로 설정
  - 모델 호출 없음 (순수 Python)

타임아웃 예산 (P1-7 체인 정합 — 바깥이 안쪽 합계보다 커야 한다):
  - Drive 인제스트: 600초
  - Monitor: 3900초 (후크 3600 + 300 여유)
  - 설정 발행: 300초
  - 전체: 4800초 (600 + 3900 + 300)

사용법:
    # 시작 (백그라운드, 세션 분리)
    python3 v2/hooks/launcher.py start

    # 상태 확인
    python3 v2/hooks/launcher.py status

    # 중지
    python3 v2/hooks/launcher.py stop
"""
import fcntl
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from proc_group import run_grouped, terminate_current


def _on_sigterm(signum, frame):
    """P1-7: SIGTERM을 받으면 현재 자식 그룹(폴)부터 종료시키고 끝낸다.
    진입 즉시 SIGTERM을 무시해 정리 도중 재진입을 막는다.
    예산 105초 — 스케줄러(전체 정리 최대 90초)가 정리할 시간을 준다."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    terminate_current(grace=105)
    raise SystemExit(143)


try:
    signal.signal(signal.SIGTERM, _on_sigterm)
except ValueError:
    pass

HOOKS_DIR = Path(__file__).resolve().parent
V2_ROOT = HOOKS_DIR.parent
PID_FILE = HOOKS_DIR / ".worker.pid"
LOCK_FILE = HOOKS_DIR / ".worker.lock"
LOG_FILE = HOOKS_DIR / "worker.log"
SCHEDULER_LOG = HOOKS_DIR / "scheduler.log"
MAX_LOG_SIZE = 1 * 1024 * 1024  # 1MB

POLL_INTERVAL = 300  # 5분 (완료 후 대기)

# 타임아웃 예산 (초): Drive 600 + monitor 3900 + 설정 발행 300
TIMEOUT_TOTAL = 4800


def rotate_log(path: Path):
    """로그 로테이션 (1MB 초과 시)."""
    if path.exists() and path.stat().st_size > MAX_LOG_SIZE:
        old = path.with_suffix(path.suffix + ".old")
        if old.exists():
            old.unlink()
        path.rename(old)


def log(msg):
    """로그 기록 (크기 제한)."""
    rotate_log(LOG_FILE)
    rotate_log(SCHEDULER_LOG)  # scheduler.log도 로테이션

    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {msg}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def is_running():
    """워커가 실행 중인지 확인 (PID가 persistent_worker.py인지 검증)."""
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
        # 프로세스 존재 확인
        os.kill(pid, 0)
        # PID가 persistent_worker.py인지 확인 (PID 재사용 방지)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="ignore")
                if "persistent_worker.py" not in cmdline:
                    return False, None  # 다른 프로세스가 PID 재사용
        except (FileNotFoundError, PermissionError):
            # /proc 접근 불가 시 PID 존재만으로 판단
            pass
        return True, pid
    except (ValueError, OSError, ProcessLookupError):
        return False, None


def run_poll():
    """scheduler_runner.py 실행 (환경변수 프로그래밍 방식 설정)."""
    # 자식 프로세스에 환경변수 설정 (os.environ을 통한 프로그래밍 방식)
    env = os.environ.copy()
    env["V2_PUBLISH"] = "1"
    env["V2_PUBLISH_BRANCH"] = "main"
    from pipeline_progress import start_phase, finish_phase
    import uuid
    cycle = uuid.uuid4().hex
    env['ARCHIVE_POLL_ID'] = cycle
    event = start_phase(HOOKS_DIR, 'worker', 'poll', TIMEOUT_TOTAL, cycle=cycle)
    phase_rc = None

    log(f"폴 시작 (타임아웃 {TIMEOUT_TOTAL}초)")
    try:
        # P1-7: 타임아웃 시 스케줄러 그룹 전체를 종료한다.
        r = run_grouped(
            [sys.executable, str(HOOKS_DIR / "scheduler_runner.py")],
            capture_output=True, text=True, timeout=TIMEOUT_TOTAL, env=env,
            grace=105)
        phase_rc = r.returncode
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
        log(f"폴 타임아웃 ({TIMEOUT_TOTAL}초 초과)")
    except Exception as e:
        log(f"폴 예외: {e}")
    finally:
        finish_phase(HOOKS_DIR, event, rc=phase_rc)


def main():
    """워커 메인 루프."""
    my_pid = os.getpid()

    # 1. flock 먼저 획득 (PID 파일 건드리기 전)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        # 락 실패 시 PID 파일을 건드리지 않음
        log("다른 워커가 실행 중 — 종료 (PID 파일 untouched)")
        lock_fh.close()
        return 1

    # 2. 락 획득 성공 후에만 PID 기록
    PID_FILE.write_text(str(my_pid))
    log(f"워커 시작 (PID {my_pid})")

    try:
        while True:
            run_poll()
            # 완료 후 300초 대기
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log("워커 중지 (인터럽트)")
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()
        # PID 파일은 자신의 PID와 일치할 때만 삭제
        try:
            if PID_FILE.exists():
                recorded = PID_FILE.read_text().strip()
                if recorded == str(my_pid):
                    PID_FILE.unlink()
                    log("PID 파일 정리됨")
                else:
                    log(f"PID 파일이 다른 값({recorded}) — 삭제하지 않음")
        except OSError:
            pass
        log("워커 종료")

    return 0


if __name__ == "__main__":
    sys.exit(main())
