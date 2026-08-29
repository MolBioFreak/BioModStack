from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import (
    Base,
    ConformationalMappingLandscapeRow,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    Job,
)
from services.conformational_mapping.contracts import (
    canonical_json_bytes,
    canonical_sha256,
    candidate_id,
    request_sha256,
    validate_schema,
)
from services.conformational_mapping.frustrampnn_adapter import bind_cm_candidate_snapshot_bytes
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    ingest_result_bundle,
    paged_landscape,
    register_prepared_request,
)
from services.frustrampnn.persistence import _FRUSTRA_LANDSCAPE_PARQUET_SCHEMA
from services.frustrampnn.settings import default_settings, requested_settings_sha256
from services.scientific_artifacts import publish_table_rows
from services.conformational_mapping.state_landscape_analysis import (
    derive_state_landscape_analysis_for_request,
    validate_state_landscape_analysis_binding,
)
from services.result_ingester import _persist_cm_bundle_atomically, ingest_job_results


FIXTURE = Path(__file__).parent / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json"


def _minimal_mmcif() -> bytes:
    return b"""data_candidate
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
ATOM 1 C CA ALA A 1 A 1
ATOM 2 C CA ALA B 1 B 1
#
"""


async def _session(tmp_path: Path) -> tuple[AsyncSession, AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingester-state.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)(), engine


