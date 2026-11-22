"""CPU-only native wire evidence. The distributed predictor is an inert recorder."""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from services.nextflow import compile_nextflow_invocation, _bind_protenix_msa_transport
from services.model_msa_handoff import prepare_launch_msa
from test_fold_cp_input_msa_metadata import provider_double

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_named_fold_cp_batch(tmp_path, monkeypatch):
    supplied = tmp_path / 'fixed.a3m'
    supplied.write_text('>q\nGGGG\n')
    rows = [('alpha', 'ACDE'), ('beta', 'FGHI')]
    params = dict(sequence='MMMM', sequence_batch_prefix='row', sequence_batch_component_id='B',
        sequence_batch_entries=[dict(name=n, sequence=s) for n,s in rows],
        complex_components=[dict(type='protein', id='A', sequence='GGGG', msa_path=str(supplied)),
                            dict(type='protein', id='B', sequence='MMMM')],
        boltz_use_msa=True, msa_provider='neurosnap_api', bcp_size_cp=2, bcp_cp_topology='1d',
        pinned_gpus=[0,1,2,3], bcp_gpu_ids='0,1,2,3', boltz_num_samples=3, run_frustrampnn=False)
    out = tmp_path / 'out'
    inv = compile_nextflow_invocation('boltz_cp_experimental', 'design', params, str(out))
    inv.materialize_inputs(out)
    native = inv.native_parameters
    roster = json.loads(Path(native['sequence_batch_json_path']).read_text())
    expected = {row['name']: seq for row, (_,seq) in zip(roster, rows, strict=True)}
    source = Path(native['bcp_input_path'])
    assert {p.stem: yaml.safe_load(p.read_text())['sequences'][1]['protein']['sequence']
            for p in source.glob('*.yaml')} == expected
    calls = provider_double(monkeypatch, tmp_path)
    prepared = prepare_launch_msa(inv.model_id, native, out / 'prepared-msa')
    native = _bind_protenix_msa_transport(inv, prepared).native_parameters
    assert [sequences for sequences, _ in calls] == [[s] for _,s in rows]
    run_fixture(tmp_path, native)
    published = out / 'cif_files/predictions'
    files = sorted(published.rglob('model_0.cif'))
    assert len(files) == 2
    for path in files:
        name = path.relative_to(published).parts[0]
        receipt = json.loads(path.read_text())
        assert receipt == [name, expected.pop(name), '4', '2', '2', '3']
    assert not expected
    from test_core_protein_candidates import setup, job
    from services.result_ingester import ingest_job_results
    from sqlalchemy import select
    from database import Design
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(out, marked=False)
            current.model_id, current.mode, current.params = inv.model_id, 'design', params
            session.add(current)
            await session.commit()
            assert await ingest_job_results(current.id, str(out), session) == 2
        async with factory() as session:
            designs = list((await session.scalars(select(Design))).all())
            assert {Path(d.pdb_path) for d in designs} == set(files)
            for design in designs:
                variant = Path(design.pdb_path).relative_to(published).parts[0]
                assert design.producer_model_id == inv.model_id
                assert Path(design.json_path).relative_to(out/'json_files/predictions').parts[0] == variant
                assert design.ptm == (0.7 if variant.endswith('alpha') else 0.8)
            assert await ingest_job_results(current.id, str(out), session) == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize('batch', [False, True])
def test_unaligned_fold_cp_inputs(tmp_path, batch):
    rows = [dict(name='alpha', sequence='ACDE'), dict(name='beta', sequence='FGHI')]
    inputs = dict(sequence_batch_entries=rows) if batch else dict(sequence='ACDE')
    out = tmp_path / 'out'
    inv = compile_nextflow_invocation('boltz_cp_experimental', 'design',
        dict(**inputs, boltz_use_msa=False, pinned_gpus=[0], run_frustrampnn=False), str(out))
    inv.materialize_inputs(out)
    run_fixture(tmp_path, inv.native_parameters)
    published = out / 'cif_files/predictions'
    expected = ({'variant_001_alpha': 'ACDE', 'variant_002_beta': 'FGHI'}
                if batch else {'boltz_cp_input': 'ACDE'})
    for path in published.rglob('model_0.cif'):
        name, seq, *settings = json.loads(path.read_text())
        assert path.relative_to(published) == (Path(name if batch else '') / 'predictions_dp0_cp0/sample/model_0.cif')
        assert expected.pop(name) == seq
        assert settings == ['1', '1', '1', '1']
    assert not expected


def run_fixture(tmp, native):
    if not all(os.getenv(k) for k in ('BMS_TEST_NEXTFLOW_JAR', 'BMS_FOLD_CP_FIXTURE_PYTHON')):
        pytest.skip('requires offline Nextflow and an RDKit/PyYAML interpreter')
    jar = os.environ['BMS_TEST_NEXTFLOW_JAR']
    python = os.environ['BMS_FOLD_CP_FIXTURE_PYTHON']  # RDKit and PyYAML
    bin_dir = tmp / 'bin'
    bin_dir.mkdir()
    shim = bin_dir / 'python3'
    shim.write_text(f'#!/bin/sh\nif [ "$1" = -m ] && [ "$2" = torch.distributed.run ]; then shift 2; exec "{python}" "{tmp}/leaf.py" "$@"; fi\nexec "{python}" "$@"\n')
    shim.chmod(0o755)
    (tmp / 'leaf.py').write_text("""import json,sys,yaml
from pathlib import Path
args=sys.argv[1:]
def arg(k): return args[args.index(k)+1]
p=Path(arg('predict'))
*fixed,b=[r['protein'] for r in yaml.safe_load(p.read_text())['sequences']]
if fixed:
    assert Path(fixed[0]['msa']).read_text()=='>q\\nGGGG\\n'
    assert b['sequence'] in Path(b['msa']).read_text()
else:
    assert b['msa']=='empty'
d=Path(arg('--out_dir'))/'boltz_results_inert/predictions_dp0_cp0/sample'
d.mkdir(parents=True)
(d.parent/'confidence_model_0.json').write_text(json.dumps({'ptm':0.7 if p.stem.endswith('alpha') else 0.8}))
(d/'model_0.cif').write_text(json.dumps([p.stem,b['sequence'],arg('--nproc_per_node'),arg('--size_dp'),arg('--size_cp'),arg('--diffusion_samples')]))
""")
    params = tmp / 'params.json'
    params.write_text(json.dumps({**native, 'bcp_repo_path': str(tmp), 'code_root': str(ROOT)}))
    config = tmp / 'fixture.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\n')
    env = {**os.environ, 'PATH': f"{bin_dir}:{os.environ['PATH']}", 'NXF_OFFLINE':'true', 'NXF_HOME':str(tmp/'nxf')}
    subprocess.run(['java','-jar',jar,'-C',str(config),'run',str(ROOT/'workflows/boltz_cp_experimental.nf'),
                    '-params-file',str(params)],cwd=tmp,env=env,check=True, timeout=120)
