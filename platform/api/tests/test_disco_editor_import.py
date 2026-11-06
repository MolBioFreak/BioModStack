"""Managed DISCO import and edited-file closure, no inference or deployment."""
import asyncio
import copy
import json
from pathlib import Path
import shutil

import pytest
from fastapi import HTTPException
from routers import files
import paths
from scripts.lib import portable_inputs as portable
from test_denovo_remote_inputs import roots, compile_cad, place


def test_managed_import_uses_existing_read_authority(tmp_path, monkeypatch):
    root = tmp_path / 'inputs'
    root.mkdir()
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': root})
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: {'inputs': root})
    source = root / 'native.json'
    raw = '[ {"name":"native", "future":false, "sequences":[]} ]\n'
    source.write_text(raw)
    result = asyncio.run(files.read_disco_jobs('inputs/native.json', ()))
    assert result == {'text': raw, 'source_path': str(source)}
    assert source.read_text() == raw
    outside = tmp_path / 'outside.json'
    outside.write_text(raw)
    (root / 'escape.json').symlink_to(outside)
    for path in ('inputs/../outside.json', 'inputs/escape.json', str(outside)):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(files.read_disco_jobs(path, ()))
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        asyncio.run(files.read_disco_jobs('inputs/native.json', (root,)))
    assert exc.value.status_code == 403


def test_imported_inline_jobs_relocate_original_and_edited_ligands(roots, tmp_path, monkeypatch):
    source = roots['inputs'] / 'jobs.json'
    ligand = roots['inputs'] / 'relative.sdf'
    added = roots['inputs'] / 'added.sdf'
    ligand.write_bytes(b'original ligand\r\n')
    added.write_bytes(b'edited extra ligand\n')
    original = [{'name': 'original', 'unknown': {'false': False}, 'sequences': [
        {'ligand': {'ligand': 'FILE_relative.sdf', 'count': 1}},
        {'dnaSequence': {'sequence': 'ACGT', 'count': 1}}]}]
    source.write_text(json.dumps(original, indent=3))
    edited = copy.deepcopy(original)
    edited[0]['name'] = 'edited'
    edited[0]['sequences'].append({'ligand': {'ligand': 'FILE_added.sdf', 'count': 1}})
    params = {'backend': 'disco', 'pcad_disco_jobs_source_path': str(source),
              'pcad_disco_jobs_json': edited,
              'disco_input_json_path': 'inputs/deleted-inactive.json',
              'disco_ligand_sdf': 'inputs/deleted-inactive.sdf'}
    invocation = compile_cad(roots, params, True)
    assert invocation.native_parameters['pcad_disco_jobs_json'] == edited
    assert '--pcad_disco_jobs_source_path' in invocation.command
    refs, _, _ = place(roots, tmp_path, monkeypatch, invocation)
    assert {ref['source_path'] for ref in refs} == {str(source), str(ligand), str(added)}
    originals = {path: path.read_bytes() for path in (source, ligand, added)}
    shutil.rmtree(roots['inputs'])
    owner = portable.resolve_input_path(source)
    assert owner.read_bytes() == originals[source]
    native = portable.bind_native_document(edited, 'disco-json', owner=owner)
    for index, path in ((0, ligand), (2, added)):
        assert Path(native[0]['sequences'][index]['ligand']['ligand'][5:]).read_bytes() == originals[path]
    assert edited[0]['sequences'][0]['ligand']['ligand'] == 'FILE_relative.sdf'
    assert native[0]['sequences'][1] == original[0]['sequences'][1]
    assert native[0]['unknown'] == original[0]['unknown']
