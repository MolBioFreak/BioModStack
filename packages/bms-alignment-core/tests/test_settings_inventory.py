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


def test_bundled_editor_schema_matches_request_owner():
    root = Path(__file__).resolve().parents[3]
    bundled = root / 'platform/frontend/src/components/alignments/alignment-request.schema.json'
    assert json.loads(bundled.read_text()) == AlignmentRequest.model_json_schema()


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
    ('twilight', 'dna', {'guide_source': 'mafft', 'method': 'tcs', 'min_len': 3, 'max_ambig': 0.5, 'check': True, 'verbose': True, 'write_filtered': True, 'write_prune': True}),
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


# Every scalar native control is exercised with an explicit value, including zero
# and false; null means omission, not substitution with a truthy default.
@pytest.mark.parametrize('engine,molecule,values,flags', [
    ('mafft', 'dna', {'op': 0, 'ep': 0, 'retree': 0, 'maxiterate': 0},
     {'op': '--op', 'ep': '--ep', 'retree': '--retree', 'maxiterate': '--maxiterate'}),
    ('famsa', 'protein', {k: 0 for k in ('medoid_seeds', 'medoid_threshold', 'trim_columns', 'r', 'go', 'ge', 'tgo', 'tge', 'gsd', 'gsl')},
     {k: '-' + k for k in ('medoid_seeds', 'medoid_threshold', 'trim_columns', 'r', 'go', 'ge', 'tgo', 'tge', 'gsd', 'gsl')}),
    ('twilight', 'dna', {k: 0 for k in ('acc_mode', 'acc_max_group', 'acc_sample_rate', 'acc_sample_top', 'consistency_weight', 'max_subtree', 'remove_gappy', 'match', 'mismatch', 'transition', 'gap_open', 'gap_extend', 'gap_ends', 'scale', 'xdrop', 'length_deviation', 'max_ambig', 'max_len', 'min_len')},
     {k: '--' + k.replace('_', '-') for k in ('acc_mode', 'acc_max_group', 'acc_sample_rate', 'acc_sample_top', 'consistency_weight', 'max_subtree', 'remove_gappy', 'match', 'mismatch', 'transition', 'gap_open', 'gap_extend', 'gap_ends', 'scale', 'xdrop', 'length_deviation', 'max_ambig', 'max_len', 'min_len')}),
])
def test_zero_values_survive_schema_compiler_and_json_roundtrip(engine, molecule, values, flags):
    original = request(engine, molecule, **values)
    restored = AlignmentRequest.model_validate_json(original.model_dump_json())
    assert restored.settings.model_dump() == original.settings.model_dump()
    args = compile_mafft(restored, 'mafft', 'in') if engine == 'mafft' else compile_engine(restored, engine, 'in', 'out', 'tree')
    for name, flag in flags.items():
        assert args.count(flag) == 1, name
        assert float(args[args.index(flag) + 1]) == 0, name


@pytest.mark.parametrize('engine,molecule,flags', [
    ('mafft', 'dna', {'treeout': '--treeout', 'reorder': '--reorder', 'quiet': '--quiet'}),
    ('famsa', 'protein', {k: '-' + k for k in ('keep_duplicates', 'medoidtree', 'dgr', 'dgo', 'dsp')} | {'verbose': '-v'}),
    ('twilight', 'dna', {k: '--' + k.replace('_', '-') for k in ('wildcard', 'rooted', 'prune', 'filter', 'write_filtered', 'write_prune', 'check', 'verbose')}),
])
def test_boolean_values_and_roundtrip(engine, molecule, flags):
    for value in (False, True):
        original = request(engine, molecule, **{key: value for key in flags})
        restored = AlignmentRequest.model_validate_json(original.model_dump_json())
        assert restored.settings.model_dump() == original.settings.model_dump()
        args = compile_mafft(restored, engine, 'in') if engine == 'mafft' else compile_engine(restored, engine, 'in', 'out', 'tree')
        for flag in flags.values():
            assert (flag in args) is value


def test_mafft_expert_options_round_trip_and_compilation():
    values = {'weighti': 2.5, 'lop': -2.0, 'lep': 0.1, 'lexp': -0.1,
              'long_gap_open': -6.0, 'long_gap_extend': 0.0, 'bl': 45,
              'jtt': 100, 'tm': 100, 'kimura': 2, 'randomseed': 7, 'nofft': True}
    req = AlignmentRequest.model_validate_json(request('mafft', strategy='einsi', **values).model_dump_json())
    assert all(getattr(req.settings, k) == v for k, v in values.items())
    args = compile_mafft(req, 'mafft', 'in')
    for key, value in values.items():
        flag = {'long_gap_open': '--LOP', 'long_gap_extend': '--LEXP'}.get(key, '--' + key)
        assert args.count(flag) == 1
        if key != 'nofft': assert float(args[args.index(flag) + 1]) == value
    omitted = compile_mafft(request('mafft'), 'mafft', 'in')
    assert not {arg for arg in args[args.index('--weighti'):-1] if arg.startswith('--')}.intersection(omitted)


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
@pytest.mark.parametrize('molecule,settings', [
    ('protein', {'strategy': 'einsi', 'maxiterate': 2, 'weighti': 2.5, 'lop': -2.0, 'lep': 0.1, 'lexp': -0.1, 'long_gap_open': -6.0, 'long_gap_extend': 0.0, 'bl': 45, 'nofft': True, 'randomseed': 7}),
    ('dna', {'strategy': 'linsi', 'maxiterate': 2, 'kimura': 2, 'weighti': 2.5}),
    ('protein', {'jtt': 100}),
    ('protein', {'tm': 100}),
])
def test_mafft_expert_native_receipt(tmp_path, molecule, settings):
    prefix = ['apptainer', 'exec', '--cleanenv', '--bind', f'{tmp_path}:{tmp_path}', os.environ['BMS_MSA_SIF'], 'mafft']
    req = request('mafft', molecule, **settings)
    result = run_engine(req, tmp_path, prefix)
    assert result['returncode'] == 0, (result, (Path(result['attempt_dir']) / 'engine.log').read_text())
    receipt = json.loads((Path(result['attempt_dir']) / 'receipt.json').read_text())
    assert receipt['requested']['settings'] == receipt['effective_settings'] == req.settings.model_dump(mode='json')
    assert receipt['observations'].get('output_headers')


def test_nullable_native_controls_omit_flags():
    for engine, molecule, flags in [
        ('mafft', 'dna', ['--op', '--ep']),
        ('famsa', 'protein', ['-r', '-sm', '-go', '-ge', '-tgo', '-tge', '-dist', '-gsd', '-gsl']),
        ('twilight', 'dna', ['--acc-max-group', '--acc-sample-rate', '--acc-sample-top', '--gap-open', '--gap-extend', '--gap-ends', '--scale', '--min-len', '--max-len']),
    ]:
        req = AlignmentRequest.model_validate_json(request(engine, molecule).model_dump_json())
        args = compile_mafft(req, engine, 'in') if engine == 'mafft' else compile_engine(req, engine, 'in', 'out', 'tree')
        assert not set(flags).intersection(args)
