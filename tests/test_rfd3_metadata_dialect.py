"""Metadata is retained metadata; dialect selects the installed parser."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from scripts.lib.rfd3_scientific_settings import conditioning_settings, scientific_inventory
from scripts.rfd3_local_redesign.contract import _build_native_spec

ROOT = Path(__file__).resolve().parents[1]


def prepare_generation(conditioning):
    # The CLI uses its sibling contract import; isolate that module binding.
    from scripts.rfd3_generation import contract
    old = sys.modules.get('contract')
    sys.modules['contract'] = contract
    try:
        spec = importlib.util.spec_from_file_location('generation_prepare_test', ROOT/'scripts/rfd3_generation/prepare_native_input.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        request = {'schema':contract.REQUEST_SCHEMA, 'request_id':'exact', 'job_id':'exact',
                   'generation':{'min_length':2,'max_length':2,'num_designs':1},
                   'execution':{'seed':0,'dump_trajectories':False}, 'conditioning':conditioning}
        return module.prepare(request)[0]['generation_0']
    finally:
        if old is None:
            sys.modules.pop('contract', None)
        else:
            sys.modules['contract'] = old


@pytest.mark.parametrize('dialect', [1,2,3,0])
def test_sparse_generation_and_local_transport(dialect):
    values = {'dialect':dialect,'extra':{'note':0,'nested':[False,None,'exact']}}
    assert conditioning_settings(values) == values
    assert conditioning_settings({}) == {}
    native = prepare_generation(values)
    assert {k:native[k] for k in values} == values
    for mode in ('native_recall','partial_diffusion'):
        native = _build_native_spec({**values, 'redesign_ranges':['A1']}, 'source.pdb', mode, [('A',1,'')])
        assert {k:native[k] for k in values} == values
    assert 'metadata' in scientific_inventory()['conditioning']['extra']['description']


def test_installed_parser_dispatch_and_metadata():
    image = os.environ.get('BMS_TEST_FOUNDRY_IMAGE')
    if not image:
        pytest.skip('Selected installed Foundry required')
    inputs = [prepare_generation({'dialect':d, 'extra':{'note':0, 'nested':[False,None,'exact']}}) for d in (1,2,3)]
    code = '''
import json,sys
from rfd3.inference.input_parsing import DesignInputSpecification
values=json.load(sys.stdin)
observations=[]
for value in values:
    spec=DesignInputSpecification.safe_init(**value)
    observations.append({'class':type(spec).__name__, 'saved':spec.get_dict_to_save() if hasattr(spec,'get_dict_to_save') else None})
print(json.dumps(observations))
'''
    done = subprocess.run(['apptainer','exec','--cleanenv',image,'python','-c',code],
                          input=json.dumps(inputs), text=True,capture_output=True)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert [v['class'] for v in result] == ['LegacySpecification','DesignInputSpecification','DesignInputSpecification']
    for v in result[1:]:
        assert v['saved']['extra'] == inputs[1]['extra']
        assert v['saved']['length'] == '2-2'
