from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import ExperimentRevision
from molbio_ngs_models import MolBioNGSGlobalBinding, MolBioNGSSampleRevision


class OntNgsHierarchyLoadError(RuntimeError):
    """Raised when a frozen hierarchy row cannot be loaded."""


def _record(row: Any, fields: Sequence[str], label: str) -> dict[str, Any]:
    if row is None:
        raise OntNgsHierarchyLoadError(f"{label} authority is unavailable")
    return {field: getattr(row, field, None) for field in fields}


async def resolve_hierarchy_records(
    job: Any,
    domain_session: AsyncSession,
    experiment_session: AsyncSession,
    *,
    build_authority: Callable[..., Any],
    get_state_revision_fn: Callable[..., Awaitable[Any]],
    verify_state_revision_fn: Callable[..., Awaitable[tuple[dict[str, Any], list[dict[str, object]]]]],
    get_reference_revision_fn: Callable[..., Awaitable[Any]],
    get_sample_revision_fn: Callable[..., Awaitable[Any]],
) -> Any:
    params_value = getattr(job, "params", None)
    params = params_value if isinstance(params_value, Mapping) else {}
    domain_id = params.get("global_domain_experiment_id")
    state_revision_id = params.get("molbio_ngs_state_revision_id")
    member_receipt_id = params.get("state_membership_receipt_id")
    reference_resource_id = params.get("ngs_reference_id")
    reference_revision_id = params.get("ngs_reference_revision_id")
    if not all(
        isinstance(value, str) and value
        for value in (
            domain_id,
            state_revision_id,
            member_receipt_id,
            reference_resource_id,
            reference_revision_id,
        )
    ):
        raise OntNgsHierarchyLoadError("Job hierarchy authority is incomplete")

    state_revision = await get_state_revision_fn(domain_session, domain_id, state_revision_id)
    _state_payload, membership_graph = await verify_state_revision_fn(domain_session, state_revision)
    exact_members = [
        member
        for member in membership_graph
        if isinstance(member, Mapping) and member.get("receipt_id") == member_receipt_id
    ]
    if len(exact_members) != 1:
        raise OntNgsHierarchyLoadError("frozen member receipt authority is unavailable")
    member = exact_members[0]
    sample_revision_id = member.get("sample_revision_id")
    if not isinstance(sample_revision_id, str) or not sample_revision_id:
        raise OntNgsHierarchyLoadError("frozen sample revision authority is unavailable")

    sample_row = await domain_session.get(MolBioNGSSampleRevision, sample_revision_id)
    if sample_row is None:
        raise OntNgsHierarchyLoadError("frozen sample revision authority is unavailable")
    sample_revision = await get_sample_revision_fn(
        domain_session,
        domain_id,
        sample_row.sample_id,
        sample_revision_id,
    )
    reference_revision = await get_reference_revision_fn(
        domain_session,
        reference_resource_id,
        reference_revision_id,
    )

    binding_revision_id = getattr(state_revision, "binding_revision_id", None)
    if not isinstance(binding_revision_id, str) or not binding_revision_id:
        raise OntNgsHierarchyLoadError("frozen binding revision authority is unavailable")
    binding = await domain_session.get(MolBioNGSGlobalBinding, binding_revision_id)
    if binding is None or not isinstance(binding.global_binding_receipt_json, str):
        raise OntNgsHierarchyLoadError("frozen binding receipt authority is unavailable")
    try:
        binding_receipt = json.loads(binding.global_binding_receipt_json)
        project_binding = binding_receipt["project"]
        global_binding = binding_receipt["global_experiment"]
        domain_binding = binding_receipt["domain_experiment"]
        project_revision_id = project_binding["revision_id"]
        global_revision_id = global_binding["revision_id"]
        domain_revision_id = domain_binding["revision_id"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OntNgsHierarchyLoadError("frozen binding receipt authority is invalid") from exc
    if not all(isinstance(value, str) and value for value in (project_revision_id, global_revision_id, domain_revision_id)):
        raise OntNgsHierarchyLoadError("frozen global revision authority is invalid")

    project_revision = await experiment_session.get(ExperimentRevision, project_revision_id)
    global_revision = await experiment_session.get(ExperimentRevision, global_revision_id)
    domain_revision = await experiment_session.get(ExperimentRevision, domain_revision_id)

    return build_authority(
        job_id=getattr(job, "id", None),
        model_id=getattr(job, "model_id", None),
        params=dict(params),
        state_revision=_record(
            state_revision,
            (
                "id",
                "global_domain_experiment_id",
                "global_domain_experiment_revision_id",
                "binding_revision_id",
                "canonical_payload",
                "payload_sha256",
                "membership_graph_sha256",
            ),
            "state revision",
        ),
        membership_graph=membership_graph,
        binding=_record(
            binding,
            (
                "binding_revision_id",
                "binding_state",
                "global_domain_experiment_id",
                "global_domain_experiment_revision_id",
                "global_domain_experiment_revision_digest",
                "project_id",
                "project_generation",
                "project_digest",
                "global_experiment_id",
                "global_experiment_generation",
                "global_experiment_digest",
                "global_binding_receipt_id",
                "global_binding_receipt_json",
                "global_binding_receipt_sha256",
            ),
            "binding",
        ),
        project_revision=_record(
            project_revision,
            ("resource_id", "subject_id", "payload_sha256"),
            "Project revision",
        ),
        global_revision=_record(
            global_revision,
            ("resource_id", "subject_id", "payload_sha256"),
            "Global Experiment revision",
        ),
        domain_revision=_record(
            domain_revision,
            ("resource_id", "subject_id", "payload_sha256"),
            "Domain Experiment revision",
        ),
        sample_revision=_record(
            sample_revision,
            (
                "id",
                "global_domain_experiment_id",
                "canonical_payload",
                "payload_sha256",
            ),
            "sample revision",
        ),
        reference_revision=_record(
            reference_revision,
            (
                "id",
                "global_domain_experiment_id",
                "canonical_payload",
                "payload_sha256",
                "canonical_fasta_sha256",
                "canonical_fasta_size_bytes",
                "normalized_sequence_sha256",
            ),
            "reference revision",
        ),
    )
