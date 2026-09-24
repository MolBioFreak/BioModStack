"""CPU-only differential for native representation transport; no inference."""
import hashlib
import json
from pathlib import Path
import sys

root, scratch, upstream = map(Path, sys.argv[1:4])
# Load the same dependency-light services namespace as the native leaf runner.
import runpy
runpy.run_path(str(root / 'scripts/run_bindcraft2_campaign.py'), run_name='bc2_leaf')
from services.bindcraft2_typed import compile_typed, schema
from bindcraft import settings, filters, parameter_sweep

for module in (settings, filters, parameter_sweep):
    installed = Path(module.__file__)
    pinned = upstream / 'bindcraft' / installed.name
    assert installed.read_bytes() == pinned.read_bytes(), installed
    print('PINNED_SOURCE', installed.name, hashlib.sha256(installed.read_bytes()).hexdigest())

pool = tuple(f'model_{i}_multimer_v3' for i in range(1, 6))
monomer = ('model_1_ptm', 'model_2_ptm')
base = {'max_trajectories': 100, 'campaign_seed': 0}
results = []
for models in (2, [pool[2], pool[0]], [2, '0'], []):
    for sweep in (False, True, {}, {'axes': ['weights_interface_contacts'], 'levels': [0.5, 2], 'max_arms': 3}):
        request = {**base, 'design_models': models, 'parameter_sweep': sweep,
                   'filters': {'Binder_RMSD': {'threshold': None, 'mandatory': False}},
                   'losses': {'induced_fit_interface': {'params': {'interface_mask': None}}}}
        compiled = compile_typed(request, scratch / 'campaign')
        assert compiled['requested_settings'] == request
        for key in ('native_request', 'effective_settings'):
            assert compiled[key]['design_models'] == models
            assert compiled[key]['parameter_sweep'] == sweep
            assert compiled[key]['filters']['Binder_RMSD']['threshold'] is None
            assert compiled[key]['losses']['induced_fit_interface']['params']['interface_mask'] is None
        effective = compiled['effective_settings']
        assert 'Binder_RMSD' not in filters.build_filters(effective['filters'])
        assert len(parameter_sweep.parameter_sweep_arms(effective)) == compiled['sweep_budget']['arms']
        selected = settings.select_design_and_validation_models(effective, pool, monomer)
        expected = settings.select_design_and_validation_models(settings.load_settings(request), pool, monomer)
        assert selected == expected
        if isinstance(models, list):
            assert selected.design_models == (() if not models else (pool[2], pool[0]))
        results.append({'models': models, 'sweep': sweep, 'arms': compiled['sweep_budget']['arms'], 'selected': selected.design_models})
for request in (base, {**base, 'filters': {'Binder_RMSD': {'threshold': 0}}}):
    compiled = compile_typed(request, scratch / 'campaign')
    assert compiled['requested_settings'] == request
    if 'filters' in request:
        assert filters.build_filters(compiled['effective_settings']['filters'])['Binder_RMSD'].threshold == 0
assert schema()['nested_control_schemas']['losses']['properties']['induced_fit_interface']['properties']['params']['properties']['interface_mask']['type'] == ['array', 'null']
print(json.dumps(results))
print('BC2_NATIVE_REPRESENTATIONS_CPU_OK', len(results) + 2)
