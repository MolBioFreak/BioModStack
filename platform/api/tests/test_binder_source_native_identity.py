"""Minimal atom specimens; encoding/selection tests, not native inference."""
import gzip
import hashlib
import io
from pathlib import Path

import pytest
from Bio.PDB import MMCIFIO, PDBParser
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from schemas import CandidateRef
from services import binder_source_materialization as sources
# The shared schema owner installs this identical rebuild after CandidateRef.
sources.StructureSourceRequest.model_rebuild(_types_namespace={'CandidateRef': CandidateRef})
from test_binder_source_materialization import source_api, stores


def specimen():
    pdb = 'ATOM      1  CA  ALA A  42B      1.000   2.000   3.000  1.00 20.00           C  \nEND\n'
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('minimal', io.StringIO(pdb)))
    out = io.StringIO(); writer.save(out)
    cols = MMCIF2Dict(io.StringIO(out.getvalue()))
    components = ['ALA', 'SEP', 'GLY', 'MSE', 'HYP', 'ZZZ', 'LIG', 'DA', 'U']
    count = len(components)
    for key, values in list(cols.items()):
        if key.startswith('_atom_site.'):
            cols[key] = values * count * 2
    cols['_atom_site.id'] = [str(i + 101) for i in range(count * 2)]
    cols['_atom_site.pdbx_PDB_model_num'] = ['3'] * count + ['7'] * count
    cols['_atom_site.label_comp_id'] = components * 2
    cols['_atom_site.label_asym_id'] = (['A'] * 6 + ['L', 'D', 'R']) * 2
    cols['_atom_site.auth_asym_id'] = (['long_author'] * 6 + ['ligand', 'dna', 'rna']) * 2
    cols['_atom_site.auth_seq_id'] = [str(i + 42) for i in range(count)] * 2
    cols['_atom_site.label_seq_id'] = ['1', '2', '3', '4', '5', '6', '.', '1', '1'] * 2
    cols['_atom_site.label_entity_id'] = (['1'] * 6 + ['2', '3', '4']) * 2
    cols['_entity.id'] = ['1', '2', '3', '4']
    cols['_entity.type'] = ['polymer', 'non-polymer', 'polymer', 'polymer']
    cols['_entity_poly.entity_id'] = ['1', '3', '4']
    cols['_entity_poly.type'] = ['polypeptide(L)', 'polydeoxyribonucleotide', 'polyribonucleotide']
    cols['_chem_comp_bond.comp_id'] = ['SEP']
    cols['_chem_comp_bond.atom_id_1'] = ['OG']
    cols['_chem_comp_bond.atom_id_2'] = ['P']
    cols['_chem_comp_bond.value_order'] = ['SING']
    for name, value in [('src-component', 'A42-50'), ('src-coordinate-conditioned-region', 'A42-50'), ('src-index-group', '5')]:
        cols[f'_atom_site.annotation_{name}_1_atom'] = [value] * count * 2
    cols['_atom_site.is_unindexed_insertion'] = ['True'] * count * 2
    # AtomWorks serialization uses model-local zero-based indices, NOT the
    # globally unique atom_site.id. Saved targets differ from generated xyz.
    cols['_atom_site.annotation_CoordinateGroup_1_atom'] = ['2'] * count * 2
    for name in ('coords-with-insertion', 'coordinate', 'InternalCoordinate'):
        cat = f'_annotation_{name}_1_atom'
        cols[cat + '.idx0'] = [str(i) for i in range(count)] * 2
        cols[cat + '.model_num'] = ['3'] * count + ['7'] * count
        for axis in range(3):
            cols[cat + f'.val{axis}'] = [str(1000 + axis)] * count + [str(7000 + axis)] * count
    # Historical shared loops are broadcast across models, not document indices.
    cols['_annotation_shared_1_atom.idx0'] = ['0', '8']
    cols['_annotation_shared_1_atom.val0'] = ['91', '92']
    writer.set_dict(cols); out = io.StringIO(); writer.save(out)
    return out.getvalue().encode(), cols


