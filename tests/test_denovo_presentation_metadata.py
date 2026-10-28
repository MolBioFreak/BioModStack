"""Presentation metadata, sparse transport, and optional exact published-source qualification."""
import ast
from copy import deepcopy
import hashlib
import os
import zipfile

import pytest
from pydantic import TypeAdapter

from scripts.lib import disco_contract as disco
from scripts.lib import laproteina_contract as la
from scripts.lib import rfd3_scientific_settings as rfd3


def test_every_rfd3_control_has_readable_source_qualified_help():
    for fields in (rfd3.SAMPLER_FIELDS, rfd3.EXECUTION_FIELDS):
        for key, field in fields.items():
            assert field['title'] != key
            assert field['description'] and 'rc-foundry==0.1.9' in field['x-help-source']
    assert 'exceeds this value' in rfd3.SAMPLER_FIELDS['cfg_t_max']['description']
    assert 'allow_realignment' in rfd3.SAMPLER_FIELDS['s_trans']['description']
    assert 'gamma_min' in rfd3.SAMPLER_FIELDS['gamma_0']['description']


def test_help_does_not_author_or_change_native_requests():
    requested = {'inference_sampler': {'cfg_t_max': None, 'cfg_scale': 0, 'allow_realignment': False, 'cfg_features': []}, 'low_memory_mode': False}
    before = deepcopy(requested)
    rfd3.scientific_inventory()
    assert rfd3.execution_settings(requested) == before == requested
    assert rfd3.execution_settings({}) == {}
    assert 'inference_sampler.cfg_t_max=null' in rfd3.native_overrides(requested)
    native: disco.NativeSettings = {'seeds': [0, 9, 0], 'num_inference_seeds': None, 'deterministic': False, 'noisy_guidance': {'omega_seq': 0}, 'sequence_sampling': {'planner_name': None}}
    before = deepcopy(native)
    disco.settings_schema()
    assert TypeAdapter(disco.NativeSettings).validate_python(native) == before == native
    assert disco.native_overrides(native) == disco.native_overrides(before)
    assert la.native_profile_defaults()['profiles']['ucond_notri_long']['bb_ca_gt_clamp_val'] is None


def test_null_labels_are_specific_without_changing_types_or_defaults():
    schema = disco.settings_schema()
    assert schema['properties']['output_format']['x-null-label'] == 'General writer (null)'
    assert schema['$defs']['SequenceSampling']['properties']['planner_name']['x-null-label'] == 'No sequence planner (null)'
    assert all('default' not in leaf for leaf in schema['properties'].values())
    schema = la.settings_schema()
    for key in ('bb_ca_gt_clamp_val', 'local_latents_gt_clamp_val'):
        assert schema['properties'][key]['type'] == ['number', 'null']
        assert schema['properties'][key]['x-null-label'] == 'No upper clamp (null)'
    assert schema['properties']['motif_atom_spec']['x-null-label'] == 'Contig-based extraction (null)'
    assert all('default' not in leaf for leaf in schema['properties'].values())


def test_rfd3_help_against_exact_published_wheel():
    wheel = os.environ.get('RFD3_PRESENTATION_TEST_WHEEL')
    if not wheel:
        pytest.skip('Set RFD3_PRESENTATION_TEST_WHEEL to published rc-foundry 0.1.9 wheel')
    with zipfile.ZipFile(wheel) as archive:
        source = archive.read(rfd3.SAMPLER_SOURCE)
        assert hashlib.sha256(source).hexdigest() == rfd3.SAMPLER_SOURCE_SHA256
        text = source.decode()
        config = next(node for node in ast.parse(text).body if isinstance(node, ast.ClassDef) and node.name == 'SampleDiffusionConfig')
        defaults = {node.target.id: ast.literal_eval(node.value) for node in config.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None}
        for key, definition in rfd3.SAMPLER_FIELDS.items():
            if definition.get('hydra_add'):
                assert definition['default'] == defaults[key]
        # The observed arithmetic/branches, not an inferred Auto/Off meaning.
        for fragment in ('gamma = self.gamma_0 if c_t > self.gamma_min else 0',
                         's_trans=self.s_trans if step_num >= threshold_step else 0.0',
                         'self.cfg_t_max is None or c_t > self.cfg_t_max',
                         'zip(noise_schedule, noise_schedule[1:])',
                         'delta_L = delta_L + (self.cfg_scale - 1) * (delta_L - delta_L_ref)'):
            assert fragment in text
        yaml = archive.read('rfd3/configs/inference_engine/rfdiffusion3.yaml').decode()
        assert 'set to None to use the default in the checkpoint' in yaml
        assert 'Translational noise scale' in yaml


def test_laproteina_null_help_against_exact_pinned_source():
    root = os.environ.get('LAPROTEINA_PRESENTATION_TEST_SOURCE')
    if not root:
        pytest.skip('Set LAPROTEINA_PRESENTATION_TEST_SOURCE to source at ' + la.SOURCE_REVISION)
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any
    import numpy as np
    expected = {
        'proteinfoundation/flow_matching/product_space_flow_matcher.py': '07d6713e659e8bb3072bb624fc4250842548fb5c44a612f91ad2f12a2df330dd',
        'proteinfoundation/utils/motif_utils.py': '5fa62669d721ee1f957dbf3c859df33abce9c11674273464a41fe430a56ba3da',
    }
    sources = {}
    for path, digest in expected.items():
        source = (Path(root) / path).read_bytes()
        assert hashlib.sha256(source).hexdigest() == digest
        sources[path] = source.decode()
    tree = ast.parse(sources['proteinfoundation/flow_matching/product_space_flow_matcher.py'])
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_gt')
    # Exercise the native function itself without importing native accelerators.
    # Postponed annotations retain the exact source body and defaults.
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function], type_ignores=[])
    namespace: dict[str, Any] = {'torch': SimpleNamespace(clamp=np.clip)}
    exec(compile(ast.fix_missing_locations(module), '<pinned-la-get-gt>', 'exec'), namespace)
    times = np.array([0.2, 0.5])
    unclamped = namespace['get_gt'](times, '1/t', 1.0, None)
    clamped = namespace['get_gt'](times, '1/t', 1.0, 1.0)
    assert np.all(unclamped > 1.0) and np.all(clamped == 1.0)
    motif = ast.parse(sources['proteinfoundation/utils/motif_utils.py'])
    parse = next(node for node in motif.body if isinstance(node, ast.FunctionDef) and node.name == 'parse_motif')
    assert 'Residue/range-based specification** (when motif_atom_spec is None)' in (ast.get_docstring(parse) or '')

