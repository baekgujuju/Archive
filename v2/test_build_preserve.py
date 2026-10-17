"""Regression tests for the 2026-10-17 build-wipes-enrich incident.

Root cause: build_data.py replaced the whole data/ directory
(data/ -> data.backup/ -> deleted) carrying over only translations.json
and shared-state.json. The ai-tools state files were dropped, so every
build reset enrich to zero and enrich rebuilt from scratch (152 -> 40).
A .retry-enrich marker forced a rebuild on every poll, making it a loop.

Fix: build_data.py preserves the enrich state files across the swap and
verifies them afterwards (fail-closed, backup kept); the hook runs
enrich-only (no build) when only the enrich retry marker is pending.
"""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import build_data
import enrich_tools as et
import post_ingest_hook as hook


def test_copy_preserved_files(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.json").write_text('{"x": 1}')
    (src / "b.json").write_text('{"y": 2}')
    copied = build_data.copy_preserved_files(src, dst, ["a.json", "b.json", "missing.json"])
    assert copied == ["a.json", "b.json"]
    assert json.loads((dst / "a.json").read_text()) == {"x": 1}
    assert not (dst / "missing.json").exists()


def test_verify_preserved_restores_from_backup(tmp_path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    data.mkdir()
    backup.mkdir()
    (backup / "ai-tools.json").write_text('{"k": 1}')
    # Missing from new data/ -> restored from backup, no raise.
    build_data.verify_preserved_files(data, backup, ["ai-tools.json"])
    assert json.loads((data / "ai-tools.json").read_text()) == {"k": 1}


def test_verify_preserved_ignores_never_existed(tmp_path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    data.mkdir()
    backup.mkdir()
    # First run: file in neither place -> not an error.
    build_data.verify_preserved_files(data, backup, ["ai-tools.json"])


def test_verify_preserved_ignores_directory_in_backup(tmp_path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    data.mkdir()
    backup.mkdir()
    # A directory in backup is not a preserved file -> skipped, no raise.
    (backup / "ai-tools.json").mkdir()
    build_data.verify_preserved_files(data, backup, ["ai-tools.json"])
    assert not (data / "ai-tools.json").exists()


def test_verify_preserved_rejects_directory_impersonating_file(tmp_path):
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    data.mkdir()
    backup.mkdir()
    (backup / "ai-tools.json").write_text('{"k": 1}')
    # Destination is a directory with the state file's name: not a file.
    (data / "ai-tools.json").mkdir()
    with pytest.raises(RuntimeError, match="ai-tools"):
        build_data.verify_preserved_files(data, backup, ["ai-tools.json"])


def test_preserved_list_covers_every_enrich_state_file():
    """Guard against drift: every state file enrich writes must be preserved."""
    written = {Path(et.TOOLS_JSON).name, Path(et.CACHE_JSON).name,
               Path(et.PROGRESS_JSON).name, "ai-tools-vision-cache.json"}
    assert written <= set(build_data.PRESERVED_FILES)
    assert set(build_data.ENRICH_STATE_FILES) == written


def _seed_build_sandbox(tmp_path):
    """Seed a fake V2_ROOT + sources; returns (v2root, data, preserved)."""
    v2root = tmp_path / "v2"
    data = v2root / "data"
    data.mkdir(parents=True)
    preserved = {
        "ai-tools.json": '{"t": 1}',
        "ai-tools-cache.json": '{"c": 1}',
        "ai-tools-vision-cache.json": '{"v": 1}',
        "ai-tools-progress.json": '{"p": 1}',
        "translations.json": '{"tr": 1}',
        "shared-state.json": json.dumps({
            "schema_version": 1, "revision": "r1",
            "updatedAt": "2026-10-17T00:00:00+00:00"}),
        "media-map.json": '{"m": 1}',
    }
    for name, content in preserved.items():
        (data / name).write_text(content)
    (data / "manifest.json").write_text(json.dumps({
        "source_hash": "old",
        "ai_tools": {"file": "data/ai-tools.json", "count": 1, "sha256": "abc"},
    }))
    (v2root / "release.json").write_text(json.dumps({"app_version": "test-1"}))
    posts = tmp_path / "posts.json"
    posts.write_text(json.dumps([
        {"tweetId": "1", "sourceUrl": "https://x.com/u/status/1", "fullText": "hi"},
    ]))
    return v2root, data, preserved, posts


def _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path):
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    monkeypatch.setattr(build_data, "DATA_DIR", data)
    monkeypatch.setattr(build_data, "MEDIA_THUMB", v2root / "media" / "thumb")
    monkeypatch.setattr(build_data, "MEDIA_FULL", v2root / "media" / "full")
    monkeypatch.setattr(build_data, "POSTS_JSON", posts)
    monkeypatch.setattr(build_data, "WEB_INDEX_JSON", tmp_path / "no-index.json")
    monkeypatch.setattr(build_data, "IB_LIBRARY_JSON", tmp_path / "no-ib.json")


def test_real_build_preserves_enrich_state(tmp_path, monkeypatch):
    """Sandboxed real build_data(): preserved files survive the swap byte-identical."""
    v2root, data, preserved, posts = _seed_build_sandbox(tmp_path)
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)
    build_data.build_data()
    for name, content in preserved.items():
        assert (data / name).read_text() == content, name
    manifest = json.loads((data / "manifest.json").read_text())
    assert manifest["ai_tools"]["count"] == 1  # carried over
    assert json.loads((data / "posts-x.json").read_text())["posts"][0]["tweetId"] == "1"
    assert not (v2root / "data.backup").exists()  # backup cleaned on success


def test_real_build_rolls_back_on_verify_failure_then_retries(tmp_path, monkeypatch):
    """Verify failure -> previous generation live again -> retry succeeds."""
    v2root, data, preserved, posts = _seed_build_sandbox(tmp_path)
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)
    monkeypatch.setattr(
        build_data, "verify_preserved_files",
        lambda *a: (_ for _ in ()).throw(RuntimeError("injected verify failure")))
    with pytest.raises(SystemExit) as exc:
        build_data.build_data()
    assert exc.value.code == 2
    # Rollback: old generation live, preserved files intact, no stale backup.
    for name, content in preserved.items():
        assert (data / name).read_text() == content, name
    assert not (v2root / "data.backup").exists()
    # Retry with real verification succeeds.
    monkeypatch.undo()
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)
    build_data.build_data()
    for name, content in preserved.items():
        assert (data / name).read_text() == content, name
    # Forced rebuild (translation/commit/media marker) wins.
    assert hook.decide_pipeline(True, 0, False, True) == (True, False)
    # Source changed -> build.
    assert hook.decide_pipeline(False, 1, False, True) == (True, False)
    # Dirty tree -> build.
    assert hook.decide_pipeline(False, 0, True, True) == (True, False)
    # Only the enrich marker, no source change -> enrich-only, no build.
    assert hook.decide_pipeline(False, 0, False, True) == (False, True)
    # Nothing pending -> idle.
    assert hook.decide_pipeline(False, 0, False, False) == (False, False)


