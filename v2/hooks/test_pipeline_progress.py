from pathlib import Path
import tempfile
import unittest

from pipeline_progress import start_phase, finish_phase, progress_health
from publication_evidence import EvidenceError
from test_publication_tracker import write

ROOT = Path(__file__).resolve().parent


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def start(self, actor='worker', phase='poll', budget=1000, cycle='a'*32, now=100):
        return start_phase(self.root, actor, phase, budget, cycle=cycle, now=now, writer=write)

    def test_no_old_evidence_fabricated(self):
        self.assertEqual(progress_health(self.root, now=100)['status'], 'phase_evidence_unavailable')

    def test_bounded_work_not_false_stall(self):
        self.start()
        self.assertFalse(progress_health(self.root, now=500)['alert'])

    def test_live_parent_does_not_hide_child_overrun(self):
        self.start(budget=1000)
        self.start('hook', 'translate_posts', budget=100, now=110)
        result = progress_health(self.root, now=391)
        self.assertTrue(result['alert']); self.assertEqual(result['actor'], 'hook')

    def test_new_child_does_not_hide_parent_overrun(self):
        self.start(budget=100)
        self.start('hook', 'translate_posts', budget=1000, now=370)
        result = progress_health(self.root, now=381)
        self.assertTrue(result['alert']); self.assertEqual(result['actor'], 'worker')

    def test_old_cycle_child_is_not_current_stall(self):
        self.start('hook', 'translate_posts', budget=1, cycle='b'*32, now=0)
        self.start()
        self.assertFalse(progress_health(self.root, now=500)['alert'])

    def test_sleeping_live_parent_without_new_poll_is_stale(self):
        event = self.start()
        finish_phase(self.root, event, rc=0, now=200, writer=write)
        self.assertFalse(progress_health(self.root, now=500)['alert'])
        self.assertEqual(progress_health(self.root, now=1101)['status'], 'poll_completion_stale')

    def test_late_completion_cannot_overwrite_new_phase(self):
        old = self.start(); newer = self.start(now=101)
        self.assertFalse(finish_phase(self.root, old, rc=0, now=102, writer=write))
        self.assertTrue(finish_phase(self.root, newer, rc=0, now=103, writer=write))

    def test_write_failure_not_reported_started(self):
        def broken(*_): raise OSError('full')
        with self.assertRaises(OSError): start_phase(self.root, 'worker', 'poll', 100, writer=broken)

    def test_invalid_actor_cycle_and_future_time_refused(self):
        with self.assertRaises(EvidenceError): self.start(actor='../other')
        with self.assertRaises(EvidenceError): self.start(cycle='not-an-id')
        self.start(now=500)
        with self.assertRaises(EvidenceError): progress_health(self.root, now=100)

    def test_repeated_failed_polls_do_not_reset_failure_age(self):
        first = self.start(now=100)
        finish_phase(self.root, first, rc=1, now=150, writer=write)
        second = self.start(now=450)
        finish_phase(self.root, second, rc=1, now=500, writer=write)
        third = self.start(now=800)
        result = progress_health(self.root, now=810)
        self.assertTrue(result['alert']); self.assertEqual(result['status'], 'poll_repeated_failure')
        finish_phase(self.root, third, rc=0, now=820, writer=write)
        self.assertFalse(progress_health(self.root, now=821)['alert'])

    def test_failure_incident_stable_across_retry_cycles(self):
        first = self.start(now=100)
        finish_phase(self.root, first, rc=1, now=150, writer=write)
        a = progress_health(self.root, now=701)
        self.start(now=702)
        b = progress_health(self.root, now=703)
        self.assertEqual(a['token'], b['token'])


if __name__ == '__main__':
    unittest.main()
