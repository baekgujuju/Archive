#!/usr/bin/env python3
"""
v2 5분 Drive 인제스트 폴 (결정적, 모델 호출 없음)

기존 12시간 x-drive-inbox-monitor의 결정적 부분만 5분 간격으로 실행:
  1. Drive 수신함("5. X 게시물, 메모모음") 파일 목록 조회 (페이지네이션)
  2. drive_inbox_state.json과 비교 → 신규/변경 파일만 다운로드
     지문: (id, modifiedTime, size) — 수정된 파일도 재처리
  3. HTML → ingest_post_html.py <tmpdir> -o <outdir> → posts.json 병합
     (tweetId 중복 제외, 실패 시 processed에 기록하지 않음)
  4. 상태 파일 원자적 업데이트

모델 호출 없음. 페르소나 생성·도구 요약은 12시간 크론에 유지.
posts.json이 변경되면 monitor.py(5분)가 감지 → 후크 → 빌드/번역/커밋.

공유 락: ~/workspace/x-bookmarks/.drive_ingest.lock
(12시간 러너와 공유 — 동시 인제스트 방지)

사용법:
    python3 v2/hooks/drive_poll.py

종료 코드: 0=정상(신규없음 포함), 1=오류
"""
import fcntl
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = V2_ROOT / "hooks"

HOME = Path.home()
XBM = HOME / "workspace" / "x-bookmarks"
CONFIG_PATH = XBM / "drive_archive_config.json"
TOOLS_DIR = XBM / "tools"
INGEST_SCRIPT = TOOLS_DIR / "ingest_post_html.py"
POSTS_JSON = XBM / "posts.json"
# 인제스트 락 (5m 폴 자체 중복 실행 방지)
# 12h 러너는 flock을 사용하지 않음이 확인됨 → 격리된 상태로 충돌 회피
SHARED_LOCK = HOOKS_DIR / ".drive_poll.lock"

sys.path.insert(0, str(V2_ROOT))
from pipeline_lock import PipelineLockTimeout, pipeline_lock  # noqa: E402


def log(msg):
    print(f"[drive_poll {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 300), **kw)
    return r


def list_drive_files(inbox_id):
    """페이지네이션으로 전체 파일 목록 조회."""
    all_files = []
    page_token = None
    while True:
        params = {
            "q": f"'{inbox_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        r = run([
            "hatch_gws_cli", "drive", "files", "list",
            "--params", json.dumps(params),
        ])
        if r.returncode != 0:
            raise RuntimeError(f"Drive 목록 실패: {r.stderr[:300]}")
        try:
            data = json.loads(r.stdout)
        except ValueError as e:
            raise RuntimeError(f"Drive 응답 파싱 실패: {e}")
        if not isinstance(data, dict):
            raise RuntimeError("Drive 응답 형식 오류")
        all_files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        log(f"  다음 페이지: {len(all_files)}개 수집됨")
    return all_files


def file_fingerprint(f):
    """(id, modifiedTime, size) 지문."""
    return (
        f.get("id", ""),
        f.get("modifiedTime", ""),
        str(f.get("size", "")),
    )


def safe_filename(name, file_id, dest_dir):
    """충돌 방지 파일명: basename + file_id 앞 8자."""
    # 경로 탐색 방지
    name = Path(name).name
    if not name:
        name = file_id
    # 이미 존재하면 ID 접미사
    candidate = dest_dir / name
    if candidate.exists():
        stem = Path(name).stem
        suffix = Path(name).suffix
        name = f"{stem}_{file_id[:8]}{suffix}"
    return dest_dir / name


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)

