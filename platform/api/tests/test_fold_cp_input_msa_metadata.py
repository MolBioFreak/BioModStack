"""Offline compiler/controller/native consumer closure, not GPU acceptance."""
import json
import shutil
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from biomodstack_msa_handoff import digest
from services.model_msa_handoff import prepare_launch_msa
from services.nextflow import compile_nextflow_invocation, _bind_protenix_msa_transport


def compiled(tmp_path, frustra=False, provider='neurosnap_api', preview=False, use_msa=True):
    params = dict(pred_method='fold_cp', sequence='ACDE', sequence_name='native',
        complex_components=[{'type': 'protein', 'id': 'A', 'sequence': 'ACDE'},
                            {'type': 'dna', 'id': 'D', 'sequence': 'ACGT'},
                            {'type': 'protein', 'id': 'B', 'sequence': 'FGHI'}],
        boltz_use_msa=use_msa, msa_provider=provider, bcp_size_cp=4,
        pinned_gpus=[0, 1, 2, 3], gpu_id=0, bcp_gpu_ids='0,1,2,3',
        boltz_sampling_steps=200, boltz_recycling_steps=3, boltz_num_samples=1,
        msa_neurosnap_coverage_percent=35, msa_neurosnap_identity_percent=50,
        msa_neurosnap_max_sequences=1000000, msa_neurosnap_pad_sequences=False,
        msa_neurosnap_force_uppercase=False, run_frustrampnn=frustra)
    if preview:
        params.pop('gpu_id')
    inv = compile_nextflow_invocation('boltz_cp_experimental', 'design', params, str(tmp_path / 'out'),
                                      _preview_only=preview)
    if not preview:
        inv.materialize_inputs(tmp_path / 'out')
    return inv


def provider_double(monkeypatch, tmp_path):
    from services import msa_preparation
    calls = []
    def prepare(*, sequences, params):
        calls.append((sequences, params))
        artifacts = []
        for i, sequence in enumerate(sequences):
            path = tmp_path / f'cache-{i}.a3m'
            path.write_text(f'>query\n{sequence}\n>fixture\n{sequence}\n')
            artifacts.append(dict(chain_index=i, role='unpaired', path=str(path), sha256=digest(path.read_bytes())))
        return dict(provider=params['msa_provider'], request_digest='fixture', artifacts=artifacts,
                    provenance={'fixture': True}, cache_hit=False)
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', prepare)
    return calls


@pytest.mark.parametrize('frustra', [False, True])
@pytest.mark.parametrize('provider', ['neurosnap_api', 'colabfold_api'])
def test_fold_cp_input_msa_producer_plan_and_portable_consumer(tmp_path, monkeypatch, frustra, provider):
    from biomodstack_boltz_msa import resolve_boltz_config
    calls = provider_double(monkeypatch, tmp_path)
    inv = compiled(tmp_path, frustra, provider)
    metadata = inv.execution_plan.metadata
    service, = metadata.external_services
    assert service.logical_id == 'boltz_cp_experimental:msa'
    assert service.provider == provider and service.state == 'planned_from_native_inputs'
    assert service.input_role_ids
    roles = {r.role_id: r for r in metadata.artifact_roles}
    assert all(roles[r].direction == 'input' for r in service.input_role_ids)
    assert all(roles[r].component_key == 'RunBoltzCPExperimental' for r in service.input_role_ids)
    assert not metadata.blockers
    assert bool(metadata.dynamic_templates) == frustra
    prepared = prepare_launch_msa(inv.model_id, inv.native_parameters, tmp_path / 'out/prepared-msa')
    bound = _bind_protenix_msa_transport(inv, prepared)
    assert bound.requested_json == inv.requested_json and bound.effective_json == inv.effective_json
    assert bound.execution_plan.metadata.static_components == metadata.static_components
    assert bound.execution_plan.metadata.dynamic_templates == metadata.dynamic_templates
    final, = bound.execution_plan.metadata.external_services
    assert final.state == 'prepared'
    assert final.operation_identity == 'sha256:' + prepared['boltz_prepared_msa_sha256']
    assert final.settings_json == service.settings_json and final.provider == provider
    assert _bind_protenix_msa_transport(bound, prepared) == bound
    assert len(calls) == 1 and calls[0][0] == ['ACDE', 'FGHI']
    assert calls[0][1]['msa_provider'] == provider
    assert calls[0][1]['boltz_sampling_steps'] == 200
    worker = tmp_path / 'worker'
    shutil.move(prepared['bcp_input_path'], worker)
    manifest = json.loads((worker / 'msa-inputs.json').read_text())
    for config in manifest['configs']:
        native = resolve_boltz_config(worker / config['path'], root=worker,
            manifest_sha256=prepared['boltz_prepared_msa_sha256'])
        assert native['sequences'][1]['dna']['sequence'] == 'ACGT'
        for row in native['sequences']:
            if 'protein' in row:
                assert Path(row['protein']['msa']).is_relative_to(worker)


