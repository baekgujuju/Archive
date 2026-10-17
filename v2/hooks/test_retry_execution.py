"""Exercise the real hook main(), with real temporary git repos and failing workers.
Windows tests stub Linux flock only; production workers still use real flock.
"""
import importlib.util,json,subprocess,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
if sys.platform=='win32':
    sys.modules['fcntl']=types.SimpleNamespace(LOCK_EX=2,LOCK_NB=4,LOCK_UN=8,flock=lambda *args:None)
HERE=Path(__file__).parent
def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def result(code=0):return subprocess.CompletedProcess([],code,'','injected failure' if code else '')

class HookExecution(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.temp.name);self.v2=self.root/'v2';self.data=self.v2/'data';self.hooks=self.v2/'hooks'
        self.data.mkdir(parents=True);self.hooks.mkdir();(self.v2/'media').mkdir();(self.v2/'media/.gitkeep').write_text('');(self.v2/'ib-library.json').write_text('{"posts":[]}')
        (self.data/'manifest.json').write_text('{"chunks":{}}');(self.data/'translations.json').write_text('{}');(self.data/'posts-x.json').write_text('{"posts":[]}')
        self.git('init');self.git('config','user.name','Test');self.git('config','user.email','test@example.com');self.git('add','.');self.git('commit','-m','baseline')
        self.h=load('post_ingest_hook');h=self.h
        for name,value in {'V2_ROOT':self.v2,'DATA_DIR':self.data,'HOOKS_DIR':self.hooks,'LOCK_FILE':self.hooks/'.lock','TRANSLATIONS':self.data/'translations.json','MANIFEST':self.data/'manifest.json','RETRY_TRANSLATION':self.hooks/'.retry_translation','RETRY_COMMIT':self.hooks/'.retry_commit','PUBLISH_ENABLED':False,'PUBLISH_BRANCH':'main'}.items():setattr(h,name,value)
        self.fail={};self.calls=[];self.built=False;self.publish_count=0
        h.log=lambda message:None
        self.original_run=h.run;h.run=self.worker
    def tearDown(self):self.temp.cleanup()
    def git(self,*args):
        r=subprocess.run(['git','-C',str(self.root),*args],capture_output=True,text=True)
        if r.returncode:raise AssertionError(r.stderr)
        return r.stdout
    def worker(self,cmd,**kwargs):
        self.calls.append(cmd)
        if cmd[0]=='git':
            if 'push' in cmd:
                self.publish_count+=1
                if self.fail.get('push',0):self.fail['push']-=1;return result(1)
                return result()
            if 'commit' in cmd and self.fail.get('commit',0):self.fail['commit']-=1;return result(1)
            return subprocess.run(cmd,capture_output=True,text=True)
        script=Path(cmd[1]).name
        if '--check' in cmd:return result(0 if self.built else 1)
        kind={'build_data.py':'build','translate_posts.py':'translation','ib_adapter.py':'adapter'}[script]
        if self.fail.get(kind,0):self.fail[kind]-=1;return result(1)
        if kind=='build':
            self.built=True;(self.data/'posts-x.json').write_text('{"posts":[{"id":"test"}]}')
        if kind=='translation':(self.data/'translations.json').write_text('{"test":{"status":"ok"}}')
        return result()
    def test_translation_failure_then_real_main_retries(self):
        self.fail['translation']=1;self.assertEqual(self.h.main(),1);self.assertTrue(self.h.RETRY_TRANSLATION.exists());self.assertEqual(self.h.main(),0);self.assertFalse(self.h.RETRY_TRANSLATION.exists())
        self.assertEqual(sum('translate_posts.py' in str(c) for c in self.calls),2)
    def test_build_failure_preserves_previous_generation_and_retries(self):
        before=(self.data/'posts-x.json').read_bytes();self.fail['build']=1;self.assertEqual(self.h.main(),1);self.assertEqual((self.data/'posts-x.json').read_bytes(),before);self.assertEqual(self.h.main(),0)
    def test_commit_failure_retries_when_source_unchanged(self):
        self.fail['commit']=1;self.assertEqual(self.h.main(),1);self.assertTrue(self.h.RETRY_COMMIT.exists());self.assertEqual(self.h.main(),0);self.assertFalse(self.h.RETRY_COMMIT.exists());self.assertEqual(self.git('status','--porcelain','--','v2/data'),'')
    def test_push_failure_then_unchanged_source_retries(self):
        self.git('update-ref','refs/remotes/origin/main','HEAD');self.h.PUBLISH_ENABLED=True;self.fail['push']=1;self.assertEqual(self.h.main(),1);self.assertEqual(self.h.main(),0);self.assertEqual(self.publish_count,2)
    def test_unrelated_staged_work_is_preserved(self):
        (self.root/'unrelated.txt').write_text('keep staged');self.git('add','unrelated.txt');self.assertEqual(self.h.main(),0);self.assertIn('unrelated.txt',self.git('diff','--cached','--name-only'));self.assertNotIn('unrelated.txt',self.git('show','--name-only','--format=','HEAD'))
    def test_manifest_translation_hash_matches_after_worker(self):
        import hashlib
        self.assertEqual(self.h.main(),0);m=json.loads((self.data/'manifest.json').read_text());self.assertEqual(m['translations']['sha256'],hashlib.sha256((self.data/'translations.json').read_bytes()).hexdigest())