def test_real_build_post_cleanup_failure_reports_new_generation(tmp_path, monkeypatch, capsys):
    """rmdir failure after swap+backup-cleanup must report the NEW generation live."""
    v2root, data, preserved, posts = _seed_build_sandbox(tmp_path)
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)

    def failing_rmdir(self):
        raise OSError("injected rmdir failure")

    monkeypatch.setattr(Path, "rmdir", failing_rmdir)
    with pytest.raises(SystemExit) as exc:
        build_data.build_data()
    assert exc.value.code == 2
    # New generation is live, backup already cleaned, preserved files intact.
    assert json.loads((data / "posts-x.json").read_text())["posts"][0]["tweetId"] == "1"
    assert not (v2root / "data.backup").exists()
    for name, content in preserved.items():
        assert (data / name).read_text() == content, name
    err = capsys.readouterr().err
    assert "새 세대가 live입니다" in err
    assert "이전 세대를 유지합니다" not in err
def test_hook_force_marker_builds_without_crash(tmp_path, monkeypatch):
    """Regression for UnboundLocalError: a forcing marker must not crash main().

    Previously enrich_only was assigned only inside `if not needs_build`,
    so a translation/commit/media retry marker crashed the hook.
    """
    v2root = tmp_path / "v2"
    hooks_dir = v2root / "hooks"
    hooks_dir.mkdir(parents=True)
    for name in ("ib_adapter.py", "build_data.py", "media-mirror.py",
                 "enrich_tools.py", "translate_posts.py"):
        (v2root / name).write_text("# stub")
    data_dir = v2root / "data"
    data_dir.mkdir()
    (data_dir / "translations.json").write_text('{"a": 1}')

    monkeypatch.setattr(hook, "V2_ROOT", v2root)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "LOCK_FILE", hooks_dir / ".post_ingest.lock")
    monkeypatch.setattr(hook, "RETRY_TRANSLATION", hooks_dir / ".retry_translation")
    monkeypatch.setattr(hook, "RETRY_COMMIT", hooks_dir / ".retry_commit")
    monkeypatch.setattr(hook, "RETRY_MEDIA", hooks_dir / ".retry_media")
    monkeypatch.setattr(hook, "RETRY_ENRICH", hooks_dir / ".retry-enrich")
    monkeypatch.setattr(hook, "TRANSLATIONS", data_dir / "translations.json")
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", False)
    monkeypatch.setattr(hook, "changed_files", lambda: [])
    monkeypatch.setattr(hook, "unpushed_commit_list", lambda: [])
    monkeypatch.setattr(hook, "do_push", lambda: True)
    monkeypatch.setattr(hook, "update_manifest_translation_hash", lambda: True)
    monkeypatch.setattr(hook, "log", lambda msg: None)

    calls = []

    class FakeResult:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""

    def fake_run(cmd, **kw):
        calls.append(" ".join(str(c) for c in cmd))
        if "enrich_tools.py" in calls[-1]:
            return FakeResult(2)
        return FakeResult(0)

    monkeypatch.setattr(hook, "run", fake_run)
    hook.RETRY_TRANSLATION.write_text("2026-10-17T00:00:00+00:00")

    rc = hook.main()  # must not raise UnboundLocalError

    joined = "\n".join(calls)
    assert "--check" not in joined  # forcing marker skips the check
    assert "build_data.py" in joined  # real build runs
    assert "ib_adapter.py" in joined
    assert "media-mirror.py" in joined
    assert "enrich_tools.py" in joined
    assert rc == 1  # enrich rc=2 -> retry marker remains
