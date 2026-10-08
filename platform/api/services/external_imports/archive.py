from __future__ import annotations

import hashlib
import os
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


MAX_MEMBERS = int(os.getenv("BMS_EXTERNAL_IMPORT_MAX_MEMBERS", "10000"))
MAX_MEMBER_BYTES = int(os.getenv("BMS_EXTERNAL_IMPORT_MAX_MEMBER_BYTES", str(2 * 1024**3)))
MAX_EXPANDED_BYTES = int(os.getenv("BMS_EXTERNAL_IMPORT_MAX_EXPANDED_BYTES", str(20 * 1024**3)))
MAX_JSON_BYTES = int(os.getenv("BMS_EXTERNAL_IMPORT_MAX_JSON_BYTES", str(100 * 1024**2)))

_ALLOWED_SAB_MEMBER = re.compile(
    r"^prediction/(metrics\.json|sample_[0-9]+_predicted_structure\.cif|sample_[0-9]+_pae\.npz)$"
)


class ArchiveSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveInventory:
    archive_sha256: str
    members: dict[str, int]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_member(member: tarfile.TarInfo, *, seen: set[str]) -> str | None:
    name = member.name.replace("\\", "/")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ArchiveSafetyError(f"unsafe archive path: {member.name}")
    normalized = str(pure)
    if normalized in seen:
        raise ArchiveSafetyError(f"duplicate archive path: {normalized}")
    seen.add(normalized)
    if member.isdir():
        return None
    if not member.isfile() or member.issym() or member.islnk() or member.isdev() or member.isfifo():
        raise ArchiveSafetyError(f"unsupported archive member type: {normalized}")
    if member.size < 0 or member.size > MAX_MEMBER_BYTES:
        raise ArchiveSafetyError(f"archive member exceeds size limit: {normalized}")
    if not _ALLOWED_SAB_MEMBER.fullmatch(normalized):
        raise ArchiveSafetyError(f"unexpected archive member: {normalized}")
    return normalized


def inspect_sab_archive(path: Path) -> ArchiveInventory:
    if not path.is_file() or path.is_symlink():
        raise ArchiveSafetyError("archive is missing or is not a regular file")
    members: dict[str, int] = {}
    expanded = 0
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for index, member in enumerate(archive):
                if index >= MAX_MEMBERS:
                    raise ArchiveSafetyError("archive member count exceeds limit")
                normalized = _validate_member(member, seen=seen)
                if normalized is None:
                    continue
                expanded += int(member.size)
                if expanded > MAX_EXPANDED_BYTES:
                    raise ArchiveSafetyError("archive expanded size exceeds limit")
                members[normalized] = int(member.size)
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveSafetyError(f"invalid tar archive: {exc}") from exc
    return ArchiveInventory(archive_sha256=sha256_file(path), members=members)


def read_member_bytes(path: Path, member_name: str, *, max_bytes: int = MAX_MEMBER_BYTES) -> bytes:
    try:
        with tarfile.open(path, "r:gz") as archive:
            member = archive.getmember(member_name)
            if not member.isfile() or member.size > max_bytes:
                raise ArchiveSafetyError(f"invalid or oversized archive member: {member_name}")
            source = archive.extractfile(member)
            if source is None:
                raise ArchiveSafetyError(f"archive member is unreadable: {member_name}")
            payload = source.read(max_bytes + 1)
            if len(payload) > max_bytes or len(payload) != member.size:
                raise ArchiveSafetyError(f"archive member size mismatch: {member_name}")
            return payload
    except (KeyError, tarfile.TarError, OSError) as exc:
        raise ArchiveSafetyError(f"cannot read archive member {member_name}: {exc}") from exc


def copy_members(path: Path, member_names: Iterable[str], destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=False)
    written: dict[str, Path] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member_name in member_names:
            member = archive.getmember(member_name)
            source = archive.extractfile(member)
            if source is None:
                raise ArchiveSafetyError(f"archive member is unreadable: {member_name}")
            target = (destination / PurePosixPath(member_name).name).resolve()
            target.relative_to(destination.resolve())
            with target.open("xb") as output:
                remaining = member.size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ArchiveSafetyError(f"truncated archive member: {member_name}")
                    output.write(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            written[member_name] = target
    return written
