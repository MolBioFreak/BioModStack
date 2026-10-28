"""Exact mounted prepared companion through existing private persistence owners."""
import json
import os
from pathlib import Path
import pytest
from test_core_protein_scientific_admission import admission  # noqa: F401
from test_project_workflow_setups import setup_store  # noqa: F401
import test_construction_parent_http as owners


def envelope():
    source = os.environ.get('BMS_PREPARED_PARENT_WIRE')
    assert source, 'Requires actual mounted prepared parent envelope'
    value = json.loads(Path(source).read_text())
    documents = value['editor_state']['prepared_geometry_documents']
    assert documents
    assert 'prepared_geometry_documents' not in value['native_job_request']['params']
    assert value['editor_state']['molecular_construction']['geometry'].get('result') is None
    for document in documents.values():
        assert document['format'] == 'pdb'
        assert document['geometry']['atom_map']
        assert document['geometry']['graph']['bonds']
        assert document['geometry']['provenance']
    return value


@pytest.mark.asyncio
async def test_prepared_template_twice_cold(admission, monkeypatch):
    monkeypatch.setattr(owners, 'mounted_envelope', envelope)
    await owners.test_mounted_construction_template_twice_and_cold_read(admission)


@pytest.mark.asyncio
async def test_prepared_project_twice_cold(admission, setup_store, tmp_path, monkeypatch, record_property):
    import paths
    from routers import files, jobs
    value = envelope()
    roots = {'inputs': tmp_path / 'inputs', 'bms_results': tmp_path / 'results'}
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    for module in (paths, files, jobs):
        monkeypatch.setattr(module, 'get_allowed_roots', lambda: roots)
    for document in value['editor_state']['prepared_geometry_documents'].values():
        destination = tmp_path / document['source']['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(document['geometry']['coordinate_text'].encode())
    monkeypatch.setattr(owners, 'mounted_envelope', envelope)
    await owners.test_mounted_construction_project_twice_cold_prepare(admission, setup_store, tmp_path, monkeypatch, record_property)
