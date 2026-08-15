"""Business services for global experiment/workspace persistence and dispatch."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from jsonschema import Draft202012Validator
from services.ngs_molbio_capabilities import (
    NgsMolBioCapabilityError,
    capability_parameter_schema,
    capability_record,
    validate_domain_experiment,
)
from model_registry import get_registry
from scripts.rfd3_local_redesign.contract import ContractError
from services.rfd3_local_redesign import (
    canonical_local_redesign_data_alias,
    local_redesign_requests_semantically_equal,
    prepare_local_redesign_scheduler_params,
    validate_local_redesign_workflow_params,
)
from services.resource_usage_evidence import (
    GLOBAL_DISPATCH_AUTHORITY_PARAM,
    GLOBAL_RESOURCE_ADMISSION_PARAM,
    RESOURCE_USAGE_RECEIPTS_PARAM,
    ResourceUsageEvidenceError,
    attach_dispatch_materialization_authority,
    attach_resource_admission_handoff,
    build_dispatch_materialization_authority,
    strip_resource_execution_metadata,
    validate_dispatch_materialization_authority,
    validate_resource_admission_handoff,
)

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDispatchOutbox,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentLaunchContext,
    ExperimentResource,
    ExperimentResearchRecord,
    ExperimentRevision,
    ExperimentRevisionEdge,
    ExperimentRunAttempt,
    ExperimentRunControlCommand,
    ExperimentWorkflowRevisionEdge,
    ExperimentWorkflowRevisionNode,
    ExperimentRunEvent,
    ExperimentRunGroup,
    ExperimentRunGroupPreparation,
    ExperimentValidation,
    ExperimentWorkflowDraft,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowPlanAuthority,
    ExperimentWorkflowRun,
)


class ExperimentServiceError(RuntimeError):
    """Base error for global experiment mutations."""


class NotFound(ExperimentServiceError):
    pass


class RevisionConflict(ExperimentServiceError):
    pass


class ValidationFailure(ExperimentServiceError):
    pass


class IdempotencyConflict(ExperimentServiceError):
    pass


class DispatchFailure(ExperimentServiceError):
    pass


PROJECT_STATUSES = {"draft", "active", "on_hold", "completed", "archived"}
EXPERIMENT_STATUSES = {
    "draft",
    "planned",
    "active",
    "analysis",
    "review",
    "completed",
    "blocked",
    "archived",
}
PROJECT_LIFECYCLE_TRANSITIONS = {
    "draft": {"draft", "active", "on_hold"},
    "active": {"active", "on_hold", "completed"},
    "on_hold": {"on_hold", "active", "completed"},
    "completed": {"completed"},
    "archived": {"archived"},
}
EXPERIMENT_LIFECYCLE_TRANSITIONS = {
    "draft": {"draft", "planned", "active", "blocked"},
    "planned": {"planned", "active", "blocked"},
    "active": {"active", "analysis", "blocked"},
    "analysis": {"analysis", "review", "blocked"},
    "review": {"review", "completed", "blocked"},
    "blocked": {"blocked", "planned", "active", "analysis", "review"},
    "completed": {"completed"},
    "archived": {"archived"},
}
DOMAIN_KINDS = {"protein_in_silico", "ngs_molbio"}
RESEARCH_RECORD_KINDS = {"note", "observation", "decision", "conclusion"}
PLAN_LAUNCH_AUTHORITY_FIELDS = {
    "project_id",
    "project_revision_id",
    "project_revision_generation",
    "project_revision_sha256",
    "global_experiment_id",
    "global_experiment_revision_id",
    "global_experiment_revision_generation",
    "global_experiment_revision_sha256",
    "domain_id",
    "domain_revision_id",
    "domain_revision_generation",
    "domain_revision_sha256",
    "binding_revision_id",
    "binding_generation",
    "connector_command_id",
    "connector_command_sha256",
    "connector_acknowledgement_id",
    "connector_acknowledgement_sha256",
    "global_binding_receipt_id",
    "global_binding_receipt_sha256",
    "local_state_revision_id",
    "local_state_generation",
    "local_state_payload_sha256",
    "local_state_membership_graph_sha256",
    "global_state_event_id",
    "global_state_event_sha256",
    "capability_contract_sha256",
}
PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS = {
    "project_revision_generation",
    "global_experiment_revision_generation",
    "domain_revision_generation",
    "binding_generation",
    "local_state_generation",
}
RECEIPT_CONTRACT_ID = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\.v[1-9][0-9]*$"
)
MAX_PINNED_RECEIPT_CONTRACTS = 32
MAX_RECEIPT_CONTRACT_ID_LENGTH = 255
TYPED_HANDOFF_LAUNCH_MODE = "typed_launcher_handoff"
MANAGED_DISPATCH_LAUNCH_MODES = {"managed_dispatch", "managed_materialization"}
SUPPORTED_PLAN_LAUNCH_MODES = {TYPED_HANDOFF_LAUNCH_MODE, *MANAGED_DISPATCH_LAUNCH_MODES}
MAX_REPLAY_AUTHORITY_BYTES = 1_000_000
MAX_REPLAY_AUTHORITY_ROWS = 2048


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _capability_model_modes(capability: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize the installed capability's exact permitted scheduler pairs."""
    raw_pairs = capability.get("allowed_model_modes")
    if raw_pairs is None:
        raw_pair = capability.get("allowed_model_mode")
        raw_pairs = [raw_pair] if isinstance(raw_pair, dict) else None
    if raw_pairs is None:
        model_id = capability.get("workflow_model_id", capability.get("model_id"))
        mode = capability.get("workflow_mode", capability.get("mode"))
        raw_pairs = [{"model_id": model_id, "mode": mode}]
    if (
        not isinstance(raw_pairs, list)
        or not raw_pairs
        or any(
            not isinstance(pair, dict)
            or set(pair) != {"model_id", "mode"}
            or not isinstance(pair.get("model_id"), str)
            or not pair["model_id"]
            or not isinstance(pair.get("mode"), str)
            or not pair["mode"]
            for pair in raw_pairs
        )
    ):
        raise ValidationFailure("accepted capability has no closed permitted model/mode contract")
    normalized = [
        {"model_id": str(pair["model_id"]), "mode": str(pair["mode"])}
        for pair in raw_pairs
    ]
    if len({(pair["model_id"], pair["mode"]) for pair in normalized}) != len(normalized):
        raise ValidationFailure("accepted capability has duplicate permitted model/mode pairs")
    return normalized


def _capability_receipt_contracts(capability: dict[str, Any]) -> list[str]:
    """Return the exact closed source-receipt contracts pinned into a Plan."""
    receipt_contracts = capability.get("receipt_contracts")
    if (
        not isinstance(receipt_contracts, list)
        or len(receipt_contracts) > MAX_PINNED_RECEIPT_CONTRACTS
        or any(
            not isinstance(value, str)
            or len(value) > MAX_RECEIPT_CONTRACT_ID_LENGTH
            or RECEIPT_CONTRACT_ID.fullmatch(value) is None
            for value in receipt_contracts
        )
        or len(set(receipt_contracts)) != len(receipt_contracts)
    ):
        raise ValidationFailure("accepted capability has no closed source receipt contract set")
    return list(receipt_contracts)


def _validate_input_receipt_contract_authority(
    input_authority: dict[str, Any],
    receipt_contracts: list[str] | None,
) -> None:
    expected_contracts = list(receipt_contracts or [])
    if (
        input_authority.get("receipt_contracts") != expected_contracts
        or input_authority.get("receipt_contracts_sha256")
        != sha256_text(canonical_json(expected_contracts))
    ):
        raise ValidationFailure(
            "preparation input authority did not preserve its immutable pinned receipt contracts"
        )


def workflow_plan_capability_contract(capability_id: str) -> dict[str, Any]:
    """Build the exact server-owned capability contract pinned by a new Plan."""
    try:
        capability = capability_record(capability_id)
        parameter_schema = capability_parameter_schema(capability_id)
    except NgsMolBioCapabilityError as exc:
        raise ValidationFailure(str(exc)) from exc
    if capability.get("plannable") is not True or capability.get("exposure_state") != "accepted":
        raise ValidationFailure("capability is not accepted for Workflow Plan launch")
    family = capability.get("workflow_family")
    adapter_id = capability.get("workflow_adapter_id")
    launch_mode = capability.get("launch_mode")
    result_contracts = capability.get("result_contracts")
    if not isinstance(family, str) or not family or not isinstance(adapter_id, str) or not adapter_id:
        raise ValidationFailure("accepted capability has no canonical workflow family and adapter")
    if launch_mode not in SUPPORTED_PLAN_LAUNCH_MODES:
        raise ValidationFailure("accepted capability has no supported Workflow Plan launch mode")
    if (
        not isinstance(result_contracts, list)
        or not result_contracts
        or any(not isinstance(value, str) or not value for value in result_contracts)
        or len(set(result_contracts)) != len(result_contracts)
    ):
        raise ValidationFailure("accepted capability has no closed result contract set")
    _capability_receipt_contracts(capability)
    if (
        not isinstance(parameter_schema, dict)
        or parameter_schema.get("$id") != capability.get("parameter_schema_id")
        or parameter_schema.get("type") != "object"
        or parameter_schema.get("additionalProperties") is not False
    ):
        raise ValidationFailure("accepted capability parameter schema is not a closed object")
    contract = {
        "schema": "bms.workflow-plan-capability-contract.v1",
        "capability": capability,
        "parameter_schema": parameter_schema,
        "allowed_model_modes": _capability_model_modes(capability),
    }
    return json.loads(canonical_json(contract))


def decode_workflow_plan_capability_contract(
    contract_json: str,
    contract_sha256: str,
) -> dict[str, Any]:
    """Verify and decode one immutable stored Plan capability contract."""
    if sha256_text(contract_json) != contract_sha256:
        raise ValidationFailure("Workflow Plan capability contract digest mismatch")
    try:
        contract = json.loads(contract_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("Workflow Plan capability contract is malformed") from exc
    if (
        not isinstance(contract, dict)
        or set(contract) != {"schema", "capability", "parameter_schema", "allowed_model_modes"}
        or contract.get("schema") != "bms.workflow-plan-capability-contract.v1"
        or canonical_json(contract) != contract_json
    ):
        raise ValidationFailure("Workflow Plan capability contract is not canonical")
    capability = contract.get("capability")
    parameter_schema = contract.get("parameter_schema")
    if not isinstance(capability, dict) or not isinstance(parameter_schema, dict):
        raise ValidationFailure("Workflow Plan capability contract is incomplete")
    if capability.get("plannable") is not True or capability.get("exposure_state") != "accepted":
        raise ValidationFailure("Workflow Plan capability contract was not accepted when pinned")
    family = capability.get("workflow_family")
    adapter_id = capability.get("workflow_adapter_id")
    launch_mode = capability.get("launch_mode")
    result_contracts = capability.get("result_contracts")
    if not isinstance(family, str) or not family or not isinstance(adapter_id, str) or not adapter_id:
        raise ValidationFailure("Workflow Plan capability contract has no family/adapter authority")
    if launch_mode not in SUPPORTED_PLAN_LAUNCH_MODES:
        raise ValidationFailure("Workflow Plan capability contract has an unsupported launch mode")
    if (
        not isinstance(result_contracts, list)
        or not result_contracts
        or any(not isinstance(value, str) or not value for value in result_contracts)
        or len(set(result_contracts)) != len(result_contracts)
    ):
        raise ValidationFailure("Workflow Plan capability contract has no closed result contracts")
    _capability_receipt_contracts(capability)
    if (
        parameter_schema.get("$id") != capability.get("parameter_schema_id")
        or parameter_schema.get("type") != "object"
        or parameter_schema.get("additionalProperties") is not False
    ):
        raise ValidationFailure("Workflow Plan capability parameter schema is not closed")
    normalized_pairs = _capability_model_modes({"allowed_model_modes": contract.get("allowed_model_modes")})
    if normalized_pairs != contract.get("allowed_model_modes"):
        raise ValidationFailure("Workflow Plan permitted model/mode contract is not canonical")
    return contract


async def load_workflow_plan_authority(
    session: AsyncSession,
    workflow_id: str,
    *,
    required: bool = True,
) -> tuple[ExperimentWorkflowPlanAuthority, dict[str, Any]] | None:
    """Load and cross-bind a Plan's immutable Domain/capability authority."""
    authority = await session.get(ExperimentWorkflowPlanAuthority, workflow_id)
    if authority is None:
        if required:
            raise ValidationFailure("Workflow Plan authority is unavailable")
        return None
    head = await _head(session, workflow_id, "workflow")
    revision = await session.get(ExperimentRevision, authority.expected_domain_revision_id)
    contract = decode_workflow_plan_capability_contract(
        authority.capability_contract_json,
        authority.capability_contract_sha256,
    )
    capability = contract["capability"]
    if (
        authority.workspace_id != head.workspace_id
        or authority.domain_experiment_id != head.parent_id
        or revision is None
        or revision.subject_id != authority.domain_experiment_id
        or capability.get("capability_id") != head.description
    ):
        raise ValidationFailure("Workflow Plan authority is not bound to its stored Plan/Domain")
    return authority, contract


async def persist_workflow_plan_authority(
    session: AsyncSession,
    *,
    workflow_id: str,
    workspace_id: str,
    domain_experiment_id: str,
    expected_domain_revision_id: str,
    capability_id: str,
) -> tuple[ExperimentWorkflowPlanAuthority, dict[str, Any]]:
    """Persist or exactly replay the immutable authority for one Project Manager Plan."""
    head = await _head(session, workflow_id, "workflow")
    domain = await _head(session, domain_experiment_id, "domain_experiment")
    revision = await session.get(ExperimentRevision, expected_domain_revision_id)
    existing = await session.get(ExperimentWorkflowPlanAuthority, workflow_id)
    if existing is not None:
        loaded = await load_workflow_plan_authority(session, workflow_id)
        if loaded is None:
            raise ValidationFailure("Workflow Plan authority is unavailable")
        existing, stored_contract = loaded
        if (
            head.workspace_id != workspace_id
            or head.parent_id != domain_experiment_id
            or head.description != capability_id
            or existing.workspace_id != workspace_id
            or existing.domain_experiment_id != domain_experiment_id
            or existing.expected_domain_revision_id != expected_domain_revision_id
            or stored_contract["capability"].get("capability_id") != capability_id
        ):
            raise IdempotencyConflict("Workflow Plan authority conflicts with the immutable stored authority")
        return existing, stored_contract
    contract = workflow_plan_capability_contract(capability_id)
    contract_json = canonical_json(contract)
    contract_sha256 = sha256_text(contract_json)
    if (
        head.workspace_id != workspace_id
        or head.parent_id != domain_experiment_id
        or head.description != capability_id
        or domain.workspace_id != workspace_id
        or domain.current_revision_id != expected_domain_revision_id
        or revision is None
        or revision.subject_id != domain_experiment_id
    ):
        raise RevisionConflict("Workflow Plan authority no longer matches the requested Project/Domain revision")
    authority = ExperimentWorkflowPlanAuthority(
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        domain_experiment_id=domain_experiment_id,
        expected_domain_revision_id=expected_domain_revision_id,
        capability_contract_json=contract_json,
        capability_contract_sha256=contract_sha256,
        created_at=now(),
    )
    session.add(authority)
    await session.flush()
    return authority, contract


def validate_workflow_payload_for_plan(
    payload: dict[str, Any],
    capability_contract: dict[str, Any],
) -> None:
    """Enforce the complete pinned family/adapter/model/schema contract."""
    _validate_workflow_payload(payload, capability_contract=capability_contract)
    capability = capability_contract["capability"]
    receipt_contracts = _capability_receipt_contracts(capability)
    source_receipt_ids = payload.get("source_receipt_ids") or []
    if source_receipt_ids and not receipt_contracts:
        raise ValidationFailure(
            "source-bearing workflow has no immutable pinned source receipt contract authority"
        )
    family = capability["workflow_family"]
    adapter_id = capability["workflow_adapter_id"]
    if payload.get("workflow_family") != family or payload.get("adapter_id") != adapter_id:
        raise ValidationFailure("workflow payload disagrees with the pinned capability family/adapter")
    if payload.get("schema") != f"bms.workflow.{family}.v1":
        raise ValidationFailure("workflow payload schema disagrees with the pinned capability family")
    scheduler = payload.get("scheduler")
    parameters = payload.get("parameters")
    if not isinstance(scheduler, dict) or not isinstance(parameters, dict):
        raise ValidationFailure("pinned Workflow Plan requires closed parameters and scheduler settings")
    scheduler_params = scheduler.get("params")
    if not isinstance(scheduler_params, dict):
        raise ValidationFailure("pinned Workflow Plan scheduler params must be an object")
    model_mode = {"model_id": scheduler.get("model_id"), "mode": scheduler.get("mode")}
    if model_mode not in capability_contract["allowed_model_modes"]:
        raise ValidationFailure("workflow scheduler model/mode is not permitted by the pinned capability")
    expected_scheduler_params = {**parameters, "workflow_adapter": adapter_id}
    if canonical_json(expected_scheduler_params) != canonical_json(scheduler_params):
        raise ValidationFailure(
            "workflow scheduler params must carry the exact pinned settings and adapter identity"
        )
    schema = capability_contract["parameter_schema"]
    errors = sorted(
        Draft202012Validator(schema).iter_errors(parameters),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValidationFailure(f"workflow parameters violate the pinned schema at {location}: {errors[0].message}")
    for node in payload.get("nodes", []):
        if isinstance(node, dict) and "adapter_id" in node and node["adapter_id"] != adapter_id:
            raise ValidationFailure("workflow node adapter disagrees with the pinned capability adapter")


def public_workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Publish native workflow intent while hiding every private runtime path."""
    scheduler = payload.get("scheduler")
    if not isinstance(scheduler, dict) or scheduler.get("model_id") != "protein_local_redesign":
        return copy.deepcopy(payload)

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized == "input_structure":
                    try:
                        result[str(key)] = canonical_local_redesign_data_alias(child)
                    except ContractError:
                        pass
                    continue
                if normalized in {
                    "input",
                    "input_pdb",
                    "input_cif",
                    "plr_input_pdb",
                    "rfd3_request",
                }:
                    continue
                if any(
                    token in normalized
                    for token in ("path", "directory", "output_dir", "command", "executable")
                ):
                    continue
                result[str(key)] = redact(child)
            return result
        if isinstance(value, list):
            return [redact(child) for child in value]
        return value

    return redact(payload)


def public_preparation_scheduler(payload: dict[str, Any]) -> dict[str, Any]:
    """Publish a prepared scheduler without private runtime paths or native request bodies."""
    if payload.get("model_id") != "protein_local_redesign":
        return copy.deepcopy(payload)
    public = public_workflow_payload({"scheduler": payload})
    scheduler = public.get("scheduler")
    return scheduler if isinstance(scheduler, dict) else {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_audit_event(
    session: AsyncSession,
    *,
    workspace_id: str,
    resource_id: str,
    event_type: str,
    generation: int,
    payload: dict[str, Any],
) -> None:
    session.add(
        ExperimentAuditEvent(
            id=new_id("audit"),
            workspace_id=workspace_id,
            resource_id=resource_id,
            event_type=event_type,
            generation=generation,
            payload_json=canonical_json(payload),
            created_at=now(),
        )
    )


def new_id(_prefix: str) -> str:
    """Return an opaque UUID-sized identity portable to the existing core store."""
    return str(uuid.uuid4())


def scheduler_job_id_for_attempt(attempt_id: str) -> str:
    """Return the deterministic UUIDv5 identity accepted by canonical Job creation."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bms:global-experiment:core-job:{attempt_id}"))


TYPED_CORE_JOB_MODELS = {
    "boltz2",
    "boltz_cp_experimental",
    "boltzgen",
    "esmfold2",
    "molecular_dynamics",
    "nanopore",
    "ngs_alignment",
    "oligo_builder",
    "oligo_design",
    "ont_fastq_qc",
    "ppiflow",
    "protein_local_redesign",
    "protein_modification_experimental",
    "protenix",
    "rf3",
    "sequence_qc",
    "template_antibody_denovo",
}
TYPED_CORE_JOB_ADAPTERS = {
    f"bms.core-job.{model_id}.adapter.v1": model_id
    for model_id in sorted(TYPED_CORE_JOB_MODELS)
}
PROJECT_SCHEDULED_TYPED_CORE_ADAPTERS = {"bms.ngs.job-reference.adapter.v1"}


def scheduler_job_identity(attempt_id: str, scheduler: Mapping[str, Any]) -> str:
    """Keep CM attempt identity while giving typed core Jobs deterministic UUIDv5 identity."""
    params = scheduler.get("params")
    adapter_id = str(params.get("workflow_adapter") or "") if isinstance(params, dict) else ""
    return (
        scheduler_job_id_for_attempt(attempt_id)
        if adapter_id in TYPED_CORE_JOB_ADAPTERS
        or adapter_id in PROJECT_SCHEDULED_TYPED_CORE_ADAPTERS
        else attempt_id
    )


WORKFLOW_ADAPTER_REGISTRY: dict[str, set[str]] = {
    "generic_test": {"generic.test.adapter.v1"},
    "typed_core_job": set(TYPED_CORE_JOB_ADAPTERS),
    "conformational_mapping": {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
    },
}


def register_workflow_adapter(workflow_family: str, adapter_id: str) -> None:
    """Register a server-owned workflow adapter; callers cannot register via HTTP."""
    WORKFLOW_ADAPTER_REGISTRY.setdefault(workflow_family, set()).add(adapter_id)


def _cm_submission_source_ids(submission: dict[str, Any]) -> list[str]:
    backend = submission.get("backend")
    if backend == "protenix_v2_ensemble":
        values = [submission.get("registered_snapshot_id")]
    elif backend == "confornets":
        values = [
            submission.get("registered_sequence_id"),
            submission.get("registered_checkpoint_id"),
            *(submission.get("registered_reference_ids") or []),
        ]
        if submission.get("registered_config_id"):
            values.append(submission["registered_config_id"])
        if submission.get("registered_transfer_id"):
            values.append(submission["registered_transfer_id"])
    else:
        raise ValidationFailure("CM global workflow backend has no materializer")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValidationFailure("CM global workflow source identities are incomplete")
    source_ids = [str(value) for value in values]
    if len(source_ids) != len(set(source_ids)):
        raise ValidationFailure("CM global workflow source identities must be unique")
    return source_ids


