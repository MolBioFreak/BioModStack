"""Exercise actual preparation/publication; molecular bytes are inert fixtures."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prep_protein_cad_request as prep
from run_laproteina_inference import collect_outputs
from lib.disco_contract import native_overrides, legacy_effective_settings


def prepare(tmp_path, monkeypatch, *args):
    output = tmp_path / 'request.json'
    monkeypatch.setattr(sys, 'argv', ['prep', '--backend', 'disco', '--task', 'dna_conditioned',
        '--num-designs', '1', '--target-lengths', '10', '--disco-na-sequence', 'ACGT',
        '--output', str(output), '--input-dir', str(tmp_path / 'inputs'), *args])
    prep.main()
    return json.loads(output.read_text())


def test_conditional_fast_is_literal_and_legacy_dna_keeps_two_strands(tmp_path, monkeypatch):
    saved = {'effort': 'fast', 'seeds': 0, 'use_deepspeed_evo_attention': False}
    assert legacy_effective_settings(saved, 'dna_conditioned') == dict(saved, effort='max')
    assert saved['effort'] == 'fast'
    assert legacy_effective_settings(saved, 'custom_json') == saved
    r = prepare(tmp_path, monkeypatch, '--disco-effort', 'fast')
    assert r['disco']['effort'] == 'fast'
    jobs = json.loads(Path(r['disco']['compiled_input_json']).read_text())
    assert len(jobs[0]['sequences']) == 3
    assert jobs[0]['sequences'][0]['proteinChain']['sequence'] == '-' * 10
    # Already migrated historical effective max is not changed back to fast.
    r = prepare(tmp_path, monkeypatch, '--disco-effort', 'max')
    assert r['disco']['effort'] == 'max'


def test_typed_settings_and_explicit_strands_reach_native(tmp_path, monkeypatch):
    settings = {'seeds': 0, 'effort': 'fast', 'use_deepspeed_evo_attention': False,
                'num_inference_seeds': None, 'gamma0': 0, 'output_format': None}
    la = {'seed': 0, 'self_cond': False, 'bb_ca_gt_clamp_val': None}
    jobs = [{'name': "operator's input", 'sequences': [
        {'proteinChain': {'sequence': '----', 'count': 1}},
        {'dnaSequence': {'sequence': 'ACGT', 'count': 1}}]}]
    r = prepare(tmp_path, monkeypatch, '--task', 'custom_json',
        '--disco-settings-json', json.dumps(settings), '--laproteina-settings-json', json.dumps(la),
        '--disco-jobs-json', json.dumps(jobs))
    assert all(r['disco'][k] == v for k, v in settings.items())
    assert all(r['laproteina'][k] == v for k, v in la.items())
    assert json.loads(Path(r['disco']['input_json_path']).read_text()) == jobs
    assert 'seeds=0' in native_overrides(r['disco'])
    assert 'num_inference_seeds=null' in native_overrides(r['disco'])


def test_laproteina_publication_relocates_exact_bytes_and_failure(tmp_path):
    output = tmp_path / 'output'
    attempt = output / 'invocation_fixture'
    native = attempt / 'inference'
    native.mkdir(parents=True)
    (attempt / 'invocation.json').write_text('{}')
    (native / 'sample.pdb').write_bytes(b'INERT WRITER FIXTURE\n')
    record = dict(native_sample_id='sample', structure_path='sample.pdb', sequence='AC',
                  batch_index=0, sample_index=0)
    (native / 'sample_index.jsonl').write_text(json.dumps(record) + '\n')
    rows = collect_outputs({'job_name': 'fixture', 'task': 'unconditional'},
        {'attempt': attempt, 'inference_root': native}, output, 7)
    returned = tmp_path / 'returned'
    shutil.copytree(output, returned)
    shutil.rmtree(output)
    row = rows[0]
    assert not Path(row['structure_path']).is_absolute()
    assert (returned / row['structure_path']).read_bytes() == b'INERT WRITER FIXTURE\n'
    metadata = json.loads((returned / row['metadata_path']).read_text())
    assert (returned / metadata['invocation_path']).is_file()
    publication = json.loads((returned / 'laproteina_publication.json').read_text())
    assert publication['execution_failed'] and publication['native_returncode'] == 7
    assert (returned / publication['sample_index']).is_file()


def test_laproteina_zero_yield_still_publishes(tmp_path):
    attempt = tmp_path / 'invocation_zero'
    attempt.mkdir()
    rows = collect_outputs({'job_name': 'fixture', 'task': 'unconditional'},
        {'attempt': attempt, 'inference_root': attempt / 'inference'}, tmp_path)
    assert rows == []
    assert json.loads((tmp_path / 'laproteina_publication.json').read_text())['emitted_sample_count'] == 0
