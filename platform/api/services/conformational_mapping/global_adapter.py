"""Typed global-workspace adapter for preallocated CM scheduler attempts."""
from __future__ import annotations

import json
import copy
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import ConformationalMappingRequest, ConformationalMappingSource, Job
from paths import get_results_dir
from routers.conformational_mapping import (
    _PERSONAL_WORKFLOW_PRINCIPAL,
    _bind_analysis_policy,
    _bind_confornets_submission_policy,
    _bind_runtime_policy,
    _cm_job_admission,
    _managed_checkpoint_for_submission,
    _runtime_registry,
    _server_confornets_identity,
)
from services.conformational_mapping.contracts import (
    ContractValidationError,
    canonical_json_loads,
    canonical_sha256,
    validate_schema,
)
from services.conformational_mapping.import_stager import RegisteredArtifact, stage_registered_assets
from services.conformational_mapping.persistence import (
    issue_request_capability,
    register_prepared_request,
    transition_request,
)
from services.conformational_mapping.request_builder import (
    materialize_trusted_internal_request,
    validate_request_params,
)
from experiment_services import (
    DispatchFailure,
    ValidationFailure,
    WORKFLOW_ADAPTER_REGISTRY,
    _cm_submission_source_ids,
    canonical_json,
    sha256_text,
)

_GENERATOR_ADAPTERS = {
    "bms.cm.protenix_v2.adapter.v1": "protenix_v2_ensemble",
    "bms.cm.confornets.adapter.v1": "confornets",
}
EXECUTABLE_CM_ADAPTERS = frozenset(_GENERATOR_ADAPTERS)
if WORKFLOW_ADAPTER_REGISTRY["conformational_mapping"] != set(EXECUTABLE_CM_ADAPTERS):
    raise RuntimeError("registered CM global adapters must have executable materializers")

_COORDINATE_PLAN_FIELDS = frozenset({
    "schema_name",
    "schema_version",
    "request_id",
    "backend",
    "request_sha256",
    "expected_cardinality",
    "coordinates",
    "coordinate_plan_sha256",
})
_RECOVERABLE_REQUEST_PARAM_FIELDS = frozenset({
    "backend",
    "targets",
    "ordered_seeds",
    "samples_per_seed",
    "feature_policy",
    "runtime_policy",
    "analysis_policy",
    "run_record",
    "state_landscape_comparison",
    "confornets",
    "protenix_snapshot_id",
})


def _validate_recovered_coordinate_plan(
    request_json: Mapping[str, Any],
    coordinate_plan_json: Mapping[str, Any],
) -> None:
    """Validate the current plan schema, with the current-tree semantic fallback."""

    try:
        validate_schema("cm_coordinate_plan_v1", coordinate_plan_json)
    except ContractValidationError as exc:
        if str(exc) != "unknown schema key: cm_coordinate_plan_v1":
            raise

    # The current contract registry already calls this schema key from retry
    # recovery, but this pinned tree does not yet publish its standalone file.
    # Enforce the same closed envelope and rederive the backend coordinates from
    # the schema-valid request rather than weakening existing-attempt recovery.
    if set(coordinate_plan_json) != _COORDINATE_PLAN_FIELDS:
        raise ContractValidationError("cm_coordinate_plan_v1 has unexpected fields")
    request_params = {
        key: request_json[key]
        for key in _RECOVERABLE_REQUEST_PARAM_FIELDS
        if key in request_json
    }
    validated = validate_request_params(request_params)
    coordinates = list(validated.coordinate_plan)
    if (
        coordinate_plan_json.get("schema_name") != "cm_coordinate_plan"
        or coordinate_plan_json.get("schema_version") != 1
        or coordinate_plan_json.get("request_id") != request_json.get("request_id")
        or coordinate_plan_json.get("backend") != request_json.get("backend")
        or coordinate_plan_json.get("request_sha256") != request_json.get("request_sha256")
        or coordinate_plan_json.get("expected_cardinality") != len(coordinates)
        or coordinate_plan_json.get("coordinates") != coordinates
    ):
        raise ContractValidationError("cm_coordinate_plan_v1 does not match request authority")


