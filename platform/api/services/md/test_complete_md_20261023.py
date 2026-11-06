"""Offline MD lane regressions; descriptors and private SQLite, no science run."""
import asyncio
import gzip
import hashlib
import io
from pathlib import Path

import pytest
from Bio.PDB import MMCIFIO, PDBParser
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import UserTemplate, UserTemplateRevision
from services.md import starting_structures as sources
from services.md.prediction_detours import MdPredictionDetour, save_prediction_detour, load_prediction_detour


def ensemble():
    atom = 'ATOM      7  CA AGLY A  12B      1.000   2.000   3.000  0.50 20.00           C  \n'
    return ('MODEL        1\n' + atom + 'ENDMDL\nMODEL        2\n' + atom.replace('   1.000','   9.000') + 'ENDMDL\nEND\n').encode()


@pytest.mark.parametrize('format', ['pdb', 'cif'])
@pytest.mark.parametrize('encoding', ['gzip', 'zstd'])
@pytest.mark.parametrize('member', [None, 1, 2])
def test_encoded_exact_source(tmp_path, monkeypatch, format, encoding, member):
    import paths
    import pyarrow as pa
    raw = ensemble()
    if format == 'cif':
        writer = MMCIFIO(); writer.set_structure(PDBParser(QUIET=True).get_structure('fixture', io.StringIO(raw.decode())))
        out = io.StringIO(); writer.save(out); raw = out.getvalue().encode()
    stored = gzip.compress(raw) if encoding == 'gzip' else pa.compress(raw, codec='zstd').to_pybytes()
    path = tmp_path / ('ensemble.' + format + ('.gz' if encoding == 'gzip' else '.zst'))
    path.write_bytes(stored)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': tmp_path})
    monkeypatch.setattr(sources, 'get_inputs_dir', lambda: tmp_path)
    ref = sources.StartingStructureSourceRef(kind='upload', id='exact', model_number=member)
    async def resolve(ref, session):
        return sources.ResolvedStartingStructure(source_ref=ref, path=path, label=path.name)
    monkeypatch.setattr(sources, '_resolve_source', resolve)
    result = asyncio.run(sources.resolve_source(ref, None))
    try:
        consumed = sources.read_resolved_structure(result)
        assert Path(result.original_runtime_path).read_bytes() == stored == path.read_bytes()
        assert result.source_provenance['original_sha256'] == hashlib.sha256(stored).hexdigest()
        assert result.source_provenance['decoded_sha256'] == hashlib.sha256(raw).hexdigest()
        assert result.source_provenance['consumed_sha256'] == consumed.sha256
        assert consumed.data == (sources.select_structure_model(raw, format, member) if member else raw)
        assert not list(tmp_path.glob('.decode-*'))
        # Exercise the launch snapshot and cold retained-source reader, not only decoding.
        from services.md import launch_contract as contract
        from test_md_native_contract import _guided_intent
        from database import Job
        intent, catalog, profile = _guided_intent()
        intent['input']['source_ref'] = ref.model_dump(mode='json')
        intent = sources.MdNativeLaunchIntent.model_validate(intent)
        spec = sources.compile_native_job_intent(intent, profile=profile, source_token=sources.resolved_runtime_path(result))
        monkeypatch.setattr(contract, '_bound_engine_runtime_identity', lambda engine: {'image_name':'inert', 'sif_sha256':'a'*64})
        original_digest = hashlib.sha256(stored).hexdigest()
        provenance = {**result.source_provenance, 'source_ref':ref.model_dump(mode='json'), 'source_sha256':consumed.sha256,
            'native_input_identity': {'structure':consumed.sha256, 'original_structure':original_digest}}
        output = tmp_path / 'returned';
        materialized = contract.materialize_md_job_spec(params={'md_job_spec':spec, 'md_source_provenance':provenance},
            job_id='33333333-3333-4333-8333-333333333333', output_dir=output, chemistry_catalog=catalog,
            resolve_runtime_path=lambda value: result.original_runtime_path if value == 'bms-md-starting-structure:original' else value)
        original_path = Path(materialized['md_source_provenance']['original_snapshot']['path'])
        assert original_path.read_bytes() == stored
        assert original_path.name.endswith('.gz' if encoding == 'gzip' else '.zst')
        job = Job(id='33333333-3333-4333-8333-333333333333', model_id='molecular_dynamics', output_dir=str(output), params=materialized)
        class Session:
            async def get(self, cls, identity):
                return job if cls is Job and identity == job.id else None
        path.unlink()
        cold_ref = ref.model_copy(update={'snapshot_job_id':job.id})
        cold = asyncio.run(sources.resolve_source(cold_ref, Session()))
        try:
            assert sources.read_resolved_structure(cold).data == consumed.data
            assert Path(cold.original_runtime_path).read_bytes() == stored
        finally:
            cold.close()
    finally:
        result.close()


