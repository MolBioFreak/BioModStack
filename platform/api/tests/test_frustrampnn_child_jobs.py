from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers.frustrampnn import AnalyzeDesignsRequest, ReanalyzeRequest, router
from services import nextflow as nextflow_service
from services.frustrampnn import jobs as child_jobs
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, default_settings


def _pdb() -> bytes:
    lines: list[str] = []
    for serial, (atom, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} GLY A   1    {serial:8.3f}{serial + 1:8.3f}"
            f"{serial + 2:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _pdb_for_chain(chain: str, residue: str = "GLY") -> bytes:
    lines: list[str] = []
    for serial, (atom, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} {residue} {chain}   1    {serial:8.3f}{serial + 1:8.3f}"
            f"{serial + 2:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _walk_public_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_public_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_public_keys(child)


def _multi_model_altloc_pdb() -> bytes:
    lines: list[str] = []
    serial = 1
    for model, residue, auth_seq, altloc in ((1, "GLY", 1, ""), (2, "ALA", 2, "B")):
        lines.append(f"MODEL     {model:4d}\n")
        for atom, element in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            atom_field = f" {atom:<3}"
            altloc_field = altloc or " "
            lines.append(
                f"ATOM  {serial:5d} {atom_field}{altloc_field}{residue} A{auth_seq:4d}    "
                f"{serial:8.3f}{serial + 1:8.3f}{serial + 2:8.3f}"
                f"{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
            )
            serial += 1
        lines.append("ENDMDL\n")
    return "".join(lines).encode("ascii") + b"END\n"


def _custom_settings() -> FrustraMPNNRequestedSettings:
    return FrustraMPNNRequestedSettings.model_validate(
        {
            "schema_name": "frustrampnn_settings",
            "schema_version": 2,
            "batching_enabled": True,
            "structures_per_job": 250,
            "protein_selection": {
                "mode": "selected_residues",
                "entities": [],
                "residues": [
                    {
                        "entity_instance_id": "pdb:A",
                        "source_entity_id": None,
                        "label_asym_id": None,
                        "auth_asym_id": "A",
                        "auth_seq_id": 2,
                        "insertion_code": "",
                        "sequence_index": 1,
                    }
                ],
            },
            "source_structure": {
                "selected_model_number": 2,
                "preferred_altloc": "B",
            },
            "classification_policy": {
                "mode": "custom",
                "high_max": -0.8,
                "minimal_min": 0.3,
            },
        }
    )


def _batched_settings(structures_per_job: int = 2) -> FrustraMPNNRequestedSettings:
    return default_settings().model_copy(
        update={
            "batching_enabled": True,
            "structures_per_job": structures_per_job,
        }
    )


