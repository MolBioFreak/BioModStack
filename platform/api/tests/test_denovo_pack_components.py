"""De novo design preparation can send a chosen subset of models to a worker.

The components are preparation leaves only: they pick which images and weights are sent, never inference
settings, and the omitted/null selection stays the whole pack.
"""
from itertools import combinations

import pytest

import model_registry
from services.remote_execution import cache
from services.remote_execution.contracts import WorkflowPackSelection

WORKFLOW = 'de_novo_design'


def refs(ids=None):
    return model_registry.workflow_pack_dependencies(WORKFLOW, ids)


def keys(deps):
    return {(item.kind, item.relative_path) for item in deps}


def test_components_are_published_with_the_usual_loop_selected_by_default():
    components = model_registry.workflow_pack_components(WORKFLOW)
    ids = [item['model_id'] for item in components]
    assert len(ids) == len(set(ids))
    assert {item['model_id'] for item in components if item['selected_by_default']} == {
        'rfd4_proteina', 'proteinmpnn', 'fampnn', 'frustrampnn', 'protenix'}
    assert all(item['label'] for item in components)


def test_all_components_together_are_exactly_the_whole_pack():
    ids = [item['model_id'] for item in model_registry.workflow_pack_components(WORKFLOW)]
    assert keys(refs(ids)) == keys(refs())
    assert len(refs(ids)) == len(keys(refs(ids)))


def test_every_component_is_a_subset_of_the_pack_and_selections_union_exactly():
    ids = [item['model_id'] for item in model_registry.workflow_pack_components(WORKFLOW)]
    whole = keys(refs())
    single = {name: keys(refs([name])) for name in ids}
    assert all(single[name] and single[name] <= whole for name in ids)
    for pair in combinations(ids, 2):
        assert keys(refs(pair)) == single[pair[0]] | single[pair[1]]
    assert keys(refs([])) == set()


def test_default_selection_leaves_out_the_heavy_models_it_does_not_need():
    default = [item['model_id'] for item in model_registry.workflow_pack_components(WORKFLOW)
               if item['selected_by_default']]
    chosen = {path for kind, path in keys(refs(default))}
    for omitted in ('laproteina.sif', 'complexa.sif', 'disco.sif', 'esmfold2.sif', 'boltz2.sif', 'foundry.sif',
                    'laproteina', 'esmfold2', 'boltz'):
        assert omitted not in chosen
    assert {'protenix.sif', 'frustrampnn.sif', 'fampnn.sif', 'dl_binder_design.sif', 'rfd4_proteina.sif'} <= chosen


def test_shared_dependencies_are_counted_once():
    both = refs(['proteinmpnn', 'fampnn'])
    assert [r.relative_path for r in both].count('pyrosetta_tools.sif') == 1


def test_unknown_and_empty_selections():
    with pytest.raises(ValueError, match='Unknown workflow'):
        WorkflowPackSelection(kind='workflow_pack', workflow_id=WORKFLOW, component_ids=['made_up'])
    assert WorkflowPackSelection(kind='workflow_pack', workflow_id=WORKFLOW, component_ids=[]).component_ids == []
    assert WorkflowPackSelection(kind='workflow_pack', workflow_id=WORKFLOW).component_ids is None


def test_a_subset_never_publishes_a_partial_combined_weight_layout():
    selection = WorkflowPackSelection(kind='workflow_pack', workflow_id=WORKFLOW,
                                      component_ids=['rfd4_proteina', 'protenix'])
    selected = {ref.relative_path for ref in refs(selection.component_ids) if ref.kind == 'weights'}
    layouts = cache.workflow_pack_weight_layouts(selection, [])
    full_groups = model_registry.workflow_pack_weight_groups(WORKFLOW)
    assert set(layouts) == {name for name, members in full_groups.items() if set(members) <= selected}
    assert 'generators' not in layouts
    assert {'rfd4_proteina', 'protenix'} <= set(layouts)
