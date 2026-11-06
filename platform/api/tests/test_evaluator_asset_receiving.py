"""Selected evaluator custody, not inference or model-wide optional acquisition."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_selected_mpnn_storage_is_separate_from_generator_layouts(tmp_path):
    from model_registry import native_checkpoint_dependencies
    request = dict(task='protein_binder', task_name='receiving', operation='evaluate',
        reward=None, evaluation_slots=['retained'], evaluation={
            'compute_binder_metrics': False, 'compute_esm_metrics': False,
            'compute_designability': True, 'compute_codesignability': False})
    refs, unresolved = native_checkpoint_dependencies('RunComplexa', {'complexa_request': request})
    assert not unresolved
    paths = {r.relative_path for r in refs}
    assert 'complexa-evaluators/ProteinMPNN/ca_model_weights/v_48_020.pt' in paths
    assert 'complexa/complexa.ckpt' in paths
    assert 'complexa/complexa_ae.ckpt' in paths
    assert not any('vanilla_model_weights' in p for p in paths)
    assert not any(p.startswith('complexa/ProteinMPNN') for p in paths)
    (tmp_path / 'request.json').write_text(json.dumps({'config_path': 'native.yaml'}))
    (tmp_path / 'native.yaml').write_text('generation:\n  metric:\n    compute_designability: true\n    compute_codesignability: false\n')
    refs, unresolved = native_checkpoint_dependencies('EvaluateLaProteina', {
        'laproteina_evaluation_input_closure': str(tmp_path),
        'laproteina_evaluation_request': {'metrics': {}}})
    assert not unresolved
    assert {r.relative_path for r in refs} == {
        'laproteina-evaluators/ProteinMPNN/vanilla_model_weights/v_48_020.pt',
        'huggingface/models--facebook--esmfold_v1'}


def test_selected_evaluator_layouts_reproduce_pinned_acquisition_identity():
    from model_registry import evaluator_acquisition_layouts
    from scripts.lib.pinned_acquisition import Artifact
    from test_layout_weight_acquisition import _digest as layout_digest
    sources = json.loads((ROOT / 'scripts/bms_complexa/assets.json').read_text())['selected_oracle_sources']
    expected = {x['relative_path']: x for x in sources['controller_acquisition']['layouts'] if x['relative_path'] != 'foundry'}
    layouts = evaluator_acquisition_layouts()
    assert {dependency['relative_path'] for dependency, _ in layouts} == set(expected)
    for dependency, members in layouts:
        spec = expected[dependency['relative_path']]
        assert {m['member_path'] for m in members} == set(spec['members'])
        assert layout_digest(dependency, members) == spec['digest']
        for member in members:
            Artifact(**member['manifest']).validate()
    # Static default preparation must not become an all-optional-oracle download.
    from model_registry import model_acquisition_plan
    paths = {x['dependency']['relative_path'] for x in model_acquisition_plan('complexa')['artifacts']}
    assert not paths.intersection(expected)


@pytest.mark.parametrize('selection_model', [None, 'complexa', 'protein_modification_experimental'])
def test_selected_sidecar_reaches_worker_without_generator_alias_or_payload_copy(tmp_path, monkeypatch, selection_model):
    import model_registry
    import paths
    from services.remote_execution import cache, bundle
    from test_layout_weight_acquisition import _digest
    import os

    weights = tmp_path / 'weights'
    # Explicit inert immutable-store fixtures, following the existing receiving
    # fixture owner. No downloader, model load or scientific result is simulated.
    manifest_members = []
    bodies = [('ProteinMPNN/ca_model_weights/v_48_020.pt', b'INERT selected CA fixture'),
              ('LigandMPNN/model_params/unselected.pt', b'INERT unselected companion')]
    for name, body in bodies:
        digest = hashlib.sha256(body).hexdigest()
        obj = weights / 'objects/sha256' / digest / 'runtime.sif'
        obj.parent.mkdir(parents=True)
        obj.write_bytes(body)
        obj.chmod(0o400)
        obj.parent.chmod(0o500)
        manifest_members.append({'member_path': name, 'manifest': {
            'sha256': digest, 'size_bytes': len(body)}})
    dependency = {'kind': 'weights', 'relative_path': 'complexa-evaluators'}
    layout_root = weights / 'layouts/complexa-evaluators' / _digest(dependency, manifest_members)
    for member in manifest_members:
        native = layout_root / member['member_path']
        native.parent.mkdir(parents=True, exist_ok=True)
        obj = weights / 'objects/sha256' / member['manifest']['sha256'] / 'runtime.sif'
        native.symlink_to(os.path.relpath(obj, native.parent))
    for directory in sorted((p for p in layout_root.rglob('*') if p.is_dir()), reverse=True):
        directory.chmod(0o500)
    layout_root.chmod(0o500)
    monkeypatch.setattr(model_registry, 'evaluator_acquisition_layouts', lambda: [(dependency, manifest_members)], raising=False)
    monkeypatch.setattr(model_registry, 'get_registry', lambda: SimpleNamespace(list_models=lambda: []))
    monkeypatch.setattr(model_registry, 'model_acquisition_plan', lambda _: {'artifacts': []})
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: weights)
    selected = 'complexa-evaluators/ProteinMPNN/ca_model_weights/v_48_020.pt'
    selection = (SimpleNamespace(kind='model', model_id=selection_model) if selection_model else
                 SimpleNamespace(kind='workflow_pack', workflow_id='de_novo_design'))
    plans = cache.independent_plan(selection, dependencies=[SimpleNamespace(kind='weights', relative_path=selected)], publication={})
    assert len(plans) == 1
    item = plans[0]
    assert item.remote_destination == 'weights/' + selected
    native = layout_root / manifest_members[0]['member_path']
    assert item.source == native.resolve(strict=True)
    assert item.sha256 == manifest_members[0]['manifest']['sha256']
    assert item.source.is_file() and not item.source.is_symlink()
    # Existing generator root and unselected companion are neither invented nor sent.
    assert not (weights / 'complexa').exists()
    assert not any('unselected' in x.remote_destination for x in plans)
    from tools.bms_artifact_cache import Cache
    worker = Cache(tmp_path / 'worker-cache')
    record = {'sha256': item.sha256, 'size_bytes': item.size_bytes}
    incoming = Path(worker.root) / 'incoming' / item.sha256
    incoming.write_bytes(item.source.read_bytes())
    worker.ingest(record, incoming)
    installed = worker.weights([{**record, 'name': selected, 'mode': 0o444}], install=True)
    received = Path(installed['root']) / selected
    assert received.read_bytes() == bodies[0][1]
    assert not (Path(installed['root']) / 'complexa').exists()
    # Neither a same-byte foreign link nor an unpinned generation is authorized.
    native.parent.chmod(0o700)
    native.unlink()
    alien = tmp_path / 'alien'
    alien.write_bytes(bodies[0][1])
    alien.chmod(0o400)
    native.symlink_to(alien)
    native.parent.chmod(0o500)
    from scripts.lib.pinned_acquisition import AcquisitionError
    with pytest.raises(AcquisitionError, match='selected acquisition object'):
        cache.independent_plan(selection, dependencies=[SimpleNamespace(kind='weights', relative_path=selected)], publication={})


def test_evaluator_native_environment_binds_the_same_selected_storage():
    text = (ROOT / 'nextflow.config').read_text()
    assert '--env COMPLEXA_PROTEINMPNN_CA_WEIGHTS=${params.weights_root}/complexa-evaluators/ProteinMPNN/ca_model_weights' in text
    assert '--env COMPLEXA_LIGANDMPNN_WEIGHTS=${params.weights_root}/complexa-evaluators/LigandMPNN/model_params' in text
    assert '--env LAPROTEINA_PROTEINMPNN_WEIGHTS=${params.weights_root}/laproteina-evaluators/ProteinMPNN/vanilla_model_weights' in text
