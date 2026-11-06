"""BMS BC2 JSON mask transport, scoped to the native leaf's PYTHONPATH.

Install lazily: native CLI sets accelerator/cache environment before importing
JAX. Python startup also runs this in native subprocess workers (-m bindcraft.cli)
and interpreter re-entry. Do not eagerly import bindcraft or alter its CLI.
"""
from functools import wraps
from importlib.machinery import PathFinder
import sys


def _adapt(module):
    if module.__name__ == 'bindcraft.score':
        # Native score-folder helper, not AF2 prediction/campaign acceptance.
        # Zero is a finite CA reading; keep the native scale convention intact.
        @wraps(module.binder_confidence)
        def binder_confidence(structure, binder_chains):
            readings = [r['b_factor'] for r in module.read_structure_atoms(structure)
                        if r['chain_id'] in binder_chains and r['name'] == 'CA'
                        and module.math.isfinite(r['b_factor'])]
            if not readings:
                return None
            confidence = sum(readings) / len(readings)
            return module.recorded_number(confidence / 100.0 if confidence > 1.0 else confidence)
        module.binder_confidence = binder_confidence
        return
    native = module.induced_fit_interface_masks

    @wraps(native)
    def portable_masks(protein_states, predictions, coordinates, valid_mask,
                       prediction_state, target, cutoff, interface_residues=(),
                       interface_mask=None):
        # _bound_metric caches JSON lists as tuples. Convert only at consumption,
        # not in settings or binding: arrays would break its hashable cache key.
        # Native arrays (including frozen/padded overrides) pass through unchanged.
        if isinstance(interface_mask, (list, tuple)):
            interface_mask = module.jnp.asarray(interface_mask)
        return native(protein_states, predictions, coordinates, valid_mask,
                      prediction_state, target, cutoff, interface_residues,
                      interface_mask)

    module.induced_fit_interface_masks = portable_masks


class _MaskLoader:
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        _adapt(module)


class _MaskFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ('bindcraft.loss', 'bindcraft.score'):
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = _MaskLoader(spec.loader)
        return spec


sys.meta_path.insert(0, _MaskFinder())
