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
# 12시간 러너와 공유하는 락
SHARED_LOCK = XBM / ".drive_ingest.lock"


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


def main():
    # 공유 락 (12시간 러너와 공유)
    SHARED_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(SHARED_LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("다른 인제스트 실행 중 (12h 러너 또는 다른 폴) — 종료")
        return 2
    log("공유 락 획득")

    try:
        if not CONFIG_PATH.exists():
            log(f"설정 없음: {CONFIG_PATH}")
            return 1
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        inbox_id = cfg.get("inbox_folder_id", "")
        local_inbox = Path(cfg.get("local_inbox", str(XBM / "drive_inbox")))
        state_path = Path(cfg.get("state_file", str(XBM / "drive_inbox_state.json")))
        if not inbox_id:
            log("inbox_folder_id 없음")
            return 1

        local_inbox.mkdir(parents=True, exist_ok=True)
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        # processed: {file_id: {"modifiedTime": ..., "size": ..., "at": ...}}
        processed = state.get("processed", {})
        # 구형식 (file_id → timestamp 문자열) 마이그레이션
        for fid, val in list(processed.items()):
            if isinstance(val, str):
                processed[fid] = {"modifiedTime": "", "size": "", "at": val}

        # 1. 전체 목록 (페이지네이션)
        try:
            files = list_drive_files(inbox_id)
        except RuntimeError as e:
            log(str(e))
            return 1
        log(f"수신함 파일: {len(files)}개")

        # 2. 신규/변경 식별 (지문 비교)
        to_process = []
        for f in files:
            fid = f.get("id", "")
            if not fid:
                continue
            fp = file_fingerprint(f)
            prev = processed.get(fid)
            if prev is None:
                to_process.append((f, "new"))
            elif (prev.get("modifiedTime"), prev.get("size")) != (fp[1], fp[2]):
                to_process.append((f, "modified"))
        if not to_process:
            log("신규/변경 파일 없음 — 종료 (모델 호출 없음)")
            return 0
        log(f"처리 대상: {len(to_process)}개")

        # 3. 다운로드
        tmpdir = Path(tempfile.mkdtemp(prefix="drive_poll_"))
        html_paths = []
        try:
            for f, reason in to_process:
                fid = f.get("id", "")
                fname = f.get("name", fid)
                local_path = safe_filename(fname, fid, local_inbox)
                log(f"다운로드 ({reason}): {fname} → {local_path.name}")

                r = run([
                    "hatch_gws_cli", "drive", "files", "get",
                    "--params", json.dumps({"fileId": fid, "alt": "media"}),
                    "-o", str(local_path),
                ])
                if r.returncode != 0 or not local_path.exists():
                    log(f"  다운로드 실패: {fname} — processed에 기록하지 않음")
                    continue

                if fname.lower().endswith((".html", ".htm")):
                    # 인제스트용 임시 디렉토리에 복사
                    dest = tmpdir / local_path.name
                    shutil.copy2(str(local_path), str(dest))
                    html_paths.append((f, dest))
                else:
                    log(f"  HTML 아님 — 다운로드만")
                    # HTML이 아니어도 처리 완료로 기록 (재다운로드 방지)
                    fp = file_fingerprint(f)
                    processed[fid] = {
                        "modifiedTime": fp[1], "size": fp[2],
                        "at": datetime.now(timezone.utc).isoformat(),
                    }

            # 4. 인제스트 (HTML만)
            if html_paths and INGEST_SCRIPT.exists():
                outdir = tmpdir / "out"
                outdir.mkdir(exist_ok=True)
                # ingest_post_html.py는 디렉토리를 입력으로 받음
                indir = tmpdir / "in"
                indir.mkdir(exist_ok=True)
                for f, src in html_paths:
                    shutil.copy2(str(src), str(indir / src.name))

                r = run([
                    sys.executable, str(INGEST_SCRIPT),
                    str(indir), "-o", str(outdir),
                ])
                if r.returncode != 0:
                    log(f"인제스트 실패: {r.stderr[:300]} — processed에 기록하지 않음")
                    # 실패한 파일은 processed에 기록하지 않음 → 다음 실행에서 재시도
                else:
                    posts_out = outdir / "posts.json"
                    if not posts_out.exists():
                        log("인제스트 출력 없음 — processed에 기록하지 않음")
                    else:
                        new_recs = json.loads(posts_out.read_text(encoding="utf-8"))
                        # posts.json에 병합 (tweetId 중복 제외)
                        existing = []
                        if POSTS_JSON.exists():
                            existing = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
                        seen = {r.get("tweetId") for r in existing if r.get("tweetId")}
                        added = 0
                        for rec in new_recs:
                            tid = rec.get("tweetId")
                            if tid and tid not in seen:
                                existing.append(rec)
                                seen.add(tid)
                                added += 1
                        if added > 0:
                            # 원자적 쓰기
                            tmp_posts = POSTS_JSON.with_suffix(".tmp")
                            tmp_posts.write_text(
                                json.dumps(existing, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                            tmp_posts.rename(POSTS_JSON)
                            log(f"posts.json에 {added}개 병합")
                        else:
                            log("신규 레코드 없음 (중복)")

                        # 성공한 파일만 processed에 기록
                        for f, _ in html_paths:
                            fid = f.get("id", "")
                            fp = file_fingerprint(f)
                            processed[fid] = {
                                "modifiedTime": fp[1], "size": fp[2],
                                "at": datetime.now(timezone.utc).isoformat(),
                            }
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        # 5. 상태 원자적 저장
        state["processed"] = processed
        state["last_poll"] = datetime.now(timezone.utc).isoformat()
        tmp_state = state_path.with_suffix(".tmp")
        tmp_state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_state.rename(state_path)
        log("상태 저장 완료")
        return 0

    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()
        log("락 해제")


if __name__ == "__main__":
    sys.exit(main())
