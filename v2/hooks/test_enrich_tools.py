#!/usr/bin/env python3
"""
v2/hooks/test_enrich_tools.py — GitHub 스크린샷 자동 분류 테스트.

- 캐시 히트는 zero model calls
- 변경된 이미지는 무효화 후 재처리
- 실패는 재시도 대상 (예외 전파)
- 모호한 경우 pending, 링크 발명 금지
- 검증되지 않은 repo는 저장하지 않음
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enrich_tools as et


class EnrichToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # 테스트용 가짜 이미지
        self.img1 = self.tmp / "test1.png"
        self.img1.write_bytes(b"fake-image-data-1")
        self.img2 = self.tmp / "test2.png"
        self.img2.write_bytes(b"fake-image-data-2")

    def test_content_hash_stable(self):
        """같은 파일은 같은 해시."""
        h1 = et.sha256_file(str(self.img1))
        h2 = et.sha256_file(str(self.img1))
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_content_hash_changes_on_modification(self):
        """파일 변경 시 해시 변경 (무효화)."""
        h1 = et.sha256_file(str(self.img1))
        self.img1.write_bytes(b"fake-image-data-1-modified")
        h2 = et.sha256_file(str(self.img1))
        self.assertNotEqual(h1, h2)

    def test_cache_hit_zero_model_calls(self):
        """캐시 히트 시 모델 호출 없음."""
        cache = {}
        model_calls = [0]
        content_hash = et.sha256_file(str(self.img1))
        entry = {"title": "test", "repo_url": "https://github.com/a/b", "status": "verified"}
        cache_key = f"tweet123:{self.img1.name}"
        cache[cache_key] = {"content_hash": content_hash, "entry": entry, "version": 1}

        with patch.object(et, "ocr_image") as mock_ocr:
            result_entry, status = et.enrich_one("tweet123", str(self.img1), "http://x", cache, model_calls)
            mock_ocr.assert_not_called()

        self.assertEqual(status, "cached")
        self.assertEqual(result_entry, entry)
        self.assertEqual(model_calls[0], 0)

    def test_changed_image_invalidates_cache(self):
        """이미지 변경 시 캐시 무효화 후 재처리."""
        cache = {}
        model_calls = [0]
        old_hash = et.sha256_file(str(self.img1))
        cache_key = f"tweet123:{self.img1.name}"
        cache[cache_key] = {"content_hash": old_hash, "entry": {"title": "old"}, "version": 1}

        # 이미지 변경
        self.img1.write_bytes(b"completely-different-image")

        with patch.object(et, "ocr_image", return_value=None) as mock_ocr:
            result_entry, status = et.enrich_one("tweet123", str(self.img1), "http://x", cache, model_calls)
            mock_ocr.assert_called_once()  # 재처리됨

        self.assertEqual(status, "pending")  # OCR 실패 → pending
        self.assertEqual(model_calls[0], 1)

    def test_verified_repo_saved(self):
        """검증된 repo는 저장됨."""
        cache = {}
        model_calls = [0]
        fake_ocr = {
            "github_urls": ["https://github.com/morluto/rea"],
            "title": "REA Reverse Engineer Anything",
            "description": "Reverse engineering tool",
        }
        with patch.object(et, "ocr_image", return_value=fake_ocr), \
             patch.object(et, "verify_github_repo", return_value=(True, "Test desc", 100)):
            entry, status = et.enrich_one("tweet999", str(self.img1), "http://x", cache, model_calls)

        self.assertEqual(status, "verified")
        self.assertEqual(entry["repo_url"], "https://github.com/morluto/rea")
        self.assertEqual(entry["status"], "verified")
        self.assertIn("content_hash", entry)

    def test_unverified_repo_not_saved(self):
        """존재하지 않는 repo는 저장하지 않음 (pending)."""
        cache = {}
        model_calls = [0]
        fake_ocr = {
            "github_urls": ["https://github.com/fakeowner/fakerepo12345"],
            "title": "Fake",
            "description": "",
        }
        with patch.object(et, "ocr_image", return_value=fake_ocr), \
             patch.object(et, "verify_github_repo", return_value=(False, "", 0)):
            entry, status = et.enrich_one("tweet888", str(self.img1), "http://x", cache, model_calls)

        self.assertEqual(status, "pending")
        self.assertIsNone(entry)

    def test_ambiguous_no_invented_link(self):
        """모호한 경우 링크 발명 금지."""
        cache = {}
        model_calls = [0]
        with patch.object(et, "ocr_image", return_value=None), \
             patch.object(et, "search_github_by_title", return_value=[]):
            entry, status = et.enrich_one("tweet777", str(self.img1), "http://x", cache, model_calls)

        self.assertEqual(status, "pending")
        self.assertIsNone(entry)

    def test_failure_raises_for_retry(self):
        """실패는 예외 전파 (재시도 대상)."""
        cache = {}
        model_calls = [0]
        with patch.object(et, "ocr_image", side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                et.enrich_one("tweet666", str(self.img1), "http://x", cache, model_calls)

    def test_github_url_extraction(self):
        """GitHub URL 패턴 추출."""
        m = et.GITHUB_RE.search("Check out https://github.com/morluto/rea for more")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "morluto")
        self.assertEqual(m.group(2), "rea")

    def test_negative_cache(self):
        """GitHub 아닌 이미지도 캐시 (negative)."""
        cache = {}
        model_calls = [0]
        with patch.object(et, "ocr_image", return_value={"github_urls": [], "title": "cat photo", "description": ""}), \
             patch.object(et, "search_github_by_title", return_value=[]):
            entry1, status1 = et.enrich_one("tweet555", str(self.img1), "http://x", cache, model_calls)

        self.assertEqual(status1, "pending")

        # 두 번째 호출은 캐시 히트
        model_calls2 = [0]
        with patch.object(et, "ocr_image") as mock_ocr:
            entry2, status2 = et.enrich_one("tweet555", str(self.img1), "http://x", cache, model_calls2)
            mock_ocr.assert_not_called()

        self.assertEqual(status2, "cached")
        self.assertEqual(model_calls2[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
