from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

ONE_AKI_SHA256 = "c75d7a689617248cdd92dc6633531d2506fb9bef1e6e21e26c8f579ae6955abb"
PROFILE_SHA256 = "a" * 64
CATALOG_DIGEST = "b" * 64


def _settings() -> dict:
    return {
        "replicas": 1,
        "random_seed": 20260825,
        "padding_nm": 1.0,
        "salt_molar": 0.15,
        "neutralize": True,
        "temperature_k": 300.0,
        "pressure_bar": 1.0,
        "timestep_fs": 2.0,
        "minimization_steps": 50000,
        "nvt_ps": 100.0,
        "npt_ps": 100.0,
        "production_ns": 0.01,
        "trajectory_interval_ps": 1.0,
        "energy_interval_ps": 0.2,
        "checkpoint_interval_minutes": 15.0,
        "ntomp": 8,
    }


def _profile() -> dict:
    return {
        "id": "profile-v1",
        "profile_sha256": PROFILE_SHA256,
        "supported_engines": ["gromacs"],
        "states": {"selectable": True},
        "launch_constraints": {
            "structure_sha256": ONE_AKI_SHA256,
            "replicas": 1,
            "engine": "gromacs",
            "padding_nm": 1.0,
            "salt_molar": 0.15,
            "timestep_fs": 2.0,
            "temperature_k": 300.0,
            "pressure_bar": 1.0,
            "max_production_steps": 5000,
            "max_minimization_steps": 50000,
            "max_nvt_steps": 50000,
            "max_npt_steps": 50000,
        },
        "scientific_validation": {"scope": {"launch_scope": "smoke_auto"}},
    }


def _intent(starting_structures, *, launch_context_id: str | None = None):
    return starting_structures.MdLaunchIntent(
        schema_version="bms.md.launch-intent.v1",
        name="Typed MD launch",
        source_ref={"kind": "managed_fixture", "id": "1aki-admitted-v1"},
        expected_source_sha256=ONE_AKI_SHA256,
        chemistry_profile_id="profile-v1",
        chemistry_profile_sha256=PROFILE_SHA256,
        catalog_digest=CATALOG_DIGEST,
        requested_settings=_settings(),
        launch_context_id=launch_context_id,
    )


def test_requested_settings_are_closed_required_finite_and_cross_bounded() -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")

    settings = starting_structures.MdRequestedSettings(**_settings())
    assert settings.model_dump() == _settings()

    for field in _settings():
        missing = _settings()
        missing.pop(field)
        with pytest.raises(ValidationError):
            starting_structures.MdRequestedSettings(**missing)

    with pytest.raises(ValidationError):
        starting_structures.MdRequestedSettings(**{**_settings(), "padding_nm": math.inf})
    with pytest.raises(ValidationError):
        starting_structures.MdRequestedSettings(**{**_settings(), "temperature_k": math.nan})
    with pytest.raises(ValidationError):
        starting_structures.MdRequestedSettings(
            **{**_settings(), "trajectory_interval_ps": 20.0}
        )
    with pytest.raises(ValidationError):
        starting_structures.MdRequestedSettings(**{**_settings(), "gpu_id": "0"})


def test_preview_compiles_profile_bounded_public_effective_request_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    canonicalized: list[dict] = []
    real_dumps = starting_structures.rfc8785.dumps

    def capture_preimage(value):
        canonicalized.append(value)
        return real_dumps(value)

    monkeypatch.setattr(starting_structures.rfc8785, "dumps", capture_preimage)
    resolved = starting_structures.resolve_product_source(
        starting_structures.StartingStructureSourceRef(
            kind="managed_fixture", id="1aki-admitted-v1"
        )
    )

    preview = starting_structures.compile_launch_preview(
        intent=_intent(starting_structures),
        resolved=resolved,
        profile=_profile(),
        current_catalog_digest=CATALOG_DIGEST,
    )

    assert preview.schema_version == "bms.md.launch-preview.v1"
    assert preview.source.sha256 == ONE_AKI_SHA256
    assert preview.chemistry.model_dump() == {
        "profile_id": "profile-v1",
        "profile_sha256": PROFILE_SHA256,
        "catalog_digest": CATALOG_DIGEST,
        "admitted": True,
    }
    assert preview.warnings == [] and preview.blockers == []
    effective = preview.effective_request.model_dump()
    assert effective["engine"] == "gromacs"
    assert effective["replicas"] == 1
    assert effective["stages"]["nvt"]["steps"] == 50000
    assert effective["stages"]["npt"]["steps"] == 50000
    assert effective["stages"]["production"]["steps"] == 5000
    assert effective["stages"]["production"]["trajectory_interval_steps"] == 500
    assert effective["stages"]["production"]["energy_interval_steps"] == 100
    assert effective["execution"] == {
        "ntmpi": 1,
        "ntomp": 8,
        "gpu_offload": "full",
        "pin": "on",
        "placement_authority": "global_scheduler",
    }
    assert "gpu_id" not in str(effective)
    assert len(preview.preview_digest) == 64
    int(preview.preview_digest, 16)
    assert canonicalized[0]["schema_version"] == "bms.md.launch-preview-preimage.v1"
    assert "schema" not in canonicalized[0]


def test_preview_digest_excludes_labels_context_and_viewer_transport() -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    resolved = starting_structures.resolve_product_source(
        starting_structures.StartingStructureSourceRef(
            kind="managed_fixture", id="1aki-admitted-v1"
        )
    )
    first = starting_structures.compile_launch_preview(
        intent=_intent(starting_structures, launch_context_id="context-one"),
        resolved=resolved,
        profile=_profile(),
        current_catalog_digest=CATALOG_DIGEST,
    )
    renamed = starting_structures.ResolvedStartingStructure(
        source_ref=resolved.source_ref,
        path=resolved.path,
        label="A presentation-only rename",
        pdb_id=resolved.pdb_id,
    )
    second = starting_structures.compile_launch_preview(
        intent=_intent(starting_structures, launch_context_id="context-two"),
        resolved=renamed,
        profile=_profile(),
        current_catalog_digest=CATALOG_DIGEST,
    )
    assert first.preview_digest == second.preview_digest
    assert first.source.label != second.source.label


