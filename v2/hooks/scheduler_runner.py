#!/usr/bin/env python3
"""
v2 결정적 스케줄러 러너 (5분 주기)

실행 순서:
  1. drive_poll.py — Drive 인제스트
  2. monitor.py — 변경 감지 및 후크 실행

특성:
  - 결정적: 실패 시 즉시 중단하고 로그 기록
  - 변경 없으면 모델 호출 없음 (monitor가 해시 비교로 판단)
  - 실패 시 로그에 기록하고 종료 코드 반환

사용법:
    python3 scheduler_runner.py
    python3 scheduler_runner.py --dry-run  # 실제 실행 없이 순서만 확인
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
V2_ROOT = HOOKS_DIR.parent
LOG_FILE = HOOKS_DIR / "scheduler.log"


def log(msg):
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(name, script):
    """단일 단계 실행. 실패 시 False 반환."""
    log(f"=== {name} 시작 ===")
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=240)
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
        log(f"✗ {name} 타임아웃 (240초)")
        return False
    except Exception as e:
        log(f"✗ {name} 예외: {e}")
        return False


def main():
    dry_run = "--dry-run" in sys.argv
    log("=" * 60)
    log("스케줄러 실행 시작" + (" (dry-run)" if dry_run else ""))

    if dry_run:
        log("순서: drive_poll.py → monitor.py")
        log("dry-run 완료")
        return 0

    # 1. Drive 인제스트
    if not run_step("drive_poll", HOOKS_DIR / "drive_poll.py"):
        log("drive_poll 실패 — monitor 실행하지 않고 중단")
        return 1

    # 2. 변경 감지 및 후크
    if not run_step("monitor", HOOKS_DIR / "monitor.py"):
        log("monitor 실패 — 중단")
        return 2

    log("스케줄러 실행 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