def _validate_workflow_payload(
    payload: dict[str, Any],
    *,
    capability_contract: dict[str, Any] | None = None,
) -> None:
    required = ("schema", "workflow_family", "contract_version", "adapter_id", "nodes", "edges")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValidationFailure(f"workflow revision missing required fields: {', '.join(missing)}")
    allowed_top_level = set(required) | {
        "parameters",
        "scheduler",
        "stage",
        "backend",
        "source_receipt_ids",
        "expected_cardinality",
        "depends_on",
    }
    unknown_top_level = sorted(set(payload) - allowed_top_level)
    if unknown_top_level:
        raise ValidationFailure(f"workflow revision has unknown fields: {', '.join(unknown_top_level)}")
    family = str(payload["workflow_family"])
    if str(payload["contract_version"]) != "1":
        raise ValidationFailure("unsupported workflow contract_version")
    schema = str(payload["schema"])
    if not schema.startswith("bms.workflow.") or not schema.endswith(".v1"):
        raise ValidationFailure("unsupported workflow schema")
    adapter_id = str(payload["adapter_id"])
    if (
        capability_contract is None
        and adapter_id not in WORKFLOW_ADAPTER_REGISTRY.get(family, set())
    ):
        raise ValidationFailure(f"workflow adapter is not registered: {family}/{adapter_id}")
    if family == "typed_core_job":
        scheduler = payload.get("scheduler")
        if not isinstance(scheduler, dict):
            raise ValidationFailure("typed core workflow requires scheduler settings")
        expected_model_id = TYPED_CORE_JOB_ADAPTERS.get(adapter_id)
        if (
            expected_model_id is None
            and capability_contract is not None
            and adapter_id in PROJECT_SCHEDULED_TYPED_CORE_ADAPTERS
            and scheduler.get("model_id") in TYPED_CORE_JOB_MODELS
        ):
            expected_model_id = str(scheduler["model_id"])
        if expected_model_id is None:
            raise ValidationFailure("typed core workflow adapter has no executable model authority")
        if scheduler.get("model_id") != expected_model_id:
            raise ValidationFailure("typed core workflow adapter and model_id disagree")
        if not isinstance(scheduler.get("name"), str) or not scheduler["name"].strip():
            raise ValidationFailure("typed core workflow requires a scheduler name")
        if not isinstance(scheduler.get("mode"), str) or not scheduler["mode"].strip():
            raise ValidationFailure("typed core workflow requires a scheduler mode")
        if not isinstance(scheduler.get("params"), dict):
            raise ValidationFailure("typed core workflow scheduler params must be an object")
        if scheduler["params"].get("workflow_adapter") != adapter_id:
            raise ValidationFailure("typed core workflow scheduler adapter identity disagrees")
        if expected_model_id == "protein_local_redesign":
            if scheduler.get("mode") != "local_redesign":
                raise ValidationFailure("native RFD3 typed workflow requires local_redesign mode")
            resources = scheduler.get("resources")
            pinned_gpu = resources.get("pinned_gpu") if isinstance(resources, dict) else None
            if isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0:
                raise ValidationFailure(
                    "native RFD3 typed workflow requires scheduler.resources.pinned_gpu as a non-negative integer"
                )
            try:
                validate_local_redesign_workflow_params(
                    scheduler["params"],
                    expected_adapter_id=adapter_id,
                )
            except ContractError as exc:
                raise ValidationFailure(str(exc)) from exc
    if family == "conformational_mapping":
        stage = payload.get("stage")
        stage_by_adapter = {
            "bms.cm.protenix_v2.adapter.v1": ("protenix_v2_sampling", "protenix_v2_ensemble"),
            "bms.cm.confornets.adapter.v1": ("confornets_sampling", "confornets"),
        }
        if adapter_id not in stage_by_adapter:
            raise ValidationFailure(f"CM workflow adapter has no executable materializer: {adapter_id}")
        expected_stage, expected_backend = stage_by_adapter[adapter_id]
        if stage != expected_stage or payload.get("backend") != expected_backend:
            raise ValidationFailure("CM workflow stage, backend, and adapter identity disagree")
        receipt_ids = payload.get("source_receipt_ids")
        if not isinstance(receipt_ids, list) or not receipt_ids or any(
            not isinstance(value, str) or not value for value in receipt_ids
        ):
            raise ValidationFailure("CM workflow requires explicit source receipt IDs")
        scheduler = payload.get("scheduler")
        params = scheduler.get("params") if isinstance(scheduler, dict) else None
        if not isinstance(params, dict):
            raise ValidationFailure("CM workflow requires typed scheduler params")
        if params.get("workflow_adapter") != adapter_id:
            raise ValidationFailure("CM nested workflow adapter disagrees with its authoritative outer adapter")
        submission = params.get("cm_submission")
        if not isinstance(submission, dict):
            raise ValidationFailure("CM workflow requires one typed generator submission")
        if submission.get("backend") != expected_backend:
            raise ValidationFailure("CM nested submission backend disagrees with its authoritative outer backend")
        if receipt_ids != _cm_submission_source_ids(submission):
            raise ValidationFailure("CM workflow source receipt IDs do not bind its submitted sources")
        cardinality = payload.get("expected_cardinality")
        if isinstance(cardinality, bool) or not isinstance(cardinality, int) or cardinality < 1:
            raise ValidationFailure("CM workflow expected_cardinality must be a positive integer")
        dependencies = payload.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) or not value for value in dependencies):
            raise ValidationFailure("CM workflow depends_on must be an ordered ID list")

    nodes = payload["nodes"]
    edges = payload["edges"]
    if not isinstance(nodes, list) or not nodes:
        raise ValidationFailure("workflow nodes must be a non-empty list")
    if not isinstance(edges, list):
        raise ValidationFailure("workflow edges must be a list")
    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("kind"):
            raise ValidationFailure("workflow nodes require unique id and kind")
        node_id = str(node["id"])
        if node_id in node_ids:
            raise ValidationFailure(f"duplicate workflow node id: {node_id}")
        node_ids.add(node_id)
    forbidden_keys = {
        "binary",
        "cmd",
        "command",
        "entry_point",
        "entrypoint",
        "executable",
        "factory",
        "import",
        "loader",
        "module",
        "path",
        "plugin",
        "python_path",
        "runtime_hook",
        "script",
        "shell",
        "callable",
    }
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).strip().lower().replace("-", "_")
                key_tokens = set(normalized_key.split("_"))
                if (
                    normalized_key in forbidden_keys
                    or normalized_key.endswith("_path")
                    or forbidden_keys.intersection(key_tokens)
                ):
                    raise ValidationFailure("workflow revisions cannot contain executable references")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    allowed_node_keys = {"id", "kind", "required", "adapter_id", "parameters", "label", "depends_on"}
    for index, node in enumerate(nodes):
        unknown_node_keys = sorted(set(node) - allowed_node_keys)
        if unknown_node_keys:
            raise ValidationFailure(
                f"workflow node {index} has unknown fields: {', '.join(unknown_node_keys)}"
            )
    allowed_edge_keys = {"source", "target", "from", "to", "kind", "edge_kind"}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValidationFailure("workflow edges must be objects")
        unknown_edge_keys = sorted(set(edge) - allowed_edge_keys)
        if unknown_edge_keys:
            raise ValidationFailure(f"workflow edge has unknown fields: {', '.join(unknown_edge_keys)}")
        source = edge.get("source", edge.get("from"))
        target = edge.get("target", edge.get("to"))
        if str(source) not in node_ids or str(target) not in node_ids:
            raise ValidationFailure("workflow edge references an unknown node")
    scheduler = payload.get("scheduler")
    if scheduler is not None:
        if not isinstance(scheduler, dict):
            raise ValidationFailure("workflow scheduler must be an object")
        unknown_scheduler_keys = sorted(
            set(scheduler) - {"name", "model_id", "mode", "params", "resources", "profile"}
        )
        if unknown_scheduler_keys:
            raise ValidationFailure(
                f"workflow scheduler has unknown fields: {', '.join(unknown_scheduler_keys)}"
            )


async def _resource(
    session: AsyncSession,
    *,
    kind: str,
    workspace_id: str | None,
    lifecycle_owner_id: str | None,
    resource_id: str | None = None,
) -> ExperimentResource:
    resource = ExperimentResource(
        id=resource_id or new_id(kind),
        kind=kind,
        workspace_id=workspace_id,
        lifecycle_owner_id=lifecycle_owner_id,
        created_at=now(),
    )
    session.add(resource)
    await session.flush()
    if lifecycle_owner_id is not None:
        session.add(
            ExperimentLineageEdge(
                id=new_id("owns"),
                workspace_id=workspace_id,
                source_resource_id=lifecycle_owner_id,
                target_resource_id=resource.id,
                edge_mode="owns",
                edge_key=f"lifecycle-owner:{resource.kind}",
                metadata_json="{}",
                created_at=now(),
            )
        )
        await session.flush()
    return resource


async def _head(session: AsyncSession, aggregate_id: str, kind: str | None = None) -> ExperimentAggregateHead:
    head = await session.get(ExperimentAggregateHead, aggregate_id)
    if head is None or (kind is not None and head.aggregate_kind != kind):
        raise NotFound(f"aggregate not found: {aggregate_id}")
    return head


async def _workspace(session: AsyncSession, workspace_id: str) -> ExperimentResource:
    resource = await session.get(ExperimentResource, workspace_id)
    if resource is None or resource.kind != "workspace":
        raise NotFound(f"workspace not found: {workspace_id}")
    return resource


async def _resource_workspace(session: AsyncSession, resource_id: str) -> str:
    resource = await session.get(ExperimentResource, resource_id)
    if resource is None:
        raise NotFound(f"resource not found: {resource_id}")
    return resource.id if resource.kind == "workspace" else str(resource.workspace_id)


async def _create_aggregate(
    session: AsyncSession,
    *,
    workspace_id: str,
    kind: str,
    display_name: str,
    description: str = "",
    parent_id: str | None = None,
    dataset_kind: str | None = None,
) -> ExperimentAggregateHead:
    await _workspace(session, workspace_id)
    if parent_id is not None:
        parent = await session.get(ExperimentResource, parent_id)
        allowed_parent_kinds = {"workspace", "experiment"}
        if kind == "domain_experiment":
            allowed_parent_kinds = {"experiment"}
        elif kind in {"workflow", "dataset"}:
            allowed_parent_kinds = {"domain_experiment"}
        if parent is None or parent.kind not in allowed_parent_kinds:
            expected = (
                "an experiment"
                if kind == "domain_experiment"
                else "a Domain Experiment"
                if kind in {"workflow", "dataset"}
                else "a workspace or experiment"
            )
            raise ValidationFailure(f"aggregate parent must be {expected}")
        if parent.kind != "workspace" and parent.workspace_id != workspace_id:
            raise ValidationFailure("aggregate parent belongs to another workspace")
        if parent.kind == "workspace" and parent.id != workspace_id:
            raise ValidationFailure("aggregate parent belongs to another workspace")
        parent_head = await session.get(ExperimentAggregateHead, parent_id)
        if parent_head is None:
            raise NotFound(f"aggregate parent not found: {parent_id}")
        if parent_head.lifecycle_state == "archived":
            raise ValidationFailure("aggregate parent is archived")
    resource = await _resource(
        session,
        kind=kind,
        workspace_id=workspace_id,
        lifecycle_owner_id=(
            parent_id
            if kind in {"domain_experiment", "workflow", "dataset"} and parent_id
            else workspace_id
        ),
    )
    head = ExperimentAggregateHead(
        aggregate_id=resource.id,
        aggregate_kind=kind,
        workspace_id=workspace_id,
        parent_id=parent_id,
        lifecycle_state="draft",
        display_name=display_name,
        description=description,
        dataset_kind=dataset_kind,
        created_at=now(),
        updated_at=now(),
    )
    session.add(head)
    await session.flush()
    if kind == "workflow":
        draft_resource = await _resource(
            session,
            kind="workflow_draft",
            workspace_id=workspace_id,
            lifecycle_owner_id=resource.id,
        )
        session.add(
            ExperimentWorkflowDraft(
                resource_id=draft_resource.id,
                workflow_id=resource.id,
                canonical_payload="{}",
                generation=0,
                created_at=now(),
                updated_at=now(),
            )
        )
        await session.flush()
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=head.aggregate_id,
        event_type="aggregate_created",
        generation=head.head_generation,
        payload={"kind": kind, "name": display_name},
    )
    return head


async def create_experiment_workspace(
    session: AsyncSession, name: str, description: str = ""
) -> ExperimentAggregateHead:
    resource = await _resource(session, kind="workspace", workspace_id=None, lifecycle_owner_id=None)
    head = ExperimentAggregateHead(
        aggregate_id=resource.id,
        aggregate_kind="workspace",
        workspace_id=resource.id,
        lifecycle_state="draft",
        display_name=name,
        description=description,
        created_at=now(),
        updated_at=now(),
    )
    session.add(head)
    await session.flush()
    add_audit_event(
        session,
        workspace_id=resource.id,
        resource_id=resource.id,
        event_type="workspace_created",
        generation=0,
        payload={"name": name},
    )
    return head


async def create_experiment(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    question: str = "",
    *,
    payload: dict[str, Any] | None = None,
) -> ExperimentAggregateHead:
    head = await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="experiment",
        display_name=name,
        description=question,
        parent_id=workspace_id,
    )
    if payload is not None:
        await _save_revision(
            session,
            aggregate_id=head.aggregate_id,
            aggregate_kind="experiment",
            payload=payload,
            expected_head_generation=0,
        )
        await session.refresh(head)
    return head


async def create_project(
    session: AsyncSession,
    payload: dict[str, Any],
) -> ExperimentAggregateHead:
    payload = {**payload, "needs_metadata_review": False}
    _validate_hierarchy_payload("workspace", payload)
    head = await create_experiment_workspace(
        session,
        str(payload["name"]),
        str(payload.get("description") or ""),
    )
    await _save_revision(
        session,
        aggregate_id=head.aggregate_id,
        aggregate_kind="workspace",
        payload=payload,
        expected_head_generation=0,
    )
    await session.refresh(head)
    return head


async def create_global_experiment(
    session: AsyncSession,
    project_id: str,
    payload: dict[str, Any],
) -> ExperimentAggregateHead:
    payload = {**payload, "needs_metadata_review": False}
    _validate_hierarchy_payload("experiment", payload)
    return await create_experiment(
        session,
        project_id,
        str(payload["name"]),
        str(payload.get("scientific_question") or payload.get("description") or ""),
        payload=payload,
    )


async def create_domain_experiment(
    session: AsyncSession,
    project_id: str,
    global_experiment_id: str,
    payload: dict[str, Any],
) -> ExperimentAggregateHead:
    _validate_hierarchy_payload("domain_experiment", payload)
    head = await _create_aggregate(
        session,
        workspace_id=project_id,
        kind="domain_experiment",
        display_name=str(payload["name"]),
        description=str(payload.get("objective") or ""),
        parent_id=global_experiment_id,
    )
    await _save_revision(
        session,
        aggregate_id=head.aggregate_id,
        aggregate_kind="domain_experiment",
        payload=payload,
        expected_head_generation=0,
    )
    await session.refresh(head)
    return head


async def create_workflow(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    workflow_family: str,
    *,
    experiment_id: str | None = None,
) -> ExperimentAggregateHead:
    return await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="workflow",
        display_name=name,
        description=workflow_family,
        parent_id=experiment_id or workspace_id,
    )


async def create_dataset(
    session: AsyncSession,
    workspace_id: str,
    name: str,
    dataset_kind: str,
    *,
    experiment_id: str | None = None,
) -> ExperimentAggregateHead:
    if not isinstance(dataset_kind, str) or not dataset_kind.strip():
        raise ValidationFailure("new Datasets require an enabled dataset_kind")
    head = await _create_aggregate(
        session,
        workspace_id=workspace_id,
        kind="dataset",
        display_name=name,
        description=dataset_kind,
        parent_id=experiment_id or workspace_id,
        dataset_kind=dataset_kind,
    )
    return head


async def save_workflow_draft(
    session: AsyncSession,
    workflow_id: str,
    payload: dict[str, Any],
    *,
    expected_generation: int,
) -> ExperimentWorkflowDraft:
    await _head(session, workflow_id, "workflow")
    result = await session.execute(
        select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == workflow_id)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise NotFound(f"workflow draft not found: {workflow_id}")
    if draft.generation != expected_generation:
        raise RevisionConflict(
            f"workflow draft generation conflict: expected {expected_generation}, current {draft.generation}"
        )
    plan_authority = await load_workflow_plan_authority(session, workflow_id, required=False)
    if plan_authority is None:
        _validate_workflow_payload(payload)
    else:
        validate_workflow_payload_for_plan(payload, plan_authority[1])
    draft.canonical_payload = canonical_json(payload)
    draft.generation += 1
    draft.updated_at = now()
    await session.flush()
    return draft


