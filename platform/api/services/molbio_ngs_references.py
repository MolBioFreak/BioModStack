"""Managed immutable FASTA authority for MolBio/NGS reference revisions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_models import MolecularDocument, MolecularRevision
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSGlobalBinding,
    MolBioNGSReferenceArtifact,
    MolBioNGSReferenceResource,
    MolBioNGSReferenceRevision,
)
from molbio_ngs_services import (
    DomainStateNotFound,
    RevisionConflict,
    StateIntegrityError,
    StateValidationError,
    _audit_and_outbox,
    _canonical,
    _complete_idempotency,
    _digest,
    _id,
    _now,
    _reserve_idempotency,
    get_state_revision,
    verify_state_revision_integrity,
)
from paths import get_molbio_ngs_reference_root
from paths import get_inputs_dir
from services.molbio_authority import SERVER_OWNED_ACTOR
from services.molbio_ngs_member_receipts import (
    ExternalMemberReceipt,
    build_external_member_receipt,
)
from services.molbio_ngs_receipts import _snapshot_sequence

REFERENCE_SCHEMA = "bms.molbio-ngs.reference-revision.v1"
REFERENCE_SCHEMA_NAME = "bms.molbio-ngs.reference-revision"
REFERENCE_SCHEMA_VERSION = "1"
_FASTA_MEDIA_TYPE = "text/x-fasta; charset=us-ascii"
_VALID_SYMBOLS = frozenset("ACGTRYSWKMBDHVN-U")
_NAME = re.compile(r"^[^\s>]+$")


@dataclass(frozen=True)
class ManagedReferenceLaunch:
    """Server-only resolved authority for one exact managed-reference launch."""

    global_domain_experiment_id: str
    molbio_ngs_state_revision_id: str
    ngs_reference_id: str
    ngs_reference_revision_id: str
    ngs_reference_artifact_id: str
    state_membership_receipt_id: str
    reference_fasta_path: Path
    selected_reference_sha256: str
    expected_reference_fasta_sha256: str
    expected_reference_fasta_size_bytes: int
    launch_snapshot_sha256: str
    launch_snapshot_size_bytes: int


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_and_canonicalize_fasta(raw_fasta: bytes) -> tuple[bytes, list[dict[str, Any]], str | None]:
    if not isinstance(raw_fasta, bytes) or not raw_fasta:
        raise StateValidationError("FASTA bytes are required")
    try:
        text = raw_fasta.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StateValidationError("FASTA must be ASCII") from exc
    records: list[tuple[str, str]] = []
    name: str | None = None
    parts: list[str] = []
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None:
                sequence = "".join(parts).replace(" ", "").upper()
                if not sequence:
                    raise StateValidationError(f"FASTA record {name!r} is empty")
                records.append((name, sequence))
            header = line[1:].strip()
            name = header.split(maxsplit=1)[0] if header else ""
            if not name or not _NAME.fullmatch(name):
                raise StateValidationError(f"FASTA header on line {number} is invalid")
            parts = []
        else:
            if name is None:
                raise StateValidationError("FASTA sequence occurs before the first header")
            parts.append("".join(line.split()))
    if name is not None:
        sequence = "".join(parts).upper()
        if not sequence:
            raise StateValidationError(f"FASTA record {name!r} is empty")
        records.append((name, sequence))
    if not records:
        raise StateValidationError("FASTA contains no records")
    names = [item[0] for item in records]
    if len(names) != len(set(names)):
        raise StateValidationError("FASTA contig names must be unique")
    for record_name, sequence in records:
        invalid = sorted(set(sequence) - _VALID_SYMBOLS)
        if invalid:
            raise StateValidationError(
                f"FASTA record {record_name!r} contains invalid nucleotide symbols: {''.join(invalid)}"
            )
    records.sort(key=lambda item: item[0])
    chunks: list[str] = []
    manifest: list[dict[str, Any]] = []
    for record_name, sequence in records:
        chunks.append(f">{record_name}\n")
        chunks.extend(f"{sequence[index:index + 80]}\n" for index in range(0, len(sequence), 80))
        manifest.append(
            {
                "name": record_name,
                "length": len(sequence),
                "sequence_sha256": _sha256(sequence.encode("ascii")),
            }
        )
    canonical = "".join(chunks).encode("ascii")
    normalized = manifest[0]["sequence_sha256"] if len(manifest) == 1 else None
    return canonical, manifest, normalized


def _managed_path(relative: str) -> Path:
    root = get_molbio_ngs_reference_root().resolve()
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise StateIntegrityError("reference artifact path is not canonical and relative")
    unresolved = root / relative_path
    current = root
    for part in relative_path.parts:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise StateIntegrityError("reference artifact path contains a symlink")
    candidate = unresolved.resolve()
    if root != candidate and root not in candidate.parents:
        raise StateIntegrityError("reference artifact escaped the managed root")
    return candidate


def _descriptor_constants() -> tuple[int, int]:
    """Return required no-follow flags or one controlled fail-closed error."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    if (
        not isinstance(nofollow, int)
        or not isinstance(directory, int)
        or not isinstance(cloexec, int)
    ):
        raise StateIntegrityError(
            "managed reference launch snapshots require no-follow descriptor support"
        )
    return os.O_RDONLY | nofollow | cloexec, directory


