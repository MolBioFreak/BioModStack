"""Inert decoded-result fixtures, not native inference or GPU qualification."""
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from database import Design, Job, get_session
from routers import designs
from services.result_ingester import ingest_esmfold2_results
from tests.test_core_protein_candidates import job, setup

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('esm_confidence_runner', ROOT / 'scripts/run_esmfold2_inference.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

# Deliberately noncontiguous/differing author and label IDs, shared entity for
# separate chain instances, insertion code and an atom-tokenized ligand.
CIF = '''data_fixture
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA X 1 7 A 1 2 3 1 0.5 42 A 1
ATOM 2 C CA . ALA Y 1 1 ? 2 3 4 1 80 42 B 1
HETATM 3 C C1 . LIG Z 2 . ? 3 4 5 1 40 1 L 1
HETATM 4 O O1 . LIG Z 2 . ? 4 5 6 1 40 1 L 1
#
'''


# Exact pinned writer's 19-column layout; deliberately inert coordinates and
# confidence, not a captured prediction. Occupancy is the only omitted column.
NATIVE_CIF = '''data_fixture
#
loop_
_atom_site.group_PDB
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.B_iso_or_equiv
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.pdbx_PDB_model_num
_atom_site.id
ATOM C CA . ALA X 1 7 A 42 ALA A CA 0.5 1 2 3 1 1
ATOM C CA . ALA Y 1 1 ? 42 ALA B CA 80 2 3 4 1 2
HETATM C C1 . LIG Z 2 . ? 1 LIG L C1 40 3 4 5 1 3
HETATM O O1 . LIG Z 2 . ? 1 LIG L O1 40 4 5 6 1 4
#
'''


@pytest.mark.parametrize('suffix', ['.cif', '.mmcif'])
def test_native_cif_compatibility_preserves_geometry_identity_and_confidence(suffix):
    from services.core_protein_result_contract import _structure_confidence
    from services.md.starting_structures import _parse_mmcif_structure

    data = NATIVE_CIF.encode()
    original = bytes(data)
    atoms = list(_parse_mmcif_structure(data).get_atoms())
    assert [atom.occupancy for atom in atoms] == [1.0] * 4
    np.testing.assert_array_equal([atom.coord for atom in atoms],
                                  [[1, 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6]])
    assert [atom.get_parent().get_parent().id for atom in atoms] == ['A', 'B', 'L', 'L']
    assert [atom.name for atom in atoms] == ['CA', 'CA', 'C1', 'O1']
    assert atoms[0].get_parent().id == (' ', 42, 'A')
    assert [atom.bfactor for atom in atoms] == [.5, 80, 40, 40]
    assert _structure_confidence(data, 'native' + suffix) == (40.25, [.5, 80])
    assert data == original and b'_atom_site.occupancy' not in data


@pytest.mark.parametrize('damage', ['nonnumeric_coordinate', 'nonfinite_coordinate',
                                   'missing_coordinate', 'missing_atom_identity',
                                   'missing_chain_identity', 'missing_residue_identity',
                                   'invalid_present_occupancy'])
def test_native_occupancy_compatibility_does_not_repair_other_damage(damage):
    from services.core_protein_result_contract import CandidateIntegrityError, _structure_confidence

    content = NATIVE_CIF
    if damage in {'nonnumeric_coordinate', 'nonfinite_coordinate'}:
        value = 'broken' if damage == 'nonnumeric_coordinate' else 'nan'
        content = content.replace('0.5 1 2 3', f'0.5 {value} 2 3')
    elif damage == 'invalid_present_occupancy':
        content = content.replace('_atom_site.id\n', '_atom_site.id\n_atom_site.occupancy\n')
        content = '\n'.join(line + ' broken' if line.startswith(('ATOM ', 'HETATM ')) else line
                            for line in content.split('\n'))
    else:
        column = {'missing_coordinate': 'Cartn_x', 'missing_atom_identity': 'label_atom_id',
                  'missing_chain_identity': 'auth_asym_id',
                  'missing_residue_identity': 'label_comp_id'}[damage]
        lines = content.splitlines()
        headers = [line for line in lines if line.startswith('_atom_site.')]
        index = headers.index('_atom_site.' + column)
        content = '\n'.join(' '.join(line.split()[:index] + line.split()[index + 1:])
                            if line.startswith(('ATOM ', 'HETATM ')) else line
                            for line in lines if line != '_atom_site.' + column) + '\n'
    with pytest.raises(CandidateIntegrityError) as error:
        _structure_confidence(content.encode(), 'native.cif')
    assert error.value.reason['code'] == 'candidate_structure_invalid'


@pytest.fixture(autouse=True)
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'scientific_artifacts'))