def test_preview_blocks_same_reference_with_different_exact_bytes(tmp_path: Path) -> None:
    starting_structures = importlib.import_module("services.md.starting_structures")
    changed = tmp_path / "changed.pdb"
    changed.write_bytes(
        (API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb").read_bytes()
        + b"REMARK changed bytes\n"
    )
    resolved = starting_structures.ResolvedStartingStructure(
        source_ref=starting_structures.StartingStructureSourceRef(kind="rcsb", id="1AKI"),
        path=changed,
        label="RCSB 1AKI",
        pdb_id="1AKI",
    )
    intent = _intent(starting_structures).model_copy(
        update={
            "source_ref": starting_structures.StartingStructureSourceRef(kind="rcsb", id="1AKI"),
            "expected_source_sha256": hashlib.sha256(changed.read_bytes()).hexdigest(),
        }
    )
    preview = starting_structures.compile_launch_preview(
        intent=intent,
        resolved=resolved,
        profile=_profile(),
        current_catalog_digest=CATALOG_DIGEST,
    )
    assert preview.chemistry.admitted is False
    assert [blocker.code for blocker in preview.blockers] == [
        "MD_STARTING_STRUCTURE_NOT_ADMITTED"
    ]


class _CatalogView:
    catalog_digest = CATALOG_DIGEST

    def get_profile(self, profile_id: str):
        return _profile() if profile_id == "profile-v1" else None


class _Catalog:
    def view(self):
        return _CatalogView()


class _UnusedSession:
    async def get(self, *_args, **_kwargs):
        return None


def _intent_payload() -> dict:
    return {
        "schema_version": "bms.md.launch-intent.v1",
        "name": "Typed MD launch",
        "source_ref": {"kind": "managed_fixture", "id": "1aki-admitted-v1"},
        "expected_source_sha256": ONE_AKI_SHA256,
        "chemistry_profile_id": "profile-v1",
        "chemistry_profile_sha256": PROFILE_SHA256,
        "catalog_digest": CATALOG_DIGEST,
        "requested_settings": _settings(),
        "launch_context_id": None,
    }


@pytest.fixture
def typed_client(monkeypatch: pytest.MonkeyPatch):
    import main
    from database import get_session
    from routers import molecular_dynamics

    async def override_session():
        yield _UnusedSession()

    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: _Catalog())
    main.app.dependency_overrides[get_session] = override_session
    test_client = TestClient(main.app)
    try:
        yield test_client
    finally:
        test_client.close()
        main.app.dependency_overrides.pop(get_session, None)


def test_launch_preview_route_is_side_effect_free_and_closed(typed_client: TestClient) -> None:
    response = typed_client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": _intent_payload(),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "bms.md.launch-preview.v1"
    assert "schema" not in payload
    assert payload["preview_digest"]
    assert payload["blockers"] == []
    assert "/home/" not in response.text
    assert "gpu_id" not in response.text

    extra = typed_client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": {**_intent_payload(), "output_dir": "/tmp/forged"},
        },
    )
    assert extra.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "bms.md.launch-preview-request.v1", "intent": _intent_payload()},
        {"intent": _intent_payload()},
        {
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": {
                **{key: value for key, value in _intent_payload().items() if key != "schema_version"},
                "schema": "bms.md.launch-intent.v1",
            },
        },
    ],
)
def test_launch_preview_requires_schema_version_and_forbids_schema(
    typed_client: TestClient, payload: dict
) -> None:
    response = typed_client.post("/api/molecular-dynamics/launch-preview", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "different_value"),
    [
        ("replicas", 2),
        ("padding_nm", 1.1),
        ("salt_molar", 0.2),
        ("temperature_k", 301.0),
        ("pressure_bar", 1.1),
        ("timestep_fs", 1.0),
    ],
)
def test_preview_rejects_every_profile_fixed_setting_mismatch_without_side_effects(
    typed_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    different_value: int | float,
) -> None:
    from routers import jobs, molecular_dynamics

    calls: list[str] = []

    async def forbidden_job(*_args, **_kwargs):
        calls.append("job")
        raise AssertionError("preview must not create a Job")

    def forbidden_context(*_args, **_kwargs):
        calls.append("context")
        raise AssertionError("preview must not claim a launch context")

    monkeypatch.setattr(jobs, "create_job", forbidden_job)
    monkeypatch.setattr(
        molecular_dynamics, "experiment_session_factory", forbidden_context, raising=False
    )
    intent = _intent_payload()
    intent["requested_settings"] = {**_settings(), field: different_value}
    response = typed_client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": intent,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "MD_SETTING_FIXED_BY_PROFILE"
    assert calls == []


