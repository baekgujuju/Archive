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

종료 코드:
  0=정상(파일 실패 없음), 2=폴 락 경합(기존 의미),
  3=파일 실패 존재 + 실패 기록 영속화 완료 (scheduler는 경고 남기고 monitor 진행),
  1=폴 자체 실패(목록 조회·설정·정본·상태·증거 저장 등 — 중단)

P2-1: 파일 단위 처리 경계는 다운로드 → 파싱 → 검증 → 정본 병합 →
원본 보관 → 성공 기록 전체이다. subprocess timeout·JSON 파싱 오류가
바깥 예외로 빠져 폴 전체를 중단하지 않는다. 실패 기록은
.drive_poll_failures.json (버전 있는 원자적 JSON), 증거는
.quarantine/drive-files/<고유 디렉터리>/에 보존한다. 실패한 지문은
processed 성공으로 기록하지 않는다.

P2-2: legacy processed는 성공 증거가 아니다. legacyBaseline 항목은
unverified로 분류하고 폴당 제한된 배치(기본 5건)로 재검증한다.
재수집 병합은 merge_policy (누락 보충만, 충돌은 제안으로 분리).

P3: drive state 손상 시 격리 후 fail-closed (복구 마커로 구분,
신규 초기화가 아님).
"""
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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

FAILURES_FILE = HOOKS_DIR / ".drive_poll_failures.json"
PROPOSALS_FILE = HOOKS_DIR / ".merge_proposals.json"
NEEDS_REVIEW = HOOKS_DIR / ".needs-review"
QUARANTINE_DRIVE_DIR = HOOKS_DIR / ".quarantine" / "drive-files"
# 실패 시 다운로드 바이트를 임시로 보관하는 스테이징 (증거 저장 후 삭제).
STAGING_DIR = HOOKS_DIR / ".drive_poll_staging"

FAILURES_VERSION = 1
PROPOSALS_VERSION = 1
LEGACY_REVALIDATION_BATCH = 5
# backoff: 5분 * 2^(attempts-1), 최대 24시간.
BACKOFF_BASE_SECONDS = 300
BACKOFF_MAX_SECONDS = 86400
# 같은 방식의 연속 실패 2회 후 격리.
QUARANTINE_AFTER_SAME_FAILURES = 2
# stderr 발췌 상한.
STDERR_EXCERPT_LIMIT = 500

sys.path.insert(0, str(V2_ROOT))
sys.path.insert(0, str(HOOKS_DIR))
from pipeline_lock import PipelineLockTimeout, pipeline_lock  # noqa: E402
from state_guard import (  # noqa: E402
    StateBlockedError,
    StateReadError,
    append_review_entry,
    atomic_json_write as atomic_json,
    load_state_guarded,
    read_recovery_marker,
)
from merge_policy import (  # noqa: E402
    CanonicalDuplicateError,
    check_canonical_duplicates,
    merge_proposal_fingerprint,
    merge_records,
)


def log(msg):
    print(f"[drive_poll {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 300), **kw)
    return r


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


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
    """충돌 방지 파일명: basename + file_id 앞 8자 (임시 입력 디렉터리용)."""
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


def archive_filename(name, file_id, content_hash):
    """영속 보관용 파일명: basename + 전체 fileId + 콘텐츠 해시.

    ID 앞 8자 접미사는 반복 충돌까지 보장하지 못한다. fileId 전체 +
    콘텐츠 해시로 구분한다 (같은 파일 재보관은 같은 이름 — 멱등).
    """
    name = Path(name).name or file_id
    stem = Path(name).stem
    suffix = Path(name).suffix
    return f"{stem}_{file_id}_{content_hash[:12]}{suffix}"


# ---------- P2-1: 실패 기록 ----------

_SECRET_PATTERNS = ("token", "api_key", "apikey", "secret", "password",
                    "authorization", "cookie", "bearer")


def sanitize_stderr(stderr):
    """민감정보 제거한 stderr 발췌 + 잘림 여부. Returns (excerpt, truncated)."""
    if not stderr:
        return "", False
    lines = []
    for line in stderr.splitlines():
        lowered = line.lower()
        if any(p in lowered for p in _SECRET_PATTERNS):
            continue
        lines.append(line)
    text = "\n".join(lines)
    truncated = len(text) > STDERR_EXCERPT_LIMIT
    return text[:STDERR_EXCERPT_LIMIT], truncated


def failure_key(fid, fingerprint):
    """실패 키 = 파일 ID + 지문 (수정된 파일은 새 지문으로 다시 검증)."""
    return f"{fid}:{fingerprint[1]}:{fingerprint[2]}"


def load_failures():
    if not FAILURES_FILE.exists():
        return {"version": FAILURES_VERSION, "records": {}}
    try:
        data = json.loads(FAILURES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # 실패 기록 자체가 손상되면 부분 성공으로 반환하지 않는다.
        raise RuntimeError(f"실패 기록 읽기 실패: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
        raise RuntimeError("실패 기록 형식 오류")
    return data


def save_failures(data):
    atomic_json(FAILURES_FILE, data)


def load_proposals():
    if not PROPOSALS_FILE.exists():
        return {"version": PROPOSALS_VERSION, "proposals": []}
    try:
        data = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"제안 기록 읽기 실패: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise RuntimeError("제안 기록 형식 오류")
    return data


def save_proposals(data):
    atomic_json(PROPOSALS_FILE, data)


def backoff_seconds(attempts):
    """5분 * 2^(attempts-1), 최대 24시간."""
    return min(BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)), BACKOFF_MAX_SECONDS)


def persist_failure_record(record, *, lock_contention=False):
    """실패 기록을 원자적으로 저장한다 (실패 기록 저장 실패 → rc=1).

    같은 키의 기존 기록이 있으면 시도 횟수·시각을 누적한다.
    구조·신원 검증 실패(stage=validation)는 즉시 격리한다.
    같은 방식(동일 stage+error_code) 연속 실패 2회 후 격리한다.
    격리·백오프 판단은 콘텐츠 실패의 연속 횟수(consecutive)로 하며,
    누적 attempts가 아니다 (A→B→B는 B 2회 연속이지 3회가 아님).
    락 경합(lock_contention)은 별도 재시도 상태로 처리한다 — 콘텐츠
    실패 횟수에 포함하지 않고, 격리 사유가 되지 않으며, 고정 짧은
    백오프로 재시도한다.
    Returns 저장된 기록.
    """
    data = load_failures()
    records = data["records"]
    key = record["key"]
    prev = records.get(key)
    now = _now_iso()
    if prev is not None and isinstance(prev, dict):
        record["attempts"] = prev.get("attempts", 0) + 1
        record["first_failed_at"] = prev.get("first_failed_at", now)
        prev_consecutive = prev.get("consecutive", 0)
        prev_content_sig = prev.get("last_content_sig")
    else:
        record["attempts"] = 1
        record["first_failed_at"] = now
        prev_consecutive = 0
        prev_content_sig = None
    record["last_failed_at"] = now
    if lock_contention:
        # 락 경합은 콘텐츠 실패 체인을 끊지도 세지도 않는다.
        record["consecutive"] = prev_consecutive
        record["last_content_sig"] = prev_content_sig
        record["lock_contention"] = True
        record["status"] = "retry_pending"
        record["next_retry_at"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=BACKOFF_BASE_SECONDS)
        ).isoformat()
        records[key] = record
        save_failures(data)
        return record
    sig = (record["stage"], record["error_code"])
    prev_sig = tuple(prev_content_sig) if isinstance(prev_content_sig, list) else None
    consecutive = prev_consecutive + 1 if prev_sig == sig else 1
    record["consecutive"] = consecutive
    record["last_content_sig"] = [record["stage"], record["error_code"]]
    record["lock_contention"] = False
    if record["stage"] == "validation":
        record["status"] = "quarantined"
        record["next_retry_at"] = None
    elif consecutive >= QUARANTINE_AFTER_SAME_FAILURES:
        record["status"] = "quarantined"
        record["next_retry_at"] = None
    else:
        record["status"] = "retry_pending"
        record["next_retry_at"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=backoff_seconds(consecutive))
        ).isoformat()
    records[key] = record
    save_failures(data)
    if record["status"] == "quarantined":
        # .needs-review에는 기존 스키마를 깨뜨리지 않는 연결 방식
        # (commit "" + reason + 추가 키)으로 알린다.
        append_review_entry(
            NEEDS_REVIEW, "",
            f"drive_poll 격리: {record['fileId']} {record['stage']} "
            f"{record['error_code']}",
            extra={"fileId": record["fileId"], "failure_key": key,
                   "evidence_path": record.get("evidence_path")})
    return record


def mark_failures_resolved(fid):
    """해당 파일의 실패 기록을 resolved로 표시한다 (재처리 성공 시)."""
    if not FAILURES_FILE.exists():
        return False
    data = load_failures()
    changed = False
    for record in data["records"].values():
        if (isinstance(record, dict) and record.get("fileId") == fid
                and record.get("status") in ("retry_pending", "quarantined")):
            record["status"] = "resolved"
            record["resolved_at"] = _now_iso()
            changed = True
    if changed:
        save_failures(data)
    return changed


def save_evidence(downloaded_path, record):
    """다운로드 바이트를 증거 디렉터리에 보존한다. Returns 증거 경로.

    증거 저장 후 스테이징 원본은 삭제한다 (이미 복사됨). 증거 저장
    실패 시 스테이징 원본은 남겨 둔다 — 유일한 증거일 수 있다.
    """
    QUARANTINE_DRIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = Path(tempfile.mkdtemp(
        prefix=f"{record['fileId']}-", dir=str(QUARANTINE_DRIVE_DIR)))
    try:
        shutil.copy2(downloaded_path, dest_dir / "download.bin")
        (dest_dir / "failure.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    if str(downloaded_path).startswith(str(STAGING_DIR)):
        try:
            Path(downloaded_path).unlink(missing_ok=True)
        except OSError:
            pass
    return str(dest_dir)


class FileFailure(Exception):
    """파일 단위 실패 — 폴 전체 중단이 아니라 실패 기록으로 전환한다."""

    def __init__(self, stage, error_code, *, subprocess_rc=None,
                 stderr="", content_sha256=None, tweet_id=None,
                 downloaded_path=None, review_items=None,
                 lock_contention=False):
        super().__init__(f"{stage}: {error_code}")
        self.stage = stage
        self.error_code = error_code
        self.subprocess_rc = subprocess_rc
        self.stderr = stderr
        self.content_sha256 = content_sha256
        self.tweet_id = tweet_id
        self.downloaded_path = downloaded_path
        self.review_items = review_items
        # 락 경합은 콘텐츠 실패 횟수에 포함하지 않는다 (별도 재시도).
        self.lock_contention = lock_contention


def _validate_parsed_records(parsed, *, content_sha256=None, downloaded_path=None):
    """병합 전 수신 레코드 재검증 (ingester 결과의 방어적 재검사).

    빈 결과도 검증 실패이다 — 단일 HTML 입력이 레코드 0건을 낸 것은
    정상 캡처가 아니다 (ingester가 rc=0으로 빈 posts.json을 낸 경우).
    모든 검증 예외에 다운로드 바이트·해시를 연결한다 (증거 보존용).
    """
    sys.path.insert(0, str(TOOLS_DIR))
    from capture_contract import verify_record  # noqa: E402
    if not isinstance(parsed, list):
        raise FileFailure("validation", "parsed_not_list",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    if not parsed:
        raise FileFailure("validation", "empty_results",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    seen = set()
    for item in parsed:
        if not isinstance(item, dict) or not item.get("tweetId"):
            raise FileFailure("validation", "record_no_tweet_id",
                              content_sha256=content_sha256,
                              downloaded_path=downloaded_path)
        tid = str(item["tweetId"])
        if tid in seen:
            raise FileFailure("validation", "duplicate_tweet_id", tweet_id=tid,
                              content_sha256=content_sha256,
                              downloaded_path=downloaded_path)
        seen.add(tid)
        ok, errors = verify_record(item)
        if not ok:
            raise FileFailure("validation", "contract_failed",
                              tweet_id=tid, stderr="; ".join(errors),
                              content_sha256=content_sha256,
                              downloaded_path=downloaded_path)
    return parsed


def process_file(f, *, local_inbox, state, state_path, processed, inbox_id=None):
    """파일 1건 처리. Returns (added, updated, proposals) — 성공 시.

    FileFailure → 호출자가 실패 기록으로 전환한다.
    CanonicalDuplicateError / OSError(정본·상태 저장) → 폴 자체 실패로
    전파한다 (rc=1).
    """
    fid = f.get("id")
    name = f.get("name", "")
    fp = file_fingerprint(f)
    content_sha256 = None
    downloaded_path = None
    with tempfile.TemporaryDirectory(prefix="ingest_", dir=str(HOOKS_DIR)) as temporary:
        try:
            return _process_file_inner(
                f, fid, name, fp, work=Path(temporary),
                local_inbox=local_inbox, state=state, state_path=state_path,
                processed=processed, inbox_id=inbox_id)
        except FileFailure as failure:
            # 임시 디렉터리가 사라지기 전에 다운로드 바이트를 스테이징으로
            # 이동한다 (증거 저장 시 이 경로를 쓴다).
            if (failure.downloaded_path is not None
                    and Path(failure.downloaded_path).exists()):
                STAGING_DIR.mkdir(parents=True, exist_ok=True)
                staging = STAGING_DIR / (
                    f"{fid}-{failure.content_sha256[:12] if failure.content_sha256 else 'noread'}.bin")
                shutil.copy2(failure.downloaded_path, staging)
                failure.downloaded_path = str(staging)
            raise


def _process_file_inner(f, fid, name, fp, *, work, local_inbox, state, state_path, processed, inbox_id=None):
    content_sha256 = None
    downloaded_path = None
    inputs = work / "in"
    outputs = work / "out"
    inputs.mkdir()
    outputs.mkdir()
    path = safe_filename(name, fid, inputs)
    # 1) 다운로드.
    try:
        response = run(["hatch_gws_cli", "drive", "files", "get",
                        "--params", json.dumps({"fileId": fid, "alt": "media"}),
                        "-o", str(path)])
    except subprocess.TimeoutExpired as e:
        raise FileFailure("download", "timeout", stderr=str(e)) from e
    if response.returncode != 0:
        raise FileFailure("download", "download_failed",
                          subprocess_rc=response.returncode,
                          stderr=response.stderr or "")
    if not path.exists():
        raise FileFailure("download", "download_missing")
    downloaded_path = path
    content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    # 다운로드 중 Drive 지문 변경 — 성공 체크포인트를 남기지 않는다.
    # 크기만 비교하면 동일 크기로 수정된 파일을 놓친다. 다운로드 후
    # 메타데이터를 재조회해 modifiedTime 변화까지 확인한다.
    if str(path.stat().st_size) != str(f.get("size", "")):
        raise FileFailure("download", "fingerprint_drift",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    refreshed = None
    try:
        for entry in list_drive_files(inbox_id):
            if entry.get("id") == fid:
                refreshed = entry
                break
    except Exception as e:
        # 메타데이터 재조회 자체가 목록 조회 실패 — 폴 자체 실패로 전파.
        raise RuntimeError(f"Drive 메타데이터 재조회 실패: {e}") from e
    if refreshed is None:
        raise FileFailure("download", "fingerprint_drift",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    if (str(refreshed.get("modifiedTime", "")) != str(f.get("modifiedTime", ""))
            or str(refreshed.get("size", "")) != str(f.get("size", ""))):
        raise FileFailure("download", "fingerprint_drift",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    if not INGEST_SCRIPT.exists():
        raise RuntimeError("ingest 스크립트 없음")
# 2) 파싱 (ingester subprocess).
    try:
        response = run([sys.executable, str(INGEST_SCRIPT), str(inputs),
                        "-o", str(outputs)])
    except subprocess.TimeoutExpired as e:
        raise FileFailure("parse", "timeout", stderr=str(e),
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path) from e
    review_file = outputs / "review.json"
    review_items = None
    if review_file.exists():
        try:
            review_items = json.loads(review_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            review_items = None
    if response.returncode != 0:
        if isinstance(review_items, list) and review_items:
            # 검수 대상 — 정상 병합하지 않고 격리한다.
            raise FileFailure("validation", "review_items",
                              subprocess_rc=response.returncode,
                              stderr=response.stderr or "",
                              content_sha256=content_sha256,
                              downloaded_path=downloaded_path,
                              review_items=review_items)
        raise FileFailure("parse", "ingest_failed",
                          subprocess_rc=response.returncode,
                          stderr=response.stderr or "",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    post_file = outputs / "posts.json"
    if not post_file.exists():
        raise FileFailure("parse", "posts_missing",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path)
    try:
        parsed = json.loads(post_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FileFailure("parse", "posts_corrupt", stderr=str(e),
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path) from e
    if isinstance(review_items, list) and review_items:
        # rc=0인데 검수 항목이 있음 — 모순이므로 검증 실패로 처리한다.
        raise FileFailure("validation", "review_items",
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path,
                          review_items=review_items)
    # 3) 검증 (공통 계약 재검사).
    _validate_parsed_records(parsed, content_sha256=content_sha256,
                             downloaded_path=downloaded_path)
    # 4) 정본 병합 (공유 파이프라인 락 하에서만).
    # 락 타임아웃 시 이 파일만 실패로 세고 다음 파일로 간다
    # (폴 전체 중단 금지).
    try:
        with pipeline_lock():
            existing = json.loads(POSTS_JSON.read_text(encoding="utf-8")) \
                if POSTS_JSON.exists() else []
            if not isinstance(existing, list):
                raise ValueError("Canonical posts must be an array")
            result = merge_records(
                existing, parsed, file_id=fid,
                fingerprint=failure_key(fid, fp))
            if result.proposals:
                prop_data = load_proposals()
                seen_fp = {
                    merge_proposal_fingerprint(p)
                    for p in prop_data["proposals"]
                }
                for proposal in result.proposals:
                    fp_prop = merge_proposal_fingerprint(proposal)
                    if fp_prop not in seen_fp:
                        proposal["created_at"] = _now_iso()
                        prop_data["proposals"].append(proposal)
                        seen_fp.add(fp_prop)
                save_proposals(prop_data)
            if result.changed:
                atomic_json(POSTS_JSON, result.merged)
    except PipelineLockTimeout as e:
        log(f"파이프라인 락 타임아웃 — 다음 폴에서 재시도: {fid} ({e})")
        raise FileFailure("merge", "lock_timeout", stderr=str(e),
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path,
                          lock_contention=True) from e
    # 5) 원본 보관 (영속 보관 경로 — fileId + 콘텐츠 해시).
    try:
        shutil.copy2(path, local_inbox / archive_filename(name, fid, content_sha256))
    except OSError as e:
        raise FileFailure("archive", "archive_failed", stderr=str(e),
                          content_sha256=content_sha256,
                          downloaded_path=downloaded_path) from e
    # 6) 성공 기록 (검증된 fileId → tweetId·지문·콘텐츠 해시·버전).
    # 실패 기록 해소(resolved)를 상태 저장보다 먼저 수행한다 —
    # 실패 기록 저장이 실패하면 상태도 저장하지 않고 재시도한다.
    mark_failures_resolved(fid)
    sys.path.insert(0, str(TOOLS_DIR))
    from capture_contract import VALIDATOR_VERSION  # noqa: E402
    processed[fid] = {
        "modifiedTime": fp[1],
        "size": fp[2],
        "at": _now_iso(),
        "verification": "verified",
        "tweetIds": [str(item["tweetId"]) for item in parsed],
        "content_sha256": content_sha256,
        "parser_version": "ingest_post_html",
        "validator_version": VALIDATOR_VERSION,
    }
    state["processed"] = processed
    atomic_json(state_path, state)
    return result.added, result.updated, result.proposals


def handle_file_failure(f, failure):
    """FileFailure → 실패 기록 영속화 + processed 상태 갱신.

    증거 저장 실패는 폴 자체 실패로 전파한다 (rc=1) — 실패 기록을
    저장하지 못하고 부분 성공으로 반환하지 않는다.
    Returns 저장된 기록.
    """
    fid = f.get("id")
    fp = file_fingerprint(f)
    excerpt, truncated = sanitize_stderr(failure.stderr)
    record = {
        "key": failure_key(fid, fp),
        "fileId": fid,
        "fileName": f.get("name", ""),
        "modifiedTime": fp[1],
        "size": fp[2],
        "content_sha256": failure.content_sha256,
        "stage": failure.stage,
        "error_code": failure.error_code,
        "subprocess_rc": failure.subprocess_rc,
        "stderr_excerpt": excerpt,
        "stderr_truncated": truncated,
        "tweetId": failure.tweet_id,
        "evidence_path": None,
        "review_items": failure.review_items,
    }
    if failure.downloaded_path is not None and not failure.lock_contention:
        # 증거 저장 실패 → 폴 자체 실패 (부분 성공 반환 금지).
        # 락 경합은 파일 불량이 아니라 일시적 경합이므로 증거를 남기지
        # 않는다 (격리 디렉터리를 경합 증거로 채우지 않음).
        record["evidence_path"] = save_evidence(failure.downloaded_path, record)
    return persist_failure_record(record, lock_contention=failure.lock_contention)


def _verification_of(entry):
    """processed 항목의 검증 상태 (구형 항목 마이그레이션 포함)."""
    if not isinstance(entry, dict):
        return "unverified"
    if entry.get("legacyBaseline"):
        return "unverified"
    verification = entry.get("verification")
    if verification is None:
        # 구형 성공 항목 (at 있음) — 검증됨으로 간주.
        return "verified" if entry.get("at") else "unverified"
    return verification


def main():
    # The old 12h writer must be paused at activation; it does not honor this lock.
    SHARED_LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(SHARED_LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fh.close()
        return 2
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        inbox_id = cfg.get("inbox_folder_id")
        if not inbox_id:
            return 1
        local_inbox = Path(cfg.get("local_inbox", str(XBM / "drive_inbox")))
        local_inbox.mkdir(parents=True, exist_ok=True)
        state_path = HOOKS_DIR / ".drive_poll_state.json"
        # P3: drive state 손상 시 격리 후 fail-closed (신규 초기화 아님).
        try:
            state, state_status = load_state_guarded(state_path, kind="drive_poll_state")
        except (StateBlockedError, StateReadError) as e:
            log(f"drive state 복구 대기 — 폴 중단: {e}")
            return 1
        if state_status == "fresh":
            log("drive state 신규 생성")
        processed = state.get("processed", {})
        if not isinstance(processed, dict):
            # load_state_guarded가 이미 검증했으므로 도달 불가 — 방어.
            log("drive state processed 형식 오류 — 폴 중단")
            return 1
        # 정본 사전 검사 (중복 tweetId → 병합 전 명시적 차단, rc=1).
        if POSTS_JSON.exists():
            try:
                canonical = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log(f"정본 읽기 실패 — 폴 중단: {e}")
                return 1
            if not isinstance(canonical, list):
                log("정본이 배열이 아님 — 폴 중단")
                return 1
            dups = check_canonical_duplicates(canonical)
            if dups:
                log(f"정본 중복 tweetId — 병합 전 차단, 폴 중단: {dups}")
                return 1
        files = list_drive_files(inbox_id)
        file_ids = {f.get("id") for f in files if f.get("id")}
        # legacy state (읽기 전용 입력 — 손상 시 fail-closed, 격리하지 않음).
        legacy_path = Path(cfg.get("state_file", str(XBM / "drive_inbox_state.json")))
        if legacy_path.exists():
            try:
                legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log(f"legacy state 읽기 실패 — 폴 중단: {e}")
                return 1
            legacy = legacy_data.get("processed", {}) if isinstance(legacy_data, dict) else None
            if not isinstance(legacy, dict):
                log("legacy state processed 형식 오류 — 폴 중단")
                return 1
        else:
            legacy = {}
        # P2-2 사전 패스: legacy 미검증 항목 등록 + 소실 항목 unavailable +
        # 이번 폴 재검증 배치 선정.
        state_dirty = False
        for f in files:
            fid = f.get("id")
            if not fid:
                continue
            if fid not in processed and fid in legacy:
                fp = file_fingerprint(f)
                processed[fid] = {
                    "modifiedTime": fp[1], "size": fp[2],
                    "verification": "unverified",
                    "legacy": True,
                }
                state_dirty = True
        for fid, entry in list(processed.items()):
            if _verification_of(entry) == "unverified" and fid not in file_ids:
                # Drive에서 사라진 legacy 파일 — unavailable로 보고하고
                # 완료로 간주하지 않는다.
                processed[fid] = dict(entry) if isinstance(entry, dict) else {}
                processed[fid]["verification"] = "unavailable"
                processed[fid]["unavailable_at"] = _now_iso()
                state_dirty = True
        # Drive에 없는 legacy 항목 중 processed에 아예 없는 것도
        # unavailable로 등록한다 (위 루프는 processed 항목만 다룬다).
        for fid, entry in legacy.items():
            if fid in file_ids or fid in processed:
                continue
            if isinstance(entry, dict):
                processed[fid] = {
                    "modifiedTime": entry.get("modifiedTime", ""),
                    "size": str(entry.get("size", "")),
                    "verification": "unavailable",
                    "unavailable_at": _now_iso(),
                    "legacy": True,
                }
                state_dirty = True
        unverified = sorted(
            (fid for fid, entry in processed.items()
             # unavailable이었는데 파일이 다시 나타나면 재검증 대상이다
             # (배치 풀에 포함 — disposition의 주석과 일치).
             if _verification_of(entry) in ("unverified", "unavailable")
             and fid in file_ids),
            key=lambda fid: (
                str(processed[fid].get("modifiedTime", "")) if isinstance(processed.get(fid), dict) else "",
                fid))
        batch_fids = set(unverified[:LEGACY_REVALIDATION_BATCH])
        if state_dirty:
            state["processed"] = processed
            atomic_json(state_path, state)
        failures = 0
        added = updated = 0
        revalidated = 0
        for f in files:
            fid = f.get("id")
            name = f.get("name", "")
            fp = file_fingerprint(f)
            if not fid:
                continue
            previous = processed.get(fid)
            was_revalidation = (
                isinstance(previous, dict)
                and _verification_of(previous) in ("unverified", "unavailable"))
            # 처리 여부 판정.
            if isinstance(previous, dict):
                verification = _verification_of(previous)
                # 미검증 legacy는 지문 일치 여부와 관계없이 폴당 배치
                # 제한을 적용한다 (지문 변경이 제한을 우회하지 않음).
                if verification in ("unverified", "unavailable") \
                        and fid not in batch_fids:
                    continue
                fingerprint_match = (
                    previous.get("modifiedTime"), str(previous.get("size", ""))
                ) == (fp[1], fp[2])
                if fingerprint_match:
                    if verification == "verified":
                        continue
                    if verification == "quarantined":
                        continue  # 운영자 검토 대기 — 자동 재시도 없음.
                    if verification == "retry_pending":
                        # 실패 기록이 손상됐으면 조용히 재처리하지 않고
                        # 폴 자체 실패로 전파한다 (백오프 상태를 믿을 수 없음).
                        failures_data = load_failures()
                        next_retry = None
                        rec = failures_data["records"].get(failure_key(fid, fp))
                        if isinstance(rec, dict):
                            next_retry = rec.get("next_retry_at")
                        if next_retry and datetime.now(timezone.utc) < datetime.fromisoformat(next_retry):
                            continue  # backoff 대기 중.
                    # fingerprint_match + retry_pending(백오프 만료) → 재처리.
                    # fingerprint_match + unverified/unavailable(배치 내) → 재검증.
                # 지문 불일치 → 수정된 파일 — 일반 경로로 재처리.
                # (미검증 legacy는 위에서 배치 제한을 이미 적용받음.)
            elif fid in legacy:
                # 사전 패스에서 이미 unverified로 등록됨 — 방어.
                if fid not in batch_fids:
                    continue
            # 신규 파일은 배치 제한 없이 처리한다.
            if not name.lower().endswith((".html", ".htm")):
                log("Unsupported material remains pending: " + name)
                continue
            if not INGEST_SCRIPT.exists():
                return 1
            try:
                file_added, file_updated, _proposals = process_file(
                    f, local_inbox=local_inbox, state=state,
                    state_path=state_path, processed=processed,
                    inbox_id=inbox_id)
            except FileFailure as failure:
                record = handle_file_failure(f, failure)
                # processed에 실패 상태 반영 (다음 폴 disposition용).
                processed[fid] = {
                    "modifiedTime": fp[1],
                    "size": fp[2],
                    "verification": record["status"],
                    "last_failure_at": record["last_failed_at"],
                    "failure_key": record["key"],
                }
                state["processed"] = processed
                atomic_json(state_path, state)
                failures += 1
                if was_revalidation:
                    revalidated += 1
                continue
            added += file_added
            updated += file_updated
            if was_revalidation:
                revalidated += 1
            # process_file이 성공 기록·상태 저장을 마쳤다.
        state["processed"] = processed
        state["last_poll"] = _now_iso()
        atomic_json(state_path, state)
        log(f"Added {added}, updated {updated}, failed {failures} "
            f"(legacy 재검증 {revalidated}건); no model calls")
        return 3 if failures else 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        log(str(error))
        return 1
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
