import os
import pytest
from bms_alignment import AlignmentRequest, parse, compile_engine, run_engine
from bms_alignment.engines import map_tree


def req(engine, molecule='protein', **settings):
    seq = 'ACDEFGHIKLMNPQRSTVWY' if molecule == 'protein' else 'ACGTACGTACGTACGTACGT'
    if molecule == 'rna': seq = seq.replace('T', 'U')
    roster = parse(f'>dup\n{seq}\n>dup\n{seq}\n>three\n{seq[:-1]}A\n', 'fasta', molecule)
    return AlignmentRequest(roster=roster, settings={'engine': engine, **settings})


def test_native_argv():
    a = compile_engine(req('famsa', keep_duplicates=True, refine_mode='off'), 'famsa', 'in', 'out')
    assert '-keep_duplicates' in a and a[-2:] == ['in', 'out']
    a = compile_engine(req('muscle', 'dna', ensemble='stratified', replicates=2), 'muscle', 'in', 'out')
    assert '-nt' in a and '-stratified' in a and a[a.index('-replicates')+1] == '2'
    a = compile_engine(req('twilight', 'rna', method='tcs'), 'twilight', 'in', 'out', 'tree')
    assert a[a.index('--accurate')+1] == 'tcs' and a[a.index('--type')+1] == 'n'


def test_tree_mapping_observes_unmapped():
    result, report = map_tree("('a b':1,x:2,z:3);", {'a b': 'bmsrow00000001', 'x': 'bmsrow00000002'}, ['bmsrow00000001', 'bmsrow00000002', 'bmsrow00000003'])
    assert result == '(bmsrow00000001:1,bmsrow00000002:2,z:3);'
    assert report['unmapped'] == ['z'] and report['missing'] == ['bmsrow00000003']


@pytest.mark.parametrize('keep', [False, True])
def test_real_famsa(tmp_path, keep):
    binary = os.environ.get('BMS_TEST_FAMSA')
    if not binary: pytest.skip('BMS_TEST_FAMSA not configured')
    result = run_engine(req('famsa', keep_duplicates=keep), tmp_path, binary)
    assert result['returncode'] == 0
    assert len(result['observations']) == 1
    assert not result['observations'][0]['missing_ids']
    assert not result['observations'][0]['unexpected_ids']

@pytest.mark.parametrize('molecule', ['protein', 'dna', 'rna'])
@pytest.mark.parametrize('mode,ensemble', [('align','none'), ('super5','none'), ('align','stratified'), ('align','diversified')])
def test_real_muscle(tmp_path, molecule, mode, ensemble):
    binary = os.environ.get('BMS_TEST_MUSCLE')
    if not binary: pytest.skip('BMS_TEST_MUSCLE not configured')
    result = run_engine(req('muscle', molecule, mode=mode, ensemble=ensemble, replicates=2 if ensemble != 'none' else None), tmp_path, binary)
    assert result['returncode'] == 0, (tmp_path/'engine.log').read_text()
    assert len(result['members']) == (8 if ensemble == 'stratified' else 2 if ensemble == 'diversified' else 1)
    assert all(not o.get('missing_ids') and not o.get('unexpected_ids') and not o.get('parse_error') for o in result['observations'])