@pytest.fixture
def materialize(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'to_allowed_relative', str)
    def run(raw, suffix, **kwargs):
        dest = tmp_path / str(len(list(tmp_path.iterdir()))); dest.mkdir()
        return sources.materialize_source_bytes(raw, suffix, dest, output_format='native', **kwargs)
    return run


def test_encoded_original_decoded_selected_and_saved_annotation_domains(materialize):
    import pyarrow as pa
    raw, original = specimen()
    encoded = [('.cif', raw), ('.cif.gz', gzip.compress(raw)), ('.cif.zst', pa.compress(raw, codec='zstd').to_pybytes())]
    results = [materialize(data, suffix, model_number=7) for suffix, data in encoded]
    for (_, stored), result in zip(encoded, results):
        assert Path(result['original_path']).read_bytes() == stored
        assert result['original_sha256'] == hashlib.sha256(stored).hexdigest()
        assert Path(result['decoded_path']).read_bytes() == raw
        selected = MMCIF2Dict(result['path'])
        assert selected['_atom_site.id'] == original['_atom_site.id'][9:]
        assert set(selected['_atom_site.pdbx_PDB_model_num']) == {'7'}
        for key, values in original.items():
            if key.startswith('_annotation_'):
                assert selected[key] == (values if key.startswith('_annotation_shared') else values[9:])
        assert selected['_annotation_coords-with-insertion_1_atom.val0'][0] == '7000'
        assert selected['_atom_site.Cartn_x'][0] != '7000'
        positions = result['source_positions']
        assert [r['component_id'] for r in positions] == ['ALA', 'SEP', 'GLY', 'MSE', 'HYP', 'ZZZ', 'LIG', 'DA', 'U']
        assert [r['canonical_amino_acid'] for r in positions[:6]] == ['A', 'S', 'G', 'M', 'P', None]
        assert positions[0]['auth_asym_id'] == 'long_author'
        assert positions[0]['pdbx_PDB_ins_code'] == 'B'
        assert positions[0]['atoms'][0]['model_atom_index'] == 0
        assert positions[0]['atoms'][0]['id'] == '110'
        assert positions[6]['polymer_membership'] is False
        assert positions[7]['polymer_type'] == 'polydeoxyribonucleotide'
        assert positions[8]['polymer_type'] == 'polyribonucleotide'
        assert result['declared_chemistry']['chem_comp_bond'][0]['value_order'] == 'SING'
    assert len({r['original_sha256'] for r in results}) == 3
    assert len({r['decoded_sha256'] for r in results}) == 1
    assert len({r['selected_sha256'] for r in results}) == 1


def test_bcif_keeps_original_and_all_categories_in_explicit_cif_derivative(materialize):
    from biotite.structure.io.pdbx import BinaryCIFFile, BinaryCIFBlock, BinaryCIFCategory
    _, columns = specimen()
    categories = {}
    for key, values in columns.items():
        if key == 'data_': continue
        category, field = key[1:].split('.')
        categories.setdefault(category, {})[field] = values
    binary = BinaryCIFFile({'minimal': BinaryCIFBlock({k: BinaryCIFCategory(v) for k, v in categories.items()})})
    out = io.BytesIO(); binary.write(out)
    raw = out.getvalue()
    whole = materialize(raw, '.bcif')
    assert Path(whole['path']).read_bytes() == raw and whole['format'] == 'bcif'
    selected = materialize(raw, '.bcif', model_number=7)
    assert Path(selected['original_path']).read_bytes() == raw
    assert selected['format'] == 'cif' and selected['native_format'] == 'bcif'
    assert MMCIF2Dict(selected['path'])['_annotation_coordinate_1_atom.val0'] == ['7000'] * 9


