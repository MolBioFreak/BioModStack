"""Synthetic PDBs: source-owned export transport, not model-performance tests."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import ast
import os
import dataclasses
import numpy as np


@pytest.mark.parametrize('producer', ['ppiflow', 'fampnn'])
def test_all_sealed_source_hooks_compile(producer):
    root = Path(os.environ['BMS_G09_' + producer.upper() + '_SOURCE'])
    for relative in native.SOURCE_SHA256[producer]:
        compile(native.instrument_source(producer, relative, (root / relative).read_bytes()), relative, 'exec')


def test_preparation_uses_parser_identity_before_any_chain_restore(tmp_path):
    # Synthetic physical-engine seam. The real preparation function must capture
    # PDBInfo at import, not zip source chains onto an already relabelled pose.
    import importlib.util
    spec = importlib.util.spec_from_file_location('g09_prep', Path(__file__).resolve().parents[1] / 'scripts/prep_fampnn_designs.py')
    # PyRosetta unavailable: only this engine is injected, never source mapping.
    previous = sys.modules.get('pyrosetta')
    sys.modules['pyrosetta'] = SimpleNamespace()
    try:
        prep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prep)
    finally:
        if previous is None:
            sys.modules.pop('pyrosetta', None)
        else:
            sys.modules['pyrosetta'] = previous
    prepare = getattr(prep, 'prepare_maturation', None)
    assert callable(prepare), 'shared preparation native transport missing'
    reference, source, output = [tmp_path / n for n in ('ref.pdb', 'in.pdb', 'out.pdb')]
    reference.write_text(atom('H', 100, 'A') + atom('T', 9, x=20, serial=2))
    source.write_text(atom('A', 1) + atom('B', 1, x=20, serial=2))
    native.configure(reference, {'binder': ['H'], 'target': ['T']}, {'selected': [['H', 100, 'A']]})
    native.publish_partial(source, SimpleNamespace(residue_index=np.array([1, 1]), chain_index=np.array([0, 1])),
        {'bms_identity': np.array([[ord('H'), 100, ord('A')], [ord('T'), 9, 32]]), 'bms_offset': np.zeros((2, 3))}, 'AB')
    class Info:
        def chain(self, i): return ['H', 'T'][i-1]
        def number(self, i): return [100, 9][i-1]
        def icode(self, i): return ['A', ' '][i-1]
    class Pose:
        def pdb_info(self): return Info()
        def total_residue(self): return 2
        def dump_pdb(self, path): Path(path).write_bytes(source.read_bytes())
    prepare(source, output, Pose())
    assert native.read_transport(output)['domains']['selected']['pairs'] == [[['H', 100, 'A'], ['H', 100, 'A']]]
    class Lost(Info):
        def chain(self, i): return ['A', 'B'][i-1]
    pose = Pose()
    pose.pdb_info = lambda: Lost()
    with pytest.raises(ValueError, match='parser identity'):
        prepare(source, tmp_path / 'bad.pdb', pose)
    assert not (tmp_path / 'bad.pdb').exists()


def test_transport_authority_survives_task_reference_removal(tmp_path):
    reference, output = tmp_path/'ref.pdb', tmp_path/'out.pdb'
    reference.write_text(atom('H', 2) + atom('T', 3, serial=2))
    native.configure(reference, {'binder': ['H'], 'target': ['T']}, {})
    authority = native._AUTHORITY.get()
    reference.unlink()
    output.write_text(atom('A', 1) + atom('B', 1, serial=2))
    native._publish(output, [dict(source=['H', 2, ''], exported=['A', 1, ''], offset=[0., 0., 0.]),
                             dict(source=['T', 3, ''], exported=['B', 1, ''], offset=[0., 0., 0.])], 'ppiflow', authority)
    assert native.read_transport(output)['candidate_sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import maturation_correspondence as correspondence
import maturation_native_adapter as native


@pytest.fixture(autouse=True)
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path))


def test_request_compilation_preserves_exact_selected_domains(tmp_path):
    build = getattr(native, 'request_domains', None)
    assert callable(build), 'native launcher request compilation missing'
    selected, loops = tmp_path/'selected.txt', tmp_path/'loops.json'
    selected.write_text('H100A,H101')
    loops.write_text(json.dumps({'H3': ['H100A', 'H101']}))
    assert build(selected, loops) == {'selected': [['H', 100, 'A'], ['H', 101, '']], 'H3': [['H', 100, 'A'], ['H', 101, '']]}


def test_workflow_stages_native_transport_to_every_consumer():
    root = Path(__file__).resolve().parents[1]
    module = (root/'modules/ppiflow.nf').read_text()
    workflow = (root/'workflows/maturation_child_core.nf').read_text()
    assert '--producer ppiflow' in module, 'native PPIFlow launcher is not wired'
    assert '--producer fampnn' in module
    assert '--maturation_transport' in module
    assert module.count('--comparison-request') == 2
    assert 'path(comparison_requests)' in module
    assert 'maturation_comparison_path' in workflow


def native_functions(producer, relative, names, namespace):
    """Execute only data definitions from sealed source, excluding all models."""
    import __future__
    root = Path(os.environ['BMS_G09_' + producer.upper() + '_SOURCE'])
    text = native.instrument_source(producer, relative, (root / relative).read_bytes())
    tree = ast.parse(text)
    selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), relative, 'exec', flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def native_constants():
    # Execute the real literal/derived alphabet definitions, not a replacement
    # atom dialect. Irrelevant training tables require unavailable dependencies.
    root = Path(os.environ['BMS_G09_FAMPNN_SOURCE'])
    source = ast.parse((root/'fampnn/data/residue_constants.py').read_text())
    required = {'restypes', 'restype_order', 'restype_num', 'restype_1to3', 'restype_3to1', 'atom_types', 'atom_order', 'atom_type_num', 'ncaa_mapping'}
    nodes = [n for n in source.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in required for t in n.targets)]
    scope = {'np': np}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'native_residue_constants', 'exec'), scope)
    return SimpleNamespace(**{k: scope[k] for k in required})


def test_real_native_parsers_and_fampnn_export_preserve_insertion_identity(tmp_path):
    import io
    from Bio.PDB import PDBParser, MMCIFParser, Structure
    from Bio.PDB.Atom import DisorderedAtom
    constants = native_constants()
    scope = dict(np=np, dataclasses=dataclasses, _bms=native, residue_constants=constants, Path=Path,
                 PDBParser=PDBParser, MMCIFParser=MMCIFParser, Structure=Structure, DisorderedAtom=DisorderedAtom,
                 PDB_CHAIN_IDS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', PDB_MAX_CHAINS=62,
                 __name__=__name__)
    native_functions('fampnn', 'fampnn/data/protein.py', ['Protein', 'read_pdb', 'to_pdb', '_chain_end', 'are_atoms_bonded'], scope)
    reference, candidate = tmp_path/'ref.pdb', tmp_path/'native.pdb'
    reference.write_text(atom('H', 100, 'A') + atom('H', 101, x=12, serial=2) + atom('T', 9, x=20, serial=3))
    # Exercise the actual PPIFlow parser, centering and native PDB writer,
    # without loading the physical generation model. Synthetic CA-only fixture.
    import collections
    import re
    pp = dict(scope, io=io, collections=collections, re=re, os=os)
    native_functions('ppiflow', 'data/protein.py', ['Protein', 'to_pdb', '_chain_end'], pp)
    native_functions('ppiflow', 'data/parsers.py', ['process_chain'], pp)
    native_functions('ppiflow', 'data/utils.py', ['parse_chain_feats', 'concat_np_features'], pp)
    pp['protein'] = SimpleNamespace(Protein=pp['Protein'], to_pdb=pp['to_pdb'], PDB_CHAIN_IDS=scope['PDB_CHAIN_IDS'])
    native_functions('ppiflow', 'analysis/utils.py', ['create_full_prot', 'write_prot_to_pdb'], pp)
    chains = PDBParser(QUIET=True).get_structure('fixture', io.StringIO(reference.read_text())).get_chains()
    features = pp['concat_np_features']([dataclasses.asdict(pp['process_chain'](chain, i)) for i, chain in enumerate(chains)], False)
    features = pp['parse_chain_feats'](features)
    native.configure(reference, {'binder': ['H'], 'target': ['T']}, {'selected': [['H', 100, 'A']]})
    partial = tmp_path/'partial.pdb'
    pp['write_prot_to_pdb'](features['atom_positions'], str(partial), no_indexing=True, bms_features=features)
    assert correspondence.pdb_identities(partial.read_bytes()) == correspondence.pdb_identities(reference.read_bytes())
    assert '  10.000' in partial.read_text()
    # Only PyRosetta is injected. Its parser-owned PDBInfo is captured by the
    # real shared preparation function before any chain-order restoration.
    prep_tree = ast.parse((Path(__file__).resolve().parents[1]/'scripts/prep_fampnn_designs.py').read_text())
    prep_scope = {}
    exec(compile(ast.Module(body=[n for n in prep_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare_maturation'], type_ignores=[]), 'prep_fampnn_designs.py', 'exec'), prep_scope)
    keys = correspondence.pdb_identities(partial.read_bytes())
    pose = SimpleNamespace(total_residue=lambda: len(keys),
        pdb_info=lambda: SimpleNamespace(chain=lambda i: keys[i-1][0], number=lambda i: keys[i-1][1], icode=lambda i: keys[i-1][2]),
        dump_pdb=lambda path: Path(path).write_bytes(partial.read_bytes()))
    prepared = tmp_path/'prepared.pdb'
    prep_scope['prepare_maturation'](partial, prepared, pose)
    parsed, chain_map = scope['read_pdb'](str(prepared))
    assert parsed.bms_identity.tolist() == [[72, 100, 65], [72, 101, 32], [84, 9, 32]]
    assert parsed.residue_index.tolist() == [101, 102, 9]
    assert chain_map == {'H': 0, 'T': 1}
    # Use the actual native exporter; it uses native chain/residue arrays, not
    # original author IDs. The writer-owned hook restores that known transform.
    candidate.write_text(scope['to_pdb'](parsed))
    native.configure(reference, {'binder': ['H'], 'target': ['T']}, {'selected': [['H', 100, 'A']]})
    context = native.fampnn_input(prepared, vars(parsed))
    native.save_fampnn(lambda samples, paths: native.publish_fampnn(paths[0], parsed, scope['PDB_CHAIN_IDS']), {}, [candidate], [context])
    assert correspondence.pdb_identities(candidate.read_bytes()) == [('H', 100, 'A'), ('H', 101, ''), ('T', 9, '')]
    assert native.read_transport(candidate)['domains']['selected']['pairs'] == [[['H', 100, 'A'], ['H', 100, 'A']]]
    pp = dict(scope)
    native_functions('ppiflow', 'data/parsers.py', ['process_chain'], pp)
    chain = next(PDBParser(QUIET=True).get_structure('fixture', io.StringIO(reference.read_text())).get_chains())
    # PPIFlow Protein has fewer native constructor fields; use its real class.
    native_functions('ppiflow', 'data/protein.py', ['Protein'], pp)
    result = pp['process_chain'](chain, 7)
    assert result.residue_index.tolist() == [100, 101]
    assert result.bms_identity.tolist() == [[72, 100, 65], [72, 101, 32]]
    assert result.chain_index.tolist() == [7, 7]


def test_partial_publication_waits_until_native_retry_checks_finish(tmp_path):
    reference, candidate = tmp_path/'ref.pdb', tmp_path/'candidate.pdb'
    reference.write_text(atom('H', 100, 'A') + atom('T', 9, x=20, serial=2))
    native.configure(reference, {'binder': ['H'], 'target': ['T']}, {})
    features = {'bms_identity': np.array([[72, 100, 65], [84, 9, 32]]), 'bms_offset': np.ones((2, 3)), 'bms_defer': True}
    prot = SimpleNamespace(chain_index=np.array([0, 1]), residue_index=np.array([1, 1]))
    for value in (1., 2.):
        candidate.write_text(atom('A', 1, x=value) + atom('B', 1, x=value, serial=2))
        before = candidate.read_bytes()
        native.publish_partial(candidate, prot, features, 'AB')
        assert candidate.read_bytes() == before, 'transport changed native retry/clash inputs'
        assert not Path(str(candidate)+'.comparison.json').exists()
    native.flush_partial(candidate)
    assert '   3.000' in candidate.read_text()
    assert native.read_transport(candidate)['native_export']['sha256'] == hashlib.sha256(before).hexdigest()


def test_native_sidecar_is_staged_into_real_nextflow_scorer(tmp_path):
    import subprocess
    test_real_native_parsers_and_fampnn_export_preserve_insertion_identity(tmp_path)
    sidecar = tmp_path/'native.pdb.comparison.json'
    request = json.loads(sidecar.read_text())
    request['reference_sha256'] = '0' * 64  # controlled negative, never invoke physics
    sidecar.write_text(json.dumps(request))
    (tmp_path/'positions.txt').write_text('H100A')
    (tmp_path/'loops.json').write_text('{}')
    root = Path(__file__).resolve().parents[1]
    harness = tmp_path/'native_transport.nf'
    harness.write_text(f'''nextflow.enable.dsl=2
params.code_root='{root}'
params.out_dir='{tmp_path}/published'
params.core_protein_scientific_contract=1
include {{ ScoreMaturationImprovement }} from '{root}/modules/ppiflow.nf'
workflow {{
    ScoreMaturationImprovement(Channel.of(tuple([id:'native'], file('{tmp_path}/ref.pdb'), [file('{tmp_path}/native.pdb'), file('{sidecar}')], file('{tmp_path}/positions.txt'), file('{tmp_path}/loops.json'))))
}}
''')
    env = dict(os.environ, NXF_OFFLINE='true', NXF_HOME=str(tmp_path/'nxf'), BMS_SCIENTIFIC_ARTIFACT_ROOT=str(tmp_path/'artifacts'))
    command = ['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], 'run', str(harness), '-work-dir', str(tmp_path/'work')]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = list((tmp_path/'work').glob('*/*/scores/native_maturation_score.json'))
    assert len(outputs) == 1
    score = json.loads(outputs[0].read_text())
    assert score['unavailable_reason'] == 'reference_identity_mismatch'
    assert score['comparison_request_sha256'] == hashlib.sha256(sidecar.read_bytes()).hexdigest()


def test_source_adapter_rejects_unrecognized_source():
    with pytest.raises(ValueError, match='source identity'):
        native.instrument_source('ppiflow', 'data/parsers.py', b'changed upstream')


def test_source_adapter_exposes_only_identity_frame_transport():
    # The immutable source map is independently checked against extracted
    # installed bytes in the static source gate; no scientific runtime here.
    assert callable(getattr(native, 'install', None)), 'G-09 source loader is missing'
    assert callable(getattr(native, 'publish_partial', None)), 'G-09 native writer hook is missing'
    assert callable(getattr(native, 'publish_fampnn', None)), 'G-09 FA-MPNN writer hook is missing'


def atom(chain, number, insertion='', x=10.0, serial=1):
    return (f'ATOM  {serial:5d}  CA  ALA {chain}{number:4d}{insertion:1s}   '
            f'{x:8.3f}{0.:8.3f}{0.:8.3f}{1.:6.2f}{0.:6.2f}           C  \n')


def test_native_writer_publishes_source_identity_and_restores_frame(tmp_path):
    # Native writer selected modeled tensor indices [2, 0], not PDB order.
    reference = tmp_path / 'reference.pdb'
    reference.write_text(atom('H', 100, 'A', 10) + atom('H', 101, '', 12, 2) + atom('T', 9, '', 20, 3))
    candidate = tmp_path / 'sample.pdb'
    candidate.write_text(atom('A', 1, '', 15) + atom('B', 1, '', 5, 2))
    publisher = getattr(correspondence, 'publish_native_export', None)
    assert callable(publisher), 'G-09 producer-owned publication is missing'
    request = publisher(reference, candidate,
        records=[{'source': ['T', 9, ''], 'exported': ['A', 1, ''], 'offset': [5., 0., 0.]},
                 {'source': ['H', 100, 'A'], 'exported': ['B', 1, ''], 'offset': [5., 0., 0.]}],
        roles={'binder': ['H'], 'target': ['T']},
        domains={'selected': [['H', 100, 'A']], 'H3': [['H', 100, 'A']]},
        source_evidence={'producer': 'synthetic_source_writer'})
    assert 'H 100A' in candidate.read_text()
    assert '  10.000' in candidate.read_text()
    assert request['candidate_sha256'] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert request['domains']['whole_binder']['reference'] == [['H', 100, 'A'], ['H', 101, '']]
    assert request['domains']['whole_binder']['candidate'] == [['H', 100, 'A']]
    assert request['domains']['whole_binder']['pairs'] == [[['H', 100, 'A'], ['H', 100, 'A']]]
    r = {('H', 100, 'A'): SimpleNamespace(x=10., y=0., z=0.), ('H', 101, ''): SimpleNamespace(x=12., y=0., z=0.)}
    c = {('H', 100, 'A'): SimpleNamespace(x=10., y=0., z=0.)}
    comparison = correspondence.compare_request_domains(request, r, c, set(r), set(c))
    assert comparison['whole_binder']['value'] is None
    assert comparison['whole_binder']['reference_coverage'] == .5
    assert comparison['whole_binder']['candidate_coverage'] == 1.
    assert comparison['selected']['value'] == 0.
    assert json.loads(Path(str(candidate) + '.comparison.json').read_text()) == request


@pytest.mark.parametrize('records', [
    [{'source': ['H', 2, ''], 'exported': ['Z', 1, ''], 'offset': [0., 0., 0.]}],
    [{'source': ['H', 2, ''], 'exported': ['A', 1, ''], 'offset': [float('nan'), 0., 0.]}],
    [{'source': ['H', 2, ''], 'exported': ['A', 1, ''], 'offset': [0., 0., 0.]},
     {'source': ['H', 3, ''], 'exported': ['A', 1, ''], 'offset': [0., 0., 0.]}],
])
def test_native_export_rejects_unowned_atoms_without_overwriting(tmp_path, records):
    publisher = getattr(correspondence, 'publish_native_export', None)
    assert callable(publisher), 'G-09 producer-owned publication is missing'
    r, c = tmp_path/'r.pdb', tmp_path/'c.pdb'
    r.write_text(atom('H', 2) + atom('T', 1, serial=2))
    c.write_text(atom('A', 1))
    before = c.read_bytes()
    with pytest.raises(ValueError):
        publisher(r, c, records=records, roles={'binder': ['H'], 'target': ['T']}, domains={}, source_evidence={})
    assert c.read_bytes() == before
    assert not Path(str(c) + '.comparison.json').exists()
