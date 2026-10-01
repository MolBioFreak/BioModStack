"""Literal native-profile resource projection; no scientific execution."""
import json
from types import SimpleNamespace

import pytest

from component_runtime import SourceIdentity
from native_profile_resources import named_profile_overrides
from paths import get_code_root
from services.nextflow import build_selected_execution_plan
from services.remote_execution.targets import selected_plan_target_resources


@pytest.mark.parametrize('profile,name,cpu,memory,time', [
    ('bindcraft2', 'RunBindCraft2', 4, '16 GB', '72h'),
    ('esmfold2', 'ESMFold2Predict', 8, '16 GB', '6h'),
    ('esmfold2', 'BatchESMFold2Validation', 8, '16 GB', '12h'),
    ('ligandmpnn_interface_context', 'RunLigandMPNNInterfaceContext', 4, '16 GB', '12h'),
])
def test_named_native_profiles_preserve_exact_source_requests(profile, name, cpu, memory, time):
    config = (get_code_root() / 'nextflow.config').read_text()
    overrides = named_profile_overrides(config, (profile, 'workstation_ryzen7960x'), name)
    assert {key: row[0] for key, row in overrides.items()} == {
        'cpus': cpu, 'memory': memory, 'time': time,
    }
    assert all(f'profiles.{profile}.process.withName.{name}' in authority
               for _, authority in overrides.values())


def test_selected_bc2_profile_drives_the_actual_target_envelope():
    root = get_code_root()
    settings = {'bindcraft2_settings': {'modality': 'VHH', 'number_of_final_designs': 25,
        'max_trajectories': 100, 'kept_sequences': 1}}
    plan = build_selected_execution_plan(model_id='bindcraft2', mode='campaign',
        entrypoint='workflows/bindcraft2.nf', requested=settings, effective=settings,
        native_parameters={}, source_identity=SourceIdentity.from_checkout(root),
        profiles=('bindcraft2', 'workstation_ryzen7960x'))
    resource_json = plan.metadata.static_components[0].resources_json
    assert resource_json is not None
    resource = json.loads(resource_json)
    assert resource['cpus']['value'] == 4
    assert resource['memory']['value'] == '16 GB'
    assert resource['time'] == '72h'
    target = selected_plan_target_resources(SimpleNamespace(id='fixture'), plan,
        gpu_ids=[0], scratch_bytes=0)
    assert target['required']['memory_bytes'] == 16 * 1024 ** 3
    assert target['required']['cpus'] == 4
    assert json.loads(plan.requested_json) == settings
    assert json.loads(plan.effective_json) == settings
    from scripts.lib.component_adapter import native_resource_config
    config = native_resource_config(plan.to_dict(), target, '/fixture/compute.lock', root)
    assert f"executor.memory = '{16 * 1024 ** 3} B'" in config
    assert 'executor.cpus = 4' in config


def test_named_selector_projection_is_shared_and_does_not_invent_dynamic_values():
    config = '''
    profiles {
        unused { process { withName: NativeLeaf { cpus = 99 } } }
        fixture {
            process {
                // A brace in a comment { must not change selector scope.
                withName: 'Native(Leaf|Other)' {
                    cpus = 7
                    memory = '18 GB'
                    time = '2h'
                    maxForks = 2
                }
                withName: DynamicLeaf {
                    cpus = params.some_existing_dynamic_cpu_request
                }
            }
        }
    }
    '''
    assert {k: v[0] for k, v in named_profile_overrides(config, ('fixture',), 'NativeLeaf').items()} == {
        'cpus': 7, 'memory': '18 GB', 'time': '2h', 'maxForks': 2,
    }
    assert not named_profile_overrides(config, ('fixture',), 'UnselectedLeaf')
    assert not named_profile_overrides(config, ('fixture',), 'DynamicLeaf')
