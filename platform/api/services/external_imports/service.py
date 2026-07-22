from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, ExternalResultImport, Job
from paths import get_data_root
from services.result_contracts import REVIEW_CONTRACT_VERSION, build_review_artifact_manifest

from .boltz_api import (
    PROVIDER_ID,
    BoltzImportError,
    normalize_boltz_api_run,
    preview_boltz_api_run,
)


logger = logging.getLogger(__name__)
_ACTIVE_STATES = {"discovered", "validating", "staging", "normalizing", "committing"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _provider_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _normalized_root(data_root: Path, resource: str, provider_job_id: str, fingerprint: str) -> Path:
    return (
        data_root
        / "external_imports"
        / PROVIDER_ID
        / resource.replace(":", "-")
        / provider_job_id
        / fingerprint
    ).resolve()


async def queue_external_import(
    session: AsyncSession,
    *,
    source_dir: Path,
    preview_fingerprint: str,
    dataset_name: str,
    job_name: str | None,
    bms_job_id: str | None = None,
) -> ExternalResultImport:
    preview = await asyncio.to_thread(preview_boltz_api_run, source_dir)
    if not preview.importable:
        raise BoltzImportError(preview.error_code or "RESOURCE_UNSUPPORTED", "; ".join(preview.errors))
    if preview.source_fingerprint != preview_fingerprint:
        raise BoltzImportError("SOURCE_CHANGED_AFTER_PREVIEW", "source bytes no longer match the preview")
    if not dataset_name.strip():
        raise BoltzImportError("RUN_METADATA_INVALID", "dataset_name is required")

    query = select(ExternalResultImport).where(
        ExternalResultImport.provider_id == preview.provider,
        ExternalResultImport.resource_type == preview.resource_type,
        ExternalResultImport.provider_job_id == preview.provider_job_id,
    )
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing is not None:
        if existing.source_fingerprint != preview.source_fingerprint:
            raise BoltzImportError(
                "IMPORT_IDENTITY_CONFLICT",
                "this provider job ID is already registered with different immutable evidence",
            )
        if bms_job_id and existing.bms_job_id not in {None, bms_job_id}:
            raise BoltzImportError(
                "IMPORT_IDENTITY_CONFLICT",
                "provider result is already attached to a different BioModStack job",
            )
        if bms_job_id and existing.bms_job_id is None:
            existing.bms_job_id = bms_job_id
            await session.commit()
            await session.refresh(existing)
        return existing

    record = ExternalResultImport(
        id=str(uuid.uuid4()),
        provider_id=preview.provider,
        resource_type=preview.resource_type,
        provider_job_id=preview.provider_job_id,
        state="discovered",
        source_path=str(source_dir.expanduser().resolve()),
        source_fingerprint=preview.source_fingerprint,
        run_metadata_sha256=preview.run_metadata_sha256,
        archive_sha256=preview.archive_sha256,
        dataset_name=dataset_name.strip()[:255],
        job_name=(job_name or "").strip()[:255] or None,
        bms_job_id=bms_job_id,
        provider_metadata=preview.provider_metadata,
        schema_version=1,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (await session.execute(query)).scalar_one_or_none()
        if existing is None:
            raise
        if existing.source_fingerprint != preview.source_fingerprint:
            raise BoltzImportError("IMPORT_IDENTITY_CONFLICT", "concurrent import registered different evidence")
        return existing
    await session.refresh(record)
    return record


async def retry_external_import(session: AsyncSession, *, import_id: str) -> ExternalResultImport:
    record = await session.get(ExternalResultImport, import_id)
    if record is None:
        raise BoltzImportError("IMPORT_NOT_FOUND", "external import was not found")
    if record.state == "completed":
        return record
    if record.state != "failed":
        raise BoltzImportError("IMPORT_NOT_RETRYABLE", f"import state {record.state} is not retryable")
    current = preview_boltz_api_run(Path(record.source_path))
    if current.source_fingerprint != record.source_fingerprint:
        raise BoltzImportError("IMPORT_IDENTITY_CONFLICT", "retry source differs from registered evidence")
    record.state = "discovered"
    record.failure_code = None
    record.failure_message = None
    record.updated_at = _utcnow()
    await session.commit()
    await session.refresh(record)
    return record


def _role_map(entities: list[dict[str, Any]]) -> dict[str, Any]:
    chains: dict[str, dict[str, Any]] = {}
    for entity in entities:
        for chain_id in entity["chain_ids"]:
            chains[chain_id] = {
                "entity_index": entity["entity_index"],
                "molecule_type": entity["molecule_type"],
                "role": entity["molecule_type"],
            }
    return {"has_binder": False, "chains": chains, "entities": entities}


async def _claim_import(
    session: AsyncSession,
    import_id: str,
) -> tuple[ExternalResultImport | None, bool]:
    result = await session.execute(
        update(ExternalResultImport)
        .where(
            ExternalResultImport.id == import_id,
            ExternalResultImport.state == "discovered",
        )
        .values(state="validating", updated_at=_utcnow())
    )
    await session.commit()
    record = await session.get(ExternalResultImport, import_id)
    return record, result.rowcount == 1


async def process_external_import(
    session: AsyncSession,
    *,
    import_id: str,
    data_root: Path | None = None,
) -> ExternalResultImport:
    record, claimed = await _claim_import(session, import_id)
    if record is None:
        raise BoltzImportError("IMPORT_NOT_FOUND", "external import was not found")
    if not claimed:
        return record

    try:
        linked_job = await session.get(Job, record.bms_job_id) if record.bms_job_id else None
        if linked_job is not None and str(linked_job.status or "").lower() == "cancelled":
            raise BoltzImportError("BMS_JOB_CANCELLED", "linked BioModStack job was cancelled")
        source_dir = Path(record.source_path).expanduser().resolve()
        preview = await asyncio.to_thread(preview_boltz_api_run, source_dir)
        if preview.source_fingerprint != record.source_fingerprint:
            raise BoltzImportError("SOURCE_CHANGED_AFTER_PREVIEW", "source changed after import was queued")
        if preview.run_metadata_sha256 != record.run_metadata_sha256 or preview.archive_sha256 != record.archive_sha256:
            raise BoltzImportError("SOURCE_CHANGED_AFTER_PREVIEW", "source evidence hashes changed")

        record.state = "staging"
        record.updated_at = _utcnow()
        await session.commit()

        selected_data_root = (data_root or get_data_root()).expanduser().resolve()
        manifest = await asyncio.to_thread(
            normalize_boltz_api_run,
            source_dir,
            selected_data_root,
            preview,
        )
        final_root = _normalized_root(
            selected_data_root,
            record.resource_type,
            record.provider_job_id,
            record.source_fingerprint,
        )
        manifest_path = final_root / "normalized" / "import-manifest.json"
        if not manifest_path.is_file():
            raise BoltzImportError("IMPORT_COMMIT_FAILED", "normalized manifest was not published")

        record.state = "committing"
        record.normalized_manifest_path = str(manifest_path)
        record.updated_at = _utcnow()
        await session.commit()

        provider = manifest["provider"]
        existing_job = await session.get(Job, record.bms_job_id) if record.bms_job_id else None
        if existing_job is not None:
            await session.refresh(existing_job)
            if str(existing_job.status or "").lower() == "cancelled":
                raise BoltzImportError("BMS_JOB_CANCELLED", "linked BioModStack job was cancelled")
        job_id = existing_job.id if existing_job is not None else str(uuid.uuid4())
        completed_at = _provider_timestamp(provider.get("completed_at")) or _utcnow()
        imported_params = {
            "external_provider": record.provider_id,
            "external_resource": record.resource_type,
            "entities": manifest["input"]["entities"],
            "num_samples": manifest["input"]["num_samples"],
        }
        imported_provenance = {
            "external_import": {
                "schema": manifest["schema"],
                "provider": record.provider_id,
                "resource_type": record.resource_type,
                "provider_job_id": record.provider_job_id,
                "source_fingerprint": record.source_fingerprint,
                "run_metadata_sha256": record.run_metadata_sha256,
                "archive_sha256": record.archive_sha256,
                "model": provider.get("model"),
                "provider_version": provider.get("provider_version"),
                "workspace_id": provider.get("workspace_id"),
                "data_deleted_at": provider.get("data_deleted_at"),
            }
        }
        if existing_job is None:
            job = Job(
                id=job_id,
                name=record.job_name or f"Boltz API {record.provider_job_id}",
                created_at=_provider_timestamp(provider.get("created_at")) or record.created_at,
            )
            session.add(job)
        else:
            job = existing_job
            prior_params = dict(job.params or {})
            imported_params = {**prior_params, **imported_params, "provider_state": "completed"}
        job.status = "completed"
        job.queue_status = "completed"
        job.model_id = "boltz2"
        job.mode = "external_import"
        job.params = imported_params
        job.started_at = _provider_timestamp(provider.get("started_at")) or job.started_at
        job.completed_at = completed_at
        job.output_dir = str(final_root)
        job.stage_family = "validation"
        job.stage_mode = "boltz_api_structure_import"
        job.selection_source_type = "external_api"
        job.selection_dataset_name = record.dataset_name
        job.source_selection_manifest_path = str(manifest_path)
        job.source_selection_count = len(manifest["samples"])
        job.provenance = imported_provenance
        job.completed_stages = ["external_import"]
        job.stage_outputs = {"external_import": [item["path"] for item in manifest["artifacts"]]}
        job.error_message = None

        roles = _role_map(manifest["input"]["entities"])
        for sample in manifest["samples"]:
            canonical = sample["canonical_metrics"]
            raw = sample["provider_metrics"]
            structure_path = (final_root / sample["structure_path"]).resolve()
            pae_path = (final_root / sample["aligned_error_path"]).resolve()
            structure_path.relative_to(final_root)
            pae_path.relative_to(final_root)
            design = Design(
                id=str(uuid.uuid4()),
                job_id=job_id,
                name=f"{record.provider_job_id}_{sample['sample_id']}",
                pdb_path=str(structure_path),
                stage_family="validation",
                stage_mode="boltz_api_structure_import",
                source_stage_family="external_api",
                source_stage_mode=record.resource_type,
                artifact_class="imported_structure",
                artifact_schema_version=1,
                review_profile_id="structure_prediction_v1",
                review_contract_version=REVIEW_CONTRACT_VERSION,
                review_contract_source="producer",
                review_role_map=roles,
                plddt_overall=canonical.get("plddt_overall"),
                ptm=canonical.get("ptm"),
                conf_score=canonical.get("conf_score"),
                iptm=None,
                aligned_error_path=str(pae_path),
                aligned_error_format="boltz_pae_npz",
                aligned_error_key="pae",
                confidence_metrics={"boltz_api": {"raw": raw}},
                provenance={
                    "external_import": {
                        "provider": record.provider_id,
                        "provider_job_id": record.provider_job_id,
                        "resource_type": record.resource_type,
                        "sample_id": sample["sample_id"],
                        "rank": sample["rank"],
                        "is_best_sample": sample["is_best_sample"],
                        "source_fingerprint": record.source_fingerprint,
                    }
                },
            )
            design.review_artifact_manifest = build_review_artifact_manifest(design)
            session.add(design)

        record.bms_job_id = job_id
        record.state = "completed"
        record.imported_at = _utcnow()
        record.updated_at = record.imported_at
        record.failure_code = None
        record.failure_message = None
        await session.commit()
        await session.refresh(record)
        return record
    except Exception as exc:
        await session.rollback()
        failed = await session.get(ExternalResultImport, import_id)
        if failed is not None and failed.state != "completed":
            failed.state = "failed"
            failed.failure_code = exc.code if isinstance(exc, BoltzImportError) else "IMPORT_COMMIT_FAILED"
            failed.failure_message = str(exc)[:2000]
            failed.updated_at = _utcnow()
            await session.commit()
        raise


async def recover_external_imports(session: AsyncSession) -> list[str]:
    """Make interrupted imports explicitly retryable without duplicating publication."""
    rows = list(
        (
            await session.execute(
                select(ExternalResultImport).where(ExternalResultImport.state.in_(_ACTIVE_STATES - {"discovered"}))
            )
        ).scalars()
    )
    recovered: list[str] = []
    for record in rows:
        linked_job = await session.get(Job, record.bms_job_id) if record.bms_job_id else None
        linked_design = None
        if linked_job is not None:
            linked_design = (
                await session.execute(select(Design).where(Design.job_id == linked_job.id).limit(1))
            ).scalar_one_or_none()
        if (
            record.state == "committing"
            and linked_job is not None
            and linked_job.status == "completed"
            and linked_design is not None
        ):
            record.state = "completed"
            record.imported_at = record.imported_at or _utcnow()
        else:
            record.state = "failed"
            record.failure_code = "IMPORT_INTERRUPTED"
            record.failure_message = "API process stopped during import; explicit retry is required"
        record.updated_at = _utcnow()
        recovered.append(record.id)
    if recovered:
        await session.commit()
    return recovered
