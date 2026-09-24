"""Selected binder asset closure and portable maturation-child inputs."""
import json
from pathlib import Path
from types import SimpleNamespace

from model_registry import selected_execution_metadata
from services.remote_execution import bundle
from scripts.lib.portable_inputs import discover_native_input_references


def test_bc2_campaign_declares_only_native_image_and_external_af2_params():
    metadata = selected_execution_metadata('bindcraft2', 'campaign', {}, 'workflows/bindcraft2.nf')
    assert {row.logical_id for row in metadata.dependencies if row.kind in {'image', 'weights'}} == {
        'image:bindcraft2.sif', 'weights:alphafold/params'}
    assert [row.component_key for row in metadata.static_components] == ['RunBindCraft2']
    assert not any(row.field == 'availability' for row in metadata.blockers)


def test_bc2_mask_adapter_is_in_both_selected_runtime_closures():
    helper = 'support_tool:scripts/bindcraft2_native_adapter/sitecustomize.py'
    for mode, component in [('campaign', 'RunBindCraft2'), ('score', 'PostprocessBindCraft2')]:
        metadata = selected_execution_metadata('bindcraft2', mode, {}, 'workflows/bindcraft2.nf')
        assert helper in {row.logical_id for row in metadata.dependencies}
        native = next(row for row in metadata.static_components if row.component_key == component)
        assert helper in native.dependency_ids


def test_maturation_selected_redesign_dependencies():
    def assets(redesign):
        metadata = selected_execution_metadata('template_antibody_denovo', 'maturation_child',
            {'maturation_redesign_enabled': redesign, 'ppiflow_mode': 'maturation'},
            'workflows/maturation_child.nf')
        return ({row.logical_id for row in metadata.dependencies if row.kind in {'image', 'weights'}},
                {row.component_key for row in metadata.static_components})
    without, stages = assets(False)
    with_redesign, redesigned_stages = assets(True)
    assert 'RunMaturationFAMPNN' not in stages
    assert 'RunMaturationFAMPNN' in redesigned_stages
    assert without == {'image:ppiflow.sif', 'image:pyrosetta_tools.sif', 'weights:ppiflow'}
    assert without < with_redesign
    default = selected_execution_metadata('template_antibody_denovo', 'maturation_child', {},
        'workflows/maturation_child.nf')
    assert 'RunMaturationFAMPNN' not in {row.component_key for row in default.static_components}
    refine = selected_execution_metadata('template_antibody_denovo', 'maturation_child',
        {'maturation_redesign_enabled': True, 'ppiflow_mode': 'backbone_refine'},
        'workflows/maturation_child.nf')
    assert 'RunMaturationFAMPNN' not in {row.component_key for row in refine.static_components}


def test_maturation_manifest_and_each_native_pdb_are_portable(tmp_path, monkeypatch):
    root = tmp_path / 'results'
    root.mkdir()
    first, second = root / 'source_000000.pdb', root / 'source_000001.pdb'
    first.write_text('ATOM 1\n')
    second.write_text('ATOM 2\n')
    manifest = root / 'source_identity.json'
    manifest.write_text(json.dumps([{'staged_name': first.name, 'source_path': '/historical/source.pdb',
                                     'source_meta': {'id': 'one'}}]))
    params = {'pdb_paths': f'{first},{second}', 'source_identity_json': str(manifest)}
    records = discover_native_input_references('template_antibody_denovo', 'maturation_child',
        params, (), output_dir=root, allowed_roots=(root,))
    assert {row['source_path'] for row in records} == {str(first), str(second), str(manifest)}
    monkeypatch.setattr(bundle, 'get_data_root', lambda: root)
    monkeypatch.setattr(bundle, 'get_inputs_dir', lambda: root)
    monkeypatch.setattr(bundle, 'get_results_dir', lambda: root)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: tmp_path / 'weights')
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: tmp_path / 'containers')
    selected = bundle._input_assets(params, native_invocation=SimpleNamespace(
        model_id='template_antibody_denovo', mode='maturation_child', generated_inputs=()),
        repo_root=tmp_path / 'source', runtime_paths=set(), output_dir=root / 'child')
    assert {path for path, _ in selected} == {first, second, manifest}
    mapping = {str(path): '/worker/inputs/' + path.name for path in (first, second, manifest)}
    assert bundle._rewrite_maturation_pdb_paths(params['pdb_paths'], mapping) == (
        '/worker/inputs/source_000000.pdb,/worker/inputs/source_000001.pdb')
    assert bundle._rewrite(params['source_identity_json'], mapping) == '/worker/inputs/source_identity.json'
    unrelated = bundle._input_assets({'pdb_paths': str(first)}, native_invocation=SimpleNamespace(  # type: ignore[arg-type]
        model_id='antibody_denovo', mode='design', generated_inputs=()),
        repo_root=tmp_path / 'source', runtime_paths=set(), output_dir=root / 'other')
    assert {path for path, _ in unrelated} == {first}
