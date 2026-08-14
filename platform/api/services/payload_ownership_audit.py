"""Fail-closed N5 payload-ownership scanning and retained audit authority.

The scanner reads only explicitly configured SQLite columns and governed artifact
manifests.  SQLite inputs are copied with the online-backup API before scanning,
so every reported source digest names the exact committed snapshot inspected.
Runtime source identity is supplied by the release caller; this module never
invokes or reads Git.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence
from urllib.parse import quote

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

from services.ngs_molbio_runtime_status import (
    NgsMolBioRuntimeAuthorityError,
    runtime_implementation_record,
)

_API_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _API_ROOT.parents[1]
_CONFIG_ROOT = _API_ROOT / "config/ngs_molbio"
_SCHEMA_REGISTRY_PATH = _CONFIG_ROOT / "schema_registry_v1.json"
_CAPABILITY_INVENTORY_PATH = _CONFIG_ROOT / "capability_inventory_v1.json"
_MANIFEST_PATH = _CONFIG_ROOT / "payload_ownership_manifest_v1.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_AUDIT_ROWS = 100_000
_MAX_PAGE_SIZE = 1_000
_MAX_CURSOR_BYTES = 4_096

LocationRole = Literal["owner", "active_copy", "projection", "preservation", "transient"]
PayloadEncoding = Literal["blob", "text", "json"]


class PayloadOwnershipError(RuntimeError):
    """Base error for fail-closed payload ownership operations."""


class PayloadOwnershipConfigurationError(PayloadOwnershipError):
    """A scan plan or cursor is incomplete, unsafe, or internally inconsistent."""


class PayloadOwnershipScanError(PayloadOwnershipError):
    """A configured source could not be scanned completely and exactly."""


class ActiveJobsPresent(PayloadOwnershipScanError):
    """The release audit was attempted while configured active jobs existed."""


class RetainedAuditUnavailable(PayloadOwnershipError):
    """The dedicated retained audit source is unavailable."""


class RetainedAuditNotFound(PayloadOwnershipError):
    """The requested retained audit does not exist."""


class RetainedAuditIntegrityError(PayloadOwnershipError):
    """A retained immutable audit or receipt failed revalidation."""


@dataclass(frozen=True)
class ReleaseSourceIdentity:
    source_commit: str
    source_tree: str


@dataclass(frozen=True)
class SQLiteColumnTarget:
    """One canonical payload or projection column scanned in keyset pages."""

    target_id: str
    database_id: str
    database_path: Path
    store_or_root: str
    table: str
    key_columns: tuple[str, ...]
    identity_columns: tuple[str, ...]
    payload_column: str
    payload_class: str
    encoding: PayloadEncoding
    location_role: LocationRole
    page_size: int = 500
    max_rows: int = 1_000_000
    max_payload_bytes: int = 64 * 1024 * 1024
    max_json_candidates: int = 2_000_000
    source_digest_column: str | None = None
    additional_forbidden_json_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SQLiteActiveJobCheck:
    """One exact active-job predicate that must return no row."""

    check_id: str
    database_id: str
    database_path: Path
    table: str
    key_column: str
    state_column: str
    active_states: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactManifestTarget:
    """A bounded governed-manifest inventory of canonical artifact payloads."""

    target_id: str
    store_or_root: str
    root: Path
    manifest_glob: str
    payload_class: str
    location_role: LocationRole
    entries_pointer: str
    identity_pointer: str
    artifact_path_pointer: str | None = None
    inline_payload_pointer: str | None = None
    declared_sha256_pointer: str | None = None
    declared_size_pointer: str | None = None
    max_manifest_files: int = 10_000
    max_manifest_bytes: int = 4 * 1024 * 1024
    max_entries: int = 100_000
    max_payload_bytes: int = 4 * 1024 * 1024 * 1024
    max_json_candidates: int = 2_000_000
    additional_forbidden_json_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PayloadOwnershipScanPlan:
    release: ReleaseSourceIdentity
    sqlite_targets: tuple[SQLiteColumnTarget, ...]
    active_job_checks: tuple[SQLiteActiveJobCheck, ...]
    artifact_targets: tuple[ArtifactManifestTarget, ...] = ()
    snapshot_directory: Path | None = None


@dataclass(frozen=True)
class PayloadOwnershipScanResult:
    audit: dict[str, Any]
    scan_sources: tuple[dict[str, Any], ...]
    target_summaries: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _InstalledContract:
    manifest: dict[str, Any]
    manifest_sha256: str
    audit_schema: dict[str, Any]


@dataclass(frozen=True)
class _Observation:
    payload_class: str
    stable_identity: str
    store_or_root: str
    sha256: str
    size_bytes: int
    location: str
    role: LocationRole
    source_digest: str | None = None


@dataclass(frozen=True)
class _Candidate:
    stable_identity: str
    store_or_root: str
    sha256: str
    size_bytes: int
    location: str


@dataclass(frozen=True)
class _Finding:
    payload_class: str
    stable_identity: str
    owner_store_or_root: str
    sha256: str
    size_bytes: int
    location: str
    reason: str


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PayloadOwnershipScanError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except PayloadOwnershipError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PayloadOwnershipScanError(f"JSON authority is unreadable: {path}") from exc
    if type(value) is not dict:
        raise PayloadOwnershipScanError(f"JSON authority must be an object: {path}")
    return value, raw


def _canonical_digest(document: Mapping[str, Any], field: str = "content_sha256") -> str:
    preimage = dict(document)
    preimage.pop(field, None)
    return hashlib.sha256(rfc8785.dumps(preimage)).hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stream_sha256(path: Path, maximum_bytes: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise PayloadOwnershipScanError(f"configured payload is not a regular file: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            size += len(chunk)
            if maximum_bytes is not None and size > maximum_bytes:
                raise PayloadOwnershipScanError(f"configured payload exceeds its byte bound: {path}")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _read_regular_bytes(path: Path, maximum_bytes: int) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PayloadOwnershipScanError(f"configured JSON source is not a regular file: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            size += len(chunk)
            if size > maximum_bytes:
                raise PayloadOwnershipScanError(f"configured JSON source exceeds its byte bound: {path}")
            chunks.append(chunk)
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return b"".join(chunks), digest.hexdigest()


def _registered_schema(
    registry: Mapping[str, Any], schema_id: str
) -> tuple[dict[str, Any], bytes]:
    entry = next(
        (item for item in registry.get("entries", ()) if item.get("schema_id") == schema_id),
        None,
    )
    if not isinstance(entry, dict):
        raise PayloadOwnershipScanError(f"required package schema is not registered: {schema_id}")
    relative = entry.get("path")
    if not isinstance(relative, str):
        raise PayloadOwnershipScanError(f"registered schema path is invalid: {schema_id}")
    path = (_REPO_ROOT / relative).resolve()
    try:
        path.relative_to(_REPO_ROOT.resolve())
    except ValueError as exc:
        raise PayloadOwnershipScanError(f"registered schema escapes package root: {schema_id}") from exc
    schema, raw = _read_json(path)
    if schema.get("$id") != schema_id:
        raise PayloadOwnershipScanError(f"registered schema identity mismatch: {schema_id}")
    if _raw_sha256(raw) != entry.get("schema_sha256"):
        raise PayloadOwnershipScanError(f"registered schema byte digest mismatch: {schema_id}")
    if _raw_sha256(rfc8785.dumps(schema)) != entry.get("schema_canonical_sha256"):
        raise PayloadOwnershipScanError(f"registered schema canonical digest mismatch: {schema_id}")
    Draft202012Validator.check_schema(schema)
    return schema, raw


def load_installed_payload_ownership_contract() -> _InstalledContract:
    """Load and byte-bind the package-local N0 manifest and its schemas."""

    registry, registry_raw = _read_json(_SCHEMA_REGISTRY_PATH)
    inventory, _inventory_raw = _read_json(_CAPABILITY_INVENTORY_PATH)
    manifest, manifest_raw = _read_json(_MANIFEST_PATH)
    if registry.get("content_sha256") != _canonical_digest(registry):
        raise PayloadOwnershipScanError("package schema registry digest mismatch")
    if inventory.get("content_sha256") != _canonical_digest(inventory):
        raise PayloadOwnershipScanError("package capability inventory digest mismatch")
    if inventory.get("schema_registry_sha256") != _raw_sha256(registry_raw):
        raise PayloadOwnershipScanError("capability inventory binds different schema-registry bytes")
    manifest_sha256 = _raw_sha256(manifest_raw)
    if inventory.get("payload_ownership_manifest_sha256") != manifest_sha256:
        raise PayloadOwnershipScanError("capability inventory binds different ownership-manifest bytes")

    manifest_schema, _ = _registered_schema(registry, "bms.payload-ownership-manifest.v1")
    audit_schema, _ = _registered_schema(registry, "bms.payload-ownership-audit.v1")
    errors = sorted(
        Draft202012Validator(manifest_schema).iter_errors(manifest),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise PayloadOwnershipScanError(
            f"package ownership manifest is invalid at {location}: {errors[0].message}"
        )
    if manifest.get("content_sha256") != _canonical_digest(manifest):
        raise PayloadOwnershipScanError("package ownership manifest canonical digest mismatch")
    if (
        manifest.get("source_commit") != inventory.get("baseline_source_commit")
        or manifest.get("source_tree") != inventory.get("baseline_source_tree")
        or manifest.get("source_commit") != registry.get("baseline_source_commit")
        or manifest.get("source_tree") != registry.get("baseline_source_tree")
    ):
        raise PayloadOwnershipScanError("package ownership source identity is internally inconsistent")
    return _InstalledContract(manifest, manifest_sha256, audit_schema)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _validate_identifier(value: str, label: str) -> None:
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise PayloadOwnershipConfigurationError(f"unsafe SQLite {label}: {value!r}")


def _quote_identifier(value: str) -> str:
    _validate_identifier(value, "identifier")
    return f'"{value}"'


def _validate_plan(
    plan: PayloadOwnershipScanPlan, contract: _InstalledContract
) -> dict[str, dict[str, Any]]:
    if _GIT_OBJECT_RE.fullmatch(plan.release.source_commit) is None:
        raise PayloadOwnershipConfigurationError("release source_commit must be a lowercase 40-hex identity")
    if _GIT_OBJECT_RE.fullmatch(plan.release.source_tree) is None:
        raise PayloadOwnershipConfigurationError("release source_tree must be a lowercase 40-hex identity")
    try:
        runtime = runtime_implementation_record()
    except (NgsMolBioRuntimeAuthorityError, ImportError, OSError) as exc:
        raise PayloadOwnershipConfigurationError(
            "package-local runtime source authority is unavailable"
        ) from exc
    if (
        plan.release.source_commit != runtime["successor_source_commit"]
        or plan.release.source_tree != runtime["successor_source_tree"]
    ):
        raise PayloadOwnershipConfigurationError(
            "release source identity differs from package-local runtime authority"
        )
    if not plan.active_job_checks:
        raise PayloadOwnershipConfigurationError(
            "at least one exact active-job check is required"
        )

    classes = {row["payload_class"]: row for row in contract.manifest["classes"]}
    target_ids: set[str] = set()
    owner_classes: set[str] = set()
    has_projection = False
    database_paths: dict[str, Path] = {}
    database_ids_by_path: dict[Path, str] = {}

    for target in (*plan.sqlite_targets, *plan.artifact_targets):
        if not target.target_id or target.target_id in target_ids:
            raise PayloadOwnershipConfigurationError("scan target IDs must be non-empty and unique")
        target_ids.add(target.target_id)
        if target.payload_class not in classes and target.payload_class != "*":
            raise PayloadOwnershipConfigurationError(
                f"unknown payload class for target {target.target_id}: {target.payload_class}"
            )
        if target.location_role not in {
            "owner", "active_copy", "projection", "preservation", "transient"
        }:
            raise PayloadOwnershipConfigurationError(
                f"target {target.target_id} has an invalid location role"
            )
        if target.payload_class == "*" and target.location_role != "projection":
            raise PayloadOwnershipConfigurationError(
                f"only projection targets may use wildcard payload class: {target.target_id}"
            )
        if target.location_role == "owner":
            if target.payload_class == "*":
                raise PayloadOwnershipConfigurationError("owner targets must name one exact payload class")
            expected = classes[target.payload_class]["active_authority"]
            if target.store_or_root != expected:
                raise PayloadOwnershipConfigurationError(
                    f"owner target {target.target_id} does not name manifest authority {expected!r}"
                )
            owner_classes.add(target.payload_class)
        if target.location_role == "projection":
            has_projection = True

    core_job_targets = [
        target
        for target in plan.sqlite_targets
        if target.table == "jobs" and target.payload_column == "params"
    ]
    if len(core_job_targets) != 1:
        raise PayloadOwnershipConfigurationError(
            "scan plan requires exactly one core jobs.params projection"
        )
    core_jobs = core_job_targets[0]
    if (
        core_jobs.key_columns != ("id",)
        or core_jobs.identity_columns != ("id",)
        or core_jobs.encoding != "json"
        or core_jobs.location_role != "projection"
        or core_jobs.payload_class != "*"
    ):
        raise PayloadOwnershipConfigurationError(
            "core jobs.params projection contract is divergent"
        )

    missing_owner_classes = set(classes) - owner_classes
    if missing_owner_classes:
        raise PayloadOwnershipConfigurationError(
            f"scan plan omits owner coverage for payload classes: {sorted(missing_owner_classes)}"
        )
    if not has_projection:
        raise PayloadOwnershipConfigurationError("scan plan must include at least one projection target")

    for target in plan.sqlite_targets:
        if target.encoding not in {"blob", "text", "json"}:
            raise PayloadOwnershipConfigurationError(
                f"target {target.target_id} has an invalid payload encoding"
            )
        for value, label in (
            (target.table, "table"),
            (target.payload_column, "payload column"),
            *[(column, "key column") for column in target.key_columns],
            *[(column, "identity column") for column in target.identity_columns],
        ):
            _validate_identifier(value, label)
        if target.source_digest_column is not None:
            _validate_identifier(target.source_digest_column, "source digest column")
        if not target.key_columns or not target.identity_columns:
            raise PayloadOwnershipConfigurationError(
                f"target {target.target_id} requires key and stable-identity columns"
            )
        if target.page_size < 1 or target.page_size > _MAX_PAGE_SIZE:
            raise PayloadOwnershipConfigurationError(
                f"target {target.target_id} page_size must be between 1 and {_MAX_PAGE_SIZE}"
            )
        if target.max_rows < 1 or target.max_payload_bytes < 1 or target.max_json_candidates < 1:
            raise PayloadOwnershipConfigurationError(f"target {target.target_id} has an invalid bound")
        if target.location_role == "preservation" and target.source_digest_column is None:
            raise PayloadOwnershipConfigurationError(
                f"preservation target {target.target_id} must bind a source digest column"
            )
        resolved = Path(target.database_path).expanduser().resolve()
        prior = database_paths.setdefault(target.database_id, resolved)
        prior_id = database_ids_by_path.setdefault(resolved, target.database_id)
        if prior != resolved or prior_id != target.database_id:
            raise PayloadOwnershipConfigurationError(
                f"database identity/path mapping is not one-to-one: {target.database_id!r}"
            )

    check_ids: set[str] = set()
    for check in plan.active_job_checks:
        if not check.check_id or check.check_id in check_ids:
            raise PayloadOwnershipConfigurationError("active-job check IDs must be non-empty and unique")
        check_ids.add(check.check_id)
        for value, label in (
            (check.table, "table"),
            (check.key_column, "key column"),
            (check.state_column, "state column"),
        ):
            _validate_identifier(value, label)
        if not check.active_states:
            raise PayloadOwnershipConfigurationError(
                f"active-job check {check.check_id} has no active states"
            )
        resolved = Path(check.database_path).expanduser().resolve()
        prior = database_paths.setdefault(check.database_id, resolved)
        if prior != resolved:
            raise PayloadOwnershipConfigurationError(
                f"database_id {check.database_id!r} maps to more than one path"
            )

    core_job_checks = [
        check
        for check in plan.active_job_checks
        if (
            check.database_id == core_jobs.database_id
            and check.table == "jobs"
            and check.key_column == "id"
            and check.state_column == "status"
        )
    ]
    if len(core_job_checks) != 1 or not {"queued", "running"}.issubset(
        set(core_job_checks[0].active_states) if core_job_checks else set()
    ):
        raise PayloadOwnershipConfigurationError(
            "scan plan requires one core jobs.status active-job check"
        )

    for target in plan.artifact_targets:
        if bool(target.artifact_path_pointer) == bool(target.inline_payload_pointer):
            raise PayloadOwnershipConfigurationError(
                f"artifact target {target.target_id} must configure exactly one payload pointer"
            )
        if target.artifact_path_pointer and (
            target.declared_sha256_pointer is None or target.declared_size_pointer is None
        ):
            raise PayloadOwnershipConfigurationError(
                f"artifact target {target.target_id} must bind declared digest and size"
            )
        if (
            target.max_manifest_files < 1
            or target.max_manifest_bytes < 1
            or target.max_entries < 1
            or target.max_payload_bytes < 1
            or target.max_json_candidates < 1
        ):
            raise PayloadOwnershipConfigurationError(f"artifact target {target.target_id} has an invalid bound")
        if Path(target.manifest_glob).is_absolute() or ".." in Path(target.manifest_glob).parts:
            raise PayloadOwnershipConfigurationError(
                f"artifact target {target.target_id} has an unsafe manifest glob"
            )
    return classes


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(path.as_posix(), safe='/')}?mode=ro"


def _open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _snapshot_database(source: Path, directory: Path | None) -> tuple[Path, str, int]:
    configured_source = source.expanduser()
    if configured_source.is_symlink():
        raise PayloadOwnershipScanError(f"SQLite source cannot be a symlink: {configured_source}")
    source = configured_source.resolve(strict=True)
    source_lstat = source.lstat()
    if stat.S_ISLNK(source_lstat.st_mode) or not stat.S_ISREG(source_lstat.st_mode):
        raise PayloadOwnershipScanError(f"SQLite source is not a direct regular file: {source}")
    snapshot_dir = None if directory is None else directory.expanduser().resolve()
    if snapshot_dir is not None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="bms-payload-audit-", suffix=".db", dir=snapshot_dir)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    snapshot = Path(name)
    try:
        with _open_readonly_sqlite(source) as source_connection:
            with sqlite3.connect(snapshot, timeout=30.0) as destination:
                source_connection.backup(destination)
                destination.commit()
        digest, size = _stream_sha256(snapshot)
        return snapshot, digest, size
    except Exception:
        snapshot.unlink(missing_ok=True)
        raise


def _table_columns(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    if not rows:
        raise PayloadOwnershipScanError(f"configured SQLite table is absent: {table}")
    return rows


def _assert_unique_key(
    connection: sqlite3.Connection, table: str, key_columns: Sequence[str]
) -> None:
    columns = _table_columns(connection, table)
    names = {str(row[1]) for row in columns}
    missing = set(key_columns) - names
    if missing:
        raise PayloadOwnershipScanError(f"configured key columns are absent from {table}: {sorted(missing)}")
    primary = tuple(
        str(row[1]) for row in sorted((row for row in columns if int(row[5]) > 0), key=lambda row: int(row[5]))
    )
    if primary == tuple(key_columns):
        return
    for index in connection.execute(f"PRAGMA index_list({_quote_identifier(table)})").fetchall():
        if not int(index[2]) or (len(index) > 4 and int(index[4])):
            continue
        indexed = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                (str(index[1]),),
            ).fetchall()
        )
        if indexed == tuple(key_columns):
            return
    raise PayloadOwnershipScanError(
        f"configured keyset columns are not an exact unique key on {table}: {tuple(key_columns)}"
    )


def _stable_part(value: Any) -> str:
    if value is None:
        raise PayloadOwnershipScanError("stable identity and keyset columns cannot be null")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, memoryview):
        return bytes(value).hex()
    return str(value)


def _stable_identity(row: sqlite3.Row, columns: Sequence[str]) -> str:
    identity = "|".join(f"{column}={_stable_part(row[column])}" for column in columns)
    if not identity or len(identity) > 1000:
        raise PayloadOwnershipScanError("stable native identity exceeds the retained audit bound")
    return identity


def _json_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except Exception as exc:
        raise PayloadOwnershipScanError("JSON payload cannot be canonicalized with RFC 8785") from exc


def _decode_cell(value: Any, encoding: PayloadEncoding, maximum: int) -> tuple[bytes, Any | None]:
    if encoding == "blob":
        if isinstance(value, memoryview):
            raw = bytes(value)
        elif isinstance(value, bytes):
            raw = value
        else:
            raise PayloadOwnershipScanError("configured BLOB payload column returned a non-BLOB value")
        parsed = None
    elif encoding == "text":
        if not isinstance(value, str):
            raise PayloadOwnershipScanError("configured TEXT payload column returned a non-text value")
        raw = value.encode("utf-8")
        parsed = None
    else:
        if not isinstance(value, str):
            raise PayloadOwnershipScanError("configured JSON payload column returned a non-text value")
        try:
            parsed = json.loads(value, object_pairs_hook=_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PayloadOwnershipScanError("configured JSON payload column contains invalid JSON") from exc
        raw = _json_bytes(parsed)
    if len(raw) > maximum:
        raise PayloadOwnershipScanError("configured payload cell exceeds its byte bound")
    return raw, parsed


def _pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise PayloadOwnershipConfigurationError(f"invalid JSON pointer: {pointer!r}")
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise PayloadOwnershipScanError(f"governed manifest JSON pointer is unresolved: {pointer}")
    return current


def _walk_json(value: Any, pointer: str = "") -> Iterable[tuple[str, str | None, Any]]:
    yield pointer, None, value
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            yield child, key, item
            yield from _walk_json_children(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{pointer}/{index}"
            yield child, None, item
            yield from _walk_json_children(item, child)


def _walk_json_children(value: Any, pointer: str) -> Iterable[tuple[str, str | None, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            yield child, key, item
            yield from _walk_json_children(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{pointer}/{index}"
            yield child, None, item
            yield from _walk_json_children(item, child)


def _candidate_bytes(value: Any) -> tuple[bytes, ...]:
    canonical = _json_bytes(value)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        if raw != canonical:
            return canonical, raw
    return (canonical,)


def _candidate_exempt_key(key: str | None) -> bool:
    if key is None:
        return False
    normalized = _normalize_key(key)
    exact = {
        "id",
        "identity",
        "revision",
        "generation",
        "digest",
        "sha256",
        "byte_size",
        "size_bytes",
        "media_type",
        "lifecycle",
        "lifecycle_state",
        "semantic_role",
        "canonical_route",
        "reopen_route",
        "reopen_uri",
    }
    return (
        normalized in exact
        or normalized.endswith("_id")
        or normalized.endswith("_ids")
        or normalized.endswith("_digest")
        or normalized.endswith("_sha256")
        or normalized.endswith("_revision")
        or normalized.endswith("_revision_id")
        or normalized.endswith("_generation")
    )


def _inspect_json(
    value: Any,
    *,
    identity: str,
    location: str,
    store_or_root: str,
    payload_class: str,
    additional_forbidden: Sequence[str],
    classes: Mapping[str, Mapping[str, Any]],
    candidate_budget: list[int],
) -> tuple[list[_Candidate], list[_Finding]]:
    forbidden: dict[str, list[str]] = {}
    for class_id, record in classes.items():
        for key in record["forbidden_global_payloads"]:
            forbidden.setdefault(_normalize_key(key), []).append(class_id)
    for key in additional_forbidden:
        if payload_class == "*":
            raise PayloadOwnershipConfigurationError(
                "a wildcard target cannot assign additional forbidden keys to one payload class"
            )
        forbidden.setdefault(_normalize_key(key), []).append(payload_class)

    candidates: list[_Candidate] = []
    findings: list[_Finding] = []
    seen_candidates: set[tuple[str, str]] = set()
    for pointer, key, item in _walk_json(value):
        if candidate_budget[0] <= 0:
            raise PayloadOwnershipScanError("JSON candidate scan exceeded its configured bound")
        candidate_budget[0] -= 1
        if not _candidate_exempt_key(key):
            for raw in _candidate_bytes(item):
                digest = _raw_sha256(raw)
                marker = (pointer, digest)
                if marker not in seen_candidates:
                    seen_candidates.add(marker)
                    candidates.append(
                        _Candidate(
                            stable_identity=identity,
                            store_or_root=store_or_root,
                            sha256=digest,
                            size_bytes=len(raw),
                            location=f"{location}#{pointer or '/'}",
                        )
                    )
        if key is None:
            continue
        for class_id in forbidden.get(_normalize_key(key), ()):
            owner = str(classes[class_id]["active_authority"])
            if store_or_root == owner:
                continue
            raw = _json_bytes(item)
            findings.append(
                _Finding(
                    payload_class=class_id,
                    stable_identity=f"{identity}#{pointer}",
                    owner_store_or_root=owner,
                    sha256=_raw_sha256(raw),
                    size_bytes=len(raw),
                    location=f"{location}#{pointer}",
                    reason=f"forbidden payload key {key!r} is present outside its manifest authority",
                )
            )
    return candidates, findings


def _scan_active_jobs(connection: sqlite3.Connection, check: SQLiteActiveJobCheck) -> None:
    columns = {str(row[1]) for row in _table_columns(connection, check.table)}
    required = {check.key_column, check.state_column}
    if not required <= columns:
        raise PayloadOwnershipScanError(
            f"active-job check {check.check_id} references absent columns: {sorted(required - columns)}"
        )
    placeholders = ",".join("?" for _ in check.active_states)
    row = connection.execute(
        f"SELECT {_quote_identifier(check.key_column)} FROM {_quote_identifier(check.table)} "
        f"WHERE {_quote_identifier(check.state_column)} IN ({placeholders}) "
        f"ORDER BY {_quote_identifier(check.key_column)} LIMIT 1",
        tuple(check.active_states),
    ).fetchone()
    if row is not None:
        raise ActiveJobsPresent(
            f"active-job check {check.check_id} found active identity {_stable_part(row[0])}"
        )


def _scan_sqlite_target(
    connection: sqlite3.Connection,
    target: SQLiteColumnTarget,
    classes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[_Observation], list[_Candidate], list[_Finding], dict[str, Any]]:
    _assert_unique_key(connection, target.table, target.key_columns)
    available = {str(row[1]) for row in _table_columns(connection, target.table)}
    selected = tuple(
        dict.fromkeys(
            (*target.key_columns, *target.identity_columns, target.payload_column, target.source_digest_column)
        )
    )
    selected = tuple(column for column in selected if column is not None)
    missing = set(selected) - available
    if missing:
        raise PayloadOwnershipScanError(
            f"target {target.target_id} references absent columns: {sorted(missing)}"
        )

    observations: list[_Observation] = []
    candidates: list[_Candidate] = []
    findings: list[_Finding] = []
    last_key: tuple[Any, ...] | None = None
    row_count = 0
    payload_count = 0
    candidate_budget = [target.max_json_candidates]
    aggregate = hashlib.sha256()
    select_sql = ", ".join(_quote_identifier(column) for column in selected)
    order_sql = ", ".join(_quote_identifier(column) for column in target.key_columns)

    while True:
        remaining = target.max_rows - row_count
        fetch_limit = min(target.page_size, remaining + 1)
        if last_key is None:
            where_sql = ""
            parameters: tuple[Any, ...] = (fetch_limit,)
        else:
            key_sql = ", ".join(_quote_identifier(column) for column in target.key_columns)
            placeholders = ", ".join("?" for _ in target.key_columns)
            where_sql = f" WHERE ({key_sql}) > ({placeholders})"
            parameters = (*last_key, fetch_limit)
        rows = connection.execute(
            f"SELECT {select_sql} FROM {_quote_identifier(target.table)}{where_sql} "
            f"ORDER BY {order_sql} LIMIT ?",
            parameters,
        ).fetchall()
        if not rows:
            break
        if row_count + len(rows) > target.max_rows:
            raise PayloadOwnershipScanError(f"target {target.target_id} exceeded its maximum row count")
        for row in rows:
            key = tuple(row[column] for column in target.key_columns)
            if any(value is None for value in key) or (last_key is not None and key == last_key):
                raise PayloadOwnershipScanError(
                    f"target {target.target_id} keyset order is null, duplicate, or unstable"
                )
            last_key = key
            row_count += 1
            value = row[target.payload_column]
            if value is None:
                continue
            identity = _stable_identity(row, target.identity_columns)
            raw, parsed = _decode_cell(value, target.encoding, target.max_payload_bytes)
            digest = _raw_sha256(raw)
            source_digest = None
            if target.source_digest_column is not None:
                source_digest = str(row[target.source_digest_column] or "")
                if _SHA256_RE.fullmatch(source_digest) is None:
                    raise PayloadOwnershipScanError(
                        f"target {target.target_id} contains an invalid preservation source digest"
                    )
            location = (
                f"sqlite:{target.database_id}:{target.table}:{target.payload_column}:{identity}"
            )
            aggregate.update(rfc8785.dumps([identity, digest, len(raw)]))
            payload_count += 1
            if target.location_role != "projection":
                observations.append(
                    _Observation(
                        payload_class=target.payload_class,
                        stable_identity=identity,
                        store_or_root=target.store_or_root,
                        sha256=digest,
                        size_bytes=len(raw),
                        location=location,
                        role=target.location_role,
                        source_digest=source_digest,
                    )
                )
            else:
                candidates.append(
                    _Candidate(identity, target.store_or_root, digest, len(raw), location)
                )
            if parsed is not None:
                inspected_candidates, inspected_findings = _inspect_json(
                    parsed,
                    identity=identity,
                    location=location,
                    store_or_root=target.store_or_root,
                    payload_class=target.payload_class,
                    additional_forbidden=target.additional_forbidden_json_keys,
                    classes=classes,
                    candidate_budget=candidate_budget,
                )
                candidates.extend(inspected_candidates)
                findings.extend(inspected_findings)
        if len(rows) < fetch_limit:
            break

    return observations, candidates, findings, {
        "target_id": target.target_id,
        "source_kind": "sqlite_column",
        "database_id": target.database_id,
        "table": target.table,
        "column": target.payload_column,
        "row_count": row_count,
        "payload_count": payload_count,
        "observed_payloads_sha256": aggregate.hexdigest(),
    }


def _safe_artifact(root: Path, relative_value: Any) -> tuple[Path, str]:
    if not isinstance(relative_value, str) or not relative_value.strip():
        raise PayloadOwnershipScanError("governed artifact path must be a non-empty relative string")
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise PayloadOwnershipScanError("governed artifact path is absolute or traversing")
    unresolved = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PayloadOwnershipScanError("governed artifact path contains a symlink")
    resolved = unresolved.resolve(strict=True)
    try:
        canonical_relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise PayloadOwnershipScanError("governed artifact escapes its configured authority root") from exc
    return resolved, canonical_relative


def _scan_artifact_target(
    target: ArtifactManifestTarget,
    classes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[_Observation], list[_Candidate], list[_Finding], dict[str, Any], dict[str, Any]]:
    configured_root = target.root.expanduser()
    if configured_root.is_symlink():
        raise PayloadOwnershipScanError(
            f"governed artifact root cannot be a symlink: {target.target_id}"
        )
    root = configured_root.resolve(strict=True)
    if not root.is_dir():
        raise PayloadOwnershipScanError(f"governed artifact root is not a directory: {target.target_id}")
    manifests: list[Path] = []
    for candidate in root.glob(target.manifest_glob):
        if len(manifests) >= target.max_manifest_files:
            raise PayloadOwnershipScanError(
                f"artifact target {target.target_id} exceeded its manifest-file bound"
            )
        if candidate.is_symlink():
            raise PayloadOwnershipScanError("governed manifest cannot be a symlink")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PayloadOwnershipScanError("governed manifest escaped its authority root") from exc
        if not resolved.is_file():
            raise PayloadOwnershipScanError("governed manifest is not a regular file")
        manifests.append(resolved)
    manifests.sort(key=lambda path: path.relative_to(root).as_posix())

    observations: list[_Observation] = []
    candidates: list[_Candidate] = []
    findings: list[_Finding] = []
    aggregate = hashlib.sha256()
    entry_count = 0
    candidate_budget = [target.max_json_candidates]
    for manifest_path in manifests:
        raw, manifest_sha = _read_regular_bytes(manifest_path, target.max_manifest_bytes)
        manifest_size = len(raw)
        try:
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        except PayloadOwnershipError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PayloadOwnershipScanError("governed artifact manifest contains invalid JSON") from exc
        entries_value = _pointer(document, target.entries_pointer)
        entries = entries_value if isinstance(entries_value, list) else [entries_value]
        if entry_count + len(entries) > target.max_entries:
            raise PayloadOwnershipScanError(
                f"artifact target {target.target_id} exceeded its entry bound"
            )
        manifest_relative = manifest_path.relative_to(root).as_posix()
        aggregate.update(rfc8785.dumps([manifest_relative, manifest_sha, manifest_size]))
        for index, entry in enumerate(entries):
            entry_count += 1
            identity_value = _pointer(entry, target.identity_pointer)
            if isinstance(identity_value, (dict, list)) or identity_value is None:
                raise PayloadOwnershipScanError("governed artifact identity must be one stable scalar")
            identity = str(identity_value)
            if not identity or len(identity) > 1000:
                raise PayloadOwnershipScanError("governed artifact identity exceeds the audit bound")
            base_location = f"artifact:{target.target_id}:{manifest_relative}:{index}:{identity}"
            declared_digest: str | None = None
            if target.artifact_path_pointer is not None:
                artifact, artifact_relative = _safe_artifact(
                    root, _pointer(entry, target.artifact_path_pointer)
                )
                declared_digest = str(_pointer(entry, str(target.declared_sha256_pointer)))
                declared_size = _pointer(entry, str(target.declared_size_pointer))
                if _SHA256_RE.fullmatch(declared_digest) is None or type(declared_size) is not int:
                    raise PayloadOwnershipScanError("governed artifact descriptor has invalid digest or size")
                digest, size = _stream_sha256(artifact, target.max_payload_bytes)
                if digest != declared_digest or size != declared_size:
                    raise PayloadOwnershipScanError(
                        f"governed artifact bytes disagree with manifest: {artifact_relative}"
                    )
                location = f"{base_location}:{artifact_relative}"
                parsed_inline = None
            else:
                parsed_inline = _pointer(entry, str(target.inline_payload_pointer))
                raw_payload = _json_bytes(parsed_inline)
                if len(raw_payload) > target.max_payload_bytes:
                    raise PayloadOwnershipScanError("governed inline payload exceeds its byte bound")
                digest, size = _raw_sha256(raw_payload), len(raw_payload)
                location = f"{base_location}#{target.inline_payload_pointer}"
            aggregate.update(rfc8785.dumps([identity, digest, size, location]))
            if target.location_role != "projection":
                observations.append(
                    _Observation(
                        target.payload_class,
                        identity,
                        target.store_or_root,
                        digest,
                        size,
                        location,
                        target.location_role,
                        declared_digest if target.location_role == "preservation" else None,
                    )
                )
            else:
                candidates.append(_Candidate(identity, target.store_or_root, digest, size, location))
            inspected_candidates, inspected_findings = _inspect_json(
                entry,
                identity=identity,
                location=base_location,
                store_or_root=target.store_or_root,
                payload_class=target.payload_class,
                additional_forbidden=target.additional_forbidden_json_keys,
                classes=classes,
                candidate_budget=candidate_budget,
            )
            candidates.extend(inspected_candidates)
            findings.extend(inspected_findings)

    summary = {
        "target_id": target.target_id,
        "source_kind": "artifact_manifest",
        "manifest_count": len(manifests),
        "entry_count": entry_count,
        "observed_payloads_sha256": aggregate.hexdigest(),
    }
    source = {
        "source_id": target.target_id,
        "source_kind": "governed_artifact_manifests",
        "snapshot_sha256": aggregate.hexdigest(),
        "manifest_count": len(manifests),
        "entry_count": entry_count,
    }
    return observations, candidates, findings, summary, source


def _checked_locations(locations: Iterable[str]) -> list[str]:
    values = sorted(set(locations))
    if not values or len(values) > 64 or any(len(value) > 2000 for value in values):
        raise PayloadOwnershipScanError("audit active-location cardinality or length exceeds schema bounds")
    return values


def _checked_preservations(locations: Iterable[str]) -> list[str]:
    values = sorted(set(locations))
    if len(values) > 64 or any(len(value) > 2000 for value in values):
        raise PayloadOwnershipScanError("audit preservation-copy cardinality or length exceeds schema bounds")
    return values


def _audit_row_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["payload_class"]),
        str(row["stable_native_identity"]),
        str(row["sha256"]),
        _raw_sha256(rfc8785.dumps(row)),
    )


def _audit_rows(
    observations: Sequence[_Observation],
    candidates: Sequence[_Candidate],
    findings: Sequence[_Finding],
    classes: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    owners = [item for item in observations if item.role == "owner"]
    owner_signatures = {
        (item.payload_class, item.sha256, item.size_bytes) for item in owners
    }
    active = [item for item in observations if item.role in {"owner", "active_copy", "transient"}]
    preservation = [item for item in observations if item.role == "preservation"]
    rows: list[dict[str, Any]] = []

    for owner in owners:
        authority = str(classes[owner.payload_class]["active_authority"])
        signature = (owner.payload_class, owner.sha256, owner.size_bytes)
        matching_active = [item for item in active if (item.payload_class, item.sha256, item.size_bytes) == signature]
        cross_candidates = [
            item
            for item in candidates
            if item.sha256 == owner.sha256
            and item.size_bytes == owner.size_bytes
            and item.store_or_root != authority
        ]
        preservation_locations: list[str] = []
        invalid_preservations = 0
        for item in preservation:
            if (item.payload_class, item.sha256, item.size_bytes) != signature:
                continue
            if item.source_digest == owner.sha256:
                preservation_locations.append(item.location)
            else:
                invalid_preservations += 1
        forbidden_active = [item for item in matching_active if item.store_or_root != authority]
        transient = [item for item in matching_active if item.role == "transient"]
        failure_count = len(forbidden_active) + len(cross_candidates) + len(transient) + invalid_preservations
        active_locations = _checked_locations(
            [item.location for item in matching_active]
            + [item.location for item in cross_candidates]
        )
        if failure_count:
            outcome = "fail"
            reason = (
                f"canonical payload has {failure_count} forbidden cross-store, transient, "
                "or unbound preservation location(s)"
            )
        else:
            outcome = "pass"
            reason = "canonical payload is active only in its manifest-declared authority"
        rows.append(
            {
                "payload_class": owner.payload_class,
                "stable_native_identity": owner.stable_identity,
                "owner_store_or_root": authority,
                "sha256": owner.sha256,
                "size_bytes": owner.size_bytes,
                "active_locations": active_locations,
                "permitted_preservation_copies": _checked_preservations(preservation_locations),
                "outcome": outcome,
                "reason": reason,
            }
        )

    for item in observations:
        if item.role == "owner":
            continue
        signature = (item.payload_class, item.sha256, item.size_bytes)
        if signature in owner_signatures:
            continue
        authority = str(classes[item.payload_class]["active_authority"])
        rows.append(
            {
                "payload_class": item.payload_class,
                "stable_native_identity": item.stable_identity,
                "owner_store_or_root": authority,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "active_locations": _checked_locations([item.location]),
                "permitted_preservation_copies": [],
                "outcome": "fail",
                "reason": "payload copy has no matching canonical owner observation in the complete scan",
            }
        )

    for finding in findings:
        rows.append(
            {
                "payload_class": finding.payload_class,
                "stable_native_identity": finding.stable_identity,
                "owner_store_or_root": finding.owner_store_or_root,
                "sha256": finding.sha256,
                "size_bytes": finding.size_bytes,
                "active_locations": _checked_locations([finding.location]),
                "permitted_preservation_copies": [],
                "outcome": "fail",
                "reason": finding.reason,
            }
        )

    rows = list({rfc8785.dumps(row): row for row in rows}.values())
    rows.sort(key=_audit_row_key)
    if len(rows) > _MAX_AUDIT_ROWS:
        raise PayloadOwnershipScanError("retained audit exceeds the schema row bound")
    return rows


def _validate_audit(document: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise PayloadOwnershipScanError(
            f"payload ownership audit is invalid at {location}: {errors[0].message}"
        )
    if document.get("content_sha256") != _canonical_digest(document):
        raise PayloadOwnershipScanError("payload ownership audit canonical digest mismatch")


def run_payload_ownership_scan(
    plan: PayloadOwnershipScanPlan,
    *,
    now: Callable[[], datetime] | None = None,
) -> PayloadOwnershipScanResult:
    """Run one complete no-active-job ownership scan without retaining it."""

    contract = load_installed_payload_ownership_contract()
    classes = _validate_plan(plan, contract)
    observations: list[_Observation] = []
    candidates: list[_Candidate] = []
    findings: list[_Finding] = []
    source_summaries: list[dict[str, Any]] = []
    target_summaries: list[dict[str, Any]] = []

    database_groups: dict[Path, dict[str, Any]] = {}
    for target in plan.sqlite_targets:
        path = Path(target.database_path).expanduser().resolve()
        group = database_groups.setdefault(
            path, {"database_id": target.database_id, "targets": [], "checks": []}
        )
        group["targets"].append(target)
    for check in plan.active_job_checks:
        path = Path(check.database_path).expanduser().resolve()
        group = database_groups.setdefault(
            path, {"database_id": check.database_id, "targets": [], "checks": []}
        )
        group["checks"].append(check)

    for source_path, group in sorted(database_groups.items(), key=lambda item: item[1]["database_id"]):
        snapshot, snapshot_sha256, snapshot_size = _snapshot_database(
            source_path, plan.snapshot_directory
        )
        try:
            with _open_readonly_sqlite(snapshot) as connection:
                for check in sorted(group["checks"], key=lambda item: item.check_id):
                    _scan_active_jobs(connection, check)
                for target in sorted(group["targets"], key=lambda item: item.target_id):
                    scanned = _scan_sqlite_target(connection, target, classes)
                    observations.extend(scanned[0])
                    candidates.extend(scanned[1])
                    findings.extend(scanned[2])
                    target_summaries.append(scanned[3])
        finally:
            snapshot.unlink(missing_ok=True)
        source_summaries.append(
            {
                "source_id": group["database_id"],
                "source_kind": "sqlite_snapshot",
                "snapshot_sha256": snapshot_sha256,
                "size_bytes": snapshot_size,
                "target_ids": sorted(target.target_id for target in group["targets"]),
                "active_job_check_ids": sorted(check.check_id for check in group["checks"]),
            }
        )

    for target in sorted(plan.artifact_targets, key=lambda item: item.target_id):
        scanned = _scan_artifact_target(target, classes)
        observations.extend(scanned[0])
        candidates.extend(scanned[1])
        findings.extend(scanned[2])
        target_summaries.append(scanned[3])
        source_summaries.append(scanned[4])

    rows = _audit_rows(observations, candidates, findings, classes)
    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    if timestamp.tzinfo is None:
        raise PayloadOwnershipConfigurationError("audit clock must return a timezone-aware datetime")
    created_at = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    audit: dict[str, Any] = {
        "schema": "bms.payload-ownership-audit.v1",
        "manifest_sha256": contract.manifest_sha256,
        "scanner_version": str(contract.manifest["scanner_version"]),
        "source_commit": plan.release.source_commit,
        "source_tree": plan.release.source_tree,
        "no_active_jobs": True,
        "rows": rows,
        "outcome": "fail" if any(row["outcome"] == "fail" for row in rows) else "pass",
        "created_at": created_at,
    }
    audit["content_sha256"] = _canonical_digest(audit)
    _validate_audit(audit, contract.audit_schema)
    return PayloadOwnershipScanResult(
        audit=audit,
        scan_sources=tuple(sorted(source_summaries, key=lambda item: item["source_id"])),
        target_summaries=tuple(sorted(target_summaries, key=lambda item: item["target_id"])),
    )


def _encode_cursor(family: str, values: Sequence[str]) -> str:
    token = base64.urlsafe_b64encode(rfc8785.dumps(list(values))).decode("ascii").rstrip("=")
    return f"{family}:{token}"


def _decode_cursor(cursor: str | None, family: str, arity: int) -> tuple[str, ...] | None:
    if cursor is None:
        return None
    if len(cursor.encode("utf-8")) > _MAX_CURSOR_BYTES or not cursor.startswith(f"{family}:"):
        raise PayloadOwnershipConfigurationError(f"{family} cursor is invalid")
    token = cursor[len(family) + 1 :]
    try:
        padding = "=" * (-len(token) % 4)
        value = json.loads(base64.urlsafe_b64decode(token + padding).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise PayloadOwnershipConfigurationError(f"{family} cursor is invalid") from exc
    if (
        not isinstance(value, list)
        or len(value) != arity
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise PayloadOwnershipConfigurationError(f"{family} cursor is invalid")
    return tuple(value)


_STORE_COLUMNS = {
    "audit_id",
    "created_at",
    "source_commit",
    "source_tree",
    "manifest_sha256",
    "outcome",
    "finding_count",
    "audit_document_sha256",
    "audit_json",
    "receipt_json",
    "retained_at",
}
_RECEIPT_FIELDS = {
    "schema",
    "audit_id",
    "audit_schema",
    "audit_content_sha256",
    "audit_document_sha256",
    "manifest_sha256",
    "scanner_id",
    "scanner_version",
    "source_commit",
    "source_tree",
    "created_at",
    "retained_at",
    "no_active_jobs",
    "outcome",
    "finding_count",
    "row_count",
    "scan_sources",
    "target_summaries",
    "content_sha256",
}


class RetainedPayloadOwnershipAuditStore:
    """Dedicated append-only SQLite source for immutable audits and receipts."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()

    def _create(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS retained_payload_ownership_audits (
                audit_id TEXT PRIMARY KEY CHECK(length(audit_id) = 64),
                created_at TEXT NOT NULL,
                source_commit TEXT NOT NULL CHECK(length(source_commit) = 40),
                source_tree TEXT NOT NULL CHECK(length(source_tree) = 40),
                manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256) = 64),
                outcome TEXT NOT NULL CHECK(outcome IN ('pass', 'fail')),
                finding_count INTEGER NOT NULL CHECK(finding_count >= 0),
                audit_document_sha256 TEXT NOT NULL CHECK(length(audit_document_sha256) = 64),
                audit_json BLOB NOT NULL,
                receipt_json BLOB NOT NULL,
                retained_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_retained_payload_audits_created
            ON retained_payload_ownership_audits(created_at DESC, audit_id DESC);
            CREATE TRIGGER IF NOT EXISTS trg_retained_payload_audits_no_update
            BEFORE UPDATE ON retained_payload_ownership_audits
            BEGIN
                SELECT RAISE(ABORT, 'retained payload ownership audits are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS trg_retained_payload_audits_no_delete
            BEFORE DELETE ON retained_payload_ownership_audits
            BEGIN
                SELECT RAISE(ABORT, 'retained payload ownership audits are immutable');
            END;
            """
        )

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(
                'PRAGMA table_info("retained_payload_ownership_audits")'
            ).fetchall()
        }
        if columns != _STORE_COLUMNS:
            raise RetainedAuditIntegrityError("retained payload audit table schema is absent or divergent")
        triggers = {
            str(row[0]): str(row[1] or "")
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='retained_payload_ownership_audits'"
            ).fetchall()
        }
        required = {
            "trg_retained_payload_audits_no_update",
            "trg_retained_payload_audits_no_delete",
        }
        if set(triggers) != required or any("RAISE(ABORT" not in triggers[name] for name in required):
            raise RetainedAuditIntegrityError("retained payload audit immutability triggers are divergent")

    def _open_readonly(self) -> sqlite3.Connection:
        try:
            if self.path.is_symlink():
                raise RetainedAuditUnavailable("retained payload audit source cannot be a symlink")
            resolved = self.path.resolve(strict=True)
            file_stat = resolved.lstat()
        except (OSError, FileNotFoundError) as exc:
            raise RetainedAuditUnavailable("retained payload audit source is unavailable") from exc
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise RetainedAuditUnavailable("retained payload audit source is not a direct regular file")
        try:
            connection = _open_readonly_sqlite(resolved)
            self._verify_schema(connection)
            return connection
        except PayloadOwnershipError:
            raise
        except sqlite3.Error as exc:
            raise RetainedAuditUnavailable("retained payload audit source cannot be opened read-only") from exc

    def retain(self, result: PayloadOwnershipScanResult) -> dict[str, Any]:
        contract = load_installed_payload_ownership_contract()
        _validate_audit(result.audit, contract.audit_schema)
        if result.audit["manifest_sha256"] != contract.manifest_sha256:
            raise RetainedAuditIntegrityError("audit binds a different installed ownership manifest")
        audit_bytes = rfc8785.dumps(result.audit)
        document_sha256 = _raw_sha256(audit_bytes)
        audit_id = str(result.audit["content_sha256"])
        retained_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt: dict[str, Any] = {
            "schema": "bms.payload-ownership-retention-receipt.v1",
            "audit_id": audit_id,
            "audit_schema": result.audit["schema"],
            "audit_content_sha256": result.audit["content_sha256"],
            "audit_document_sha256": document_sha256,
            "manifest_sha256": result.audit["manifest_sha256"],
            "scanner_id": contract.manifest["scanner_id"],
            "scanner_version": result.audit["scanner_version"],
            "source_commit": result.audit["source_commit"],
            "source_tree": result.audit["source_tree"],
            "created_at": result.audit["created_at"],
            "retained_at": retained_at,
            "no_active_jobs": result.audit["no_active_jobs"],
            "outcome": result.audit["outcome"],
            "finding_count": sum(row["outcome"] == "fail" for row in result.audit["rows"]),
            "row_count": len(result.audit["rows"]),
            "scan_sources": list(result.scan_sources),
            "target_summaries": list(result.target_summaries),
        }
        receipt["content_sha256"] = _canonical_digest(receipt)
        receipt_bytes = rfc8785.dumps(receipt)

        configured_path = self.path.absolute()
        if configured_path.is_symlink():
            raise RetainedAuditUnavailable("refusing a symlink retained audit source")
        path = configured_path.resolve() if configured_path.exists() else configured_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(path, timeout=30.0) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                self._create(connection)
                self._verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT audit_json, receipt_json, retained_at "
                    "FROM retained_payload_ownership_audits WHERE audit_id = ?",
                    (audit_id,),
                ).fetchone()
                if existing is not None:
                    if bytes(existing[0]) != audit_bytes:
                        raise RetainedAuditIntegrityError("retained audit identity/content conflict")
                    stored = self._decode_receipt(bytes(existing[1]))
                    if (
                        stored["audit_id"] != audit_id
                        or stored["audit_document_sha256"] != document_sha256
                        or stored["manifest_sha256"] != result.audit["manifest_sha256"]
                        or stored["source_commit"] != result.audit["source_commit"]
                        or stored["source_tree"] != result.audit["source_tree"]
                        or stored["created_at"] != result.audit["created_at"]
                        or stored["outcome"] != result.audit["outcome"]
                        or stored["no_active_jobs"] != result.audit["no_active_jobs"]
                        or stored["retained_at"] != existing[2]
                    ):
                        raise RetainedAuditIntegrityError("retained audit receipt binding conflict")
                    connection.rollback()
                    return stored
                connection.execute(
                    "INSERT INTO retained_payload_ownership_audits "
                    "(audit_id, created_at, source_commit, source_tree, manifest_sha256, outcome, "
                    "finding_count, audit_document_sha256, audit_json, receipt_json, retained_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        audit_id,
                        result.audit["created_at"],
                        result.audit["source_commit"],
                        result.audit["source_tree"],
                        result.audit["manifest_sha256"],
                        result.audit["outcome"],
                        receipt["finding_count"],
                        document_sha256,
                        audit_bytes,
                        receipt_bytes,
                        retained_at,
                    ),
                )
                connection.commit()
        except PayloadOwnershipError:
            raise
        except sqlite3.Error as exc:
            raise RetainedAuditUnavailable("retained payload audit could not be published") from exc
        return receipt

    @staticmethod
    def _decode_receipt(raw: bytes) -> dict[str, Any]:
        try:
            receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        except (UnicodeError, json.JSONDecodeError, PayloadOwnershipError) as exc:
            raise RetainedAuditIntegrityError("retained payload audit receipt is invalid JSON") from exc
        if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
            raise RetainedAuditIntegrityError("retained payload audit receipt shape is divergent")
        hash_fields = (
            "audit_id",
            "audit_content_sha256",
            "audit_document_sha256",
            "manifest_sha256",
            "content_sha256",
        )
        if any(_SHA256_RE.fullmatch(str(receipt[field])) is None for field in hash_fields):
            raise RetainedAuditIntegrityError("retained payload audit receipt has an invalid digest")
        if (
            receipt["schema"] != "bms.payload-ownership-retention-receipt.v1"
            or receipt["audit_schema"] != "bms.payload-ownership-audit.v1"
            or receipt["audit_id"] != receipt["audit_content_sha256"]
            or _GIT_OBJECT_RE.fullmatch(str(receipt["source_commit"])) is None
            or _GIT_OBJECT_RE.fullmatch(str(receipt["source_tree"])) is None
            or not isinstance(receipt["scanner_id"], str)
            or not isinstance(receipt["scanner_version"], str)
            or not isinstance(receipt["created_at"], str)
            or not isinstance(receipt["retained_at"], str)
            or type(receipt["no_active_jobs"]) is not bool
            or receipt["no_active_jobs"] is not True
            or not isinstance(receipt["outcome"], str)
            or receipt["outcome"] not in {"pass", "fail"}
            or type(receipt["finding_count"]) is not int
            or receipt["finding_count"] < 0
            or type(receipt["row_count"]) is not int
            or receipt["row_count"] < 0
            or not isinstance(receipt["scan_sources"], list)
            or not isinstance(receipt["target_summaries"], list)
        ):
            raise RetainedAuditIntegrityError("retained payload audit receipt values are invalid")
        if receipt["content_sha256"] != _canonical_digest(receipt):
            raise RetainedAuditIntegrityError("retained payload audit receipt digest mismatch")
        return receipt

    @staticmethod
    def _assert_receipt_row_binding(receipt: Mapping[str, Any], row: sqlite3.Row) -> None:
        bindings = {
            "audit_id": "audit_id",
            "created_at": "created_at",
            "source_commit": "source_commit",
            "source_tree": "source_tree",
            "manifest_sha256": "manifest_sha256",
            "outcome": "outcome",
            "finding_count": "finding_count",
            "audit_document_sha256": "audit_document_sha256",
            "retained_at": "retained_at",
        }
        if any(receipt[receipt_key] != row[column] for receipt_key, column in bindings.items()):
            raise RetainedAuditIntegrityError("retained payload audit receipt/row binding mismatch")

    @staticmethod
    def _receipt_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
        fields = (
            "audit_id",
            "audit_content_sha256",
            "audit_document_sha256",
            "manifest_sha256",
            "scanner_version",
            "source_commit",
            "source_tree",
            "created_at",
            "retained_at",
            "no_active_jobs",
            "outcome",
            "finding_count",
            "row_count",
        )
        return {field: receipt[field] for field in fields}

    def list(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise PayloadOwnershipConfigurationError("audit list limit must be between 1 and 100")
        decoded = _decode_cursor(cursor, "payload-ownership-audits", 2)
        with self._open_readonly() as connection:
            if decoded is None:
                where = ""
                parameters: tuple[Any, ...] = (limit + 1,)
            else:
                where = "WHERE created_at < ? OR (created_at = ? AND audit_id < ?)"
                parameters = (decoded[0], decoded[0], decoded[1], limit + 1)
            rows = connection.execute(
                "SELECT audit_id, created_at, source_commit, source_tree, manifest_sha256, "
                "outcome, finding_count, audit_document_sha256, retained_at, receipt_json "
                "FROM retained_payload_ownership_audits "
                f"{where} ORDER BY created_at DESC, audit_id DESC LIMIT ?",
                parameters,
            ).fetchall()
        receipts: list[dict[str, Any]] = []
        for row in rows:
            receipt = self._decode_receipt(bytes(row["receipt_json"]))
            self._assert_receipt_row_binding(receipt, row)
            receipts.append(receipt)
        has_more = len(receipts) > limit
        page = receipts[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(
                "payload-ownership-audits", [last["created_at"], last["audit_id"]]
            )
        return {
            "schema": "bms.payload-ownership-audit-list.v1",
            "items": [self._receipt_summary(item) for item in page],
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def status(self) -> dict[str, Any]:
        with self._open_readonly() as connection:
            counts = connection.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN outcome='pass' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='fail' THEN 1 ELSE 0 END) "
                "FROM retained_payload_ownership_audits"
            ).fetchone()
            row = connection.execute(
                "SELECT audit_id, created_at, source_commit, source_tree, manifest_sha256, "
                "outcome, finding_count, audit_document_sha256, retained_at, receipt_json "
                "FROM retained_payload_ownership_audits "
                "ORDER BY created_at DESC, audit_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            latest = None
        else:
            latest_receipt = self._decode_receipt(bytes(row["receipt_json"]))
            self._assert_receipt_row_binding(latest_receipt, row)
            latest = self._receipt_summary(latest_receipt)
        return {
            "schema": "bms.payload-ownership-audit-status.v1",
            "available": True,
            "immutable": True,
            "audit_count": int(counts[0] or 0),
            "passing_audit_count": int(counts[1] or 0),
            "failing_audit_count": int(counts[2] or 0),
            "latest": latest,
        }

    def detail(
        self,
        audit_id: str,
        *,
        finding_limit: int = 50,
        finding_cursor: str | None = None,
    ) -> dict[str, Any]:
        if _SHA256_RE.fullmatch(audit_id) is None:
            raise PayloadOwnershipConfigurationError("audit_id must be a lowercase SHA-256")
        if finding_limit < 1 or finding_limit > 100:
            raise PayloadOwnershipConfigurationError("finding limit must be between 1 and 100")
        family = f"payload-ownership-findings-{audit_id}"
        decoded = _decode_cursor(finding_cursor, family, 4)
        with self._open_readonly() as connection:
            row = connection.execute(
                "SELECT audit_document_sha256, audit_json, receipt_json "
                "FROM retained_payload_ownership_audits WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        if row is None:
            raise RetainedAuditNotFound("retained payload ownership audit not found")
        raw_audit = bytes(row[1])
        try:
            audit = json.loads(raw_audit.decode("utf-8"), object_pairs_hook=_pairs)
        except (UnicodeError, json.JSONDecodeError, PayloadOwnershipError) as exc:
            raise RetainedAuditIntegrityError("retained payload audit is invalid JSON") from exc
        contract = load_installed_payload_ownership_contract()
        _validate_audit(audit, contract.audit_schema)
        if audit.get("content_sha256") != audit_id or _raw_sha256(raw_audit) != str(row[0]):
            raise RetainedAuditIntegrityError("retained payload audit identity or document digest mismatch")
        receipt = self._decode_receipt(bytes(row[2]))
        if (
            receipt.get("audit_id") != audit_id
            or receipt.get("audit_content_sha256") != audit.get("content_sha256")
            or receipt.get("audit_document_sha256") != str(row[0])
            or receipt.get("manifest_sha256") != audit.get("manifest_sha256")
            or receipt.get("source_commit") != audit.get("source_commit")
            or receipt.get("source_tree") != audit.get("source_tree")
            or receipt.get("created_at") != audit.get("created_at")
            or receipt.get("no_active_jobs") != audit.get("no_active_jobs")
            or receipt.get("outcome") != audit.get("outcome")
            or receipt.get("row_count") != len(audit["rows"])
            or receipt.get("finding_count")
            != sum(item["outcome"] == "fail" for item in audit["rows"])
        ):
            raise RetainedAuditIntegrityError("retained payload audit receipt binding mismatch")

        audit_rows = list(audit["rows"])
        if decoded is not None:
            audit_rows = [item for item in audit_rows if _audit_row_key(item) > decoded]
        page = audit_rows[: finding_limit + 1]
        has_more = len(page) > finding_limit
        page = page[:finding_limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(family, list(_audit_row_key(last)))
        metadata = {key: value for key, value in audit.items() if key != "rows"}
        return {
            "schema": "bms.payload-ownership-audit-detail.v1",
            "audit": metadata,
            "receipt": receipt,
            "findings": {
                "items": page,
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        }


def validate_retained_payload_ownership_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one exact, digest-valid retained receipt or fail closed."""

    try:
        raw = rfc8785.dumps(dict(receipt))
    except (TypeError, ValueError) as exc:
        raise RetainedAuditIntegrityError(
            "retained payload audit receipt is not canonically serializable"
        ) from exc
    return RetainedPayloadOwnershipAuditStore._decode_receipt(raw)


def run_and_retain_payload_ownership_scan(
    plan: PayloadOwnershipScanPlan,
    retained_store_path: Path,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run one exact scan and atomically append its immutable audit/receipt."""

    result = run_payload_ownership_scan(plan, now=now)
    return RetainedPayloadOwnershipAuditStore(retained_store_path).retain(result)
