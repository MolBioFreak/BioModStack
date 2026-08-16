from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base,
    Design,
    FrustraMPNNArtifact,
    FrustraMPNNResult,
    Job,
    get_session,
)
from routers.frustrampnn import FrustraMPNNHistoricalSummaryV1Document, router
from services.frustrampnn.analytics import comparison_compatibility_id
from services.frustrampnn.contracts import canonical_json_bytes, load_schema
from services.frustrampnn.manifests import MANIFEST_PATH, build_result_manifest
from services.frustrampnn.persistence import ingest_result_bundle
from services.frustrampnn.settings import resolve_effective_settings


TESTS_DIR = Path(__file__).resolve().parent


def _fixture_module():
    name = "_frustrampnn_manifest_fixture_for_results_api"
    path = TESTS_DIR / "test_frustrampnn_manifests.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MANIFEST_FIXTURE = _fixture_module()


def test_historical_summary_accepts_only_the_persisted_transitional_policy_identity(tmp_path: Path) -> None:
    root = tmp_path / "historical-summary"
    root.mkdir()
    MANIFEST_FIXTURE._bundle(root)
    summary = json.loads((root / "frustrampnn_summary_v1.json").read_text(encoding="utf-8"))
    transitional_policy = {"id": "frustrampnn_threshold_v1", "high_max": -1.0, "minimal_min": 0.58}
    summary["threshold_policy"] = transitional_policy
    summary["threshold_policy_sha256"] = hashlib.sha256(canonical_json_bytes(transitional_policy)).hexdigest()
    parsed = FrustraMPNNHistoricalSummaryV1Document.model_validate(summary)
    assert parsed.root == summary

    unsafe = json.loads(json.dumps(summary))
    unsafe["threshold_policy"]["minimal_min"] = 0.59
    with pytest.raises(ValueError):
        FrustraMPNNHistoricalSummaryV1Document.model_validate(unsafe)