def main():
    # The old 12h writer must be paused at activation; it does not honor this lock.
    SHARED_LOCK.parent.mkdir(parents=True,exist_ok=True)
    lock_fh=open(SHARED_LOCK,'w')
    try:fcntl.flock(lock_fh,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:lock_fh.close();return 2
    try:
        cfg=json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        inbox_id=cfg.get('inbox_folder_id')
        if not inbox_id:return 1
        local_inbox=Path(cfg.get('local_inbox',str(XBM/'drive_inbox')))
        local_inbox.mkdir(parents=True,exist_ok=True)
        state_path=HOOKS_DIR/'.drive_poll_state.json'
        state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        processed=state.get('processed',{})
        files=list_drive_files(inbox_id)
        # Establish fingerprints for files already processed by the legacy runner.
        legacy_path=Path(cfg.get('state_file',str(XBM/'drive_inbox_state.json')))
        legacy=json.loads(legacy_path.read_text(encoding='utf-8')).get('processed',{}) if legacy_path.exists() else {}
        failures=0;added=updated=0
        for f in files:
            fid=f.get('id');name=f.get('name','');fp=file_fingerprint(f)
            if not fid:continue
            previous=processed.get(fid)
            if not previous and fid in legacy:
                processed[fid]={'modifiedTime':fp[1],'size':fp[2],'legacyBaseline':True}
                continue
            if isinstance(previous,dict) and (previous.get('modifiedTime'),str(previous.get('size','')))==(fp[1],fp[2]):continue
            if not name.lower().endswith(('.html','.htm')):
                log('Unsupported material remains pending: '+name)
                continue
            if not INGEST_SCRIPT.exists():return 1
            with tempfile.TemporaryDirectory(prefix='ingest_',dir=HOOKS_DIR) as temporary:
                work=Path(temporary);inputs=work/'in';outputs=work/'out';inputs.mkdir();outputs.mkdir()
                path=safe_filename(name,fid,inputs)
                response=run(['hatch_gws_cli','drive','files','get','--params',json.dumps({'fileId':fid,'alt':'media'}),'-o',str(path)])
                if response.returncode or not path.exists():failures+=1;continue
                response=run([sys.executable,str(INGEST_SCRIPT),str(inputs),'-o',str(outputs)])
                post_file=outputs/'posts.json'
                if response.returncode or not post_file.exists():failures+=1;continue
                parsed=json.loads(post_file.read_text(encoding='utf-8'))
                valid=isinstance(parsed,list) and parsed and all(isinstance(item,dict) and item.get('tweetId') and isinstance(item.get('fullText'),str) for item in parsed)
                if not valid:failures+=1;continue
                # 정본 병합은 공유 파이프라인 락 하에서만 수행 (P1-2).
                # 락 타임아웃 시 이 파일만 실패로 세고 다음 파일로 간다 (폴 전체 중단 금지).
                try:
                    with pipeline_lock():
                        existing=json.loads(POSTS_JSON.read_text(encoding='utf-8')) if POSTS_JSON.exists() else []
                        if not isinstance(existing,list):raise ValueError('Canonical posts must be an array')
                        indices={str(item['tweetId']):i for i,item in enumerate(existing) if isinstance(item,dict) and item.get('tweetId')}
                        changed=False
                        for item in parsed:
                            tid=str(item['tweetId'])
                            if tid in indices:
                                old=existing[indices[tid]];merged=dict(old)
                                for key,value in item.items():
                                    if value not in (None,'',[]):merged[key]=value
                                if merged!=old:existing[indices[tid]]=merged;updated+=1;changed=True
                            else:indices[tid]=len(existing);existing.append(item);added+=1;changed=True
                        if changed:atomic_json(POSTS_JSON,existing)
                except PipelineLockTimeout as e:
                    log(f"파이프라인 락 타임아웃 — 다음 폴에서 재시도: {fid} ({e})")
                    failures+=1;continue
                shutil.copy2(path,safe_filename(name,fid,local_inbox))
                # Per-file success: one malformed capture cannot mark another complete.
                processed[fid]={'modifiedTime':fp[1],'size':fp[2],'at':datetime.now(timezone.utc).isoformat()}
                state['processed']=processed;atomic_json(state_path,state)
        state['processed']=processed;state['last_poll']=datetime.now(timezone.utc).isoformat();atomic_json(state_path,state)
        log(f'Added {added}, updated {updated}, failed {failures}; no model calls')
        return 1 if failures else 0
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
        log(str(error));return 1
    finally:
        fcntl.flock(lock_fh,fcntl.LOCK_UN);lock_fh.close()

if __name__=='__main__':sys.exit(main())
