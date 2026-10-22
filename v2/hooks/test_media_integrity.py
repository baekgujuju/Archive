import hashlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from media_integrity import MediaIntegrityError, owned_path, validate_owned_media


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'blue').save(image, format='PNG')
        self.raw = image.getvalue()
        self.path = 'v2/media/full/embedded-' + hashlib.sha256(self.raw).hexdigest() + '.png'
        self.url = 'https://baekgujuju.github.io/Archive/' + self.path
        self.record = {'media': [{'type': 'photo', 'url': self.url}]}

    def test_valid_content_and_dedup(self):
        self.assertEqual(validate_owned_media([self.record, self.record], lambda p: self.raw), [self.path])

    def test_missing(self):
        with self.assertRaisesRegex(MediaIntegrityError, 'absent'):
            validate_owned_media(self.record, lambda p: None)

    def test_corrupt(self):
        with self.assertRaisesRegex(MediaIntegrityError, 'hash'):
            validate_owned_media(self.record, lambda p: b'wrong')

    def test_invalid_raster_with_matching_hash(self):
        raw = b'not an image'
        url = 'https://baekgujuju.github.io/Archive/v2/media/full/embedded-' + hashlib.sha256(raw).hexdigest() + '.png'
        with self.assertRaisesRegex(MediaIntegrityError, 'complete'):
            validate_owned_media({'media': [url]}, lambda p: raw)

    def test_nested_quote_reply(self):
        with self.assertRaises(MediaIntegrityError):
            validate_owned_media({'replies': [{'quoted': self.record}]}, lambda p: None)

    def test_html_image(self):
        with self.assertRaises(MediaIntegrityError):
            validate_owned_media({'fullHtml': '<img src="' + self.url + '">'}, lambda p: None)

    def test_relative(self):
        self.assertEqual(owned_path('media/full/test.png'), 'v2/media/full/test.png')

    def test_external_and_prose_not_owned(self):
        self.assertIsNone(owned_path('https://pbs.twimg.com/media/test.png'))
        self.assertEqual(validate_owned_media({'fullText': self.url}, lambda p: None), [])

    def test_path_traversal(self):
        for path in ('../test.png', '%2e%2e/test.png', '%252e%252e/test.png', 'full\\bad.png'):
            with self.assertRaises(MediaIntegrityError):
                owned_path('https://baekgujuju.github.io/Archive/v2/media/' + path)

    def test_infrastructure_not_corruption(self):
        def error(p):
            raise RuntimeError('Git unavailable')
        with self.assertRaises(RuntimeError):
            validate_owned_media(self.record, error)

    def test_real_git_commit_not_worktree_or_index(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            def git(*args, check=True):
                return subprocess.run(['git', '-C', tmp, *args], capture_output=True, check=check)
            git('init', '-q')
            (root / 'README').write_text('fixture')
            git('add', 'README')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline')
            sha = git('rev-parse', 'HEAD').stdout.decode().strip()
            def reader(p):
                result = git('show', sha + ':' + p, check=False)
                return result.stdout if result.returncode == 0 else None
            target = root / self.path
            target.parent.mkdir(parents=True)
            target.write_bytes(self.raw)
            git('add', self.path)
            with self.assertRaisesRegex(MediaIntegrityError, 'absent'):
                validate_owned_media(self.record, reader)
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'include asset')
            sha = git('rev-parse', 'HEAD').stdout.decode().strip()
            target.unlink()  # Worktree missing but candidate commit is complete.
            self.assertEqual(validate_owned_media(self.record, reader), [self.path])


if __name__ == '__main__':
    unittest.main()
