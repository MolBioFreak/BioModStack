import os
from pathlib import Path

import pytest
from bms_alignment import AlignmentRequest, run_engine
from bms_alignment.engines import compile_engine
from bms_alignment.native import compile_mafft


def request(engine, molecule='protein', **settings):
    return AlignmentRequest.model_validate({'roster': {'molecule': molecule, 'rows': [
        {'row_id': 'a', 'header': 'a', 'sequence': 'ACGTACGTACGT'},
        {'row_id': 'b', 'header': 'b', 'sequence': 'ACGTACGTACGA'},
        {'row_id': 'c', 'header': 'c', 'sequence': 'ACGTACGTACGG'}]},
        'settings': {'engine': engine, **settings}})


def test_schema_and_nondefault_compilers():
    schema = AlignmentRequest.model_json_schema()['$defs']
    assert {'op', 'ep'} <= schema['MafftSettings']['properties'].keys()
    assert {'sm', 'go', 'dgr'} <= schema['FamsaSettings']['properties'].keys()
    assert {'min_len', 'max_ambig', 'check'} <= schema['TwilightSettings']['properties'].keys()
    mafft = compile_mafft(request('mafft', strategy='einsi', op=1.2, ep=0.1), 'mafft', 'in')
    assert ['--genafpair', '--maxiterate', '1000', '--op', '1.2', '--ep', '0.1'] == mafft[4:-1]
    famsa = compile_engine(request('famsa', sm='MIQS', go=-14000, dgr=True), 'famsa', 'in', 'out')
    assert all(x in famsa for x in ('-sm', 'MIQS', '-go', '-14000', '-dgr'))
    twilight = compile_engine(request('twilight', 'dna', min_len=3, max_ambig=0.5, check=True), 'twilight', 'in', 'out', 'tree')
    assert all(x in twilight for x in ('--min-len', '3', '--max-ambig', '0.5', '--check'))


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
@pytest.mark.parametrize('engine,molecule,settings', [
    ('mafft', 'dna', {'strategy': 'einsi', 'op': 1.2, 'ep': 0.1}),
    ('famsa', 'protein', {'sm': 'MIQS', 'go': -14000, 'dgr': True}),
    ('muscle', 'protein', {'mode': 'super5', 'permutation': 'abc', 'perturb': 1}),
    ('twilight', 'dna', {'guide_source': 'mafft', 'method': 'tcs', 'min_len': 3, 'max_ambig': 0.5, 'check': True}),
])
def test_native_nondefaults(tmp_path, engine, molecule, settings):
    sif = os.environ['BMS_MSA_SIF']
    prefix = ['apptainer', 'exec', '--bind', f'{tmp_path}:{tmp_path}', sif]
    result = run_engine(request(engine, molecule, **settings), tmp_path, prefix + [engine], prefix + ['mafft'])
    assert result['returncode'] == 0, (result, (Path(result['attempt_dir']) / 'engine.log').read_text())
    assert result['artifacts']
