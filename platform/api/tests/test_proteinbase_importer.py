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
from routers.designs import _build_plotly_metrics, _collect_plotly_metrics, _design_to_response
from routers.jobs import ProteinBaseBundleImportRequest, import_proteinbase_bundle_job
from services.proteinbase_importer import import_proteinbase_bundle, normalize_proteinbase_record


async def _build_session_factory(tmp_path: Path) -> tuple[sessionmaker, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def _make_record(
    *,
    protein_id: str,
    name: str,
    structure_url: str,
    plddt_value: float,
    include_boltz_metrics: bool,
) -> dict:
    evaluations: list[dict] = [
        {"metric": "design_class", "type": "computational", "value": "Peptide", "valueType": "label"},
        {"metric": "proteinmpnn_score", "type": "computational", "value": 2.9509, "valueType": "numeric"},
        {
            "metric": "esmfold_structure_prediction",
            "type": "computational",
            "value": {"url": "https://proteinbase-pub.t3.storage.dev/esmfold-default.cif"},
            "valueType": "json",
        },
        {"metric": "esmfold_plddt", "type": "computational", "unit": "%", "value": 42.09418604651162, "valueType": "numeric"},
    ]
    if include_boltz_metrics:
        evaluations.extend(
            [
                {"metric": "boltz2_ptm", "type": "computational", "value": 0.6554087400436401, "valueType": "numeric"},
                {"metric": "boltz2_complex_pde", "type": "computational", "value": 0.5991594195365906, "valueType": "numeric"},
                {"metric": "boltz2_min_ipsae", "type": "computational", "value": 0.313784, "valueType": "numeric"},
                {
                    "metric": "pae_file",
                    "type": "computational",
                    "value": {"file": {"filename": "sample-pae.json", "filetype": "json", "url": "s3://proteinbase-pub/sample-pae.json"}},
                    "valueType": "json",
                },
                {"metric": "boltz2_complex_plddt", "type": "computational", "value": 0.7622371315956116, "valueType": "numeric"},
                {"metric": "boltz2_complex_iplddt", "type": "computational", "value": 0.7517083883285522, "valueType": "numeric"},
                {"metric": "boltz2_pdockq2", "type": "computational", "value": 0.0073, "valueType": "numeric"},
                {"metric": "boltz2_plddt", "type": "computational", "value": plddt_value, "valueType": "numeric"},
                {"metric": "boltz2_ipsae", "type": "computational", "value": 0.57821, "valueType": "numeric"},
                {"metric": "boltz2_pdockq", "type": "computational", "value": 0.0183, "valueType": "numeric"},
                {"metric": "boltz2_lis", "type": "computational", "value": 0.3892, "valueType": "numeric"},
                {"metric": "boltz2_iptm", "type": "computational", "value": 0.8910560011863708, "valueType": "numeric"},
                {
                    "metric": "boltz2_structure_prediction",
                    "type": "computational",
                    "value": {"url": structure_url},
                    "valueType": "json",
                },
            ]
        )
    return {
        "id": protein_id,
        "name": name,
        "author": "nanogenomic",
        "designMethod": "",
        "length_aa": 60,
        "protein_url": f"https://proteinbase.com/proteins/{protein_id}",
        "sequence": "ASHMPWSYNQAQSAIDLQTFVSEWPCRHQFLEFDKTKRKNSDKKHHGFDQAQVMWIREWT",
        "evaluations": evaluations,
    }


def test_normalize_proteinbase_record_promotes_boltz_metrics_and_preserves_raw_payload() -> None:
    record = _make_record(
        protein_id="boltz-entry",
        name="LGDL_RBX1_001",
        structure_url="https://proteinbase-pub.t3.storage.dev/boltz-entry.cif",
        plddt_value=0.7880009412765503,
        include_boltz_metrics=True,
    )

    normalized = normalize_proteinbase_record(record)

    assert normalized["name"] == "LGDL_RBX1_001"
    assert normalized["structure_url"] == "https://proteinbase-pub.t3.storage.dev/boltz-entry.cif"
    assert normalized["plddt_overall"] == pytest.approx(76.22371315956116)
    assert normalized["ptm"] == pytest.approx(0.6554087400436401)
    assert normalized["iptm"] == pytest.approx(0.8910560011863708)
    assert normalized["complex_iplddt"] == pytest.approx(0.7517083883285522)
    assert normalized["complex_ipde"] == pytest.approx(0.5991594195365906)
    assert normalized["ipsae"] == pytest.approx(0.57821)
    assert normalized["confidence_metrics"]["min_iPSAE"] == pytest.approx(0.313784)
    assert normalized["confidence_metrics"]["LIS"] == pytest.approx(0.3892)
    assert normalized["confidence_metrics"]["pDockQ"] == pytest.approx(0.0183)
    assert normalized["confidence_metrics"]["pDockQ2"] == pytest.approx(0.0073)
    assert normalized["confidence_metrics"]["pae_json_url"] == "s3://proteinbase-pub/sample-pae.json"
    assert normalized["confidence_metrics"]["structure_prediction_url"] == "https://proteinbase-pub.t3.storage.dev/boltz-entry.cif"
    assert normalized["confidence_metrics"]["proteinbase"]["id"] == "boltz-entry"


@pytest.mark.asyncio
async def test_import_proteinbase_bundle_creates_completed_job_and_design_rows(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    bundle_dir = tmp_path / "proteinbase_bundle"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "selected_submissions.jsonl"
    bundle_path.write_text(
        "\n".join(
            [
                json.dumps(
                    _make_record(
                        protein_id="boltz-entry",
                        name="LGDL_RBX1_001",
                        structure_url="https://proteinbase-pub.t3.storage.dev/boltz-entry.cif",
                        plddt_value=0.7880009412765503,
                        include_boltz_metrics=True,
                    )
                ),
                json.dumps(
                    _make_record(
                        protein_id="esmfold-entry",
                        name="LGDL_RBX1_002",
                        structure_url="https://proteinbase-pub.t3.storage.dev/unused-boltz.cif",
                        plddt_value=0.8123,
                        include_boltz_metrics=False,
                    )
                ),
            ]
        ),
        encoding="utf-8",
    )

    downloaded_urls: list[str] = []

    def _fake_downloader(url: str, destination: Path) -> None:
        downloaded_urls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "data_test\n#\nloop_\n_atom_site.group_PDB\n",
            encoding="utf-8",
        )

    async with session_factory() as session:
        job = await import_proteinbase_bundle(
            session=session,
            bundle_path=bundle_path,
            dataset_name="ProteinBase RBX1 Selected Submissions",
            job_name="ProteinBase RBX1 Import",
            downloader=_fake_downloader,
            imported_at=datetime(2026, 4, 14, 18, 0, 0),
        )

        await session.refresh(job)
        designs = (
            await session.execute(
                select(Design).where(Design.job_id == job.id).order_by(Design.name.asc())
            )
        ).scalars().all()

    assert job.status == "completed"
    assert job.model_id == "proteinbase"
    assert job.mode == "external_import"
    assert job.stage_family == "validation"
    assert job.stage_mode == "proteinbase_import"
    assert job.selection_dataset_name == "ProteinBase RBX1 Selected Submissions"
    assert job.output_dir is not None
    assert Path(job.output_dir).exists()
    assert len(designs) == 2
    assert downloaded_urls == [
        "https://proteinbase-pub.t3.storage.dev/boltz-entry.cif",
        "https://proteinbase-pub.t3.storage.dev/esmfold-default.cif",
    ]

    boltz_design = designs[0]
    esmfold_design = designs[1]

    assert boltz_design.name == "LGDL_RBX1_001"
    assert boltz_design.stage_family == "validation"
    assert boltz_design.stage_mode == "proteinbase_import"
    assert boltz_design.pdb_path.endswith("LGDL_RBX1_001.cif")
    assert Path(boltz_design.pdb_path).exists()
    assert boltz_design.plddt_overall == pytest.approx(76.22371315956116)
    assert boltz_design.binder_length == 60
    assert boltz_design.ipsae == pytest.approx(0.57821)
    assert boltz_design.iptm == pytest.approx(0.8910560011863708)
    assert boltz_design.complex_iplddt == pytest.approx(0.7517083883285522)
    assert boltz_design.confidence_metrics["min_iPSAE"] == pytest.approx(0.313784)
    assert boltz_design.confidence_metrics["proteinbase"]["protein_url"] == "https://proteinbase.com/proteins/boltz-entry"

    assert esmfold_design.name == "LGDL_RBX1_002"
    assert esmfold_design.plddt_overall == pytest.approx(42.09418604651162)
    assert esmfold_design.binder_length == 60
    assert esmfold_design.ipsae is None
    assert esmfold_design.iptm is None
    assert esmfold_design.pdb_path.endswith("LGDL_RBX1_002.cif")
    assert esmfold_design.confidence_metrics["structure_prediction_url"] == "https://proteinbase-pub.t3.storage.dev/esmfold-default.cif"

    await engine.dispose()


@pytest.mark.asyncio
async def test_design_response_keeps_untyped_proteinbase_sequence_as_lineage_only(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    imported_at = datetime(2026, 4, 14, 18, 0, 0)

    async with session_factory() as session:
        job = Job(
            id="proteinbase-list-job-1",
            name="ProteinBase RBX1 Import",
            status="completed",
            model_id="proteinbase",
            mode="external_import",
            params={},
            stage_family="validation",
            stage_mode="proteinbase_import",
            created_at=imported_at,
            completed_at=imported_at,
        )
        design = Design(
            id="proteinbase-list-design-1",
            job_id=job.id,
            name="LGDL_RBX1_001",
            pdb_path=str(tmp_path / "LGDL_RBX1_001.cif"),
            stage_family="validation",
            stage_mode="proteinbase_import",
            provenance={
                "source": "proteinbase",
                "author": "nanogenomic",
                "sequence": "ASHMPW",
                "length_aa": 6,
            },
            confidence_metrics={
                "proteinbase": {
                    "author": "nanogenomic",
                    "sequence": "ASHMPW",
                    "length_aa": 6,
                }
            },
            created_at=imported_at,
        )
        session.add(job)
        session.add(design)
        await session.commit()
        await session.refresh(design)

        response = _design_to_response(design)

    assert response.binder_sequence is None
    assert response.binder_length is None
    assert isinstance(response.provenance, dict)
    assert response.provenance["sequence"] == "ASHMPW"
    assert response.provenance["length_aa"] == 6

    await engine.dispose()


@pytest.mark.asyncio
async def test_import_proteinbase_bundle_route_returns_job_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    bundle_path = tmp_path / "selected_submissions.jsonl"
    bundle_path.write_text("", encoding="utf-8")
    resolved_bundle_paths: list[Path] = []

    async def _fake_import(*, session: AsyncSession, bundle_path: str | Path, dataset_name: str, job_name: str | None = None, downloader=None, imported_at=None):
        resolved_bundle_paths.append(Path(bundle_path))
        job = Job(
            id="import-job-1",
            name=job_name or dataset_name,
            status="completed",
            model_id="proteinbase",
            mode="external_import",
            params={"bundle_path": str(bundle_path)},
            output_dir=str(tmp_path / "imported"),
            stage_family="validation",
            stage_mode="proteinbase_import",
            selection_dataset_name=dataset_name,
            created_at=imported_at or datetime(2026, 4, 14, 18, 0, 0),
            completed_at=imported_at or datetime(2026, 4, 14, 18, 0, 0),
        )
        session.add(job)
        session.add(
            Design(
                id="design-1",
                job_id=job.id,
                name="LGDL_RBX1_001",
                pdb_path=str(tmp_path / "imported" / "LGDL_RBX1_001.cif"),
                stage_family="validation",
                stage_mode="proteinbase_import",
                created_at=imported_at or datetime(2026, 4, 14, 18, 0, 0),
            )
        )
        await session.commit()
        return job

    monkeypatch.setattr("routers.jobs.import_proteinbase_bundle", _fake_import)
    monkeypatch.setattr("routers.jobs.resolve_allowed_path", lambda path: bundle_path)

    async with session_factory() as session:
        response = await import_proteinbase_bundle_job(
            ProteinBaseBundleImportRequest(
                bundle_path="inputs/imports/selected_submissions.jsonl",
                dataset_name="ProteinBase RBX1 Selected Submissions",
                job_name="ProteinBase RBX1 Import",
            ),
            session=session,
        )

    assert resolved_bundle_paths == [bundle_path]
    assert response.id == "import-job-1"
    assert response.name == "ProteinBase RBX1 Import"
    assert response.status == "completed"
    assert response.model_id == "proteinbase"
    assert response.mode == "external_import"
    assert response.stage_family == "validation"
    assert response.stage_mode == "proteinbase_import"
    assert response.selection_dataset_name == "ProteinBase RBX1 Selected Submissions"
    assert response.design_count == 1

    await engine.dispose()


def test_build_plotly_metrics_includes_promoted_ipsae_fields() -> None:
    design = Design(
        id="design-plotly-1",
        job_id="job-plotly-1",
        name="LGDL_RBX1_001",
        ipsae=0.57821,
        ipsae_binder_to_target=0.481,
        ipsae_target_to_binder=0.512,
        ipsae_d0chn=0.123,
        ipsae_d0dom=0.456,
        complex_iplddt=0.7517,
        complex_ipde=0.5991,
        created_at=datetime(2026, 4, 14, 18, 0, 0),
    )

    metrics = _build_plotly_metrics(design)

    assert metrics["ipsae"] == pytest.approx(0.57821)
    assert metrics["ipsae_binder_to_target"] == pytest.approx(0.481)
    assert metrics["ipsae_target_to_binder"] == pytest.approx(0.512)
    assert metrics["ipsae_d0chn"] == pytest.approx(0.123)
    assert metrics["ipsae_d0dom"] == pytest.approx(0.456)
    assert metrics["complex_iplddt"] == pytest.approx(0.7517)
    assert metrics["complex_ipde"] == pytest.approx(0.5991)


@pytest.mark.asyncio
async def test_collect_plotly_metrics_loads_promoted_ipsae_fields_from_db(tmp_path: Path) -> None:
    session_factory, engine = await _build_session_factory(tmp_path)
    imported_at = datetime(2026, 4, 14, 18, 0, 0)

    async with session_factory() as session:
        job = Job(
            id="plotly-job-1",
            name="ProteinBase RBX1 Import",
            status="completed",
            model_id="proteinbase",
            mode="external_import",
            params={},
            created_at=imported_at,
            completed_at=imported_at,
        )
        design = Design(
            id="plotly-design-1",
            job_id=job.id,
            name="LGDL_RBX1_001",
            pdb_path=str(tmp_path / "LGDL_RBX1_001.cif"),
            stage_family="validation",
            stage_mode="proteinbase_import",
            ipsae=0.57821,
            ipsae_binder_to_target=0.481,
            ipsae_target_to_binder=0.512,
            ipsae_d0chn=0.123,
            ipsae_d0dom=0.456,
            complex_iplddt=0.7517,
            complex_ipde=0.5991,
            created_at=imported_at,
        )
        session.add(job)
        session.add(design)
        await session.commit()

    async with session_factory() as session:
        response = await _collect_plotly_metrics(
            job_id="plotly-job-1",
            include_children=False,
            requested_design_ids=None,
            limit=100,
            offset=0,
            session=session,
        )

    assert response.metric_keys == sorted(response.metric_keys)
    assert "ipsae" in response.metric_keys
    assert "ipsae_binder_to_target" in response.metric_keys
    assert "ipsae_target_to_binder" in response.metric_keys
    assert "ipsae_d0chn" in response.metric_keys
    assert "ipsae_d0dom" in response.metric_keys
    assert response.points[0].metrics["ipsae"] == pytest.approx(0.57821)
    assert response.points[0].metrics["ipsae_binder_to_target"] == pytest.approx(0.481)
    assert response.points[0].metrics["ipsae_target_to_binder"] == pytest.approx(0.512)
    assert response.points[0].metrics["ipsae_d0chn"] == pytest.approx(0.123)
    assert response.points[0].metrics["ipsae_d0dom"] == pytest.approx(0.456)

    await engine.dispose()
