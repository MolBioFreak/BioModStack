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
