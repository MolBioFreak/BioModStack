from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import numpy as np
import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from routers import external_imports
from schemas import ExternalImportCreateRequest, ExternalImportPreviewRequest


def _source(tmp_path: Path) -> Path:
    run_dir = tmp_path / "sab_pred_router"
    (run_dir / "outputs").mkdir(parents=True)
    run = {
        "id": "sab_pred_router",
        "status": "succeeded",
        "model": "boltz-2.1",
        "input": {"entities": [{"protein": {"id": "A", "sequence": "A"}}], "num_samples": 1},
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    (run_dir / ".boltz-run.json").write_text(
        json.dumps({"resource": "predictions:structure-and-binding", "job_id": "sab_pred_router"})
    )
    stream = io.BytesIO()
    np.savez(stream, pae=np.asarray([[1.0]]))
    metrics = {
        "best_sample": {"metrics": {"complex_plddt": 0.8, "ptm": 0.7, "structure_confidence": 0.9}},
        "all_sample_results": [{"metrics": {"complex_plddt": 0.8, "ptm": 0.7, "structure_confidence": 0.9}}],
    }
    cif = """data_x
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.auth_asym_id
ATOM 1 CA ALA A 1 0 0 0 A
"""
    with tarfile.open(run_dir / "outputs" / "archive.tar.gz", "w:gz") as archive:
        for name, payload in {
            "prediction/metrics.json": json.dumps(metrics).encode(),
            "prediction/sample_0_predicted_structure.cif": cif.encode(),
            "prediction/sample_0_pae.npz": stream.getvalue(),
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return run_dir


@pytest.mark.asyncio
async def test_preview_route_resolves_only_server_allowed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(external_imports, "resolve_allowed_path", lambda value: source)

    response = await external_imports.preview_external_import(ExternalImportPreviewRequest(source_path="data/source"))

    assert response.importable is True
    assert response.provider_job_id == "sab_pred_router"


@pytest.mark.asyncio
async def test_import_route_queues_durable_background_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(external_imports, "resolve_allowed_path", lambda value: source)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preview = await external_imports.preview_external_import(ExternalImportPreviewRequest(source_path="data/source"))
        background = BackgroundTasks()
        async with Session() as session:
            response = await external_imports.create_external_import(
                ExternalImportCreateRequest(
                    source_path="data/source",
                    provider="boltz_api",
                    preview_fingerprint=preview.source_fingerprint,
                    dataset_name="router test",
                ),
                background,
                session,
            )
        assert response.state == "discovered"
        assert response.provider_job_id == "sab_pred_router"
        assert len(background.tasks) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_route_maps_disallowed_path_to_typed_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(_value: str) -> Path:
        raise ValueError("Path escapes allowed root")

    monkeypatch.setattr(external_imports, "resolve_allowed_path", deny)
    with pytest.raises(HTTPException) as caught:
        await external_imports.preview_external_import(ExternalImportPreviewRequest(source_path="../../etc"))
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "SOURCE_NOT_ALLOWED"
