#!/usr/bin/env python3
"""
v2/hooks/test_enrich_tools.py — GitHub 스크린샷 자동 분류 테스트 v2.

- 캐시 히트는 zero model calls
- 변경된 이미지는 무효화 후 재처리
- 일시적 실패(503/네트워크)는 캐시하지 않음
- 구버전 캐시는 무시 (poisoned 무효화)
- 모호한 경우 pending, 링크 발명 금지
- 검증되지 않은 repo는 저장하지 않음
- 같은 이미지 여러 글에 전파
"""
import hashlib
import json
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
        self.img2 = self.tmp / "test2.png"
        self.img2.write_bytes(b"fake-image-data-2")

    def test_content_hash_stable(self):
        h1 = et.sha256_file(str(self.img1))
        h2 = et.sha256_file(str(self.img1))
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_content_hash_changes_on_modification(self):
        h1 = et.sha256_file(str(self.img1))
        self.img1.write_bytes(b"fake-image-data-1-modified")
        h2 = et.sha256_file(str(self.img1))
        self.assertNotEqual(h1, h2)

    def test_cache_hit_zero_model_calls(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        content_hash = et.sha256_file(str(self.img1))
        entry = {"title": "test", "repo_url": "https://github.com/a/b", "status": "verified"}
        cache_key = f"tweet123:{self.img1.name}"
        cache[cache_key] = {"content_hash": content_hash, "entry": entry, "version": et.CACHE_VERSION}

        with patch.object(et, "analyze_image") as mock_analyze:
            result_entry, status = et.enrich_one(
                "tweet123", str(self.img1), "http://x", "", cache, image_memo, model_calls)
            mock_analyze.assert_not_called()

        self.assertEqual(status, "cached")
        self.assertEqual(result_entry, entry)
        self.assertEqual(model_calls[0], 0)

    def test_old_cache_version_ignored(self):
        """구버전 캐시는 무시 (poisoned 무효화)."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        content_hash = et.sha256_file(str(self.img1))
        cache_key = f"tweet123:{self.img1.name}"
        # v1 poisoned 캐시
        cache[cache_key] = {"content_hash": content_hash, "entry": None, "version": 1}

        fake_analysis = {"github_urls": [], "title": "", "description_ko": "", "category_ko": ""}
        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)) as mock_analyze:
            result_entry, status = et.enrich_one(
                "tweet123", str(self.img1), "http://x", "", cache, image_memo, model_calls)
            mock_analyze.assert_called_once()  # 구버전 무시하고 재분석

        self.assertEqual(model_calls[0], 1)

    def test_temporary_failure_not_cached(self):
        """일시적 실패는 캐시하지 않음."""
        cache = {}
        image_memo = {}
        model_calls = [0]

        with patch.object(et, "analyze_image", return_value=(None, True)):
            entry1, status1 = et.enrich_one(
                "tweet123", str(self.img1), "http://x", "", cache, image_memo, model_calls)

        self.assertEqual(status1, "temporary_failure")
        self.assertIsNone(entry1)
        # 캐시에 저장되지 않음
        cache_key = f"tweet123:{self.img1.name}"
        self.assertNotIn(cache_key, cache)

        # 두 번째 호출도 재시도 (캐시 없음)
        model_calls2 = [0]
        with patch.object(et, "analyze_image", return_value=(None, True)) as mock_analyze:
            entry2, status2 = et.enrich_one(
                "tweet123", str(self.img1), "http://x", "", cache, image_memo, model_calls2)
            mock_analyze.assert_called_once()

        self.assertEqual(status2, "temporary_failure")

    def test_changed_image_invalidates_cache(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        old_hash = et.sha256_file(str(self.img1))
        cache_key = f"tweet123:{self.img1.name}"
        cache[cache_key] = {"content_hash": old_hash, "entry": {"title": "old"}, "version": et.CACHE_VERSION}

        self.img1.write_bytes(b"completely-different-image")
        fake_analysis = {"github_urls": [], "title": "", "description_ko": "", "category_ko": ""}

        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)) as mock_analyze:
            result_entry, status = et.enrich_one(
                "tweet123", str(self.img1), "http://x", "", cache, image_memo, model_calls)
            mock_analyze.assert_called_once()

        self.assertEqual(status, "pending")
        self.assertEqual(model_calls[0], 1)

    def test_verified_repo_saved(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "github_urls": ["https://github.com/morluto/rea"],
            "title": "REA Reverse Engineer Anything",
            "description_ko": "리버스 엔지니어링 도구",
            "category_ko": "리버스 엔지니어링·개발 도구",
        }
        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)), \
             patch.object(et, "verify_github_repo", return_value=(True, "desc", "", False)):
            entry, status = et.enrich_one(
                "tweet999", str(self.img1), "http://x", "", cache, image_memo, model_calls)

        self.assertEqual(status, "verified")
        self.assertEqual(entry["repo_url"], "https://github.com/morluto/rea")
        self.assertEqual(entry["status"], "verified")
        self.assertEqual(entry["category"], "리버스 엔지니어링·개발 도구")
        self.assertIn("content_hash", entry)

    def test_fork_skipped(self):
        """포크는 건너뜀."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "github_urls": ["https://github.com/somefork/rea"],
            "title": "REA",
            "description_ko": "",
            "category_ko": "",
        }
        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)), \
             patch.object(et, "verify_github_repo", return_value=(True, "desc", "", True)):
            entry, status = et.enrich_one(
                "tweet888", str(self.img1), "http://x", "", cache, image_memo, model_calls)

        # 포크는 건너뛰고 pending
        self.assertEqual(status, "pending")
        self.assertIsNone(entry)

    def test_unverified_repo_not_saved(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "github_urls": ["https://github.com/fakeowner/fakerepo12345"],
            "title": "Fake",
            "description_ko": "",
            "category_ko": "",
        }
        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)), \
             patch.object(et, "verify_github_repo", return_value=(False, "", "", False)):
            entry, status = et.enrich_one(
                "tweet888", str(self.img1), "http://x", "", cache, image_memo, model_calls)

        self.assertEqual(status, "pending")
        self.assertIsNone(entry)

    def test_text_github_url_handled(self):
        """텍스트 내 GitHub URL 처리."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        post_text = "Check out https://github.com/morluto/rea for more info"

        with patch.object(et, "verify_github_repo", return_value=(True, "desc", "", False)), \
             patch.object(et, "analyze_image") as mock_analyze:
            entry, status = et.enrich_one(
                "tweet777", None, "", post_text, cache, image_memo, model_calls)
            # 텍스트 URL로 처리되어 이미지 분석 불필요
            mock_analyze.assert_not_called()

        self.assertEqual(status, "verified")
        self.assertEqual(entry["repo_url"], "https://github.com/morluto/rea")

    def test_ambiguous_no_invented_link(self):
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {"github_urls": [], "title": "cat", "description_ko": "", "category_ko": ""}
        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)):
            entry, status = et.enrich_one(
                "tweet777", str(self.img1), "http://x", "", cache, image_memo, model_calls)

        self.assertEqual(status, "pending")
        self.assertIsNone(entry)

    def test_same_image_multiple_posts(self):
        """같은 이미지 여러 글에 전파 (메모이제이션)."""
        cache = {}
        image_memo = {}
        model_calls = [0]
        fake_analysis = {
            "github_urls": ["https://github.com/morluto/rea"],
            "title": "REA",
            "description_ko": "도구",
            "category_ko": "개발 도구",
        }
        with patch.object(et, "analyze_image", return_value=(fake_analysis, False)) as mock_analyze, \
             patch.object(et, "verify_github_repo", return_value=(True, "desc", "", False)):
            # 첫 번째 글
            entry1, status1 = et.enrich_one(
                "tweet1", str(self.img1), "http://x", "", cache, image_memo, model_calls)
            # 두 번째 글 (같은 이미지)
            entry2, status2 = et.enrich_one(
                "tweet2", str(self.img1), "http://x", "", cache, image_memo, model_calls)
            # 이미지 분석은 1번만
            self.assertEqual(mock_analyze.call_count, 1)

        self.assertEqual(status1, "verified")
        self.assertEqual(status2, "verified")
        # model_calls는 1 (두 번째는 메모 히트)
        self.assertEqual(model_calls[0], 1)

    def test_github_url_extraction(self):
        m = et.GITHUB_RE.search("Check out https://github.com/morluto/rea for more")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "morluto")
        self.assertEqual(m.group(2), "rea")

    def test_media_url_shapes(self):
        """다양한 media 형태 처리."""
        self.assertEqual(et.get_media_url("http://x.jpg"), "http://x.jpg")
        self.assertEqual(et.get_media_url({"url": "http://y.png"}), "http://y.png")
        self.assertEqual(et.get_media_url({"src": "http://z.gif"}), "http://z.gif")
        self.assertEqual(et.get_media_url({}), "")
        self.assertEqual(et.get_media_url(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
