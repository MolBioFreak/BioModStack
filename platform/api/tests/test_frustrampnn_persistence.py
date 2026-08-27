from __future__ import annotations

import copy
import csv
from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest
import pytest_asyncio
import rfc8785
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base,
    ConformationalMappingRequest,
    Design,
    FrustraMPNNArtifact,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    FrustraMPNNStatisticsAnalysis,
    Job,
    ScientificArtifactReceipt,
)
from services.frustrampnn.analysis import summarize_landscape
from services.frustrampnn.contracts import (
    canonical_json_bytes,
    canonical_json_loads,
)
from services.frustrampnn.manifests import MANIFEST_PATH, build_result_manifest
from services.conformational_mapping.contracts import candidate_id as cm_candidate_id


TESTS_DIR = Path(__file__).resolve().parent


def _fixture_module():
    name = "_frustrampnn_manifest_fixture_for_persistence"
    path = TESTS_DIR / "test_frustrampnn_manifests.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANIFEST_FIXTURE = _fixture_module()


def _component_fixture_module():
    name = "_frustrampnn_component_fixture_for_persistence"
    path = TESTS_DIR / "test_frustrampnn_component_phase3.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPONENT_FIXTURE = _component_fixture_module()


def _persistence():
    path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "frustrampnn"
        / "persistence.py"
    )
    assert path.is_file(), "FrustraMPNN persistence service is missing"
    return importlib.import_module("services.frustrampnn.persistence")


@pytest.mark.parametrize(
    "statistics",
    [
        {"integral_float": 1.0, "nonintegral_float": 1.25},
        {"integral_float": -0.0, "nonintegral_float": -12.5},
    ],
)
def test_statistics_persistence_requires_exact_rfc8785_float_bytes(
    statistics: dict[str, float],
) -> None:
    persistence = _persistence()
    canonical = rfc8785.dumps(statistics)

    assert persistence._canonical_object(
        canonical, "frustrampnn_statistics_v1.json"
    ) == statistics

    alternate = canonical_json_bytes(statistics)
    assert alternate != canonical
    with pytest.raises(
        persistence.FrustraMPNNPersistenceError,
        match="exact canonical JSON object",
    ):
        persistence._canonical_object(
            alternate, "frustrampnn_statistics_v1.json"
        )


def test_nonstatistics_persistence_keeps_existing_canonical_profile() -> None:
    persistence = _persistence()
    payload = {"integral_float": 1.0, "nonintegral_float": 1.0e-7}

    assert persistence._canonical_object(
        canonical_json_bytes(payload), "frustrampnn_summary_v2.json"
    ) == payload
    with pytest.raises(
        persistence.FrustraMPNNPersistenceError,
        match="exact canonical JSON object",
    ):
        persistence._canonical_object(
            rfc8785.dumps(payload), "frustrampnn_summary_v2.json"
        )


def _load_json(root: Path, relative: str) -> dict:
    return canonical_json_loads((root / relative).read_bytes())


def _write_json(root: Path, relative: str, value: dict) -> None:
    (root / relative).write_bytes(canonical_json_bytes(value))


def _publish_manifest(root: Path) -> tuple[dict, dict]:
    manifest_path = root / MANIFEST_PATH
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest_path.unlink()
    manifest = build_result_manifest(root)
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    return manifest, _load_json(root, "workflow_component_result_v1.json")


def _bundle(root: Path, *, source_artifact_id: str | None = "design-1") -> tuple[dict, dict]:
    root.mkdir(parents=True)
    MANIFEST_FIXTURE._bundle(root)
    if source_artifact_id is not None:
        request = _load_json(root, "workflow_component_request_v1.json")
        request["source_artifact"]["artifact_id"] = source_artifact_id
        _write_json(root, "workflow_component_request_v1.json", request)
        MANIFEST_FIXTURE._rehash_bundle(root)
    return _publish_manifest(root)


def _v2_bundle(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    residues: list[tuple[str, int]] | None = None,
    thresholds: tuple[float, float] = (-0.5, 0.5),
    scores: dict[str, float] | None = None,
    distinct_source_identity: bool = False,
) -> tuple[dict, dict]:
    component = importlib.import_module("scripts.run_frustrampnn_component")
    selected_residues = residues or [("A", 1)]
    inputs = root.parent / f"{root.name}-inputs"
    inputs.mkdir()
    if distinct_source_identity:
        physical_source = COMPONENT_FIXTURE._multi_residue_pdb
        monkeypatch.setattr(
            COMPONENT_FIXTURE,
            "_multi_residue_pdb",
            lambda selected: b"HEADER    ORIGINAL SOURCE AUTHORITY\n"
            + physical_source(selected),
        )
    request, normalized, structure_map, _ = COMPONENT_FIXTURE._v2_inputs(
        inputs,
        residues=selected_residues,
        selected=[
            (chain, index)
            for index, (chain, _residue_id) in enumerate(selected_residues)
        ],
        thresholds=thresholds,
    )
    COMPONENT_FIXTURE._mock_v2_runtime(
        component,
        monkeypatch,
        inputs,
        scores=scores,
    )
    manifest = component.run_component(
        request=request,
        source_structure=normalized,
        structure_map=structure_map,
        output_dir=root,
        container=inputs / "mock.sif",
        physical_gpu_id=3,
    )
    terminal = _load_json(root, "workflow_component_result_v2.json")
    return manifest, terminal