def test_hook_enrich_only_skips_build_runs_enrich(tmp_path, monkeypatch):
    """End-to-end: only .retry-enrich pending -> no build/ib/mirror, enrich runs."""
    v2root = tmp_path / "v2"
    hooks_dir = v2root / "hooks"
    hooks_dir.mkdir(parents=True)
    (v2root / "enrich_tools.py").write_text("# stub")
    (v2root / "translate_posts.py").write_text("# stub")
    data_dir = v2root / "data"
    data_dir.mkdir()
    (data_dir / "ai-tools.json").write_text('{"old": 1}')

    monkeypatch.setattr(hook, "V2_ROOT", v2root)
    monkeypatch.setattr(hook, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(hook, "LOCK_FILE", hooks_dir / ".post_ingest.lock")
    monkeypatch.setattr(hook, "RETRY_TRANSLATION", hooks_dir / ".retry_translation")
    monkeypatch.setattr(hook, "RETRY_COMMIT", hooks_dir / ".retry_commit")
    monkeypatch.setattr(hook, "RETRY_MEDIA", hooks_dir / ".retry_media")
    monkeypatch.setattr(hook, "RETRY_ENRICH", hooks_dir / ".retry-enrich")
    monkeypatch.setattr(hook, "TRANSLATIONS", data_dir / "translations.json")
    monkeypatch.setattr(hook, "PUBLISH_ENABLED", False)
    monkeypatch.setattr(hook, "changed_files", lambda: [])
    monkeypatch.setattr(hook, "unpushed_commit_list", lambda: [])
    monkeypatch.setattr(hook, "do_push", lambda: True)
    monkeypatch.setattr(hook, "update_manifest_translation_hash", lambda: True)
    logs = []
    monkeypatch.setattr(hook, "log", lambda msg: logs.append(msg))

    calls = []

    class FakeResult:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""

    def fake_run(cmd, **kw):
        calls.append(" ".join(str(c) for c in cmd))
        if "--check" in cmd:
            return FakeResult(0)
        if "enrich_tools.py" in " ".join(str(c) for c in cmd):
            return FakeResult(2)
        return FakeResult(0)

    monkeypatch.setattr(hook, "run", fake_run)
    hook.RETRY_ENRICH.write_text("2026-10-17T00:00:00+00:00")

    rc = hook.main()

    joined = "\n".join(calls)
    assert "build_data.py" in joined  # only the --check, never the real build
    assert "build_data.py --check" in joined
    assert joined.count("build_data.py") == 1
    assert "ib_adapter.py" not in joined
    assert "media-mirror.py" not in joined
    assert "enrich_tools.py" in joined
    assert "translate_posts.py" in joined
    assert any("enrich만 실행" in m for m in logs)
    assert rc == 1  # enrich rc=2 -> retry marker remains -> failure return


# ---------- P2 배치 1: 보존 세대 (rollback generation) ----------

def _generation_dirs(v2root):
    return sorted(p for p in v2root.iterdir()
                  if p.is_dir() and p.name.startswith("data.backup."))


def test_three_builds_retain_two_generations(tmp_path, monkeypatch):
    """3번의 성공 빌드 후 이전 세대 2개만 남는다 (가장 오래된 세대 정리)."""
    v2root, data, preserved, posts = _seed_build_sandbox(tmp_path)
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)
    for _ in range(3):
        build_data.build_data()
    gens = _generation_dirs(v2root)
    assert len(gens) == 2
    # 가장 오래된 세대(시드 매니페스트 source_hash "old")는 정리됨.
    for g in gens:
        m = json.loads((g / "manifest.json").read_text())
        assert m["source_hash"] != "old"
    # live는 최신 빌드 결과.
    assert json.loads((data / "posts-x.json").read_text())["posts"][0]["tweetId"] == "1"


