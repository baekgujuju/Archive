import unittest

from input_publication import reconcile_inputs
from publication_evidence import EvidenceError


class InputPublicationTests(unittest.TestCase):
    def test_no_git_commit_is_needed_to_detect_unbuilt_input(self):
        state, summary = reconcile_inputs({'source_hash': 'a'*64, 'built_source_hash': 'b'*64},
            {'source_hash': 'b'*64}, {}, now=100)
        _, summary = reconcile_inputs({'source_hash': 'a'*64, 'built_source_hash': 'b'*64},
            {'source_hash': 'b'*64}, state, now=701)
        self.assertEqual(summary['status'], 'input_build_pending'); self.assertTrue(summary['alert'])

    def test_git_push_is_not_a_pages_receipt(self):
        _, summary = reconcile_inputs({'source_hash': 'a'*64, 'built_source_hash': 'a'*64},
            {'source_hash': 'b'*64}, {}, now=100)
        self.assertEqual(summary['status'], 'input_pages_pending')

    def test_every_source_lane_uses_same_hash_contract(self):
        for lane_hash in ('a'*64, 'b'*64, 'c'*64):
            _, summary = reconcile_inputs({'source_hash': lane_hash}, {'source_hash': lane_hash}, {}, now=100)
            self.assertEqual(summary['status'], 'input_version_published')

    def test_new_upload_during_stall_does_not_reset_age(self):
        first, _ = reconcile_inputs({'source_hash': 'a'*64}, None, {}, now=100)
        second, result = reconcile_inputs({'source_hash': 'b'*64}, {'source_hash': 'c'*64}, first, now=701)
        self.assertEqual(result['age_seconds'], 601)
        self.assertTrue(result['alert'])
        self.assertEqual(first['incident'], second['incident'])

    def test_ack_is_delivery_bound_and_network_flap_deduplicated(self):
        first, event = reconcile_inputs({'source_hash': 'a'*64}, None, {}, now=100)
        ack, _ = reconcile_inputs({'source_hash': 'a'*64}, None, first, now=701,
                                   acknowledge_oldest=event['oldest'])
        _, result = reconcile_inputs({'source_hash': 'a'*64}, {'source_hash': 'b'*64}, ack, now=800)
        self.assertFalse(result['alert'])

    def test_recovery_closes_incident_and_next_change_starts_fresh(self):
        first, _ = reconcile_inputs({'source_hash': 'a'*64}, None, {}, now=100)
        recovered, _ = reconcile_inputs({'source_hash': 'a'*64}, {'source_hash': 'a'*64}, first, now=800)
        new, result = reconcile_inputs({'source_hash': 'b'*64}, {'source_hash': 'a'*64}, recovered, now=900)
        self.assertFalse(result['alert']); self.assertNotEqual(first['incident'], new['incident'])

    def test_wrong_ack_or_invalid_hash_refused(self):
        with self.assertRaises(EvidenceError): reconcile_inputs({'source_hash': 'bad'}, None, {}, now=100)
        with self.assertRaises(EvidenceError): reconcile_inputs({'source_hash': 'a'*64}, None, {}, now=100,
                                                               acknowledge_oldest='input:wrong')


if __name__ == '__main__':
    unittest.main()