def _open_absolute_directory_nofollow(path: Path) -> int:
    file_flags, directory_flag = _descriptor_constants()
    current_fd = os.open(os.sep, file_flags | directory_flag)
    try:
        for component in path.absolute().parts[1:]:
            child_fd = os.open(
                component, file_flags | directory_flag, dir_fd=current_fd
            )
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _snapshot_managed_reference(
    source: Path, *, expected_sha256: str, expected_size_bytes: int
) -> tuple[Path, str, int]:
    """Copy exact source bytes through descriptors into a unique launch root."""

    file_flags, _directory_flag = _descriptor_constants()
    source_parent_fd = _open_absolute_directory_nofollow(source.parent)
    source_fd = -1
    destination_fd = -1
    try:
        source_fd = os.open(source.name, file_flags, dir_fd=source_parent_fd)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise StateIntegrityError("managed reference source is not a regular file")

        snapshot_root = (
            get_inputs_dir()
            / "molbio_ngs_managed_launch_snapshots"
            / str(uuid.uuid4())
        )
        snapshot_root.mkdir(parents=True, exist_ok=False)
        destination_parent_fd = _open_absolute_directory_nofollow(snapshot_root)
        try:
            create_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC")
            )
            destination_fd = os.open(
                "reference.fasta", create_flags, 0o600, dir_fd=destination_parent_fd
            )
            digest = hashlib.sha256()
            copied = 0
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise StateIntegrityError(
                            "short write while snapshotting managed reference"
                        )
                    view = view[written:]
                digest.update(chunk)
                copied += len(chunk)
            after = os.fstat(source_fd)
            if (
                (source_stat.st_dev, source_stat.st_ino, source_stat.st_size)
                != (after.st_dev, after.st_ino, after.st_size)
                or copied != expected_size_bytes
                or digest.hexdigest() != expected_sha256
            ):
                raise StateIntegrityError(
                    "managed reference bytes drifted while creating launch snapshot"
                )
            os.fsync(destination_fd)
            os.fchmod(destination_fd, 0o400)
            os.fsync(destination_fd)
        finally:
            os.close(destination_parent_fd)
        return snapshot_root / "reference.fasta", digest.hexdigest(), copied
    except (AttributeError, NotImplementedError) as exc:
        raise StateIntegrityError(
            "managed reference launch snapshots require supported descriptor primitives"
        ) from exc
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)
        os.close(source_parent_fd)


