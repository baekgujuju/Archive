"""Private source backup/restore and selected-source dry run. No network.

Never modifies canonical records, Drive state, annotations, or source files.
Backup hashes detect damage; they are not signatures/authentication. Keep
bundles private, independently stored, and retain their external SHA-256.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from preservation_audit import audit_capture

MAX_FILE = 96 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_FILES = 20_000
HASH = re.compile(r"^[a-f0-9]{64}$")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as src:
        for data in iter(lambda: src.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def _dir_sync(path):
    if os.name == "nt":
        # Windows supports file fsync, not POSIX directory fsync. Production
        # Linux must perform directory sync; do not swallow Linux failures.
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unique_json(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate_json_key")
        out[k] = v
    return out


def _private_json(path, value):
    # Only called inside a newly created private staging directory.
    with Path(path).open("x", encoding="utf-8", newline="\n") as dst:
        json.dump(value, dst, ensure_ascii=False, indent=2)
        dst.flush()
        os.fsync(dst.fileno())


def _new_target(path):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("destination_symlink_component")
    if path.exists() or path.is_symlink():
        raise ValueError("destination_must_not_exist")
    if not path.parent.is_dir():
        raise ValueError("destination_parent_missing")
    return path


def _publish_directory(staging, target):
    # Publish an entirely verified directory atomically, without replacing even
    # an empty directory that appeared in the meantime. No partial tree leaks.
    if os.name == "nt":
        os.rename(staging, target)  # Windows rename refuses existing targets.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise RuntimeError("atomic_noreplace_unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(staging), -100, os.fsencode(target), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    else:
        raise RuntimeError("atomic_noreplace_unavailable")
    _dir_sync(target.parent)


def _remove_owned_tree(path, container):
    path, container = Path(path), Path(container).resolve()
    resolved = path.resolve()
    if path.is_symlink() or resolved == container or not resolved.is_relative_to(container):
        raise ValueError("cleanup_target_outside_owned_directory")
    shutil.rmtree(path)


def backup_sources(sources, target):
    target = _new_target(target)
    paths = [Path(p) for p in sources]
    if not paths or len(paths) > MAX_FILES:
        raise ValueError("source_count_invalid")
    entries, unique, total = [], {}, 0
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("source_must_be_regular_capture_html")
        size = path.stat().st_size
        if not 0 < size <= MAX_FILE:
            raise ValueError("source_size_invalid")
        sha = digest(path)
        entries.append(dict(name=path.name, size=size, sha256=sha,
                            source_mtime_ns=path.stat().st_mtime_ns,
                            object="objects/" + sha + ".html"))
        if sha not in unique:
            total += size
            unique[sha] = path
        if total > MAX_TOTAL:
            raise ValueError("backup_total_limit")
    manifest = dict(schema_version=1, kind="archive-source-backup",
                    created_at=datetime.now(timezone.utc).isoformat(), entries=entries)
    fd, tmp = tempfile.mkstemp(prefix=".source-backup-", suffix=".zip", dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for sha, path in unique.items():
                archive.write(path, "objects/" + sha + ".html")
                if digest(path) != sha:
                    raise ValueError("source_changed_during_backup")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        # Re-read archive objects, not just live sources, before acknowledging.
        verify_backup(tmp)
        with open(tmp, "rb+") as stream:
            os.fsync(stream.fileno())
        # Exclusive publication prevents races from overwriting another backup.
        os.link(tmp, target)
        _dir_sync(target.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return dict(entries=len(entries), objects=len(unique), sha256=digest(target), bytes=target.stat().st_size)


def _checked_manifest(archive):
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)) or len(names) > MAX_FILES + 1:
        raise ValueError("duplicate_or_excess_zip_entries")
    if "manifest.json" not in names:
        raise ValueError("backup_manifest_missing")
    if archive.getinfo("manifest.json").file_size > 8 * 1024 * 1024:
        raise ValueError("manifest_size_limit")
    manifest = json.loads(archive.read("manifest.json").decode("utf-8"), object_pairs_hook=_unique_json)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("kind") != "archive-source-backup":
        raise ValueError("backup_manifest_invalid")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_FILES:
        raise ValueError("backup_entries_invalid")
    objects = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("backup_entry_invalid")
        sha, size, name = entry.get("sha256"), entry.get("size"), entry.get("name")
        if not isinstance(sha, str) or not HASH.fullmatch(sha) or type(size) is not int or not 0 < size <= MAX_FILE:
            raise ValueError("backup_entry_invalid")
        if not isinstance(name, str) or not name or any(c in name for c in "/\\\x00"):
            raise ValueError("backup_name_invalid")
        obj = "objects/" + sha + ".html"
        if entry.get("object") != obj or objects.get(obj, size) != size:
            raise ValueError("backup_object_invalid")
        objects[obj] = size
    if sum(objects.values()) > MAX_TOTAL or set(names) != {"manifest.json", *objects}:
        raise ValueError("backup_membership_invalid")
    for info in infos:
        if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError("backup_zip_entry_invalid")
        if info.filename in objects and info.file_size != objects[info.filename]:
            raise ValueError("backup_object_size_mismatch")
    return manifest, objects


def _copy_checked(archive, name, size, dest=None):
    h, count = hashlib.sha256(), 0
    with archive.open(name) as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            count += len(data)
            if count > size:
                raise ValueError("backup_stream_size_exceeded")
            h.update(data)
            if dest is not None:
                dest.write(data)
    if count != size or h.hexdigest() != Path(name).stem:
        raise ValueError("backup_hash_or_size_mismatch")


def verify_backup(bundle, expected_sha256=None):
    if expected_sha256 is not None and (not HASH.fullmatch(expected_sha256) or digest(bundle) != expected_sha256):
        raise ValueError("backup_external_hash_mismatch")
    with zipfile.ZipFile(bundle) as archive:
        manifest, objects = _checked_manifest(archive)
        for name, size in objects.items():
            _copy_checked(archive, name, size)
    return dict(entries=len(manifest["entries"]), objects=len(objects), sha256=digest(bundle))


def restore_backup(bundle, target, *, expected_sha256):
    target = _new_target(target)
    verify_backup(bundle, expected_sha256)
    staging = Path(tempfile.mkdtemp(prefix=".source-restore-", dir=target.parent))
    try:
        with zipfile.ZipFile(bundle) as archive:
            manifest, objects = _checked_manifest(archive)
            for name, size in objects.items():
                # Filenames come only from checked content hashes; original
                # display names remain in the manifest and are never paths.
                with (staging / Path(name).name).open("xb") as dest:
                    _copy_checked(archive, name, size, dest)
                    dest.flush(); os.fsync(dest.fileno())
        if digest(bundle) != expected_sha256:
            raise ValueError("backup_changed_during_restore")
        _private_json(staging / "restore-manifest.json", manifest)
        _dir_sync(staging)
        _publish_directory(staging, target)
    finally:
        if staging.exists():
            _remove_owned_tree(staging, target.parent)
    return dict(entries=len(manifest["entries"]), restored_objects=len(objects))


def replay_capture(source, ingester, target, *, expected_sha256):
    source, ingester = Path(source), Path(ingester)
    target = _new_target(target)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_FILE:
        raise ValueError("source_invalid")
    if not HASH.fullmatch(expected_sha256) or digest(source) != expected_sha256:
        raise ValueError("source_hash_mismatch")
    if ingester.is_symlink() or not ingester.is_file() or ingester.suffix != ".py":
        raise ValueError("ingester_invalid")
    staging = Path(tempfile.mkdtemp(prefix=".source-replay-", dir=target.parent))
    try:
        inputs, outputs = staging / "inputs", staging / "parsed"
        inputs.mkdir(); outputs.mkdir()
        original = inputs / "capture.html"
        shutil.copy2(source, original)
        if digest(original) != expected_sha256:
            raise ValueError("source_changed_during_replay")
        components = [ingester, Path(__file__), Path(audit_capture.__code__.co_filename)]
        components += [ingester.parent / name for name in ("capture_fragment.py", "capture_contract.py")
                       if (ingester.parent / name).is_file()]
        component_hashes = {p.name: digest(p) for p in components}
        ingester_sha = component_hashes[ingester.name]
        result = subprocess.run([sys.executable, str(ingester.resolve()), str(inputs), "-o", str(outputs)],
                                capture_output=True, timeout=300)
        if any(digest(p) != component_hashes[p.name] for p in components) or digest(source) != expected_sha256:
            raise ValueError("input_or_ingester_changed_during_replay")
        report = dict(version=1, mode="dry_run_only", source_sha256=expected_sha256,
                      ingester_sha256=ingester_sha, ingester_returncode=result.returncode,
                      code_components=component_hashes, source_mtime_ns=source.stat().st_mtime_ns,
                      canonical_modified=False, drive_modified=False)
        if result.returncode == 0:
            records = json.loads((outputs / "posts.json").read_text(encoding="utf-8"))
            report["audit"] = audit_capture(original.read_bytes().decode("utf-8"), records)
            report["status"] = "needs_review" if report["audit"]["blocked"] else "conversion_preserved_review_before_reupload"
            _private_json(staging / "replay-records.json", records)
        else:
            report["status"] = "ingester_rejected"
        _private_json(staging / "replay-report.json", report)
        # Only review outputs survive. Source remains at its original path.
        _remove_owned_tree(inputs, staging); _remove_owned_tree(outputs, staging)
        _dir_sync(staging)
        _publish_directory(staging, target)
    finally:
        if staging.exists():
            _remove_owned_tree(staging, target.parent)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest="action",required=True)
    backup=actions.add_parser("backup");backup.add_argument("target",type=Path);backup.add_argument("sources",type=Path,nargs="+")
    verify=actions.add_parser("verify");verify.add_argument("bundle",type=Path);verify.add_argument("--expected-sha256",required=True)
    restore=actions.add_parser("restore");restore.add_argument("bundle",type=Path);restore.add_argument("target",type=Path);restore.add_argument("--expected-sha256",required=True)
    replay=actions.add_parser("replay");replay.add_argument("source",type=Path);replay.add_argument("ingester",type=Path);replay.add_argument("target",type=Path);replay.add_argument("--expected-sha256",required=True)
    args=parser.parse_args()
    if args.action=="backup":result=backup_sources(args.sources,args.target)
    elif args.action=="verify":result=verify_backup(args.bundle,args.expected_sha256)
    elif args.action=="restore":result=restore_backup(args.bundle,args.target,expected_sha256=args.expected_sha256)
    else:result=replay_capture(args.source,args.ingester,args.target,expected_sha256=args.expected_sha256)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