@pytest.mark.parametrize('failure', ['digest', 'settings', 'source', 'config', 'alignment', 'extra'])
def test_fold_cp_binding_rejects_changed_inputs(tmp_path, monkeypatch, failure):
    provider_double(monkeypatch, tmp_path)
    inv = compiled(tmp_path)
    prepared = prepare_launch_msa(inv.model_id, inv.native_parameters, tmp_path / 'out/prepared-msa')
    root = Path(prepared['bcp_input_path'])
    path = root / 'msa-inputs.json'
    manifest = json.loads(path.read_text())
    if failure == 'digest':
        prepared['boltz_prepared_msa_sha256'] = '0' * 64
    elif failure in {'settings', 'source'}:
        if failure == 'settings':
            manifest['settings']['msa_provider'] = 'colabfold_api'
        else:
            manifest['configs'][0]['source_sha256'] = '0' * 64
        path.write_text(json.dumps(manifest))
        prepared['boltz_prepared_msa_sha256'] = digest(path.read_bytes())
    elif failure == 'config':
        (root / manifest['configs'][0]['path']).write_text('sequences: []\n')
    elif failure == 'alignment':
        (root / manifest['configs'][0]['chains'][0]['alignment']['path']).write_text('>q\nXXXX\n')
    else:
        (root / 'extra.yaml').write_text('sequences: []\n')
    with pytest.raises(ValueError):
        _bind_protenix_msa_transport(inv, prepared)


@pytest.mark.parametrize('frustra', [False, True])
def test_fold_cp_preview_is_input_preparation_not_generated_design(tmp_path, monkeypatch, frustra):
    calls = provider_double(monkeypatch, tmp_path)
    inv = compiled(tmp_path, frustra=frustra, preview=True)
    assert not calls and not (tmp_path / 'out').exists()
    service, = inv.execution_plan.metadata.external_services
    assert service.state == 'planned_from_native_inputs'
    assert service.operation_identity is None
    assert not inv.execution_plan.metadata.blockers
    assert json.loads(service.settings_json)['msa_neurosnap_max_sequences'] == 1000000


def test_fold_cp_disabled_msa_keeps_native_science(tmp_path, monkeypatch):
    calls = provider_double(monkeypatch, tmp_path)
    inv = compiled(tmp_path, use_msa=False)
    service, = inv.execution_plan.metadata.external_services
    assert service.state == 'disabled'
    params = prepare_launch_msa(inv.model_id, inv.native_parameters, tmp_path / 'out/prepared-msa')
    assert params == inv.native_parameters
    assert _bind_protenix_msa_transport(inv, params) == inv
    assert not calls


def test_fold_cp_explicit_empty_and_retained_v1_consumer(tmp_path, monkeypatch):
    import yaml
    from biomodstack_boltz_msa import resolve_boltz_config
    calls = provider_double(monkeypatch, tmp_path)
    inv = compiled(tmp_path)
    source = Path(inv.native_parameters['bcp_input_path'])
    payload = yaml.safe_load(source.read_text())
    for entry in payload['sequences']:
        if 'protein' in entry:
            entry['protein']['msa'] = 'empty'
    source.write_text(yaml.safe_dump(payload))
    prepared = prepare_launch_msa(inv.model_id, inv.native_parameters, tmp_path / 'out/prepared-msa')
    bound = _bind_protenix_msa_transport(inv, prepared)
    assert not calls and bound.requested_json == inv.requested_json
    root = Path(prepared['bcp_input_path'])
    manifest_path = root / 'msa-inputs.json'
    manifest = json.loads(manifest_path.read_text())
    # The optional new controller proof does not version-gate retained native
    # consumers/checkpoints. Old v1 packages remain valid native input bundles.
    manifest.pop('settings')
    manifest_path.write_text(json.dumps(manifest))
    sha = digest(manifest_path.read_bytes())
    native = resolve_boltz_config(root / manifest['configs'][0]['path'], root=root, manifest_sha256=sha)
    assert native == payload
    # New plan publication, unlike unchanged native/resume consumption, must
    # carry producer settings proof rather than claim an unverified identity.
    with pytest.raises(ValueError, match='settings'):
        _bind_protenix_msa_transport(inv, {**prepared, 'boltz_prepared_msa_sha256': sha})


def test_fold_cp_local_search_still_refused(tmp_path):
    with pytest.raises(ValueError, match='[Ll]ocal'):
        compiled(tmp_path, provider='local')
