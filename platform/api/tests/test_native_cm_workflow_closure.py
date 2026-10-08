"""Full external-import CM DAG with real analysis and fixture FrustraMPNN inference."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from native_design_runtime_fixture import offline_worker_env
from services.conformational_mapping.import_stager import RegisteredArtifact, stage_registered_artifacts
from services.conformational_mapping.import_snapshot import build_import_snapshot_from_mmcif
from services.conformational_mapping.contracts import candidate_id, canonical_sha256
from services.frustrampnn.settings import default_settings
from test_conformational_mapping_import_snapshot import MMCIF

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.runtime_integration
@pytest.mark.parametrize('placement', ['local', 'worker'])
def test_complete_cm_import_native_analysis_without_host(tmp_path, placement):
    jar=Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not jar.is_file(): pytest.skip('pinned offline Nextflow runtime absent')
    source=tmp_path/'input.cif'; source.write_bytes(MMCIF)
    inputs=tmp_path/'inputs'; inputs.mkdir()
    request_id='5bd7e715-6f73-4e6a-a270-09f486d1da86'
    staged=stage_registered_artifacts([RegisteredArtifact('fixture','alice',tmp_path,'input.cif',hashlib.sha256(MMCIF).hexdigest(),len(MMCIF))],
        principal_id='alice',request_id=request_id,destination_root=inputs/'registered_import')
    entry=staged.receipt['entries'][0]
    coords={'backend':'external_import','target_id':'t','staged_index':0,'source_content_sha256':entry['source_content_sha256'],
        'staged_receipt_sha256':staged.receipt['receipt_sha256']}
    cid=candidate_id(coords)
    snapshot=build_import_snapshot_from_mmcif(MMCIF,target_id='t',candidate_id=cid,original_source_path=f"registered_import/{entry['destination_relative_path']}")
    settings=default_settings().model_dump(mode='json',exclude_none=False)
    settings.update(batching_enabled=False,structures_per_job=25)
    request={'schema_name':'cm_request','schema_version':1,'request_id':request_id,'backend':'external_import',
        'targets':[{'target_id':'t','target_order':0}],'ordered_seeds':[0],'samples_per_seed':1,
        'feature_policy':{'mode':'features_disabled_control_v1'},'runtime_policy':{'use_default_params':True},
        'analysis_policy':{'sign_zero_epsilon':1e-6,'clash_detector_id':'bms_clash','clash_detector_version':'1',
            'outer_support_minimum':1.0,'inner_support_minimum':1.0,'sign_consistency_minimum':1.0,'clash_free_minimum':1.0,
            'rank_stability_minimum':1.0,'minimum_common_ranked_universe_size':3},
        'import_receipt_id':staged.receipt['receipt_sha256'],'source_snapshot_sha256':canonical_sha256(snapshot),
        'source':{'kind':'api_submission_v1','sha256':'a'*64},'created_by':{'principal_id':'alice'},
        'frustrampnn_requiredness':'required','frustrampnn_settings':settings}
    request['request_sha256']=canonical_sha256(request)
    (inputs/'cm_request_v1.json').write_text(json.dumps(request))
    (inputs/'cm_complex_snapshots_v1.json').write_text(json.dumps([snapshot]))
    (tmp_path/'out').mkdir()
    shim=tmp_path/'python3'
    shim.write_text(f'''#!{sys.executable}
import os, sys, pathlib, runpy
args=sys.argv[1:]
if args and args[0].endswith('native_frustrampnn_parent.py') and args[1]=='run':
    sys.path[:0]=[{str(ROOT/'platform/api/tests')!r},{str(ROOT/'platform/api')!r},{str(ROOT/'scripts')!r}]
    import pytest, run_frustrampnn_component as component
    from native_design_runtime_fixture import patch_native_runtime
    with pytest.MonkeyPatch.context() as patch:
        patch_native_runtime(component,patch,pathlib.Path.cwd())
        sys.argv=args
        runpy.run_path(args[0],run_name='__main__')
else:
    os.execv({sys.executable!r},[{sys.executable!r}]+args)
''')
    shim.chmod(0o755)
    config=tmp_path/'nextflow.config'
    config.write_text(f"""params.code_root='{ROOT}'
params.api_python='{shim}'
params.out_dir='{tmp_path}/out'
params.job_id='cm-parent'
params.container_dir='{tmp_path}'
params.frustrampnn_physical_gpu_id=0
params.cm_request_path='{inputs}/cm_request_v1.json'
process.executor='local'
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
    result=subprocess.run(['java','--add-opens=java.base/java.util=ALL-UNNAMED','-jar',str(jar),'-C',str(config),'run',str(ROOT/'workflows/conformational_mapping.nf'),'-offline','-w',str(tmp_path/'work')],
        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+result.stderr
    receipt=tmp_path/'out/frustrampnn/component_runtime/terminal.json'
    assert receipt.is_file(),result.stdout+result.stderr
    assert json.loads(receipt.read_bytes())['candidate_ids']==[cid]
    final=tmp_path/'out/final/conformational_mapping/canonical_import/canonical_result'
    assert (final/'cm_derived_index_v1.json').is_file()
    references=json.loads((final/'derived/cm_frustrampnn_result_references_v1.json').read_bytes())
    assert references['results'][0]['candidate_id']==cid
    assert references['results'][0]['source_sha256']==hashlib.sha256(MMCIF).hexdigest()
    bundle=final/'frustrampnn/results'/cid
    native_request=json.loads((bundle/'workflow_component_request_v3.json').read_bytes())
    assert native_request['identity_authority']=='cm_complex_snapshot'
    assert native_request['parent_job_id']=='cm-parent'
