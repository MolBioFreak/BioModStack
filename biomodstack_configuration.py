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
import stat
import tempfile
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
    # Canonical HOME/XDG aliases are resolved before reaching this boundary.
    # Below those roots, never follow a pre-existing symlink or adopt a foreign
    # directory. Root-owned ancestors (e.g. /tmp) are not write destinations.
    for ancestor in reversed(path.parents):
        if not os.path.lexists(ancestor):
            continue
        info = ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {Path("/").stat().st_uid, os.geteuid()}:
            raise ConfigurationBlocked(f"unsafe_directory: {ancestor}")
    if not os.path.lexists(path):
        if not os.path.lexists(path.parent):
            _mkdir(path.parent)
        path.mkdir(mode=0o700)
        _sync(path.parent)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ConfigurationBlocked(f"unsafe_directory: {path}")


def _write(path: Path, data: str, *, replace: bool = False, release: bool = False) -> None:
    _mkdir(path.parent)
    # mkstemp uses random names, O_CREAT|O_EXCL, and mode 0600. O_EXCL
    # refuses existing symlinks (including dangling ones) without following.
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                raise ConfigurationBlocked(f"unsafe_file: {path}")
            os.replace(temporary, path)
        elif release:
            # Linux RENAME_NOREPLACE has no link/unlink crash window: a killed
            # writer cannot leave the committed file with an extra temporary
            # hardlink that readers correctly reject. Unsupported FS fail closed.
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(path))
        else:
            os.link(temporary, path)  # exclusive publication; preserve existing entries
            temporary.unlink()
        _sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def configuration_lock(*, name: str = "configuration.lock"):
    root = profiles.get_biomodstack_config_dir()
    _mkdir(root)
    if name not in {"configuration.lock", "managed-release.lock"}:
        raise ValueError("unsupported lock")
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "r+") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ConfigurationBlocked("unsafe_lock: expected owned regular file")
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
    for directory in (root, root / "generation"):
        if directory.is_symlink() or not directory.is_dir():
            raise ConfigurationBlocked("configuration_invalid_generation")
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
    profiles.validate_install_profile_raw(profile, admit_local_capacity=False)
    resolved = profiles.resolve_runtime_paths(Path(journal["context"]["source"]), profile=profile, environ={})
    profiles.validate_runtime_port_contract(resolved)
    from biomodstack_local_resources import committed_local_policy
    from biomodstack_install_document import _path, MUTABLE_PATH_FIELDS
    policy = committed_local_policy(profile)
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
        identity = configuration_identity()
        if identity != "generation":
            _verify_release_generation(journal, identity)
        if identity != configuration_identity():
            raise ConfigurationBlocked("configuration_changed: retry")
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


def _check_first_install_state(resolved: dict, source: Path, *, owned_links: bool = False) -> None:
    destinations = _destinations()
    def owned(path: Path) -> bool:
        return owned_links and any(
            path == Path(target) and path.is_symlink()
            and os.readlink(path) == str(transaction_dir() / "active" / key)
            for key, target in destinations.items())

    for target in [*destinations.values(), str(source / ".env.core-runtime.local")]:
        path = Path(target)
        if os.path.lexists(path) and not owned(path):
            raise ConfigurationBlocked(f"existing_install_migration_unsupported: {path}")
    for field in profiles.MUTABLE_RUNTIME_STORAGE_FIELDS:
        for key in (field, "dev_" + field):
            path = Path(str(resolved[key]))
            if os.path.lexists(path) and (not path.is_dir() or path.is_symlink() or any(path.iterdir())):
                raise ConfigurationBlocked(f"stale_storage: first-install state appeared at {path}")
    for path in (Path.home().resolve() / ".biomodstack", Path.home().resolve() / ".biomodstack-dev"):
        if os.path.lexists(path):
            if path.is_symlink() or not path.is_dir() or any(not owned(entry) for entry in path.iterdir()):
                raise ConfigurationBlocked(f"existing_state_migration_unsupported: {path}")