@pytest_asyncio.fixture
async def child_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(child_jobs, "get_results_dir", lambda: results)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'children.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions, results
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_child_creation_commits_immutable_authority_and_builds_scheduler_handoff(
    child_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb",
        payload=_pdb(),
        expected_sha256=hashlib.sha256(_pdb()).hexdigest(),
    )
    async with sessions() as session:
        child = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=default_settings(),
        )
        child_id = child.id

    async with sessions() as session:
        persisted = await session.get(Job, child_id)
        assert persisted is not None
        assert persisted.status == persisted.queue_status == "queued"
        assert persisted.sequence_length == 1
        assert persisted.vram_estimate_mb is not None
        assert persisted.vram_estimate_mb > 0
        assert persisted.output_dir == persisted.child_output_dir
        root = Path(persisted.output_dir)
        assert root.parent == results
        envelope = persisted.params[child_jobs.ENVELOPE_KEY]
        assert envelope["execution_owner_job_id"] == child_id
        assert set(persisted.params) == {
            child_jobs.ENVELOPE_KEY,
            "frustrampnn_batch_manifest_path",
        }
        manifest_path = Path(persisted.params["frustrampnn_batch_manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["execution_owner_job_id"] == child_id
        record = manifest["records"][0]
        assert manifest["schema_name"] == "bms_frustrampnn_scheduler_batch"
        assert manifest["schema_version"] == 3
        assert manifest["batching_enabled"] is False
        assert manifest["structures_per_job"] == 1
        assert manifest["settings_sha256"] == envelope["settings_sha256"]
        assert manifest["expected_cardinality"] == 1
        assert record["record_schema_name"] == "bms_frustrampnn_scheduler_record"
        assert record["record_schema_version"] == 2
        assert set(record) == {
            "record_schema_name",
            "record_schema_version",
            "ordinal",
            "candidate_id",
            "invocation_id",
            "request_relative_path",
            "request_sha256",
            "request_size_bytes",
            "source_relative_path",
            "source_sha256",
            "source_size_bytes",
            "structure_map_relative_path",
            "structure_map_sha256",
            "structure_map_size_bytes",
        }
        assert "launch_authority" not in record
        request_path = root / record["request_relative_path"]
        source_path = root / record["source_relative_path"]
        structure_map_path = root / record["structure_map_relative_path"]
        assert request_path.name == "workflow_component_request_v3.json"
        assert source_path.name == "canonical_source.pdb"
        assert structure_map_path.name == "frustrampnn_structure_map_v1.json"
        assert stat.S_IMODE(request_path.stat().st_mode) == 0o444
        assert stat.S_IMODE(source_path.stat().st_mode) == 0o444
        assert stat.S_IMODE(structure_map_path.stat().st_mode) == 0o444
        assert hashlib.sha256(request_path.read_bytes()).hexdigest() == record["request_sha256"]
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == record["source_sha256"]
        assert hashlib.sha256(structure_map_path.read_bytes()).hexdigest() == record[
            "structure_map_sha256"
        ]
        request = json.loads(request_path.read_text(encoding="utf-8"))
        structure_map = json.loads(structure_map_path.read_text(encoding="utf-8"))
        child_jobs.validate_schema("workflow_component_request_v3", request)
        assert request["schema_version"] == 3
        assert request["component_contract_version"] == "3.0"
        assert request["parent_job_id"] == child_id
        assert request["parent_workflow_id"] == "frustrampnn_analysis"
        assert request["candidate_id"] == record["candidate_id"] == structure_map["candidate_id"]
        assert request["invocation_id"] == record["invocation_id"]
        assert request["source_artifact"]["sha256"] == selection.source_sha256
        assert request["source_artifact"]["relative_path"] == envelope["selection"][0][
            "snapshot_relative_path"
        ]
        original_path = root / request["source_artifact"]["relative_path"]
        assert original_path.read_bytes() == selection.source_bytes
        assert hashlib.sha256(original_path.read_bytes()).hexdigest() == request[
            "source_artifact"
        ]["sha256"]
        assert request["source_artifact"]["relative_path"] != record[
            "source_relative_path"
        ]
        assert request["normalized_pdb_sha256"] == record["source_sha256"]
        assert request["structure_map_sha256"] == record["structure_map_sha256"]
        effective = request["effective_settings"]
        resolution = effective["resolution_identity"]
        configuration = request["execution_configuration"]
        assert request["requested_settings"] == effective["requested_settings"]
        assert request["requested_settings_sha256"] == effective["settings_sha256"]
        assert request["effective_settings_sha256"] == effective[
            "effective_settings_sha256"
        ]
        assert request["classification_policy_sha256"] == effective[
            "threshold_policy_sha256"
        ]
        assert request["capability_inventory_byte_sha256"] == effective[
            "capability_inventory_byte_sha256"
        ]
        assert resolution["source_artifact_sha256"] == request["source_artifact"][
            "sha256"
        ]
        assert resolution["structure_map_sha256"] == request["structure_map_sha256"]
        assert resolution["normalized_pdb_sha256"] == request["normalized_pdb_sha256"]
        assert configuration["effective_settings"] == effective
        for field in (
            "requested_settings_sha256",
            "effective_settings_sha256",
            "classification_policy_sha256",
            "capability_inventory_byte_sha256",
            "runtime_identity_sha256",
            "structure_map_sha256",
            "normalized_pdb_sha256",
        ):
            assert request[field] == configuration[field]
        assert request["execution_configuration_sha256"] == configuration[
            "configuration_sha256"
        ]
        assert structure_map["source_sha256"] == request["source_artifact"]["sha256"]
        assert structure_map["normalized_pdb_sha256"] == record["source_sha256"]
        assert structure_map["parent_job_id"] == structure_map["target_id"] == child_id
        assert persisted.stage_outputs["canonical_frustrampnn"] == [
            str(root / "frustrampnn" / "results" / "upload-1" / "frustrampnn_result_manifest_v3.json"),
            str(root / "frustrampnn" / "results" / "upload-1" / "workflow_component_result_v3.json"),
        ]

        monkeypatch.setattr(nextflow_service, "resolve_nextflow_executable", lambda: "/opt/nextflow-25.10.1")
        command = nextflow_service.build_nextflow_command(
            model_id=persisted.model_id,
            mode=persisted.mode,
            params={**persisted.params, "gpu_id": 2},
            output_dir=persisted.output_dir,
            job_id=child_id,
        )
        assert command[:3] == [
            "/opt/nextflow-25.10.1",
            "run",
            "workflows/frustrampnn_analysis.nf",
        ]
        assert command[command.index("--frustrampnn_physical_gpu_id") + 1] == "2"
        assert command[command.index("--job_id") + 1] == child_id


@pytest.mark.asyncio
async def test_child_creation_commit_failure_removes_attempt_and_persists_no_job(child_db) -> None:
    sessions, results = child_db
    selection = child_jobs.upload_selection(filename="candidate.pdb", payload=_pdb(), expected_sha256=None)
    async with sessions() as session:
        async def fail_commit() -> None:
            raise RuntimeError("injected commit failure")

        session.commit = fail_commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="injected commit failure"):
            await child_jobs.create_child_job(
                session,
                selections=[selection],
                source_parent=None,
                trigger="upload_analyze",
                requested_settings=default_settings(),
            )
        await session.rollback()

    async with sessions() as session:
        assert (await session.execute(select(func.count(Job.id)))).scalar_one() == 0
    assert list(results.iterdir()) == []


def test_retired_batch_completion_owner_cannot_be_called() -> None:
    trigger_name = "maybe_trigger_batch_" + "frustrampnn"
    runner_name = "run_batch_" + "frustrampnn"
    assert not hasattr(nextflow_service, trigger_name)
    assert not hasattr(nextflow_service, runner_name)


def test_request_models_forbid_runtime_and_path_overrides() -> None:
    with pytest.raises(ValidationError):
        ReanalyzeRequest.model_validate({"gpu_id": 0})
    with pytest.raises(ValidationError):
        AnalyzeDesignsRequest.model_validate(
            {
                "selections": [{"design_id": "d1", "source_sha256": "a" * 64}],
                "output_dir": "/tmp/caller-owned",
            }
        )


@pytest.mark.asyncio
async def test_upload_router_returns_persisted_receipt_and_rejects_unknown_fields(child_db) -> None:
    sessions, _results = child_db
    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/jobs/uploads/analyze",
            files={"pdb_file": ("candidate.pdb", _pdb(), "chemical/x-pdb")},
        )
        assert response.status_code == 202
        child_id = response.json()["job_id"]
        forbidden = await client.post(
            "/api/frustrampnn/jobs/uploads/analyze?gpu_id=0",
            files={"pdb_file": ("candidate.pdb", _pdb(), "chemical/x-pdb")},
        )
        assert forbidden.status_code == 422

    async with sessions() as session:
        assert await session.get(Job, child_id) is not None


