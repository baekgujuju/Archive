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


def test_validate_non_dict_post_member_fails(hook_repo):
    """청크 posts 안에 dict가 아닌 멤버가 있으면 커밋 검증 실패."""
    repo, sha, manifest, chunks, _ = hook_repo
    posts = [{"id": "ok"}, "not-a-dict"]
    raw = _chunk_bytes(posts)
    (repo / "v2" / "data" / "posts-x.json").write_bytes(raw)
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"] = {
        "file": "data/posts-x.json",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "count": len(posts),
    }
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    bad = _commit(repo, "non-dict post")
    ok, reason = hook.validate_commit_artifacts(bad)
    assert not ok and "dict" in reason


def test_validate_bool_count_fails(hook_repo):
    """count가 bool(True)이면 커밋 검증 실패 (bool은 int의 서브클래스)."""
    repo, sha, manifest, chunks, _ = hook_repo
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["count"] = True
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    bad = _commit(repo, "bool count")
    ok, reason = hook.validate_commit_artifacts(bad)
    assert not ok and "count" in reason


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


def _valid_manifest():
    """구조가 유효한 매니페스트 (필수 청크 3종 포함)."""
    chunks = {}
    for name in ("x", "web", "ib"):
        raw = _chunk_bytes([])
        chunks[f"posts-{name}"] = {
            "file": f"data/posts-{name}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "count": 0,
        }
    return {"source_hash": "SRC-1", "chunks": chunks}


