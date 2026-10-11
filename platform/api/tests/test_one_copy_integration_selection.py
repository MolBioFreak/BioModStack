"""Selection-copy regressions; no model execution or live storage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from database import Design, Job
from routers import jobs


@pytest.mark.parametrize('suffix', ['.cif', '.pdb'])
@pytest.mark.parametrize('alias', [False, True])
def test_sidecars_exclude_resolved_primary_and_keep_distinct_evidence(tmp_path, suffix, alias):
    primary = tmp_path / ('selected' + suffix)
    if alias:
        original = tmp_path / ('native' + suffix)
        original.write_bytes(b'opaque structure selection fixture')
        primary.symlink_to(original)
    else:
        primary.write_bytes(b'opaque structure selection fixture')
    sidecars = []
    for extension in ('.json', '.cif', '.npz', '.pae.npz'):
        path = primary.with_suffix(extension)
        if path == primary:
            continue
        path.write_bytes(extension.encode())
        sidecars.append(path.resolve())
    design = Design(id='selected-design', job_id='source', name='selected', pdb_path=str(primary))
    actual = jobs._candidate_sidecar_paths(design, primary)
    assert actual == sidecars
    assert primary.resolve() not in actual


def test_selection_materializes_cif_once_preserving_manifest_and_mutable_source_isolation(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    primary = source / 'selected.cif'
    primary.write_bytes(b'opaque CIF fixture\n')
    (source / 'selected.json').write_text('{"native_evidence":true}')
    (source / 'selected.npz').write_bytes(b'opaque binary evidence')
    selection = tmp_path / 'selection'
    selection.mkdir()
    monkeypatch.setattr(jobs, '_create_generic_selection_dir', lambda *_: selection)
    root = Job(id='root', name='root', model_id='protein_local_redesign', mode='redesign')
    producer = Job(id='source', name='source', model_id='protein_local_redesign', mode='redesign')
    design = Design(id='selected-design', job_id='source', name='selected', pdb_path=str(primary))
    copied = []
    original_copy = jobs.shutil.copyfile

    def counted_copy(src, dst, *args, **kwargs):
        copied.append((Path(src).resolve(), Path(dst)))
        return original_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(jobs.shutil, 'copyfile', counted_copy)
    actual = jobs._materialize_protein_local_selection(root, producer, [design], 'redesign')
    assert actual == selection
    assert [src for src, _ in copied].count(primary.resolve()) == 1
    assert {p.name for p in selection.iterdir()} == {
        'selected.cif', 'selected.json', 'selected.npz', 'selection_manifest.json',
    }
    manifest = json.loads((selection / 'selection_manifest.json').read_text())
    assert manifest['design_count'] == 1
    assert manifest['designs'][0]['design_id'] == 'selected-design'
    assert manifest['designs'][0]['selection_pdb_path'] == str(selection / 'selected.cif')
    assert manifest['designs'][0]['source_pdb_path'] == str(primary)
    assert (selection / 'selected.cif').read_bytes() == b'opaque CIF fixture\n'
    primary.write_bytes(b'changed mutable source after selection')
    assert (selection / 'selected.cif').read_bytes() == b'opaque CIF fixture\n'
    assert (selection / 'selected.json').read_bytes() == (source / 'selected.json').read_bytes()
