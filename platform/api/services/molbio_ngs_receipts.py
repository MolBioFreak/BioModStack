"""Server-mediated immutable MolBio expected-reference receipts."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import MolBioNgsReceipt
from paths import get_inputs_dir
from services.molbio_persistence import sha256_text
from services.nucleotide_validation import canonicalize_nucleotide_sequence

RECEIPT_TTL = timedelta(minutes=15)
MOLBIO_NGS_RECEIPT_SCHEMA = "bms.molbio.ngs-receipt.v2"


def serialize_molbio_ngs_receipt(receipt: MolBioNgsReceipt) -> dict[str, Any]:
    """Serialize the public one-time receipt without exposing its snapshot path."""

    return {
        "schema": MOLBIO_NGS_RECEIPT_SCHEMA,
        "receipt_id": receipt.id,
        "sequence_id": receipt.sequence_id,
        "revision_id": receipt.revision_id,
        "revision_sha256": receipt.revision_sha256,
        "reference_snapshot_sha256": receipt.reference_snapshot_sha256,
        "expires_at": receipt.expires_at.isoformat() + "Z",
        "one_time": True,
    }


def build_molbio_revision_binding(receipt: MolBioNgsReceipt) -> dict[str, str]:
    """Build durable job provenance only from a consumed server receipt."""

    return {
        "sequence_id": receipt.sequence_id,
        "revision_id": receipt.revision_id,
        "revision_sha256": receipt.revision_sha256,
        "reference_snapshot_sha256": receipt.reference_snapshot_sha256,
        "receipt_id": receipt.id,
        "receipt_schema": MOLBIO_NGS_RECEIPT_SCHEMA,
        "binding_source": "server_consumed_receipt",
    }


def _snapshot_sequence(revision: Any) -> str:
    snapshot = getattr(revision, "snapshot", None)
    sequence_type = str((snapshot or {}).get("sequence_type") or "dna").lower()
    sequence = canonicalize_nucleotide_sequence(str((snapshot or {}).get("sequence") or ""), sequence_type, allow_empty=False)
    if sha256_text(sequence) != getattr(revision, "content_sha256", None):
        raise ValueError("immutable molecular revision content does not match its digest")
    return sequence


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".expected_reference.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


async def issue_molbio_ngs_receipt(session: AsyncSession, *, sequence_id: str, revision: Any) -> MolBioNgsReceipt:
    sequence = _snapshot_sequence(revision)
    receipt_id = str(uuid.uuid4())
    fasta = f">molbio_revision_{revision.id}\n{sequence}\n".encode("ascii")
    digest = hashlib.sha256(fasta).hexdigest()
    path = get_inputs_dir() / "molbio_ngs_receipts" / receipt_id / "expected_reference.fasta"
    _atomic_write(path, fasta)
    now = datetime.utcnow()
    receipt = MolBioNgsReceipt(
        id=receipt_id, sequence_id=sequence_id, revision_id=revision.id,
        revision_sha256=revision.content_sha256, reference_snapshot_path=str(path.resolve()),
        reference_snapshot_sha256=digest, expires_at=now + RECEIPT_TTL, created_at=now,
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def validate_molbio_ngs_receipt(
    session: AsyncSession, *, receipt_id: str
) -> MolBioNgsReceipt:
    """Resolve exact live receipt authority without consuming its one-time claim."""

    now = datetime.utcnow()
    receipt = (
        await session.execute(
            select(MolBioNgsReceipt).where(
                MolBioNgsReceipt.id == receipt_id,
                MolBioNgsReceipt.consumed_at.is_(None),
                MolBioNgsReceipt.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise ValueError("MolBio NGS receipt is missing, expired, or already used")
    path = Path(receipt.reference_snapshot_path)
    try:
        allowed = get_inputs_dir().resolve()
        resolved = path.resolve(strict=True)
        resolved.relative_to(allowed)
        if (
            resolved.is_symlink()
            or not resolved.is_file()
            or hashlib.sha256(resolved.read_bytes()).hexdigest()
            != receipt.reference_snapshot_sha256
        ):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise ValueError(
            "MolBio NGS receipt reference snapshot is unavailable or digest-mismatched"
        ) from exc
    return receipt


async def consume_molbio_ngs_receipt(session: AsyncSession, *, receipt_id: str) -> MolBioNgsReceipt:
    await validate_molbio_ngs_receipt(session, receipt_id=receipt_id)
    now = datetime.utcnow()
    claimed = await session.execute(
        update(MolBioNgsReceipt)
        .where(
            MolBioNgsReceipt.id == receipt_id,
            MolBioNgsReceipt.consumed_at.is_(None),
            MolBioNgsReceipt.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise ValueError("MolBio NGS receipt is missing, expired, or already used")
    receipt = (
        await session.execute(
            select(MolBioNgsReceipt)
            .where(MolBioNgsReceipt.id == receipt_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return receipt
