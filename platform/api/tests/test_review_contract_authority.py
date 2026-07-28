from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import asyncio
import json
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import result_contracts


def test_unknown_result_set_fails_closed_instead_of_falling_through_to_broad_family() -> None:
    contract = result_contracts.resolve_result_contract(
        result_set="unrecognized_result_set",
        stage_family="rfantibody",
        artifact_class="backbone_complex",
    )

    assert contract.analysis_contract_id is None
    assert contract.contract_source == "unsupported"


def test_persisted_review_profile_is_authoritative_over_conflicting_legacy_selectors() -> None:
    contract = result_contracts.resolve_result_contract(
        review_profile_id="structure_prediction_v1",
        result_set="rfantibody_backbones",
        stage_family="rfantibody",
        artifact_class="backbone_complex",
    )

    assert contract.analysis_contract_id == "structure_prediction_v1"
    assert contract.contract_source == "persisted"
    assert "antibody_annotation_pack" not in contract.supported_analyzers


def test_boltzgen_resolves_to_de_novo_review_not_sequence_design() -> None:
    contract = result_contracts.resolve_result_contract(
        stage_family="boltzgen",
        stage_mode="backbone_generation",
        provenance={"model_id": "boltzgen"},
    )

    assert contract.analysis_contract_id == "de_novo_generation_v1"
    assert "de_novo_generation_metrics" in contract.viewer_capabilities
    assert "sequence_design_metrics" not in contract.viewer_capabilities


def test_typed_artifact_manifest_reports_ready_missing_and_role_state(tmp_path: Path) -> None:
    builder = getattr(result_contracts, "build_review_artifact_manifest", None)
    assert callable(builder), "typed review artifact manifest builder is required"
    structure_path = tmp_path / "design.pdb"
    structure_path.write_text("END\n", encoding="utf-8")

    manifest = builder(
        SimpleNamespace(
            pdb_path=str(structure_path),
            aligned_error_path=None,
            artifact_class="validated_complex",
            review_role_map={"chain_roles": {"A": "target"}},
        )
    )

    assert manifest["schema"] == "bms.review-artifacts.v1"
    assert manifest["artifacts"]["structure"]["state"] == "ready"
    assert manifest["artifacts"]["aligned_error"]["state"] == "missing"
    assert manifest["roles"]["chain_roles"] == {"A": "target"}
    assert manifest["roles"]["has_binder"] is False


def test_artifact_manifest_persists_declared_path_across_runtime_path_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    declared_root = tmp_path / "host-data"
    runtime_root = tmp_path / "container-data"
    declared_path = declared_root / "bms_results" / "job-1" / "design.cif"
    runtime_path = runtime_root / "bms_results" / "job-1" / "design.cif"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text("data_test\n", encoding="utf-8")

    monkeypatch.setattr(
        result_contracts,
        "resolve_runtime_data_path",
        lambda candidate: runtime_path if Path(candidate) == declared_path else Path(candidate),
    )

    manifest = result_contracts.build_review_artifact_manifest(
        SimpleNamespace(
            pdb_path=str(declared_path),
            aligned_error_path=None,
            review_role_map={},
        )
    )

    assert manifest["artifacts"]["structure"]["state"] == "ready"
    assert manifest["artifacts"]["structure"]["path"] == str(declared_path)


def test_analysis_allowlist_is_derived_from_authoritative_profile() -> None:
    checker = getattr(result_contracts, "is_design_analysis_allowed", None)
    assert callable(checker), "analysis contract enforcement helper is required"

    antibody = SimpleNamespace(
        review_profile_id="antibody_backbone_v1",
        stage_family="rfantibody",
        stage_mode="backbone_generation",
        artifact_class="backbone_complex",
        provenance={"model_id": "rfantibody"},
    )
    structure = SimpleNamespace(
        review_profile_id="structure_prediction_v1",
        stage_family="boltz2",
        stage_mode="validation",
        artifact_class="validated_complex",
        provenance={"model_id": "boltz2"},
    )

    assert checker(antibody, "antibody_annotation_pack") is True
    assert checker(structure, "antibody_annotation_pack") is False
    assert checker(structure, "structure_summary") is True


