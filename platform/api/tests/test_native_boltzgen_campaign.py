"""Offline campaign DAG and portable native input/output conformance."""
from __future__ import annotations
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from native_design_runtime_fixture import offline_worker_env
from component_runtime import plan_boltzgen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts'))
campaign = importlib.import_module('native_boltzgen_campaign')


def test_campaign_partition_settings_and_exact_partial_join(tmp_path):
    settings = {'boltzgen_noise_scale': 0.0, 'boltzgen_skip_inverse_folding': False, 'boltzgen_budget': 1}
    children = list(plan_boltzgen(5, 2, settings))
    assert [c['designs'] for c in children] == [2, 2, 1]
    assert [c['design_start'] for c in children] == [0, 2, 4]
    assert all(c['settings'] == settings for c in children)
    plan = {'children': children}
    records = []
    for c in children:
        root = tmp_path/f'child{c["index"]}'
        root.mkdir()
        failed = c['index'] == 1
        (root/'component_execution.json').write_text(json.dumps({'stage': 'inference' if failed else 'filter', 'exit_code': 2 if failed else 0}))
        (root/'filter_summary.json').write_text('{"final_count":1}')
        (root/'model.pdb').write_text('fixture native PDB bytes')
        (root/'confidence_model.json').write_text('{"native":true}')
        records.append((c, root))
    receipt = campaign.collect(plan, list(reversed(records)), tmp_path/'out')
    assert receipt['selected_pdbs'] == ['job0_model.pdb', 'job2_model.pdb']
    assert [r['status'] for r in receipt['children']] == ['completed', 'failed', 'completed']
    assert (tmp_path/'out/native/job1/model.pdb').read_bytes() == b'fixture native PDB bytes'
    assert not receipt['ingestion_triggered']
    with pytest.raises(ValueError, match='exact join'):
        campaign.collect(plan, records[:-1], tmp_path/'missing')
    with pytest.raises(ValueError, match='duplicate'):
        campaign.collect(plan, records+records[:1], tmp_path/'duplicate')
    for _, root in records:
        (root/'component_execution.json').write_text('{"stage":"inference","exit_code":2}')
    with pytest.raises(ValueError, match='all BoltzGen'):
        campaign.collect(plan, records, tmp_path/'failed')
    assert json.loads((tmp_path/'failed/collection_manifest.json').read_text())['status'] == 'failed'


def test_boltzgen_native_input_closure_is_portable_and_fails_escape(tmp_path):
    import yaml
    (tmp_path/'target.pdb').write_bytes(b'fixture PDB bytes')
    (tmp_path/'scaffold.yaml').write_text('path: target.pdb\ninclude:\n- chain:\n    id: A\n    res_index: 1-2\n')
    source = tmp_path/'boltzgen_input.yaml'
    source.write_text('entities:\n- file:\n    path: [scaffold.yaml]\n')
    campaign.bundle_input(source, tmp_path/'portable')
    relocated = tmp_path/'worker'
    shutil.copytree(tmp_path/'portable', relocated)
    assert (relocated/'boltzgen_input.yaml').read_bytes() == source.read_bytes()
    assert (relocated/'target.pdb').read_bytes() == b'fixture PDB bytes'
    source.write_text('entities:\n- file:\n    path: ../escape.pdb\n')
    with pytest.raises(ValueError, match='relative'):
        campaign.bundle_input(source, tmp_path/'unsafe')


