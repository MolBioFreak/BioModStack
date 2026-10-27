import os
from pathlib import Path
import pytest
from bms_alignment import AlignmentRequest, parse, export, coordinates, summary, compare, run_mafft
from bms_alignment.bundle import make_bundle, verify_bundle
from bms_alignment.tree import map_mafft_tree


def test_duplicate_headers_and_unknown_source():
    x = parse('>same\nA-C\n>same\nATC\n', 'fasta', 'dna')
    assert len({r.row_id for r in x.rows}) == 2
    assert compare(x, x)['left_excluded'] == 2
    assert coordinates(x.rows[0]) == [None, None, None]
    assert summary(x)[1]['occupancy'] == .5


def test_explicit_identity_and_offsets():
    a = parse('>x\nA-C\n', 'fasta', 'dna')
    b = parse('>renamed\nAC-\n', 'fasta', 'dna')
    for r in (a.rows[0], b.rows[0]):
        r.source_member_id = 'source'; r.occurrence_id = 'one'; r.source_offset = 20
        r.transformation_provenance = 'declared original'
    a.rows[0].source_positions = [20, None, 21]
    b.rows[0].source_positions = [20, 21, None]
    assert compare(a,b)['rows'][0]['positions'] == [dict(source=20,left_column=0,right_column=0), dict(source=21,left_column=2,right_column=1)]


def test_format_retention_and_loss_reporting():
    text = '# STOCKHOLM 1.0\n#=GF ID test\nx A-C\n//\n'
    s = parse(text, 'stockholm', 'protein')
    assert export(s, 'stockholm', original=True) == (text, [])
    assert export(s, 'fasta')[1]
    a = parse('>x\nACaaGT\n', 'a3m', 'dna')
    assert export(a, 'a3m', original=True)[0].endswith('ACaaGT\n')
    with pytest.raises(ValueError): export(a, 'fasta')


def test_bundle_integrity_and_traversal(tmp_path):
    source = tmp_path/'src'; source.mkdir(); (source/'x').write_text('abc')
    dest = tmp_path/'dst'; make_bundle(source,dest,['x'])
    assert verify_bundle(dest)['valid']
    (dest/'x').write_text('bad')
    assert not verify_bundle(dest)['valid']
    with pytest.raises(ValueError): make_bundle(source,dest,['../x'])


def test_mafft_numbered_tree_mapping():
    tree, report = map_mafft_tree('(2_row_B:0.1,(1_row_A:0.2,3_bad:0.3):0.4);', ['row_A','row_B','row_C'])
    assert "'row_A'" in tree and "'row_B'" in tree
    assert report['unmapped'] == ['3_bad'] and report['missing'] == ['row_C']


@pytest.mark.parametrize(('molecule', 'sequences'), [
    ('dna', ('ACGTACGTACGT', 'ACGTACGTTCGT', 'ACGTACGTACGA')),
    ('rna', ('ACGUACGUACGU', 'ACGUACGUUCGU', 'ACGUACGUACGA')),
    ('protein', ('ACDEFGHIKLMN', 'ACDEFGHIKLMQ', 'ACDEFGHIKLMS')),
])
def test_real_mafft(tmp_path, molecule, sequences):
    binary = os.environ.get('BMS_TEST_MAFFT')
    if not binary: pytest.skip('BMS_TEST_MAFFT not configured')
    roster = parse(f'>duplicate\n{sequences[0]}\n>duplicate\n{sequences[1]}\n>third\n{sequences[2]}\n', 'fasta', molecule)
    request = AlignmentRequest(roster=roster, settings={'engine':'mafft','treeout':True})
    result = run_mafft(request, tmp_path, binary)
    assert result['returncode'] == 0
    assert result['observations'] == {'missing_ids': [], 'unexpected_ids': []}
    raw = (Path(result['attempt_dir'])/'input.fasta.tree').read_text()
    _, report = map_mafft_tree(raw, [r.row_id for r in roster.rows])
    assert not report['missing'] and not report['unmapped'] and not report['duplicate_mapped']