def _bundle(
    root: Path,
    *,
    parent_job_id: str = "job-1",
    candidate_id: str = "candidate-1",
    design_id: str = "design-1",
) -> dict:
    root.mkdir(parents=True)
    MANIFEST_FIXTURE._bundle(root)
    replacements = {
        "job-1": parent_job_id,
        "candidate-1": candidate_id,
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
        payload = replace_identity(json.loads(path.read_text(encoding="utf-8")))
        assert isinstance(payload, dict)
        if path.name == "workflow_component_request_v1.json":
            payload["source_artifact"]["artifact_id"] = design_id
        path.write_bytes(canonical_json_bytes(payload))
    MANIFEST_FIXTURE._rehash_bundle(root)
    manifest = build_result_manifest(root)
    (root / MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    return json.loads((root / "workflow_component_result_v1.json").read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def api(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'results-api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    root = tmp_path / "candidate_bundle"
    terminal = _bundle(root)
    second_root = tmp_path / "candidate_bundle_job_2"
    second_terminal = _bundle(
        second_root,
        parent_job_id="job-2",
        candidate_id="candidate-2",
        design_id="design-2",
    )
    pdb = tmp_path / "design-1.pdb"
    pdb.write_bytes(MANIFEST_FIXTURE._pdb())
    second_pdb = tmp_path / "design-2.pdb"
    second_pdb.write_bytes(MANIFEST_FIXTURE._pdb())
    async with sessions() as session:
        for job_id in ("job-1", "job-2"):
            session.add(
                Job(
                    id=job_id,
                    name=job_id,
                    status="completed",
                    queue_status="completed",
                    model_id="boltz2",
                    mode="predict",
                    params={},
                    output_dir=str(tmp_path),
                )
            )
        session.add(
            Design(
                id="design-1",
                job_id="job-1",
                name="candidate-1",
                pdb_path=str(pdb),
            )
        )
        session.add(
            Design(
                id="design-2",
                job_id="job-2",
                name="candidate-2",
                pdb_path=str(second_pdb),
            )
        )
        await session.commit()
        await ingest_result_bundle(
            session,
            root,
            parent_job_id="job-1",
            terminal_envelope=terminal,
        )
        await ingest_result_bundle(
            session,
            second_root,
            parent_job_id="job-2",
            terminal_envelope=second_terminal,
        )

    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with sessions() as session:
            async def forbidden_commit():
                raise AssertionError("GET endpoint attempted a database commit")

            session.commit = forbidden_commit  # type: ignore[method-assign]
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sessions, root
    await engine.dispose()


def _assert_no_server_path(value) -> None:
    if isinstance(value, dict):
        assert "storage_path" not in value
        for item in value.values():
            _assert_no_server_path(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_server_path(item)


def _assert_no_runtime_control_or_path(value) -> None:
    forbidden_parts = {"path", "argv", "storage", "scheduler", "configured_sif"}
    forbidden_keys = {"command", "commands", "command_plan"}
    if isinstance(value, dict):
        for key, child in value.items():
            assert key.lower() not in forbidden_keys, key
            assert not any(part in key.lower() for part in forbidden_parts), key
            _assert_no_runtime_control_or_path(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_runtime_control_or_path(child)
    elif isinstance(value, str):
        assert not value.startswith(("/", "./", "../")), value


PHASE4_FIELDS = [
    "settings_sha256",
    "effective_settings_sha256",
    "effective_settings_json",
    "capability_inventory_sha256",
    "statistics_sha256",
    "statistics_json",
    "comparison_compatibility_id",
]


def _effective_settings_fixture() -> dict:
    name = "_frustrampnn_source_fixture_for_results_api"
    path = TESTS_DIR / "test_frustrampnn_source_inspection.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return resolve_effective_settings(
        module._settings(), module.structure_map_fixture()
    ).model_dump(mode="json")


def _statistics_fixture() -> dict:
    name = "_frustrampnn_statistics_fixture_for_results_api"
    path = TESTS_DIR / "test_frustrampnn_statistics.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._build()


async def _mark_job_2_as_v2(sessions) -> dict:
    basis = {
        "schema_name": "frustrampnn_comparison_compatibility_basis",
        "schema_version": 1,
        "execution_configuration": {"configuration_sha256": "8" * 64},
    }
    compatibility_id = comparison_compatibility_id(basis)
    distribution = {
        "count": 2,
        "mean": 0.25,
        "median": 0.25,
        "sample_sd": 0.3535533905932738,
        "min": 0.0,
        "max": 0.5,
        "q1": 0.125,
        "q3": 0.375,
        "iqr": 0.25,
        "denominators": {
            name: {"kind": "finite_scoreable_values", "count": 2}
            for name in (
                "count",
                "mean",
                "median",
                "sample_sd",
                "min",
                "max",
                "q1",
                "q3",
                "iqr",
            )
        },
        "missingness_reasons": {
            name: None
            for name in (
                "count",
                "mean",
                "median",
                "sample_sd",
                "min",
                "max",
                "q1",
                "q3",
                "iqr",
            )
        },
    }
    burden = {
        "support_count": 2,
        "counts": {"high": 0, "neutral": 1, "minimal": 1},
        "fractions": {"high": 0.0, "neutral": 0.5, "minimal": 0.5},
        "denominator": {"kind": "finite_scoreable_values", "count": 2},
        "missingness_reason": None,
    }
    residue_key = {
        "entity_instance_id": "entity-2",
        "source_entity_id": "2",
        "label_asym_id": "B",
        "auth_asym_id": "B",
        "auth_seq_id": 7,
        "insertion_code": "",
        "sequence_index": 7,
        "wt": "A",
        "pdb_chain_id": "B",
        "model_position": 6,
    }
    statistics = {
        "schema_name": "frustrampnn_statistics",
        "schema_version": 1,
        "invocation_id": "invoke-1",
        "parent_job_id": "job-2",
        "candidate_id": "candidate-2",
        "target_id": "target-2",
        "landscape_sha256": "b" * 64,
        "configuration_sha256": "8" * 64,
        "comparison_compatibility_basis": basis,
        "comparison_compatibility_id": compatibility_id,
        "statistics_sha256": "7" * 64,
        "support": {
            "selected_residue_count": 1,
            "observed_residue_count": 1,
            "scoreable_residue_count": 1,
            "excluded_residue_count": 0,
            "missing_residue_count": 0,
            "expected_slot_count": 20,
            "observed_slot_count": 20,
            "scoreable_slot_count": 20,
        },
        "distributions": {"overall": distribution, "native": distribution},
        "class_burden": {"all": burden, "native": burden},
        "per_residue": [
            {
                **residue_key,
                "native_score": 0.0,
                "native_class": "neutral",
                "all": distribution,
                "alternative_class_burden": burden,
            }
        ],
        "per_mutation_amino_acid": [
            {
                "mutation_aa": "A",
                "distribution": distribution,
                "class_composition": burden,
            },
            {
                "mutation_aa": "C",
                "distribution": distribution,
                "class_composition": burden,
            },
        ],
        "per_chain": [
            {
                "entity_instance_id": "entity-2",
                "source_entity_id": "2",
                "label_asym_id": "B",
                "auth_asym_id": "B",
                "pdb_chain_id": "B",
                "support": {"selected_residue_count": 1, "observed_residue_count": 1},
                "all": distribution,
                "native": distribution,
            }
        ],
        "per_entity": [
            {
                "entity_instance_id": "entity-2",
                "source_entity_id": "2",
                "label_asym_id": "B",
                "support": {"selected_residue_count": 1, "observed_residue_count": 1},
                "all": distribution,
                "native": distribution,
            }
        ],
    }
    exact_statistics = _statistics_fixture()
    exact_statistics.update({
        "invocation_id": "invoke-1",
        "parent_job_id": "job-2",
        "candidate_id": "candidate-2",
        "target_id": "target-2",
    })
    exact_statistics["distributions"]["overall"] = distribution
    exact_statistics["class_burden"]["all"] = burden
    exact_statistics["per_residue"][0].update(residue_key)
    exact_statistics["per_residue"][0]["all"] = distribution
    exact_statistics["per_residue"][0]["alternative_class_burden"] = burden
    exact_statistics["per_chain"][0].update({
        "entity_instance_id": "entity-2",
        "source_entity_id": "2",
        "label_asym_id": "B",
        "auth_asym_id": "B",
        "pdb_chain_id": "B",
    })
    exact_statistics["per_entity"][0].update({
        "entity_instance_id": "entity-2",
        "source_entity_id": "2",
        "label_asym_id": "B",
    })
    exact_statistics["per_chain"] = [exact_statistics["per_chain"][0]]
    exact_statistics["per_entity"] = [exact_statistics["per_entity"][0]]
    statistics = exact_statistics
    compatibility_id = statistics["comparison_compatibility_id"]
    async with sessions() as session:
        result = await session.get(FrustraMPNNResult, ("job-2", "invoke-1"))
        assert result is not None
        terminal = dict(result.terminal_result_json)
        terminal["component_contract_version"] = "2.0"
        terminal.pop("runtime_identity", None)
        result.terminal_result_json = terminal
        result.runtime_identity_json = {
            "schema_name": "frustrampnn_execution_receipt",
            "schema_version": 2,
            "runtime_identity_sha256": "a" * 64,
            "command_plan": {"persisted_receipt_marker": True},
        }
        result.settings_sha256 = "4" * 64
        result.effective_settings_sha256 = "5" * 64
        result.effective_settings_json = _effective_settings_fixture()
        result.capability_inventory_sha256 = "6" * 64
        result.statistics_sha256 = statistics["statistics_sha256"]
        result.statistics_json = statistics
        result.comparison_compatibility_id = compatibility_id
        await session.commit()
    return statistics


@pytest.mark.asyncio
async def test_result_list_detail_and_artifact_metadata_are_job_scoped(api) -> None:
    client, _sessions, _root = api
    listed = await client.get(
        "/api/frustrampnn/jobs/job-1/results",
        params={"limit": 1, "offset": 0, "candidate_id": "candidate-1"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["limit"] == 1 and body["offset"] == 0 and body["total"] == 1
    assert [item["invocation_id"] for item in body["items"]] == ["invoke-1"]
    assert body["items"][0]["manifest_sha256"]
    assert body["items"][0]["request_sha256"]
    assert body["items"][0]["runtime_identity"]["checkpoint_sha256"]

    filtered = await client.get(
        "/api/frustrampnn/jobs/job-1/results",
        params={"candidate_id": "not-candidate-1"},
    )
    assert filtered.status_code == 200 and filtered.json()["total"] == 0

    detail = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-1"}
    )
    assert detail.status_code == 200
    assert detail.json()["source_artifact_id"] == "design-1"
    mismatch = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-other"}
    )
    assert mismatch.status_code == 404
    second_detail = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-2"}
    )
    assert second_detail.status_code == 200
    assert second_detail.json()["source_artifact_id"] == "design-2"

    artifacts = await client.get(
        "/api/frustrampnn/results/invoke-1/artifacts", params={"job_id": "job-1"}
    )
    assert artifacts.status_code == 200
    assert len(artifacts.json()["items"]) == 10
    artifact_items = artifacts.json()["items"]
    assert {item["role"] for item in artifact_items} >= {"raw_csv", "landscape"}
    assert all(
        set(item)
        == {
            "artifact_id",
            "role",
            "content_sha256",
            "size_bytes",
            "media_type",
            "schema_name",
            "schema_version",
            "cardinality",
            "download_url",
        }
        for item in artifact_items
    )
    assert all("relative_path" not in item and "invocation_id" not in item for item in artifact_items)
    assert all(
        item["download_url"]
        == f"/api/frustrampnn/artifacts/{item['artifact_id']}?job_id=job-1"
        for item in artifact_items
    )
    _assert_no_server_path(body)
    _assert_no_server_path(detail.json())
    _assert_no_server_path(artifacts.json())


@pytest.mark.asyncio
async def test_v2_statistics_returns_exact_persisted_receipt_and_audit_identity(api) -> None:
    client, sessions, _root = api
    expected_statistics = await _mark_job_2_as_v2(sessions)

    response = await client.get(
        "/api/frustrampnn/results/invoke-1/statistics",
        params={"job_id": "job-2"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "result_id": "invoke-1",
        "parent_job_id": "job-2",
        "candidate_id": "candidate-2",
        "invocation_id": "invoke-1",
        "authority_version": "v2",
        "availability": True,
        "missing_fields": [],
        "settings_sha256": "4" * 64,
        "effective_settings_sha256": "5" * 64,
        "effective_settings_json": _effective_settings_fixture(),
        "capability_inventory_sha256": "6" * 64,
        "statistics_sha256": expected_statistics["statistics_sha256"],
        "statistics_json": expected_statistics,
        "comparison_compatibility_id": expected_statistics[
            "comparison_compatibility_id"
        ],
        "statistics": expected_statistics,
    }


@pytest.mark.asyncio
async def test_historical_v1_statistics_is_explicitly_unavailable_without_defaults(api) -> None:
    client, _sessions, _root = api

    response = await client.get(
        "/api/frustrampnn/results/invoke-1/statistics",
        params={"job_id": "job-1"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authority_version"] == "historical_v1"
    assert body["availability"] is False
    assert body["statistics"] is None
    assert body["missing_fields"] == PHASE4_FIELDS
    assert all(
        body[field] is None
        for field in PHASE4_FIELDS
    )
    missing = await client.get(
        "/api/frustrampnn/results/invoke-1/statistics",
        params={"job_id": "job-other"},
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "FrustraMPNN result not found"}


@pytest.mark.asyncio
async def test_typed_statistics_query_reads_persisted_levels_filters_and_v1_missingness(api) -> None:
    client, sessions, _root = api
    await _mark_job_2_as_v2(sessions)
    references = [
        {"parent_job_id": "job-2", "invocation_id": "invoke-1"},
        {"parent_job_id": "job-1", "invocation_id": "invoke-1"},
    ]

    overview = await client.post(
        "/api/frustrampnn/statistics/query",
        json={"datasets": references, "level": "overview", "limit": 10, "offset": 0},
    )
    assert overview.status_code == 200, overview.text
    overview_body = overview.json()
    assert overview_body["total"] == 2
    available, unavailable = overview_body["items"]
    assert available["dataset"]["parent_job_id"] == "job-2"
    assert available["availability"] is True
    assert available["distribution"]["mean"] == 0.25
    assert available["class_burden"]["counts"] == {
        "high": 0,
        "neutral": 1,
        "minimal": 1,
    }
    assert unavailable["dataset"]["parent_job_id"] == "job-1"
    assert unavailable["availability"] is False
    assert unavailable["unavailable_reason"] == "historical_v1_statistics_unavailable"
    assert unavailable["distribution"] is None

    cases = [
        (
            "residue",
            {"auth_asym_id": "B", "auth_seq_id": 7, "insertion_code": ""},
            "auth_seq_id",
            7,
        ),
        ("mutation_aa", {"mutation_aa": "C"}, "mutation_aa", "C"),
        ("chain", {"auth_asym_id": "B", "pdb_chain_id": "B"}, "auth_asym_id", "B"),
        ("entity", {"entity_instance_id": "entity-2"}, "entity_instance_id", "entity-2"),
    ]
    for level, filters, key_name, key_value in cases:
        response = await client.post(
            "/api/frustrampnn/statistics/query",
            json={
                "datasets": [references[0]],
                "level": level,
                "filters": filters,
                "limit": 10,
                "offset": 0,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["level"] == level
        assert body["items"][0]["key"][key_name] == key_value


@pytest.mark.asyncio
async def test_statistics_query_is_bounded_closed_and_rejects_wrong_level_filters(api) -> None:
    client, _sessions, _root = api
    reference = {"parent_job_id": "job-1", "invocation_id": "invoke-1"}
    invalid_bodies = [
        {"datasets": [], "level": "overview"},
        {"datasets": [reference] * 51, "level": "overview"},
        {"datasets": [reference], "level": "overview", "limit": 501},
        {
            "datasets": [reference],
            "level": "residue",
            "filters": {"mutation_aa": "A"},
        },
        {
            "datasets": [reference],
            "level": "mutation_aa",
            "filters": {"auth_asym_id": "A"},
        },
        {"datasets": [reference], "level": "overview", "expression": "1 + 1"},
    ]
    for body in invalid_bodies:
        response = await client.post("/api/frustrampnn/statistics/query", json=body)
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_result_list_and_detail_expose_persisted_phase4_hashes(api) -> None:
    client, sessions, _root = api
    statistics = await _mark_job_2_as_v2(sessions)

    listed = await client.get("/api/frustrampnn/jobs/job-2/results")
    detail = await client.get(
        "/api/frustrampnn/results/invoke-1", params={"job_id": "job-2"}
    )

    assert listed.status_code == detail.status_code == 200
    for payload in (listed.json()["items"][0], detail.json()):
        assert payload["authority_version"] == "v2"
        assert payload["availability"] is True
        assert payload["statistics_available"] is True
        assert payload["missing_fields"] == []
        assert payload["settings_sha256"] == "4" * 64
        assert payload["effective_settings_sha256"] == "5" * 64
        assert payload["effective_settings_json"] == _effective_settings_fixture()
        assert payload["capability_inventory_sha256"] == "6" * 64
        assert payload["statistics_sha256"] == statistics["statistics_sha256"]
        assert payload["statistics_json"] == statistics
        assert (
            payload["comparison_compatibility_id"]
            == statistics["comparison_compatibility_id"]
        )
        assert "statistics" not in payload
        assert payload["runtime_identity"] == {
            "runtime_identity_sha256": "a" * 64,
        }
        assert payload["runtime_identity_sha256"] == "a" * 64
        assert "command_plan" not in payload["runtime_identity"]
        _assert_no_runtime_control_or_path(payload)
    assert "execution_receipt" not in listed.json()["items"][0]
    assert detail.json()["execution_receipt"] == {
        "schema_name": "frustrampnn_execution_receipt",
        "schema_version": 2,
        "invocation_id": "invoke-1",
        "execution_configuration_sha256": None,
        "requested_settings_sha256": "4" * 64,
        "effective_settings_sha256": "5" * 64,
        "runtime_identity_sha256": "a" * 64,
        "source_artifact_sha256": detail.json()["source_artifact_sha256"],
        "structure_map_sha256": None,
        "normalized_pdb_sha256": None,
        "command_count": None,
        "gpu_provenance": None,
        "started_at": None,
        "ended_at": None,
        "duration_seconds": None,
    }


def test_openapi_describes_statistics_response_and_comparison_override() -> None:
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()

    list_operation = schema["paths"]["/api/frustrampnn/jobs/{job_id}/results"]["get"]
    list_ref = list_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    list_schema = schema["components"]["schemas"][list_ref.rsplit("/", 1)[1]]
    assert list_schema["additionalProperties"] is False
    item_ref = list_schema["properties"]["items"]["items"]["$ref"]
    item_schema = schema["components"]["schemas"][item_ref.rsplit("/", 1)[1]]
    assert item_schema["additionalProperties"] is False
    assert "runtime_identity_sha256" in item_schema["properties"]

    detail_operation = schema["paths"]["/api/frustrampnn/results/{invocation_id}"]["get"]
    detail_ref = detail_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    detail_schema = schema["components"]["schemas"][detail_ref.rsplit("/", 1)[1]]
    assert detail_schema["additionalProperties"] is False
    assert "execution_receipt" in detail_schema["properties"]
    summary_schema = detail_schema["properties"]["summary"]
    summary_refs = {item["$ref"] for item in summary_schema["anyOf"]}
    assert summary_refs == {
        "#/components/schemas/FrustraMPNNSummaryV2Document",
        "#/components/schemas/FrustraMPNNHistoricalSummaryV1Document",
    }
    assert (
        schema["components"]["schemas"]["FrustraMPNNSummaryV2Document"]
        == load_schema("frustrampnn_summary_v2")
    )
    assert (
        schema["components"]["schemas"]["FrustraMPNNHistoricalSummaryV1Document"]
        == load_schema("frustrampnn_summary_v1")
    )

    for path in (
        "/api/frustrampnn/results/{invocation_id}/landscape",
        "/api/frustrampnn/results/{invocation_id}/artifacts",
    ):
        response_ref = schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        response_schema = schema["components"]["schemas"][response_ref.rsplit("/", 1)[1]]
        assert response_schema["additionalProperties"] is False

    for name in (
        "FrustraMPNNRuntimeIdentityResponse",
        "FrustraMPNNGpuProvenanceResponse",
        "FrustraMPNNTerminalResultResponse",
        "FrustraMPNNExecutionReceiptResponse",
        "FrustraMPNNLandscapeRowResponse",
        "FrustraMPNNArtifactResponse",
    ):
        assert schema["components"]["schemas"][name]["additionalProperties"] is False

    statistics_operation = schema["paths"][
        "/api/frustrampnn/results/{invocation_id}/statistics"
    ]["get"]
    statistics_ref = statistics_operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    statistics_schema = schema["components"]["schemas"][statistics_ref.rsplit("/", 1)[1]]
    assert statistics_schema["properties"]["availability"]["type"] == "boolean"
    assert statistics_schema["properties"]["statistics"]
    assert set(PHASE4_FIELDS) == set(
        statistics_schema["properties"]["missing_fields"]["items"]["enum"]
    )
    assert (
        schema["components"]["schemas"]["FrustraMPNNStatisticsDocument"]
        == load_schema("frustrampnn_statistics_v1")
    )
    for field in ("statistics_json", "statistics"):
        field_schema = statistics_schema["properties"][field]
        refs = [item.get("$ref") for item in field_schema["anyOf"]]
        assert "#/components/schemas/FrustraMPNNStatisticsDocument" in refs

    analytics_response = schema["paths"]["/api/frustrampnn/analytics/points"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert len(analytics_response["anyOf"]) == 3
    for response_variant in analytics_response["anyOf"]:
        response_name = response_variant["$ref"].rsplit("/", 1)[1]
        response_component = schema["components"]["schemas"][response_name]
        assert response_component["additionalProperties"] is False
        item_schema = response_component["properties"]["items"]["items"]
        item_name = item_schema["$ref"].rsplit("/", 1)[1]
        assert schema["components"]["schemas"][item_name]["additionalProperties"] is False

    for path, method, response_code in (
        ("/api/frustrampnn/guidance", "post", "201"),
        ("/api/frustrampnn/guidance/{guidance_id}", "get", "200"),
    ):
        operation = schema["paths"][path][method]
        response_ref = operation["responses"][response_code]["content"][
            "application/json"
        ]["schema"]["$ref"]
        response_component = schema["components"]["schemas"][
            response_ref.rsplit("/", 1)[1]
        ]
        assert response_component["additionalProperties"] is False
        assert response_component["properties"]["ranked_slots"]["items"]["$ref"]
    guidance_request = schema["components"]["schemas"]["GuidanceCreateRequest"]
    assert guidance_request["additionalProperties"] is False
    assert guidance_request["properties"]["region"]["discriminator"]["propertyName"] == (
        "region_type"
    )

    query_operation = schema["paths"]["/api/frustrampnn/statistics/query"]["post"]
    query_request = query_operation["requestBody"]["content"]["application/json"]["schema"]
    assert query_request["discriminator"]["propertyName"] == "level"
    for request_ref in query_request["oneOf"]:
        request_schema = schema["components"]["schemas"][request_ref["$ref"].rsplit("/", 1)[1]]
        assert request_schema["additionalProperties"] is False
    query_response_ref = query_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    query_response = schema["components"]["schemas"][query_response_ref.rsplit("/", 1)[1]]
    assert query_response["additionalProperties"] is False
    assert query_response["properties"]["limit"]["maximum"] == 500

    comparison_operation = schema["paths"]["/api/frustrampnn/comparisons"]["post"]
    request_ref = comparison_operation["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[1]]
    assert request_schema["properties"]["allow_incompatible"] == {
        "type": "boolean",
        "title": "Allow Incompatible",
        "default": False,
    }
    response_ref = comparison_operation["responses"]["201"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    response_schema = schema["components"]["schemas"][response_ref.rsplit("/", 1)[1]]
    assert response_schema["properties"]["compatibility_status"]["enum"] == [
        "compatible",
        "incompatible",
        "unknown",
    ]
    assert response_schema["properties"]["override_used"]["type"] == "boolean"
    assert response_schema["properties"]["compatibility_differences"]["type"] == "array"
    pair_row_ref = response_schema["properties"]["rows"]["items"]["$ref"]
    assert pair_row_ref.endswith("/PairComparisonRowResponse")
    assert schema["components"]["schemas"]["PairComparisonRowResponse"][
        "additionalProperties"
    ] is False

    multi_operation = schema["paths"]["/api/frustrampnn/comparisons/multi"]["post"]
    multi_request_ref = multi_operation["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    multi_request_schema = schema["components"]["schemas"][
        multi_request_ref.rsplit("/", 1)[1]
    ]
    assert multi_request_schema["properties"]["targets"]["minItems"] == 1
    assert multi_request_schema["properties"]["targets"]["maxItems"] == 8
    multi_response_ref = multi_operation["responses"]["201"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    multi_response_schema = schema["components"]["schemas"][
        multi_response_ref.rsplit("/", 1)[1]
    ]
    assert multi_response_schema["properties"]["rows"]["items"]["$ref"].endswith(
        "/MultiComparisonRowResponse"
    )
    assert schema["components"]["schemas"]["MultiComparisonRowResponse"][
        "additionalProperties"
    ] is False

    persisted_operation = schema["paths"][
        "/api/frustrampnn/comparisons/{comparison_id}"
    ]["get"]
    persisted_schema = persisted_operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    persisted_refs = [item["$ref"] for item in persisted_schema["anyOf"]]
    assert len(persisted_refs) == 2
    assert all(
        schema["components"]["schemas"][ref.rsplit("/", 1)[1]][
            "additionalProperties"
        ]
        is False
        for ref in persisted_refs
    )

    rows_operation = schema["paths"][
        "/api/frustrampnn/comparisons/{comparison_id}/rows"
    ]["get"]
    rows_ref = rows_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    rows_schema = schema["components"]["schemas"][rows_ref.rsplit("/", 1)[1]]
    assert rows_schema["additionalProperties"] is False
    assert rows_schema["properties"]["items"]["type"] == "array"
    row_variants = rows_schema["properties"]["items"]["items"]["anyOf"]
    assert {item["$ref"].rsplit("/", 1)[1] for item in row_variants} == {
        "PairComparisonRowResponse",
        "MultiComparisonRowResponse",
    }


@pytest.mark.asyncio
async def test_landscape_pagination_is_stable_bounded_and_exactly_filtered(api) -> None:
    client, _sessions, _root = api
    first = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 7, "offset": 0},
    )
    second = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 7, "offset": 7},
    )
    assert first.status_code == second.status_code == 200
    first_body, second_body = first.json(), second.json()
    assert first_body["total"] == second_body["total"] == 20
    assert len(first_body["items"]) == len(second_body["items"]) == 7
    assert set(item["id"] for item in first_body["items"]).isdisjoint(
        item["id"] for item in second_body["items"]
    )
    repeated = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 7, "offset": 0},
    )
    assert repeated.json()["items"] == first_body["items"]

    exact = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "mutation_aa": "G"},
    )
    assert exact.status_code == 200
    assert exact.json()["total"] == 1
    assert exact.json()["items"][0]["mutation_aa"] == "G"
    absent = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "auth_asym_id": "B"},
    )
    assert absent.status_code == 200 and absent.json()["total"] == 0
    oversized = await client.get(
        "/api/frustrampnn/results/invoke-1/landscape",
        params={"job_id": "job-1", "limit": 501},
    )
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_verified_artifact_download_supports_etag_ranges_and_416(api) -> None:
    client, sessions, root = api
    async with sessions() as session:
        artifact = (
            await session.execute(
                select(FrustraMPNNArtifact).where(
                    FrustraMPNNArtifact.parent_job_id == "job-1",
                    FrustraMPNNArtifact.role == "raw_csv",
                )
            )
        ).scalar_one()
        artifact_id = artifact.artifact_id
        expected_hash = artifact.content_sha256
    expected = (root / "raw_frustrampnn.csv").read_bytes()

    full = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}", params={"job_id": "job-1"}
    )
    assert full.status_code == 200
    assert full.content == expected
    assert hashlib.sha256(full.content).hexdigest() == expected_hash
    assert full.headers["etag"] == f'"{expected_hash}"'
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-length"] == str(len(expected))

    partial = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}",
        params={"job_id": "job-1"},
        headers={"Range": "bytes=2-11"},
    )
    assert partial.status_code == 206
    assert partial.content == expected[2:12]
    assert partial.headers["content-range"] == f"bytes 2-11/{len(expected)}"

    unsatisfied = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}",
        params={"job_id": "job-1"},
        headers={"Range": f"bytes={len(expected)}-"},
    )
    assert unsatisfied.status_code == 416
    assert unsatisfied.headers["content-range"] == f"bytes */{len(expected)}"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["hash", "symlink", "escape"])
async def test_download_fails_closed_for_changed_or_unsafe_artifact(api, mutation: str) -> None:
    client, sessions, root = api
    async with sessions() as session:
        artifact = (
            await session.execute(
                select(FrustraMPNNArtifact).where(
                    FrustraMPNNArtifact.parent_job_id == "job-1",
                    FrustraMPNNArtifact.role == "raw_csv",
                )
            )
        ).scalar_one()
        artifact_id = artifact.artifact_id
        if mutation == "escape":
            artifact.storage_path = str(root.parent / "outside.csv")
            await session.commit()
        else:
            target = Path(artifact.storage_path)
            if mutation == "hash":
                target.write_bytes(b"x" * artifact.size_bytes)
            else:
                replacement = root.parent / "replacement.csv"
                replacement.write_bytes(target.read_bytes())
                target.unlink()
                target.symlink_to(replacement)

    response = await client.get(
        f"/api/frustrampnn/artifacts/{artifact_id}", params={"job_id": "job-1"}
    )
    assert response.status_code == 409