def _seal_confornets_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a generic normalized ConforNets snapshot without fixture identity."""

    sealed = copy.deepcopy(dict(snapshot))
    if not sealed.get("instance_mappings"):
        entities = sealed.get("entities")
        if not isinstance(entities, list) or not entities or not isinstance(entities[0], Mapping):
            raise DispatchFailure("ConforNets snapshot has no entity mapping context")
        entity = entities[0]
        instances = entity.get("ordered_instance_ids")
        if not isinstance(instances, list) or not instances:
            raise DispatchFailure("ConforNets snapshot has no instance mapping context")
        target_id = str(sealed.get("target_id") or "")
        source_entity_id = str(entity.get("source_entity_id") or "")
        instance_id = str(instances[0])
        sealed["instance_mappings"] = [{
            "source_entity_id": source_entity_id,
            "source_instance_id": instance_id,
            "runtime_target_id": target_id,
            "runtime_entity_id": "1",
            "runtime_instance_id": instance_id,
            "runtime_order": 0,
            "candidate_id": f"{target_id}:0",
            "output_entity_id": "1",
            "output_label_asym_id": instance_id,
            "output_auth_asym_id": instance_id,
            "output_entity_order": 0,
        }]
    sealed.pop("normalized_source_sha256", None)
    sealed["normalized_source_sha256"] = canonical_sha256(
        {key: value for key, value in sealed.items() if key != "normalized_source_sha256"}
    )
    validate_schema("cm_complex_snapshot_v1", sealed)
    return sealed


def _largest_gpu_with_memory(minimum_mb: int) -> int:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DispatchFailure("cannot discover GPU inventory for memory-bound CM attempt") from exc
    candidates: list[tuple[int, int]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            gpu_id, memory_mb = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if memory_mb >= minimum_mb:
            candidates.append((memory_mb, gpu_id))
    if not candidates:
        raise DispatchFailure(f"no installed GPU satisfies the {minimum_mb} MB CM memory requirement")
    return max(candidates)[1]


def _registered(source: ConformationalMappingSource) -> RegisteredArtifact:
    return RegisteredArtifact(
        artifact_id=source.source_id,
        principal_id=source.principal_id,
        storage_root=Path(source.storage_root),
        relative_path=source.relative_path,
        content_sha256=source.content_sha256,
        size_bytes=source.size_bytes,
    )


async def _source(
    session: AsyncSession,
    source_id: str,
    expected_kind: str | frozenset[str],
) -> ConformationalMappingSource:
    source = await session.get(ConformationalMappingSource, source_id)
    expected_kinds = frozenset({expected_kind}) if isinstance(expected_kind, str) else expected_kind
    if source is None or source.source_kind not in expected_kinds or not source.immutable:
        raise DispatchFailure(f"CM global adapter source is unavailable: {expected_kind}/{source_id}")
    if source.principal_id != _PERSONAL_WORKFLOW_PRINCIPAL:
        raise DispatchFailure("CM global adapter source belongs to an unexpected principal")
    return source


def _verify_existing_attempt_authority(
    *,
    existing_job: Job,
    existing_request: ConformationalMappingRequest,
    expected_output_root: Path,
    attempt_id: str,
    scheduler: Mapping[str, Any],
    scheduler_sha256: str,
    adapter_id: str,
    backend: str,
    submission_sha256: str,
    run_group_id: str,
) -> None:
    """Fail closed unless a recovered core attempt matches the incoming authority."""

    expected_request_path = expected_output_root / "cm_request_v1.json"
    expected_coordinate_plan_path = expected_output_root / "cm_coordinate_plan_v1.json"
    expected_job_params = {"cm_request_path": str(expected_request_path)}
    if (
        not isinstance(existing_job.params, dict)
        or existing_job.params != expected_job_params
        or existing_job.output_dir != str(expected_output_root)
        or expected_output_root.is_symlink()
        or not expected_output_root.is_dir()
        or expected_request_path.is_symlink()
        or not expected_request_path.is_file()
        or expected_coordinate_plan_path.is_symlink()
        or not expected_coordinate_plan_path.is_file()
    ):
        raise DispatchFailure("CM existing-attempt executable authority is invalid")

    provenance = existing_job.provenance
    request_json = existing_request.request_json
    coordinate_plan_json = existing_request.coordinate_plan_json
    if not all(isinstance(value, Mapping) for value in (provenance, request_json, coordinate_plan_json)):
        raise DispatchFailure("CM existing-attempt recovery authority is incomplete")
    assert isinstance(provenance, Mapping)
    assert isinstance(request_json, Mapping)
    assert isinstance(coordinate_plan_json, Mapping)
    try:
        persisted_request = dict(request_json)
        persisted_coordinate_plan = dict(coordinate_plan_json)
        executable_request = canonical_json_loads(expected_request_path.read_bytes())
        executable_coordinate_plan = canonical_json_loads(expected_coordinate_plan_path.read_bytes())
        if (
            not isinstance(executable_request, Mapping)
            or not isinstance(executable_coordinate_plan, Mapping)
            or dict(executable_request) != persisted_request
            or dict(executable_coordinate_plan) != persisted_coordinate_plan
        ):
            raise DispatchFailure(
                "CM existing-attempt executable authority conflicts with persisted documents"
            )
        validate_schema("cm_request_v1", persisted_request)
        _validate_recovered_coordinate_plan(persisted_request, persisted_coordinate_plan)
    except DispatchFailure:
        raise
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise DispatchFailure(
            "CM existing-attempt request and coordinate plan are not schema-valid"
        ) from exc
    try:
        persisted_request_sha256 = canonical_sha256(
            {key: value for key, value in request_json.items() if key != "request_sha256"}
        )
        persisted_coordinate_plan_sha256 = canonical_sha256(
            {key: value for key, value in coordinate_plan_json.items() if key != "coordinate_plan_sha256"}
        )
    except (TypeError, ValueError) as exc:
        raise DispatchFailure("CM existing-attempt recovery authority contains invalid canonical provenance") from exc

    created_by = request_json.get("created_by")
    expected_bindings = (
        existing_job.id == attempt_id,
        existing_job.model_id == scheduler.get("model_id") == "conformational_mapping",
        existing_job.mode == scheduler.get("mode") == "map",
        existing_job.batch_id == run_group_id,
        existing_job.lineage_root_job_id == attempt_id,
        existing_job.stage_family == "conformational_mapping",
        existing_job.stage_mode == backend,
        existing_request.request_id == attempt_id,
        existing_request.job_id == attempt_id,
        existing_request.principal_id == _PERSONAL_WORKFLOW_PRINCIPAL,
        existing_request.backend == backend,
        provenance.get("cm_principal_id") == _PERSONAL_WORKFLOW_PRINCIPAL,
        provenance.get("cm_workflow_adapter") == adapter_id,
        provenance.get("cm_scheduler_sha256") == scheduler_sha256,
        provenance.get("cm_submission_sha256") == submission_sha256,
        provenance.get("global_run_group_id") == run_group_id,
        provenance.get("global_attempt_id") == attempt_id,
        provenance.get("cm_request_sha256") == existing_request.request_sha256,
        provenance.get("cm_coordinate_plan_sha256") == existing_request.coordinate_plan_sha256,
        request_json.get("request_id") == existing_request.request_id,
        request_json.get("backend") == backend,
        request_json.get("request_sha256") == existing_request.request_sha256 == persisted_request_sha256,
        isinstance(created_by, Mapping)
        and created_by.get("principal_id") == _PERSONAL_WORKFLOW_PRINCIPAL,
        coordinate_plan_json.get("request_id") == existing_request.request_id,
        coordinate_plan_json.get("backend") == backend,
        coordinate_plan_json.get("request_sha256") == existing_request.request_sha256,
        coordinate_plan_json.get("coordinate_plan_sha256")
        == existing_request.coordinate_plan_sha256
        == persisted_coordinate_plan_sha256,
    )
    if not all(expected_bindings):
        raise DispatchFailure("CM existing-attempt recovery authority conflicts with persisted provenance")


async def _materialize_preallocated_cm_job(
    core_session: AsyncSession,
    *,
    attempt_id: str,
    scheduler: Mapping[str, Any],
    run_group_id: str,
) -> dict[str, Any]:
    """Materialize one global attempt through the canonical CM request contract."""
    try:
        scheduler = copy.deepcopy(dict(scheduler))
    except (TypeError, ValueError) as exc:
        raise DispatchFailure("CM scheduler payload cannot be defensively bound") from exc
    params = scheduler.get("params")
    if not isinstance(params, dict):
        raise DispatchFailure("CM scheduler payload params are incomplete")
    adapter_id = str(params.get("workflow_adapter") or "")
    backend = _GENERATOR_ADAPTERS.get(adapter_id)
    submission = params.get("cm_submission")
    if backend is None or not isinstance(submission, dict):
        raise DispatchFailure("global CM adapter requires a typed generator submission")
    if str(submission.get("backend")) != backend:
        raise DispatchFailure("CM adapter/backend identity disagrees")
    scheduler_sha256 = sha256_text(canonical_json(scheduler))
    submission_sha256 = sha256_text(canonical_json(submission))
    receipt_ids = params.get("cm_source_receipt_ids")
    if not isinstance(receipt_ids, list) or any(
        not isinstance(value, str) or not value for value in receipt_ids
    ):
        raise DispatchFailure("CM global adapter source receipt binding is unavailable")
    try:
        expected_source_ids = _cm_submission_source_ids(submission)
    except ValidationFailure as exc:
        raise DispatchFailure(str(exc)) from exc
    if receipt_ids != expected_source_ids:
        raise DispatchFailure("CM global adapter receipts do not bind the submitted source identities")

    output_root = get_results_dir() / f"conformational_mapping_{attempt_id}"
    existing_job = await core_session.get(Job, attempt_id)
    if existing_job is not None:
        existing_request = (
            await core_session.execute(
                select(ConformationalMappingRequest).where(ConformationalMappingRequest.job_id == attempt_id)
            )
        ).scalar_one_or_none()
        if existing_request is None:
            raise DispatchFailure("preallocated CM job exists without its canonical request")
        _verify_existing_attempt_authority(
            existing_job=existing_job,
            existing_request=existing_request,
            expected_output_root=output_root,
            attempt_id=attempt_id,
            scheduler=scheduler,
            scheduler_sha256=scheduler_sha256,
            adapter_id=adapter_id,
            backend=backend,
            submission_sha256=submission_sha256,
            run_group_id=run_group_id,
        )
        return {
            "scheduler_job_id": attempt_id,
            "request_id": existing_request.request_id,
            "request_sha256": existing_request.request_sha256,
            "core_status": existing_job.status,
            "recovered_existing": True,
        }

    if output_root.exists() and any(output_root.iterdir()):
        raise DispatchFailure("preallocated CM output root already contains unowned files")
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        runtime_policy = _bind_runtime_policy(backend, submission["runtime_policy"])
        analysis_policy = _bind_analysis_policy(submission["analysis_policy"])
    except HTTPException as exc:
        raise DispatchFailure(str(exc.detail)) from exc
    request_params: dict[str, Any] = {
        "backend": backend,
        "ordered_seeds": list(submission["ordered_seeds"]),
        "samples_per_seed": int(submission["samples_per_seed"]),
        "feature_policy": dict(submission["feature_policy"]),
        "runtime_policy": runtime_policy,
        "analysis_policy": analysis_policy,
    }
    analysis_targets: list[dict[str, Any]]
    if backend == "protenix_v2_ensemble":
        snapshot_source = await _source(core_session, str(submission["registered_snapshot_id"]), "complex_snapshot")
        staged = stage_registered_assets(
            [_registered(snapshot_source)],
            principal_id=_PERSONAL_WORKFLOW_PRINCIPAL,
            destination_root=output_root / "registered_snapshot",
        )[snapshot_source.source_id]
        snapshots = json.loads(staged.read_text(encoding="utf-8"))
        if isinstance(snapshots, dict):
            snapshots = [snapshots]
        if not isinstance(snapshots, list) or not snapshots:
            raise DispatchFailure("CM Protenix snapshot bundle is empty")
        for snapshot in snapshots:
            validate_schema("cm_complex_snapshot_v1", snapshot)
        request_params["targets"] = [
            {"target_id": snapshot["target_id"], "target_order": index}
            for index, snapshot in enumerate(snapshots)
        ]
        request_params["protenix_snapshot_id"] = snapshot_source.source_id
        (output_root / "cm_complex_snapshots_v1.json").write_text(
            json.dumps(snapshots, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        analysis_targets = snapshots
    else:
        sequence_source = await _source(core_session, str(submission["registered_sequence_id"]), "protein_sequence")
        try:
            checkpoint_source = await _managed_checkpoint_for_submission(
                core_session, str(submission["registered_checkpoint_id"])
            )
        except HTTPException as exc:
            raise DispatchFailure(str(exc.detail)) from exc
        references = [
            await _source(
                core_session,
                str(source_id),
                frozenset({"structure_upload", "structure_artifact"}),
            )
            for source_id in submission.get("registered_reference_ids", [])
        ]
        config_source = (
            await _source(core_session, str(submission["registered_config_id"]), "confornets_config")
            if submission.get("registered_config_id") else None
        )
        transfer_source = (
            await _source(core_session, str(submission["registered_transfer_id"]), "confornets_state")
            if submission.get("registered_transfer_id") else None
        )
        confor_sources = [sequence_source, checkpoint_source, *references]
        if config_source is not None:
            confor_sources.append(config_source)
        if transfer_source is not None:
            confor_sources.append(transfer_source)
        staged_assets = stage_registered_assets(
            [_registered(source) for source in confor_sources],
            principal_id=_PERSONAL_WORKFLOW_PRINCIPAL,
            destination_root=output_root / "registered",
        )
        metadata = sequence_source.metadata_json if isinstance(sequence_source.metadata_json, dict) else {}
        sequence = str(metadata.get("sequence") or "").upper()
        if not sequence or any(value not in "ACDEFGHIKLMNPQRSTVWYX" for value in sequence):
            raise DispatchFailure("CM ConforNets sequence authority is invalid")
        try:
            settings = _bind_confornets_submission_policy(submission["confornets"])
        except HTTPException as exc:
            raise DispatchFailure(str(exc.detail)) from exc
        settings.update(
            {
                "sequence": sequence,
                "checkpoint": {
                    "path": staged_assets[checkpoint_source.source_id].relative_to(output_root).as_posix(),
                    "sha256": checkpoint_source.content_sha256,
                },
                "config": None if config_source is None else {
                    "path": staged_assets[config_source.source_id].relative_to(output_root).as_posix(),
                    "sha256": config_source.content_sha256,
                },
                "references": [
                    {
                        "reference_id": source.source_id,
                        "staged_path": staged_assets[source.source_id].relative_to(output_root).as_posix(),
                        "content_sha256": source.content_sha256,
                        "state": str(source.metadata_json.get("state") or "reference"),
                        "source": "registered_artifact",
                    }
                    for source in references
                ],
                "transfer_source": None if transfer_source is None else {
                    "kind": str(transfer_source.metadata_json.get("kind") or "confornet_state"),
                    "staged_path": staged_assets[transfer_source.source_id].relative_to(output_root).as_posix(),
                    "content_sha256": transfer_source.content_sha256,
                    "source_test_cases": str(transfer_source.metadata_json.get("source_test_cases") or ""),
                },
                "backend_identity": _server_confornets_identity(),
            }
        )
        request_params["confornets"] = settings
        target_id = str(metadata.get("target_id") or sequence_source.source_id)
        source_entity_id = str(
            metadata.get("source_entity_id") or metadata.get("entity_id") or target_id
        )
        chain_id = str(settings["chain_id"])
        request_params["targets"] = [
            {"target_id": target_id, "target_order": 0, "sequence": sequence, "molecule_type": "protein", "chain_count": 1}
        ]
        snapshot = _seal_confornets_snapshot({
            "schema_name": "cm_complex_snapshot",
            "schema_version": 1,
            "target_id": target_id,
            "target_order": 0,
            "original_source_path": f"registered/{sequence_source.source_id}",
            "original_source_sha256": sequence_source.content_sha256,
            "entities": [{"entity_type": "protein", "source_entity_id": source_entity_id, "count": 1, "ordered_instance_ids": [chain_id], "sequence": sequence}],
            "bonds": [],
            "instance_mappings": [{"source_entity_id": source_entity_id, "source_instance_id": chain_id, "runtime_target_id": target_id, "runtime_entity_id": "1", "runtime_instance_id": chain_id, "runtime_order": 0, "candidate_id": f"{target_id}:0", "output_entity_id": "1", "output_label_asym_id": chain_id, "output_auth_asym_id": chain_id, "output_entity_order": 0}],
            "admission": {"token_count": len(sequence), "atom_count": 0, "token_limit": 10000, "conversion_omissions": []},
            "unsupported_fields": [],
        })
        analysis_targets = [snapshot]
        (output_root / "cm_complex_snapshots_v1.json").write_text(json.dumps([snapshot], sort_keys=True, separators=(",", ":")), encoding="utf-8")

    validate_request_params(request_params)
    materialized = materialize_trusted_internal_request(
        request_params,
        output_dir=output_root,
        request_id=attempt_id,
        principal_id=_PERSONAL_WORKFLOW_PRINCIPAL,
    )
    request_payload = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    coordinate_plan = json.loads(materialized.coordinate_plan_path.read_text(encoding="utf-8"))
    (output_root / "cm_runtime_registry_v1.json").write_text(
        json.dumps(_runtime_registry(backend), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    token, token_digest = issue_request_capability()
    admission = _cm_job_admission(backend, {"targets": analysis_targets})
    if backend == "confornets" and int(submission.get("confornets", {}).get("confornet_count", 0)) >= 5:
        admission["vram_estimate_mb"] = 24000
        admission["pinned_gpu"] = _largest_gpu_with_memory(32000)
    job = Job(
        id=attempt_id,
        name=str(submission["name"]),
        status="queued",
        model_id="conformational_mapping",
        mode="map",
        params=materialized.launch_params,
        output_dir=str(output_root),
        queue_status="queued",
        **admission,
        lineage_root_job_id=attempt_id,
        batch_id=run_group_id,
        stage_family="conformational_mapping",
        stage_mode=backend,
        provenance={
            "cm_request_sha256": request_payload["request_sha256"],
            "cm_coordinate_plan_sha256": coordinate_plan["coordinate_plan_sha256"],
            "cm_principal_id": _PERSONAL_WORKFLOW_PRINCIPAL,
            "cm_workflow_adapter": adapter_id,
            "cm_scheduler_sha256": scheduler_sha256,
            "cm_submission_sha256": submission_sha256,
            "global_run_group_id": run_group_id,
            "global_attempt_id": attempt_id,
        },
    )
    record = await register_prepared_request(
        core_session,
        job=job,
        principal_id=_PERSONAL_WORKFLOW_PRINCIPAL,
        request=request_payload,
        coordinate_plan=coordinate_plan,
        resume_key="0" * 64,
        capability_sha256=token_digest,
    )
    if record.status == "prepared":
        await transition_request(core_session, record, status="queued", progress={"phase": "queued"})
    await core_session.commit()
    return {
        "store_id": "core",
        "entity_kind": "conformational_mapping_request",
        "entity_id": record.request_id,
        "scheduler_job_id": attempt_id,
        "generation": 1,
        "content_digest": sha256_text(canonical_json(scheduler)),
        "request_sha256": request_payload["request_sha256"],
        "coordinate_plan_sha256": coordinate_plan["coordinate_plan_sha256"],
        "expected_cardinality": coordinate_plan["expected_cardinality"],
    }


async def materialize_preallocated_cm_job(
    core_session: AsyncSession,
    *,
    attempt_id: str,
    scheduler: Mapping[str, Any],
    run_group_id: str,
) -> dict[str, Any]:
    """Run the canonical CM materializer and remove an unowned failed root.

    The private materializer applies the server-owned _bind_runtime_policy,
    _bind_confornets_submission_policy, _bind_analysis_policy, and
    _managed_checkpoint_for_submission gates before persistence.
    """

    output_root = get_results_dir() / f"conformational_mapping_{attempt_id}"
    existed = output_root.exists()
    preexisting_empty_directory = existed and output_root.is_dir() and not any(output_root.iterdir())
    try:
        return await _materialize_preallocated_cm_job(
            core_session,
            attempt_id=attempt_id,
            scheduler=scheduler,
            run_group_id=run_group_id,
        )
    except Exception:
        await core_session.rollback()
        if (not existed or preexisting_empty_directory) and output_root.exists():
            shutil.rmtree(output_root, ignore_errors=True)
        raise


__all__ = ["EXECUTABLE_CM_ADAPTERS", "_seal_confornets_snapshot", "materialize_preallocated_cm_job"]