def _write_managed_bytes(relative: str, content: bytes) -> None:
    destination = _managed_path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not stat.S_ISREG(os.lstat(destination).st_mode):
            raise StateIntegrityError("managed reference artifact must be a regular file")
        existing = destination.read_bytes()
        if existing != content:
            raise StateIntegrityError("managed reference artifact path already has different bytes")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=".reference-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _reference_payload(
    *,
    reference_id: str,
    revision_number: int,
    parent_revision_id: str | None,
    canonical_fasta: bytes,
    manifest: list[dict[str, Any]],
    normalized_sequence_sha256: str | None,
    molecule_type: str,
    topology: str,
    coordinate_contract: str,
    source_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    if molecule_type not in {"dna", "rna"}:
        raise StateValidationError("molecule_type must be dna or rna")
    if topology not in {"linear", "circular", "mixed", "unknown"}:
        raise StateValidationError("topology is invalid")
    if not coordinate_contract.strip() or len(coordinate_contract) > 128:
        raise StateValidationError("coordinate_contract is required")
    manifest_sha256 = _digest(_canonical(manifest))
    return {
        "schema": REFERENCE_SCHEMA,
        "reference_id": reference_id,
        "revision_number": revision_number,
        "parent_revision_id": parent_revision_id,
        "head_generation": revision_number,
        "canonical_fasta": {
            "sha256": _sha256(canonical_fasta),
            "size_bytes": len(canonical_fasta),
            "media_type": _FASTA_MEDIA_TYPE,
        },
        "contigs": manifest,
        "contig_manifest_sha256": manifest_sha256,
        "normalized_sequence_sha256": normalized_sequence_sha256,
        "molecule_type": molecule_type,
        "topology": topology,
        "coordinate_contract": coordinate_contract,
        "source_provenance": dict(source_provenance),
    }


async def create_reference(
    session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    name: str,
    raw_fasta: bytes,
    molecule_type: str,
    topology: str,
    coordinate_contract: str,
    source_provenance: Mapping[str, Any],
    idempotency_key: str,
    created_by: str | None = None,
) -> tuple[MolBioNGSReferenceResource, MolBioNGSReferenceRevision]:
    if not name.strip() or len(name) > 255 or not idempotency_key.strip():
        raise StateValidationError("reference name and idempotency_key are required")
    state = await session.get(MolBioNGSDomainState, global_domain_experiment_id)
    binding = (
        await session.get(MolBioNGSGlobalBinding, state.current_binding_revision_id)
        if state is not None else None
    )
    if state is None or binding is None or binding.binding_state != "acknowledged":
        raise DomainStateNotFound(
            "acknowledged MolBio/NGS Domain Experiment state was not found"
        )
    canonical_fasta, manifest, normalized = parse_and_canonicalize_fasta(raw_fasta)
    reference_id = _id("molbio_ngs_reference")
    payload = _reference_payload(
        reference_id=reference_id,
        revision_number=1,
        parent_revision_id=None,
        canonical_fasta=canonical_fasta,
        manifest=manifest,
        normalized_sequence_sha256=normalized,
        molecule_type=molecule_type,
        topology=topology,
        coordinate_contract=coordinate_contract,
        source_provenance=source_provenance,
    )
    request_payload = dict(payload)
    request_payload.pop("reference_id")
    request_sha256 = _digest(_canonical({
        "domain": global_domain_experiment_id, "name": name.strip(),
        "payload": request_payload, "created_by": SERVER_OWNED_ACTOR,
    }))
    scope = f"create-reference:{global_domain_experiment_id}"
    replay_id = await _reserve_idempotency(
        session, scope=scope, idempotency_key=idempotency_key,
        request_sha256=request_sha256, result_resource_id=reference_id,
    )
    if replay_id is not None:
        resource = await get_reference_resource(session, replay_id, global_domain_experiment_id)
        revision = await get_reference_revision(session, replay_id, resource.current_revision_id or "")
        return resource, revision
    now = _now()
    resource = MolBioNGSReferenceResource(
        id=reference_id, global_domain_experiment_id=global_domain_experiment_id,
        name=name.strip(), current_revision_id=None, head_generation=0,
        archived_at=None, created_at=now, updated_at=now,
    )
    session.add(resource)
    await session.flush([resource])
    revision = await _store_revision(
        session, resource=resource, canonical_fasta=canonical_fasta,
        manifest=manifest, normalized_sequence_sha256=normalized,
        molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract, source_provenance=source_provenance,
        revision_number=1, parent_revision_id=None, created_by=SERVER_OWNED_ACTOR,
    )
    resource.current_revision_id = revision.id
    resource.head_generation = 1
    resource.updated_at = now
    await _audit_and_outbox(
        session, domain_id=global_domain_experiment_id, resource_id=reference_id,
        state_revision_id=None, event_type="molbio_ngs.reference.created", generation=1,
        payload={"schema": "bms.molbio-ngs.reference-created.v1", "reference_id": reference_id,
                 "reference_revision_id": revision.id,
                 "reference_revision_number": revision.revision_number,
                 "canonical_fasta_sha256": revision.canonical_fasta_sha256},
        created_by=SERVER_OWNED_ACTOR,
    )
    await session.flush()
    await _complete_idempotency(
        session, scope=scope, idempotency_key=idempotency_key,
        request_sha256=request_sha256, result_resource_id=resource.id,
        response={"reference_id": resource.id, "reference_revision_id": revision.id},
    )
    await session.flush()
    return resource, revision


