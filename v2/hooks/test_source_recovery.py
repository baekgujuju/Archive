import hashlib
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import source_recovery as recovery

ROOT=Path(__file__).resolve().parent


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT);self.root=Path(self.tmp.name)
        self.source=self.root/'source.html';self.source.write_bytes(b'<p>source\r\noriginal</p>')
        self.sha=recovery.digest(self.source)

    def tearDown(self):self.tmp.cleanup()

    def bundle(self):
        path=self.root/'backup.zip';info=recovery.backup_sources([self.source],path)
        return path,info

    def forge(self, entries, objects, extra=None):
        path=self.root/'forged.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('manifest.json',json.dumps(dict(schema_version=1,kind='archive-source-backup',entries=entries)))
            for name,value in objects.items():z.writestr(name,value)
            if extra:z.writestr(extra,b'bad')
        return path

    def test_backup_restore_exact_crlf_bytes(self):
        path,info=self.bundle();dest=self.root/'restored'
        recovery.restore_backup(path,dest,expected_sha256=info['sha256'])
        self.assertEqual((dest/(self.sha+'.html')).read_bytes(),self.source.read_bytes())
        self.assertFalse((dest/'.recovery-incomplete.json').exists())
        self.assertEqual(recovery.digest(self.source),self.sha)

    def test_deduplicate_objects_but_keep_source_names(self):
        other=self.root/'other.html';other.write_bytes(self.source.read_bytes())
        info=recovery.backup_sources([self.source,other],self.root/'backup.zip')
        self.assertEqual(info['entries'],2);self.assertEqual(info['objects'],1)

    def test_never_overwrite_backup(self):
        path,info=self.bundle()
        with self.assertRaises(ValueError):recovery.backup_sources([self.source],path)
        self.assertEqual(recovery.digest(path),info['sha256'])

    def test_never_overwrite_restore_directory(self):
        path,info=self.bundle();dest=self.root/'restore';dest.mkdir();(dest/'keep').write_text('keep')
        with self.assertRaises(ValueError):recovery.restore_backup(path,dest,expected_sha256=info['sha256'])
        self.assertEqual((dest/'keep').read_text(),'keep')

    def test_wrong_external_backup_hash(self):
        path,info=self.bundle()
        with self.assertRaises(ValueError):recovery.restore_backup(path,self.root/'restore',expected_sha256='0'*64)
        self.assertFalse((self.root/'restore').exists())

    def test_object_tampering_rejected(self):
        path=self.forge([dict(name='a.html',size=4,sha256=self.sha,object='objects/'+self.sha+'.html')],{'objects/'+self.sha+'.html':b'evil'})
        with self.assertRaises(ValueError):recovery.verify_backup(path)

    def test_zip_slip_rejected_without_any_extraction(self):
        raw=self.source.read_bytes();entry=dict(name='a.html',size=len(raw),sha256=self.sha,object='objects/'+self.sha+'.html')
        path=self.forge([entry],{entry['object']:raw},extra='../escaped.html')
        with self.assertRaises(ValueError):recovery.verify_backup(path)
        self.assertFalse((self.root.parent/'escaped.html').exists())

    def test_manifest_object_path_cannot_traverse(self):
        entry=dict(name='a.html',size=4,sha256=self.sha,object='../escape')
        path=self.forge([entry],{'../escape':b'evil'})
        with self.assertRaises(ValueError):recovery.verify_backup(path)

    def test_display_name_cannot_be_path(self):
        raw=self.source.read_bytes();entry=dict(name='../a.html',size=len(raw),sha256=self.sha,object='objects/'+self.sha+'.html')
        path=self.forge([entry],{entry['object']:raw})
        with self.assertRaises(ValueError):recovery.verify_backup(path)

    def test_symlink_entry_rejected(self):
        path,info=self.bundle()
        # A ZIP symlink is rejected even when its filename looks like an object.
        raw=self.source.read_bytes();entry=dict(name='a.html',size=len(raw),sha256=self.sha,object='objects/'+self.sha+'.html')
        bad=self.root/'link.zip'
        with zipfile.ZipFile(bad,'w') as z:
            z.writestr('manifest.json',json.dumps(dict(schema_version=1,kind='archive-source-backup',entries=[entry])))
            member=zipfile.ZipInfo(entry['object']);member.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(member,raw)
        with self.assertRaises(ValueError):recovery.verify_backup(bad)

    def test_stream_and_total_bounds(self):
        with patch.object(recovery,'MAX_TOTAL',1):
            with self.assertRaises(ValueError):recovery.backup_sources([self.source],self.root/'backup.zip')
        self.assertFalse((self.root/'backup.zip').exists())

    def test_only_html_sources(self):
        source=self.root/'credentials.txt';source.write_text('not uploaded')
        with self.assertRaises(ValueError):recovery.backup_sources([source],self.root/'backup.zip')

    def test_source_change_does_not_publish_backup(self):
        original=recovery.digest;calls=0
        def changed(path):
            nonlocal calls
            if Path(path)==self.source:
                calls+=1
                if calls>1:return '0'*64
            return original(path)
        with patch.object(recovery,'digest',changed):
            with self.assertRaises(ValueError):recovery.backup_sources([self.source],self.root/'backup.zip')
        self.assertFalse((self.root/'backup.zip').exists())

    def ingester(self, source_text='sourceoriginal', rc=0):
        path=self.root/'fake_ingest.py'
        path.write_text('import sys,json\nfrom pathlib import Path\n'+
            'Path(sys.argv[-1],"posts.json").write_text(json.dumps('+repr([dict(tweetId='100',fullText=source_text,media=[],replies=[])])+'))\n'+
            'raise SystemExit('+str(rc)+')\n')
        return path

    def test_replay_checks_source_hash_before_executing(self):
        ing=self.ingester()
        with patch.object(recovery.subprocess,'run',side_effect=AssertionError('must not run')):
            with self.assertRaises(ValueError):recovery.replay_capture(self.source,ing,self.root/'review',expected_sha256='0'*64)

    def test_replay_is_review_output_only_and_does_not_modify_canonical(self):
        raw=b'<article class="tweet" data-tweet-id="100"><div class="tweet-text">source original</div></article>'
        self.source.write_bytes(raw);sha=recovery.digest(self.source)
        canonical=self.root/'posts.json';canonical.write_text('["keep"]');before=canonical.read_bytes()
        report=recovery.replay_capture(self.source,self.ingester(),self.root/'review',expected_sha256=sha)
        self.assertEqual(report['mode'],'dry_run_only');self.assertFalse(report['canonical_modified'])
        self.assertEqual(report['status'],'conversion_preserved_review_before_reupload')
        self.assertEqual(canonical.read_bytes(),before);self.assertEqual(self.source.read_bytes(),raw)
        self.assertEqual(sorted(p.name for p in (self.root/'review').iterdir()),['replay-records.json','replay-report.json'])

    def test_replay_blocks_lost_text(self):
        self.source.write_bytes(b'<article class="tweet" data-tweet-id="100"><div class="tweet-text">source original tail</div></article>')
        report=recovery.replay_capture(self.source,self.ingester(),self.root/'review',expected_sha256=recovery.digest(self.source))
        self.assertEqual(report['status'],'needs_review');self.assertTrue(report['audit']['blocked'])

    def test_replay_ingester_rejection_is_not_success(self):
        report=recovery.replay_capture(self.source,self.ingester(rc=2),self.root/'review',expected_sha256=self.sha)
        self.assertEqual(report['status'],'ingester_rejected')
        self.assertFalse((self.root/'review/replay-records.json').exists())

    def test_exclusive_directory_reservation_rejects_race(self):
        stage=self.root/'stage';stage.mkdir();(stage/'a').write_text('a')
        target=self.root/'target';target.mkdir()
        with self.assertRaises(FileExistsError):recovery._publish_directory(stage,target)
        self.assertEqual(list(target.iterdir()),[])

    def test_existing_symlink_component_rejected(self):
        target=self.root/'parent'/'target'
        # Portable component test; the real Linux symlink check is also a
        # required reviewer test, since Windows may require symlink privileges.
        real=Path.is_symlink
        with patch.object(Path,'is_symlink',lambda p: p==target.parent or real(p)):
            with self.assertRaisesRegex(ValueError,'symlink_component'):
                recovery._new_target(target)

    def test_restore_failure_never_publishes_partial_tree(self):
        path,info=self.bundle();dest=self.root/'restore'
        with patch.object(recovery,'_copy_checked',side_effect=ValueError('injected corruption')):
            with self.assertRaises(ValueError):recovery.restore_backup(path,dest,expected_sha256=info['sha256'])
        self.assertFalse(dest.exists())

    def test_cleanup_cannot_escape_owned_directory(self):
        outside=self.root/'outside';outside.mkdir();owned=self.root/'owned';owned.mkdir()
        with self.assertRaises(ValueError):recovery._remove_owned_tree(outside,owned)
        self.assertTrue(outside.exists())

    @unittest.skipIf(os.name=='nt','Linux real symlink component check')
    def test_real_symlink_component_rejected(self):
        real=self.root/'real';real.mkdir();link=self.root/'link';link.symlink_to(real,target_is_directory=True)
        with self.assertRaises(ValueError):recovery._new_target(link/'restore')


if __name__=='__main__':unittest.main()
