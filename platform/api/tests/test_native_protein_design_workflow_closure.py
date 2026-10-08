"""Full protein-design analysis-import DAG with native component fixture science."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from native_design_runtime_fixture import offline_worker_env
from services.frustrampnn.settings import default_settings
from services.frustrampnn.contracts import canonical_json_bytes
from test_structure_prediction_frustrampnn_v2_transport import _two_model_pdb

ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.runtime_integration
@pytest.mark.parametrize('placement',['local','worker'])
@pytest.mark.parametrize('branch',['analysis_import','boltzgen_campaign'])
def test_full_protein_design_analysis_import_native_closure(tmp_path,placement,branch):
    jar=Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not jar.is_file(): pytest.skip('pinned offline Nextflow runtime absent')
    inputs=tmp_path/'inputs'; inputs.mkdir()
    (inputs/'model.pdb').write_bytes(_two_model_pdb())
    (tmp_path/'out').mkdir()
    shim=tmp_path/'python3'
    shim.write_text(f'''#!{sys.executable}
import os, sys, pathlib, runpy
args=sys.argv[1:]
if any(arg.endswith('/run_boltzgen_wrapper.py') for arg in args):
    import json
    count=int(args[args.index('--num_designs')+1])
    output=pathlib.Path('output/designs'); output.mkdir(parents=True)
    for index in range(count):
        name='model_'+str(index)
        (output/(name+'.pdb')).write_bytes(pathlib.Path({str(inputs/'model.pdb')!r}).read_bytes())
        (output/('confidence_'+name+'.json')).write_text(json.dumps({{'design_id':name,'designed_sequence':'GA','affinity_probability':0.8,'design_ptm':0.7,'filter_rmsd':1.0,'source':'boltzgen'}}))
elif args and args[0].endswith('native_frustrampnn_parent.py') and args[1]=='run':
    sys.path[:0]=[{str(ROOT/'platform/api/tests')!r},{str(ROOT/'platform/api')!r},{str(ROOT/'scripts')!r}]
    import pytest, run_frustrampnn_component as component
    from native_design_runtime_fixture import patch_native_runtime
    with pytest.MonkeyPatch.context() as patch:
        patch_native_runtime(component,patch,pathlib.Path.cwd())
        sys.argv=args
        runpy.run_path(args[0],run_name='__main__')
elif any(arg.endswith('/analyse_best_designs.py') for arg in args):
    # Explicit native PyRosetta-output fixture: the licensed runtime is not in
    # the frozen API environment. All binding/join/publication remains real.
    import json
    source=next(pathlib.Path.cwd().glob('*.pdb'))
    pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps({{'description':source.stem,'fold_id':None,'seq_id':None,'pr_RoG':1.0,'fixture_only':True}})+'\\n')
else:
    # Container /scripts bindings are supplied as read-only repository paths in
    # this CPU fixture; this does not replace any scientific helper.
    args=[{str(ROOT)!r}+arg if arg.startswith('/scripts/') else arg for arg in args]
    os.execv({sys.executable!r},[{sys.executable!r}]+args)
''')
    shim.chmod(0o755)
    (tmp_path/'python').symlink_to(shim)
    settings=default_settings().model_dump(mode='json',exclude_none=False,exclude={'settings_value_origin'})
    settings.update(batching_enabled=branch=='boltzgen_campaign',structures_per_job=2 if branch=='boltzgen_campaign' else 25)
    config=tmp_path/'nextflow.config'
    config.write_text(f"""params.code_root='{ROOT}'
params.api_python='{shim}'
params.out_dir='{tmp_path}/out'
params.job_id='protein-parent'
params.container_dir='{tmp_path}'
params.frustrampnn_physical_gpu_id=0
params.frustrampnn_settings='{canonical_json_bytes(settings).decode()}'
params.frustrampnn_settings_value_origin='bms_default'
params.skip_rfd=false
params.skip_rfd_seq=false
params.skip_rfd_seq_pred={'true' if branch=='analysis_import' else 'false'}
params.seqs_per_design=8
params.skip_input_dir='{inputs}'
params.run_rfd_only=false
params.diffusion_method='{'rfd3' if branch=='analysis_import' else 'boltzgen'}'
params.boltzgen_num_designs=5
params.boltzgen_designs_per_job=2
params.parallel_mode='full_orchestrator'
params.boltzgen_scaffold_length='2'
params.boltzgen_protocol='protein-anything'
params.boltzgen_budget=1
params.boltzgen_filter_biased=false
params.pred_method='boltz'
params.seq_method='mpnn'
params.gpus=1
params.rfd_num_designs=1
params.zip_pdbs=false
params.run_frustrampnn=true
process.executor='local'
process.container=null
process.shell=['/bin/bash','-euo','pipefail']
singularity.enabled=false
apptainer.enabled=false
docker.enabled=false
""")
    env={**os.environ,'PATH':str(tmp_path)+':'+os.environ['PATH'],'NXF_OFFLINE':'true','NXF_DISABLE_CHECK_LATEST':'true',
        'NXF_HOME':str(tmp_path/'nxf'),'PYTHONDONTWRITEBYTECODE':'1','API_BASE_URL':'http://127.0.0.1:1'}
    env.pop('BMS_REMOTE_EXECUTION',None); env.pop('BMS_STAGE_REPORT_TOKEN',None)
    if placement=='worker': env['BMS_REMOTE_EXECUTION']='1'
    env = offline_worker_env(tmp_path, env)
    result=subprocess.run(['java','--add-opens=java.base/java.util=ALL-UNNAMED','-jar',str(jar),'-C',str(config),'run',str(ROOT/'workflows/protein_design.nf'),'-offline','-w',str(tmp_path/'work')],
        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr
    receipt=tmp_path/'out/frustrampnn/component_runtime/terminal.json'
    assert receipt.is_file(),result.stdout+result.stderr
    ids=json.loads(receipt.read_bytes())['candidate_ids']
    assert len(ids)==(1 if branch=='analysis_import' else 3)
    assert (tmp_path/'out/results/success_metrics.json').is_file()
    assert (tmp_path/'out/results/best_designs.csv').is_file()
    request=json.loads((tmp_path/'out/frustrampnn/results'/ids[0]/'workflow_component_request_v3.json').read_bytes())
    assert request['parent_workflow_id']=='protein_design'
    assert request['source_artifact']['producer_stage']==('protein_design:analysis_import' if branch=='analysis_import' else 'protein_design:boltzgen_child')
    if branch=='boltzgen_campaign':
        campaign=json.loads((tmp_path/'out/components/boltzgen/campaign/collection_manifest.json').read_bytes())
        assert [child['designs'] for child in campaign['plan']['children']]==[2,2,1]
        assert campaign['partial_failure_policy']=='at_least_one_completed_child'
