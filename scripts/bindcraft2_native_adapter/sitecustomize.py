"""BMS BC2 JSON mask transport, scoped to the native leaf's PYTHONPATH.

Install lazily: native CLI sets accelerator/cache environment before importing
JAX. Python startup also runs this in native subprocess workers (-m bindcraft.cli)
and interpreter re-entry. Do not eagerly import bindcraft or alter its CLI.
"""
from functools import wraps
from importlib.machinery import PathFinder
import sys


def _adapt(module):
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
        if fullname != 'bindcraft.loss':
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = _MaskLoader(spec.loader)
        return spec


sys.meta_path.insert(0, _MaskFinder())