def test_prune_collision_ordering_is_numeric(tmp_path, monkeypatch):
    """같은 초의 충돌 접미사 12개(.0~.11) 중 숫자 기준 최신 2개만 남는다.

    사전식 정렬은 .10을 .2보다 앞으로 두어 최신 세대를 지운다.
    """
    v2root = tmp_path / "v2"
    v2root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    for i in range(12):
        (v2root / f"data.backup.20260101T000000.{i}").mkdir()
    build_data._prune_generations(keep=2)
    remaining = sorted(p.name for p in _generation_dirs(v2root))
    assert remaining == ["data.backup.20260101T000000.10",
                         "data.backup.20260101T000000.11"]


def test_prune_keeps_transient_and_unrelated(tmp_path, monkeypatch):
    """prune은 data.backup 본체와 무관한 디렉터리를 건드리지 않는다."""
    v2root = tmp_path / "v2"
    v2root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    (v2root / "data.backup").mkdir()
    (v2root / "data.backup-foo").mkdir()
    (v2root / "data.backup2").mkdir()
    (v2root / "other").mkdir()
    for stamp in ("20260101T000000", "20260102T000000", "20260103T000000"):
        (v2root / f"data.backup.{stamp}").mkdir()
    build_data._prune_generations(keep=2)
    assert (v2root / "data.backup").is_dir()
    assert (v2root / "data.backup-foo").is_dir()
    assert (v2root / "data.backup2").is_dir()
    assert (v2root / "other").is_dir()
    remaining = sorted(p.name for p in _generation_dirs(v2root))
    assert remaining == ["data.backup.20260102T000000", "data.backup.20260103T000000"]


