"""P2 배치 1 후크 테스트 (Codex 테스트 게이트).

실제 임시 Git 저장소를 사용한다 (mock git이 아닌 진짜 커밋/해시).
- 유효한 매니페스트 경로 통과 (이진·CRLF 포함 블롭).
- 매니페스트 없음/파싱 불가/청크 없음/경로·해시 형식 오류/탈출/해시 불일치 실패.
- 무효한 오래된 미푸시 커밋 + 유효한 HEAD → 푸시 차단.
- 무효한 신규 HEAD → 푸시 차단.
- 푸시는 검증한 SHA 정확히 사용.
- 기준선 없음 / detached HEAD / ref 조회·rev-list·브랜치 조회·status 실패는
  성공형 무작업이 될 수 없음.
- 매니페스트 락 타임아웃/읽기·해시·쓰기 실패/스키마 오류 → 커밋 차단, 복구 후 성공.
- 검수 기록 중복 제거; 일시적 Git 실패는 검수 기록을 만들지 않음.
- 복구: 누락 키 병합·현재 값 보존, 손상 현재 파일 보존, 손상 백업·
  무효 엔트리·충돌 백업·비어 있지 않은 디렉터리 보존, 락 타임아웃·
  쓰기 실패 시 백업 보존, 소스 변경 시 보존, legacy 보존, 복구 멱등.
"""
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_ingest_hook as hook
from pipeline_lock import PipelineLockTimeout


# ---------- Git 저장소 헬퍼 ----------

def _git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} 실패: {r.stderr}")
    return r


def _chunk_bytes(posts):
    return json.dumps({"posts": posts}, ensure_ascii=False).encode("utf-8")


def _manifest_for(repo, chunks, extra=None):
    """chunks: {name: posts}. 매니페스트 dict를 만든다 (파일은 쓰지 않음)."""
    m = {"source_hash": "s1", "chunks": {}}
    for name, posts in chunks.items():
        raw = _chunk_bytes(posts)
        m["chunks"][f"posts-{name}"] = {
            "file": f"data/posts-{name}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "count": len(posts),
        }
    if extra:
        m.update(extra)
    return m