def produce(tmp_path, monkeypatch, *, confidence=True, pae=True, cif=CIF):
    """Run the real CLI writer against an explicitly inert fold result."""
    import sys
    output = tmp_path / 'esmfold2_results'
    output.mkdir()
    matrix = np.arange(25, dtype=np.float32).reshape(5, 5) / 2
    samples = [SimpleNamespace(complex=SimpleNamespace(to_mmcif=lambda: cif),
        plddt=np.array([0, .2, .6, .8, 1], dtype=np.float32), ptm=.7, iptm=0,
        pae=matrix + index if pae else None,
        pair_chains_iptm=np.array([[.2, .4], [.8, .6]]),
        distogram=np.arange(50, dtype=np.float32).reshape(5, 5, 2),
        residue_index=np.array([0, 0, 0, 0, 0], dtype=np.int64),
        entity_id=np.array([1, 1, 2, 2, 2], dtype=np.int64)) for index in range(2)]
    observed = []
    class Builder:
        def fold(self, model, spi, **kwargs):
            observed.append((spi, kwargs))
            return samples
    class Model:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()
        def to(self, device): return self
        def eval(self): return self
    modules = {
        'torch': dict(cuda=SimpleNamespace(is_available=lambda: False, device_count=lambda: 0)),
        'esm.models.esmfold2': dict(ESMFold2InputBuilder=Builder,
            **{key: lambda **kw: SimpleNamespace(**kw) for key in ('ProteinInput', 'DNAInput', 'RNAInput', 'LigandInput', 'StructurePredictionInput')}),
        'esm.utils.msa': dict(MSA=object),
        'transformers.models.esmfold2.modeling_esmfold2': dict(ESMFold2Model=Model),
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    argv = ['--sequence', 'ACD', '--sequence-name', 'fixture', '--num-diffusion-samples', '2', '--device', 'cuda', '--output-dir', str(output)]
    # Device is inert; no CUDA library or inference is invoked.
    modules_torch = sys.modules['torch']
    modules_torch.cuda.get_device_name = lambda _: 'INERT FIXTURE'
    modules_torch.cuda.current_device = lambda: 0
    if confidence:
        receipt = output / 'effective_settings.json'
        receipt.write_text(json.dumps({'core_protein_scientific_contract': 1, 'argv': argv, 'sources': []}))
        monkeypatch.setenv('BMS_ESMFOLD2_EFFECTIVE_SETTINGS', str(receipt))
    else:
        monkeypatch.delenv('BMS_ESMFOLD2_EFFECTIVE_SETTINGS', raising=False)
    assert runner.main(argv) == 0
    assert len(observed) == 1
    assert observed[0][0].sequences[0].sequence == 'ACD'
    assert observed[0][1]['num_diffusion_samples'] == 2
    return output, samples


async def app_for(factory):
    app = FastAPI()
    app.include_router(designs.router, prefix='/api/designs')
    async def session():
        async with factory() as fresh:
            yield fresh
    app.dependency_overrides[get_session] = session
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['esmfold2', 'esmfold2_experimental'])
@pytest.mark.parametrize('cif', [CIF, NATIVE_CIF], ids=['occupancy_present', 'native_occupancy_absent'])
async def test_producer_ingest_fresh_api_sample_binding(tmp_path, monkeypatch, model_id, cif):
    root, samples = produce(tmp_path, monkeypatch, cif=cif)
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['sample_count'] == 2
    for entry, sample in zip(manifest['samples'], samples):
        with np.load(root / entry['native_confidence'], allow_pickle=False) as arrays:
            for field in ('plddt', 'pae', 'residue_index', 'entity_id', 'pair_chains_iptm', 'distogram'):
                np.testing.assert_array_equal(arrays[field], getattr(sample, field))
            assert arrays['sample_id'].item() == entry['sample_id']
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = model_id
            current.params = {'num_diffusion_samples': 2}
            session.add(current)
            await session.commit()
            assert await ingest_esmfold2_results('job', tmp_path, session, current) == 2
            ids = {row.name: row.id for row in (await session.scalars(select(Design))).all()}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=await app_for(factory)), base_url='http://test') as client:
            for index, (name, design_id) in enumerate(sorted(ids.items())):
                residue = (await client.get(f'/api/designs/{design_id}/residue-metrics')).json()
                assert residue['status'] == 'ok', residue
                assert residue['values'] == [.005, .8, .4]
                assert residue['axis']['confidence_scope'] == 'collapsed_residue'
                assert residue['axis']['stored_units'] == 'percent'
                assert 'c94ed8d' in residue['axis']['producer_version']
                positions = residue['axis']['residues']
                assert [r['chain_id'] for r in positions] == ['A', 'B', 'L']
                assert [r['label_asym_id'] for r in positions] == ['X', 'Y', 'Z']
                assert positions[0]['auth_seq_id'] == 42 and positions[0]['label_seq_id'] == 7
                assert positions[0]['insertion_code'] == 'A'
                assert positions[2]['label_seq_id'] is None
                response = await client.get(f'/api/designs/{design_id}/pae?max_size=50')
                assert response.status_code == 200, response.text
                pae = response.json()
                assert pae['status'] == 'ok', pae
                assert pae['native_shape'] == [5, 5]  # NOT three collapsed CIF residues
                assert pae['pae_matrix'] == samples[index].pae.tolist()
                assert pae['row_axis']['axis_kind'] == 'model_token'
                assert pae['row_axis']['orientation'] == 'native_output_order'
                assert pae['row_axis']['mapping_reason'] == 'native_token_to_structure_mapping_unavailable'
                assert 'residues' not in pae['row_axis']
                assert pae['row_axis']['tokens'][1] == {'index': 1, 'residue_index': 0, 'entity_id': 1}
                chain = (await client.get(f'/api/designs/{design_id}/chain-metrics')).json()
                assert chain['reason'] == 'missing_native_chain_axis_mapping'
                detail = (await client.get(f'/api/designs/{design_id}')).json()
                assert detail['scientific_structure_document'] == pae['document']
                assert detail['plddt_overall'] == pytest.approx(52)
                structure = await client.get(f'/api/designs/{design_id}/pdb')
                assert structure.status_code == 200
                assert structure.content == (root / f'{name}.cif').read_bytes()
            # Native point sampling, never block means; tested below HTTP's min=50.
            from services.core_protein_scientific_contract import compute_persisted_pae
            async with factory() as session:
                row = await session.get(Design, ids['fixture_000'])
                sampled, _, _ = await compute_persisted_pae(row, {'max_size': 2}, session)
                assert sampled['sampled_row_indices'] == [0, 4]
                assert sampled['pae_matrix'] == samples[0].pae[np.ix_([0, 4], [0, 4])].tolist()
            # Global analysis uses the same native projection, not legacy B factors.
            if model_id == 'esmfold2':
                from services import analysis_subprocess as worker
                from tests.test_core_protein_analysis_dispatch import cache
                from routers.analyses import trigger_design_analysis, get_design_analysis, AnalysisRunRequest
                cache(monkeypatch, tmp_path, factory)
                async with factory() as session:
                    queued = await trigger_design_analysis(ids['fixture_000'], 'pae_matrix', AnalysisRunRequest(), session)
                assert await worker._run_analysis(queued.run_id) == 0
                async with factory() as session:
                    cached = await get_design_analysis(ids['fixture_000'], 'pae_matrix', None, session)
                    wire = json.loads(cached.model_dump_json())['result']
                    assert wire['pae_matrix'] == samples[0].pae.tolist()
                    assert wire['row_axis']['axis_kind'] == 'model_token'
            # Whole-set custody still detects optional-sidecar substitution.
            path = root / 'fixture_001.confidence.npz'
            path.write_bytes((root / 'fixture_000.confidence.npz').read_bytes())
            corrupted = (await client.get(f'/api/designs/{ids["fixture_001"]}/pae')).json()
            assert corrupted['status'] == 'unavailable'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['old_no_sidecar', 'old_scalar_only', 'native_no_pae', 'malformed_pae', 'malformed_metadata', 'foreign_sample', 'invalid_archive'])
