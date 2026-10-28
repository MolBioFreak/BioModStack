"""Pinned-source full-family controls; no hidden algorithm repair."""
import json
import os
from pathlib import Path

import pytest
from bms_alignment import AlignmentRequest, run_engine
from bms_alignment.native import compile_mafft
from bms_alignment.engines import compile_engine


def request(engine='mafft', **settings):
    return AlignmentRequest.model_validate({'roster': {'molecule': 'protein', 'rows': [
        {'row_id': f'row_{i}', 'header': f'original {i}', 'sequence': seq}
        for i, seq in enumerate(['ACDEFGHIKLMNPQRSTVWY', 'ACDEYGHIKLMNPQRSTVWY',
                                'ACDEFGHIKLMNPQRSVWY', 'ACDEFGHIKLMNPQRTVWY'])]},
        'settings': {'engine': engine, **settings}})


MAFFT_CASES = [
    *[{'pair_method': x} for x in ['6merpair', '10merpair', 'hybridpair', 'localgenafpair', 'oldgenafpair', 'shortlongpair', 'longshortpair']],
    *[{'tree_method': x} for x in ['large', 'memsavetree', 'memsavetreex', 'stepadd', 'youngestlinkage', 'pileup', 'randomchain']],
    *[{'refinement_policy': x, 'strategy': 'linsi', 'maxiterate': 2, 'threads': 2} for x in ['bestfirst', 'adhoc0', 'adhoc1', 'adhoc2', 'simplehillclimbing']],
    {'alga': True}, {'strategy': 'ginsi', 'maxiterate': 2, 'allowshift': True, 'shiftpenalty': 2, 'unalignlevel': 0.1},
    {'anchors': '# synthetic anchor, exact bytes retained\n1 2 1 4 1 4 100\n', 'terminalmargin': 2, 'oneiteration': True},
    {'strategy': 'ginsi', 'maxiterate': 2, 'gep': 0.1, 'gexp': -0.1, 'gop': -1.5},
    {'kappa': 2}, {'leavegappyregion': True},
    {'strategy': 'ginsi', 'tree_method': 'memsavetree', 'maxiterate': 0, 'lhlimit': 100, 'focus': True, 'focus_rows': 'row_0\nrow_2'},
    {'strategy': 'linsi', 'maxiterate': 2, 'threadit': 0, 'minimumweight': 0.001, 'noscore': True, 'bunkatsunashi': True, 'skipiterate': 0.1},
    {'similaritylevel': 0.1}, {'sp': True}, {'sparsepickup': 2},
    {'nodeout': True}, {'distance_output': 'distout'}, {'distance_output': 'distoutclustalo'},
    {'scoreout': True, 'strategy': 'linsi', 'maxiterate': 2},
]


@pytest.mark.parametrize('settings', MAFFT_CASES)
def test_new_mafft_controls_roundtrip(settings):
    req = request(**settings)
    restored = AlignmentRequest.model_validate_json(req.model_dump_json())
    assert restored == req
    args = compile_mafft(restored, 'mafft', '/work/input.fasta')
    for key, value in settings.items():
        if key in {'strategy', 'threads', 'maxiterate', 'focus_rows'}:
            continue
        flag = '--' + (value if key in {'pair_method', 'tree_method', 'refinement_policy', 'distance_output'} else key)
        assert flag in args
        if key == 'anchors': assert args[args.index(flag)+1] == '/work/anchors.txt'
    assert req.roster.rows[0].header == 'original 0'


def test_muscle_new_controls_roundtrip_and_omission():
    settings = dict(guidetreeout=True, hmmout=True, hmmin='', anchor_letter='A', randseed=0,
                    accs=True, randomchaintree=True, native_log=True)
    req = request('muscle', **settings)
    restored = AlignmentRequest.model_validate_json(req.model_dump_json())
    assert restored == req
    args = compile_engine(restored, 'muscle', '/work/input.fasta', '/work/alignment.fasta')
    assert args[args.index('-randseed')+1] == '0'
    for flag in ['-guidetreeout', '-hmmout', '-hmmin', '-anchor_letter', '-accs', '-randomchaintree', '-log']:
        assert flag in args
        assert flag not in compile_engine(request('muscle'), 'muscle', 'in', 'out')


