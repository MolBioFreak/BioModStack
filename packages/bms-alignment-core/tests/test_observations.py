import json
from pathlib import Path

from bms_alignment import AlignmentRequest, run_mafft
from bms_alignment.engines import map_tree
from bms_alignment.observations import observe_alignment


def request():
    return AlignmentRequest.model_validate({'roster': {'molecule': 'dna', 'rows': [
        {'row_id': 'a', 'header': 'a', 'sequence': 'ACGT'},
        {'row_id': 'b', 'header': 'b', 'sequence': 'AAAA'}]}, 'settings': {}})


def test_observations_are_nonblocking_and_do_not_log_sequences(tmp_path):
    binary = tmp_path / 'mafft'
    binary.write_text('#!/bin/sh\nprintf ">a\\nAC-T\\n>a\\nACGT\\n>x\\nAAAA\\n"\n')
    binary.chmod(0o755)
    result = run_mafft(request(), tmp_path / 'run', str(binary))
    assert result['returncode'] == 0
    observation = result['observations']
    assert observation['missing_ids'] == ['b']
    assert observation['unexpected_ids'] == ['x']
    assert observation['duplicate_ids'] == ['a']
    assert observation['changed_residue_ids'] == ['a']
    assert 'ACGT' not in json.dumps(observation)
    assert json.loads((Path(result['attempt_dir']) / 'receipt.json').read_text())['observations'] == observation
    (tmp_path / 'bad').write_text('not fasta')
    assert 'observation_error' in observe_alignment(tmp_path / 'bad', request().roster)


def test_tree_targets_are_structurally_quoted():
    targets = ['a,b', "c'd", 'e:f', 'g(h)', 'semi;colon']
    result, report = map_tree('(a:1,b:2,c:3,d:4,e:5);', dict(zip('abcde', targets)), targets)
    assert result == "('a,b':1,'c''d':2,'e:f':3,'g(h)':4,'semi;colon':5);"
    assert report['mapped'] == targets and not report['missing']


def test_reverse_complement_requires_policy_and_matching_residues(tmp_path):
    req = request()
    req.roster.rows[0].sequence = 'ACGTRYMKBDHVSWN'
    output = tmp_path / 'alignment.fasta'
    output.write_text('>_R_a\nNWSBDHVMKRYACGT\n>b\nAAAA-----------\n')
    unknown = observe_alignment(output, req.roster)
    assert unknown['missing_ids'] == ['a'] and unknown['unexpected_ids'] == ['_R_a']
    observed = observe_alignment(output, req.roster, allow_reverse_complement=True)
    assert not observed['missing_ids'] and not observed['unexpected_ids']
    assert observed['reverse_complemented_ids'] == ['a']
    assert not observed['changed_residue_ids']
    output.write_text('>_R_a\nAAAAAAAAAAAAAAA\n>b\nAAAA-----------\n')
    invalid = observe_alignment(output, req.roster, allow_reverse_complement=True)
    assert invalid['missing_ids'] == ['a'] and invalid['unexpected_ids'] == ['_R_a']
    assert invalid['reverse_complemented_ids'] == []


def test_integrity_is_separate_from_native_exit_success(tmp_path):
    from bms_alignment.observations import validate_alignment_output
    req = request()
    output = tmp_path / 'alignment.fasta'
    cases = [
        ('>a\nACGT\n>b\nAAAA\n', 'valid'),
        ('>a\nACGA\n>b\nAAAA\n', 'invalid'),
        ('>a\nACGT\n', 'invalid'),
        ('>a\nACGT\n>a\nACGT\n>b\nAAAA\n', 'invalid'),
        ('>a\nACGT\n>b\nAAA\n', 'invalid'),
        ('>a\nACGT\n>b\nAAAA\n>x\nAAAA\n', 'invalid'),
        ('', 'invalid'),
    ]
    for text, status in cases:
        output.write_text(text)
        assert validate_alignment_output(req, observe_alignment(output, req.roster))['status'] == status
    req.settings.sparsepickup = 1
    output.write_text('>a\nACGT\n')
    assert validate_alignment_output(req, observe_alignment(output, req.roster))['status'] == 'valid'
    output.write_text('>a\nACGA\n')
    assert validate_alignment_output(req, observe_alignment(output, req.roster))['status'] == 'invalid'


def test_twilight_tree_pruning_does_not_authorize_dropping_input_rows(tmp_path):
    from bms_alignment.observations import validate_alignment_output
    req = request()
    req = AlignmentRequest(roster=req.roster, settings={'engine': 'twilight', 'prune': True})
    output = tmp_path / 'alignment.fasta'
    output.write_text('>a\nACGT\n')
    result = validate_alignment_output(req, observe_alignment(output, req.roster))
    assert result['status'] == 'invalid'
    assert result['row_subsetting_requested'] is False


def test_explicit_famsa_column_trimming_allows_only_subsequence_deletion(tmp_path):
    from bms_alignment.observations import validate_alignment_output
    req = AlignmentRequest(roster={'molecule': 'protein', 'rows': [
        {'row_id': 'a', 'header': 'a', 'sequence': 'ACDEAAAAFGHIK'},
        {'row_id': 'b', 'header': 'b', 'sequence': 'ACDEFGHIK'}]}, settings={'engine': 'famsa'})
    output = tmp_path / 'alignment.fasta'
    output.write_text('>a\nACDEFGHIK\n>b\nACDEFGHIK\n')
    observations = observe_alignment(output, req.roster)
    assert validate_alignment_output(req, observations)['status'] == 'invalid'
    req.settings.trim_columns = 0.8
    accepted = validate_alignment_output(req, observations)
    assert accepted['status'] == 'valid' and accepted['trimmed_row_ids'] == ['a']
    assert observations['removed_residue_counts'] == {'a': 4}
    assert observations['row_residue_counts'] == [
        {'row_id': 'a', 'input_residues': 13, 'output_residues': 9, 'output_fraction_of_input': 9 / 13},
        {'row_id': 'b', 'input_residues': 9, 'output_residues': 9, 'output_fraction_of_input': 1.0},
    ]
    output.write_text('>a\nACDEYGHIK\n>b\nACDEFGHIK\n')
    assert validate_alignment_output(req, observe_alignment(output, req.roster))['status'] == 'invalid'