@pytest.mark.parametrize(
    ("checkpoint_minutes", "expected_status"),
    [(0.5, 422), (1.0, 200), (1.5, 200)],
)
def test_preview_checkpoint_interval_matches_current_md_v2_lower_bound(
    typed_client: TestClient,
    checkpoint_minutes: float,
    expected_status: int,
) -> None:
    from services.md import starting_structures

    intent = _intent_payload()
    intent["requested_settings"] = {
        **_settings(),
        "checkpoint_interval_minutes": checkpoint_minutes,
    }
    response = typed_client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": intent,
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_status != 200:
        return
    preview = starting_structures.MdLaunchPreview.model_validate(response.json())
    assert preview.blockers == []
    materializer_input = starting_structures.compile_md_job_v2(
        preview=preview,
        profile=_profile(),
        job_id="assigned-by-server",
        source_token="trusted-source-token",
    )
    schema = json.loads(
        (API_ROOT.parent.parent / "schemas" / "md_job_v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(materializer_input)


def test_launch_recompiles_digest_and_calls_one_canonical_job_wrapper(
    typed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routers import jobs

    preview_response = typed_client.post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": _intent_payload(),
        },
    )
    digest = preview_response.json()["preview_digest"]
    calls: list[dict] = []

    async def fake_create_job(job_data, background_tasks, session, **kwargs):
        calls.append({"job_data": job_data, "session": session, "kwargs": kwargs})
        resolver = kwargs["_md_input_resolver"]
        token = job_data.params["md_job_spec"]["input"]["structure"]
        assert Path(resolver(token)).read_bytes() == (
            API_ROOT / "assets" / "md" / "admitted_structures" / "1AKI.pdb"
        ).read_bytes()
        return SimpleNamespace(
            id="33333333-3333-4333-8333-333333333333",
            name=job_data.name,
            status="queued",
            model_id="molecular_dynamics",
            mode="simulate",
            params={},
            created_at=None,
            started_at=None,
            completed_at=None,
            output_dir=None,
            error_message=None,
            design_count=0,
            batch_id=None,
            batch_name=None,
            parent_job_id=None,
            child_stage=None,
            lineage_root_job_id=None,
            stage_family=None,
            stage_mode=None,
            source_stage_job_id=None,
            source_stage_family=None,
            source_stage_mode=None,
            source_selection_manifest_path=None,
            source_selection_count=None,
            selected_input_artifact_class=None,
            selected_input_schema_version=None,
            selection_source_type=None,
            selection_source_job_id=None,
            selection_dataset_name=None,
            selected_loop_scope=None,
            provenance=None,
            saved_selection_sets=None,
            pinned_gpu=None,
            awaiting_input=None,
            awaiting_stage=None,
            awaiting_payload=None,
            decision_history=None,
            launch_context_id=None,
            launch_context_binding=None,
            return_uri=None,
            frustrampnn_result_count=0,
            frustrampnn_reopen_destination=None,
            conformational_mapping_request_id=None,
        )

    monkeypatch.setattr(jobs, "create_job", fake_create_job)
    stale = typed_client.post(
        "/api/molecular-dynamics/launch",
        json={
            "schema_version": "bms.md.launch-request.v1",
            "intent": _intent_payload(),
            "preview_digest": "0" * 64,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "MD_LAUNCH_PREVIEW_STALE"
    assert calls == []

    launched = typed_client.post(
        "/api/molecular-dynamics/launch",
        json={
            "schema_version": "bms.md.launch-request.v1",
            "intent": _intent_payload(),
            "preview_digest": digest,
        },
    )
    assert launched.status_code == 201, launched.text
    assert launched.json()["id"] == "33333333-3333-4333-8333-333333333333"
    assert len(calls) == 1
    job_data = calls[0]["job_data"]
    assert job_data.model_id == "molecular_dynamics" and job_data.mode == "simulate"
    assert job_data.launch_context_id is None
    assert job_data.params["md_job_spec"]["schema"] == "bms.md.job.v2"
    assert calls[0]["kwargs"]["_md_output_creation"] == {}

    legacy = typed_client.post(
        "/api/molecular-dynamics/launch",
        json={
            "schema": "bms.md.launch-request.v1",
            "intent": _intent_payload(),
            "preview_digest": digest,
        },
    )
    assert legacy.status_code == 422


@pytest.fixture
def project_context_preview_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import main
    from database import Base, Design, Job, get_session
    from experiment_models import (
        ExperimentAggregateHead,
        ExperimentBase,
        ExperimentLaunchContext,
        ExperimentOperationalReceipt,
        ExperimentResource,
        ExperimentResourceAdmission,
        ExperimentRevision,
        ExperimentRunAttempt,
        ExperimentRunGroup,
        ExperimentValidation,
        ExperimentWorkflowPreparation,
        ExperimentWorkflowRun,
    )
    from routers import molecular_dynamics
    from services.md import starting_structures
    from services.resource_usage_evidence import build_resource_admission_handoff
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    experiment_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'experiments.db'}"
    )
    core_sessions = async_sessionmaker(core_engine, expire_on_commit=False)
    experiment_sessions = async_sessionmaker(experiment_engine, expire_on_commit=False)
    results_root = tmp_path / "results"
    results_root.mkdir()
    disposable_roots = {
        "BMS_DATA": tmp_path / "data",
        "BMS_RESULTS_DIR": results_root,
        "BMS_WORK": tmp_path / "work",
        "BMS_STATE_DIR": tmp_path / "state",
        "BMS_SCHEDULER_STATE_DIR": tmp_path / "scheduler",
        "BMS_MSA_CACHE": tmp_path / "cache" / "msa",
        "BMS_SABDAB_CACHE": tmp_path / "cache" / "sabdab",
    }
    for variable, root in disposable_roots.items():
        root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(root))
    structure_path = results_root / "project-design.pdb"
    structure_path.write_bytes(
        (API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb").read_bytes()
    )
    expected_design_id = "22222222-2222-4222-8222-222222222222"
    other_design_id = "33333333-3333-4333-8333-333333333333"
    producer_job_id = "11111111-1111-4111-8111-111111111111"
    ids = {
        "project": "project:md-preview",
        "global": "global-experiment:md-preview",
        "domain": "domain-experiment:md-preview",
        "domain_revision": "revision:md-domain",
        "workflow": "workflow:md-preview",
        "workflow_revision": "revision:md-workflow",
        "preparation": "preparation:md-preview",
        "validation": "validation:md-preview",
        "run_group": "run-group:md-preview",
        "run": "run:md-preview",
        "attempt": "attempt:md-preview",
        "plan": "plan:md-preview",
        "admission": "admission:md-preview",
        "context": "launch-context:md-preview",
    }
    prepared_md_authority = {
        "schema_version": "bms.md.launch-intent.v1",
        "source_ref": {"kind": "design", "id": expected_design_id},
        "expected_source_sha256": ONE_AKI_SHA256,
        "chemistry_profile_id": "profile-v1",
        "chemistry_profile_sha256": PROFILE_SHA256,
        "catalog_digest": CATALOG_DIGEST,
        "requested_settings": _settings(),
    }
    workflow_payload = {
        "schema": "bms.workflow.generic.v1",
        "workflow_family": "typed_core_job",
        "contract_version": "1",
        "adapter_id": "molecular-dynamics.v2",
        "nodes": [{"id": "main", "kind": "scheduler_job", "required": True}],
        "edges": [],
        "parameters": {},
        "scheduler": {
            "name": "Prepared MD",
            "model_id": "molecular_dynamics",
            "mode": "simulate",
            "params": prepared_md_authority,
        },
    }
    canonical = lambda value: json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    input_authority = {
        "schema": "bms.preparation-input-authority.v1",
        "workflow_revision_id": ids["workflow_revision"],
        "project_id": ids["project"],
        "global_experiment_id": ids["global"],
        "domain_id": ids["domain"],
        "dataset_inputs": [],
        "receipt_contracts": [],
        "receipt_contracts_sha256": hashlib.sha256(canonical([]).encode()).hexdigest(),
        "workflow_source_receipts": [],
    }
    normalized_request = {
        "workflow_revision_id": ids["workflow_revision"],
        "input_dataset_revision_ids": [],
        "input_authority": input_authority,
        "workflow": workflow_payload,
    }
    normalized_json = canonical(normalized_request)
    normalized_sha = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
    validation_json = canonical(
        {
            "schema": "bms.experiment.validation.v1",
            "status": "valid",
            "workflow_revision_id": ids["workflow_revision"],
            "normalized_request_sha256": normalized_sha,
            "input_authority_sha256": hashlib.sha256(
                canonical(input_authority).encode()
            ).hexdigest(),
        }
    )
    validation_sha = hashlib.sha256(validation_json.encode("utf-8")).hexdigest()
    issued_at = "2026-08-25T12:00:00Z"
    scheduler_job_id = "44444444-4444-5444-8444-444444444444"
    source_revision = "1" * 40
    source_tree = "2" * 40
    lease_token = "md-preview-admission-lease"
    admission_handoff = build_resource_admission_handoff(
        admission_id=ids["admission"],
        run_attempt_id=ids["attempt"],
        canonical_job_id=scheduler_job_id,
        preparation_id=ids["preparation"],
        cpu_threads=1,
        dram_bytes=1024**3,
        gpu_index=None,
        gpu_uuid=None,
        policy_source="project-scheduler",
        policy_version="bms.resource-admission-policy.v1",
        owner="project-manager",
        lease_token=lease_token,
        source_revision=source_revision,
        source_tree=source_tree,
    )
    admission_receipt = canonical(
        {
            "schema": "bms.resource-admission-authority.v1",
            "admission_id": ids["admission"],
            "run_attempt_id": ids["attempt"],
            "canonical_job_id": scheduler_job_id,
            "preparation_id": ids["preparation"],
            "source_revision": source_revision,
            "source_tree": source_tree,
            "handoff_sha256": admission_handoff["handoff_sha256"],
        }
    )

    async def prepare() -> None:
        async with core_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with experiment_engine.begin() as connection:
            await connection.run_sync(ExperimentBase.metadata.create_all)
        async with core_sessions() as session:
            session.add(
                Job(
                    id=producer_job_id,
                    name="Project structure prediction",
                    status="completed",
                    queue_status="completed",
                    model_id="boltz2",
                    mode="predict",
                    params={},
                    output_dir=str(results_root),
                )
            )
            session.add_all(
                [
                    Design(
                        id=expected_design_id,
                        job_id=producer_job_id,
                        name="prepared-design",
                        pdb_path=str(structure_path),
                    ),
                    Design(
                        id=other_design_id,
                        job_id=producer_job_id,
                        name="other-design",
                        pdb_path=str(structure_path),
                    ),
                ]
            )
            await session.commit()
        async with experiment_sessions() as session:
            resource_kinds = {
                ids["project"]: "workspace",
                ids["global"]: "experiment",
                ids["domain"]: "domain_experiment",
                ids["domain_revision"]: "revision",
                ids["workflow"]: "workflow",
                ids["workflow_revision"]: "revision",
                ids["preparation"]: "preparation",
                ids["validation"]: "validation",
                ids["run_group"]: "run_group",
                ids["run"]: "workflow_run",
                ids["attempt"]: "run_attempt",
                ids["plan"]: "plan",
            }
            session.add_all(
                [
                    ExperimentResource(
                        id=resource_id,
                        kind=kind,
                        workspace_id=None if resource_id == ids["project"] else ids["project"],
                        lifecycle_owner_id=None,
                        created_at=issued_at,
                    )
                    for resource_id, kind in resource_kinds.items()
                ]
            )
            session.add_all(
                [
                    ExperimentAggregateHead(
                        aggregate_id=ids["project"],
                        aggregate_kind="workspace",
                        workspace_id=ids["project"],
                        parent_id=None,
                        current_revision_id=None,
                        head_generation=1,
                        lifecycle_state="active",
                        display_name="MD project",
                        description="",
                    ),
                    ExperimentAggregateHead(
                        aggregate_id=ids["global"],
                        aggregate_kind="experiment",
                        workspace_id=ids["project"],
                        parent_id=ids["project"],
                        current_revision_id=None,
                        head_generation=1,
                        lifecycle_state="active",
                        display_name="MD global experiment",
                        description="",
                    ),
                    ExperimentAggregateHead(
                        aggregate_id=ids["domain"],
                        aggregate_kind="domain_experiment",
                        workspace_id=ids["project"],
                        parent_id=ids["global"],
                        current_revision_id=ids["domain_revision"],
                        head_generation=1,
                        lifecycle_state="active",
                        display_name="MD domain",
                        description="",
                    ),
                    ExperimentAggregateHead(
                        aggregate_id=ids["workflow"],
                        aggregate_kind="workflow",
                        workspace_id=ids["project"],
                        parent_id=ids["domain"],
                        current_revision_id=ids["workflow_revision"],
                        head_generation=1,
                        lifecycle_state="active",
                        display_name="MD workflow",
                        description="",
                    ),
                ]
            )
            session.add_all(
                [
                    ExperimentRevision(
                        resource_id=ids["domain_revision"],
                        subject_id=ids["domain"],
                        revision_number=1,
                        schema_name="bms.domain-experiment.v1",
                        schema_version="1",
                        canonical_payload=canonical({"domain_kind": "protein_in_silico"}),
                        payload_sha256=hashlib.sha256(
                            canonical({"domain_kind": "protein_in_silico"}).encode()
                        ).hexdigest(),
                        dependency_graph_sha256="e" * 64,
                    ),
                    ExperimentRevision(
                        resource_id=ids["workflow_revision"],
                        subject_id=ids["workflow"],
                        revision_number=1,
                        schema_name="bms.workflow.generic.v1",
                        schema_version="1",
                        canonical_payload=canonical(workflow_payload),
                        payload_sha256=hashlib.sha256(
                            canonical(workflow_payload).encode()
                        ).hexdigest(),
                        dependency_graph_sha256="0" * 64,
                    ),
                ]
            )
            session.add(
                ExperimentWorkflowPreparation(
                    resource_id=ids["preparation"],
                    workspace_id=ids["project"],
                    workflow_revision_id=ids["workflow_revision"],
                    normalized_request_json=normalized_json,
                    normalized_request_sha256=normalized_sha,
                    scheduler_payload_json=canonical(workflow_payload["scheduler"]),
                    validation_status="valid",
                    validation_receipt_json=validation_json,
                    validation_resource_id=ids["validation"],
                    expected_cardinality=1,
                    created_at=issued_at,
                    prepared_at=issued_at,
                )
            )
            session.add(
                ExperimentValidation(
                    resource_id=ids["validation"],
                    subject_resource_id=ids["preparation"],
                    validator_name="global-workflow-contract",
                    validator_version="v2",
                    outcome="valid",
                    input_graph_sha256="0" * 64,
                    receipt_json=validation_json,
                    receipt_sha256=validation_sha,
                    created_at=issued_at,
                )
            )
            session.add(
                ExperimentRunGroup(
                    resource_id=ids["run_group"],
                    workspace_id=ids["project"],
                    launch_idempotency_key="md-preview",
                    request_sha256=normalized_sha,
                    state="dispatch_pending",
                    generation=1,
                    created_at=issued_at,
                    updated_at=issued_at,
                )
            )
            session.add(
                ExperimentWorkflowRun(
                    resource_id=ids["run"],
                    workspace_id=ids["project"],
                    run_group_id=ids["run_group"],
                    preparation_id=ids["preparation"],
                    node_id="main",
                    requiredness="required",
                    state="dispatch_pending",
                    generation=1,
                    created_at=issued_at,
                )
            )
            session.add(
                ExperimentRunAttempt(
                    resource_id=ids["attempt"],
                    workspace_id=ids["project"],
                    workflow_run_id=ids["run"],
                    preparation_id=ids["preparation"],
                    attempt_number=1,
                    scheduler_job_id=scheduler_job_id,
                    state="pending",
                    created_at=issued_at,
                )
            )
            session.add(
                ExperimentResourceAdmission(
                    admission_id=ids["admission"],
                    workspace_id=ids["project"],
                    domain_experiment_id=ids["domain"],
                    plan_id=ids["plan"],
                    preparation_id=ids["preparation"],
                    run_attempt_id=ids["attempt"],
                    canonical_job_id=scheduler_job_id,
                    state="admitted",
                    cpu_threads=1,
                    dram_bytes=1024**3,
                    gpu_index=None,
                    gpu_uuid=None,
                    policy_source="project-scheduler",
                    policy_version="bms.resource-admission-policy.v1",
                    owner="project-manager",
                    lease_token=lease_token,
                    admitted_at=issued_at,
                    created_at=issued_at,
                    updated_at=issued_at,
                )
            )
            session.add(
                ExperimentOperationalReceipt(
                    receipt_id=f"resource-admission:{ids['admission']}",
                    operation_kind="resource_admission",
                    workspace_id=ids["project"],
                    native_identity=scheduler_job_id,
                    state="sealed",
                    receipt_json=admission_receipt,
                    receipt_sha256=hashlib.sha256(admission_receipt.encode()).hexdigest(),
                    source_revision=source_revision,
                    occurred_at=issued_at,
                    verified_at=issued_at,
                )
            )
            session.add(
                ExperimentLaunchContext(
                    launch_context_id=ids["context"],
                    project_id=ids["project"],
                    global_experiment_id=ids["global"],
                    domain_experiment_id=ids["domain"],
                    workflow_id=ids["workflow"],
                    workflow_revision_id=ids["workflow_revision"],
                    preparation_id=ids["preparation"],
                    run_attempt_id=ids["attempt"],
                    contract_version="2",
                    normalized_request_sha256=normalized_sha,
                    validation_receipt_id=ids["validation"],
                    validation_receipt_sha256=validation_sha,
                    source_receipt_id=ids["workflow_revision"],
                    return_uri=(
                        f"/projects/{ids['project']}?focus={ids['global']}"
                        f"&selected=domain_experiment:{ids['domain']}"
                    ),
                    state="reserved",
                    issued_at=issued_at,
                    expires_at="2099-08-25T12:15:00Z",
                )
            )
            await session.commit()

    asyncio.run(prepare())

    async def override_core_session():
        async with core_sessions() as session:
            yield session

    @asynccontextmanager
    async def override_experiment_session():
        async with experiment_sessions() as session:
            yield session

    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: _Catalog())
    monkeypatch.setattr(
        molecular_dynamics,
        "experiment_session_factory",
        override_experiment_session,
        raising=False,
    )
    monkeypatch.setattr(
        starting_structures, "get_allowed_roots", lambda: {"results": results_root}
    )
    main.app.dependency_overrides[get_session] = override_core_session
    test_client = TestClient(main.app)
    try:
        yield {
            "client": test_client,
            "experiment_sessions": experiment_sessions,
            "core_sessions": core_sessions,
            "ids": ids,
            "expected_design_id": expected_design_id,
            "other_design_id": other_design_id,
            "results_root": results_root,
            "scheduler_job_id": scheduler_job_id,
        }
    finally:
        test_client.close()
        main.app.dependency_overrides.pop(get_session, None)
        asyncio.run(core_engine.dispose())
        asyncio.run(experiment_engine.dispose())


