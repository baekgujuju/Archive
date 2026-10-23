import unittest

from publication_evidence import CHUNKS, EvidenceError
from public_observation import observe_public, fetch_public_bytes
from test_publication_evidence import encoded, post, public


class ObservationTests(unittest.TestCase):
    def setUp(self):
        manifest, self.chunks = public([post()])
        self.manifest = encoded(manifest)
        self.calls = []

    def fetch(self, path):
        self.calls.append(path)
        if path == 'data/manifest.json':
            return self.manifest
        name = next(name for name, p in CHUNKS.items() if p == path)
        return self.chunks[name]

    def test_uncached_checks_every_chunk(self):
        observed, _ = observe_public(self.fetch)
        self.assertEqual(set(observed['records']), {'123'})
        self.assertEqual(len(self.calls), 5)

    def test_verified_cache_still_requires_two_fresh_manifest_reads(self):
        observe_public(self.fetch, self.chunks)
        self.assertEqual(self.calls, ['data/manifest.json', 'data/manifest.json'])

    def test_cache_not_used_when_hash_differs(self):
        bad = dict(self.chunks); bad['posts-x'] += b' '
        observe_public(self.fetch, bad)
        self.assertEqual(self.calls, ['data/manifest.json', 'data/posts-x.json', 'data/manifest.json'])

    def test_manifest_network_failure_not_hidden_by_cache(self):
        def unavailable(path):
            raise EvidenceError('public_network_unavailable')
        with self.assertRaises(EvidenceError): observe_public(unavailable, self.chunks)

    def test_generation_changed_refused(self):
        def changed(path):
            value = self.fetch(path)
            return value+b' ' if path == 'data/manifest.json' and len(self.calls) > 1 else value
        with self.assertRaisesRegex(EvidenceError, 'public_generation_changed'):
            observe_public(changed, self.chunks)

    def test_mixed_deployment_refused(self):
        self.chunks['posts-x'] += b' '
        with self.assertRaisesRegex(EvidenceError, 'chunk_sha_mismatch'): observe_public(self.fetch)

    def test_arbitrary_path_refused_before_network(self):
        with self.assertRaisesRegex(EvidenceError, 'public_path_refused'):
            fetch_public_bytes('https://evil.example', deadline=1e30)


if __name__ == '__main__':
    unittest.main()
