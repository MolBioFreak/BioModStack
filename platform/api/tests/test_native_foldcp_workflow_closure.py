"""Full Fold-CP production DAG with offline model fixtures, not GPU acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from native_design_runtime_fixture import offline_worker_env
from services.frustrampnn.settings import default_settings
from services.frustrampnn.contracts import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.runtime_integration
@pytest.mark.parametrize('placement', ['local', 'worker'])
def test_full_foldcp_native_parent_offline(tmp_path, placement):
    jar = Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not jar.is_file(): pytest.skip('pinned offline Nextflow runtime absent')
    (tmp_path/'repo').mkdir()
    (tmp_path/'out').mkdir()
    (tmp_path/'input.yaml').write_text('version: 1\nsequences: []\n')
    shim = tmp_path/'python3'
    shim.write_text(f'''#!{sys.executable}
import sys, os, pathlib, json, runpy
args=sys.argv[1:]
if args[:2]==['-m','torch.distributed.run']:
    root=pathlib.Path(args[args.index('--out_dir')+1])/'boltz_results_fixture'
    pred=root/'predictions_dp0_cp0/sample'; pred.mkdir(parents=True)
    (root/'processed').mkdir()
    (root/'processed/manifest.json').write_text('{{"inputs":["sample"]}}')
    (pred/'sample_model_0.pdb').write_text('ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 80.00           N\\nATOM      2  CA  ALA A   1       1.400   0.000   0.000  1.00 80.00           C\\nATOM      3  C   ALA A   1       2.000   1.000   0.000  1.00 80.00           C\\nATOM      4  O   ALA A   1       2.000   2.000   0.000  1.00 80.00           O\\nTER\\nEND\\n')
    (pred/'confidence_sample_model_0.json').write_text('{{"confidence_score":0.8}}')
    print('Explicit offline Fold-CP scientific fixture')
elif args and args[0].endswith('native_frustrampnn_parent.py') and args[1]=='run':
    sys.path[:0]=[{str(ROOT/'platform/api/tests')!r},{str(ROOT/'platform/api')!r},{str(ROOT/'scripts')!r}]
    import pytest, run_frustrampnn_component as component
    from native_design_runtime_fixture import patch_native_runtime
    with pytest.MonkeyPatch.context() as patch:
        patch_native_runtime(component, patch, pathlib.Path.cwd())
        sys.argv=args
        runpy.run_path(args[0],run_name='__main__')
else:
    os.execv({sys.executable!r},[{sys.executable!r}]+args)
''')
    shim.chmod(0o755)
    settings = default_settings().model_dump(mode='json', exclude_none=False, exclude={'settings_value_origin'})
    settings.update(batching_enabled=False, structures_per_job=25)
    config = tmp_path/'nextflow.config'
    config.write_text(f"""params.code_root='{ROOT}'
params.api_python='{shim}'
params.out_dir='{tmp_path}/out'
params.job_id='foldcp-parent'
params.container_dir='{tmp_path}'
params.frustrampnn_physical_gpu_id=0
params.frustrampnn_settings='{canonical_json_bytes(settings).decode()}'
params.frustrampnn_settings_value_origin='bms_default'
params.bcp_input_path='{tmp_path}/input.yaml'
params.bcp_repo_path='{tmp_path}/repo'
params.bcp_size_cp=1
params.bcp_gpu_ids='0'
params.bcp_input_format='config_files'
params.bcp_output_format='pdb'
params.boltz_use_msa=false
params.run_frustrampnn=true
process.executor='local'
process.shell=['/bin/bash','-euo','pipefail']
singularity.enabled=false
apptainer.enabled=false
docker.enabled=false
""")
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],'NXF_OFFLINE':'true','NXF_DISABLE_CHECK_LATEST':'true',
        'NXF_HOME':str(tmp_path/'nxf'),'PYTHONDONTWRITEBYTECODE':'1','API_BASE_URL':'http://127.0.0.1:1'}
    env.pop('BMS_REMOTE_EXECUTION',None)
    env.pop('BMS_STAGE_REPORT_TOKEN',None)
    if placement=='worker': env['BMS_REMOTE_EXECUTION']='1'
    env = offline_worker_env(tmp_path, env)
    result=subprocess.run(['java','--add-opens=java.base/java.util=ALL-UNNAMED','-jar',str(jar),'-C',str(config),'run',str(ROOT/'workflows/boltz_cp_experimental.nf'),'-offline','-w',str(tmp_path/'work')],
        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr
    receipt_path=tmp_path/'out/frustrampnn/component_runtime/terminal.json'
    assert receipt_path.is_file(),result.stdout+result.stderr
    receipt=json.loads(receipt_path.read_bytes())
    assert receipt['candidate_ids']==['foldcp_sample_model_0']
    bundle=tmp_path/'out/frustrampnn/results/foldcp_sample_model_0'
    request=json.loads((bundle/'workflow_component_request_v3.json').read_bytes())
    assert request['requested_settings']['batching_enabled'] is False
    assert request['source_artifact']['relative_path']=='fold_cp/sample_model_0.pdb'
    assert (tmp_path/'out/processed/boltz_cp/manifest.json').is_file()
    assert (tmp_path/'out/json_files/predictions/confidence_sample_model_0.json').is_file()
