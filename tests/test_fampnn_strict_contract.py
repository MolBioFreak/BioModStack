"""Producer-shaped, CPU-only FA-MPNN scientific-contract tests."""
import copy
import hashlib
import importlib.util
import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/analyse_fampnn_seq_probs.py'
spec = importlib.util.spec_from_file_location('fa_analysis', SCRIPT)
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)
DIALECT = 'fampnn-18363df253dbeb7b2cb963daf7a732fbaa25157d'
ALPHABET = 'ARNDCQEGHILKMFPSTWYVX'


def inputs(tmp_path, n=3):
    # Upstream uses encounter order, NOT alphabetical or PDB_CHAIN_IDS order.
    source = tmp_path / 'input.pdb'
    source.write_text(''.join(
        f'ATOM  {i+1:5d}  CA  ALA Z{10+i:4d}    {0:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C\n'
        for i in range(n)))
    payload = dict(seq_probs=np.eye(21)[np.arange(n) % 21],
                   pred_aatype=np.arange(n) % 21, seq_mask=np.ones(n),
                   aatype_override_mask=np.zeros(n), chain_index=np.zeros(n),
                   residue_index=np.arange(10, 10+n))
    ids = [f'Z:{10+i}:' for i in range(n)]
    policy = dict(schema_version=1, owner='protein_design', version=1,
                  declaration='declared_protein_inputs', dialect=DIALECT,
                  require_full_coverage=False, allow_summary_override=True,
                  inputs={'input': dict(input_domain=ids, sequence_design=ids,
                                        summary=ids, summary_override=None,
                                        mutation_override=None)})
    path = tmp_path / 'input_sample0.pkl'
    bind_fixture(policy, source)
    return payload, policy, path, source


def run(payload, policy, path, source, **kwargs):
    path.write_bytes(pickle.dumps(payload))
    return fa.analyze_sample_pkl(path, core_protein_scientific_contract=1,
                                 analysis_policy=policy, source_pdb_dir=source.parent, **kwargs)


def bind_fixture(policy, source):
    policy['inputs']['input']['artifact_binding'] = {
        'producer_input_id': 'input',
        'source_pdb_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'producer_candidate_ids': ['input_sample0'],
    }
    (source.parent / 'samples').mkdir(exist_ok=True)
    (source.parent / 'samples/input_sample0.pdb').write_bytes(source.read_bytes())


def test_exact_byte_bindings_use_captured_source_sample_policy_and_candidate(tmp_path, monkeypatch):
    p, policy, path, source = inputs(tmp_path)
    bind_fixture(policy, source)
    sample_bytes = pickle.dumps(p)
    path.write_bytes(sample_bytes)
    policy_bytes = json.dumps(policy, indent=3).encode()
    candidate = source.parent / 'samples/input_sample0.pdb'
    original = {source: source.read_bytes(), path: sample_bytes, candidate: candidate.read_bytes()}
    reads = {}
    real_read = Path.read_bytes
    def replacing_read(file):
        data = real_read(file)
        if file in original:
            reads[file] = reads.get(file, 0) + 1
            file.write_bytes(b'replaced after capture')
        return data
    monkeypatch.setattr(Path, 'read_bytes', replacing_read)
    row = fa.analyze_sample_pkl(path, core_protein_scientific_contract=1,
        analysis_policy=policy_bytes, source_pdb_dir=source.parent)
    binding = row['artifact_binding']
    assert binding['producer_input_id'] == 'input'
    assert binding['producer_candidate_id'] == 'input_sample0'
    for key, file in [('source_pdb', source), ('sample_pkl', path), ('candidate_pdb', candidate)]:
        assert binding[key]['sha256'] == hashlib.sha256(original[file]).hexdigest()
        assert binding[key]['size_bytes'] == len(original[file])
    assert binding['analysis_policy']['sha256'] == hashlib.sha256(policy_bytes).hexdigest()
    assert reads == {source: 1, path: 1, candidate: 1}
    assert row['selected_count'] == 3


