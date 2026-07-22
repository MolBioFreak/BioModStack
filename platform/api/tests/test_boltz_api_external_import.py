from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, ExternalResultImport, Job
from services.external_imports.boltz_api import BoltzImportError, preview_boltz_api_run
from services.external_imports.service import process_external_import, queue_external_import, recover_external_imports
from services.external_imports.worker import ExternalImportWorker


_CIF = """data_test
#
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
ATOM 1 CA ALA A 1 0.0 0.0 0.0 A
ATOM 2 CB ALA A 1 1.0 0.0 0.0 A
ATOM 3 "C1'" DA B 1 5.0 0.0 0.0 B
ATOM 4 C3' DA B 1 6.0 0.0 0.0 B
#
"""


def _npz_bytes(matrix: np.ndarray) -> bytes:
    stream = io.BytesIO()
    np.savez(stream, pae=matrix)
    return stream.getvalue()


def _write_run(
    root: Path,
    *,
    job_id: str = "sab_pred_test123",
    resource: str = "predictions:structure-and-binding",
    status: str = "succeeded",
    unsafe_member: str | None = None,
) -> Path:
    run_dir = root / job_id
    outputs = run_dir / "outputs"
    outputs.mkdir(parents=True)
    run = {
        "id": job_id,
        "status": status,
        "model": "boltz-2.1",
        "model_version": "v2026-03-01",
        "created_at": "2026-07-19T12:00:00Z",
        "started_at": "2026-07-19T12:00:01Z",
        "completed_at": "2026-07-19T12:00:10Z",
        "data_deleted_at": None,
        "workspace_id": "ws_test",
        "input": {
            "entities": [
                {"protein": {"id": "A", "sequence": "A"}},
                {"dna": {"id": "B", "sequence": "A"}},
            ],
            "num_samples": 1,
        },
    }
    (run_dir / "run.json").write_text(json.dumps(run))
    (run_dir / ".boltz-run.json").write_text(
        json.dumps({"schema_version": 1, "resource": resource, "job_id": job_id})
    )
    metrics = {
        "best_sample": {"metrics": {"structure_confidence": 0.9, "ptm": 0.8, "iptm": 0.7, "complex_plddt": 0.75}},
        "all_sample_results": [
            {"metrics": {"structure_confidence": 0.9, "ptm": 0.8, "iptm": 0.7, "complex_plddt": 0.75}}
        ],
    }
    with tarfile.open(outputs / "archive.tar.gz", "w:gz") as archive:
        members = {
            "prediction/metrics.json": json.dumps(metrics).encode(),
            "prediction/sample_0_predicted_structure.cif": _CIF.encode(),
            "prediction/sample_0_pae.npz": _npz_bytes(np.asarray([[1.0, 4.0], [4.0, 1.0]])),
        }
        if unsafe_member:
            members[unsafe_member] = b"escape"
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return run_dir


def test_preview_validates_complete_structure_run_without_extracting(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)

    preview = preview_boltz_api_run(run_dir)

    assert preview.importable is True
    assert preview.provider == "boltz_api"
    assert preview.resource_type == "predictions:structure-and-binding"
    assert preview.provider_job_id == "sab_pred_test123"
    assert preview.sample_count == 1
    assert preview.entities == [
        {"entity_index": 0, "molecule_type": "protein", "chain_ids": ["A"], "sequence": "A"},
        {"entity_index": 1, "molecule_type": "dna", "chain_ids": ["B"], "sequence": "A"},
    ]
    assert len(preview.source_fingerprint) == 64
    assert not (run_dir / "prediction").exists()


