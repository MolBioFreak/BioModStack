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


@pytest.mark.asyncio
@pytest.mark.parametrize('callsite', [0, 1])
@pytest.mark.parametrize('published', [False, True])
async def test_actual_jobs_action_calls_pass_published_custody(tmp_path, monkeypatch, callsite, published):
    """Exact Jobs call expressions + real materializer; settings compiler inert."""
    import ast
    import asyncio
    from functools import partial
    from types import SimpleNamespace
    from services.bindcraft2_launch import materialize_native_action
    from services.bindcraft2_publication import published_campaign_immutable, _SCHEMA
    from test_bindcraft2_lifecycle import setup, compiler

    campaign, _ = setup(tmp_path, monkeypatch)
    retained = campaign / '3_Ranked/example.cif'
    retained.parent.mkdir()
    retained.write_bytes(b'inert closed native structure\n')
    source = Job(id='source', model_id='bindcraft2', status='completed' if published else 'failed',
                 paused=False, awaiting_input=False, params={}, retry_count=0,
                 output_dir=str(tmp_path / 'parent'), remote_attempt_id=None,
                 provenance={'bindcraft2_native_publication': {
                     'schema': _SCHEMA, 'root': str(tmp_path / 'parent'),
                     'campaign_root': 'bindcraft2/campaign', 'attempt': 0,
                     'remote_attempt_id': None,
                 }})
    tree = ast.parse(Path(jobs.__file__).read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_create_job')
    calls = [node for node in ast.walk(owner) if isinstance(node, ast.Await)
             and isinstance(node.value, ast.Call) and node.value.args
             and isinstance(node.value.args[0], ast.Name)
             and node.value.args[0].id == 'materialize_native_action']
    calls.sort(key=lambda node: node.lineno)
    assert len(calls) == 2  # Remote preparation and ordinary local submission.
    output = tmp_path / 'child'
    namespace = dict(asyncio=asyncio, Path=Path, bc2_action_source=source,
        output=output, output_dir=str(output), normalized_mode='rank', job_params={},
        job_data=SimpleNamespace(params={}),
        published_campaign_immutable=published_campaign_immutable,
        materialize_native_action=partial(materialize_native_action, compiler=compiler))
    result = await eval(compile(ast.Expression(calls[callsite].value), jobs.__file__, 'eval'), namespace)
    received = Path(result['bc2_campaign_dir']) / 'campaign' / retained.relative_to(campaign)
    assert received.samefile(retained) is published
    assert received.read_bytes() == retained.read_bytes()
    retained.unlink()
    assert received.read_bytes() == b'inert closed native structure\n'
