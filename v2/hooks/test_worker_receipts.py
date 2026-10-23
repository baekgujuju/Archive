import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from publication_evidence import EvidenceError

ROOT = Path(__file__).resolve().parent
CONTROL_SOURCE = ROOT/'candidate/v2/hooks/worker_control.py'
if not CONTROL_SOURCE.is_file():
    CONTROL_SOURCE = ROOT/'worker_control.py'
spec = importlib.util.spec_from_file_location('candidate_worker_control', CONTROL_SOURCE)
control = importlib.util.module_from_spec(spec); spec.loader.exec_module(control)


class WorkerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.worker = control.WorkerControl(self.root)
        self.git = patch.object(self.worker, '_git_publication_health', return_value={
            'status': 'healthy', 'pending': 0, 'alert': False}).start()
        self.addCleanup(patch.stopall)
        patch('review_notifications.check_reviews', return_value={'status': 'review_notices_checked', 'alert': False}).start()

    def test_existing_git_path_unchanged_without_intake(self):
        self.assertFalse(self.worker.publication_health()['alert'])
        self.git.assert_called_once_with(now=None, grace=600, acknowledge_oldest='')

    def test_receipt_alarm_works_with_no_git_pending(self):
        (self.root/'.drive_poll_state.json').write_text('{}')
        with patch('publication_tracker.check_receipts', return_value={
            'status': 'publication_pending', 'alert': True, 'oldest': 'receipt:key:incident'}):
            result = self.worker.publication_health(now=100)
        self.assertTrue(result['alert']); self.assertEqual(result['oldest'], 'receipt:key:incident')

    def test_receipt_ack_not_sent_to_git_acknowledger(self):
        (self.root/'.drive_poll_state.json').write_text('{}')
        with patch('publication_tracker.check_receipts', return_value={
            'status': 'receipt_publication_checked', 'alert': False}) as tracker:
            self.worker.publication_health(now=100, acknowledge_oldest='receipt:key:incident')
        self.assertEqual(self.git.call_args.kwargs['acknowledge_oldest'], '')
        self.assertEqual(tracker.call_args.kwargs['acknowledge_oldest'], 'receipt:key:incident')

    def test_unacknowledged_git_alarm_not_hidden_by_healthy_receipt(self):
        (self.root/'.drive_poll_state.json').write_text('{}')
        self.git.return_value = {'status': 'publication_stalled', 'alert': True, 'oldest': 'commit'}
        with patch('publication_tracker.check_receipts', return_value={
            'status': 'receipt_publication_checked', 'counts': {'published': 1}, 'alert': False}):
            self.assertEqual(self.worker.publication_health()['oldest'], 'commit')

    def test_storage_failure_never_certifies_healthy(self):
        (self.root/'.drive_poll_state.json').write_text('{}')
        with patch('publication_tracker.check_receipts', side_effect=OSError('disk full')):
            with self.assertRaises(control.ControlError): self.worker.publication_health()

    def test_busy_snapshot_defers_but_never_acknowledges(self):
        (self.root/'.drive_poll_state.json').write_text('{}')
        with patch('publication_tracker.check_receipts', side_effect=EvidenceError('local_snapshot_changed')):
            self.assertEqual(self.worker.publication_health()['receipt_observation'], 'deferred_local_change')
            with self.assertRaises(control.ControlError):
                self.worker.publication_health(acknowledge_oldest='receipt:key:incident')

    def test_phase_stall_uses_same_delivery_then_ack_contract(self):
        observation = {'status': 'phase_overrun', 'alert': True, 'token': 'a'*32,
                       'actor': 'hook', 'phase': 'translate_posts', 'age_seconds': 2000}
        with patch('pipeline_progress.progress_health', return_value=observation):
            first = self.worker._phase_publication_health(now=2000)
            second = self.worker._phase_publication_health(now=2001)
            self.assertTrue(first['alert']); self.assertTrue(second['alert'])
            self.worker._phase_publication_health(now=2002, acknowledge_oldest=first['oldest'])
            self.assertFalse(self.worker._phase_publication_health(now=2003)['alert'])
            with self.assertRaises(control.ControlError):
                self.worker._phase_publication_health(now=2004, acknowledge_oldest='phase:wrong')

    def test_phase_alert_does_not_depend_on_new_receipts_or_git_commit(self):
        (self.root/'.pipeline-progress-worker.json').write_text('{}')
        with patch.object(self.worker, '_phase_publication_health', return_value={
            'status': 'phase_overrun', 'alert': True, 'oldest': 'phase:incident'}):
            self.assertEqual(self.worker.publication_health()['status'], 'phase_overrun')


if __name__ == '__main__':
    unittest.main()
