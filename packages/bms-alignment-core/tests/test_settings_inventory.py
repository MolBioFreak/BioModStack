import os
import json
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


@pytest.mark.parametrize('strategy', ['fft_ns_2', 'linsi', 'ginsi', 'einsi', 'auto'])
def test_mafft_explicit_overrides(strategy):
    args = compile_mafft(request('mafft', strategy=strategy, retree=1, maxiterate=3, reorder=True, quiet=True), 'mafft', 'in')
    assert args.count('--retree') == args.count('--maxiterate') == 1
    assert args[args.index('--retree') + 1] == '1'
    assert args[args.index('--maxiterate') + 1] == '3'
    assert '--reorder' in args and '--quiet' in args


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
@pytest.mark.parametrize('engine,molecule,settings', [
    ('mafft', 'dna', {'strategy': 'einsi', 'op': 1.2, 'ep': 0.1, 'retree': 1, 'maxiterate': 3, 'reorder': True, 'quiet': True}),
    ('famsa', 'protein', {'sm': 'MIQS', 'go': -14000, 'dgr': True, 'verbose': True}),
    ('muscle', 'protein', {'mode': 'super5', 'permutation': 'abc', 'perturb': 1}),
    ('twilight', 'dna', {'guide_source': 'mafft', 'method': 'tcs', 'min_len': 3, 'max_ambig': 0.5, 'check': True, 'verbose': True}),
])
def test_native_nondefaults(tmp_path, engine, molecule, settings):
    sif = os.environ['BMS_MSA_SIF']
    prefix = ['apptainer', 'exec', '--bind', f'{tmp_path}:{tmp_path}', sif]
    result = run_engine(request(engine, molecule, **settings), tmp_path, prefix + [engine], prefix + ['mafft'])
    assert result['returncode'] == 0, (result, (Path(result['attempt_dir']) / 'engine.log').read_text())
    assert result['artifacts']
    receipt = json.loads((Path(result['attempt_dir']) / 'receipt.json').read_text())
    expected = request(engine, molecule, **settings).settings.model_dump(mode='json')
    assert receipt['requested']['settings'] == receipt['effective_settings'] == expected
