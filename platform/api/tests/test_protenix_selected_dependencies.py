"""Offline native dependency metadata/projection; no inference or acquisition."""
from copy import deepcopy
from dataclasses import replace

import pytest

from component_runtime import SourceIdentity
from model_registry import model_runtime_dependencies, native_checkpoint_dependencies
from native_components import _NativeAnnotations
from services.nextflow import build_selected_execution_plan
from services.remote_execution import bundle


BASE = {
    'checkpoint/protenix-v2.pt',
    'common/components.cif',
    'common/components.cif.rdkit_mol.pkl',
    'common/clusters-by-entity-40.txt',
    'common/obsolete_release_date.csv',
}
TEMPLATE = {'common/obsolete_to_successor.json', 'common/release_date_cache.json'}
PROCESSES = ('ProtenixPredict', 'ProtenixFromComplex', 'BatchProtenixValidation',
             'CanonicalProtenixEnsemble', 'RunShapeProtenixValidator')


def science(**extra):
    return dict(pred_method='protenix', protenix_model_weights='protenix-v2',
                protenix_seeds='17,29', protenix_n_sample=7, protenix_n_step=311,
                protenix_n_cycle=13, protenix_use_msa=False,
                protenix_enable_cache=False, protenix_enable_fusion=False,
                protenix_use_template=False, **extra)


def plan_for(settings, workflow='structure_prediction'):
    return build_selected_execution_plan(model_id='protenix', mode=workflow,
        entrypoint='workflows/' + workflow + '.nf', requested=settings,
        effective=settings, native_parameters=settings,
        source_identity=SourceIdentity('a' * 40, 'b' * 40))


@pytest.mark.parametrize('process', PROCESSES)
@pytest.mark.parametrize('template,anchor', [(False, False), ('true', False), (False, True), (True, True)])
def test_native_members_and_embedded_stage_agree(process, template, anchor):
    params = science(protenix_anchor_target=anchor)
    params['protenix_use_template'] = template
    before = deepcopy(params)
    deps, blockers = native_checkpoint_dependencies(process, params)
    anchored = anchor and process in {'ProtenixFromComplex', 'BatchProtenixValidation'}
    templates = process != 'RunShapeProtenixValidator' and (bool(template) or anchored)
    expected = BASE | (TEMPLATE if templates else set()) | ({'mmcif'} if templates and not anchored else set())
    assert {d.selector_subpath for d in deps} == expected
    assert {d.relative_path for d in deps} == {'protenix/' + p for p in expected}
    assert all(d.selector == 'protenix_weights' and d.requiredness == 'required' for d in deps)
    assert not blockers
    dependencies, components = {}, []
    annotations = _NativeAnnotations(params, 'fixture', components, [], dependencies, [], [],
                                     lambda *a, **k: pytest.fail('unexpected blocker'))
    annotations.stage(process)
    assert {d.relative_path for d in dependencies.values() if d.kind == 'weights'} == {'protenix/' + p for p in expected}
    assert set(d.logical_id for d in deps) <= set(components[0].dependency_ids)
    assert params == before


def test_independent_model_keeps_provisioning_root_contract():
    # Independent acquisition binds a reviewed layout root, not a selected run.
    refs = model_runtime_dependencies('protenix')
    assert {r.relative_path for r in refs if r.kind == 'weights'} == {'protenix'}


