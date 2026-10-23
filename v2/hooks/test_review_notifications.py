import unittest

from publication_evidence import EvidenceError
from review_notifications import review_events, update_notices, add_poll_delay


class ReviewNoticeTests(unittest.TestCase):
    def test_quarantine_not_silent_without_publication_target(self):
        events = review_events({'file': {'verification': 'quarantined', 'failure_key': 'f'}}, [])
        ledger, result = update_notices(events, {}, now=100)
        self.assertEqual(result['review_counts'], {'intake_quarantined': 1})
        self.assertTrue(result['alert'])
        self.assertNotIn('file', str(ledger))

    def test_retry_grace_not_reset_by_polls(self):
        events = review_events({'f': {'verification': 'retry_pending', 'failure_key': 'x'}}, [])
        first, result = update_notices(events, {}, now=100)
        self.assertFalse(result['alert'])
        second, result = update_notices(events, first['records'], now=500)
        self.assertFalse(result['alert'])
        _, result = update_notices(events, second['records'], now=701)
        self.assertTrue(result['alert'])

    def test_partial_and_conflict_remain_review_even_if_published(self):
        events = review_events({'f': {'verification': 'verified', 'conversion_audit':
            {'capture_completeness': 'partial'}}}, [{'status': 'pending', 'existing': 'private old', 'proposed': 'private new'}])
        _, result = update_notices(events, {}, now=100)
        self.assertEqual(result['review_item_count'], 2)
        self.assertNotIn('private', str(result))

    def test_failed_delivery_retries_successful_delivery_deduplicates(self):
        events = review_events({'f': {'verification': 'unavailable'}}, [])
        first, a = update_notices(events, {}, now=100)
        second, b = update_notices(events, first['records'], now=200)
        self.assertTrue(b['alert']); self.assertEqual(a['oldest'], b['oldest'])
        ack, _ = update_notices(events, second['records'], now=201, acknowledge_oldest=b['oldest'])
        _, result = update_notices(events, ack['records'], now=300)
        self.assertFalse(result['alert'])

    def test_changed_batch_cannot_ack_unseen_new_notice(self):
        first = review_events({'f': {'verification': 'quarantined'}}, [])
        ledger, result = update_notices(first, {}, now=100)
        changed = review_events({'f': {'verification': 'quarantined'}, 'g': {'verification': 'quarantined'}}, [])
        with self.assertRaises(EvidenceError):
            update_notices(changed, ledger['records'], now=200, acknowledge_oldest=result['oldest'])

    def test_resolved_proposals_do_not_generate_notices(self):
        self.assertEqual(review_events({}, [{'status': 'resolved'}]), {})

    def test_reappearing_issue_is_new_episode(self):
        events = review_events({'f': {'verification': 'quarantined'}}, [])
        ledger, result = update_notices(events, {}, now=100)
        ledger, _ = update_notices(events, ledger['records'], now=101, acknowledge_oldest=result['oldest'])
        cleared, _ = update_notices({}, ledger['records'], now=200)
        _, result = update_notices(events, cleared['records'], now=300)
        self.assertTrue(result['alert'])

    def test_long_optional_work_does_not_hide_overdue_intake(self):
        events={}; add_poll_delay(events, '1970-01-01T00:01:40+00:00', now=1001)
        _, result=update_notices(events, {}, now=1001)
        self.assertEqual(result['review_counts'], {'intake_poll_delayed': 1})

    def test_no_upload_age_false_alarm_when_poll_is_current(self):
        events={}; add_poll_delay(events, '1970-01-01T00:16:40+00:00', now=1001)
        self.assertEqual(events,{})

    def test_unknown_old_poll_time_is_not_invented(self):
        events={}; add_poll_delay(events, None, now=1001)
        self.assertEqual(events,{})

    def test_bad_poll_timestamp_refused(self):
        for value in ('invalid', '1970-01-01T00:01:40', '2100-01-01T00:00:00+00:00'):
            with self.assertRaises(EvidenceError): add_poll_delay({}, value, now=1001)


if __name__ == '__main__':
    unittest.main()