def test_manifest_update_missing_manifest_blocks(manifest_env):
    data = manifest_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_invalid_schema_blocks(manifest_env):
    data = manifest_env
    (data / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    (data / "translations.json").write_text("{}", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False
    # 필수 청크가 없는 {}도 구조 오류로 차단 (구버그는 성공 처리했다).
    (data / "manifest.json").write_text("{}", encoding="utf-8")
    (data / "translations.json").write_text("{}", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False
    # chunks가 비어 있어도 차단.
    (data / "manifest.json").write_text(json.dumps({"chunks": {}}), encoding="utf-8")
    (data / "translations.json").write_text("{}", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False
    # translations가 dict가 아니면 차단 (매니페스트는 유효).
    _write_manifest_translations(data, _valid_manifest(), None)
    (data / "translations.json").write_text("[1]", encoding="utf-8")
    assert hook.update_manifest_translation_hash() is False
    # count가 bool이면 차단 (bool은 int의 서브클래스).
    m = _valid_manifest()
    m["chunks"]["posts-x"]["count"] = True
    _write_manifest_translations(data, m, {})
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_missing_translations_with_ref_blocks(manifest_env):
    data = manifest_env
    m = _valid_manifest()
    m["translations"] = {"file": "data/translations.json",
                         "sha256": "0" * 64, "count": 0}
    (data / "manifest.json").write_text(json.dumps(m))
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_missing_translations_without_ref_ok(manifest_env):
    data = manifest_env
    (data / "manifest.json").write_text(json.dumps(_valid_manifest()))
    assert hook.update_manifest_translation_hash() is True


def test_manifest_update_lock_timeout_blocks(manifest_env, monkeypatch):
    data = manifest_env
    _write_manifest_translations(data, _valid_manifest(), {"a": 1})

    def raising_lock():
        raise PipelineLockTimeout("mock timeout")

    monkeypatch.setattr(hook, "pipeline_lock", raising_lock)
    assert hook.update_manifest_translation_hash() is False


def test_manifest_update_hash_failure_blocks_then_repair_succeeds(manifest_env, monkeypatch):
    data = manifest_env
    _write_manifest_translations(data, _valid_manifest(), {"a": 1})
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


def test_review_marker_two_quarantines_both_preserved(tmp_path, monkeypatch):
    """같은 초에 두 번 격리해도 앞 증거를 덮지 않는다 (고유 예약)."""
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    (hooks_dir / ".needs-review").write_text("{broken-1")
    hook.flag_needs_review("abc123", "reason-1")
    (hooks_dir / ".needs-review").write_text("{broken-2")
    hook.flag_needs_review("def456", "reason-2")
    quarantined = sorted(hooks_dir.glob(".needs-review.quarantine.*"),
                         key=lambda p: p.name)
    assert len(quarantined) == 2
    contents = sorted(p.read_text() for p in quarantined)
    assert contents == ["{broken-1", "{broken-2"]
    entries = json.loads((hooks_dir / ".needs-review").read_text())
    assert len(entries) == 1 and entries[0]["commit"] == "def456"


def test_review_marker_quarantine_failure_preserves_original(tmp_path, monkeypatch):
    """격리 실패 시 원본 손상 마커를 건드리지 않고 중단한다."""
    import tempfile

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    (hooks_dir / ".needs-review").write_text("{broken")

    def failing_mkstemp(*a, **kw):
        raise OSError("injected mkstemp failure")

    monkeypatch.setattr(tempfile, "mkstemp", failing_mkstemp)
    hook.flag_needs_review("abc123", "reason-1")
    # 원본 그대로, 새 마커 없음, 격리 파일 없음.
    assert (hooks_dir / ".needs-review").read_text() == "{broken"
    assert not list(hooks_dir.glob(".needs-review.quarantine.*"))


def test_publish_off_defers_pending_validation_then_on_blocks(hook_repo, monkeypatch):
    """PUBLISH 비활성 시 미푸시 검증 건너뜀 → 활성 시 다시 검증해 차단.

    Codex 2차 리뷰 뉘앙스: off일 때 검증을 건너뛰는 것은 지연된
    감지이지 푸시 우회가 아니다 — on으로 바꾸면 같은 미푸시 커밋이
    검증에 걸려야 한다. main()을 실제로 두 번 호출해 확인한다.
    """
    repo, sha, manifest, chunks, hooks_dir = hook_repo
    # 무효 커밋 C2 (해시 불일치) + 유효 커밋 C3.
    m = json.loads((repo / "v2" / "data" / "manifest.json").read_text())
    m["chunks"]["posts-x"]["sha256"] = "0" * 64
    (repo / "v2" / "data" / "manifest.json").write_text(json.dumps(m))
    bad = _commit(repo, "bad older")
    _write_repo_files(repo, manifest, chunks)
    good = _commit(repo, "good newer")
    data = repo / "v2" / "data"
    monkeypatch.setattr(hook, "MANIFEST", data / "manifest.json")
    monkeypatch.setattr(hook, "TRANSLATIONS", data / "translations.json")
    monkeypatch.setattr(hook, "LOCK_FILE", hooks_dir / ".post_ingest.lock")
    for attr, name in [("RETRY_TRANSLATION", ".retry_translation"),
                       ("RETRY_COMMIT", ".retry_commit"),
                       ("RETRY_MEDIA", ".retry_media"),
                       ("RETRY_ENRICH", ".retry-enrich")]:
        monkeypatch.setattr(hook, attr, hooks_dir / name)

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    # 파이프라인 서브프로세스만 가짜 성공으로 처리하고, git 명령은
    # 실제 실행해야 한다 (hook.git()이 hook.run()을 쓰므로, run을
    # 전부 모킹하면 current_branch가 빈 출력을 보고 detached로 오판한다).
    real_run = hook.run

    def fake_run(cmd, **kw):
        if cmd and cmd[0] == "git":
            return real_run(cmd, **kw)
        return _R()

    monkeypatch.setattr(hook, "run", fake_run)
    calls = []
    real_unpushed = hook.unpushed_commit_list

    def guarded_unpushed():
        calls.append(1)
        return real_unpushed()

    monkeypatch.setattr(hook, "unpushed_commit_list", guarded_unpushed)

    # PUBLISH off: 미푸시 검증을 호출하지 않고 정상 종료한다.
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", False)
    assert hook.main() == 0
    assert calls == []
    assert not (hooks_dir / ".needs-review").exists()
    # PUBLISH on: 같은 미푸시 커밋이 검증에 걸려 차단된다.
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", True)
    assert hook.main() == 1
    assert len(calls) == 1
    entries = json.loads((hooks_dir / ".needs-review").read_text())
    assert any(e["commit"] == bad for e in entries)


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
    # 구조가 유효한 매니페스트 (복구 후 update_manifest_translation_hash가 동작).
    (data / "manifest.json").write_text(
        json.dumps(_valid_manifest()), encoding="utf-8")
    return hooks_dir, data


def _make_backup(hooks_dir, name, entries, source_hash="SRC-1",
                 trans_hash=None, with_txn=True):
    d = hooks_dir / name
    d.mkdir()
    raw = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    (d / "translations.json").write_bytes(raw)
    if with_txn:
        # 트랜잭션은 실제 백업 바이트 해시를 묶는다 (임의 문자열 금지).
        txn = {
            "transaction_id": uuid.uuid4().hex,
            "created_at": "2026-10-17T00:00:00+00:00",
            "source_hash": source_hash,
            "translations_sha256": trans_hash or hashlib.sha256(raw).hexdigest(),
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
        "source_hash": "SRC-1",
        "translations_sha256": hashlib.sha256(b"{broken").hexdigest()}))
    hook.recover_abandoned_backups()
    assert d.exists()


def test_backup_hash_mismatch_retained(recovery_env):
    """트랜잭션 해시와 백업 바이트가 다르면 생성 후 변경된 것 — 보존."""
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a", {"k": _valid_entry()})
    # 백업을 생성 후 변경 (트랜잭션 해시는 원래 바이트를 가리킴).
    (d / "translations.json").write_text(
        json.dumps({"k": _valid_entry("tampered")}), encoding="utf-8")
    hook.recover_abandoned_backups()
    assert d.exists()
    assert json.loads((data / "translations.json").read_text()) == {}


def test_modified_backup_not_deleted_as_duplicate(recovery_env):
    """현재 파일이 메타데이터와 일치해도 백업 바이트가 다르면 중복이 아니다.

    구버그는 현재 해시 == 메타데이터 해시면 백업을 삭제해, 변경된
    백업의 고유 엔트리를 지웠다. 신버그는 백업 해시를 먼저 대조해
    변경된 백업을 신뢰하지 않고 보존한다 (병합하지 않음 — 위조 가능).
    고유 엔트리는 보존된 백업 안에 남는다.
    """
    hooks_dir, data = recovery_env
    entries = {"k": _valid_entry()}
    raw = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    # 현재 파일을 백업과 동일하게 만든다 (중복 상태).
    (data / "translations.json").write_bytes(raw)
    d = _make_backup(hooks_dir, "v2_trans_a", entries)
    # 백업을 생성 후 변경 — 이제 현재와 바이트가 다르고, 트랜잭션 해시와도 다르다.
    (d / "translations.json").write_text(
        json.dumps({"k": _valid_entry(), "extra": _valid_entry("unique")}),
        encoding="utf-8")
    hook.recover_abandoned_backups()
    assert d.exists()  # 중복이 아니므로 삭제되지 않음
    cur = json.loads((data / "translations.json").read_text())
    assert cur == entries  # 신뢰할 수 없는 백업은 병합하지 않음
    retained = json.loads((d / "translations.json").read_text())
    assert "extra" in retained  # 고유 엔트리는 보존된 백업 안에 남음


def test_true_duplicate_backup_deleted(recovery_env):
    """백업 바이트가 현재와 실제로 동일하면 중복 — 삭제한다."""
    hooks_dir, data = recovery_env
    entries = {"k": _valid_entry()}
    raw = json.dumps(entries, ensure_ascii=False).encode("utf-8")
    (data / "translations.json").write_bytes(raw)
    d = _make_backup(hooks_dir, "v2_trans_a", entries)
    hook.recover_abandoned_backups()
    assert not d.exists()
    assert json.loads((data / "translations.json").read_text()) == entries


def test_invalid_entries_skipped_and_quarantined(recovery_env):
    hooks_dir, data = recovery_env
    (data / "translations.json").write_text("{}", encoding="utf-8")
    d = _make_backup(hooks_dir, "v2_trans_a",
                     {"good": _valid_entry(), "bad": "not-a-dict",
                      "bad2": {"translated": 123, "paragraphs": []}})
    hook.recover_abandoned_backups()
    cur = json.loads((data / "translations.json").read_text())
    assert "good" in cur
    assert "bad" not in cur and "bad2" not in cur
    # 유효 엔트리는 병합됐지만 무효 엔트리 증거는 격리에 보존된다 — 삭제 아님.
    assert not d.exists()
    quarantined = list((hooks_dir / ".quarantine").glob("v2_trans_a.*"))
    assert len(quarantined) == 1
    qbak = json.loads((quarantined[0] / "translations.json").read_text())
    assert "bad" in qbak and "bad2" in qbak


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
