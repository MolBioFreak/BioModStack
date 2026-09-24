"""Retained refinement software tests. No model imports or native inference."""
import ast

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from ppiflow_sample_identity import collect, publish_sample
from validate_ppiflow_masks import validate_masks
from validate_ppiflow_roles import roles


def atom(chain, number, serial=1, insertion=''):
    return f'ATOM  {serial:5d}  CA  ALA {chain}{number:4d}{insertion:1s}   {0:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C\n'


def test_roles_read_same_document_once_and_preserve_case(tmp_path, monkeypatch):
    pdb = tmp_path / 'source.pdb'
    pdb.write_text(atom('h', 1) + atom('l', 1, 2) + atom('2', 10, 3) + atom('2', 10, 4, 'A'))
    read = Path.read_text
    reads = []
    def counted(path, *args, **kwargs):
        reads.append(path)
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', counted)
    assert roles(pdb, 'h', 'l', '2', '210,210A') == '2'
    assert reads == [pdb]
    with pytest.raises(ValueError, match='heavy chain'):
        roles(pdb, 'H', 'l', '2')
    with pytest.raises(ValueError, match='single-chain'):
        roles(pdb, 'h', 'l', '2,T')
    with pytest.raises(ValueError, match='absent'):
        roles(pdb, 'h', 'l', '2', '210B')


@pytest.mark.parametrize('fixed,movable,overlap', [
    ('h100', 'h100A', []), ('h100A', 'h100B', []),
    ('h100A', 'h100A', ['h100A']), ('h100', 'H100', []),
    ('210', '210', ['210']), ('h100-102', 'h101A', []),
    ('h100-102', 'h101', ['h101']),
])
def test_exact_mask_identity(fixed, movable, overlap):
    report = validate_masks(fixed, movable)
    assert report['overlap_positions'] == overlap
    assert report['valid'] == (not overlap)


@pytest.mark.parametrize('indices', [[], [0], [0, 2]])
def test_accounting_retains_zero_yield_and_missing_samples(tmp_path, indices):
    native = tmp_path / 'native'
    native.mkdir()
    for index in indices:
        pdb = native / f'arbitrary-{index}.pdb'
        pdb.write_text(f'sample bytes {index}')
        publish_sample(pdb, index)
    (native / 'unassociated.pdb').write_text('not a candidate')
    accounting = tmp_path / 'accounting.json'
    rows = collect(native, tmp_path / 'outputs', 'document', tmp_path / 'manifest.json',
                   requested_count=3, accounting_path=accounting)
    report = json.loads(accounting.read_text())
    assert report['requested_count'] == 3
    assert report['emitted_count'] == len(indices)
    assert report['missing_sample_indices'] == sorted(set(range(3)) - set(indices))
    assert report['missing_count'] == 3 - len(indices)
    assert [row['sample_index'] for row in rows] == indices


def test_pinned_native_mask_identity_and_cardinality_contract():
    source = os.environ.get('BMS_TEST_PPIFLOW_PARTIAL_SOURCE')
    if not source:
        pytest.skip('set BMS_TEST_PPIFLOW_PARTIAL_SOURCE for pinned source-only differential')
    path = Path(source) / 'sample_antibody_nanobody_partial.py'
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'expand_ranges', 'get_indices_from_spec'}]
    assert len(functions) == 2
    import Bio.PDB
    namespace = {'re': re, 'List': list, 'PDB': Bio.PDB}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    import io
    structure = Bio.PDB.PDBParser(QUIET=True).get_structure('fixture', io.StringIO(
        atom('h', 100) + atom('h', 100, 2, 'A') + atom('h', 101, 3) + atom('2', 10, 4)))
    for token, expected in [('h100', [0]), ('h100A', [1]), ('h100-101', [0, 2]), ('210', [3])]:
        assert namespace['get_indices_from_spec'](structure, namespace['expand_ranges'](token)) == expected
    native = path.read_text()
    assert 'for id in range(args.samples_per_target):' in native
    assert '"samples_per_target": 1' in native
    producer = (Path(source) / 'models_flow_module_antibody_partial.py').read_text()
    assert 'f"sample{batch_idx}.pdb"' in producer
    method = next(n for n in ast.walk(ast.parse(producer)) if isinstance(n, ast.FunctionDef) and n.name == 'test_step')
    assert not any(isinstance(n, ast.Return) for n in ast.walk(method))


@pytest.fixture
def nextflow(tmp_path):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('set BMS_TEST_NEXTFLOW_JAR for pinned non-science graphs')
    config = tmp_path / 'clean.config'
    config.write_text('process.executor = "local"\napptainer.enabled = false\ndocker.enabled = false\n')
    env = {**os.environ, 'NXF_HOME': str(tmp_path / 'nxf'), 'NXF_OFFLINE': 'true'}
    def run(workflow, params=None, inspect=False):
        command = ['java', '-jar', jar, '-C', str(config), '-log', str(tmp_path / 'nextflow.log')]
        if inspect:
            command += ['inspect', str(workflow)]
        else:
            settings = tmp_path / 'params.json'
            settings.write_text(json.dumps(params))
            command += ['run', str(workflow), '-ansi-log', 'false', '-params-file', str(settings),
                        '-work-dir', str(tmp_path / 'work'), '-with-trace', str(tmp_path / 'trace.tsv')]
        result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result
    return run


