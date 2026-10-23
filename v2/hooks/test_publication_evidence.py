import copy
import hashlib
import json
import unittest

from publication_evidence import (CHUNKS, EvidenceError, acknowledge,
    capture_targets, content_hash, digest, reconcile, verify_public)


def encoded(value):
    return json.dumps(value).encode()


def post(text='full paragraph and final link', key='123'):
    return {'tweetId': key, 'fullText': text, 'sourceUrl': 'https://x.com/user/status/'+key,
            'author': {'name': 'Name', 'handle': 'user'}, 'createdAt': '2026-10-24',
            'media': [{'kind': 'video_thumbnail', 'url': 'https://example.com/poster.png',
                       'sourceUrl': 'https://x.com/user/status/'+key}],
            'links': ['https://example.com/actual'], 'quoted': None,
            'replies': [{'tweetId': '456', 'fullText': 'the complete first reply',
                         'links': ['https://example.com/prompt']} ]}


def public(records):
    chunks = {name: encoded({'posts': records if name == 'posts-x' else []}) for name in CHUNKS}
    manifest = {'chunks': {name: {'file': path, 'sha256': hashlib.sha256(chunks[name]).hexdigest(),
        'count': len(records) if name == 'posts-x' else 0} for name, path in CHUNKS.items()}}
    return manifest, chunks


def receipt(record):
    return {'f': {'verification': 'verified', 'modifiedTime': 'v1', 'size': 100,
        'content_sha256': 'a'*64, 'publication_targets': capture_targets([record], ['123']),
        'conversion_audit': {'capture_completeness': 'unknown'}}}


