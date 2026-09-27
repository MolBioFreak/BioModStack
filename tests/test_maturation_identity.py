"""Selected PPIFlow source and native-sample identity transport."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'


def test_stage_and_native_samples_keep_distinct_source_evidence(tmp_path):
    originals = []
    for index in range(2):
        src = tmp_path / f'original_{index}' / 'same.pdb'
        src.parent.mkdir()
        src.write_text(f'ATOM {index}\n')
        originals.append(src)
    selected = tmp_path / 'selected.json'
    selected.write_text(json.dumps([
        {'path': str(src), 'meta': {'id': f'validated_document_{index}',
                                     'validation_status': 'validated',
                                     'structure_state': f'target_{index}'}}
        for index, src in enumerate(originals)
    ]))
    staged = tmp_path / 'staged'
    subprocess.run([sys.executable, str(SCRIPTS / 'maturation_identity.py'),
                    'stage', str(selected), str(staged)], check=True)
    sources = json.loads((staged / 'source_identity.json').read_text())
    assert [row['staged_name'] for row in sources] == ['source_000000.pdb', 'source_000001.pdb']
    assert [(staged / row['staged_name']).read_text() for row in sources] == [src.read_text() for src in originals]
    for index, source in enumerate(sources):
        native_name = f"source_{index:06d}_ppiflow_sample0.pdb"
        sidecar = tmp_path / f'{native_name}.json'
        subprocess.run([sys.executable, str(SCRIPTS / 'maturation_identity.py'), 'sample',
                        str(staged / 'source_identity.json'),
                        json.dumps({'id': native_name.removesuffix('.pdb'),
                                    'source_staged_name': source['staged_name'],
                                    'sample_index': 0}), native_name, str(sidecar)], check=True)
        evidence = json.loads(sidecar.read_text())
        assert evidence['source'] == source
        assert evidence['validation_status'] == 'unvalidated'
        assert evidence['source']['source_meta']['validation_status'] == 'validated'


def test_collector_joins_only_native_sidecars_and_keeps_same_named_children(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    from collect_maturation_outputs import main
    import sys as system

    dirs = []
    for index in range(2):
        root = tmp_path / f'child{index}'
        results = root / 'run/ppiflow/results'
        identity_dir = root / 'run/ppiflow/sample_identity'
        results.mkdir(parents=True)
        identity_dir.mkdir(parents=True)
        (results / 'same_ppiflow.pdb').write_text(f'ATOM {index}\n')
        if index == 0:
            (identity_dir / 'same_ppiflow_sample_identity.json').write_text(json.dumps({
                'pdb_name': 'same_ppiflow.pdb', 'sample_meta': {'id': 'native0'},
                'source': {'source_meta': {'id': 'validated0', 'validation_status': 'validated'}},
                'validation_status': 'unvalidated',
            }))
        dirs.append(str(root))
    inputs = tmp_path / 'children.json'
    inputs.write_text(json.dumps({'child_output_dirs': dirs}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(system, 'argv', ['collector', '--child_outputs_json', str(inputs)])
    main()
    report = json.loads((tmp_path / 'collection_manifest.json').read_text())
    assert len(report['samples']) == 2
    assert len(set(row['pdb'] for row in report['samples'])) == 2
    assert report['samples'][0]['identity']['source']['source_meta']['id'] == 'validated0'
    assert report['samples'][1]['identity'] is None
    assert all((tmp_path / row['pdb']).is_file() for row in report['samples'])


def test_spawn_passes_staged_source_manifest_to_child(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    import spawn_maturation_children as spawn

    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'source_000000.pdb').write_text('ATOM\n')
    (staged / 'source_identity.json').write_text(json.dumps([
        {'staged_name': 'source_000000.pdb', 'source_meta': {'id': 'validated0'}}]))
    sent = []

    class Response:
        ok = True

        def json(self):
            return {'job_id': 'child0'}

    monkeypatch.setattr(spawn, 'check_existing_children', lambda *args, **kwargs: (False, [], {}))
    monkeypatch.setattr(spawn.requests, 'post', lambda url, json, timeout: (sent.append(json), Response())[1])
    result = spawn.spawn_jobs('parent0', staged, 1, 'batch0', 'Source',
                              'maturation_post_validation', '{}', 'http://example.invalid')
    assert result['spawned_jobs'] == 1
    assert sent[0]['params']['source_identity_json'] == str((staged / 'source_identity.json').resolve())


def test_unassociated_native_sample_remains_unassociated(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from maturation_identity import sample
    output = tmp_path / 'unknown.json'
    sample(tmp_path / 'missing.json', json.dumps({'id': 'new_sample',
                                                  'source_staged_name': 'source_000000.pdb'}),
           Path('new_sample_ppiflow.pdb'), output)
    evidence = json.loads(output.read_text())
    assert evidence['source'] is None
    assert evidence['validation_status'] == 'unvalidated'