@pytest.mark.parametrize('encoding', ['identity', 'gzip', 'zstd'])
@pytest.mark.parametrize('member', [1, 2])
def test_binarycif_decoder_keeps_stored_native_decoded_domains(tmp_path, monkeypatch, encoding, member):
    import paths
    import pyarrow as pa
    from biotite.structure.io.pdbx import BinaryCIFFile, BinaryCIFBlock, BinaryCIFCategory
    writer = MMCIFIO(); writer.set_structure(PDBParser(QUIET=True).get_structure('fixture', io.StringIO(ensemble().decode())))
    out = io.StringIO(); writer.save(out)
    columns = MMCIF2Dict(io.StringIO(out.getvalue()))
    categories = {}
    for key, values in columns.items():
        if '.' not in key:
            continue
        category, field = key[1:].split('.', 1)
        categories.setdefault(category, {})[field] = values
    binary = BinaryCIFFile({'fixture':BinaryCIFBlock({key:BinaryCIFCategory(value) for key,value in categories.items()})})
    target = io.BytesIO(); binary.write(target); native = target.getvalue()
    stored = native if encoding == 'identity' else gzip.compress(native) if encoding == 'gzip' else pa.compress(native, codec='zstd').to_pybytes()
    suffix = '.bcif' + ('.gz' if encoding == 'gzip' else '.zst' if encoding == 'zstd' else '')
    path = tmp_path / ('fixture' + suffix); path.write_bytes(stored)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs':tmp_path})
    monkeypatch.setattr(sources, 'get_inputs_dir', lambda: tmp_path)
    ref = sources.StartingStructureSourceRef(kind='upload', id='fixture', model_number=member)
    async def resolve(ref, session):
        return sources.ResolvedStartingStructure(source_ref=ref, path=path, label=path.name)
    monkeypatch.setattr(sources, '_resolve_source', resolve)
    resolved = asyncio.run(sources.resolve_source(ref, None))
    try:
        assert Path(resolved.original_runtime_path).read_bytes() == stored
        assert resolved.source_provenance['original_sha256'] == hashlib.sha256(stored).hexdigest()
        assert resolved.source_provenance['native_sha256'] == hashlib.sha256(native).hexdigest()
        selected = MMCIF2Dict(io.StringIO(sources.read_resolved_structure(resolved).data.decode()))
        rows = [i for i,n in enumerate(columns['_atom_site.pdbx_PDB_model_num']) if int(n) == member]
        for key, values in columns.items():
            if key.startswith('_atom_site.'):
                assert selected[key] == [values[i] for i in rows]
    finally:
        resolved.close()


def test_cif_model_annotations_follow_selected_model():
    raw = ensemble()
    writer = MMCIFIO(); writer.set_structure(PDBParser(QUIET=True).get_structure('fixture', io.StringIO(raw.decode())))
    out = io.StringIO(); writer.save(out)
    raw = (out.getvalue() + '\nloop_\n_annotation_C_CRD.model_num\n_annotation_C_CRD.idx0\n_annotation_C_CRD.val0\n1 0 11\n2 0 99\n#\n').encode()
    selected = MMCIF2Dict(io.StringIO(sources.select_structure_model(raw, 'cif', 2).decode()))
    assert selected['_annotation_C_CRD.model_num'] == ['2']
    assert selected['_annotation_C_CRD.idx0'] == ['0']
    assert selected['_annotation_C_CRD.val0'] == ['99']


