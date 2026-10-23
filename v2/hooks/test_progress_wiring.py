import ast
from collections import namedtuple
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, function, values):
    path = ROOT/'candidate/v2/hooks'/name
    if not path.is_file():
        path = ROOT/name
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
    exec(compile(tree, str(path), 'exec'), values)
    return values[function]


class ProgressWiringTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.values = dict(Path=Path, HOOKS_DIR=ROOT, log=lambda *_: None,
            start_phase=self.start, finish_phase=self.finish, os=os,
            sys=SimpleNamespace(executable='python'), subprocess=subprocess,
            run_grouped=lambda *_, **kw: SimpleNamespace(returncode=0, stdout='', stderr=''))

    def start(self, root, actor, phase, budget, **kw):
        event = dict(actor=actor, phase=phase, budget=budget, **kw)
        self.events.append(('start', event)); return event

    def finish(self, root, event, **kw):
        self.events.append(('finish', event, kw))

    def test_hook_records_provider_budget_and_result(self):
        run = load('post_ingest_hook.py', 'run', self.values)
        run(['python', '/some/path/translate_posts.py'], timeout=1800)
        self.assertEqual(self.events[0][1]['phase'], 'translate_posts')
        self.assertEqual(self.events[0][1]['budget'], 1800)
        self.assertEqual(self.events[-1][2]['rc'], 0)

    def test_hook_timeout_finishes_evidence_and_propagates(self):
        def timeout(*_, **kw): raise subprocess.TimeoutExpired('synthetic', 10)
        self.values['run_grouped'] = timeout
        run = load('post_ingest_hook.py', 'run', self.values)
        with self.assertRaises(subprocess.TimeoutExpired): run(['python', 'enrich_tools.py'], timeout=10)
        self.assertIsNone(self.events[-1][2]['rc'])

    def test_phase_metadata_does_not_copy_arbitrary_command_argument(self):
        run = load('post_ingest_hook.py', 'run', self.values)
        run(['unknown', 'https://example.com/private-token'])
        self.assertEqual(self.events[0][1]['phase'], 'command')

    def test_scheduler_preserves_file_failure_rc_and_phase(self):
        self.values['StepResult'] = namedtuple('StepResult', ['kind', 'rc'])
        self.values['run_grouped'] = lambda *_, **kw: SimpleNamespace(returncode=3, stdout='', stderr='')
        run = load('scheduler_runner.py', 'run_step', self.values)
        result = run('drive_poll', ROOT/'synthetic.py', 600)
        self.assertEqual((result.kind, result.rc), ('failed', 3))
        self.assertEqual(self.events[-1][2]['rc'], 3)

    def test_scheduler_timeout_keeps_existing_result_semantics(self):
        self.values['StepResult'] = namedtuple('StepResult', ['kind', 'rc'])
        def timeout(*_, **kw): raise subprocess.TimeoutExpired('synthetic', 10)
        self.values['run_grouped'] = timeout
        run = load('scheduler_runner.py', 'run_step', self.values)
        result = run('monitor', ROOT/'synthetic.py', 10)
        self.assertEqual(result.kind, 'timeout')
        self.assertIsNone(self.events[-1][2]['rc'])

    def test_worker_propagates_same_cycle_to_scheduler_environment(self):
        self.values['TIMEOUT_TOTAL'] = 4800
        def child(*_, **kw):
            self.assertEqual(kw['env']['ARCHIVE_POLL_ID'], self.events[0][1]['cycle'])
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        self.values['run_grouped'] = child
        run = load('persistent_worker.py', 'run_poll', self.values)
        with patch('pipeline_progress.start_phase', side_effect=self.start), patch('pipeline_progress.finish_phase', side_effect=self.finish):
            run()
        self.assertEqual(self.events[0][1]['actor'], 'worker')
        self.assertEqual(self.events[-1][2]['rc'], 0)


if __name__ == '__main__':
    unittest.main()
