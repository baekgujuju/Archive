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
V2_ROOT = HOOKS_DIR.parent
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
# 식별해 SIGTERM -> 대기 -> SIGKILL 순으로 정리한다.
# 식별은 (a) 인터프리터가 python이고 (b) argv 중 하나가 파이프라인
# 스크립트의 절대 경로와 정확히 일치할 때만 — hooks 경로를 포함하는
# 에디터·셸 같은 무관한 프로세스를 부분 문자열 매칭으로 죽이지 않는다.
PIPELINE_SCRIPTS = (
    "persistent_worker.py", "scheduler_runner.py", "drive_poll.py",
    "monitor.py", "post_ingest_hook.py", "publish_settings.py",
)
V2_SCRIPTS = (
    "build_data.py", "enrich_tools.py", "translate_posts.py",
    "media-mirror.py", "ib_adapter.py",
)


def _pipeline_script_paths():
    paths = set()
    for name in PIPELINE_SCRIPTS:
        paths.add(str(HOOKS_DIR / name))
    for name in V2_SCRIPTS:
        paths.add(str(V2_ROOT / name))
    return paths


# _script_operand가 옵션을 해석할 수 없으면 이 값을 반환한다.
# 해석 불가 = 추측해서 PID 종료 대상으로 삼지 않는다.
_UNPARSEABLE = object()


def _script_operand(argv):
    """python argv에서 실제 실행 스크립트 피연산자를 찾는다 (CPython 규칙).

    - `-c`/`-m`/`-` (붙여 쓴 -c<cmd>/-m<mod> 포함)는 스크립트 파일이
      아니므로 None (payload 인자를 스크립트 경로로 오인하지 않는다).
    - 묶인 짧은 옵션(-uW ignore)을 풀어서 해석한다. -W/-X는 값(붙여 쓴
      값 또는 다음 인자)을 먹고, -u/-B/-E 등은 플래그다.
    - `--` 뒤의 첫 인자가 스크립트다.
    - 해석할 수 없는 옵션은 _UNPARSEABLE (추측 금지).
    - 첫 번째 비옵션 인자가 스크립트 경로다.
    """
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return argv[i + 1] if i + 1 < len(argv) else None
        if a == "-":
            return None
        if a.startswith("--"):
            # 긴 옵션은 값을 별도 인자로 먹지 않는다 (--opt=val 형태 포함).
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            j = 1
            value_in_next_arg = False
            while j < len(a):
                ch = a[j]
                if ch in "cm":
                    # -c/-m: 나머지가 값(붙여 쓴 경우) 또는 다음 인자가 값.
                    return None
                if ch in "WX":
                    # 값을 먹는다: 붙여 쓴 값이면 이 인자에서 끝,
                    # 아니면 다음 인자가 값.
                    if j + 1 >= len(a):
                        value_in_next_arg = True
                    break
                if ch in "uBEsvOObdqiv":
                    j += 1
                    continue
                # 해석 불가한 짧은 옵션: 추측하지 않는다.
                return _UNPARSEABLE
            i += 2 if value_in_next_arg else 1
            continue
        # 비옵션: 스크립트 경로.
        return a
    return None


def _pipeline_pids():
    """이 checkout의 파이프라인 프로세스 PID 목록 (정확한 argv 대조).

    조건: (a) /proc/PID/exe가 python 바이너리, (b) 스크립트 피연산자가
    파이프라인 스크립트 절대 경로와 정확히 일치 (상대 경로는
    /proc/PID/cwd 기준으로 해석). 부분 문자열 매칭 금지.
    """
    script_paths = _pipeline_script_paths()
    found = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            exe = os.readlink(entry / "exe")
        except OSError:
            continue
        if "python" not in Path(exe).name:
            continue
        argv = [a.decode("utf-8", "ignore") for a in raw.split(b"\x00") if a]
        if not argv:
            continue
        script = _script_operand(argv)
        if script is None or script is _UNPARSEABLE:
            # payload(-c/-m)/stdin(-) 또는 해석 불가 옵션: 추측해서
            # 종료 대상으로 삼지 않는다.
            continue
        script_path = Path(script)
        if not script_path.is_absolute():
            try:
                cwd = os.readlink(entry / "cwd")
            except OSError:
                continue
            script_path = Path(cwd) / script_path
        try:
            resolved = str(script_path.resolve())
        except OSError:
            continue
        if resolved in script_paths:
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
