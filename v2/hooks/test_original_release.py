"""Run the candidate's real main/helper bodies with synthetic subprocesses."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT/'candidate/v2/hooks/post_ingest_hook.py'
if not SOURCE.is_file():
    SOURCE = ROOT/'post_ingest_hook.py'


def functions(names):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    return compile(tree, str(SOURCE), 'exec')


class OriginalReleaseTests(unittest.TestCase):
    def scenario(self, original_rc=0, translation_rc=0):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp); hooks = root/'hooks'; hooks.mkdir()
            for name in ('enrich_tools.py', 'media-mirror.py'):
                (root/name).write_text('synthetic')
            calls = []
            def run(cmd, **kw):
                name = Path(cmd[1]).name
                calls.append(name + (' --check' if '--check' in cmd else ''))
                rc = 1 if '--check' in cmd else translation_rc if name == 'translate_posts.py' else 0
                return SimpleNamespace(returncode=rc)
            def release(pending):
                calls.append('release')
                return original_rc if calls.count('release') == 1 else 0
            values = dict(HOOKS_DIR=hooks, V2_ROOT=root, LOCK_FILE=hooks/'lock',
                TRANSLATIONS=root/'data/translations.json', MANIFEST=root/'data/manifest.json',
                RETRY_TRANSLATION=hooks/'.retry_translation', RETRY_COMMIT=hooks/'.retry_commit',
                RETRY_MEDIA=hooks/'.retry_media', RETRY_ENRICH=hooks/'.retry-enrich',
                PUBLISH_ENABLED=True, PUBLISH_BRANCH='main',
                fcntl=SimpleNamespace(flock=lambda *_: None, LOCK_EX=1, LOCK_NB=2, LOCK_UN=4),
                log=lambda *_: None, recover_abandoned_backups=lambda: None,
                current_branch=lambda: 'main', unpushed_commit_list=lambda: [],
                changed_files=lambda: [], run=run, sys=SimpleNamespace(executable='python'),
                publish_current_outputs=release, datetime=datetime, timezone=timezone)
            exec(functions({'main', 'decide_pipeline', 'compile_article_media_map'}), values)
            result = values['main']()
            return result, calls, (hooks/'.retry_translation').exists()

    def test_original_published_before_any_optional_provider(self):
        rc, calls, pending = self.scenario()
        self.assertEqual(rc, 0)
        self.assertLess(calls.index('media-mirror.py'), calls.index('release'))
        self.assertLess(calls.index('release'), calls.index('enrich_tools.py'))
        self.assertLess(calls.index('release'), calls.index('translate_posts.py'))
        self.assertEqual(calls.count('release'), 2)
        self.assertFalse(pending)

    def test_required_original_publication_failure_stops_optional(self):
        rc, calls, pending = self.scenario(original_rc=1)
        self.assertEqual(rc, 1)
        self.assertNotIn('enrich_tools.py', calls)
        self.assertNotIn('translate_posts.py', calls)
        self.assertTrue(pending)

    def test_optional_failure_keeps_retry_after_original_release(self):
        rc, calls, pending = self.scenario(translation_rc=1)
        self.assertEqual(rc, 1)
        self.assertEqual(calls.count('release'), 2)
        self.assertTrue(pending)

    def helper(self, manifest=True, files=None, push=True, git_rc=0):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            calls = []
            def git(args):
                calls.append(args); return SimpleNamespace(returncode=git_rc)
            values = dict(update_manifest_translation_hash=lambda: manifest,
                changed_files=lambda: ['data/posts-x.json'] if files is None else files,
                git=git, COMMIT_PATHS=['data/', 'media/', 'ib-library.json'],
                RETRY_COMMIT=Path(temp)/'retry', datetime=datetime, timezone=timezone,
                PUBLISH_ENABLED=True, do_push=lambda: calls.append('fixed_commit_push') or push,
                publish_pending=lambda _: 0, log=lambda *_: None)
            exec(functions({'publish_current_outputs'}), values)
            return values['publish_current_outputs'](), calls

    def test_manifest_failure_blocks_all_git_calls(self):
        self.assertEqual(self.helper(manifest=False), (1, []))

    def test_commit_uses_same_explicit_paths_and_fixed_gate(self):
        rc, calls = self.helper()
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0], ['add', '--', 'data/', 'media/', 'ib-library.json'])
        self.assertEqual(calls[1][-4:], ['--', 'data/', 'media/', 'ib-library.json'])
        self.assertEqual(calls[-1], 'fixed_commit_push')

    def test_git_or_push_failure_not_reported_success(self):
        self.assertEqual(self.helper(git_rc=1)[0], 1)
        self.assertEqual(self.helper(push=False)[0], 1)


if __name__ == '__main__':
    unittest.main()
