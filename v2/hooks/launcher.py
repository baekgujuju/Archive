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


def pid_belongs_to_worker(pid):
    """PID가 persistent_worker.py인지 확인 (PID 재사용 방지)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="ignore")
            return "persistent_worker.py" in cmdline
    except (FileNotFoundError, PermissionError):
        # /proc 접근 불가 시 확인 불가 — 존재만으로 판단
        return True


def get_pid():
    """실행 중인 워커 PID 반환 (검증됨)."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # 존재 확인
        if not pid_belongs_to_worker(pid):
            return None  # PID 재사용됨
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
        # stale PID 파일 정리 (단, 검증된 경우만)
        if PID_FILE.exists():
            try:
                recorded = int(PID_FILE.read_text().strip())
                # PID가 존재하지 않거나 워커가 아니면 정리
                try:
                    os.kill(recorded, 0)
                    if not pid_belongs_to_worker(recorded):
                        PID_FILE.unlink()
                        print("stale PID 파일 정리됨")
                except OSError:
                    PID_FILE.unlink()
                    print("stale PID 파일 정리됨")
            except (ValueError, OSError):
                pass
        # 워커 없이 남은 고아 파이프라인 프로세스도 정리 (P1-7)
        _stop_pipeline_processes()
        return 0

    print(f"워커 중지 중 (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # 종료 대기
        for _ in range(10):
            time.sleep(0.5)
            if not get_pid():
                print("워커 중지됨")
                _stop_pipeline_processes()
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
    _stop_pipeline_processes()
    return 0


# P1-7: 워커만 죽이면 scheduler/hook/번역 같은 자손이 고아로 남아 계속
# 쓰고 게시할 수 있다. 이 checkout의 파이프라인 스크립트 프로세스를
# cmdline으로 식별해 SIGTERM -> 대기 -> SIGKILL 순으로 정리한다.
# (스크립트 파일명 + checkout 경로로만 식별 — 이름만으로 codex 같은
#  일반 프로세스를 일괄 종료하지 않는다.)
PIPELINE_SCRIPTS = (
    "persistent_worker.py", "scheduler_runner.py", "drive_poll.py",
    "monitor.py", "post_ingest_hook.py", "build_data.py",
    "enrich_tools.py", "translate_posts.py", "publish_settings.py",
    "media-mirror.py", "ib_adapter.py",
)


def _pipeline_pids():
    """이 checkout의 파이프라인 프로세스 PID 목록 (/proc cmdline 대조)."""
    found = []
    hooks = str(HOOKS_DIR)
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        if hooks not in cmdline:
            continue
        if any(script in cmdline for script in PIPELINE_SCRIPTS):
            found.append(int(entry.name))
    return found


def _stop_pipeline_processes():
    """남은 파이프라인 프로세스 정리: SIGTERM -> 대기 -> SIGKILL -> 검증."""
    remaining = [p for p in _pipeline_pids() if p != os.getpid()]
    if not remaining:
        return
    print(f"남은 파이프라인 프로세스 정리: {remaining}")
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 10
    while time.time() < deadline:
        remaining = [p for p in _pipeline_pids() if p != os.getpid()]
        if not remaining:
            print("파이프라인 프로세스 정리됨")
            return
        time.sleep(0.5)
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(1)
    remaining = [p for p in _pipeline_pids() if p != os.getpid()]
    if remaining:
        print(f"경고: 파이프라인 프로세스가 남아 있음: {remaining} — 수동 확인 필요")
    else:
        print("파이프라인 프로세스 정리됨")


def status():
    pid = get_pid()
    if pid:
        # PID가 실제 워커인지 재확인
        if pid_belongs_to_worker(pid):
            print(f"워커 실행 중 (PID {pid}, 검증됨)")
        else:
            print(f"경고: PID {pid}가 워커가 아님 (PID 재사용 의심)")
            return 1
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