@pytest.mark.asyncio
async def test_child_receipt_is_closed_redacted_projection_without_mutating_internal_envelope(
    child_db,
) -> None:
    sessions, _results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb", payload=_pdb(), expected_sha256=None
    )
    async with sessions() as session:
        child = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=default_settings(),
        )
        child_id = str(child.id)
        internal_before = json.loads(json.dumps(child.params[child_jobs.ENVELOPE_KEY]))
        receipt = await child_jobs.child_receipt(session, child=child)
        assert child.params[child_jobs.ENVELOPE_KEY] == internal_before

    assert receipt["child_job_id"] == receipt["job_id"] == receipt["result_job_id"] == child_id
    assert receipt["name"].startswith("FrustraMPNN analysis ")
    assert receipt["trigger"] == "upload_analyze"
    assert receipt["settings_value_origin"] == "bms_default"
    assert receipt["requested_settings"] == default_settings().model_dump(mode="json")
    assert receipt["requested_settings_sha256"] == internal_before["settings_sha256"]
    assert len(receipt["candidates"]) == 1
    candidate = receipt["candidates"][0]
    authority = internal_before["selection"][0]["launch_authority"]
    assert candidate["candidate_id"] == "upload-1"
    assert candidate["source_artifact_sha256"] == selection.source_sha256
    assert candidate["normalized_pdb_sha256"] is not None
    assert candidate["normalized_pdb_sha256"] == authority["normalized_pdb_sha256"]
    assert candidate["normalized_pdb_sha256"] == authority["effective_settings"][
        "resolution_identity"
    ]["normalized_pdb_sha256"]
    assert candidate["requested_settings_sha256"] == authority["settings_sha256"]
    assert candidate["effective_settings"] == authority["effective_settings"]
    assert candidate["effective_settings_sha256"] == authority["effective_settings_sha256"]
    assert candidate["execution_configuration_sha256"] == authority["configuration_sha256"]
    assert candidate["capability_inventory_byte_sha256"] == authority["effective_settings"][
        "capability_inventory_byte_sha256"
    ]
    assert candidate["runtime_identity_sha256"] == authority["execution_configuration"][
        "runtime_identity_sha256"
    ]

    forbidden_key_parts = {"path", "command", "scheduler", "storage", "output_dir"}
    for key in _walk_public_keys(receipt):
        assert not any(part in key.lower() for part in forbidden_key_parts), key
        assert key != "execution_configuration"
    serialized = json.dumps(receipt, default=str)
    assert "/" not in serialized
    assert "frustrampnn_batch_manifest_path" not in serialized


