"""Web-specific intake transaction; caller supplies existing guarded services."""
import hashlib
import json
import os
import shutil
from pathlib import Path
from web_capture import VERSION, WebCaptureError, parse_web, merge_web, normalize_web
from publication_evidence import capture_targets
from materialize_media import materialize_records, EmbeddedMediaError

def process_web(raw,path,fid,name,fp,*,local_inbox,state,state_path,processed,services):
    s=services;sha=hashlib.sha256(path.read_bytes()).hexdigest()
    def fail(stage,code,exc=None):
        raise s.FileFailure(stage,code,content_sha256=sha,downloaded_path=path,stderr=type(exc).__name__ if exc else '')
    try:
        record,audit=parse_web(raw)
        if audit['source_sha256']!=sha:raise WebCaptureError('web_capture_bytes_changed')
    except (ValueError,TypeError,RecursionError) as exc:fail('validation',str(exc) if isinstance(exc,WebCaptureError) else 'web_capture_invalid',exc)
    archived=local_inbox/s.archive_filename(name,fid,sha)
    audit_path=local_inbox.parent/(local_inbox.name+'.preservation')/(archived.name+'.web-preservation-v1.json')
    try:
        shutil.copy2(path,archived)
        if hashlib.sha256(archived.read_bytes()).hexdigest()!=sha:raise OSError('web_archive_hash_mismatch')
        with archived.open('rb+') as f:os.fsync(f.fileno())
        s._fsync_directory(archived.parent)
        s.atomic_json(audit_path,audit);s._fsync_directory(audit_path.parent.parent)
    except OSError as exc:fail('archive','web_archive_failed',exc)
    try:
        with s.pipeline_lock():
            try:record=materialize_records([record],s.V2_ROOT/'media/full')[0]
            except EmbeddedMediaError as exc:fail('media','embedded_invalid',exc)
            index_path=s.WEB_INDEX_JSON
            if index_path.is_symlink():raise WebCaptureError('web_index_symlink')
            index=json.loads(index_path.read_text(encoding='utf-8-sig')) if index_path.exists() else {'articles':[]}
            merged,key,added,updated,proposals=merge_web(index,record,fid,s.failure_key(fid,fp))
            normalized=[normalize_web(a) for a in merged['articles']]
            targets=capture_targets(normalized,[key])
            if proposals:
                def proposal_key(p):
                    return hashlib.sha256(json.dumps({k:v for k,v in p.items() if k!='created_at'},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                ledger=s.load_proposals();known={proposal_key(p) for p in ledger['proposals']}
                for proposal in proposals:
                    digest=proposal_key(proposal)
                    if digest not in known:
                        proposal['created_at']=s._now_iso();ledger['proposals'].append(proposal);known.add(digest)
                s.save_proposals(ledger)
            if added or updated:
                backup_root=local_inbox.parent/(local_inbox.name+'.preservation')/'web-index'
                backup_root.mkdir(parents=True,exist_ok=True)
                def snapshot(raw_bytes):
                    target=backup_root/(hashlib.sha256(raw_bytes).hexdigest()+'.json')
                    if target.exists():
                        if target.is_symlink() or target.read_bytes()!=raw_bytes:raise OSError('web_index_snapshot_collision')
                    else:
                        with target.open('xb') as saved:saved.write(raw_bytes);saved.flush();os.fsync(saved.fileno())
                    s._fsync_directory(backup_root)
                if index_path.exists():snapshot(index_path.read_bytes())
                s.atomic_json(index_path,merged)
                snapshot(index_path.read_bytes())
    except s.PipelineLockTimeout as exc:
        raise s.FileFailure('merge','lock_timeout',content_sha256=sha,downloaded_path=path,lock_contention=True) from exc
    except (ValueError,TypeError,RecursionError) as exc:fail('validation','web_canonical_invalid',exc)
    # Durable audit and canonical writes precede a success checkpoint.
    s.mark_failures_resolved(fid)
    prior=processed.get(fid,{})
    claims=set(prior.get('claimedWebIds',[])) if isinstance(prior,dict) else set()
    claims.add(key)
    processed[fid]={'modifiedTime':fp[1],'size':fp[2],'at':s._now_iso(),'verification':'verified',
                    'webIds':[key],'claimedWebIds':sorted(claims),'content_sha256':sha,
                    'parser_version':VERSION,'validator_version':VERSION,
                    'publication_targets':targets,'publication_review_required':bool(proposals),
                    'conversion_audit':{k:audit[k] for k in ('version','scope','source_sha256','conversion_status','capture_completeness')}}
    state['processed']=processed;s.atomic_json(state_path,state)
    return added,updated,proposals