def _validate_hierarchy_payload(aggregate_kind: str, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValidationFailure("aggregate payload must be an object")
    expected_schema = {
        "workspace": "bms.project.v1",
        "experiment": "bms.global-experiment.v1",
        "domain_experiment": payload.get("schema"),
    }[aggregate_kind]
    if payload.get("schema") != expected_schema:
        raise ValidationFailure(f"{aggregate_kind} payload schema must be {expected_schema}")
    required = {
        "workspace": {"name", "description", "research_objective", "status", "needs_metadata_review"},
        "experiment": {"name", "objective", "scientific_question", "description", "status", "priority", "success_criteria", "needs_metadata_review"},
        "domain_experiment": {"domain_kind", "domain_contract_version", "name", "objective", "status", "domain_payload"},
    }[aggregate_kind]
    missing = sorted(field for field in required if field not in payload)
    if missing:
        raise ValidationFailure(f"{aggregate_kind} payload missing required fields: {', '.join(missing)}")
    statuses = PROJECT_STATUSES if aggregate_kind == "workspace" else EXPERIMENT_STATUSES
    if payload.get("status") not in statuses:
        raise ValidationFailure(f"invalid {aggregate_kind} lifecycle status")
    if aggregate_kind == "experiment":
        if payload.get("status") == "active":
            criteria = payload.get("success_criteria")
            if not isinstance(criteria, list) or not criteria:
                raise ValidationFailure("active global experiments require success criteria")
        if payload.get("status") == "completed":
            if not str(payload.get("review_summary") or "").strip() or not str(payload.get("conclusion") or "").strip():
                raise ValidationFailure("completed global experiments require review_summary and conclusion")
    if aggregate_kind == "domain_experiment":
        domain_kind = payload.get("domain_kind")
        if domain_kind not in DOMAIN_KINDS:
            raise ValidationFailure("domain_kind must be protein_in_silico or ngs_molbio")
        if payload.get("domain_contract_version") == "2":
            if payload.get("schema") != "bms.domain-experiment.v2":
                raise ValidationFailure("Domain v2 requires bms.domain-experiment.v2")
            try:
                validate_domain_experiment(payload)
            except NgsMolBioCapabilityError as exc:
                raise ValidationFailure(str(exc)) from exc
            return
        if payload.get("domain_contract_version") != "1" or payload.get("schema") != "bms.domain-experiment.v1":
            raise ValidationFailure("unsupported domain_contract_version")
        domain_payload = payload.get("domain_payload")
        if not isinstance(domain_payload, dict):
            raise ValidationFailure("domain_payload must be an object")
        if domain_kind == "ngs_molbio":
            if domain_payload != {"schema": "bms.ngs-molbio-experiment.v1"}:
                raise ValidationFailure("ngs_molbio domain_payload has unsupported or unknown fields")
            return
        expected_keys = {
            "schema",
            "experiment_mode",
            "targets",
            "scientific_objective",
            "design_constraints",
            "planned_capabilities",
            "comparison_groups",
            "validation_strategy",
        }
        if set(domain_payload) != expected_keys:
            raise ValidationFailure("protein_in_silico domain_payload fields do not match the frozen contract")
        if domain_payload.get("schema") != "bms.protein-in-silico-experiment.v1":
            raise ValidationFailure("protein_in_silico domain_payload schema is invalid")
        if domain_payload.get("experiment_mode") not in {
            "exploration", "design", "redesign", "prediction", "validation", "comparison", "simulation", "analysis"
        }:
            raise ValidationFailure("protein_in_silico experiment_mode is invalid")
        targets = domain_payload.get("targets")
        if not isinstance(targets, list):
            raise ValidationFailure("protein_in_silico targets must be an array")
        target_keys = {"target_id", "label", "entity_receipt_ids", "role"}
        target_roles = {"target", "binder", "partner", "template", "reference", "control", "other"}
        for target in targets:
            if not isinstance(target, dict) or set(target) != target_keys:
                raise ValidationFailure("protein_in_silico target fields do not match the frozen contract")
            if not str(target.get("target_id") or "").strip() or target.get("role") not in target_roles:
                raise ValidationFailure("protein_in_silico target identity or role is invalid")
            receipt_ids = target.get("entity_receipt_ids")
            if not isinstance(receipt_ids, list) or any(not isinstance(value, str) or not value for value in receipt_ids):
                raise ValidationFailure("protein_in_silico target receipt IDs are invalid")
        if not isinstance(domain_payload.get("scientific_objective"), str):
            raise ValidationFailure("protein_in_silico scientific_objective must be a string")
        design_constraints = domain_payload.get("design_constraints")
        if design_constraints != []:
            raise ValidationFailure("protein_in_silico design_constraints must be exactly []")
        comparison_groups = domain_payload.get("comparison_groups")
        if not isinstance(comparison_groups, list) or any(
            not isinstance(value, dict) for value in comparison_groups
        ):
            raise ValidationFailure("protein_in_silico comparison_groups must contain objects")
        for field in ("planned_capabilities", "validation_strategy"):
            values = domain_payload.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ValidationFailure(f"protein_in_silico {field} must contain non-empty capability IDs")


def _hierarchy_reference_ids(
    payload: dict[str, Any],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    receipt_references: list[tuple[str, str]] = []
    dataset_references: list[tuple[str, str]] = []
    dataset_revision_references: list[tuple[str, str]] = []

    def collect(field: str, *, role: str, target: list[tuple[str, str]], source: dict[str, Any]) -> None:
        values = source.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValidationFailure(f"{field} must contain non-empty resource IDs")
        target.extend((role, value) for value in values)

    schema = payload.get("schema")
    if schema == "bms.global-experiment.v1":
        collect(
            "shared_source_receipt_ids",
            role="shared_source_receipt",
            target=receipt_references,
            source=payload,
        )
        collect("shared_dataset_ids", role="shared_dataset", target=dataset_references, source=payload)
    elif schema in {"bms.domain-experiment.v1", "bms.domain-experiment.v2"}:
        collect("source_receipt_ids", role="source_receipt", target=receipt_references, source=payload)
        if schema == "bms.domain-experiment.v1":
            collect("dataset_ids", role="dataset", target=dataset_references, source=payload)
        else:
            collect(
                "dataset_revision_ids",
                role="dataset_revision",
                target=dataset_revision_references,
                source=payload,
            )
        domain_payload = payload.get("domain_payload")
        if isinstance(domain_payload, dict):
            targets = domain_payload.get("targets", [])
            if isinstance(targets, list):
                for target_index, target_payload in enumerate(targets):
                    if isinstance(target_payload, dict):
                        collect(
                            "entity_receipt_ids",
                            role=f"target_entity_receipt:{target_index}",
                            target=receipt_references,
                            source=target_payload,
                        )
    return receipt_references, dataset_references, dataset_revision_references


async def _resolve_hierarchy_references(
    session: AsyncSession,
    *,
    workspace_id: str,
    aggregate_id: str,
    parent_id: str | None,
    payload: dict[str, Any],
) -> list[dict[str, str | int]]:
    receipt_references, dataset_references, dataset_revision_references = _hierarchy_reference_ids(payload)
    bindings: list[dict[str, str | int]] = []
    exact_v2_authority = payload.get("schema") == "bms.domain-experiment.v2"
    allowed_authority_ids = {workspace_id, aggregate_id}
    if parent_id is not None:
        allowed_authority_ids.add(parent_id)

    receipt_ids = {receipt_id for _role, receipt_id in receipt_references}
    receipts = (
        (
            await session.execute(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.id.in_(receipt_ids)
                )
            )
        ).scalars().all()
        if receipt_ids
        else []
    )
    receipts_by_id = {receipt.id: receipt for receipt in receipts}
    if set(receipts_by_id) != receipt_ids:
        raise ValidationFailure("one or more hierarchy receipt references are unknown")
    receipt_ordinals: dict[str, int] = {}
    for role, receipt_id in receipt_references:
        receipt = receipts_by_id[receipt_id]
        receipt_resource = await session.get(ExperimentResource, receipt.resource_id)
        if (
            receipt_resource is None
            or receipt_resource.kind != "external_entity_receipt"
            or receipt_resource.workspace_id != workspace_id
            or receipt_resource.archived_at is not None
            or (
                exact_v2_authority
                and receipt_resource.lifecycle_owner_id not in allowed_authority_ids
            )
        ):
            raise ValidationFailure("hierarchy receipt resource is unavailable or belongs to another project")
        if receipt.workspace_id != workspace_id:
            raise ValidationFailure("hierarchy receipt reference belongs to another project")
        if receipt.availability != "available":
            raise ValidationFailure("hierarchy receipt reference is not verified as available")
        authority = str(receipt.verification_authority or "").strip()
        if (
            not authority
            or authority in {"legacy_unverified", "caller_unverified"}
            or authority.startswith("unverified:")
        ):
            raise ValidationFailure("hierarchy receipt reference has no durable server verification authority")
        try:
            acknowledgement = json.loads(receipt.acknowledgement_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValidationFailure("hierarchy receipt acknowledgement is malformed") from exc
        expected_acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "store_id": receipt.store_id,
            "entity_kind": receipt.entity_kind,
            "entity_id": receipt.entity_id,
            "entity_revision_id": receipt.generation_or_revision,
            "content_digest": receipt.content_digest,
            "availability": receipt.availability,
            "verifier_id": authority,
        }
        if not isinstance(acknowledgement, dict) or any(
            str(acknowledgement.get(field) or "") != str(value)
            for field, value in expected_acknowledgement.items()
        ):
            raise ValidationFailure("hierarchy receipt acknowledgement does not match persisted authority")
        if len(receipt.content_digest) != 64 or any(
            character not in "0123456789abcdef" for character in receipt.content_digest
        ):
            raise ValidationFailure("hierarchy receipt has no immutable digest")
        ordinal = receipt_ordinals.get(role, 0)
        receipt_ordinals[role] = ordinal + 1
        bindings.append(
            {
                "role": role,
                "ordinal": ordinal,
                "target_resource_id": receipt.id,
                "expected_sha256": receipt.content_digest,
            }
        )

    dataset_ordinals: dict[str, int] = {}
    for role, dataset_id in dataset_references:
        resource = await session.get(ExperimentResource, dataset_id)
        head = await session.get(ExperimentAggregateHead, dataset_id)
        if resource is None or head is None or resource.kind != "dataset" or head.aggregate_kind != "dataset":
            raise ValidationFailure("one or more hierarchy dataset references are unknown")
        if resource.workspace_id != workspace_id or head.workspace_id != workspace_id:
            raise ValidationFailure("hierarchy dataset reference belongs to another project")
        if resource.archived_at is not None or head.lifecycle_state == "archived" or head.current_revision_id is None:
            raise ValidationFailure("hierarchy dataset reference is not available")
        revision = await session.get(ExperimentRevision, head.current_revision_id)
        if revision is None or revision.subject_id != dataset_id:
            raise ValidationFailure("hierarchy dataset reference has no durable server revision authority")
        if revision.payload_sha256 != sha256_text(revision.canonical_payload):
            raise ValidationFailure("hierarchy dataset revision immutable digest does not match")
        ordinal = dataset_ordinals.get(role, 0)
        dataset_ordinals[role] = ordinal + 1
        bindings.append(
            {
                "role": role,
                "ordinal": ordinal,
                "target_resource_id": dataset_id,
                "expected_sha256": revision.payload_sha256,
            }
        )

    dataset_revision_ordinals: dict[str, int] = {}
    for role, revision_id in dataset_revision_references:
        revision_resource = await session.get(ExperimentResource, revision_id)
        revision = await session.get(ExperimentRevision, revision_id)
        if (
            revision_resource is None
            or revision is None
            or revision_resource.kind != "revision"
            or revision_resource.workspace_id != workspace_id
            or revision_resource.archived_at is not None
        ):
            raise ValidationFailure("one or more exact Dataset revision references are unknown")
        dataset_resource = await session.get(ExperimentResource, revision.subject_id)
        dataset_head = await session.get(ExperimentAggregateHead, revision.subject_id)
        if (
            dataset_resource is None
            or dataset_head is None
            or dataset_resource.kind != "dataset"
            or dataset_head.aggregate_kind != "dataset"
            or dataset_resource.workspace_id != workspace_id
            or dataset_head.workspace_id != workspace_id
            or dataset_resource.archived_at is not None
            or dataset_head.lifecycle_state == "archived"
            or dataset_head.parent_id not in allowed_authority_ids
            or revision_resource.lifecycle_owner_id != revision.subject_id
        ):
            raise ValidationFailure("exact Dataset revision belongs to an unavailable or unauthorized hierarchy")
        if revision.payload_sha256 != sha256_text(revision.canonical_payload):
            raise ValidationFailure("exact Dataset revision immutable digest does not match")
        ordinal = dataset_revision_ordinals.get(role, 0)
        dataset_revision_ordinals[role] = ordinal + 1
        bindings.append(
            {
                "role": role,
                "ordinal": ordinal,
                "target_resource_id": revision_id,
                "expected_sha256": revision.payload_sha256,
            }
        )
    return bindings


def _validate_lifecycle_transition(
    aggregate_kind: str,
    current_status: str,
    requested_status: str,
    lifecycle_operation: str | None,
) -> None:
    if lifecycle_operation == "archive":
        if current_status == "archived" or requested_status != "archived":
            raise ValidationFailure(
                f"invalid lifecycle transition for {aggregate_kind}: {current_status} -> {requested_status}"
            )
        return
    if lifecycle_operation == "restore":
        if current_status != "archived" or requested_status == "archived":
            raise ValidationFailure(
                f"invalid lifecycle transition for {aggregate_kind}: {current_status} -> {requested_status}"
            )
        return
    if lifecycle_operation is not None:
        raise ValidationFailure(f"unsupported lifecycle operation: {lifecycle_operation}")
    transitions = (
        PROJECT_LIFECYCLE_TRANSITIONS
        if aggregate_kind == "workspace"
        else EXPERIMENT_LIFECYCLE_TRANSITIONS
    )
    if requested_status not in transitions.get(current_status, set()):
        raise ValidationFailure(
            f"invalid lifecycle transition for {aggregate_kind}: {current_status} -> {requested_status}"
        )


async def _save_revision(
    session: AsyncSession,
    *,
    aggregate_id: str,
    aggregate_kind: str,
    payload: dict[str, Any],
    expected_head_generation: int,
    lifecycle_operation: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> ExperimentRevision:
    head = await _head(session, aggregate_id, aggregate_kind)
    if head.head_generation != expected_head_generation:
        raise RevisionConflict(
            f"{aggregate_kind} head generation conflict: expected {expected_head_generation}, current {head.head_generation}"
        )
    workspace_id = await _resource_workspace(session, aggregate_id)
    hierarchy_bindings: list[dict[str, str | int]] = []
    previous_status: str | None = None
    if aggregate_kind == "workflow":
        plan_authority = await load_workflow_plan_authority(
            session,
            aggregate_id,
            required=False,
        )
        if plan_authority is None:
            _validate_workflow_payload(payload)
        else:
            validate_workflow_payload_for_plan(payload, plan_authority[1])
    elif aggregate_kind in {"workspace", "experiment", "domain_experiment"}:
        _validate_hierarchy_payload(aggregate_kind, payload)
        current_payload: dict[str, Any] | None = None
        if head.current_revision_id:
            current_revision = await session.get(ExperimentRevision, head.current_revision_id)
            if current_revision is None or current_revision.subject_id != aggregate_id:
                raise ValidationFailure("aggregate current revision is unavailable or belongs to another aggregate")
            current_payload = json.loads(current_revision.canonical_payload)
            if not isinstance(current_payload, dict):
                raise ValidationFailure("aggregate current revision payload is invalid")
            previous_status = str(current_payload.get("status") or "")
            if previous_status != head.lifecycle_state:
                raise ValidationFailure("aggregate lifecycle projection is inconsistent with current revision")
            _validate_lifecycle_transition(
                aggregate_kind,
                previous_status,
                str(payload["status"]),
                lifecycle_operation,
            )
        if aggregate_kind == "domain_experiment" and current_payload is not None:
            if current_payload.get("domain_kind") != payload.get("domain_kind"):
                raise ValidationFailure("domain_kind is immutable; create a new Domain Experiment")
            if current_payload.get("schema") != payload.get("schema"):
                raise ValidationFailure("Domain Experiment schema is immutable")
            if current_payload.get("domain_contract_version") != payload.get("domain_contract_version"):
                raise ValidationFailure("Domain Experiment contract version is immutable")
        if payload.get("status") == "archived" and lifecycle_operation != "archive":
            raise ValidationFailure("archival is a lifecycle operation; use the archive route")
        if head.lifecycle_state == "archived" and lifecycle_operation != "restore":
            raise ValidationFailure("archived aggregates must be restored before revision")
        hierarchy_bindings = await _resolve_hierarchy_references(
            session,
            workspace_id=workspace_id,
            aggregate_id=aggregate_id,
            parent_id=head.parent_id,
            payload=payload,
        )
    payload_json = canonical_json(payload)
    graph_json = canonical_json(
        {
            "nodes": payload.get("nodes", []),
            "edges": payload.get("edges", []),
            "references": hierarchy_bindings,
        }
    )
    parent_revision_id = head.current_revision_id
    next_generation = expected_head_generation + 1
    revision: ExperimentRevision
    try:
        async with session.begin_nested():
            revision_resource = await _resource(
                session,
                kind="revision",
                workspace_id=workspace_id,
                lifecycle_owner_id=aggregate_id,
            )
            revision = ExperimentRevision(
                resource_id=revision_resource.id,
                subject_id=aggregate_id,
                revision_number=next_generation,
                parent_revision_id=parent_revision_id,
                schema_name=str(payload.get("schema") or f"bms.workflow.{aggregate_kind}.v1"),
                schema_version=(
                    str(payload.get("contract_version") or "1")
                    if aggregate_kind == "workflow"
                    else "1"
                ),
                canonical_payload=payload_json,
                payload_sha256=sha256_text(payload_json),
                dependency_graph_sha256=sha256_text(graph_json),
                provenance_json=canonical_json(provenance if provenance is not None else payload.get("provenance", {})),
                created_at=now(),
            )
            session.add(revision)
            await session.flush()
            for binding in hierarchy_bindings:
                session.add(
                    ExperimentRevisionEdge(
                        revision_id=revision.resource_id,
                        target_resource_id=str(binding["target_resource_id"]),
                        role=str(binding["role"]),
                        ordinal=int(binding["ordinal"]),
                        expected_sha256=str(binding["expected_sha256"]),
                        metadata_json=canonical_json({"authority": "server_resolved"}),
                    )
                )
            if aggregate_kind == "workflow":
                for ordinal, node in enumerate(payload["nodes"]):
                    session.add(
                        ExperimentWorkflowRevisionNode(
                            revision_id=revision.resource_id,
                            ordinal=ordinal,
                            node_id=str(node["id"]),
                            node_kind=str(node["kind"]),
                            node_json=canonical_json(node),
                        )
                    )
                for ordinal, edge in enumerate(payload["edges"]):
                    session.add(
                        ExperimentWorkflowRevisionEdge(
                            revision_id=revision.resource_id,
                            ordinal=ordinal,
                            source_node_id=str(edge.get("source", edge.get("from"))),
                            target_node_id=str(edge.get("target", edge.get("to"))),
                            edge_json=canonical_json(edge),
                        )
                    )
            await session.flush()
            changed = await session.execute(
                update(ExperimentAggregateHead)
                .where(
                    ExperimentAggregateHead.aggregate_id == aggregate_id,
                    ExperimentAggregateHead.aggregate_kind == aggregate_kind,
                    ExperimentAggregateHead.head_generation == expected_head_generation,
                    ExperimentAggregateHead.current_revision_id == parent_revision_id,
                )
                .values(
                    current_revision_id=revision.resource_id,
                    head_generation=next_generation,
                    lifecycle_state=(
                        str(payload["status"])
                        if aggregate_kind in {"workspace", "experiment", "domain_experiment"}
                        else "validated"
                    ),
                    updated_at=now(),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise RevisionConflict("aggregate head changed while saving revision")
    except (RevisionConflict, IntegrityError) as exc:
        await session.refresh(head)
        raise RevisionConflict(
            f"{aggregate_kind} head generation conflict: expected {expected_head_generation}, current {head.head_generation}"
        ) from exc
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=revision.resource_id,
        event_type="immutable_revision_saved",
        generation=revision.revision_number,
        payload={"subject_id": aggregate_id, "payload_sha256": revision.payload_sha256},
    )
    requested_status = str(payload.get("status") or "")
    if previous_status is not None and previous_status != requested_status:
        add_audit_event(
            session,
            workspace_id=workspace_id,
            resource_id=aggregate_id,
            event_type="aggregate_lifecycle_transitioned",
            generation=revision.revision_number,
            payload={"from": previous_status, "to": requested_status},
        )
    await session.refresh(head)
    return revision


async def save_workflow_revision(
    session: AsyncSession,
    workflow_id: str,
    *,
    expected_head_generation: int,
    change_summary: str | None = None,
) -> ExperimentRevision:
    await _head(session, workflow_id, "workflow")
    result = await session.execute(
        select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == workflow_id)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise NotFound(f"workflow draft not found: {workflow_id}")
    payload = json.loads(draft.canonical_payload)
    plan_authority = await load_workflow_plan_authority(session, workflow_id, required=False)
    if plan_authority is not None:
        validate_workflow_payload_for_plan(payload, plan_authority[1])
    revision = await _save_revision(
        session,
        aggregate_id=workflow_id,
        aggregate_kind="workflow",
        payload=payload,
        expected_head_generation=expected_head_generation,
        provenance={"change_summary": change_summary} if change_summary is not None else None,
    )
    draft.base_revision_id = revision.resource_id
    draft.updated_at = now()
    return revision


async def clone_workflow(
    session: AsyncSession,
    source_workflow_id: str,
    *,
    source_revision_id: str | None = None,
    name: str | None = None,
) -> ExperimentAggregateHead:
    source = await _head(session, source_workflow_id, "workflow")
    revision_id = source_revision_id or source.current_revision_id
    if revision_id is None:
        raise ValidationFailure("workflow has no immutable revision to clone")
    revision = await session.get(ExperimentRevision, revision_id)
    if revision is None or revision.subject_id != source_workflow_id:
        raise NotFound("source workflow revision not found")
    workspace_id = await _resource_workspace(session, source_workflow_id)
    clone = await create_workflow(
        session,
        workspace_id,
        name or f"{source.display_name} (clone)",
        json.loads(revision.canonical_payload).get("workflow_family", source.description),
        experiment_id=source.parent_id,
    )
    draft = (
        await session.execute(
            select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == clone.aggregate_id)
        )
    ).scalar_one()
    draft.canonical_payload = revision.canonical_payload
    draft.base_revision_id = revision.resource_id
    draft.generation = 1
    draft.updated_at = now()
    clone_revision = await _save_revision(
        session,
        aggregate_id=clone.aggregate_id,
        aggregate_kind="workflow",
        payload=json.loads(revision.canonical_payload),
        expected_head_generation=0,
    )
    draft.base_revision_id = clone_revision.resource_id
    session.add(
        ExperimentLineageEdge(
            id=new_id("fork"),
            workspace_id=workspace_id,
            source_resource_id=clone.aggregate_id,
            target_resource_id=revision.resource_id,
            edge_mode="forked_from",
            edge_key="origin-revision",
            metadata_json=canonical_json({"source_workflow_id": source_workflow_id}),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=clone.aggregate_id,
        event_type="workflow_cloned",
        generation=clone.head_generation,
        payload={
            "source_workflow_id": source_workflow_id,
            "source_revision_id": revision.resource_id,
            "clone_revision_id": clone_revision.resource_id,
        },
    )
    await session.flush()
    return clone


async def archive_aggregate(
    session: AsyncSession,
    aggregate_id: str,
    *,
    expected_head_generation: int | None = None,
) -> ExperimentAggregateHead:
    head = await _head(session, aggregate_id)
    expected_generation = head.head_generation if expected_head_generation is None else expected_head_generation
    if head.head_generation != expected_generation:
        raise RevisionConflict("aggregate head changed before archive")
    resource = await session.get(ExperimentResource, aggregate_id)
    if resource is None:
        raise NotFound(f"aggregate not found: {aggregate_id}")
    if head.lifecycle_state == "archived" or resource.archived_at is not None:
        raise ValidationFailure("aggregate is already archived")
    if head.aggregate_kind in {"workspace", "experiment", "domain_experiment"}:
        if head.current_revision_id is None:
            raise ValidationFailure("hierarchy aggregate has no immutable revision")
        current_revision = await session.get(ExperimentRevision, head.current_revision_id)
        if current_revision is None:
            raise ValidationFailure("hierarchy aggregate current revision is unavailable")
        payload = json.loads(current_revision.canonical_payload)
        payload["status"] = "archived"
        payload["change_summary"] = "archived"
        await _save_revision(
            session,
            aggregate_id=aggregate_id,
            aggregate_kind=head.aggregate_kind,
            payload=payload,
            expected_head_generation=expected_generation,
            lifecycle_operation="archive",
        )
        await session.refresh(head)
    else:
        head.lifecycle_state = "archived"
        head.updated_at = now()
    resource.archived_at = now()
    workspace_id = await _resource_workspace(session, aggregate_id)
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=aggregate_id,
        event_type="aggregate_archived",
        generation=head.head_generation,
        payload={},
    )
    await session.flush()
    return head


async def restore_aggregate(
    session: AsyncSession,
    aggregate_id: str,
    *,
    expected_head_generation: int | None = None,
) -> ExperimentAggregateHead:
    head = await _head(session, aggregate_id)
    expected_generation = head.head_generation if expected_head_generation is None else expected_head_generation
    if head.head_generation != expected_generation:
        raise RevisionConflict("aggregate head changed before restore")
    resource = await session.get(ExperimentResource, aggregate_id)
    if resource is None:
        raise NotFound(f"aggregate not found: {aggregate_id}")
    if head.lifecycle_state != "archived" and resource.archived_at is None:
        raise ValidationFailure("aggregate is not archived")
    lifecycle_state = "draft"
    if head.aggregate_kind in {"workspace", "experiment", "domain_experiment"}:
        if head.current_revision_id is None:
            raise ValidationFailure("hierarchy aggregate has no immutable revision")
        archived_revision = await session.get(ExperimentRevision, head.current_revision_id)
        if archived_revision is None:
            raise ValidationFailure("hierarchy aggregate current revision is unavailable")
        archived_payload = json.loads(archived_revision.canonical_payload)
        prior_revision = (
            await session.get(ExperimentRevision, archived_revision.parent_revision_id)
            if archived_revision.parent_revision_id
            else None
        )
        prior_payload = json.loads(prior_revision.canonical_payload) if prior_revision is not None else {}
        lifecycle_state = str(prior_payload.get("status") or "draft")
        if lifecycle_state == "archived":
            lifecycle_state = "draft"
        archived_payload["status"] = lifecycle_state
        archived_payload["change_summary"] = "restored"
        await _save_revision(
            session,
            aggregate_id=aggregate_id,
            aggregate_kind=head.aggregate_kind,
            payload=archived_payload,
            expected_head_generation=expected_generation,
            lifecycle_operation="restore",
        )
        await session.refresh(head)
    else:
        head.lifecycle_state = "draft"
        head.updated_at = now()
    resource.archived_at = None
    workspace_id = await _resource_workspace(session, aggregate_id)
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=aggregate_id,
        event_type="aggregate_restored",
        generation=head.head_generation,
        payload={"lifecycle_state": lifecycle_state},
    )
    await session.flush()
    return head


async def save_hierarchy_revision(
    session: AsyncSession,
    aggregate_id: str,
    aggregate_kind: str,
    payload: dict[str, Any],
    *,
    expected_head_generation: int,
) -> ExperimentRevision:
    if aggregate_kind not in {"workspace", "experiment", "domain_experiment"}:
        raise ValidationFailure("unsupported hierarchy aggregate kind")
    return await _save_revision(
        session,
        aggregate_id=aggregate_id,
        aggregate_kind=aggregate_kind,
        payload=payload,
        expected_head_generation=expected_head_generation,
    )


async def append_research_record(
    session: AsyncSession,
    *,
    workspace_id: str,
    subject_resource_id: str,
    record_kind: str,
    body: str,
    author: str | None = None,
    source_receipt_ids: list[str] | None = None,
    supersedes_record_id: str | None = None,
) -> ExperimentResearchRecord:
    await _workspace(session, workspace_id)
    if record_kind not in RESEARCH_RECORD_KINDS:
        raise ValidationFailure("record_kind must be note, observation, decision, or conclusion")
    if not isinstance(body, str) or not body.strip():
        raise ValidationFailure("research record body must not be empty")
    subject = await session.get(ExperimentResource, subject_resource_id)
    if subject is None:
        raise NotFound(f"research record subject not found: {subject_resource_id}")
    subject_workspace = subject.id if subject.kind == "workspace" else subject.workspace_id
    if subject_workspace != workspace_id or subject.kind not in {"workspace", "experiment", "domain_experiment"}:
        raise ValidationFailure("research record subject belongs to another project or is not a hierarchy aggregate")
    if supersedes_record_id is not None:
        prior = await session.get(ExperimentResearchRecord, supersedes_record_id)
        if prior is None:
            raise NotFound(f"research record not found: {supersedes_record_id}")
        if prior.subject_resource_id != subject_resource_id or prior.workspace_id != workspace_id:
            raise ValidationFailure("replacement record must keep the same project scope")
    receipt_ids = source_receipt_ids or []
    if any(not isinstance(receipt_id, str) or not receipt_id for receipt_id in receipt_ids):
        raise ValidationFailure("source_receipt_ids must contain non-empty strings")
    if receipt_ids:
        receipts = (
            await session.execute(
                select(ExperimentExternalEntityReceipt).where(
                    ExperimentExternalEntityReceipt.id.in_(receipt_ids)
                )
            )
        ).scalars().all()
        by_id = {receipt.id: receipt for receipt in receipts}
        if set(by_id) != set(receipt_ids):
            raise ValidationFailure("one or more source receipts are unknown")
        if any(receipt.workspace_id != workspace_id for receipt in receipts):
            raise ValidationFailure("source receipt belongs to another project")
        if any(receipt.availability != "available" for receipt in receipts):
            raise ValidationFailure("source receipt is not currently verified as available")
        if any(
            receipt.verification_authority in {"legacy_unverified", "caller_unverified"}
            for receipt in receipts
        ):
            raise ValidationFailure("source receipt has no persisted server verification authority")
        for receipt in receipts:
            try:
                acknowledgement = json.loads(receipt.acknowledgement_json or "{}")
            except json.JSONDecodeError as exc:
                raise ValidationFailure("source receipt acknowledgement is invalid") from exc
            if (
                acknowledgement.get("schema") != "bms.global.external-entity-receipt.v1"
                or acknowledgement.get("verifier_id") != receipt.verification_authority
                or acknowledgement.get("store_id") != receipt.store_id
                or acknowledgement.get("entity_kind") != receipt.entity_kind
                or acknowledgement.get("entity_id") != receipt.entity_id
                or str(acknowledgement.get("entity_revision_id")) != receipt.generation_or_revision
                or acknowledgement.get("content_digest") != receipt.content_digest
                or not acknowledgement.get("verifier_id")
                or not acknowledgement.get("source_build_revision")
                or not acknowledgement.get("verified_at")
                or not acknowledgement.get("reopen_uri")
            ):
                raise ValidationFailure("source receipt is not server verified")
    resource = await _resource(
        session,
        kind="research_record",
        workspace_id=workspace_id,
        lifecycle_owner_id=subject_resource_id,
    )
    record = ExperimentResearchRecord(
        resource_id=resource.id,
        workspace_id=workspace_id,
        subject_resource_id=subject_resource_id,
        record_kind=record_kind,
        body=body,
        author=author,
        source_receipt_ids_json=canonical_json(receipt_ids),
        supersedes_record_id=supersedes_record_id,
        created_at=now(),
    )
    session.add(record)
    await session.flush()
    subject_head = await session.get(ExperimentAggregateHead, subject_resource_id)
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=record.resource_id,
        event_type="research_record_appended",
        generation=subject_head.head_generation if subject_head is not None else 0,
        payload={
            "subject_resource_id": subject_resource_id,
            "record_kind": record_kind,
            "supersedes_record_id": supersedes_record_id,
        },
    )
    return record


async def save_dataset_revision(
    session: AsyncSession,
    dataset_id: str,
    payload: dict[str, Any],
    *,
    expected_head_generation: int,
) -> ExperimentRevision:
    revision = await _save_revision(
        session,
        aggregate_id=dataset_id,
        aggregate_kind="dataset",
        payload=payload,
        expected_head_generation=expected_head_generation,
    )
    members = payload.get("members") or []
    if not isinstance(members, list):
        raise ValidationFailure("dataset members must be a list")
    for ordinal, member in enumerate(members):
        if not isinstance(member, dict):
            raise ValidationFailure("dataset members must be objects")
        value = member.get("value", member)
        value_json = canonical_json(value)
        session.add(
            ExperimentDatasetRevisionMember(
                revision_id=revision.resource_id,
                ordinal=ordinal,
                role=str(member.get("role") or "member"),
                semantic_identity=str(member.get("identity") or f"member:{ordinal}"),
                value_json=value_json,
                content_sha256=str(member.get("content_sha256") or sha256_text(value_json)),
                size_bytes=member.get("size_bytes"),
                media_type=member.get("media_type"),
            )
        )
    await session.flush()
    return revision


async def prepare_workflow(
    session: AsyncSession,
    workflow_revision_id: str,
    bindings: dict[str, Any],
    *,
    core_session: AsyncSession | None = None,
) -> ExperimentWorkflowPreparation:
    revision = await session.get(ExperimentRevision, workflow_revision_id)
    if revision is None:
        raise NotFound(f"workflow revision not found: {workflow_revision_id}")
    workflow_resource = await session.get(ExperimentResource, revision.subject_id)
    if workflow_resource is None or workflow_resource.kind != "workflow":
        raise ValidationFailure("preparation requires a workflow revision")
    workspace_id = str(workflow_resource.workspace_id)
    dataset_revision_ids = bindings.get("input_dataset_revision_ids") or []
    if not isinstance(dataset_revision_ids, list):
        raise ValidationFailure("input_dataset_revision_ids must be a list")
    payload = json.loads(revision.canonical_payload)
    plan_authority = await load_workflow_plan_authority(
        session,
        revision.subject_id,
        required=False,
    )
    launch_authority: dict[str, Any] | None = None
    receipt_contracts: list[str] | None = None
    if plan_authority is not None:
        authority_row, capability_contract = plan_authority
        validate_workflow_payload_for_plan(payload, capability_contract)
        receipt_contracts = _capability_receipt_contracts(capability_contract["capability"])
        raw_launch_authority = bindings.get("launch_authority")
        if (
            not isinstance(raw_launch_authority, dict)
            or set(raw_launch_authority) != PLAN_LAUNCH_AUTHORITY_FIELDS
            or any(
                not isinstance(raw_launch_authority.get(field), str)
                or not raw_launch_authority[field]
                for field in PLAN_LAUNCH_AUTHORITY_FIELDS - PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS
            )
            or any(
                isinstance(raw_launch_authority.get(field), bool)
                or not isinstance(raw_launch_authority.get(field), int)
                or raw_launch_authority[field] < 1
                for field in PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS
            )
            or raw_launch_authority["domain_revision_id"] != authority_row.expected_domain_revision_id
            or raw_launch_authority["capability_contract_sha256"] != authority_row.capability_contract_sha256
        ):
            raise ValidationFailure("preparation requires the exact pinned Plan launch authority")
        launch_authority = copy.deepcopy(raw_launch_authority)
    scheduler_payload = payload.get("scheduler")
    reasons: list[str] = []
    try:
        if plan_authority is None:
            _validate_workflow_payload(payload)
        else:
            validate_workflow_payload_for_plan(payload, plan_authority[1])
    except ValidationFailure as exc:
        reasons.append(str(exc))
    source_receipt_ids = payload.get("source_receipt_ids") or []
    if not isinstance(source_receipt_ids, list):
        raise ValidationFailure("workflow source_receipt_ids must be a list")
    if source_receipt_ids and receipt_contracts is None:
        raise ValidationFailure(
            "source-bearing workflow has no immutable pinned source receipt contract authority"
        )
    from services.ngs_molbio_preparation_authority import (
        PreparationInputAuthorityError,
        build_preparation_input_authority,
    )

    try:
        input_authority = await build_preparation_input_authority(
            session,
            core_session,
            workflow_revision_id=workflow_revision_id,
            dataset_revision_ids=dataset_revision_ids,
            source_receipt_ids=source_receipt_ids,
            receipt_contracts=receipt_contracts,
        )
    except PreparationInputAuthorityError as exc:
        raise ValidationFailure(str(exc)) from exc
    _validate_input_receipt_contract_authority(input_authority, receipt_contracts)
    if not isinstance(scheduler_payload, dict):
        scheduler_payload = {}
        reasons.append("workflow revision has no scheduler payload")
    else:
        scheduler_payload = copy.deepcopy(scheduler_payload)
        for field in ("name", "model_id", "mode", "params"):
            if field not in scheduler_payload:
                reasons.append(f"scheduler payload missing {field}")
        if not isinstance(scheduler_payload.get("params", {}), dict):
            reasons.append("scheduler params must be an object")
        elif payload.get("workflow_family") == "typed_core_job":
            model_id = scheduler_payload.get("model_id")
            mode = scheduler_payload.get("mode")
            params = scheduler_payload.get("params")
            if isinstance(model_id, str) and isinstance(mode, str) and isinstance(params, dict):
                if model_id == "protein_local_redesign":
                    try:
                        params = prepare_local_redesign_scheduler_params(
                            params,
                            job_name=str(scheduler_payload.get("name") or ""),
                            expected_adapter_id=str(payload.get("adapter_id") or ""),
                        )
                    except ContractError as exc:
                        reasons.append(str(exc))
                    else:
                        scheduler_payload["params"] = params
                reasons.extend(get_registry().validate_job_params(model_id, mode, params))
        elif payload.get("workflow_family") == "conformational_mapping":
            scheduler_payload["params"]["cm_source_receipt_ids"] = list(
                payload.get("source_receipt_ids") or []
            )
    normalized = {
        "workflow_revision_id": workflow_revision_id,
        "input_dataset_revision_ids": [str(value) for value in dataset_revision_ids],
        "input_authority": input_authority,
        "workflow": payload,
    }
    if launch_authority is not None:
        normalized["launch_authority"] = launch_authority
    normalized_json = canonical_json(normalized)
    validation_status = "valid" if not reasons else "invalid"
    receipt = {
        "schema": "bms.experiment.validation.v1",
        "status": validation_status,
        "validator": "global-workflow-contract.v2",
        "reasons": reasons,
        "workflow_revision_id": workflow_revision_id,
        "normalized_request_sha256": sha256_text(normalized_json),
        "input_authority_sha256": sha256_text(canonical_json(input_authority)),
    }
    if plan_authority is not None:
        receipt["capability_contract_sha256"] = plan_authority[0].capability_contract_sha256
        receipt["launch_authority"] = launch_authority
    preparation_resource_id = new_id("preparation")
    validation_resource_id = new_id("validation")
    preparation_resource = await _resource(
        session,
        kind="preparation",
        workspace_id=workspace_id,
        lifecycle_owner_id=workflow_resource.id,
        resource_id=preparation_resource_id,
    )
    validation_resource = await _resource(
        session,
        kind="validation",
        workspace_id=workspace_id,
        lifecycle_owner_id=preparation_resource_id,
        resource_id=validation_resource_id,
    )
    receipt_json = canonical_json(receipt)
    validation = ExperimentValidation(
        resource_id=validation_resource.id,
        subject_resource_id=preparation_resource.id,
        validator_name="global-workflow-contract",
        validator_version="v2",
        outcome="valid" if validation_status == "valid" else "invalid",
        input_graph_sha256=revision.dependency_graph_sha256,
        receipt_json=receipt_json,
        receipt_sha256=sha256_text(receipt_json),
        created_at=now(),
    )
    preparation = ExperimentWorkflowPreparation(
        resource_id=preparation_resource.id,
        workspace_id=workspace_id,
        workflow_revision_id=workflow_revision_id,
        normalized_request_json=normalized_json,
        normalized_request_sha256=sha256_text(normalized_json),
        scheduler_payload_json=canonical_json(scheduler_payload),
        validation_status=validation_status,
        validation_receipt_json=receipt_json,
        validation_resource_id=validation_resource.id,
        expected_cardinality=payload.get("expected_cardinality") if isinstance(payload.get("expected_cardinality"), int) else None,
        created_at=now(),
        prepared_at=now() if validation_status == "valid" else None,
    )
    session.add(validation)
    session.add(preparation)
    await session.flush()
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=preparation.resource_id,
        event_type="workflow_prepared",
        generation=0,
        payload={"workflow_revision_id": workflow_revision_id, "validation_status": validation_status},
    )
    return preparation


