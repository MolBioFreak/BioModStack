"""Dispatch-time runtime asset binding for selector-less weights held only in pinned layouts."""
from pathlib import Path

import pytest

from component_runtime import SelectedExecutionPlan, SourceIdentity, canonical_bytes
from model_registry import selected_execution_metadata
from services.remote_execution import bundle

CA = 'complexa-evaluators/ProteinMPNN/ca_model_weights/v_48_020.pt'
ESM = 'huggingface/models--facebook--esmfold_v1'
GEN = 'a' * 64
REQUEST = {'task': 'monomer_motif_indexed', 'task_name': '1PRW_AA', 'operation': 'design',
           'nsamples': 1, 'min_length': 60, 'max_length': 105,
           'source': {'document_id': 'motif', 'atom_selection_mode': 'all_atom', 'motif_only': True,
                      'contig_string': '5-20/A1-20/10-25/B1-20/5-20', 'segment_order': 'A;B'}}


def leaf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'offline asset fixture')
    return path


@pytest.fixture
def weights(tmp_path, monkeypatch):
    import paths
    roots = {name: tmp_path / name for name in ('containers', 'weights', 'data')}
    for root in roots.values():
        root.mkdir()
    for module in (paths, bundle):
        monkeypatch.setattr(module, 'get_container_dir', lambda: roots['containers'])
        monkeypatch.setattr(module, 'get_weights_root', lambda: roots['weights'])
        monkeypatch.setattr(module, 'get_data_root', lambda: roots['data'])
    return roots['weights']


def selected_weights(params):
    entrypoint = 'workflows/complexa.nf'
    metadata = selected_execution_metadata('complexa', 'generate', params, entrypoint)
    plan = SelectedExecutionPlan(SourceIdentity('a' * 40, 'b' * 40), 'complexa', 'complexa', 'generate',
        entrypoint, b'{}', b'{}', canonical_bytes(params), metadata)
    return plan, bundle._runtime_assets('complexa', 'generate', params, selected_plan=plan,
        only_kinds=frozenset({'weights'}))


def test_selectorless_weights_bind_through_their_single_pinned_layout(weights, tmp_path):
    params = {'complexa_request': REQUEST, 'complexa_weights_dir': str(weights / 'complexa')}
    leaf(weights / 'complexa/complexa_ame.ckpt')
    leaf(weights / 'complexa/complexa_ame_ae.ckpt')
    ca = leaf(weights / 'layouts' / CA.split('/')[0] / GEN / CA.split('/', 1)[1])
    esm = leaf(weights / 'layouts/huggingface' / GEN / 'models--facebook--esmfold_v1/snapshots/x/config.json')
    plan, assets = selected_weights(params)
    assert {d.relative_path for d in plan.dependencies if d.kind == 'weights'} >= {CA, ESM}
    bound = dict((name, path) for path, name in assets)
    # Native logical destination is unchanged; only the controller source is the layout member.
    assert bound['weights/' + CA] == ca
    assert bound['weights/' + ESM] == weights / 'layouts/huggingface' / GEN / 'models--facebook--esmfold_v1'
    assert esm.parent.parent.parent in [bound['weights/' + ESM]] or esm.is_relative_to(bound['weights/' + ESM])


def test_plain_weights_path_keeps_precedence_over_a_layout(weights):
    params = {'complexa_request': REQUEST, 'complexa_weights_dir': str(weights / 'complexa')}
    leaf(weights / 'complexa/complexa_ame.ckpt')
    leaf(weights / 'complexa/complexa_ame_ae.ckpt')
    plain = leaf(weights / CA)
    leaf(weights / 'layouts' / CA.split('/')[0] / GEN / CA.split('/', 1)[1])
    leaf(weights / 'huggingface/models--facebook--esmfold_v1/config.json')
    _, assets = selected_weights(params)
    assert dict((name, path) for path, name in assets)['weights/' + CA] == plain


def test_ambiguous_or_missing_layout_still_reports_the_asset_unavailable(weights):
    params = {'complexa_request': REQUEST, 'complexa_weights_dir': str(weights / 'complexa')}
    leaf(weights / 'complexa/complexa_ame.ckpt')
    leaf(weights / 'complexa/complexa_ame_ae.ckpt')
    leaf(weights / 'huggingface/models--facebook--esmfold_v1/config.json')
    for generation in ('b' * 64, 'c' * 64):  # two generations: selection is never guessed
        leaf(weights / 'layouts/complexa-evaluators' / generation / 'ProteinMPNN/ca_model_weights/v_48_020.pt')
    with pytest.raises(bundle.RemoteBundleError, match='Required runtime asset is unavailable'):
        selected_weights(params)
