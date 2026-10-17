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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from proc_group import run_grouped

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
    """단일 단계 실행. 실패 시 False 반환.

    P1-7: run_grouped로 실행해 타임아웃 시 단계 프로세스 그룹 전체를
    종료한다 (subprocess.run은 직접 자식만 죽여 손자를 고아로 남긴다).
    """
    log(f"=== {name} 시작 (타임아웃 {timeout}초) ===")
    try:
        env = os.environ.copy()
        # 발행 환경변수 기본값 보장
        env.setdefault("V2_PUBLISH", "1")
        env.setdefault("V2_PUBLISH_BRANCH", "main")
        r = run_grouped(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=timeout, env=env)
        if r.stdout:
            for line in r.stdout.strip().split("\n"):
                log(f"  [{name}] {line}")
        if r.returncode != 0:
            log(f"✗ {name} 실패 (rc={r.returncode})")
            if r.stderr:
                log(f"  오류: {r.stderr[:500]}")
            return False
        log(f"✓ {name} 완료")
        return True
    except subprocess.TimeoutExpired:
        log(f"✗ {name} 타임아웃 ({timeout}초 초과)")
        return False
    except Exception as e:
        log(f"✗ {name} 예외: {e}")
        return False


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
        if not run_step("drive_poll", HOOKS_DIR / "drive_poll.py", TIMEOUT_DRIVE_POLL):
            log("drive_poll 실패 — monitor 실행하지 않고 중단")
            return 1

        # 2. 변경 감지 및 후크 (실패해도 설정 발행은 독립 시도)
        monitor_ok = run_step("monitor", HOOKS_DIR / "monitor.py", TIMEOUT_MONITOR)
        if not monitor_ok:
            log("monitor 실패 — 설정 발행은 독립 시도")

        # 3. 공통 설정 발행 (monitor 실패와 무관하게 독립 시도)
        if not run_step("publish_settings", HOOKS_DIR / "publish_settings.py", 300):
            log("공통 설정 발행 실패 — 다음 주기 재시도")
            return 3 if monitor_ok else 2

        log("스케줄러 실행 완료")
        return 0 if monitor_ok else 2
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