@pytest.mark.asyncio
async def test_standalone_detour_cold_sqlite_without_project(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'draft.db'))
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: UserTemplate.__table__.create(conn))
        await connection.run_sync(lambda conn: UserTemplateRevision.__table__.create(conn))
    sessions = async_sessionmaker(engine)
    raw = {'schema_version': 1, 'composition': {'sequence':'ACDE', 'source_sequence_id':'immutable',
        'complex_components':[{'type':'protein','sequence':'ACDE','copies':2}, {'type':'ligand','smiles':'O'}]},
        'round_plan': {'schema_version':2,'stages':[]},
        'md_draft': {'form': {'native_intent': {'stages':[], 'unknown-key':0}, 'salt':0, 'enabled':False, 'empty':[], 'nullable':None},
        'source_ref': {'kind':'candidate','model_number':2}, 'execution_target_id':None},
        'return_to':'/designs/parent?tab=md', 'destination_launch_context_id':None}
    async with sessions() as session:
        identity = await save_prediction_detour(MdPredictionDetour.model_validate(raw), session)
    await engine.dispose()
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'draft.db'))
    async with async_sessionmaker(engine)() as session:
        loaded = await load_prediction_detour(identity, session)
        assert loaded.model_dump(mode='json', exclude_unset=True) == raw
        # The retained revision, not mutable template display params, is authority.
        template = await session.get(UserTemplate, identity); template.params = {'changed':True}
        await session.commit()
        assert (await load_prediction_detour(identity, session)).composition == raw['composition']
    await engine.dispose()


@pytest.mark.asyncio
async def test_standalone_router_exact_target_readback(tmp_path):
    import os
    import importlib.util
    import httpx
    from fastapi import FastAPI
    from database import get_session
    overlay = os.environ.get('BMS_MD_PARENT_ROUTER')
    if overlay:
        spec = importlib.util.spec_from_file_location('routers.md_qualified_parent', overlay)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    else:
        from routers import molecular_dynamics as module
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'http-draft.db'))
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: UserTemplate.__table__.create(conn))
        await connection.run_sync(lambda conn: UserTemplateRevision.__table__.create(conn))
    sessions = async_sessionmaker(engine)
    async def session_dependency():
        async with sessions() as session:
            yield session
    app = FastAPI(); app.include_router(module.router); app.dependency_overrides[get_session] = session_dependency
    raw = {'schema_version':1, 'composition':{'sequence':'ACDE', 'complex_components':[]},
           'round_plan':{'schema_version':2,'stages':[]}, 'md_draft':{'form':{'zero':0,'false':False,'empty':[],'null':None}},
           'return_to':'/designs/parent'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline') as client:
        response = await client.post('/api/molecular-dynamics/prediction-detours', json=raw)
        assert response.status_code == 201, response.text
        identity = response.json()['id']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline') as client:
        response = await client.get('/api/molecular-dynamics/prediction-detours/' + identity)
        assert response.status_code == 200 and response.json() == raw
        assert (await client.post('/api/molecular-dynamics/prediction-detours', json={**raw,'return_to':'https://evil.test'})).status_code == 422
        assert (await client.get('/api/molecular-dynamics/prediction-detours/missing')).status_code == 404
    await engine.dispose()


@pytest.mark.parametrize('route', ['https://evil.test/designs/a','//evil.test/designs/a','/designs/a\\evil'])
def test_detour_existing_safe_local_return_policy(route):
    with pytest.raises(ValueError):
        MdPredictionDetour(composition={}, round_plan={}, md_draft={}, return_to=route)
