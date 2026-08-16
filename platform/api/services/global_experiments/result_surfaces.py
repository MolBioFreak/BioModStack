"""Explicit Project Manager result surfaces for verified native references."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import ExperimentExternalEntityReceipt
from experiment_services import NotFound, ValidationFailure


@lru_cache(maxsize=1)
def _result_surface_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[4] / "docs" / "specs" / "schemas" / "result-surface-v1.schema.json"
    with schema_path.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_result_surface(surface: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(_result_surface_validator().iter_errors(surface), key=lambda error: list(error.path))
    if errors:
        path = ".".join(str(part) for part in errors[0].path) or "surface"
        raise ValidationFailure(f"result surface violates frozen schema at {path}: {errors[0].message}")
    return surface


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _acknowledgement(receipt: ExperimentExternalEntityReceipt) -> dict[str, Any]:
    try:
        payload = json.loads(receipt.acknowledgement_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("stored verification acknowledgement is malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "bms.global.external-entity-receipt.v1":
        raise ValidationFailure("stored verification acknowledgement schema is invalid")
    if not isinstance(payload.get("content_digest"), str) or _SHA256_RE.fullmatch(payload["content_digest"]) is None:
        raise ValidationFailure("stored verification acknowledgement has no immutable digest")
    if not isinstance(payload.get("entity_kind"), str) or not payload["entity_kind"]:
        raise ValidationFailure("stored verification acknowledgement has no entity kind")
    if not isinstance(payload.get("entity_id"), str) or not payload["entity_id"]:
        raise ValidationFailure("stored verification acknowledgement has no entity identity")
    if not isinstance(payload.get("reopen_uri"), str) or not payload["reopen_uri"].startswith("/"):
        raise ValidationFailure("stored verification acknowledgement has no safe reopen URI")
    if not isinstance(payload.get("metadata"), dict):
        raise ValidationFailure("stored verification acknowledgement metadata is malformed")
    return payload


def _validate_persisted_authority(
    receipt: ExperimentExternalEntityReceipt,
    payload: dict[str, Any],
) -> None:
    authority = str(receipt.verification_authority or "").strip()
    if receipt.availability != "available":
        raise ValidationFailure("stored receipt is not verified as available")
    if not authority or authority in {"legacy_unverified", "caller_unverified"} or authority.startswith("unverified:"):
        raise ValidationFailure("stored receipt has no durable server verification authority")
    expected = {
        "store_id": receipt.store_id,
        "entity_kind": receipt.entity_kind,
        "entity_id": receipt.entity_id,
        "entity_revision_id": receipt.generation_or_revision,
        "content_digest": receipt.content_digest,
        "availability": receipt.availability,
        "verifier_id": authority,
    }
    if any(str(payload.get(field) or "") != str(value) for field, value in expected.items()):
        raise ValidationFailure("stored verification acknowledgement does not match persisted receipt authority")


def _base_surface(payload: dict[str, Any], *, surface_kind: str, readiness: str) -> dict[str, Any]:
    metadata = payload["metadata"]
    return {
        "surface_kind": surface_kind,
        "entity_kind": payload["entity_kind"],
        "entity_id": payload["entity_id"],
        "canonical_state": metadata.get("canonical_state"),
        "job_status": metadata.get("job_status"),
        "readiness": readiness,
        "immutable_digest": payload["content_digest"],
        "contract_digest": payload.get("contract_digest"),
        "result_contract_id": metadata.get("result_contract_id"),
        "source_build_revision": payload["source_build_revision"],
        "reopen_uri": payload["reopen_uri"],
        "metadata": metadata,
    }


def _completed(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"completed", "complete", "immutable", "available", "consumed", "ready", "succeeded", "success"}:
        return "ready"
    if normalized in {"running", "in_progress", "processing", "queued", "pending", "submitted", "requested"}:
        return "running"
    if normalized in {"failed", "failure", "error", "cancelled", "canceled", "expired"}:
        return "failed"
    if normalized == "blocked":
        return "blocked"
    return "partial"


def _design_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="protein_design", readiness=_completed(payload["metadata"].get("job_status")))


def _rfd3_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="protein_design", readiness=_completed(payload["metadata"].get("request_status")))


def _cm_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="conformational_mapping", readiness=_completed(payload["metadata"].get("canonical_state")))


def _md_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="molecular_dynamics", readiness=_completed(payload["metadata"].get("result_state")))


def _frustrampnn_surface(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload["metadata"].get("canonical_state")
    return _base_surface(
        payload,
        surface_kind="frustrampnn",
        readiness="ready" if state == "immutable" else _completed(state),
    )


def _molbio_revision_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="molbio", readiness="ready")


def _expected_reference_surface(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload["metadata"].get("canonical_state")
    readiness = "ready" if state in {"available", "consumed"} else _completed(state)
    return _base_surface(payload, surface_kind="ngs", readiness=readiness)


def _reference_set_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="ngs", readiness="ready")


def _sequence_qc_surface(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload["metadata"].get("job_status") or payload["metadata"].get("canonical_state")
    return _base_surface(payload, surface_kind="ngs", readiness=_completed(status))


def _ont_observation_surface(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload["metadata"]
    state = metadata.get("state")
    generation = metadata.get("observed_generation")
    event_type = metadata.get("event_type")
    reason = metadata.get("observation_reason")
    expected_reason = f"event={event_type}; state={state}; observed_generation={generation}"
    if (
        not isinstance(state, str)
        or not state.strip()
        or not isinstance(event_type, str)
        or not event_type.strip()
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 256
        or reason != expected_reason
    ):
        raise ValidationFailure(
            "verified ONT observation lacks bounded authoritative status, generation, or reason metadata"
        )
    terminal_digest = metadata.get("terminal_manifest_sha256")
    terminal_generation = metadata.get("terminal_manifest_observed_generation")
    terminal_state = metadata.get("terminal_manifest_state")
    terminal_artifacts = metadata.get("terminal_artifacts")
    if any(value is not None for value in (terminal_digest, terminal_generation, terminal_state)):
        if (
            not isinstance(terminal_digest, str)
            or _SHA256_RE.fullmatch(terminal_digest) is None
            or terminal_generation != generation
            or terminal_state != state
            or not isinstance(terminal_artifacts, list)
            or not terminal_artifacts
        ):
            raise ValidationFailure("verified ONT observation terminal evidence is inconsistent")
        for artifact in terminal_artifacts:
            if (
                not isinstance(artifact, dict)
                or not isinstance(artifact.get("kind"), str)
                or not artifact["kind"]
                or not isinstance(artifact.get("sha256"), str)
                or _SHA256_RE.fullmatch(artifact["sha256"]) is None
                or isinstance(artifact.get("size_bytes"), bool)
                or not isinstance(artifact.get("size_bytes"), int)
                or artifact["size_bytes"] < 0
            ):
                raise ValidationFailure("verified ONT observation terminal artifact metadata is invalid")
    elif terminal_artifacts not in (None, []):
        raise ValidationFailure("verified ONT observation has artifacts without terminal authority")
    readiness = _completed(state)
    if state.strip().lower() == "stopped" and terminal_digest is not None:
        readiness = "ready"
    details = _base_surface(payload, surface_kind="ngs", readiness=readiness)
    details["scientific_acceptance"] = {
        "state": "review" if details["readiness"] == "ready" else "unavailable",
        "reason": reason.strip(),
    }
    return details


def _alignment_surface(payload: dict[str, Any]) -> dict[str, Any]:
    readiness = "ready" if int(payload["metadata"].get("ready_session_count") or 0) > 0 else "partial"
    return _base_surface(payload, surface_kind="ngs", readiness=readiness)


def _exact_ngs_member_surface(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload["metadata"].get("job_status") or payload["metadata"].get("canonical_state")
    return _base_surface(payload, surface_kind="ngs", readiness=_completed(status))


def _evidence_assessment_surface(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload["metadata"]
    assessment = str(metadata.get("scientific_assessment") or "").strip().upper()
    acceptance_state = {
        "PASS": "passed",
        "FAIL": "failed",
        "REVIEW": "review",
    }.get(assessment)
    reason = metadata.get("scientific_assessment_reason")
    rule_id = metadata.get("assessment_rule_id")
    lifecycle = metadata.get("job_lifecycle_state")
    integrity = metadata.get("manifest_integrity")
    expected_reason = f"rule={rule_id}; job_lifecycle_state={lifecycle}; manifest_integrity={integrity}"
    if (
        acceptance_state is None
        or not all(isinstance(value, str) and value.strip() for value in (rule_id, lifecycle, integrity))
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 256
        or reason != expected_reason
    ):
        raise ValidationFailure(
            "verified NGS evidence assessment lacks an authoritative assessment state or reason"
        )
    details = _base_surface(payload, surface_kind="ngs", readiness="ready")
    details["scientific_acceptance"] = {
        "state": acceptance_state,
        "reason": reason.strip(),
    }
    return details


def _exact_molbio_member_surface(payload: dict[str, Any]) -> dict[str, Any]:
    return _base_surface(payload, surface_kind="molbio", readiness="ready")


_SURFACE_BUILDERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "design": _design_surface,
    "typed_core_job_result": _design_surface,
    "rfd3_local_redesign_request": _rfd3_surface,
    "conformational_mapping_request": _cm_surface,
    "md_result": _md_surface,
    "frustrampnn_result": _frustrampnn_surface,
    "frustrampnn_comparison": _frustrampnn_surface,
    "frustrampnn_guidance": _frustrampnn_surface,
    "molbio_revision": _molbio_revision_surface,
    "molbio_construct_revision": _molbio_revision_surface,
    "molbio_operation": _molbio_revision_surface,
    "molecular_revision": _exact_molbio_member_surface,
    "molecular_operation": _exact_molbio_member_surface,
    "primer_revision": _exact_molbio_member_surface,
    "pcr_experiment_revision": _exact_molbio_member_surface,
    "sample_revision": _exact_molbio_member_surface,
    "ngs_molbio_state_revision": _exact_molbio_member_surface,
    "ngs_expected_reference_receipt": _expected_reference_surface,
    "ngs_reference_set": _reference_set_surface,
    "ngs_reference_revision": _exact_ngs_member_surface,
    "ngs_comparison_panel": _exact_ngs_member_surface,
    "ngs_job": _exact_ngs_member_surface,
    "ngs_result_manifest": _exact_ngs_member_surface,
    "ngs_evidence_assessment": _evidence_assessment_surface,
    "ont_instrument_run": _ont_observation_surface,
    "ngs_pooled_assignment_release": _sequence_qc_surface,
    "sequence_qc_job": _sequence_qc_surface,
    "ngs_analysis_job": _sequence_qc_surface,
    "ngs_alignment_job": _alignment_surface,
}


async def result_surface_for_receipt(
    session: AsyncSession,
    *,
    project_id: str,
    receipt_id: str,
) -> dict[str, Any]:
    receipt = await session.get(ExperimentExternalEntityReceipt, receipt_id)
    if receipt is None or receipt.workspace_id != project_id:
        raise NotFound("verified reference receipt not found")
    payload = _acknowledgement(receipt)
    _validate_persisted_authority(receipt, payload)
    entity_kind = payload["entity_kind"]
    builder = _SURFACE_BUILDERS.get(entity_kind)
    if builder is None:
        details = _base_surface(payload, surface_kind="unsupported", readiness="unsupported")
        scientific_acceptance = {
            "state": "not_applicable",
            "reason": f"Unsupported verified entity kind: {entity_kind}"[:256],
        }
    else:
        details = builder(payload)
        readiness = str(details["readiness"])
        scientific_acceptance = details.get("scientific_acceptance") or {
            "state": "review" if readiness == "ready" else "unavailable",
            "reason": None,
        }
    readiness = str(details["readiness"])
    return _validate_result_surface({
        "schema": "bms.result-surface.v1",
        "receipt_id": receipt.id,
        "entity_kind": payload["entity_kind"],
        "entity_id": payload["entity_id"],
        "contract_id": str(details.get("result_contract_id") or "verified_external_entity_v1"),
        "content_digest": payload["content_digest"],
        "surface_kind": str(details["surface_kind"]),
        "route": payload["reopen_uri"],
        "readiness": readiness,
        "native_summary": dict(payload["metadata"]),
        "scientific_acceptance": scientific_acceptance,
        "provenance": {
            "store_id": payload.get("store_id"),
            "entity_revision_id": payload.get("entity_revision_id"),
            "contract_digest": payload.get("contract_digest"),
            "source_build_revision": payload.get("source_build_revision"),
            "verified_at": payload.get("verified_at"),
            "verifier_id": payload.get("verifier_id"),
        },
        "available_actions": ["open"],
    })
