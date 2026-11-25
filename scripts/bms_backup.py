#!/usr/bin/env python3
"""Backup, verify, and restore BMS's four SQLite stores and artifact roots."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform" / "api"
for _import_root in (str(API_ROOT), str(REPO_ROOT)):
    if _import_root not in sys.path:
        sys.path.insert(0, _import_root)

import paths  # noqa: E402

try:
    import telemetry_store  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - stdlib-only host fallback
    telemetry_store = None


def _load_sqlite_backup_module() -> Any:
    try:
        from services import sqlite_backup as mod  # type: ignore[import-not-found]

        return mod
    except ModuleNotFoundError:
        module_path = API_ROOT / "services" / "sqlite_backup.py"
        spec = importlib.util.spec_from_file_location("_bms_sqlite_backup", module_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


_sqlite_backup = _load_sqlite_backup_module()
backup_sqlite_database = _sqlite_backup.backup_sqlite_database
copy_sqlite_database = _sqlite_backup.copy_sqlite_database
verify_sqlite_backup = _sqlite_backup.verify_sqlite_backup

SCHEMA_VERSION = 1
DEFAULT_WRITER_UNITS = (
    "biomodstack-api.service",
    "biomodstack-development-workflow-adapter.service",
)
OPTIONAL_DATA_ARTIFACT_DIRS = (
    "preserved-artifacts",
    "scientific_artifacts",
    "experiment-artifacts",
    "conformational_mapping_sources",
    "ont-raw-signal",
    "external_imports",
)
EXCLUDED_ARTIFACT_DIRS = frozenset({
    "work",
    "analysis_cache",
    "predeploy-backup",
    "cache",
})
STOPPED_UNIT_STATES = frozenset({
    "inactive",
    "failed",
    "dead",
    "not-found",
    "unknown",
})
ACTIVE_UNIT_STATES = frozenset({
    "active",
    "activating",
    "reloading",
})
DEFAULT_STORE_TARGET_RELPATHS = {
    "biomodstack": "biomodstack.db",
    "experiments": "experiments.db",
    "molbio_ngs": "molbio_ngs.db",
    "telemetry": "telemetry/telemetry.sqlite3",
}


class BackupError(RuntimeError):
    """Raised when backup, verification, restore, or preflight checks fail."""


def _resolve_telemetry_db_path() -> Path:
    if telemetry_store is not None:
        return telemetry_store.telemetry_db_path()
    explicit = os.getenv("BMS_TELEMETRY_DB_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (paths.get_data_root() / "telemetry" / "telemetry.sqlite3").resolve()


def discover_stores() -> list[tuple[str, Path, str]]:
    """Return (name, source_path, target_relpath) for BMS's four SQLite stores."""
    return [
        ("biomodstack", paths.get_db_path(), DEFAULT_STORE_TARGET_RELPATHS["biomodstack"]),
        ("experiments", paths.get_experiment_db_path(), DEFAULT_STORE_TARGET_RELPATHS["experiments"]),
        ("molbio_ngs", paths.get_molbio_ngs_db_path(), DEFAULT_STORE_TARGET_RELPATHS["molbio_ngs"]),
        ("telemetry", _resolve_telemetry_db_path(), DEFAULT_STORE_TARGET_RELPATHS["telemetry"]),
    ]


def discover_artifact_roots() -> list[tuple[str, Path]]:
    """Return (name, source_path) for backed-up artifact roots (excluding caches/work)."""
    data_root = paths.get_data_root()
    roots: list[tuple[str, Path]] = [
        ("bms_results", paths.get_results_dir()),
        ("inputs", paths.get_inputs_dir()),
    ]
    for name in OPTIONAL_DATA_ARTIFACT_DIRS:
        if name in EXCLUDED_ARTIFACT_DIRS:
            continue
        roots.append((name, data_root / name))
    return roots


