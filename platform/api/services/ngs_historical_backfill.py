"""Explicit historical inventory/admission; no scientific writes or product builder.

The plan is a source-bound review artifact, not authority to bypass fresh checks.
Historical ownership lives on existing product rows (migration 49), not in a
rewritten scientific receipt. Construction/adoption stays with the product worker.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import rfc8785
from sqlalchemy import select

from database import Job, NgsAlignmentDerivedProduct as Product
from services import ngs_alignment_derived_products as lifecycle
from services import ngs_alignment_sessions as storage
from services.job_result_roots import resolve_persisted_job_result_root
from services.ngs_alignment_presentation import PresentationSourceStale

PROTECTED_JOB = "e48193a5-f6e1-47af-9c1d-30fda50090a8"
ACCEPTED_FIXTURE = "d08ca589-af8b-46dc-98bd-f17ed512cecd"
# Regression requirements, never observed results or synthesized acceptance.
ACCEPTED_FIXTURE_REQUIREMENTS = {
    "source_bam_size_bytes": 818274983, "preview_read_ids": 5000,
    "preview_mapped_projections": 9522, "preview_bam_ceiling_bytes": 67108864,
    "locus": "eGFP_plasmid:1-4252", "read_id": "b0aabdbd-d617-4392-83db-9c0c7083e688",
    "waveform_source_samples": 84356, "waveform_displayed_points": 16872, "waveform_units": "pA",
}
BlockedReason = Literal["protected", "not_ngs", "not_completed", "rejected_receipt", "job_missing",
    "identity_invalid", "missing_reference", "missing_receipt", "digest_mismatch",
    "unsupported_source", "source_invalid", "source_drift", "resource_limit", "infrastructure_failed"]


class Blocked(ValueError):
    def __init__(self, reason: BlockedReason, message: str):
        super().__init__(message)
        self.reason: BlockedReason = reason


def scientific_binding(job) -> str:
    """Exact persisted science, with its original hash conventions untouched."""
    return hashlib.sha256(rfc8785.dumps({"job_id": str(job.id),
        "params": job.params, "receipt": (job.provenance or {}).get("result_integrity"),
        "result_root": str(resolve_persisted_job_result_root(job))})).hexdigest()


def eligibility(job):
    from services.ont_ngs_contract import ont_workflow_identity_values, get_ont_workflow_spec
    if str(job.id) == PROTECTED_JOB:
        raise Blocked("protected", "protected preimage is never a backfill source")
    from services.molbio_ngs_member_receipts import is_ngs_job_identity
    if not is_ngs_job_identity(job):
        raise Blocked("not_ngs", "outside canonical NGS discovery identity")
    if job.status != "completed" or job.queue_status != "completed" or job.awaiting_input:
        raise Blocked("not_completed", "scientific terminal state is not accepted")
    receipt = (job.provenance or {}).get("result_integrity")
    if not isinstance(receipt, dict):
        raise Blocked("missing_receipt", "accepted result receipt is absent")
    if receipt.get("state") != "validated" or receipt.get("partial") is not False:
        raise Blocked("rejected_receipt", "receipt is not a complete validated result")
    params = job.params or {}
    identities = ont_workflow_identity_values(params)
    from services.ont_ngs_contract import resolve_ont_workflow_alias
    workflow = receipt.get("workflow_id")
    if not isinstance(workflow, str):
        raise Blocked("identity_invalid", "receipt has no canonical NGS workflow")
    try:
        get_ont_workflow_spec(workflow)
        canonical = resolve_ont_workflow_alias(workflow)
    except KeyError:
        raise Blocked("identity_invalid", "receipt has no canonical NGS workflow") from None
    if identities != {canonical} or receipt.get("input_mode") != params.get("ont_input_mode"):
        raise Blocked("identity_invalid", "workflow/input identity conflicts with accepted receipt")
    return receipt, params


def discover_sources(job):
    """Use native/QC owners, then hash exact BAM/BAI before admitting anything.

    No legacy package is imported, relabelled, removed or adopted here. The
    current builder alone validates compatible orphan products after claiming.
    """
    receipt, params = eligibility(job)
    from services.ngs_native_alignment_sources import is_native, sources
    from services.ngs_alignment_product_builder import _sources
    root = Path(resolve_persisted_job_result_root(job))
    if is_native(job):
        candidates = sources(job, root)
    else:
        reference = receipt.get("reference_sequence_sha256") or params.get("reference_sequence_sha256")
        if not reference:
            raise Blocked("missing_reference", "immutable reference digest is absent")
        if not receipt.get("artifact_set_sha256"):
            raise Blocked("missing_receipt", "accepted artifact-set digest is absent")
        kwargs = dict(source_reference_sha256=reference, workflow_id=receipt["workflow_id"],
                      input_mode=receipt["input_mode"], job_output_dir=root)
        available = storage.build_alignment_sessions(str(job.id),
            package_artifact_set_sha256=receipt["artifact_set_sha256"], **kwargs)
        candidates = []
        for ready in available:
            if ready.get("ready") is not True:
                message = "alignment source unavailable: " + str(ready.get("unavailable_reason"))
                candidates.append((None, {"session_id": ready.get("session_id"), "mode": ready.get("mode"),
                    "reason": failure_reason(storage.AlignmentSessionError(message)), "message": message}))
                continue
            bam_path, bam, bai_path, bai = storage.resolve_session_alignment_bundle(
                str(job.id), ready["session_id"], **kwargs)
            source = lifecycle.source_from_bundle(job.id, ready, bam, bai, receipt["artifact_set_sha256"])
            candidates.append((source, {"alignment_path": Path(bam_path), "index_path": Path(bai_path), "result_root": root}))
    if not candidates:
        raise Blocked("unsupported_source", "accepted result has no supported single-reference alignment")
    for source, inputs in candidates:
        if source is None:
            continue
        with _sources(inputs, source, lambda: None):
            pass
    return candidates


def failure_reason(exc) -> BlockedReason:
    if isinstance(exc, Blocked):
        return exc.reason
    # Preserve explicit owner errors, never turn missing authority into success.
    text = str(exc).lower()
    if "reference" in text and ("missing" in text or "absent" in text):
        return "missing_reference"
    if "digest" in text or "integrity mismatch" in text or "bytes changed" in text:
        return "digest_mismatch"
    if "unsupported" in text:
        return "unsupported_source"
    if isinstance(exc, storage.AlignmentCapacityUnavailable) or getattr(exc, "code", None) == "resource_limit":
        return "resource_limit"
    if isinstance(exc, OSError):
        return "infrastructure_failed"
    return "source_invalid"


async def inventory(session, job_ids):
    rows = []
    for job_id in sorted(set(job_ids)):
        job = await session.get(Job, job_id, populate_existing=True)
        if job is None:
            rows.append({"job_id": job_id, "disposition": "blocked", "reason": "job_missing"})
            continue
        try:
            candidates = discover_sources(job)
            binding = scientific_binding(job)
            from services.ngs_alignment_product_builder import resolved_preview_policy
            policy = resolved_preview_policy()
            for source, _inputs in candidates:
                if source is None:
                    rows.append({"job_id": job_id, "disposition": "blocked", **_inputs})
                    continue
                rows.append({"job_id": job_id, "disposition": "eligible", "source": source,
                    "scientific_binding_sha256": binding, "preview_policy": policy,
                    "request_id": lifecycle.catalog_request_id(source)})
        except (Blocked, storage.AlignmentSessionError, PresentationSourceStale, OSError, ValueError, KeyError, TypeError) as exc:
            reason = failure_reason(exc)
            rows.append({"job_id": job_id,
                "disposition": "excluded" if reason in {"protected", "not_ngs", "not_completed", "rejected_receipt"} else "blocked",
                "reason": reason, "message": str(exc)})
    plan = {"schema": "bms.ngs.historical-inventory.v1", "rows": rows}
    return {**plan, "plan_sha256": lifecycle.identity_sha256(plan)}


def validate_plan(plan):
    if set(plan) != {"schema", "rows", "plan_sha256"} or plan["schema"] != "bms.ngs.historical-inventory.v1":
        raise ValueError("unsupported inventory plan")
    if lifecycle.identity_sha256({key: value for key, value in plan.items() if key != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("inventory plan digest mismatch")
    keys = [(row["job_id"], row.get("request_id"), row.get("session_id")) for row in plan["rows"]]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate inventory designation")


def ownership(job, source):
    return {"schema": "bms.ngs.historical-request-owner.v1", "job_id": str(job.id),
        "session_id": source["session_id"], "request_id": lifecycle.catalog_request_id(source),
        "source_authority_sha256": lifecycle.identity_sha256(source),
        "scientific_binding_sha256": scientific_binding(job)}


def owns_request(job, row):
    eligibility(job)
    return getattr(row, "historical_owner", None) == ownership(job, row.source_identity)


async def current_historical_catalog(session, job, session_id):
    """Read-only exact ownership selection; old policy generations cannot win."""
    candidates = (await session.scalars(select(Product).where(
        Product.job_id == str(job.id), Product.session_id == session_id,
        Product.product == "catalog", Product.historical_owner.is_not(None)))).all()
    matches = []
    for row in candidates:
        try:
            if owns_request(job, row) and row.id == lifecycle.catalog_request_id(row.source_identity):
                from starlette.concurrency import run_in_threadpool
                await run_in_threadpool(lifecycle.resolve_product_source, job, row)
                matches.append(row)
        except (ValueError, OSError, storage.AlignmentSessionError, PresentationSourceStale):
            continue
    if candidates and len(matches) != 1:
        raise PresentationSourceStale("historical catalog ownership is stale or ambiguous")
    return matches[0] if matches else None


async def backfill(session, plan):
    """Flush-only, all-or-nothing. Caller holds BEGIN IMMEDIATE through commit.

    No retry or reset on rerun: failed/cancelled/running/ready requests remain
    exactly as observed. Cancellation/crash before commit rolls back ownership
    and intent together; a lost response after commit is an ordinary replay.
    """
    validate_plan(plan)
    fresh = await inventory(session, [row["job_id"] for row in plan["rows"]])
    if fresh != plan:
        raise Blocked("source_drift", "fresh inventory differs; review a new plan")
    admitted = []
    for item in plan["rows"]:
        if item["disposition"] != "eligible":
            continue
        job = await session.get(Job, item["job_id"])
        source = item["source"]
        catalog = await lifecycle.request_catalog(session, source)
        proof = ownership(job, source)
        if catalog.historical_owner not in (None, proof):
            raise Blocked("source_drift", "historical ownership conflicts")
        catalog.historical_owner = proof
        preview = await lifecycle.request_preview(session, catalog, policy=item["preview_policy"])
        if preview.historical_owner not in (None, proof):
            raise Blocked("source_drift", "preview historical ownership conflicts")
        preview.historical_owner = proof
        admitted.append(catalog.id)
    await session.flush()
    return admitted


def activation_disposition(catalog, preview):
    return {"catalog": lifecycle.product_status(catalog),
        "preview": lifecycle.product_status(preview, catalog=catalog),
        "complete_reads_ready": catalog is not None and catalog.state == "ready",
        "cutover_terminal": catalog is not None and catalog.state == "ready"
            and preview is not None and preview.state in {"ready", "failed"}}


async def report(session, plan):
    validate_plan(plan)
    current = await inventory(session, [row["job_id"] for row in plan["rows"]])
    drift = current != plan
    result = []
    for item in plan["rows"]:
        row = dict(item)
        if item["disposition"] == "eligible":
            catalog = await session.get(Product, item["request_id"])
            preview_id = "ngs-preview-" + lifecycle.identity_sha256(lifecycle.preview_intent(item["request_id"], item["preview_policy"]))
            preview = await session.get(Product, preview_id) if catalog else None
            row.update(activation_disposition(catalog, preview))
            if catalog is not None:
                job = await session.get(Job, item["job_id"])
                if job is None:
                    row.update(complete_reads_ready=False, cutover_terminal=False, reason="job_missing")
                    result.append(row)
                    continue
                try:
                    lifecycle.resolve_product_source(job, catalog)
                    from services.ngs_alignment_product_builder import _namespace, _manifest
                    from contextlib import ExitStack
                    root = Path(resolve_persisted_job_result_root(job)) / ".alignment-products"
                    for product in (catalog,):
                        if product is None or product.state != "ready":
                            continue
                        with ExitStack() as stack:
                            namespace = stack.enter_context(_namespace(root, product, create=False))
                            sealed = stack.enter_context(storage.open_presentation_authority_root(namespace / "sealed", create=False))
                            _manifest(sealed, product, lambda: None, expected_manifest=product.manifest_sha256,
                                      expected_authority=product.authority_sha256)
                except (ValueError, OSError, storage.AlignmentSessionError, PresentationSourceStale) as exc:
                    row.update(complete_reads_ready=False, cutover_terminal=False, reason=failure_reason(exc))
        result.append(row)
    designated = [row for row in result if row["disposition"] == "eligible"]
    return {"schema": "bms.ngs.historical-activation-report.v1", "plan_sha256": plan["plan_sha256"],
        "source_drift": drift, "rows": result, "current_inventory": current if drift else None,
        "reader_activation_eligible": not drift and bool(designated)
            and all(row["cutover_terminal"] for row in designated)
            and not any(row["disposition"] == "blocked" for row in result),
        "accepted_fixture_job_id": ACCEPTED_FIXTURE,
        "accepted_preview_fixture": "not_verified_by_this_report",
        "accepted_fixture_requirements": ACCEPTED_FIXTURE_REQUIREMENTS,
        "deployment_authorized": False}