@pytest.mark.runtime_integration
@pytest.mark.parametrize('placement', ['local', 'worker'])
@pytest.mark.parametrize('failed_index', [-1, 1])
def test_actual_offline_boltzgen_campaign_dag(tmp_path, placement, failed_index):
    """Actual production Nextflow plan/run/filter/join; model output is a fixture.

    The shim substitutes native wrapper bytes only. FilterBoltzGen runs its real
    scientific filtering script, with one requested design selected per child.
    """
    framework = Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not framework.is_file():
        pytest.skip('offline pinned Nextflow 25.10.1 unavailable')
    bundle = tmp_path/'input'
    (tmp_path/'boltzgen_input.yaml').write_text('entities: []\n')
    campaign.bundle_input(tmp_path/'boltzgen_input.yaml', bundle)
    (tmp_path/'out').mkdir()
    bin_dir = tmp_path/'bin'; bin_dir.mkdir()
    shim = bin_dir/'python3'
    shim.write_text(f'''#!{sys.executable}
import sys, pathlib, json, runpy
args=sys.argv[1:]
if args[0]=='/scripts/run_boltzgen_wrapper.py':
    argv=args[1:]
    n=int(argv[argv.index('--num_designs')+1])
    # The descriptor ordinal remains in the task script's publish destination;
    # deterministic failure of the singleton remainder proves partial joins.
    if {failed_index} >= 0 and n == 1: sys.exit(7)
    out=pathlib.Path('output/designs'); out.mkdir(parents=True)
    assert float(argv[argv.index('--noise_scale')+1]) == 0.0
    for i in range(n):
        name='model_'+str(i)
        (out/(name+'.pdb')).write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\\nTER\\nEND\\n')
        (out/('confidence_'+name+'.json')).write_text(json.dumps({{'design_id':name,'designed_sequence':'A','affinity_probability':0.8,'design_ptm':0.7,'filter_rmsd':1.0,'source':'boltzgen'}}))
else:
    sys.argv=args
    runpy.run_path(args[0], run_name='__main__')
''')
    shim.chmod(0o755)
    config = tmp_path/'nextflow.config'
    config.write_text(f"""params.code_root='{ROOT}'
params.api_python='{sys.executable}'
params.out_dir='{tmp_path}/out'
params.boltzgen_protocol='protein-anything'
params.boltzgen_noise_scale=0.0
params.boltzgen_budget=1
params.boltzgen_filter_biased=false
params.boltzgen_num_designs=5
params.boltzgen_designs_per_job=2
params.boltzgen_extra_params=null
params.core_protein_scientific_contract=null
process.executor='local'
process.shell=['/bin/bash','-euo','pipefail']
singularity.enabled=false
apptainer.enabled=false
docker.enabled=false
""")
    main = tmp_path/'main.nf'
    main.write_text(f"""nextflow.enable.dsl=2
include {{ NativeBoltzGenCampaign }} from '{ROOT}/modules/boltzgen_native_campaign.nf'
workflow {{ NativeBoltzGenCampaign(Channel.value(file('{bundle}')), Channel.value(5), Channel.value(2), Channel.value('parent')) }}
""")
    env = {**os.environ, 'PATH': str(bin_dir)+':'+os.environ['PATH'], 'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true',
        'NXF_HOME': str(tmp_path/'nxf'), 'API_BASE_URL': 'http://127.0.0.1:1', 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('BMS_STAGE_REPORT_TOKEN', None)
    env.pop('BMS_REMOTE_EXECUTION', None)
    if placement == 'worker': env['BMS_REMOTE_EXECUTION'] = '1'
    env = offline_worker_env(tmp_path, env)
    result = subprocess.run(['java','--add-opens=java.base/java.util=ALL-UNNAMED','-jar',str(framework),'-C',str(config),'run',str(main),'-offline','-w',str(tmp_path/'work')],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout+result.stderr
    path = tmp_path/'out/components/boltzgen/campaign/collection_manifest.json'
    assert path.exists(), result.stdout+result.stderr
    receipt = json.loads(path.read_bytes())
    assert [r['designs'] for r in receipt['plan']['children']] == [2,2,1]
    assert len(receipt['selected_pdbs']) == (3 if failed_index < 0 else 2)
    assert len(receipt['children']) == 3
    assert receipt['plan']['children'][0]['settings']['boltzgen_filter_biased'] is False
