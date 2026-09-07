"""Real software wrapper/filter/SQLite chain. Only the model call is a fixture."""
import io
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from database import Design
from test_boltzgen_native_scalars import observed_source, setup, make_job, publication, run_strict_filter


@pytest.mark.asyncio
@pytest.mark.parametrize('unknown_source,preexisting', [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize('aliases', ['single', 'matching', 'conflicting'])
async def test_wrapper_native_csv_to_filter_sqlite_and_canonical_reader(tmp_path, monkeypatch, unknown_source, preexisting, aliases):
    from Bio.PDB import PDBParser, MMCIFIO
    import run_boltzgen_wrapper as wrapper
    from lib import boltzgen_native
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    identity, root = observed_source(tmp_path)
    observe = boltzgen_native.observe_source
    if unknown_source:
        (root / 'opt/venv/lib/python3.11/site-packages/boltzgen/model/layers/confidence_utils.py').write_text('# unsupported')
    monkeypatch.setattr(boltzgen_native, 'observe_source', lambda: observe(root / 'opt/venv/bin/boltzgen', root=root))
    calls = []
    def fixture_model(command):
        calls.append(command)
        argv = shlex.split(command)
        output = Path(argv[argv.index('--output') + 1])
        ranked = output / 'final_ranked_designs'; ranked.mkdir()
        structures = ranked / 'final_30_designs'; structures.mkdir()
        structure = PDBParser(QUIET=True).get_structure('fixture', io.StringIO(
            'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n'))
        writer = MMCIFIO(); writer.set_structure(structure); writer.save(str(structures / 'rank1_a.cif'))
        header = 'id' if aliases == 'single' else 'file_name,id'
        identity_row = 'a' if aliases == 'single' else ('a.cif,a' if aliases == 'matching' else 'a.cif,foreign')
        (ranked / 'all_designs_metrics.csv').write_text(header + ',final_rank,design_ptm,affinity_probability_binary1,filter_rmsd\n' + identity_row + ',1,0,0,0\n')
        return 0
    monkeypatch.setattr(wrapper.os, 'system', fixture_model)
    monkeypatch.setattr(wrapper, 'report_stage', lambda *a, **k: None)
    config = tmp_path / 'config.yaml'; config.write_text('entities: []\n')
    work = tmp_path / 'wrapper'
    if preexisting:
        stale = work / 'batch_0_config'; stale.mkdir(parents=True)
        (stale / 'old_output').write_text('previous attempt')
    monkeypatch.setattr(sys, 'argv', ['wrapper', '--config', str(config), '--out_dir', str(work), '--num_designs', '1', '--core-protein-scientific-contract', '1'])
    if aliases == 'conflicting':
        with pytest.raises(ValueError, match='CSV.*identity'):
            wrapper.main()
        assert not list(work.glob('designs/native_*.csv'))
        return
    wrapper.main()
    assert len(calls) == 1
    designs = work / 'designs'
    output = tmp_path / 'collected/boltzgen_filtered'; output.mkdir(parents=True)
    run_strict_filter(SimpleNamespace(pdbs=[str(designs / 'rank1_a.pdb')], jsons=[str(designs / 'confidence_rank1_a.json')],
        out_dir=str(output), filter_biased='false', metrics_override=None, additional_filters=None, size_buckets=None,
        boltzgen_min_plddt=None, boltzgen_min_conf_score=None, boltzgen_max_rmsd=None, budget=1, alpha=0))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            assert await publication.ingest(job, tmp_path, db) == 1
        async with factory() as db:
            row = await db.scalar(select(Design))
            result = await publication.verified_boltzgen_design(db, row)
            assert set(result['block']['metrics']) == {'design_ptm', 'affinity_probability', 'filter_rmsd'}
            for record in result['block']['metrics'].values():
                assert record['state'] == ('unavailable' if unknown_source or preexisting else 'ok')
                assert record['value'] == (None if unknown_source or preexisting else 0.)
            assert result['block']['metrics']['filter_rmsd']['scope'] == 'native_refolded_complex_backbone'
    finally:
        await engine.dispose()
