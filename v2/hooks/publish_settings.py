#!/usr/bin/env python3
"""Publish only the owner's explicitly exported, whitelisted archive preferences."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime

from pipeline_lock import PipelineLockTimeout, pipeline_lock

V2 = Path(__file__).resolve().parent.parent
ARRAYS = ('pinned', 'deletedPosts', 'deletedReplies', 'deletedRemovedReplies')
MAPS = ('bookmarks', 'names', 'memos', 'highlights')
FORBIDDEN = {'__proto__', 'prototype', 'constructor'}


def validate(raw):
    if not isinstance(raw, dict) or raw.get('schema_version') != 1:
        raise ValueError('Invalid shared settings schema')
    revision = raw.get('revision')
    if not isinstance(revision, str) or not 1 <= len(revision) <= 160:
        raise ValueError('Invalid revision')
    stamp = raw.get('updatedAt')
    if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Invalid timestamp')
    out = {'schema_version': 1, 'revision': revision, 'updatedAt': stamp}
    for key in ARRAYS:
        value = raw.get(key, [])
        if not isinstance(value, list) or len(value) > 20000 or any(not isinstance(x, str) or len(x) > 1000 or x in FORBIDDEN for x in value):
            raise ValueError('Invalid array: ' + key)
        out[key] = list(dict.fromkeys(value))
    for key in MAPS:
        value = raw.get(key, {})
        if not isinstance(value, dict) or len(value) > 20000 or any(k in FORBIDDEN or len(k) > 1000 or not isinstance(v, str) or len(v) > 200000 for k, v in value.items()):
            raise ValueError('Invalid map: ' + key)
        if key == 'highlights':
            for encoded in value.values():
                anchors = json.loads(encoded)
                if not isinstance(anchors, list) or len(anchors) > 2000:
                    raise ValueError('Invalid highlight array')
                for a in anchors:
                    if (not isinstance(a, dict) or not isinstance(a.get('exact'), str)
                        or not 0 < len(a['exact']) <= 10000
                        or not isinstance(a.get('id'), str) or not 0 < len(a['id']) <= 100
                        or type(a.get('start')) is not int or not 0 <= a['start'] <= 9007199254740991
                        or not isinstance(a.get('prefix'), str) or len(a['prefix']) > 32
                        or not isinstance(a.get('suffix'), str) or len(a['suffix']) > 32):
                        raise ValueError('Invalid highlight anchor')
        out[key] = value
    # Unknown fields (including authentication details) are never published.
    return out


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, timeout=90).stdout


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def publish(v2=V2):
    if os.environ.get('V2_PUBLISH', '0') != '1':
        print('Shared settings publication disabled')
        return
    import fcntl
    with (v2 / 'hooks' / '.post_ingest.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        repo = Path(run(['git', 'rev-parse', '--show-toplevel'], v2).strip())
        branch = os.environ.get('V2_PUBLISH_BRANCH', 'main')
        if run(['git', 'branch', '--show-current'], repo).strip() != branch:
            raise ValueError('Unexpected publishing branch')
        if run(['git', 'diff', '--cached', '--name-only'], repo).strip():
            raise ValueError('Unrelated staged changes; publication deferred')
        query = "name='archive-shared-state.json' and 'me' in owners and trashed=false and properties has { key='archive' and value='baekgujuju/Archive' } and properties has { key='purpose' and value='shared-state' }"
        params = {'q': query, 'spaces': 'drive', 'fields': 'files(id,size),nextPageToken', 'orderBy': 'modifiedTime desc', 'pageSize': 100}
        found = json.loads(run(['hatch_gws_cli', 'drive', 'files', 'list', '--params', json.dumps(params)]))
        files = found.get('files', [])
        if not files:
            print('No owner export yet; existing state untouched')
            return
        item = files[0]
        if int(item.get('size', 0)) > 2000000:
            raise ValueError('Export exceeds size limit')
        with tempfile.TemporaryDirectory(prefix='settings_', dir=v2 / 'hooks') as tmp:
            downloaded = Path(tmp) / 'export.json'
            # hatch_gws_cli writes file content to stdout, not -o file. Capture stdout.
            content = run(['hatch_gws_cli', 'drive', 'files', 'get', '--params', json.dumps({'fileId': item['id'], 'alt': 'media'})])
            # Strip any JSON metadata wrapper if present; file content starts with '{'
            idx = content.find('{')
            if idx > 0:
                content = content[idx:]
            downloaded.write_text(content, encoding='utf-8')
            if downloaded.stat().st_size > 2000000:
                raise ValueError('Export exceeds size limit')
            state = validate(json.loads(downloaded.read_text(encoding='utf-8')))
        target = v2 / 'data' / 'shared-state.json'
        manifest_path = v2 / 'data' / 'manifest.json'
        paths = [str(p.relative_to(repo)) for p in (target, manifest_path)]
        # P1-4: read-validate-write under the shared lock so a concurrent
        # build cannot preserve a stale shared-state and then lose this write.
        try:
            with pipeline_lock():
                if target.exists():
                    previous = validate(json.loads(target.read_text(encoding='utf-8')))
                    if datetime.fromisoformat(previous['updatedAt'].replace('Z', '+00:00')) > datetime.fromisoformat(state['updatedAt'].replace('Z', '+00:00')):
                        raise ValueError('Older export rejected')
                # Never overwrite a pending article build or manual change.
                if run(['git', 'status', '--porcelain', '--'] + paths, repo).strip():
                    raise ValueError('Target files have uncommitted changes; deferred')
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                atomic(target, state)
                manifest['shared_state'] = {'file': 'data/shared-state.json', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'revision': state['revision']}
                atomic(manifest_path, manifest)
        except PipelineLockTimeout as e:
            raise ValueError(f"공유 상태 쓰기 건너뜀 (락 타임아웃): {e}")
        if run(['git', 'status', '--porcelain', '--'] + paths, repo).strip():
            run(['git', 'add', '--'] + paths, repo)
            run(['git', 'commit', '-m', 'Publish owner archive settings'], repo)
        # Retry a previous failed push even when downloaded state is unchanged.
        run(['git', 'push', 'origin', 'HEAD:' + branch], repo)
        print('Owner shared settings published')


if __name__ == '__main__':
    publish()
