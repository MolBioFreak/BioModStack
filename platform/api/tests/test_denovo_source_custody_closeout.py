"""Exact source ownership fixtures, not inference or live lifecycle acceptance."""
import gzip
import hashlib
import io
from pathlib import Path

import pytest
from Bio.PDB import PDBParser
from fastapi import HTTPException

from database import Job, JobArtifact
from schemas import CandidateRef
from services import binder_source_materialization as sources
from test_binder_source_materialization import source_api, stores, cif


def atom(serial, name, res, chain, number, x=1, insertion=' ', alt=' '):
    return (f'HETATM{serial:5d} {name:4s}{alt}{res:3s} {chain}{number:4d}{insertion}   '
            f'{x:8.3f}{2.:8.3f}{3.:8.3f}{1.:6.2f}{20.:6.2f}          {name.strip()[0]:>2s}  \r\n')


def declaration(record, fields):
    line = list(' ' * 80)
    line[:6] = f'{record:<6}'
    for start, end, value in fields:
        line[start:end] = value
    return ''.join(line) + '\r\n'


def pdb_specimen():
    link = declaration('LINK', [(12,16,' C1 '),(17,20,'LIG'),(21,22,'A'),(22,26,'  42'),(26,27,'B'),
                                (42,46,' N1 '),(47,50,'ZZZ'),(51,52,'B'),(52,56,'  71')])
    ssbond = declaration('SSBOND', [(7,10,'  1'),(11,14,'CYS'),(15,16,'C'),(17,21,'  10'),
                                  (25,28,'CYS'),(29,30,'D'),(31,35,'  20')])
    header = 'HEADER    SUPPLIED NONSTANDARD BOND FIXTURE\r\nREMARK 350 ASSEMBLY DECLARATION\r\n'
    selected = (atom(41,' C1 ','LIG','A',42,7,insertion='B',alt='A') +
                atom(42,' N1 ','ZZZ','B',71,8) + atom(51,' SG ','CYS','C',10) + atom(52,' SG ','CYS','D',20))
    raw = (header + 'NUMMDL       2\r\n' + link + ssbond +
           'MODEL        3\r\n' + atom(41,' C1 ','LIG','A',42,3,insertion='B') +
           'ENDMDL\r\nMODEL        7\r\n' + selected +
           'ENDMDL\r\nCONECT   41   42   42\r\nCONECT   42   41   41\r\n'
           'CONECT   51   52\r\nCONECT   99   41\r\nEND\r\n').encode()
    return raw, selected.encode(), link.encode(), ssbond.encode(), header.encode()


@pytest.mark.parametrize('output_format', ['native', 'pdb'])
def test_selected_pdb_keeps_supplied_nonstandard_bonds_and_headers(tmp_path, monkeypatch, output_format):
    monkeypatch.setattr(sources, 'to_allowed_relative', str)
    raw, atoms, link, ssbond, header = pdb_specimen()
    result = sources.materialize_source_bytes(raw, '.pdb', tmp_path, output_format=output_format, model_number=7)
    selected = Path(result['path']).read_bytes()
    assert header in selected
    assert link in selected and ssbond in selected
    assert atoms in selected
    assert b'CONECT   41   42   42\r\n' in selected  # multiplicity is not deduplicated
    assert b'CONECT   99' not in selected and b'CONECT   41   99' not in selected
    assert b'MODEL        3' not in selected and b'MODEL        7' in selected
    assert b'NUMMDL       1' in selected
    assert Path(result['original_path']).read_bytes() == raw
    parsed = PDBParser(QUIET=True).get_structure('selected', io.StringIO(selected.decode()))
    assert [m.serial_num for m in parsed] == [7]
    assert [a.serial_number for a in parsed.get_atoms()] == [41,42,51,52]
    assert result['source_positions'][0]['pdbx_PDB_ins_code'] == 'B'
    assert result['sha256'] == hashlib.sha256(selected).hexdigest()


def test_pdb_projection_removes_only_absent_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'to_allowed_relative', str)
    raw, _, link, ssbond, _ = pdb_specimen()
    # Remove one atom from the selected MODEL, not from declarations.
    raw = raw.replace(atom(52,' SG ','CYS','D',20).encode(), b'')
    raw = raw.replace(b'CONECT   41   42   42\r\n', b'CONECT   41   42   42   99\r\n')
    selected = Path(sources.materialize_source_bytes(raw, '.pdb', tmp_path,
        output_format='pdb', model_number=7)['path']).read_bytes()
    assert link in selected and ssbond not in selected
    assert b'CONECT   41   42   42\r\n' in selected and b'CONECT   51' not in selected


async def registered_source(session, root, *, encoded=True):
    raw = cif(chain='a', models=(3,7)).encode()
    stored = gzip.compress(raw) if encoded else raw
    output = root / 'results/producer'
    path = output / ('collected/rfd4_proteina/native/source.cif.gz' if encoded else 'collected/rfd4_proteina/native/source.cif')
    path.parent.mkdir(parents=True); path.write_bytes(stored)
    owner = Job(id='producer', name='custody fixture', model_id='rfd4_proteina', mode='generate',
                params={}, output_dir=str(output), status='completed')
    session.add(owner); await session.flush()
    artifact = JobArtifact(id='native-source', owner_job_id=owner.id, attempt=0,
        logical_path=path.relative_to(output).as_posix(), storage_path=str(path),
        sha256=hashlib.sha256(stored).hexdigest(), bytes=len(stored), media_type='chemical/x-mmcif',
        provenance={})  # annotations are optional, not an admission condition
    session.add(artifact); await session.commit()
    request = sources.StructureSourceRequest(candidate=CandidateRef(kind='native_document',
        job_id=owner.id, artifact_id=artifact.id, model=7), expected_sha256=artifact.sha256)
    return request, path, stored, raw, owner, artifact