def _v3_bundle(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, dict]:
    component = importlib.import_module("scripts.run_frustrampnn_component")
    inputs = root.parent / f"{root.name}-inputs"
    inputs.mkdir()
    request, normalized, structure_map, _ = COMPONENT_FIXTURE._v2_inputs(
        inputs,
        residues=[("A", 1)],
        selected=[("A", 0)],
        request_generation=3,
    )
    request["schema_version"] = 3
    request["component_contract_version"] = "3.0"
    COMPONENT_FIXTURE._mock_v2_runtime(component, monkeypatch, inputs)
    manifest = component.run_component(
        request=request,
        source_structure=normalized,
        structure_map=structure_map,
        output_dir=root,
        container=inputs / "mock.sif",
        physical_gpu_id=3,
    )
    return manifest, _load_json(root, "workflow_component_result_v3.json")


def _republish(root: Path) -> tuple[dict, dict]:
    (root / MANIFEST_PATH).unlink()
    MANIFEST_FIXTURE._rehash_bundle(root)
    return _publish_manifest(root)


def _retarget_bundle(
    root: Path, *, parent_job_id: str, candidate_id: str, design_id: str
) -> tuple[dict, dict]:
    manifest_path = root / MANIFEST_PATH
    if manifest_path.exists():
        manifest_path.unlink()
    replacements = {
        "job-1": parent_job_id,
        "candidate-1": candidate_id,
        "design-1": design_id,
    }

    def replace_identity(value):
        if isinstance(value, dict):
            return {key: replace_identity(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_identity(item) for item in value]
        if isinstance(value, str):
            return replacements.get(value, value)
        return value

    for path in sorted(root.glob("*.json")):
        _write_json(root, path.name, replace_identity(_load_json(root, path.name)))
    MANIFEST_FIXTURE._rehash_bundle(root)
    return _publish_manifest(root)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database_path = tmp_path / "persistence.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _seed_job(
    sessions: async_sessionmaker,
    *,
    job_id: str = "job-1",
    design_id: str | None = "design-1",
    design_name: str = "candidate-1",
    design_pdb_path: Path | None = None,
) -> None:
    async with sessions() as session:
        session.add(
            Job(
                id=job_id,
                name=job_id,
                status="completed",
                model_id="boltz2",
                mode="structure_prediction",
                params={},
            )
        )
        if design_id is not None:
            if design_pdb_path is None:
                engine = sessions.kw["bind"]
                design_pdb_path = Path(engine.url.database).parent / f"{design_id}.pdb"
            design_pdb_path.write_bytes(MANIFEST_FIXTURE._pdb())
            session.add(
                Design(
                    id=design_id,
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(design_pdb_path),
                )
            )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_status", ["queued", "completed"])
async def test_exact_source_link_accepts_typed_cm_candidate_without_design(
    db, request_status: str,
) -> None:
    persistence = _persistence()
    request_id = "cm-request"
    job_id = "cm-retry-job"
    coordinate = {
        "backend": "confornets",
        "target_id": "target-a",
        "task": "diversity",
        "test_case_id": "case-a",
        "run_index": 0,
        "sample_index": 0,
        "saved_step": 0,
        "confornet_index": 0,
        "reference_id": None,
    }
    candidate_id = cm_candidate_id(coordinate)
    async with db() as session:
        job = Job(
            id=job_id,
            name="CM retry",
            status="running",
            queue_status="running",
            model_id="conformational_mapping",
            mode="map",
            params={},
            lineage_root_job_id=request_id,
            stage_family="conformational_mapping",
        )
        request = ConformationalMappingRequest(
            request_id=request_id,
            job_id=job_id,
            principal_id="alice",
            backend="confornets",
            status=request_status,
            request_sha256="1" * 64,
            coordinate_plan_sha256="2" * 64,
            resume_key="3" * 64,
            result_contract_id="conformational_mapping_confornets_v1",
            request_json={},
            coordinate_plan_json={"coordinates": [coordinate], "expected_cardinality": 1},
            progress_json={"phase": "queued"},
        )
        session.add(job)
        await session.flush()
        session.add(request)
        await session.commit()

        assert await persistence._exact_design_link(
            session,
            source_artifact_id=candidate_id,
            source_artifact_sha256="4" * 64,
            normalized_source_sha256="5" * 64,
            parent_job_id=job_id,
            parent_workflow_id="conformational_mapping",
            candidate_id=candidate_id,
        ) is None


async def _seed_v2_child_job(sessions: async_sessionmaker, root: Path) -> None:
    request = _load_json(root, "workflow_component_request_v2.json")
    async with sessions() as session:
        session.add(
            Job(
                id=request["parent_job_id"],
                name=request["parent_job_id"],
                status="completed",
                model_id="frustrampnn",
                mode="component",
                params={
                    "requested_settings_sha256": "0" * 64,
                    "effective_settings": {"mutable_job_param": True},
                    "statistics": {"mutable_job_param": True},
                    "_frustrampnn_child_v1": {
                        "selection": [
                            {
                                "design_id": None,
                                "source_job_id": None,
                                "normalized_source_sha256": request["normalized_pdb_sha256"],
                            }
                        ]
                    }
                },
            )
        )
        await session.commit()


async def _seed_v3_child_job(sessions: async_sessionmaker, root: Path) -> None:
    request = _load_json(root, "workflow_component_request_v3.json")
    async with sessions() as session:
        session.add(
            Job(
                id=request["parent_job_id"],
                name=request["parent_job_id"],
                status="completed",
                model_id="frustrampnn",
                mode="component",
                output_dir=str(root.parent),
                params={
                    "_frustrampnn_child_v1": {
                        "selection": [
                            {
                                "design_id": None,
                                "source_job_id": None,
                                "normalized_source_sha256": request[
                                    "normalized_pdb_sha256"
                                ],
                            }
                        ]
                    }
                },
            )
        )
        await session.commit()