async def validate_preparation_authority(
    session: AsyncSession,
    preparation: ExperimentWorkflowPreparation,
    *,
    core_session: AsyncSession | None = None,
) -> None:
    try:
        normalized = json.loads(preparation.normalized_request_json)
        scheduler = json.loads(preparation.scheduler_payload_json)
        receipt = json.loads(preparation.validation_receipt_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("preparation validation authority is malformed") from exc
    validation = await session.get(ExperimentValidation, preparation.validation_resource_id)
    if (
        validation is None
        or validation.subject_resource_id != preparation.resource_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
        or validation.receipt_sha256 != sha256_text(validation.receipt_json)
        or receipt != json.loads(validation.receipt_json)
    ):
        raise ValidationFailure("preparation does not match its immutable validation authority")
    if (
        not isinstance(normalized, dict)
        or not isinstance(scheduler, dict)
        or not isinstance(receipt, dict)
        or sha256_text(preparation.normalized_request_json) != preparation.normalized_request_sha256
        or receipt.get("status") != "valid"
        or receipt.get("workflow_revision_id") != preparation.workflow_revision_id
        or receipt.get("normalized_request_sha256") != preparation.normalized_request_sha256
        or normalized.get("workflow_revision_id") != preparation.workflow_revision_id
    ):
        raise ValidationFailure("preparation no longer matches its immutable validation authority")
    workflow_revision = await session.get(ExperimentRevision, preparation.workflow_revision_id)
    if workflow_revision is None:
        raise ValidationFailure("preparation workflow revision authority is unavailable")
    normalized_workflow = normalized.get("workflow")
    if not isinstance(normalized_workflow, dict):
        raise ValidationFailure("preparation workflow authority is unavailable")
    try:
        revision_workflow = json.loads(workflow_revision.canonical_payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("preparation workflow revision authority is malformed") from exc
    normalized_workflow_json = canonical_json(normalized_workflow)
    if (
        not isinstance(revision_workflow, dict)
        or canonical_json(revision_workflow) != workflow_revision.canonical_payload
        or sha256_text(workflow_revision.canonical_payload) != workflow_revision.payload_sha256
        or normalized_workflow_json != workflow_revision.canonical_payload
        or sha256_text(normalized_workflow_json) != workflow_revision.payload_sha256
    ):
        raise ValidationFailure(
            "preparation workflow bytes do not match the immutable workflow revision"
        )
    plan_authority = await load_workflow_plan_authority(
        session,
        workflow_revision.subject_id,
        required=False,
    )
    receipt_contracts: list[str] | None = None
    if plan_authority is not None:
        authority_row, capability_contract = plan_authority
        validate_workflow_payload_for_plan(normalized_workflow, capability_contract)
        receipt_contracts = _capability_receipt_contracts(capability_contract["capability"])
        launch_authority = normalized.get("launch_authority")
        if (
            not isinstance(launch_authority, dict)
            or set(launch_authority) != PLAN_LAUNCH_AUTHORITY_FIELDS
            or any(
                not isinstance(launch_authority.get(field), str)
                or not launch_authority[field]
                for field in PLAN_LAUNCH_AUTHORITY_FIELDS - PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS
            )
            or any(
                isinstance(launch_authority.get(field), bool)
                or not isinstance(launch_authority.get(field), int)
                or launch_authority[field] < 1
                for field in PLAN_LAUNCH_AUTHORITY_GENERATION_FIELDS
            )
            or launch_authority.get("domain_revision_id") != authority_row.expected_domain_revision_id
            or launch_authority.get("capability_contract_sha256") != authority_row.capability_contract_sha256
            or receipt.get("capability_contract_sha256") != authority_row.capability_contract_sha256
            or receipt.get("launch_authority") != launch_authority
        ):
            raise ValidationFailure("preparation no longer matches its pinned Plan authority")
    dataset_revision_ids = normalized.get("input_dataset_revision_ids")
    source_receipt_ids = normalized_workflow.get("source_receipt_ids") or []
    stored_input_authority = normalized.get("input_authority")
    if (
        not isinstance(dataset_revision_ids, list)
        or not isinstance(source_receipt_ids, list)
        or not isinstance(stored_input_authority, dict)
    ):
        raise ValidationFailure("preparation input authority is unavailable")
    if source_receipt_ids and receipt_contracts is None:
        raise ValidationFailure(
            "source-bearing workflow has no immutable pinned source receipt contract authority"
        )
    from services.ngs_molbio_preparation_authority import (
        PreparationInputAuthorityError,
        build_preparation_input_authority,
    )

    try:
        fresh_input_authority = await build_preparation_input_authority(
            session,
            core_session,
            workflow_revision_id=preparation.workflow_revision_id,
            dataset_revision_ids=dataset_revision_ids,
            source_receipt_ids=source_receipt_ids,
            receipt_contracts=receipt_contracts,
        )
    except PreparationInputAuthorityError as exc:
        raise ValidationFailure(str(exc)) from exc
    _validate_input_receipt_contract_authority(fresh_input_authority, receipt_contracts)
    input_authority_sha256 = sha256_text(canonical_json(fresh_input_authority))
    if (
        fresh_input_authority != stored_input_authority
        or receipt.get("input_authority_sha256") != input_authority_sha256
    ):
        raise ValidationFailure("preparation inputs no longer match their immutable fresh authority")
    raw_expected_scheduler = normalized_workflow.get("scheduler")
    if not isinstance(raw_expected_scheduler, dict):
        raise ValidationFailure("preparation scheduler authority is unavailable")
    expected_scheduler = copy.deepcopy(raw_expected_scheduler)
    if (
        normalized_workflow.get("workflow_family") == "typed_core_job"
        and expected_scheduler.get("model_id") == "protein_local_redesign"
    ):
        expected_params = expected_scheduler.get("params")
        if not isinstance(expected_params, dict):
            raise ValidationFailure("native RFD3 preparation scheduler parameters are malformed")
        try:
            expected_scheduler["params"] = prepare_local_redesign_scheduler_params(
                expected_params,
                job_name=str(expected_scheduler.get("name") or ""),
                expected_adapter_id=str(normalized_workflow.get("adapter_id") or ""),
            )
        except ContractError as exc:
            raise ValidationFailure(str(exc)) from exc
    if normalized_workflow.get("workflow_family") == "conformational_mapping":
        expected_params = expected_scheduler.get("params")
        if not isinstance(expected_params, dict):
            raise ValidationFailure("preparation scheduler parameters are malformed")
        expected_params["cm_source_receipt_ids"] = list(normalized_workflow.get("source_receipt_ids") or [])
    if canonical_json(scheduler) != canonical_json(expected_scheduler):
        raise ValidationFailure("preparation scheduler no longer matches its validated workflow")


async def create_run_group(
    session: AsyncSession,
    workspace_id: str,
    preparation_ids: list[str],
    *,
    idempotency_key: str,
    idempotency_authority: dict[str, Any] | None = None,
    launch_context_ids: dict[str, str] | None = None,
    core_session: AsyncSession | None = None,
    source_domain_id: str | None = None,
) -> ExperimentRunGroup:
    await _workspace(session, workspace_id)
    normalized_preparation_ids = [str(value) for value in preparation_ids]
    launch_context_ids = {} if launch_context_ids is None else launch_context_ids
    if (
        not isinstance(launch_context_ids, dict)
        or any(
            not isinstance(preparation_id, str)
            or not preparation_id
            or not isinstance(context_id, str)
            or not context_id
            for preparation_id, context_id in launch_context_ids.items()
        )
        or len(set(launch_context_ids.values())) != len(launch_context_ids)
    ):
        raise ValidationFailure("launch context mapping is malformed")
    launch_context_ids = dict(launch_context_ids)
    if not normalized_preparation_ids:
        raise ValidationFailure("run group requires at least one preparation")
    if len(set(normalized_preparation_ids)) != len(normalized_preparation_ids):
        raise ValidationFailure("run group preparations must be unique")
    result = await session.execute(
        select(ExperimentWorkflowPreparation).where(
            ExperimentWorkflowPreparation.resource_id.in_(normalized_preparation_ids)
        )
    )
    scoped_preparations = {row.resource_id: row for row in result.scalars().all()}
    if len(scoped_preparations) != len(normalized_preparation_ids):
        raise NotFound("one or more preparations were not found")
    if any(row.workspace_id != workspace_id for row in scoped_preparations.values()):
        raise ValidationFailure("all preparations must belong to the selected workspace")
    if any(row.validation_status != "valid" for row in scoped_preparations.values()):
        raise ValidationFailure("run group cannot launch an invalid preparation")
    derived_domain_ids: set[str] = set()
    for preparation in scoped_preparations.values():
        domain_id, _global_experiment_id, _revision, _plan = await _preparation_plan_scope(
            session,
            preparation,
            workspace_id=workspace_id,
        )
        derived_domain_ids.add(domain_id)
    if len(derived_domain_ids) != 1:
        raise ValidationFailure("run group preparations must share one exact immutable Domain")
    derived_domain_id = next(iter(derived_domain_ids))
    if source_domain_id is not None and source_domain_id != derived_domain_id:
        raise ValidationFailure("run group preparations do not match the explicit source Domain")
    request: dict[str, Any] = {
        "workspace_id": workspace_id,
        "preparation_ids": normalized_preparation_ids,
        "source_domain_id": derived_domain_id,
    }
    if launch_context_ids:
        request["launch_context_ids"] = launch_context_ids
    if idempotency_authority is not None:
        request["idempotency_authority"] = idempotency_authority
    request_json = canonical_json(request)
    request_sha256 = sha256_text(request_json)
    legacy_request = dict(request)
    legacy_request.pop("source_domain_id")
    legacy_request_sha256 = sha256_text(canonical_json(legacy_request))
    scope = f"run_group:{workspace_id}"
    existing_claim = await session.get(ExperimentIdempotencyClaim, (scope, idempotency_key))
    if existing_claim is not None:
        replay_request_sha256 = request_sha256
        if existing_claim.request_sha256 == legacy_request_sha256:
            replay_request_sha256 = legacy_request_sha256
        elif existing_claim.request_sha256 != request_sha256:
            raise IdempotencyConflict("idempotency key was already used for a different launch request")
        group = await session.get(ExperimentRunGroup, existing_claim.result_resource_id)
        if (
            group is None
            or group.workspace_id != workspace_id
            or group.request_sha256 != replay_request_sha256
            or existing_claim.response_json
            != canonical_json({"run_group_id": existing_claim.result_resource_id})
        ):
            raise DispatchFailure("idempotency claim points to a malformed or missing run group")
        replay_links = list(
            (
                await session.execute(
                    select(ExperimentRunGroupPreparation)
                    .where(ExperimentRunGroupPreparation.run_group_id == group.resource_id)
                    .order_by(ExperimentRunGroupPreparation.ordinal)
                )
            ).scalars().all()
        )
        if [link.preparation_id for link in replay_links] != normalized_preparation_ids:
            raise DispatchFailure("idempotent run group preparation order no longer matches its request")
        replay_runs = list(
            (
                await session.execute(
                    select(ExperimentWorkflowRun)
                    .where(ExperimentWorkflowRun.run_group_id == group.resource_id)
                    .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
                )
            ).scalars().all()
        )
        if (
            len(replay_runs) != len(normalized_preparation_ids)
            or {run.preparation_id for run in replay_runs}
            != set(normalized_preparation_ids)
        ):
            raise DispatchFailure("idempotent run group has incomplete immutable run authority")
        required_context_preparations: set[str] = set()
        replay_domain_ids: set[str] = set()
        for run in replay_runs:
            preparation = await session.get(ExperimentWorkflowPreparation, run.preparation_id)
            if preparation is None:
                raise DispatchFailure("idempotent run group has no immutable preparation authority")
            await validate_preparation_authority(session, preparation, core_session=core_session)
            authority, _revision, _plan = await _preparation_plan_launch_authority(
                session, preparation, workspace_id=workspace_id
            )
            replay_domain_ids.add(authority["domain_id"])
            attempt = await session.scalar(
                select(ExperimentRunAttempt)
                .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                .order_by(ExperimentRunAttempt.attempt_number)
            )
            if attempt is None or attempt.preparation_id != preparation.resource_id:
                raise DispatchFailure("idempotent run group has incomplete attempt authority")
            if authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
                required_context_preparations.add(preparation.resource_id)
                context_id = launch_context_ids.get(preparation.resource_id)
                if context_id is None:
                    raise IdempotencyConflict("typed handoff preparation has no exact launch context")
                await _validate_bound_launch_context(
                    session,
                    launch_context_id=context_id,
                    preparation=preparation,
                    workspace_id=workspace_id,
                    authority=authority,
                    attempt=attempt,
                )
        if set(launch_context_ids) != required_context_preparations:
            raise IdempotencyConflict("launch contexts must identify every and only typed handoff preparation")
        if replay_domain_ids != {derived_domain_id}:
            raise DispatchFailure("idempotent run group no longer has one exact Domain authority")
        return group
    preparations = scoped_preparations
    launch_authorities: dict[str, dict[str, Any]] = {}
    pending_contexts: dict[str, ExperimentLaunchContext] = {}
    required_context_preparations: set[str] = set()
    launch_domain_ids: set[str] = set()
    for preparation in preparations.values():
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        authority, revision, plan = await _preparation_plan_launch_authority(
            session, preparation, workspace_id=workspace_id
        )
        launch_authorities[preparation.resource_id] = authority
        launch_domain_ids.add(authority["domain_id"])
        if authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
            required_context_preparations.add(preparation.resource_id)
            context_id = launch_context_ids.get(preparation.resource_id)
            if context_id is None:
                raise ValidationFailure("typed handoff preparation requires an exact v2 launch context")
            pending_contexts[preparation.resource_id] = await _retry_launch_context(
                session,
                launch_context_id=context_id,
                preparation=preparation,
                workspace_id=workspace_id,
                domain_id=authority["domain_id"],
                global_experiment_id=authority["global_experiment_id"],
                revision=revision,
                plan=plan,
            )
    if set(launch_context_ids) != required_context_preparations:
        raise ValidationFailure("launch contexts must identify every and only typed handoff preparation")
    if launch_domain_ids != {derived_domain_id}:
        raise ValidationFailure("run group launch authority no longer has one exact Domain")
    group_resource = await _resource(
        session,
        kind="run_group",
        workspace_id=workspace_id,
        lifecycle_owner_id=workspace_id,
    )
    group = ExperimentRunGroup(
        resource_id=group_resource.id,
        workspace_id=workspace_id,
        launch_idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        state="dispatch_pending",
        generation=0,
        created_at=now(),
        updated_at=now(),
    )
    session.add(group)
    await session.flush()
    claim = ExperimentIdempotencyClaim(
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=group.resource_id,
        response_json=canonical_json({"run_group_id": group.resource_id}),
        created_at=now(),
    )
    session.add(claim)
    for ordinal, preparation_id in enumerate(normalized_preparation_ids):
        preparation = preparations[str(preparation_id)]
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        session.add(
            ExperimentRunGroupPreparation(
                run_group_id=group.resource_id,
                preparation_id=preparation.resource_id,
                ordinal=ordinal,
            )
        )
        run_resource = await _resource(
            session,
            kind="workflow_run",
            workspace_id=workspace_id,
            lifecycle_owner_id=group.resource_id,
        )
        workflow_run = ExperimentWorkflowRun(
            resource_id=run_resource.id,
            workspace_id=workspace_id,
            run_group_id=group.resource_id,
            preparation_id=preparation.resource_id,
            node_id="main",
            requiredness="required",
            state="dispatch_pending",
            generation=0,
            created_at=now(),
        )
        session.add(workflow_run)
        await session.flush()
        attempt_resource = await _resource(
            session,
            kind="run_attempt",
            workspace_id=workspace_id,
            lifecycle_owner_id=workflow_run.resource_id,
        )
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
        attempt = ExperimentRunAttempt(
            resource_id=attempt_resource.id,
            workspace_id=workspace_id,
            workflow_run_id=workflow_run.resource_id,
            preparation_id=preparation.resource_id,
            attempt_number=1,
            scheduler_job_id=scheduler_job_identity(attempt_resource.id, scheduler_payload),
            state="pending",
            created_at=now(),
        )
        session.add(attempt)
        await session.flush()
        authority = launch_authorities[preparation.resource_id]
        if authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
            context = pending_contexts[preparation.resource_id]
            context.run_attempt_id = attempt.resource_id
            context.state = "reserved"
            await session.flush()
        elif authority["launch_mode"] in MANAGED_DISPATCH_LAUNCH_MODES:
            outbox_payload = {
                "schema": "bms.experiment.dispatch.v1",
                "run_group_id": group.resource_id,
                "workflow_run_id": workflow_run.resource_id,
                "attempt_id": attempt.resource_id,
                "scheduler_job_id": attempt.scheduler_job_id,
                "workflow_revision_id": preparation.workflow_revision_id,
                "scheduler": scheduler_payload,
            }
            outbox_json = canonical_json(outbox_payload)
            session.add(
                ExperimentDispatchOutbox(
                    id=new_id("dispatch"),
                    workspace_id=workspace_id,
                    run_attempt_id=attempt.resource_id,
                    event_type="materialize_scheduler_job",
                    payload_json=outbox_json,
                    payload_sha256=sha256_text(outbox_json),
                    status="pending",
                    dispatch_attempts=0,
                    created_at=now(),
                    updated_at=now(),
                )
            )
        else:
            raise ValidationFailure("pinned Plan capability has an unknown launch mode")
        session.add(
            ExperimentRunEvent(
                workspace_id=workspace_id,
                workflow_run_id=workflow_run.resource_id,
                sequence_number=1,
                expected_generation=0,
                resulting_generation=0,
                idempotency_key=f"run-group-created:{group.resource_id}",
                event_type="run_group_created",
                payload_json=canonical_json({"run_group_id": group.resource_id}),
                created_at=now(),
            )
        )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise IdempotencyConflict("launch idempotency claim raced with another request") from exc
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=group.resource_id,
        event_type="run_group_launch_intent_created",
        generation=0,
        payload={"preparation_ids": [str(value) for value in preparation_ids], "idempotency_key": idempotency_key},
    )
    return group


TERMINAL_CORE_JOB_STATES = {"completed", "succeeded", "awaiting_input", "failed", "cancelled", "canceled"}
LIVE_CORE_JOB_STATE_MAP = {
    "pending": "dispatched",
    "queued": "dispatched",
    "dispatching": "dispatched",
    "processing": "running",
    "running": "running",
}
LIVE_CORE_JOB_STATES = set(LIVE_CORE_JOB_STATE_MAP)


async def _blocking_run_control_command(
    session: AsyncSession,
    run_group_id: str,
) -> ExperimentRunControlCommand | None:
    """Return durable launch/retry fence authority, excluding conflicts."""
    return await session.scalar(
        select(ExperimentRunControlCommand)
        .where(
            ExperimentRunControlCommand.run_group_id == run_group_id,
            ExperimentRunControlCommand.command_type == "cancel",
            ExperimentRunControlCommand.status.in_(
                {"pending", "leased", "retryable", "applied"}
            ),
        )
        .limit(1)
    )


def _public_runtime_metadata(value: Any) -> Any:
    """Remove filesystem/process launch details from durable public receipts."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in ("path", "directory", "output_dir", "command", "executable")):
                continue
            result[str(key)] = _public_runtime_metadata(child)
        return result
    if isinstance(value, list):
        return [_public_runtime_metadata(child) for child in value]
    return value


def _project_run_group_state(states: list[str]) -> str:
    if not states:
        return "dispatch_pending"
    terminal = {"completed", "failed", "cancelled"}
    if all(state == "cancelled" for state in states):
        return "cancelled"
    if all(state in terminal for state in states):
        if any(state == "failed" for state in states):
            return "failed"
        return "completed"
    if any(state == "running" for state in states):
        return "running"
    if any(state in {"pending", "queued"} for state in states):
        return "queued"
    if all(state == "dispatch_pending" for state in states):
        return "dispatch_pending"
    if all(state == "dispatched" for state in states):
        return "dispatched"
    return "partially_dispatched"


async def derive_run_group_state(
    session: AsyncSession,
    run_group_id: str,
) -> str:
    """Derive one aggregate state from every current WorkflowRun lane."""
    states = list(
        (
            await session.execute(
                select(ExperimentWorkflowRun.state).where(
                    ExperimentWorkflowRun.run_group_id == run_group_id
                )
            )
        ).scalars().all()
    )
    return _project_run_group_state(states)


def _core_job_progress(stage_progress: Any) -> dict[str, float | str | None]:
    raw = str(stage_progress or "").strip()
    if raw.endswith("%"):
        try:
            percentage = float(raw[:-1].strip())
        except ValueError:
            percentage = -1.0
        if 0.0 <= percentage <= 100.0:
            return {"kind": "fraction", "value": percentage / 100.0}
    if "/" in raw:
        numerator_text, denominator_text = raw.split("/", 1)
        try:
            numerator = float(numerator_text.strip())
            denominator = float(denominator_text.strip())
        except ValueError:
            numerator = -1.0
            denominator = 0.0
        if denominator > 0.0 and 0.0 <= numerator <= denominator:
            return {"kind": "fraction", "value": numerator / denominator}
    return {"kind": "indeterminate", "value": None}


def _core_job_elapsed_seconds(started_at: Any) -> int:
    if not isinstance(started_at, datetime):
        return 0
    current = datetime.now(started_at.tzinfo) if started_at.tzinfo is not None else datetime.utcnow()
    return max(0, int((current - started_at).total_seconds()))


def _core_job_live_receipt(job: Any, status: str) -> dict[str, Any]:
    return {
        "schema": "bms.experiment.runtime-receipt.v1",
        "job_id": str(job.id),
        "status": status,
        "canonical_state": status,
        "queue_status": str(job.queue_status) if job.queue_status is not None else None,
        "stage": str(job.current_stage) if job.current_stage is not None else None,
        "stage_progress": str(job.stage_progress) if job.stage_progress is not None else None,
        "progress": _core_job_progress(job.stage_progress),
        "started_at": str(job.started_at) if job.started_at is not None else None,
        "elapsed_seconds": _core_job_elapsed_seconds(job.started_at),
        "assigned_gpu": job.assigned_gpu,
        "provenance": _public_runtime_metadata(job.provenance or {}),
    }


async def _attempt_launch_projection_is_durable(
    session: AsyncSession,
    attempt: ExperimentRunAttempt,
) -> bool:
    """Require one complete typed or managed handoff before reading its Job."""
    contexts = list(
        (
            await session.execute(
                select(ExperimentLaunchContext)
                .where(ExperimentLaunchContext.run_attempt_id == attempt.resource_id)
                .limit(2)
            )
        ).scalars().all()
    )
    outboxes = list(
        (
            await session.execute(
                select(ExperimentDispatchOutbox)
                .where(
                    ExperimentDispatchOutbox.run_attempt_id == attempt.resource_id,
                    ExperimentDispatchOutbox.event_type == "materialize_scheduler_job",
                )
                .limit(2)
            )
        ).scalars().all()
    )
    receipt_json = attempt.external_binding_receipt_json
    if not isinstance(receipt_json, str) or not receipt_json:
        return False

    if len(contexts) == 1 and not outboxes:
        context = contexts[0]
        try:
            receipt = json.loads(receipt_json)
        except (TypeError, json.JSONDecodeError):
            return False
        return bool(
            context.contract_version == "2"
            and context.state == "consumed"
            and context.run_attempt_id == attempt.resource_id
            and context.preparation_id == attempt.preparation_id
            and context.canonical_job_id == attempt.scheduler_job_id
            and context.consumed_at is not None
            and context.binding_receipt_json == receipt_json
            and isinstance(receipt, dict)
            and canonical_json(receipt) == receipt_json
            and receipt.get("schema") == "bms.launch-context-binding.v2"
            and receipt.get("launch_context_id") == context.launch_context_id
            and receipt.get("canonical_store_id") == "core.jobs"
            and receipt.get("canonical_job_id") == attempt.scheduler_job_id
            and receipt.get("project_id") == context.project_id
            and receipt.get("global_experiment_id") == context.global_experiment_id
            and receipt.get("domain_experiment_id") == context.domain_experiment_id
            and receipt.get("workflow_id") == context.workflow_id
            and receipt.get("workflow_revision_id") == context.workflow_revision_id
            and receipt.get("return_uri") == context.return_uri
            and receipt.get("preparation_id") == attempt.preparation_id
            and receipt.get("run_attempt_id") == attempt.resource_id
            and receipt.get("normalized_request_sha256") == context.normalized_request_sha256
            and receipt.get("validation_receipt_id") == context.validation_receipt_id
            and receipt.get("validation_receipt_sha256") == context.validation_receipt_sha256
            and receipt.get("verified") is True
        )

    if len(outboxes) == 1 and not contexts:
        outbox = outboxes[0]
        try:
            payload = json.loads(outbox.payload_json)
            receipt = json.loads(receipt_json)
        except (TypeError, json.JSONDecodeError):
            return False
        if (
            outbox.status != "acknowledged"
            or outbox.acknowledgement_json != receipt_json
            or not isinstance(payload, dict)
            or payload.get("schema") != "bms.experiment.dispatch.v1"
            or canonical_json(payload) != outbox.payload_json
            or sha256_text(outbox.payload_json) != outbox.payload_sha256
            or payload.get("attempt_id") != attempt.resource_id
            or payload.get("scheduler_job_id") != attempt.scheduler_job_id
            or not isinstance(receipt, dict)
            or canonical_json(receipt) != receipt_json
        ):
            return False
        external_job_id = receipt.get("external_job_id")
        materialized_job_id = receipt.get("scheduler_job_id")
        if (
            external_job_id is None
            and materialized_job_id is None
            or external_job_id is not None
            and external_job_id != attempt.scheduler_job_id
            or materialized_job_id is not None
            and materialized_job_id != attempt.scheduler_job_id
        ):
            return False
        try:
            expected_handoff, expected_dispatch = _materialization_resource_authority(
                attempt.resource_id,
                payload,
            )
            expected_resource_binding = _public_materialization_resource_binding(
                expected_handoff,
                expected_dispatch,
            )
        except (DispatchFailure, ResourceUsageEvidenceError):
            return False
        binding = receipt.get("acknowledgement")
        if binding is None:
            resource_binding = receipt.get("resource_authority")
            binding_matches = True
        else:
            resource_binding = binding.get("resource_authority") if isinstance(binding, dict) else None
            binding_matches = bool(
                isinstance(binding, dict)
                and binding.get("schema") == "bms.global.external-binding-receipt.v1"
                and binding.get("attempt_id") == attempt.resource_id
                and binding.get("external_job_id") == attempt.scheduler_job_id
            )
        return binding_matches and resource_binding == expected_resource_binding
    return False


async def reconcile_run_group(
    session: AsyncSession,
    core_session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
) -> ExperimentRunGroup:
    """Project authoritative core live and terminal state into global attempts."""
    from database import Job

    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != workspace_id:
        raise NotFound("run group not found")
    if await _blocking_run_control_command(session, run_group_id) is not None:
        return group
    runs = (
        await session.execute(
            select(ExperimentWorkflowRun).where(ExperimentWorkflowRun.run_group_id == run_group_id)
        )
    ).scalars().all()
    changed = False
    for run in runs:
        attempts = (
            await session.execute(
                select(ExperimentRunAttempt)
                .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                .order_by(ExperimentRunAttempt.attempt_number.desc())
            )
        ).scalars().all()
        if not attempts:
            continue
        attempt = attempts[0]
        if attempt.state in {"completed", "failed", "cancelled"}:
            continue
        # A deterministic Job may survive a typed-claim or outbox crash cut.
        # Recovery owns completion of that durable handoff; reconciliation must
        # not project the Job until the matching source receipt is committed.
        if not await _attempt_launch_projection_is_durable(session, attempt):
            continue
        job = await core_session.get(Job, attempt.scheduler_job_id)
        if job is None:
            continue
        status = str(job.status).lower()
        event_type = ""
        idempotency_key = ""
        if status in TERMINAL_CORE_JOB_STATES:
            projected_state = (
                "completed"
                if status in {"completed", "succeeded", "awaiting_input"}
                else "cancelled"
                if status in {"cancelled", "canceled"}
                else "failed"
            )
            try:
                from services.ngs_molbio_n5 import (
                    ResourceUsageEvidenceUnavailable,
                    persist_producer_resource_usage_evidence,
                )

                resource_usage = await persist_producer_resource_usage_evidence(
                    session,
                    core_job=job,
                    run_attempt_id=attempt.resource_id,
                )
            except ResourceUsageEvidenceUnavailable as exc:
                pending_receipt = {
                    "schema": "bms.experiment-resource-reconciliation.v1",
                    "job_id": attempt.scheduler_job_id,
                    "core_status": status,
                    "state": "producer_resource_evidence_pending",
                    "message": str(exc)[:512],
                }
                pending_json = canonical_json(pending_receipt)
                if attempt.runtime_identity_json != pending_json:
                    attempt.runtime_identity_json = pending_json
                    await session.flush()
                continue
            receipt = {
                "schema": "bms.experiment.terminal-receipt.v1",
                "job_id": attempt.scheduler_job_id,
                "status": status,
                "terminal_state": projected_state,
                "completed_at": str(job.completed_at) if job.completed_at else None,
                "error_message": job.error_message,
                "provenance": _public_runtime_metadata(job.provenance or {}),
                "resource_usage_receipt_id": resource_usage.receipt_id,
                "resource_usage_receipt_sha256": resource_usage.receipt_sha256,
            }
            if projected_state == "completed":
                try:
                    from services.global_experiments.receipts import verify_and_link_terminal_outputs

                    receipt.update(
                        await verify_and_link_terminal_outputs(
                            session,
                            core_session,
                            attempt_id=attempt.resource_id,
                        )
                    )
                except Exception as exc:
                    condition_code = str(getattr(exc, "code", "terminal_output_verification_pending"))
                    pending_receipt = {
                        "schema": "bms.experiment.output-reconciliation.v1",
                        "job_id": attempt.scheduler_job_id,
                        "core_status": status,
                        "state": condition_code if condition_code in {"source_unavailable", "source_contract_unavailable", "source_digest_mismatch", "digest_mismatch"} else "pending",
                        "message": str(exc)[:512],
                        "provenance": _public_runtime_metadata(job.provenance or {}),
                    }
                    pending_json = canonical_json(pending_receipt)
                    if attempt.runtime_identity_json == pending_json:
                        continue
                    attempt.runtime_identity_json = pending_json
                    projected_state = "running"
                    event_type = "core_output_reconciliation_pending"
                    idempotency_key = f"core-output-pending:{attempt.scheduler_job_id}:{sha256_text(pending_json)}"
                    receipt = None
            if receipt is not None:
                terminal_receipt_json = canonical_json(receipt)
                attempt.terminal_receipt_json = terminal_receipt_json
                attempt.terminal_receipt_sha256 = sha256_text(terminal_receipt_json)
                attempt.runtime_identity_json = canonical_json(_public_runtime_metadata(job.provenance or {}))
                event_type = "core_terminal_projected"
                idempotency_key = f"core-terminal:{attempt.scheduler_job_id}:{status}"
        elif status in LIVE_CORE_JOB_STATES:
            projected_state = LIVE_CORE_JOB_STATE_MAP[status]
            receipt = _core_job_live_receipt(job, status)
            receipt_json = canonical_json(receipt)
            if (
                attempt.state == projected_state
                and run.state == projected_state
                and attempt.runtime_identity_json == receipt_json
            ):
                continue
            attempt.runtime_identity_json = receipt_json
            event_type = "core_live_projected"
            idempotency_key = (
                f"core-live:{attempt.scheduler_job_id}:{sha256_text(receipt_json)}"
            )
        else:
            continue
        expected_generation = int(run.generation)
        attempt.state = projected_state
        from services.ngs_molbio_n5 import (
            append_attempt_log_chunk,
            persist_attempt_validation,
        )

        log_payload = {
            "state": projected_state,
            "scheduler_job_id": attempt.scheduler_job_id,
            "terminal_receipt_sha256": attempt.terminal_receipt_sha256,
            "runtime_identity_sha256": sha256_text(attempt.runtime_identity_json) if attempt.runtime_identity_json else None,
            "event_type": event_type,
        }
        await append_attempt_log_chunk(
            session,
            attempt_id=attempt.resource_id,
            stream_name="status",
            content=canonical_json(log_payload or {"state": projected_state}),
            close=projected_state in {"completed", "failed", "cancelled"},
        )
        if projected_state in {"completed", "failed", "cancelled"}:
            if receipt is not None:
                preparation = await session.get(
                    ExperimentWorkflowPreparation,
                    attempt.preparation_id,
                )
                if preparation is None:
                    raise ValidationFailure("attempt preparation authority is missing")
                outcome = (
                    "passed"
                    if projected_state == "completed"
                    else "failed"
                    if projected_state == "failed"
                    else "review"
                )
                await persist_attempt_validation(
                    session,
                    attempt_id=attempt.resource_id,
                    validator_name="canonical-job-terminal-receipt",
                    validator_version="1",
                    outcome=outcome,
                    reason=f"canonical Job projected {projected_state}",
                    input_graph_sha256=preparation.normalized_request_sha256,
                    receipt=receipt,
                )
        run.state = projected_state
        run.generation = expected_generation + 1
        sequence = int(
            (
                await session.execute(
                    select(func.max(ExperimentRunEvent.sequence_number)).where(
                        ExperimentRunEvent.workflow_run_id == run.resource_id
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        session.add(
            ExperimentRunEvent(
                workspace_id=workspace_id,
                workflow_run_id=run.resource_id,
                sequence_number=sequence,
                expected_generation=expected_generation,
                resulting_generation=run.generation,
                idempotency_key=idempotency_key,
                event_type=event_type,
                payload_json=canonical_json(receipt),
                created_at=now(),
            )
        )
        changed = True
    projected_group_state = _project_run_group_state([run.state for run in runs])
    if group.state != projected_group_state:
        group.state = projected_group_state
        changed = True
    if changed:
        group.generation += 1
        group.updated_at = now()
        add_audit_event(
            session,
            workspace_id=workspace_id,
            resource_id=run_group_id,
            event_type="run_group_reconciled",
            generation=group.generation,
            payload={"state": group.state},
        )
        await session.flush()
    return group


async def _preparation_plan_scope(
    session: AsyncSession,
    preparation: ExperimentWorkflowPreparation,
    *,
    workspace_id: str,
) -> tuple[str, str, ExperimentRevision, ExperimentAggregateHead]:
    """Resolve immutable preparation -> revision -> Plan -> Domain authority."""
    revision = await session.get(ExperimentRevision, preparation.workflow_revision_id)
    plan = await session.get(
        ExperimentAggregateHead,
        revision.subject_id if revision is not None else "",
    )
    domain = await session.get(
        ExperimentAggregateHead,
        plan.parent_id if plan is not None and plan.parent_id is not None else "",
    )
    global_experiment = await session.get(
        ExperimentAggregateHead,
        domain.parent_id if domain is not None and domain.parent_id is not None else "",
    )
    if (
        preparation.workspace_id != workspace_id
        or revision is None
        or plan is None
        or plan.aggregate_kind != "workflow"
        or plan.workspace_id != workspace_id
        or not plan.parent_id
        or domain is None
        or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != workspace_id
        or domain.aggregate_id != plan.parent_id
        or not domain.parent_id
        or global_experiment is None
        or global_experiment.aggregate_kind not in {"experiment", "global_experiment"}
        or global_experiment.workspace_id != workspace_id
        or global_experiment.aggregate_id != domain.parent_id
    ):
        raise ValidationFailure("preparation has no exact immutable Plan/Domain authority")
    return str(domain.aggregate_id), str(global_experiment.aggregate_id), revision, plan


def _bounded_canonical_authority(value: dict[str, Any], *, label: str) -> str:
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > MAX_REPLAY_AUTHORITY_BYTES:
        raise ValidationFailure(f"{label} exceeds the bounded durable authority limit")
    return encoded


def _decode_canonical_claim_response(
    claim: ExperimentIdempotencyClaim,
    *,
    schema: str,
    label: str,
) -> dict[str, Any]:
    if (
        not isinstance(claim.response_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", claim.response_sha256) is None
        or sha256_text(claim.response_json) != claim.response_sha256
    ):
        raise DispatchFailure(f"{label} idempotency claim response digest is unavailable or invalid")
    if len(claim.response_json.encode("utf-8")) > MAX_REPLAY_AUTHORITY_BYTES:
        raise DispatchFailure(f"{label} idempotency authority exceeds its bounded limit")
    try:
        response = json.loads(claim.response_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DispatchFailure(f"{label} idempotency claim response is malformed") from exc
    if (
        not isinstance(response, dict)
        or response.get("schema") != schema
        or canonical_json(response) != claim.response_json
    ):
        raise DispatchFailure(f"{label} idempotency claim response is not canonical {schema} authority")
    return response


async def _preparation_plan_launch_authority(
    session: AsyncSession,
    preparation: ExperimentWorkflowPreparation,
    *,
    workspace_id: str,
) -> tuple[dict[str, Any], ExperimentRevision, ExperimentAggregateHead]:
    """Resolve launch mode only from the preparation's immutable pinned Plan."""
    domain_id, global_experiment_id, revision, plan = await _preparation_plan_scope(
        session,
        preparation,
        workspace_id=workspace_id,
    )
    loaded = await load_workflow_plan_authority(session, plan.aggregate_id)
    if loaded is None:
        raise ValidationFailure("preparation has no immutable pinned Plan capability authority")
    plan_authority, capability_contract = loaded
    launch_mode = capability_contract["capability"].get("launch_mode")
    if launch_mode not in SUPPORTED_PLAN_LAUNCH_MODES:
        raise ValidationFailure("pinned Plan capability has an unknown launch mode")
    if (
        plan_authority.workspace_id != workspace_id
        or plan_authority.domain_experiment_id != domain_id
        or revision.subject_id != plan.aggregate_id
    ):
        raise ValidationFailure("preparation Plan launch authority is not exactly bound")
    return (
        {
            "domain_id": domain_id,
            "global_experiment_id": global_experiment_id,
            "plan_id": str(plan.aggregate_id),
            "workflow_revision_id": str(revision.resource_id),
            "expected_domain_revision_id": str(plan_authority.expected_domain_revision_id),
            "capability_contract_sha256": str(plan_authority.capability_contract_sha256),
            "launch_mode": str(launch_mode),
        },
        revision,
        plan,
    )


def _attempt_identity_authority(attempt: ExperimentRunAttempt) -> dict[str, Any]:
    return {
        "attempt_id": str(attempt.resource_id),
        "workflow_run_id": str(attempt.workflow_run_id),
        "preparation_id": str(attempt.preparation_id),
        "attempt_number": int(attempt.attempt_number),
        "scheduler_job_id": str(attempt.scheduler_job_id),
        "created_at": str(attempt.created_at),
    }


def _attempt_authority(attempt: ExperimentRunAttempt) -> dict[str, Any]:
    return {
        "attempt_id": str(attempt.resource_id),
        "workflow_run_id": str(attempt.workflow_run_id),
        "preparation_id": str(attempt.preparation_id),
        "attempt_number": int(attempt.attempt_number),
        "scheduler_job_id": str(attempt.scheduler_job_id),
        "state": str(attempt.state),
        "external_binding_receipt_json": attempt.external_binding_receipt_json,
        "runtime_identity_json": attempt.runtime_identity_json,
        "terminal_receipt_json": attempt.terminal_receipt_json,
        "terminal_receipt_sha256": attempt.terminal_receipt_sha256,
        "created_at": str(attempt.created_at),
    }


def _preparation_identity_authority(
    preparation: ExperimentWorkflowPreparation,
) -> dict[str, Any]:
    return {
        "preparation_id": str(preparation.resource_id),
        "workflow_revision_id": str(preparation.workflow_revision_id),
        "normalized_request_sha256": str(preparation.normalized_request_sha256),
        "validation_resource_id": str(preparation.validation_resource_id),
        "validation_receipt_sha256": sha256_text(preparation.validation_receipt_json),
    }


async def _attempt_preparation_plan_authority(
    session: AsyncSession,
    attempt: ExperimentRunAttempt,
    *,
    workspace_id: str,
    source_domain_id: str,
    core_session: AsyncSession | None,
) -> dict[str, Any]:
    preparation = await session.get(
        ExperimentWorkflowPreparation,
        attempt.preparation_id,
    )
    if preparation is None:
        raise ValidationFailure("attempt preparation authority is unavailable")
    await validate_preparation_authority(
        session,
        preparation,
        core_session=core_session,
    )
    plan_authority, _revision, _plan = await _preparation_plan_launch_authority(
        session,
        preparation,
        workspace_id=workspace_id,
    )
    if plan_authority["domain_id"] != source_domain_id:
        raise ValidationFailure("attempt effective Plan belongs to another Domain")
    return {
        "attempt_id": str(attempt.resource_id),
        "preparation_authority": _preparation_identity_authority(preparation),
        "plan_authority": plan_authority,
    }


def _lineage_authority(edge: ExperimentLineageEdge) -> dict[str, Any]:
    return {
        "id": str(edge.id),
        "workspace_id": str(edge.workspace_id),
        "source_resource_id": str(edge.source_resource_id),
        "target_resource_id": str(edge.target_resource_id),
        "edge_mode": str(edge.edge_mode),
        "edge_key": str(edge.edge_key),
        "metadata_json": str(edge.metadata_json),
        "created_at": str(edge.created_at),
    }


def _launch_context_authority(context: ExperimentLaunchContext) -> dict[str, Any]:
    return {
        "launch_context_id": str(context.launch_context_id),
        "project_id": str(context.project_id),
        "global_experiment_id": str(context.global_experiment_id),
        "domain_experiment_id": str(context.domain_experiment_id),
        "workflow_id": str(context.workflow_id),
        "workflow_revision_id": str(context.workflow_revision_id),
        "preparation_id": str(context.preparation_id),
        "run_attempt_id": str(context.run_attempt_id),
        "contract_version": str(context.contract_version),
        "normalized_request_sha256": str(context.normalized_request_sha256),
        "validation_receipt_id": str(context.validation_receipt_id),
        "validation_receipt_sha256": str(context.validation_receipt_sha256),
        "source_receipt_id": str(context.source_receipt_id),
    }


async def _cancellation_disposition(
    session: AsyncSession,
    *,
    group: ExperimentRunGroup,
    runs: list[ExperimentWorkflowRun],
    attempts: list[ExperimentRunAttempt],
) -> dict[str, Any]:
    cancel_scope = f"run-group-cancel:{sha256_text(group.resource_id)}"
    claims = list(
        (
            await session.execute(
                select(ExperimentIdempotencyClaim)
                .where(ExperimentIdempotencyClaim.scope == cancel_scope)
                .order_by(ExperimentIdempotencyClaim.created_at, ExperimentIdempotencyClaim.idempotency_key)
            )
        ).scalars().all()
    )
    audits = list(
        (
            await session.execute(
                select(ExperimentAuditEvent)
                .where(
                    ExperimentAuditEvent.workspace_id == group.workspace_id,
                    ExperimentAuditEvent.resource_id == group.resource_id,
                    ExperimentAuditEvent.event_type == "run_group_cancelled",
                )
                .order_by(ExperimentAuditEvent.created_at, ExperimentAuditEvent.id)
            )
        ).scalars().all()
    )
    commands = list(
        (
            await session.execute(
                select(ExperimentRunControlCommand)
                .where(
                    ExperimentRunControlCommand.run_group_id == group.resource_id,
                    ExperimentRunControlCommand.command_type == "cancel",
                )
                .order_by(
                    ExperimentRunControlCommand.created_at,
                    ExperimentRunControlCommand.command_id,
                )
            )
        ).scalars().all()
    )
    if len(claims) + len(audits) + len(commands) > MAX_REPLAY_AUTHORITY_ROWS:
        raise ValidationFailure("cancellation disposition exceeds the bounded durable authority limit")
    return {
        "group_cancelled": group.state == "cancelled",
        "cancelled_run_ids": sorted(run.resource_id for run in runs if run.state == "cancelled"),
        "cancelled_attempt_ids": sorted(
            attempt.resource_id for attempt in attempts if attempt.state == "cancelled"
        ),
        "commands": [
            {
                "command_id": command.command_id,
                "request_scope": command.request_scope,
                "idempotency_key": command.idempotency_key,
                "request_sha256": command.request_sha256,
                "target_snapshot_sha256": command.target_snapshot_sha256,
                "status": command.status,
                "acknowledgement_sha256": command.acknowledgement_sha256,
                "conflict_sha256": command.conflict_sha256,
                "created_at": str(command.created_at),
                "applied_at": str(command.applied_at) if command.applied_at else None,
            }
            for command in commands
        ],
        "claims": [
            {
                "scope": claim.scope,
                "idempotency_key": claim.idempotency_key,
                "request_sha256": claim.request_sha256,
                "result_resource_id": claim.result_resource_id,
                "response_json": claim.response_json,
                "created_at": str(claim.created_at),
            }
            for claim in claims
        ],
        "audit_events": [
            {
                "id": event.id,
                "generation": int(event.generation),
                "payload_json": event.payload_json,
                "created_at": str(event.created_at),
            }
            for event in audits
        ],
    }


def _require_no_cancellation(disposition: dict[str, Any]) -> None:
    blocking_commands = [
        command
        for command in disposition.get("commands", [])
        if isinstance(command, dict)
        and command.get("status") in {"pending", "leased", "retryable", "applied", "conflicted"}
    ]
    if (
        disposition.get("group_cancelled") is not False
        or disposition.get("cancelled_run_ids") != []
        or disposition.get("cancelled_attempt_ids") != []
        or blocking_commands
        or disposition.get("claims") != []
        or disposition.get("audit_events") != []
    ):
        raise ValidationFailure("cancellation permanently closes retry authority")


async def _retry_launch_context(
    session: AsyncSession,
    *,
    launch_context_id: str,
    preparation: ExperimentWorkflowPreparation,
    workspace_id: str,
    domain_id: str,
    global_experiment_id: str,
    revision: ExperimentRevision,
    plan: ExperimentAggregateHead,
) -> ExperimentLaunchContext:
    """Freshly prove the complete v2 replacement handoff authority."""
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    validation = await session.get(ExperimentValidation, preparation.validation_resource_id)
    if (
        context is None
        or validation is None
        or validation.subject_resource_id != preparation.resource_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
        or validation.receipt_sha256 != sha256_text(validation.receipt_json)
        or context.contract_version != "2"
        or context.project_id != workspace_id
        or context.global_experiment_id != global_experiment_id
        or context.domain_experiment_id != domain_id
        or context.workflow_id != plan.aggregate_id
        or context.workflow_revision_id != revision.resource_id
        or context.preparation_id != preparation.resource_id
        or context.normalized_request_sha256 != preparation.normalized_request_sha256
        or context.validation_receipt_id != validation.resource_id
        or context.validation_receipt_sha256 != validation.receipt_sha256
        or context.source_receipt_id != revision.resource_id
        or context.state != "issued"
        or context.run_attempt_id is not None
    ):
        raise ValidationFailure("fresh exact v2 prepared launch context is required")
    return context


async def _validate_bound_launch_context(
    session: AsyncSession,
    *,
    launch_context_id: str,
    preparation: ExperimentWorkflowPreparation,
    workspace_id: str,
    authority: dict[str, Any],
    attempt: ExperimentRunAttempt,
) -> ExperimentLaunchContext:
    """Revalidate a v2 context after reservation without requiring it to stay issued."""
    context = await session.get(ExperimentLaunchContext, launch_context_id)
    validation = await session.get(ExperimentValidation, preparation.validation_resource_id)
    if (
        context is None
        or validation is None
        or validation.subject_resource_id != preparation.resource_id
        or validation.outcome != "valid"
        or validation.receipt_json != preparation.validation_receipt_json
        or validation.receipt_sha256 != sha256_text(validation.receipt_json)
        or context.contract_version != "2"
        or context.project_id != workspace_id
        or context.global_experiment_id != authority["global_experiment_id"]
        or context.domain_experiment_id != authority["domain_id"]
        or context.workflow_id != authority["plan_id"]
        or context.workflow_revision_id != authority["workflow_revision_id"]
        or context.preparation_id != preparation.resource_id
        or context.normalized_request_sha256 != preparation.normalized_request_sha256
        or context.validation_receipt_id != validation.resource_id
        or context.validation_receipt_sha256 != validation.receipt_sha256
        or context.source_receipt_id != authority["workflow_revision_id"]
        or context.run_attempt_id != attempt.resource_id
        or context.state not in {"reserved", "claimed", "consumed"}
    ):
        raise ValidationFailure("reserved v2 launch context no longer matches its resulting attempt")
    if context.state == "claimed" and not context.claim_token:
        raise ValidationFailure("claimed launch context has no claim authority")
    if context.state == "consumed":
        try:
            binding = json.loads(context.binding_receipt_json or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationFailure("consumed launch context binding receipt is malformed") from exc
        if (
            context.canonical_job_id != attempt.scheduler_job_id
            or not isinstance(binding, dict)
            or canonical_json(binding) != context.binding_receipt_json
            or binding.get("schema") != "bms.launch-context-binding.v2"
            or binding.get("launch_context_id") != context.launch_context_id
            or binding.get("canonical_job_id") != attempt.scheduler_job_id
            or binding.get("preparation_id") != preparation.resource_id
            or binding.get("run_attempt_id") != attempt.resource_id
            or binding.get("normalized_request_sha256") != preparation.normalized_request_sha256
            or binding.get("validation_receipt_id") != validation.resource_id
            or binding.get("validation_receipt_sha256") != validation.receipt_sha256
            or binding.get("verified") is not True
        ):
            raise ValidationFailure("consumed launch context no longer has exact attempt binding authority")
    elif context.canonical_job_id is not None or context.binding_receipt_json is not None:
        raise ValidationFailure("unconsumed launch context carries impossible canonical Job authority")
    return context


async def retry_failed_run_group(
    session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
    *,
    idempotency_key: str,
    replacement_preparation_ids: dict[str, str] | None = None,
    replacement_launch_context_ids: dict[str, str] | None = None,
    expected_generation: int | None = None,
    core_session: AsyncSession | None = None,
    source_domain_id: str | None = None,
) -> ExperimentRunGroup:
    """Create fresh attempts for failed runs with replay-stable v2 authority."""
    replacement_preparation_ids = {} if replacement_preparation_ids is None else replacement_preparation_ids
    replacement_launch_context_ids = (
        {} if replacement_launch_context_ids is None else replacement_launch_context_ids
    )
    if (
        isinstance(expected_generation, bool)
        or not isinstance(expected_generation, int)
        or expected_generation < 0
    ):
        raise ValidationFailure("retry requires an explicit expected run-group generation")
    if not isinstance(source_domain_id, str) or not source_domain_id:
        raise ValidationFailure("retry requires an explicit exact source Domain")
    if (
        not isinstance(replacement_preparation_ids, dict)
        or any(
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(preparation_id, str)
            or not preparation_id
            for run_id, preparation_id in replacement_preparation_ids.items()
        )
    ):
        raise ValidationFailure("retry replacement preparation mapping is malformed")
    if (
        not isinstance(replacement_launch_context_ids, dict)
        or any(
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(context_id, str)
            or not context_id
            for run_id, context_id in replacement_launch_context_ids.items()
        )
        or len(set(replacement_launch_context_ids.values()))
        != len(replacement_launch_context_ids)
    ):
        raise ValidationFailure("retry replacement launch context mapping is malformed")

    caller_request = {
        "schema": "bms.experiment.retry-request.v2",
        "workspace_id": workspace_id,
        "run_group_id": run_group_id,
        "expected_generation": expected_generation,
        "source_domain_id": source_domain_id,
        "replacement_preparation_ids": dict(replacement_preparation_ids),
        "replacement_launch_context_ids": dict(replacement_launch_context_ids),
    }
    request_sha256 = sha256_text(canonical_json(caller_request))
    scope = f"run_group_retry:{workspace_id}:{run_group_id}"
    existing_claim = await session.get(
        ExperimentIdempotencyClaim, (scope, idempotency_key)
    )
    if existing_claim is not None:
        response = _decode_canonical_claim_response(
            existing_claim,
            schema="bms.experiment.retry-response.v2",
            label="retry",
        )
        if (
            existing_claim.request_sha256 != request_sha256
            or response.get("caller_request") != caller_request
            or existing_claim.result_resource_id != run_group_id
            or response.get("result_run_group_id") != run_group_id
            or set(response)
            != {
                "schema",
                "caller_request",
                "result_run_group_id",
                "source",
                "replacements",
            }
        ):
            raise IdempotencyConflict("retry idempotency key was used for different authority")
        group = await session.get(ExperimentRunGroup, run_group_id)
        source = response.get("source")
        replacements = response.get("replacements")
        if (
            group is None
            or group.workspace_id != workspace_id
            or not isinstance(source, dict)
            or not isinstance(replacements, list)
            or source.get("run_group_id") != run_group_id
            or source.get("workspace_id") != workspace_id
            or source.get("domain_id") != source_domain_id
            or source.get("request_sha256") != group.request_sha256
            or source.get("state") != "failed"
            or source.get("generation") != expected_generation
            or set(source)
            != {
                "run_group_id",
                "workspace_id",
                "domain_id",
                "state",
                "generation",
                "request_sha256",
                "eligible_failed_runs",
                "all_run_disposition",
                "cancellation_disposition",
            }
        ):
            raise DispatchFailure("retry replay source/result identity is unavailable")
        runs = list(
            (
                await session.execute(
                    select(ExperimentWorkflowRun)
                    .where(ExperimentWorkflowRun.run_group_id == run_group_id)
                    .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
                )
            ).scalars().all()
        )
        attempts = list(
            (
                await session.execute(
                    select(ExperimentRunAttempt)
                    .where(
                        ExperimentRunAttempt.workflow_run_id.in_(
                            [run.resource_id for run in runs]
                        )
                    )
                    .order_by(
                        ExperimentRunAttempt.workflow_run_id,
                        ExperimentRunAttempt.attempt_number,
                    )
                )
            ).scalars().all()
        ) if runs else []
        stored_all_runs = source.get("all_run_disposition")
        replay_replacement_by_run = {
            item.get("run_id"): item for item in replacements if isinstance(item, dict)
        }
        if len(replay_replacement_by_run) != len(replacements):
            raise DispatchFailure("retry replacement authority is malformed")
        if (
            not isinstance(stored_all_runs, list)
            or len(stored_all_runs) != len(runs)
            or len(stored_all_runs) > MAX_REPLAY_AUTHORITY_ROWS
            or len(runs) + len(attempts) > MAX_REPLAY_AUTHORITY_ROWS
        ):
            raise DispatchFailure("retry complete source disposition is malformed")
        stored_all_by_run = {
            item.get("run_id"): item for item in stored_all_runs if isinstance(item, dict)
        }
        if len(stored_all_by_run) != len(runs):
            raise DispatchFailure("retry complete source disposition is incomplete")
        for run in runs:
            stored_run = stored_all_by_run.get(run.resource_id)
            stored_attempts = stored_run.get("attempts") if isinstance(stored_run, dict) else None
            stored_attempt_plans = (
                stored_run.get("attempt_plan_authorities")
                if isinstance(stored_run, dict)
                else None
            )
            stored_generation = stored_run.get("generation") if isinstance(stored_run, dict) else None
            if (
                not isinstance(stored_run, dict)
                or set(stored_run)
                != {
                    "run_id",
                    "preparation_id",
                    "plan_authority",
                    "state",
                    "generation",
                    "attempts",
                    "attempt_plan_authorities",
                }
                or stored_run.get("preparation_id") != run.preparation_id
                or not isinstance(stored_attempts, list)
                or not isinstance(stored_attempt_plans, list)
                or len(stored_attempt_plans) != len(stored_attempts)
                or not isinstance(stored_generation, int)
            ):
                raise DispatchFailure("retry source run disposition changed")
            current_attempt_rows = [
                attempt for attempt in attempts if attempt.workflow_run_id == run.resource_id
            ]
            current_attempts = [_attempt_authority(attempt) for attempt in current_attempt_rows]
            current_attempt_plans = [
                await _attempt_preparation_plan_authority(
                    session,
                    attempt,
                    workspace_id=workspace_id,
                    source_domain_id=source_domain_id,
                    core_session=core_session,
                )
                for attempt in current_attempt_rows
            ]
            if run.resource_id in replacement_preparation_ids:
                replacement = replay_replacement_by_run.get(run.resource_id)
                disposition_matches = (
                    isinstance(replacement, dict)
                    and stored_run.get("state") == "failed"
                    and run.state == "dispatch_pending"
                    and int(run.generation) == stored_generation + 1
                    and len(current_attempts) == len(stored_attempts) + 1
                    and current_attempts[:-1] == stored_attempts
                    and current_attempt_plans[:-1] == stored_attempt_plans
                    and current_attempt_plans[-1]
                    == replacement.get("resulting_attempt_plan_authority")
                )
            else:
                disposition_matches = (
                    stored_run.get("state") == run.state
                    and stored_run.get("generation") == int(run.generation)
                    and current_attempts == stored_attempts
                    and current_attempt_plans == stored_attempt_plans
                )
            if not disposition_matches:
                raise DispatchFailure("retry original attempt history changed")
            source_preparation = await session.get(
                ExperimentWorkflowPreparation, run.preparation_id
            )
            if source_preparation is None:
                raise DispatchFailure("retry source preparation authority is unavailable")
            await validate_preparation_authority(
                session,
                source_preparation,
                core_session=core_session,
            )
            current_plan, _revision, _plan = await _preparation_plan_launch_authority(
                session,
                source_preparation,
                workspace_id=workspace_id,
            )
            if (
                current_plan != stored_run.get("plan_authority")
                or current_plan["domain_id"] != source_domain_id
            ):
                raise DispatchFailure("retry immutable all-run Domain/Plan authority changed")
        _require_no_cancellation(
            await _cancellation_disposition(
                session, group=group, runs=runs, attempts=attempts
            )
        )
        if source.get("cancellation_disposition") != {
            "group_cancelled": False,
            "cancelled_run_ids": [],
            "cancelled_attempt_ids": [],
            "commands": [],
            "claims": [],
            "audit_events": [],
        }:
            raise DispatchFailure("retry stored cancellation disposition is malformed")
        runs_by_id = {run.resource_id: run for run in runs}
        stored_eligible = source.get("eligible_failed_runs")
        if (
            not isinstance(stored_eligible, list)
            or len(stored_eligible) != len(replacements)
            or len(stored_eligible) > MAX_REPLAY_AUTHORITY_ROWS
        ):
            raise DispatchFailure("retry stored failed-run authority is malformed")
        stored_by_run = {
            item.get("run_id"): item for item in stored_eligible if isinstance(item, dict)
        }
        replacement_by_run = {
            item.get("run_id"): item for item in replacements if isinstance(item, dict)
        }
        if (
            len(stored_by_run) != len(stored_eligible)
            or len(replacement_by_run) != len(replacements)
            or set(stored_by_run) != set(replacement_preparation_ids)
            or set(replacement_by_run) != set(replacement_preparation_ids)
        ):
            raise DispatchFailure("retry stored run/replacement authority is incomplete")
        for run_id in sorted(stored_by_run):
            stored_run = stored_by_run[run_id]
            replacement = replacement_by_run[run_id]
            run = runs_by_id.get(run_id)
            if run is None or run.preparation_id != stored_run.get("source_preparation_id"):
                raise DispatchFailure("retry source run lineage no longer matches")
            source_preparation = await session.get(
                ExperimentWorkflowPreparation, stored_run["source_preparation_id"]
            )
            prior = stored_run.get("prior_failed_attempt")
            prior_attempt = await session.get(
                ExperimentRunAttempt,
                prior.get("attempt_id") if isinstance(prior, dict) else "",
            )
            if (
                source_preparation is None
                or prior_attempt is None
                or prior_attempt.workflow_run_id != run_id
                or prior_attempt.state != "failed"
                or _attempt_authority(prior_attempt) != prior
            ):
                raise DispatchFailure("retry prior failed attempt is no longer exact historical authority")
            source_plan, _source_revision, _source_head = (
                await _preparation_plan_launch_authority(
                    session, source_preparation, workspace_id=workspace_id
                )
            )
            prior_preparation = await session.get(
                ExperimentWorkflowPreparation, prior_attempt.preparation_id
            )
            if prior_preparation is None:
                raise DispatchFailure("retry prior preparation authority is unavailable")
            prior_plan, _prior_revision, _prior_head = (
                await _preparation_plan_launch_authority(
                    session, prior_preparation, workspace_id=workspace_id
                )
            )
            if (
                source_plan != stored_run.get("source_plan_authority")
                or prior_plan != stored_run.get("prior_plan_authority")
                or source_plan["domain_id"] != source_domain_id
                or prior_plan["domain_id"] != source_domain_id
            ):
                raise DispatchFailure("retry immutable source Domain/Plan lineage changed")
            preparation = await session.get(
                ExperimentWorkflowPreparation, replacement.get("preparation_id", "")
            )
            if (
                preparation is None
                or preparation.resource_id
                != replacement_preparation_ids.get(run_id)
            ):
                raise DispatchFailure("retry replacement preparation identity changed")
            await validate_preparation_authority(
                session, preparation, core_session=core_session
            )
            plan_authority, _revision, _plan = await _preparation_plan_launch_authority(
                session, preparation, workspace_id=workspace_id
            )
            if (
                plan_authority != replacement.get("plan_authority")
                or plan_authority["domain_id"] != source_domain_id
            ):
                raise DispatchFailure("retry replacement Plan authority changed")
            supersession = replacement.get("supersession")
            if preparation.resource_id == prior_attempt.preparation_id:
                if supersession is not None:
                    raise DispatchFailure("retry stored an impossible supersession proof")
            else:
                edges = list(
                    (
                        await session.execute(
                            select(ExperimentLineageEdge).where(
                                ExperimentLineageEdge.source_resource_id == preparation.resource_id,
                                ExperimentLineageEdge.target_resource_id == prior_attempt.preparation_id,
                                ExperimentLineageEdge.edge_mode == "supersedes",
                            )
                        )
                    ).scalars().all()
                )
                if len(edges) != 1 or _lineage_authority(edges[0]) != supersession:
                    raise DispatchFailure("retry replacement supersession proof changed")
            resulting = replacement.get("resulting_attempt")
            attempt = await session.get(
                ExperimentRunAttempt,
                resulting.get("attempt_id") if isinstance(resulting, dict) else "",
            )
            retry_edges = list(
                (
                    await session.execute(
                        select(ExperimentLineageEdge).where(
                            ExperimentLineageEdge.source_resource_id
                            == (attempt.resource_id if attempt is not None else ""),
                            ExperimentLineageEdge.target_resource_id == prior_attempt.resource_id,
                            ExperimentLineageEdge.edge_mode == "retried_from",
                            ExperimentLineageEdge.edge_key == "immediate-prior-attempt",
                        )
                    )
                ).scalars().all()
            )
            if (
                attempt is None
                or _attempt_identity_authority(attempt) != resulting
                or len(retry_edges) != 1
                or _lineage_authority(retry_edges[0]) != replacement.get("retry_lineage")
            ):
                raise DispatchFailure("retry resulting attempt/lineage authority changed")
            resulting_attempt_plan_authority = await _attempt_preparation_plan_authority(
                session,
                attempt,
                workspace_id=workspace_id,
                source_domain_id=source_domain_id,
                core_session=core_session,
            )
            if (
                resulting_attempt_plan_authority
                != replacement.get("resulting_attempt_plan_authority")
            ):
                raise DispatchFailure("retry resulting attempt Plan authority changed")
            context_id = replacement.get("launch_context_id")
            if plan_authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
                if (
                    context_id != replacement_launch_context_ids.get(run_id)
                    or replacement.get("launch_context") is None
                ):
                    raise DispatchFailure("retry typed handoff context authority is incomplete")
                context = await _validate_bound_launch_context(
                    session,
                    launch_context_id=context_id,
                    preparation=preparation,
                    workspace_id=workspace_id,
                    authority=plan_authority,
                    attempt=attempt,
                )
                if _launch_context_authority(context) != replacement["launch_context"]:
                    raise DispatchFailure("retry launch context binding authority changed")
            else:
                if context_id is not None or replacement.get("launch_context") is not None:
                    raise DispatchFailure("managed retry carries prohibited launch context authority")
                outbox = await session.scalar(
                    select(ExperimentDispatchOutbox).where(
                        ExperimentDispatchOutbox.run_attempt_id == attempt.resource_id,
                        ExperimentDispatchOutbox.event_type == "materialize_scheduler_job",
                    )
                )
                if outbox is None:
                    raise DispatchFailure("managed retry has no exact dispatch outbox authority")
        return group

    group = await session.get(ExperimentRunGroup, run_group_id)
    if group is None or group.workspace_id != workspace_id:
        raise NotFound("run group not found")
    if group.state == "cancelled":
        raise ValidationFailure("cancelled run groups are permanently ineligible for retry")
    if group.state != "failed":
        raise ValidationFailure("only reconciled failed run groups are eligible for retry")
    if group.generation != expected_generation:
        raise RevisionConflict("run group generation changed")
    runs = list(
        (
            await session.execute(
                select(ExperimentWorkflowRun)
                .where(ExperimentWorkflowRun.run_group_id == run_group_id)
                .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
            )
        ).scalars().all()
    )
    if not runs or len(runs) > MAX_REPLAY_AUTHORITY_ROWS:
        raise ValidationFailure("run group has no bounded immutable run authority")
    attempts_by_run: dict[str, list[ExperimentRunAttempt]] = {}
    all_attempts: list[ExperimentRunAttempt] = []
    for run in runs:
        run_attempts = list(
            (
                await session.execute(
                    select(ExperimentRunAttempt)
                    .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                    .order_by(ExperimentRunAttempt.attempt_number)
                )
            ).scalars().all()
        )
        attempts_by_run[run.resource_id] = run_attempts
        all_attempts.extend(run_attempts)
    if len(runs) + len(all_attempts) > MAX_REPLAY_AUTHORITY_ROWS:
        raise ValidationFailure("retry source history exceeds the bounded durable authority limit")
    cancellation = await _cancellation_disposition(
        session, group=group, runs=runs, attempts=all_attempts
    )
    _require_no_cancellation(cancellation)

    eligible: list[dict[str, Any]] = []
    source_domains: set[str] = set()
    source_plans_by_run: dict[str, dict[str, Any]] = {}
    attempt_plans_by_run: dict[str, list[dict[str, Any]]] = {}
    latest_by_run: dict[str, ExperimentRunAttempt] = {}
    for run in runs:
        source_preparation = await session.get(
            ExperimentWorkflowPreparation, run.preparation_id
        )
        if source_preparation is None:
            raise ValidationFailure("run group source preparation authority is unavailable")
        await validate_preparation_authority(
            session,
            source_preparation,
            core_session=core_session,
        )
        source_plan, _source_revision, _source_head = (
            await _preparation_plan_launch_authority(
                session, source_preparation, workspace_id=workspace_id
            )
        )
        source_domains.add(source_plan["domain_id"])
        source_plans_by_run[run.resource_id] = source_plan
        run_attempts = attempts_by_run[run.resource_id]
        attempt_plan_authorities: list[dict[str, Any]] = []
        for attempt in run_attempts:
            attempt_authority = await _attempt_preparation_plan_authority(
                session,
                attempt,
                workspace_id=workspace_id,
                source_domain_id=source_domain_id,
                core_session=core_session,
            )
            source_domains.add(attempt_authority["plan_authority"]["domain_id"])
            attempt_plan_authorities.append(attempt_authority)
        attempt_plans_by_run[run.resource_id] = attempt_plan_authorities
        if run.state == "failed":
            if not run_attempts or run_attempts[-1].state != "failed":
                raise ValidationFailure("failed run has no exact latest failed attempt authority")
            prior = run_attempts[-1]
            prior_plan = attempt_plan_authorities[-1]["plan_authority"]
            latest_by_run[run.resource_id] = prior
            eligible.append(
                {
                    "run_id": run.resource_id,
                    "source_preparation_id": source_preparation.resource_id,
                    "source_plan_authority": source_plan,
                    "prior_plan_authority": prior_plan,
                    "prior_failed_attempt": _attempt_authority(prior),
                }
            )
    if source_domains != {source_domain_id}:
        raise NotFound("run group not found in the exact source Domain")
    failed_run_ids = [item["run_id"] for item in eligible]
    if not failed_run_ids:
        raise ValidationFailure("run group has no reconciled failed runs eligible for retry")
    if set(replacement_preparation_ids) != set(failed_run_ids):
        raise ValidationFailure("retry replacements must identify every and only eligible failed run")

    preparations_by_run: dict[str, ExperimentWorkflowPreparation] = {}
    plans_by_run: dict[str, dict[str, Any]] = {}
    contexts_by_run: dict[str, ExperimentLaunchContext] = {}
    supersession_by_run: dict[str, dict[str, Any] | None] = {}
    required_context_runs: set[str] = set()
    for run_id in failed_run_ids:
        previous = latest_by_run[run_id]
        preparation = await session.get(
            ExperimentWorkflowPreparation, replacement_preparation_ids[run_id]
        )
        if (
            preparation is None
            or preparation.workspace_id != workspace_id
            or preparation.validation_status != "valid"
        ):
            raise ValidationFailure("failed run has no valid replacement preparation in this workspace")
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        plan_authority, revision, plan = await _preparation_plan_launch_authority(
            session, preparation, workspace_id=workspace_id
        )
        if plan_authority["domain_id"] != source_domain_id:
            raise ValidationFailure("replacement preparation belongs to another Domain")
        supersession: dict[str, Any] | None = None
        if preparation.resource_id != previous.preparation_id:
            successors = list(
                (
                    await session.execute(
                        select(ExperimentLineageEdge).where(
                            ExperimentLineageEdge.source_resource_id == preparation.resource_id,
                            ExperimentLineageEdge.target_resource_id == previous.preparation_id,
                            ExperimentLineageEdge.edge_mode == "supersedes",
                        )
                    )
                ).scalars().all()
            )
            if len(successors) != 1:
                raise ValidationFailure("replacement_preparation_required")
            supersession = _lineage_authority(successors[0])
        if plan_authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
            required_context_runs.add(run_id)
            context_id = replacement_launch_context_ids.get(run_id)
            if context_id is None:
                raise ValidationFailure("typed handoff retry requires an exact v2 launch context")
            contexts_by_run[run_id] = await _retry_launch_context(
                session,
                launch_context_id=context_id,
                preparation=preparation,
                workspace_id=workspace_id,
                domain_id=source_domain_id,
                global_experiment_id=plan_authority["global_experiment_id"],
                revision=revision,
                plan=plan,
            )
        preparations_by_run[run_id] = preparation
        plans_by_run[run_id] = plan_authority
        supersession_by_run[run_id] = supersession
    if set(replacement_launch_context_ids) != required_context_runs:
        raise ValidationFailure("retry contexts must identify every and only typed handoff run")

    source_snapshot = {
        "run_group_id": group.resource_id,
        "workspace_id": workspace_id,
        "domain_id": source_domain_id,
        "state": group.state,
        "generation": int(group.generation),
        "request_sha256": group.request_sha256,
        "eligible_failed_runs": eligible,
        "all_run_disposition": [
            {
                "run_id": run.resource_id,
                "preparation_id": run.preparation_id,
                "plan_authority": source_plans_by_run[run.resource_id],
                "state": run.state,
                "generation": int(run.generation),
                "attempts": [
                    _attempt_authority(attempt)
                    for attempt in attempts_by_run[run.resource_id]
                ],
                "attempt_plan_authorities": attempt_plans_by_run[run.resource_id],
            }
            for run in runs
        ],
        "cancellation_disposition": cancellation,
    }
    replacements: list[dict[str, Any]] = []
    for run_id in failed_run_ids:
        run = next(item for item in runs if item.resource_id == run_id)
        previous = latest_by_run[run_id]
        preparation = preparations_by_run[run_id]
        plan_authority = plans_by_run[run_id]
        attempt_resource = await _resource(
            session,
            kind="run_attempt",
            workspace_id=workspace_id,
            lifecycle_owner_id=run.resource_id,
        )
        scheduler_payload = json.loads(preparation.scheduler_payload_json)
        attempt = ExperimentRunAttempt(
            resource_id=attempt_resource.id,
            workspace_id=workspace_id,
            workflow_run_id=run.resource_id,
            preparation_id=preparation.resource_id,
            attempt_number=previous.attempt_number + 1,
            scheduler_job_id=scheduler_job_identity(attempt_resource.id, scheduler_payload),
            state="pending",
            created_at=now(),
        )
        session.add(attempt)
        await session.flush()
        retry_edge = ExperimentLineageEdge(
            id=new_id("retry-lineage"),
            workspace_id=workspace_id,
            source_resource_id=attempt.resource_id,
            target_resource_id=previous.resource_id,
            edge_mode="retried_from",
            edge_key="immediate-prior-attempt",
            metadata_json=canonical_json(
                {
                    "run_group_id": run_group_id,
                    "previous_attempt_number": previous.attempt_number,
                    "attempt_number": attempt.attempt_number,
                }
            ),
            created_at=now(),
        )
        session.add(retry_edge)
        context = contexts_by_run.get(run_id)
        if plan_authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
            if context is None:
                raise ValidationFailure("typed handoff retry lost its launch context authority")
            context.run_attempt_id = attempt.resource_id
            context.state = "reserved"
            await session.flush()
        elif plan_authority["launch_mode"] in MANAGED_DISPATCH_LAUNCH_MODES:
            outbox_payload = {
                "schema": "bms.experiment.dispatch.v1",
                "run_group_id": run_group_id,
                "workflow_run_id": run.resource_id,
                "attempt_id": attempt.resource_id,
                "scheduler_job_id": attempt.scheduler_job_id,
                "workflow_revision_id": preparation.workflow_revision_id,
                "scheduler": scheduler_payload,
            }
            outbox_json = canonical_json(outbox_payload)
            session.add(
                ExperimentDispatchOutbox(
                    id=new_id("dispatch"),
                    workspace_id=workspace_id,
                    run_attempt_id=attempt.resource_id,
                    event_type="materialize_scheduler_job",
                    payload_json=outbox_json,
                    payload_sha256=sha256_text(outbox_json),
                    status="pending",
                    dispatch_attempts=0,
                    created_at=now(),
                    updated_at=now(),
                )
            )
        else:
            raise ValidationFailure("pinned Plan capability has an unknown launch mode")
        run_expected_generation = int(run.generation)
        run.state = "dispatch_pending"
        run.generation = run_expected_generation + 1
        sequence = int(
            (
                await session.execute(
                    select(func.max(ExperimentRunEvent.sequence_number)).where(
                        ExperimentRunEvent.workflow_run_id == run.resource_id
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        session.add(
            ExperimentRunEvent(
                workspace_id=workspace_id,
                workflow_run_id=run.resource_id,
                sequence_number=sequence,
                expected_generation=run_expected_generation,
                resulting_generation=run.generation,
                idempotency_key=f"retry:{run_group_id}:{attempt.resource_id}",
                event_type="run_attempt_retry_created",
                payload_json=canonical_json(
                    {
                        "previous_attempt_id": previous.resource_id,
                        "attempt_id": attempt.resource_id,
                        "previous_preparation_id": previous.preparation_id,
                        "replacement_preparation_id": preparation.resource_id,
                    }
                ),
                created_at=now(),
            )
        )
        await session.flush()
        resulting_attempt_plan_authority = await _attempt_preparation_plan_authority(
            session,
            attempt,
            workspace_id=workspace_id,
            source_domain_id=source_domain_id,
            core_session=core_session,
        )
        replacements.append(
            {
                "run_id": run_id,
                "preparation_id": preparation.resource_id,
                "plan_authority": plan_authority,
                "supersession": supersession_by_run[run_id],
                "launch_context_id": (
                    context.launch_context_id if context is not None else None
                ),
                "launch_context": (
                    _launch_context_authority(context) if context is not None else None
                ),
                "resulting_attempt": _attempt_identity_authority(attempt),
                "resulting_attempt_plan_authority": resulting_attempt_plan_authority,
                "retry_lineage": _lineage_authority(retry_edge),
            }
        )
    await session.flush()
    group.state = await derive_run_group_state(session, group.resource_id)
    group.generation += 1
    group.updated_at = now()
    response = {
        "schema": "bms.experiment.retry-response.v2",
        "caller_request": caller_request,
        "result_run_group_id": run_group_id,
        "source": source_snapshot,
        "replacements": replacements,
    }
    response_json = _bounded_canonical_authority(response, label="retry response")
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            result_resource_id=run_group_id,
            response_json=response_json,
            response_sha256=sha256_text(response_json),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=run_group_id,
        event_type="run_group_retry_created",
        generation=group.generation,
        payload={"failed_run_ids": failed_run_ids, "idempotency_key": idempotency_key},
    )
    await session.flush()
    return group

async def resubmit_run_group(
    session: AsyncSession,
    workspace_id: str,
    run_group_id: str,
    *,
    idempotency_key: str,
    preparation_ids: list[str] | None = None,
    launch_context_ids: dict[str, str] | None = None,
    expected_generation: int | None = None,
    core_session: AsyncSession | None = None,
    source_domain_id: str | None = None,
) -> ExperimentRunGroup:
    """Create one lineage-linked run group with replay-stable source authority."""
    if (
        not isinstance(preparation_ids, list)
        or not preparation_ids
        or any(not isinstance(value, str) or not value for value in preparation_ids)
        or len(set(preparation_ids)) != len(preparation_ids)
    ):
        raise ValidationFailure("resubmit requires explicit nonempty unique preparation IDs")
    if (
        isinstance(expected_generation, bool)
        or not isinstance(expected_generation, int)
        or expected_generation < 0
    ):
        raise ValidationFailure("resubmit requires an explicit expected run-group generation")
    if not isinstance(source_domain_id, str) or not source_domain_id:
        raise ValidationFailure("resubmit requires an explicit exact source Domain")
    launch_context_ids = {} if launch_context_ids is None else launch_context_ids
    if (
        not isinstance(launch_context_ids, dict)
        or any(
            not isinstance(preparation_id, str)
            or not preparation_id
            or not isinstance(context_id, str)
            or not context_id
            for preparation_id, context_id in launch_context_ids.items()
        )
        or len(set(launch_context_ids.values())) != len(launch_context_ids)
    ):
        raise ValidationFailure("resubmit launch context mapping is malformed")
    replacement_ids = list(preparation_ids)
    launch_context_ids = dict(launch_context_ids)
    caller_request = {
        "schema": "bms.experiment.resubmit-request.v2",
        "workspace_id": workspace_id,
        "source_run_group_id": run_group_id,
        "preparation_ids": replacement_ids,
        "launch_context_ids": launch_context_ids,
        "expected_generation": expected_generation,
        "source_domain_id": source_domain_id,
    }
    request_sha256 = sha256_text(canonical_json(caller_request))
    scope = f"run_group_resubmit:{workspace_id}:{run_group_id}"
    existing_claim = await session.get(
        ExperimentIdempotencyClaim, (scope, idempotency_key)
    )
    if existing_claim is not None:
        response = _decode_canonical_claim_response(
            existing_claim,
            schema="bms.experiment.resubmit-response.v2",
            label="resubmit",
        )
        if (
            existing_claim.request_sha256 != request_sha256
            or response.get("caller_request") != caller_request
            or not isinstance(response.get("source"), dict)
            or response["source"].get("run_group_id") != run_group_id
            or existing_claim.result_resource_id != response.get("result_run_group_id")
            or set(response)
            != {
                "schema",
                "caller_request",
                "source",
                "replacements",
                "result_run_group_id",
                "result_request_sha256",
                "lineage",
            }
        ):
            raise IdempotencyConflict("resubmit idempotency key was used for different authority")
        source_group = await session.get(ExperimentRunGroup, run_group_id)
        result_group = await session.get(
            ExperimentRunGroup, existing_claim.result_resource_id
        )
        source_snapshot = response.get("source")
        replacements = response.get("replacements")
        if (
            source_group is None
            or source_group.workspace_id != workspace_id
            or result_group is None
            or result_group.workspace_id != workspace_id
            or result_group.resource_id == source_group.resource_id
            or result_group.request_sha256 != response.get("result_request_sha256")
            or not isinstance(source_snapshot, dict)
            or not isinstance(replacements, list)
            or source_snapshot.get("workspace_id") != workspace_id
            or source_snapshot.get("domain_id") != source_domain_id
            or source_snapshot.get("request_sha256") != source_group.request_sha256
            or source_snapshot.get("state") not in {"completed", "failed", "cancelled"}
            or source_snapshot.get("generation") != expected_generation
            or set(source_snapshot)
            != {
                "run_group_id",
                "workspace_id",
                "domain_id",
                "state",
                "generation",
                "request_sha256",
                "runs",
                "cancellation_disposition",
            }
        ):
            raise DispatchFailure("resubmit replay source/result identity is unavailable")
        source_runs = list(
            (
                await session.execute(
                    select(ExperimentWorkflowRun)
                    .where(ExperimentWorkflowRun.run_group_id == run_group_id)
                    .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
                )
            ).scalars().all()
        )
        source_runs_by_id = {run.resource_id: run for run in source_runs}
        stored_runs = source_snapshot.get("runs")
        if (
            not isinstance(stored_runs, list)
            or len(stored_runs) != len(source_runs)
            or len(stored_runs) > MAX_REPLAY_AUTHORITY_ROWS
        ):
            raise DispatchFailure("resubmit stored source history is malformed")
        stored_run_ids = [
            item.get("run_id") for item in stored_runs if isinstance(item, dict)
        ]
        if (
            len(stored_run_ids) != len(stored_runs)
            or len(set(stored_run_ids)) != len(stored_run_ids)
            or set(stored_run_ids) != set(source_runs_by_id)
        ):
            raise DispatchFailure("resubmit stored source run inventory is incomplete")
        current_attempts: list[ExperimentRunAttempt] = []
        for stored_run in stored_runs:
            if (
                not isinstance(stored_run, dict)
                or set(stored_run)
                != {
                    "run_id",
                    "preparation_id",
                    "plan_authority",
                    "state",
                    "generation",
                    "attempts",
                    "attempt_plan_authorities",
                }
                or not isinstance(stored_run.get("attempts"), list)
                or not isinstance(stored_run.get("attempt_plan_authorities"), list)
                or len(stored_run["attempt_plan_authorities"])
                != len(stored_run["attempts"])
            ):
                raise DispatchFailure("resubmit stored source run is malformed")
            run = source_runs_by_id.get(stored_run.get("run_id"))
            if run is None or run.preparation_id != stored_run.get("preparation_id"):
                raise DispatchFailure("resubmit source run lineage changed")
            preparation = await session.get(
                ExperimentWorkflowPreparation, run.preparation_id
            )
            if preparation is None:
                raise DispatchFailure("resubmit source preparation authority is unavailable")
            await validate_preparation_authority(
                session,
                preparation,
                core_session=core_session,
            )
            plan_authority, _revision, _plan = await _preparation_plan_launch_authority(
                session, preparation, workspace_id=workspace_id
            )
            if (
                plan_authority != stored_run.get("plan_authority")
                or plan_authority["domain_id"] != source_domain_id
            ):
                raise DispatchFailure("resubmit immutable source Domain/Plan lineage changed")
            attempts = list(
                (
                    await session.execute(
                        select(ExperimentRunAttempt)
                        .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                        .order_by(ExperimentRunAttempt.attempt_number)
                    )
                ).scalars().all()
            )
            current_attempts.extend(attempts)
            attempts_by_id = {attempt.resource_id: attempt for attempt in attempts}
            current_attempt_plans = {
                attempt.resource_id: await _attempt_preparation_plan_authority(
                    session,
                    attempt,
                    workspace_id=workspace_id,
                    source_domain_id=source_domain_id,
                    core_session=core_session,
                )
                for attempt in attempts
            }
            stored_attempt_plans = {
                item.get("attempt_id"): item
                for item in stored_run["attempt_plan_authorities"]
                if isinstance(item, dict)
            }
            if len(stored_attempt_plans) != len(stored_run["attempt_plan_authorities"]):
                raise DispatchFailure("resubmit stored attempt Plan authority is malformed")
            for stored_attempt in stored_run["attempts"]:
                current_attempt = attempts_by_id.get(
                    stored_attempt.get("attempt_id")
                    if isinstance(stored_attempt, dict)
                    else ""
                )
                stored_attempt_plan = stored_attempt_plans.get(
                    current_attempt.resource_id if current_attempt is not None else ""
                )
                if (
                    current_attempt is None
                    or _attempt_authority(current_attempt) != stored_attempt
                    or current_attempt_plans[current_attempt.resource_id]
                    != stored_attempt_plan
                ):
                    raise DispatchFailure("resubmit source attempt history changed")
        if len(source_runs) + len(current_attempts) > MAX_REPLAY_AUTHORITY_ROWS:
            raise DispatchFailure("resubmit current source history exceeds its bounded limit")
        current_cancellation = await _cancellation_disposition(
            session,
            group=source_group,
            runs=source_runs,
            attempts=current_attempts,
        )
        stored_cancellation = source_snapshot.get("cancellation_disposition")
        if (
            not isinstance(stored_cancellation, dict)
            or set(stored_cancellation)
            != {
                "group_cancelled",
                "cancelled_run_ids",
                "cancelled_attempt_ids",
                "commands",
                "claims",
                "audit_events",
            }
            or not isinstance(stored_cancellation.get("group_cancelled"), bool)
            or any(
                not isinstance(stored_cancellation.get(field), list)
                for field in (
                    "cancelled_run_ids",
                    "cancelled_attempt_ids",
                    "commands",
                    "claims",
                    "audit_events",
                )
            )
        ):
            raise DispatchFailure("resubmit stored cancellation disposition is malformed")
        if (
            not set(stored_cancellation.get("cancelled_run_ids", []))
            <= set(current_cancellation["cancelled_run_ids"])
            or not set(stored_cancellation.get("cancelled_attempt_ids", []))
            <= set(current_cancellation["cancelled_attempt_ids"])
            or any(
                item not in current_cancellation["commands"]
                for item in stored_cancellation.get("commands", [])
            )
            or any(item not in current_cancellation["claims"] for item in stored_cancellation.get("claims", []))
            or any(item not in current_cancellation["audit_events"] for item in stored_cancellation.get("audit_events", []))
            or (
                stored_cancellation.get("group_cancelled") is True
                and current_cancellation.get("group_cancelled") is not True
                and not current_cancellation.get("audit_events")
            )
        ):
            raise DispatchFailure("resubmit source cancellation proof changed")
        result_runs = list(
            (
                await session.execute(
                    select(ExperimentWorkflowRun)
                    .where(ExperimentWorkflowRun.run_group_id == result_group.resource_id)
                )
            ).scalars().all()
        )
        result_runs_by_preparation = {run.preparation_id: run for run in result_runs}
        if (
            len(replacements) != len(replacement_ids)
            or [item.get("preparation_id") for item in replacements if isinstance(item, dict)]
            != replacement_ids
            or len(result_runs_by_preparation) != len(replacement_ids)
        ):
            raise DispatchFailure("resubmit replacement/result authority is incomplete")
        for stored in replacements:
            preparation_id = stored["preparation_id"]
            preparation = await session.get(
                ExperimentWorkflowPreparation, preparation_id
            )
            if preparation is None:
                raise DispatchFailure("resubmit replacement preparation disappeared")
            await validate_preparation_authority(
                session, preparation, core_session=core_session
            )
            plan_authority, _revision, _plan = await _preparation_plan_launch_authority(
                session, preparation, workspace_id=workspace_id
            )
            if (
                plan_authority != stored.get("plan_authority")
                or plan_authority["domain_id"] != source_domain_id
            ):
                raise DispatchFailure("resubmit replacement Plan authority changed")
            run = result_runs_by_preparation.get(preparation_id)
            attempt_identity = stored.get("resulting_attempt")
            attempt = await session.get(
                ExperimentRunAttempt,
                attempt_identity.get("attempt_id")
                if isinstance(attempt_identity, dict)
                else "",
            )
            if (
                run is None
                or run.resource_id != stored.get("resulting_run_id")
                or attempt is None
                or attempt.workflow_run_id != run.resource_id
                or _attempt_identity_authority(attempt) != attempt_identity
            ):
                raise DispatchFailure("resubmit resulting run/attempt identity changed")
            context_id = stored.get("launch_context_id")
            if plan_authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
                if (
                    context_id != launch_context_ids.get(preparation_id)
                    or stored.get("launch_context") is None
                ):
                    raise DispatchFailure("resubmit typed handoff context authority is incomplete")
                context = await _validate_bound_launch_context(
                    session,
                    launch_context_id=context_id,
                    preparation=preparation,
                    workspace_id=workspace_id,
                    authority=plan_authority,
                    attempt=attempt,
                )
                if _launch_context_authority(context) != stored["launch_context"]:
                    raise DispatchFailure("resubmit launch context binding authority changed")
            else:
                if context_id is not None or stored.get("launch_context") is not None:
                    raise DispatchFailure("managed resubmit carries prohibited launch context authority")
                outbox = await session.scalar(
                    select(ExperimentDispatchOutbox).where(
                        ExperimentDispatchOutbox.run_attempt_id == attempt.resource_id,
                        ExperimentDispatchOutbox.event_type == "materialize_scheduler_job",
                    )
                )
                if outbox is None:
                    raise DispatchFailure("managed resubmit has no exact dispatch outbox authority")
        edges = list(
            (
                await session.execute(
                    select(ExperimentLineageEdge).where(
                        ExperimentLineageEdge.source_resource_id == result_group.resource_id,
                        ExperimentLineageEdge.target_resource_id == source_group.resource_id,
                        ExperimentLineageEdge.edge_mode == "resubmitted_from",
                    )
                )
            ).scalars().all()
        )
        if len(edges) != 1 or _lineage_authority(edges[0]) != response.get("lineage"):
            raise DispatchFailure("resubmit exact one lineage edge authority changed")
        return result_group

    source = await session.get(ExperimentRunGroup, run_group_id)
    if source is None or source.workspace_id != workspace_id:
        raise NotFound("run group not found")
    if source.generation != expected_generation:
        raise RevisionConflict("run group generation changed")
    if source.state not in {"completed", "failed", "cancelled"}:
        raise ValidationFailure("only terminal run groups can be resubmitted")
    source_runs = list(
        (
            await session.execute(
                select(ExperimentWorkflowRun)
                .where(ExperimentWorkflowRun.run_group_id == run_group_id)
                .order_by(ExperimentWorkflowRun.created_at, ExperimentWorkflowRun.resource_id)
            )
        ).scalars().all()
    )
    if not source_runs:
        raise ValidationFailure("run group has no immutable run intent to resubmit")
    source_run_authority: list[dict[str, Any]] = []
    source_attempts: list[ExperimentRunAttempt] = []
    source_domains: set[str] = set()
    for run in source_runs:
        source_preparation = await session.get(
            ExperimentWorkflowPreparation, run.preparation_id
        )
        if source_preparation is None:
            raise ValidationFailure("run group source preparation authority is unavailable")
        await validate_preparation_authority(
            session,
            source_preparation,
            core_session=core_session,
        )
        plan_authority, _revision, _plan = await _preparation_plan_launch_authority(
            session, source_preparation, workspace_id=workspace_id
        )
        source_domains.add(plan_authority["domain_id"])
        attempts = list(
            (
                await session.execute(
                    select(ExperimentRunAttempt)
                    .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
                    .order_by(ExperimentRunAttempt.attempt_number)
                )
            ).scalars().all()
        )
        attempt_plan_authorities: list[dict[str, Any]] = []
        for attempt in attempts:
            attempt_authority = await _attempt_preparation_plan_authority(
                session,
                attempt,
                workspace_id=workspace_id,
                source_domain_id=source_domain_id,
                core_session=core_session,
            )
            source_domains.add(attempt_authority["plan_authority"]["domain_id"])
            attempt_plan_authorities.append(attempt_authority)
        source_attempts.extend(attempts)
        source_run_authority.append(
            {
                "run_id": run.resource_id,
                "preparation_id": run.preparation_id,
                "plan_authority": plan_authority,
                "state": run.state,
                "generation": int(run.generation),
                "attempts": [_attempt_authority(attempt) for attempt in attempts],
                "attempt_plan_authorities": attempt_plan_authorities,
            }
        )
    if source_domains != {source_domain_id}:
        raise NotFound("run group not found in the exact source Domain")
    if len(source_runs) + len(source_attempts) > MAX_REPLAY_AUTHORITY_ROWS:
        raise ValidationFailure("resubmit source history exceeds the bounded durable authority limit")
    cancellation = await _cancellation_disposition(
        session,
        group=source,
        runs=source_runs,
        attempts=source_attempts,
    )
    source_disposition = {
        "run_group_id": source.resource_id,
        "workspace_id": workspace_id,
        "domain_id": source_domain_id,
        "state": source.state,
        "generation": int(source.generation),
        "request_sha256": source.request_sha256,
        "runs": source_run_authority,
        "cancellation_disposition": cancellation,
    }

    replacement_plan_authorities: dict[str, dict[str, Any]] = {}
    for preparation_id in replacement_ids:
        preparation = await session.get(
            ExperimentWorkflowPreparation, preparation_id
        )
        if preparation is None:
            raise NotFound("one or more preparations were not found")
        await validate_preparation_authority(
            session, preparation, core_session=core_session
        )
        plan_authority, _revision, _plan = await _preparation_plan_launch_authority(
            session, preparation, workspace_id=workspace_id
        )
        if plan_authority["domain_id"] != source_domain_id:
            raise ValidationFailure("resubmit preparation belongs to another Domain")
        replacement_plan_authorities[preparation_id] = plan_authority

    resubmitted = await create_run_group(
        session,
        workspace_id,
        replacement_ids,
        idempotency_key=idempotency_key,
        idempotency_authority={
            "schema": "bms.experiment.resubmit-authority.v2",
            "request_sha256": request_sha256,
            "source_run_group_id": run_group_id,
            "source_domain_id": source_domain_id,
        },
        launch_context_ids=launch_context_ids,
        core_session=core_session,
        source_domain_id=source_domain_id,
    )
    if resubmitted.resource_id == source.resource_id:
        raise IdempotencyConflict("resubmit idempotency cannot resolve to its source run group")
    result_runs = list(
        (
            await session.execute(
                select(ExperimentWorkflowRun).where(
                    ExperimentWorkflowRun.run_group_id == resubmitted.resource_id
                )
            )
        ).scalars().all()
    )
    result_runs_by_preparation = {run.preparation_id: run for run in result_runs}
    if len(result_runs_by_preparation) != len(replacement_ids):
        raise DispatchFailure("resubmit result has incomplete run authority")
    replacements: list[dict[str, Any]] = []
    for preparation_id in replacement_ids:
        preparation = await session.get(
            ExperimentWorkflowPreparation, preparation_id
        )
        run = result_runs_by_preparation[preparation_id]
        attempt = await session.scalar(
            select(ExperimentRunAttempt)
            .where(ExperimentRunAttempt.workflow_run_id == run.resource_id)
            .order_by(ExperimentRunAttempt.attempt_number)
        )
        if preparation is None or attempt is None:
            raise DispatchFailure("resubmit result has incomplete attempt authority")
        plan_authority = replacement_plan_authorities[preparation_id]
        context_id = launch_context_ids.get(preparation_id)
        context: ExperimentLaunchContext | None = None
        if plan_authority["launch_mode"] == TYPED_HANDOFF_LAUNCH_MODE:
            if context_id is None:
                raise DispatchFailure("typed resubmit result lost its launch context authority")
            context = await _validate_bound_launch_context(
                session,
                launch_context_id=context_id,
                preparation=preparation,
                workspace_id=workspace_id,
                authority=plan_authority,
                attempt=attempt,
            )
        replacements.append(
            {
                "preparation_id": preparation_id,
                "plan_authority": plan_authority,
                "resulting_run_id": run.resource_id,
                "resulting_attempt": _attempt_identity_authority(attempt),
                "launch_context_id": context_id,
                "launch_context": (
                    _launch_context_authority(context) if context is not None else None
                ),
            }
        )
    existing_edges = list(
        (
            await session.execute(
                select(ExperimentLineageEdge).where(
                    ExperimentLineageEdge.source_resource_id == resubmitted.resource_id,
                    ExperimentLineageEdge.target_resource_id == source.resource_id,
                    ExperimentLineageEdge.edge_mode == "resubmitted_from",
                )
            )
        ).scalars().all()
    )
    if len(existing_edges) > 1:
        raise DispatchFailure("resubmit has more than one source lineage edge")
    if existing_edges:
        lineage = existing_edges[0]
    else:
        lineage = ExperimentLineageEdge(
            id=new_id("resubmit-lineage"),
            workspace_id=workspace_id,
            source_resource_id=resubmitted.resource_id,
            target_resource_id=source.resource_id,
            edge_mode="resubmitted_from",
            edge_key="source-run-group",
            metadata_json=canonical_json(
                {
                    "schema": "bms.experiment.resubmit-lineage.v2",
                    "request_sha256": request_sha256,
                    "source_run_group_id": source.resource_id,
                    "source_domain_id": source_domain_id,
                    "source_state": source.state,
                    "source_generation": int(source.generation),
                    "source_request_sha256": source.request_sha256,
                }
            ),
            created_at=now(),
        )
        session.add(lineage)
    await session.flush()
    response = {
        "schema": "bms.experiment.resubmit-response.v2",
        "caller_request": caller_request,
        "source": source_disposition,
        "replacements": replacements,
        "result_run_group_id": resubmitted.resource_id,
        "result_request_sha256": resubmitted.request_sha256,
        "lineage": _lineage_authority(lineage),
    }
    response_json = _bounded_canonical_authority(response, label="resubmit response")
    session.add(
        ExperimentIdempotencyClaim(
            scope=scope,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            result_resource_id=resubmitted.resource_id,
            response_json=response_json,
            response_sha256=sha256_text(response_json),
            created_at=now(),
        )
    )
    add_audit_event(
        session,
        workspace_id=workspace_id,
        resource_id=resubmitted.resource_id,
        event_type="run_group_resubmitted",
        generation=resubmitted.generation,
        payload={
            "source_run_group_id": source.resource_id,
            "source_domain_id": source_domain_id,
            "request_sha256": request_sha256,
        },
    )
    await session.flush()
    return resubmitted

class DispatchMaterializer(Protocol):
    async def materialize(self, attempt_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class _DeferredCommitCoreSession:
    """Hold managed materializer writes until Job resource authority is attached."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def commit(self) -> None:
        await self._session.flush()


def _materialization_resource_authority(
    attempt_id: str,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        handoff = validate_resource_admission_handoff(payload.get("resource_admission"))
        if handoff is None:
            raise ResourceUsageEvidenceError("dispatch resource admission authority is required")
        if (
            handoff["run_attempt_id"] != attempt_id
            or handoff["canonical_job_id"] != payload.get("scheduler_job_id")
        ):
            raise ResourceUsageEvidenceError("dispatch resource admission identity diverges")
        dispatch = build_dispatch_materialization_authority(
            payload_sha256=sha256_text(canonical_json(dict(payload))),
            handoff=handoff,
        )
    except ResourceUsageEvidenceError as exc:
        raise DispatchFailure(str(exc)) from exc
    return handoff, dispatch


def _attach_materialization_resource_authority(
    params: object,
    handoff: Mapping[str, Any],
    dispatch: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return attach_dispatch_materialization_authority(
            attach_resource_admission_handoff(params, handoff),
            dispatch,
        )
    except ResourceUsageEvidenceError as exc:
        raise DispatchFailure(str(exc)) from exc


def _public_materialization_resource_binding(
    handoff: Mapping[str, Any],
    dispatch: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "bms.global-resource-materialization-binding.v1",
        "admission_id": handoff["admission_id"],
        "run_attempt_id": handoff["run_attempt_id"],
        "canonical_job_id": handoff["canonical_job_id"],
        "admission_handoff_sha256": handoff["handoff_sha256"],
        "dispatch_payload_sha256": dispatch["payload_sha256"],
        "dispatch_authority_sha256": dispatch["authority_sha256"],
    }


class ExistingJobMaterializer:
    """Dispatch only through explicitly registered typed materializers."""

    _CM_MATERIALIZERS = {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
    }

    def __init__(self, core_session: AsyncSession):
        self.core_session = core_session

    async def materialize(self, attempt_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        scheduler = payload.get("scheduler")
        if not isinstance(scheduler, dict):
            raise DispatchFailure("dispatch payload has no scheduler object")
        params = scheduler.get("params")
        if not isinstance(params, dict):
            raise DispatchFailure("dispatch scheduler payload has no typed params")
        adapter_id = str(params.get("workflow_adapter") or "")
        handoff, dispatch_authority = _materialization_resource_authority(attempt_id, payload)
        authoritative_params = _attach_materialization_resource_authority(
            params,
            handoff,
            dispatch_authority,
        )
        resource_binding = _public_materialization_resource_binding(handoff, dispatch_authority)
        if (
            adapter_id in TYPED_CORE_JOB_ADAPTERS
            or adapter_id in PROJECT_SCHEDULED_TYPED_CORE_ADAPTERS
        ):
            from database import Job

            scheduler_job_id = str(payload.get("scheduler_job_id") or "")
            if scheduler_job_id != scheduler_job_id_for_attempt(attempt_id):
                raise DispatchFailure("typed core scheduler job identity disagrees with its run attempt")
            expected_model_id = TYPED_CORE_JOB_ADAPTERS.get(adapter_id)
            if expected_model_id is None and scheduler.get("model_id") in TYPED_CORE_JOB_MODELS:
                expected_model_id = str(scheduler["model_id"])
            if expected_model_id is None:
                raise DispatchFailure("typed workflow adapter has no executable model authority")
            expected_mode = str(scheduler.get("mode") or "run")
            if scheduler.get("model_id") != expected_model_id:
                raise DispatchFailure("typed workflow adapter and scheduler model_id disagree")
            pinned_gpu: int | None = handoff["gpu_index"]
            if expected_model_id == "protein_local_redesign":
                resources = scheduler.get("resources")
                raw_pinned_gpu = resources.get("pinned_gpu") if isinstance(resources, dict) else None
                if (
                    isinstance(raw_pinned_gpu, bool)
                    or not isinstance(raw_pinned_gpu, int)
                    or raw_pinned_gpu < 0
                    or raw_pinned_gpu != pinned_gpu
                ):
                    raise DispatchFailure("native RFD3 GPU authority differs from resource admission")
            existing_job = await self.core_session.get(Job, scheduler_job_id)
            if existing_job is not None:
                params_match = dict(existing_job.params or {}) == authoritative_params
                if expected_model_id == "protein_local_redesign":
                    expected_request = authoritative_params.get("rfd3_request")
                    existing_params = dict(existing_job.params or {})
                    existing_request = existing_params.get("rfd3_request")
                    expected_without_request = dict(authoritative_params)
                    expected_without_request.pop("rfd3_request", None)
                    existing_without_request = dict(existing_params)
                    existing_without_request.pop("rfd3_request", None)
                    params_match = (
                        existing_without_request == expected_without_request
                        and isinstance(expected_request, dict)
                        and isinstance(existing_request, dict)
                        and local_redesign_requests_semantically_equal(existing_request, expected_request)
                    )
                if (
                    existing_job.model_id != expected_model_id
                    or existing_job.mode != expected_mode
                    or not params_match
                    or existing_job.pinned_gpu != pinned_gpu
                ):
                    raise DispatchFailure("preallocated Job identity conflicts with typed dispatch replay")
                job = existing_job
            else:
                from fastapi import BackgroundTasks
                from routers.jobs import _create_job
                from schemas import JobCreate

                request = JobCreate(
                    name=str(scheduler.get("name") or f"Global Experiment {expected_model_id}"),
                    model_id=expected_model_id,
                    mode=expected_mode,
                    params=authoritative_params,
                    pinned_gpu=pinned_gpu,
                )
                await _create_job(
                    request,
                    BackgroundTasks(),
                    self.core_session,
                    scheduler_job_id,
                    True,
                    None,
                    None,
                    True,
                )
                job = await self.core_session.get(Job, scheduler_job_id)
                if job is None:
                    raise DispatchFailure("canonical typed Job creation did not persist the preallocated Job")
            return {
                "external_job_id": job.id,
                "acknowledgement": {
                    "schema": "bms.global.external-binding-receipt.v1",
                    "adapter_id": adapter_id,
                    "attempt_id": attempt_id,
                    "external_store": "core.jobs",
                    "external_job_id": job.id,
                    "external_model_id": job.model_id,
                    "external_mode": job.mode,
                    "external_state": job.status,
                    "pinned_gpu": job.pinned_gpu,
                    "resource_authority": resource_binding,
                },
            }
        if adapter_id not in self._CM_MATERIALIZERS:
            raise DispatchFailure("no registered typed materializer accepts this workflow adapter")
        from database import Job
        from services.conformational_mapping.global_adapter import materialize_preallocated_cm_job

        existing_job = await self.core_session.get(Job, attempt_id)
        if existing_job is not None:
            existing_params = dict(existing_job.params or {})
            if RESOURCE_USAGE_RECEIPTS_PARAM in existing_params:
                raise DispatchFailure("managed Job has resource receipts before dispatch acknowledgement")
            try:
                existing_handoff = validate_resource_admission_handoff(
                    existing_params.get(GLOBAL_RESOURCE_ADMISSION_PARAM)
                )
                existing_dispatch = validate_dispatch_materialization_authority(
                    existing_params.get(GLOBAL_DISPATCH_AUTHORITY_PARAM),
                    expected_handoff=existing_handoff,
                )
            except ResourceUsageEvidenceError as exc:
                raise DispatchFailure("managed Job recovery resource authority is invalid") from exc
            if existing_handoff != handoff or existing_dispatch != dispatch_authority:
                raise DispatchFailure("managed Job recovery resource authority has conflicting bytes")
            base_params = strip_resource_execution_metadata(existing_params)
            if (
                _attach_materialization_resource_authority(
                    base_params,
                    handoff,
                    dispatch_authority,
                )
                != existing_params
            ):
                raise DispatchFailure("managed Job recovery resource authority is not byte-identical")
            existing_job.params = base_params

        receipt = await materialize_preallocated_cm_job(
            _DeferredCommitCoreSession(self.core_session),
            attempt_id=attempt_id,
            scheduler=scheduler,
            run_group_id=str(payload.get("run_group_id") or ""),
        )
        job = await self.core_session.get(Job, attempt_id)
        if job is None:
            raise DispatchFailure("managed materializer did not persist its canonical Job")
        if getattr(job, "pinned_gpu", None) != handoff["gpu_index"]:
            raise DispatchFailure("managed Job GPU authority differs from resource admission")
        job.params = _attach_materialization_resource_authority(
            job.params,
            handoff,
            dispatch_authority,
        )
        await self.core_session.commit()
        return {**receipt, "resource_authority": resource_binding}


def _outbox_values(**values: Any) -> dict[str, Any]:
    """Use v8 lease columns when present while remaining compatible with v7."""
    columns = ExperimentDispatchOutbox.__table__.columns
    return {key: value for key, value in values.items() if key in columns}


def _public_dispatch_failure_text(exc: Exception) -> str:
    if isinstance(exc, ExperimentServiceError):
        controlled = str(exc).strip() or "dispatch failed"
    else:
        controlled = "dispatch materialization failed"
    return controlled[:512]


def _failed_outbox_materialization_evidence(
    row: ExperimentDispatchOutbox,
) -> str | None:
    acknowledgement_json = row.acknowledgement_json
    if (
        row.status != "failed"
        or not isinstance(acknowledgement_json, str)
        or not acknowledgement_json
        or len(acknowledgement_json.encode("utf-8")) > MAX_REPLAY_AUTHORITY_BYTES
    ):
        return None
    try:
        acknowledgement = json.loads(acknowledgement_json)
        payload = json.loads(row.payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(acknowledgement, dict)
        or canonical_json(acknowledgement) != acknowledgement_json
        or not isinstance(payload, dict)
        or payload.get("schema") != "bms.experiment.dispatch.v1"
        or canonical_json(payload) != row.payload_json
        or sha256_text(row.payload_json) != row.payload_sha256
        or payload.get("attempt_id") != row.run_attempt_id
    ):
        return None
    scheduler_job_id = payload.get("scheduler_job_id")
    if not isinstance(scheduler_job_id, str) or not scheduler_job_id:
        return None
    external_job_id = acknowledgement.get("external_job_id")
    materialized_job_id = acknowledgement.get("scheduler_job_id")
    if (
        external_job_id is None
        and materialized_job_id is None
        or external_job_id is not None
        and external_job_id != scheduler_job_id
        or materialized_job_id is not None
        and materialized_job_id != scheduler_job_id
    ):
        return None
    binding = acknowledgement.get("acknowledgement")
    if binding is not None and (
        not isinstance(binding, dict)
        or binding.get("schema") != "bms.global.external-binding-receipt.v1"
        or binding.get("attempt_id") != row.run_attempt_id
        or binding.get("external_job_id") != scheduler_job_id
    ):
        return None
    return acknowledgement_json


async def _failed_outbox_recovery_is_open(
    session: AsyncSession,
    row: ExperimentDispatchOutbox,
) -> bool:
    attempt = await session.get(ExperimentRunAttempt, row.run_attempt_id)
    run = await session.get(
        ExperimentWorkflowRun,
        attempt.workflow_run_id if attempt is not None else "",
    )
    group = await session.get(
        ExperimentRunGroup,
        run.run_group_id if run is not None else "",
    )
    if (
        attempt is None
        or run is None
        or group is None
        or attempt.state != "pending"
        or attempt.external_binding_receipt_json is not None
        or attempt.terminal_receipt_json is not None
        or attempt.terminal_receipt_sha256 is not None
        or run.state != "dispatch_pending"
    ):
        return False
    expected_group_state = await derive_run_group_state(session, group.resource_id)
    if (
        expected_group_state not in {"dispatch_pending", "partially_dispatched"}
        or group.state != expected_group_state
    ):
        return False
    if await _blocking_run_control_command(session, group.resource_id) is not None:
        return False
    cancel_scope = f"run-group-cancel:{sha256_text(group.resource_id)}"
    cancellation_claim = await session.scalar(
        select(ExperimentIdempotencyClaim.scope).where(
            ExperimentIdempotencyClaim.scope == cancel_scope
        ).limit(1)
    )
    cancellation_audit = await session.scalar(
        select(ExperimentAuditEvent.id).where(
            ExperimentAuditEvent.workspace_id == group.workspace_id,
            ExperimentAuditEvent.resource_id == group.resource_id,
            ExperimentAuditEvent.event_type == "run_group_cancelled",
        ).limit(1)
    )
    return cancellation_claim is None and cancellation_audit is None


async def _require_dispatch_eligibility(
    session: AsyncSession,
    *,
    row: ExperimentDispatchOutbox,
    lease_token: str,
    payload: dict[str, Any],
    attempt: ExperimentRunAttempt,
    preparation: ExperimentWorkflowPreparation,
    run: ExperimentWorkflowRun,
    group: ExperimentRunGroup,
    recovery_receipt_json: str | None,
) -> str:
    expected_group_state = await derive_run_group_state(session, group.resource_id)
    if (
        row.status != "dispatching"
        or row.lease_token != lease_token
        or row.run_attempt_id != attempt.resource_id
        or row.acknowledgement_json != recovery_receipt_json
        or attempt.state != "pending"
        or attempt.external_binding_receipt_json is not None
        or attempt.terminal_receipt_json is not None
        or attempt.terminal_receipt_sha256 is not None
        or run.state != "dispatch_pending"
        or expected_group_state not in {"dispatch_pending", "partially_dispatched"}
        or group.state != expected_group_state
        or row.workspace_id != attempt.workspace_id
        or attempt.workspace_id != preparation.workspace_id
        or run.workspace_id != row.workspace_id
        or group.workspace_id != row.workspace_id
        or run.resource_id != attempt.workflow_run_id
        or run.run_group_id != group.resource_id
        or payload.get("run_group_id") != group.resource_id
        or payload.get("workflow_run_id") != run.resource_id
        or payload.get("attempt_id") != attempt.resource_id
        or payload.get("scheduler_job_id") != attempt.scheduler_job_id
        or payload.get("workflow_revision_id") != preparation.workflow_revision_id
        or canonical_json(payload.get("scheduler")) != preparation.scheduler_payload_json
    ):
        raise DispatchFailure("dispatch lease is no longer exactly eligible for materialization")
    if await _blocking_run_control_command(session, group.resource_id) is not None:
        raise DispatchFailure("dispatch authority is permanently closed by cancellation command")
    cancel_scope = f"run-group-cancel:{sha256_text(group.resource_id)}"
    cancellation_claim = await session.scalar(
        select(ExperimentIdempotencyClaim.scope).where(
            ExperimentIdempotencyClaim.scope == cancel_scope
        ).limit(1)
    )
    cancellation_audit = await session.scalar(
        select(ExperimentAuditEvent.id).where(
            ExperimentAuditEvent.workspace_id == group.workspace_id,
            ExperimentAuditEvent.resource_id == group.resource_id,
            ExperimentAuditEvent.event_type == "run_group_cancelled",
        ).limit(1)
    )
    if cancellation_claim is not None or cancellation_audit is not None:
        raise DispatchFailure("dispatch authority is permanently closed by cancellation history")
    return expected_group_state


async def dispatch_pending_outbox(
    session: AsyncSession,
    materializer: DispatchMaterializer,
    *,
    core_session: AsyncSession,
    lease_owner: str | None = None,
) -> int:
    lease_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    standard_dispatchable = or_(
        ExperimentDispatchOutbox.status == "pending",
        and_(
            ExperimentDispatchOutbox.status == "dispatching",
            or_(
                ExperimentDispatchOutbox.lease_expires_at < now(),
                and_(
                    ExperimentDispatchOutbox.lease_expires_at.is_(None),
                    ExperimentDispatchOutbox.updated_at < lease_cutoff,
                ),
            ),
        ),
    )
    candidate = (
        await session.execute(
            select(ExperimentDispatchOutbox)
            .where(standard_dispatchable)
            .order_by(ExperimentDispatchOutbox.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    recovery_receipt_json: str | None = None
    if candidate is None and isinstance(materializer, ExistingJobMaterializer):
        failed_candidates = list(
            (
                await session.execute(
                    select(ExperimentDispatchOutbox)
                    .where(
                        ExperimentDispatchOutbox.status == "failed",
                        ExperimentDispatchOutbox.acknowledgement_json.is_not(None),
                    )
                    .order_by(ExperimentDispatchOutbox.created_at)
                    .limit(MAX_REPLAY_AUTHORITY_ROWS)
                )
            ).scalars().all()
        )
        for failed_candidate in failed_candidates:
            evidence = _failed_outbox_materialization_evidence(failed_candidate)
            if evidence is not None and await _failed_outbox_recovery_is_open(
                session, failed_candidate
            ):
                candidate = failed_candidate
                recovery_receipt_json = evidence
                break
    if candidate is None:
        return 0
    lease_token = new_id("lease")
    claim_eligibility = (
        and_(
            ExperimentDispatchOutbox.status == "failed",
            ExperimentDispatchOutbox.acknowledgement_json == recovery_receipt_json,
        )
        if recovery_receipt_json is not None
        else standard_dispatchable
    )
    claimed = await session.execute(
        update(ExperimentDispatchOutbox)
        .where(
            ExperimentDispatchOutbox.id == candidate.id,
            claim_eligibility,
        )
        .values(
            **_outbox_values(
                status="dispatching",
                dispatch_attempts=ExperimentDispatchOutbox.dispatch_attempts + 1,
                lease_token=lease_token,
                lease_owner=lease_owner,
                lease_acquired_at=now(),
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                updated_at=now(),
            )
        )
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return 0
    await session.commit()
    row = await session.get(ExperimentDispatchOutbox, candidate.id)
    if row is None:
        raise DispatchFailure("outbox row disappeared after lease claim")
    row_id = str(row.id)
    row_attempt_id = str(row.run_attempt_id)
    materialized_receipt_json: str | None = recovery_receipt_json
    try:
        payload = json.loads(row.payload_json)
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "bms.experiment.dispatch.v1"
            or canonical_json(payload) != row.payload_json
            or sha256_text(row.payload_json) != row.payload_sha256
        ):
            raise DispatchFailure("dispatch payload does not match canonical durable outbox authority")
        attempt = await session.get(ExperimentRunAttempt, row.run_attempt_id)
        preparation = await session.get(
            ExperimentWorkflowPreparation,
            attempt.preparation_id if attempt is not None else "",
        )
        run = await session.get(
            ExperimentWorkflowRun,
            attempt.workflow_run_id if attempt is not None else "",
        )
        group = await session.get(
            ExperimentRunGroup,
            run.run_group_id if run is not None else "",
        )
        if attempt is None or preparation is None or run is None or group is None:
            raise DispatchFailure("outbox does not have complete attempt/run/group authority")

        # This no-op CAS acquires the experiment-store write transaction before
        # the external materializer runs. Cancellation either commits first and
        # is observed below, or waits and must observe this dispatch transaction.
        eligibility_lock = await session.execute(
            update(ExperimentDispatchOutbox)
            .where(
                ExperimentDispatchOutbox.id == row.id,
                ExperimentDispatchOutbox.status == "dispatching",
                ExperimentDispatchOutbox.lease_token == lease_token,
            )
            .values(updated_at=ExperimentDispatchOutbox.updated_at)
            .execution_options(synchronize_session=False)
        )
        if eligibility_lock.rowcount != 1:
            raise DispatchFailure("dispatch lease changed before materialization")
        await session.refresh(row)
        await session.refresh(attempt)
        await session.refresh(run)
        await session.refresh(group)
        expected_group_state = await _require_dispatch_eligibility(
            session,
            row=row,
            lease_token=lease_token,
            payload=payload,
            attempt=attempt,
            preparation=preparation,
            run=run,
            group=group,
            recovery_receipt_json=recovery_receipt_json,
        )
        launch_authority, _revision, _plan = await _preparation_plan_launch_authority(
            session,
            preparation,
            workspace_id=row.workspace_id,
        )
        if launch_authority["launch_mode"] not in MANAGED_DISPATCH_LAUNCH_MODES:
            raise DispatchFailure("outbox materialization is prohibited by the pinned Plan launch mode")
        bound_context = await session.scalar(
            select(ExperimentLaunchContext).where(
                ExperimentLaunchContext.run_attempt_id == attempt.resource_id
            )
        )
        if bound_context is not None:
            raise DispatchFailure("managed outbox attempt has prohibited external launch context authority")
        await validate_preparation_authority(
            session,
            preparation,
            core_session=core_session,
        )
        receipt = await materializer.materialize(row.run_attempt_id, payload)
        replayed_receipt_json = canonical_json(_public_runtime_metadata(receipt))
        if (
            recovery_receipt_json is not None
            and replayed_receipt_json != recovery_receipt_json
        ):
            raise DispatchFailure("recovered Job public receipt changed during idempotent replay")
        materialized_receipt_json = replayed_receipt_json

        # Re-read every mutable authority row after the external side effect and
        # before any acknowledgement or dispatched-state publication.
        await session.refresh(row)
        await session.refresh(attempt)
        await session.refresh(run)
        await session.refresh(group)
        confirmed_group_state = await _require_dispatch_eligibility(
            session,
            row=row,
            lease_token=lease_token,
            payload=payload,
            attempt=attempt,
            preparation=preparation,
            run=run,
            group=group,
            recovery_receipt_json=recovery_receipt_json,
        )
        if confirmed_group_state != expected_group_state:
            raise DispatchFailure("dispatch group state changed before acknowledgement")
        if materialized_receipt_json is None:
            raise DispatchFailure("materializer returned no public receipt authority")

        expected_generation = int(run.generation)
        attempt_update = await session.execute(
            update(ExperimentRunAttempt)
            .where(
                ExperimentRunAttempt.resource_id == attempt.resource_id,
                ExperimentRunAttempt.workspace_id == row.workspace_id,
                ExperimentRunAttempt.workflow_run_id == run.resource_id,
                ExperimentRunAttempt.preparation_id == preparation.resource_id,
                ExperimentRunAttempt.scheduler_job_id == payload["scheduler_job_id"],
                ExperimentRunAttempt.state == "pending",
                ExperimentRunAttempt.external_binding_receipt_json.is_(None),
                ExperimentRunAttempt.terminal_receipt_json.is_(None),
                ExperimentRunAttempt.terminal_receipt_sha256.is_(None),
            )
            .values(
                state="dispatched",
                external_binding_receipt_json=materialized_receipt_json,
            )
            .execution_options(synchronize_session=False)
        )
        run_update = await session.execute(
            update(ExperimentWorkflowRun)
            .where(
                ExperimentWorkflowRun.resource_id == run.resource_id,
                ExperimentWorkflowRun.workspace_id == row.workspace_id,
                ExperimentWorkflowRun.run_group_id == group.resource_id,
                ExperimentWorkflowRun.state == "dispatch_pending",
                ExperimentWorkflowRun.generation == expected_generation,
            )
            .values(state="dispatched", generation=expected_generation + 1)
            .execution_options(synchronize_session=False)
        )
        post_group_state = await derive_run_group_state(session, group.resource_id)
        group_generation = int(group.generation)
        group_update = await session.execute(
            update(ExperimentRunGroup)
            .where(
                ExperimentRunGroup.resource_id == group.resource_id,
                ExperimentRunGroup.workspace_id == row.workspace_id,
                ExperimentRunGroup.state == expected_group_state,
                ExperimentRunGroup.generation == group_generation,
            )
            .values(
                state=post_group_state,
                generation=group_generation + 1,
                updated_at=now(),
            )
            .execution_options(synchronize_session=False)
        )
        acknowledgement_guard = (
            ExperimentDispatchOutbox.acknowledgement_json == recovery_receipt_json
            if recovery_receipt_json is not None
            else ExperimentDispatchOutbox.acknowledgement_json.is_(None)
        )
        acknowledged_update = await session.execute(
            update(ExperimentDispatchOutbox)
            .where(
                ExperimentDispatchOutbox.id == row.id,
                ExperimentDispatchOutbox.status == "dispatching",
                ExperimentDispatchOutbox.lease_token == lease_token,
                acknowledgement_guard,
            )
            .values(
                **_outbox_values(
                    status="acknowledged",
                    lease_token=None,
                    lease_owner=None,
                    lease_acquired_at=None,
                    lease_expires_at=None,
                    acknowledgement_json=materialized_receipt_json,
                    last_error=None,
                    acknowledged_at=now(),
                    updated_at=now(),
                )
            )
            .execution_options(synchronize_session=False)
        )
        if (
            attempt_update.rowcount != 1
            or run_update.rowcount != 1
            or group_update.rowcount != 1
            or acknowledged_update.rowcount != 1
        ):
            raise DispatchFailure("dispatch authority changed during atomic acknowledgement")
        sequence = int(
            (
                await session.execute(
                    select(func.max(ExperimentRunEvent.sequence_number)).where(
                        ExperimentRunEvent.workflow_run_id == run.resource_id
                    )
                )
            ).scalar_one()
            or 0
        ) + 1
        session.add(
            ExperimentRunEvent(
                workspace_id=run.workspace_id,
                workflow_run_id=run.resource_id,
                sequence_number=sequence,
                expected_generation=expected_generation,
                resulting_generation=expected_generation + 1,
                idempotency_key=f"scheduler-materialized:{attempt.resource_id}",
                event_type="scheduler_job_materialized",
                payload_json=materialized_receipt_json,
                created_at=now(),
            )
        )
        await session.commit()
        return 1
    except Exception as exc:
        # Discard every uncommitted success projection before handling failure;
        # stale ORM state must never flush over cancellation or terminal state.
        await session.rollback()
        failure_values: dict[str, Any] = {
            "status": "failed",
            "lease_token": None,
            "lease_owner": None,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "last_error": _public_dispatch_failure_text(exc),
            "updated_at": now(),
        }
        if materialized_receipt_json is not None:
            failure_values["acknowledgement_json"] = materialized_receipt_json
        failed_update = await session.execute(
            update(ExperimentDispatchOutbox)
            .where(
                ExperimentDispatchOutbox.id == row_id,
                ExperimentDispatchOutbox.status == "dispatching",
                ExperimentDispatchOutbox.lease_token == lease_token,
            )
            .values(**_outbox_values(**failure_values))
            .execution_options(synchronize_session=False)
        )
        if failed_update.rowcount == 1:
            if materialized_receipt_json is None:
                current_attempt = await session.get(ExperimentRunAttempt, row_attempt_id)
                current_run = await session.get(
                    ExperimentWorkflowRun,
                    current_attempt.workflow_run_id if current_attempt is not None else "",
                )
                current_group = await session.get(
                    ExperimentRunGroup,
                    current_run.run_group_id if current_run is not None else "",
                )
                cancel_scope = (
                    f"run-group-cancel:{sha256_text(current_group.resource_id)}"
                    if current_group is not None
                    else ""
                )
                cancellation_claim = await session.scalar(
                    select(ExperimentIdempotencyClaim.scope).where(
                        ExperimentIdempotencyClaim.scope == cancel_scope
                    ).limit(1)
                )
                cancellation_audit = await session.scalar(
                    select(ExperimentAuditEvent.id).where(
                        ExperimentAuditEvent.workspace_id
                        == (current_group.workspace_id if current_group is not None else ""),
                        ExperimentAuditEvent.resource_id
                        == (current_group.resource_id if current_group is not None else ""),
                        ExperimentAuditEvent.event_type == "run_group_cancelled",
                    ).limit(1)
                )
                cancellation_command = (
                    await _blocking_run_control_command(session, current_group.resource_id)
                    if current_group is not None
                    else None
                )
                expected_group_state = (
                    await derive_run_group_state(session, current_group.resource_id)
                    if current_group is not None
                    else None
                )
                if (
                    current_attempt is not None
                    and current_run is not None
                    and current_group is not None
                    and current_attempt.state == "pending"
                    and current_attempt.external_binding_receipt_json is None
                    and current_attempt.terminal_receipt_json is None
                    and current_attempt.terminal_receipt_sha256 is None
                    and current_run.state == "dispatch_pending"
                    and expected_group_state
                    in {"dispatch_pending", "partially_dispatched"}
                    and current_group.state == expected_group_state
                    and cancellation_command is None
                    and cancellation_claim is None
                    and cancellation_audit is None
                ):
                    attempt_failure = await session.execute(
                        update(ExperimentRunAttempt)
                        .where(
                            ExperimentRunAttempt.resource_id == current_attempt.resource_id,
                            ExperimentRunAttempt.workspace_id == current_attempt.workspace_id,
                            ExperimentRunAttempt.workflow_run_id == current_run.resource_id,
                            ExperimentRunAttempt.preparation_id
                            == current_attempt.preparation_id,
                            ExperimentRunAttempt.state == "pending",
                            ExperimentRunAttempt.external_binding_receipt_json.is_(None),
                            ExperimentRunAttempt.terminal_receipt_json.is_(None),
                            ExperimentRunAttempt.terminal_receipt_sha256.is_(None),
                        )
                        .values(state="failed")
                        .execution_options(synchronize_session=False)
                    )
                    run_generation = int(current_run.generation)
                    run_failure = await session.execute(
                        update(ExperimentWorkflowRun)
                        .where(
                            ExperimentWorkflowRun.resource_id == current_run.resource_id,
                            ExperimentWorkflowRun.workspace_id == current_run.workspace_id,
                            ExperimentWorkflowRun.run_group_id == current_group.resource_id,
                            ExperimentWorkflowRun.state == "dispatch_pending",
                            ExperimentWorkflowRun.generation == run_generation,
                        )
                        .values(
                            state="failed",
                            generation=run_generation + 1,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    post_failure_group_state = await derive_run_group_state(
                        session, current_group.resource_id
                    )
                    group_generation = int(current_group.generation)
                    group_failure = await session.execute(
                        update(ExperimentRunGroup)
                        .where(
                            ExperimentRunGroup.resource_id == current_group.resource_id,
                            ExperimentRunGroup.workspace_id == current_group.workspace_id,
                            ExperimentRunGroup.state == expected_group_state,
                            ExperimentRunGroup.generation == group_generation,
                        )
                        .values(
                            state=post_failure_group_state,
                            generation=group_generation + 1,
                            updated_at=now(),
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if (
                        attempt_failure.rowcount != 1
                        or run_failure.rowcount != 1
                        or group_failure.rowcount != 1
                    ):
                        await session.rollback()
                        raise
            await session.commit()
        else:
            await session.rollback()
        raise


__all__ = [
    "DispatchFailure",
    "DispatchMaterializer",
    "ExistingJobMaterializer",
    "ExperimentServiceError",
    "IdempotencyConflict",
    "NotFound",
    "RevisionConflict",
    "ValidationFailure",
    "append_research_record",
    "canonical_json",
    "create_dataset",
    "create_domain_experiment",
    "create_experiment",
    "create_global_experiment",
    "create_experiment_workspace",
    "create_project",
    "create_run_group",
    "create_workflow",
    "derive_run_group_state",
    "dispatch_pending_outbox",
    "now",
    "prepare_workflow",
    "restore_aggregate",
    "save_hierarchy_revision",
    "save_dataset_revision",
    "save_workflow_draft",
    "save_workflow_revision",
    "sha256_text",
]
