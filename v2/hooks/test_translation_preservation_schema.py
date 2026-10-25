"""Execute the actual hook validator/merge with producer-shaped fixtures."""
import ast
import sys
from retry_state import mark, read as read_retry, due, clear_completed, preserve, digest
from state_guard import atomic_json_write
import json
from pathlib import Path
import tempfile
import unittest
import contextlib
import datetime
import hashlib
import os
import shutil
import uuid
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "post_ingest_hook.py"


class PreservationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = dict(Path=Path, json=json, TRANSLATIONS=self.root / "current.json",
                           log=lambda *_: None, os=__import__("os"))
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"_validate_translation_entry", "merge_translation_backup"}]
        exec(compile(tree, str(SOURCE), "exec"), self.values)

    def valid(self, entry):
        return self.values["_validate_translation_entry"](entry)

    def merge(self, backup, current):
        path = self.root / "backup.json"
        path.write_text(json.dumps(backup), encoding="utf-8")
        self.values["TRANSLATIONS"].write_text(json.dumps(current), encoding="utf-8")
        result = self.values["merge_translation_backup"](path)
        return result, json.loads(self.values["TRANSLATIONS"].read_text()), path

    def test_current_producer_success_failed_and_skipped_shapes(self):
        for status, translated in (("ok", ["번역"]), ("failed", []), ("skipped-no-prose", [])):
            with self.subTest(status=status):
                self.assertTrue(self.valid(dict(status=status, paragraphs=["source"], translated=translated)))

    def test_legacy_string_is_preserved(self):
        self.assertTrue(self.valid(dict(paragraphs=["source"], translated="번역")))

    def test_misaligned_arrays_preserved_without_relabeling(self):
        entry = dict(status="ok", paragraphs=["s"], translated=["a", "b"])
        result, current, _ = self.merge({"legacy": entry}, {})
        self.assertEqual(result, (1, ""))
        self.assertEqual(current["legacy"], entry)

    def test_malformed_entries_are_refused(self):
        for entry in (None, {}, dict(paragraphs=[{}], translated=[]),
                      dict(paragraphs=["s"], translated=[{}]),
                      dict(paragraphs=["s"], translated=1),
                      dict(paragraphs=[], translated=[], status={})):
            with self.subTest(entry=entry):
                self.assertFalse(self.valid(entry))

    def test_existing_current_entries_never_overwritten_and_backup_unchanged(self):
        old = dict(status="ok", paragraphs=["old"], translated=["이전"])
        new = dict(status="ok", paragraphs=["new"], translated=["현재"])
        result, current, path = self.merge({"shared": old, "missing": old}, {"shared": new})
        self.assertEqual(result, (1, ""))
        self.assertEqual(current, {"shared": new, "missing": old})
        self.assertEqual(json.loads(path.read_text()), {"shared": old, "missing": old})

    def test_invalid_entry_still_blocks_success_even_if_key_exists(self):
        result, current, path = self.merge({"bad": {"paragraphs": [], "translated": 4}}, {"bad": {}})
        self.assertEqual(result, (0, "partial"))
        self.assertEqual(current, {"bad": {}})
        self.assertTrue(path.is_file())

    def test_actual_main_restores_modern_cache_before_original_release(self):
        root = self.root
        hooks = root / "hooks"
        hooks.mkdir()
        data = root / "data"
        data.mkdir()
        cache = data / "translations.json"
        fixture = {"post": dict(status="ok", paragraphs=["source"], translated=["번역"]),
                   "failed": dict(status="failed", paragraphs=["s"], translated=[])}
        cache.write_text(json.dumps(fixture), encoding="utf-8")
        calls = []
        def run(cmd, **kwargs):
            name = Path(cmd[1]).name
            calls.append(name)
            if name == "build_data.py" and "--check" not in cmd:
                cache.write_text("{}", encoding="utf-8")
            return SimpleNamespace(returncode=1 if "--check" in cmd else 0)
        def release(_):
            self.assertEqual(json.loads(cache.read_text()), fixture)
            calls.append("release")
            return 0
        values = dict(self.values, git=lambda *a: SimpleNamespace(returncode=0, stdout='a'*40), mark=mark, read_retry=read_retry, due=due, clear_completed=clear_completed, preserve=preserve, digest=digest, atomic_json_write=atomic_json_write, input_hashes=lambda: {'source':'same'}, DATA_DIR=data, HOOKS_DIR=hooks, V2_ROOT=root,
                      LOCK_FILE=hooks / "lock", TRANSLATIONS=cache,
                      MANIFEST=data / "manifest.json", run=run,
                      sys=SimpleNamespace(executable="python"), tempfile=tempfile,
                      shutil=shutil, uuid=uuid, datetime=datetime.datetime,
                      timezone=datetime.timezone, os=os,
                      pipeline_lock=contextlib.nullcontext, PipelineLockTimeout=TimeoutError,
                      sha256_file=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest(),
                      fcntl=SimpleNamespace(flock=lambda *_: None, LOCK_EX=1, LOCK_NB=2, LOCK_UN=4),
                      recover_abandoned_backups=lambda: None, current_branch=lambda: "main",
                      unpushed_commit_list=lambda: [], changed_files=lambda: [],
                      PUBLISH_ENABLED=True, PUBLISH_BRANCH="main", publish_current_outputs=release)
        for key in ("RETRY_TRANSLATION", "RETRY_COMMIT", "RETRY_MEDIA", "RETRY_ENRICH"):
            values[key] = hooks / key
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"main", "_pipeline", "decide_pipeline", "_validate_translation_entry", "merge_translation_backup"}]
        exec(compile(tree, str(SOURCE), "exec"), values)
        self.assertEqual(values["main"](), 0)
        self.assertEqual(calls.count("release"), 2)
        self.assertLess(calls.index("release"), calls.index("translate_posts.py"))
        self.assertFalse(list(hooks.glob("v2_trans_*")))


if __name__ == "__main__":
    unittest.main()