def _no_authority_bundle(root: Path) -> tuple[dict, dict]:
    fixture = copy.deepcopy(json.loads(FIXTURE.read_text()))
    request = fixture["cm_request_v1"]
    ensemble = fixture["cm_ensemble_v1"]
    native = fixture["cm_native_artifacts_v1"]
    for index, item in enumerate(native["files"], start=1):
        path = root / item["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = bytes([index]) * index
        path.write_bytes(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        item["bytes"] = len(payload)
    ensemble["runtime_attestation_sha256"] = next(
        item["sha256"]
        for item in native["files"]
        if item.get("semantic_role") == "runtime_attestation"
    )
    authoritative_path = ensemble["candidates"][0]["authoritative_structure_path"]
    ensemble["candidates"][0]["authoritative_structure_sha256"] = next(
        item["sha256"] for item in native["files"] if item["relative_path"] == authoritative_path
    )
    ensemble["native_manifest_sha256"] = canonical_sha256(native)
    fixture["cm_analysis_v1"]["source_ensemble_sha256"] = canonical_sha256(ensemble)
    bundle = {
        "cm_ensemble_v1": ensemble,
        "cm_native_artifacts_v1": native,
        "cm_structure_maps": [fixture["cm_structure_map_v1"]],
        "cm_frustration_landscapes": [fixture["cm_frustration_landscape_v1"]],
        "cm_analysis_v1": fixture["cm_analysis_v1"],
    }
    return request, bundle


def _coherent_state_bundle(root: Path) -> tuple[dict, dict, dict]:
    request, bundle = _no_authority_bundle(root)
    request["state_landscape_comparison"] = {
        "mode": "pairwise", "target_id": "target-a", "scope": "all_within_target",
    }
    request["request_sha256"] = request_sha256(request)
    ensemble = bundle["cm_ensemble_v1"]
    ensemble["request_sha256"] = request["request_sha256"]
    second_candidate = copy.deepcopy(ensemble["candidates"][0])
    second_candidate["backend_coordinates"] = {
        "backend": "protenix_v2_ensemble", "target_id": "target-a",
        "ordered_seed": 202, "sample_index": 0,
    }
    second_candidate["candidate_id"] = candidate_id(second_candidate["backend_coordinates"])
    second_candidate["authoritative_structure_path"] = "targets/b/structure.cif"
    second_candidate["sidecar_paths"] = [
        path.replace("targets/a/", "targets/b/") for path in second_candidate["sidecar_paths"]
    ]
    native = bundle["cm_native_artifacts_v1"]
    copied_native = []
    for item in native["files"]:
        if item["candidate_id"] != ensemble["candidates"][0]["candidate_id"]:
            continue
        copied = copy.deepcopy(item)
        copied["relative_path"] = copied["relative_path"].replace("targets/a/", "targets/b/")
        copied["candidate_id"] = second_candidate["candidate_id"]
        copied["backend_coordinates"] = second_candidate["backend_coordinates"]
        copied["related_paths"] = [path.replace("targets/a/", "targets/b/") for path in copied["related_paths"]]
        source = root / item["relative_path"]
        destination = root / copied["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        copied_native.append(copied)
    native["files"].extend(copied_native)
    second_candidate["authoritative_structure_sha256"] = next(
        item["sha256"] for item in copied_native if item["relative_path"] == "targets/b/structure.cif"
    )
    ensemble["native_manifest_sha256"] = canonical_sha256(native)
    ensemble["candidates"].append(second_candidate)
    ensemble["expected_coordinates"].append(second_candidate["backend_coordinates"])
    ensemble["expected_cardinality"] = 2
    second_map = copy.deepcopy(bundle["cm_structure_maps"][0])
    second_map["candidate_id"] = second_candidate["candidate_id"]
    second_landscape = copy.deepcopy(bundle["cm_frustration_landscapes"][0])
    second_landscape["candidate_id"] = second_candidate["candidate_id"]
    second_landscape["raw_csv_sha256"] = "d" * 64
    bundle["cm_structure_maps"].append(second_map)
    bundle["cm_frustration_landscapes"].append(second_landscape)
    bundle["cm_analysis_v1"]["source_ensemble_sha256"] = canonical_sha256(ensemble)
    artifact = derive_state_landscape_analysis_for_request(
        request,
        ensemble,
        bundle["cm_frustration_landscapes"],
        bundle["cm_structure_maps"],
    )
    assert artifact is not None
    state_path = root / "derived" / "cm_state_landscape_analysis_v1.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_bytes = canonical_json_bytes(artifact)
    state_path.write_bytes(state_bytes)
    bundle["cm_derived_files"] = [{
        "relative_path": "derived/cm_state_landscape_analysis_v1.json",
        "sha256": hashlib.sha256(state_bytes).hexdigest(),
        "bytes": len(state_bytes),
        "semantic_role": "state_landscape_analysis",
        "candidate_id": None,
    }]
    return request, bundle, artifact


async def _register(
    session: AsyncSession, request: dict, bundle: dict,
) -> ConformationalMappingRequest:
    return await register_prepared_request(
        session,
        job=Job(
            id=request["request_id"], name="legacy-state", model_id="conformational_mapping",
            mode="map", status="queued", params={}, created_at=datetime.utcnow(),
        ),
        principal_id="alice",
        request=request,
        coordinate_plan={
            "coordinate_plan_sha256": "b" * 64,
            "expected_cardinality": bundle["cm_ensemble_v1"]["expected_cardinality"],
            "coordinates": bundle["cm_ensemble_v1"]["expected_coordinates"],
        },
        resume_key="0" * 64,
        capability_sha256="c" * 64,
    )


def _global_bundle(root: Path) -> tuple[dict, dict, dict, dict]:
    request, bundle = _no_authority_bundle(root)
    settings = default_settings()
    request["frustrampnn_settings"] = settings.model_dump(mode="json", exclude_none=False)
    request["frustrampnn_requiredness"] = "required"
    request["request_sha256"] = request_sha256(request)
    ensemble = bundle["cm_ensemble_v1"]
    ensemble["request_sha256"] = request["request_sha256"]
    bundle["cm_analysis_v1"]["source_ensemble_sha256"] = canonical_sha256(ensemble)
    fixture = copy.deepcopy(json.loads(FIXTURE.read_text()))
    snapshot = fixture["cm_complex_snapshot_v1"]
    candidate = ensemble["candidates"][0]
    candidate_id_value = candidate["candidate_id"]
    source_path = root / candidate["authoritative_structure_path"]
    source_bytes = _minimal_mmcif()
    source_path.write_bytes(source_bytes)
    native = bundle["cm_native_artifacts_v1"]
    source_record = next(
        item for item in native["files"]
        if item["relative_path"] == candidate["authoritative_structure_path"]
    )
    source_record["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    source_record["bytes"] = len(source_bytes)
    candidate["authoritative_structure_sha256"] = source_record["sha256"]
    ensemble["native_manifest_sha256"] = canonical_sha256(native)
    bundle["cm_analysis_v1"]["source_ensemble_sha256"] = canonical_sha256(ensemble)
    bound_snapshot = bind_cm_candidate_snapshot_bytes(
        snapshot,
        candidate_id=candidate_id_value,
        source_bytes=source_bytes,
        source_suffix=source_path.suffix,
        source_relative_path=candidate["authoritative_structure_path"],
    )
    invocation_id = f"frustrampnn:{request['request_id']}:{candidate_id_value}"
    settings_sha256 = requested_settings_sha256(settings)
    manifest_sha256 = "7" * 64
    reference = {
        "candidate_id": candidate_id_value,
        "invocation_id": invocation_id,
        "source_sha256": "1" * 64,
        "cm_complex_snapshot_sha256": canonical_sha256(bound_snapshot),
        "requested_settings_sha256": settings_sha256,
        "effective_settings_sha256": "6" * 64,
        "bundle_relative_path": f"frustrampnn/results/{candidate_id_value}",
        "result_manifest_sha256": manifest_sha256,
        "landscape_sha256": "8" * 64,
        "structure_map_sha256": "9" * 64,
    }
    bundle.pop("cm_structure_maps")
    bundle.pop("cm_frustration_landscapes")
    legacy_map = copy.deepcopy(fixture["cm_structure_map_v1"])
    snapshot_mapping_by_instance = {
        item["source_instance_id"]: item
        for item in snapshot["instance_mappings"]
    }
    global_map = {
        "schema_name": "frustrampnn_structure_map",
        "schema_version": 1,
        "target_id": legacy_map["target_id"],
        "parent_job_id": request["request_id"],
        "candidate_id": legacy_map["candidate_id"],
        "source_format": legacy_map["source_format"],
        "source_sha256": legacy_map["source_sha256"],
        "source_bytes": legacy_map["source_bytes"],
        "normalized_pdb_sha256": legacy_map["normalized_pdb_sha256"],
        "selected_source_model": legacy_map["selected_source_model"],
        "altloc_policy": legacy_map["altloc_policy"],
        "rows": [
            {
                "entity_instance_id": row["entity_instance_id"],
                "source_entity_id": snapshot_mapping_by_instance[
                    row["entity_instance_id"]
                ]["output_entity_id"],
                "label_asym_id": row["label_asym_id"],
                "auth_asym_id": row["auth_asym_id"],
                "label_seq_id": row["label_seq_id"],
                "auth_seq_id": row["auth_seq_id"],
                "insertion_code": row["insertion_code"],
                "residue_name": row["residue_name"],
                "sequence_index": row["sequence_index"],
                "pdb_chain_id": row["pdb_chain_id"],
                "pdb_residue_id": row["pdb_residue_id"],
                "pdb_insertion_code": row["pdb_insertion_code"],
                "backbone_atoms": {
                    atom: f"cif:{source_id}" if source_id is not None else None
                    for atom, source_id in row["backbone_atoms"].items()
                },
                "selected_altloc": row["selected_altloc"],
                "selected_model": row["source_model"],
                "status": row["status"],
                "reason": row["reason"],
            }
            for row in legacy_map["rows"]
        ],
    }
    global_landscape = copy.deepcopy(fixture["cm_frustration_landscape_v1"])
    global_landscape["schema_name"] = "frustrampnn_landscape"
    global_landscape["schema_version"] = 2
    bundle.update({
        "cm_complex_snapshots": [snapshot],
        "frustrampnn_structure_maps": [global_map],
        "frustrampnn_landscapes": [global_landscape],
        "cm_frustrampnn_result_references": {
            "schema_name": "cm_frustrampnn_result_references",
            "schema_version": 1,
            "parent_job_id": request["request_id"],
            "parent_workflow_id": "conformational_mapping",
            "expected_cardinality": 1,
            "results": [reference],
        },
    })
    result_values = {
        "parent_job_id": request["request_id"],
        "invocation_id": invocation_id,
        "parent_workflow_id": "conformational_mapping",
        "candidate_id": candidate_id_value,
        "requiredness": "required",
        "request_sha256": "5" * 64,
        "source_artifact_sha256": reference["source_sha256"],
        "manifest_sha256": manifest_sha256,
        "manifest_json": {},
        "summary_sha256": "4" * 64,
        "summary_json": {},
        "runtime_identity_json": {},
        "assigned_gpu_json": {},
        "terminal_result_json": {},
        "settings_sha256": settings_sha256,
        "effective_settings_sha256": reference["effective_settings_sha256"],
    }
    return request, bundle, reference, result_values


@pytest.mark.asyncio
@pytest.mark.parametrize("authority_field", ["requested_settings_sha256", "cm_complex_snapshot_sha256"])
async def test_cm_global_ingest_rejects_cross_bound_settings_or_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority_field: str,
) -> None:
    from services.conformational_mapping import persistence as cm_persistence

    root = tmp_path / "global-cross-binding"
    root.mkdir()
    request, bundle, reference, result_values = _global_bundle(root)
    reference[authority_field] = "0" * 64
    monkeypatch.setattr(cm_persistence, "validate_frustrampnn_schema", lambda *_args: None)
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        session.add(FrustraMPNNResult(**result_values))
        await session.flush()
        with pytest.raises(ConformationalPersistenceError, match="crosses CM settings or snapshot"):
            await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_mode", ["leaf", "intermediate"])
async def test_cm_global_ingest_rejects_candidate_path_replacement_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement_mode: str,
) -> None:
    from services.conformational_mapping import persistence as cm_persistence

    root = tmp_path / f"global-race-{replacement_mode}"
    root.mkdir()
    request, bundle, reference, result_values = _global_bundle(root)
    candidate = bundle["cm_ensemble_v1"]["candidates"][0]
    source_path = root / candidate["authoritative_structure_path"]
    malicious_bytes = _minimal_mmcif() + b"# substituted generation\n"
    malicious_snapshot = bind_cm_candidate_snapshot_bytes(
        bundle["cm_complex_snapshots"][0],
        candidate_id=candidate["candidate_id"],
        source_bytes=malicious_bytes,
        source_suffix=source_path.suffix,
        source_relative_path=candidate["authoritative_structure_path"],
    )
    reference["cm_complex_snapshot_sha256"] = canonical_sha256(malicious_snapshot)
    attack_root = root / "attacker"
    attack_root.mkdir()
    attack_file = attack_root / source_path.name
    attack_file.write_bytes(malicious_bytes)
    original_read_bytes = Path.read_bytes
    original_bind = cm_persistence.bind_cm_candidate_snapshot_bytes
    replaced = False

    def replace_candidate_path() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        if replacement_mode == "leaf":
            source_path.unlink()
            source_path.symlink_to(attack_file)
        else:
            original_parent = source_path.parent.with_name(source_path.parent.name + "-original")
            source_path.parent.rename(original_parent)
            source_path.parent.symlink_to(attack_root, target_is_directory=True)

    def replace_before_reopen(path: Path) -> bytes:
        if path == source_path:
            replace_candidate_path()
        return original_read_bytes(path)

    def replace_before_binding(
        snapshot: Mapping[str, Any], *, candidate_id: str, source_bytes: bytes,
        source_suffix: str, source_relative_path: str | None = None,
    ) -> dict[str, Any]:
        replace_candidate_path()
        return original_bind(
            snapshot,
            candidate_id=candidate_id,
            source_bytes=source_bytes,
            source_suffix=source_suffix,
            source_relative_path=source_relative_path,
        )

    monkeypatch.setattr(Path, "read_bytes", replace_before_reopen)
    monkeypatch.setattr(
        cm_persistence, "bind_cm_candidate_snapshot_bytes", replace_before_binding
    )
    monkeypatch.setattr(cm_persistence, "validate_frustrampnn_schema", lambda *_args: None)
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        session.add(FrustraMPNNResult(**result_values))
        await session.flush()
        with pytest.raises(
            ConformationalPersistenceError,
            match="crosses CM settings or snapshot|path contains a symlink",
        ):
            await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        assert replaced is True
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_global_ingest_rejects_duplicate_candidate_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import persistence as cm_persistence

    root = tmp_path / "global-duplicate"
    root.mkdir()
    request, bundle, _reference, result_values = _global_bundle(root)
    monkeypatch.setattr(cm_persistence, "validate_frustrampnn_schema", lambda *_args: None)
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        session.add(FrustraMPNNResult(**result_values))
        duplicate = dict(result_values)
        duplicate["invocation_id"] = str(result_values["invocation_id"]) + "-duplicate"
        duplicate["manifest_sha256"] = "a" * 64
        session.add(FrustraMPNNResult(**duplicate))
        await session.flush()
        with pytest.raises(ConformationalPersistenceError, match="required canonical FrustraMPNN results"):
            await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_global_landscape_query_uses_only_referenced_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import persistence as cm_persistence

    root = tmp_path / "global-query"
    root.mkdir()
    request, bundle, reference, result_values = _global_bundle(root)
    assert reference["cm_complex_snapshot_sha256"] != canonical_sha256(
        bundle["cm_complex_snapshots"][0]
    )
    monkeypatch.setattr(cm_persistence, "validate_frustrampnn_schema", lambda *_args: None)
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        session.add(FrustraMPNNResult(**result_values))
        await session.flush()
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        assert await session.scalar(select(ConformationalMappingLandscapeRow)) is None

        extra_result = dict(result_values)
        extra_result["invocation_id"] = str(result_values["invocation_id"]) + "-unreferenced"
        extra_result["candidate_id"] = "unreferenced-candidate"
        extra_result["manifest_sha256"] = "a" * 64
        session.add(FrustraMPNNResult(**extra_result))
        await session.flush()
        for invocation_id, score in (
            (reference["invocation_id"], 1.0),
            (extra_result["invocation_id"], 99.0),
        ):
            await publish_table_rows(
                session,
                owner_kind="frustrampnn_result",
                owner_id=f"{request['request_id']}:{invocation_id}",
                role="landscape",
                schema_id="bms.frustrampnn-landscape.v1",
                source_sha256=hashlib.sha256(str(score).encode()).hexdigest(),
                rows=[{
                    "id": f"row-{score}",
                    "target_id": "target-a",
                    "entity_instance_id": "protein-1",
                    "auth_asym_id": "A",
                    "auth_seq_id": "1",
                    "insertion_code": "",
                    "sequence_index": 1,
                    "wt": "A",
                    "mutation_aa": "V",
                    "score": score,
                    "score_class": "neutral",
                    "scoreable": True,
                    "status": "ok",
                    "reason": None,
                    "row_json": json.dumps({"score": score}),
                    "provenance_json": "{}",
                }],
                schema=_FRUSTRA_LANDSCAPE_PARQUET_SCHEMA,
            )
        await session.flush()

        rows = await paged_landscape(session, request["request_id"])
        assert [(row.candidate_id, row.score) for row in rows] == [
            (result_values["candidate_id"], 1.0),
        ]
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_global_ingest_persists_candidate_structure_map_presentations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import persistence as cm_persistence

    root = tmp_path / "global-map-presentations"
    root.mkdir()
    request, bundle, _reference, result_values = _global_bundle(root)
    projected = cm_persistence.project_cm_structure_map(
        bundle["frustrampnn_structure_maps"][0],
        bundle["cm_complex_snapshots"][0],
    )

    monkeypatch.setattr(cm_persistence, "validate_frustrampnn_schema", lambda *_args: None)
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        session.add(FrustraMPNNResult(**result_values))
        await session.flush()
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        rows = list((await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == request["request_id"],
                ConformationalMappingRecord.record_type == "structure_map",
            )
        )).scalars().all())
        assert [(row.record_key, row.payload_json) for row in rows] == [
            (result_values["candidate_id"], projected),
        ]
        assert await session.scalar(select(ConformationalMappingLandscapeRow)) is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_atomic_persistence_rolls_back_global_rows_when_cm_ingest_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import result_ingester

    session, engine = await _session(tmp_path)
    job_id = "atomic-cm-job"
    result_values = {
        "parent_job_id": job_id,
        "invocation_id": "frustrampnn:atomic-cm-job:candidate-a",
        "parent_workflow_id": "conformational_mapping",
        "candidate_id": "candidate-a",
        "requiredness": "required",
        "request_sha256": "5" * 64,
        "source_artifact_sha256": "1" * 64,
        "manifest_sha256": "7" * 64,
        "manifest_json": {},
        "summary_sha256": "4" * 64,
        "summary_json": {},
        "runtime_identity_json": {},
        "assigned_gpu_json": {},
        "terminal_result_json": {},
    }

    async def fail_after_global_row(
        active_session: AsyncSession, _cm_request: object, **_kwargs: object,
    ) -> None:
        active_session.add(FrustraMPNNResult(**result_values))
        await active_session.flush()
        raise ConformationalPersistenceError("forced CM persistence failure")

    try:
        session.add(Job(
            id=job_id, name="atomic", model_id="conformational_mapping",
            mode="map", status="running", params={}, created_at=datetime.utcnow(),
        ))
        await session.commit()
        monkeypatch.setattr(result_ingester, "ingest_cm_result_bundle", fail_after_global_row)
        with pytest.raises(ConformationalPersistenceError, match="forced CM persistence failure"):
            await _persist_cm_bundle_atomically(
                session, object(), bundle={}, result_root=tmp_path, commit=True,
            )
        assert await session.scalar(select(FrustraMPNNResult)) is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("state_value", [None, []])
