"""Approved, server-owned NGS comparison panels and launch snapshots.

Neither browser storage nor an upload can enter this authority boundary.  The
only panel source is an immutable MolBio molecular revision supplied by the
restricted seeding endpoint below.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import ApprovedNgsComparisonPanel, NgsComparisonPanelReceipt
from paths import get_inputs_dir
from services.molbio_ngs_receipts import _snapshot_sequence
from services.molbio_persistence import sha256_text

PANEL_RECEIPT_TTL = timedelta(minutes=15)
PANEL_SCHEMA = "bms.ngs.approved-comparison-panel.v1"
VALID_ROLES = frozenset({"host", "plasmid_decoy"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_directory(destination: Path, build: Any) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".approved_panel.", dir=destination.parent))
    try:
        build(temporary)
        if destination.exists():
            raise ValueError("approved panel version already exists")
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _approved_label(panel_id: str, version: int) -> str:
    return f"Approved comparison panel {panel_id} v{version}"


async def seed_approved_panel(
    session: AsyncSession, *, entries: list[dict[str, Any]], actor: str, label: str | None = None
) -> ApprovedNgsComparisonPanel:
    """Create one immutable APPROVED v1 panel from already-saved revisions.

    ``label`` is intentionally ignored: displayed labels are server generated,
    and the provenance retains the immutable revision identity instead.
    """
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("approved panel seeding requires an administrative actor")
    if not isinstance(entries, list) or not entries:
        raise ValueError("approved panel requires at least one immutable revision entry")
    panel_id = str(uuid.uuid4())
    frozen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict) or str(raw.get("role") or "") not in VALID_ROLES:
            raise ValueError("approved panel roles must be exactly host or plasmid_decoy")
        revision = raw.get("revision")
        sequence_id = str(raw.get("sequence_id") or "").strip()
        if not sequence_id or revision is None:
            raise ValueError("approved panel entries require a saved sequence and immutable revision")
        revision_id = str(getattr(revision, "id", "") or "").strip()
        if not revision_id or revision_id in seen:
            raise ValueError("approved panel entries require unique immutable revision ids")
        sequence = _snapshot_sequence(revision)
        frozen.append({
            "id": revision_id,
            "role": str(raw["role"]),
            "label": f"{raw['role']}:{revision_id}",
            "sequence_id": sequence_id,
            "revision_id": revision_id,
            "revision_sha256": str(getattr(revision, "content_sha256", "")),
            "sequence": sequence,
        })
        seen.add(revision_id)
    target = get_inputs_dir() / "approved_ngs_comparison_panels" / panel_id / "v1"

    def build(root: Path) -> None:
        manifest_entries = []
        for item in frozen:
            filename = f"{item['id']}.fasta"
            fasta = f">panel__{item['id']}\n{item['sequence']}\n".encode("ascii")
            (root / filename).write_bytes(fasta)
            manifest_entries.append({key: item[key] for key in ("id", "role", "label", "sequence_id", "revision_id", "revision_sha256")}
                                  | {"fasta_filename": filename, "fasta_sha256": hashlib.sha256(fasta).hexdigest()})
        manifest = {"schema": PANEL_SCHEMA, "panel_id": panel_id, "version": 1, "status": "APPROVED", "entries": manifest_entries}
        (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    _atomic_directory(target, build)
    manifest_path = target / "manifest.json"
    panel = ApprovedNgsComparisonPanel(
        id=panel_id, version=1, status="APPROVED", label=_approved_label(panel_id, 1),
        manifest_path=str(manifest_path.resolve()), snapshot_sha256=_sha256(manifest_path),
        provenance={"source": "immutable_molecular_revisions", "entries": [{k: x[k] for k in ("sequence_id", "revision_id", "revision_sha256", "role")} for x in frozen]},
        created_at=datetime.utcnow(), created_by=actor.strip(),
    )
    session.add(panel)
    await session.flush()
    return panel


def _validated_panel_manifest(panel: ApprovedNgsComparisonPanel) -> dict[str, Any]:
    if panel.status != "APPROVED":
        raise ValueError("comparison panel is not approved")
    root = get_inputs_dir().resolve()
    path = Path(panel.manifest_path)
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or _sha256(resolved) != panel.snapshot_sha256:
        raise ValueError("approved comparison panel manifest is unavailable or digest-mismatched")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("approved comparison panel is outside server data root") from exc
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("schema") != PANEL_SCHEMA or payload.get("status") != "APPROVED" or payload.get("panel_id") != panel.id or payload.get("version") != panel.version:
        raise ValueError("approved comparison panel manifest is malformed")
    for entry in payload.get("entries", []):
        if not isinstance(entry, dict) or entry.get("role") not in VALID_ROLES:
            raise ValueError("approved comparison panel contains an invalid role")
        filename, digest = entry.get("fasta_filename"), entry.get("fasta_sha256")
        if not isinstance(filename, str) or Path(filename).name != filename or not isinstance(digest, str):
            raise ValueError("approved comparison panel manifest contains an unsafe FASTA reference")
        fasta = resolved.parent / filename
        if fasta.is_symlink() or not fasta.is_file() or _sha256(fasta) != digest:
            raise ValueError("approved comparison panel FASTA is unavailable or digest-mismatched")
    return payload


async def list_approved_panels(session: AsyncSession) -> list[ApprovedNgsComparisonPanel]:
    return (await session.execute(select(ApprovedNgsComparisonPanel).where(ApprovedNgsComparisonPanel.status == "APPROVED"))).scalars().all()


async def issue_comparison_panel_receipt(session: AsyncSession, *, panel_id: str, expected_receipt_id: str) -> NgsComparisonPanelReceipt:
    panel = await session.get(ApprovedNgsComparisonPanel, panel_id)
    if panel is None:
        raise ValueError("approved comparison panel was not found")
    _validated_panel_manifest(panel)
    now = datetime.utcnow()
    receipt = NgsComparisonPanelReceipt(
        id=str(uuid.uuid4()), panel_id=panel.id, panel_version=panel.version, panel_snapshot_path=panel.manifest_path,
        panel_snapshot_sha256=panel.snapshot_sha256, expected_receipt_id=expected_receipt_id,
        expires_at=now + PANEL_RECEIPT_TTL, created_at=now,
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def consume_comparison_panel_receipt(session: AsyncSession, *, receipt_id: str, expected_receipt_id: str) -> NgsComparisonPanelReceipt:
    receipt = (await session.execute(select(NgsComparisonPanelReceipt).where(NgsComparisonPanelReceipt.id == receipt_id))).scalar_one_or_none()
    if receipt is None or receipt.consumed_at is not None or receipt.expires_at <= datetime.utcnow() or receipt.expected_receipt_id != expected_receipt_id:
        raise ValueError("comparison panel receipt is missing, expired, already used, or not bound to the expected receipt")
    panel = await session.get(ApprovedNgsComparisonPanel, receipt.panel_id)
    if panel is None or panel.version != receipt.panel_version or panel.snapshot_sha256 != receipt.panel_snapshot_sha256:
        raise ValueError("comparison panel receipt no longer matches an approved panel")
    _validated_panel_manifest(panel)
    return receipt


def materialize_comparison_launch(*, expected_fasta: str, expected_sha256: str, panel_receipt: NgsComparisonPanelReceipt) -> dict[str, str]:
    """Atomically copy all comparison inputs to a unique server task input root."""
    expected = Path(expected_fasta).resolve(strict=True)
    if _sha256(expected) != expected_sha256:
        raise ValueError("expected reference receipt changed before comparison materialization")
    manifest = Path(panel_receipt.panel_snapshot_path).resolve(strict=True)
    root = get_inputs_dir() / "ngs_comparison_task_inputs" / str(uuid.uuid4())

    def build(stage: Path) -> None:
        shutil.copy2(expected, stage / "expected_reference.fasta")
        source = json.loads(manifest.read_text(encoding="utf-8"))
        snapshot_entries = []
        for entry in source["entries"]:
            filename = str(entry["fasta_filename"])
            shutil.copy2(manifest.parent / filename, stage / filename)
            snapshot_entries.append({"id": entry["id"], "label": entry["label"], "role": entry["role"], "fasta_path": filename, "fasta_sha256": entry["fasta_sha256"]})
        snapshot = {"schema": "bms.ngs.comparison-panel.v1", "panel_id": source["panel_id"], "panel_version": source["version"], "panel_manifest_sha256": panel_receipt.panel_snapshot_sha256, "entries": snapshot_entries}
        (stage / "comparison_panel_snapshot.json").write_text(json.dumps(snapshot, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    _atomic_directory(root, build)
    return {"input_root": str(root.resolve()), "reference_fasta": str((root / "expected_reference.fasta").resolve()), "comparison_panel_snapshot": str((root / "comparison_panel_snapshot.json").resolve())}
