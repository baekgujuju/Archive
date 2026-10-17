"""Run actual hook control flow: failed image mirroring must still publish text."""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# The production hook runs on Linux. Windows uses a test-only lock shim;
# publication/build/lock concurrency are separately tested on the Linux host.
if sys.platform == 'win32' and 'fcntl' not in sys.modules:
    sys.modules['fcntl'] = types.SimpleNamespace(flock=lambda *a: None, LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_ingest_hook as hook

class MirrorPublicationTests(unittest.TestCase):
    def exercise(self, mirror_error):
        workspace = Path(__file__).resolve().parents[1] / '.qa-test-temp'
        workspace.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace) as name:
            root = Path(name).resolve()
            self.assertTrue(root.is_relative_to(workspace.resolve()))
            (root/'data').mkdir(); (root/'hooks').mkdir(); (root/'media-mirror.py').touch()
            # P2 배치 1: 매니페스트 업데이트 실패 시 커밋이 막힌다
            # (fail-closed). 테스트 공간에도 구조가 유효한 매니페스트 +
            # 번역 파일이 있어야 2f 단계가 성공한다 (필수 청크 3종).
            chunks = {}
            for name in ("x", "web", "ib"):
                chunks[f"posts-{name}"] = {
                    "file": f"data/posts-{name}.json",
                    "sha256": "0" * 64,
                    "count": 0,
                }
            (root/'data/manifest.json').write_text(
                json.dumps({"source_hash": "x", "chunks": chunks}))
            (root/'data/translations.json').write_text('{}')
            calls=[]
            def run(cmd, **kwargs):
                calls.append([str(v) for v in cmd])
                if any(str(v).endswith('media-mirror.py') for v in cmd):
                    if isinstance(mirror_error, Exception): raise mirror_error
                    return subprocess.CompletedProcess(cmd, mirror_error, '', 'failed' if mirror_error else '')
                return subprocess.CompletedProcess(cmd, 1 if '--check' in cmd else 0, '', '')
            paths={'V2_ROOT':root,'DATA_DIR':root/'data','HOOKS_DIR':root/'hooks','LOCK_FILE':root/'hooks/.lock','TRANSLATIONS':root/'data/translations.json','MANIFEST':root/'data/manifest.json','RETRY_TRANSLATION':root/'hooks/.retry_translation','RETRY_COMMIT':root/'hooks/.retry_commit','RETRY_MEDIA':root/'hooks/.retry_media','RETRY_ENRICH':root/'hooks/.retry-enrich','PUBLISH_ENABLED':True}
            with patch.multiple(hook, **paths), patch.object(hook,'run',side_effect=run), patch.object(hook,'unpushed_commit_list',return_value=[]), patch.object(hook,'changed_files',side_effect=[[],['data/posts-x.json','data/media-map.json','media/full/test.png'],[],['data/media-map.json','media/full/test.png']]), patch.object(hook,'git',side_effect=lambda args,**kw: subprocess.CompletedProcess(args,0,'','')) as git, patch.object(hook,'do_push',return_value=True) as publish:
                self.assertEqual(hook.main(),1 if mirror_error else 0)
                self.assertEqual((root/'hooks/.retry_media').exists(),bool(mirror_error))
                self.assertTrue(any(any(v.endswith('media-mirror.py') for v in cmd) for cmd in calls))
                self.assertTrue(any(any(v.endswith('translate_posts.py') for v in cmd) for cmd in calls))
                publish.assert_called_once()
                self.assertTrue(any(c.args[0][:2]==['add','--'] for c in git.call_args_list))
                self.assertTrue(any('commit' in c.args[0] for c in git.call_args_list))
                for call in git.call_args_list:
                    if 'add' in call.args[0] or 'commit' in call.args[0]:
                        self.assertIn('data/',call.args[0]); self.assertIn('media/',call.args[0])
                if mirror_error:
                    # Recover on a second worker run even when source data has not changed.
                    mirror_error=0
                    self.assertEqual(hook.main(),0)
                    self.assertFalse((root/'hooks/.retry_media').exists())
                    self.assertEqual(publish.call_count,2)
                    self.assertEqual(sum('--check' in cmd for cmd in calls),1)
    def test_review_branch_cannot_publish_to_main(self):
        with patch.object(hook,'PUBLISH_BRANCH','main'), patch.object(hook,'current_branch',return_value='review/v2-ux'), patch.object(hook,'git') as git:
            self.assertFalse(hook.do_push())
            git.assert_not_called()
    def test_main_branch_can_publish_to_main(self):
        def _git(args, **kw):
            # P2 배치 1: do_push는 HEAD를 SHA로 고정해 검증한 뒤 같은 SHA를
            # 푸시한다. rev-parse는 SHA를, 나머지는 성공을 반환한다.
            if args[:2] == ['rev-parse', 'HEAD']:
                return subprocess.CompletedProcess(args, 0, 'a' * 40 + '\n', '')
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(hook,'PUBLISH_BRANCH','main'), patch.object(hook,'current_branch',return_value='main'), patch.object(hook,'validate_commit_artifacts',return_value=(True,'')), patch.object(hook,'git',side_effect=_git) as git:
            self.assertTrue(hook.do_push())
            self.assertEqual(git.call_args.args[0],['push','origin','a' * 40 + ':refs/heads/main'])
    def test_monitor_retries_media_with_unchanged_sources(self):
        import monitor
        workspace=Path(__file__).resolve().parents[1]/'.qa-test-temp'
        workspace.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace) as name:
            root=Path(name).resolve(); self.assertTrue(root.is_relative_to(workspace.resolve()))
            (root/'.retry_media').touch()
            hashes={'x':'same'}
            def run(cmd,**kw): return subprocess.CompletedProcess(cmd,0,'0' if 'rev-list' in cmd else '', '')
            # P1-7 이후 monitor는 후크를 run_grouped로 실행한다 (subprocess.run
            # 모킹만으로는 후크 호출이 실제 파일로 간다). 후크 호출은
            # run_grouped으로 모킹하고, git 호출은 subprocess.run으로 모킹한다.
            hook_calls=[]
            def run_grouped(cmd,**kw):
                hook_calls.append([str(v) for v in cmd])
                return subprocess.CompletedProcess(cmd,0,'','')
            with patch.multiple(monitor,HOOKS_DIR=root,V2_ROOT=root,LOCK_FILE=root/'.monitor.lock'), patch.object(sys,'argv',['monitor.py']), patch.object(monitor,'current_hashes',return_value=hashes), patch.object(monitor,'load_state',return_value={'hashes':hashes}), patch.object(monitor,'save_state'), patch.object(monitor,'run_grouped',side_effect=run_grouped), patch.object(monitor.subprocess,'run',side_effect=run) as calls:
                self.assertEqual(monitor.main(),0)
                self.assertTrue(any(any(str(v).endswith('post_ingest_hook.py') for v in c) for c in hook_calls))
    def test_nonzero_mirror_still_commits_and_publishes(self): self.exercise(1)
    def test_timeout_mirror_still_commits_and_publishes(self): self.exercise(subprocess.TimeoutExpired('mirror',600))
    def test_successful_mirror_commits_and_publishes(self): self.exercise(0)

if __name__ == '__main__': unittest.main()
