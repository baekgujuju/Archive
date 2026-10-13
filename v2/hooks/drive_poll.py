#!/usr/bin/env python3
"""
v2 5분 Drive 인제스트 폴 (결정적, 모델 호출 없음)

기존 12시간 x-drive-inbox-monitor의 결정적 부분만 5분 간격으로 실행:
  1. Drive 수신함("5. X 게시물, 메모모음") 파일 목록 조회
  2. drive_inbox_state.json과 비교 → 신규 파일만 다운로드
  3. HTML → ingest_post_html.py → posts.json 병합 (tweetId 중복 제외)
  4. 상태 파일 업데이트

모델 호출 없음. 페르소나 생성·도구 요약 등 무거운 작업은 12시간 크론에 유지.
posts.json이 변경되면 monitor.py(5분)가 감지 → 후크 → 빌드/번역/커밋.

기존 Drive 크레덴셜/스코프 사용 (hatch_gws_cli, 별도 인증 불필요).

사용법:
    python3 v2/hooks/drive_poll.py

종료 코드: 0=정상(신규없음 포함), 1=오류
"""
import fcntl
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = V2_ROOT / "hooks"
LOCK_FILE = HOOKS_DIR / ".drive_poll.lock"

HOME = Path.home()
XBM = HOME / "workspace" / "x-bookmarks"
CONFIG_PATH = XBM / "drive_archive_config.json"
TOOLS_DIR = XBM / "tools"
INGEST_SCRIPT = TOOLS_DIR / "ingest_post_html.py"


def log(msg):
    print(f"[drive_poll {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 300), **kw)
    return r


def main():
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        log("다른 폴 실행 중 — 종료")
        return 2

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
        processed = state.get("processed", {})

        # 1. Drive 수신함 목록 (결정적)
        r = run([
            "hatch_gws_cli", "drive", "files", "list",
            "--params", json.dumps({
                "q": f"'{inbox_id}' in parents and trashed=false",
                "fields": "files(id,name,mimeType,modifiedTime,size)",
                "pageSize": 100,
            }),
        ])
        if r.returncode != 0:
            log(f"Drive 목록 실패: {r.stderr[:300]}")
            return 1
        try:
            listing = json.loads(r.stdout)
        except ValueError as e:
            log(f"Drive 응답 파싱 실패: {e}")
            return 1
        files = listing.get("files", []) if isinstance(listing, dict) else []
        log(f"수신함 파일: {len(files)}개")

        # 2. 신규 파일 식별
        new_files = [f for f in files if f.get("id") not in processed]
        if not new_files:
            log("신규 파일 없음 — 종료 (모델 호출 없음)")
            return 0
        log(f"신규 파일: {len(new_files)}개")

        # 3. 다운로드 + 인제스트
        ingested = 0
        for f in new_files:
            fid = f.get("id", "")
            fname = f.get("name", fid)
            local_path = local_inbox / fname
            log(f"다운로드: {fname}")

            r = run([
                "hatch_gws_cli", "drive", "files", "get",
                "--params", json.dumps({"fileId": fid, "alt": "media"}),
                "-o", str(local_path),
            ])
            if r.returncode != 0 or not local_path.exists():
                log(f"  다운로드 실패: {fname}")
                continue

            # HTML만 인제스트 (결정적 파싱, 모델 호출 없음)
            if fname.lower().endswith((".html", ".htm")) and INGEST_SCRIPT.exists():
                r = run([sys.executable, str(INGEST_SCRIPT), str(local_path)])
                if r.returncode == 0:
                    ingested += 1
                    log(f"  인제스트 완료: {fname}")
                else:
                    log(f"  인제스트 실패: {fname} — {r.stderr[:200]}")
            else:
                log(f"  HTML 아님 — 다운로드만: {fname}")

            # 처리 기록 (실패해도 기록 → 무한 재시도 방지)
            processed[fid] = datetime.now(timezone.utc).isoformat()

        # 4. 상태 저장
        state["processed"] = processed
        state["last_poll"] = datetime.now(timezone.utc).isoformat()
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"완료: 인제스트 {ingested}개, 상태 저장")

        # posts.json이 변경되었으면 monitor.py가 다음 틱에서 감지
        return 0

    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
