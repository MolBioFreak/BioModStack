"""Pinned native scientific inputs; synthetic sequences only."""
import json
import os
from pathlib import Path

import pytest
from bms_alignment import AlignmentRequest, run_engine
from bms_alignment.native import compile_mafft
from bms_alignment.engines import compile_engine


def request(engine='mafft', molecule='protein', **settings):
    return AlignmentRequest.model_validate({'roster': {'molecule': molecule, 'rows': [
        {'row_id': name, 'header': name, 'sequence': seq} for name, seq in
        [('a', 'ACGTACGTACGT'), ('b', 'ACGTACGTACGA'), ('c', 'ACGTACGTACGG')]]},
        'settings': {'engine': engine, **settings}})


MATRIX = ' '.join('ARNDCQEGHILKMFPSTWYV') + '\n' + ''.join(
    ' '.join('5' if i == j else '-1' for j in range(i + 1)) + '\n' for i in range(20))
TREE = '1 2 0.1 0.1\n1 3 0.2 0.2\n'


def test_explicit_inventory_dispositions_are_unique_and_not_blanket_complete():
    inventory = json.loads((Path(__file__).parents[1]/'option-dispositions.json').read_text())
    rows = inventory['options']
    assert len({(row['engine'], row['option']) for row in rows}) == len(rows)
    assert {row['engine'] for row in rows} == {'mafft', 'famsa', 'muscle', 'twilight'}
    assert all(row['source'] and row['disposition'] for row in rows)
    for engine, option in [('mafft', '--treein'), ('mafft', '--aamatrix'), ('famsa', '-gt'), ('twilight', '--matrix'), ('muscle', '-consiters')]:
        row = next(row for row in rows if (row['engine'], row['option']) == (engine, option))
        assert row['disposition'] == 'implemented' and row['field']
    assert any(row['disposition'].startswith('gap:') for row in rows)


def test_files_and_native_zero_false_null_contract():
    settings = dict(treein='', aamatrix=MATRIX, threadit=0, threadtb=0, mixedlinkage=0,
                    partsize=0, groupsize=0, opdist=0, exp=0, fft=False, anysymbol=False)
    req = request(**settings)
    restored = AlignmentRequest.model_validate_json(req.model_dump_json())
    assert restored.settings == req.settings
    args = compile_mafft(restored, 'mafft', '/attempt/input.fasta')
    for key in ('threadit', 'threadtb', 'mixedlinkage', 'partsize', 'groupsize', 'opdist', 'exp'):
        assert float(args[args.index('--' + key) + 1]) == 0
    assert args[args.index('--treein') + 1] == '/attempt/treein.txt'
    assert args[args.index('--aamatrix') + 1] == '/attempt/aamatrix.txt'
    assert '--fft' not in args and '--anysymbol' not in args
    default = compile_mafft(request(), 'mafft', 'input.fasta')
    assert '--treein' not in default and '--aamatrix' not in default
    famsa = compile_engine(request('famsa', guide_tree='import', guide_tree_newick=''), 'famsa', '/attempt/in', '/attempt/out')
    assert famsa[famsa.index('-gt') + 1:famsa.index('-gt') + 3] == ['import', '/attempt/guide.submitted.nwk']
    muscle = compile_engine(request('muscle', ensemble='stratified', permutation='none', perturb=0), 'muscle', 'in', 'out')
    assert muscle[muscle.index('-perturb') + 1] == '0'
    assert muscle[muscle.index('-perm') + 1] == 'none'
    omitted = compile_engine(request('muscle', ensemble='stratified'), 'muscle', 'in', 'out')
    assert '-perturb' not in omitted and '-perm' not in omitted


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
@pytest.mark.parametrize('engine,molecule,settings', [
    ('muscle', 'protein', {'consiters': 0, 'refineiters': 0, 'perturb': 0, 'permutation': 'none',
                            'output_order': 'input_order', 'm_is': 0.02, 'm_il': 0.01,
                            's_is': 0.02, 's_il': 0.01, 'is_is': 0.5, 'il_il': 0.9}),
    ('muscle', 'protein', {'mode': 'super5', 'output_order': 'tree_order', 'quiet': True, 'verbose': True}),
    ('twilight', 'dna', {'guide_source': 'mafft', 'matrix': 'A C G T\n18 -8 -4 -8\n-8 18 -8 -4\n-4 -8 18 -8\n-8 -4 -8 18\n'}),
    ('mafft', 'protein', {'treein': TREE, 'aamatrix': MATRIX}),
    ('mafft', 'protein', {'threadit': 0, 'threadtb': 1, 'fft': True, 'mixedlinkage': 0,
                          'unweight': True, 'termgappenalty': True, 'legacygappenalty': True,
                          'fmodel': True, 'opdist': 1.5, 'exp': 0}),
    ('mafft', 'protein', {'parttree': 'parttree', 'partsize': 50, 'groupsize': 50}),
    ('mafft', 'protein', {'parttree': 'dpparttree', 'partsize': 50, 'groupsize': 50}),
    ('mafft', 'protein', {'linkage': 'averagelinkage', 'memsave': 'memsave', 'anysymbol': True, 'preservecase': True}),
    ('mafft', 'dna', {'direction': 'adjustdirectionaccurately', 'nucleotide_scoring': 'nwildcard'}),
    ('famsa', 'protein', {'guide_tree': 'import', 'guide_tree_newick': '((left:0.1,right:0.1):0.2,third:0.3);\n',
                          'leaf_mapping': {'left': 'a', 'right': 'b', 'third': 'c'}}),
])
def test_native_files_experts(tmp_path, engine, molecule, settings):
    prefix = ['apptainer', 'exec', '--cleanenv', '--bind', f'{tmp_path}:{tmp_path}', os.environ['BMS_MSA_SIF']]
    req = request(engine, molecule, **settings)
    result = run_engine(req, tmp_path, prefix + [engine], prefix + ['mafft'])
    work = Path(result['attempt_dir'])
    assert result['returncode'] == 0, (result, (work/'engine.log').read_text())
    receipt = json.loads((work/'receipt.json').read_text())
    assert receipt['effective_settings'] == receipt['requested']['settings'] == req.settings.model_dump(mode='json')
    from bms_alignment.formats import parse
    output = parse((work/'alignment.fasta').read_text(), 'fasta', molecule)
    assert len(output.rows) == 3
    assert sorted(r.sequence.replace('-', '').upper() for r in output.rows) == sorted(r.sequence for r in req.roster.rows)
    for key in ('treein', 'aamatrix'):
        if key in settings:
            assert (work/(key + '.txt')).read_bytes() == settings[key].encode()
            assert key + '.txt' in receipt['artifacts']
    if engine == 'famsa':
        assert (work/'guide.original.nwk').read_text() == settings['guide_tree_newick']
        assert not receipt['tree_mapping']['missing']
        assert 'bmsrow00000001' in (work/'guide.submitted.nwk').read_text()
    if engine == 'twilight':
        assert (work/'matrix.txt').read_bytes() == settings['matrix'].encode()


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
def test_native_minsuper_failure_is_not_rewritten(tmp_path):
    req = request('muscle', minsuper=0)
    prefix = ['apptainer', 'exec', '--cleanenv', '--bind', f'{tmp_path}:{tmp_path}', os.environ['BMS_MSA_SIF'], 'muscle']
    result = run_engine(req, tmp_path, prefix)
    assert result['returncode'] == 1
    assert result['effective_settings']['minsuper'] == 0
    assert 'globalinputms.cpp(70) assert failed' in (Path(result['attempt_dir'])/'engine.log').read_text()