@pytest.mark.parametrize(
    ("case", "expected_status", "expected_code"),
    [
        ("valid", 200, None),
        ("unknown", 404, "launch_context_unknown"),
        ("expired", 410, "launch_context_expired"),
        ("consumed", 409, "launch_context_consumed"),
        ("historical_v1", 409, "launch_context_version_read_only"),
        ("missing_preparation", 409, "launch_context_binding_invalid"),
        ("wrong_workflow", 409, "launch_context_hierarchy_mismatch"),
        ("wrong_revision", 409, "launch_context_revision_mismatch"),
        ("wrong_preparation", 409, "launch_context_preparation_mismatch"),
        ("wrong_scope", 409, "launch_context_scope_mismatch"),
        ("matching_non_design_ref", 409, "launch_context_source_mismatch"),
        ("unbound_non_design_intent", 409, "launch_context_source_mismatch"),
        ("duplicate_matching_design_ref", 409, "launch_context_preparation_mismatch"),
        ("multiple_design_refs", 409, "launch_context_preparation_mismatch"),
        ("source_design_mismatch", 409, "launch_context_source_mismatch"),
    ],
)
def test_preview_read_only_validates_current_project_v2_context(
    project_context_preview_store,
    case: str,
    expected_status: int,
    expected_code: str | None,
) -> None:
    from experiment_models import (
        ExperimentDispatchOutbox,
        ExperimentLaunchContext,
        ExperimentRevision,
        ExperimentWorkflowPreparation,
    )
    from database import Job
    from sqlalchemy import func, select

    store = project_context_preview_store
    ids = store["ids"]
    launch_context_id = ids["context"]
    source_design_id = store["expected_design_id"]

    async def mutate_and_snapshot():
        requested_context_id = launch_context_id
        async with store["experiment_sessions"]() as session:
            context = await session.get(ExperimentLaunchContext, ids["context"])
            if case == "expired":
                context.expires_at = "2000-01-01T00:00:00Z"
            elif case == "consumed":
                context.state = "consumed"
            elif case == "historical_v1":
                context.contract_version = "1"
            elif case == "missing_preparation":
                context.preparation_id = "preparation:missing"
            elif case == "wrong_workflow":
                context.workflow_id = "workflow:missing"
            elif case == "wrong_revision":
                context.workflow_revision_id = "revision:missing"
            elif case == "wrong_preparation":
                preparation = await session.get(
                    ExperimentWorkflowPreparation, ids["preparation"]
                )
                preparation.workflow_revision_id = "revision:other"
            elif case == "wrong_scope":
                revision = await session.get(ExperimentRevision, ids["domain_revision"])
                revision.canonical_payload = json.dumps(
                    {"domain_kind": "ngs_molbio"}, sort_keys=True, separators=(",", ":")
                )
            elif case in {
                "matching_non_design_ref",
                "unbound_non_design_intent",
                "duplicate_matching_design_ref",
                "multiple_design_refs",
            }:
                workflow_revision = await session.get(
                    ExperimentRevision, ids["workflow_revision"]
                )
                preparation = await session.get(
                    ExperimentWorkflowPreparation, ids["preparation"]
                )
                prepared_workflow = json.loads(workflow_revision.canonical_payload)
                prepared_request = json.loads(preparation.normalized_request_json)
                scheduler_params = prepared_workflow["scheduler"]["params"]
                if case == "matching_non_design_ref":
                    scheduler_params["source_ref"] = {
                        "kind": "managed_fixture",
                        "id": "1aki-admitted-v1",
                    }
                elif case == "unbound_non_design_intent":
                    scheduler_params.pop("source_ref")
                elif case == "duplicate_matching_design_ref":
                    prepared_request["source_ref"] = {
                        "kind": "design",
                        "id": store["expected_design_id"],
                    }
                else:
                    prepared_request["source_ref"] = {
                        "kind": "design",
                        "id": store["other_design_id"],
                    }
                prepared_request["workflow"] = prepared_workflow
                workflow_revision.canonical_payload = json.dumps(
                    prepared_workflow,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
                preparation.scheduler_payload_json = json.dumps(
                    prepared_workflow["scheduler"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
                preparation.normalized_request_json = json.dumps(
                    prepared_request,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
                preparation.normalized_request_sha256 = hashlib.sha256(
                    preparation.normalized_request_json.encode("utf-8")
                ).hexdigest()
                context.normalized_request_sha256 = preparation.normalized_request_sha256
            elif case == "unknown":
                requested_context_id = "launch-context:unknown"
            await session.commit()
            observed = await session.get(ExperimentLaunchContext, ids["context"])
            return requested_context_id, (
                (
                    observed.state,
                    observed.claim_token,
                    observed.canonical_job_id,
                    observed.binding_receipt_json,
                    observed.consumed_at,
                ),
                await session.scalar(select(func.count(ExperimentDispatchOutbox.id))),
            )

    launch_context_id, before = asyncio.run(mutate_and_snapshot())
    async def core_job_count():
        async with store["core_sessions"]() as session:
            return await session.scalar(select(func.count(Job.id)))

    core_jobs_before = asyncio.run(core_job_count())
    output_paths_before = sorted(
        str(path.relative_to(store["results_root"]))
        for path in store["results_root"].rglob("*")
    )
    if case == "source_design_mismatch":
        source_design_id = store["other_design_id"]
    intent = _intent_payload()
    intent["source_ref"] = (
        {"kind": "managed_fixture", "id": "1aki-admitted-v1"}
        if case in {"matching_non_design_ref", "unbound_non_design_intent"}
        else {"kind": "design", "id": source_design_id}
    )
    intent["launch_context_id"] = launch_context_id
    response = store["client"].post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": intent,
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_code is not None:
        assert response.json()["detail"]["code"] == expected_code

    async def snapshot_after():
        async with store["experiment_sessions"]() as session:
            observed = await session.get(ExperimentLaunchContext, ids["context"])
            return (
                (
                    observed.state,
                    observed.claim_token,
                    observed.canonical_job_id,
                    observed.binding_receipt_json,
                    observed.consumed_at,
                ),
                await session.scalar(select(func.count(ExperimentDispatchOutbox.id))),
            )

    after = asyncio.run(snapshot_after())
    assert after == before
    assert asyncio.run(core_job_count()) == core_jobs_before
    assert sorted(
        str(path.relative_to(store["results_root"]))
        for path in store["results_root"].rglob("*")
    ) == output_paths_before


class _BindingProbe(RuntimeError):
    pass


class _BindingProbeSession:
    async def get(self, *_args, **_kwargs):
        return None

    async def rollback(self) -> None:
        return None


class _AcceptingRegistry:
    def reload(self) -> None:
        return None

    def validate_job_params(self, *_args, **_kwargs) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_canonical_job_wrapper_binds_trusted_md_seams_by_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import BackgroundTasks
    from routers import jobs
    from schemas import JobCreate
    from services import gpu_orchestrator

    results_root = tmp_path / "results"
    results_root.mkdir()
    trusted_source = tmp_path / "trusted-source.pdb"
    trusted_source.write_text("ATOM      1  N   ALA A   1      11.104  13.207   9.997  1.00 20.00           N\n")
    opaque_token = "bms-md-starting-structure:binding-probe"
    calls: list[str] = []
    output_receipt: dict = {}

    def trusted_resolver(value: str) -> str:
        assert value == opaque_token
        calls.append("trusted-resolver")
        return str(trusted_source)

    def generic_resolver(value: str) -> str:
        raise AssertionError(f"opaque token reached generic resolver: {value}")

    def normalize(*, params, resolve_runtime_path, **_kwargs):
        assert resolve_runtime_path(opaque_token) == str(trusted_source)
        calls.append("normalize")
        return dict(params["md_job_spec"])

    def materialize(*, params, output_dir, resolve_runtime_path, **_kwargs):
        assert resolve_runtime_path(opaque_token) == str(trusted_source)
        assert output_dir.is_relative_to(results_root)
        calls.append("materialize")
        raise _BindingProbe("trusted MD seams reached materialization")

    monkeypatch.setattr(jobs, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(jobs, "_resolve_md_input_path_for_runtime", generic_resolver)
    monkeypatch.setattr(jobs, "normalize_md_job_spec", normalize)
    monkeypatch.setattr(jobs, "materialize_md_job_spec", materialize)
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_args, **_kwargs: 0)

    with pytest.raises(_BindingProbe, match="trusted MD seams"):
        await jobs.create_job(
            JobCreate(
                name="typed-binding-probe",
                model_id="molecular_dynamics",
                mode="simulate",
                params={
                    "md_job_spec": {
                        "schema": "bms.md.job.v2",
                        "input": {"structure": opaque_token},
                    }
                },
            ),
            BackgroundTasks(),
            _BindingProbeSession(),
            _preallocated_job_id=None,
            _commit=True,
            _skip_parent_lineage_update=False,
            _md_output_creation=output_receipt,
            _md_input_resolver=trusted_resolver,
            experiment_session=None,
        )

    assert calls == ["trusted-resolver", "normalize", "trusted-resolver", "materialize"]
    assert output_receipt["created"] is True
    assert output_receipt["path"].is_relative_to(results_root)


@pytest.mark.asyncio
async def test_canonical_job_wrapper_keeps_generic_md_defaults_without_internal_seams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import BackgroundTasks
    from routers import jobs
    from schemas import JobCreate
    from services import gpu_orchestrator

    results_root = tmp_path / "results"
    results_root.mkdir()
    generic_source = tmp_path / "generic-source.pdb"
    generic_source.write_text("ATOM      1  N   ALA A   1      11.104  13.207   9.997  1.00 20.00           N\n")
    calls: list[str] = []

    def generic_resolver(value: str) -> str:
        assert value == str(generic_source)
        calls.append("generic-resolver")
        return value

    def normalize(*, params, resolve_runtime_path, **_kwargs):
        assert resolve_runtime_path(str(generic_source)) == str(generic_source)
        calls.append("normalize")
        return dict(params["md_job_spec"])

    def materialize(*, resolve_runtime_path, **_kwargs):
        assert resolve_runtime_path(str(generic_source)) == str(generic_source)
        calls.append("materialize")
        raise _BindingProbe("generic MD defaults reached materialization")

    monkeypatch.setattr(jobs, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(jobs, "_resolve_md_input_path_for_runtime", generic_resolver)
    monkeypatch.setattr(jobs, "normalize_md_job_spec", normalize)
    monkeypatch.setattr(jobs, "materialize_md_job_spec", materialize)
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_args, **_kwargs: 0)

    with pytest.raises(_BindingProbe, match="generic MD defaults"):
        await jobs.create_job(
            JobCreate(
                name="generic-binding-probe",
                model_id="molecular_dynamics",
                mode="simulate",
                params={
                    "md_job_spec": {
                        "schema": "bms.md.job.v2",
                        "input": {"structure": str(generic_source)},
                    }
                },
            ),
            BackgroundTasks(),
            _BindingProbeSession(),
            _preallocated_job_id=None,
            _commit=True,
            _skip_parent_lineage_update=False,
            experiment_session=None,
        )

    assert calls == ["generic-resolver", "normalize", "generic-resolver", "materialize"]


@pytest.mark.asyncio
async def test_canonical_job_context_branch_preserves_trusted_adapter_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import BackgroundTasks
    from routers import gpu, jobs
    from schemas import JobCreate
    from services import gpu_orchestrator

    results_root = tmp_path / "results"
    results_root.mkdir()
    input_pdb = tmp_path / "input.pdb"
    input_pdb.write_text("ATOM      1  N   ALA A   1      11.104  13.207   9.997  1.00 20.00           N\n")
    launch_context_id = "launch-context:binding-probe"
    context = SimpleNamespace(contract_version="1")

    class _ExperimentSession:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def resolve_context(_session, requested_id):
        assert requested_id == launch_context_id
        return context

    async def validate_request(_session, _context, **kwargs):
        return kwargs["params"]

    async def claim_context(_session, requested_id):
        assert requested_id == launch_context_id
        return context, "claim-token"

    def stop_after_trusted_gate(*_args, **_kwargs):
        raise _BindingProbe("trusted context adapter passed its ownership gate")

    monkeypatch.setattr(jobs, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(jobs, "resolve_launch_context", resolve_context)
    monkeypatch.setattr(jobs, "validate_bound_job_request", validate_request)
    monkeypatch.setattr(jobs, "claim_launch_context", claim_context)
    monkeypatch.setattr(jobs, "normalize_plr_input_pdb_path", lambda params, **_kwargs: params)
    monkeypatch.setattr(jobs, "prepare_local_redesign_scheduler_params", lambda params, **_kwargs: params)
    monkeypatch.setattr(jobs, "materialize_local_redesign_request", stop_after_trusted_gate)
    monkeypatch.setattr(gpu, "get_gpu_stats_with_error", lambda _refresh: ([SimpleNamespace(index=0)], None))
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_args, **_kwargs: 0)

    token = jobs.current_launch_context_id.set(launch_context_id)
    try:
        with pytest.raises(_BindingProbe, match="trusted context adapter"):
            await jobs.create_job(
                JobCreate(
                    name="trusted-context-probe",
                    model_id="protein_local_redesign",
                    mode="local_redesign",
                    pinned_gpu=0,
                    launch_context_id=launch_context_id,
                    params={
                        "workflow_adapter": "native-rfd3",
                        "input_pdb": str(input_pdb),
                    },
                ),
                BackgroundTasks(),
                _BindingProbeSession(),
                _preallocated_job_id=None,
                _commit=True,
                _skip_parent_lineage_update=False,
                _md_output_creation=None,
                _md_input_resolver=None,
                experiment_session=_ExperimentSession(),
            )
    finally:
        jobs.current_launch_context_id.reset(token)


@pytest.mark.asyncio
async def test_typed_launch_preserves_optional_launch_context_wrapper_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from routers import jobs, molecular_dynamics
    from services.md import starting_structures

    intent_payload = {**_intent_payload(), "launch_context_id": "context-one"}
    intent = starting_structures.MdLaunchIntent.model_validate(intent_payload)
    resolved = starting_structures.resolve_product_source(intent.source_ref)
    preview = starting_structures.compile_launch_preview(
        intent=intent,
        resolved=resolved,
        profile=_profile(),
        current_catalog_digest=CATALOG_DIGEST,
    )
    request = starting_structures.MdLaunchRequest(
        schema_version="bms.md.launch-request.v1",
        intent=intent,
        preview_digest=preview.preview_digest,
    )
    experiment_session = object()
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_factory():
        yield experiment_session

    async def fake_create_job(*args, **kwargs):
        calls.append(kwargs)
        return "created"

    async def validated_elsewhere(_intent):
        return None

    monkeypatch.setattr(molecular_dynamics, "get_chemistry_catalog", lambda: _Catalog())
    monkeypatch.setattr(
        molecular_dynamics, "_validate_preview_launch_context", validated_elsewhere
    )
    monkeypatch.setattr(
        molecular_dynamics, "experiment_session_factory", fake_factory, raising=False
    )
    monkeypatch.setattr(jobs, "create_job", fake_create_job)

    result = await molecular_dynamics.launch_typed_md_job(request, _UnusedSession())
    assert result == "created"
    assert len(calls) == 1
    call = calls[0]
    assert call["_md_output_creation"] == {}
    assert callable(call["_md_input_resolver"])
    assert call["experiment_session"] is experiment_session
    adapter = call["_typed_md_project_launch"]
    assert isinstance(adapter, jobs.TypedMdProjectLaunch)
    assert adapter.request_schema_version == "bms.md.launch-request.v1"
    assert adapter.intent["launch_context_id"] == "context-one"
    assert adapter.preview_digest == preview.preview_digest


def test_project_v2_typed_md_launch_reaches_canonical_job_and_consumes_context(
    project_context_preview_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import Job
    from experiment_models import (
        ExperimentDispatchOutbox,
        ExperimentLaunchContext,
        ExperimentRunAttempt,
    )
    from routers import jobs
    from services import gpu_orchestrator, ngs_molbio_capabilities
    from sqlalchemy import func, select

    store = project_context_preview_store
    ids = store["ids"]
    intent = {
        **_intent_payload(),
        "name": "Typed-MD-launch",
        "source_ref": {"kind": "design", "id": store["expected_design_id"]},
        "launch_context_id": ids["context"],
    }
    preview_response = store["client"].post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": intent,
        },
    )
    assert preview_response.status_code == 200, preview_response.text

    leaf_calls: list[str] = []

    def normalize(*, params, job_id, resolve_runtime_path, **_kwargs):
        token = params["md_job_spec"]["input"]["structure"]
        resolved_path = Path(resolve_runtime_path(token))
        assert resolved_path.read_bytes() == (
            API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb"
        ).read_bytes()
        leaf_calls.append(f"normalize:{job_id}")
        return dict(params["md_job_spec"])

    def materialize(*, params, job_id, output_dir, resolve_runtime_path, **_kwargs):
        token = params["md_job_spec"]["input"]["structure"]
        resolved_path = Path(resolve_runtime_path(token))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "typed-md-materialized.txt").write_text(
            resolved_path.name, encoding="utf-8"
        )
        compiled = json.loads(json.dumps(params["md_job_spec"]))
        compiled["job_id"] = job_id
        compiled["input"]["structure"] = str(resolved_path)
        compiled["chemistry"]["assurance"] = "typed-md-test-materialization"
        leaf_calls.append(f"materialize:{job_id}")
        return {**params, "md_job_spec": compiled}

    source_pin = json.loads(
        (
            API_ROOT / "config" / "ngs_molbio" / "source_pin_v1.json"
        ).read_text(encoding="utf-8")
    )
    candidate_source_authorities = {}
    verification_receipt = json.loads(
        (
            API_ROOT.parents[1]
            / "docs"
            / "reports"
            / "ngs-molbio-phase-n0-verification-v1.json"
        ).read_text(encoding="utf-8")
    )
    candidate_rows = [
        *source_pin["authorities"],
        *verification_receipt["payload_files"],
    ]
    for row in candidate_rows:
        candidate_path = API_ROOT.parents[1] / row["path"]
        candidate_bytes = candidate_path.read_bytes()
        candidate_source_authorities[row["path"]] = {
            "path": row["path"],
            "size_bytes": len(candidate_bytes),
            "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        }

    monkeypatch.setattr(jobs, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, "get_results_dir", lambda: store["results_root"])
    monkeypatch.setattr(jobs, "normalize_md_job_spec", normalize)
    monkeypatch.setattr(jobs, "materialize_md_job_spec", materialize)
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        ngs_molbio_capabilities,
        "_runtime_overlay_authorities",
        lambda _receipt=None: candidate_source_authorities,
    )

    response = store["client"].post(
        "/api/molecular-dynamics/launch",
        headers={"x-bms-launch-context-id": ids["context"]},
        json={
            "schema_version": "bms.md.launch-request.v1",
            "intent": intent,
            "preview_digest": preview_response.json()["preview_digest"],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["id"] == store["scheduler_job_id"]
    assert payload["model_id"] == "molecular_dynamics"
    assert payload["mode"] == "simulate"
    assert payload["launch_context_id"] == ids["context"]
    assert payload["return_uri"].startswith(f"/projects/{ids['project']}")
    assert leaf_calls == [
        "normalize:validation-preview",
        f"materialize:{store['scheduler_job_id']}",
    ]

    async def durable_state():
        async with store["core_sessions"]() as core_session:
            jobs_found = list((await core_session.scalars(select(Job))).all())
            created = await core_session.get(Job, store["scheduler_job_id"])
        async with store["experiment_sessions"]() as experiment_session:
            context = await experiment_session.get(
                ExperimentLaunchContext, ids["context"]
            )
            attempt = await experiment_session.get(ExperimentRunAttempt, ids["attempt"])
            outbox_count = await experiment_session.scalar(
                select(func.count(ExperimentDispatchOutbox.id))
            )
        return jobs_found, created, context, attempt, outbox_count

    jobs_found, created, context, attempt, outbox_count = asyncio.run(durable_state())
    assert len(jobs_found) == 2
    assert created is not None
    assert created.queue_status == "queued"
    assert created.provenance["launch_context_id"] == ids["context"]
    assert context.state == "consumed"
    assert context.canonical_job_id == store["scheduler_job_id"]
    assert context.binding_receipt_json
    assert attempt.state == "dispatched"
    assert outbox_count == 0
    assert (store["results_root"] / store["scheduler_job_id"] / "typed-md-materialized.txt").is_file()


@pytest.mark.parametrize(
    "case",
    [
        "normalized_setting",
        "source_design",
        "chemistry_profile",
        "chemistry_profile_digest",
        "catalog_digest",
        "stale_preview",
        "arbitrary_flag",
        "caller_server_field",
    ],
)
def test_typed_md_project_adapter_rejects_divergent_authority_without_side_effects(
    project_context_preview_store,
    case: str,
) -> None:
    from database import Job
    from experiment_models import (
        ExperimentDispatchOutbox,
        ExperimentLaunchContext,
        ExperimentRunAttempt,
    )
    from fastapi import BackgroundTasks, HTTPException
    from routers import jobs
    from schemas import JobCreate
    from services.md import starting_structures
    from sqlalchemy import func, select

    store = project_context_preview_store
    ids = store["ids"]
    intent = {
        **_intent_payload(),
        "name": "Typed-MD-negative",
        "source_ref": {"kind": "design", "id": store["expected_design_id"]},
        "launch_context_id": ids["context"],
    }
    preview_response = store["client"].post(
        "/api/molecular-dynamics/launch-preview",
        json={
            "schema_version": "bms.md.launch-preview-request.v1",
            "intent": intent,
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    source_token = "bms-md-starting-structure:negative-probe"
    md_job_spec = starting_structures.compile_md_job_v2(
        preview=starting_structures.MdLaunchPreview.model_validate(preview),
        profile=_profile(),
        job_id="assigned-by-server",
        source_token=source_token,
    )
    adapter_intent = json.loads(json.dumps(intent))
    adapter_preview = json.loads(json.dumps(preview))
    adapter_spec = json.loads(json.dumps(md_job_spec))
    adapter_digest = preview["preview_digest"]
    job_params = {"md_job_spec": adapter_spec}

    if case == "normalized_setting":
        adapter_intent["requested_settings"]["random_seed"] += 1
        adapter_preview["requested_settings"]["random_seed"] += 1
        adapter_preview["effective_request"]["random_seed"] += 1
        adapter_spec["random_seed"] += 1
        adapter_digest = adapter_preview["preview_digest"] = "c" * 64
    elif case == "source_design":
        other_ref = {"kind": "design", "id": store["other_design_id"]}
        adapter_intent["source_ref"] = other_ref
        adapter_preview["source"]["source_ref"] = other_ref
        adapter_digest = adapter_preview["preview_digest"] = "c" * 64
    elif case == "chemistry_profile":
        adapter_intent["chemistry_profile_id"] = "other-profile"
        adapter_preview["chemistry"]["profile_id"] = "other-profile"
        adapter_spec["chemistry"]["profile_id"] = "other-profile"
        adapter_digest = adapter_preview["preview_digest"] = "c" * 64
    elif case == "chemistry_profile_digest":
        adapter_intent["chemistry_profile_sha256"] = "d" * 64
        adapter_preview["chemistry"]["profile_sha256"] = "d" * 64
        adapter_spec["chemistry"]["profile_sha256"] = "d" * 64
        adapter_digest = adapter_preview["preview_digest"] = "c" * 64
    elif case == "catalog_digest":
        adapter_intent["catalog_digest"] = "e" * 64
        adapter_preview["chemistry"]["catalog_digest"] = "e" * 64
        adapter_spec["chemistry"]["catalog_digest"] = "e" * 64
        adapter_digest = adapter_preview["preview_digest"] = "c" * 64
    elif case == "stale_preview":
        adapter_digest = "0" * 64
    elif case == "caller_server_field":
        job_params["_trusted_workflow_adapter"] = True

    adapter = (
        True
        if case == "arbitrary_flag"
        else jobs.TypedMdProjectLaunch(
            request_schema_version="bms.md.launch-request.v1",
            intent=adapter_intent,
            preview=adapter_preview,
            preview_digest=adapter_digest,
            md_job_spec=adapter_spec,
            source_token=source_token,
        )
    )
    before_paths = sorted(
        str(path.relative_to(store["results_root"]))
        for path in store["results_root"].rglob("*")
    )

    async def reject_and_snapshot():
        async with store["core_sessions"]() as core_session:
            async with store["experiment_sessions"]() as experiment_session:
                token = jobs.current_launch_context_id.set(ids["context"])
                try:
                    with pytest.raises(HTTPException) as rejected:
                        await jobs.create_job(
                            JobCreate(
                                name="Typed-MD-negative",
                                model_id="molecular_dynamics",
                                mode="simulate",
                                params=job_params,
                                launch_context_id=ids["context"],
                            ),
                            BackgroundTasks(),
                            core_session,
                            _preallocated_job_id=None,
                            _commit=True,
                            _skip_parent_lineage_update=False,
                            _md_output_creation={},
                            _md_input_resolver=lambda value: value,
                            _typed_md_project_launch=adapter,
                            experiment_session=experiment_session,
                        )
                finally:
                    jobs.current_launch_context_id.reset(token)
                assert rejected.value.status_code == 409
                await experiment_session.rollback()
                context = await experiment_session.get(
                    ExperimentLaunchContext, ids["context"]
                )
                attempt = await experiment_session.get(
                    ExperimentRunAttempt, ids["attempt"]
                )
                outbox_count = await experiment_session.scalar(
                    select(func.count(ExperimentDispatchOutbox.id))
                )
            job_count = await core_session.scalar(select(func.count(Job.id)))
        return context, attempt, outbox_count, job_count

    context, attempt, outbox_count, job_count = asyncio.run(reject_and_snapshot())
    assert context.state == "reserved"
    assert context.claim_token is None
    assert context.canonical_job_id is None
    assert context.binding_receipt_json is None
    assert context.consumed_at is None
    assert attempt.state == "pending"
    assert outbox_count == 0
    assert job_count == 1
    assert sorted(
        str(path.relative_to(store["results_root"]))
        for path in store["results_root"].rglob("*")
    ) == before_paths


def test_ordinary_no_context_jobs_endpoint_remains_canonical(
    project_context_preview_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import Job
    from routers import jobs
    from services import gpu_orchestrator
    from sqlalchemy import func, select

    store = project_context_preview_store
    monkeypatch.setattr(jobs, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs, "get_results_dir", lambda: store["results_root"])
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_args, **_kwargs: 0)

    response = store["client"].post(
        "/api/jobs",
        json={
            "name": "ordinary-no-context",
            "model_id": "generic_probe",
            "mode": "run",
            "params": {},
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["model_id"] == "generic_probe"
    assert payload["mode"] == "run"
    assert payload["launch_context_id"] is None

    async def job_count():
        async with store["core_sessions"]() as session:
            return await session.scalar(select(func.count(Job.id)))

    assert asyncio.run(job_count()) == 2