def test_child_launch_and_receipt_routes_publish_closed_response_models() -> None:
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    operations = [
        ("/api/frustrampnn/jobs/uploads/analyze", "post", 202),
        ("/api/frustrampnn/candidates/handoff", "post", 202),
        ("/api/frustrampnn/jobs/{parent_job_id}/analyze", "post", 202),
        ("/api/frustrampnn/jobs/{child_job_id}/reanalyze", "post", 202),
        ("/api/frustrampnn/jobs/{child_job_id}/receipt", "get", 200),
    ]
    for path, method, status_code in operations:
        operation = schema["paths"][path][method]
        response_schema = operation["responses"][str(status_code)]["content"][
            "application/json"
        ]["schema"]
        ref = response_schema["$ref"]
        model_schema = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert model_schema["additionalProperties"] is False

    receipt_ref = schema["paths"]["/api/frustrampnn/jobs/{child_job_id}/receipt"][
        "get"
    ]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    receipt_schema = schema["components"]["schemas"][receipt_ref.rsplit("/", 1)[1]]
    assert "lineage" not in receipt_schema["properties"]
    assert "assigned_gpu" not in receipt_schema["properties"]
    assert "candidates" in receipt_schema["properties"]


@pytest.mark.asyncio
async def test_owned_design_reader_uses_the_64_mib_no_follow_bound(
    child_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, results = child_db
    owner_root = results / "owner"
    owner_root.mkdir()
    source = owner_root / "design.pdb"
    source.write_bytes(_pdb())
    async with sessions() as session:
        owner = Job(
            id="owner-design-bound",
            name="owner-design-bound",
            status="completed",
            model_id="boltz2",
            mode="structure_prediction",
            params={},
            output_dir=str(owner_root),
            child_output_dir=str(owner_root),
        )
        session.add(owner)
        session.add(
            Design(
                id="design-bound",
                job_id=owner.id,
                name="design-bound",
                pdb_path=str(source),
            )
        )
        await session.commit()

    calls: list[int | None] = []
    real_reader = child_jobs.read_structure_bytes

    def bounded_reader(path, *, max_bytes=None):
        calls.append(max_bytes)
        return real_reader(path, max_bytes=max_bytes)

    monkeypatch.setattr(child_jobs, "read_structure_bytes", bounded_reader)
    async with sessions() as session:
        owner = await session.get(Job, "owner-design-bound")
        selections = await child_jobs.design_selections(
            session,
            source_parent=owner,
            design_ids=["design-bound"],
        )

    assert len(selections) == 1
    assert calls == [child_jobs.MAX_UPLOAD_BYTES]


@pytest.mark.asyncio
async def test_owned_design_too_large_error_is_distinct(child_db) -> None:
    sessions, results = child_db
    owner_root = results / "owner-too-large"
    owner_root.mkdir()
    source = owner_root / "design.pdb"
    with source.open("wb") as handle:
        handle.truncate(child_jobs.MAX_UPLOAD_BYTES + 1)
    async with sessions() as session:
        owner = Job(
            id="owner-design-too-large",
            name="owner-design-too-large",
            status="completed",
            model_id="boltz2",
            mode="structure_prediction",
            params={},
            output_dir=str(owner_root),
            child_output_dir=str(owner_root),
        )
        session.add(owner)
        session.add(
            Design(
                id="design-too-large",
                job_id=owner.id,
                name="design-too-large",
                pdb_path=str(source),
            )
        )
        await session.commit()

    async with sessions() as session:
        owner = await session.get(Job, "owner-design-too-large")
        with pytest.raises(
            child_jobs.FrustraMPNNChildError,
            match="selected Design source exceeds the 64 MiB limit",
        ):
            await child_jobs.design_selections(
                session,
                source_parent=owner,
                design_ids=["design-too-large"],
            )


@pytest.mark.asyncio
async def test_child_authority_uses_requested_model_altloc_and_exact_residue_selection(child_db) -> None:
    sessions, _results = child_db
    requested = _custom_settings()
    selection = child_jobs.upload_selection(
        filename="multi-model.pdb",
        payload=_multi_model_altloc_pdb(),
        expected_sha256=None,
    )

    async with sessions() as session:
        child = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=requested,
        )
        child_id = child.id

    async with sessions() as session:
        persisted = await session.get(Job, child_id)
        assert persisted is not None
        envelope = persisted.params[child_jobs.ENVELOPE_KEY]
        assert envelope["settings_contract_version"] == "typed_v2"
        assert envelope["normalized_requested_settings"] == requested.model_dump(mode="json")
        assert envelope["normalized_requested_settings"]["batching_enabled"] is True
        assert envelope["normalized_requested_settings"]["structures_per_job"] == 250
        lineage_authority = envelope["selection"][0]["launch_authority"]
        assert lineage_authority["normalized_requested_settings"] == requested.model_dump(mode="json")
        assert lineage_authority["settings_sha256"] == envelope["settings_sha256"]
        assert lineage_authority["effective_settings"]["settings_sha256"] == envelope["settings_sha256"]
        assert lineage_authority["effective_settings_sha256"] == lineage_authority["effective_settings"][
            "effective_settings_sha256"
        ]
        assert lineage_authority["configuration_sha256"] == lineage_authority[
            "execution_configuration"
        ]["configuration_sha256"]
        assert lineage_authority["structure_map_sha256"] == lineage_authority[
            "effective_settings"
        ]["resolution_identity"]["structure_map_sha256"]
        resolved = lineage_authority["effective_settings"]["resolved_chains"]
        assert [(chain["pdb_chain_id"], [row["auth_seq_id"] for row in chain["residues"]]) for chain in resolved] == [
            ("A", [2])
        ]
        assert lineage_authority["execution_configuration"]["effective_settings"] == lineage_authority[
            "effective_settings"
        ]

        batch = json.loads(Path(persisted.params["frustrampnn_batch_manifest_path"]).read_text())
        record = batch["records"][0]
        assert "launch_authority" not in record
        request_files = sorted((Path(persisted.output_dir) / "inputs" / "requests").rglob("*.json"))
        assert [path.name for path in request_files] == ["workflow_component_request_v3.json"]
        request = json.loads(request_files[0].read_text())
        assert request["schema_version"] == 3
        assert request["requested_settings"] == lineage_authority["normalized_requested_settings"]
        assert request["requested_settings_sha256"] == lineage_authority["settings_sha256"]
        assert request["effective_settings"] == lineage_authority["effective_settings"]
        assert request["effective_settings_sha256"] == lineage_authority["effective_settings_sha256"]
        assert request["execution_configuration"] == lineage_authority["execution_configuration"]
        assert request["execution_configuration_sha256"] == lineage_authority["configuration_sha256"]
        assert request["structure_map_sha256"] == lineage_authority["structure_map_sha256"]
        assert list(Path(persisted.output_dir).rglob("frustrampnn_structure_map_v1.json"))
        assert not list(Path(persisted.output_dir).rglob("workflow_component_request_v1.json"))
        assert not list(Path(persisted.output_dir).rglob("workflow_component_request_v2.json"))