class IBPreservation(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('ib',HERE.parent/'ib_adapter.py');self.ib=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.ib)
    def test_legacy_first_sync_keeps_rich_body_and_ids(self):
        old={'id':'old-pin','memoId':'old-memo','source_url':'https://example.com/one','title':'One','fullText':'curated complete original','fullHtml':'<p>curated complete original</p>','topics':['수학','교재']}
        merged,_=self.ib.merge([old],[{'url':old['source_url'],'title':'One','description':'short description','subject':'math'}]);self.assertEqual(merged[0]['fullHtml'],old['fullHtml']);self.assertEqual(merged[0]['id'],old['id']);self.assertEqual(merged[0]['memoId'],old['memoId'])
    def test_later_full_body_correction_can_be_shorter(self):
        old={'id':'old','memoId':'memo','title':'One','source_url':'https://example.com/one','fullHtml':'old html','fullText':'long original','_source_hash':'old'}
        merged,_=self.ib.merge([old],[{'url':old['source_url'],'title':'One','full_text':'new','subject':'math'}]);self.assertEqual(merged[0]['fullText'],'new');self.assertIn('new',merged[0]['fullHtml']);self.assertNotIn('old html',merged[0]['fullHtml'])
    def test_video_query_ids_remain_distinct(self):
        self.assertNotEqual(self.ib.norm_url('https://youtube.com/watch?v=a'),self.ib.norm_url('https://youtube.com/watch?v=b'))