def ensure_writers_stopped(
    writer_units: list[str] | tuple[str, ...] | None = None,
    *,
    skip_service_check: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Fail closed if any writer systemd user unit is active, activating, or unverifiable."""
    if skip_service_check:
        return
    run_fn = runner if runner is not None else subprocess.run
    units = tuple(writer_units) if writer_units else DEFAULT_WRITER_UNITS
    active_units: list[str] = []
    for unit in units:
        try:
            proc = run_fn(
                ["systemctl", "--user", "is-active", unit],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise BackupError(
                f"Unable to query systemd user unit '{unit}': {exc}. "
                "Pass --skip-service-check on non-systemd hosts."
            ) from exc
        state = (proc.stdout or "").strip().splitlines()
        state_text = state[0].strip().lower() if state else ""
        if proc.returncode == 0 or state_text in ACTIVE_UNIT_STATES:
            active_units.append(f"{unit} ({state_text or 'active'})")
            continue
        if state_text in STOPPED_UNIT_STATES:
            continue
        detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
        raise BackupError(
            f"Cannot confirm writer unit '{unit}' is stopped ({detail}). "
            "Pass --skip-service-check on non-systemd hosts."
        )
    if active_units:
        raise BackupError(
            "Refusing to proceed while writer services are active: " + ", ".join(active_units)
        )


def _git_sha(repo_root: Path = REPO_ROOT) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "unknown"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_directory(src: Path, dst: Path) -> None:
    """Copy directory tree via cp --reflink=auto -a with shutil.copytree fallback."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["cp", "--reflink=auto", "-a", str(src), str(dst)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return
    except OSError:
        pass
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.copytree(src, dst, symlinks=True)


def _restore_directory(src: Path, dst: Path) -> None:
    """Replace destination directory cleanly via sibling staging directory."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dst.name}.restore-staging-", dir=dst.parent))
    staging.rmdir()
    aside: Path | None = None
    try:
        _copy_directory(src, staging)
        if dst.exists() or dst.is_symlink():
            aside = Path(tempfile.mkdtemp(prefix=f".{dst.name}.restore-old-", dir=dst.parent))
            aside.rmdir()
            dst.rename(aside)
        try:
            staging.rename(dst)
        except Exception:
            if aside is not None and aside.exists() and not dst.exists():
                aside.rename(dst)
            raise
        if aside is not None and (aside.exists() or aside.is_symlink()):
            if aside.is_dir() and not aside.is_symlink():
                shutil.rmtree(aside)
            else:
                aside.unlink()
    finally:
        if staging.exists() or staging.is_symlink():
            if staging.is_dir() and not staging.is_symlink():
                shutil.rmtree(staging)
            else:
                staging.unlink()


def _remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


def _safe_subpath(root: Path, relpath: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relpath).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise BackupError(f"Backup manifest path escapes backup directory: {relpath}") from exc
    return candidate


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    staging = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def run_backup(
    dest: Path,
    *,
    stores_only: bool = False,
    checkpoint_wal: bool = False,
    allow_missing_stores: bool = False,
    writer_units: list[str] | tuple[str, ...] | None = None,
    skip_service_check: bool = False,
) -> dict[str, Any]:
    ensure_writers_stopped(writer_units, skip_service_check=skip_service_check)

    dest_resolved = dest.expanduser().resolve()
    if dest_resolved.exists():
        if not dest_resolved.is_dir():
            raise BackupError(f"Backup destination exists and is not a directory: {dest_resolved}")
        if any(dest_resolved.iterdir()):
            raise BackupError(f"Refusing to write backup into non-empty directory: {dest_resolved}")
    else:
        dest_resolved.mkdir(parents=True, exist_ok=True)

    discovered_stores = discover_stores()
    missing_stores = [
        f"{name} ({source_path})"
        for name, source_path, _ in discovered_stores
        if not source_path.is_file()
    ]
    if missing_stores and not allow_missing_stores:
        raise BackupError(
            "Missing required SQLite store(s): " + ", ".join(missing_stores)
        )

    stores_dir = dest_resolved / "stores"
    stores_dir.mkdir(parents=True, exist_ok=True)
    store_entries: list[dict[str, Any]] = []
    for name, source_path, target_relpath in discovered_stores:
        if not source_path.is_file():
            continue
        backup_relpath = f"stores/{name}.db"
        backup_path = dest_resolved / backup_relpath
        report = backup_sqlite_database(
            source_path,
            backup_path,
            checkpoint_wal=checkpoint_wal,
        )
        store_entries.append({
            "name": name,
            "source_path": str(report.source_path),
            "backup_relpath": backup_relpath,
            "target_relpath": target_relpath,
            "size_bytes": report.size_bytes,
            "sha256": report.sha256,
            "integrity_check": report.integrity_check,
            "foreign_key_violations": report.foreign_key_violations,
        })

    artifact_entries: list[dict[str, Any]] = []
    artifacts_dir = dest_resolved / "artifacts"
    for name, source_path in discover_artifact_roots():
        if not stores_only and source_path.is_dir():
            backup_relpath = f"artifacts/{name}"
            _copy_directory(source_path, artifacts_dir / name)
            artifact_entries.append({
                "name": name,
                "source_path": str(source_path.resolve()),
                "backup_relpath": backup_relpath,
                "copied": True,
            })
        else:
            artifact_entries.append({
                "name": name,
                "source_path": str(source_path),
                "backup_relpath": None,
                "copied": False,
            })

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_sha": _git_sha(),
        "stores_only": stores_only,
        "stores": store_entries,
        "artifacts": artifact_entries,
    }
    _write_json_atomic(dest_resolved / "manifest.json", manifest)
    return manifest


def run_verify(backup_dir: Path) -> dict[str, Any]:
    root = backup_dir.expanduser().resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise BackupError(f"Backup manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"Unable to parse backup manifest {manifest_path}: {exc}") from exc

    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BackupError(f"Unsupported or invalid backup manifest schema in {manifest_path}")

    stores = manifest.get("stores")
    if not isinstance(stores, list) or not stores:
        raise BackupError("Backup manifest contains no SQLite stores to verify")

    for entry in stores:
        if not isinstance(entry, dict):
            raise BackupError("Invalid store entry in backup manifest")
        name = entry.get("name", "<unknown>")
        relpath = entry.get("backup_relpath")
        expected_sha = entry.get("sha256")
        expected_size = entry.get("size_bytes")
        if not isinstance(relpath, str) or not isinstance(expected_sha, str) or not isinstance(expected_size, int):
            raise BackupError(f"Incomplete manifest metadata for store '{name}'")
        backup_file = _safe_subpath(root, relpath)
        if not backup_file.is_file():
            raise BackupError(f"Backed-up store file missing for '{name}': {backup_file}")
        actual_sha = _sha256_file(backup_file)
        if actual_sha != expected_sha:
            raise BackupError(
                f"SHA-256 mismatch for store '{name}': expected {expected_sha}, got {actual_sha}"
            )
        try:
            verify_sqlite_backup(
                backup_file,
                expected_size_bytes=expected_size,
                expected_sha256=expected_sha,
            )
        except Exception as exc:
            raise BackupError(f"SQLite verification failed for store '{name}': {exc}") from exc

    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise BackupError("Invalid artifacts list in backup manifest")
    for entry in artifacts:
        if not isinstance(entry, dict):
            raise BackupError("Invalid artifact entry in backup manifest")
        if entry.get("copied"):
            relpath = entry.get("backup_relpath")
            if not isinstance(relpath, str):
                raise BackupError(f"Missing backup_relpath for copied artifact '{entry.get('name')}'")
            artifact_dir = _safe_subpath(root, relpath)
            if not artifact_dir.is_dir():
                raise BackupError(f"Backed-up artifact directory missing: {artifact_dir}")

    return manifest


def run_restore(
    backup_dir: Path,
    *,
    target_root: Path | None = None,
    stores_only: bool = False,
    writer_units: list[str] | tuple[str, ...] | None = None,
    skip_service_check: bool = False,
) -> dict[str, Any]:
    ensure_writers_stopped(writer_units, skip_service_check=skip_service_check)
    manifest = run_verify(backup_dir)
    root = backup_dir.expanduser().resolve()
    resolved_target_root = target_root.expanduser().resolve() if target_root is not None else None

    restored_stores: list[dict[str, str]] = []
    for entry in manifest["stores"]:
        name = str(entry["name"])
        backup_file = _safe_subpath(root, str(entry["backup_relpath"]))
        if resolved_target_root is not None:
            target_relpath = entry.get("target_relpath") or DEFAULT_STORE_TARGET_RELPATHS.get(name, f"{name}.db")
            destination = (resolved_target_root / str(target_relpath)).resolve()
        else:
            destination = Path(str(entry["source_path"])).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        _remove_sqlite_sidecars(destination)
        copy_sqlite_database(backup_file, destination)
        _remove_sqlite_sidecars(destination)
        restored_stores.append({"name": name, "destination": str(destination)})

    restored_artifacts: list[dict[str, str]] = []
    if not stores_only:
        for entry in manifest.get("artifacts", []):
            if not entry.get("copied"):
                continue
            name = str(entry["name"])
            source_artifact_dir = _safe_subpath(root, str(entry["backup_relpath"]))
            if resolved_target_root is not None:
                destination = (resolved_target_root / name).resolve()
            else:
                destination = Path(str(entry["source_path"])).expanduser().resolve()
            _restore_directory(source_artifact_dir, destination)
            restored_artifacts.append({"name": name, "destination": str(destination)})

    return {
        "restored_stores": restored_stores,
        "restored_artifacts": restored_artifacts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Back up BMS SQLite stores and artifact roots.")
    backup_parser.add_argument("--dest", type=Path, required=True, help="Empty or new destination directory.")
    backup_parser.add_argument("--stores-only", action="store_true", help="Skip copying artifact directories.")
    backup_parser.add_argument("--checkpoint-wal", action="store_true", help="Checkpoint SQLite WAL before backup.")
    backup_parser.add_argument("--allow-missing-stores", action="store_true", help="Skip missing SQLite stores instead of failing.")
    backup_parser.add_argument("--writer-unit", action="append", default=None, help="Systemd user unit that must be stopped (repeatable).")
    backup_parser.add_argument("--skip-service-check", action="store_true", help="Skip systemd writer-stopped check.")

    verify_parser = subparsers.add_parser("verify", help="Verify a backup directory against manifest.json.")
    verify_parser.add_argument("--backup-dir", type=Path, required=True, help="Backup directory containing manifest.json.")

    restore_parser = subparsers.add_parser("restore", help="Verify and restore BMS SQLite stores and artifact roots.")
    restore_parser.add_argument("--backup-dir", type=Path, required=True, help="Backup directory containing manifest.json.")
    restore_parser.add_argument("--target-root", type=Path, default=None, help="Optional alternate root directory to restore into.")
    restore_parser.add_argument("--stores-only", action="store_true", help="Restore SQLite stores only; skip artifact directories.")
    restore_parser.add_argument("--writer-unit", action="append", default=None, help="Systemd user unit that must be stopped (repeatable).")
    restore_parser.add_argument("--skip-service-check", action="store_true", help="Skip systemd writer-stopped check.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = run_backup(
                args.dest,
                stores_only=args.stores_only,
                checkpoint_wal=args.checkpoint_wal,
                allow_missing_stores=args.allow_missing_stores,
                writer_units=args.writer_unit,
                skip_service_check=args.skip_service_check,
            )
        elif args.command == "verify":
            result = run_verify(args.backup_dir)
        elif args.command == "restore":
            result = run_restore(
                args.backup_dir,
                target_root=args.target_root,
                stores_only=args.stores_only,
                writer_units=args.writer_unit,
                skip_service_check=args.skip_service_check,
            )
        else:
            raise BackupError(f"Unsupported command: {args.command}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
