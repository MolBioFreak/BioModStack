"""Inert native producer/transport boundary; fixtures are not science evidence."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ligandmpnn_runner', ROOT / 'scripts/run_ligandmpnn_design.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def request(mode='ligand_aware'):
    return {'contract': runner.CONTRACT, 'model_type': 'ligand_mpnn', 'mode': mode,
            'options': {'seed': 0, 'temperature': 0.23, 'designed_chains': ['b'],
                        'atomize_side_chains': False, 'remove_ccds': []},
            'annotations': {'metal_type': 'Mg2+'}, 'write_fasta': True, 'write_structures': True}


class InertResult:
    def __init__(self, options, batch, design):
        self.input_dict = options
        self.output_dict = {'model_type': 'ligand_mpnn', 'batch_idx': batch, 'design_idx': design,
                            'designed_sequence': 'AG', 'sequence_recovery': 0.5,
                            'ligand_interface_sequence_recovery': float('nan')}

    def write_structure(self, *, base_path):
        Path(base_path).with_suffix('.cif').write_text('data_INERT_TEST_ONLY\n')

    def write_fasta(self, *, base_path):
        Path(base_path).with_suffix('.fa').write_text('>INERT_TEST_ONLY\nAG\n')


class InertEngine:
    def __init__(self, **kwargs):
        assert kwargs['model_type'] == 'ligand_mpnn'
        assert kwargs['is_legacy_weights'] is True
        assert kwargs['checkpoint_path'] == runner.CHECKPOINT
        assert kwargs['write_fasta'] is False

    def run(self, *, input_dicts, atom_arrays):
        assert atom_arrays is None
        options, = input_dicts
        assert options['designed_chains'] == ['b']
        assert 'metal_type' not in options
        # Deliberately non-contiguous and out of order, so enumeration cannot join.
        return [InertResult(options, 7, 11), InertResult(options, 2, 4)]


@pytest.mark.parametrize('mode', sorted(runner.MODES))
def test_native_object_identity_and_returned_publication(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(runner.importlib.metadata, 'version', lambda _: 'inert-test')
    source = tmp_path / 'source.cif'
    source.write_text('data_INERT_INPUT\n')
    doc = runner.execute(request(mode), source, tmp_path / 'worker', engine_class=InertEngine)
    assert [r['producer']['design_idx'] for r in doc['records']] == [11, 4]
    assert doc['records'][0]['native_output']['ligand_interface_sequence_recovery'] is None
    assert doc['records'][0]['artifacts'][0]['path'] == 'design_b7_d11.cif'
    returned = tmp_path / 'returned'
    shutil.copytree(tmp_path / 'worker', returned)
    shutil.rmtree(tmp_path / 'worker')
    source.unlink()
    sys.path.insert(0, str(ROOT / 'platform/api'))
    try:
        from services.ligandmpnn_design import read_design_result
        assert read_design_result(returned) == doc
        (returned / 'design_b7_d11.cif').write_text('changed')
        with pytest.raises(ValueError, match='digest'):
            read_design_result(returned)
    finally:
        sys.path.pop(0)


def native_source(name):
    directory = os.environ.get('BMS_TEST_LIGANDMPNN_NATIVE_SOURCE')
    if not directory:
        pytest.skip('Set BMS_TEST_LIGANDMPNN_NATIVE_SOURCE to read-only installed Foundry source export')
    return (Path(directory) / name).read_text()


def test_installed_defaults_match_typed_request():
    tree = ast.parse(native_source('utils_inference.py'))
    defaults = next(ast.literal_eval(n.value) for n in tree.body
                    if isinstance(n, ast.AnnAssign) and getattr(n.target, 'id', '') == 'MPNN_PER_INPUT_INFERENCE_DEFAULTS')
    sys.path.insert(0, str(ROOT / 'platform/api'))
    try:
        from services.ligandmpnn_design import NativeOptions
        expected = {k: v for k, v in defaults.items()
                    if k not in {'structure_path', 'name', 'features_to_return', 'undesired_res_names'}}
        assert NativeOptions().model_dump() == expected
    finally:
        sys.path.pop(0)


def test_installed_fasta_writer_uses_producer_keys(tmp_path):
    tree = ast.parse(native_source('utils_inference.py'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MPNNInferenceOutput')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'write_fasta')
    namespace = {'Path': Path, 'PathLike': os.PathLike}
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<installed-fasta-writer>', 'exec'), namespace)
    result = InertResult({'name': 'design'}, 7, 11)
    namespace['write_fasta'](result, base_path=tmp_path / 'native')
    fasta = (tmp_path / 'native.fa').read_text()
    assert fasta.startswith('>design_b7_d11, sequence_recovery=0.5000')
    assert fasta.endswith('\nAG\n')


def test_installed_engine_run_preserves_native_keys(tmp_path, monkeypatch):
    """Execute installed engine.run orchestration; replace only science batch."""
    tree = ast.parse(native_source('inference_engines_mpnn.py'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MPNNInferenceEngine')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    namespace = {'Any': object, 'AtomArray': object, 'MPNNInferenceOutput': object,
                 'ranked_logger': SimpleNamespace(info=lambda *a: None),
                 'MPNNInferenceInput': SimpleNamespace(from_atom_array_and_dict=lambda **kw:
                     SimpleNamespace(atom_array=None, input_dict={**kw['input_dict'], 'seed': None, 'number_of_batches': 2}))}
    exec(compile(ast.Module(body=[method], type_ignores=[]), '<installed-engine-run>', 'exec'), namespace)
    class NativeOrchestration(InertEngine):
        run = namespace['run']
        def _run_batch(self, *, atom_array, input_dict, batch_idx):
            return [InertResult(input_dict, batch_idx, 9)]
        def _write_outputs(self, results):
            assert len(results) == 2
    monkeypatch.setattr(runner.importlib.metadata, 'version', lambda _: 'inert-test')
    source = tmp_path / 'source.pdb'
    source.write_text('INERT\n')
    doc = runner.execute(request(), source, tmp_path / 'out', engine_class=NativeOrchestration)
    assert [r['producer'] for r in doc['records']] == [
        {'name': 'design', 'batch_idx': 0, 'design_idx': 9},
        {'name': 'design', 'batch_idx': 1, 'design_idx': 9}]


@pytest.mark.parametrize('transport', ['shell', 'nextflow'])
def test_actual_nextflow_shell_with_inert_native_module(tmp_path, transport):
    """Run the production shell/graph with only native science replaced."""
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if transport == 'nextflow' and not jar:
        pytest.skip('Set BMS_TEST_NEXTFLOW_JAR for real inert Nextflow transport')
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'source.cif').write_text('data_INERT\n')
    (task / 'request.json').write_text(json.dumps(request()))
    fake = tmp_path / 'fake'
    (fake / 'mpnn/inference_engines').mkdir(parents=True)
    (fake / 'mpnn/__init__.py').write_text('')
    (fake / 'mpnn/inference_engines/__init__.py').write_text('')
    # Load explicitly named inert fixture classes, never real model code.
    (fake / 'mpnn/inference_engines/mpnn.py').write_text(
        'import runpy\nMPNNInferenceEngine = runpy.run_path(' + repr(str(Path(__file__))) + ')["InertEngine"]\n')
    dist = fake / 'rc_foundry-0.1.9.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text('Name: rc-foundry\nVersion: 0.1.9\n')
    shim = fake / 'apptainer'
    shim.write_text('#!' + sys.executable + '\nimport os,sys\nargs=sys.argv[1:]\ni=args.index("python")\nos.execv(sys.executable, [sys.executable, os.environ["INERT_RUNNER"], *args[i+2:]])\n')
    shim.chmod(0o755)
    module = (ROOT / 'modules/ligandmpnn_design.nf').read_text()
    shell = module.split('    """', 1)[1].split('    """', 1)[0]
    for key, value in {'params.gpu_id': '0', 'runner': str(ROOT / 'scripts/run_ligandmpnn_design.py'),
                       'image': 'INERT_IMAGE', 'request': 'request.json', 'source': 'source.cif'}.items():
        shell = shell.replace('${' + key + '}', value)
    shell = shell.replace('\\$', '$')
    env = {**os.environ, 'PATH': str(fake) + os.pathsep + os.environ['PATH'],
           'PYTHONPATH': str(fake), 'INERT_RUNNER': str(ROOT / 'scripts/run_ligandmpnn_design.py')}
    if transport == 'shell':
        command = ['bash', '-c', shell]
        output = task / 'ligandmpnn_design'
    else:
        config = tmp_path / 'inert.config'
        config.write_text('process.executor = "local"\n')
        env.update(NXF_HOME=str(tmp_path / 'nxf-home'), NXF_OFFLINE='true')
        output = tmp_path / 'published/ligandmpnn_design'
        command = ['java', '-jar', jar, '-C', str(config), 'run',
                   str(ROOT / 'workflows/ligandmpnn_design.nf'),
                   '-ansi-log', 'false', '-work-dir', str(tmp_path / 'work'),
                   '--code_root', str(ROOT), '--container_dir', 'INERT_IMAGES',
                   '--gpu_id', '0', '--out_dir', str(output.parent),
                   '--ligandmpnn_design_request', str(task / 'request.json'),
                   '--ligandmpnn_design_input', str(task / 'source.cif')]
    completed = subprocess.run(command, cwd=task, env=env, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    doc = json.loads((output / 'manifest.json').read_text())
    assert [r['producer']['design_idx'] for r in doc['records']] == [11, 4]
    assert doc['request']['options']['seed'] == 0