async def _counts(session) -> tuple[int, int, int]:
    counts: list[int] = []
    for model in (FrustraMPNNResult, FrustraMPNNArtifact, FrustraMPNNLandscapeRow):
        value = (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()
        counts.append(int(value))
    return counts[0], counts[1], counts[2]


@pytest.mark.asyncio
async def test_v3_core_ingestion_queues_statistics_child_in_same_transaction(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _persistence()
    root = tmp_path / "v3-core"
    _, terminal = _v3_bundle(root, monkeypatch)
    await _seed_v3_child_job(db, root)

    async with db() as session:
        result = await persistence.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )
        children = (
            await session.execute(select(FrustraMPNNStatisticsAnalysis))
        ).scalars().all()
        assert len(children) == 1
        child = children[0]
        assert child.state == "queued"
        assert child.parent_job_id == result.parent_job_id
        assert child.invocation_id == result.invocation_id
        assert child.core_manifest_sha256 == result.manifest_sha256
        assert child.core_landscape_sha256 == result.summary_json["landscape_sha256"]
        assert child.core_bundle_relative_path == "v3-core"
        artifact_receipt = await session.get(
            ScientificArtifactReceipt,
            child.core_artifact_id,
        )
        assert artifact_receipt is not None
        assert artifact_receipt.role == "landscape"
        assert artifact_receipt.owner_id == f"{result.parent_job_id}:{result.invocation_id}"


@pytest.mark.asyncio
async def test_statistics_worker_publishes_derived_artifact_without_rewriting_core(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _persistence()
    statistics_jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    root = tmp_path / "v3-statistics-worker"
    _, terminal = _v3_bundle(root, monkeypatch)
    await _seed_v3_child_job(db, root)

    async with db() as session:
        result = await persistence.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )
        child = (
            await session.execute(select(FrustraMPNNStatisticsAnalysis))
        ).scalar_one()
        core_authority = (
            result.request_sha256,
            result.manifest_sha256,
            copy.deepcopy(result.manifest_json),
            copy.deepcopy(result.terminal_result_json),
        )
        statistics = await statistics_jobs.run_statistics_child_once(
            session,
            analysis_id=child.analysis_id,
        )
        await session.commit()

        assert statistics["schema_version"] == 2
        router = importlib.import_module("routers.frustrampnn")
        assert (
            router.FrustraMPNNStatisticsDocument.model_validate(statistics).root
            == statistics
        )
        assert statistics["analysis_receipt"]["analysis_id"] == child.analysis_id
        assert statistics["landscape_sha256"] == child.core_landscape_sha256
        assert child.state == "completed"
        assert child.statistics_sha256 == statistics["statistics_sha256"]
        assert child.artifact_sha256 == result.statistics_json["content_sha256"]
        statistics_receipt = await session.get(
            ScientificArtifactReceipt,
            result.statistics_json["artifact_id"],
        )
        assert statistics_receipt is not None
        assert statistics_receipt.owner_kind == "frustrampnn_statistics_analysis"
        assert statistics_receipt.owner_id == child.analysis_id
        assert result.statistics_sha256 == statistics["statistics_sha256"]
        assert result.comparison_compatibility_id == statistics[
            "comparison_compatibility_id"
        ]
        assert (
            result.request_sha256,
            result.manifest_sha256,
            result.manifest_json,
            result.terminal_result_json,
        ) == core_authority


@pytest.mark.asyncio
async def test_statistics_worker_adapter_claims_one_pending_child(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _persistence()
    statistics_jobs = importlib.import_module("services.frustrampnn.statistics_jobs")
    root = tmp_path / "v3-statistics-adapter"
    _, terminal = _v3_bundle(root, monkeypatch)
    await _seed_v3_child_job(db, root)
    async with db() as session:
        result = await persistence.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )
        analysis_id = (
            await session.execute(select(FrustraMPNNStatisticsAnalysis.analysis_id))
        ).scalar_one()
        expected_statistics_hash = result.statistics_sha256

    worker = statistics_jobs.FrustraMPNNStatisticsWorker(db)
    processed = await worker.run_pending_once()
    assert processed == analysis_id

    async with db() as session:
        child = await session.get(FrustraMPNNStatisticsAnalysis, analysis_id)
        result = await session.get(FrustraMPNNResult, ("job-v2", "invoke-v2"))
        assert child is not None and child.state == "completed"
        assert result is not None and result.statistics_sha256 is not None
        assert result.statistics_sha256 != expected_statistics_hash


@pytest.mark.asyncio
async def test_complete_manifest_bundle_persists_exact_authority_and_rows(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle")
    await _seed_job(db)

    async with db() as session:
        result = await module.ingest_result_bundle(
            session,
            tmp_path / "bundle",
            parent_job_id="job-1",
            terminal_envelope=terminal,
        )
        await session.refresh(result)
        artifacts = (
            await session.execute(
                select(FrustraMPNNArtifact)
                .where(FrustraMPNNArtifact.invocation_id == result.invocation_id)
                .order_by(FrustraMPNNArtifact.relative_path)
            )
        ).scalars().all()
        rows = await module.paged_landscape(
            session,
            "job-1",
            result.invocation_id,
            limit=20,
        )

    request = _load_json(tmp_path / "bundle", "workflow_component_request_v1.json")
    receipt = _load_json(tmp_path / "bundle", "frustrampnn_execution_receipt_v1.json")
    summary = _load_json(tmp_path / "bundle", "frustrampnn_summary_v1.json")
    assert result.invocation_id == "invoke-1"
    assert result.parent_job_id == terminal["parent_job_id"] == "job-1"
    assert result.parent_workflow_id == terminal["parent_workflow_id"]
    assert result.candidate_id == terminal["candidate_id"]
    assert result.request_sha256 == terminal["request_sha256"]
    assert result.source_artifact_id == "design-1"
    assert result.design_id == "design-1"
    assert result.source_artifact_sha256 == request["source_artifact"]["sha256"]
    assert result.runtime_identity_json == receipt
    assert result.assigned_gpu_json == terminal["assigned_gpu"]
    assert result.terminal_result_json == terminal
    assert result.summary_json == summary
    assert len(artifacts) == 10
    assert len(rows) == 20
    assert all(Path(artifact.storage_path).is_file() for artifact in artifacts)
    assert {row["mutation_aa"] for row in rows} == set("ACDEFGHIKLMNPQRSTVWY")
    assert all(row["row"]["residue"]["auth_seq_id"] == 1 for row in rows)
    assert all(row["provenance"]["landscape_sha256"] == summary["landscape_sha256"] for row in rows)
    assert (
        result.settings_sha256,
        result.effective_settings_sha256,
        result.effective_settings_json,
        result.capability_inventory_sha256,
        result.statistics_sha256,
        result.statistics_json,
        result.comparison_compatibility_id,
    ) == (None, None, None, None, None, None, None)


@pytest.mark.asyncio
async def test_v2_persists_exact_authority_statistics_artifacts_rows_and_replays(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _persistence()
    root = tmp_path / "v2-bundle"
    manifest, terminal = _v2_bundle(
        root,
        monkeypatch,
        residues=[("A", 1), ("A", 2)],
        scores={"A": -0.75, "G": 0.75},
        distinct_source_identity=True,
    )
    await _seed_v2_child_job(db, root)
    request = _load_json(root, "workflow_component_request_v2.json")
    statistics = _load_json(root, "frustrampnn_statistics_v1.json")
    landscape = _load_json(root, "frustrampnn_landscape_v2.json")

    async with db() as session:
        result = await module.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )
        artifacts = (
            await session.execute(
                select(FrustraMPNNArtifact)
                .where(FrustraMPNNArtifact.invocation_id == "invoke-v2")
                .order_by(FrustraMPNNArtifact.relative_path)
            )
        ).scalars().all()
        rows = await module.paged_landscape(
            session,
            "job-v2",
            "invoke-v2",
            limit=40,
        )
        created_at = result.created_at
        counts = await _counts(session)
        replay = await module.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )

    assert result.settings_sha256 == request["requested_settings_sha256"]
    assert result.effective_settings_sha256 == request["effective_settings_sha256"]
    assert result.effective_settings_json == request["effective_settings"]
    assert result.source_artifact_sha256 == request["source_artifact"]["sha256"]
    assert request["source_artifact"]["sha256"] != request["normalized_pdb_sha256"]
    assert (
        result.capability_inventory_sha256
        == request["capability_inventory_byte_sha256"]
        == statistics["capability_inventory_byte_sha256"]
    )
    assert result.statistics_sha256 == statistics["statistics_sha256"]
    assert result.statistics_json == statistics
    assert (
        result.comparison_compatibility_id
        == statistics["comparison_compatibility_id"]
        == manifest["comparison_compatibility_id"]
    )
    assert result.terminal_result_json == terminal
    assert result.runtime_identity_json == _load_json(
        root, "frustrampnn_execution_receipt_v2.json"
    )
    assert result.assigned_gpu_json == {
        "physical_device_id": "3",
        "task_visible_device_index": 0,
    }
    assert len(artifacts) == 10
    records = {record["relative_path"]: record for record in manifest["artifacts"]}
    assert {artifact.relative_path for artifact in artifacts} == set(records)
    assert (
        records["normalized_input.pdb"]["sha256"]
        == request["normalized_pdb_sha256"]
    )
    for artifact in artifacts:
        record = records[artifact.relative_path]
        payload = (root / artifact.relative_path).read_bytes()
        assert artifact.metadata_json == record
        assert artifact.content_sha256 == hashlib.sha256(payload).hexdigest() == record["sha256"]
        assert artifact.size_bytes == len(payload) == record["bytes"]
        assert artifact.media_type in {
            "application/json",
            "chemical/x-pdb",
            "text/csv",
            "text/plain",
        }
    assert len(rows) == 2 * 20
    assert len({row["id"] for row in rows}) == 2 * 20
    expected_row_json = {
        canonical_json_bytes(
            {
                "residue": {
                    key: value for key, value in residue.items() if key != "slots"
                },
                "slot": slot,
            }
        )
        for residue in landscape["residues"]
        for slot in residue["slots"]
    }
    assert {canonical_json_bytes(row["row"]) for row in rows} == expected_row_json
    assert all(
        {
            "source_entity_id",
            "label_seq_id",
            "pdb_residue_id",
            "pdb_insertion_code",
            "residue_name",
        }.issubset(row["row"]["residue"])
        for row in rows
    )
    assert replay.created_at == created_at
    async with db() as session:
        assert await _counts(session) == counts == (1, 10, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "contradiction"),
    [
        ("settings_sha256", "0" * 64),
        ("effective_settings_sha256", "1" * 64),
        ("effective_settings_json", {"contradiction": "effective_settings"}),
        ("capability_inventory_sha256", "2" * 64),
        ("statistics_sha256", "3" * 64),
        ("statistics_json", {"contradiction": "statistics"}),
        ("comparison_compatibility_id", "4" * 64),
    ],
)
async def test_v2_replay_conflicts_on_each_new_authority_field_without_repair(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    contradiction,
) -> None:
    module = _persistence()
    root = tmp_path / f"v2-conflict-{field}"
    _, terminal = _v2_bundle(root, monkeypatch)
    await _seed_v2_child_job(db, root)

    async with db() as session:
        result = await module.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )
        setattr(result, field, copy.deepcopy(contradiction))
        await session.commit()
        counts = await _counts(session)

        with pytest.raises(module.FrustraMPNNConflictError, match="result authority"):
            await module.ingest_result_bundle(
                session,
                root,
                parent_job_id="job-v2",
                terminal_envelope=terminal,
            )

    async with db() as verification:
        persisted = await verification.get(FrustraMPNNResult, ("job-v2", "invoke-v2"))
        assert persisted is not None
        assert canonical_json_bytes(getattr(persisted, field)) == canonical_json_bytes(
            contradiction
        )
        assert await _counts(verification) == counts == (1, 10, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    [
        "artifact_storage_path",
        "artifact_metadata",
        "landscape_source_hash",
        "landscape_row_count",
    ],
)
async def test_v2_replay_conflicts_on_artifact_and_landscape_contradictions(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
) -> None:
    module = _persistence()
    root = tmp_path / f"v2-{authority}"
    _, terminal = _v2_bundle(root, monkeypatch)
    await _seed_v2_child_job(db, root)

    async with db() as session:
        await module.ingest_result_bundle(
            session,
            root,
            parent_job_id="job-v2",
            terminal_envelope=terminal,
        )
        if authority.startswith("artifact_"):
            persisted = (
                await session.execute(
                    select(FrustraMPNNArtifact).where(
                        FrustraMPNNArtifact.parent_job_id == "job-v2",
                        FrustraMPNNArtifact.invocation_id == "invoke-v2",
                    )
                )
            ).scalars().first()
            assert persisted is not None
            if authority == "artifact_storage_path":
                persisted.storage_path += ".contradiction"
            else:
                persisted.metadata_json = {"contradiction": "artifact metadata"}
        else:
            persisted = (
                await session.execute(
                    select(ScientificArtifactReceipt).where(
                        ScientificArtifactReceipt.owner_kind == "frustrampnn_result",
                        ScientificArtifactReceipt.owner_id == "job-v2:invoke-v2",
                        ScientificArtifactReceipt.role == "landscape",
                    )
                )
            ).scalar_one_or_none()
            assert persisted is not None
            if authority == "landscape_source_hash":
                persisted.source_receipts_json = {
                    **persisted.source_receipts_json,
                    "source_sha256": "0" * 64,
                }
            else:
                persisted.row_count += 1
        await session.commit()
        counts = await _counts(session)

        with pytest.raises(module.FrustraMPNNConflictError, match="artifact|landscape"):
            await module.ingest_result_bundle(
                session,
                root,
                parent_job_id="job-v2",
                terminal_envelope=terminal,
            )
        assert await _counts(session) == counts == (1, 10, 0)