def test_preview_accepts_the_flat_entity_shape_emitted_by_boltz_api(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    run = json.loads((run_dir / "run.json").read_text())
    run["input"]["entities"] = [
        {"type": "protein", "chain_ids": ["A"], "value": "A", "modifications": []},
        {"type": "dna", "chain_ids": ["B"], "value": "A", "modifications": []},
    ]
    (run_dir / "run.json").write_text(json.dumps(run))

    preview = preview_boltz_api_run(run_dir)

    assert [entity["molecule_type"] for entity in preview.entities] == ["protein", "dna"]
    assert [entity["sequence"] for entity in preview.entities] == ["A", "A"]


def test_preview_rejects_provider_job_id_that_could_escape_the_publish_root(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path)
    run = json.loads((run_dir / "run.json").read_text())
    run["id"] = "../escape"
    (run_dir / "run.json").write_text(json.dumps(run))
    checkpoint = json.loads((run_dir / ".boltz-run.json").read_text())
    checkpoint["job_id"] = "../escape"
    (run_dir / ".boltz-run.json").write_text(json.dumps(checkpoint))

    with pytest.raises(BoltzImportError, match="unsafe characters"):
        preview_boltz_api_run(run_dir)


def test_preview_rejects_archive_path_traversal(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, unsafe_member="../../escape.txt")

    with pytest.raises(BoltzImportError, match="ARCHIVE_UNSAFE"):
        preview_boltz_api_run(run_dir)


def test_preview_fails_closed_for_unsupported_resource(tmp_path: Path) -> None:
    run_dir = _write_run(
        tmp_path,
        job_id="prot_des_test123",
        resource="protein:design",
    )

    preview = preview_boltz_api_run(run_dir)

    assert preview.importable is False
    assert preview.error_code == "RESOURCE_UNSUPPORTED"
    assert preview.resource_type == "protein:design"


@pytest.mark.asyncio
async def test_import_creates_authoritative_viewer_job_and_is_idempotent(tmp_path: Path) -> None:
    source = _write_run(tmp_path / "source")
    data_root = tmp_path / "data"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preview = preview_boltz_api_run(source)
        async with Session() as session:
            queued = await queue_external_import(
                session,
                source_dir=source,
                preview_fingerprint=preview.source_fingerprint,
                dataset_name="Synthetic Boltz API run",
                job_name="Imported structure",
            )
            import_id = queued.id

        async with Session() as session:
            completed = await process_external_import(session, import_id=import_id, data_root=data_root)
            assert completed.state == "completed"
            assert completed.bms_job_id

        async with Session() as session:
            job = await session.get(Job, completed.bms_job_id)
            designs = list((await session.execute(select(Design).where(Design.job_id == job.id))).scalars())
            assert job.status == "completed"
            assert job.queue_status == "completed"
            assert job.model_id == "boltz2"
            assert job.mode == "external_import"
            assert job.provenance["external_import"]["provider_job_id"] == "sab_pred_test123"
            assert len(designs) == 1
            design = designs[0]
            assert Path(design.pdb_path).is_file()
            assert Path(design.aligned_error_path).is_file()
            assert design.review_profile_id == "structure_prediction_v1"
            assert design.review_contract_source == "producer"
            assert design.review_role_map["has_binder"] is False
            assert design.review_role_map["chains"]["A"]["molecule_type"] == "protein"
            assert design.review_role_map["chains"]["B"]["molecule_type"] == "dna"
            assert design.plddt_overall == pytest.approx(75.0)
            assert design.ptm == pytest.approx(0.8)
            assert design.conf_score == pytest.approx(0.9)
            assert design.iptm is None
            assert design.confidence_metrics["boltz_api"]["raw"]["iptm"] == pytest.approx(0.7)

        async with Session() as session:
            duplicate = await queue_external_import(
                session,
                source_dir=source,
                preview_fingerprint=preview.source_fingerprint,
                dataset_name="Different display name",
                job_name=None,
            )
            imports = list((await session.execute(select(ExternalResultImport))).scalars())
            jobs = list((await session.execute(select(Job))).scalars())
            designs = list((await session.execute(select(Design))).scalars())
            assert duplicate.id == import_id
            assert len(imports) == 1
            assert len(jobs) == 1
            assert len(designs) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_completes_the_existing_remote_submission_job_in_place(tmp_path: Path) -> None:
    source = _write_run(tmp_path / "source")
    data_root = tmp_path / "data"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preview = preview_boltz_api_run(source)
        async with Session() as session:
            remote_job = Job(
                id="remote-submission-job",
                name="Remote structure",
                status="running",
                queue_status="running",
                model_id="boltz_api",
                mode="external_api",
                params={"provider_state": "downloaded", "provider_job_id": "sab_pred_test123"},
            )
            session.add(remote_job)
            await session.commit()
            queued = await queue_external_import(
                session,
                source_dir=source,
                preview_fingerprint=preview.source_fingerprint,
                dataset_name="Remote Boltz API run",
                job_name=remote_job.name,
                bms_job_id=remote_job.id,
            )
            import_id = queued.id

        async with Session() as session:
            completed = await process_external_import(session, import_id=import_id, data_root=data_root)
            assert completed.bms_job_id == "remote-submission-job"
            job = await session.get(Job, "remote-submission-job")
            designs = list((await session.execute(select(Design).where(Design.job_id == job.id))).scalars())
            assert job.status == "completed"
            assert job.model_id == "boltz2"
            assert job.params["provider_state"] == "completed"
            assert job.params["provider_job_id"] == "sab_pred_test123"
            assert len(designs) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_remote_job_with_changed_archive_is_rejected(tmp_path: Path) -> None:
    source = _write_run(tmp_path / "source")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first = preview_boltz_api_run(source)
        async with Session() as session:
            await queue_external_import(
                session,
                source_dir=source,
                preview_fingerprint=first.source_fingerprint,
                dataset_name="first",
                job_name=None,
            )

        _write_run(tmp_path / "replacement", job_id="sab_pred_test123")
        replacement_archive = tmp_path / "replacement" / "sab_pred_test123" / "outputs" / "archive.tar.gz"
        source_archive = source / "outputs" / "archive.tar.gz"
        source_archive.write_bytes(replacement_archive.read_bytes() + b"changed")
        changed = preview_boltz_api_run(source)

        async with Session() as session:
            with pytest.raises(BoltzImportError, match="IMPORT_IDENTITY_CONFLICT"):
                await queue_external_import(
                    session,
                    source_dir=source,
                    preview_fingerprint=changed.source_fingerprint,
                    dataset_name="changed",
                    job_name=None,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_processes_discovered_import_after_request_process_loss(tmp_path: Path) -> None:
    source = _write_run(tmp_path / "source")
    data_root = tmp_path / "data"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preview = preview_boltz_api_run(source)
        async with Session() as session:
            queued = await queue_external_import(
                session,
                source_dir=source,
                preview_fingerprint=preview.source_fingerprint,
                dataset_name="worker test",
                job_name=None,
            )

        worker = ExternalImportWorker(Session, data_root=data_root, poll_interval=0.01)
        assert await worker.run_once() == queued.id

        async with Session() as session:
            completed = await session.get(ExternalResultImport, queued.id)
            assert completed.state == "completed"
            assert completed.bms_job_id is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_redacts_credentials_from_copied_source_and_persisted_records(tmp_path: Path) -> None:
    source = _write_run(tmp_path / "source")
    run = json.loads((source / "run.json").read_text())
    run["api_token"] = "supersecret-run-token"
    run["callback_url"] = "https://user:password@example.invalid/result?token=supersecret-url-token"
    (source / "run.json").write_text(json.dumps(run))
    checkpoint = json.loads((source / ".boltz-run.json").read_text())
    checkpoint["authorization"] = "Bearer supersecret-checkpoint-token"
    (source / ".boltz-run.json").write_text(json.dumps(checkpoint))

    data_root = tmp_path / "data"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'secrets.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        preview = preview_boltz_api_run(source)
        async with Session() as session:
            queued = await queue_external_import(
                session,
                source_dir=source,
                preview_fingerprint=preview.source_fingerprint,
                dataset_name="Secret redaction",
                job_name=None,
            )
            completed = await process_external_import(session, import_id=queued.id, data_root=data_root)
            job = await session.get(Job, completed.bms_job_id)
            assert job is not None
            persisted = json.dumps({"params": job.params, "provenance": job.provenance})
            source_text = "\n".join(path.read_text() for path in (Path(job.output_dir) / "source").glob("*.json"))
            assert "supersecret" not in persisted
            assert "supersecret" not in source_text
            assert "[REDACTED]" in source_text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_does_not_complete_a_linked_import_without_canonical_artifacts(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            job = Job(
                id="remote-incomplete",
                name="remote incomplete",
                status="running",
                queue_status="running",
                model_id="boltz_api",
                mode="external_api",
                params={"provider_state": "importing"},
            )
            session.add(job)
            session.add(ExternalResultImport(
                id="import-committing",
                provider_id="boltz_api",
                resource_type="predictions:structure-and-binding",
                provider_job_id="sab_pred_recovery",
                state="committing",
                source_path=str(tmp_path),
                source_fingerprint="a" * 64,
                run_metadata_sha256="b" * 64,
                archive_sha256="c" * 64,
                bms_job_id=job.id,
                dataset_name="recovery",
                provider_metadata={},
            ))
            await session.commit()
            assert await recover_external_imports(session) == ["import-committing"]
            record = await session.get(ExternalResultImport, "import-committing")
            assert record.state == "failed"
            assert record.failure_code == "IMPORT_INTERRUPTED"
            assert (await session.execute(select(Design).where(Design.job_id == job.id))).scalar_one_or_none() is None
    finally:
        await engine.dispose()