def test_candidate_model_exactness_and_single_parse(materialize, monkeypatch):
    request = sources.StructureSourceRequest(candidate={'kind': 'native_document', 'job_id': 'owner',
        'artifact_id': 'exact', 'target_state': 'state-b', 'model': 7})
    assert request.model_number == 7 and request.candidate.target_state == 'state-b'
    with pytest.raises(ValueError, match='model differs'):
        sources.StructureSourceRequest(candidate=request.candidate, model_number=3)
    with pytest.raises(ValueError, match='not both'):
        sources.StructureSourceRequest(candidate=request.candidate, job_id='other')
    import Bio.PDB.MMCIF2Dict as module
    original = module.MMCIF2Dict
    calls = []
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    raw, _ = specimen()
    monkeypatch.setattr(module, 'MMCIF2Dict', tracked)
    original_hash = sources.hashlib.sha256
    hashed = []
    def tracked_hash(data):
        hashed.append(data)
        return original_hash(data)
    monkeypatch.setattr(sources.hashlib, 'sha256', tracked_hash)
    result = materialize(raw, '.cif', model_number=7)
    assert result['inspection_error'] is None and len(calls) == 1
    assert len(hashed) == 2  # original/native/decoded share one domain, selection another.
    assert hashed[0] == raw
    hashed.clear()
    directory = Path(result['path']).parent / 'verified'; directory.mkdir()
    sources.materialize_source_bytes(raw, '.cif', directory, output_format='native',
                                    verified_original_sha256=original_hash(raw).hexdigest())
    assert hashed == []  # router's already checked digest reused, not recomputed.


def test_native_enrichment_failure_still_retains_bytes(materialize):
    raw = b'data_native\n_custom.native_field retained\n'
    result = materialize(raw, '.cif')
    assert result['inspection_error']
    assert Path(result['path']).read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize('producer', ['fampnn', 'rfd4_proteina'])
async def test_exact_candidate_api_retains_document_state_model_and_replay(source_api, producer):
    from sqlalchemy import select
    from database import Job, JobArtifact
    from services.binder_retained_publication import publish_retained_documents
    client, session, _, root = source_api
    output = root / 'results/sequence-native'
    documents = output / ('pdb_files' if producer == 'fampnn' else 'collected/rfd4_proteina/native')
    documents.mkdir(parents=True)
    raw, _ = specimen()
    (documents / 'alternate.cif').write_bytes(raw)
    (documents / 'generator_alternate.json').write_text('{"target_state":"state-b"}')
    job = Job(id='exact-producer', name='Minimal atom specimen', model_id=producer, mode='design',
              params={}, output_dir=str(output), status='completed')
    session.add(job); await session.flush()
    if producer == 'fampnn':
        await publish_retained_documents(session, job, output)
    else:
        # Exercise the ingester's registered artifact handoff, not an invented
        # native manifest or a claim this specimen came from native inference.
        path = documents / 'alternate.cif'
        session.add(JobArtifact(id='registered-native', owner_job_id=job.id, attempt=0,
            logical_path=path.relative_to(output).as_posix(), storage_path=str(path),
            sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), media_type='chemical/x-mmcif',
            provenance={'model_id': producer, 'native_document': {'target_state': 'state-b',
                'example_id': 'emitted-example', 'document': 'native/alternate.cif'}}))
    await session.commit()
    artifact = await session.scalar(select(JobArtifact).where(JobArtifact.logical_path.endswith('alternate.cif')))
    ref = {'kind': 'native_document', 'job_id': job.id, 'artifact_id': artifact.id,
           'target_state': 'state-b', 'model': 7}
    response = await client.post('/api/files/materialize-structure', json={'candidate': ref})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['source_identity']['candidate_ref'] == ref
    assert result['source_structure']['candidate'] == ref
    assert result['model_number'] == 7
    assert result['source_identity']['target_state'] == 'state-b'
    assert (await client.get('/api/files/download/' + result['original_path'])).content == raw
    again = await client.post('/api/files/materialize-structure', json=result['source_structure'])
    assert again.status_code == 200, again.text
    assert again.json()['selected_sha256'] == result['selected_sha256']
    wrong = await client.post('/api/files/materialize-structure', json={'candidate': {**ref, 'target_state': 'wrong'}})
    assert wrong.status_code == 422
    assert job.parent_job_id is None
