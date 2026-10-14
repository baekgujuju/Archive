import importlib.util,sys,tempfile,json,unittest
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sp=importlib.util.spec_from_file_location('collector',root/'collect_sources.py');c=importlib.util.module_from_spec(sp);sp.loader.exec_module(c)
class CollectorGateTest(unittest.TestCase):
 def run_case(self,body,returncode=0):
  with tempfile.TemporaryDirectory(dir='work/review') as td:
   d=Path(td); c.PRIVATE_DIR=d;c.TARGETS_JSON=d/'targets.json';c.RESULTS_JSON=d/'results.json';c.TARGETS_JSON.write_text(json.dumps([{'id':'sample','title':'Original article','url':'https://example.org/post'}]));(d/'sample').mkdir();(d/'sample/article.md').write_text(body)
   with patch.object(c.subprocess,'run',return_value=type('R',(),{'returncode':returncode})()): c.main()
   return json.loads(c.RESULTS_JSON.read_text())['sample']
 def test_missing_structured_evidence_fails_closed(self):self.assertEqual(self.run_case('Original article '+('body '*150))['status'],'needs_review')
 def test_navigation_never_overwritten(self):self.assertEqual(self.run_case('카페정보 카페멤버수 게시판 카페등급 '*100)['status'],'poisoned-navigation')
 def test_failed_fetch_does_not_use_stale_file(self):self.assertEqual(self.run_case('Original article '+'body '*150,1)['status'],'failed')
 def test_naver_host_detection(self):
  self.assertTrue(c.is_naver_url('https://m.cafe.naver.com/post'));self.assertFalse(c.is_naver_url('https://example.org/?url=naver.com'))
unittest.main()