async def _store_revision(
    session: AsyncSession, *, resource: MolBioNGSReferenceResource,
    canonical_fasta: bytes, manifest: list[dict[str, Any]],
    normalized_sequence_sha256: str | None, molecule_type: str, topology: str,
    coordinate_contract: str, source_provenance: Mapping[str, Any],
    revision_number: int, parent_revision_id: str | None, created_by: str | None,
    revision_id: str | None = None,
) -> MolBioNGSReferenceRevision:
    payload = _reference_payload(
        reference_id=resource.id, revision_number=revision_number,
        parent_revision_id=parent_revision_id, canonical_fasta=canonical_fasta,
        manifest=manifest, normalized_sequence_sha256=normalized_sequence_sha256,
        molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract, source_provenance=source_provenance,
    )
    fasta_sha256 = payload["canonical_fasta"]["sha256"]
    artifact_id = _id("molbio_ngs_reference_artifact")
    relative = f"sha256/{fasta_sha256[:2]}/{fasta_sha256}.fasta"
    _write_managed_bytes(relative, canonical_fasta)
    now = _now()
    existing_artifact = (
        await session.execute(
            select(MolBioNGSReferenceArtifact).where(
                MolBioNGSReferenceArtifact.managed_relative_path == relative
            )
        )
    ).scalar_one_or_none()
    if existing_artifact is not None:
        if existing_artifact.reference_id != resource.id:
            relative = f"references/{resource.id}/{fasta_sha256}.fasta"
            _write_managed_bytes(relative, canonical_fasta)
        else:
            artifact_id = existing_artifact.id
    artifact = await session.get(MolBioNGSReferenceArtifact, artifact_id)
    if artifact is None:
        artifact = MolBioNGSReferenceArtifact(
            id=artifact_id, reference_id=resource.id, managed_relative_path=relative,
            media_type=_FASTA_MEDIA_TYPE, sha256=fasta_sha256,
            size_bytes=len(canonical_fasta), created_at=now,
        )
        session.add(artifact)
        await session.flush([artifact])
    canonical_payload = _canonical(payload)
    revision = MolBioNGSReferenceRevision(
        id=revision_id or _id("molbio_ngs_reference_revision"), reference_id=resource.id,
        global_domain_experiment_id=resource.global_domain_experiment_id,
        revision_number=revision_number, parent_revision_id=parent_revision_id,
        artifact_id=artifact.id, schema_name=REFERENCE_SCHEMA_NAME,
        schema_version=REFERENCE_SCHEMA_VERSION, canonical_payload=canonical_payload,
        payload_sha256=_digest(canonical_payload), canonical_fasta_sha256=fasta_sha256,
        canonical_fasta_size_bytes=len(canonical_fasta),
        contig_manifest_sha256=payload["contig_manifest_sha256"],
        normalized_sequence_sha256=normalized_sequence_sha256,
        molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract,
        source_provenance=_canonical(source_provenance), created_at=now,
        created_by=SERVER_OWNED_ACTOR,
    )
    session.add(revision)
    await session.flush([revision])
    return revision


