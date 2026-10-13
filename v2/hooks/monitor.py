#!/usr/bin/env python3
"""
v2 5분 경량 모니터 (결정적, 모델 호출 없음)

정규 소스 5종의 sha256을 워터마크와 비교:
  - X:   ~/workspace/x-bookmarks/posts.json
  - Web: ~/workspace/article-archive/index.json
  - IB:  ~/workspace/x-bookmarks/study_links.json
         ~/workspace/x-bookmarks/resource_library/index.json
         ~/workspace/x-bookmarks/resource_library/ib_juwon.json

변경 없으면 조용히 종료 (LLM/API 호출 절대 없음).
변경 있으면 post_ingest_hook.py 실행.
워터마크는 후크 성공 후에만 저장 → 실패 시 다음 실행에서 재시도됨.

사용법 (크론에서 5분 간격 호출):
    python3 v2/hooks/monitor.py
    python3 v2/hooks/monitor.py --force   # 워터마크 무시하고 후크 실행

종료 코드: 0=정상(변경없음 포함), 1=후크 실패, 2=락/환경 오류
워터마크: v2/hooks/.monitor_state.json (소스 해시 기록)
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = V2_ROOT / "hooks"
STATE_FILE = HOOKS_DIR / ".monitor_state.json"
LOCK_FILE = HOOKS_DIR / ".monitor.lock"

HOME = Path.home()
SOURCES = {
    "posts.json (X)": HOME / "workspace" / "x-bookmarks" / "posts.json",
    "index.json (Web)": HOME / "workspace" / "article-archive" / "index.json",
    "study_links.json (IB)": HOME / "workspace" / "x-bookmarks" / "study_links.json",
    "resource_library/index.json (IB)": HOME / "workspace" / "x-bookmarks" / "resource_library" / "index.json",
    "ib_juwon.json (IB)": HOME / "workspace" / "x-bookmarks" / "resource_library" / "ib_juwon.json",
}


def sha256_file(path: Path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def current_hashes():
    out = {}
    for name, path in SOURCES.items():
        out[name] = sha256_file(path) if path.exists() else None
    # IB 정본 해시 (텍스트 에셋 포함) — ib_adapter.canonical_fingerprint()
    try:
        sys.path.insert(0, str(V2_ROOT))
        from ib_adapter import canonical_fingerprint
        out["ib_canonical_fingerprint"] = canonical_fingerprint()
    except Exception:
        out["ib_canonical_fingerprint"] = None
    return out


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def save_state(hashes):
    STATE_FILE.write_text(
        json.dumps({
            "hashes": hashes,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="v2 5분 경량 모니터")
    parser.add_argument("--force", action="store_true", help="워터마크 무시하고 후크 실행")
    args = parser.parse_args()

    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        print("[monitor] 다른 모니터 실행 중 — 종료")
        return 2

    try:
        current = current_hashes()
        prev = load_state().get("hashes", {})

        # 재시도 마커 확인 (소스 변경 없어도 후크 실행)
        retry_marker = (
            (HOOKS_DIR / ".retry_translation").exists() or
            (HOOKS_DIR / ".retry_commit").exists() or
            (HOOKS_DIR / ".retry_media").exists()
        )
        if retry_marker:
            print("[monitor] 재시도 마커 감지 — 후크 실행")

        # A restart or publication activation must also retry pending artifacts/commits.
        dirty=subprocess.run(['git','-C',str(V2_ROOT),'status','--porcelain','--','data/','media/','ib-library.json'],capture_output=True,text=True)
        branch=os.environ.get('V2_PUBLISH_BRANCH','main')
        pending=subprocess.run(['git','-C',str(V2_ROOT),'rev-list','--count',f'origin/{branch}..HEAD'],capture_output=True,text=True)
        pending_work=bool(dirty.returncode==0 and dirty.stdout.strip()) or bool(os.environ.get('V2_PUBLISH')=='1' and pending.returncode==0 and pending.stdout.strip()!='0')
        changed = args.force or retry_marker or pending_work or any(current[k] != prev.get(k) for k in current)
        if not changed:
            save_state(current)
            return 0

        if not retry_marker:
            diffs = [k for k in current if current[k] != prev.get(k)]
            print(f"[monitor] 변경 감지: {diffs}")
        # 워터마크는 후크 성공 후에만 저장 (실패 시 재시도 보장)

        r = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "post_ingest_hook.py")],
            capture_output=True, text=True, timeout=1800,
        )
        print(r.stdout[-3000:])
        if r.returncode != 0:
            print(f"[monitor] 후크 실패 (exit {r.returncode}) — 워터마크 유지, 다음 실행에서 재시도")
            print(r.stderr[-1000:] if r.stderr else "")
            return r.returncode

        # 후크 성공 → 워터마크 저장
        save_state(current)
        print("[monitor] 후크 성공 — 워터마크 저장")
        return 0
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