@pytest.mark.asyncio
async def test_batch_keeps_candidate_specific_effective_and_configuration_authority(child_db) -> None:
    sessions, _results = child_db
    selections = [
        child_jobs.upload_selection(
            filename=f"candidate-{chain}.pdb",
            payload=_pdb_for_chain(chain),
            expected_sha256=None,
        )
        for chain in ("A", "B")
    ]

    async with sessions() as session:
        child = await child_jobs.create_child_job(
            session,
            selections=selections,
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=_batched_settings(2),
        )
        child_id = child.id

    async with sessions() as session:
        persisted = await session.get(Job, child_id)
        assert persisted is not None
        envelope = persisted.params[child_jobs.ENVELOPE_KEY]
        authorities = [item["launch_authority"] for item in envelope["selection"]]
        assert [item["candidate_id"] for item in envelope["selection"]] == ["upload-1", "upload-2"]
        assert [authority["effective_settings"]["resolved_chains"][0]["pdb_chain_id"] for authority in authorities] == [
            "A",
            "B",
        ]
        assert len({authority["effective_settings_sha256"] for authority in authorities}) == 2
        assert len({authority["configuration_sha256"] for authority in authorities}) == 2
        assert len({authority["structure_map_sha256"] for authority in authorities}) == 2


@pytest.mark.asyncio
async def test_new_v2_writer_resolves_each_candidate_settings_exactly_once(
    child_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, _results = child_db
    selections = [
        child_jobs.upload_selection(
            filename=f"candidate-{chain}.pdb",
            payload=_pdb_for_chain(chain),
            expected_sha256=None,
        )
        for chain in ("A", "B")
    ]
    real_resolve = child_jobs.resolve_effective_settings
    calls: list[str] = []

    def counted_resolve(requested_settings, structure_map):
        calls.append(str(structure_map["candidate_id"]))
        return real_resolve(requested_settings, structure_map)

    monkeypatch.setattr(child_jobs, "resolve_effective_settings", counted_resolve)
    async with sessions() as session:
        await child_jobs.create_child_job(
            session,
            selections=selections,
            source_parent=None,
            trigger="structure_prediction_child",
            requested_settings=_batched_settings(2),
        )

    assert calls == ["upload-1", "upload-2"]


@pytest.mark.asyncio
async def test_new_child_rejects_historical_v1_requested_settings(child_db) -> None:
    sessions, _results = child_db
    historical = FrustraMPNNRequestedSettings.model_validate(
        {
            "schema_name": "frustrampnn_settings",
            "schema_version": 1,
            "settings_value_origin": "bms_default",
            "protein_selection": {
                "mode": "all_protein_entities",
                "entities": [],
                "regions": [],
                "residues": [],
            },
            "source_structure": {
                "selected_model_number": 1,
                "preferred_altloc": "",
            },
            "classification_policy": {
                "mode": "canonical",
                "high_max": -1.0,
                "minimal_min": 0.58,
            },
        }
    )
    selection = child_jobs.upload_selection(
        filename="candidate.pdb", payload=_pdb(), expected_sha256=None
    )

    async with sessions() as session:
        with pytest.raises(child_jobs.FrustraMPNNChildError, match="schema_version 2"):
            await child_jobs.create_child_job(
                session,
                selections=[selection],
                source_parent=None,
                trigger="upload_analyze",
                requested_settings=historical,
            )


@pytest.mark.asyncio
async def test_batch_capacity_is_governed_by_requested_settings(child_db) -> None:
    sessions, _results = child_db
    selections = [
        child_jobs.upload_selection(
            filename=f"candidate-{chain}.pdb",
            payload=_pdb_for_chain(chain),
            expected_sha256=None,
        )
        for chain in ("A", "B")
    ]

    async with sessions() as session:
        with pytest.raises(child_jobs.FrustraMPNNChildError, match="batching is disabled"):
            await child_jobs.create_child_job(
                session,
                selections=selections,
                source_parent=None,
                trigger="upload_analyze",
                requested_settings=default_settings(),
            )
    async with sessions() as session:
        with pytest.raises(child_jobs.FrustraMPNNChildError, match="structures_per_job"):
            await child_jobs.create_child_job(
                session,
                selections=selections,
                source_parent=None,
                trigger="upload_analyze",
                requested_settings=_batched_settings(1),
            )


@pytest.mark.asyncio
async def test_batch_resolution_failure_is_atomic_before_job_commit(child_db) -> None:
    sessions, results = child_db
    selections = [
        child_jobs.upload_selection(filename="valid.pdb", payload=_pdb(), expected_sha256=None),
        child_jobs.upload_selection(filename="wrong-chain.pdb", payload=_pdb_for_chain("B"), expected_sha256=None),
    ]
    requested = FrustraMPNNRequestedSettings.model_validate(
        {
            **_batched_settings(2).model_dump(mode="json"),
            "protein_selection": {
                "mode": "selected_entities",
                "entities": [
                    {
                        "entity_instance_id": "pdb:A",
                        "source_entity_id": None,
                        "label_asym_id": None,
                        "auth_asym_id": "A",
                    }
                ],
                "residues": [],
            },
        }
    )

    async with sessions() as session:
        with pytest.raises(child_jobs.FrustraMPNNChildError, match="resolution"):
            await child_jobs.create_child_job(
                session,
                selections=selections,
                source_parent=None,
                trigger="upload_analyze",
                requested_settings=requested,
            )

    async with sessions() as session:
        assert (await session.execute(select(func.count(Job.id)))).scalar_one() == 0
    assert list(results.iterdir()) == []


@pytest.mark.asyncio
async def test_reanalysis_upgrades_verified_typed_v1_settings_to_v2(child_db) -> None:
    sessions, _results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb", payload=_pdb(), expected_sha256=None
    )
    historical_payload = {
        "schema_name": "frustrampnn_settings",
        "schema_version": 1,
        "settings_value_origin": "bms_default",
        "protein_selection": {
            "mode": "all_protein_entities",
            "entities": [],
            "regions": [],
            "residues": [],
        },
        "source_structure": {
            "selected_model_number": 1,
            "preferred_altloc": "",
        },
        "classification_policy": {
            "mode": "canonical",
            "high_max": -1.0,
            "minimal_min": 0.58,
        },
    }
    historical = FrustraMPNNRequestedSettings.model_validate(historical_payload)
    historical_sha256 = child_jobs.requested_settings_sha256(historical)

    async with sessions() as session:
        prior = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=default_settings(),
        )
        params = dict(prior.params)
        envelope = dict(params[child_jobs.ENVELOPE_KEY])
        envelope["settings_contract_version"] = "typed_v1"
        envelope["settings_value_origin"] = "bms_default"
        envelope["normalized_requested_settings"] = historical_payload
        envelope["settings_sha256"] = historical_sha256
        lineage = json.loads(json.dumps(envelope["selection"]))
        for item in lineage:
            item["launch_authority"]["settings_value_origin"] = "bms_default"
            item["launch_authority"]["normalized_requested_settings"] = historical_payload
            item["launch_authority"]["settings_sha256"] = historical_sha256
        envelope["selection"] = lineage
        params[child_jobs.ENVELOPE_KEY] = envelope
        prior.params = params
        prior.queue_status = "completed"
        prior.status = "completed"
        await session.commit()
        prior_id = prior.id

    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        upgraded = await child_jobs.create_reanalysis_child(
            session,
            prior_child=prior,
            replacement_settings=None,
        )
        upgraded_envelope = upgraded.params[child_jobs.ENVELOPE_KEY]
        assert upgraded_envelope["settings_contract_version"] == "typed_v2"
        assert upgraded_envelope["normalized_requested_settings"]["schema_version"] == 2
        assert upgraded_envelope["normalized_requested_settings"]["batching_enabled"] is False
        assert upgraded_envelope["normalized_requested_settings"]["structures_per_job"] == 1
        assert upgraded_envelope["settings_sha256"] != historical_sha256


