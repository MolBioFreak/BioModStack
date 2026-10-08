"""SQLite online-backup helpers shared by owned persistence subsystems."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SQLiteBackupReport:
    source_path: Path
    backup_path: Path
    size_bytes: int
    sha256: str
    quick_check: str
    foreign_key_violations: int


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


def backup_sqlite_database(source_path: Path, backup_path: Path) -> SQLiteBackupReport:
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
            descriptors_before_source_open = _file_descriptor_identities()
            with sqlite3.connect(source_uri, uri=True, timeout=30.0) as source_connection:
                _attest_sqlite_connection(
                    source_connection,
                    descriptors_before_source_open,
                    source_identity,
                    label="source",
                )
                with sqlite3.connect(staging, timeout=30.0) as backup_connection:
                    source_connection.backup(backup_connection)
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
            quick_check = str(check_connection.execute("PRAGMA quick_check").fetchone()[0])
            foreign_key_violations = len(
                check_connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        if quick_check.casefold() != "ok":
            raise RuntimeError(f"SQLite backup failed quick_check: {quick_check}")
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
            quick_check=quick_check,
            foreign_key_violations=foreign_key_violations,
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
