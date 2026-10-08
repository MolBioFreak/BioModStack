"""Independent catalog/preview lifecycle. Builders own bytes; this module owns CAS.

No GET hydrates requests. No lifecycle operation writes scientific Job state.
Request helpers are flush-only for the winning scientific terminal transaction.
Preview intent may wait without a claim; its execution identity is bound only
when its exact catalog is ready. Retrying it never mutates that catalog.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, NgsAlignmentDerivedProduct as Product
from services.ngs_alignment_presentation import (
    PresentationAlreadyReady, PresentationClaimLost, PresentationSourceStale,
)

ProductKind = Literal["catalog", "preview"]
ERROR_CODES = frozenset({"source_invalid", "resource_limit", "cancelled",
                        "infrastructure_failed", "publication_failed", "integrity_mismatch"})
CATALOG_SEMANTICS = {
    "catalog_schema": "bms.ngs.read-catalog.v2",
    "locator_schema": "bms.ngs.read-record-locators.v2",
    "fingerprint_policy": "bms.ngs.alignment-record-fingerprint.v1",
    "exact_record_sizing": "bms.ngs.exact-tag-bam-sizing.v1",
    "overlay_admission_metadata": "standard-writer-bgzf.v2",
}


def canonical_bytes(value: Any) -> bytes:
    def check(item: Any) -> None:
        if item is None or type(item) in {str, int, bool}:
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("derived identity contains a non-canonical value")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def identity_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _digest(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("derived artifact digest is invalid")
    return value


def source_identity(*, job_id: str, session_id: str, mode: str,
                    reference: dict[str, Any], source_manifest_sha256: str,
                    source_artifact_set_sha256: str, package_artifact_set_sha256: str,
                    alignment_pair_sha256: str, alignment_sha256: str,
                    alignment_size_bytes: int, alignment_index_sha256: str,
                    alignment_index_size_bytes: int) -> dict[str, Any]:
    """Build v2 source identity from accepted evidence, never preview policy."""
    if not job_id or not session_id or mode not in {"primary", "dimer_candidates"}:
        raise ValueError("derived source owner is invalid")
    if set(reference) != {"contig", "length_bp", "topology", "normalized_sequence_sha256", "fasta_sha256", "fai_sha256"}:
        raise ValueError("immutable reference identity is incomplete")
    if (not isinstance(reference["contig"], str) or not reference["contig"]
            or type(reference["length_bp"]) is not int or reference["length_bp"] < 1
            or reference["topology"] not in {"linear", "circular"}):
        raise ValueError("immutable reference identity is invalid")
    for field in ("normalized_sequence_sha256", "fasta_sha256", "fai_sha256"):
        _digest(reference[field])
    for value in (source_manifest_sha256, source_artifact_set_sha256,
                  package_artifact_set_sha256, alignment_pair_sha256,
                  alignment_sha256, alignment_index_sha256):
        _digest(value)
    if any(type(value) is not int or value < 1 for value in (alignment_size_bytes, alignment_index_size_bytes)):
        raise ValueError("derived source artifact length is invalid")
    return {
        "schema": "bms.ngs.alignment-presentation-source.v2",
        "job_id": job_id, "session_id": session_id, "mode": mode,
        "reference": dict(reference), "source_manifest_sha256": source_manifest_sha256,
        "source_artifact_set_sha256": source_artifact_set_sha256,
        "package_artifact_set_sha256": package_artifact_set_sha256,
        "alignment_pair_sha256": alignment_pair_sha256,
        "alignment_sha256": alignment_sha256, "alignment_size_bytes": alignment_size_bytes,
        "alignment_index_sha256": alignment_index_sha256,
        "alignment_index_size_bytes": alignment_index_size_bytes,
    }


def validate_source(source: dict[str, Any]) -> None:
    fields = dict(source)
    if fields.pop("schema", None) != "bms.ngs.alignment-presentation-source.v2":
        raise ValueError("unsupported derived source schema")
    if source_identity(**fields) != source:
        raise ValueError("derived source identity mismatch")


def catalog_contract(source: dict[str, Any]) -> dict[str, Any]:
    validate_source(source)
    return {"schema": "bms.ngs.read-catalog-request.v2",
            "source_authority_sha256": identity_sha256(source), **CATALOG_SEMANTICS}


def catalog_request_id(source: dict[str, Any]) -> str:
    return "ngs-catalog-" + identity_sha256(catalog_contract(source))


async def request_catalog(session: AsyncSession, source: dict[str, Any]) -> Product:
    contract = catalog_contract(source)
    return await _request(session, source=source, product="catalog", contract=contract,
                          request_sha256=identity_sha256(contract), catalog=None)


async def request_preview(session: AsyncSession, catalog: Product, *, policy: dict[str, Any]) -> Product:
    """Persist intent with the complete caller-resolved v6 writer/projection policy.

    This function deliberately supplies no writer/resource defaults. Policy is
    system-owned; no router accepts an arbitrary caller-supplied policy object.
    """
    if catalog.product != "catalog":
        raise ValueError("preview requires an exact catalog request")
    if (set(policy) != {"schema", "target_reads", "max_records", "max_bytes", "projection", "header_policy", "writer_contract", "bgzf_admission_version"}
            or policy["schema"] != "bms.ngs.alignment-preview-policy.v6"
            or policy["bgzf_admission_version"] != 2
            or any(type(policy[field]) is not int or policy[field] < 1
                   for field in ("target_reads", "max_records", "max_bytes"))
            or any(not isinstance(policy[field], str) or not policy[field]
                   for field in ("projection", "header_policy"))
            or not isinstance(policy["writer_contract"], dict) or not policy["writer_contract"]):
        raise ValueError("complete v6 preview policy is required")
    contract = preview_intent(catalog.id, policy)
    return await _request(session, source=catalog.source_identity, product="preview",
                          contract=contract, request_sha256=None, catalog=catalog)


async def _request(session: AsyncSession, *, source: dict[str, Any], product: ProductKind,
                   contract: dict[str, Any], request_sha256: str | None,
                   catalog: Product | None) -> Product:
    validate_source(source)
    from services.ngs_historical_backfill import PROTECTED_JOB
    if source["job_id"] == PROTECTED_JOB:
        raise PresentationSourceStale("protected result is excluded from derived work")
    # A stale in-memory Job or worker success report is not admission authority.
    accepted = await session.scalar(select(Job.id).where(
        Job.id == source["job_id"], Job.status == "completed", Job.queue_status == "completed",
        Job.awaiting_input.is_(False),
    ))
    if accepted is None:
        raise PresentationSourceStale("derived work requires winning scientific completion")
    digest = identity_sha256(contract)
    request_id = f"ngs-{product}-" + digest
    existing = await session.get(Product, request_id)
    if existing is not None:
        if (existing.source_identity != source or existing.request_contract != contract
                or existing.product != product or existing.job_id != source["job_id"]
                or existing.session_id != source["session_id"] or existing.mode != source["mode"]
                or existing.catalog_request_id != (catalog.id if catalog else None)):
            raise PresentationSourceStale("derived intent conflicts with persisted authority")
        return existing
    row = Product(id=request_id, job_id=source["job_id"], session_id=source["session_id"],
                  mode=source["mode"], product=product, source_identity=source,
                  source_authority_sha256=identity_sha256(source), intent_sha256=digest,
                  request_contract=contract, request_sha256=request_sha256,
                  catalog_request_id=catalog.id if catalog else None,
                  state="requested", attempt_count=0, manual_retry_count=0)
    session.add(row)
    await session.flush()
    return row


async def get_catalog(session: AsyncSession, source: dict[str, Any]) -> Product | None:
    """Exact source + semantic version selection, never newest-row selection."""
    return await session.get(Product, catalog_request_id(source))


def _ready_catalog(row: Product):
    return exists(select(Product.id).where(
        Product.id == row.catalog_request_id, Product.product == "catalog",
        Product.state == "ready", Product.job_id == row.job_id,
        Product.session_id == row.session_id,
        Product.source_authority_sha256 == row.source_authority_sha256,
        Product.authority_sha256 == row.catalog_authority_sha256,
    ))


def _owned(row: Product, claim_token: str, now: datetime):
    predicates = [Product.id == row.id, Product.state == "running",
                  Product.claim_token == claim_token, Product.lease_expires_at > now,
                  Product.source_authority_sha256 == row.source_authority_sha256,
                  Product.request_sha256 == row.request_sha256]
    if row.product == "preview":
        predicates.append(_ready_catalog(row))
    return predicates


async def claim_next_product(session: AsyncSession, *, product: ProductKind,
                             claim_token: str, lease_seconds: int = 300,
                             now: datetime | None = None) -> Product | None:
    if product not in {"catalog", "preview"} or not claim_token or lease_seconds <= 0:
        raise ValueError("invalid derived claim")
    now = now or datetime.utcnow()
    predicates = [Product.product == product, Product.state == "requested",
                  Product.claim_token.is_(None), Product.lease_expires_at.is_(None)]
    if product == "preview":
        from sqlalchemy.orm import aliased
        dependency = aliased(Product)
        predicates.append(exists(select(dependency.id).where(
            dependency.id == Product.catalog_request_id, dependency.product == "catalog",
            dependency.state == "ready", dependency.job_id == Product.job_id,
            dependency.session_id == Product.session_id,
            dependency.source_authority_sha256 == Product.source_authority_sha256,
        )))
    row = await session.scalar(select(Product).where(*predicates).order_by(Product.created_at, Product.id).limit(1))
    if row is None:
        return None
    values: dict[str, Any] = {"state": "running", "claim_token": claim_token,
        "lease_expires_at": now + timedelta(seconds=lease_seconds),
        "attempt_count": Product.attempt_count + 1, "updated_at": now}
    if product == "preview":
        catalog = await session.get(Product, row.catalog_request_id, populate_existing=True)
        if catalog is None or catalog.state != "ready":
            return None
        # Request identity changes only when first binding the exact ready catalog.
        request_digest = identity_sha256({"schema": "bms.ngs.alignment-preview-request.v6",
            "catalog_authority_sha256": catalog.authority_sha256,
            "policy": row.request_contract["policy"]})
        if row.catalog_authority_sha256 not in {None, catalog.authority_sha256} or row.request_sha256 not in {None, request_digest}:
            raise PresentationSourceStale("preview catalog binding changed")
        from sqlalchemy.orm import aliased
        bound_catalog = aliased(Product)
        predicates.append(exists(select(bound_catalog.id).where(
            bound_catalog.id == row.catalog_request_id, bound_catalog.state == "ready",
            bound_catalog.authority_sha256 == catalog.authority_sha256,
        )))
        values.update(catalog_authority_sha256=catalog.authority_sha256, request_sha256=request_digest)
    result = await session.execute(update(Product).where(Product.id == row.id, *predicates)
                                   .values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    await session.refresh(row)
    return row


async def renew_product_lease(session: AsyncSession, row: Product, claim_token: str, *,
                              lease_seconds: int = 300, now: datetime | None = None) -> None:
    if lease_seconds <= 0:
        raise ValueError("lease must be positive")
    now = now or datetime.utcnow()
    result = await session.execute(update(Product).where(*_owned(row, claim_token, now)).values(
        lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now,
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("derived lease expired or ownership was lost")
    await session.commit()


async def publish_product(session: AsyncSession, row: Product, claim_token: str, *,
                          source_authority_sha256: str, authority_sha256: str,
                          manifest_sha256: str, now: datetime | None = None) -> None:
    """Publish only after the builder validates this product's sealed artifacts."""
    _digest(authority_sha256)
    _digest(manifest_sha256)
    if source_authority_sha256 != row.source_authority_sha256:
        raise PresentationSourceStale("derived source changed before publication")
    now = now or datetime.utcnow()
    result = await session.execute(update(Product).where(*_owned(row, claim_token, now)).values(
        state="ready", authority_sha256=authority_sha256, manifest_sha256=manifest_sha256,
        claim_token=None, lease_expires_at=None, error_code=None, updated_at=now,
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("derived publication ownership was lost")
    await session.commit()


async def fail_product(session: AsyncSession, row: Product, claim_token: str, *,
                       error_code: str, now: datetime | None = None) -> None:
    if error_code not in ERROR_CODES:
        raise ValueError("unsupported derived failure code")
    now = now or datetime.utcnow()
    # Failure does not depend on a healthy catalog: an invalid dependency must
    # still allow the currently owned preview to reach an explicit disposition.
    result = await session.execute(update(Product).where(
        Product.id == row.id, Product.state == "running", Product.claim_token == claim_token,
        Product.lease_expires_at > now,
    ).values(state="failed", error_code=error_code, claim_token=None, lease_expires_at=None,
             authority_sha256=None, manifest_sha256=None, updated_at=now)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("derived failure ownership was lost")
    await session.commit()


async def recover_expired_products(session: AsyncSession, *, now: datetime | None = None) -> int:
    """No auto-requeue. A later explicit retry may independently validate/adopt bytes."""
    now = now or datetime.utcnow()
    result = await session.execute(update(Product).where(
        Product.state == "running", Product.lease_expires_at <= now,
    ).values(state="failed", error_code="infrastructure_failed", claim_token=None,
             lease_expires_at=None, authority_sha256=None, manifest_sha256=None, updated_at=now)
        .execution_options(synchronize_session=False))
    await session.commit()
    return int(result.rowcount or 0)


async def retry_product(session: AsyncSession, row: Product, *,
                        current_source_authority_sha256: str, now: datetime | None = None) -> Product:
    if current_source_authority_sha256 != row.source_authority_sha256:
        raise PresentationSourceStale("derived retry source is no longer current")
    await session.refresh(row)
    if row.state == "ready":
        raise PresentationAlreadyReady("derived product is already ready")
    if row.state in {"requested", "running"}:
        return row
    predicates = [Product.id == row.id, Product.state == "failed",
                  Product.source_authority_sha256 == current_source_authority_sha256,
                  Product.claim_token.is_(None), Product.lease_expires_at.is_(None)]
    if row.product == "preview" and row.catalog_authority_sha256 is not None:
        predicates.append(_ready_catalog(row))
    result = await session.execute(update(Product).where(*predicates).values(
        state="requested", error_code=None, manual_retry_count=Product.manual_retry_count + 1,
        updated_at=now or datetime.utcnow(),
    ).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("derived retry conflicted with another transition")
    await session.commit()
    await session.refresh(row)
    return row


def product_status(row: Product | None, *, unsupported: bool = False,
                   catalog: Product | None = None) -> dict[str, Any]:
    if row is None:
        return {"state": "unavailable", "reason": "unsupported_source" if unsupported else "request_missing"}
    response = {"state": row.state, "request_id": row.id,
                "attempt_count": row.attempt_count, "manual_retry_count": row.manual_retry_count,
                "request_sha256": row.request_sha256}
    if row.product == "preview" and row.state == "requested" and (catalog is None or catalog.state != "ready"):
        response["blocked_on"] = "catalog"
    if row.state == "ready":
        response.update(authority_sha256=row.authority_sha256, manifest_sha256=row.manifest_sha256)
    if row.state == "failed":
        response.update(code=row.error_code, retryable=row.error_code != "source_invalid")
    return response


def preview_intent(catalog_id: str, policy: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "bms.ngs.alignment-preview-intent.v6",
            "catalog_request_id": catalog_id, "policy": policy}


def default_preview_request_id(catalog: Product) -> str:
    from services.ngs_alignment_product_builder import resolved_preview_policy
    return "ngs-preview-" + identity_sha256(preview_intent(catalog.id, resolved_preview_policy()))


async def request_default_preview(session: AsyncSession, catalog: Product) -> Product:
    from services.ngs_alignment_product_builder import resolved_preview_policy
    return await request_preview(session, catalog, policy=resolved_preview_policy())


def source_from_bundle(job_id, ready, bam, bai, package_artifact_set_sha256):
    """One v2 identity constructor for completion, worker and explicit retry."""
    if bam["source_manifest_sha256"] != bai["source_manifest_sha256"]:
        raise PresentationSourceStale("alignment source manifests disagree")
    return source_identity(job_id=str(job_id), session_id=ready["session_id"], mode=ready["mode"],
        reference=ready["reference"], source_manifest_sha256=bam["source_manifest_sha256"],
        source_artifact_set_sha256=ready["artifact_set_sha256"],
        package_artifact_set_sha256=package_artifact_set_sha256,
        alignment_pair_sha256=ready["alignment_pair_sha256"],
        alignment_sha256=bam["sha256"], alignment_size_bytes=bam["size_bytes"],
        alignment_index_sha256=bai["sha256"], alignment_index_size_bytes=bai["size_bytes"])


def resolve_product_source(job: Job, row: Product):
    """Observational resolution from the current accepted native result receipt.

    Existing source resolvers still define supported sessions. No synthesized
    native reference or automatic request admission from a read path.
    """
    from pathlib import Path
    from services import ngs_alignment_sessions as source_service
    from services.job_result_roots import resolve_persisted_job_result_root
    if job.status != "completed" or job.queue_status != "completed" or job.awaiting_input:
        raise PresentationSourceStale("scientific result is not accepted")
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    integrity = provenance.get("result_integrity", {})
    params = job.params if isinstance(job.params, dict) else {}
    receipts = integrity.get("alignment_presentations", [])
    catalog_id = row.id if row.product == "catalog" else row.catalog_request_id
    matches = [receipt for receipt in receipts if isinstance(receipt, dict)
               and receipt.get("request_id") == catalog_id and receipt.get("session_id") == row.session_id
               and receipt.get("source_authority_sha256") == row.source_authority_sha256]
    if len(matches) != 1:
        from services.ngs_historical_backfill import owns_request
        if matches or not owns_request(job, row):
            raise PresentationSourceStale("current result does not own this catalog request")
    root = resolve_persisted_job_result_root(job)
    from services.ngs_native_alignment_sources import is_native, sources
    if is_native(job):
        candidates = sources(job, Path(root))
        matched = [(source, inputs) for source, inputs in candidates if source["session_id"] == row.session_id]
        if len(matched) != 1 or matched[0][0] != row.source_identity:
            raise PresentationSourceStale("native alignment generation changed")
        return matched[0]
    reference_sha = integrity.get("reference_sequence_sha256") or params.get("reference_sequence_sha256")
    workflow = integrity.get("workflow_id") or params.get("ont_workflow_id")
    mode = integrity.get("input_mode") or params.get("ont_input_mode")
    package_sha = integrity.get("artifact_set_sha256")
    available = source_service.build_alignment_sessions(str(job.id),
        source_reference_sha256=reference_sha, package_artifact_set_sha256=package_sha,
        workflow_id=workflow, input_mode=mode, job_output_dir=root)
    ready = [item for item in available if item.get("ready") is True
             and item.get("session_id") == row.session_id and item.get("mode") == row.mode]
    if len(ready) != 1:
        raise PresentationSourceStale("exact accepted alignment session is unavailable")
    bam_path, bam, bai_path, bai = source_service.resolve_session_alignment_bundle(str(job.id), row.session_id,
        source_reference_sha256=reference_sha, workflow_id=workflow, input_mode=mode, job_output_dir=root)
    current = source_from_bundle(job.id, ready[0], bam, bai, package_sha)
    if current != row.source_identity or identity_sha256(current) != row.source_authority_sha256:
        raise PresentationSourceStale("accepted alignment generation changed")
    return current, {"alignment_path": Path(bam_path), "index_path": Path(bai_path), "result_root": Path(root)}