class PublicationTests(unittest.TestCase):
    def observation(self, records):
        manifest, chunks = public(records)
        return verify_public(encoded(manifest), chunks)

    def test_verified_exact_versions_publish(self):
        p = post()
        result = reconcile(receipt(p), [p], self.observation([p]), {}, now=1000)
        self.assertEqual(next(iter(result['receipts'].values()))['status'], 'published')

    def test_same_id_old_content_never_passes(self):
        p = post()
        mutations = []
        for key, value in [('fullText', 'short'), ('links', []), ('replies', []), ('media', [])]:
            q = copy.deepcopy(p); q[key] = value; mutations.append(q)
        q = copy.deepcopy(p); q['replies'][0]['fullText'] = 'the complete'; mutations.append(q)
        q = copy.deepcopy(p); q['media'][0]['sourceUrl'] = 'https://example.com/wrong'; mutations.append(q)
        for q in mutations:
            with self.subTest(q=q):
                result = reconcile(receipt(p), [p], self.observation([q]), {}, now=1000)
                self.assertEqual(next(iter(result['receipts'].values()))['status'], 'publication_pending')

    def test_nested_quote_loss_changes_hash(self):
        p = post(); p['replies'][0]['quoted'] = {'tweetId': '789', 'fullText': 'quoted final sentence'}
        q = copy.deepcopy(p); del q['replies'][0]['quoted']
        self.assertNotEqual(content_hash(p), content_hash(q))

    def test_optional_fields_do_not_block_original(self):
        p = post(); q = copy.deepcopy(p)
        q.update(translation={'ko': '번역'}, category='tools', topics=['new'])
        q['replies'][0]['translation'] = '댓글 번역'
        self.assertEqual(content_hash(p), content_hash(q))

    def test_empty_quote_allowed(self):
        p = post(); p['quoted'] = {}
        self.assertIsInstance(content_hash(p), str)

    def test_legacy_nested_missing_id_preserves_content(self):
        p = post(); p['quoted'] = {'tweetId': None, 'fullText': 'legacy quote'}
        q = copy.deepcopy(p); q['quoted']['fullText'] = 'different quote'
        self.assertNotEqual(content_hash(p), content_hash(q))

    def test_duplicate_ids_refused(self):
        with self.assertRaises(EvidenceError): self.observation([post(), post()])

    def test_chunk_tamper_refused(self):
        m, c = public([post()]); c['posts-x'] += b' '
        with self.assertRaisesRegex(EvidenceError, 'chunk_sha_mismatch'): verify_public(encoded(m), c)

    def test_manifest_paths_not_followed(self):
        for path in ('https://evil.example/data.json', '../secret', '/data/posts-x.json'):
            m, c = public([post()]); m['chunks']['posts-x']['file'] = path
            with self.assertRaises(EvidenceError): verify_public(encoded(m), c)

    def test_bad_count_and_missing_chunk_refused(self):
        m, c = public([post()]); m['chunks']['posts-x']['count'] = True
        with self.assertRaises(EvidenceError): verify_public(encoded(m), c)
        m, c = public([post()]); del c['posts-ib']
        with self.assertRaises(EvidenceError): verify_public(encoded(m), c)

    def test_duplicate_json_keys_refused_even_with_valid_sha(self):
        m, c = public([]); c['posts-x'] = b'{"posts":[],"posts":[]}'
        m['chunks']['posts-x']['sha256'] = hashlib.sha256(c['posts-x']).hexdigest()
        with self.assertRaises(EvidenceError): verify_public(encoded(m), c)

    def test_retry_preserves_age_and_failed_delivery_retries(self):
        p = post(); args = receipt(p), [p], self.observation([])
        first = reconcile(*args, {}, now=100)
        second = reconcile(*args, first['receipts'], now=700)
        third = reconcile(*args, second['receipts'], now=1000)
        self.assertEqual(len(second['due']), 1)
        self.assertEqual(third['due'][0]['age_seconds'], 900)

    def test_acknowledge_only_same_incident_and_new_version_alerts(self):
        p = post(); args = receipt(p), [p], self.observation([])
        first = reconcile(*args, {}, now=100)
        due = reconcile(*args, first['receipts'], now=700)
        incident = due['due'][0]
        acknowledge(due, incident['key'], incident['incident'])
        repeat = reconcile(*args, due['receipts'], now=1000)
        self.assertFalse(repeat['due'])
        with self.assertRaises(EvidenceError): acknowledge(due, incident['key'], 'wrong')
        changed = receipt(p); changed['f']['modifiedTime'] = 'v2'
        new = reconcile(changed, [p], self.observation([]), repeat['receipts'], now=1001)
        self.assertFalse(new['due'])
        self.assertNotEqual(set(new['receipts']), set(repeat['receipts']))

    def test_network_failure_never_reuses_historical_success(self):
        p = post(); done = reconcile(receipt(p), [p], self.observation([p]), {}, now=100)
        down = reconcile(receipt(p), [p], None, done['receipts'], now=10000)
        value = next(iter(down['receipts'].values()))
        self.assertEqual(value['status'], 'public_observation_unavailable')
        self.assertEqual(value['published_at'], 100)
        self.assertFalse(down['due'])  # transient outage gets its own grace

    def test_network_pending_flip_does_not_reset_wait(self):
        p = post(); a = reconcile(receipt(p), [p], self.observation([]), {}, now=100)
        b = reconcile(receipt(p), [p], None, a['receipts'], now=500)
        c = reconcile(receipt(p), [p], self.observation([]), b['receipts'], now=701)
        self.assertEqual(c['due'][0]['age_seconds'], 601)

    def test_no_uploads_no_age_based_false_alarm(self):
        result = reconcile({}, [post()], self.observation([post()]), {}, now=99999999)
        self.assertEqual(result['due'], [])

    def test_unexplained_canonical_change_is_not_silent(self):
        p = post(); result = reconcile(receipt(p), [post('later canonical version')],
                                      self.observation([p]), {}, now=100)
        self.assertEqual(next(iter(result['receipts'].values()))['status'], 'canonical_changed_unverified')
        later = reconcile(receipt(p), [post('later canonical version')], None,
                          result['receipts'], now=701)
        self.assertEqual(later['due'][0]['status'], 'canonical_changed_unverified')

    def test_known_new_receipt_supersedes_old_target(self):
        p = post(); q = post('updated accepted version'); r = receipt(p)
        r['new-file'] = receipt(q)['f']
        r['new-file']['modifiedTime'] = 'v2'
        result = reconcile(r, [q], self.observation([q]), {}, now=100)
        statuses = {item['status'] for item in result['receipts'].values()}
        self.assertEqual(statuses, {'published', 'superseded_by_known_receipt'})

    def test_missing_canonical_becomes_due_incident(self):
        p = post(); first = reconcile(receipt(p), [], None, {}, now=100)
        result = reconcile(receipt(p), [], None, first['receipts'], now=701)
        self.assertEqual(result['due'][0]['status'], 'canonical_missing')

    def test_network_flap_after_ack_does_not_notify_twice(self):
        p = post(); r = receipt(p)
        first = reconcile(r, [p], self.observation([]), {}, now=100)
        due = reconcile(r, [p], self.observation([]), first['receipts'], now=701)
        event = due['due'][0]; acknowledge(due, event['key'], event['incident'])
        down = reconcile(r, [p], None, due['receipts'], now=800)
        back = reconcile(r, [p], self.observation([]), down['receipts'], now=900)
        self.assertFalse(down['due']); self.assertFalse(back['due'])

    def test_malformed_public_contract_refused(self):
        p = post()
        for value in ({}, {'records': []}, {'records': {'wrong': p}}):
            with self.assertRaises(EvidenceError): reconcile(receipt(p), [p], value, {}, now=100)

    def test_merge_conflict_review_not_erased_by_public_match(self):
        p = post(); r = receipt(p); r['f']['publication_review_required'] = True
        result = reconcile(r, [p], self.observation([p]), {}, now=100)
        value = next(iter(result['receipts'].values()))
        self.assertEqual(value['status'], 'published')
        self.assertTrue(value['review_required'])
        self.assertEqual(value['capture_completeness'], 'unknown')

    def test_legacy_has_no_invented_target(self):
        result = reconcile({'f': {'verification': 'verified', 'tweetIds': ['123']}},
                           [post()], self.observation([]), {}, now=100)
        self.assertFalse(result['receipts'])

    def test_invalid_clock_or_target_refused(self):
        p = post()
        for now in (float('nan'), float('inf'), -1):
            with self.assertRaises(EvidenceError): reconcile(receipt(p), [p], None, {}, now=now)
        r = receipt(p); r['f']['publication_targets']['123'] = 'not-a-hash'
        with self.assertRaises(EvidenceError): reconcile(r, [p], None, {}, now=100)


if __name__ == '__main__':
    unittest.main()
