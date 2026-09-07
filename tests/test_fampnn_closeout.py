"""Regression counterexamples exercised through the real analyzer CLI."""
import json
import pickle
import subprocess
import sys

import pytest
from test_fampnn_strict_contract import inputs, SCRIPT


def cli(tmp_path, policy):
    policy_path = tmp_path/'policy.json'
    policy_path.write_text(json.dumps(policy))
    return subprocess.run([sys.executable, str(SCRIPT), '--core-protein-scientific-contract', '1',
        '--analysis-policy', str(policy_path), '--source-pdb-dir', str(tmp_path),
        '--sample-pkl-dir', str(tmp_path/'pkls'), '--candidate-pdb-dir', str(tmp_path/'samples'),
        '--out-jsonl', str(tmp_path/'out.jsonl')], text=True, capture_output=True)


@pytest.mark.parametrize('required', [False, True])
def test_declared_missing_pkl_is_not_silently_omitted(tmp_path, required):
    payload, policy, path, source = inputs(tmp_path)
    policy['require_full_coverage'] = required
    # Entire optional PKL directory may be absent, not only one sample file.
    result = cli(tmp_path, policy)
    if required:
        assert result.returncode != 0
        assert 'coverage' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        row = json.loads((tmp_path/'out.jsonl').read_text())
        assert row['design'] == 'input_sample0'
        assert row['fampnn_seq_probs_available'] is False
        assert row['seq_probs_reason'] == 'missing_declared_sample_pkl'
        assert row['fampnn_mean_sampled_prob'] is None


@pytest.mark.parametrize('foreign', ['candidate', 'tensor'])
def test_foreign_artifact_rejected_by_analyzer(tmp_path, foreign):
    payload, policy, path, source = inputs(tmp_path)
    # Fixture export identity is captured before substitution.
    from test_fampnn_strict_contract import run
    run(payload, policy, path, source)
    (tmp_path/'pkls').mkdir()
    if foreign == 'candidate':
        (tmp_path/'samples/input_sample0.pdb').write_bytes(b'foreign non-structure')
    else:
        payload['seq_probs'][0] = payload['seq_probs'][1]
        path.write_bytes(pickle.dumps(payload))
    (tmp_path/'pkls'/path.name).write_bytes(path.read_bytes())
    result = cli(tmp_path, policy)
    assert result.returncode != 0, 'foreign native artifact was accepted'
    assert 'binding' in result.stderr