async def append_reference_revision(
    session: AsyncSession, *, reference_id: str, raw_fasta: bytes,
    molecule_type: str, topology: str, coordinate_contract: str,
    source_provenance: Mapping[str, Any], expected_head_generation: int,
    parent_revision_id: str | None, idempotency_key: str,
    created_by: str | None = None,
) -> MolBioNGSReferenceRevision:
    if not idempotency_key.strip():
        raise StateValidationError("idempotency_key is required")
    canonical_fasta, manifest, normalized = parse_and_canonicalize_fasta(raw_fasta)
    resource = await get_reference_resource(session, reference_id)
    payload = _reference_payload(
        reference_id=reference_id, revision_number=expected_head_generation + 1,
        parent_revision_id=parent_revision_id, canonical_fasta=canonical_fasta,
        manifest=manifest, normalized_sequence_sha256=normalized,
        molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract, source_provenance=source_provenance,
    )
    request_sha256 = _digest(_canonical({"reference_id": reference_id, "payload": payload,
                                         "expected_head_generation": expected_head_generation,
                                         "created_by": SERVER_OWNED_ACTOR}))
    scope = f"append-reference-revision:{reference_id}"
    new_revision_id = _id("molbio_ngs_reference_revision")
    replay = await _reserve_idempotency(
        session, scope=scope, idempotency_key=idempotency_key,
        request_sha256=request_sha256, result_resource_id=new_revision_id,
    )
    if replay is not None:
        return await get_reference_revision(session, reference_id, replay)
    if resource.archived_at is not None:
        raise RevisionConflict("archived reference cannot receive a revision")
    if resource.head_generation != expected_head_generation or resource.current_revision_id != parent_revision_id:
        raise RevisionConflict("reference head generation or parent revision changed")
    revision = await _store_revision(
        session, resource=resource, canonical_fasta=canonical_fasta,
        manifest=manifest, normalized_sequence_sha256=normalized,
        molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract, source_provenance=source_provenance,
        revision_number=expected_head_generation + 1,
        parent_revision_id=parent_revision_id, created_by=SERVER_OWNED_ACTOR,
        revision_id=new_revision_id,
    )
    result = await session.execute(
        update(MolBioNGSReferenceResource)
        .where(MolBioNGSReferenceResource.id == reference_id,
               MolBioNGSReferenceResource.head_generation == expected_head_generation,
               MolBioNGSReferenceResource.current_revision_id == parent_revision_id)
        .values(current_revision_id=revision.id, head_generation=expected_head_generation + 1,
                updated_at=_now())
    )
    if result.rowcount != 1:
        raise RevisionConflict("reference head changed during revision save")
    await _audit_and_outbox(
        session,
        domain_id=resource.global_domain_experiment_id,
        resource_id=reference_id,
        state_revision_id=None,
        event_type="molbio_ngs.reference.revision_saved",
        generation=expected_head_generation + 1,
        payload={
            "schema": "bms.molbio-ngs.reference-revision-saved.v1",
            "reference_id": reference_id,
            "reference_revision_id": revision.id,
            "reference_revision_number": revision.revision_number,
            "canonical_fasta_sha256": revision.canonical_fasta_sha256,
        },
        created_by=SERVER_OWNED_ACTOR,
    )
    await session.flush()
    await _complete_idempotency(
        session, scope=scope, idempotency_key=idempotency_key,
        request_sha256=request_sha256, result_resource_id=revision.id,
        response={"reference_revision_id": revision.id},
    )
    await session.flush()
    return revision


async def get_reference_resource(
    session: AsyncSession, reference_id: str,
    global_domain_experiment_id: str | None = None,
) -> MolBioNGSReferenceResource:
    resource = await session.get(MolBioNGSReferenceResource, reference_id)
    if resource is None or (
        global_domain_experiment_id is not None
        and resource.global_domain_experiment_id != global_domain_experiment_id
    ):
        raise DomainStateNotFound("MolBio/NGS reference was not found")
    return resource


async def list_references(
    session: AsyncSession, global_domain_experiment_id: str | None = None,
) -> list[MolBioNGSReferenceResource]:
    query = select(MolBioNGSReferenceResource)
    if global_domain_experiment_id is not None:
        query = query.where(
            MolBioNGSReferenceResource.global_domain_experiment_id == global_domain_experiment_id
        )
    return list((await session.execute(query.order_by(
        MolBioNGSReferenceResource.created_at, MolBioNGSReferenceResource.id
    ))).scalars())


