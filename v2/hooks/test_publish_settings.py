import importlib.util,json,tempfile,unittest,os,types,sys
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('publisher',Path(__file__).with_name('publish_settings.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_whitelist(self):
  out=m.validate({'schema_version':1,'revision':'r','updatedAt':'2026-10-16T00:00:00Z','access_token':'secret','memos':{'a':'note'}})
  self.assertNotIn('access_token',out);self.assertEqual(out['memos']['a'],'note')
 def test_bad_data(self):
  for bad in [{'memos':{'__proto__':'x'}},{'pinned':[1]},{'bookmarks':[]},{'updatedAt':'bad'},{'revision':''}]:
   with self.assertRaises((ValueError,TypeError)):m.validate(dict({'schema_version':1,'revision':'r','updatedAt':'2026-10-16T00:00:00Z'},**bad))
 def test_highlight_schema(self):
  base={'schema_version':1,'revision':'r','updatedAt':'2026-10-16T00:00:00Z'}
  good={'id':'h1','exact':'quote','start':0,'prefix':'','suffix':''}
  self.assertEqual(m.validate(dict(base,highlights={'a':json.dumps([good])}))['highlights']['a'],json.dumps([good]))
  for bad in ['broken', '{}', '[1]', json.dumps([dict(good,start=True)]),json.dumps([dict(good,prefix=1)]),json.dumps([dict(good,id='')])]:
   with self.assertRaises((ValueError,TypeError)):m.validate(dict(base,highlights={'a':bad}))
 def test_publish_and_retry(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
   root=Path(tmp);v2=root/'v2';(v2/'hooks').mkdir(parents=True);(v2/'data').mkdir();manifest={'content_updated_at':'unchanged','version':'unchanged','source_hash':'unchanged'};(v2/'data/manifest.json').write_text(json.dumps(manifest))
   state={'schema_version':1,'revision':'r','updatedAt':'2026-10-16T00:00:00Z','memos':{'a':'public'}};calls=[];dirty=[False];fail=[True]
   def run(args,cwd=None):
    calls.append(args)
    if args[:3]==['git','rev-parse','--show-toplevel']:return str(root)
    if args[:3]==['git','branch','--show-current']:return 'main'
    if args[:3]==['git','diff','--cached']:return ''
    if args[:3]==['git','status','--porcelain']:return ' M' if dirty.pop(0) else ''
    if args[:4]==['hatch_gws_cli','drive','files','list']:return json.dumps({'files':[{'id':'owner','size':100}]})
    if args[:4]==['hatch_gws_cli','drive','files','get']:dirty.append(True);return json.dumps(state)
    if args[:2]==['git','push'] and fail[0]:fail[0]=False;raise RuntimeError('push failed')
    return ''
   fake=types.SimpleNamespace(LOCK_EX=2,flock=lambda *args:None)
   with patch.dict(sys.modules,{'fcntl':fake}),patch.dict(os.environ,{'V2_PUBLISH':'1'}),patch.object(m,'run',run):
    with self.assertRaises(RuntimeError):m.publish(v2)
    dirty[:]=[False,False];m.publish(v2)
   self.assertEqual(sum(a[:2]==['git','push'] for a in calls),2)
   actual=json.loads((v2/'data/manifest.json').read_text());self.assertEqual(actual['content_updated_at'],'unchanged');self.assertEqual(actual['source_hash'],'unchanged')
   self.assertEqual(json.loads((v2/'data/shared-state.json').read_text())['memos'],{'a':'public'})
if __name__=='__main__':unittest.main()