async def test_cm_ingest_bundle_treats_missing_or_empty_state_analysis_as_legacy_absence(
    tmp_path: Path, state_value: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import persistence as cm_persistence

    root = tmp_path / "result"
    root.mkdir()
    request, bundle = _no_authority_bundle(root)
    capture_flags: list[bool] = []
    original_measurement = cm_persistence._stable_file_measurement

    def record_capture(path: Path, *, capture_bytes: bool = False) -> tuple[str, int, bytes | None]:
        capture_flags.append(capture_bytes)
        return original_measurement(path, capture_bytes=capture_bytes)

    monkeypatch.setattr(cm_persistence, "_stable_file_measurement", record_capture)
    if state_value is not None:
        bundle["cm_state_landscape_analyses"] = state_value
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        assert await session.scalar(select(ConformationalMappingLandscapeRow)) is None
        assert capture_flags
        assert not any(capture_flags)
        assert await session.scalar(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.record_type == "state_landscape_analysis"
            )
        ) is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_ingest_bundle_rejects_state_artifact_without_comparison_authority(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle, artifact = _coherent_state_bundle(root)
    request.pop("state_landscape_comparison")
    request["request_sha256"] = request_sha256(request)
    bundle["cm_ensemble_v1"]["request_sha256"] = request["request_sha256"]
    bundle["cm_analysis_v1"]["source_ensemble_sha256"] = canonical_sha256(bundle["cm_ensemble_v1"])
    bundle["cm_state_landscape_analyses"] = [artifact]
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        with pytest.raises(
            ConformationalPersistenceError,
            match="state landscape analysis is not authorized without comparison authority",
        ):
            await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_ingest_bundle_is_the_accepted_state_artifact_persistence_path(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle, artifact = _coherent_state_bundle(root)
    validate_schema("cm_request_v1", request)
    validate_state_landscape_analysis_binding(
        request,
        bundle["cm_ensemble_v1"],
        bundle["cm_frustration_landscapes"],
        bundle["cm_structure_maps"],
        artifact,
    )
    bundle["cm_state_landscape_analyses"] = [artifact]
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        stored = await session.scalar(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.record_type == "state_landscape_analysis"
            )
        )
        assert stored is not None and stored.payload_json == artifact
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_ingest_bundle_failing_state_binding_persists_no_partial_records_after_caller_commit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle, artifact = _coherent_state_bundle(root)
    forged = copy.deepcopy(artifact)
    forged["analysis_id"] = "cm_state_landscape_analysis_" + "0" * 32
    validate_schema("cm_state_landscape_analysis_v1", forged)
    bundle["cm_state_landscape_analyses"] = [forged]
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        with pytest.raises(ConformationalPersistenceError, match="binding validation failed"):
            await ingest_result_bundle(session, record, bundle=bundle, result_root=root)

        await session.commit()
        assert list((await session.execute(
            select(ConformationalMappingRecord).where(
                ConformationalMappingRecord.request_id == record.request_id
            )
        )).scalars()) == []
        assert list((await session.execute(
            select(ConformationalMappingLandscapeRow).where(
                ConformationalMappingLandscapeRow.request_id == record.request_id
            )
        )).scalars()) == []
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_result_ingester_accepts_legacy_derived_index_that_omits_state_analysis(tmp_path: Path) -> None:
    output = tmp_path / "job-output"
    result_root = output / "canonical_protenix"
    result_root.mkdir(parents=True)
    request, bundle = _no_authority_bundle(result_root)
    (result_root / "cm_ensemble_v1.json").write_text(json.dumps(bundle["cm_ensemble_v1"]))
    (result_root / "cm_native_artifacts_v1.json").write_text(json.dumps(bundle["cm_native_artifacts_v1"]))
    index_without_hash = {
        "schema_name": "cm_derived_index", "schema_version": 1,
        "request_id": request["request_id"],
        "source_ensemble_sha256": canonical_sha256(bundle["cm_ensemble_v1"]),
        "records": [],
        "structure_maps": bundle["cm_structure_maps"],
        "landscapes": bundle["cm_frustration_landscapes"],
        "analysis": bundle["cm_analysis_v1"],
        "lineage": None, "support": None, "missingness": None, "resampling": None,
    }
    (result_root / "cm_derived_index_v1.json").write_text(json.dumps({
        **index_without_hash,
        "index_sha256": canonical_sha256(index_without_hash),
    }))
    session, engine = await _session(tmp_path)
    try:
        await _register(session, request, bundle)
        await session.commit()
        assert await ingest_job_results(request["request_id"], str(output), session) == 1
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_result_ingester_resolves_retry_through_canonical_lineage_root(
    tmp_path: Path,
) -> None:
    output = tmp_path / "retry-output"
    result_root = output / "canonical_protenix"
    result_root.mkdir(parents=True)
    request, bundle = _no_authority_bundle(result_root)
    (result_root / "cm_ensemble_v1.json").write_text(json.dumps(bundle["cm_ensemble_v1"]))
    (result_root / "cm_native_artifacts_v1.json").write_text(json.dumps(bundle["cm_native_artifacts_v1"]))
    index_without_hash = {
        "schema_name": "cm_derived_index", "schema_version": 1,
        "request_id": request["request_id"],
        "source_ensemble_sha256": canonical_sha256(bundle["cm_ensemble_v1"]),
        "records": [],
        "structure_maps": bundle["cm_structure_maps"],
        "landscapes": bundle["cm_frustration_landscapes"],
        "analysis": bundle["cm_analysis_v1"],
        "lineage": None, "support": None, "missingness": None, "resampling": None,
    }
    (result_root / "cm_derived_index_v1.json").write_text(json.dumps({
        **index_without_hash,
        "index_sha256": canonical_sha256(index_without_hash),
    }))
    session, engine = await _session(tmp_path)
    retry_job_id = "cm-retry-job"
    try:
        record = await _register(session, request, bundle)
        session.add(Job(
            id=retry_job_id,
            name="CM retry",
            model_id="conformational_mapping",
            mode="map",
            status="running",
            queue_status="running",
            params={},
            output_dir=str(output),
            lineage_root_job_id=request["request_id"],
            stage_family="conformational_mapping",
            created_at=datetime.utcnow(),
        ))
        await session.flush()
        record.job_id = retry_job_id
        await session.commit()

        assert await ingest_job_results(retry_job_id, str(output), session) == 1
        await session.refresh(record)
        assert record.status == "completed"
        assert record.job_id == retry_job_id
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("intermediate_symlink", [False, True])
async def test_cm_result_ingester_persists_sealed_referenced_global_results_before_cm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    intermediate_symlink: bool,
) -> None:
    from services import result_ingester

    output = tmp_path / "global-output"
    result_root = output / "canonical_protenix"
    result_root.mkdir(parents=True)
    request, bundle, reference, result_values = _global_bundle(result_root)
    candidate_id_value = reference["candidate_id"]
    bundle_relative = Path(reference["bundle_relative_path"])
    if intermediate_symlink:
        real_prefix = result_root / "real-frustrampnn"
        real_prefix.mkdir()
        (result_root / bundle_relative.parts[0]).symlink_to(
            real_prefix.name,
            target_is_directory=True,
        )
        bundle_root = real_prefix.joinpath(*bundle_relative.parts[1:])
    else:
        bundle_root = result_root / bundle_relative
    bundle_root.mkdir(parents=True)
    manifest_bytes = canonical_json_bytes({"schema_name": "frustrampnn_result_manifest", "schema_version": 2})
    landscape_bytes = canonical_json_bytes(bundle["frustrampnn_landscapes"][0])
    structure_map_bytes = canonical_json_bytes(bundle["frustrampnn_structure_maps"][0])
    (bundle_root / "frustrampnn_result_manifest_v2.json").write_bytes(manifest_bytes)
    (bundle_root / "frustrampnn_landscape_v2.json").write_bytes(landscape_bytes)
    (bundle_root / "frustrampnn_structure_map_v1.json").write_bytes(structure_map_bytes)
    (bundle_root / "workflow_component_result_v2.json").write_text("{}")
    reference.update({
        "result_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "landscape_sha256": hashlib.sha256(landscape_bytes).hexdigest(),
        "structure_map_sha256": hashlib.sha256(structure_map_bytes).hexdigest(),
    })
    (result_root / "cm_ensemble_v1.json").write_text(json.dumps(bundle["cm_ensemble_v1"]))
    (result_root / "cm_native_artifacts_v1.json").write_text(json.dumps(bundle["cm_native_artifacts_v1"]))
    (result_root / "cm_complex_snapshots_v1.json").write_text(json.dumps(bundle["cm_complex_snapshots"]))
    index_without_hash = {
        "schema_name": "cm_derived_index", "schema_version": 1,
        "request_id": request["request_id"],
        "source_ensemble_sha256": canonical_sha256(bundle["cm_ensemble_v1"]),
        "records": [],
        "frustrampnn_result_references": bundle["cm_frustrampnn_result_references"],
        "analysis": bundle["cm_analysis_v1"],
        "lineage": None, "support": None, "missingness": None, "resampling": None,
    }
    (result_root / "cm_derived_index_v1.json").write_text(json.dumps({
        **index_without_hash,
        "index_sha256": canonical_sha256(index_without_hash),
    }))
    validated_bundle = SimpleNamespace(
        manifest={
            "invocation_id": reference["invocation_id"],
            "candidate_id": candidate_id_value,
        },
        request={"parent_workflow_id": "conformational_mapping"},
    )

    monkeypatch.setattr(result_ingester, "_read_explicit_terminal_envelope", lambda *_args: {})
    monkeypatch.setattr(
        result_ingester,
        "validate_frustrampnn_result_bundle",
        lambda *_args, **_kwargs: validated_bundle,
    )

    async def persist_global(ingest_session, *_args, **_kwargs) -> None:
        existing = await ingest_session.get(
            FrustraMPNNResult,
            (request["request_id"], reference["invocation_id"]),
        )
        if existing is None:
            ingest_session.add(FrustraMPNNResult(**result_values))
            await ingest_session.flush()

    async def persist_cm(ingest_session, cm_request, **_kwargs) -> None:
        persisted = await ingest_session.get(
            FrustraMPNNResult,
            (request["request_id"], reference["invocation_id"]),
        )
        assert persisted is not None, "referenced global result was not persisted before CM"
        cm_request.status = "completed"
        await ingest_session.commit()

    monkeypatch.setattr(result_ingester, "ingest_frustrampnn_result_bundle", persist_global)
    monkeypatch.setattr(result_ingester, "_persist_cm_bundle_atomically", persist_cm)

    session, engine = await _session(tmp_path)
    try:
        await _register(session, request, bundle)
        await session.commit()
        if intermediate_symlink:
            with pytest.raises(ConformationalPersistenceError, match="unsafe"):
                await ingest_job_results(request["request_id"], str(output), session)
        else:
            assert await ingest_job_results(request["request_id"], str(output), session) == 1
            assert await ingest_job_results(request["request_id"], str(output), session) == 1
    finally:
        await session.close()
        await engine.dispose()
