import importlib.util
import unittest
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
if __name__=='__main__':unittest.main()
