"""Typed global-workspace adapter for preallocated CM scheduler attempts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from database import ConformationalMappingSource, Job
from paths import get_results_dir
from routers.conformational_mapping import (
    _PERSONAL_WORKFLOW_PRINCIPAL,
    _cm_job_admission,
    _runtime_registry,
    _server_confornets_identity,
)
from services.conformational_mapping.contracts import validate_schema
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
from experiment_services import DispatchFailure, canonical_json, sha256_text

_GENERATOR_ADAPTERS = {
    "bms.cm.protenix_v2.adapter.v1": "protenix_v2_ensemble",
    "bms.cm.confornets.adapter.v1": "confornets",
}


def _registered(source: ConformationalMappingSource) -> RegisteredArtifact:
    return RegisteredArtifact(
        artifact_id=source.source_id,
        principal_id=source.principal_id,
        storage_root=Path(source.storage_root),
        relative_path=source.relative_path,
        content_sha256=source.content_sha256,
        size_bytes=source.size_bytes,
    )


async def _source(session: AsyncSession, source_id: str, expected_kind: str) -> ConformationalMappingSource:
    source = await session.get(ConformationalMappingSource, source_id)
    if source is None or source.source_kind != expected_kind or not source.immutable:
        raise DispatchFailure(f"CM global adapter source is unavailable: {expected_kind}/{source_id}")
    if source.principal_id != _PERSONAL_WORKFLOW_PRINCIPAL:
        raise DispatchFailure("CM global adapter source belongs to an unexpected principal")
    return source


async def materialize_preallocated_cm_job(
    core_session: AsyncSession,
    *,
    attempt_id: str,
    scheduler: Mapping[str, Any],
    run_group_id: str,
) -> dict[str, Any]:
    """Materialize one global attempt through the canonical CM request contract."""
    params = scheduler.get("params")
    if not isinstance(params, dict):
        raise DispatchFailure("CM scheduler payload params are incomplete")
    adapter_id = params.get("workflow_adapter")
    backend = _GENERATOR_ADAPTERS.get(str(adapter_id))
    submission = params.get("cm_submission")
    if backend is None or not isinstance(submission, dict):
        raise DispatchFailure("global CM adapter requires a typed generator submission")
    if str(submission.get("backend")) != backend:
        raise DispatchFailure("CM adapter/backend identity disagrees")

    output_root = get_results_dir() / f"conformational_mapping_{attempt_id}"
    if output_root.exists() and any(output_root.iterdir()):
        raise DispatchFailure("preallocated CM output root already contains unowned files")
    output_root.mkdir(parents=True, exist_ok=True)

    request_params: dict[str, Any] = {
        "backend": backend,
        "ordered_seeds": list(submission["ordered_seeds"]),
        "samples_per_seed": int(submission["samples_per_seed"]),
        "feature_policy": dict(submission["feature_policy"]),
        "runtime_policy": dict(submission["runtime_policy"]),
        "analysis_policy": dict(submission["analysis_policy"]),
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
        (output_root / "cm_complex_snapshots_v1.json").write_bytes(staged.read_bytes())
        analysis_targets = snapshots
    else:
        sequence_source = await _source(core_session, str(submission["registered_sequence_id"]), "protein_sequence")
        checkpoint_source = await _source(core_session, str(submission["registered_checkpoint_id"]), "confornets_checkpoint")
        staged_assets = stage_registered_assets(
            [_registered(sequence_source), _registered(checkpoint_source)],
            principal_id=_PERSONAL_WORKFLOW_PRINCIPAL,
            destination_root=output_root / "registered",
        )
        metadata = sequence_source.metadata_json if isinstance(sequence_source.metadata_json, dict) else {}
        sequence = str(metadata.get("sequence") or "").upper()
        if len(sequence) != 540:
            raise DispatchFailure(f"CM ConforNets DRT4 sequence length is not 540: {len(sequence)}")
        settings = dict(submission["confornets"])
        settings.update(
            {
                "sequence": sequence,
                "chain_id": "A",
                "checkpoint": {
                    "path": staged_assets[checkpoint_source.source_id].relative_to(output_root).as_posix(),
                    "sha256": checkpoint_source.content_sha256,
                },
                "config": None,
                "references": [],
                "transfer_source": None,
                "backend_identity": _server_confornets_identity(),
            }
        )
        request_params["confornets"] = settings
        target_id = str(metadata.get("target_id") or "DRT4_WP_031606642_1")
        request_params["targets"] = [
            {"target_id": target_id, "target_order": 0, "sequence": sequence, "molecule_type": "protein", "chain_count": 1}
        ]
        analysis_targets = [{"target_id": target_id, "sequence": sequence}]
        snapshot = {
            "schema_name": "cm_complex_snapshot",
            "schema_version": 1,
            "target_id": target_id,
            "target_order": 0,
            "original_source_path": f"registered/{sequence_source.source_id}",
            "original_source_sha256": sequence_source.content_sha256,
            "normalized_source_sha256": sequence_source.content_sha256,
            "entities": [{"entity_type": "protein", "source_entity_id": "WP_031606642.1", "count": 1, "ordered_instance_ids": ["DRT4_A"], "sequence": sequence}],
            "bonds": [],
            "instance_mappings": [{"source_entity_id": "WP_031606642.1", "source_instance_id": "DRT4_A", "runtime_target_id": target_id, "runtime_entity_id": "1", "runtime_instance_id": "DRT4_A", "runtime_order": 0, "candidate_id": target_id, "output_entity_id": "1", "output_label_asym_id": "A", "output_auth_asym_id": "A", "output_entity_order": 0}],
            "admission": {"token_count": len(sequence), "atom_count": 0, "token_limit": 10000, "conversion_omissions": []},
            "unsupported_fields": [],
        }
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
    job = Job(
        id=attempt_id,
        name=str(submission["name"]),
        status="queued",
        model_id="conformational_mapping",
        mode="map",
        params=materialized.launch_params,
        output_dir=str(output_root),
        queue_status="queued",
        **_cm_job_admission(backend, {"targets": analysis_targets}),
        lineage_root_job_id=attempt_id,
        batch_id=run_group_id,
        stage_family="conformational_mapping",
        stage_mode=backend,
        provenance={
            "cm_request_sha256": request_payload["request_sha256"],
            "cm_coordinate_plan_sha256": coordinate_plan["coordinate_plan_sha256"],
            "cm_principal_id": _PERSONAL_WORKFLOW_PRINCIPAL,
            "cm_submission_sha256": sha256_text(canonical_json(submission)),
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


__all__ = ["materialize_preallocated_cm_job"]
