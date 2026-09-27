"""Explicit synthetic CPU qualification, not structure prediction or inference.

Run inside the installed native image. The outer process exercises the production
run_campaign subprocess environment; fresh children import the real installed
loss/binding code. Native worker launch is exercised with a synthetic CPU command,
not a GPU campaign. Native CLI entrypoint itself is exercised via design --help.
"""
import inspect
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT, WORK = map(Path, sys.argv[1:3])
runpy.run_path(str(ROOT / 'scripts/run_bindcraft2_campaign.py'), run_name='bc2_leaf')
from services.bindcraft2_native import PIN
from services.bindcraft2_runtime import run_campaign


def science():
    import jax
    import jax.numpy as jnp
    import numpy as np
    import bindcraft.loss as loss
    from bindcraft.protein import Protein, StructurePrediction, ATOM_INDEX
    assert jax.default_backend() == 'cpu'
    assert hasattr(loss.induced_fit_interface_masks, '__wrapped__')
    assert inspect.signature(loss.induced_fit_interface_masks) == inspect.signature(loss.induced_fit_interface_masks.__wrapped__)
    # Eight coordinate rows, deliberately unrelated to author numbering.
    xyz = jnp.asarray([[0., 0., 0.], [1., 1., 0.], [0., 1., 1.], [1., 0., 1.],
                       [30., 0., 0.], [31., 1., 0.], [30., 1., 1.], [31., 0., 1.]])

    def protein(coordinates):
        p = Protein.from_fasta('>A\n' + 'A' * len(coordinates) + '\n')
        return p.replace(atoms=p.atoms.astype(jnp.float32).at[:, ATOM_INDEX['CA'], :].set(coordinates),
                         atom_mask=p.atom_mask.at[:, ATOM_INDEX['CA']].set(True),
                         residue_index=jnp.arange(len(coordinates)) * 3 + 101)

    binder = protein(xyz)
    reference = protein(xyz.at[:4, 2].add(jnp.asarray([0.8, 1.2, 0.5, 1.0])))
    target = protein(jnp.asarray([[0., 0., 2.], [1., 1., 2.]]))
    states = {'complex': {'binder': binder, 'target': target}, 'binder_alone': {'binder': reference}}
    predictions = {name: StructurePrediction(chains, {}) for name, chains in states.items()}
    numeric = [1.0, 0.75, 0.5, 0.75, 0.0, 0.25, 0.0, 0.25]
    entries = {}
    observed = {}
    for name, params in [('omitted', {}), ('null', {'interface_mask': None}),
                         ('numeric', {'interface_mask': numeric})]:
        bound, required = loss.bind_state_metric(loss.REGISTERED_LOSSES['induced_fit_interface'], {'params': params})
        assert bound is loss.bind_state_metric(loss.REGISTERED_LOSSES['induced_fit_interface'], {'params': params})[0]
        assert required == frozenset()
        if name == 'numeric':
            assert bound.keywords['interface_mask'] == tuple(numeric)
        value = bound(states, predictions)
        expected = loss.induced_fit_interface_loss(states, predictions,
                    **({'interface_mask': jnp.asarray(numeric)} if name == 'numeric' else {}))
        np.testing.assert_allclose(value, expected, rtol=1e-6)
        assert np.isfinite(float(value))
        entries[name] = bound
        observed[name] = float(value)
    assert observed['omitted'] == observed['null']
    assert observed['numeric'] != observed['null']
    np.testing.assert_allclose(jax.jit(lambda: entries['numeric'](states, predictions))(),
                               observed['numeric'], rtol=1e-5)
    # Negative and >1 numbers are not binarized; leave native mathematics intact.
    arbitrary = [-0.5, 1.5, 0.25, 0., 2., 0., 0.5, 0.]
    args = (states, predictions, xyz, jnp.ones(8), 'complex', 'target', 8.)
    # Negative control: the installed native function has the transport gap.
    try:
        loss.induced_fit_interface_masks.__wrapped__(*args, interface_mask=tuple(arbitrary))
    except TypeError:
        pass
    else:
        raise AssertionError('pinned native tuple gap unexpectedly absent')
    actual = loss.induced_fit_interface_masks(*args, interface_mask=arbitrary)
    expected = loss.induced_fit_interface_masks.__wrapped__(*args, interface_mask=jnp.asarray(arbitrary))
    for left, right in zip(actual, expected):
        np.testing.assert_array_equal(left, right)
    np.testing.assert_array_equal(actual[0], arbitrary)
    # The actual native build_design_losses path still binds hashable tuples.
    built = loss.build_design_losses({'max_trajectories': 1, 'weights_induced_fit_interface': 1.0,
                 'losses': {'induced_fit_interface': {'params': {'interface_mask': numeric}}}},
                 {'complex': 1.0}, 8)
    native_entry = built['induced_fit_interface']
    assert native_entry.function.keywords['interface_mask'] == tuple(numeric)
    assert native_entry.required_states == frozenset({'complex', 'binder_alone'})
    assert native_entry.interface_mask is None
    frozen = loss.freeze_induced_fit_interface({'induced_fit_interface': native_entry}, states, predictions)
    np.testing.assert_array_equal(frozen['induced_fit_interface'].interface_mask, [1, 1, 1, 1, 0, 0, 0, 0])
    override = loss.frozen_interface_arguments(frozen)['induced_fit_interface']
    frozen_value = native_entry.function(states, predictions, **override)
    np.testing.assert_allclose(frozen_value, entries['null'](states, predictions), rtol=1e-6)
    padded = {name: p.padded_to(11 if name == 'binder' else len(p)) for name, p in states['complex'].items()}
    padded_states = {'complex': padded, 'binder_alone': {'binder': reference.padded_to(11)}}
    padded_predictions = {name: StructurePrediction(chains, {}) for name, chains in padded_states.items()}
    padded_override = loss.frozen_interface_arguments(frozen, padded)['induced_fit_interface']
    np.testing.assert_array_equal(padded_override['interface_mask'], [1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    np.testing.assert_allclose(native_entry.function(padded_states, padded_predictions, **padded_override), frozen_value, rtol=1e-5)
    # No automatic padding of operator vectors: native broadcasting errors remain native.
    try:
        native_entry.function(padded_states, padded_predictions)
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError('operator vector was silently padded')
    reference_bound = loss.induced_fit_reference_losses(frozen, 'binder_alone')['induced_fit_interface']
    assert reference_bound.function.keywords['interface_mask'] == tuple(numeric)
    assert reference_bound.interface_mask is frozen['induced_fit_interface'].interface_mask
    print('NATIVE_SYNTHETIC_LOSS ' + json.dumps(observed), flush=True)


def preparation():
    from bindcraft.settings import load_settings, read_settings
    from bindcraft.parameter_sweep import parameter_sweep_arms
    from bindcraft.preflight import cleaned_campaign_settings
    from services.bindcraft2_typed import compile_typed
    from services.bindcraft2_runtime import prepare_campaign
    from services.bindcraft2_native import _canonical
    for name, params in [('omitted', {}), ('null', {'interface_mask': None}),
                         ('numeric', {'interface_mask': [1, .75, .5, .75, 0, .25, 0, .25]})]:
        destination = WORK / name
        target = WORK / 'synthetic.fasta'
        target.write_text('>A\nAAAAAAAA\n')
        request = {'max_trajectories': 1, 'targets': [{'name': 'synthetic', 'target_path': str(target)}],
                   'weights_induced_fit_interface': 1.0, 'losses': {'induced_fit_interface': {'params': params}}}
        compiled = compile_typed(request, destination / 'campaign', load_settings, parameter_sweep_arms)
        prepared = prepare_campaign(compiled, destination, load_settings, parameter_sweep_arms,
                                   lambda p: cleaned_campaign_settings(read_settings(p)))
        for key in ('requested_settings', 'native_request', 'effective_settings'):
            assert compiled[key]['losses']['induced_fit_interface']['params'] == params
        assert _canonical(cleaned_campaign_settings(read_settings(prepared['settings_path']))) == _canonical(compiled['effective_settings'])
        assert prepare_campaign(compiled, destination, load_settings, parameter_sweep_arms,
                                lambda p: cleaned_campaign_settings(read_settings(p))) == prepared
        # Actual public leaf preparation and transported-folder re-entry.
        command = [sys.executable, str(ROOT / 'scripts/run_bindcraft2_campaign.py'), prepared['receipt_path'],
                   str(destination), '--native-source', '/opt/bindcraft']
        subprocess.run(command, check=True)
        assert not (destination / 'campaign').exists()
    print('NATIVE_PREPARATION_OMITTED_NULL_VECTOR_OK', flush=True)


if len(sys.argv) > 3:
    science()
    if sys.argv[3] == 'child':
        from bindcraft.design_workers import launch_design_workers
        command = [sys.executable, str(Path(__file__)), str(ROOT), str(WORK), 'worker']
        assert launch_design_workers([{'gpu': ''}], str(WORK / 'worker-logs'), command) == 0
        # Native subprocess module entrypoint used by design workers, no inference.
        subprocess.run([sys.executable, '-u', '-m', 'bindcraft.cli', 'design', '--help'], check=True)
    raise SystemExit(0)

assert subprocess.check_output(['git', '-C', '/opt/bindcraft', 'rev-parse', 'HEAD'], text=True).strip() == PIN
preparation()
# Production leaf launches the installed console entrypoint, not a mock executable.
assert run_campaign({'settings_path': '', 'command': ['bindcraft', 'design', '--help']}) == 0
# Same production environment/transport, now executing synthetic native CPU loss.
assert run_campaign({'settings_path': '', 'command': ['bindcraft', str(Path(__file__)), str(ROOT), str(WORK), 'child']},
                    executable=sys.executable) == 0
print('BC2_NATIVE_MASK_CPU_OK', flush=True)
