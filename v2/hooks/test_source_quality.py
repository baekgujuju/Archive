import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('source_quality',Path(__file__).resolve().parents[1]/'source_quality.py')
quality=importlib.util.module_from_spec(spec);spec.loader.exec_module(quality)

class SourceQualityTest(unittest.TestCase):
    def setUp(self):
        self.record={'title':'IB 만점자가 말해주는 IB 공부방법'}
        self.capture={'body_text':'목표를 먼저 정하세요.\nIA를 마무리하세요.',
                      'paragraphs':['목표를 먼저 정하세요.','IA를 마무리하세요.'],
                      'source_title':self.record['title'],'content_selector':'.se-main-container',
                      'extraction_scope':'article','capture_complete':True,'images':[]}
    def test_long_navigation_is_not_full(self):
        self.capture['body_text']='카페정보 카페멤버수 카페 가입하기 전체글보기 '*600
        self.assertNotEqual(quality.assess_capture(self.record,self.capture)['status'],'full')
    def test_real_short_article_evidence_is_accepted(self):
        self.assertEqual(quality.assess_capture(self.record,self.capture)['status'],'full')
    def test_access_wall_is_not_full(self):
        self.capture['access_state']='membership_required'
        self.assertNotEqual(quality.assess_capture(self.record,self.capture)['status'],'full')
    def test_missing_image_cannot_pass(self):
        self.capture['images']=[{'url':'https://example.org/image.jpg'}]
        result=quality.assess_capture(self.record,self.capture)
        self.assertEqual(result['status'],'needs_review');self.assertEqual(result['images_saved'],0)
    def test_complete_flag_alone_is_insufficient(self):
        self.capture.pop('content_selector');self.capture.pop('paragraphs')
        self.assertNotEqual(quality.assess_capture(self.record,self.capture)['status'],'full')
    def test_wrong_article_is_not_full(self):
        self.capture['source_title']='다른 게시글'
        self.assertNotEqual(quality.assess_capture(self.record,self.capture)['status'],'full')

if __name__=='__main__':unittest.main()
