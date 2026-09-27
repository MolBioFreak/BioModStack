"""CPU-only native-parser/process tests. All science kernels/bytes are INERT.

Installed images are reused without --nv, downloads, checkpoint loading, provider
submission, or worker startup. These are transport tests, not scientific evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
IMAGES = Path('/mnt/BioModStack/apptainer')


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/shape_blueprint' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def native(image, code, *args, env=None):
    path = IMAGES / image
    if not path.is_file() or not shutil.which('apptainer'):
        pytest.skip('Installed pinned image and apptainer required; never download')
    cmd = ['apptainer', 'exec', '--cleanenv', '--no-home', '--bind', str(ROOT) + ',' + os.environ['TMPDIR']]
    import tempfile
    cache = Path(tempfile.mkdtemp(prefix='shape-inert-', dir=os.environ['TMPDIR']))
    for key, value in dict(HOME=cache, TMPDIR=cache, NUMBA_CACHE_DIR=cache, MPLCONFIGDIR=cache, XDG_CACHE_HOME=cache, **(env or {})).items():
        cmd += ['--env', key + '=' + str(value)]
    interpreter = ['/bin/micromamba', 'run', '-n', 'mpnn', 'python'] if image == 'dl_binder_design.sif' else ['python']
    completed = subprocess.run(cmd + [str(path)] + interpreter + ['-c', code, *map(str, args)],
                               text=True, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def backbone(path):
    path.write_text(''.join(f'ATOM  {i:5d}  CA  GLY A{i:4d}    {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\n' for i in range(1, 6)) + 'END\n')


def request(engine, settings, count=1):
    value = dict(schema='bms_shape_design_request_v3', sequence_engine=engine,
                 sequences_per_backbone=count, seed=19, sequence_policy='design',
                 sequence_settings=settings, requested_sequence_settings=settings,
                 sequence_settings_identity={'engine': engine})
    value['request_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


def test_caliby_real_nextflow_leaf_and_installed_api(tmp_path):
    """Real Shape leaf -> preparation -> ordinary runner -> installed API."""
    import ast
    owner = ROOT / 'platform/api/services/caliby_native.py'
    spec = importlib.util.spec_from_file_location('shape_test_caliby_owner', owner)
    contract = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contract
    spec.loader.exec_module(contract)
    fixture = tmp_path / 'fixture'
    package = fixture / 'caliby'
    (package / 'eval/eval_utils').mkdir(parents=True)
    api = native('caliby.sif', "from pathlib import Path; print(Path('/opt/caliby/caliby/api.py').read_text())")
    (package / 'api.py').write_text(api)
    # Reuse the already qualified inert native API fixture, not its assertions.
    source = ast.parse((ROOT / 'tests/test_caliby_native_transport.py').read_text())
    literals = [n.value for n in ast.walk(source) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    init = next(s for s in literals if 'def load_model(name, device=None):' in s)
    kernel = next(s for s in literals if 'def run_seq_des_ensemble(' in s)
    (package / '__init__.py').write_text(init)
    for directory in [package / 'eval', package / 'eval/eval_utils']:
        (directory / '__init__.py').write_text('')
    (package / 'eval/eval_utils/seq_des_utils.py').write_text(kernel)
    source_pdb = tmp_path / 'backbone.pdb'
    backbone(source_pdb)
    settings = contract.EnsembleDesign.model_validate({'ensembles': [{'ensemble_id': 'x', 'states': [{'state_id': 'x', 'path': str(source_pdb)}]}]}).model_dump(mode='json')
    for key in ('task', 'schema_version', 'ensembles', 'num_seqs_per_pdb'):
        settings.pop(key)
    settings.update(temperature=0.23, potts_sweeps=11, num_workers=0, verbose=False, omit_aas=[])
    req = request('caliby_experimental', settings)
    req['sequence_input_settings'] = contract.Conformer(state_id='generated', path=str(source_pdb)).model_dump(mode='json', exclude={'state_id', 'path'})
    req.pop('request_sha256')
    req['request_sha256'] = hashlib.sha256(json.dumps(req, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    request_file = tmp_path / 'request.json'
    request_file.write_text(json.dumps(req))
    weights = tmp_path / 'weights/caliby'
    weights.mkdir(parents=True)
    (weights / (settings['model_name'] + '.ckpt')).write_text('INERT; not loaded')
    cache = tmp_path / 'cache'
    cache.mkdir()
    binary = tmp_path / 'bin'
    binary.mkdir()
    binds = f'{cache}:/cache,{weights.parent}:/weights/caliby/model_params,{ROOT},{tmp_path}'
    shim = binary / 'python3'
    shim.write_text('#!/bin/sh\nexec apptainer exec --cleanenv --bind ' + shlex.quote(binds) + ' --env ' + shlex.quote('PYTHONPATH=' + str(fixture)) + ' ' + str(IMAGES / 'caliby.sif') + ' python "$@"\n')
    shim.chmod(0o755)
    workflow = tmp_path / 'transport.nf'
    workflow.write_text(f"include {{ RunShapeCaliby }} from '{ROOT}/modules/shape_blueprint'\nworkflow {{ RunShapeCaliby(Channel.of(tuple('{'a' * 64}', file('{source_pdb}'))), 1, 19, file('{request_file}')) }}\n")
    config = tmp_path / 'fixture.config'
    config.write_text("process.executor='local'\napptainer.enabled=false\ndocker.enabled=false\n")
    jars = sorted((Path.home() / '.nextflow/framework').glob('*/nextflow-*-one.jar'))
    assert jars, 'An installed offline Nextflow is required'
    env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'], NXF_HOME=str(tmp_path / 'nxf'), NXF_OFFLINE='true',
               JAVA_TOOL_OPTIONS='--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED')
    result = subprocess.run(['java', '-jar', str(jars[-1]), '-C', str(config), 'run', str(workflow),
                             '--code_root', str(ROOT), '--out_dir', str(tmp_path / 'out'), '-work-dir', str(tmp_path / 'work')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    original = tmp_path / 'out/run/shape_sequences/caliby_experimental' / ('a' * 64 + '_caliby_experimental')
    returned = tmp_path / 'returned'
    shutil.copytree(original, returned)
    shutil.rmtree(tmp_path / 'work')
    shutil.rmtree(tmp_path / 'out')
    source_pdb.unlink()
    rows = json.loads((returned / 'sequence_records.json').read_text())
    assert rows['schema'] == 'bms_shape_sequences_v2'
    row, = rows['records']
    assert row['sequence_name'] == 'a' * 64 + '__caliby_experimental__000'
    assert row['sequence'] == 'INERT'
    assert row['native_source']['ensemble_id'] == 'a' * 64
    assert (returned / row['native_structure']['filename']).read_text().startswith('data_EXPLICITLY_INERT')
    call = json.loads((returned / 'runtime/native/inert_call.json').read_text())
    assert call['operation'] == 'ensemble_design'
    assert call['cfg']['potts_sampling_cfg']['potts_temperature'] == 0.23
    assert call['cfg']['potts_sampling_cfg']['potts_sweeps'] == 11
    assert call['cfg']['omit_aas'] == []


def test_esm_native_sample_manifest_survives_relocation(tmp_path):
    owner = module('run_shape_validator_suite')
    bundle = tmp_path / 'esm'
    bundle.mkdir()
    rows = []
    for key in ('seq_001', 'seq_000'):
        (bundle / (key + '.cif')).write_text('data_INERT_' + key)
        (bundle / (key + '.json')).write_text(json.dumps({'sample_id': key, 'sequence_name': 'seq', 'plddt_mean': 0.8}))
        rows.append({'sample_id': key, 'cif': key + '.cif', 'metrics': key + '.json'})
    (bundle / 'manifest.json').write_text(json.dumps({'samples': rows}))
    output = tmp_path / 'suite/shape_validator_records.json'
    result = owner.run_validator_suite(sequence='INERT', sequence_name='seq', esm_metrics_path=bundle / 'seq_000.json',
        esm_structure_path=bundle / 'seq_000.cif', esm_bundle=bundle, output_path=output,
        validators=['esmfold2'], peer_evidence=[], seed=0, code_root=ROOT,
        request={'schema': 'bms_shape_design_request_v3', 'validator_settings': {'esmfold2': {'num_diffusion_samples': 2}}})
    shutil.rmtree(bundle)
    record = result['records']['esmfold2']
    assert record['baseline_native_sample_key'] == 'seq_000'
    assert [s['native_sample_key'] for s in record['samples']] == ['seq_001', 'seq_000']
    for sample in record['samples']:
        assert (output.parent / sample['structure']['filename']).read_text() == 'data_INERT_' + sample['native_sample_key']
        assert json.loads((output.parent / sample['metrics']['filename']).read_text())['sample_id'] == sample['native_sample_key']


def test_boltz_real_click_and_sample_capture_boundary(tmp_path):
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    # Preserve installed Click parsing and BMS emission capture; replace only
    # inference and scientific serialization with explicitly inert operations.
    (fixture / 'sitecustomize.py').write_text('''
import json
from pathlib import Path
from types import SimpleNamespace
import torch
from boltz.data.write.writer import BoltzWriter
from boltz.main import predict

def inert_writer(self, trainer, module, prediction, indices, batch, batch_idx, dataloader_idx):
    native = self.output_dir / 'shape'
    native.mkdir(parents=True)
    for rank in range(len(prediction['confidence_score'])):
        stem = 'shape_model_' + str(rank)
        (native / (stem + '.pdb')).write_text('REMARK EXPLICITLY INERT rank=' + str(rank))
        (native / ('confidence_' + stem + '.json')).write_text(json.dumps({'confidence_score': 0.9 - 0.5 * rank}))
BoltzWriter.write_on_batch_end = inert_writer

def inert_predict(**kwargs):
    Path('parsed_native.json').write_text(json.dumps(kwargs, default=str))
    writer = object.__new__(BoltzWriter)
    writer.output_dir = Path('native')
    writer.output_format = 'pdb'
    prediction = {'exception': False, 'confidence_score': torch.tensor([0.4, 0.9])}
    writer.write_on_batch_end(None, None, prediction, None, {'record': [SimpleNamespace(id='shape')]}, 0, 0)
predict.callback = inert_predict
''')
    script = ROOT / 'scripts/shape_blueprint/run_shape_validator_suite.py'
    code = '''import sys,runpy,os; from boltz.main import predict; assert predict.callback.__name__ == 'inert_predict'; os.chdir(sys.argv[1]); sys.argv=[sys.argv[2], '--mode','native','--validator','boltz2','--request',sys.argv[3],'--sequence','INERT','--sequence-name','seq','--seed','3','--code-root',sys.argv[4],'--output',sys.argv[5]]; runpy.run_path(sys.argv[0],run_name='__main__')'''
    req = tmp_path / 'request.json'
    req.write_text(json.dumps({'schema': 'bms_shape_design_request_v3', 'validator_settings': {'boltz2': {
        'boltz_diffusion_samples': 2, 'boltz_sampling_steps': 7, 'boltz_recycling_steps': 1,
        'boltz_step_scale': 1.3, 'boltz_seed': 0, 'boltz_use_potentials': False}}}))
    output = tmp_path / 'suite/shape_validator_records.json'
    native('boltz2-v2.2.1.sif', code, tmp_path, script, req, ROOT, output, env={'PYTHONPATH': fixture})
    result = json.loads(output.read_text())
    record = result['records']['boltz2']
    assert record['status'] == 'completed', record
    assert [s['native_sample_key'] for s in record['samples']] == ['shape/batch_0/sample_1', 'shape/batch_0/sample_0']
    assert [s['native_rank'] for s in record['samples']] == [0, 1]
    for sample in record['samples']:
        path = output.parent / sample['structure']['filename']
        assert path.read_text().endswith(str(sample['native_rank']))
    parsed = json.loads((tmp_path / 'suite_runtime/boltz2/parsed_native.json').read_text())
    assert parsed['sampling_steps'] == 7 and parsed['diffusion_samples'] == 2
    assert parsed['step_scale'] == 1.3 and parsed['seed'] == 0 and parsed['use_potentials'] is False


@pytest.mark.parametrize('engine,image', [('proteinmpnn', 'dl_binder_design.sif'), ('fampnn', 'fampnn.sif')])
def test_sequence_installed_parser_and_real_adapter_subprocess(tmp_path, engine, image):
    source = tmp_path / 'backbone.pdb'
    backbone(source)
    runner = tmp_path / 'inert_native.py'
    if engine == 'proteinmpnn':
        settings = dict(mpnn_temperature=0.23, mpnn_backbone_noise=0.0, mpnn_omitAAs='',
                        mpnn_checkpoint_type='soluble', mpnn_checkpoint_model='v_48_020')
        runner.write_text('''
import ast,argparse,json
from pathlib import Path

def main(args):
    out = Path(args.out_folder)
    (out / 'seqs').mkdir(parents=True, exist_ok=True)
    (out / 'parsed_native.json').write_text(json.dumps(vars(args)))
    (out / 'seqs' / (Path(args.pdb_path).stem + '.fa')).write_text('>INERT_SOURCE\\nGGGGG\\n' + ''.join('>INERT_SAMPLE_' + str(i) + '\\nINERT\\n' for i in range(args.num_seq_per_target)))
source = Path('/dl_binder_design/mpnn_fr/ProteinMPNN/protein_mpnn_run.py')
tree = ast.parse(source.read_text())
# Execute the installed parser unmodified; never execute its science function.
block = next(n for n in tree.body if isinstance(n,ast.If) and '__name__' in ast.unparse(n.test))
exec(compile(ast.Module(body=[block],type_ignores=[]), str(source), 'exec'))
''')
    else:
        settings = dict(fampnn_batch_size=2, fampnn_seq_only=True, fampnn_repack_last=False,
                        fampnn_temperature=0.23, fampnn_num_steps=7, fampnn_exclude_cys=False,
                        fampnn_psce_threshold=None, fampnn_seed=0, fampnn_presort_by_length=False,
                        fampnn_timestep_mode='linear', fampnn_scn_num_steps=3, fampnn_scn_step_scale=0.8)
        runner.write_text('''
import hydra,json
from pathlib import Path
from omegaconf import OmegaConf
@hydra.main(config_path='/app/fampnn/configs', config_name='seq_design', version_base='1.3.2')
def main(cfg):
    out=Path(cfg.out_dir)
    (out / 'fastas').mkdir(parents=True,exist_ok=True)
    (out / 'parsed_native.json').write_text(json.dumps(OmegaConf.to_container(cfg,resolve=True)))
    for source in Path(cfg.pdb_dir).glob('*.pdb'):
        for i in range(cfg.num_seqs_per_pdb):
            name=source.stem+'_sample'+str(i)
            (out / 'fastas' / (name+'.fasta')).write_text('>'+name+'\\nINERT\\n')
main()
''')
    req = tmp_path / 'request.json'
    req.write_text(json.dumps(request(engine, settings, count=2)))
    output = tmp_path / 'output'
    code = '''import sys,runpy; sys.argv=[sys.argv[1],'--engine',sys.argv[2],'--backbone',sys.argv[3],'--candidate-id','a'*64,'--output-dir',sys.argv[4],'--receipt',sys.argv[4]+'/runtime_receipt.json','--count','2','--seed','19','--request',sys.argv[5],'--runner',sys.argv[6]]; runpy.run_path(sys.argv[0],run_name='__main__')'''
    native(image, code, ROOT / 'scripts/shape_blueprint/run_shape_sequence.py', engine, source, output, req, runner)
    rows = json.loads((output / 'sequence_records.json').read_text())['records']
    assert len(rows) == 2 and all(row['sequence'] == 'INERT' for row in rows)
    assert [r['sample_index'] for r in rows] == ([1, 2] if engine == 'proteinmpnn' else [0, 1])
    parsed = json.loads((output / 'runtime/parsed_native.json').read_text())
    if engine == 'proteinmpnn':
        assert parsed['num_seq_per_target'] == 2 and parsed['sampling_temp'] == '0.23'
        assert parsed['omit_AAs'] == [] and parsed['backbone_noise'] == 0.0
    else:
        assert parsed['num_seqs_per_pdb'] == 2 and parsed['seed'] == 0
        assert parsed['psce_threshold'] is None and parsed['exclude_cys'] is False
        assert parsed['scn_diffusion']['num_steps'] == 3
        assert parsed['scn_diffusion']['step_scale'] == 0.8


def test_protenix_real_wrapper_and_native_ranked_writer(tmp_path):
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    (fixture / 'sitecustomize.py').write_text('''
import json
from pathlib import Path
from types import SimpleNamespace
class Config(dict):
    __getattr__ = dict.__getitem__
from runner import batch_inference, inference
from runner.dumper import DataDumper
from configs.configs_inference import inference_configs

# Only coordinate serialization and inference are inert. Native ranking and
# confidence serialization are retained, including deliberately reversed rank.
def inert_structure(self, pred_coordinates, prediction_save_dir, sample_name, atom_array, entity_poly_type, seed, sorted_indices, b_factor):
    for index, rank in enumerate(sorted_indices):
        Path(prediction_save_dir, sample_name+'_sample_'+str(int(rank))+'.cif').write_text('data_INERT_seed_'+str(seed)+'_generation_'+str(index))
DataDumper._save_structure = inert_structure

def inert_runner(**kwargs):
    Path('parsed_native.json').write_text(json.dumps(kwargs))
    cfg = Config(kwargs, sorted_by_ranking_score=True, dump_dir=inference_configs['dump_dir'])
    runner = SimpleNamespace(configs=cfg, init_dumper=lambda **kwargs: None)
    return runner
batch_inference.get_default_runner = inert_runner
batch_inference.preprocess_input = lambda path, **kwargs: path

def inert_predict(runner, cfg):
    dumper = object.__new__(DataDumper)
    dumper.sorted_by_ranking_score = True
    dumper.need_atom_confidence = False
    for seed in cfg.seeds:
        data = {'coordinate': 'INERT', 'summary_confidence': [
            {'ranking_score': 0.2, 'seed': seed, 'generation_index': 0},
            {'ranking_score': 0.9, 'seed': seed, 'generation_index': 1}]}
        dumper.dump_predictions(data, cfg.dump_dir, 'shape', None, {}, seed)
inference.infer_predict = inert_predict
''')
    req = tmp_path / 'request.json'
    req.write_text(json.dumps({'schema': 'bms_shape_design_request_v3', 'validator_settings': {'protenix_v2': {
        'protenix_seeds': '3,7', 'protenix_n_sample': 2, 'protenix_n_step': 7, 'protenix_n_cycle': 2,
        'protenix_use_msa': False, 'protenix_use_template': False, 'protenix_enable_cache': False}}}))
    output = tmp_path / 'suite/shape_validator_records.json'
    script = ROOT / 'scripts/shape_blueprint/run_shape_validator_suite.py'
    code = '''import sys,os,runpy; from runner.inference import infer_predict; assert infer_predict.__name__ == 'inert_predict'; os.chdir(sys.argv[1]); sys.argv=[sys.argv[2],'--mode','native','--validator','protenix_v2','--request',sys.argv[3],'--sequence','INERT','--sequence-name','seq','--seed','19','--code-root',sys.argv[4],'--output',sys.argv[5]]; runpy.run_path(sys.argv[0],run_name='__main__')'''
    native('protenix.sif', code, tmp_path, script, req, ROOT, output, env={'PYTHONPATH': str(fixture) + ':/opt/protenix'})
    record = json.loads(output.read_text())['records']['protenix_v2']
    assert record['status'] == 'completed', record
    assert {(s['native_seed'], s['native_sample_index']) for s in record['samples']} == {(3, 0), (3, 1), (7, 0), (7, 1)}
    for sample in record['samples']:
        key = 'data_INERT_seed_' + str(sample['native_seed']) + '_generation_' + str(sample['native_sample_index'])
        assert (output.parent / sample['structure']['filename']).read_text() == key
        confidence = json.loads((output.parent / sample['metrics']['filename']).read_text())
        assert confidence['seed'] == sample['native_seed']
        assert confidence['generation_index'] == sample['native_sample_index']
        assert sample['native_rank'] == 1 - sample['native_sample_index']
    parsed = json.loads((tmp_path / 'suite_runtime/protenix_v2/parsed_native.json').read_text())
    assert parsed['seeds'] == [3, 7] and parsed['n_sample'] == 2
    assert parsed['n_step'] == 7 and parsed['n_cycle'] == 2 and parsed['enable_cache'] is False


def test_rfd3_installed_hydra_settings_boundary(tmp_path):
    owner = module('run_shape_rfd3')
    settings = dict(min_t=0.0, max_t=1.0, sigma_data=16.0, s_min=0.0004, s_max=160.0,
                    p=7.0, gamma_0=0.0, cfg_t_max=None, use_classifier_free_guidance=False,
                    read_sequence_from_sequence_head=False, dump_trajectories=True,
                    align_trajectory_structures=False, low_memory_mode=False)
    arguments = owner.native_settings_overrides(settings)
    output = tmp_path / 'native_config.json'
    code = '''
import sys,json
from pathlib import Path
import rfd3
from hydra import initialize_config_dir, compose
from omegaconf import OmegaConf
root=Path(rfd3.__file__).parent/'configs'
with initialize_config_dir(config_dir=str(root),version_base='1.3'):
    cfg=compose(config_name='inference',overrides=sys.argv[2:])
Path(sys.argv[1]).write_text(json.dumps(OmegaConf.to_container(cfg,resolve=False)))
'''
    native('shape_rfd3.sif', code, output, *arguments)
    value = json.loads(output.read_text())
    assert value['inference_sampler']['gamma_0'] == 0.0
    assert value['inference_sampler']['cfg_t_max'] is None
    assert value['inference_sampler']['sigma_data'] == 16.0
    assert value['read_sequence_from_sequence_head'] is False
    assert value['dump_trajectories'] is True


def test_esm_real_nextflow_settings_process_and_native_input_types(tmp_path):
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    (fixture / 'sitecustomize.py').write_text('''
import json
from pathlib import Path
from types import SimpleNamespace
import torch
import triton
triton.autotune=lambda *args, **kwargs: (lambda function: function)
from esm.models.esmfold2 import ESMFold2InputBuilder
from transformers.models.esmfold2.modeling_esmfold2 import ESMFold2Model

# No device access or checkpoint loading; the real native input types and BMS
# parser/writer remain active. This fixture never returns scientific results.
torch.cuda.is_available=lambda: False
torch.cuda.device_count=lambda: 0
torch.cuda.current_device=lambda: 0
torch.cuda.get_device_name=lambda *_: 'INERT_NO_GPU'
class InertModel:
    def to(self,device): return self
    def eval(self): return self
ESMFold2Model.from_pretrained=classmethod(lambda cls,*a,**kw: InertModel())
def inert_fold(self, model, spi, **kwargs):
    assert isinstance(model,InertModel)
    Path('inert_esm_call.json').write_text(json.dumps(kwargs))
    return [SimpleNamespace(complex=SimpleNamespace(to_mmcif=lambda: 'data_EXPLICITLY_INERT\\n'),
                            plddt=torch.tensor([0.8]),ptm=torch.tensor(0.7),iptm=None)
            for _ in range(kwargs['num_diffusion_samples'])]
ESMFold2InputBuilder.__init__=lambda self,*args,**kwargs: None
ESMFold2InputBuilder.fold=inert_fold
''')
    import textwrap
    fixture_path = fixture / 'sitecustomize.py'
    fixture_path.write_text('try:\n' + textwrap.indent(fixture_path.read_text(), '    ') +
                            '\nexcept BaseException as error:\n    import os,sys\n    print(error,file=sys.stderr)\n    os._exit(90)\n')
    binary = tmp_path / 'bin'
    binary.mkdir()
    cache = tmp_path / 'cache'
    cache.mkdir()
    shim = binary / 'python3'
    shim.write_text('#!/bin/sh\nexec apptainer exec --cleanenv --no-home --bind ' + shlex.quote(str(ROOT) + ',' + str(tmp_path)) +
                   ' --env ' + shlex.quote('PYTHONPATH=' + str(fixture)) + ' --env ' + shlex.quote('HOME=' + str(cache)) +
                   ' --env ' + shlex.quote('TMPDIR=' + str(cache)) + ' ' + str(IMAGES / 'esmfold2.sif') + ' python "$@"\n')
    shim.chmod(0o755)
    workflow = tmp_path / 'transport.nf'
    workflow.write_text(f"include {{ ESMFold2Predict }} from '{ROOT}/modules/esmfold2_experimental'\nworkflow {{ ESMFold2Predict(Channel.of(tuple([shape_settings:[num_diffusion_samples:2,num_loops:2,num_sampling_steps:7,seed:0,local_files_only:true]], 'INERT', 'native_seq'))) }}\n")
    config = tmp_path / 'fixture.config'
    config.write_text("process.executor='local'\napptainer.enabled=false\ndocker.enabled=false\n")
    jars = sorted((Path.home() / '.nextflow/framework').glob('*/nextflow-*-one.jar'))
    assert jars
    env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'], NXF_HOME=str(tmp_path / 'nxf'), NXF_OFFLINE='true',
               JAVA_TOOL_OPTIONS='--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED')
    done = subprocess.run(['java','-jar',str(jars[-1]),'-C',str(config),'run',str(workflow),
        '--code_root',str(ROOT),'--out_dir',str(tmp_path / 'out'),'--modification_mode','shape_blueprint',
        '-work-dir',str(tmp_path / 'work')],cwd=tmp_path,env=env,text=True,capture_output=True,timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    calls = list((tmp_path / 'work').rglob('inert_esm_call.json'))
    assert len(calls) == 1
    assert json.loads(calls[0].read_text()) == {'num_loops': 2, 'num_sampling_steps': 7,
        'num_diffusion_samples': 2, 'seed': 0, 'complex_id': 'native_seq'}
    manifest = json.loads((calls[0].parent / 'esmfold2_results/manifest.json').read_text())
    assert [row['sample_id'] for row in manifest['samples']] == ['native_seq_000', 'native_seq_001']