@pytest.mark.asyncio
async def test_retained_source_replay_after_producer_removal(source_api):
    client, session, _, root = source_api
    request, path, stored, _, owner, artifact = await registered_source(session, root)
    response = await client.post('/api/files/materialize-structure', json=request.model_dump(mode='json', exclude_none=True))
    assert response.status_code == 200, response.text
    retained = response.json()
    await session.delete(artifact); await session.delete(owner); await session.commit()
    path.unlink()
    replay = await client.post('/api/files/materialize-structure', json=retained['source_structure'])
    assert replay.status_code == 200, replay.text
    assert replay.json()['source_identity'] == retained['source_identity']
    assert replay.json()['original_sha256'] == hashlib.sha256(stored).hexdigest()
    # No generic fallback from an unretained selector or a substituted identity.
    bad = {**retained['source_structure'], 'candidate': {**retained['source_structure']['candidate'], 'artifact_id':'other'}}
    assert (await client.post('/api/files/materialize-structure', json=bad)).status_code in (404,422)
    from paths import resolve_allowed_path
    resolve_allowed_path(retained['original_path']).write_bytes(b'changed')
    assert (await client.post('/api/files/materialize-structure', json=retained['source_structure'])).status_code == 422


@pytest.mark.asyncio
async def test_resequence_shared_acquisition_retains_byte_domains_and_identity(source_api):
    _, session, _, root = source_api
    request, _, stored, raw, _, _ = await registered_source(session, root)
    selection = root / 'inputs/design_selections/protein_local_redesign/prepared'; selection.mkdir(parents=True)
    roster = await sources.prepare_resequence_sources([request], session, selection, ())
    from paths import resolve_allowed_path
    assert len(roster) == 1
    result = roster[0]
    assert resolve_allowed_path(result['original_path']).read_bytes() == stored
    assert resolve_allowed_path(result['native_path']).read_bytes() == raw
    consumed = resolve_allowed_path(result['path']).read_bytes()
    assert b'   7.000' in consumed and b'   3.000   2.000' not in consumed
    assert result['sha256'] == hashlib.sha256(consumed).hexdigest()
    assert result['source_identity']['candidate_ref'] == request.candidate.model_dump(exclude_none=True)
    assert result['source_structure']['expected_sha256'] == hashlib.sha256(stored).hexdigest()
    assert result['source_identity']['owner_job_id'] == 'producer'
    assert result['model_number'] == 7
    assert len(list(selection.glob('*.pdb'))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['registry', 'request', 'governed'])
async def test_resequence_obeys_existing_digest_and_path_contract(source_api, changed):
    _, session, _, root = source_api
    request, path, _, _, _, _ = await registered_source(session, root)
    selection = root / 'inputs/design_selections/protein_local_redesign/prepared'; selection.mkdir(parents=True)
    if changed == 'registry':
        path.write_bytes(path.read_bytes() + b'changed')
        request.expected_sha256 = None
    elif changed == 'request':
        request.expected_sha256 = '0' * 64
    with pytest.raises(HTTPException) as exc:
        await sources.prepare_resequence_sources([request], session, selection,
            (root / 'results',) if changed == 'governed' else ())
    assert exc.value.status_code == (403 if changed == 'governed' else 422)
    assert not list(selection.rglob('*.pdb'))


@pytest.mark.asyncio
async def test_preparation_cleanup_is_capability_bound_idempotent_and_keeps_job_owned_sources(source_api):
    _, session, _, root = source_api
    parent = root / 'inputs/design_selections/protein_local_redesign'; parent.mkdir(parents=True)
    first, other = parent / 'first', parent / 'other'
    first.mkdir(); other.mkdir()
    receipt = sources.begin_resequence_preparation(first)
    other_receipt = sources.begin_resequence_preparation(other)
    sibling = first.with_name(first.name + '_designer.json'); sibling.write_text('{}')
    (first / '.sources').mkdir(); (first / '.sources/original.cif').write_text('original')
    with pytest.raises(ValueError, match='receipt'):
        await sources.cleanup_resequence_preparation({**receipt, 'token':other_receipt['token']}, session)
    assert first.exists() and other.exists()
    job = Job(id='using-source', name='reuse', model_id='protein_modification_experimental', mode='region_redesign',
              params={'backbone_input_pdbs': str(first)}, status='pending')
    session.add(job); await session.commit()
    assert not await sources.cleanup_resequence_preparation(receipt, session)
    assert sibling.exists() and first.exists()
    await session.delete(job); await session.commit()
    assert await sources.cleanup_resequence_preparation(receipt, session)
    assert not first.exists() and not sibling.exists() and other.exists()
    assert not await sources.cleanup_resequence_preparation(receipt, session)
