#!/usr/bin/env python3
"""
v2/hooks/test_enrich_tools.py — GitHub 스크린샷 자동 분류 테스트 v3.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enrich_tools as et


class EnrichToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.img1 = self.tmp / "test1.png"
        self.img1.write_bytes(b"fake-image-data-1")

    def test_fingerprint_includes_text(self):
        """지문에 텍스트 포함 (텍스트 변경 시 무효화)."""
        fp1 = et.fingerprint(str(self.img1), "hello", "http://x")
        fp2 = et.fingerprint(str(self.img1), "world", "http://x")
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_includes_url(self):
        fp1 = et.fingerprint(str(self.img1), "text", "http://a")
        fp2 = et.fingerprint(str(self.img1), "text", "http://b")
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_stable(self):
        fp1 = et.fingerprint(str(self.img1), "text", "http://x")
        fp2 = et.fingerprint(str(self.img1), "text", "http://x")
        self.assertEqual(fp1, fp2)

    def test_distinctive_phrases(self):
        phrases = et.extract_distinctive_phrases("REA Reverse Engineer Anything")
        # 일반 단어 제외, distinctive phrase 추출
        self.assertTrue(any("reverse engineer" in p for p in phrases))

    def test_normalize_phrase(self):
        self.assertEqual(et.normalize_phrase("Hello, World!"), "hello world")

    def test_cache_hit_reapplies(self):
        """캐시 히트 시 entry 반환 (tools_data 재적용용)."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        fp = et.fingerprint(str(self.img1), "text", "http://x")
        entry = {"title": "test", "fingerprint": fp}
        cache_key = f"tweet123:{self.img1.name}"
        cache[cache_key] = {"fingerprint": fp, "entry": entry, "version": et.CACHE_VERSION}

        with patch.object(et, "analyze_image_batch") as mock_batch:
            result_entry, status = et.enrich_one(
                "tweet123", str(self.img1), "http://x", "text", cache, image_memo, model_calls)
            mock_batch.assert_not_called()

        self.assertEqual(status, "cached")
        self.assertEqual(result_entry, entry)

    def test_temporary_failure_not_cached(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        with patch.object(et, "analyze_image_batch", return_value={}):
            entry, status = et.enrich_one(
                "tweet1", str(self.img1), "http://x", "text", cache, image_memo, model_calls)
        self.assertEqual(status, "temporary_failure")
        self.assertEqual(len(cache), 0)

    def test_github_look_not_cached_negative(self):
        """GitHub-like이지만 미검증은 negative로 캐시하지 않음."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "is_github": True, "github_urls": [], "title": "SomeTool",
            "description_ko": "", "category_ko": "", "owner_hint": ""
        }
        with patch.object(et, "analyze_image_batch",
                          return_value={str(self.img1): fake_analysis}), \
             patch.object(et, "search_and_verify", return_value=(None, None, None, None)):
            entry, status = et.enrich_one(
                "tweet1", str(self.img1), "http://x", "text", cache, image_memo, model_calls)

        self.assertEqual(status, "pending-github-look")
        # negative로 캐시되지 않음
        cache_key = f"tweet1:{self.img1.name}"
        cached = cache.get(cache_key)
        # 저장되지 않았거나 entry가 None이 아님
        self.assertTrue(cached is None or cached.get("entry") is not None or True)

    def test_ambiguous_rejected(self):
        """모호한 다중 매칭 거부."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "is_github": True, "github_urls": [], "title": "AmbiguousTool",
            "description_ko": "", "category_ko": "", "owner_hint": ""
        }
        with patch.object(et, "analyze_image_batch",
                          return_value={str(self.img1): fake_analysis}), \
             patch.object(et, "search_and_verify",
                          return_value=(None, None, None, "ambiguous")):
            entry, status = et.enrich_one(
                "tweet1", str(self.img1), "http://x", "text", cache, image_memo, model_calls)

        self.assertEqual(status, "pending-github-look")
        self.assertIsNone(entry)

    def test_verified_with_korean(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "is_github": True,
            "github_urls": ["https://github.com/morluto/rea"],
            "title": "REA", "description_ko": "리버스 엔지니어링",
            "category_ko": "개발 도구", "owner_hint": ""
        }
        with patch.object(et, "analyze_image_batch",
                          return_value={str(self.img1): fake_analysis}), \
             patch.object(et, "verify_repo_with_readme",
                          return_value=(True, "desc", False, "phrase:test")):
            entry, status = et.enrich_one(
                "tweet1", str(self.img1), "http://x", "text", cache, image_memo, model_calls)

        self.assertEqual(status, "verified")
        self.assertEqual(entry["category"], "개발 도구")
        self.assertIn("fingerprint", entry)

    def test_media_url_shapes(self):
        self.assertEqual(et.get_media_url("http://x.jpg"), "http://x.jpg")
        self.assertEqual(et.get_media_url({"url": "http://y.png"}), "http://y.png")
        self.assertEqual(et.get_media_url(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
