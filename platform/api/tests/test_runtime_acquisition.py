"""Production registry stays fail closed; test bytes never enter model YAML."""
from pathlib import Path

import pytest

from model_registry import get_registry, model_acquisition_plan
from services.runtime_acquisition import AcquisitionError, acquire_model, preview_model_acquisition


def test_no_production_acquisition_approvals_are_invented(tmp_path):
    registry = get_registry()
    assert registry._models
    for model_id, model in registry._models.items():
        assert model.acquisition == []
        plan = preview_model_acquisition(model_id)
        assert plan['blockers']
        assert plan['artifacts'] == []
        with pytest.raises(AcquisitionError):
            acquire_model(model_id, tmp_path, expected_plan_digest=plan['plan_digest'])
    assert list(tmp_path.iterdir()) == []


def test_known_artifact_blockers_and_plan_binding(tmp_path):
    plan = model_acquisition_plan('protenix')
    assert plan['blockers'] == [
        {'code': 'approved_acquisition_metadata_missing', 'kind': 'image', 'relative_path': 'protenix.sif'},
        {'code': 'approved_acquisition_metadata_missing', 'kind': 'weights', 'relative_path': 'protenix'},
    ]
    with pytest.raises(AcquisitionError, match='plan changed'):
        acquire_model('protenix', tmp_path, expected_plan_digest='unreviewed')
    assert list(tmp_path.iterdir()) == []
