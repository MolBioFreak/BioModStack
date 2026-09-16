"""Registry schema/plan contract only; invented metadata is TEST-ONLY, never approval."""
from types import SimpleNamespace

import pytest

import model_registry
from model_registry import RuntimeAcquisitionArtifact
from services import runtime_acquisition as service


@pytest.fixture
def registry_members(monkeypatch):
    model = model_registry.get_registry().get_model('esmfold2')
    assert model is not None
    refs = model_registry.model_runtime_dependencies('esmfold2')
    entries = []
    for n, (ref, member) in enumerate([(refs[0], None), (refs[1], 'config.json'),
                                      (refs[1], 'nested/model.bin')]):
        url = f'https://test-only.invalid/pinned-revision/member-{n}'
        entries.append(RuntimeAcquisitionArtifact(
            artifact_id=f'test-only-{n}', dependency=ref, url=url,
            sha256='a' * 64, size_bytes=1, source_authority='test-only.invalid',
            approval_ref='TEST-ONLY-NOT-APPROVAL',
            license_id='TEST-ONLY-LICENSE' if member else None, member_path=member,
            redirect_policy={'source_url': url, 'approval_ref': 'TEST-ONLY',
                             'allowed_authorities': ['cdn.test-only.invalid'], 'max_hops': 2}))
    fake = model.model_copy(update={'acquisition': entries})
    monkeypatch.setattr(model_registry, 'get_registry', lambda: SimpleNamespace(get_model=lambda _: fake))
    return fake


def test_member_metadata_bound_to_plan_and_licenses(registry_members, tmp_path):
    plan = service.preview_model_acquisition('esmfold2')
    assert not plan['blockers']
    assert [e['member_path'] for e in plan['artifacts']] == [None, 'config.json', 'nested/model.bin']
    assert 'member_path' not in plan['artifacts'][1]['manifest']
    with pytest.raises(service.AcquisitionError, match='license acceptance'):
        service.acquire_model('esmfold2', tmp_path, expected_plan_digest=plan['plan_digest'])
    assert not list(tmp_path.iterdir())
    registry_members.acquisition[1] = registry_members.acquisition[1].model_copy(
        update={'member_path': 'other.json'})
    assert service.preview_model_acquisition('esmfold2')['plan_digest'] != plan['plan_digest']


def test_incomplete_or_unsafe_layout_fails_preview(registry_members):
    registry_members.acquisition[1] = registry_members.acquisition[1].model_copy(
        update={'member_path': '../unsafe'})
    assert any(b['code'] == 'invalid_weight_layout'
               for b in service.preview_model_acquisition('esmfold2')['blockers'])


def test_setup_dispatches_grouped_layout_contract(registry_members, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(service, 'acquire', lambda artifact, *args, **kwargs:
                        {'artifact_id': artifact.artifact_id})
    def capture(dependency, members, root, **kwargs):
        calls.append((dependency, members, kwargs))
        return {'qualification': 'not_checked'}
    monkeypatch.setattr(service, 'materialize_weights', capture)
    plan = service.preview_model_acquisition('esmfold2')
    receipt = service.acquire_model('esmfold2', tmp_path,
        expected_plan_digest=plan['plan_digest'], accepted_licenses=['TEST-ONLY-LICENSE'])
    assert len(calls) == 1
    assert calls[0][0] == {'kind': 'weights', 'relative_path': 'esmfold2'}
    assert len(calls[0][1]) == 2
    assert calls[0][2]['accepted_licenses'] == frozenset({'TEST-ONLY-LICENSE'})
    assert receipt['layouts'] == [{'qualification': 'not_checked'}]
    assert receipt['qualification'] == 'not_checked'
