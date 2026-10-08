"""Focused execution-provenance regressions; isolated synthetic identities."""
import subprocess

import pytest

from services import ngs_molbio_n5 as admission
from services import ngs_molbio_runtime_status as release_audit
from services import ngs_molbio_source_authority as source


def test_admission_uses_deployed_identity_without_release_material(monkeypatch):
    monkeypatch.setenv('BMS_BUILD_SHA', 'a' * 40)
    monkeypatch.setenv('BMS_BUILD_TREE', 'b' * 40)
    def forbidden(*args, **kwargs):
        raise AssertionError('Ordinary admission must not read release evidence or Git')
    monkeypatch.setattr(release_audit, 'runtime_implementation_record', forbidden)
    monkeypatch.setattr(source, '_checkout_tree', forbidden)
    assert admission._runtime_source_authority([]) == ('a' * 40, 'b' * 40)
    assert source.source_build_revision() == 'a' * 40


@pytest.mark.parametrize('revision,tree', [('unknown', 'b' * 40), ('a' * 40, 'unknown')])
def test_missing_real_build_identity_remains_unavailable(monkeypatch, revision, tree):
    monkeypatch.setenv('BMS_BUILD_SHA', revision)
    monkeypatch.setenv('BMS_BUILD_TREE', tree)
    with pytest.raises(admission.ResourceAdmissionDenied, match='deployed build') as raised:
        admission._runtime_source_authority([])
    assert raised.value.code == 'resource_source_revision_unavailable'


def test_old_development_uses_one_exact_git_object_lookup(monkeypatch):
    monkeypatch.setenv('BMS_BUILD_SHA', 'c' * 40)
    monkeypatch.delenv('BMS_BUILD_TREE', raising=False)
    source._checkout_tree.cache_clear()
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout='d' * 40 + '\n')
    monkeypatch.setattr(source.subprocess, 'run', run)
    try:
        assert source.source_build_identity() == ('c' * 40, 'd' * 40)
        assert source.source_build_identity() == ('c' * 40, 'd' * 40)
        assert len(calls) == 1
        assert calls[0][0][-1] == 'c' * 40 + '^{tree}'
        assert calls[0][1]['timeout'] == 5
    finally:
        source._checkout_tree.cache_clear()
