import ast
import io
import json
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))


class PublishGuardTests(unittest.TestCase):
    def run_guard(self, missing=False, corrupt=False, mapped=False):
        tree=ast.parse((ROOT/'post_ingest_hook.py').read_text(encoding='utf-8'))
        selected=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('validate_candidate_media','do_push')],type_ignores=[])
        image=io.BytesIO(); Image.new('RGB',(2,2),'red').save(image,format='PNG'); raw=image.getvalue()
        asset='v2/media/full/embedded-'+hashlib.sha256(raw).hexdigest()+'.png'
        url='https://baekgujuju.github.io/Archive/'+asset
        source_url='https://pbs.twimg.com/media/original.png' if mapped else url
        manifest={'chunks':{'posts-x':{'file':'data/posts-x.json'}}}
        blobs={'v2/data/manifest.json':json.dumps(manifest).encode(),'v2/data/posts-x.json':json.dumps({'posts':[{'media':[source_url]}]}).encode()}
        if mapped:blobs['v2/data/media-map.json']=json.dumps({source_url:asset.removeprefix('v2/')}).encode()
        if not missing:blobs[asset]=b'corrupt' if corrupt else raw
        calls=[]
        def git(args,**kw):
            calls.append(args)
            return SimpleNamespace(returncode=0,stdout='a'*40,stderr='')
        def show(commit,path):
            self.assertEqual(commit,'a'*40)
            return blobs.get(path,b''),path not in blobs
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            marker=Path(tmp)/'retry'
            env={'current_branch':lambda:'main','PUBLISH_BRANCH':'main','ref_exists':lambda ref:True,
                 'git':git,'_git_show_bytes':show,'_manifest_relpath':lambda p:'v2/'+p,
                 'validate_commit_artifacts':lambda sha:(True,''),'flag_needs_review':lambda *a:self.fail('media failure must remain retryable'),
                 'RETRY_MEDIA':marker,'datetime':datetime,'timezone':timezone,'json':json,'log':lambda s:None}
            exec(compile(selected,'actual-candidate-hook','exec'),env)
            ok=env['do_push']()
            self.assertEqual(ok,not(missing or corrupt))
            pushes=[args for args in calls if args[0]=='push']
            self.assertEqual(len(pushes),int(ok))
            if ok:self.assertEqual(pushes[0][2],'a'*40+':refs/heads/main')
            else:self.assertTrue(marker.exists())

    def test_missing_blocks_before_network(self):self.run_guard(missing=True)
    def test_corrupt_blocks_before_network(self):self.run_guard(corrupt=True)
    def test_complete_publishes_same_sha(self):self.run_guard()
    def test_mapped_missing_blocks(self):self.run_guard(missing=True,mapped=True)


if __name__=='__main__':unittest.main()