@pytest.mark.asyncio
async def test_reanalysis_defaults_to_prior_settings_and_complete_replacement_supersedes(child_db) -> None:
    sessions, _results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb",
        payload=_pdb(),
        expected_sha256=None,
    )
    prior_settings = default_settings()
    async with sessions() as session:
        prior = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=prior_settings,
        )
        prior.queue_status = "completed"
        prior.status = "completed"
        await session.commit()
        prior_id = prior.id
        prior_envelope = json.loads(json.dumps(prior.params[child_jobs.ENVELOPE_KEY]))

    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        inherited = await child_jobs.create_reanalysis_child(
            session,
            prior_child=prior,
            replacement_settings=None,
        )
        inherited_id = inherited.id

    async with sessions() as session:
        inherited = await session.get(Job, inherited_id)
        inherited_envelope = inherited.params[child_jobs.ENVELOPE_KEY]
        assert inherited_envelope["normalized_requested_settings"] == prior_settings.model_dump(mode="json")
        assert inherited_envelope["supersedes_child_job_id"] == prior_id
        assert inherited_envelope["prior_invocation_ids"] == prior_envelope["component_invocation_ids"]
        prior = await session.get(Job, prior_id)
        assert prior.params[child_jobs.ENVELOPE_KEY] == prior_envelope
        inherited.queue_status = "failed"
        inherited.status = "failed"
        await session.commit()

    replacement = FrustraMPNNRequestedSettings.model_validate(
        {
            **default_settings().model_dump(mode="json"),
            "classification_policy": {
                "mode": "custom",
                "high_max": -0.5,
                "minimal_min": 0.4,
            },
        }
    )
    async with sessions() as session:
        inherited = await session.get(Job, inherited_id)
        replaced = await child_jobs.create_reanalysis_child(
            session,
            prior_child=inherited,
            replacement_settings=replacement,
        )
        assert replaced.params[child_jobs.ENVELOPE_KEY]["normalized_requested_settings"] == replacement.model_dump(
            mode="json"
        )
        assert replaced.params[child_jobs.ENVELOPE_KEY]["settings_sha256"] != inherited.params[
            child_jobs.ENVELOPE_KEY
        ]["settings_sha256"]


