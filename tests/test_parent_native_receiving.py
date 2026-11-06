"""Read-only discovery + actual materializer provenance; no inference or live stores."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'platform/api'))
sys.path.insert(0, str(ROOT))


def test_discovery_exact_tasks_and_typed_modifications():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.models import router
    from scripts.lib.laproteina_contract import native_task_catalog
    app = FastAPI()
    app.include_router(router, prefix='/api/models')
    with TestClient(app) as client:
        base = '/api/models/protein_modification_experimental/generation-settings?mode=de_novo_design&generator='
        la = client.get(base + 'laproteina&task=motif_scaffolding')
        assert la.status_code == 200, la.text
        assert la.json()['native_task_catalog'] == native_task_catalog()
        assert len(la.json()['native_task_catalog']['tasks']) == 52
        assert not any(k in la.json()['parameter_schema']['properties']['atom_selection_mode'] for k in ['default'])
        disco = client.get(base + 'disco&task=custom_json')
        assert disco.status_code == 200, disco.text
        defs = disco.json()['input_schema']['$defs']
        for name, position, kind in [('ProteinModification', 'ptmPosition', 'ptmType'), ('NucleicModification', 'basePosition', 'modificationType')]:
            assert defs[name]['required'] == [position, kind]
            assert defs[name]['properties'][position]['type'] == 'integer'
            assert defs[name]['properties'][kind]['type'] == 'string'
        for name in ['ProteinPolymer', 'NucleicPolymer']:
            assert 'modifications' not in defs[name]['required']
            assert set(defs[name]['required']) == {'sequence', 'count'}


def test_exact_original_selected_ledger_correspondence(tmp_path, monkeypatch):
    from services import binder_source_materialization as owner
    monkeypatch.setattr(owner, 'to_allowed_relative', str)
    raw = (ROOT / 'platform/frontend/tests/fixtures/rfd3-selection-identity.cif').read_bytes()
    receipt = owner.materialize_source_bytes(raw, '.cif', tmp_path, output_format='native', model_number=2)
    assert receipt['original_sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt['original_path'] != receipt['path'] == receipt['selected_path']
    assert receipt['sha256'] == hashlib.sha256(Path(receipt['path']).read_bytes()).hexdigest()
    assert {r['model_number'] for r in receipt['source_positions']} == {2}
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    actual = owner._cif_ledger(MMCIF2Dict(receipt['path']))
    assert receipt['source_positions'] == actual['source_positions']
    original = owner._cif_ledger(MMCIF2Dict(receipt['original_path']))
    # Source and derivative retain exact author/label/insertion/atom identities;
    # per-model indices are owned by materialization, never guessed by the browser.
    assert receipt['source_positions'] == [r for r in original['source_positions'] if r['model_number'] == 2]
    receipt['source_structure'] = {'path': receipt['original_path'], 'output_format': 'native', 'model_number': 2}
    receipt.update(source_identity={}, source_path=receipt['original_path'])
    if os.environ.get('BMS_PARENT_RECEIPT_WIRE'):
        Path(os.environ['BMS_PARENT_RECEIPT_WIRE']).write_text(json.dumps(receipt))


def test_installed_task_catalog_matches_source_exactly():
    """Run inside the selected La image with its original installed YAML."""
    import pytest
    source = os.environ.get('LAPROTEINA_TEST_SOURCE')
    if not source:
        pytest.skip('explicit selected native image required')
    import yaml
    from scripts.lib.laproteina_contract import native_task_catalog
    metadata = native_task_catalog()
    raw = (Path(source) / metadata['source_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == metadata['source_sha256']
    assert metadata['tasks'] == yaml.safe_load(raw)['dataset']['motif_dict_cfg']
    assert len(metadata['tasks']) == 52