async def get_reference_revision(
    session: AsyncSession, reference_id: str, revision_id: str,
) -> MolBioNGSReferenceRevision:
    revision = await session.get(MolBioNGSReferenceRevision, revision_id)
    if revision is None or revision.reference_id != reference_id:
        raise DomainStateNotFound("MolBio/NGS reference revision was not found")
    try:
        payload = json.loads(revision.canonical_payload)
    except json.JSONDecodeError as exc:
        raise StateIntegrityError("reference revision authority is invalid") from exc
    required_keys = {
        "schema", "reference_id", "revision_number", "parent_revision_id",
        "head_generation", "canonical_fasta", "contigs",
        "contig_manifest_sha256", "normalized_sequence_sha256", "molecule_type",
        "topology", "coordinate_contract", "source_provenance",
    }
    fasta = payload.get("canonical_fasta") if isinstance(payload, dict) else None
    contigs = payload.get("contigs") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != required_keys
        or not isinstance(fasta, dict)
        or set(fasta) != {"sha256", "size_bytes", "media_type"}
        or not isinstance(contigs, list)
        or not contigs
        or any(
            not isinstance(contig, dict)
            or set(contig) != {"name", "length", "sequence_sha256"}
            for contig in contigs
        )
        or _canonical(payload) != revision.canonical_payload
        or _digest(revision.canonical_payload) != revision.payload_sha256
        or revision.schema_name != REFERENCE_SCHEMA_NAME
        or revision.schema_version != REFERENCE_SCHEMA_VERSION
        or payload["schema"] != REFERENCE_SCHEMA
        or payload["reference_id"] != revision.reference_id
        or payload["revision_number"] != revision.revision_number
        or payload["head_generation"] != revision.revision_number
        or payload["parent_revision_id"] != revision.parent_revision_id
        or fasta["sha256"] != revision.canonical_fasta_sha256
        or fasta["size_bytes"] != revision.canonical_fasta_size_bytes
        or fasta["media_type"] != _FASTA_MEDIA_TYPE
        or _digest(_canonical(contigs)) != revision.contig_manifest_sha256
        or payload["contig_manifest_sha256"] != revision.contig_manifest_sha256
        or payload["normalized_sequence_sha256"] != revision.normalized_sequence_sha256
        or payload["molecule_type"] != revision.molecule_type
        or payload["topology"] != revision.topology
        or payload["coordinate_contract"] != revision.coordinate_contract
        or _canonical(payload["source_provenance"]) != revision.source_provenance
    ):
        raise StateIntegrityError("reference revision authority is invalid")
    return revision


async def list_reference_revisions(
    session: AsyncSession, reference_id: str,
) -> list[MolBioNGSReferenceRevision]:
    await get_reference_resource(session, reference_id)
    revisions = list((await session.execute(
        select(MolBioNGSReferenceRevision)
        .where(MolBioNGSReferenceRevision.reference_id == reference_id)
        .order_by(MolBioNGSReferenceRevision.revision_number.desc())
    )).scalars())
    for revision in revisions:
        await get_reference_revision(session, reference_id, revision.id)
    return revisions


async def read_reference_artifact_bytes(
    session: AsyncSession, revision: MolBioNGSReferenceRevision,
) -> bytes:
    checked = await get_reference_revision(session, revision.reference_id, revision.id)
    artifact = await session.get(MolBioNGSReferenceArtifact, checked.artifact_id)
    if artifact is None or artifact.reference_id != revision.reference_id:
        raise StateIntegrityError("reference artifact authority is missing")
    artifact_path = _managed_path(artifact.managed_relative_path)
    if not stat.S_ISREG(os.lstat(artifact_path).st_mode):
        raise StateIntegrityError("managed reference artifact must be a regular file")
    content = artifact_path.read_bytes()
    if len(content) != artifact.size_bytes or _sha256(content) != artifact.sha256:
        raise StateIntegrityError("managed reference artifact bytes do not match authority")
    return content


