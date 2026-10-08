"""First-install configuration transaction (Linux, local POSIX filesystems).

Destination links may span filesystems. They all reference ONE activation
pointer on the config filesystem; publication of the links is NOT atomic.
Managed readers fail closed until that pointer exists and every link/file is
verified. Existing configurations are never migrated or overwritten here.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import biomodstack_runtime_profile as profiles
from biomodstack_install_document import configuration_preview, parse_install_document


class ConfigurationBlocked(RuntimeError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def transaction_dir() -> Path:
    return profiles.get_biomodstack_config_dir() / "configuration-v1"


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir(path: Path) -> None:
    if not path.exists():
        _mkdir(path.parent)
        path.mkdir(mode=0o700)
        _sync(path.parent)


def _write(path: Path, data: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync(path.parent)


@contextmanager
def configuration_lock():
    root = profiles.get_biomodstack_config_dir()
    _mkdir(root)
    with (root / "configuration.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConfigurationBlocked("configuration_busy: another configuration writer holds the lock") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _destinations() -> dict[str, str]:
    return {"profile": str(profiles.get_install_profile_path()),
            "core_runtime_env": str(profiles.get_core_runtime_env_path()),
            "compat_env": str(profiles.get_compat_env_path())}


def _context(project_root: Path) -> dict:
    return {"home": str(Path.home().resolve()), "source": str(project_root.resolve()),
            "state_home": str(Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state").resolve()),
            "destinations": _destinations()}


def _load() -> dict:
    return json.loads((transaction_dir() / "journal.json").read_text(encoding="utf-8"))


def _verify(journal: dict, *, activated: bool) -> None:
    root = transaction_dir()
    if journal["schema_version"] != "bms.configuration-journal.v1":
        raise ConfigurationBlocked("journal_invalid: unsupported schema")
    if journal["context"]["destinations"] != _destinations():
        raise ConfigurationBlocked("context_changed: configuration destinations changed")
    for key, content in journal["files"].items():
        if key not in _destinations() or _digest(content.encode()) != journal["hashes"][key]:
            raise ConfigurationBlocked("journal_invalid: file digest mismatch")
        staged = root / "generation" / key
        if staged.is_symlink() or not staged.is_file() or staged.read_text() != content:
            raise ConfigurationBlocked(f"staging_invalid: {key}")
    if set(journal["files"]) != set(_destinations()):
        raise ConfigurationBlocked("journal_invalid: incomplete generation")
    profile = json.loads(journal["files"]["profile"])
    profiles.validate_install_profile_raw(profile)
    resolved = profiles.resolve_runtime_paths(Path(journal["context"]["source"]), profile=profile, environ={})
    profiles.validate_runtime_port_contract(resolved)
    from biomodstack_local_resources import configured_local_policy
    from biomodstack_install_document import _path, MUTABLE_PATH_FIELDS
    policy = configured_local_policy(profile)
    resolved.update(local_cpu_threads=policy.cpu_threads, local_memory_bytes=policy.memory_bytes)
    for key, renderer in (("core_runtime_env", profiles._core_runtime_env_lines),
                          ("compat_env", profiles._compat_env_lines)):
        if journal["files"][key] != "\n".join(renderer(resolved)):
            raise ConfigurationBlocked("stale_generation: exports no longer match runtime authority")
    canonical = {key: _path(resolved[key], key, file=key.endswith("db_path"))
                 for field in MUTABLE_PATH_FIELDS for key in (field, "dev_" + field)}
    source = Path(journal["context"]["source"])
    for path in canonical.values():
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ConfigurationBlocked("stale_storage: mutable path overlaps source")
    for prod in MUTABLE_PATH_FIELDS:
        for dev in MUTABLE_PATH_FIELDS:
            a, b = canonical[prod], canonical["dev_" + dev]
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise ConfigurationBlocked("stale_storage: cross-lane mutable overlap")
    if activated:
        if not (root / "active").is_symlink() or os.readlink(root / "active") != "generation":
            raise ConfigurationBlocked("configuration_incomplete: run recover")
        for key, target in _destinations().items():
            path = Path(target)
            if not path.is_symlink() or os.readlink(path) != str(root / "active" / key):
                raise ConfigurationBlocked(f"destination_conflict: {target}")


def assert_configuration_readable() -> None:
    """No writes. Called outside legacy readers' permissive error handling."""
    root = transaction_dir()
    if root.exists():
        try:
            _verify(_load(), activated=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigurationBlocked("configuration_incomplete_or_corrupt: run recover") from exc


def reject_managed_write() -> None:
    if transaction_dir().exists() or transaction_dir().with_name("configuration-v1-preparing").exists():
        raise ConfigurationBlocked("managed_configuration_read_only: explicit migration is not implemented")


def _checkpoint(name: str) -> None:
    """Test seam: tests monkeypatch this, never an environment-controlled backdoor."""


def _finish(journal: dict) -> None:
    root = transaction_dir()
    # Staging is restartable from the durable, validated journal. Never repair
    # changed existing files: corruption/tampering must not become success.
    _mkdir(root / "generation")
    for key, content in journal["files"].items():
        path = root / "generation" / key
        if not path.exists():
            _write(path, content)
        elif path.is_symlink() or path.read_text() != content:
            raise ConfigurationBlocked(f"staging_conflict: {key}")
        _checkpoint("stage:" + key)
    # Also sync on resume: a previous directory fsync may have failed after rename.
    _sync(root / "generation")
    _verify(journal, activated=False)
    if not (root / "active").exists():
        # A resumed first-install must not adopt state that appeared while it
        # was interrupted. Once activated, normal runtime state is permitted.
        profile = json.loads(journal["files"]["profile"])
        resolved = profiles.resolve_runtime_paths(Path(journal["context"]["source"]),
                                                  profile=profile, environ={})
        for field in profiles.MUTABLE_RUNTIME_STORAGE_FIELDS:
            for key in (field, "dev_" + field):
                path = Path(str(resolved[key]))
                if path.exists() and (path.is_file() or any(path.iterdir())):
                    raise ConfigurationBlocked(f"stale_storage: first-install state appeared at {path}")
    _checkpoint("validated")
    for key, target in journal["context"]["destinations"].items():
        path = Path(target)
        expected = str(root / "active" / key)
        _mkdir(path.parent)
        if path.is_symlink():
            if os.readlink(path) != expected:
                raise ConfigurationBlocked(f"destination_conflict: {target}")
        else:
            # symlink(2) refuses to overwrite even an empty existing file.
            os.symlink(expected, path)
        _sync(path.parent)
        _checkpoint("publish:" + key)
    _checkpoint("before_activation")
    active = root / "active"
    if not active.is_symlink():
        os.symlink("generation", active)
    _sync(root)
    _verify(journal, activated=True)
    _checkpoint("activated")
    journal["state"] = "committed"
    _write(root / "journal.json", _json(journal))
    _checkpoint("committed")


def configuration_report(action: str, *, project_root: Path, document: Path | None = None,
                         operation_id: str | None = None,
                         expect_document_sha256: str | None = None) -> dict:
    report = {"schema_version": "bms.configuration.v1", "action": action,
              "status": "blocked", "ready": False, "configured": False, "configuration_active": False,
              "effects": {"writes": False, "downloads": False, "service_changes": False,
                          "registration": False, "ingress_changes": False},
              "blockers": [], "installation_readiness": "not_verified"}
    try:
        if action not in {"configure", "recover", "resume"}:
            raise ValueError("unsupported configuration action")
        if document and not document.is_file():
            raise ValueError("install document must be an existing regular file")
        data = document.read_bytes() if document else None
        doc = parse_install_document(data.decode("utf-8")) if data is not None else None
        input_hash = _digest(data) if data is not None else None
        preview = None
        if expect_document_sha256 and input_hash != expect_document_sha256:
            raise ConfigurationBlocked("stale_input: document SHA256 does not match expectation")
        if action == "configure" and doc is None:
            raise ValueError("configure requires --document")
        # Resolve/validate HOME/XDG before any mkdir, including recovery.
        from biomodstack_install_document import _path
        _path(os.environ.get("HOME"), "HOME")
        _path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"), "XDG_CONFIG_HOME")
        _path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state"), "XDG_STATE_HOME")
        context = _context(project_root)
        root = transaction_dir()
        if root.is_relative_to(project_root.resolve()):
            raise ConfigurationBlocked("configuration_in_source")
        # Rendering uses existing export authority. Its shell/dotenv format is
        # intentionally bounded here: reject metacharacters rather than execute
        # or reinterpret user paths (preview accepts a broader path surface).
        pending = root.with_name("configuration-v1-preparing")
        if not root.exists() and not (pending / "journal.json").is_file():
            if action != "configure" or operation_id:
                raise ConfigurationBlocked("no_operation: no recorded operation matches this request")
            preview = configuration_preview(doc, project_root=project_root)
            for field in profiles.MUTABLE_RUNTIME_STORAGE_FIELDS:
                for key in (field, "dev_" + field):
                    storage = Path(preview["resolved"][key]).resolve()
                    for config in (root.parent.resolve(), profiles.get_compat_env_path().parent.resolve()):
                        if storage.is_relative_to(config) or config.is_relative_to(storage):
                            raise ConfigurationBlocked("configuration_storage_overlap: first-install config must be outside mutable state")
            for value in [*preview["resolved"].values(), *context["destinations"].values()]:
                if isinstance(value, str) and not re.fullmatch(r"[A-Za-z0-9_./,:@+\-]+", value):
                    raise ConfigurationBlocked("export_format_unsupported: paths must use shell/dotenv-safe characters")
        with configuration_lock():
            report["effects"]["writes"] = True  # lock/directory metadata, even on rejection
            if not root.exists() and (pending / "journal.json").is_file():
                os.rename(pending, root)
                _sync(root.parent)
            if root.exists():
                journal = _load()
                report["operation_id"] = journal["operation_id"]
                if context != journal["context"]:
                    raise ConfigurationBlocked("context_changed: recover with original HOME/XDG and checkout")
                if operation_id and operation_id != journal["operation_id"]:
                    raise ConfigurationBlocked("operation_mismatch")
                if doc is not None and doc != journal["document"]:
                    raise ConfigurationBlocked("stale_input: operation has a different install document; migration unsupported")
                if action == "configure" and journal["state"] != "committed":
                    raise ConfigurationBlocked("recovery_required: use recover or resume")
                if journal["state"] == "committed":
                    _verify(journal, activated=True)
                    report["status"] = "already_configured"
                else:
                    _checkpoint("recover")
                    _finish(journal)
                    report["status"] = "recovered"
            else:
                if preview is None or doc is None:
                    raise ConfigurationBlocked("operation_disappeared: retry configuration")
                # Recheck after locking: do not accept candidate defaults over
                # existing profiles, exports, legacy source config or user state.
                for target in [*context["destinations"].values(), str(project_root / ".env.core-runtime.local")]:
                    if os.path.lexists(target):
                        raise ConfigurationBlocked(f"existing_install_migration_unsupported: {target}")
                for key in profiles.MUTABLE_RUNTIME_STORAGE_FIELDS:
                    for lane in (key, "dev_" + key):
                        path = Path(preview["resolved"][lane])
                        if path.exists() and (path.is_file() or any(path.iterdir())):
                            raise ConfigurationBlocked(f"existing_state_migration_unsupported: {path}")
                for path in (Path.home() / ".biomodstack", Path.home() / ".biomodstack-dev"):
                    if path.exists() and any(path.iterdir()):
                        raise ConfigurationBlocked(f"existing_state_migration_unsupported: {path}")
                # Freeze effective local defaults so exports and runtime profile
                # remain equal even if host capacity changes after recovery.
                profile = dict(preview["profile"])
                profile.update(local_cpu_threads=preview["resolved"]["local_cpu_threads"],
                               local_memory_gib=preview["resolved"]["local_memory_bytes"] / 1024**3)
                profiles.validate_install_profile_raw(profile)
                files = {"profile": _json(profile),
                         "core_runtime_env": "\n".join(profiles._core_runtime_env_lines(preview["resolved"])),
                         "compat_env": "\n".join(profiles._compat_env_lines(preview["resolved"]))}
                journal = {"schema_version": "bms.configuration-journal.v1", "state": "prepared",
                           "operation_id": str(uuid.uuid4()), "context": context,
                           "document": doc, "document_sha256": input_hash,
                           "files": files, "hashes": {k: _digest(v.encode()) for k, v in files.items()},
                           "ingress": {**doc["ingress"], "applied": False}}
                # Write journal in a sibling directory before durable rename:
                # interruption can never expose an operation without its identity.
                pending = root.with_name("configuration-v1-preparing")
                _mkdir(pending)
                _write(pending / "journal.json", _json(journal))
                report["operation_id"] = journal["operation_id"]
                _checkpoint("pending_journal")
                os.rename(pending, root)
                _sync(root.parent)
                report["operation_id"] = journal["operation_id"]
                _checkpoint("journal")
                _finish(journal)
                report["status"] = "configured"
            report.update(configured=True, configuration_active=True, destinations=context["destinations"], ingress=journal["ingress"],
                          operation_id=journal["operation_id"], document_sha256=journal["document_sha256"])
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, OverflowError) as exc:
        report["blockers"].append({"code": str(exc).split(":", 1)[0] if isinstance(exc, ConfigurationBlocked)
                                   else "configuration_error", "message": str(exc)})
        report["recovery_available"] = any((path / "journal.json").is_file() for path in
                                           (transaction_dir(), transaction_dir().with_name("configuration-v1-preparing")))
        if report["recovery_available"]:
            try:
                _verify(_load(), activated=True)
                report["configuration_active"] = True
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                pass
    return report
