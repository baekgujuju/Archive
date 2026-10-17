#!/usr/bin/env python3
"""상태 파일 손상 격리 가드 (P3).

drive state와 monitor state의 성격을 구분한다.
- drive state: 격리 후 fail-closed. 손상 감지 + 영속 복구 마커를 먼저
  남기고, 검증된 백업 복원 또는 명시적 재구축으로 해제한다. 자동
  재구축은 도입하지 않는다. 격리로 파일이 없어졌다고 다음 실행이
  신규 설치로 초기화하면 fail-closed가 아니다 — 복구 마커로 구분한다.
- monitor state: 파생 워터마크이므로 격리·로그·검수 기록 후 보이는
  초기화를 허용한다. 후크 성공 후에만 새 워터마크를 저장한다.

격리는 pipeline_lock.quarantine_file()을 재사용한다. 메타데이터
(원래 경로·UTC 시각·바이트 해시·오류·코드 SHA·복원 절차)는 격리된
디렉터리 안 metadata.json에 남긴다.

손상 판정: 잘린 JSON·비UTF-8·잘못된 구조([], 잘못된 processed/hashes,
잘못된 항목 타입)까지 잡는다. 읽기 권한 오류는 손상으로 간주하지
않는다 (인프라 실패 — 격리하지 않고 그대로 raise). 격리 실패 시
원본을 덮지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline_lock import quarantine_file

# 복구 마커 파일명 (state 파일명 + 접미사).
RECOVERY_SUFFIX = ".recovery"


class StateCorruptError(ValueError):
    """상태 파일이 손상됨 — 격리 대상."""


class StateBlockedError(RuntimeError):
    """복구 마커 존재 — fail-closed 중단 (신규 초기화 금지)."""


class StateReadError(OSError):
    """상태 파일 읽기 인프라 실패 (권한 등) — 손상이 아님."""


def code_sha():
    """가드 코드 버전 (이 파일의 바이트 SHA-256)."""
    h = hashlib.sha256()
    with open(Path(__file__), "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def recovery_marker_path(state_path):
    return Path(str(state_path) + RECOVERY_SUFFIX)


def read_recovery_marker(state_path):
    """복구 마커 dict 또는 None."""
    marker = recovery_marker_path(state_path)
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"raw": "invalid"}
    except (OSError, ValueError):
        return {"raw": "unreadable"}


def write_recovery_marker(state_path, *, kind, quarantined_path,
                          original_path, byte_sha256, error, status="quarantined"):
    """복구 마커를 원자적으로 쓴다 (격리 정보 + 복원 절차).

    status: "quarantine_pending" (이동 전 의도 마커 — 이동 직후 중단돼도
    다음 실행이 fresh로 진행하지 않는다) | "quarantined" (격리 완료).
    """
    marker = recovery_marker_path(state_path)
    if kind == "drive_poll_state":
        procedure = (
            "1) 격리본(.quarantine/...)의 해시·스키마를 검사한다. "
            "2) 검증된 백업을 원자적으로 복원한다. "
            "3) 복구 마커를 삭제한다. 마커가 있으면 다음 실행도 "
            "fail-closed로 중단된다 (신규 초기화가 아님)."
        )
        if status == "quarantine_pending":
            procedure += (
                " 마커 상태가 quarantine_pending이면 격리가 중단됐을 수 있어 "
                "원본이 남아 있거나 이미 이동했을 수 있다 — 격리 디렉터리를 "
                "확인한 뒤 마커를 삭제한다."
            )
    else:
        procedure = (
            "격리된 워터마크는 파생 값이다. 다음 실행은 보이는 초기화로 "
            "진행하고 후크 성공 후 새 워터마크를 저장한다. 새 워터마크가 "
            "저장되면 이 마커는 자동으로 제거된다."
        )
    data = {
        "kind": kind,
        "status": status,
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
        "quarantined_path": str(quarantined_path) if quarantined_path else None,
        "original_path": str(original_path),
        "byte_sha256": byte_sha256,
        "error": error,
        "code_sha": code_sha(),
        "restore_procedure": procedure,
    }
    atomic_json_write(marker, data)
    return marker


def _validate_processed_entry(entry, path):
    """processed 항목 검증. Returns 오류 문자열 또는 None."""
    if not isinstance(entry, dict):
        return f"{path}: 항목이 dict가 아님"
    mt = entry.get("modifiedTime")
    if mt is not None and not isinstance(mt, str):
        return f"{path}: modifiedTime가 문자열이 아님"
    size = entry.get("size")
    if size is not None and not isinstance(size, (str, int)):
        return f"{path}: size가 문자열/정수가 아님"
    ver = entry.get("verification")
    if ver is not None and ver not in (
            "verified", "unverified", "retry_pending", "quarantined", "unavailable"):
        return f"{path}: verification 값 오류"
    return None


def validate_drive_state(data):
    """drive state 구조 검증. Returns (ok, reason)."""
    if not isinstance(data, dict):
        return False, "drive state가 dict가 아님"
    processed = data.get("processed")
    if processed is not None:
        if not isinstance(processed, dict):
            return False, "drive state processed가 dict가 아님"
        for fid, entry in processed.items():
            err = _validate_processed_entry(entry, f"processed[{fid}]")
            if err:
                return False, err
    last_poll = data.get("last_poll")
    if last_poll is not None and not isinstance(last_poll, str):
        return False, "drive state last_poll가 문자열이 아님"
    return True, ""


def validate_monitor_state(data):
    """monitor state 구조 검증. Returns (ok, reason)."""
    if not isinstance(data, dict):
        return False, "monitor state가 dict가 아님"
    hashes = data.get("hashes")
    if hashes is None:
        return False, "monitor state에 hashes 없음"
    if not isinstance(hashes, dict):
        return False, "monitor state hashes가 dict가 아님"
    for key, value in hashes.items():
        if value is not None and not isinstance(value, str):
            return False, f"monitor state hashes[{key}]가 문자열/None이 아님"
    return True, ""


def load_state_guarded(state_path, *, kind):
    """상태 파일 읽기 + 검증 + 손상 시 격리.

    kind: "drive_poll_state" | "monitor_state".
    Returns (data, status) — status ∈ {"ok", "fresh", "post_quarantine_init"}.
    - 손상 → 격리 + 복구 마커 + (drive: StateBlockedError / monitor: ({}, "post_quarantine_init")).
    - 파일 없음 + 복구 마커 있음 → drive: StateBlockedError / monitor: ({}, "post_quarantine_init").
    - 파일 없음 + 마커 없음 → ({}, "fresh").
    - 읽기 권한 오류 → StateReadError (손상 아님, 격리 없음).
    """
    state_path = Path(state_path)
    # 복구 마커를 state 파일 존재 여부보다 먼저 검사한다. 마커가 남은
    # 상태에서 유효한 state 파일이 생겨도 복구 차단을 우회하지 않는다
    # (격리 의도 마커가 이동 전에 저장되므로, 중단 후 원본이 남아 있어도
    # 마커가 있으면 fail-closed / 보이는 초기화로 처리한다).
    marker = read_recovery_marker(state_path)
    if not state_path.exists():
        if marker is not None:
            if kind == "drive_poll_state":
                raise StateBlockedError(
                    "복구 마커 존재 — drive state 복구 대기 중. "
                    "신규 초기화가 아니라 복원/명시적 재구축이 필요하다.")
            return {}, "post_quarantine_init"
        return {}, "fresh"
    try:
        raw = state_path.read_bytes()
    except PermissionError as e:
        raise StateReadError(f"상태 파일 읽기 권한 오류 (손상 아님): {e}") from e
    except OSError as e:
        raise StateReadError(f"상태 파일 읽기 실패: {e}") from e
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        _quarantine_and_mark(state_path, raw, kind, f"파싱 실패: {e}")
        if kind == "drive_poll_state":
            raise StateBlockedError(
                "drive state 손상 — 격리 후 fail-closed 중단") from e
        return {}, "post_quarantine_init"
    validator = validate_drive_state if kind == "drive_poll_state" else validate_monitor_state
    ok, reason = validator(data)
    if not ok:
        _quarantine_and_mark(state_path, raw, kind, reason)
        if kind == "drive_poll_state":
            raise StateBlockedError(
                f"drive state 손상 — 격리 후 fail-closed 중단 ({reason})")
        return {}, "post_quarantine_init"
    if marker is not None:
        # 유효한 state 파일 + 미해결 복구 마커 — 마커를 무시하고
        # state를 쓰면 복구 차단을 우회한다. drive는 차단, monitor는
        # 보이는 초기화로 진행한다 (워터마크는 재계산되므로 안전).
        if kind == "drive_poll_state":
            raise StateBlockedError(
                "복구 마커 존재 — drive state 복구 대기 중 "
                "(state 파일이 유효해도 마커 삭제 없이는 진행하지 않음).")
        return {}, "post_quarantine_init"
    return data, "ok"


def _quarantine_and_mark(state_path, raw, kind, error):
    """손상 상태 파일을 격리하고 복구 마커를 쓴다.

    순서: 복구 의도 마커(내구성) → 원본 이동 → 메타데이터 → 최종 마커.
    이동 직후 중단돼도 의도 마커가 남아 다음 실행이 fresh로 진행하지
    않는다. 격리 실패 시 원본을 덮지 않는다 (예외를 그대로 전파).
    """
    byte_sha = hashlib.sha256(raw).hexdigest()
    # 1) 복구 의도 마커를 이동 전에 내구성 있게 저장한다.
    write_recovery_marker(
        state_path, kind=kind, quarantined_path=None,
        original_path=state_path, byte_sha256=byte_sha, error=error,
        status="quarantine_pending")
    quarantined = quarantine_file(
        state_path, Path(state_path).parent / ".quarantine",
        Path(state_path).name)
    # 메타데이터를 격리 디렉터리 안에 원자적으로 남긴다.
    meta = {
        "original_path": str(state_path),
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
        "byte_sha256": byte_sha,
        "error": error,
        "code_sha": code_sha(),
        "restore_procedure": (
            "격리본을 검사해 검증된 백업으로 복원하거나, 운영자가 "
            "명시적으로 재구축한 뒤 복구 마커를 삭제한다. "
            "마커 상태가 quarantine_pending이면 격리가 중단됐을 수 있어 "
            "원본이 남아 있거나 이미 이동했을 수 있다 — 격리 디렉터리를 "
            "확인한 뒤 마커를 삭제한다."
        ),
    }
    atomic_json_write(Path(quarantined).parent / "metadata.json", meta)
    # 2) 최종 마커 (격리 완료 경로).
    write_recovery_marker(
        state_path, kind=kind, quarantined_path=quarantined,
        original_path=state_path, byte_sha256=byte_sha, error=error,
        status="quarantined")
    return quarantined


def atomic_json_write(path, data):
    """원자적 JSON 저장: 고유 임시 파일 + flush + fsync + replace + 디렉터리 fsync.

    실패 시 원본을 덮지 않는다 (replace 전 실패는 임시 파일만 남기고,
    replace 후 디렉터리 fsync 실패는 예외로 보고해 호출자가 재시도한다).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # 디렉터리 fsync — rename의 내구성 보장. open 실패도 삼키지 않고
    # 보고한다 (내구성을 보장할 수 없는데 성공으로 반환하지 않는다).
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def append_review_entry(marker_path, commit, reason, extra=None):
    """검수 마커(.needs-review)에 항목 추가 (기존 스키마 유지).

    마커는 list[{commit, reason, timestamp, ...}]이다. commit ""은
    "커밋이 아닌 검토 항목" (drive/monitor 상태 격리 등)을 뜻한다.
    extra 키는 추가 정보 (기존 리더를 깨뜨리지 않는다).
    마커가 손상됐으면 격리하고 새로 시작한다 (flag_needs_review와
    동일한 패턴 — 교차 모듈 import를 피하기 위해 분리 구현).
    """
    marker = Path(marker_path)
    entries = []
    if marker.exists():
        try:
            entries = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("not a list")
        except (OSError, ValueError):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            try:
                fd, qpath = tempfile.mkstemp(
                    prefix=f".needs-review.quarantine.{stamp}.", dir=str(marker.parent))
                try:
                    with os.fdopen(fd, "wb") as dst, open(marker, "rb") as src:
                        import shutil
                        shutil.copyfileobj(src, dst)
                except OSError:
                    try:
                        os.unlink(qpath)
                    except OSError:
                        pass
                    raise
                marker.unlink()
            except OSError:
                return False
            entries = []
    for e in entries:
        if isinstance(e, dict) and e.get("commit") == commit and e.get("reason") == reason:
            return True
    entry = {"commit": commit, "reason": reason,
             "timestamp": datetime.now(timezone.utc).isoformat()}
    if extra:
        entry.update(extra)
    entries.append(entry)
    atomic_json_write(marker, entries)
    return True