async def resolve_managed_reference_for_launch(
    session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    molbio_ngs_state_revision_id: str,
    ngs_reference_revision_id: str,
) -> ManagedReferenceLaunch:
    """Resolve one state-member reference to a verified server-managed FASTA path."""

    state_revision = await get_state_revision(
        session,
        global_domain_experiment_id,
        molbio_ngs_state_revision_id,
    )
    _, membership = await verify_state_revision_integrity(session, state_revision)

    revision = await session.get(MolBioNGSReferenceRevision, ngs_reference_revision_id)
    if (
        revision is None
        or revision.global_domain_experiment_id != global_domain_experiment_id
    ):
        raise DomainStateNotFound("MolBio/NGS reference revision was not found")
    revision = await get_reference_revision(
        session,
        revision.reference_id,
        revision.id,
    )
    resource = await get_reference_resource(
        session,
        revision.reference_id,
        global_domain_experiment_id,
    )
    if resource.archived_at is not None:
        raise StateValidationError("archived MolBio/NGS references cannot be used for a new launch")

    artifact = await session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
    if (
        artifact is None
        or artifact.reference_id != revision.reference_id
        or artifact.sha256 != revision.canonical_fasta_sha256
        or artifact.size_bytes != revision.canonical_fasta_size_bytes
        or artifact.media_type != _FASTA_MEDIA_TYPE
    ):
        raise StateIntegrityError("reference revision and artifact authority diverge")
    try:
        content = await read_reference_artifact_bytes(session, revision)
    except OSError as exc:
        raise StateIntegrityError("managed reference artifact is unavailable") from exc
    if (
        len(content) != revision.canonical_fasta_size_bytes
        or _sha256(content) != revision.canonical_fasta_sha256
    ):
        raise StateIntegrityError("managed reference bytes do not match the exact revision")

    exact_members = [
        member
        for member in membership
        if member.get("role") == "ngs_reference"
        and member.get("source_store_id") == "molbio-ngs-domain"
        and member.get("entity_kind") == "ngs_reference_revision"
        and member.get("entity_id") == revision.id
        and member.get("source_generation_or_revision") == str(revision.revision_number)
        and member.get("content_digest") == revision.canonical_fasta_sha256
        and member.get("source_schema") == REFERENCE_SCHEMA
        and member.get("availability") == "available"
        and member.get("reopen_destination") == {
            "surface": "molbio-ngs-reference-revision",
            "params": {
                "reference_id": revision.reference_id,
                "revision_id": revision.id,
            },
        }
    ]
    if len(exact_members) != 1:
        raise StateValidationError(
            "state revision must contain exactly one matching immutable NGS reference member"
        )

    source_path = _managed_path(artifact.managed_relative_path)
    try:
        snapshot_path, snapshot_sha256, snapshot_size_bytes = (
            _snapshot_managed_reference(
                source_path,
                expected_sha256=revision.canonical_fasta_sha256,
                expected_size_bytes=revision.canonical_fasta_size_bytes,
            )
        )
    except OSError as exc:
        raise StateIntegrityError(
            "managed reference launch snapshot could not be created"
        ) from exc

    return ManagedReferenceLaunch(
        global_domain_experiment_id=global_domain_experiment_id,
        molbio_ngs_state_revision_id=state_revision.id,
        ngs_reference_id=revision.reference_id,
        ngs_reference_revision_id=revision.id,
        ngs_reference_artifact_id=artifact.id,
        state_membership_receipt_id=str(exact_members[0]["receipt_id"]),
        reference_fasta_path=snapshot_path,
        selected_reference_sha256=revision.canonical_fasta_sha256,
        expected_reference_fasta_sha256=revision.canonical_fasta_sha256,
        expected_reference_fasta_size_bytes=revision.canonical_fasta_size_bytes,
        launch_snapshot_sha256=snapshot_sha256,
        launch_snapshot_size_bytes=snapshot_size_bytes,
    )


