"""Historical maturation transport/publication; inert science executables only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_binder_refinement_completion import ROOT, atom, nextflow


@pytest.mark.parametrize('emitted,associated', [(0, True), (2, True), (2, False)])
def test_historical_child_to_actual_collector(nextflow, tmp_path, emitted, associated, monkeypatch):
    scripts = tmp_path / 'code/scripts'
    scripts.mkdir(parents=True)
    # Preserve the production shell and all identity/filter/publication consumers.
    # Only Rosetta preparation/scoring and native sampling are inert fixtures.
    for source in (ROOT / 'scripts').iterdir():
        if source.name not in {'prepare_ppiflow_maturation.py', 'score_maturation.py'}:
            (scripts / source.name).symlink_to(source, target_is_directory=source.is_dir())
    (scripts / 'prepare_ppiflow_maturation.py').write_text('''import json, sys
from pathlib import Path
args={}
items=iter(sys.argv[1:])
for item in items:
    args[item]=True if item.startswith('--skip_') or item in ['--rotamer_enrichment','--relax_antibody_backbone_shell'] else next(items)
for key,value in args.items():
    if key.startswith('--output_'):
        Path(value).write_text('{}' if value.endswith('.json') else '')
Path(args['--output_enriched_pdb']).write_bytes(Path(args['--pdb']).read_bytes())
Path(args['--output_anchors']).write_text(json.dumps({'anchors':[], 'anchor_count':1, 'non_science_fixture':True}))
''')
    (scripts / 'score_maturation.py').write_text('''import sys, json
from pathlib import Path
Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps({'non_science_fixture':True}))
''')
    native = tmp_path / 'native'
    native.mkdir()
    (native / 'sample_antibody_nanobody_partial.py').write_text('''import sys
from pathlib import Path
from ppiflow_sample_identity import publish_sample
args=dict(zip(sys.argv[1::2],sys.argv[2::2]))
out=Path(args['--output_dir']);out.mkdir()
for index in range(''' + str(emitted) + '''):
    pdb=out / ('fixture-'+str(index)+'.pdb')
    pdb.write_bytes(Path(args['--complex_pdb']).read_bytes())
    publish_sample(pdb,index)
''')
    config = native / 'config.yaml'
    config.write_text('non_science_fixture: true\n')
    module = tmp_path / 'ppiflow.nf'
    module.write_text((ROOT / 'modules/ppiflow.nf').read_text().replace('/app/ppiflow', str(native)))
    core = tmp_path / 'maturation_child_core.nf'
    core.write_text((ROOT / 'workflows/maturation_child_core.nf').read_text()
                    .replace('../modules/ppiflow.nf', str(module))
                    .replace('../modules/utils/anarci', str(ROOT / 'modules/utils/anarci')))
    workflow = tmp_path / 'maturation_child.nf'
    workflow.write_bytes((ROOT / 'workflows/maturation_child.nf').read_bytes())
    evidence = {'id': 'validated-parent', 'structure_state': 'state-B', 'target_state': 'state-A',
                'document_artifact_id': 73, 'validation_status': 'validated', 'plddt': 99,
                'iptm': 0.99, 'passed': True, 'confidence': {'accepted': True},
                'provenance': {'validation': 'parent-only'}, 'custom_scientific_score': 42}
    original = tmp_path / 'original.pdb'
    original.write_text(atom('H', 1) + atom('T', 1, 2))
    selected = tmp_path / 'selected.json'
    selected.write_text(json.dumps([{'path': str(original), 'meta': evidence}]))
    staged = tmp_path / 'staged'
    subprocess.run([sys.executable, str(ROOT / 'scripts/maturation_identity.py'),
                    'stage', str(selected), str(staged)], check=True)
    identity = staged / 'source_identity.json'
    if not associated:
        identity.write_text('[]')
    source_bytes = identity.read_bytes()
    out = tmp_path / 'out'
    nextflow(workflow, dict(code_root=str(scripts.parent), out_dir=str(out),
        pdb_paths=str(staged / 'source_000000.pdb'), source_identity_json=str(identity),
        framework_type='nanobody', antibody_chains='H', antigen_chains='T',
        ppiflow_samples_per_target=3, ppiflow_config=str(config),
        ppiflow_checkpoint_path='fixture-not-loaded.ckpt'))
    assert identity.read_bytes() == source_bytes
    rows = [json.loads(p.read_text()) for p in (out / 'run/ppiflow/sample_identity').glob('*_sample_identity.json')]
    assert len(rows) == emitted
    assert {r['sample_meta']['sample_index'] for r in rows} == set(range(emitted))
    for row in rows:
        meta = row['sample_meta']
        assert meta['validation_status'] == row['validation_status'] == 'unvalidated'
        assert meta['terminal_producer'] == 'ppiflow_maturation_post_validation'
        assert meta['source_staged_name'] == 'source_000000.pdb'
        assert meta['parent_id'] == 'source_000000'
        assert meta['id'] == Path(row['pdb_name']).stem
        assert meta['source_meta'] == (evidence if associated else {})
        assert meta['source_document_id'] == ('validated-parent' if associated else None)
        assert meta['source_structure_state'] == ('state-B' if associated else None)
        assert not {'plddt', 'iptm', 'passed', 'confidence', 'provenance', 'custom_scientific_score'} & meta.keys()
        if associated:
            assert row['source']['source_meta'] == evidence
        else:
            assert row['source'] is None
    # Exercise the existing worker inventory and read the exact returned bytes.
    # This is local transport, not a remote worker/native inference acceptance.
    import hashlib
    import shutil
    monkeypatch.syspath_prepend(str(ROOT / 'platform/api'))
    from tools import bms_remote_worker as worker
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    envelope = dict(output_directory=str(out), attempt_id='fixture-attempt',
                    job_id='fixture-job', source_revision='a' * 40, source_tree='b' * 40)
    worker.envelope_path(attempt).write_text(json.dumps(envelope))
    manifest = worker.build_result_manifest(attempt, envelope, 0)
    names = {record['relative_path'] for record in manifest['artifacts']}
    assert 'run/ppiflow/sample_identity/source_000000_ppiflow_accounting.json' in names
    returned = tmp_path / 'returned'
    for record in manifest['artifacts']:
        source, destination = out / record['relative_path'], returned / record['relative_path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == record['sha256']
    shutil.rmtree(out)
    out = returned
    collection = tmp_path / 'collection'
    collection.mkdir()
    children = tmp_path / 'children.json'
    children.write_text(json.dumps({'child_output_dirs': [str(out)]}))
    env = {k: v for k, v in os.environ.items() if not k.startswith('BMS_COMPONENT_')}
    subprocess.run([sys.executable, str(ROOT / 'scripts/collect_maturation_outputs.py'),
                    '--child_outputs_json', str(children)], cwd=collection, env=env, check=True)
    report = json.loads((collection / 'collection_manifest.json').read_text())
    assert report['count_pdbs'] == emitted
    assert len(report['samples']) == emitted
    assert sorted((r['identity'] for r in report['samples']), key=lambda r: r['pdb_name']) == sorted(rows, key=lambda r: r['pdb_name'])
    accounting = [p for p in report['collected_jsons'] if p.endswith('_ppiflow_accounting.json')]
    assert len(accounting) == 1
    retained = json.loads((collection / accounting[0]).read_text())
    assert retained['requested_count'] == 3 and retained['emitted_count'] == emitted
    assert retained['missing_sample_indices'] == list(range(emitted, 3))
    for name in report['collected_jsons']:
        source = next(out.rglob(name))
        assert (collection / name).read_bytes() == source.read_bytes()
    trace = (tmp_path / 'trace.tsv').read_text()
    assert 'RunPartialFlow' in trace
    assert ('PublishMaturationSampleIdentity' in trace) == bool(emitted)
    assert 'RunMaturationFAMPNN' not in trace and 'ANARCII' not in trace