def _write_repo_files(repo, manifest, chunks, translations=None):
    data = repo / "v2" / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name, posts in chunks.items():
        (data / f"posts-{name}.json").write_bytes(_chunk_bytes(posts))
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if translations is not None:
        (data / "translations.json").write_text(
            json.dumps(translations, ensure_ascii=False), encoding="utf-8")


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg, "--allow-empty")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _make_repo(tmp_path, chunks=None, translations=None, with_origin=True):
    """유효한 매니페스트 + 청크가 있는 저장소. origin/main 기준선 설정."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    if chunks is None:
        chunks = {"x": [{"id": "1"}], "web": [], "ib": []}
    manifest = _manifest_for(repo, chunks)
    if translations is not None:
        manifest["translations"] = {
            "file": "data/translations.json",
            "sha256": hashlib.sha256(
                json.dumps(translations, ensure_ascii=False).encode()).hexdigest(),
            "count": len(translations),
        }
    _write_repo_files(repo, manifest, chunks, translations)
    sha = _commit(repo, "init")
    if with_origin:
        _git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo, sha, manifest, chunks


@pytest.fixture
def hook_repo(tmp_path, monkeypatch):
    repo, sha, manifest, chunks = _make_repo(tmp_path)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", repo)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "")
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", True)
    return repo, sha, manifest, chunks, hooks_dir


# ---------- validate_commit_artifacts ----------

def test_validate_valid_commit(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    ok, reason = hook.validate_commit_artifacts(sha)
    assert ok, reason


def test_validate_binary_and_crlf_blobs(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    # 이진 + CRLF 포함 청크를 추가하고 매니페스트를 갱신한다.
    posts = [{"id": "bin", "blob": "line1\r\nline2\r\n\x00\xff"}]
    raw = _chunk_bytes(posts)
    (repo / "v2" / "data" / "posts-x.json").write_bytes(raw)
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"] = {
        "file": "data/posts-x.json",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "count": 1,
    }
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    sha2 = _commit(repo, "binary+crlf")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert ok, reason


def test_validate_missing_manifest(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    (repo / "v2" / "data" / "manifest.json").unlink()
    sha2 = _commit(repo, "no manifest")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert not ok and "manifest" in reason


def test_validate_malformed_manifest(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    (repo / "v2" / "data" / "manifest.json").write_text("[1, 2]")
    sha2 = _commit(repo, "list manifest")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert not ok


def test_validate_missing_chunk(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    (repo / "v2" / "data" / "posts-ib.json").unlink()
    sha2 = _commit(repo, "missing chunk")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert not ok and "posts-ib" in reason


def test_validate_traversal_path(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["file"] = "../etc/passwd"
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    sha2 = _commit(repo, "traversal")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert not ok and "경로" in reason


def test_validate_hash_mismatch(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["sha256"] = "0" * 64
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    sha2 = _commit(repo, "hash mismatch")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert not ok and "해시 불일치" in reason


def test_validate_count_mismatch(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["count"] = 999
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    sha2 = _commit(repo, "count mismatch")
    ok, reason = hook.validate_commit_artifacts(sha2)
    assert not ok and "count" in reason


def test_validate_git_infra_failure_raises(hook_repo, monkeypatch):
    repo, sha, manifest, chunks, _ = hook_repo

    def failing_git_bytes(args, **kw):
        class R:
            returncode = 128
            stdout = b""
            stderr = b"fatal: not a git repository"
        return R()

    monkeypatch.setattr(hook, "git_bytes", failing_git_bytes)
    with pytest.raises(RuntimeError, match="git show"):
        hook.validate_commit_artifacts(sha)


# ---------- unpushed_commit_list / do_push ----------

def test_unpushed_missing_baseline_raises(tmp_path, monkeypatch):
    repo, sha, manifest, chunks = _make_repo(tmp_path, with_origin=False)
    monkeypatch.setattr(hook, "V2_ROOT", repo)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "")
    with pytest.raises(RuntimeError, match="기준선"):
        hook.unpushed_commit_list()


def test_unpushed_detached_head_raises(hook_repo):
    repo, sha, manifest, chunks, _ = hook_repo
    _git(repo, "checkout", "--detach", "HEAD")
    with pytest.raises(RuntimeError, match="detached"):
        hook.unpushed_commit_list()
    with pytest.raises(RuntimeError, match="detached"):
        hook.current_branch()


def test_unpushed_list_and_push_exact_sha(tmp_path, monkeypatch):
    # 실제 bare origin에 푸시 — do_push는 검증한 SHA 정확히 푸시한다.
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                   capture_output=True)
    repo, sha, manifest, chunks = _make_repo(tmp_path, with_origin=False)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", repo)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "")
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", True)
    # 새 유효 커밋.
    (repo / "v2" / "data" / "posts-web.json").write_bytes(
        _chunk_bytes([{"id": "w1"}]))
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    raw = _chunk_bytes([{"id": "w1"}])
    m["chunks"]["posts-web"] = {"file": "data/posts-web.json",
                                "sha256": hashlib.sha256(raw).hexdigest(),
                                "count": 1}
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    sha2 = _commit(repo, "second")
    assert hook.unpushed_commit_list() == [sha2]
    assert hook.do_push() is True
    # bare의 main이 검증한 SHA 정확히.
    bare_main = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert bare_main == sha2


def test_do_push_blocks_invalid_head(tmp_path, monkeypatch):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                   capture_output=True)
    repo, sha, manifest, chunks = _make_repo(tmp_path, with_origin=False)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "origin", "main")
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", repo)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "")
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", True)
    # 무효 HEAD (해시 불일치).
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["sha256"] = "0" * 64
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    bad = _commit(repo, "bad head")
    assert hook.do_push() is False
    # bare는 그대로.
    bare_main = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert bare_main == sha
    # 검수 기록에 남는다.
    entries = json.loads((hooks_dir / ".needs-review").read_text())
    assert any(e["commit"] == bad for e in entries)


def test_do_push_blocks_missing_baseline(tmp_path, monkeypatch):
    repo, sha, manifest, chunks = _make_repo(tmp_path, with_origin=False)
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "V2_ROOT", repo)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "PUBLISH_BRANCH", "")
    assert hook.do_push() is False


def test_step0_invalid_older_commit_blocks_and_flags(hook_repo, monkeypatch):
    repo, sha, manifest, chunks, hooks_dir = hook_repo
    # 무효 커밋 C2 (해시 불일치) + 유효 커밋 C3.
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["sha256"] = "0" * 64
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    bad = _commit(repo, "bad older")
    # C3: 매니페스트 복원 (유효).
    _write_repo_files(repo, manifest, chunks)
    good = _commit(repo, "good newer")
    pending = hook.unpushed_commit_list()
    assert pending == [bad, good]
    # 0단계 로직: 모든 미푸시 커밋을 검증한다 — C2에서 차단.
    blocked = None
    for commit in pending:
        ok, reason = hook.validate_commit_artifacts(commit)
        if not ok:
            hook.flag_needs_review(commit, reason)
            blocked = commit
            break
    assert blocked == bad
    entries = json.loads((hooks_dir / ".needs-review").read_text())
    assert any(e["commit"] == bad for e in entries)


def test_transient_git_failure_creates_no_review_record(hook_repo, monkeypatch):
    repo, sha, manifest, chunks, hooks_dir = hook_repo
    # rev-list 실패를 흉내: git을 모킹해 rev-list만 실패시킨다.
    real_git = hook.git

    def flaky_git(args, **kw):
        if args and args[0] == "rev-list":
            class R:
                returncode = 128
                stdout = ""
                stderr = "fatal: bad revision"
            return R()
        return real_git(args, **kw)

    monkeypatch.setattr(hook, "git", flaky_git)
    with pytest.raises(RuntimeError, match="rev-list"):
        hook.unpushed_commit_list()
    assert not (hooks_dir / ".needs-review").exists()


def test_failed_status_cannot_be_no_work(hook_repo, monkeypatch):
    repo, sha, manifest, chunks, _ = hook_repo

    def failing_git(args, **kw):
        class R:
            returncode = 128
            stdout = ""
            stderr = "fatal: boom"
        return R()

    monkeypatch.setattr(hook, "git", failing_git)
    with pytest.raises(RuntimeError):
        hook.changed_files()
    with pytest.raises(RuntimeError):
        hook.current_branch()


# ---------- manifest 번역 해시 업데이트 ----------

@pytest.fixture
def manifest_env(tmp_path, monkeypatch):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "MANIFEST", data / "manifest.json")
    monkeypatch.setattr(hook, "TRANSLATIONS", data / "translations.json")
    return data


def _write_manifest_translations(data, manifest, translations):
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if translations is not None:
        (data / "translations.json").write_text(
            json.dumps(translations), encoding="utf-8")


def test_manifest_update_missing_manifest_blocks(manifest_env):
    data = manifest_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_invalid_schema_blocks(manifest_env):
    data = manifest_env
    (data / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    (data / "translations.json").write_text("{}", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False
    # translations가 dict가 아니면 차단.
    (data / "manifest.json").write_text("{}", encoding="utf-8")
    (data / "translations.json").write_text("[1]", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_missing_translations_with_ref_blocks(manifest_env):
    data = manifest_env
    (data / "manifest.json").write_text(
        json.dumps({"translations": {"file": "x", "sha256": "0" * 64}}))
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_missing_translations_without_ref_ok(manifest_env):
    data = manifest_env
    (data / "manifest.json").write_text(json.dumps({"chunks": {}}))
    assert hook.update_manifest_translation_hash() is True


def test_manifest_update_lock_timeout_blocks(manifest_env, monkeypatch):
    data = manifest_env
    _write_manifest_translations(data, {}, {"a": 1})

    def raising_lock():
        raise PipelineLockTimeout("mock timeout")

    monkeypatch.setattr(hook, "pipeline_lock", raising_lock)
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_hash_failure_blocks_then_repair_succeeds(manifest_env, monkeypatch):
    data = manifest_env
    _write_manifest_translations(data, {}, {"a": 1})
    real_hash = hook.sha256_file

    def failing_hash(path):
        raise OSError("injected hash failure")

    monkeypatch.setattr(hook, "sha256_file", failing_hash)
    assert hook.update_manifest_translation_hash() is False
    monkeypatch.setattr(hook, "sha256_file", real_hash)
    assert hook.update_manifest_translation_hash() is True
    m = json.loads((data / "manifest.json").read_text())
    assert m["translations"]["count"] == 1


# ---------- 검수 기록 ----------

def test_review_records_deduplicate(tmp_path, monkeypatch):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    hook.flag_needs_review("abc123", "reason-1")
    hook.flag_needs_review("abc123", "reason-1")  # 중복 — 추가 안 됨
    hook.flag_needs_review("abc123", "reason-2")  # 다른 사유 — 추가됨
    entries = json.loads((hooks_dir / ".needs-review").read_text())
    assert len(entries) == 2


def test_review_marker_malformed_is_quarantined(tmp_path, monkeypatch):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    (hooks_dir / ".needs-review").write_text("{broken")
    hook.flag_needs_review("abc123", "reason-1")
    quarantined = list(hooks_dir.glob(".needs-review.quarantine.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == "{broken"
    entries = json.loads((hooks_dir / ".needs-review").read_text())
    assert len(entries) == 1 and entries[0]["commit"] == "abc123"


# ---------- 복구 ----------

def _valid_entry(translated="hello"):
    return {"paragraphs": ["p1"], "translated": translated,
            "content_hash": "h", "status": "done", "translated_at": "2026-10-17"}


@pytest.fixture
def recovery_env(tmp_path, monkeypatch):
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "MANIFEST", data / "manifest.json")
    monkeypatch.setattr(hook, "TRANSLATIONS", data / "translations.json")
    (data / "manifest.json").write_text(
        json.dumps({"source_hash": "SRC-1"}), encoding="utf-8")
    return hooks_dir, data


def _make_backup(hooks_dir, name, entries, source_hash="SRC-1",
                 trans_hash=None, with_txn=True):
    d = hooks_dir / name
    d.mkdir()
    (d / "translations.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    if with_txn:
        txn = {
            "transaction_id": uuid.uuid4().hex,
            "created_at": "2026-10-17T00:00:00+00:00",
            "source_hash": source_hash,
            "translations_sha256": trans_hash or "deadbeef",
        }
        (d / "transaction.json").write_text(json.dumps(txn), encoding="utf-8")
    return d


def test_merge_preserves_current_values(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text(
        json.dumps({"k1": _valid_entry("current")}), encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a",
                     {"k1": _valid_entry("backup"), "k2": _valid_entry("new")})
    hook.recover_abandoned_backups()
    cur = json.loads((data / "translations.json").read_text())
    assert cur["k1"]["translated"] == "current"  # 현재 값 보존
    assert cur["k2"]["translated"] == "new"  # 누락 키 복원
    assert not d.exists()  # 성공 시 삭제


def test_corrupt_current_cache_is_preserved(recovery_env):
    hooks_dir, data = recovery_env
    corrupt = "{broken json"
    (data / "translations.json").write_text(corrupt, encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k1": _valid_entry()})
    hook.recover_abandoned_backups()
    # 현재 손상 파일은 덮지 않고, 백업도 보존한다.
    assert (data / "translations.json").read_text() == corrupt
    assert d.exists()


def test_corrupt_backup_retained(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = hooks_dir / "v2_trans_bad"
    d.mkdir()
    (d / "translations.json").write_text("{broken", encoding="utf-8")
    (d / "transaction.json").write_text(json.dumps({
        "transaction_id": "x", "created_at": "2026-10-17T00:00:00+00:00",
        "source_hash": "SRC-1", "translations_sha256": "00"}))
    hook.recover_abandoned_backups()
    assert d.exists()


def test_invalid_entries_skipped(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a",
                     {"good": _valid_entry(), "bad": "not-a-dict",
                      "bad2": {"translated": 123, "paragraphs": []}})
    hook.recover_abandoned_backups()
    cur = json.loads((data / "translations.json").read_text())
    assert "good" in cur
    assert "bad" not in cur and "bad2" not in cur
    assert not d.exists()  # 유효 엔트리 병합은 성공 → 삭제


def test_conflicting_backups_all_preserved(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d1 = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry("v1")})
    d2 = _make_backup(hooks_dir, "v2_trans_b", {"k": _valid_entry("v2")})
    hook.recover_abandoned_backups()
    assert d1.exists() and d2.exists()
    assert json.loads((data / "translations.json").read_text()) == {}


def test_nonempty_dir_without_translations_retained(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = hooks_dir / "v2_trans_partial"
    d.mkdir()
    (d / "transaction.json").write_text("{}")
    hook.recover_abandoned_backups()
    assert d.exists()


def test_empty_backup_dir_deleted(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = hooks_dir / "v2_trans_empty"
    d.mkdir()
    hook.recover_abandoned_backups()
    assert not d.exists()


def test_source_changed_backup_preserved(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry()},
                     source_hash="SRC-OLD")  # 현재 SRC-1과 다름
    hook.recover_abandoned_backups()
    assert d.exists()


def test_legacy_backup_without_txn_preserved(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry()},
                     with_txn=False)
    hook.recover_abandoned_backups()
    assert d.exists()


def test_lock_timeout_retains_backup(recovery_env, monkeypatch):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry()})

    def raising_lock():
        raise PipelineLockTimeout("mock timeout")

    monkeypatch.setattr(hook, "pipeline_lock", raising_lock)
    hook.recover_abandoned_backups()
    assert d.exists()


def test_write_failure_retains_backup(recovery_env, monkeypatch):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry()})

    orig_replace = Path.replace

    def failing_replace(self, target):
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", failing_replace)
    try:
        hook.recover_abandoned_backups()
    finally:
        monkeypatch.setattr(Path, "replace", orig_replace)
    assert d.exists()


def test_recovery_idempotent_and_manifest_consistent(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry()})
    hook.recover_abandoned_backups()
    assert not d.exists()
    # 두 번째 실행은 no-op.
    hook.recover_abandoned_backups()
    assert json.loads((data / "translations.json").read_text())["k"]["translated"] == "hello"
    # 복구 후 매니페스트 동기화 → 커밋 전 일관성.
    assert hook.update_manifest_translation_hash() is True
    m = json.loads((data / "manifest.json").read_text())
    assert m["translations"]["sha256"] == hashlib.sha256(
        (data / "translations.json").read_bytes()).hexdigest()
