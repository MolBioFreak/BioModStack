"""Server-mediated immutable MolBio expected-reference receipts."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import MolBioNgsReceipt
from paths import get_inputs_dir
from services.molbio_persistence import sha256_text
from services.nucleotide_validation import canonicalize_nucleotide_sequence

RECEIPT_TTL = timedelta(minutes=15)


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


async def consume_molbio_ngs_receipt(session: AsyncSession, *, receipt_id: str) -> MolBioNgsReceipt:
    receipt = (await session.execute(select(MolBioNgsReceipt).where(MolBioNgsReceipt.id == receipt_id))).scalar_one_or_none()
    if receipt is None or receipt.consumed_at is not None or receipt.expires_at <= datetime.utcnow():
        raise ValueError("MolBio NGS receipt is missing, expired, or already used")
    path = Path(receipt.reference_snapshot_path)
    try:
        allowed = get_inputs_dir().resolve()
        resolved = path.resolve(strict=True)
        resolved.relative_to(allowed)
        if resolved.is_symlink() or not resolved.is_file() or hashlib.sha256(resolved.read_bytes()).hexdigest() != receipt.reference_snapshot_sha256:
            raise ValueError
    except (OSError, ValueError) as exc:
        raise ValueError("MolBio NGS receipt reference snapshot is unavailable or digest-mismatched") from exc
    return receipt
