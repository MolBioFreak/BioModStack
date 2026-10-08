from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import AnalysisRun, Base, Design, Job
from services import analysis_subprocess, result_ingester
from services.analysis_registry import CHAIN_METRICS_ANALYSIS
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

    assert await analysis_subprocess._run_analysis("run-1") == 0

    async with session_factory() as session:
        design = (await session.execute(select(Design).where(Design.id == "design-1"))).scalar_one()
        run = (await session.execute(select(AnalysisRun).where(AnalysisRun.id == "run-1"))).scalar_one()

    assert design.chain_metrics == expected_metrics
    assert run.status == "completed"
    assert (tmp_path / "analysis_cache" / "run-1" / "result.json").exists()

    await engine.dispose()


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