def _finish(journal: dict) -> None:
    root = transaction_dir()
    _mkdir(root)
    # Staging is restartable from the durable, validated journal. Never repair
    # changed existing files: corruption/tampering must not become success.
    _mkdir(root / "generation")
    for key, content in journal["files"].items():
        path = root / "generation" / key
        if not os.path.lexists(path):
            _write(path, content)
        elif path.is_symlink() or path.read_text() != content:
            raise ConfigurationBlocked(f"staging_conflict: {key}")
        _checkpoint("stage:" + key)
    # Also sync on resume: a previous directory fsync may have failed after rename.
    _sync(root / "generation")
    _verify(journal, activated=False)
    profile = json.loads(journal["files"]["profile"])
    resolved = profiles.resolve_runtime_paths(Path(journal["context"]["source"]),
                                              profile=profile, environ={})
    if not (root / "active").exists():
        # A resumed first-install must not adopt state that appeared while it
        # was interrupted. Once activated, normal runtime state is permitted.
        _check_first_install_state(resolved, Path(journal["context"]["source"]), owned_links=True)
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
    if not active.exists():
        _check_first_install_state(resolved, Path(journal["context"]["source"]), owned_links=True)
    if not active.is_symlink():
        os.symlink("generation", active)
    _sync(root)
    _verify(journal, activated=True)
    _checkpoint("activated")
    journal["state"] = "committed"
    _write(root / "journal.json", _json(journal), replace=True)
    _checkpoint("committed")


RECEIPT_KEYS = {"BMS_BUILD_SHA", "BMS_BUILD_ID", "BMS_BUILD_TIME",
                "BMS_MANAGED_API_IMAGE_ID", "BMS_MANAGED_WEB_IMAGE_ID"}


class ManagedReleaseRecoveryRequired(ConfigurationBlocked):
    """Candidate must not be rolled back after durable acceptance intent."""


def configuration_identity() -> str | None:
    root = transaction_dir()
    if not root.exists():
        return None
    active = root / "active"
    if not active.is_symlink():
        raise ConfigurationBlocked("configuration_incomplete: run recover")
    target = os.readlink(active)
    if target != "generation" and not re.fullmatch(r"release-[0-9a-f]{64}", target):
        raise ConfigurationBlocked("configuration_invalid_target")
    if (root / target).is_symlink() or not (root / target).is_dir():
        raise ConfigurationBlocked("configuration_invalid_generation")
    return target


def configured_ingress_policy() -> dict | None:
    """Return committed setup ingress intent without applying any service changes.

    Legacy installations have no managed policy and retain their existing launch
    behavior. Incomplete or changed generations fail closed like other readers.
    """
    before = configuration_identity()
    assert_configuration_readable()
    if before is None:
        return None
    policy = _load().get("ingress")
    if not isinstance(policy, dict) or policy.get("mode") not in {"local-only", "tailnet"}:
        raise ConfigurationBlocked("invalid_ingress_policy")
    if policy["mode"] == "tailnet" and policy.get("target") not in {"development", "production"}:
        raise ConfigurationBlocked("invalid_ingress_policy")
    if before != configuration_identity():
        raise ConfigurationBlocked("configuration_changed: retry ingress read")
    return dict(policy)


def _receipt(receipt: dict) -> dict:
    from scripts.biomodstack_release import BuildIdentity
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise ConfigurationBlocked("release_receipt_invalid: closed five-key receipt required")
    if not all(isinstance(v, str) for v in receipt.values()):
        raise ConfigurationBlocked("release_receipt_invalid: strings required")
    BuildIdentity(receipt["BMS_BUILD_SHA"], receipt["BMS_BUILD_ID"], receipt["BMS_BUILD_TIME"])
    for key in RECEIPT_KEYS:
        if not re.fullmatch(r"[A-Za-z0-9_.:+/@-]+", receipt[key]):
            raise ConfigurationBlocked("release_receipt_invalid: unsafe export")
    for key in ("BMS_MANAGED_API_IMAGE_ID", "BMS_MANAGED_WEB_IMAGE_ID"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt[key]):
            raise ConfigurationBlocked("release_receipt_invalid: immutable image required")
    return dict(receipt)


def _release_files(journal, receipt):
    files = dict(journal["files"])
    files["core_runtime_env"] = files["core_runtime_env"].rstrip("\n") + "\n" + "".join(
        f"{k}={receipt[k]}\n" for k in sorted(receipt))
    return files


