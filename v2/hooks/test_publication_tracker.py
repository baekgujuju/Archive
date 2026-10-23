import json
from pathlib import Path
import tempfile
import unittest

from publication_evidence import EvidenceError
from publication_tracker import check_receipts
from test_publication_evidence import encoded, post, receipt, public
from publication_evidence import verify_public


def write(path, value):
    Path(path).write_bytes(encoded(value))


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = post(); self.canonical = self.root/'canonical.json'
        write(self.canonical, [self.p])
        write(self.root/'.drive_poll_state.json', {'processed': receipt(self.p)})
        self.before = self.canonical.read_bytes()

    def observe(self, records):
        m, c = public(records)
        return lambda cached_chunks=None: (verify_public(encoded(m), c), c)

    def run_check(self, **kw):
        return check_receipts(self.root, self.canonical, writer=write, **kw)

    def test_roundtrip_success_has_no_canonical_or_intake_write(self):
        intake = (self.root/'.drive_poll_state.json').read_bytes()
        result = self.run_check(now=100, observe=self.observe([self.p]))
        self.assertEqual(result['counts'], {'published': 1})
        self.assertEqual(self.canonical.read_bytes(), self.before)
        self.assertEqual((self.root/'.drive_poll_state.json').read_bytes(), intake)

    def test_alert_then_delivery_ack_survives_restart(self):
        self.run_check(now=100, observe=self.observe([]))
        event = self.run_check(now=701, observe=self.observe([]))
        self.assertTrue(event['alert'])
        ack = self.run_check(now=702, observe=self.observe([]), acknowledge_oldest=event['oldest'])
        self.assertFalse(ack['alert'])
        self.assertFalse(self.run_check(now=999, observe=self.observe([]))['alert'])

    def test_failed_delivery_does_not_ack(self):
        self.run_check(now=100, observe=self.observe([]))
        a = self.run_check(now=701, observe=self.observe([]))
        b = self.run_check(now=702, observe=self.observe([]))
        self.assertTrue(b['alert']); self.assertEqual(a['oldest'], b['oldest'])

    def test_failed_write_cannot_report_success(self):
        def broken(*_): raise OSError('disk full')
        with self.assertRaises(OSError):
            check_receipts(self.root, self.canonical, now=100, writer=broken, observe=self.observe([self.p]))

    def test_malformed_ledger_is_not_silently_reset(self):
        (self.root/'.publication-receipts.json').write_text('broken')
        with self.assertRaises(EvidenceError): self.run_check(now=100, observe=self.observe([self.p]))

    def test_concurrent_local_change_does_not_certify_old_version(self):
        def observe(cached_chunks=None):
            write(self.canonical, [post('new version')])
            return self.observe([self.p])()
        with self.assertRaisesRegex(EvidenceError, 'local_snapshot_changed'):
            self.run_check(now=100, observe=observe)

    def test_no_receipts_means_no_network_request(self):
        write(self.root/'.drive_poll_state.json', {'processed': {}})
        def forbidden(cached_chunks=None): raise AssertionError('unexpected network')
        self.assertEqual(self.run_check(now=100, observe=forbidden)['tracked_receipts'], 0)

    def test_changed_incident_rejects_ack(self):
        self.run_check(now=100, observe=self.observe([]))
        event = self.run_check(now=701, observe=self.observe([]))
        with self.assertRaises(EvidenceError):
            self.run_check(now=702, observe=self.observe([self.p]), acknowledge_oldest=event['oldest'])

    def test_public_byte_cache_persists_but_manifest_observer_still_runs(self):
        self.run_check(now=100, observe=self.observe([self.p]))
        calls=[]
        def observed(cached_chunks=None):
            calls.append(cached_chunks)
            return self.observe([self.p])(cached_chunks)
        self.run_check(now=200, observe=observed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), {'posts-x', 'posts-web', 'posts-ib'})

    def test_network_failure_does_not_infer_success_from_persisted_cache(self):
        self.run_check(now=100, observe=self.observe([self.p]))
        def unavailable(cached_chunks=None): raise EvidenceError('public_network_unavailable')
        result=self.run_check(now=200, observe=unavailable)
        self.assertEqual(result['counts'], {'public_observation_unavailable': 1})

    def test_global_input_stall_detected_even_without_new_drive_receipts(self):
        write(self.root/'.drive_poll_state.json', {'processed': {}})
        source=lambda: {'source_hash': 'a'*64, 'built_source_hash': 'b'*64}
        self.run_check(now=100, observe=self.observe([self.p]), input_source=source)
        event=self.run_check(now=701, observe=self.observe([self.p]), input_source=source)
        self.assertTrue(event['alert']); self.assertEqual(event['status'], 'input_build_pending')

    def test_input_ack_persists_without_acknowledging_unseen_receipt(self):
        source=lambda: {'source_hash': 'a'*64, 'built_source_hash': 'b'*64}
        self.run_check(now=100, observe=self.observe([]), input_source=source)
        event=self.run_check(now=701, observe=self.observe([]), input_source=source)
        self.assertTrue(event['oldest'].startswith('input:'))
        self.run_check(now=702, observe=self.observe([]), input_source=source, acknowledge_oldest=event['oldest'])
        result=self.run_check(now=800, observe=self.observe([]), input_source=source)
        self.assertFalse(result['alert'])


if __name__ == '__main__':
    unittest.main()
