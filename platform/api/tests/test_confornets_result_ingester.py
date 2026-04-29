from __future__ import annotations

import json
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

from database import Base, Design, Job
from routers.designs import _build_plotly_chart_suggestions, _build_plotly_metric_metadata, _build_plotly_metrics
from services import result_ingester
from services.result_ingester import ingest_job_results


async def _build_session_factory(tmp_path: Path) -> tuple[sessionmaker, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def _write_minimal_cif(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "data_conformer\n"
        "#\n"
        "loop_\n"
        "_atom_site.group_PDB\n"
        "_atom_site.id\n"
        "_atom_site.type_symbol\n"
        "_atom_site.label_atom_id\n"
        "_atom_site.label_comp_id\n"
        "_atom_site.label_asym_id\n"
        "_atom_site.label_seq_id\n"
        "_atom_site.Cartn_x\n"
        "_atom_site.Cartn_y\n"
        "_atom_site.Cartn_z\n"
        "_atom_site.B_iso_or_equiv\n"
        "ATOM 1 C CA GLY A 1 0.0 0.0 0.0 91.0\n"
        "#\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_confornets_ingests_only_final_conformer_artifacts(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "tdt_confornets_mse_finalpub_20260426_230518"
    final_root = output_root / "final" / "confornets"
    final_conformer = final_root / "conformers" / "cn_00000_sample_0.cif"
    raw_duplicate = final_root / "raw" / "bms_tdt_confornets" / "tdt_p04053_af_v6" / "alphafold_v6" / "run_0" / "sample_0.cif"
    work_duplicate = output_root / "work" / "aa" / "bb" / "confornets_results" / "conformers" / "cn_00000_sample_0.cif"
    _write_minimal_cif(final_conformer)
    _write_minimal_cif(raw_duplicate)
    _write_minimal_cif(work_duplicate)
    (final_root / "samples.json").write_text(
        '[{"sample_id":"sample_0","conformer_path":"conformers/cn_00000_sample_0.cif","loss":1.25}]',
        encoding="utf-8",
    )
    (final_root / "landscape.json").write_text('{"objective":"mse","sample_count":1}', encoding="utf-8")
    (final_root / "provenance.json").write_text('{"workflow":"confornets_experimental"}', encoding="utf-8")
    (final_root / "ensemble_manifest.json").write_text(
        '{"conformers":[{"sample_id":"sample_0","path":"conformers/cn_00000_sample_0.cif"}]}',
        encoding="utf-8",
    )

    async with session_factory() as session:
        session.add(
            Job(
                id="job-confornets",
                name="tdt-confornets-smoke",
                model_id="confornets_experimental",
                mode="design",
                params={"cn_objective": "mse"},
                output_dir=str(output_root),
                status="completed",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-confornets", str(output_root), session)
        assert created == 1

        designs = (await session.execute(select(Design).where(Design.job_id == "job-confornets"))).scalars().all()
        assert len(designs) == 1
        design = designs[0]
        assert design.name == "cn_00000_sample_0"
        assert Path(design.pdb_path) == final_conformer
        assert design.json_path == str(final_root / "samples.json")
        assert design.provenance["artifact_group"] == "confornets"
        assert design.provenance["model_id"] == "confornets_experimental"
        assert design.provenance["landscape_json"] == str(final_root / "landscape.json")
        assert design.confidence_metrics["confornets_sample"]["sample_id"] == "sample_0"
        assert design.confidence_metrics["confornets_landscape"]["objective"] == "mse"

    await engine.dispose()


@pytest.mark.asyncio
async def test_confornets_ingests_manifest_request_training_loss_and_named_metrics(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "tdt_confornets_mse_10samples_20260427T161305Z"
    final_root = output_root / "final" / "confornets"
    first_conformer = final_root / "conformers" / "cn_00000_sample_0.cif"
    second_conformer = final_root / "conformers" / "cn_00001_sample_1.cif"
    _write_minimal_cif(first_conformer)
    _write_minimal_cif(second_conformer)
    (final_root / "confidence").mkdir(parents=True, exist_ok=True)
    (final_root / "confidence" / "training_loss.csv").write_text(
        "step,loss\n0,2.5\n10,1.25\n20,0.75\n",
        encoding="utf-8",
    )
    (final_root / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow": "confornets_experimental",
                "sample_count": 2,
                "samples_json": "samples.json",
                "landscape_json": "landscape.json",
                "ensemble_manifest_json": "ensemble_manifest.json",
            }
        ),
        encoding="utf-8",
    )
    (final_root / "request.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow": "confornets_experimental",
                "job_id": "job-confornets-rich",
                "task": "mse",
                "query_id": "tdt_p04053_af_v6",
                "sequence": "MKT",
                "chain_id": "A",
                "upstream_contract": {"monomer_only": True},
            }
        ),
        encoding="utf-8",
    )
    (final_root / "samples.json").write_text(
        json.dumps(
            [
                {
                    "sample_id": "sample_0",
                    "frame_index": 0,
                    "relative_path": "conformers/cn_00000_sample_0.cif",
                    "source_relative_path": "raw/run_0/sample_0.cif",
                    "sha256": "sha0",
                    "bytes": 123,
                    "format": "cif",
                    "task": "mse",
                    "query_id": "tdt_p04053_af_v6",
                    "test_case": "alphafold_v6",
                },
                {
                    "sample_id": "sample_1",
                    "frame_index": 1,
                    "relative_path": "conformers/cn_00001_sample_1.cif",
                    "source_relative_path": "raw/run_0/sample_1.cif",
                    "sha256": "sha1",
                    "bytes": 456,
                    "format": "cif",
                    "task": "mse",
                    "query_id": "tdt_p04053_af_v6",
                    "test_case": "alphafold_v6",
                },
            ]
        ),
        encoding="utf-8",
    )
    (final_root / "landscape.json").write_text(
        json.dumps({"schema_version": 1, "workflow": "confornets_experimental", "status": "not_computed", "sample_count": 2}),
        encoding="utf-8",
    )
    (final_root / "provenance.json").write_text(
        json.dumps({"workflow": "confornets_experimental", "sample_count": 2}),
        encoding="utf-8",
    )
    (final_root / "ensemble_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflow": "confornets_experimental",
                "frame_count": 2,
                "conformers": [
                    {"sample_id": "sample_0", "frame_index": 0, "relative_path": "conformers/cn_00000_sample_0.cif"},
                    {"sample_id": "sample_1", "frame_index": 1, "relative_path": "conformers/cn_00001_sample_1.cif"},
                ],
            }
        ),
        encoding="utf-8",
    )

    async with session_factory() as session:
        session.add(
            Job(
                id="job-confornets-rich",
                name="tdt-confornets-rich",
                model_id="confornets_experimental",
                mode="design",
                params={"cn_objective": "mse"},
                output_dir=str(output_root),
                status="completed",
                stage_family="confornets",
                stage_mode="mse",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-confornets-rich", str(output_root), session)
        assert created == 2

        designs = (
            await session.execute(select(Design).where(Design.job_id == "job-confornets-rich").order_by(Design.name))
        ).scalars().all()
        assert len(designs) == 2
        design = designs[0]
        assert design.artifact_group == "confornets"
        assert design.artifact_class == "conformer"
        assert design.artifact_schema_version == 1
        assert design.stage_family == "confornets"
        assert design.stage_mode == "mse"
        assert design.confidence_metrics["confornets_schema"] == "confornets.confidence_metrics.v1"
        assert design.confidence_metrics["confornets_sample"]["frame_index"] == 0
        assert design.confidence_metrics["confornets_sample"]["sample_index"] == 0
        assert design.confidence_metrics["confornets_reporting"]["sample_semantics"] == "independent_generated_conformer_sample"
        assert "not a time-resolved trajectory" in design.confidence_metrics["confornets_reporting"]["sample_semantics_note"]
        assert design.confidence_metrics["confornets_sample"]["bytes"] == 123
        assert design.confidence_metrics["confornets_ensemble"]["relative_path"] == "conformers/cn_00000_sample_0.cif"
        assert design.confidence_metrics["confornets_artifact_manifest"]["sample_count"] == 2
        assert design.confidence_metrics["confornets_request"]["task"] == "mse"
        assert design.confidence_metrics["confornets_training_loss_summary"] == {
            "csv_path": str(final_root / "confidence" / "training_loss.csv"),
            "row_count": 3,
            "first_step": 0.0,
            "last_step": 20.0,
            "first_loss": 2.5,
            "final_loss": 0.75,
            "min_loss": 0.75,
            "max_loss": 2.5,
        }

        metrics = _build_plotly_metrics(design)
        assert metrics["confornets_sample_index"] == 0.0
        assert metrics["confornets_frame_index"] == 0.0
        assert metrics["confornets_bytes"] == 123.0
        assert metrics["confornets_sample_count"] == 2.0
        assert metrics["confornets_training_final_loss"] == 0.75
        assert metrics["confornets_training_min_loss"] == 0.75
        assert metrics["confornets_training_step_count"] == 3.0

    await engine.dispose()


@pytest.mark.asyncio
async def test_confornets_ingests_native_confidence_evaluation_diversity_and_landscape_metrics(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "tdt_confornets_analytics"
    final_root = output_root / "final" / "confornets"
    first_conformer = final_root / "conformers" / "cn_00000_sample_0.cif"
    second_conformer = final_root / "conformers" / "cn_00001_sample_1.cif"
    _write_minimal_cif(first_conformer)
    _write_minimal_cif(second_conformer)
    (final_root / "confidence").mkdir(parents=True, exist_ok=True)
    (final_root / "evaluation").mkdir(parents=True, exist_ok=True)
    (final_root / "samples.json").write_text(
        json.dumps(
            [
                {"sample_id": "cn_00000_sample_0", "frame_index": 0, "relative_path": "conformers/cn_00000_sample_0.cif", "bytes": 123},
                {"sample_id": "cn_00001_sample_1", "frame_index": 1, "relative_path": "conformers/cn_00001_sample_1.cif", "bytes": 456},
            ]
        ),
        encoding="utf-8",
    )
    (final_root / "ensemble_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workflow": "confornets_experimental",
                "frame_count": 2,
                "conformers": [
                    {"sample_id": "cn_00000_sample_0", "frame_index": 0, "relative_path": "conformers/cn_00000_sample_0.cif"},
                    {"sample_id": "cn_00001_sample_1", "frame_index": 1, "relative_path": "conformers/cn_00001_sample_1.cif"},
                ],
            }
        ),
        encoding="utf-8",
    )
    confidence_summary = {
        "schema_version": 1,
        "status": "computed",
        "samples": [
            {
                "sample_id": "cn_00000_sample_0",
                "frame_index": 0,
                "plddt": 91.25,
                "gpde": 0.72,
                "ptm": 0.81,
                "iptm": 0.77,
                "full_confidence_tensor": "confidence/sample_0_confidence.pt",
            },
            {"sample_id": "cn_00001_sample_1", "frame_index": 1, "plddt": 86.5, "gpde": 0.95, "ptm": 0.78, "iptm": 0.74},
        ],
        "summary": {"plddt_mean": 88.875, "gpde_mean": 0.835},
    }
    evaluation_summary = {
        "schema_version": 1,
        "status": "computed",
        "rmsd_threshold": 3.0,
        "sample_count": 2,
        "reference_count": 2,
        "success_at_1_rate": 1.0,
        "pairwise_rmsd": {"min": 1.5, "max": 1.5, "mean": 1.5, "count": 1},
        "samples": [
            {
                "sample_id": "cn_00000_sample_0",
                "frame_index": 0,
                "nearest_reference": "open_ref",
                "min_reference_rmsd": 0.42,
                "success_at_1": True,
                "rmsd_to_references": {"open_ref": 0.42, "closed_ref": 4.2},
                "pairwise_diversity": {"min_pairwise_rmsd": 1.5, "mean_pairwise_rmsd": 1.5, "max_pairwise_rmsd": 1.5},
                "landscape": {"x": -0.75, "y": 0.0},
            },
            {
                "sample_id": "cn_00001_sample_1",
                "frame_index": 1,
                "nearest_reference": "closed_ref",
                "min_reference_rmsd": 0.38,
                "success_at_1": True,
                "rmsd_to_references": {"open_ref": 4.5, "closed_ref": 0.38},
                "pairwise_diversity": {"min_pairwise_rmsd": 1.5, "mean_pairwise_rmsd": 1.5, "max_pairwise_rmsd": 1.5},
                "landscape": {"x": 0.75, "y": 0.0},
            },
        ],
    }
    (final_root / "confidence" / "confidence_summary.json").write_text(json.dumps(confidence_summary), encoding="utf-8")
    (final_root / "evaluation" / "evaluation_summary.json").write_text(json.dumps(evaluation_summary), encoding="utf-8")
    (final_root / "landscape.json").write_text(
        json.dumps({"schema_version": 1, "status": "computed", "method": "ca_kabsch_rmsd_mds", "coordinates": evaluation_summary["samples"]}),
        encoding="utf-8",
    )
    (final_root / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "sample_count": 2,
                "confidence_summary_json": "confidence/confidence_summary.json",
                "evaluation_summary_json": "evaluation/evaluation_summary.json",
                "pairwise_rmsd_csv": "evaluation/pairwise_rmsd_matrix.csv",
                "reference_rmsd_csv": "evaluation/reference_rmsd.csv",
                "full_confidence_tensor_count": 1,
            }
        ),
        encoding="utf-8",
    )

    async with session_factory() as session:
        session.add(
            Job(
                id="job-confornets-analytics",
                name="tdt-confornets-analytics",
                model_id="confornets_experimental",
                mode="design",
                params={"cn_task": "mse", "cn_compute_confidence": True, "cn_compute_evaluation": True},
                output_dir=str(output_root),
                status="completed",
                stage_family="confornets",
                stage_mode="mse",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-confornets-analytics", str(output_root), session)
        assert created == 2
        design = (
            await session.execute(select(Design).where(Design.job_id == "job-confornets-analytics", Design.name == "cn_00000_sample_0"))
        ).scalar_one()
        assert design.plddt_overall == 91.25
        assert design.confidence_metrics["confornets_confidence"]["plddt"] == 91.25
        assert design.confidence_metrics["confornets_confidence"]["gpde"] == 0.72
        assert design.confidence_metrics["confornets_reference_evaluation"]["nearest_reference"] == "open_ref"
        assert design.confidence_metrics["confornets_reference_evaluation"]["min_reference_rmsd"] == 0.42
        assert design.confidence_metrics["confornets_pairwise_diversity"]["mean_pairwise_rmsd"] == 1.5
        assert design.confidence_metrics["confornets_landscape_point"] == {"x": -0.75, "y": 0.0}
        assert design.confidence_metrics["confornets_evaluation_summary"]["success_at_1_rate"] == 1.0

        metrics = _build_plotly_metrics(design)
        assert metrics["confornets_sample_index"] == 0.0
        assert metrics["confornets_confidence_plddt"] == 91.25
        assert metrics["confornets_confidence_gpde"] == 0.72
        assert metrics["confornets_min_reference_rmsd"] == 0.42
        assert metrics["confornets_reference_success_at_1"] == 1.0
        assert metrics["confornets_pairwise_mean_rmsd"] == 1.5
        assert metrics["confornets_landscape_x"] == -0.75
        assert metrics["confornets_success_at_1_rate"] == 1.0
        metadata = _build_plotly_metric_metadata(metrics.keys())
        assert metadata["confornets_sample_index"]["label"] == "ConforNets sample index"
        assert metadata["confornets_min_reference_rmsd"]["label"] == "Nearest staged-reference Cα RMSD"
        assert "staged reference" in metadata["confornets_min_reference_rmsd"]["description"]
        assert metadata["confornets_pairwise_mean_rmsd"]["label"] == "Post-hoc pairwise sample RMSD"
        assert metadata["confornets_landscape_x"]["source"] == "bms_wrapper"
        suggestions = _build_plotly_chart_suggestions(metrics.keys())
        suggestion_ids = {suggestion["id"] for suggestion in suggestions}
        assert "confornets_reference_confidence" in suggestion_ids
        assert "confornets_sample_landscape" in suggestion_ids

    await engine.dispose()


@pytest.mark.asyncio
async def test_confornets_reingest_enriches_existing_rows_without_duplicates(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    output_root = tmp_path / "tdt_confornets_mse_existing"
    final_root = output_root / "final" / "confornets"
    conformer = final_root / "conformers" / "cn_00000_sample_0.cif"
    _write_minimal_cif(conformer)
    (final_root / "confidence").mkdir(parents=True, exist_ok=True)
    (final_root / "confidence" / "training_loss.csv").write_text(
        "step,loss\n0,4.0\n1,1.5\n",
        encoding="utf-8",
    )
    (final_root / "samples.json").write_text(
        json.dumps([
            {
                "sample_id": "sample_0",
                "frame_index": 0,
                "relative_path": "conformers/cn_00000_sample_0.cif",
                "bytes": 789,
            }
        ]),
        encoding="utf-8",
    )
    (final_root / "landscape.json").write_text(json.dumps({"sample_count": 1, "objective": "mse"}), encoding="utf-8")
    (final_root / "artifact_manifest.json").write_text(json.dumps({"schema_version": 2, "sample_count": 1}), encoding="utf-8")
    (final_root / "request.json").write_text(json.dumps({"task": "mse", "query_id": "tdt"}), encoding="utf-8")
    (final_root / "ensemble_manifest.json").write_text(
        json.dumps({
            "frame_count": 1,
            "conformers": [
                {"sample_id": "sample_0", "frame_index": 0, "relative_path": "conformers/cn_00000_sample_0.cif", "rmsd_to_seed": 2.25}
            ],
        }),
        encoding="utf-8",
    )

    async with session_factory() as session:
        session.add(
            Job(
                id="job-confornets-existing",
                name="tdt-confornets-existing",
                model_id="confornets_experimental",
                mode="design",
                params={"cn_objective": "mse"},
                output_dir=str(output_root),
                status="completed",
                stage_family="confornets",
                stage_mode="mse",
                created_at=datetime.utcnow(),
            )
        )
        session.add(
            Design(
                id="design-existing",
                job_id="job-confornets-existing",
                name="cn_00000_sample_0",
                pdb_path=str(conformer),
                confidence_metrics={"confornets_sample": {"sample_id": "sample_0", "frame_index": 0}},
                provenance={"artifact_group": "confornets", "model_id": "confornets_experimental"},
                stage_family="confornets",
                stage_mode="mse",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        updated = await ingest_job_results("job-confornets-existing", str(output_root), session)
        assert updated == 1

        designs = (
            await session.execute(select(Design).where(Design.job_id == "job-confornets-existing"))
        ).scalars().all()
        assert len(designs) == 1
        design = designs[0]
        assert design.id == "design-existing"
        assert design.artifact_group == "confornets"
        assert design.artifact_class == "conformer"
        assert design.artifact_schema_version == 2
        assert design.json_path == str(final_root / "samples.json")
        assert design.confidence_metrics["confornets_sample"]["bytes"] == 789
        assert design.confidence_metrics["confornets_ensemble"]["rmsd_to_seed"] == 2.25
        assert design.confidence_metrics["confornets_artifact_manifest"]["sample_count"] == 1
        assert design.confidence_metrics["confornets_request"]["query_id"] == "tdt"
        assert design.confidence_metrics["confornets_training_loss_summary"]["final_loss"] == 1.5
        assert design.provenance["final_root"] == str(final_root)
        metrics = _build_plotly_metrics(design)
        assert metrics["confornets_sample_count"] == 1.0
        assert metrics["confornets_training_final_loss"] == 1.5

    await engine.dispose()


@pytest.mark.asyncio
async def test_confornets_ingest_resolves_legacy_absolute_output_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    run_name = "tdt_confornets_legacy_absolute"
    runtime_output = tmp_path / "runtime-root" / "results" / run_name
    legacy_output = tmp_path / "legacy-root" / "results" / run_name
    final_root = runtime_output / "final" / "confornets"
    conformer = final_root / "conformers" / "cn_00000_sample_0.cif"
    _write_minimal_cif(conformer)
    (final_root / "samples.json").write_text(
        json.dumps([
            {"sample_id": "sample_0", "frame_index": 0, "relative_path": "conformers/cn_00000_sample_0.cif", "bytes": 456}
        ]),
        encoding="utf-8",
    )
    (final_root / "landscape.json").write_text(json.dumps({"sample_count": 1}), encoding="utf-8")
    (final_root / "ensemble_manifest.json").write_text(
        json.dumps({"conformers": [{"sample_id": "sample_0", "frame_index": 0, "relative_path": "conformers/cn_00000_sample_0.cif"}]}),
        encoding="utf-8",
    )

    resolver_calls: list[Path] = []

    def fake_resolve_runtime_data_path(path: str | Path) -> Path:
        resolved = Path(path).resolve()
        resolver_calls.append(resolved)
        if resolved == legacy_output.resolve():
            return runtime_output.resolve()
        return resolved

    monkeypatch.setattr(result_ingester, "resolve_runtime_data_path", fake_resolve_runtime_data_path)

    async with session_factory() as session:
        session.add(
            Job(
                id="job-confornets-legacy-absolute",
                name=run_name,
                model_id="confornets_experimental",
                mode="design",
                params={},
                output_dir=str(legacy_output),
                status="completed",
                stage_family="confornets",
                stage_mode="mse",
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        created = await ingest_job_results("job-confornets-legacy-absolute", str(legacy_output), session)
        assert created == 1
        assert resolver_calls == [legacy_output.resolve()]
        design = (
            await session.execute(select(Design).where(Design.job_id == "job-confornets-legacy-absolute"))
        ).scalar_one()
        assert Path(design.pdb_path) == conformer.resolve()
        assert design.confidence_metrics["confornets_sample"]["bytes"] == 456

    await engine.dispose()