async def test_optional_confidence_never_becomes_prediction_success_gate(tmp_path, monkeypatch, case):
    root, _ = produce(tmp_path, monkeypatch, confidence=case != 'old_no_sidecar', pae=case != 'native_no_pae')
    if case == 'old_scalar_only':
        for path in root.glob('*.confidence.npz'):
            path.unlink()
        for path in [root / 'manifest.json', *root.glob('*.metrics.json')]:
            payload = json.loads(path.read_text())
            for record in [payload, *payload.get('samples', [])]:
                for key in ('native_confidence', 'confidence_dialect', 'confidence_states'):
                    record.pop(key, None)
            path.write_text(json.dumps(payload))
    if case == 'invalid_archive':
        (root / 'fixture_000.confidence.npz').write_bytes(b'not an archive')
    if case in ('malformed_pae', 'malformed_metadata', 'foreign_sample'):
        path = root / 'fixture_000.confidence.npz'
        with np.load(path, allow_pickle=False) as arrays:
            content = {key: arrays[key] for key in arrays.files}
        if case == 'malformed_pae': content['pae'] = np.array([[float('nan')]])
        if case == 'malformed_metadata': content['entity_id'] = np.array([1, 2])
        if case == 'foreign_sample': content['sample_id'] = np.asarray('fixture_001')
        np.savez_compressed(path, **content)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            assert await ingest_esmfold2_results('job', tmp_path, session, current) == 2
            from services.result_state_integrity import finalize_successful_job
            async def replay(jid, output, db, **kwargs):
                return await ingest_esmfold2_results(jid, Path(output), db, await db.get(Job, jid), commit=False)
            terminal = await finalize_successful_job(current, str(tmp_path), session, ingest_fn=replay)
            assert terminal.completed
            row = (await session.scalars(select(Design).where(Design.name == 'fixture_000'))).one()
            design_id = row.id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=await app_for(factory)), base_url='http://test') as client:
            structure = await client.get(f'/api/designs/{design_id}/pdb')
            assert structure.status_code == 200
            pae = (await client.get(f'/api/designs/{design_id}/pae')).json()
            if case == 'malformed_metadata':
                assert pae['status'] == 'ok'
                assert all(t['entity_id'] is None for t in pae['row_axis']['tokens'])
            else:
                assert pae['status'] == 'unavailable'
                assert pae['reason'] == {'old_no_sidecar': 'not_retained_by_producer', 'old_scalar_only': 'not_retained_by_producer', 'native_no_pae': 'native_pae_not_reported'}.get(case, 'missing_or_invalid_esmfold2_native_evidence')
            if case != 'old_no_sidecar':
                residue = (await client.get(f'/api/designs/{design_id}/residue-metrics')).json()
                assert residue['status'] == 'ok'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('confidence', [False, True])
