"""RFD4 refolding-oracle assets: declared acquisition, optional selection, layout resolution.

Offline: no network, no inference. Digests are the pinned Hugging Face artifacts of
nvidia/RFD4-Proteina at the generator revision.
"""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import model_registry as mr
from services import rfd4_proteina as service

OF3_WEIGHTS = '7ae00e49f8507593d6a8c5a7c0389e2151fea9a79062069979c42ebb5bbf14d2'
OF3_PROVENANCE = 'b4e437d93b33c8b736c43c0238e9419c011f1890aafae6bbffc2c77b8a7cf416'
AF3_IMAGE = '7bbd35478ed5e95131efa7756d03a2e754f3508e603ad08bb8f8db0bd89484a1'
AF2_IMAGE = 'b5160d179a0f11608e176f8883c8e9c644ab5c6a6b92bf05c2ca5b7a5ecb8d3f'


def of3_params():
    return {'rfd4_request': {
        'version': 'rfd4_request_v1', 'mode': 'evaluate',
        'input': {'kind': 'annotated', 'slots': ['sample']},
        'oracle_parameter_family': 'of3',
        'providers': {'refolding_oracles': {'profile': 'of3', 'overrides': {}}},
        'settings': {'metrics_tags': ['unconditional_refolding']}}}


def test_oracle_artifacts_are_declared_with_pinned_digests():
    entries = {e.artifact_id: e for e in mr.get_registry().get_model('rfd4_proteina').acquisition}
    by_sha = {e.sha256: e for e in entries.values()}
    assert by_sha[AF3_IMAGE].dependency.kind == 'image'
    assert by_sha[AF3_IMAGE].dependency.relative_path == 'af3-open.sif'
    assert by_sha[AF3_IMAGE].license_id is None and by_sha[AF3_IMAGE].size_bytes == 3502800896
    assert by_sha[AF2_IMAGE].dependency.relative_path == 'af2-open.sif'
    for sha, member in ((OF3_WEIGHTS, 'of3_ported_weights.bin.zst'), (OF3_PROVENANCE, 'of3_ported_weights.provenance.json')):
        entry = by_sha[sha]
        assert (entry.dependency.kind, entry.dependency.relative_path) == ('weights', 'rfd4-oracles/of3')
        assert entry.member_path == member and entry.license_id == 'Apache-2.0'


def test_every_declared_artifact_validates_for_its_kind():
    from scripts.lib.pinned_acquisition import Artifact
    for entry in mr.get_registry().get_model('rfd4_proteina').acquisition:
        Artifact(**entry.model_dump(exclude={'dependency', 'member_path'}), kind=entry.dependency.kind).validate()


def test_oracle_assets_are_optional_selected_not_default_closure():
    # The model's own plan/provision set is unchanged: generator image + weights only.
    default = mr.model_acquisition_plan('rfd4_proteina')
    assert default['blockers'] == []
    shas = {a['manifest']['sha256'] for a in default['artifacts'] if 'manifest' in a}
    assert not shas & {AF3_IMAGE, AF2_IMAGE, OF3_WEIGHTS, OF3_PROVENANCE}
    # Selected-layout receivers see the full declared authority.
    full = mr.model_acquisition_plan('rfd4_proteina', include_optional=True)
    assert full['blockers'] == []
    full_shas = {a['manifest']['sha256'] for a in full['artifacts'] if 'manifest' in a}
    assert {AF3_IMAGE, AF2_IMAGE, OF3_WEIGHTS, OF3_PROVENANCE} <= full_shas
    assert not {d.relative_path for d in mr.model_runtime_dependencies('rfd4_proteina')} & {
        'af3-open.sif', 'af2-open.sif', 'rfd4-oracles/of3'}


def test_request_without_oracle_tags_selects_no_oracle_assets():
    base = {'rfd4_request': {'version': 'rfd4_request_v1', 'mode': 'evaluate',
                             'input': {'kind': 'annotated', 'slots': ['sample']}}}
    assert tuple(service.selected_oracle_assets('evaluate', base)) == ()


def test_of3_refolding_request_selects_image_and_ported_weights():
    rows = service.selected_oracle_assets('evaluate', of3_params())
    assert {(r['kind'], r['relative_path'], r['environment']) for r in rows} == {
        ('image', 'af3-open.sif', 'AF3_IMAGE'), ('weights', 'rfd4-oracles/of3', 'AF3_MODEL_DIR')}


def test_nested_weight_layout_root_is_accepted_and_traversal_is_not():
    from scripts.lib.pinned_acquisition import AcquisitionError
    from scripts.lib.pinned_weight_layout import validate_members
    entry = {'member_path': 'a.bin', 'manifest': dict(
        artifact_id='x', kind='weights', url='https://huggingface.co/x', sha256='0' * 64, size_bytes=1,
        source_authority='huggingface.co', approval_ref='r', license_id='Apache-2.0')}
    assert validate_members({'kind': 'weights', 'relative_path': 'rfd4-oracles/of3'}, [entry]) == {'a.bin'}
    for bad in ('../of3', 'rfd4-oracles/../of3', '/abs', 'a//b', '.hidden/x', ''):
        with pytest.raises(AcquisitionError):
            validate_members({'kind': 'weights', 'relative_path': bad}, [entry])


def test_controller_runtime_follows_the_single_layout_generation(tmp_path):
    generation = tmp_path / 'weights' / 'layouts' / 'rfd4-oracles' / 'of3' / ('a' * 64)
    generation.mkdir(parents=True)
    (generation / 'of3_ported_weights.provenance.json').write_text('{}')
    from paths import resolve_weight_dependency
    assert resolve_weight_dependency('rfd4-oracles/of3', tmp_path / 'weights') == generation
    # resolve_oracle_runtime binds the generation, not a plain rfd4-oracles/of3 directory.
    import paths
    plain = tmp_path / 'weights' / 'rfd4-oracles' / 'of3'
    assert not plain.exists()
    rows = service.selected_oracle_assets('evaluate', of3_params())
    weights = next(r for r in rows if r['kind'] == 'weights')
    assert resolve_weight_dependency(weights['relative_path'], tmp_path / 'weights') != plain