def test_analysis_readiness_requires_resolvable_artifacts(tmp_path: Path) -> None:
    validator = getattr(result_contracts, "validate_design_analysis_request", None)
    assert callable(validator), "analysis artifact readiness validator is required"

    structure_path = tmp_path / "ready.pdb"
    missing_structure = SimpleNamespace(
        review_profile_id="structure_prediction_v1",
        stage_family="boltz2",
        stage_mode="validation",
        artifact_class="validated_complex",
        provenance={"model_id": "boltz2"},
        pdb_path=str(structure_path),
        aligned_error_path=None,
        aligned_error_format=None,
        review_role_map={},
    )
    missing_reason = validator(missing_structure, "structure_summary")
    structure_path.write_text("END\n", encoding="utf-8")
    ready_reason = validator(missing_structure, "structure_summary")

    assert missing_reason == "required artifact 'structure' is not ready"
    assert ready_reason is None


def test_inline_pae_or_forged_manifest_cannot_replace_raw_aligned_error_artifact() -> None:
    design = SimpleNamespace(
        review_profile_id="structure_prediction_v1",
        result_set=None,
        stage_family="boltz2",
        stage_mode="validation",
        artifact_class="validated_complex",
        provenance={},
        pdb_path=None,
        aligned_error_path=None,
        aligned_error_format=None,
        pae_matrix=[[0.0]],
        review_role_map={},
        review_artifact_manifest={
            "schema": "bms.review-artifacts.v1",
            "artifacts": {"aligned_error": {"state": "ready", "path": "/forged/pae.json"}},
        },
    )

    assert result_contracts.validate_design_analysis_request(design, "pae_matrix") == (
        "required artifact 'aligned_error' is not ready"
    )


def test_job_analysis_scope_filters_designs_by_persisted_review_profile() -> None:
    from database import Design
    from services.analysis_registry import _design_supports_job_analysis

    antibody = Design(id="antibody", job_id="job", name="antibody", pdb_path="")
    antibody.review_profile_id = "antibody_backbone_v1"
    sequence = Design(id="sequence", job_id="job", name="sequence", pdb_path="")
    sequence.review_profile_id = "sequence_design_v1"
    compatibility_shaped = Design(
        id="compatibility-shaped",
        job_id="job",
        name="compatibility-shaped",
        pdb_path="",
        stage_family="rfantibody",
        artifact_class="backbone_complex",
    )

    assert _design_supports_job_analysis(antibody, "job_cdr_logo_pack") is True
    assert _design_supports_job_analysis(antibody, "job_aa_composition") is True
    assert _design_supports_job_analysis(antibody, "job_correlation_matrix") is True
    assert _design_supports_job_analysis(sequence, "job_cdr_logo_pack") is False
    assert _design_supports_job_analysis(sequence, "job_correlation_matrix") is False
    assert _design_supports_job_analysis(compatibility_shaped, "job_aa_composition") is False


@pytest.mark.asyncio
async def test_rejected_job_analyzer_creates_zero_runs() -> None:
    from services.analysis_runs import request_job_analysis

    unsupported_design = SimpleNamespace(
        id="unsupported-design",
        job_id="unsupported-job",
        review_profile_id="unsupported_legacy",
        result_set=None,
        stage_family=None,
        stage_mode=None,
        artifact_class=None,
        provenance={},
    )

    class _Result:
        def __init__(self, rows: list[Any]) -> None:
            self.rows = rows

        def all(self) -> list[Any]:
            return self.rows

        def scalars(self) -> "_Result":
            return self

    class _Session:
        def __init__(self) -> None:
            self.execute_calls = 0
            self.add_calls = 0

        async def execute(self, _query):
            self.execute_calls += 1
            return _Result([] if self.execute_calls == 1 else [unsupported_design])

        def add(self, _value) -> None:
            self.add_calls += 1

    session = _Session()
    job: Any = SimpleNamespace(id="unsupported-job")
    with pytest.raises(ValueError, match="authoritative review profile"):
        await request_job_analysis(  # type: ignore[arg-type]
            session,
            job,
            "job_cdr_logo_pack",
            raw_params={"include_children": True},
        )
    assert session.add_calls == 0