async def test_worker_manifest_and_relocated_return_keep_native_files(tmp_path, monkeypatch, confidence):
    import hashlib
    import shutil
    import uuid
    from services.remote_execution.executor import _verify_result_package
    source = tmp_path / 'worker-results'
    source.mkdir()
    root, samples = produce(source, monkeypatch, confidence=confidence)
    worker_spec = importlib.util.spec_from_file_location('confidence_test_worker', ROOT / 'platform/api/tools/bms_remote_worker.py')
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    (attempt / 'execution-envelope.json').write_text('{}')
    attempt_id = str(uuid.uuid4())
    manifest = worker.build_result_manifest(attempt, dict(output_directory=str(source),
        attempt_id=attempt_id, job_id='job', source_revision='a' * 40, source_tree='b' * 40), 0)
    retained = {record['relative_path']: record for record in manifest['artifacts']}
    assert len([path for path in retained if path.endswith('.confidence.npz')]) == (2 if confidence else 0)
    incoming = tmp_path / 'returned'
    # Inert file transport only; real worker inventory and controller validation.
    shutil.copytree(source, incoming)
    shutil.rmtree(source)
    raw = json.dumps(manifest).encode()
    (incoming / 'result-manifest.json').write_bytes(raw)
    current = job(incoming)
    current.remote_attempt_id = attempt_id
    current.execution_source_revision = 'a' * 40
    current.execution_source_tree = 'b' * 40
    current.execution_bundle_sha256 = manifest['execution_envelope_sha256']
    status = SimpleNamespace(result_manifest_sha256=hashlib.sha256(raw).hexdigest(), generation=0)
    verified = _verify_result_package(incoming, current, status)
    assert {a.relative_path for a in verified.artifacts} == set(retained)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(current)
            await session.commit()
            assert await ingest_esmfold2_results('job', incoming, session, current) == 2
            row = (await session.scalars(select(Design).where(Design.name == 'fixture_001'))).one()
            design_id = row.id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=await app_for(factory)), base_url='http://test') as client:
            response = (await client.get(f'/api/designs/{design_id}/pae')).json()
            if confidence:
                assert response['pae_matrix'] == samples[1].pae.tolist()
                sidecar = incoming / 'esmfold2_results/fixture_001.confidence.npz'
                assert response['artifact_sha256'] == retained['esmfold2_results/fixture_001.confidence.npz']['sha256']
                sidecar.write_bytes(b'tampered')
                with pytest.raises(RuntimeError, match='hash mismatch'):
                    _verify_result_package(incoming, current, status)
            else:
                assert response['reason'] == 'not_retained_by_producer'
    finally:
        await engine.dispose()