async def archive_reference(
    session: AsyncSession, *, reference_id: str, expected_head_generation: int,
    idempotency_key: str, archived_by: str | None = None,
) -> MolBioNGSReferenceResource:
    if not idempotency_key.strip():
        raise StateValidationError("idempotency_key is required")
    resource = await get_reference_resource(session, reference_id)
    request_sha256 = _digest(_canonical({"reference_id": reference_id,
        "expected_head_generation": expected_head_generation, "archived_by": SERVER_OWNED_ACTOR}))
    scope = f"archive-reference:{reference_id}"
    replay = await _reserve_idempotency(
        session, scope=scope, idempotency_key=idempotency_key,
        request_sha256=request_sha256, result_resource_id=reference_id,
    )
    if replay is not None:
        return await get_reference_resource(session, replay)
    if resource.head_generation != expected_head_generation:
        raise RevisionConflict("reference head generation changed")
    now = _now()
    result = await session.execute(
        update(MolBioNGSReferenceResource)
        .where(MolBioNGSReferenceResource.id == reference_id,
               MolBioNGSReferenceResource.head_generation == expected_head_generation,
               MolBioNGSReferenceResource.archived_at.is_(None))
        .values(archived_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        raise RevisionConflict("reference archive state changed")
    await _audit_and_outbox(
        session,
        domain_id=resource.global_domain_experiment_id,
        resource_id=reference_id,
        state_revision_id=None,
        event_type="molbio_ngs.reference.archived",
        generation=expected_head_generation,
        payload={
            "schema": "bms.molbio-ngs.reference-archived.v1",
            "reference_id": reference_id,
            "head_generation": expected_head_generation,
            "archived_at": now,
        },
        created_by=SERVER_OWNED_ACTOR,
    )
    await session.flush()
    await _complete_idempotency(
        session, scope=scope, idempotency_key=idempotency_key,
        request_sha256=request_sha256, result_resource_id=reference_id,
        response={"reference_id": reference_id, "archived_at": now},
    )
    await session.flush()
    return await get_reference_resource(session, reference_id)


async def create_reference_from_molbio_revision(
    session: AsyncSession, molbio_session: AsyncSession, *,
    global_domain_experiment_id: str, sequence_id: str, molecular_revision_id: str,
    name: str, molecule_type: str, topology: str, coordinate_contract: str,
    idempotency_key: str, created_by: str | None = None,
) -> tuple[MolBioNGSReferenceResource, MolBioNGSReferenceRevision]:
    document = await molbio_session.get(MolecularDocument, sequence_id)
    revision = await molbio_session.get(MolecularRevision, molecular_revision_id)
    if document is None or revision is None or revision.document_id != sequence_id:
        raise DomainStateNotFound("exact MolBio molecular revision was not found")
    sequence = _snapshot_sequence(revision)
    record_name = re.sub(r"[^A-Za-z0-9_.:-]+", "_", name.strip()).strip("_") or "reference"
    raw_fasta = f">{record_name}\n{sequence}\n".encode("ascii")
    return await create_reference(
        session, global_domain_experiment_id=global_domain_experiment_id, name=name,
        raw_fasta=raw_fasta, molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract,
        source_provenance={"kind": "molbio_molecular_revision", "sequence_id": sequence_id,
                           "molecular_revision_id": molecular_revision_id,
                           "molecular_revision_sha256": revision.content_sha256},
        idempotency_key=idempotency_key, created_by=SERVER_OWNED_ACTOR,
    )


async def import_browser_entry(
    session: AsyncSession, *, global_domain_experiment_id: str,
    entry: Mapping[str, Any], name: str, molecule_type: str, topology: str,
    coordinate_contract: str, idempotency_key: str,
    created_by: str | None = None,
) -> tuple[MolBioNGSReferenceResource, MolBioNGSReferenceRevision]:
    # Browser fields are hints only. Paths are never accepted as identities or persisted.
    source = entry.get("source")
    if source == "path" or entry.get("path"):
        raise StateValidationError("browser path imports fail closed; upload inline FASTA bytes")
    inline = entry.get("fasta")
    if not isinstance(inline, str) or not inline:
        raise StateValidationError("legacy browser entry must provide inline fasta")
    return await create_reference(
        session, global_domain_experiment_id=global_domain_experiment_id, name=name,
        raw_fasta=inline.encode("utf-8"), molecule_type=molecule_type, topology=topology,
        coordinate_contract=coordinate_contract,
        source_provenance={"kind": "legacy_browser_entry", "hint_id": entry.get("id"),
                           "hint_name": entry.get("name")},
        idempotency_key=idempotency_key, created_by=SERVER_OWNED_ACTOR,
    )


async def resolve_ngs_reference_revision_receipt(
    session: AsyncSession, *, global_domain_experiment_id: str,
    reference_id: str, revision_id: str,
) -> ExternalMemberReceipt:
    resource = await get_reference_resource(
        session, reference_id, global_domain_experiment_id
    )
    revision = await get_reference_revision(session, resource.id, revision_id)
    if revision.global_domain_experiment_id != global_domain_experiment_id:
        raise DomainStateNotFound(
            "managed reference revision is not owned by this Domain Experiment"
        )
    await read_reference_artifact_bytes(session, revision)
    return build_external_member_receipt(
        source_store_id="molbio-ngs-domain", entity_kind="ngs_reference_revision",
        entity_id=revision.id, source_generation_or_revision=revision.revision_number,
        content_digest=revision.canonical_fasta_sha256,
        source_schema=REFERENCE_SCHEMA, availability="available",
        reopen_destination={"surface": "molbio-ngs-reference-revision",
                            "params": {
                                "global_domain_experiment_id": global_domain_experiment_id,
                                "reference_id": reference_id,
                                "revision_id": revision.id,
                            }},
    )
