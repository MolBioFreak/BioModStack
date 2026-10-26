from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, func, text, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, InputFile, OntInstrumentRun, OntRawSignalRepresentation, _canonical_sqlite_json
from migrations.add_ont_terminal_artifact_manifests import migrate as migrate_terminal_manifests
from migrations.seal_ont_external_source_identity import migrate as seal_external_identity
from migrations.sqlite_sha256 import register_sqlite_sha256
from services import ont_raw_signal as raw
from services.container_cli_environment import container_user_namespace_args


@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['pod5', 'slow5', 'blow5'])
async def test_native_upload_is_registered_once_and_never_claims_validation(tmp_path, monkeypatch, format):
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path/'inputs'))
    monkeypatch.setenv(raw.BLOW5_STAGING_ROOT_ENV, str(tmp_path/'staging'))
    context = {'experiment_group': 'domain-test', 'state_revision_id': 'state-1', 'binding_revision_id': 'binding-1', 'head_generation': 1, 'sample_revision_id': None}
    monkeypatch.setattr(raw, '_validate_external_registration_context', AsyncMock(return_value=context))
    folder = f'inputs/ont-raw-signal/{uuid4()}'
    root = tmp_path/folder
    root.mkdir(parents=True)
    source = root/f'source.{format}'
    source.write_bytes(b'native upload awaiting semantic validation')
    index = Path(str(source)+'.idx')
    if format == 'blow5': index.write_bytes(b'index awaiting semantic validation')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "intake.db"}', json_serializer=_canonical_sqlite_json)
    @event.listens_for(engine.sync_engine, 'connect')
    def database_contract(connection, _record):
        register_sqlite_sha256(connection)
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    migrate_terminal_manifests(str(tmp_path / "intake.db"))
    seal_external_identity(str(tmp_path / "intake.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    kwargs = dict(file_path=f'{folder}/{source.name}', format=format, index_file_path=f'{folder}/{index.name}' if format == 'blow5' else None, experiment_group='domain-test', sample_id=None)
    try:
        async with factory() as session:
            first = await raw.import_uploaded_raw_signal(session, None, **kwargs)
            await session.commit()
            assert first['representation']['format'] == format
            assert first['representation']['state'] != 'ready'
            assert first['representation']['source_fidelity'] == 'unknown'
            assert not first['already_registered']
            run = await session.get(OntInstrumentRun, first['run_id'])
            assert run.minknow_run_id is None
            assert run.terminal_artifact_manifest is None
            assert run.terminal_artifact_manifest_sha256 is None
            assert run.last_minknow_payload['domain_context'] == context
            assert (await session.scalar(text('SELECT terminal_artifact_manifest IS NULL FROM ont_instrument_runs'))) == 1
        async with factory() as session:
            repeated = await raw.import_uploaded_raw_signal(session, None, **kwargs)
            await session.commit()
            assert repeated['run_id'] == first['run_id']
            assert repeated['already_registered']
            assert (await session.scalar(select(func.count()).select_from(OntInstrumentRun))) == 1
            assert (await session.scalar(select(func.count()).select_from(OntRawSignalRepresentation))) == 1
            assert (await session.scalar(select(func.count()).select_from(InputFile))) == (2 if format == 'blow5' else 1)
        assert source.read_bytes() == b'native upload awaiting semantic validation'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('relative', ['inputs/other/data.pod5', 'inputs/ont-raw-signal/../source.pod5', 'bms_results/source.pod5'])
async def test_upload_registration_rejects_paths_outside_the_intake_authority(monkeypatch, relative):
    monkeypatch.setattr(raw, '_validate_external_registration_context', AsyncMock(return_value={}))
    with pytest.raises(ValueError, match='native raw-signal upload'):
        await raw.import_uploaded_raw_signal(None, None, file_path=relative, format='pod5', index_file_path=None, experiment_group='domain', sample_id=None)


def test_rootless_podman_keeps_host_uid_but_docker_and_rootful_do_not(monkeypatch):
    monkeypatch.setattr('os.getuid', lambda: 1000)
    assert container_user_namespace_args('podman') == ['--userns=keep-id']
    assert container_user_namespace_args('docker') == []
    monkeypatch.setattr('os.getuid', lambda: 0)
    assert container_user_namespace_args('podman') == []