@pytest.mark.parametrize('workflow', ['binder_refinement', 'ppiflow_generator_design', 'maturation_child', 'antibody_denovo'])
def test_pinned_nextflow_compiles_retained_workflows(nextflow, workflow):
    nextflow(ROOT / 'workflows' / f'{workflow}.nf', inspect=True)


@pytest.mark.parametrize('light,emitted', [('', 0), ('l', 2)])
@pytest.mark.parametrize('source_count', [1, 2])
def test_real_partial_flow_transport_without_native_science(nextflow, tmp_path, light, emitted, source_count):
    native = tmp_path / 'native'
    native.mkdir()
    # Only the native entrypoint is replaced. Actual module shell, role/mask
    # scripts, sidecar collector, region preparation and publisher execute.
    (native / 'sample_antibody_nanobody_partial.py').write_text('''import argparse, json
from pathlib import Path
from ppiflow_sample_identity import publish_sample
p=argparse.ArgumentParser()
for key in ['complex_pdb','fixed_positions','cdr_position','start_t','samples_per_target','output_dir','retry_Limit','config','model_weights','antigen_chain','heavy_chain','light_chain','specified_hotspots','name']:
    p.add_argument('--'+key)
a=p.parse_args()
Path('native-argv.json').write_text(json.dumps(vars(a)))
out=Path(a.output_dir);out.mkdir()
for index in range(''' + str(emitted) + '''):
    pdb=out / ('unrelated'+str(index)+'.pdb')
    pdb.write_bytes(Path(a.complex_pdb).read_bytes())
    publish_sample(pdb,index)
''')
    module = tmp_path / 'ppiflow.nf'
    module.write_text((ROOT / 'modules/ppiflow.nf').read_text().replace('/app/ppiflow', str(native)))
    workflow = tmp_path / 'selected.nf'
    workflow.write_text((ROOT / 'workflows/binder_refinement.nf').read_text().replace('../modules/ppiflow.nf', str(module)))
    pdb = tmp_path / 'exact-document.pdb'
    pdb.write_text(atom('h', 1) + atom('h', 2, 2) + (atom('l', 1, 3) if light else '') + atom('T', 1, 4))
    config = native / 'config.yaml'
    config.write_text('non_science_fixture: true\n')
    identity = tmp_path / 'identity.json'
    pdbs, sources = [], []
    for index in range(source_count):
        snapshot = tmp_path / f'document-{index}.pdb'
        snapshot.write_bytes(pdb.read_bytes())
        pdbs.append(snapshot)
        sources.append({'staged_name': snapshot.name, 'source_meta': {
            'id': f'design-parent-{index}', 'target_state': 'alternate-state',
            'document_artifact_id': 9 + index, 'validation_status': 'validated', 'plddt': 99}})
    identity.write_text(json.dumps(sources))
    out = tmp_path / 'out'
    nextflow(workflow, dict(code_root=str(ROOT), out_dir=str(out), pdb_paths=','.join(map(str, pdbs)),
        source_identity_json=str(identity), binder_chains='h,l' if light else 'h', target_chains='T',
        framework_type='standard-fv' if light else 'nanobody', maturation_flow_enabled=True,
        ppiflow_heavy_chain='h', ppiflow_light_chain=light, ppiflow_antigen_chain='T',
        ppiflow_region_mode='all_antibody', ppiflow_samples_per_target=3, ppiflow_config=str(config),
        ppiflow_checkpoint_path='fixture-not-loaded.ckpt', ppiflow_retry_limit=10))
    accounting = [json.loads(path.read_text()) for path in out.rglob('*_ppiflow_accounting.json')]
    assert len(accounting) == source_count
    assert all(row['requested_count'] == 3 and row['emitted_count'] == emitted for row in accounting)
    records = [json.loads(path.read_text()) for path in out.rglob('generator_*.json')]
    assert len(records) == emitted * source_count
    assert len({row['id'] for row in records}) == len(records)
    assert {row['sample_index'] for row in records} == set(range(emitted))
    for row in records:
        assert row['source_structure_state'] == 'alternate-state'
        source_index = int(row['source_document_id'].removeprefix('design-parent-'))
        assert row['source_meta']['document_artifact_id'] == 9 + source_index
        assert row['validation_status'] == 'unvalidated' and 'plddt' not in row
    args = json.loads(next((tmp_path / 'work').rglob('native-argv.json')).read_text())
    assert args['heavy_chain'] == 'h' and args['light_chain'] == (light or None)
    assert args['antigen_chain'] == 'T' and args['samples_per_target'] == '3'
    trace = (tmp_path / 'trace.tsv').read_text()
    assert 'IdentifyAnchorResidues' not in trace and 'RunMaturationFAMPNN' not in trace


