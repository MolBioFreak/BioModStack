"""Native-lane pure API binding regression; no scientific runtime import."""
import json
from pathlib import Path
import pytest
from services.rfd4_proteina import compile_request, prepare_for_job


def test_native_complete_complexa_discovery_uses_canonical_api_environment():
    from services.complexa import discovery, normalize_job_request
    from scripts.lib.complexa_contract import TASKS
    view = discovery()
    assert set(view['profiles']) == set(TASKS)
    assert view['native_profile_defaults']['tasks']['AME']['reward_model'] is None
    assert view['schema']['additionalProperties'] is False
    with pytest.raises(ValueError):
        normalize_job_request('evaluate', {'task': 'AME', 'task_name': 'M0024_1nzy_v3'})


def test_native_complete_strict_bounds_survive_api_compile_and_retained_payload(tmp_path):
    bounds = {'min': None, 'max': None, 'min_exclusive': 0, 'max_exclusive': 1.5}
    request = {'input': {'kind': 'unconditional', 'name': 'sample',
                        'chains': [{'chain_id': 'A', 'min_length': 12, 'max_length': 19}]},
               'providers': {'pass_criteria': {'profile': 'all_pass_criteria',
                   'overrides': {'ame': {'metric_thresholds': {'score': bounds}}}}}}
    params = {'rfd4_request': request}
    compiled = compile_request('generate', params)
    assert compiled['native_overlay']['metrics']['pass_criteria']['ame']['metric_thresholds']['score'] == bounds
    prepared = prepare_for_job('generate', params, tmp_path, resolved_sources={}, allowed_roots=[tmp_path])
    payload = json.loads(Path(prepared['rfd4_prepared_request']).read_text())
    assert payload['request'] == compiled['request']
    assert payload['request_identity'] == compiled['request_identity']
