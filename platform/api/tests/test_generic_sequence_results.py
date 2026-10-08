"""Native-format fixtures through the real shared transactional result owner.

These small files are explicit scientific-output stubs, not inference or a claim
about scientific scores. The real native file index and SQLite importer execute.
"""
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from database import Design, Job
from scripts.sequence_design_results import (
    RESULT_PATH, SequenceDesignResultError, build_result_index, load_result_index,
)
from services.result_ingester import ingest_job_results
from test_core_protein_scientific_admission import admission


def native_fixture(tmp_path, engine, selected):
    output = tmp_path / 'results'; output.mkdir()
    unfiltered = output / 'pdb_files'; unfiltered.mkdir()
    subdir = 'fampnn_filtered' if engine == 'fampnn' else 'mpnn_filtered'
    filtered = output / 'collected' / subdir; filtered.mkdir(parents=True)
    source = tmp_path / 'source.pdb'
    pdb = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 87.00           C\nEND\n'
    source.write_text(pdb)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'sequence_design_engine': engine,
        'sequence_design_mode': 'design', 'mpnn_omitAAs': '', 'fampnn_repack_last': False,
        'fampnn_psce_threshold': 0.0, 'input_note': 'explicit inference output fixture'}))
    for i in range(2):
        name = f'source_seq_{i}'
        structure = unfiltered / f'{name}.pdb'; structure.write_text(pdb)
        metrics = unfiltered / f'{name}.json'
        payload = {'design': name, 'sequence': 'A', 'fixture': 'not scientific inference'}
        payload.update({'score': float(i)} if engine == 'proteinmpnn' else {'fampnn_avg_psce': float(i)})
        metrics.write_text(json.dumps(payload))
        if i in selected:
            shutil.copy2(structure, filtered / structure.name)
            shutil.copy2(metrics, filtered / metrics.name)
    index = build_result_index(engine=engine, mode='design', source=source,
        settings=settings, unfiltered_dir=unfiltered, filtered_dir=filtered)
    manifest = output / RESULT_PATH; manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(index))
    # These retained unrelated/raw/prepared files are deliberately ineligible.
    raw = output / 'run' / 'raw'; raw.mkdir(parents=True)
    (raw / 'wrong_raw_candidate.pdb').write_text(pdb)
    (raw / 'native.fasta').write_text('>explicit fixture\nA\n')
    return output, index


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['fampnn', 'proteinmpnn'])
@pytest.mark.parametrize('selected', [[], [0], [0, 1]])
async def test_shared_ingester_reads_native_membership_and_is_replay_safe(admission, tmp_path, engine, selected):
    output, index = native_fixture(tmp_path, engine, selected)
    job = Job(id='sequence-native', name='explicit native format fixture', model_id=engine,
              mode='design', status='completed', params={}, output_dir=str(output))
    admission.add(job); await admission.commit()
    job_id = job.id
    assert await ingest_job_results(job_id, output, admission) == len(selected)
    admission.expire_all()
    rows = list((await admission.execute(select(Design).where(Design.job_id == job_id))).scalars())
    assert {r.name for r in rows} == {f'source_seq_{i}' for i in selected}
    for row in rows:
        assert row.artifact_class == 'sequence_designed_complex'
        assert row.review_profile_id == 'sequence_design_v1'
        assert row.provenance['result_set'] == 'sequence_designs'
        assert row.provenance['stage_settings'] == index['settings']
        assert row.provenance['sequence_design_source'] == index['source']
        assert row.provenance['sequence_design_counts'] == {'unfiltered': 2, 'selected': len(selected)}
        assert row.plddt_overall is None and row.residue_plddt is None and row.pae_overall is None
        assert (row.fampnn_psce if engine == 'fampnn' else row.mpnn_score) == float(row.name.rsplit('_', 1)[1])
        assert row.confidence_metrics[engine] == json.loads(Path(row.json_path).read_text())
        assert Path(row.pdb_path).parent.parent.name == 'collected'
    before = {r.id: r.provenance for r in rows}
    assert await ingest_job_results(job_id, output, admission) == 0
    admission.expire_all()
    after = list((await admission.execute(select(Design).where(Design.job_id == job_id))).scalars())
    assert {r.id: r.provenance for r in after} == before
    assert len(list((output / 'pdb_files').glob('*.pdb'))) == 2
    assert (output / 'run/raw/native.fasta').read_text() == '>explicit fixture\nA\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['selected_byte', 'raw_byte', 'foreign_name', 'duplicate',
                                      'nonfinite_score', 'path_escape', 'symlink', 'missing_index'])