@pytest.mark.asyncio
async def test_v2_statistics_physical_tamper_fails_before_persistence(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _persistence()
    root = tmp_path / "v2-statistics-tamper"
    _, terminal = _v2_bundle(root, monkeypatch)
    await _seed_v2_child_job(db, root)
    statistics = _load_json(root, "frustrampnn_statistics_v1.json")
    statistics["target_id"] = "tampered-target"
    _write_json(root, "frustrampnn_statistics_v1.json", statistics)

    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError, match="manifest|statistics"):
            await module.ingest_result_bundle(
                session,
                root,
                parent_job_id="job-v2",
                terminal_envelope=terminal,
            )
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_v2_insert_failure_rolls_back_result_artifacts_and_rows(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _persistence()
    root = tmp_path / "v2-insert-rollback"
    _, terminal = _v2_bundle(root, monkeypatch)
    await _seed_v2_child_job(db, root)

    def fail_landscape_insert(*_args, **_kwargs):
        raise RuntimeError("injected v2 landscape insert failure")

    event.listen(ScientificArtifactReceipt, "before_insert", fail_landscape_insert)
    try:
        async with db() as session:
            with pytest.raises(module.FrustraMPNNPersistenceError, match="injected v2"):
                await module.ingest_result_bundle(
                    session,
                    root,
                    parent_job_id="job-v2",
                    terminal_envelope=terminal,
                )
            assert await _counts(session) == (0, 0, 0)
    finally:
        event.remove(ScientificArtifactReceipt, "before_insert", fail_landscape_insert)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["failed", "not_run"])
