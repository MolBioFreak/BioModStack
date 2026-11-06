"""The submission guard stays the documented legacy no-op when no inputs root exists."""
from types import SimpleNamespace

import pytest

import paths
from services import binder_source_materialization as materialization


@pytest.fixture
def results_only(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'results': tmp_path})


def test_preparation_directories_without_inputs_root_is_empty(results_only):
    assert materialization._preparation_directories({'a': 'inputs/design_selections/protein_local_redesign/x/y.pdb'}) == []


@pytest.mark.asyncio
async def test_guard_without_inputs_root_is_noop(results_only):
    session = SimpleNamespace(sync_session=SimpleNamespace(info={}))
    async with materialization.resequence_submission_guard({'a': 'inputs/x'}, session):
        pass
    assert session.sync_session.info == {}