async def test_malformed_native_collection_cannot_partially_mutate_sqlite(admission, tmp_path, mutation):
    output, index = native_fixture(tmp_path, 'proteinmpnn', [0, 1])
    manifest = output / RESULT_PATH
    if mutation == 'selected_byte':
        (output / index['candidates'][1]['filtered_structure_path']).write_text('changed bytes')
    elif mutation == 'raw_byte':
        (output / index['candidates'][1]['metrics']['path']).write_text('{}')
    elif mutation == 'foreign_name':
        index['candidates'][1]['name'] = 'foreign'
    elif mutation == 'duplicate':
        index['candidates'][1] = index['candidates'][0]
    elif mutation == 'nonfinite_score':
        from scripts.sequence_design_results import digest
        candidate = index['candidates'][1]
        data = json.dumps({'design': candidate['name'], 'sequence': 'A', 'score': float('nan')}).encode()
        for relative in (candidate['metrics']['path'], candidate['filtered_metrics_path']):
            (output / relative).write_bytes(data)
        candidate['metrics'].update(digest(data))
    elif mutation == 'path_escape':
        index['candidates'][1]['structure']['path'] = '../source.pdb'
    elif mutation == 'symlink':
        file = output / index['candidates'][1]['structure']['path']
        original = file.read_bytes(); file.unlink()
        target = tmp_path / 'unowned.pdb'; target.write_bytes(original)
        file.symlink_to(target)
    manifest.write_text(json.dumps(index))
    if mutation == 'missing_index':
        manifest.unlink()
    job = Job(id='bad-native-sequence', name='explicit malformed fixture', model_id='proteinmpnn',
              mode='design', status='completed', params={})
    admission.add(job); await admission.commit()
    with pytest.raises(SequenceDesignResultError):
        await ingest_job_results(job.id, output, admission)
    assert list((await admission.execute(select(Design))).scalars()) == []
    assert await admission.get(Job, 'bad-native-sequence') is not None


@pytest.mark.asyncio
async def test_native_generation_replacement_is_not_silent_import_replay(admission, tmp_path):
    output, index = native_fixture(tmp_path, 'fampnn', [0])
    job = Job(id='generation-replay', name='explicit generation fixture', model_id='fampnn',
              mode='design', status='completed', params={})
    admission.add(job); await admission.commit()
    assert await ingest_job_results(job.id, output, admission) == 1
    original = (await admission.execute(select(Design))).scalars().one()
    original_id = original.id
    index['settings']['fampnn_repack_last'] = True
    (output / RESULT_PATH).write_text(json.dumps(index))
    with pytest.raises(SequenceDesignResultError, match='different native source'):
        await ingest_job_results(job.id, output, admission)
    admission.expire_all()
    row = (await admission.execute(select(Design))).scalars().one()
    assert row.id == original_id
    assert row.provenance['stage_settings']['fampnn_repack_last'] is False


def test_producer_rejects_partial_and_changed_filter_files(tmp_path):
    output, index = native_fixture(tmp_path, 'proteinmpnn', [0])
    target = output / index['candidates'][0]['filtered_metrics_path']
    target.write_text('{}')
    with pytest.raises(SequenceDesignResultError, match='changed candidate bytes'):
        build_result_index(engine='proteinmpnn', mode='design', source=tmp_path / 'source.pdb',
            settings=tmp_path / 'settings.json', unfiltered_dir=output / 'pdb_files',
            filtered_dir=target.parent)
