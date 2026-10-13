"""Run actual hook control flow: failed image mirroring must still publish text."""
import importlib.util
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
            calls=[]
            def run(cmd, **kwargs):
                calls.append([str(v) for v in cmd])
                if any(str(v).endswith('media-mirror.py') for v in cmd):
                    if isinstance(mirror_error, Exception): raise mirror_error
                    return subprocess.CompletedProcess(cmd, mirror_error, '', 'failed' if mirror_error else '')
                return subprocess.CompletedProcess(cmd, 1 if '--check' in cmd else 0, '', '')
            paths={'V2_ROOT':root,'DATA_DIR':root/'data','HOOKS_DIR':root/'hooks','LOCK_FILE':root/'hooks/.lock','TRANSLATIONS':root/'data/translations.json','MANIFEST':root/'data/manifest.json','RETRY_TRANSLATION':root/'hooks/.retry_translation','RETRY_COMMIT':root/'hooks/.retry_commit','RETRY_MEDIA':root/'hooks/.retry_media','PUBLISH_ENABLED':True}
            with patch.multiple(hook, **paths), patch.object(hook,'run',side_effect=run), patch.object(hook,'unpushed_commits',return_value=0), patch.object(hook,'changed_files',side_effect=[[],['data/posts-x.json','data/media-map.json','media/full/test.png'],[],['data/media-map.json','media/full/test.png']]), patch.object(hook,'git',side_effect=lambda args,**kw: subprocess.CompletedProcess(args,0,'','')) as git, patch.object(hook,'do_push',return_value=True) as publish:
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
        with patch.object(hook,'PUBLISH_BRANCH','main'), patch.object(hook,'current_branch',return_value='main'), patch.object(hook,'git',return_value=subprocess.CompletedProcess([],0,'','')) as git:
            self.assertTrue(hook.do_push())
            self.assertEqual(git.call_args.args[0],['push','origin','HEAD:refs/heads/main'])
    def test_monitor_retries_media_with_unchanged_sources(self):
        import monitor
        workspace=Path(__file__).resolve().parents[1]/'.qa-test-temp'
        workspace.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace) as name:
            root=Path(name).resolve(); self.assertTrue(root.is_relative_to(workspace.resolve()))
            (root/'.retry_media').touch()
            hashes={'x':'same'}
            def run(cmd,**kw): return subprocess.CompletedProcess(cmd,0,'0' if 'rev-list' in cmd else '', '')
            with patch.multiple(monitor,HOOKS_DIR=root,V2_ROOT=root,LOCK_FILE=root/'.monitor.lock'), patch.object(sys,'argv',['monitor.py']), patch.object(monitor,'current_hashes',return_value=hashes), patch.object(monitor,'load_state',return_value={'hashes':hashes}), patch.object(monitor,'save_state'), patch.object(monitor.subprocess,'run',side_effect=run) as calls:
                self.assertEqual(monitor.main(),0)
                self.assertTrue(any(any(str(v).endswith('post_ingest_hook.py') for v in c.args[0]) for c in calls.call_args_list))
    def test_nonzero_mirror_still_commits_and_publishes(self): self.exercise(1)
    def test_timeout_mirror_still_commits_and_publishes(self): self.exercise(subprocess.TimeoutExpired('mirror',600))
    def test_successful_mirror_commits_and_publishes(self): self.exercise(0)

if __name__ == '__main__': unittest.main()