@pytest.mark.parametrize('workflow', ['structure_prediction', 'complex_prediction'])
@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('template,anchor', [(False, False), (True, False), (False, True)])
def test_selected_plan_projects_only_required_members(tmp_path, monkeypatch, workflow, custom, template, anchor):
    root = tmp_path / 'weights'
    selected_root = root / ('custom-root' if custom else 'protenix')
    for member in BASE | TEMPLATE | {'checkpoint/protenix_base_default_v0.5.0.pt',
                                    'triton/kernel.so', 'matplotlib/fontlist.json',
                                    'common/cuequivariance-triton/kernel.bin', 'mmcif/unused.cif'}:
        path = selected_root / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(member.encode())
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: root)
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: tmp_path / 'images')
    monkeypatch.setattr(bundle, 'get_data_root', lambda: tmp_path / 'data')
    settings = science(**({'protenix_weights': str(selected_root)} if custom else {}))
    settings.update(protenix_use_template=template, protenix_anchor_target=anchor)
    anchored = anchor and workflow == 'complex_prediction'
    expected = BASE | (TEMPLATE if template or anchored else set()) | ({'mmcif'} if template and not anchored else set())
    before = deepcopy(settings)
    plan = plan_for(settings, workflow)
    assert plan.dependency_closure_complete
    original = plan.to_dict()
    assert {d['selector_subpath'] for d in original['metadata']['dependencies']
            if d['kind'] == 'weights'} == expected
    assets = bundle._runtime_assets('protenix', workflow, settings, selected_plan=plan,
                                    only_kinds=frozenset({'weights'}))
    assert {(str(p.relative_to(selected_root)), d) for p, d in assets} == {
        (member, 'weights/protenix/' + member) for member in expected}
    assert len(assets) == len(expected)
    assert plan.requested_json == plan.effective_json == plan.native_parameters_json
    physical = {leaf.relative_to(selected_root).as_posix()
                for source, _ in assets for leaf in (source.rglob('*') if source.is_dir() else [source])
                if leaf.is_file()}
    assert physical == (expected - {'mmcif'}) | ({'mmcif/unused.cif'} if 'mmcif' in expected else set())
    assert settings == before
    assert plan.to_dict() == original
    # Exact common assets are required, never replaced by an available broad root.
    (selected_root / 'common/components.cif').unlink()
    with pytest.raises(bundle.RemoteBundleError, match='Required runtime asset is unavailable'):
        bundle._runtime_assets('protenix', workflow, settings, selected_plan=plan,
                               only_kinds=frozenset({'weights'}))


@pytest.mark.parametrize('member', ['', '.', '../outside', '/outside', 'common/../../outside', 'common\\outside'])
def test_selector_projection_rejects_unsafe_member(tmp_path, monkeypatch, member):
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: tmp_path)
    settings = science(protenix_weights=str(tmp_path))
    plan = plan_for(settings)
    dependency = next(d for d in plan.dependencies if d.kind == 'weights')
    bad = replace(dependency, selector_subpath=member)
    plan = replace(plan, metadata=replace(plan.metadata, dependencies=(bad,)))
    with pytest.raises(bundle.RemoteBundleError, match='invalid selector member'):
        bundle._runtime_assets('protenix', 'structure_prediction', settings,
                               selected_plan=plan, only_kinds=frozenset({'weights'}))


@pytest.mark.parametrize('templates', [False, True])
def test_cm_request_policy_selects_dependencies_before_native_stage(templates):
    settings = {'cm_request': {'backend': 'protenix_v2_ensemble',
        'feature_policy': {'mode': 'regenerate_mutated_protein_v1',
                           'protein_msa_enabled': False, 'templates_enabled': templates,
                           'rna_msa_enabled': False}}, 'protenix_use_template': not templates}
    before = deepcopy(settings)
    plan = build_selected_execution_plan(model_id='conformational_mapping', mode='map',
        entrypoint='workflows/conformational_mapping.nf', requested=settings,
        effective=settings, native_parameters=settings,
        source_identity=SourceIdentity('a' * 40, 'b' * 40))
    expected = BASE | (TEMPLATE | {'mmcif'} if templates else set())
    assert {d.selector_subpath for d in plan.dependencies if d.kind == 'weights'} == expected
    assert settings == before


def test_selector_projection_cannot_follow_member_outside_selected_root(tmp_path, monkeypatch):
    selected = tmp_path / 'selected'
    selected.mkdir()
    (selected / 'checkpoint').symlink_to(tmp_path / 'outside')
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: tmp_path)
    settings = science(protenix_weights=str(selected))
    plan = plan_for(settings)
    checkpoint = next(d for d in plan.dependencies if d.selector_subpath == 'checkpoint/protenix-v2.pt')
    plan = replace(plan, metadata=replace(plan.metadata, dependencies=(checkpoint,)))
    with pytest.raises(bundle.RemoteBundleError, match='escapes selector root'):
        bundle._runtime_assets('protenix', 'structure_prediction', settings,
                               selected_plan=plan, only_kinds=frozenset({'weights'}))
