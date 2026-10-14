import importlib.util
import unittest
import tempfile,json,sys
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('translate_posts',Path(__file__).resolve().parents[1]/'translate_posts.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ParagraphTests(unittest.TestCase):
    def test_html_boundaries_and_nested_list(self):
        post={'fullText':'stale collapsed text','fullHtml':'<h1>Title</h1><p>First <b>paragraph.</b></p><ul><li><p>Nested paragraph.</p></li></ul><table><tr><td>Cell</td></tr></table>'}
        self.assertEqual(module.split_paragraphs(module.get_post_text(post)),['Title','First paragraph.','Nested paragraph.','Cell'])
    def test_mixed_language_does_not_skip_foreign_paragraph(self):
        ps=module.split_paragraphs(module.get_post_text({'fullHtml':'<p>한국어 설명입니다.</p><p>The original English paragraph.</p>'}))
        self.assertEqual([p for p in ps if module.is_english_only(p)],['The original English paragraph.'])
    def test_no_script_text(self):
        self.assertEqual(module.get_post_text({'fullHtml':'<p>Actual text<script>BAD()</script></p>'}),'Actual text')
    def test_nonprose_does_not_trigger_translation(self):
        for value in ['https://naver.me/F1atsZoX','─────────────────','$ANET\n$CSCO\n$HPE','952M22','-']:
            self.assertFalse(module.is_english_only(value),value)
        self.assertTrue(module.is_english_only('$HPE\nTrend Continuation'))
        self.assertTrue(module.is_english_only('东芝扩产HDD'))
    def test_failed_refresh_keeps_previous_success(self):
        with tempfile.TemporaryDirectory(prefix='translation-test-',dir=module.V2_ROOT) as tmp:
            root=Path(tmp);cache=root/'translations.json'
            self.assertTrue(root.resolve().is_relative_to(module.V2_ROOT.resolve()))
            prior={'paragraphs':['Old text.'],'translated':['이전 번역.'],'content_hash':'old','status':'ok'}
            cache.write_text(json.dumps({'qa':prior}),encoding='utf-8')
            (root/'posts.json').write_text(json.dumps({'posts':[{'id':'qa','fullText':'New English text.'}]}),encoding='utf-8')
            with patch.object(module,'DATA_DIR',root),patch.object(module,'CACHE_FILE',cache),patch.object(module,'TARGET_CHUNKS',['posts.json']),patch.object(module,'translate_paragraphs',side_effect=RuntimeError('temporary outage')),patch.object(module.time,'sleep'),patch.object(sys,'argv',['translate_posts.py']):
                self.assertEqual(module.main(),1)
            saved=json.loads(cache.read_text(encoding='utf-8'))['qa']
            self.assertEqual(saved['translated'],prior['translated'])
            self.assertEqual(saved['last_attempt']['status'],'failed')
if __name__=='__main__':unittest.main()
