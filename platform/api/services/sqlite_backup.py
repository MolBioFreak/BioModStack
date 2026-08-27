"""SQLite online-backup helpers shared by owned persistence subsystems."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SQLiteBackupReport:
    source_path: Path
    backup_path: Path
    size_bytes: int
    sha256: str
    integrity_check: str
    foreign_key_violations: int
    source_snapshot: dict[str, Any]

    @property
    def quick_check(self) -> str:
        """Compatibility alias for older non-authority callers."""
        return self.integrity_check


FileIdentity = tuple[int, int]


def _identity(file_stat: os.stat_result) -> FileIdentity:
    return (file_stat.st_dev, file_stat.st_ino)


def _file_descriptor_identities() -> dict[int, FileIdentity]:
    """Snapshot live descriptor identities for SQLite connection attestation."""

    try:
        descriptor_names = os.listdir("/proc/self/fd")
    except OSError as exc:  # Linux is required for inode-pinned SQLite backup attestation.
        raise RuntimeError("Unable to enumerate file descriptors for SQLite identity attestation") from exc

    identities: dict[int, FileIdentity] = {}
    for name in descriptor_names:
        if not name.isdigit():
            continue
        descriptor = int(name)
        try:
            identities[descriptor] = _identity(os.fstat(descriptor))
        except OSError:
            continue
    return identities


def _attest_sqlite_connection(
    connection: sqlite3.Connection,
    descriptors_before_open: dict[int, FileIdentity],
    expected_identity: FileIdentity,
    *,
    label: str,
) -> None:
    """Prove the SQLite connection opened the already-pinned database inode."""

    connection.execute("PRAGMA schema_version").fetchone()
    descriptors_after_open = _file_descriptor_identities()
    for descriptor, descriptor_identity in descriptors_after_open.items():
        if (
            descriptor_identity == expected_identity
            and descriptors_before_open.get(descriptor) != expected_identity
        ):
            return
    raise RuntimeError(f"SQLite {label} connection identity does not match its pinned file")


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    duplicate = os.dup(descriptor)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        while chunk := os.read(duplicate, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(duplicate)
    return digest.hexdigest()


def _open_pinned_readonly(path: Path, expected_identity: FileIdentity, *, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if _identity(os.fstat(descriptor)) != expected_identity:
            raise RuntimeError(f"SQLite {label} identity changed before it could be opened")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None


def checkpoint_sqlite_wal(source_path: Path, *, mode: str = "TRUNCATE") -> None:
    """Checkpoint every committed WAL frame through an inode-attested handle."""

    if mode not in {"PASSIVE", "TRUNCATE"}:
        raise ValueError("SQLite WAL checkpoint mode is invalid")
    source = Path(source_path).expanduser().resolve()
    source_path_stat = _lstat(source)
    if source_path_stat is None or not stat.S_ISREG(source_path_stat.st_mode):
        raise FileNotFoundError(f"SQLite source database does not exist: {source}")
    source_identity = _identity(source_path_stat)
    descriptors_before_open = _file_descriptor_identities()
    with sqlite3.connect(f"file:{source}?mode=rw", uri=True, timeout=30.0) as connection:
        _attest_sqlite_connection(
            connection,
            descriptors_before_open,
            source_identity,
            label="WAL checkpoint",
        )
        row = connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        if row is None or len(row) != 3:
            raise RuntimeError("SQLite WAL checkpoint returned no authority result")
        busy, log_frames, checkpointed_frames = (int(value) for value in row)
        if busy != 0 or log_frames != checkpointed_frames:
            raise RuntimeError("SQLite WAL checkpoint could not establish quiescent authority")
    source_path_after = _lstat(source)
    if source_path_after is None or _identity(source_path_after) != source_identity:
        raise RuntimeError("SQLite source identity changed during WAL checkpoint")


def open_attested_sqlite_readonly_connection(source_path: Path) -> sqlite3.Connection:
    """Open and attest the exact SQLite source connection retained by reconciliation."""

    source = Path(source_path).expanduser().resolve()
    source_stat = _lstat(source)
    if source_stat is None or not stat.S_ISREG(source_stat.st_mode):
        raise FileNotFoundError(f"SQLite source database does not exist: {source}")
    descriptors_before_open = _file_descriptor_identities()
    connection = sqlite3.connect(
        f"file:{source}?mode=ro",
        uri=True,
        timeout=30.0,
        check_same_thread=False,
    )
    try:
        _attest_sqlite_connection(
            connection,
            descriptors_before_open,
            _identity(source_stat),
            label="retained backup source",
        )
    except Exception:
        connection.close()
        raise
    return connection


def inspect_sqlite_source_snapshot(
    source_path: Path,
    *,
    database_identity_sha256: str,
) -> dict[str, Any]:
    """Read the complete reconciliation source tuple from one pinned inode."""

    source = Path(source_path).expanduser().resolve()
    source_path_stat = _lstat(source)
    if source_path_stat is None or not stat.S_ISREG(source_path_stat.st_mode):
        raise FileNotFoundError(f"SQLite source database does not exist: {source}")
    source_identity = _identity(source_path_stat)
    source_descriptor = _open_pinned_readonly(source, source_identity, label="source snapshot")
    try:
        source_stat = os.fstat(source_descriptor)
        descriptors_before_open = _file_descriptor_identities()
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30.0) as connection:
            _attest_sqlite_connection(
                connection,
                descriptors_before_open,
                source_identity,
                label="source snapshot",
            )
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_check = [str(row[0]) for row in integrity_rows]
            foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            if integrity_check != ["ok"] or foreign_key_violations:
                raise RuntimeError("SQLite source failed integrity validation")
            snapshot = {
                "schema": "bms.sqlite-backup-source-preimage.v1",
                "database_identity_sha256": database_identity_sha256,
                "source_size_bytes": int(source_stat.st_size),
                "source_sha256": _sha256_descriptor(source_descriptor),
                "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
                "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
                "schema_version": int(connection.execute("PRAGMA schema_version").fetchone()[0]),
                "data_version": int(connection.execute("PRAGMA data_version").fetchone()[0]),
                "integrity_check": integrity_check[0],
                "foreign_key_violations": foreign_key_violations,
            }
        source_stat_after = os.fstat(source_descriptor)
        source_path_after = _lstat(source)
        if (
            source_path_after is None
            or _identity(source_path_after) != source_identity
            or _identity(source_stat_after) != source_identity
            or source_stat_after.st_size != source_stat.st_size
            or source_stat_after.st_mtime_ns != source_stat.st_mtime_ns
        ):
            raise RuntimeError("SQLite source identity or metadata changed during snapshot")
        return snapshot
    finally:
        os.close(source_descriptor)


def open_verified_sqlite_backup(
    backup_path: Path,
    *,
    expected_size_bytes: int,
    expected_sha256: str,
) -> sqlite3.Connection:
    """Open one verified backup inode and return a connection bound to that inode."""

    backup = Path(backup_path).expanduser().resolve()
    backup_stat = _lstat(backup)
    if backup_stat is None or not stat.S_ISREG(backup_stat.st_mode):
        raise FileNotFoundError(f"SQLite backup does not exist: {backup}")
    backup_identity = _identity(backup_stat)
    descriptor = _open_pinned_readonly(backup, backup_identity, label="backup replay")
    connection: sqlite3.Connection | None = None
    sealed_connection: sqlite3.Connection | None = None
    try:
        if os.fstat(descriptor).st_size != expected_size_bytes:
            raise RuntimeError("SQLite backup replay size does not match its receipt")
        if _sha256_descriptor(descriptor) != expected_sha256:
            raise RuntimeError("SQLite backup replay digest does not match its receipt")
        descriptors_before_open = _file_descriptor_identities()
        connection = sqlite3.connect(f"file:{backup}?mode=ro", uri=True, timeout=30.0)
        _attest_sqlite_connection(
            connection,
            descriptors_before_open,
            backup_identity,
            label="backup replay",
        )
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if [str(row[0]) for row in integrity_rows] != ["ok"] or foreign_key_violations:
            raise RuntimeError("SQLite backup replay failed integrity validation")
        sealed_connection = sqlite3.connect(":memory:")
        connection.backup(sealed_connection)
        if hashlib.sha256(sealed_connection.serialize()).hexdigest() != expected_sha256:
            raise RuntimeError("SQLite sealed backup copy does not match its receipt")
        if _sha256_descriptor(descriptor) != expected_sha256:
            raise RuntimeError("SQLite backup bytes changed during verified readback")
        backup_path_after = _lstat(backup)
        if backup_path_after is None or _identity(backup_path_after) != backup_identity:
            raise RuntimeError("SQLite backup replay identity changed during verification")
        connection.close()
        connection = None
        result = sealed_connection
        sealed_connection = None
        return result
    finally:
        if connection is not None:
            connection.close()
        if sealed_connection is not None:
            sealed_connection.close()
        os.close(descriptor)


def verify_sqlite_backup(
    backup_path: Path,
    *,
    expected_size_bytes: int,
    expected_sha256: str,
) -> None:
    """Replay full integrity and byte checks against one immutable backup object."""

    connection = open_verified_sqlite_backup(
        backup_path,
        expected_size_bytes=expected_size_bytes,
        expected_sha256=expected_sha256,
    )
    connection.close()


def backup_sqlite_database(
    source_path: Path,
    backup_path: Path,
    *,
    database_identity_sha256: str | None = None,
    checkpoint_wal: bool = False,
    source_connection: sqlite3.Connection | None = None,
) -> SQLiteBackupReport:
    """Create and verify a consistent, inode-pinned SQLite online backup.

    ``sqlite3.Connection.backup`` captures committed WAL pages. Publication is
    one no-overwrite hard-link operation from a mode-0600 staging inode. Every
    path reopen is matched to a held descriptor so a same-byte inode swap cannot
    pass validation, and a failed publication is removed only when it is still
    the inode created by this operation.
    """

    source = Path(source_path).expanduser().resolve()
    destination = Path(backup_path).expanduser().resolve()
    if source == destination:
        raise ValueError("SQLite backup source and destination must be distinct, non-alias paths")

    if checkpoint_wal:
        checkpoint_sqlite_wal(source)

    source_path_stat = _lstat(source)
    if source_path_stat is None or not stat.S_ISREG(source_path_stat.st_mode):
        raise FileNotFoundError(f"SQLite source database does not exist: {source}")
    if _lstat(destination) is not None:
        if os.path.samefile(source, destination):
            raise ValueError("SQLite backup source and destination must be distinct, non-alias paths")
        raise FileExistsError(f"Refusing to overwrite existing SQLite backup: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp-", dir=destination.parent
    )
    staging = Path(staging_name)
    os.fchmod(staging_descriptor, 0o600)
    staging_identity = _identity(os.fstat(staging_descriptor))
    publication_attempted = False

    try:
        source_descriptor = _open_pinned_readonly(
            source,
            _identity(source_path_stat),
            label="source",
        )
        try:
            source_stat_before = os.fstat(source_descriptor)
            source_identity = _identity(source_stat_before)
            if _lstat(destination) is not None:
                destination_stat = _lstat(destination)
                if destination_stat is not None and _identity(destination_stat) == source_identity:
                    raise ValueError(
                        "SQLite backup source and destination must be distinct, non-alias paths"
                    )
                raise FileExistsError(
                    f"Refusing to overwrite existing SQLite backup: {destination}"
                )

            source_uri = f"file:{source}?mode=ro"
            owns_source_connection = source_connection is None
            descriptors_before_source_open = _file_descriptor_identities()
            source_context = (
                sqlite3.connect(source_uri, uri=True, timeout=30.0)
                if owns_source_connection
                else nullcontext(source_connection)
            )
            with source_context as active_source_connection:
                if owns_source_connection:
                    _attest_sqlite_connection(
                        active_source_connection,
                        descriptors_before_source_open,
                        source_identity,
                        label="source",
                    )
                source_integrity_rows = active_source_connection.execute("PRAGMA integrity_check").fetchall()
                source_integrity_check = [str(row[0]) for row in source_integrity_rows]
                source_foreign_key_violations = len(
                    active_source_connection.execute("PRAGMA foreign_key_check").fetchall()
                )
                if source_integrity_check != ["ok"] or source_foreign_key_violations:
                    raise RuntimeError("SQLite source failed integrity validation")
                source_snapshot = {
                    "schema": "bms.sqlite-backup-source-preimage.v1",
                    "database_identity_sha256": database_identity_sha256
                    or hashlib.sha256(
                        f"{source}:{source_identity[0]}:{source_identity[1]}".encode("utf-8")
                    ).hexdigest(),
                    "source_size_bytes": int(source_stat_before.st_size),
                    "source_sha256": _sha256_descriptor(source_descriptor),
                    "page_size": int(active_source_connection.execute("PRAGMA page_size").fetchone()[0]),
                    "page_count": int(active_source_connection.execute("PRAGMA page_count").fetchone()[0]),
                    "schema_version": int(active_source_connection.execute("PRAGMA schema_version").fetchone()[0]),
                    "data_version": int(active_source_connection.execute("PRAGMA data_version").fetchone()[0]),
                    "integrity_check": source_integrity_check[0],
                    "foreign_key_violations": source_foreign_key_violations,
                }
                with sqlite3.connect(staging, timeout=30.0) as backup_connection:
                    active_source_connection.backup(backup_connection)
                    backup_connection.commit()

            source_stat_after = os.fstat(source_descriptor)
            source_path_after = _lstat(source)
            if (
                source_path_after is None
                or _identity(source_path_after) != source_identity
                or _identity(source_stat_after) != source_identity
                or source_stat_after.st_size != source_stat_before.st_size
                or source_stat_after.st_mtime_ns != source_stat_before.st_mtime_ns
            ):
                raise RuntimeError("SQLite source identity or metadata changed during backup")
        finally:
            os.close(source_descriptor)

        staging_stat = os.fstat(staging_descriptor)
        if _identity(staging_stat) != staging_identity:
            raise RuntimeError("SQLite backup staging identity changed during backup")
        os.fchmod(staging_descriptor, 0o600)

        descriptors_before_check_open = _file_descriptor_identities()
        with sqlite3.connect(
            f"file:{staging}?mode=ro", uri=True, timeout=30.0
        ) as check_connection:
            _attest_sqlite_connection(
                check_connection,
                descriptors_before_check_open,
                staging_identity,
                label="staging validation",
            )
            integrity_rows = check_connection.execute("PRAGMA integrity_check").fetchall()
            integrity_check = [str(row[0]) for row in integrity_rows]
            foreign_key_violations = len(
                check_connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        if integrity_check != ["ok"]:
            raise RuntimeError(f"SQLite backup failed integrity_check: {integrity_check}")
        if foreign_key_violations:
            raise RuntimeError(
                "SQLite backup failed foreign key validation: "
                f"{foreign_key_violations} violation(s)"
            )

        expected_size = os.fstat(staging_descriptor).st_size
        expected_digest = _sha256_descriptor(staging_descriptor)

        # Hard-link publication is atomic and refuses to overwrite a path that
        # appeared after the initial check. Mark the attempt first because a
        # wrapper or OS error can be raised after the link side effect occurred.
        publication_attempted = True
        os.link(staging, destination)

        published_stat = _lstat(destination)
        if published_stat is None or _identity(published_stat) != staging_identity:
            raise RuntimeError("SQLite backup publication identity does not match staging")
        destination_descriptor = _open_pinned_readonly(
            destination,
            staging_identity,
            label="publication",
        )
        try:
            os.fchmod(destination_descriptor, 0o600)
            published_size = os.fstat(destination_descriptor).st_size
            published_digest = _sha256_descriptor(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        final_published_stat = _lstat(destination)
        if (
            final_published_stat is None
            or _identity(final_published_stat) != staging_identity
            or published_size != expected_size
            or published_digest != expected_digest
            or stat.S_IMODE(final_published_stat.st_mode) != 0o600
        ):
            raise RuntimeError("SQLite backup publication identity or content verification failed")

        staging.unlink()
        return SQLiteBackupReport(
            source_path=source,
            backup_path=destination,
            size_bytes=published_size,
            sha256=published_digest,
            integrity_check=integrity_check[0],
            foreign_key_violations=foreign_key_violations,
            source_snapshot=source_snapshot,
        )
    except Exception as error:
        cleanup_errors: list[str] = []
        if publication_attempted:
            published_stat = _lstat(destination)
            if published_stat is not None and _identity(published_stat) == staging_identity:
                try:
                    destination.unlink()
                except OSError as cleanup_error:
                    cleanup_errors.append(f"published destination: {cleanup_error}")
        try:
            staging.unlink(missing_ok=True)
        except OSError as cleanup_error:
            cleanup_errors.append(f"staging file: {cleanup_error}")
        if cleanup_errors:
            raise RuntimeError(
                "SQLite backup failed and cleanup was incomplete: " + "; ".join(cleanup_errors)
            ) from error
        raise
    finally:
        os.close(staging_descriptor)
