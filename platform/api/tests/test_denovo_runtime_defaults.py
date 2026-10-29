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