def test_candidate_byte_binding_does_not_invent_source_author_chain_parity(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    candidate = source.parent / 'samples/input_sample0.pdb'
    # Native output may serialize numeric chain indexes as A/B/... while source
    # author chains were Z/B/.... Bind bytes; do not reuse source-author mapping.
    candidate.write_text(source.read_text().replace('ALA Z', 'ALA A'))
    row = run(p, policy, path, source)
    assert row['artifact_binding']['candidate_pdb']['sha256'] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert row['residue_evidence'][0]['identity'] == 'Z:10:'


@pytest.mark.parametrize('defect', ['missing', 'foreign_source', 'foreign_input', 'foreign_candidate', 'replaced_source'])
def test_authoritative_artifact_binding_rejects_substitution(tmp_path, defect):
    p, policy, path, source = inputs(tmp_path)
    bind_fixture(policy, source)
    binding = policy['inputs']['input']['artifact_binding']
    if defect == 'missing':
        del policy['inputs']['input']['artifact_binding']
    elif defect == 'foreign_source':
        binding['source_pdb_sha256'] = '0' * 64
    elif defect == 'foreign_input':
        binding['producer_input_id'] = 'foreign'
    elif defect == 'foreign_candidate':
        binding['producer_candidate_ids'] = ['input_sample1']
    else:
        source.write_text(source.read_text().replace('0.000', '9.000'))
    with pytest.raises(ValueError, match='binding'):
        run(p, policy, path, source)


def test_versioned_policy_schema_matches_adapter(tmp_path):
    import jsonschema
    p, policy, path, source = inputs(tmp_path)
    schema_path = ROOT / 'schemas/fampnn_analysis_policy_v1.schema.json'
    assert schema_path.exists(), 'Resolved workflow policy needs a closed transport schema'
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(policy, schema)
    policy['unexpected'] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(policy, schema)


def test_pinned_alphabet_and_source_identity(tmp_path):
    p, policy, path, source = inputs(tmp_path, 21)
    row = run(p, policy, path, source)
    assert ''.join(r['aa'] for r in row['residue_evidence']) == ALPHABET
    assert row['residue_evidence'][0]['identity'] == 'Z:10:'
    assert row['fampnn_mean_sampled_prob'] == 1
    assert row['fampnn_mean_entropy'] == 0
    assert row['scored_selected_count'] == 21


def test_positive_x_is_confidence_only_not_mutation_source_or_target(tmp_path):
    p, policy, path, source = inputs(tmp_path, 2)
    p['pred_aatype'] = [0, 20]
    p['seq_probs'] = np.full((2, 21), 1 / 21)
    row = run(p, policy, path, source, mutation_top_n=100, mutation_min_log_odds_delta=-10)
    candidates = row['fampnn_top_model_favored_mutations']
    assert candidates and any(c['from_aa'] == 'A' and c['to_aa'] == 'R' for c in candidates)
    assert all('X' not in (c['from_aa'], c['to_aa']) for c in candidates)
    assert row['residue_evidence'][1]['aa'] == 'X'
    assert row['residue_evidence'][1]['sampled_prob'] == pytest.approx(1 / 21)
    assert not row['mutation_omissions']  # no zero-log pathway masks this defect


def test_fixed_zero_row_is_unscored_and_not_mutatable(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    p['seq_probs'][0] = 0
    p['aatype_override_mask'][0] = 1
    row = run(p, policy, path, source)
    assert row['selected_count'] == 3
    assert row['scored_selected_count'] == 2
    assert row['unscored_selected_count'] == 1
    assert row['coverage'] == pytest.approx(2/3)
    assert row['fampnn_mean_sampled_prob'] == 1
    assert row['residue_evidence'][0]['entropy'] is None
    assert row['residue_evidence'][0]['scored'] is False
    assert 'Z:10:' not in row['resolved_mutation_membership']


def test_zero_probability_is_not_epsilon_evidence(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    p['seq_probs'][0] = np.eye(21)[1]
    row = run(p, policy, path, source)
    assert row['residue_evidence'][0]['sampled_prob'] == 0
    assert row['residue_evidence'][0]['log_prob'] is None
    assert row['residue_evidence'][0]['log_prob_reason'] == 'zero_probability'
    assert row['fampnn_total_sampled_log_prob'] is None
    assert row['sampled_log_prob_reason'] == 'zero_probability'
    assert not row['fampnn_top_model_favored_mutations']
    assert row['mutation_omissions'][0]['reason'] == 'zero_probability'


@pytest.mark.parametrize('key,value', [
    ('seq_probs', np.ones((3,20))), ('seq_probs', np.ones((1,3,21))),
    ('pred_aatype', [0,1]), ('pred_aatype', [0,1,21]),
    ('pred_aatype', [0,1,0.5]), ('seq_mask', [1,1]),
    ('seq_mask', [1,1,2]), ('aatype_override_mask', [0,0,np.nan]),
    ('chain_index', [0,0,0.5]), ('residue_index', [10,11,np.inf]),
    ('residue_index', [10,11,11]), ('chain_index', [1,1,1]),
    ('seq_probs', np.full((3,21), -1.0)),
    ('seq_probs', np.full((3,21), np.nan)),
    ('seq_probs', np.full((3,21), np.inf)),
])
def test_rejects_malformed_producer_arrays(tmp_path, key, value):
    p, policy, path, source = inputs(tmp_path)
    p[key] = value
    with pytest.raises(ValueError):
        run(p, policy, path, source)


@pytest.mark.parametrize('key', ['seq_mask','aatype_override_mask','chain_index','residue_index'])
def test_required_arrays_are_not_fabricated(tmp_path, key):
    p, policy, path, source = inputs(tmp_path)
    del p[key]
    with pytest.raises(ValueError):
        run(p, policy, path, source)


def test_independent_scope_overrides_and_fixed_exclusion(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    entry = policy['inputs']['input']
    entry['summary_override'] = ['Z:10:']
    entry['mutation_override'] = ['Z:11:']
    p['seq_probs'][1] = np.eye(21)[1]*0.2 + np.eye(21)[2]*0.8
    row = run(p, policy, path, source)
    assert row['selected_count'] == 1
    assert row['resolved_summary_membership'] == ['Z:10:']
    assert row['resolved_mutation_membership'] == ['Z:11:']
    assert row['fampnn_top_model_favored_mutations'][0]['mutation'] == 'R11N'
    assert row['analysis_policy'] == policy
    entry['sequence_design'] = ['Z:10:']
    with pytest.raises(ValueError, match='mutation'):
        run(p, policy, path, source)


def test_constraint_transport_omission_is_not_blessed(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    policy['inputs']['input']['sequence_design'] = ['Z:10:']
    with pytest.raises(ValueError, match='constraint authority'):
        run(p, policy, path, source)
    p['aatype_override_mask'][1:] = 1
    row = run(p, policy, path, source)
    assert row['resolved_mutation_membership'] == ['Z:10:']


def test_policy_and_identity_fail_closed(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    with pytest.raises(ValueError, match='policy'):
        run(p, None, path, source)
    bad = copy.deepcopy(policy)
    bad['dialect'] = 'width21'
    with pytest.raises(ValueError, match='dialect'):
        run(p, bad, path, source)
    bad = copy.deepcopy(policy)
    bad['allow_summary_override'] = False
    bad['inputs']['input']['summary_override'] = ['Z:10:']
    with pytest.raises(ValueError, match='override'):
        run(p, bad, path, source)
    source.unlink()
    with pytest.raises(ValueError, match='source'):
        run(p, policy, path, source)


def test_empty_scope_and_required_coverage(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    policy['inputs']['input']['summary_override'] = []
    row = run(p, policy, path, source)
    assert row['selected_count'] == 0
    assert row['coverage'] is None
    assert row['fampnn_mean_sampled_prob'] is None
    policy['inputs']['input']['summary_override'] = None
    policy['require_full_coverage'] = True
    p['seq_probs'][0] = 0
    with pytest.raises(ValueError, match='coverage'):
        run(p, policy, path, source)


def test_presence_does_not_redefine_selected_scope(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    p['seq_mask'][0] = 0
    row = run(p, policy, path, source)
    assert row['present_count'] == 2
    assert row['selected_count'] == 3
    assert row['residue_evidence'][0]['selected'] is True
    assert row['residue_evidence'][0]['scored'] is False
    assert row['unscored_selected_count'] == 1


def test_pinned_insertion_offsets_and_chain_encounter_order(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    source.write_text(''.join(
        f'ATOM  {i+1:5d}  CA  ALA {chain}{num:4d}{ins:1s}   {0:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C\n'
        for i, (chain, num, ins) in enumerate([('Z',10,''),('Z',10,'A'),('B',4,'')])
    ))
    p['residue_index'] = [10,11,4]
    p['chain_index'] = [0,0,1]
    bind_fixture(policy, source)
    entry = policy['inputs']['input']
    for key in ['input_domain','sequence_design','summary']:
        entry[key] = ['Z:10:','Z:10:A','B:4:']
    p['seq_probs'][1] = np.eye(21)[1]*0.2 + np.eye(21)[2]*0.8
    row = run(p, policy, path, source)
    assert [r['identity'] for r in row['residue_evidence']] == entry['summary']
    assert row['fampnn_top_model_favored_mutations'][0]['mutation'] == 'R10AN'


@pytest.mark.parametrize('change', ['hetero', 'multiple_models', 'delimiter_chain'])
def test_unsupported_source_identity_is_explicitly_blocked(tmp_path, change):
    p, policy, path, source = inputs(tmp_path)
    text = source.read_text()
    if change == 'hetero':
        text = text.replace('ATOM  ', 'HETATM')
    elif change == 'multiple_models':
        text = 'MODEL        1\n' + text + 'ENDMDL\nMODEL        2\n' + text
    else:
        text = text.replace('ALA Z', 'ALA :')
    source.write_text(text)
    bind_fixture(policy, source)
    with pytest.raises(ValueError, match='source identity'):
        run(p, policy, path, source)


@pytest.mark.parametrize('defect', ['extra', 'missing', 'duplicate', 'wrong_type'])
@pytest.mark.parametrize('empty', [False, True])
def test_whole_policy_validation_before_iteration(tmp_path, defect, empty):
    p, policy, path, source = inputs(tmp_path)
    unused = copy.deepcopy(policy['inputs']['input'])
    unused['artifact_binding']['producer_input_id'] = 'unused'
    unused['artifact_binding']['producer_candidate_ids'] = ['unused_sample0']
    policy['inputs']['unused'] = unused
    if defect == 'extra':
        unused['extra'] = True
    elif defect == 'missing':
        del unused['summary']
    elif defect == 'duplicate':
        unused['summary'] = ['Z:10:', 'Z:10:']
    else:
        unused['mutation_override'] = 'Z:10:'
    if not empty:
        path.write_bytes(pickle.dumps(p))
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy))
    out = tmp_path / 'out.jsonl'
    result = subprocess.run([sys.executable, str(SCRIPT), '--sample-pkl-dir', str(tmp_path),
        '--out-jsonl', str(out), '--core-protein-scientific-contract', '1',
        '--analysis-policy', str(policy_path), '--source-pdb-dir', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0, 'malformed unused policy was accepted'
    assert 'policy' in result.stderr
    assert not out.exists()


def test_policy_json_duplicate_keys_rejected(tmp_path):
    _, policy, _, _ = inputs(tmp_path)
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy).replace('"version": 1', '"version": 2, "version": 1'))
    result = subprocess.run([sys.executable, str(SCRIPT), '--sample-pkl-dir', str(tmp_path),
        '--out-jsonl', str(tmp_path/'out.jsonl'), '--core-protein-scientific-contract', '1',
        '--analysis-policy', str(policy_path), '--source-pdb-dir', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'duplicate' in result.stderr


def test_marked_empty_required_directory_is_not_success(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    policy['require_full_coverage'] = True
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy))
    result = subprocess.run([sys.executable, str(SCRIPT), '--sample-pkl-dir', str(tmp_path),
        '--out-jsonl', str(tmp_path/'out.jsonl'), '--core-protein-scientific-contract', '1',
        '--analysis-policy', str(policy_path), '--source-pdb-dir', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'coverage' in result.stderr


def test_marked_cli_requires_policy_and_unmarked_payload_marker_is_ignored(tmp_path):
    p, policy, path, source = inputs(tmp_path)
    p['core_protein_scientific_contract'] = 1
    path.write_bytes(pickle.dumps(p))
    out = tmp_path / 'out.jsonl'
    command = [sys.executable, str(SCRIPT), '--sample-pkl-dir', str(tmp_path), '--out-jsonl', str(out)]
    old = subprocess.run(command, capture_output=True, text=True)
    assert old.returncode == 0, old.stderr
    assert 'analysis_policy' not in json.loads(out.read_text())
    out.unlink()
    strict = subprocess.run(command + ['--core-protein-scientific-contract', '1'], capture_output=True, text=True)
    assert strict.returncode != 0
    assert 'policy' in strict.stderr
    assert not out.exists()
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy))
    strict = subprocess.run(command + ['--core-protein-scientific-contract', '1',
        '--analysis-policy', str(policy_path), '--source-pdb-dir', str(tmp_path)], capture_output=True, text=True)
    assert strict.returncode == 0, strict.stderr
    assert json.loads(out.read_text())['selected_count'] == 3
    assert json.loads(out.read_text())['artifact_binding']['analysis_policy'] == {
        'sha256': hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        'size_bytes': len(policy_path.read_bytes()),
    }


@pytest.mark.parametrize('defect', ['boolean', 'above_one', 'positive_underflow_sum', 'positive_overflow_sum'])
def test_probability_contract_rejects_malformed_rows(tmp_path, defect):
    payload, policy, path, source = inputs(tmp_path)
    if defect == 'boolean':
        payload['seq_probs'] = payload['seq_probs'].astype(bool)
    elif defect == 'above_one':
        payload['seq_probs'][0, 0] = 1.01
    elif defect == 'positive_underflow_sum':
        payload['seq_probs'][0, 0] = 0.1
    else:
        payload['seq_probs'][0, :2] = [0.6, 0.6]
    with pytest.raises(ValueError, match='seq_probs'):
        run(payload, policy, path, source)


def test_probability_roundoff_tolerance_preserves_zero_channels(tmp_path):
    payload, policy, path, source = inputs(tmp_path)
    payload['seq_probs'][0, :2] = [0.5, 0.5000001]
    row = run(payload, policy, path, source)
    first = row['residue_evidence'][0]
    assert first['sampled_prob'] == pytest.approx(0.5 / 1.0000001)
    assert first['scored'] is True
    assert any(item['reason'] == 'zero_probability' for item in row['mutation_omissions'])
