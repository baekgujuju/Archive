"""Drive 삭제 정책: 모든 외부 요청은 fake, 정본/상태는 임시 파일."""
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drive_poll as poll
from test_p2_batch2 import poll_env, FakeRun, _drive_file, _valid_html


@pytest.fixture
def deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(poll, 'POSTS_JSON', tmp_path / 'posts.json')
    monkeypatch.setattr(poll, 'FAILURES_FILE', tmp_path / 'failures.json')
    monkeypatch.setattr(poll, 'PROPOSALS_FILE', tmp_path / 'proposals.json')
    monkeypatch.setattr(poll, 'pipeline_lock', nullcontext)
    poll.POSTS_JSON.write_text('[{"tweetId":"123"}]')
    f = dict(id='file-1', name='123.html', modifiedTime='now', size='100')
    record = dict(verification='verified', tweetIds=['123'], modifiedTime='now',
                  size='100', content_sha256='abc', validator_version='v1')
    monkeypatch.setattr(poll, 'list_drive_files', lambda inbox: [f.copy()])
    calls = []
    def run(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(poll, 'run', run)
    return f, record, calls


def delete(f, record, **kw):
    return poll.delete_verified_inbox_file(f, record, inbox_id='inbox', **({'max_bytes': 1000} | kw))


def test_verified_deleted_with_logs(deletion, capsys):
    f, record, calls = deletion
    assert delete(f, record)
    assert calls == [['hatch_gws_cli', 'drive', 'files', 'delete', '--params', '{"fileId": "file-1"}']]
    output = capsys.readouterr().out
    assert '삭제 시작' in output and '삭제 완료' in output


@pytest.mark.parametrize('patch', [
    {'verification': 'quarantined'}, {'verification': 'retry_pending'},
    {'verification': None}, {'legacyBaseline': True}, {'tweetIds': []},
    {'tweetIds': ['123', '456']}, {'tweetIds': '123'},
    {'content_sha256': None}, {'validator_version': None}, {'size': '101'},
])
def test_unverified_evidence_never_deleted(deletion, patch):
    f, record, calls = deletion
    record.update(patch)
    assert not delete(f, record)
    assert not calls


@pytest.mark.parametrize('patch', [
    {'name': 'book.html'}, {'name': 'book.pdf'}, {'size': 'unknown'},
    {'size': '1001'}, {'size': '0'}, {'size': None},
])
def test_protected_material_never_deleted(deletion, patch):
    f, record, calls = deletion
    f.update(patch)
    assert not delete(f, record)
    assert not calls


@pytest.mark.parametrize('limit', [None, 0, -1, True, '1000'])
def test_missing_or_invalid_size_policy(deletion, limit):
    f, record, calls = deletion
    assert not delete(f, record, max_bytes=limit)
    assert not calls


@pytest.mark.parametrize('canonical', ['[]', '{', '{}', '[null]', '[{"tweetId":"123"},{"tweetId":"123"}]'])
def test_bad_or_missing_canonical(deletion, canonical):
    f, record, calls = deletion
    poll.POSTS_JSON.write_text(canonical)
    assert not delete(f, record)
    assert not calls


def test_drive_changed_or_moved(deletion, monkeypatch):
    f, record, calls = deletion
    for entries in [[], [dict(f, size='101')], [dict(f, name='book.html')]]:
        monkeypatch.setattr(poll, 'list_drive_files', lambda inbox: entries)
        assert not delete(f, record)
    assert not calls


@pytest.mark.parametrize('status,evidence', [('retry_pending', None), ('quarantined', 'path'), ('resolved', 'path')])
def test_failure_or_quarantine_history(deletion, status, evidence):
    f, record, calls = deletion
    poll.FAILURES_FILE.write_text(json.dumps({'records': {'key': {
        'fileId': f['id'], 'status': status, 'evidence_path': evidence}}}))
    assert not delete(f, record)
    assert not calls


def test_merge_proposal_preserved(deletion):
    f, record, calls = deletion
    poll.PROPOSALS_FILE.write_text(json.dumps({'proposals': [{'fileId': f['id']}]}))
    assert not delete(f, record)
    assert not calls


@pytest.mark.parametrize('timeout', [False, True])
def test_delete_error_no_credentials_logged(deletion, monkeypatch, capsys, timeout):
    f, record, calls = deletion
    def fail(cmd):
        if timeout:
            raise subprocess.TimeoutExpired(cmd, 1, stderr='secret-token')
        return subprocess.CompletedProcess(cmd, 1, '', 'secret-token')
    monkeypatch.setattr(poll, 'run', fail)
    assert not delete(f, record)
    assert 'secret-token' not in capsys.readouterr().out


def test_main_new_and_existing_verified_retry(poll_env, monkeypatch):
    content = _valid_html('123')
    f = _drive_file('file-1', '123.html', content)
    monkeypatch.setattr(poll, 'list_drive_files', lambda inbox: [f.copy()])
    config = json.loads(poll.CONFIG_PATH.read_text())
    config['inbox_delete_max_bytes'] = len(content)
    poll.CONFIG_PATH.write_text(json.dumps(config))
    fake = FakeRun({'file-1': content})
    deletes = []
    def run(cmd, **kw):
        if cmd[:4] == ['hatch_gws_cli', 'drive', 'files', 'delete']:
            deletes.append(cmd)
            return subprocess.CompletedProcess(cmd, 1 if len(deletes) == 1 else 0, '', '')
        return fake(cmd, **kw)
    monkeypatch.setattr(poll, 'run', run)
    assert poll.main() == 0
    assert len(deletes) == 1
    assert poll.main() == 0
    assert len(deletes) == 2
    assert fake.download_calls == ['file-1']


def test_missing_canonical(deletion):
    f, record, calls = deletion
    poll.POSTS_JSON.unlink()
    assert not delete(f, record)
    assert not calls


def test_metadata_lookup_failure(deletion, monkeypatch):
    f, record, calls = deletion
    def fail(inbox):
        raise RuntimeError('lookup failed')
    monkeypatch.setattr(poll, 'list_drive_files', fail)
    assert not delete(f, record)
    assert not calls


def test_main_failed_capture_never_deleted(poll_env, monkeypatch):
    f = _drive_file('bad-file', '123.html', b'<html>invalid</html>')
    monkeypatch.setattr(poll, 'list_drive_files', lambda inbox: [f.copy()])
    config = json.loads(poll.CONFIG_PATH.read_text())
    config['inbox_delete_max_bytes'] = 1000
    poll.CONFIG_PATH.write_text(json.dumps(config))
    fake = FakeRun({'bad-file': b'<html>invalid</html>'})
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return fake(cmd, **kw)
    monkeypatch.setattr(poll, 'run', run)
    assert poll.main() == 3
    assert not any(cmd[:4] == ['hatch_gws_cli', 'drive', 'files', 'delete'] for cmd in calls)