async def test_v2_non_success_terminal_never_persists_success(
    tmp_path: Path,
    db,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    module = _persistence()
    root = tmp_path / f"v2-{terminal_status}"
    _, terminal = _v2_bundle(root, monkeypatch)
    await _seed_v2_child_job(db, root)
    terminal["status"] = terminal_status
    terminal["failure_class"] = "inference_nonzero_exit"
    terminal["diagnostic"] = f"terminal was {terminal_status}"
    terminal["result_manifest"] = None
    terminal["result_payload"] = None
    _write_json(root, "workflow_component_result_v2.json", terminal)

    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError, match="result|success|manifest"):
            await module.ingest_result_bundle(
                session,
                root,
                parent_job_id="job-v2",
                terminal_envelope=terminal,
            )
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_identical_replay_returns_existing_and_preserves_historical_design_fields(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle", source_artifact_id="design-1")
    await _seed_job(
        db, design_id="design-1", design_pdb_path=tmp_path / "design-1.pdb"
    )

    async with db() as session:
        first = await module.ingest_result_bundle(
            session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
        )
        created_at = first.created_at
        counts = await _counts(session)
        design = await session.get(Design, "design-1")
        assert design.frustration_high_count is None
        assert design.frustration_min_count is None
        assert design.frustration_pct_high is None
        assert design.frustration_residues is None
        assert design.frustration_csv_path is None
        assert design.frustrampnn_contract_version == "1.0"
        assert design.frustrampnn_status == "succeeded"
        assert design.frustrampnn_source_sha256 == first.source_artifact_sha256
        assert design.frustrampnn_manifest_relpath == "frustrampnn_result_manifest_v1.json"
        assert design.frustrampnn_landscape_relpath == "frustrampnn_landscape_v1.json"
        assert design.frustrampnn_summary_relpath == "frustrampnn_summary_v1.json"
        assert design.frustrampnn_runtime_sha256 == first.runtime_identity_json["sif_sha256"]
        design.frustration_high_count = 99
        await session.commit()

        replay = await module.ingest_result_bundle(
            session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
        )
        await session.refresh(design)
        assert replay.invocation_id == first.invocation_id
        assert replay.created_at == created_at
        assert await _counts(session) == counts == (1, 10, 0)
        assert design.frustration_high_count == 99
        assert design.frustration_min_count is None
        assert design.frustration_pct_high is None


@pytest.mark.asyncio
async def test_same_local_invocation_id_is_isolated_by_parent_job(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal_one = _bundle(
        tmp_path / "job-one", source_artifact_id="design-1"
    )
    _bundle(tmp_path / "job-two", source_artifact_id="design-2")
    _, terminal_two = _retarget_bundle(
        tmp_path / "job-two",
        parent_job_id="job-2",
        candidate_id="candidate-2",
        design_id="design-2",
    )
    await _seed_job(
        db,
        job_id="job-1",
        design_id="design-1",
        design_pdb_path=tmp_path / "design-1.pdb",
    )
    await _seed_job(
        db,
        job_id="job-2",
        design_id="design-2",
        design_pdb_path=tmp_path / "design-2.pdb",
    )

    async with db() as session:
        first = await module.ingest_result_bundle(
            session,
            tmp_path / "job-one",
            parent_job_id="job-1",
            terminal_envelope=terminal_one,
        )
        second = await module.ingest_result_bundle(
            session,
            tmp_path / "job-two",
            parent_job_id="job-2",
            terminal_envelope=terminal_two,
        )
        assert first.invocation_id == second.invocation_id == "invoke-1"
        assert first.parent_job_id == "job-1"
        assert second.parent_job_id == "job-2"
        assert (
            await session.execute(select(func.count()).select_from(FrustraMPNNResult))
        ).scalar_one() == 2
        artifact_ids_by_job = {}
        row_ids_by_job = {}
        for job_id in ("job-1", "job-2"):
            artifact_ids_by_job[job_id] = set(
                (
                    await session.execute(
                        select(FrustraMPNNArtifact.artifact_id).where(
                            FrustraMPNNArtifact.parent_job_id == job_id,
                            FrustraMPNNArtifact.invocation_id == "invoke-1",
                        )
                    )
                ).scalars()
            )
            row_ids_by_job[job_id] = {
                row["id"]
                for row in await module.paged_landscape(
                    session,
                    job_id,
                    "invoke-1",
                    limit=20,
                )
            }
        assert len(artifact_ids_by_job["job-1"]) == len(artifact_ids_by_job["job-2"]) == 10
        assert len(row_ids_by_job["job-1"]) == len(row_ids_by_job["job-2"]) == 20
        assert artifact_ids_by_job["job-1"].isdisjoint(artifact_ids_by_job["job-2"])
        assert row_ids_by_job["job-1"].isdisjoint(row_ids_by_job["job-2"])
        replay_one = await module.ingest_result_bundle(
            session,
            tmp_path / "job-one",
            parent_job_id="job-1",
            terminal_envelope=terminal_one,
        )
        replay_two = await module.ingest_result_bundle(
            session,
            tmp_path / "job-two",
            parent_job_id="job-2",
            terminal_envelope=terminal_two,
        )
        assert replay_one.parent_job_id == "job-1"
        assert replay_two.parent_job_id == "job-2"
        assert await _counts(session) == (2, 20, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator",
    [
        "request",
        "source",
        "runtime",
        "gpu",
        "artifact",
        "landscape_summary",
    ],
)
async def test_changed_immutable_closure_conflicts_without_modification(
    tmp_path: Path, db, monkeypatch, mutator: str
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "original")
    _, changed_terminal = _bundle(tmp_path / "changed")
    await _seed_job(db)

    async with db() as session:
        original = await module.ingest_result_bundle(
            session, tmp_path / "original", parent_job_id="job-1", terminal_envelope=terminal
        )
        original_manifest_sha = original.manifest_sha256
        original_counts = await _counts(session)

    root = tmp_path / "changed"
    if mutator == "request":
        request = _load_json(root, "workflow_component_request_v1.json")
        request["source_artifact"]["producer_stage"] = "prediction:changed"
        _write_json(root, "workflow_component_request_v1.json", request)
    elif mutator == "source":
        changed_sha = "2" * 64
        request = _load_json(root, "workflow_component_request_v1.json")
        request["source_artifact"]["sha256"] = changed_sha
        _write_json(root, "workflow_component_request_v1.json", request)
        structure = _load_json(root, "frustrampnn_structure_map_v1.json")
        structure["source_sha256"] = changed_sha
        structure["authority_artifact_sha256"] = changed_sha
        _write_json(root, "frustrampnn_structure_map_v1.json", structure)
        receipt = _load_json(root, "frustrampnn_execution_receipt_v1.json")
        receipt["input_sha256"] = changed_sha
        _write_json(root, "frustrampnn_execution_receipt_v1.json", receipt)
    elif mutator == "runtime":
        from services.frustrampnn import runtime

        changed_identity = replace(
            runtime.FRUSTRAMPNN_RUNTIME_IDENTITY,
            image_version="1.4",
        )
        monkeypatch.setattr(
            runtime, "FRUSTRAMPNN_RUNTIME_IDENTITY", changed_identity
        )
        receipt = _load_json(root, "frustrampnn_execution_receipt_v1.json")
        receipt["software_versions"]["image"] = "1.4"
        _write_json(root, "frustrampnn_execution_receipt_v1.json", receipt)
    elif mutator == "gpu":
        receipt = _load_json(root, "frustrampnn_execution_receipt_v1.json")
        receipt["assigned_physical_gpu_id"] = "2"
        receipt["argv"] = [
            "CUDA_VISIBLE_DEVICES=2" if value == "CUDA_VISIBLE_DEVICES=3" else value
            for value in receipt["argv"]
        ]
        _write_json(root, "frustrampnn_execution_receipt_v1.json", receipt)
        result = _load_json(root, "workflow_component_result_v1.json")
        result["assigned_gpu"]["physical_device_id"] = "2"
        _write_json(root, "workflow_component_result_v1.json", result)
    elif mutator == "artifact":
        (root / "frustrampnn_stdout.log").write_bytes(b"different exact stdout\n")
    else:
        landscape = _load_json(root, "frustrampnn_landscape_v1.json")
        landscape["residues"][0]["slots"][0]["score"] = 0.1
        _write_json(root, "frustrampnn_landscape_v1.json", landscape)
        raw_path = root / "raw_frustrampnn.csv"
        with raw_path.open(newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.reader(handle))
        raw_rows[1][0] = "0.1"
        with raw_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerows(raw_rows)
        structure = _load_json(root, "frustrampnn_structure_map_v1.json")
        summary = summarize_landscape(landscape, structure)
        _write_json(root, "frustrampnn_summary_v1.json", summary)

    _, changed_terminal = _republish(root)
    async with db() as session:
        expected_error = (
            module.FrustraMPNNPersistenceError
            if mutator == "source"
            else module.FrustraMPNNConflictError
        )
        with pytest.raises(expected_error):
            await module.ingest_result_bundle(
                session,
                root,
                parent_job_id="job-1",
                terminal_envelope=changed_terminal,
            )
        existing = await session.get(FrustraMPNNResult, ("job-1", "invoke-1"))
        assert existing is not None
        assert existing.manifest_sha256 == original_manifest_sha
        assert await _counts(session) == original_counts


@pytest.mark.asyncio
async def test_cross_job_terminal_envelope_rolls_back_without_rows(tmp_path: Path, db) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle")
    await _seed_job(db)
    foreign = copy.deepcopy(terminal)
    foreign["parent_job_id"] = "job-other"

    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError, match="terminal envelope"):
            await module.ingest_result_bundle(
                session,
                tmp_path / "bundle",
                parent_job_id="job-1",
                terminal_envelope=foreign,
            )
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_fuzzy_design_name_fails_without_rewriting_legacy_fields(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle")
    await _seed_job(db, design_id="unrelated-design", design_name="candidate-1")

    async with db() as session:
        design = await session.get(Design, "unrelated-design")
        design.frustration_high_count = 7
        design.frustration_csv_path = "/legacy.csv"
        await session.commit()
        with pytest.raises(module.FrustraMPNNPersistenceError, match="persisted Design"):
            await module.ingest_result_bundle(
                session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
            )
        await session.refresh(design)
        assert design.frustration_high_count == 7
        assert design.frustration_csv_path == "/legacy.csv"
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_exact_source_id_without_matching_physical_hash_fails_without_writes(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle", source_artifact_id="design-1")
    wrong = tmp_path / "wrong.pdb"
    await _seed_job(db, design_id="design-1", design_pdb_path=wrong)
    wrong.write_bytes(b"END\n")
    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError, match="physical source"):
            await module.ingest_result_bundle(
                session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
            )
        design = await session.get(Design, "design-1")
        assert design.frustrampnn_status is None
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_exact_authoritative_design_link_updates_canonical_status_without_legacy_writes(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle", source_artifact_id="design-1")
    await _seed_job(
        db, design_id="design-1", design_pdb_path=tmp_path / "design-1.pdb"
    )

    async with db() as session:
        result = await module.ingest_result_bundle(
            session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
        )
        design = await session.get(Design, "design-1")
        assert result.design_id == "design-1"
        assert design.frustration_high_count is None
        assert design.frustration_min_count is None
        assert design.frustration_pct_high is None
        assert design.frustration_csv_path is None
        assert design.frustration_residues is None
        assert design.frustrampnn_contract_version == "1.0"
        assert design.frustrampnn_status == "succeeded"
        assert design.frustrampnn_source_sha256 == result.source_artifact_sha256
        assert design.frustrampnn_runtime_sha256 == result.runtime_identity_json["sif_sha256"]


@pytest.mark.asyncio
async def test_missing_exact_design_rejects_then_clean_retry_attaches(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(
        tmp_path / "bundle", source_artifact_id="future-design"
    )
    await _seed_job(db)

    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError, match="persisted Design"):
            await module.ingest_result_bundle(
                session,
                tmp_path / "bundle",
                parent_job_id="job-1",
                terminal_envelope=terminal,
            )
        assert await _counts(session) == (0, 0, 0)
        future_pdb = tmp_path / "future.pdb"
        future_pdb.write_bytes(MANIFEST_FIXTURE._pdb())
        session.add(
            Design(
                id="future-design",
                job_id="job-1",
                name="future",
                pdb_path=str(future_pdb),
            )
        )
        await session.commit()

        accepted = await module.ingest_result_bundle(
            session,
            tmp_path / "bundle",
            parent_job_id="job-1",
            terminal_envelope=terminal,
        )
        design = await session.get(Design, "future-design")
        assert accepted.design_id == "future-design"
        assert design.frustrampnn_status == "succeeded"


def test_post_validation_bundle_mutation_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle")
    original_validate = module.validate_result_manifest
    calls = 0

    def mutate_after_first_validation(root, manifest):
        nonlocal calls
        calls += 1
        payloads = original_validate(root, manifest)
        if calls == 1:
            (tmp_path / "bundle" / "late-unmanifested.txt").write_text(
                "hostile", encoding="utf-8"
            )
        return payloads

    monkeypatch.setattr(module, "validate_result_manifest", mutate_after_first_validation)
    with pytest.raises(module.FrustraMPNNPersistenceError, match="changed|validation|path set"):
        module.load_and_validate_result_bundle(
            tmp_path / "bundle",
            expected_parent_job_id="job-1",
            terminal_envelope=terminal,
        )


@pytest.mark.asyncio
async def test_cross_job_exact_design_artifact_is_rejected(tmp_path: Path, db) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle", source_artifact_id="foreign-design")
    await _seed_job(db)
    await _seed_job(db, job_id="job-other", design_id="foreign-design")

    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError, match="authorized job"):
            await module.ingest_result_bundle(
                session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
            )
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["noncanonical_manifest", "missing_artifact", "symlink"])
async def test_invalid_physical_bundle_leaves_zero_persistence_rows(
    tmp_path: Path, db, damage: str
) -> None:
    module = _persistence()
    manifest, terminal = _bundle(tmp_path / "bundle")
    await _seed_job(db)
    root = tmp_path / "bundle"
    if damage == "noncanonical_manifest":
        (root / MANIFEST_PATH).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    elif damage == "missing_artifact":
        (root / "frustrampnn_stdout.log").unlink()
    else:
        target = tmp_path / "elsewhere.log"
        target.write_bytes(b"model stdout\n")
        (root / "frustrampnn_stdout.log").unlink()
        (root / "frustrampnn_stdout.log").symlink_to(target)

    async with db() as session:
        with pytest.raises(module.FrustraMPNNPersistenceError):
            await module.ingest_result_bundle(
                session, root, parent_job_id="job-1", terminal_envelope=terminal
            )
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_insert_failure_rolls_back_result_artifacts_rows_and_legacy_projection(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle", source_artifact_id="design-1")
    await _seed_job(db, design_id="design-1")

    def fail_landscape_insert(*_args, **_kwargs):
        raise RuntimeError("injected landscape insert failure")

    event.listen(ScientificArtifactReceipt, "before_insert", fail_landscape_insert)
    try:
        async with db() as session:
            with pytest.raises(module.FrustraMPNNPersistenceError, match="injected"):
                await module.ingest_result_bundle(
                    session,
                    tmp_path / "bundle",
                    parent_job_id="job-1",
                    terminal_envelope=terminal,
                )
            assert await _counts(session) == (0, 0, 0)
            design = await session.get(Design, "design-1")
            assert design.frustration_high_count is None
            assert design.frustration_csv_path is None
    finally:
        event.remove(ScientificArtifactReceipt, "before_insert", fail_landscape_insert)


@pytest.mark.asyncio
async def test_commit_false_flushes_for_caller_owned_transaction_without_committing(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle")
    await _seed_job(db)

    async with db() as session:
        result = await module.ingest_result_bundle(
            session,
            tmp_path / "bundle",
            parent_job_id="job-1",
            terminal_envelope=terminal,
            commit=False,
        )
        assert result.invocation_id == "invoke-1"
        assert await _counts(session) == (1, 10, 0)
        await session.rollback()

    async with db() as session:
        assert await _counts(session) == (0, 0, 0)


@pytest.mark.asyncio
async def test_read_projections_are_ordered_and_never_expose_storage_paths(
    tmp_path: Path, db
) -> None:
    module = _persistence()
    _, terminal = _bundle(tmp_path / "bundle")
    await _seed_job(db)
    async with db() as session:
        await module.ingest_result_bundle(
            session, tmp_path / "bundle", parent_job_id="job-1", terminal_envelope=terminal
        )
        projection = await module.get_result_projection(session, "job-1", "invoke-1")
        artifacts = await module.list_result_artifacts(session, "job-1", "invoke-1")
        rows = await module.paged_landscape(
            session, "job-1", "invoke-1", limit=5, offset=0
        )
        duplicated_sql_rows = int(
            (
                await session.execute(
                    select(func.count()).select_from(FrustraMPNNLandscapeRow)
                )
            ).scalar_one()
        )

    assert duplicated_sql_rows == 0
    assert projection["invocation_id"] == "invoke-1"
    assert projection["manifest_sha256"]
    assert all("storage_path" not in artifact for artifact in artifacts)
    assert [row["mutation_aa"] for row in rows] == sorted(
        row["mutation_aa"] for row in rows
    )
    assert len(rows) == 5
