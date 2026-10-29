"""Default runtime bindings for acquired weights and the shared launch environment.

Real filesystem layouts and the real compiler path; no model is loaded or run.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import paths
from services.result_state_integrity import job_expects_design_results

REPO_ROOT = Path(__file__).resolve().parents[3]
DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64


def _job(**values):
    base = dict(model_id='protein_modification_experimental', mode='de_novo_design', params={}, provenance={})
    return SimpleNamespace(**{**base, **values})


def test_plain_weights_path_keeps_precedence(tmp_path):
    (tmp_path / 'rfd4-proteina').mkdir()
    (tmp_path / 'layouts' / 'rfd4-proteina' / DIGEST_A).mkdir(parents=True)
    assert paths.default_weight_location('rfd4-proteina', tmp_path) == tmp_path / 'rfd4-proteina'


def test_single_acquired_layout_is_the_default(tmp_path):
    generation = tmp_path / 'layouts' / 'rfd4-proteina' / DIGEST_A
    generation.mkdir(parents=True)
    (tmp_path / 'layouts' / 'rfd4-proteina' / '.locks').mkdir()
    assert paths.default_weight_location('rfd4-proteina', tmp_path) == generation


@pytest.mark.parametrize('generations', [[], [DIGEST_A, DIGEST_B]])
def test_absent_or_ambiguous_layouts_are_never_guessed(tmp_path, generations):
    for name in generations:
        (tmp_path / 'layouts' / 'complexa' / name).mkdir(parents=True)
    assert paths.default_weight_location('complexa', tmp_path) == tmp_path / 'complexa'


def test_registered_native_generation_is_not_a_generic_design_job():
    registered = _job(model_id='protein_modification_experimental', mode='de_novo_design',
                      params={'generator': 'laproteina'},
                      provenance={'native_generation_publication': {'contract': 'laproteina_native_v1'}})
    assert job_expects_design_results(registered) is False
    # Without the publication, the historical expectation is unchanged.
    assert job_expects_design_results(_job(params={'generator': 'laproteina'})) is True


@pytest.mark.parametrize('model,key,request_key,request_value,member', [
    ('rfd4_proteina', 'rfd4_proteina_bundle', 'rfd4_request',
     {'input': {'kind': 'unconditional', 'name': 'sample',
                'chains': [{'chain_id': 'A', 'min_length': 100, 'max_length': 100}]},
      'settings': {'seed': 0, 'metrics_tags': []}}, 'rfd4-proteina'),
    ('complexa', 'complexa_weights_dir', 'complexa_request',
     {'task': 'AME', 'task_name': 'fixture', 'reward': None, 'seed': 0}, 'complexa'),
])
def test_real_compiler_binds_acquired_layout_when_plain_path_is_absent(
        tmp_path, monkeypatch, model, key, request_key, request_value, member):
    from component_runtime import SourceIdentity
    from services import nextflow
    from services.remote_execution import preloading
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: identity))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    generation = tmp_path / 'layouts' / member / DIGEST_A
    generation.mkdir(parents=True)
    monkeypatch.setattr(paths, 'get_weights_root', lambda: tmp_path)
    monkeypatch.setattr(nextflow, 'get_weights_root', lambda: tmp_path)
    params = {request_key: request_value}
    if model == 'rfd4_proteina':
        from scripts.lib.rfd4_proteina_contract import normalize_request
        params[request_key] = normalize_request(request_value)
    job = SimpleNamespace(id='recipe', model_id=model, mode='generate', params=params,
        child_output_dir=None, output_dir=str(tmp_path / 'uncreated'), provenance={},
        execution_source_revision=identity.revision, execution_source_tree=identity.tree)
    invocations = []
    preloading.compile_recipe(job, native_invocations=invocations)
    invocation, = invocations
    assert invocation.native_parameters[key] == str(generation)
    # An explicit operator selection still wins over the discovered default.
    explicit = tmp_path / 'chosen'
    job.params = {**params, key: str(explicit)}
    invocations.clear()
    preloading.compile_recipe(job, native_invocations=invocations)
    assert invocations[0].native_parameters[key] == str(explicit)


def test_workflow_adapter_prefers_the_installed_apptainer_without_reordering_path():
    script = (REPO_ROOT / 'scripts/run_biomodstack_workflow_adapter.sh').read_text()
    original = 'export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"'
    assert original in script
    assert script.index(original) < script.index('readlink -f "$HOME/.local/bin/apptainer"')
    assert 'export PATH="$_bms_apptainer_dir:$PATH"' in script


def test_general_rfd3_request_is_read_from_the_staged_task_copy():
    module = (REPO_ROOT / 'modules/rfd3.nf').read_text()
    assert 'new File(params.rfd3_generation_request_path.toString()).name' in module
    assert "--request '${requestPath}'" in module
    # PrepareGeneralRFD3Input already stages the request next to the native input.
    workflow = (REPO_ROOT / 'workflows/protein_design.nf').read_text()
    assert "path({ [request_json, 'rfd3_sources'] })" in workflow


def test_native_runners_set_the_environment_their_images_require():
    complexa = (REPO_ROOT / 'scripts/bms_complexa/run.py').read_text()
    assert 'env.setdefault("COMPLEXA_INIT", "bms")' in complexa
    assert 'env.setdefault("XDG_CACHE_HOME"' in complexa
    disco = (REPO_ROOT / 'scripts/run_disco_inference.py').read_text()
    assert 'os.access(hf_cache / "hub", os.W_OK)' in disco
    assert 'env.setdefault("HF_HUB_OFFLINE", "1")' in disco


def test_framed_native_log_scheduling_event_is_decoded_and_idempotent(tmp_path):
    from services import rf_filter_task_roster as roster
    from test_rf_filter_task_roster import owner
    job = owner(tmp_path)
    assert roster.begin(job, job.params)
    bare = '[30/cfa1c7] Submitted process > PROTEIN_LOCAL_REDESIGN:FilterRFD3 (1)'
    framed = 'Oct-29 14:06:17.649 [Task submitter] INFO  nextflow.Session - ' + bare
    assert roster.observe(job, bare)
    assert roster.observe(job, framed)
    task = job.provenance[roster.KEY]['stages']['rfd3_backbone_filter']['tasks']['1']
    assert len(task['executions']) == 1
    with pytest.raises(ValueError, match='decoded'):
        roster.observe(job, 'Oct-29 14:06:17.649 [Task submitter] INFO  nextflow.Session - '
                            '[30/cfa1c7] Submitted process > PROTEIN_LOCAL_REDESIGN:FilterRFD3 (bad)')


def test_filter_stage_label_never_replaces_canonical_candidate_identity(tmp_path):
    import csv
    import json
    import sys
    sys.path.insert(0, str(REPO_ROOT / 'scripts'))
    from metadata_converter import MetadataConverter
    canonical = '7ba52db3-b31f-de56-297d-3de7b479b253'
    fold = tmp_path / 'fold.jsonl'
    fold.write_text(json.dumps({'fold_id': 0, 'rfd_RoG': 10.3, 'passed': True,
                                'candidate_id': 'model_0.cif.gz'}) + '\n')
    fold_seq = tmp_path / 'fold-seq.jsonl'
    fold_seq.write_text(json.dumps({'candidate_id': canonical, 'fold_id': 0, 'seq_id': None,
                                    'description': 'model_0', 'pr_RoG': 10.4}) + '\n')
    output = tmp_path / 'combined.csv'
    assert MetadataConverter().merge_all(str(fold), str(fold_seq), str(output))
    rows = list(csv.DictReader(output.open()))
    assert [r['candidate_id'] for r in rows] == [canonical]
    assert rows[0]['rfd_RoG'] == '10.3' and rows[0]['pr_RoG'] == '10.4'


def test_complexa_publishes_its_results_directory_where_the_reader_looks():
    module = (REPO_ROOT / 'modules/complexa.nf').read_text()
    # The only output is the directory 'results'; a 'results/**' pattern never matches it.
    assert "pattern: 'results/**'" not in module
    assert "saveAs: { it == 'results' ? 'complexa' : null }" in module
    assert 'publishDir "${params.out_dir}/collected"' in module
    reader = (REPO_ROOT / 'platform/api/services/rfd4_proteina_results.py').read_text()
    assert "'complexa': ('collected/complexa', 'complexa-results.json'" in reader


def test_laproteina_sample_count_follows_requested_designs_unless_set_explicitly():
    module = (REPO_ROOT / 'modules/protein_cad_experimental.nf').read_text()
    la = "params.pcad_laproteina_samples_per_length ?: params.laproteina_samples_per_length ?: params.pcad_num_designs ?: 8"
    assert la in module
    # DISCO's own count already follows the request the same way.
    assert "params.disco_num_inference_seeds ?: params.pcad_num_designs ?: 8" in module


def test_compiled_laproteina_job_carries_requested_designs_and_no_invented_sample_count():
    from services.nextflow import build_nextflow_command
    cmd = build_nextflow_command(
        'protein_modification_experimental', 'de_novo_design',
        {'generator': 'laproteina', 'backend': 'laproteina', 'design_task': 'unconditional',
         'num_designs': 1, 'target_lengths': '60'}, '/tmp/out', job_id='job-la')
    joined = ' '.join(cmd)
    assert '--pcad_num_designs 1' in joined
    assert 'samples_per_length' not in joined   # nothing authored; the module derives it from num_designs


def test_profile_does_not_pin_laproteina_samples_per_length_ahead_of_requested_designs():
    import re
    config = (REPO_ROOT / 'nextflow.config').read_text()
    profile = re.search(r"\n    protein_cad_experimental \{.*?\n    \}\n", config, re.S).group(0)
    # A profile default would always win over the requested design count.
    assert 'laproteina_samples_per_length' not in profile
    assert 'pcad_num_designs = 8' in profile
