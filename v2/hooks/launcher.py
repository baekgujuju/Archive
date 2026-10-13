#!/usr/bin/env python3
"""
v2 워커 런처

사용법:
    python3 launcher.py start   # 워커 시작 (백그라운드, 세션 분리)
    python3 launcher.py stop    # 워커 중지
    python3 launcher.py status  # 상태 확인
    python3 launcher.py restart # 재시작
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
WORKER_SCRIPT = HOOKS_DIR / "persistent_worker.py"
PID_FILE = HOOKS_DIR / ".worker.pid"
LOG_FILE = HOOKS_DIR / "worker.log"


def get_pid():
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # 존재 확인
        return pid
    except (ValueError, OSError, ProcessLookupError):
        return None


def start():
    pid = get_pid()
    if pid:
        print(f"워커가 이미 실행 중 (PID {pid})")
        return 0

    print("워커 시작 중...")
    # 세션 분리하여 백그라운드 실행 (exec 완료 후에도 생존)
    proc = subprocess.Popen(
        [sys.executable, str(WORKER_SCRIPT)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=str(HOOKS_DIR),
    )
    # PID 파일이 생성될 때까지 대기
    for _ in range(10):
        time.sleep(0.5)
        pid = get_pid()
        if pid:
            print(f"워커 시작됨 (PID {pid})")
            print(f"로그: {LOG_FILE}")
            return 0
    print("경고: PID 파일을 확인하지 못했습니다")
    return 1


def stop():
    pid = get_pid()
    if not pid:
        print("워커가 실행 중이 아닙니다")
        # stale PID 파일 정리
        if PID_FILE.exists():
            PID_FILE.unlink()
        return 0

    print(f"워커 중지 중 (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # 종료 대기
        for _ in range(10):
            time.sleep(0.5)
            if not get_pid():
                print("워커 중지됨")
                return 0
        # 강제 종료
        print("SIGKILL 전송...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
        if PID_FILE.exists():
            PID_FILE.unlink()
        print("워커 강제 중지됨")
    except ProcessLookupError:
        print("워커가 이미 종료됨")
        if PID_FILE.exists():
            PID_FILE.unlink()
    return 0


def status():
    pid = get_pid()
    if pid:
        print(f"워커 실행 중 (PID {pid})")
        if LOG_FILE.exists():
            # 마지막 로그 5줄
            lines = LOG_FILE.read_text(encoding="utf-8").strip().split("\n")
            print("최근 로그:")
            for line in lines[-5:]:
                print(f"  {line}")
        return 0
    else:
        print("워커가 실행 중이 아닙니다")
        return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1].lower()
    if cmd == "start":
        return start()
    elif cmd == "stop":
        return stop()
    elif cmd == "status":
        return status()
    elif cmd == "restart":
        stop()
        time.sleep(2)
        return start()
    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