def native(req, tmp_path, alignment=True, expected_failure=False):
    prefix = ['apptainer', 'exec', '--cleanenv', '--bind', f'{tmp_path}:{tmp_path}', os.environ['BMS_MSA_SIF']]
    result = run_engine(req, tmp_path, prefix + [req.settings.engine])
    attempt = Path(result['attempt_dir'])
    if expected_failure:
        assert result['returncode'] != 0
        assert 'Segmentation fault' in (attempt/'engine.log').read_text()
    else:
        assert result['returncode'] == 0, (result, (attempt/'engine.log').read_text())
    assert result['requested'] == req.model_dump(mode='json')
    if alignment:
        assert (attempt/'alignment.fasta').stat().st_size > 0
        from bms_alignment.formats import parse
        parsed = parse((attempt/'alignment.fasta').read_text(), 'fasta', req.roster.molecule)
        mapping = json.loads((attempt/'row-map.json').read_text()) if (attempt/'row-map.json').exists() else {r.row_id: r.row_id for r in req.roster.rows}
        expected = {r.row_id: r.sequence.upper() for r in req.roster.rows}
        assert parsed.rows
        for row in parsed.rows:
            assert row.sequence.replace('-', '').upper() == expected[mapping[row.header.split()[0]]]
        if not getattr(req.settings, 'sparsepickup', None): assert len(parsed.rows) == len(req.roster.rows)
    assert set(result['artifacts']) == {p.name for p in attempt.iterdir() if p.is_file() and p.name != 'receipt.json'}
    return result, attempt


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
@pytest.mark.parametrize('settings', MAFFT_CASES)
def test_mafft_remaining_native(tmp_path, settings):
    req = request(**settings)
    if settings.get('pair_method') == '10merpair':
        req.roster.molecule = 'dna'
        for i, row in enumerate(req.roster.rows): row.sequence = 'ACGTACGTACGTACGTACGT' + 'ACGT'[i]
    failure = settings.get('pair_method') == 'hybridpair' or settings.get('scoreout', False)
    result, attempt = native(req, tmp_path, alignment=not failure, expected_failure=failure)
    if settings.get('focus'):
        mapping = json.loads((attempt/'row-map.json').read_text())
        assert mapping['_focus_bmsrow00000001'] == 'row_0'
        assert result['focus_mapping']['unmatched_row_ids'] == []
    if 'anchors' in settings: assert (attempt/'anchors.txt').read_bytes() == settings['anchors'].encode()
    if 'distance_output' in settings:
        suffix = 'hat2' if settings['distance_output'] == 'distout' else 'clodist'
        assert (attempt/f'input.fasta.{suffix}').stat().st_size > 0
    if settings.get('nodeout'): assert (attempt/'input.fasta.tree').stat().st_size > 0
    if settings.get('sparsepickup'): assert 'input.fasta.notused' in result['artifacts']


@pytest.mark.skipif(not os.environ.get('BMS_MSA_SIF'), reason='BMS_MSA_SIF not configured')
def test_muscle_actual_guide_log_and_hmm_roundtrip(tmp_path):
    _, attempt = native(request('muscle', guidetreeout=True, native_log=True, randseed=7, accs=True, anchor_letter='A'), tmp_path, alignment=False)
    assert (attempt/'alignment.fasta').stat().st_size == 0  # native tree writer exits zero
    assert (attempt/'guide.native.nwk').stat().st_size > 0
    assert (attempt/'muscle.native.log').stat().st_size > 0
    native(request('muscle', native_log=True, randseed=7, accs=True, anchor_letter='A'), tmp_path)
    _, super_attempt = native(request('muscle', mode='super5', hmmout=True, native_log=True), tmp_path)
    hmm = (super_attempt/'hmm.native.txt').read_text()
    assert hmm
    for mode in ['align', 'super5']:
        _, loaded = native(request('muscle', mode=mode, hmmin=hmm), tmp_path)
        assert (loaded/'hmmin.txt').read_bytes() == hmm.encode()
    result, chain = native(request('muscle', randomchaintree=True, randseed=7, guidetreeout=True), tmp_path)
    assert '-guidetreeout' in result['argv']  # do not silently strip an ineffective request
    assert not (chain/'guide.native.nwk').exists()  # pinned MPC native early return
