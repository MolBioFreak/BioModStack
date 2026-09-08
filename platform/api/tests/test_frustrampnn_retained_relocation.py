"""Retained v1 provenance survives relocation, never execution authority."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


API_ROOT = Path(__file__).resolve().parents[1]


def _process(code, env, *args):
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *map(str, args)],
        cwd=API_ROOT, env=env, text=True, capture_output=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.fixture
def retained(tmp_path):
    root = tmp_path / "retained"
    root.mkdir()
    historical = tmp_path / "removed-install/containers/frustrampnn.sif"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(API_ROOT), str(API_ROOT / "tests"))),
           "BMS_DATA_ROOT": str(tmp_path / "data"),
           "BMS_CONTAINER_DIR": str(historical.parent), "BMS_FRUSTRAMPNN_SIF": str(historical)}
    _process("""
        import sys
        from pathlib import Path
        from test_frustrampnn_manifests import _bundle, _rehash_bundle, _write_json
        from services.frustrampnn import manifests, runtime
        from services.frustrampnn.contracts import canonical_json_loads, canonical_json_bytes
        root = Path(sys.argv[1])
        _bundle(root)
        name = 'frustrampnn_execution_receipt_v1.json'
        receipt = canonical_json_loads((root / name).read_bytes())
        receipt['configured_sif_path'] = runtime.FRUSTRAMPNN_RUNTIME_IDENTITY.configured_sif_path
        _write_json(root, name, receipt)
        _rehash_bundle(root)
        (root / manifests.MANIFEST_PATH).write_bytes(
            canonical_json_bytes(manifests.build_result_manifest(root)))
    """, env, root)
    # The old installation is absent, not a symlink to the new store. The reader
    # intentionally needs no image bytes: no scientific execution is requested.
    assert not historical.parent.exists()
    import json
    receipt = json.loads((root / 'frustrampnn_execution_receipt_v1.json').read_bytes())
    current = tmp_path / 'current/store/objects/sha256' / receipt['sif_sha256'] / 'runtime.sif'
    env.update(BMS_CONTAINER_DIR=str(tmp_path / 'current/containers'),
               BMS_RUNTIME_IMAGE_STORE=str(tmp_path / 'current/store'),
               BMS_FRUSTRAMPNN_SIF=str(current))
    return root, historical, current, env


def test_retained_bundle_reopens_in_fresh_relocated_process(retained):
    root, historical, current, env = retained
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    _process("""
        import sys
        from pathlib import Path
        from services.frustrampnn import manifests, runtime
        from services.frustrampnn.contracts import canonical_json_loads
        root, historical, current = map(Path, sys.argv[1:])
        assert not historical.exists()
        assert runtime.FRUSTRAMPNN_RUNTIME_IDENTITY.configured_sif_path == str(current)
        assert runtime.get_container_path('frustrampnn.sif') != historical
        expected = canonical_json_loads((root / manifests.MANIFEST_PATH).read_bytes())
        assert manifests.load_result_manifest(root) == expected
        manifests.validate_result_manifest(root, expected)
        # General request admission remains current-installation-only.
        from services.frustrampnn.contracts import validate_schema, ContractValidationError
        request = canonical_json_loads((root / 'workflow_component_request_v1.json').read_bytes())
        try:
            validate_schema('workflow_component_request_v1', request)
        except ContractValidationError:
            pass
        else:
            raise AssertionError('general request validation was relaxed')
        # Historical receipt acceptance must not authorize opening that location.
        try:
            runtime.validate_configured_container_path(historical)
        except runtime.RuntimeValidationError:
            pass
        else:
            raise AssertionError('historical location authorized for execution')
    """, env, root, historical, current)
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize('mutation', [
    'sif_sha256', 'executable_sha256', 'executable_path', 'checkpoint_sha256',
    'checkpoint_id', 'checkpoint_path',
    *('software:' + key for key in ('frustrampnn', 'adapter', 'normalizer', 'finalizer',
                                    'source_commit', 'python', 'pytorch', 'image')),
    *('path:' + path for path in ('relative.sif', '/old/../image.sif', '/old/./image.sif',
                                  '/old//image.sif', '/old/image.sif/', '/old/\\image.sif',
                                  '/old/\x00image.sif')),
    'argv', 'configuration_hash', 'receipt_hash', 'request_hash', 'manifest_hash',
])
def test_relocated_retained_bundle_rejects_tampering(retained, mutation):
    root, _, _, env = retained
    # Pass NUL-bearing test data through JSON rather than the OS argv boundary.
    import json
    _process("""
        import json, sys
        from pathlib import Path
        from services.frustrampnn import manifests
        from services.frustrampnn.contracts import canonical_json_loads, canonical_json_bytes
        from test_frustrampnn_manifests import _rehash_bundle, _write_json
        root = Path(sys.argv[1])
        mutation = json.loads(sys.argv[2])
        name = 'frustrampnn_execution_receipt_v1.json'
        receipt = canonical_json_loads((root / name).read_bytes())
        if mutation == 'manifest_hash':
            manifest = canonical_json_loads((root / manifests.MANIFEST_PATH).read_bytes())
            manifest['artifacts'][0]['sha256'] = '0' * 64
            _write_json(root, manifests.MANIFEST_PATH, manifest)
        elif mutation == 'configuration_hash':
            request_name = 'workflow_component_request_v1.json'
            request = canonical_json_loads((root / request_name).read_bytes())
            request['parameters']['configuration_sha256'] = '0' * 64
            _write_json(root, request_name, request)
        elif mutation == 'request_hash':
            (root / 'workflow_component_request_v1.json').write_bytes(
                (root / 'workflow_component_request_v1.json').read_bytes() + b' ')
        else:
            if mutation.startswith('path:'):
                receipt['configured_sif_path'] = mutation.removeprefix('path:')
            elif mutation.startswith('software:'):
                receipt['software_versions'][mutation.split(':')[1]] = 'changed'
            elif mutation == 'argv':
                receipt['argv'][13] = receipt['configured_sif_path']
            elif mutation == 'receipt_hash':
                receipt['configured_sif_path'] = '/another/safe/historical.sif'
            else:
                receipt[mutation] = '0' * 64 if mutation.endswith('sha256') else '/changed'
            _write_json(root, name, receipt)
        try:
            if mutation not in {'receipt_hash', 'request_hash', 'manifest_hash'}:
                # Rehash the adversarial bundle so scientific/path/argv checks,
                # not merely stale inventory hashes, must reject the change.
                result_name = 'workflow_component_result_v1.json'
                result = canonical_json_loads((root / result_name).read_bytes())
                for key in result['runtime_identity']:
                    result['runtime_identity'][key] = receipt[key]
                _write_json(root, result_name, result)
                _rehash_bundle(root)
                (root / manifests.MANIFEST_PATH).unlink()
                manifests.build_result_manifest(root)
            else:
                manifests.validate_result_manifest(root, manifests.load_result_manifest(root))
        except manifests.ManifestValidationError:
            pass
        else:
            raise AssertionError('tampered bundle accepted: ' + mutation)
    """, env, root, json.dumps(mutation))
