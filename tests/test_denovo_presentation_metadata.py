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


def test_help_does_not_author_or_change_native_requests():
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

