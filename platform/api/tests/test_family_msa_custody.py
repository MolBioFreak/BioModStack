"""Scratch-only tests for Experiment artifact byte custody."""
import asyncio
import hashlib
import io
import shutil
import sqlite3

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_migrations import run_all
from experiment_models import (ExperimentResource, ExperimentArtifact, ExperimentArtifactBlob)
from services.family_msa.publication import CustodyError, install_blob, retain_artifact, retained_path


def test_blob_source_validation_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))
    data = b">one\nAC-G\n"
    digest = hashlib.sha256(data).hexdigest()
    with pytest.raises(CustodyError):
        install_blob(io.BytesIO(data), expected_sha256="0" * 64)
    assert not list((tmp_path / "blobs" / ".staging").iterdir())
    sha, size, key = install_blob(io.BytesIO(data), expected_sha256=digest, expected_size=len(data))
    assert (sha, size) == (digest, len(data))
    assert install_blob(io.BytesIO(data)) == (sha, size, key)
    (tmp_path / "blobs" / key).write_bytes(b"bad")
    with pytest.raises(CustodyError):
        install_blob(io.BytesIO(data))


def test_project_custody_replay_and_original_removal(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))

    async def exercise():
        run_all(tmp_path / "pm.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pm.db'}")
        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        source = tmp_path / "job-output"
        source.write_bytes(b">row\nAC-G\n")
        async with sessions() as s:
            s.add(ExperimentResource(id="workspace", kind="workspace"))
            await s.flush()
            s.add_all([ExperimentResource(id="project1", kind="project", workspace_id="workspace", lifecycle_owner_id="workspace"),
                       ExperimentResource(id="project2", kind="project", workspace_id="workspace", lifecycle_owner_id="workspace")])
            await s.commit()
        async def put(s, owner, resource_id, data):
            return await retain_artifact(s, source=io.BytesIO(data), workspace_id="workspace",
                lifecycle_owner_id=owner, resource_id=resource_id, logical_role="alignment",
                logical_key="alignment.fasta", schema_name="alignment.native", schema_version="1",
                media_type="text/plain", provenance={"producer": "import"})
        data = source.read_bytes()
        async with sessions() as s:
            # Simulate a crash/rollback after installing bytes but before PM commit.
            await put(s, "project1", "artifact1", data)
            await s.rollback()
        async with sessions() as s:
            assert await s.get(ExperimentArtifact, "artifact1") is None
            await put(s, "project1", "artifact1", data)
            await put(s, "project2", "artifact2", data)
            await s.commit()
        source.unlink()
        async with sessions() as s:
            for id in ("artifact1", "artifact2"):
                artifact = await s.get(ExperimentArtifact, id)
                assert (await retained_path(s, artifact)).read_bytes() == data
            assert len((await s.scalars(select(ExperimentArtifactBlob))).all()) == 1
            await put(s, "project1", "artifact1", data)
            with pytest.raises(CustodyError):
                await put(s, "project1", "artifact1", b"different")
            await s.rollback()
        # A consistent PM database backup alone does not contain blob bytes.
        with sqlite3.connect(tmp_path / "pm.db") as original, sqlite3.connect(tmp_path / "backup.db") as backup:
            original.backup(backup)
        shutil.copytree(tmp_path / "blobs", tmp_path / "backup-blobs")
        monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "backup-blobs"))
        backup_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backup.db'}")
        async with async_sessionmaker(backup_engine, expire_on_commit=False)() as s:
            artifact = await s.get(ExperimentArtifact, "artifact2")
            assert (await retained_path(s, artifact)).read_bytes() == data
        await backup_engine.dispose()
        await engine.dispose()
    asyncio.run(exercise())


def test_concurrent_catalog_and_replay_media(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))

    async def exercise():
        run_all(tmp_path / "pm.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pm.db'}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as s:
            s.add(ExperimentResource(id="workspace", kind="workspace"))
            await s.commit()
        async def put(resource, media="text/plain"):
            async with sessions() as s:
                obj = await retain_artifact(s, source=io.BytesIO(b"same"),
                    workspace_id="workspace", lifecycle_owner_id="workspace",
                    resource_id=resource, logical_role="alignment", logical_key="x",
                    schema_name="native", schema_version="1", media_type=media, provenance={})
                await s.commit()
                return obj.resource_id
        assert len(await asyncio.gather(*(put(f"a{i}") for i in range(12)))) == 12
        assert await asyncio.gather(put("same-id"), put("same-id")) == ["same-id", "same-id"]
        await put("other-media", "application/octet-stream")
        with pytest.raises(CustodyError):
            await put("same-id", "application/octet-stream")
        async with sessions() as s:
            assert len((await s.scalars(select(ExperimentArtifactBlob))).all()) == 1
            assert len((await s.scalars(select(ExperimentArtifact))).all()) == 14
        await engine.dispose()
    asyncio.run(exercise())