def test_prune_deletion_failure_warns_without_failing(tmp_path, monkeypatch, capsys):
    """prune 삭제 실패는 경고만 남기고 빌드를 실패시키지 않는다."""
    v2root = tmp_path / "v2"
    v2root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    for stamp in ("20260101T000000", "20260102T000000", "20260103T000000"):
        (v2root / f"data.backup.{stamp}").mkdir()
    real_rmtree = build_data.shutil.rmtree

    def failing_rmtree(path, *a, **kw):
        if "20260101T000000" in str(path):
            raise OSError("injected prune failure")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(build_data.shutil, "rmtree", failing_rmtree)
    build_data._prune_generations(keep=2)  # raise하지 않음
    assert "정리 실패" in capsys.readouterr().err
    assert (v2root / "data.backup.20260101T000000").is_dir()


def test_interrupted_swap_restores_backup(tmp_path, monkeypatch):
    """중단된 교체(data/ 없음 + data.backup/ 있음) 시 다음 빌드가 복원한다.

    이전 빌드가 data/ → data.backup/ 이동 후 staging → data/ 이동 전에
    죽은 상태를 재현한다. 복원 없이는 보존 파일이 빠진 채로 재구축된다.
    """
    v2root, data, preserved, posts = _seed_build_sandbox(tmp_path)
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)
    # 중단 상태: data/를 data.backup/으로 이동 (staging 설치 전 사망).
    import shutil
    shutil.move(str(data), str(v2root / "data.backup"))
    assert not data.exists()
    assert (v2root / "data.backup").exists()
    build_data.build_data()
    # 복원된 보존 파일이 live에 있고, 빌드가 성공했다.
    for name, content in preserved.items():
        assert (data / name).read_text() == content, name
    assert json.loads((data / "posts-x.json").read_text())["posts"][0]["tweetId"] == "1"
    # 복원된 세대는 교체 후 세대 디렉터리로 보존됨.
    assert len(_generation_dirs(v2root)) == 1


def test_verify_failure_does_not_create_generation(tmp_path, monkeypatch):
    """검증 실패 시 실패 세대는 세대 디렉터리로 보존되지 않는다."""
    v2root, data, preserved, posts = _seed_build_sandbox(tmp_path)
    _patch_build_globals(monkeypatch, v2root, data, posts, tmp_path)
    build_data.build_data()  # 성공 → 세대 1개
    assert len(_generation_dirs(v2root)) == 1
    monkeypatch.setattr(
        build_data, "verify_preserved_files",
        lambda *a: (_ for _ in ()).throw(RuntimeError("injected verify failure")))
    with pytest.raises(SystemExit) as exc:
        build_data.build_data()
    assert exc.value.code == 2
    # 실패 빌드는 세대를 만들지 않고, 이전 세대가 live로 복원됨.
    assert len(_generation_dirs(v2root)) == 1
    for name, content in preserved.items():
        assert (data / name).read_text() == content, name


