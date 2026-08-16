from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import AnalysisRun, Base, Design, Job
import paths
from services import analysis_subprocess, result_ingester
from services.analysis_registry import CHAIN_METRICS_ANALYSIS, JOB_AA_COMPOSITION_ANALYSIS, PAE_MATRIX_ANALYSIS
from services.stage_review import _rfantibody_review_metadata_refresh_required


async def _build_session_factory(tmp_path: Path) -> tuple[sessionmaker, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


@pytest.mark.asyncio
async def test_chain_metrics_analysis_persists_design_chain_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    structure_path = tmp_path / "design.pdb"
    structure_path.write_text("MODEL\nENDMDL\n", encoding="utf-8")

    async with session_factory() as session:
        session.add(
            Job(
                id="job-1",
                name="analysis-job",
                model_id="boltz2",
                mode="predict",
                params={},
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            Design(
                id="design-1",
                job_id="job-1",
                name="design-1",
                pdb_path=str(structure_path),
                review_profile_id="structure_prediction_v1",
                review_contract_version=1,
                review_contract_source="job_identity",
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            AnalysisRun(
                id="run-1",
                subject_kind="design",
                subject_id="design-1",
                analysis_type=CHAIN_METRICS_ANALYSIS,
                status="queued",
                params_json={},
                params_hash="hash",
                input_signature="sig",
                code_version="test",
                cache_key="cache",
            )
        )
        await session.commit()

    expected_metrics = {
        "A": {"type": "protein", "length": 21, "residue_numbers": list(range(1, 22))},
        "L": {"type": "ligand", "length": 1, "residue_numbers": [1]},
    }
    monkeypatch.setattr("services.analysis_subprocess.async_session", session_factory)
    monkeypatch.setattr("services.analysis_subprocess.get_per_chain_metrics", lambda _path: expected_metrics)
    monkeypatch.setattr(
        "services.analysis_subprocess.build_artifact_manifest_for_run",
        lambda _run: {
            "cache_dir": "analysis_cache/run-1",
            "result_json": "analysis_cache/run-1/result.json",
            "summary_json": "analysis_cache/run-1/summary.json",
        },
    )
    monkeypatch.setattr("services.analysis_subprocess.resolve_allowed_path", lambda raw: tmp_path / raw)

    async def _queued_signature(*_args, **_kwargs) -> str:
        return "sig"

    monkeypatch.setattr("services.analysis_subprocess.build_analysis_input_signature", _queued_signature)

    assert await analysis_subprocess._run_analysis("run-1") == 0

    async with session_factory() as session:
        design = (await session.execute(select(Design).where(Design.id == "design-1"))).scalar_one()
        run = (await session.execute(select(AnalysisRun).where(AnalysisRun.id == "run-1"))).scalar_one()

    assert design.chain_metrics == expected_metrics
    assert run.status == "completed"
    assert (tmp_path / "analysis_cache" / "run-1" / "result.json").exists()

    await engine.dispose()


@pytest.mark.asyncio
async def test_queued_job_analysis_stops_when_execution_authority_is_revoked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    async with session_factory() as session:
        session.add(
            Job(
                id="job-revoked",
                name="revoked-analysis-job",
                model_id="external_new_model",
                mode="predict",
                params={},
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            AnalysisRun(
                id="run-revoked",
                subject_kind="job",
                subject_id="job-revoked",
                analysis_type=JOB_AA_COMPOSITION_ANALYSIS,
                status="queued",
                params_json={},
                params_hash="hash",
                input_signature="unchanged-signature",
                code_version="test",
                cache_key="revoked-cache",
            )
        )
        await session.commit()

    async def _revoked(*_args, **_kwargs) -> str:
        return "analysis is no longer allowed by persisted review profiles"

    async def _must_not_compute(*_args, **_kwargs):
        raise AssertionError("revoked job analysis reached computation")

    monkeypatch.setattr("services.analysis_subprocess.async_session", session_factory)
    monkeypatch.setattr("services.analysis_subprocess.validate_job_analysis_request", _revoked)
    monkeypatch.setattr("services.analysis_subprocess._compute_job_aa_composition", _must_not_compute)

    assert await analysis_subprocess._async_main("run-revoked") == 1

    async with session_factory() as session:
        run = (await session.execute(select(AnalysisRun).where(AnalysisRun.id == "run-revoked"))).scalar_one()
    assert run.status == "failed"
    assert "no longer allowed" in str(run.error_message)

    await engine.dispose()


def test_run_analysis_rewrites_legacy_host_structure_paths_to_active_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        session_factory, engine = await _build_session_factory(tmp_path)
        legacy_root = (tmp_path / "host-data").resolve()
        active_root = (tmp_path / "runtime-data").resolve()
        runtime_file = active_root / "bms_results" / "job-legacy" / "model_0.pdb"
        runtime_file.parent.mkdir(parents=True)
        runtime_file.write_text("MODEL\nENDMDL\n", encoding="utf-8")
        legacy_path = legacy_root / "bms_results" / "job-legacy" / "model_0.pdb"

        async with session_factory() as session:
            session.add(
                Job(
                    id="job-legacy",
                    name="legacy-path-job",
                    model_id="boltz2",
                    mode="predict",
                    params={},
                    created_at=datetime.utcnow(),
                )
            )
            session.add(
                Design(
                    id="design-legacy",
                    job_id="job-legacy",
                    name="design-legacy",
                    pdb_path=str(legacy_path),
                    review_profile_id="structure_prediction_v1",
                    review_contract_version=1,
                    review_contract_source="job_identity",
                    created_at=datetime.utcnow(),
                )
            )
            session.add(
                AnalysisRun(
                    id="run-legacy",
                    subject_kind="design",
                    subject_id="design-legacy",
                    analysis_type=CHAIN_METRICS_ANALYSIS,
                    status="queued",
                    params_json={},
                    params_hash="hash",
                    input_signature="sig",
                    code_version="test",
                    cache_key="cache",
                )
            )
            await session.commit()

        captured: dict[str, Path] = {}
        expected_metrics = {
            "A": {"type": "protein", "length": 1, "residue_numbers": [1]},
        }

        monkeypatch.setattr("services.analysis_subprocess.async_session", session_factory)
        monkeypatch.setattr(
            "services.analysis_subprocess.build_artifact_manifest_for_run",
            lambda _run: {
                "cache_dir": "analysis_cache/run-legacy",
                "result_json": "analysis_cache/run-legacy/result.json",
                "summary_json": "analysis_cache/run-legacy/summary.json",
            },
        )
        monkeypatch.setattr("services.analysis_subprocess.resolve_allowed_path", lambda raw: tmp_path / raw)

        async def _queued_signature(*_args, **_kwargs) -> str:
            return "sig"

        monkeypatch.setattr("services.analysis_subprocess.build_analysis_input_signature", _queued_signature)
        monkeypatch.setattr(paths, "get_data_root", lambda: active_root)
        monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [legacy_root])
        monkeypatch.setattr(paths, "_runtime_paths", lambda: {"container_state_path": str(active_root)})

        def _fake_get_per_chain_metrics(path: str | Path) -> dict[str, object]:
            captured["structure_path"] = Path(path)
            return expected_metrics

        monkeypatch.setattr("services.analysis_subprocess.get_per_chain_metrics", _fake_get_per_chain_metrics)

        assert await analysis_subprocess._run_analysis("run-legacy") == 0

        assert captured["structure_path"] == runtime_file

        async with session_factory() as session:
            run = (await session.execute(select(AnalysisRun).where(AnalysisRun.id == "run-legacy"))).scalar_one()
            design = (await session.execute(select(Design).where(Design.id == "design-legacy"))).scalar_one()

        assert run.status == "completed"
        assert design.chain_metrics == expected_metrics

        await engine.dispose()

    asyncio.run(_run())


def test_compute_pae_matrix_rewrites_legacy_host_paths_to_active_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy_root = (tmp_path / "host-data").resolve()
    active_root = (tmp_path / "runtime-data").resolve()
    runtime_file = active_root / "bms_results" / "job-legacy" / "model_0.pdb"
    runtime_artifact = active_root / "bms_results" / "job-legacy" / "confidence_model_0.json"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("MODEL\nENDMDL\n", encoding="utf-8")
    runtime_artifact.write_text('{"pae": [[1.0]]}', encoding="utf-8")

    legacy_structure_path = legacy_root / "bms_results" / "job-legacy" / "model_0.pdb"
    legacy_artifact_path = legacy_root / "bms_results" / "job-legacy" / "confidence_model_0.json"

    monkeypatch.setattr(paths, "get_data_root", lambda: active_root)
    monkeypatch.setattr(paths, "_candidate_data_roots", lambda: [legacy_root])
    monkeypatch.setattr(paths, "_runtime_paths", lambda: {"container_state_path": str(active_root)})

    captured: dict[str, Path] = {}

    class _FakeMatrix:
        def tolist(self) -> list[list[float]]:
            return [[1.0]]

    def _fake_load_aligned_error_artifact(**kwargs):
        captured["aligned_error_path"] = Path(kwargs["aligned_error_path"])
        captured["structure_path"] = Path(kwargs["structure_path"])
        return SimpleNamespace(path=runtime_artifact, format="confidence_json", matrix=_FakeMatrix())

    monkeypatch.setattr("services.analysis_subprocess.load_aligned_error_artifact", _fake_load_aligned_error_artifact)

    design = SimpleNamespace(
        id="design-legacy",
        name="design-legacy",
        pdb_path=str(legacy_structure_path),
        aligned_error_path=str(legacy_artifact_path),
        aligned_error_format="confidence_json",
        aligned_error_key="pae",
    )

    result, summary, inline_payload = analysis_subprocess._compute_pae_matrix(design, {"max_size": 200})

    assert captured["aligned_error_path"] == runtime_artifact
    assert captured["structure_path"] == runtime_file
    assert result["size"] == 1
    assert summary["source_mode"] == "confidence_json"
    assert inline_payload is None


@pytest.mark.asyncio
async def test_ingest_loose_files_uses_reference_target_role_inference_for_validation_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_dir = tmp_path / "job-output"
    predictions_dir = output_dir / "pdb_files" / "predictions"
    predictions_dir.mkdir(parents=True)
    (predictions_dir / "confidence_design_0.json").write_text('{"complex_plddt": 0.92}', encoding="utf-8")
    (predictions_dir / "design_0.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")

    async with session_factory() as session:
        job = Job(
            id="job-1",
            name="validation-job",
            model_id="boltz2",
            mode="validation",
            params={"target_pdb": "reference_target.pdb"},
            output_dir=str(output_dir),
            created_at=datetime.utcnow(),
        )
        session.add(job)
        await session.commit()

    async def _fake_lineage(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "services.result_ingester._job_stage_context",
        lambda _job: {
            "provenance": {},
            "stage_family": "validation",
            "stage_mode": "post_structure_validation",
            "selected_loop_scope": None,
        },
    )
    monkeypatch.setattr("services.result_ingester._resolve_parent_design_lineage", _fake_lineage)
    monkeypatch.setattr("services.result_ingester.extract_plddt_from_pdb", lambda _path: (91.5, [91.5]))
    monkeypatch.setattr("services.result_ingester._parse_hlt_cdr_lengths", lambda _path: {})
    monkeypatch.setattr(
        "services.result_ingester._resolve_validation_structure_role_fields",
        lambda **_kwargs: {
            "detected_antibody_chains": "H",
            "detected_target_chain": "A",
        },
    )
    monkeypatch.setattr(
        "services.result_ingester._compute_validation_geometry_fields",
        lambda **_kwargs: {
            "detected_antibody_chains": "H",
            "detected_target_chain": "A",
            "target_contact_count": 4,
            "target_min_distance": 3.2,
        },
    )
    monkeypatch.setattr(
        "services.result_ingester._strict_aligned_error_fields",
        lambda **_kwargs: {
            "aligned_error_path": "/tmp/aligned_error.json",
            "aligned_error_format": "json",
            "aligned_error_key": "pae",
            "ipsae": 0.77,
        },
    )
    monkeypatch.setattr("services.result_ingester._find_fampnn_sidecar_path", lambda *_args, **_kwargs: tmp_path / "missing.json")
    monkeypatch.setattr("services.result_ingester._load_json_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "services.result_ingester._extract_fampnn_metrics",
        lambda *_args, **_kwargs: {
            "avg_psce": None,
            "binder_length": None,
            "mpnn_score": None,
        },
    )
    monkeypatch.setattr("services.result_ingester._build_fampnn_payload", lambda *_args, **_kwargs: None)

    async with session_factory() as session:
        job = (await session.execute(select(Job).where(Job.id == "job-1"))).scalar_one()
        assert not result_ingester._job_has_explicit_binder_target_roles(job)
        assert result_ingester._job_supports_inferred_validation_roles(job, job.params)

        count = await result_ingester.ingest_loose_files(job.id, output_dir, session, current_job=job)
        design = (await session.execute(select(Design).where(Design.job_id == job.id))).scalar_one()

    assert count == 1
    assert design.detected_antibody_chains == "H"
    assert design.detected_target_chain == "A"
    assert design.target_contact_count == 4
    assert design.target_min_distance == 3.2
    assert design.ipsae == 0.77
    assert design.plddt_binder is None
    assert design.plddt_target is None

    await engine.dispose()


def test_rfantibody_review_metadata_refresh_detects_missing_source_backed_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdb_path = tmp_path / "antibody_job_gpu0_0.pdb"
    pdb_path.write_text("MODEL\nENDMDL\n", encoding="utf-8")

    row = Design(
        id="design-1",
        job_id="job-1",
        name="antibody_job_gpu0_0",
        pdb_path=str(pdb_path),
        created_at=datetime.utcnow(),
    )

    screening_by_name = {
        "antibody_job_gpu0_0": {
            "design_name": "antibody_job_gpu0_0",
            "detected_antibody_chains": "H",
            "detected_target_chain": "A",
            "target_contact_count": 7,
            "screening_reason": "passed",
            "rfa_loop_metrics": {"H3": {"length": 12}},
            "rfa_hotspot_metrics": {"covered": 3},
            "rfa_hotspot_covered_count": 3,
        }
    }

    monkeypatch.setattr(
        "services.stage_review.load_rfantibody_trb_summary",
        lambda _path: {
            "rfa_metadata": {"device": "cuda:0"},
            "rfa_hotspot_min_distance": 2.1,
            "rfa_hotspot_avg_min_distance": 2.4,
            "rfa_plddt_final": 88.5,
            "rfa_design_loops": ["H3"],
            "rfa_hotspots": ["A52"],
        },
    )

    assert _rfantibody_review_metadata_refresh_required(row, screening_by_name, {}) is True


def test_rfantibody_review_metadata_refresh_skips_rows_without_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdb_path = tmp_path / "antibody_job_gpu0_1.pdb"
    pdb_path.write_text("MODEL\nENDMDL\n", encoding="utf-8")

    row = Design(
        id="design-2",
        job_id="job-1",
        name="antibody_job_gpu0_1",
        pdb_path=str(pdb_path),
        created_at=datetime.utcnow(),
    )

    monkeypatch.setattr("services.stage_review.load_rfantibody_trb_summary", lambda _path: {})

    assert _rfantibody_review_metadata_refresh_required(row, {}, {}) is False
