#!/usr/bin/env python3
"""
v2 결정적 스케줄러 러너 (5분 주기)

실행 순서:
  1. drive_poll.py — Drive 인제스트 (타임아웃 600초)
  2. monitor.py — 변경 감지 및 후크 실행 (타임아웃 2400초, 번역 1800초 허용)

특성:
  - 전체 실행에 flock (중복 실행 시 안전하게 스킵)
  - 단계별 타임아웃 (Drive 600s, monitor 2400s)
  - 결정적: 실패 시 즉시 중단하고 로그 기록
  - 변경 없으면 모델 호출 없음 (monitor가 해시 비교로 판단)
  - 실패 시 로그에 기록하고 종료 코드 반환

사용법:
    python3 scheduler_runner.py
    python3 scheduler_runner.py --dry-run  # 실제 실행 없이 순서만 확인

환경변수:
    V2_PUBLISH=1 — 발행 활성화
    V2_PUBLISH_BRANCH=main — 발행 브랜치
"""
import fcntl
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from collections import namedtuple

from proc_group import run_grouped, terminate_current
from pipeline_progress import start_phase, finish_phase

# run_step 결과: (kind, rc). kind ∈ {"ok", "failed", "timeout", "exception"}.
# rc는 자식 프로세스의 실제 종료 코드 (drive_poll의 3은 파일 실패
# 존재 + 실패 기록 영속화 완료 — 폴 자체 실패가 아니다).
StepResult = namedtuple("StepResult", ["kind", "rc"])


def _on_sigterm(signum, frame):
    """P1-7: SIGTERM을 받으면 현재 자식 그룹(단계)부터 종료시키고 끝낸다.
    진입 즉시 SIGTERM을 무시해 정리 도중 재진입을 막는다.
    예산 75초 — 모니터(전체 정리 최대 60초)가 정리할 시간을 준다."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    terminate_current(grace=75)
    raise SystemExit(143)


try:
    signal.signal(signal.SIGTERM, _on_sigterm)
except ValueError:
    pass

HOOKS_DIR = Path(__file__).parent
V2_ROOT = HOOKS_DIR.parent
LOG_FILE = HOOKS_DIR / "scheduler.log"
LOCK_FILE = HOOKS_DIR / ".scheduler.lock"

# 단계별 타임아웃 (초)
TIMEOUT_DRIVE_POLL = 600   # Drive 인제스트
TIMEOUT_MONITOR = 3900     # monitor (후크 3600 + 300 여유, P1-7 체인 정합)


def log(msg):
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(name, script, timeout):
    """단일 단계 실행. Returns StepResult(kind, rc).

    P1-7: run_grouped로 실행해 타임아웃 시 단계 프로세스 그룹 전체를
    종료한다 (subprocess.run은 직접 자식만 죽여 손자를 고아로 남긴다).
    P2-1: bool 대신 결과 종류와 종료 코드를 반환한다. drive_poll의
    rc=3은 폴 자체 실패가 아니라 "파일 실패 존재 + 실패 기록
    영속화 완료"이므로, 호출자가 rc를 보고 판단한다.
    """
    log(f"=== {name} 시작 (타임아웃 {timeout}초) ===")
    event = start_phase(HOOKS_DIR, 'scheduler',
                        name if name in ('drive_poll', 'monitor', 'publish_settings') else 'command', timeout)
    phase_rc = None
    try:
        env = os.environ.copy()
        # 발행 환경변수 기본값 보장
        env.setdefault("V2_PUBLISH", "1")
        env.setdefault("V2_PUBLISH_BRANCH", "main")
        r = run_grouped(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=timeout, env=env, grace=75)
        phase_rc = r.returncode
        if r.stdout:
            for line in r.stdout.strip().split("\n"):
                log(f"  [{name}] {line}")
        if r.returncode != 0:
            log(f"✗ {name} 실패 (rc={r.returncode})")
            if r.stderr:
                log(f"  오류: {r.stderr[:500]}")
            return StepResult("failed", r.returncode)
        log(f"✓ {name} 완료")
        return StepResult("ok", 0)
    except subprocess.TimeoutExpired:
        log(f"✗ {name} 타임아웃 ({timeout}초 초과)")
        return StepResult("timeout", None)
    except Exception as e:
        log(f"✗ {name} 예외: {e}")
        return StepResult("exception", None)
    finally:
        finish_phase(HOOKS_DIR, event, rc=phase_rc)


def main():
    dry_run = "--dry-run" in sys.argv
    log("=" * 60)
    log("스케줄러 실행 시작" + (" (dry-run)" if dry_run else ""))
    log(f"환경: V2_PUBLISH={os.environ.get('V2_PUBLISH', '1')}, "
        f"V2_PUBLISH_BRANCH={os.environ.get('V2_PUBLISH_BRANCH', 'main')}")

    if dry_run:
        log("순서: drive_poll.py (600s) → monitor.py (2400s)")
        log("dry-run 완료")
        return 0

    # 전체 실행 flock (중복 틱 스킵)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("이전 실행이 진행 중 — 스킵")
        lock_fh.close()
        return 0  # 스킵은 성공으로 간주

    try:
        # 1. Drive 인제스트
        # P2-1: drive_poll rc=3은 폴 자체 실패가 아니다 (파일 실패 존재
        # + 실패 기록 영속화 완료). 경고를 남기고 monitor를 진행한다.
        # monitor 성공으로 3의 경고를 지우면 안 된다 (최종 반환에 반영).
        drive_file_failures = False
        drive = run_step("drive_poll", HOOKS_DIR / "drive_poll.py", TIMEOUT_DRIVE_POLL)
        if drive.rc == 3:
            drive_file_failures = True
            log("drive_poll: 파일 실패 존재 (실패 기록 영속화 완료) — 경고 남기고 monitor 진행")
        elif drive.kind != "ok":
            if drive.rc == 2:
                log("drive_poll 락 경합 — 다음 주기 대기 (정상 완료로 기록하지 않음)")
                return 2
            log("drive_poll 실패 — monitor 실행하지 않고 중단")
            return 1

        # 2. 변경 감지 및 후크 (실패해도 설정 발행은 독립 시도)
        monitor_result = run_step("monitor", HOOKS_DIR / "monitor.py", TIMEOUT_MONITOR)
        monitor_ok = monitor_result.kind == "ok"
        if not monitor_ok:
            log("monitor 실패 — 설정 발행은 독립 시도")

        # 3. 공통 설정 발행 (monitor 실패와 무관하게 독립 시도)
        publish_result = run_step("publish_settings", HOOKS_DIR / "publish_settings.py", 300)
        if publish_result.kind != "ok":
            log("공통 설정 발행 실패 — 다음 주기 재시도")
            return 3 if monitor_ok else 2

        log("스케줄러 실행 완료")
        if drive_file_failures:
            return 3
        return 0 if monitor_ok else 2
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
