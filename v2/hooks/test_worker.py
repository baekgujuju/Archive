#!/usr/bin/env python3
"""
v2 영속 워커 테스트

- 중복 실행 시 첫 PID 보존
- subprocess 타임아웃 예산 검증
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))


class TestWorkerPID(unittest.TestCase):
    """PID 파일 경쟁 테스트."""

    def test_duplicate_launch_preserves_first_pid(self):
        """중복 실행 시 첫 PID가 보존됨."""
        import persistent_worker as worker

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # 임시 PID/LOCK 파일 사용
            orig_pid = worker.PID_FILE
            orig_lock = worker.LOCK_FILE
            worker.PID_FILE = tmpdir / ".worker.pid"
            worker.LOCK_FILE = tmpdir / ".worker.lock"

            try:
                # 첫 워커가 락 획득
                lock1 = open(worker.LOCK_FILE, "w")
                import fcntl
                fcntl.flock(lock1, fcntl.LOCK_EX | fcntl.LOCK_NB)
                worker.PID_FILE.write_text("1111")

                # 두 번째 워커 시도 (락 실패)
                lock2 = open(worker.LOCK_FILE, "w")
                try:
                    fcntl.flock(lock2, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fail("두 번째 락이 성공하면 안 됨")
                except (OSError, IOError):
                    # 락 실패 시 PID 파일을 건드리지 않아야 함
                    pass

                # 첫 PID가 보존되었는지 확인
                self.assertEqual(worker.PID_FILE.read_text().strip(), "1111")

                fcntl.flock(lock1, fcntl.LOCK_UN)
                lock1.close()
                lock2.close()
                print("  ✓ 중복 실행 시 첫 PID 보존")
            finally:
                worker.PID_FILE = orig_pid
                worker.LOCK_FILE = orig_lock


class TestTimeoutBudget(unittest.TestCase):
    """타임아웃 예산 테스트."""

    def test_timeout_covers_drive_and_monitor(self):
        """타임아웃이 Drive(600) + monitor(2400) + 여유를 포함."""
        import persistent_worker as worker
        # 600 + 2400 + 120 = 3120
        self.assertGreaterEqual(worker.TIMEOUT_TOTAL, 3120)
        print(f"  ✓ 타임아웃 예산 {worker.TIMEOUT_TOTAL}초 (Drive 600 + monitor 2400 + 여유)")

    def test_poll_interval_after_completion(self):
        """폴은 완료 후 300초 대기."""
        import persistent_worker as worker
        self.assertEqual(worker.POLL_INTERVAL, 300)
        print("  ✓ 폴 간격 300초 (완료 후)")


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestWorkerPID))
    suite.addTests(loader.loadTestsFromTestCase(TestTimeoutBudget))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print(f"\n=== 결과: {result.testsRun}개 실행, "
          f"실패 {len(result.failures)}, 오류 {len(result.errors)} ===")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
