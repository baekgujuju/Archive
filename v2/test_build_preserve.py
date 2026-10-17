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
    monkeypatch.setattr(hook, "unpushed_commits", lambda: 0)
    monkeypatch.setattr(hook, "do_push", lambda: True)
    monkeypatch.setattr(hook, "update_manifest_translation_hash", lambda: None)
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
    monkeypatch.setattr(hook, "unpushed_commits", lambda: 0)
    monkeypatch.setattr(hook, "do_push", lambda: True)
    monkeypatch.setattr(hook, "update_manifest_translation_hash", lambda: None)
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
