"""Shared compiler/readback acceptance; no model execution or live stores."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from database import Design
from services import nextflow
from test_protenix_chemistry_transport import compiler_environment, science
from test_protenix_external_import import source  # noqa: F401


@pytest.mark.asyncio
async def test_external_import_confidence_download_uses_import_custody(source, tmp_path, monkeypatch):
    from routers import external_imports
    from schemas import ExternalImportCreateRequest, ExternalImportPreviewRequest
    from services.external_imports.service import process_external_import
    from test_core_protein_candidates import setup
    from test_protenix_native_confidence import mixed_bytes
    from test_protenix_confidence_inventory import check_inventory
    import paths
    structure, full, summary = mixed_bytes()
    (source / 'nested/model_sample_7.cif').write_bytes(structure)
    (source / 'nested/full.json').write_bytes(full)
    (source / 'nested/summary.json').write_bytes(summary)
    monkeypatch.setattr(external_imports, 'resolve_allowed_path', lambda _: source)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {})
    factory, engine = await setup(tmp_path)
    try:
        preview = await external_imports.preview_external_import(ExternalImportPreviewRequest(
            source_path='inputs/native', provider_hint='protenix_native'))
        async with factory() as session:
            queued = await external_imports.create_external_import(ExternalImportCreateRequest(
                source_path='inputs/native', provider='protenix_native',
                preview_fingerprint=preview.source_fingerprint, dataset_name='inert native import'), session)
        async with factory() as session:
            imported = await process_external_import(session, import_id=queued.id, data_root=tmp_path / 'data')
            assert imported.state == 'completed'
        async with factory() as session:
            row = (await session.scalars(select(Design))).one()
            inventory = await check_inventory(session, row.id)
            assert all('/confidence-artifacts/' in a['download_url'] for a in inventory['artifacts'])
    finally:
        await engine.dispose()


@pytest.mark.parametrize('replacement', ['AAA', 'A' * len('YAQGTFTSDYSILLDKKAQAAFIEYLLEGG'), 'YAQGTFTSDYSILLDKKAQAAFIEYLLEGG'])
def test_batch_selected_single_roster_and_sequence_conditioning(tmp_path, compiler_environment, replacement):
    params = science()
    original = deepcopy(params)
    params.update(protenix_use_msa=False, sequence_batch_component_id='peptide',
                  sequence_batch_entries=[{'name': 'variant', 'sequence': replacement}])
    invocation = nextflow.compile_nextflow_invocation('protenix', 'complex', params, str(tmp_path / 'out'))
    inputs = {i.relative_path: json.loads(i.payload) for i in invocation.generated_inputs if i.relative_path.endswith('.json')}
    variant = next(value for key, value in inputs.items() if key.startswith('complex_batch_inputs/'))
    assert len(variant['components']) == len(original['complex_components'])
    assert variant['components'][1:] == original['complex_components'][1:]
    primary = variant['components'][0]
    assert primary['id'] == 'peptide' and primary['copy_ids'] == ['A', 'B'] and primary['count'] == 2
    assert primary['sequence'] == replacement
    removed = inputs['sequence_batch_manifest.json'][0]['invalidated_sequence_annotations']
    if replacement != original['complex_components'][0]['sequence']:
        assert all(k not in primary for k in ('modifications', 'unpairedMsa', 'pairedMsa', 'templatesPath'))
        assert removed['modifications'] == original['complex_components'][0]['modifications']
    else:
        assert primary == original['complex_components'][0]
        assert removed == {}
    assert variant['covalent_bonds'] == original['covalent_bonds']
    assert params['complex_components'] == original['complex_components']


def test_ensemble_preserves_task_chemistry_and_stages_file(tmp_path, compiler_environment, monkeypatch):
    import paths
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'ligand.sdf').write_bytes(b'inert ligand bytes\n')
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': inputs})
    params = science()
    params.update(pred_method='boltz_protenix', protenix_use_msa=False, boltz_use_msa=False)
    params['complex_components'][3] = {'type': 'ligand', 'id': 'L', 'ligand_file': 'inputs/ligand.sdf', 'count': 15}
    invocation = nextflow.compile_nextflow_invocation('boltz2', 'complex', params, str(tmp_path / 'out'))
    generated = {i.relative_path: i.payload for i in invocation.generated_inputs}
    assert generated['protenix_ligands/4.sdf'] == b'inert ligand bytes\n'
    payload = next(json.loads(value) for key, value in generated.items() if key.endswith('.json') and 'components' in json.loads(value))
    assert payload['covalent_bonds'] == params['covalent_bonds']
    assert payload['constraint'] == params['constraint']
    assert payload['components'][0] == params['complex_components'][0]
    assert payload['components'][3]['count'] == 15
    invocation.materialize_inputs(tmp_path / 'out')
    from scripts.prepare_protenix_msa import load_native_protenix_input
    native = load_native_protenix_input(invocation.native_parameters)[0]
    assert native['covalent_bonds'] == params['covalent_bonds']
    assert native['constraint'] == params['constraint']
    assert native['sequences'][3]['ligand']['ligand'] == 'FILE_' + str(tmp_path / 'out/protenix_ligands/4.sdf')
    # Native Boltz's distinct modification dialect is transported, not validated as Protenix.
    params['pred_method'] = 'boltz'
    params['complex_components'][0]['modifications'] = [{'position': 2, 'ccd': 'CSO'}]
    nextflow.compile_nextflow_invocation('boltz2', 'complex', params, str(tmp_path / 'boltz'))


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['esmfold2', 'esmfold2_experimental'])
async def test_esm_original_inventory_and_download_scratch_sqlite(tmp_path, monkeypatch, model_id):
    from test_esmfold2_native_confidence import produce, setup, job, app_for
    from services.result_ingester import ingest_esmfold2_results
    import paths
    root, _ = produce(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {})
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = model_id
            current.params = {'num_diffusion_samples': 2}
            session.add(current)
            await session.commit()
            assert await ingest_esmfold2_results('job', tmp_path, session, current) == 2
            ids = list((await session.scalars(select(Design.id))).all())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=await app_for(factory)), base_url='http://test') as client:
            for design_id in ids:
                inventory = (await client.get(f'/api/designs/{design_id}/confidence-artifacts')).json()
                assert inventory['status'] == 'ok', inventory
                assert {a['role'] for a in inventory['artifacts']} == {'structure', 'metrics', 'native_confidence'}
                for artifact in inventory['artifacts']:
                    response = await client.get(artifact['download_url'])
                    assert response.status_code == 200, response.text
                    assert hashlib.sha256(response.content).hexdigest() == artifact['artifact_sha256']
                    assert response.content == (root / artifact['filename']).read_bytes()
            assert (await client.get(f'/api/designs/{ids[0]}/confidence-artifacts/unknown/download')).status_code == 404
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_protenix_snapshot_download_outside_file_roots(tmp_path, monkeypatch):
    from test_protenix_native_confidence import make_publication, store
    from test_protenix_confidence_inventory import check_inventory, app_for
    import paths
    job, design, root = make_publication(tmp_path)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {})
    engine, factory = await store(tmp_path, job, design)
    try:
        async with factory() as session:
            inventory = await check_inventory(session, design.id)
            artifact = next(a for a in inventory['artifacts'] if a['role'] == 'pae')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(session)), base_url='http://test') as client:
                from services.protenix_scientific_consumer import verified_native_design
                selected = await verified_native_design(await session.get(Design, design.id), session)
                Path(selected['artifacts']['pae']['path']).write_bytes(b'changed')
                assert (await client.get(artifact['download_url'])).status_code == 404
                assert (await client.get(f'/api/designs/{design.id}/pdb')).status_code == 200
    finally:
        await engine.dispose()