def test_actual_anchor_module_preserves_roles_and_json_transport(nextflow, tmp_path):
    code = tmp_path / 'code/scripts'
    code.mkdir(parents=True)
    (code / 'publish_binder_refinement.py').write_bytes((ROOT / 'scripts/publish_binder_refinement.py').read_bytes())
    # Replace only Rosetta preparation; exercise actual process arguments/files.
    (code / 'prepare_ppiflow_maturation.py').write_text('''import json, sys
from pathlib import Path
args={}
items=iter(sys.argv[1:])
for item in items:
    args[item]=True if item.startswith('--skip_') or item in ['--rotamer_enrichment','--relax_antibody_backbone_shell'] else next(items)
loops=json.loads(Path(args['--cdr_positions_by_loop_json']).read_text())
manual=json.loads(Path(args['--manual_cdr_definitions_json']).read_text())
assert isinstance(loops,dict),repr(loops)
assert isinstance(manual,list),repr(manual)
for key,value in args.items():
    if key.startswith('--output_'):
        Path(value).write_text('{}' if value.endswith('.json') else '')
Path(args['--output_enriched_pdb']).write_bytes(Path(args['--pdb']).read_bytes())
Path(args['--output_anchors']).write_text(json.dumps({'anchors':[], 'anchor_count':0}))
Path(args['--output_rotamer_enrichment']).write_text(json.dumps({'args':args,'loops':loops,'manual':manual}))
''')
    pdb = tmp_path / 'selected.pdb'
    pdb.write_text(atom('h', 1) + atom('T', 1, 2))
    loops, manual = {'H3': [1]}, [{'chain': 'h', 'start': 1, 'end': 1}]
    out = tmp_path / 'out'
    nextflow(ROOT / 'workflows/binder_refinement.nf', dict(code_root=str(code.parent),
        out_dir=str(out), pdb_paths=str(pdb), binder_chains='h', target_chains='T',
        maturation_anchors_enabled=True, maturation_repack_enabled=False,
        maturation_flow_enabled=False, maturation_redesign_enabled=False,
        cdr_positions_by_loop=json.dumps(loops), manual_cdr_definitions=json.dumps(manual)))
    record = json.loads(next(out.rglob('*_rotamer_enrichment.json')).read_text())
    assert record['loops'] == loops and record['manual'] == manual
    assert record['args']['--antibody_chains'] == 'h'
    assert record['args']['--antigen_chains'] == 'T'
    assert record['args']['--skip_region_resolution'] is True
    assert '--rotamer_enrichment' not in record['args']
    assert next(out.rglob('published/selected.pdb')).read_bytes() == pdb.read_bytes()


@pytest.mark.parametrize('count', [0, 2])
def test_iggm_descendant_publication_graph(nextflow, tmp_path, count):
    # Execute the production post-IgGM transformation and publication, replacing
    # only the scientific producer channel with inert document fixtures.
    source = (ROOT / 'workflows/antibody_denovo.nf').read_text()
    start = source.index('final_designs = matured_designs.flatMap')
    end = source.index('final_designs = PublishIgGMMaturedCandidates.out.candidates', start)
    block = source[start:end]
    paths = []
    for index in range(count):
        path = tmp_path / f'iggm-descendant-{index}.pdb'
        path.write_text(atom('h', index + 1))
        paths.append(str(path))
    channel = ("Channel.of(tuple([id:'parent', plddt:99, validation_status:'validated', target_state:'state-B'], "
               + '[' + ','.join(f"file('{path}')" for path in paths) + ']))') if paths else 'Channel.empty()'
    workflow = tmp_path / 'iggm-transport.nf'
    workflow.write_text("nextflow.enable.dsl = 2\ninclude { PublishIgGMMaturedCandidates } from '"
                        + str(ROOT / 'workflows/maturation_child_core.nf')
                        + "'\nworkflow {\n def matured_designs = " + channel + '\n' + block + '\n}\n')
    out = tmp_path / 'out'
    nextflow(workflow, dict(code_root=str(ROOT), out_dir=str(out)))
    rows = [json.loads(path.read_text()) for path in out.rglob('generator_*.json')]
    assert len(rows) == count
    for row in rows:
        assert row['id'].startswith('iggm-descendant-') and row['parent_id'] == 'parent'
        assert row['source'] == 'iggm_affinity_maturation'
        assert row['source_structure_state'] == 'state-B'
        assert row['validation_status'] == 'unvalidated' and 'plddt' not in row
        assert row['source_meta']['plddt'] == 99
