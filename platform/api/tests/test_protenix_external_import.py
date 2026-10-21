from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from routers import external_imports
from schemas import ExternalImportCreateRequest, ExternalImportPreviewRequest
from services.aligned_error_utils import load_aligned_error_artifact
from services.external_imports.archive import sha256_file
from services.external_imports.boltz_api import BoltzImportError
from services.external_imports.protenix import preview_protenix_run
from services.external_imports.service import process_external_import


@pytest.fixture
def source(tmp_path: Path) -> Path:
    from test_boltz_api_external_import import _CIF
    root = tmp_path / "native"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested/model_sample_7.cif").write_text(_CIF)
    (root / "nested/full.json").write_text(json.dumps({"token_pair_pae": [[1, 4], [4, 1]]}))
    (root / "nested/summary.json").write_text(json.dumps({"plddt": 82, "ptm": 0.8, "iptm": 0.7, "ranking_score": 0.77}))
    (root / "input.json").write_text('[{"name":"native"}]')
    descriptor = {
        "schema": "bms.protenix-external-import.v1", "id": "native-run", "model_version": "protenix-v2",
        "native_input_path": "input.json",
        "entities": [{"entity_index": 0, "molecule_type": "protein", "chain_ids": ["A"], "sequence": "A",
                      "modifications": [{"ptmPosition": 1, "ptmType": "CCD_AIB"}]},
                     {"entity_index": 1, "molecule_type": "dna", "chain_ids": ["B"], "sequence": "A"}],
        "settings": {"seeds": [42], "samples": 1, "steps": 200, "cycles": 10, "use_template": False},
        "samples": [{"sample_id": "seed_42/sample_7", "structure_path": "nested/model_sample_7.cif",
                     "summary_path": "nested/summary.json", "aligned_error_path": "nested/full.json"}],
    }
    (root / "protenix-import.json").write_text(json.dumps(descriptor))
    return root


@pytest.mark.asyncio
async def test_native_import_route_worker_and_viewer_readback(source, tmp_path, monkeypatch):
    monkeypatch.setattr(external_imports, "resolve_allowed_path", lambda _: source)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preview = await external_imports.preview_external_import(ExternalImportPreviewRequest(source_path="inputs/native", provider_hint="protenix_native"))
        request = ExternalImportCreateRequest(source_path="inputs/native", provider="protenix_native",
            preview_fingerprint=preview.source_fingerprint, dataset_name="Native result")
        async with Session() as session:
            queued = await external_imports.create_external_import(request, session)
        async with Session() as session:
            result = await process_external_import(session, import_id=queued.id, data_root=tmp_path / "data")
            assert result.state == "completed"
            job_id = result.bms_job_id
        async with Session() as session:
            job = await session.get(Job, job_id)
            designs = list((await session.execute(select(Design).where(Design.job_id == job_id))).scalars())
            assert job.model_id == "protenix" and job.mode == "external_import"
            assert job.started_at is None
            assert job.completed_stages == ["external_import"]
            assert job.params["external_native_settings"]["use_template"] is False
            assert job.params["entities"][0]["modifications"][0]["ptmType"] == "CCD_AIB"
            assert len(designs) == 1
            design = designs[0]
            assert design.provenance["external_import"]["sample_id"] == "seed_42/sample_7"
            assert design.pdb_path.endswith("source/nested/model_sample_7.cif")
            assert sha256_file(Path(design.pdb_path)) == sha256_file(source / "nested/model_sample_7.cif")
            assert design.iptm == 0.7 and design.plddt_overall == 82
            from services.protenix_scientific_consumer import scientific_document, verified_scalar_metrics
            document = await scientific_document(design, session)
            assert document.contentSha256 == sha256_file(Path(design.pdb_path))
            scalars = await verified_scalar_metrics(design, session)
            assert next(r for r in scalars if r['metric_key'] == 'iptm')['value'] == 0.7
            assert design.conf_score is None  # native ranking is not a shared confidence score
            assert design.confidence_metrics["protenix_native"]["raw"]["ranking_score"] == 0.77
            artifact = load_aligned_error_artifact(structure_path=design.pdb_path, aligned_error_path=design.aligned_error_path,
                aligned_error_format=design.aligned_error_format, matrix_key=design.aligned_error_key)
            assert artifact.matrix.shape == (2, 2)
            manifest = json.loads(Path(job.source_selection_manifest_path).read_text())
            assert all(sha256_file(Path(job.output_dir) / item["path"]) == item["sha256"] for item in manifest["artifacts"])
            duplicate = await external_imports.create_external_import(request, session)
            assert duplicate.id == queued.id
            # Later source loss cannot change retained custody; confidence corruption
            # is unavailable evidence, not loss of the valid structure document.
            (source / "nested/model_sample_7.cif").unlink()
            assert (await scientific_document(design, session)).contentSha256 == document.contentSha256
            Path(design.aligned_error_path).write_text('{}')
            from services.protenix_scientific_consumer import compute_persisted_pae
            pae, _, _ = await compute_persisted_pae(design, {}, session)
            assert pae['status'] != 'ok'
            assert await scientific_document(design, session) is not None
    finally:
        await engine.dispose()


def test_native_preview_binds_every_source_byte_and_rejects_escape(source):
    before = preview_protenix_run(source)
    (source / "nested/summary.json").write_text('{"plddt": 81}')
    assert preview_protenix_run(source).source_fingerprint != before.source_fingerprint
    descriptor = json.loads((source / "protenix-import.json").read_text())
    descriptor["native_input_path"] = "../escape"
    (source / "protenix-import.json").write_text(json.dumps(descriptor))
    with pytest.raises(BoltzImportError, match="SOURCE_NOT_ALLOWED"):
        preview_protenix_run(source)


def test_native_preview_rejects_missing_results_and_symlinks(source):
    summary = source / "nested/summary.json"
    summary.unlink()
    with pytest.raises(BoltzImportError, match="SOURCE_NOT_ALLOWED"):
        preview_protenix_run(source)
    summary.symlink_to(source / "input.json")
    with pytest.raises(BoltzImportError, match="SOURCE_NOT_ALLOWED"):
        preview_protenix_run(source)

