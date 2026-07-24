from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingLandscapeRow, ConformationalMappingRecord, Job
from services.conformational_mapping.contracts import (
    canonical_sha256,
    candidate_id,
    request_sha256,
    validate_schema,
)
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    ingest_result_bundle,
    register_prepared_request,
)
from services.conformational_mapping.state_landscape_analysis import (
    derive_state_landscape_analysis_for_request,
    validate_state_landscape_analysis_binding,
)
from services.result_ingester import ingest_job_results


FIXTURE = Path(__file__).parent / "fixtures" / "conformational_mapping" / "schemas" / "positive" / "all_schemas.json"


async def _session(tmp_path: Path) -> tuple[AsyncSession, object]:
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
    return request, bundle, artifact


async def _register(session: AsyncSession, request: dict, bundle: dict) -> object:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("state_value", [None, []])
async def test_cm_ingest_bundle_treats_missing_or_empty_state_analysis_as_legacy_absence(
    tmp_path: Path, state_value: object
) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle = _no_authority_bundle(root)
    if state_value is not None:
        bundle["cm_state_landscape_analyses"] = state_value
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
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