@pytest.mark.asyncio
async def test_reanalysis_snapshot_reader_uses_the_64_mib_no_follow_bound(
    child_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, _results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb", payload=_pdb(), expected_sha256=None
    )
    async with sessions() as session:
        prior = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=default_settings(),
        )
        prior.queue_status = "completed"
        prior.status = "completed"
        await session.commit()
        prior_id = prior.id

    calls: list[int | None] = []
    real_reader = child_jobs.read_structure_bytes

    def bounded_reader(path, *, max_bytes=None):
        calls.append(max_bytes)
        return real_reader(path, max_bytes=max_bytes)

    monkeypatch.setattr(child_jobs, "read_structure_bytes", bounded_reader)
    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        await child_jobs.create_reanalysis_child(
            session,
            prior_child=prior,
            replacement_settings=None,
        )

    assert calls == [child_jobs.MAX_UPLOAD_BYTES]


@pytest.mark.asyncio
async def test_reanalysis_snapshot_too_large_error_is_distinct(child_db) -> None:
    sessions, _results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb", payload=_pdb(), expected_sha256=None
    )
    async with sessions() as session:
        prior = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=default_settings(),
        )
        prior.queue_status = "completed"
        prior.status = "completed"
        await session.commit()
        prior_id = prior.id
        envelope = prior.params[child_jobs.ENVELOPE_KEY]
        snapshot = Path(prior.output_dir) / envelope["selection"][0][
            "snapshot_relative_path"
        ]
        snapshot.chmod(0o644)
        with snapshot.open("wb") as handle:
            handle.truncate(child_jobs.MAX_UPLOAD_BYTES + 1)

    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        with pytest.raises(
            child_jobs.FrustraMPNNChildError,
            match="prior child source snapshot exceeds the 64 MiB limit",
        ):
            await child_jobs.create_reanalysis_child(
                session,
                prior_child=prior,
                replacement_settings=None,
            )


