import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class BuilderRegressionTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("archive_builder", Path(__file__).with_name("build_data.py"))
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        b = self.builder
        b.V2_ROOT = self.root
        b.DATA_DIR = self.root / "data"
        b.MEDIA_THUMB = self.root / "media/thumb"
        b.MEDIA_FULL = self.root / "media/full"
        b.POSTS_JSON = self.root / "posts.json"
        b.WEB_INDEX_JSON = self.root / "index.json"
        b.IB_LIBRARY_JSON = self.root / "ib-library.json"
        b.POSTS_JSON.write_text(json.dumps([{"tweetId":"x1","sourceUrl":"https://x.com/u/status/1","fullText":"sample"}]), encoding="utf-8")
        b.WEB_INDEX_JSON.write_text(json.dumps({"articles":[{"id":"web1","fullText":"web body"}]}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_web_only_edit_and_builder_version_trigger_rebuild(self):
        b = self.builder
        b.build_data()
        self.assertEqual(b.check_changes(), 0)
        b.WEB_INDEX_JSON.write_text('{"articles":[]}', encoding="utf-8")
        self.assertEqual(b.check_changes(), 1)
        b.BUILDER_VERSION = "future"
        self.assertEqual(b.check_changes(), 1)

    def test_staging_failure_preserves_previous_generation(self):
        b = self.builder
        b.build_data()
        original = (b.DATA_DIR / "manifest.json").read_bytes()
        b.process_posts = lambda *args: (_ for _ in ()).throw(RuntimeError("injected staging failure"))
        with self.assertRaises(SystemExit) as result:
            b.build_data()
        self.assertEqual(result.exception.code, 2)
        self.assertEqual((b.DATA_DIR / "manifest.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
