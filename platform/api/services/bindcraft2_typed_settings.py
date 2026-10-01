"""Read-only selected-profile display, separate from native request compilation."""
from __future__ import annotations

import copy
import difflib
import hashlib
import inspect
import json
import math
import os
import re
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
from typing import Callable, NamedTuple

from services.bindcraft2_inventory import PIN

# Context read by the pinned pure feature resolver, not arbitrary scientific edits.
FEATURE_SELECTORS = frozenset({
    'copies', 'binder_scaffold', 'mutate_positions', 'binder_lengths',
    'binder_shapes', 'targets', 'forced_targeting', 'initial_guess',
    'bigbang_initialization', 'cyclize_peptide', 'sparse_output',
    'relax_accepted_designs',
})


def _namespace(data: dict) -> dict:
    resolver = data['display_resolver']
    source = resolver['source']
    if (data['upstream_commit'] != PIN
            or resolver['source_files']['settings.py'] != data['source_sha256']
            or resolver['source_files']['loss.py'] != data['registry_sha256']['loss.py']
            or hashlib.sha256(source.encode()).hexdigest() != resolver['sha256']):
        raise ValueError('BC2 display resolver source pin mismatch')
    layers = data['presets']

    def shipped_preset_names(tier, presets=None):
        return tuple(sorted(layers[tier]))

    def read_preset(tier, name, presets=None):
        if name not in layers[tier]:
            raise ValueError(f'Unknown BC2 {tier} preset: {name}')
        # Same native path resolution, with a display-only package location. This
        # never probes/acquires these files; actual paths remain compiler-owned.
        preset = copy.deepcopy(layers[tier][name]['values'])
        if preset.get('binder_scaffold'):
            preset['binder_scaffold'] = str((Path('/native/bindcraft2') / preset['binder_scaffold']).resolve())
        for target in preset.get('targets', []):
            target['target_path'] = str((Path('/native/bindcraft2/settings') / tier / target['target_path']).resolve())
        return preset

    def signatures(block):
        registry = {}
        for name, entry in data['registered_metrics'][block].items():
            def metric():
                pass
            setattr(metric, '__signature__', inspect.Signature([
                inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY)
                for key in entry['params']]))
            registry[name] = metric
        return registry

    namespace = dict(copy=copy, json=json, os=os, math=math, re=re,
                     difflib=difflib, inspect=inspect, dataclass=dataclass,
                     IntFlag=IntFlag, Callable=Callable, NamedTuple=NamedTuple,
                     Path=Path, CAMPAIGN_PRESETS=Path('/native/bindcraft2/settings'),
                     DEFAULT_SETTINGS=data['default_values'],
                     DEFAULT_LOSSES=data['default_values']['losses'],
                     read_preset=read_preset, shipped_preset_names=shipped_preset_names,
                     REGISTERED_LOSSES=signatures('losses'),
                     REGISTERED_FILTER_METRICS=signatures('filters'))
    # Trusted generated package metadata, never source supplied by the caller.
    exec(compile(source, f'BindCraft2@{PIN}/pure-settings', 'exec'), namespace)
    return namespace


def _json_values(value):
    # Native observational sentinel thresholds are not finite cutoffs. Preserve
    # their identity explicitly rather than emitting invalid Infinity JSON.
    if isinstance(value, float) and not math.isfinite(value):
        return 'Infinity' if value > 0 else '-Infinity' if value < 0 else 'NaN'
    if isinstance(value, dict):
        return {key: _json_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_values(item) for item in value]
    return value


def display_projection(selectors: dict, data: dict | None = None) -> dict:
    """Bounded current-selection inheritance; does not mutate any request.

    Unknown/inapplicable display selectors fail discovery only. Scientific
    normalization, preview and launch do not consult this projection.
    """
    if data is None:
        from services.bindcraft2_typed import schema
        data = schema()
    if not isinstance(selectors, dict):
        raise ValueError('BC2 display selectors must be an object')
    allowed = {'core', 'modality', 'target'} | set(data['presets']['property']) | FEATURE_SELECTORS
    if set(selectors) - allowed:
        raise ValueError('Unknown BC2 display selectors: ' + ', '.join(sorted(set(selectors) - allowed)))
    for key in {'core', 'modality', 'target'} & selectors.keys():
        value = selectors[key]
        if value is not None and not isinstance(value, str) and not (
                isinstance(value, list) and all(isinstance(item, str) for item in value)):
            raise ValueError(f'{key}: expected native preset name or ordered names')
    for key in set(data['presets']['property']) & selectors.keys():
        if type(selectors[key]) is not bool:
            raise ValueError(f'{key}: expected boolean design property')
    native = _namespace(data)
    requested = copy.deepcopy(selectors)
    values = native['load_settings'](requested)
    origin = f'BindCraft2@{PIN}:bindcraft/settings.py:load_settings'
    origins = {key: origin for key in values}
    def record(layer, authority, prefix=''):
        for key, value in layer.items():
            path = prefix + key
            origins[path] = authority
            if isinstance(value, dict):
                record(value, authority, path + '.')
    record(data['default_values'], f'BindCraft2@{PIN}:settings/core/default.json')
    for tier in ('core', *native['PRESET_TIERS']):
        names = (native['requested_core_profiles'](requested) if tier == 'core'
                 else native['requested_preset_names'](requested, tier))
        for name in names:
            record(data['presets'][tier][name]['values'],
                   f'BindCraft2@{PIN}:settings/{tier}/{name}.json')
    # Feature-created fields and selector context retain the resolver authority.
    composed = native['campaign_over_presets'](copy.deepcopy(requested))
    for key, value in values.items():
        expected = composed.get(key, data['default_values'].get(key))
        if value != expected:
            origins[key] = origin
    for key in requested:
        origins[key] = origin + ':selector-context'
    # Source-qualified runtime fallbacks are conditional, never schema defaults.
    for key, field in data['fields'].items():
        if key in values or 'runtime_fallback' not in field:
            continue
        condition = field.get('applicable_when', {})
        if any(values.get(name) != expected for name, expected in condition.items()):
            continue
        if key == 'oligomer_tie' and int(values.get('copies', 1) or 1) <= 1:
            continue
        values[key] = copy.deepcopy(field['runtime_fallback'])
        origins[key] = f'BindCraft2@{PIN}:' + field.get('fallback_authority', field.get('source_evidence', 'bindcraft/settings.py:load_settings'))
    # Native package-relative structural assets are known layer provenance, not
    # portable source inputs. Do not advertise the virtual evaluation location
    # as an acquired/effective source. Their bytes/paths stay compiler-owned.
    for source_slot in ('binder_scaffold', 'targets'):
        values.pop(source_slot, None)
        origins.pop(source_slot, None)
    return {'selectors': copy.deepcopy(selectors), 'values': _json_values(_unbound_sources(values, origins)),
            'origins': origins}


def _unbound_sources(value, origins, prefix=''):
    """An inventory cannot bind a worker's package-relative structural asset."""
    if isinstance(value, str) and value.startswith('/native/bindcraft2/'):
        origins[prefix] = origins.get(prefix, f'BindCraft2@{PIN}') + ':native package source binding unavailable'
        return None
    if isinstance(value, dict):
        return {key: _unbound_sources(item, origins, f'{prefix}.{key}' if prefix else key)
                for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_unbound_sources(item, origins, f'{prefix}.{index}') for index, item in enumerate(value)]
    return value