@pytest.mark.asyncio
async def test_reanalysis_rejects_missing_typed_history_unless_explicitly_historical_v1(child_db) -> None:
    sessions, _results = child_db
    selection = child_jobs.upload_selection(filename="candidate.pdb", payload=_pdb(), expected_sha256=None)
    async with sessions() as session:
        prior = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=default_settings(),
        )
        params = dict(prior.params)
        envelope = dict(params[child_jobs.ENVELOPE_KEY])
        envelope.pop("normalized_requested_settings")
        envelope.pop("settings_sha256")
        envelope["settings_contract_version"] = "missing"
        params[child_jobs.ENVELOPE_KEY] = envelope
        prior.params = params
        prior.queue_status = "completed"
        prior.status = "completed"
        await session.commit()
        prior_id = prior.id

    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        with pytest.raises(child_jobs.FrustraMPNNChildError, match="prior typed settings"):
            await child_jobs.create_reanalysis_child(
                session,
                prior_child=prior,
                replacement_settings=None,
            )

    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        params = dict(prior.params)
        envelope = dict(params[child_jobs.ENVELOPE_KEY])
        envelope["settings_contract_version"] = "historical_v1"
        params[child_jobs.ENVELOPE_KEY] = envelope
        prior.params = params
        await session.commit()

    async with sessions() as session:
        prior = await session.get(Job, prior_id)
        compatible = await child_jobs.create_reanalysis_child(
            session,
            prior_child=prior,
            replacement_settings=None,
        )
        assert compatible.params[child_jobs.ENVELOPE_KEY]["normalized_requested_settings"] == default_settings().model_dump(
            mode="json"
        )