def test_rejected_analyzer_creates_zero_runs() -> None:
    from services.analysis_runs import request_design_analysis

    class NoQueueSession:
        def __init__(self) -> None:
            self.add_calls = 0

        def add(self, _value) -> None:
            self.add_calls += 1

    session = NoQueueSession()
    design = SimpleNamespace(
        id="structure-only",
        review_profile_id="structure_prediction_v1",
        result_set=None,
        stage_family="boltz2",
        stage_mode="validation",
        artifact_class="validated_complex",
        provenance={},
        pdb_path="result.pdb",
        aligned_error_path=None,
        pae_matrix=None,
        review_artifact_manifest=None,
    )

    try:
        asyncio.run(request_design_analysis(
            session,  # type: ignore[arg-type]
            design,  # type: ignore[arg-type]
            "antibody_annotation_pack",
        ))
    except ValueError as exc:
        assert "not allowed by review profile 'structure_prediction_v1'" in str(exc)
    else:
        raise AssertionError("unsupported analyzer request must be rejected")
    assert session.add_calls == 0


@pytest.mark.asyncio
async def test_analysis_endpoint_rejects_before_queue_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from routers import analyses as analyses_router

    design = SimpleNamespace(
        id="unsupported-design",
        review_profile_id="sequence_design_v1",
        review_contract_version=1,
        review_contract_source="producer",
        review_artifact_manifest={
            "schema": "bms.review-artifacts.v1",
            "artifacts": {"structure": {"state": "ready", "path": "/tmp/design.pdb"}},
            "roles": {"has_binder": False, "has_antibody": False},
        },
        review_role_map={"has_binder": False},
        provenance={},
        stage_family="sequence_design",
        stage_mode="fampnn",
        artifact_class="sequence_design",
        model_type="fampnn",
    )

    class _Result:
        def scalar_one_or_none(self):
            return design

    class _Session:
        async def execute(self, _statement):
            return _Result()

    queue_calls = 0

    async def _queue_should_not_run(*_args, **_kwargs):
        nonlocal queue_calls
        queue_calls += 1
        raise AssertionError("queue service must not run")

    monkeypatch.setattr(analyses_router, "request_design_analysis", _queue_should_not_run)
    with pytest.raises(HTTPException) as exc_info:
        await analyses_router.trigger_design_analysis(
            design.id,
            "ipsae_interface",
            analyses_router.AnalysisRunRequest(params={}, force_refresh=False),
            session=_Session(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert queue_calls == 0


@pytest.mark.asyncio
async def test_legacy_analytics_cache_route_rejects_before_cache_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from routers import analytics

    cache_called = False

    async def _reject(*_args, **_kwargs):
        raise ValueError("job analysis has no review-compatible designs in scope")

    async def _cache_lookup(*_args, **_kwargs):
        nonlocal cache_called
        cache_called = True
        raise AssertionError("cache lookup must not run after authority rejection")

    monkeypatch.setattr(analytics, "validate_job_analysis_request", _reject)
    monkeypatch.setattr(analytics, "get_matching_job_analysis_run", _cache_lookup)

    job: Any = SimpleNamespace(id="unsupported-job")
    with pytest.raises(HTTPException, match="review-compatible") as exc_info:
        await analytics._get_cached_job_analysis_payload(  # type: ignore[arg-type]
            SimpleNamespace(),
            job,
            "job_cdr_logo_pack",
        )

    assert exc_info.value.status_code == 400
    assert cache_called is False


@pytest.mark.asyncio
async def test_legacy_design_cache_route_rejects_before_cache_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from database import Design
    from routers import designs as designs_router

    cache_called = False

    async def _cache_lookup(*_args, **_kwargs):
        nonlocal cache_called
        cache_called = True
        raise AssertionError("cache lookup must not run after authority rejection")

    monkeypatch.setattr(designs_router, "get_matching_design_analysis_run", _cache_lookup)
    design = Design(id="unsupported-design", job_id="job", name="unsupported", pdb_path="")
    design.review_profile_id = "unsupported_legacy"

    with pytest.raises(HTTPException, match="not allowed") as exc_info:
        await designs_router._get_cached_design_analysis_payload(  # type: ignore[arg-type]
            SimpleNamespace(),
            design,
            "structure_summary",
        )

    assert exc_info.value.status_code == 409
    assert cache_called is False


def test_job_identity_emits_typed_review_profile_and_roles() -> None:
    from services.result_ingester import _job_stage_context

    antibody_job: Any = SimpleNamespace(
        id="rfa-job",
        name="RFA",
        model_id="rfantibody",
        mode="backbone_generation",
        stage_family="rfantibody",
        stage_mode="backbone_generation",
        params={"antigen_chains": "A,B"},
        parent_job_id=None,
        source_stage_job_id=None,
        source_stage_family=None,
        source_stage_mode=None,
        source_selection_count=None,
        selected_input_artifact_class=None,
        selected_input_schema_version=None,
    )
    de_novo_job: Any = SimpleNamespace(
        id="boltzgen-job",
        name="BoltzGen",
        model_id="boltzgen",
        mode="generation",
        stage_family="boltzgen",
        stage_mode="backbone_generation",
        params={},
        parent_job_id=None,
        source_stage_job_id=None,
        source_stage_family=None,
        source_stage_mode=None,
        source_selection_count=None,
        selected_input_artifact_class=None,
        selected_input_schema_version=None,
    )
    antibody_context = _job_stage_context(antibody_job)
    de_novo_context = _job_stage_context(de_novo_job)

    assert antibody_context["review_profile_id"] == "antibody_backbone_v1"
    assert antibody_context["review_contract_source"] == "job_identity"
    assert antibody_context["review_role_map"] == {
        "result_role": "antibody_binder",
        "target_chains": ["A", "B"],
    }
    assert antibody_context["provenance"]["review_profile_id"] == "antibody_backbone_v1"
    assert de_novo_context["review_profile_id"] == "de_novo_generation_v1"
    assert de_novo_context["review_role_map"] is None


@pytest.mark.asyncio
async def test_job_launch_rejects_client_review_authority_fields_before_persistence() -> None:
    from fastapi import BackgroundTasks, HTTPException
    from routers import jobs as jobs_router
    from schemas import JobCreate

    with pytest.raises(HTTPException) as exc_info:
        await jobs_router.create_job(
            JobCreate(
                name="forged-review-job",
                model_id="boltz2",
                mode="predict",
                params={"review_profile_id": "antibody_backbone_v1"},
                pinned_gpu=None,
                parent_job_id=None,
                child_stage=None,
                batch_id=None,
                batch_name=None,
                sequence_length=None,
            ),
            BackgroundTasks(),
            object(),  # type: ignore[arg-type]
        )
    assert exc_info.value.status_code == 422
    assert "server-controlled" in str(exc_info.value.detail)


def test_trusted_producer_contract_is_scoped_to_server_owned_job_identity() -> None:
    from types import SimpleNamespace

    from services.result_ingester import _trusted_producer_review_fields

    producer_payload = {
        "review_profile_id": "de_novo_generation_v1",
        "review_contract_version": 1,
        "review_contract_source": "producer",
        "review_role_map": {
            "result_role": "locally_redesigned_backbone",
            "design_chains": ["A"],
            "context_chains": ["B"],
        },
        "review_artifact_manifest": {
            "schema": "bms.review-artifacts.v1",
            "artifacts": {"structure": {"kind": "structure", "state": "ready", "path": "design_0.pdb"}},
        },
    }
    protein_local_job = SimpleNamespace(model_id="protein_local_redesign")

    trusted = _trusted_producer_review_fields(protein_local_job, producer_payload)
    assert trusted == {
        "review_profile_id": "de_novo_generation_v1",
        "review_contract_version": 1,
        "review_contract_source": "producer",
        "review_role_map": producer_payload["review_role_map"],
    }

    forged_antibody = {**producer_payload, "review_profile_id": "antibody_backbone_v1"}
    assert _trusted_producer_review_fields(protein_local_job, forged_antibody) == {}
    assert _trusted_producer_review_fields(SimpleNamespace(model_id="external_new_model"), producer_payload) == {}
    malformed_manifest = {
        **producer_payload,
        "review_artifact_manifest": {"schema": "bms.review-artifacts.v1"},
    }
    assert _trusted_producer_review_fields(protein_local_job, malformed_manifest) == {}
    malformed_roles = {**producer_payload, "review_role_map": {}}
    assert _trusted_producer_review_fields(protein_local_job, malformed_roles) == {}
    wrong_stage_profile = {**producer_payload, "review_profile_id": "structure_prediction_v1"}
    assert _trusted_producer_review_fields(protein_local_job, wrong_stage_profile) == {}


def test_response_serialization_cannot_infer_authority_from_legacy_metadata() -> None:
    from database import Design
    from routers.designs import _design_to_response

    design = Design(
        id="legacy-shaped",
        job_id="job",
        name="legacy-shaped",
        pdb_path="",
        review_profile_id=None,
        stage_family="rfantibody",
        artifact_class="backbone_complex",
        provenance={"model_id": "rfantibody"},
        ppiflow_objective_score=99.0,
        cdr_h3="FORGED",
        iptm=0.99,
        review_role_map={"result_role": "antibody_binder"},
        is_favorite=False,
        created_at=datetime.utcnow(),
    )

    response = _design_to_response(design)
    assert response.review_profile_id == "unsupported_legacy"
    assert response.analysis_contract_id is None
    assert response.supported_analyzers == []
    assert response.viewer_capabilities == []
    assert response.binder_sequence is None
    assert response.ppiflow_objective_score is None
    assert response.cdr_h3 is None
    assert response.iptm is None
    assert response.review_role_map == {}


def test_response_serialization_tolerates_nullable_legacy_annotation_fields() -> None:
    from database import Design
    from routers.designs import _design_to_response

    design = Design(
        id="legacy-nullable",
        job_id="legacy-job",
        name="legacy-nullable",
        pdb_path="/tmp/legacy-nullable.pdb",
        is_favorite=None,
        created_at=None,
    )

    response = _design_to_response(design)

    assert response.is_favorite is False
    assert response.created_at is None


@pytest.mark.asyncio
async def test_design_list_projection_serializes_material_structure_review_contract(tmp_path: Path) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import load_only, sessionmaker

    from database import Base, Design, Job
    from routers.designs import DESIGN_LIST_LOAD_ONLY_COLUMNS, _design_to_response

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'projected-design.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    structure_path = tmp_path / "esmfold2_candidate.cif"
    structure_path.write_text("data_esmfold2\n", encoding="utf-8")
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Job(id="esm-job", name="ESMFold2", model_id="esmfold2", mode="predict", params={}))
        session.add(
            Design(
                id="esm-design",
                job_id="esm-job",
                name="esmfold2_candidate",
                pdb_path=str(structure_path),
                review_profile_id="structure_prediction_v1",
                review_contract_source="job_identity",
                plddt_overall=72.8,
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    async with session_factory() as session:
        design = (
            await session.execute(
                select(Design).options(load_only(*DESIGN_LIST_LOAD_ONLY_COLUMNS)).where(Design.id == "esm-design")
            )
        ).scalar_one()
        response = _design_to_response(design)

    assert response.analysis_contract_id == "structure_prediction_v1"
    assert "structure_viewer" in response.viewer_capabilities
    assert response.review_artifact_manifest["artifacts"]["structure"]["state"] == "ready"
    await engine.dispose()


@pytest.mark.asyncio
async def test_analysis_run_by_id_rechecks_current_subject_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException
    from database import Design
    from routers import analyses as analyses_router

    run = SimpleNamespace(
        id="stale-run",
        subject_kind="design",
        subject_id="unsupported-design",
        analysis_type="antibody_annotation_pack",
        params_json={},
        status="completed",
    )
    design = Design(id="unsupported-design", job_id="job", name="unsupported", pdb_path="")
    design.review_profile_id = "structure_prediction_v1"

    async def _get_run(*_args, **_kwargs):
        return run

    class _ScalarResult:
        def scalar_one_or_none(self):
            return design

    class _Session:
        async def execute(self, _statement):
            return _ScalarResult()

    monkeypatch.setattr(analyses_router, "get_analysis_run_by_id", _get_run)
    with pytest.raises(HTTPException, match="not allowed") as exc_info:
        await analyses_router.get_analysis_run("stale-run", session=_Session())  # type: ignore[arg-type]
    assert exc_info.value.status_code == 409


def test_job_params_cannot_impersonate_review_producer_authority() -> None:
    from services.result_ingester import _job_stage_context

    forged_job: Any = SimpleNamespace(
        id="forged-job",
        name="Forged",
        model_id="external_new_model",
        mode="predict",
        stage_family=None,
        stage_mode=None,
        params={
            "review_profile_id": "antibody_backbone_v1",
            "review_contract_source": "producer",
            "review_role_map": {"result_role": "antibody_binder"},
            "review_artifact_manifest": {
                "schema": "bms.review-artifacts.v1",
                "artifacts": {"structure": {"state": "ready", "path": "/forged.pdb"}},
            },
        },
        parent_job_id=None,
        source_stage_job_id=None,
        source_stage_family=None,
        source_stage_mode=None,
        source_selection_count=None,
        selected_input_artifact_class=None,
        selected_input_schema_version=None,
    )

    context = _job_stage_context(forged_job)
    assert context["review_profile_id"] is None
    assert context["review_contract_source"] is None
    assert context["review_role_map"] is None
    assert context["review_artifact_manifest"] is None

    forged_stage_job: Any = SimpleNamespace(
        **{
            **forged_job.__dict__,
            "model_id": "protein_modification_experimental",
            "mode": "de_novo_design",
            "params": {"stage_family": "rfantibody", "stage_mode": "backbone_generation"},
            "stage_family": None,
            "stage_mode": None,
        }
    )
    forged_stage_context = _job_stage_context(forged_stage_job)
    assert forged_stage_context["review_profile_id"] is None
    assert forged_stage_context["review_role_map"] is None

    from routers.jobs import _derive_job_stage_tags

    assert _derive_job_stage_tags(
        "protein_modification_experimental",
        "de_novo_design",
        {"iteration_action": "maturation", "stage_family": "ppiflow"},
        "ppiflow",
    ) == (None, None)


def test_ingestion_finalizer_persists_profile_version_and_manifest() -> None:
    finalizer = getattr(result_contracts, "apply_review_contract_to_design", None)
    assert callable(finalizer), "ingestion review-contract finalizer is required"

    design = SimpleNamespace(
        review_profile_id="antibody_backbone_v1",
        review_contract_version=None,
        review_contract_source="job_identity",
        review_artifact_manifest=None,
        review_role_map={"chain_roles": {"H": "antibody_heavy", "A": "target"}},
        result_set=None,
        stage_family="rfantibody",
        stage_mode="backbone_generation",
        artifact_class="backbone_complex",
        provenance={"model_id": "rfantibody"},
        pdb_path="/data/rfa.pdb",
        aligned_error_path=None,
    )

    finalizer(design)

    assert design.review_profile_id == "antibody_backbone_v1"
    assert design.review_contract_version == 1
    assert design.review_contract_source == "job_identity"
    assert design.review_artifact_manifest["schema"] == "bms.review-artifacts.v1"
    assert design.review_artifact_manifest["roles"]["has_binder"] is True


def test_ingestion_finalizer_does_not_promote_arbitrary_import_metadata() -> None:
    design = SimpleNamespace(
        review_profile_id=None,
        review_contract_version=None,
        review_contract_source=None,
        review_artifact_manifest={"schema": "bms.review-artifacts.v1", "artifacts": {}},
        review_role_map={"result_role": "antibody_binder"},
        result_set="rfantibody_backbones",
        stage_family="rfantibody",
        stage_mode="backbone_generation",
        artifact_class="backbone_complex",
        provenance={"model_id": "rfantibody", "review_profile_id": "antibody_backbone_v1"},
        pdb_path=None,
        aligned_error_path=None,
    )

    result_contracts.apply_review_contract_to_design(design)

    assert design.review_profile_id == "unsupported_legacy"
    assert design.review_contract_source == "unsupported_legacy"
    assert design.review_artifact_manifest["artifacts"]["structure"]["state"] == "missing"


@pytest.mark.asyncio
async def test_schema_migration_adds_and_backfills_review_authority(tmp_path: Path) -> None:
    import database

    structure_path = tmp_path / "legacy-design.pdb"
    structure_path.write_text("END\n", encoding="utf-8")
    migration_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    original_engine = database.engine
    database.engine = migration_engine
    try:
        async with migration_engine.begin() as connection:
            await connection.run_sync(database.Base.metadata.create_all)
            await connection.execute(text("DROP INDEX IF EXISTS ix_designs_review_profile_id"))
            for column_name in (
                "review_profile_id",
                "review_contract_version",
                "review_contract_source",
                "review_artifact_manifest",
                "review_role_map",
            ):
                await connection.execute(text(f'ALTER TABLE designs DROP COLUMN "{column_name}"'))
            await connection.execute(text(
                "INSERT INTO jobs (id, name, model_id, mode, params, output_dir, status, queue_status, created_at) VALUES "
                "('recover-job', 'Recoverable legacy job', 'boltz2', 'predict', '{}', '/tmp/recover', 'completed', 'completed', CURRENT_TIMESTAMP)"
            ))
            await connection.execute(text(
                "INSERT INTO designs (id, job_id, name, stage_family, stage_mode, artifact_class, pdb_path, is_favorite, created_at) VALUES "
                "('boltzgen-row', 'legacy-job', 'BoltzGen row', 'boltzgen', 'backbone_generation', 'generated_backbone', :pdb_path, 0, CURRENT_TIMESTAMP), "
                "('recoverable-row', 'recover-job', 'Recoverable row', NULL, NULL, NULL, :pdb_path, 0, CURRENT_TIMESTAMP), "
                "('ambiguous-row', 'legacy-job', 'Ambiguous row', NULL, NULL, NULL, '', 0, CURRENT_TIMESTAMP)"
            ), {"pdb_path": str(structure_path)})
            await database._ensure_schema(connection)
            # Simulate a stale stamp produced by an earlier fail-closed migration.
            # A second startup must recover it from unambiguous server-owned job identity.
            await connection.execute(text(
                "UPDATE designs SET review_profile_id = 'unsupported_legacy', "
                "review_contract_source = 'unsupported_legacy' WHERE id = 'recoverable-row'"
            ))
            await database._ensure_schema(connection)

            migrated = (await connection.execute(text(
                "SELECT id, review_profile_id, review_contract_source, review_artifact_manifest "
                "FROM designs ORDER BY id"
            ))).mappings().all()

        by_id = {row["id"]: row for row in migrated}
        assert by_id["boltzgen-row"]["review_profile_id"] == "de_novo_generation_v1"
        assert by_id["boltzgen-row"]["review_contract_source"] == "legacy_backfill"
        boltzgen_manifest = json.loads(by_id["boltzgen-row"]["review_artifact_manifest"])
        assert boltzgen_manifest["artifacts"]["structure"]["state"] == "ready"
        assert boltzgen_manifest["roles"]["has_binder"] is False

        assert by_id["recoverable-row"]["review_profile_id"] == "structure_prediction_v1"
        assert by_id["recoverable-row"]["review_contract_source"] == "legacy_backfill"

        assert by_id["ambiguous-row"]["review_profile_id"] == "unsupported_legacy"
        assert by_id["ambiguous-row"]["review_contract_source"] == "unsupported_legacy"
        ambiguous_manifest = json.loads(by_id["ambiguous-row"]["review_artifact_manifest"])
        assert ambiguous_manifest["artifacts"]["structure"]["state"] == "missing"
        assert ambiguous_manifest["roles"]["has_binder"] is False
    finally:
        database.engine = original_engine
        await migration_engine.dispose()
