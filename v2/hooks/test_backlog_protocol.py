"""Isolated hook -> monitor -> scheduler contract; no operational worker starts."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
import test_retry_execution as fixtures
import retry_state as retry


class BacklogProtocol(unittest.TestCase):
    def scenario(self, translation_failure=False, push_failure=False, followup_failure=False):
        fixture = fixtures.HookExecution()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        h = fixture.h
        h.PUBLISH_ENABLED = True
        fixture.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        if translation_failure:
            fixture.fail['translation'] = 1
        pushes = []
        def push():
            pushes.append(1)
            if push_failure or (followup_failure and len(pushes) == 2):
                return False
            h.PUBLISHED_SHA = fixture.git('rev-parse', 'HEAD').strip()
            return True
        h.do_push = push
        m = fixtures.load('monitor')
        s = fixtures.load('scheduler_runner')
        for module in (m, s):
            module.HOOKS_DIR = fixture.hooks
            module.V2_ROOT = fixture.v2
            module.LOCK_FILE = fixture.hooks / (module.__name__ + '.lock')
        m.STATE_FILE = fixture.hooks / 'state.json'
        m.current_hashes = h.input_hashes
        s.LOG_FILE = fixture.hooks / 'scheduler.log'
        logs = []
        phases = []
        s.log = logs.append
        s.start_phase = lambda *a: {}
        s.finish_phase = lambda *a, **kw: phases.append(kw)
        codes = []
        def hook(cmd, **kw):
            with patch.dict(os.environ, kw['env']), patch.object(sys, 'argv', ['hook']):
                rc = h.main()
            codes.append(rc)
            return fixtures.result(rc)
        def scheduler_step(cmd, **kw):
            if Path(cmd[1]).name == 'monitor.py':
                with patch.dict(os.environ, kw['env']), patch.object(sys, 'argv', ['monitor']):
                    rc = m.main()
                return fixtures.result(rc)
            return fixtures.result()
        git_result = subprocess.CompletedProcess([], 0, '', '')
        # Patch monitor's git queries via its own proxy; never replace the shared subprocess module.
        from types import SimpleNamespace
        m.subprocess = SimpleNamespace(run=lambda *a, **kw: git_result, TimeoutExpired=subprocess.TimeoutExpired)
        with patch.object(m, 'run_grouped', hook), patch.object(s, 'run_grouped', scheduler_step), \
             patch.dict(sys.modules, {'monitor': m}), patch.object(sys, 'argv', ['scheduler']):
            rc = s.main()
        return fixture, m, codes, rc, logs, phases

    def test_published_backlog_wait_advances_watermark_and_scheduler_succeeds(self):
        fixture, m, codes, rc, logs, phases = self.scenario(translation_failure=True)
        self.assertEqual(codes, [10])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(m.STATE_FILE.read_text())['hashes'], {'fixture': 'source'})
        self.assertTrue(any('backlog_wait' in line for line in logs))
        self.assertTrue(any(p.get('status') == 'backlog_wait' and p['rc'] == 10 for p in phases))
        self.assertTrue(fixture.h.RETRY_TRANSLATION.exists())
        self.assertEqual(sum('build_data.py' in str(c) and '--check' not in c for c in fixture.calls), 1)

    def test_publish_failure_with_backlog_keeps_watermark_and_scheduler_fails(self):
        fixture, m, codes, rc, logs, phases = self.scenario(push_failure=True)
        self.assertEqual(codes, [1])
        self.assertNotEqual(rc, 0)
        self.assertFalse(m.STATE_FILE.exists())
        evidence = json.loads(next(fixture.hooks.glob('.hook-result-*')).read_text())
        self.assertEqual(evidence['required_status'], 'failed')
        self.assertTrue(evidence['backlog'])

    def test_publish_success_without_backlog(self):
        fixture, m, codes, rc, logs, phases = self.scenario()
        self.assertEqual(codes, [0])
        self.assertEqual(rc, 0)
        self.assertTrue(m.STATE_FILE.exists())

    def test_original_success_followup_failure_remains_failure(self):
        fixture, m, codes, rc, logs, phases = self.scenario(followup_failure=True)
        self.assertEqual(codes, [1])
        self.assertNotEqual(rc, 0)
        self.assertFalse(m.STATE_FILE.exists())
        evidence = json.loads(next(fixture.hooks.glob('.hook-result-*')).read_text())
        self.assertEqual(evidence['publication_status'], 'failed')
        self.assertEqual(evidence['original_publication_status'], 'published')

    def test_legacy_and_changed_queue_are_never_cleared(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.retry_media'
            path.write_text('2026-10-20T00:00:00Z')
            retry.preserve(path.parent, path.parent / 'data')
            rec = retry.read(path)
            retry.clear_completed(path, rec)
            self.assertTrue(path.exists())
            self.assertEqual(rec['reason_code'], 'legacy_unknown')
            path.unlink()
            old = retry.mark(path, 'mirror_failed')
            new = retry.mark(path, 'mirror_failed', 'new generation')
            retry.clear_completed(path, old)
            self.assertEqual(retry.read(path), new)

    def test_missing_queue_references_are_preserved_without_unlink(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hooks' / '.retry_media'
            for refs in (None, [], [None, '']):
                with self.subTest(queue_refs=refs):
                    rec = retry.mark(path, 'mirror_failed')
                    rec['queue_ref'] = None
                    rec.pop('queue_refs', None)
                    if refs is not None:
                        rec['queue_refs'] = refs
                    path.write_text(json.dumps(rec))
                    with patch.object(Path, 'unlink') as unlink:
                        retry.clear_completed(path, rec)
                    unlink.assert_not_called()
                    saved = retry.read(path)
                    self.assertEqual(saved, dict(rec, manual_review=True,
                                                preservation_reason='missing_queue_reference'))
                    self.assertTrue(path.exists())

    def test_single_or_multiple_queue_reference_still_allows_completion(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hooks' / '.retry_media'
            for mode in ('single_only', 'single_with_empty_list', 'multiple_only'):
                with self.subTest(mode=mode):
                    rec = retry.mark(path, 'mirror_failed')
                    if mode == 'single_only':
                        rec.pop('queue_refs')
                    elif mode == 'single_with_empty_list':
                        rec['queue_refs'] = []
                    else:
                        rec['queue_ref'] = None
                    path.write_text(json.dumps(rec))
                    retry.clear_completed(path, rec)
                    self.assertFalse(path.exists())

    def test_source_change_during_hook_returns_incomplete(self):
        fixture = fixtures.HookExecution(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        values = iter([{'fixture': 'old'}, {'fixture': 'new'}])
        fixture.h.input_hashes = lambda: next(values)
        self.assertEqual(fixture.h.main(), 2)

    def test_unverified_ten_is_rejected_by_monitor_and_scheduler(self):
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m = fixtures.load('monitor'); s = fixtures.load('scheduler_runner')
            m.HOOKS_DIR = s.HOOKS_DIR = root
            m.V2_ROOT = root; m.STATE_FILE = root/'state'; m.LOCK_FILE = root/'lock'
            m.current_hashes = lambda: {'fixture': 'new'}
            m.subprocess = SimpleNamespace(run=lambda *a, **kw: fixtures.result(), TimeoutExpired=subprocess.TimeoutExpired)
            m.run_grouped = lambda *a, **kw: fixtures.result(10)
            s.log = lambda *a: None; s.start_phase = lambda *a: {}; s.finish_phase = lambda *a, **kw: None
            s.run_grouped = lambda *a, **kw: fixtures.result(10)
            with patch.object(sys, 'argv', ['monitor']), patch.dict(sys.modules, {'monitor': m}):
                self.assertEqual(m.main(), 1)
                self.assertFalse(m.STATE_FILE.exists())
                self.assertEqual(s.run_step('monitor', root/'monitor.py', 10).kind, 'failed')

    def test_unchanged_source_retries_only_due_kind_without_build(self):
        fixture = fixtures.HookExecution(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        fixture.built = True
        retry.mark(fixture.h.RETRY_TRANSLATION, 'translation_incomplete')
        rec = retry.read(fixture.h.RETRY_TRANSLATION)
        rec['next_retry_at'] = '2000-01-01T00:00:00+00:00'
        fixture.h.RETRY_TRANSLATION.write_text(json.dumps(rec))
        retry.mark(fixture.h.RETRY_MEDIA, 'mirror_pending')
        self.assertEqual(fixture.h.main(), 10)
        scripts = [Path(c[1]).name for c in fixture.calls if c[0] != 'git']
        self.assertEqual(scripts, ['build_data.py', 'translate_posts.py'])
        self.assertTrue(all('--check' in c for c in fixture.calls if 'build_data.py' in str(c)))
        self.assertFalse(fixture.h.RETRY_TRANSLATION.exists())
        self.assertTrue(fixture.h.RETRY_MEDIA.exists())

    def test_queue_write_failure_cannot_succeed(self):
        fixture = fixtures.HookExecution(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        with patch.object(fixture.h, 'preserve', side_effect=OSError('disk unavailable')):
            self.assertEqual(fixture.h.main(), 1)

    def test_retired_source_items_are_preserved_for_review(self):
        fixture = fixtures.HookExecution(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        path = fixture.data / 'posts-x.json'
        path.write_text('{"posts":[{"id":"old","fullText":"old body"}]}')
        marker = fixture.h.RETRY_TRANSLATION
        rec = retry.mark(marker, 'translation_incomplete')
        path.write_text('{"posts":[{"id":"new","fullText":"new body"}]}')
        retry.clear_completed(marker, rec)
        self.assertTrue(marker.exists())
        self.assertTrue(retry.read(marker)['manual_review'])
        self.assertTrue(Path(rec['queue_ref']).exists())

    def test_phase_backlog_wait_can_be_read_by_health_checker(self):
        import tempfile
        import pipeline_progress as progress
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event = progress.start_phase(root, 'scheduler', 'monitor', 100)
            self.assertTrue(progress.finish_phase(root, event, rc=10, status='backlog_wait'))
            self.assertEqual(progress._read(root / '.pipeline-progress-scheduler.json')['status'], 'backlog_wait')

    def test_future_backlog_does_not_invoke_hook(self):
        import tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); hooks = root / 'hooks'; hooks.mkdir()
            retry.mark(hooks / '.retry_media', 'mirror_failed')
            m = fixtures.load('monitor')
            m.HOOKS_DIR = hooks; m.V2_ROOT = root
            m.STATE_FILE = hooks / 'state'; m.LOCK_FILE = hooks / 'lock'
            m.current_hashes = lambda: {'fixture': 'same'}
            m.save_state(m.current_hashes())
            m.subprocess = SimpleNamespace(run=lambda *a, **kw: fixtures.result(), TimeoutExpired=subprocess.TimeoutExpired)
            with patch.object(m, 'run_grouped', side_effect=AssertionError('hook must not run')), \
                 patch.object(sys, 'argv', ['monitor']):
                self.assertEqual(m.main(), 10)

    def test_multiple_checkpoints_count_as_one_attempt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / '.retry-enrich'
            retry.begin_attempt('first')
            retry.mark(marker, 'enrich_pending')
            self.assertEqual(retry.mark(marker, 'enrich_pending')['attempts'], 1)
            retry.begin_attempt('second')
            self.assertEqual(retry.mark(marker, 'enrich_pending')['attempts'], 2)

    def test_translation_counts_do_not_double_count_repeated_summary(self):
        fixture = fixtures.HookExecution(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        def worker(cmd, **kw):
            if len(cmd) > 1 and Path(cmd[1]).name == 'translate_posts.py':
                return subprocess.CompletedProcess(cmd, 1,
                    '답글 번역: 성공 0개, 실패 6개, 남은 백로그 6개\n'
                    '본문 번역: 남은 백로그 4개 (쿨다운 4개)\n'
                    '답글 번역: 실패 6개, 남은 백로그 6개\n', '429')
            return fixture.worker(cmd, **kw)
        fixture.h.run = worker
        self.assertEqual(fixture.h.main(), 10)
        marker = retry.read(fixture.h.RETRY_TRANSLATION)
        self.assertEqual(marker['count'], 10)
        self.assertEqual(marker['count_unit'], 'items')
        self.assertEqual(marker['reason_code'], 'provider_rate_limit')