def test_archive_allocation_above_max_suffix(tmp_path, monkeypatch):
    """같은 초 4개 아카이브 + 중간 prune 시 최신 세대가 지워지지 않는다.

    Codex 2차 리뷰 차단 2: "첫 빈 접미사" 할당은 prune이 bare를 지운
    뒤 같은 초에 bare를 재할당해 최신 세대를 가장 오래된 것처럼
    정렬하고 prune이 최신 세대를 지운다. 할당은 max+1이어야 한다.
    """
    import datetime as _dt

    v2root = tmp_path / "v2"
    v2root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    fixed = _dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=_dt.timezone.utc)

    class _FixedDatetime:
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(build_data, "datetime", _FixedDatetime)
    stamp = "20260101T000000"

    def _archive_one():
        transient = v2root / "data.backup"
        transient.mkdir()
        return build_data._archive_backup(transient)

    assert _archive_one().name == f"data.backup.{stamp}"
    assert _archive_one().name == f"data.backup.{stamp}.1"
    assert _archive_one().name == f"data.backup.{stamp}.2"
    assert _archive_one().name == f"data.backup.{stamp}.3"
    build_data._prune_generations(keep=2)
    remaining = sorted(p.name for p in _generation_dirs(v2root))
    assert remaining == [f"data.backup.{stamp}.2", f"data.backup.{stamp}.3"]
    # bare를 재사용하면 안 된다 — max+1인 .4여야 한다.
    assert _archive_one().name == f"data.backup.{stamp}.4"
    build_data._prune_generations(keep=2)
    remaining = sorted(p.name for p in _generation_dirs(v2root))
    assert remaining == [f"data.backup.{stamp}.3", f"data.backup.{stamp}.4"]
    assert not (v2root / f"data.backup.{stamp}").exists()  # 최신이 지워지지 않음


def test_prune_ignores_unicode_digit_and_newline_names(tmp_path, monkeypatch):
    """유니코드 숫자·개행 접미사 이름은 세대가 아니다 (prune이 건드리지 않음)."""
    v2root = tmp_path / "v2"
    v2root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    (v2root / "data.backup.20260101T000000").mkdir()
    (v2root / "data.backup.20260101T000000\n").mkdir()
    (v2root / "data.backup.٢٠٢٦٠١٠١T٠٠٠٠٠٠").mkdir()
    build_data._prune_generations(keep=0)
    assert (v2root / "data.backup.20260101T000000\n").is_dir()
    assert (v2root / "data.backup.٢٠٢٦٠١٠١T٠٠٠٠٠٠").is_dir()


def test_generation_sort_total_order_leading_zero_and_bare(tmp_path, monkeypatch):
    """선행 0 별칭(.1 vs .01)과 bare vs .0이 정렬에서 동률이 되지 않는다.

    Codex 3차 리뷰 차단 3: .1과 .01은 숫자 키가 같아 prune이 열거
    순서에 의존했다. 원본 이름을 최종 타이브레이크로 둬 전체 순서로 만든다.
    """
    v2root = tmp_path / "v2"
    v2root.mkdir()
    monkeypatch.setattr(build_data, "V2_ROOT", v2root)
    stamp = "20260101T000000"
    for suffix in (".1", ".01"):
        (v2root / f"data.backup.{stamp}{suffix}").mkdir()
    # 정렬 키가 서로 다르고, 결정적이다 (열거 순서와 무관).
    keys = [build_data._generation_sort_key(p.name)
            for p in _generation_dirs(v2root)]
    assert len(set(keys)) == len(keys)
    build_data._prune_generations(keep=1)
    remaining = sorted(p.name for p in _generation_dirs(v2root))
    assert remaining == [f"data.backup.{stamp}.1"]  # .01이 오래된 쪽으로 정리됨
    # bare vs .0: bare(-1)가 .0(0)보다 오래됨.
    (v2root / f"data.backup.{stamp}").mkdir()
    (v2root / f"data.backup.{stamp}.0").mkdir()
    build_data._prune_generations(keep=1)
    remaining = sorted(p.name for p in _generation_dirs(v2root))
    assert remaining == [f"data.backup.{stamp}.1"]  # 가장 오래된 bare/.0 정리, 최신 .1 유지