class DriveExecution(unittest.TestCase):
    # 픽스처 레코드는 P2-7 캡처 계약을 만족해야 한다 (숫자 tweetId,
    # tweetId와 일치하는 sourceUrl, sourceUrl 핸들과 일치하는 author).
    _FIXTURE_RECORDS = {
        'good': {'tweetId': '111', 'fullText': 'updated body',
                 'sourceUrl': 'https://x.com/testuser/status/111',
                 'author': {'handle': '@testuser'}},
        'bad': {'tweetId': '222', 'fullText': 'updated body',
                'sourceUrl': 'https://x.com/testuser/status/222',
                'author': {'handle': '@testuser'}},
    }

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=HERE);self.root=Path(self.temp.name);self.d=load('drive_poll');d=self.d
        d.HOOKS_DIR=self.root;d.SHARED_LOCK=self.root/'.lock';d.XBM=self.root;d.CONFIG_PATH=self.root/'config.json';d.INGEST_SCRIPT=self.root/'ingest.py';d.POSTS_JSON=self.root/'posts.json';d.log=lambda text:None
        # 모듈 상수는 import 시점에 실제 hooks 디렉터리에 묶이므로,
        # 테스트가 실제 디렉터리를 오염시키지 않도록 임시 루트로 재배정한다.
        d.FAILURES_FILE=self.root/'.drive_poll_failures.json';d.PROPOSALS_FILE=self.root/'.merge_proposals.json';d.NEEDS_REVIEW=self.root/'.needs-review';d.QUARANTINE_DRIVE_DIR=self.root/'.quarantine'/'drive-files';d.STAGING_DIR=self.root/'.drive_poll_staging'
        d.CONFIG_PATH.write_text(json.dumps({'inbox_folder_id':'fixture','local_inbox':str(self.root/'inbox'),'state_file':str(self.root/'legacy.json')}));d.INGEST_SCRIPT.write_text('');d.POSTS_JSON.write_text(json.dumps([{'tweetId':'111','fullText':'old','category':'투자'}]))
        # 'good' 파일이 이전에 검증된 상태로 '111'을 생산했다는 기록.
        # 지문(modifiedTime '0')이 현재 파일('1')과 달라 재처리되며,
        # 동일 파일 재인제스트로 인정되어 새 파싱이 덮어쓴다.
        (self.root/'.drive_poll_state.json').write_text(json.dumps({'processed': {'good': {'modifiedTime': '0', 'size': '10', 'verification': 'verified', 'tweetIds': ['111']}}}))
        self.files=[{'id':x,'name':x+'.html','modifiedTime':'1','size':'10'} for x in ['good','bad']];self.failed=True;self.downloads=[]
        d.list_drive_files=lambda folder:self.files;d.run=self.worker
    def tearDown(self):self.temp.cleanup()
    def worker(self,cmd,**kwargs):
        if cmd[0]=='hatch_gws_cli':
            fid=json.loads(cmd[cmd.index('--params')+1])['fileId'];self.downloads.append(fid)
            # 다운로드 바이트 수는 선언 크기와 일치해야 한다 (drift 검사).
            size=int(next(x['size'] for x in self.files if x['id']==fid));Path(cmd[-1]).write_text('x'*size);return result()
        inputs=Path(cmd[2]);name=next(inputs.iterdir()).stem
        if name=='bad' and self.failed:return result(1)
        outputs=Path(cmd[-1]);(outputs/'posts.json').write_text(json.dumps([dict(self._FIXTURE_RECORDS[name])]));return result()
    def test_failed_capture_not_marked_successful_and_retried(self):
        # P2-1: 파일 실패는 rc=3 (실패 기록 영속화 완료, 스케줄러는 monitor 진행).
        # 실패 파일도 processed에 남지만 성공이 아닌 retry_pending으로 기록된다.
        self.assertEqual(self.d.main(),3);state=json.loads((self.root/'.drive_poll_state.json').read_text());self.assertIn('good',state['processed']);self.assertEqual(state['processed']['bad']['verification'],'retry_pending')
        # 백오프 만료 — 재시도 시점이 미래면 두 번째 폴이 건너뛰므로 과거로 당긴다.
        failures_path=self.root/'.drive_poll_failures.json';data=json.loads(failures_path.read_text())
        for rec in data['records'].values():
            if rec.get('fileId')=='bad':rec['next_retry_at']='2000-01-01T00:00:00+00:00'
        failures_path.write_text(json.dumps(data))
        self.failed=False;self.assertEqual(self.d.main(),0);self.assertEqual(self.downloads,['good','bad','bad'])
        posts=json.loads(self.d.POSTS_JSON.read_text());self.assertEqual(len(posts),2);self.assertEqual(posts[0]['category'],'투자');self.assertEqual(posts[0]['fullText'],'updated body')
    def test_modified_capture_replaces_same_post_without_duplicate(self):
        self.files=self.files[:1];self.assertEqual(self.d.main(),0);self.files[0]['modifiedTime']='2';self.assertEqual(self.d.main(),0);self.assertEqual(len(json.loads(self.d.POSTS_JSON.read_text())),1);self.assertEqual(self.downloads,['good','good'])
    def test_unsupported_material_stays_pending_without_download(self):
        self.files=[{'id':'pdf','name':'document.pdf','modifiedTime':'1','size':'10'}];self.assertEqual(self.d.main(),0);state=json.loads((self.root/'.drive_poll_state.json').read_text());self.assertNotIn('pdf',state['processed']);self.assertEqual(self.downloads,[])

class MonitorExecution(unittest.TestCase):
    def test_failed_hook_keeps_watermark_then_success_updates_it(self):
        m=load('monitor')
        with tempfile.TemporaryDirectory(dir=HERE) as temporary:
            root=Path(temporary);m.HOOKS_DIR=root;m.V2_ROOT=root;m.STATE_FILE=root/'state.json';m.LOCK_FILE=root/'.lock';m.current_hashes=lambda:{'fixture':'changed'};codes=[1,0]
            def worker(cmd,**kwargs):
                if cmd[0]=='git':return subprocess.CompletedProcess(cmd,0,'0' if 'rev-list' in cmd else '','')
                return result(codes.pop(0))
            with patch.object(m.subprocess,'run',worker),patch.object(sys,'argv',['monitor.py']):
                self.assertEqual(m.main(),1);self.assertFalse(m.STATE_FILE.exists());self.assertEqual(m.main(),0);self.assertEqual(json.loads(m.STATE_FILE.read_text())['hashes'],{'fixture':'changed'})

if __name__=='__main__':unittest.main(verbosity=2)
