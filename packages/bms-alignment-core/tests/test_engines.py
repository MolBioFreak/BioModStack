from pathlib import Path
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
    assert result['returncode'] == 0, (Path(result['attempt_dir'])/'engine.log').read_text()
    assert len(result['members']) == (8 if ensemble == 'stratified' else 2 if ensemble == 'diversified' else 1)
    assert all(not o.get('missing_ids') and not o.get('unexpected_ids') and not o.get('parse_error') for o in result['observations'])


def test_implicit_and_explicit_engine_discriminators():
    roster = req('mafft').roster
    assert AlignmentRequest(roster=roster, settings={}).settings.engine == 'mafft'
    assert AlignmentRequest(roster=roster, settings={'strategy': 'auto'}).settings.engine == 'mafft'
    for engine in ('mafft', 'famsa', 'muscle', 'twilight'):
        assert AlignmentRequest(roster=roster, settings={'engine': engine}).settings.engine == engine
    with pytest.raises(ValueError):
        AlignmentRequest(roster=roster, settings={'engine': 'unknown'})

def test_attempt_isolation(tmp_path):
    first = run_engine(req('famsa'), tmp_path, '/bin/false')
    old = Path(first['attempt_dir']) / 'alignment.fasta'
    old.write_text('>stale\nAAA\n')
    second = run_engine(req('famsa'), tmp_path, '/bin/false')
    assert first['attempt_dir'] != second['attempt_dir']
    assert not second['members'] and 'alignment.fasta' not in second['artifacts']


@pytest.mark.parametrize('molecule', ['dna', 'rna'])
@pytest.mark.parametrize('method,source', [('pairwise', 'mafft'), ('tcs', 'supplied')])
def test_real_twilight(tmp_path, molecule, method, source):
    binary = os.environ.get('BMS_TEST_TWILIGHT')
    mafft = os.environ.get('BMS_TEST_MAFFT')
    if not binary or not mafft: pytest.skip('native binaries not configured')
    request = req('twilight', molecule, method=method, guide_source=source)
    if source == 'supplied':
        rows = request.roster.rows
        for row, name in zip(rows, ["id,one", "id'two", 'id:three']): row.row_id = name
        request.settings.guide_tree = "(('a,b':0.1,'b''x':0.1):0.1,'c:d':0.1);"
        request.settings.leaf_mapping = dict(zip(['a,b', "b'x", 'c:d'], [r.row_id for r in rows]))
    result = run_engine(request, tmp_path, binary, mafft)
    attempt = Path(result['attempt_dir'])
    assert result['returncode'] == 0, (attempt/'engine.log').read_text() if (attempt/'engine.log').exists() else result
    assert not result['tree_mapping']['missing'] and not result['tree_mapping']['unmapped']
    assert len(result['members']) == 1
    assert not result['observations'][0]['missing_ids'] and not result['observations'][0]['unexpected_ids']


def test_mafft_attempt_isolation(tmp_path):
    from bms_alignment import run_mafft
    first = run_mafft(req('mafft', treeout=True), tmp_path, '/bin/false')
    (Path(first['attempt_dir']) / 'input.fasta.tree').write_text('(stale);')
    second = run_mafft(req('mafft', treeout=True), tmp_path, '/bin/false')
    assert first['attempt_dir'] != second['attempt_dir']
    assert 'input.fasta.tree' not in second['artifacts']

def test_failed_guide_does_not_reuse_prior_tree(tmp_path):
    request = req('twilight', 'dna', guide_source='mafft')
    result = run_engine(request, tmp_path, '/bin/true', '/bin/false')
    assert result['returncode'] != 0
    assert result['error'] == 'MAFFT guide tree unavailable'
