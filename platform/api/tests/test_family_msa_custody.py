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
from services.family_msa.publication import CustodyError, install_blob, retain_artifact, retained_path, retain_job_artifact


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


def test_real_job_artifact_copy_survives_job_deletion(tmp_path, monkeypatch):
    from database import Base, Job, JobArtifact

    results = tmp_path / 'results'
    output = results / 'job'
    output.mkdir(parents=True)
    data = b'>fixture\nAC-G\n'
    source = output / 'alignment.fasta'
    source.write_bytes(data)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(results))
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))

    async def exercise():
        run_all(tmp_path / 'pm.db')
        core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
        pm_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pm.db'}")
        @event.listens_for(core_engine.sync_engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        async with core_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        core_sessions = async_sessionmaker(core_engine, expire_on_commit=False)
        pm_sessions = async_sessionmaker(pm_engine, expire_on_commit=False)
        async with core_sessions() as core:
            core.add(Job(id='job', name='fixture', model_id='family_msa_protein', mode='family_msa', status='completed', params={}, output_dir=str(output)))
            await core.flush()
            core.add(JobArtifact(id='native', owner_job_id='job', attempt=0, logical_path='alignment.fasta', storage_path=str(source), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), media_type='text/plain', provenance={}))
            await core.commit()
        async with pm_sessions() as pm:
            pm.add(ExperimentResource(id='workspace', kind='workspace'))
            await pm.commit()
        args = dict(job_id='job', artifact_id='native', workspace_id='workspace', lifecycle_owner_id='workspace', resource_id='retained', logical_role='alignment', schema_name='alignment.native', schema_version='1')
        async with core_sessions() as core, pm_sessions() as pm:
            with pytest.raises(CustodyError):
                await retain_job_artifact(core, pm, **{**args, 'job_id': 'wrong'})
            source.write_bytes(b'changed')
            from services.global_experiments.adapters import AdapterError
            with pytest.raises(AdapterError):
                await retain_job_artifact(core, pm, **args)
            source.write_bytes(data)
            await retain_job_artifact(core, pm, **args)
            await pm.rollback()
        async with core_sessions() as core, pm_sessions() as pm:
            assert await pm.get(ExperimentArtifact, 'retained') is None
            await retain_job_artifact(core, pm, **args)
            await pm.commit()
        async with core_sessions() as core:
            await core.delete(await core.get(Job, 'job'))
            await core.commit()
        shutil.rmtree(output)
        async with core_sessions() as core, pm_sessions() as pm:
            assert await core.get(JobArtifact, 'native') is None
            artifact = await pm.get(ExperimentArtifact, 'retained')
            assert (await retained_path(pm, artifact)).read_bytes() == data
        await core_engine.dispose()
        await pm_engine.dispose()
    asyncio.run(exercise())


def test_streamed_job_blob_concurrent_and_nofollow(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from services.family_msa.publication import _install_job_blob
    from services.global_experiments.adapters import AdapterError
    root = tmp_path / "results"
    root.mkdir()
    source = root / "source"
    source.write_bytes(b"abc" * 1000000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _install_job_blob(str(source), digest, source.stat().st_size, root), range(6)))
    assert len(set(results)) == 1
    assert (tmp_path / "blobs" / results[0][2]).read_bytes() == source.read_bytes()
    assert not list((tmp_path / "blobs" / ".staging").iterdir())
    outside = tmp_path / "outside"
    outside.write_bytes(source.read_bytes())
    (root / "alias").symlink_to(outside)
    (root / "diralias").symlink_to(tmp_path, target_is_directory=True)
    for path in (root / "alias", root / "diralias" / "outside", outside, root / ".." / "outside"):
        with pytest.raises(AdapterError):
            _install_job_blob(str(path), digest, source.stat().st_size, root)
    with pytest.raises(AdapterError):
        _install_job_blob(str(source), "0" * 64, source.stat().st_size, root)
    with pytest.raises(AdapterError):
        _install_job_blob(str(source), digest, source.stat().st_size + 1, root)
    assert not list((tmp_path / "blobs" / ".staging").iterdir())


def test_streamed_job_blob_detects_mutation(tmp_path, monkeypatch):
    import os
    from services.family_msa import publication
    from services.global_experiments.adapters import AdapterError
    root = tmp_path / "results"
    root.mkdir()
    source = root / "source"
    source.write_bytes(b"a" * 3000000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))
    original_read = os.read
    changed = False
    def racing_read(fd, n):
        nonlocal changed
        chunk = original_read(fd, n)
        if chunk and not changed:
            changed = True
            with source.open("r+b") as f:
                f.seek(2000000)
                f.write(b"b")
        return chunk
    monkeypatch.setattr(publication.os, "read", racing_read)
    with pytest.raises(AdapterError):
        publication._install_job_blob(str(source), digest, 3000000, root)
    assert not list((tmp_path / "blobs" / ".staging").iterdir())
    assert not list((tmp_path / "blobs").glob("sha256/*/*"))


def test_streamed_job_blob_over_four_gib(tmp_path, monkeypatch):
    import os
    import resource
    from services.family_msa.publication import _install_job_blob
    if os.environ.get("BMS_RUN_LARGE_CUSTODY_TEST") != "1":
        pytest.skip("opt-in: writes over 4 GiB to scratch")
    root = tmp_path / "results"
    root.mkdir()
    source = root / "sparse"
    size = 4 * 1024**3 + 1
    with source.open("wb") as f:
        f.truncate(size)
    digest = hashlib.sha256()
    block = bytes(1024 * 1024)
    for _ in range(size // len(block)):
        digest.update(block)
    digest.update(b"\0" * (size % len(block)))
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    sha, actual, key = _install_job_blob(str(source), digest.hexdigest(), size, root)
    growth = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    print(f"large custody size={actual} peak_rss_growth_kib={growth}")
    assert (sha, actual) == (digest.hexdigest(), size)
    assert (tmp_path / "blobs" / key).stat().st_size == size
    assert growth < 100 * 1024


def test_streamed_job_blob_leaf_swap_race(tmp_path, monkeypatch):
    import os
    from services.family_msa import publication
    from services.global_experiments.adapters import AdapterError
    root = tmp_path / "results"
    root.mkdir()
    source = root / "source"
    source.write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.write_bytes(b"inside")
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "blobs"))
    real_open = os.open
    def racing_open(path, flags, *args, **kwargs):
        if path == "source" and kwargs.get("dir_fd") is not None:
            source.unlink()
            source.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(publication.os, "open", racing_open)
    with pytest.raises(AdapterError):
        publication._install_job_blob(str(source), hashlib.sha256(b"inside").hexdigest(), 6, root)
    assert not (tmp_path / "blobs" / "sha256").exists()
