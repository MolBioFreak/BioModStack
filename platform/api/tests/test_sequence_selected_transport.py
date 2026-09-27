"""Selected sequence request/compiler and portable-byte transport, without science."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import shutil
from types import SimpleNamespace

import pytest

from schemas import JobCreate
from routers.jobs import normalize_job_request
from services.binder_continuation import individual_model_requests, model_request
from services.nextflow import compile_job_nextflow_invocation
from services.sequence_designer_settings import normalize_historical_sequence_settings
from services.remote_execution import bundle
from scripts.lib.portable_inputs import discover_native_input_references, resolve_input_path
from test_remote_bundle_path_gaps import roots  # noqa: F401


PDB = ('ATOM      1  CA  ALA T   7       0.000   0.000   0.000  1.00 20.00           C\n'
       'ATOM      2  CA  ALA a  10       1.000   0.000   0.000  1.00 20.00           C\n'
       'ATOM      3  CA  GLY 1   5       2.000   0.000   0.000  1.00 20.00           C\nEND\n')


def selected_requests(tmp_path, model, settings):
    selection = tmp_path / 'selection'
    selection.mkdir()
    rows = []
    for design_id in ['d.second', 'd.first']:
        path = selection / (design_id + '.pdb')
        path.write_text(PDB)
        rows.append(dict(design_id=design_id, design_job_id='source', selection_pdb_path=str(path)))
    (selection / 'selection_manifest.json').write_text(json.dumps({'designs': rows}))
    (selection / 'source_identity.json').write_text(json.dumps([
        {'staged_name': Path(row['selection_pdb_path']).name, 'source_path': row['selection_pdb_path'],
         'source_meta': {'parent_design_id': row['design_id'], 'source_structure_path': '/archived/unavailable.pdb'}}
        for row in rows]))
    base = model_request(operation=model, params=settings,
                         source=SimpleNamespace(id='source', name='selected generic'),
                         root=SimpleNamespace(id='root'), selection_dir=selection, execution_target_id=None)
    return base, individual_model_requests(base, model, selection)


def compile_request(request, output):
    normalized = normalize_job_request(request)
    replay = normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert replay.model_dump() == normalized.model_dump()
    job = SimpleNamespace(id='selected-transport', model_id=normalized.model_id,
                          mode=normalized.mode, params=normalized.params, provenance={})
    invocation = compile_job_nextflow_invocation(job, deepcopy(normalized.params), str(output))
    settings = json.loads(next(item.payload for item in invocation.generated_inputs
                               if item.relative_path == '.sequence-design-settings.json'))
    return normalized, invocation, settings


@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn'])
def test_selected_roles_count_identity_and_falsey_science_reach_real_compiler(model, tmp_path):
    settings = dict(binder_chains='a,1', target_chains='T', fixed_positions='a:10,1:5', seqs_per_design=3)
    expected = dict(design_chain='a,1', target_chain='T', fixed_positions='a:10,1:5', seqs_per_design=3)
    if model == 'proteinmpnn':
        expected.update(mpnn_relax_max_cycles=0, mpnn_relax_output=False, mpnn_omitAAs='',
                        mpnn_output_intermediates=False, mpnn_backbone_noise=0,
                        mpnn_num_connections=32)
    else:
        expected.update(fampnn_presort_by_length=False, fampnn_seed=42, fampnn_psce_threshold=None,
                        fampnn_scn_s_churn=0, fampnn_scn_s_noise=0, fampnn_repack_last=False,
                        fampnn_checkpoint='fampnn_0_3_cath.pt')
    settings.update({key: value for key, value in expected.items() if key not in {'design_chain', 'target_chain'}})
    original = deepcopy(settings)
    base, children = selected_requests(tmp_path, model, settings)
    assert settings == original
    assert base.params['iteration_source_design_ids'] == ['d.second', 'd.first']
    assert len(children) == 2
    for child, design_id in zip(children, ['d.second', 'd.first']):
        normalized, invocation, document = compile_request(child, tmp_path / ('out-' + design_id))
        for key, value in {**expected, 'iteration_source_design_ids': [design_id]}.items():
            assert normalized.params[key] == value
            assert invocation.native_parameters[key] == value
            assert document[key] == value
        assert 'binder_chains' not in normalized.params
        assert 'target_chains' not in normalized.params
        assert not any(key in normalized.params for key in ('antibody_design_loops', 'antibody_chains', 'cdr_positions'))
        assert invocation.entrypoint == 'workflows/protein_sequence_design.nf'
        assert '-params-file' in invocation.command


@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn'])
def test_explicit_native_roles_win_over_selected_aliases(model, tmp_path):
    base, _ = selected_requests(tmp_path, model, dict(binder_chains='a', target_chains='T',
                                                     design_chain='1', target_chain=''))
    assert base.params['design_chain'] == '1'
    assert base.params['target_chain'] == ''


def test_historical_fampnn_migration_preserves_unknown_and_explicit_values():
    raw = dict(fampnn_extra_config='seed=42 seed=7 presort_by_length=false scn_diffusion.churn_cfg.s_noise=0 '
               'psce_threshold=null timestep_schedule.mode=cosine num_seqs_per_pdb=3 temperature=0.2 '
               "unconsumed='keep these words' ~checkpoint_path", fampnn_seed=0, fampnn_temperature=0)
    before = deepcopy(raw)
    migrated = normalize_historical_sequence_settings('fampnn', raw)
    assert raw == before
    assert migrated['fampnn_seed'] == 0
    assert migrated['fampnn_presort_by_length'] is False
    assert migrated['fampnn_scn_s_noise'] == 0
    assert migrated['fampnn_psce_threshold'] is None
    assert migrated['fampnn_timestep_mode'] == 'cosine'
    assert migrated['seqs_per_design'] == 3
    assert migrated['fampnn_temperature'] == 0
    assert shlex.split(migrated['fampnn_extra_config']) == ['unconsumed=keep these words', '~checkpoint_path']
    assert normalize_historical_sequence_settings('fampnn', migrated) == migrated
    assert normalize_historical_sequence_settings('other', raw) == raw


def test_historical_fampnn_actual_jobs_defaults_and_clone_compiler(tmp_path):
    pdb = tmp_path / 'input.pdb'
    pdb.write_text(PDB)
    request = JobCreate(name='historical scalar migration', model_id='fampnn', mode='binder_design', params={
        'input_pdb': str(pdb), 'design_chain': 'a,1', 'target_chain': 'T',
        'fampnn_extra_config': 'seed=42 presort_by_length=false scn_diffusion.churn_cfg.s_noise=0 '
                               'psce_threshold=null num_seqs_per_pdb=3 timestep_schedule.mode=cosine',
        'fampnn_seed': 0})
    normalized, invocation, document = compile_request(request, tmp_path / 'out')
    for key, value in dict(fampnn_seed=0, fampnn_presort_by_length=False, fampnn_scn_s_noise=0,
                           fampnn_psce_threshold=None, seqs_per_design=3, fampnn_timestep_mode='cosine',
                           fampnn_extra_config='').items():
        assert normalized.params[key] == document[key] == invocation.native_parameters[key] == value


@pytest.mark.parametrize('as_dict', [False, True])
def test_historical_caliby_migration_keeps_unknown_nested_extras(as_dict):
    overrides = {'verbose': True, 'gaussian_conformers_cfg': {'n_conformers': 0, 'noise_std': 0},
                 'potts_sampling_cfg': {'potts_proposal': '', 'rejection_step': False, 'native_extra': 2},
                 'scn_packing_cfg': {'step_scale': 0}, 'unknown_native': {'keep': False}}
    raw = {'caliby_sampling_overrides_json': overrides if as_dict else json.dumps(overrides),
           'caliby_verbose': False}
    before = deepcopy(raw)
    migrated = normalize_historical_sequence_settings('caliby_binder', raw)
    assert raw == before
    for key, value in dict(caliby_verbose=False, caliby_gaussian_n_conformers=0,
                           caliby_gaussian_noise_std=0, caliby_potts_proposal='',
                           caliby_potts_rejection_step=False, caliby_scn_step_scale=0).items():
        assert migrated[key] == value
    remainder = migrated['caliby_sampling_overrides_json']
    assert (remainder if as_dict else json.loads(remainder)) == {
        'potts_sampling_cfg': {'native_extra': 2}, 'unknown_native': {'keep': False}}
    assert normalize_historical_sequence_settings('caliby_binder', migrated) == migrated


def test_historical_caliby_actual_normalization_before_defaults(tmp_path):
    selection = tmp_path / 'input.pdb'
    selection.write_text(PDB)
    request = JobCreate(name='historical Caliby settings', model_id='caliby_binder', mode='design', params={
        'pdb_paths': str(selection), 'binder_chains': 'a,1', 'target_chains': 'T',
        'caliby_verbose': False,
        'caliby_sampling_overrides_json': json.dumps({
            'verbose': True, 'gaussian_conformers_cfg': {'n_conformers': 0, 'noise_std': 0},
            'potts_sampling_cfg': {'rejection_step': False}, 'scn_packing_cfg': {'step_scale': 0}})})
    normalized = normalize_job_request(request)
    expected = dict(caliby_verbose=False, caliby_gaussian_n_conformers=0, caliby_gaussian_noise_std=0,
                    caliby_potts_rejection_step=False, caliby_scn_step_scale=0)
    for key, value in expected.items():
        assert normalized.params[key] == value
    assert normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json'))).params == normalized.params


def test_bias_file_real_compiler_bundle_inventory_and_offline_binding(roots, tmp_path, monkeypatch):
    bias = roots['inputs'] / 'native-bias.jsonl'
    bias.write_text('{"A": -0.25, "G": 0.0}\n')
    expected_bias = bias.read_bytes()
    base, children = selected_requests(roots['inputs'], 'proteinmpnn', {
        'binder_chains': 'a,1', 'target_chains': 'T', 'seqs_per_design': 3,
        'mpnn_bias_AA_jsonl': str(bias), 'mpnn_omitAAs': ''})
    output = roots['results'] / 'child'
    normalized, invocation, settings = compile_request(children[0], output)
    assert 'mpnn_bias_AA_jsonl' not in settings  # File is relocated as argv, never science JSON.
    assert invocation.native_parameters['mpnn_bias_AA_jsonl'] == str(bias)
    assert invocation.command[invocation.command.index('--mpnn_bias_AA_jsonl') + 1] == str(bias)
    invocation.materialize_inputs(output)
    refs = []
    assets = bundle._input_assets(normalized.params, native_invocation=invocation,
                                  repo_root=roots['repo'], runtime_paths=set(), output_dir=output, references=refs)
    bias_refs = [ref for ref in refs if ref['selector'] == ['mpnn_bias_AA_jsonl']]
    assert len(bias_refs) == 1
    reference = bias_refs[0]
    assert reference['sha256'] == hashlib.sha256(expected_bias).hexdigest()
    assert reference['size_bytes'] == len(expected_bias)
    assert all(ref['source_path'] != '/archived/unavailable.pdb' for ref in refs)
    transfers, records, inventory = [], [], {}
    attempt = tmp_path / 'worker'
    for source, relative in assets:
        prefix = 'inputs/' + relative
        member_records = bundle._input_records(source, prefix, native_invocation=invocation, output_dir=output)
        records.extend(member_records)
        remote = attempt / 'bundle' / prefix
        remote.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, remote)
        else:
            shutil.copyfile(source, remote)
        transfers.append(bundle.TransferPlan(source, str(remote)))
        for record in member_records:
            original = source / record.relative_path[len(prefix):].lstrip('/') if source.is_dir() else source
            inventory[str(original)] = (record.sha256, record.size_bytes)
    for ref in refs:
        assert inventory[ref['source_path']] == (ref['sha256'], ref['size_bytes'])
    staging = tmp_path / 'bindings'
    staging.mkdir()
    binding, _ = bundle._write_portable_bindings(staging_root=staging, remote_attempt=str(attempt),
        references=refs, input_transfers=transfers, input_records=records,
        remote_runtime=str(attempt / 'runtime'), remote_results=str(attempt / 'results'))
    monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', str(binding.source))
    bias.unlink()
    shutil.rmtree(roots['inputs'] / 'selection')
    relocated = resolve_input_path(bias)
    assert relocated.read_bytes() == expected_bias
    assert resolve_input_path(normalized.params['source_identity_json']).is_file()
    # Archived settings and identity bytes remain unchanged; provenance is not reacquired.
    assert json.loads(resolve_input_path(output / '.sequence-design-settings.json').read_bytes()) == settings
    relocated.write_text('changed')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        resolve_input_path(bias)


def test_bias_file_full_bundle_keeps_compiled_file_argument(roots, tmp_path, monkeypatch):
    """Actual request/compiler + bundle, with CPU-only runtime/source projection."""
    from dataclasses import replace
    from component_runtime import SourceIdentity
    from test_remote_cache_integration import cache_only_plan_fixture
    from test_remote_bundle_runtime_gaps import bundle_assignment_fixture

    bias = roots['inputs'] / 'bias.jsonl'
    bias.write_text('{"A": 0}\n')
    _, children = selected_requests(roots['inputs'], 'proteinmpnn', {
        'binder_chains': 'a,1', 'target_chains': 'T', 'mpnn_bias_AA_jsonl': str(bias)})
    output = roots['results'] / 'job'
    normalized, compiled, _ = compile_request(children[0], output)
    compiled.materialize_inputs(output)
    repo = Path(__file__).resolve().parents[3]
    identity = SourceIdentity.from_checkout(repo)
    native = cache_only_plan_fixture(replace(compiled, source_identity=identity))
    # Source archive and GPU dependency qualification have separate owners.
    # These substitutions do not alter compiled scientific values or input bytes.
    monkeypatch.setattr(bundle, 'get_code_root', lambda: repo)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: (identity.revision, identity.tree))
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *_, **__: [])
    job = SimpleNamespace(id='job', model_id=normalized.model_id, mode=normalized.mode,
        params=normalized.params, output_dir=str(output), child_output_dir=None,
        lineage_root_job_id='root', parent_job_id=None, execution_source_revision=identity.revision,
        execution_source_tree=identity.tree, assigned_gpu=None, stage_family=None,
        stage_mode=normalized.mode, selected_input_artifact_class=None,
        provenance=bundle_assignment_fixture(()))
    dispatch = bundle.prepare_remote_bundle(job=job,
        target=SimpleNamespace(id='target', remote_root=str(tmp_path / 'remote')),
        command=list(native.command), native_invocation=native)
    mapped = dispatch.envelope.path_map[str(bias)]
    command = dispatch.envelope.command
    assert command[command.index('--mpnn_bias_AA_jsonl') + 1] == mapped
    relative = mapped.removeprefix(dispatch.remote_attempt_dir + '/bundle/')
    files = {item.relative_path: item for item in dispatch.envelope.files}
    assert files[relative].sha256 == hashlib.sha256(bias.read_bytes()).hexdigest()
    assert files[relative].size_bytes == bias.stat().st_size
    assert '/archived/unavailable.pdb' not in dispatch.envelope.path_map


def test_bias_file_discovery_retains_existing_containment(roots, tmp_path):
    outside = tmp_path / 'outside.jsonl'
    outside.write_text('{}\n')
    with pytest.raises(ValueError, match='outside approved roots'):
        discover_native_input_references('proteinmpnn', 'design', {'mpnn_bias_AA_jsonl': str(outside)}, (),
            output_dir=roots['results'], allowed_roots=[roots['inputs'], roots['results']])
    link = roots['inputs'] / 'bias.jsonl'
    link.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        discover_native_input_references('proteinmpnn', 'design', {'mpnn_bias_AA_jsonl': str(link)}, (),
            output_dir=roots['results'], allowed_roots=[roots['inputs'], roots['results']])