def _regular_text(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise ConfigurationBlocked(f"unsafe_file: {path}")
    return path.read_text()


def _verify_release_generation(journal, generation):
    root = transaction_dir() / generation
    manifest = json.loads(_regular_text(root / "manifest.json"))
    receipt = _receipt(manifest["receipt"])
    if (manifest["schema_version"] != "bms.configuration-release.v1"
            or manifest["operation_id"] != journal["operation_id"]
            or manifest["base_hashes"] != journal["hashes"]
            or manifest["generation_id"] != generation
            or generation != "release-" + _digest(manifest["release_id"].encode())):
        raise ConfigurationBlocked("release_manifest_invalid")
    files = _release_files(journal, receipt)
    if manifest["hashes"] != {k: _digest(v.encode()) for k, v in files.items()}:
        raise ConfigurationBlocked("release_manifest_hashes_invalid")
    for key, content in files.items():
        if _regular_text(root / key) != content:
            raise ConfigurationBlocked("release_generation_corrupt: " + key)
    return manifest


def managed_release_base(project_root: Path) -> dict:
    journal = _load()
    if journal["state"] != "committed" or journal["context"] != _context(project_root):
        raise ConfigurationBlocked("managed_release_context_invalid")
    before = configuration_identity()
    _verify(journal, activated=True)
    hashes = journal["hashes"] if before == "generation" else _verify_release_generation(journal, before)["hashes"]
    if before != configuration_identity():
        raise ConfigurationBlocked("configuration_changed: retry")
    return {"operation_id": journal["operation_id"], "generation_id": before, "hashes": hashes}


def _finish_managed_release(project_root, record):
    journal = _load()
    current = managed_release_base(project_root)
    manifest = record["manifest"]
    receipt = _receipt(manifest["receipt"])
    if (record["base"] != {"operation_id": journal["operation_id"], "generation_id": "generation", "hashes": journal["hashes"]}
            or manifest["operation_id"] != journal["operation_id"]
            or manifest["schema_version"] != "bms.configuration-release.v1"
            or manifest["base_hashes"] != journal["hashes"]
            or manifest["hashes"] != {k: _digest(v.encode()) for k, v in _release_files(journal, receipt).items()}):
        raise ConfigurationBlocked("release_record_invalid")
    _validate_known_good(record["known_good"], receipt)
    generation = record["manifest"]["generation_id"]
    if current != record["base"] and current["generation_id"] != generation:
        raise ConfigurationBlocked("managed_release_stale_base")
    root = transaction_dir()
    if generation != "release-" + _digest(record["manifest"]["release_id"].encode()):
        raise ConfigurationBlocked("release_manifest_invalid")
    if record["known_good"].get("configuration_generation_id") != generation or record["known_good"].get("release_id") != record["manifest"]["release_id"]:
        raise ConfigurationBlocked("release_binding_invalid")
    directory = root / generation
    _mkdir(directory)
    for key, content in {**_release_files(journal, _receipt(record["manifest"]["receipt"])),
                         "manifest.json": _json(record["manifest"])}.items():
        if not os.path.lexists(directory / key):
            _write(directory / key, content, release=True)
        elif _regular_text(directory / key) != content:
            raise ConfigurationBlocked("release_staging_conflict: " + key)
        _checkpoint("release_stage:" + key)
    _sync(directory)
    _checkpoint("release_directory")
    _verify_release_generation(journal, generation)
    _checkpoint("release_before_activation")
    if current["generation_id"] != generation:
        temporary = root / ("activate-" + str(uuid.uuid4()))
        os.symlink(generation, temporary)
        try:
            os.replace(temporary, root / "active")
        finally:
            temporary.unlink(missing_ok=True)
    _checkpoint("release_activation_rename")
    _sync(root)
    _checkpoint("release_activation_sync")
    _verify(journal, activated=True)
    destination = Path(record["known_good_path"])
    _checkpoint("release_before_known_good")
    if os.path.lexists(destination):
        if json.loads(_regular_text(destination)) != record["known_good"]:
            raise ConfigurationBlocked("known_good_conflict")
        _sync(destination.parent)
    else:
        _write(destination, _json(record["known_good"]), release=True)
    _checkpoint("release_known_good")
    if json.loads(_regular_text(destination)) != record["known_good"]:
        raise ConfigurationBlocked("known_good_verification_failed")
    _checkpoint("release_before_ack")
    if record["state"] != "committed":
        record["state"] = "committed"
        _write(root / "release.json", _json(record), replace=True)
    _sync(root)
    _checkpoint("release_ack")
    return record["known_good"]


def _validate_known_good(payload, receipt):
    if (payload.get("build") != {k: receipt[k] for k in ("BMS_BUILD_SHA", "BMS_BUILD_ID", "BMS_BUILD_TIME")}
            or payload.get("images", {}).get("bms-api") != receipt["BMS_MANAGED_API_IMAGE_ID"]
            or payload.get("images", {}).get("bms-web") != receipt["BMS_MANAGED_WEB_IMAGE_ID"]):
        raise ConfigurationBlocked("release_known_good_binding_invalid")


def commit_managed_release(project_root: Path, expected_base: dict, release_id: str,
                           receipt: dict, *, known_good_path: Path, known_good: dict) -> dict:
    receipt = _receipt(receipt)
    _validate_known_good(known_good, receipt)
    if not isinstance(release_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:+@-]+", release_id):
        raise ConfigurationBlocked("release_id_invalid")
    canonical = Path(_context(project_root)["state_home"]) / "biomodstack/releases/known-good.json"
    if known_good_path != canonical:
        raise ConfigurationBlocked("managed_release_state_override_unsupported")
    intent_recorded = False
    try:
        with configuration_lock():
            root = transaction_dir()
            path = root / "release.json"
            if os.path.lexists(path):
                record = json.loads(_regular_text(path))
                if record["manifest"]["release_id"] != release_id or record["manifest"]["receipt"] != receipt:
                    raise ConfigurationBlocked("managed_release_migration_unsupported")
                if record["base"] != expected_base:
                    raise ConfigurationBlocked("managed_release_stale_base")
            else:
                if managed_release_base(project_root) != expected_base or expected_base["generation_id"] != "generation":
                    raise ConfigurationBlocked("managed_release_stale_base")
                if os.path.lexists(canonical):
                    raise ConfigurationBlocked("managed_release_existing_known_good")
                journal = _load()
                generation = "release-" + _digest(release_id.encode())
                if os.path.lexists(root / generation):
                    raise ConfigurationBlocked("release_staging_conflict")
                manifest = {"schema_version": "bms.configuration-release.v1", "release_id": release_id,
                            "operation_id": journal["operation_id"], "generation_id": generation,
                            "base_hashes": journal["hashes"], "receipt": receipt,
                            "hashes": {k: _digest(v.encode()) for k, v in _release_files(journal, receipt).items()}}
                record = {"state": "prepared", "base": expected_base, "manifest": manifest,
                          "known_good_path": str(canonical), "known_good": {**known_good,
                          "release_id": release_id, "configuration_generation_id": generation}}
                _checkpoint("release_before_intent")
                try:
                    _write(path, _json(record), release=True)
                except BaseException as exc:
                    if os.path.lexists(path):
                        intent_recorded = True
                        raise ManagedReleaseRecoveryRequired(f"managed_release_recovery_required: {release_id}") from exc
                    raise
            intent_recorded = True
            try:
                _sync(root)
                _checkpoint("release_intent")
                return _finish_managed_release(project_root, record)
            except BaseException as exc:
                raise ManagedReleaseRecoveryRequired(f"managed_release_recovery_required: {release_id}: {exc}") from exc
    except BaseException as exc:
        if intent_recorded and not isinstance(exc, ManagedReleaseRecoveryRequired):
            raise ManagedReleaseRecoveryRequired(
                f"managed_release_recovery_required: {release_id}: {exc}") from exc
        raise


def recover_managed_release(project_root: Path, release_id: str) -> dict:
    with configuration_lock():
        record = json.loads(_regular_text(transaction_dir() / "release.json"))
        if record["manifest"]["release_id"] != release_id:
            raise ConfigurationBlocked("release_id_mismatch")
        canonical = Path(_context(project_root)["state_home"]) / "biomodstack/releases/known-good.json"
        if record["known_good_path"] != str(canonical):
            raise ConfigurationBlocked("managed_release_context_invalid")
        return _finish_managed_release(project_root, record)


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
            if os.path.lexists(pending):
                _mkdir(pending)
            if os.path.lexists(root):
                _mkdir(root)
            if not root.exists() and (pending / "journal.json").is_file():
                journal_path = pending / "journal.json"
                if set(pending.iterdir()) != {journal_path}:
                    raise ConfigurationBlocked("staging_conflict: unowned preparing entries")
                info = journal_path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                    raise ConfigurationBlocked("unsafe_file: preparing journal")
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
                _check_first_install_state(preview["resolved"], project_root)
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
                if any(pending.iterdir()):
                    raise ConfigurationBlocked("staging_conflict: unowned preparing entries")
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
