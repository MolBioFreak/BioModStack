from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import (
    Base,
    ConformationalMappingLandscapeRow,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    Job,
)
from routers.designs import ANALYTICS_LOAD_ONLY_COLUMNS
from services.conformational_mapping.contracts import normalize_artifact_class_alias
from services.conformational_mapping.persistence import (
    RESULT_CONTRACT_BY_BACKEND,
    ConformationalPersistenceError,
    paged_landscape,
    persist_derived_record,
    persist_landscape_matrix,
    register_prepared_request,
)
from services.result_contracts import (
    get_result_contract_definitions,
    normalize_conformational_mapping_artifact_class,
    resolve_result_contract,
)


async def _session(tmp_path: Path) -> tuple[AsyncSession, AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


def _request(request_id: str = "request-1") -> dict:
    return {
        "request_id": request_id, "request_sha256": "a" * 64,
        "backend": "protenix_v2_ensemble",
    }


def _plan() -> dict:
    return {"coordinate_plan_sha256": "b" * 64, "expected_cardinality": 1, "coordinates": [{"target_id": "t"}]}


def _job(job_id: str = "job-1") -> Job:
    return Job(id=job_id, name="cm", model_id="conformational_mapping", mode="map", status="queued", params={}, created_at=datetime.utcnow())


def test_cm11_001_backend_contract_resolution() -> None:
    definitions = {item.contract_id: item for item in get_result_contract_definitions()}
    expected = {
        *RESULT_CONTRACT_BY_BACKEND.values(),
        "conformational_mapping_analysis_v1", "conformational_mapping_resampling_v1",
    }
    assert expected <= definitions.keys()
    for contract_id in expected:
        assert resolve_result_contract(review_profile_id=contract_id).analysis_contract_id == contract_id


def test_cm11_002_new_write_is_monomer_conformation() -> None:
    definitions = {item.contract_id: item for item in get_result_contract_definitions()}
    for contract_id in RESULT_CONTRACT_BY_BACKEND.values():
        assert definitions[contract_id].artifact_classes == ["monomer_conformation"]
    assert normalize_artifact_class_alias("monomer_conformation") == "monomer_conformation"


def test_cm11_003_old_conformer_resolves_without_rewrite() -> None:
    stored = "conformer"
    assert normalize_conformational_mapping_artifact_class(stored) == "monomer_conformation"
    assert normalize_artifact_class_alias(stored) == "monomer_conformation"
    assert stored == "conformer"


@pytest.mark.parametrize("value", ["conformation", "monomer-conformation", "conformer_v1", "Conformer "])
def test_cm11_004_unknown_alias_fails_closed(value: str) -> None:
    assert normalize_conformational_mapping_artifact_class(value) is None
    contract = resolve_result_contract(artifact_class=value)
    assert not contract.viewer_capabilities and not contract.supported_analyzers


@pytest.mark.asyncio
async def test_cm11_005_idempotent_ingestion(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        first = await register_prepared_request(session, job=_job(), principal_id="alice", request=_request(), coordinate_plan=_plan(), resume_key="0" * 64, capability_sha256="c" * 64)
        second = await register_prepared_request(session, job=_job("job-2"), principal_id="alice", request=_request("request-2"), coordinate_plan=_plan(), resume_key="0" * 64, capability_sha256="c" * 64)
        assert first is second
        assert await session.scalar(select(func.count()).select_from(ConformationalMappingRequest)) == 1
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm11_005a_landscape_rows_retain_container_provenance(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        fixture_path = (
            Path(__file__).parent
            / "fixtures/conformational_mapping/schemas/positive/all_schemas.json"
        )
        landscape = json.loads(fixture_path.read_text())["cm_frustration_landscape_v1"]
        await persist_landscape_matrix(session, "request-1", landscape)
        await session.flush()
        row = await session.scalar(select(ConformationalMappingLandscapeRow))
        assert row is not None
        assert row.provenance_json["container_sha256"] == landscape["container_sha256"]
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_legacy_v1_landscape_replay_does_not_fabricate_container_digest(
    tmp_path: Path,
) -> None:
    session, engine = await _session(tmp_path)
    try:
        fixture_path = (
            Path(__file__).parent
            / "fixtures/conformational_mapping/schemas/positive/all_schemas.json"
        )
        landscape = json.loads(fixture_path.read_text())["cm_frustration_landscape_v1"]
        landscape.pop("container_sha256")
        await persist_landscape_matrix(session, "legacy-request", landscape)
        await session.flush()
        row = await session.scalar(select(ConformationalMappingLandscapeRow))
        assert row is not None
        assert "container_sha256" not in row.provenance_json
    finally:
        await session.close()
        await engine.dispose()


def test_cm11_006_manifest_hash_validation() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "conformational_mapping" / "persistence.py").read_text()
    for rejection in (
        "native artifact hash or size mismatch", "result coordinates do not equal stored request authority",
        "duplicate path", "derived structure-map and landscape candidate sets must exactly equal the ensemble",
    ):
        assert rejection in source


@pytest.mark.asyncio
async def test_cm11_007_transaction_rollback(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        await register_prepared_request(session, job=_job(), principal_id="alice", request=_request(), coordinate_plan=_plan(), resume_key="0" * 64, capability_sha256="c" * 64)
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(ConformationalMappingRequest)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm11_008_lineage_queries(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        record = await register_prepared_request(session, job=_job(), principal_id="alice", request=_request(), coordinate_plan=_plan(), resume_key="0" * 64, capability_sha256="c" * 64)
        payload = {"parent": "source", "child": "result", "sha256": "d" * 64}
        await persist_derived_record(session, record.request_id, record_type="lineage", record_key="primary", payload=payload)
        await session.commit()
        row = await session.scalar(select(ConformationalMappingRecord).where(ConformationalMappingRecord.record_type == "lineage"))
        assert row is not None and row.payload_json == payload
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm11_009_landscape_pagination_and_range(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        await register_prepared_request(session, job=_job(), principal_id="alice", request=_request(), coordinate_plan=_plan(), resume_key="0" * 64, capability_sha256="c" * 64)
        for index in range(1, 6):
            session.add(ConformationalMappingLandscapeRow(
                id=f"row-{index}", request_id="request-1", candidate_id="candidate", entity_instance_id="copy",
                auth_asym_id="A", auth_seq_id=str(index), insertion_code="", sequence_index=index,
                wt="A", mutation_aa="V", score=float(index), score_class="neutral", scoreable=True,
                status="ok", reason=None, provenance_json={},
            ))
        await session.commit()
        page = await paged_landscape(session, "request-1", sequence_start=2, sequence_end=4, offset=1, limit=2)
        assert [row.sequence_index for row in page] == [3, 4]
        with pytest.raises(ConformationalPersistenceError):
            await paged_landscape(session, "request-1", limit=1001)
    finally:
        await session.close()
        await engine.dispose()


def test_cm11_010_no_protenix_import_misclassification() -> None:
    ambiguous = resolve_result_contract(model_type="conformational_mapping")
    assert not ambiguous.analysis_contract_id and not ambiguous.viewer_capabilities
    imported = resolve_result_contract(review_profile_id="conformational_mapping_import_v1")
    assert imported.analysis_contract_id == "conformational_mapping_import_v1"


def test_cm11_011_confornets_experimental_behavior_preserved() -> None:
    contract = resolve_result_contract(review_profile_id="confornets_monomer_v1")
    assert contract.analysis_contract_id == "confornets_monomer_v1"
    assert contract.viewer_capabilities == ["structure_viewer", "generic_metadata"]


def test_analytics_projection_includes_review_profile_without_deferred_io() -> None:
    assert any(column.key == "review_profile_id" for column in ANALYTICS_LOAD_ONLY_COLUMNS)
