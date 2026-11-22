"""Compiler manifest -> direct workflow -> inert native executable -> canonical channel."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('method,mode', [(method, mode) for method in ('boltz', 'protenix', 'esmfold2')
    for mode in ('batch', 'batch+primary', 'replicas')] + [('esmfold2', 'complex-batch')])
@pytest.mark.asyncio
async def test_standalone_rows(tmp_path, method, mode, monkeypatch):
    from services import nextflow
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    jar = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR', Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'))
    assert jar.is_file(), 'offline Nextflow jar required'
    request = dict(sequence_name='parent', run_frustrampnn=False, num_parallel_jobs=2)
    request[{'boltz':'boltz_use_msa', 'protenix':'protenix_use_msa', 'esmfold2':'esmf_use_msa'}[method]] = False
    if mode in ('replicas', 'batch+primary'):
        request['sequence'] = 'WWWW'
    if mode != 'replicas':
        request['sequence_batch_entries'] = [dict(name='alpha', sequence='ACDE'), dict(name='beta', sequence='FGHI')]
    if mode == 'complex-batch':
        request.update(complex_components=[dict(type='protein', id='A', sequence='PPPP'),
            dict(type='protein', id='B', sequence='GGGG')], sequence_batch_component_id='A')
    output = tmp_path/'out'
    from database import Job
    monkeypatch.syspath_prepend(str(ROOT / 'platform/api/tests'))
    from test_core_protein_candidates import setup, job
    factory, engine = await setup(tmp_path)
    async with factory() as session:
        current = job(output)
        current.model_id = 'boltz2' if method == 'boltz' else method
        current.params = request
        current.provenance = {**current.provenance, "core_protein_requested_params": request}
        session.add(current)
        await session.commit()
        invocation = await nextflow._compile_launch_nextflow_invocation(session, current, request, str(output))
    await engine.dispose()
    assert 'workflows/structure_prediction.nf' in ' '.join(invocation.command)
    params = dict(invocation.native_parameters, out_dir=str(output), code_root=str(ROOT), job_id=current.id)
    assert params['core_protein_scientific_contract'] == 1
    if mode == 'replicas':
        expected = {'parent_job0': 'WWWW', 'parent_job1': 'WWWW'}
    else:
        rows = json.loads(Path(params['sequence_batch_json_path']).read_text())
        expected = {row['name']: row['sequence'] for row in rows}
        assert list(expected.values()) == ['ACDE', 'FGHI']
    binary = tmp_path / 'bin'
    binary.mkdir()
    shim = binary / 'python3'
    python = shlex.quote(sys.executable)
    shim.write_text(f'''#!/bin/sh
case "$1" in
*/run_protenix_inference.py|*/run_boltz_wrapper.py) exec {python} {ROOT}/tests/fixtures/standalone_row_capture.py "$@";;
*/run_esmfold2_inference.py) shift; exec {python} {ROOT}/tests/fixtures/wp06_capture.py "$@";;
*/stage_reporter.py) exit 0;;
esac
exec {python} "$@"
''')
    shim.chmod(0o755)
    params['api_python'] = str(shim)
    pf = tmp_path/'params.json'
    pf.write_text(json.dumps(params))
    config = tmp_path/'fixture.config'
    config.write_text(f"process.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\nprocess.ext.scripts_root='{ROOT}/scripts'\n")
    # Observe real emitted metadata without replacing the entry, tasks or row owner.
    source = (ROOT/'workflows/structure_prediction.nf').read_text().replace("from '../modules/", f"from '{ROOT}/modules/")
    anchor = '        structure_prediction_wf(sequence_ch)'
    assert source.count(anchor) == 1
    entry = tmp_path/'entry.nf'
    entry.write_text(source.replace(anchor, anchor + "\n        structure_prediction_wf.out.canonical_structures.view { m, p -> 'ROW=' + JsonOutput.toJson([m, p.toString()]) }"))
    env = dict(os.environ, PATH=str(binary)+':'+os.environ['PATH'], NXF_OFFLINE='true', NXF_ANSI_LOG='false',
               NXF_HOME=str(tmp_path/'nxf'), NXF_PLUGINS_DEFAULT='', NXF_DISABLE_CHECK_LATEST='true',
               BMS_TEST_ROW_PUBLICATION='1', JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2', BMS_NVIDIA_SMI='/bin/false')
    result = subprocess.run(['java', '-jar', str(jar), '-C', str(config), 'run', str(entry), '-params-file', str(pf)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = [json.loads(line[4:]) for line in result.stdout.splitlines() if line.startswith('ROW=')]
    assert len(outputs) == len(expected) == 2
    observed = {}
    for meta, path in outputs:
        path = Path(path)
        if method == 'esmfold2':
            native = json.loads((path.parent/'capture.json').read_text())['parsed']
            name, sequence = native['sequence_name'], native['sequence']
            if mode == 'complex-batch':
                assert sequence == '' and 'producer_sequence' not in meta
                document = json.loads(Path(next(row['complex_json'] for row in rows if row['name'] == name)).read_text())
                assert meta['producer_components'] == document
                components = json.loads(native['complex_components_json'])['components']
                sequence, retained = [row['sequence'] for row in components]
                assert retained == 'GGGG'
        else:
            task = next(p for p in path.parents if (p/'native.json').is_file())
            native = json.loads((task/'native.json').read_text())
            name, sequence = native['name'], native['sequence']
        assert meta['producer_method'] == method
        assert meta['producer_artifact_id'] == meta['producer_sample'] == meta['producer_artifact_key'] == name
        assert meta['original_submission_identity'] == dict(id=name, name=name)
        if mode != 'complex-batch':
            assert meta['producer_sequence'] == sequence
        assert meta['producer_output_key'].startswith(name + '/')
        assert meta['producer_artifact_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        observed[name] = sequence
    assert observed == expected
    if method == 'boltz':
        from services.result_ingester import ingest_job_results
        async with factory() as session:
            loaded = await session.get(Job, current.id)
            tasks = loaded.provenance['boltz_launch_authority']['tasks']
            assert {t['namespace']: t['metadata']['producer_sequence'] for t in tasks} == expected
            assert await ingest_job_results(current.id, str(output), session) == len(expected)
            assert await ingest_job_results(current.id, str(output), session) == 0
        await engine.dispose()
    if method == 'protenix':
        from services.result_ingester import ingest_job_results
        from sqlalchemy import select
        from database import Design
        async with factory() as session:
            assert await ingest_job_results(current.id, str(output), session) == len(expected)
            assert await ingest_job_results(current.id, str(output), session) == 0
            designs = (await session.scalars(select(Design))).all()
            producers = [d.provenance['native_producer'] for d in designs]
            assert {p['producer_sample']: p['producer_sequence'] for p in producers} == expected
        await engine.dispose()
    if method == 'esmfold2':
        from services.result_ingester import ingest_job_results
        from services.esmfold2_scientific_consumer import verified_esmfold2_design
        from sqlalchemy import select
        from database import Design
        async with factory() as session:
            assert await ingest_job_results(current.id, str(output), session) == len(expected)
            assert await ingest_job_results(current.id, str(output), session) == 0
            designs = (await session.scalars(select(Design))).all()
            assert {d.name for d in designs} == {n + '_000' for n in expected}
            for design in designs:
                verified = await verified_esmfold2_design(design, session)
                assert verified['payload']['sequence_name'] == design.name[:-4]
            if mode == 'batch':
                # A missing second producer must not turn replay into a successful single-row import.
                from services.core_protein_result_contract import CandidateIntegrityError
                manifest = Path(designs[-1].json_path).parent / 'manifest.json'
                manifest.rename(manifest.with_suffix('.missing'))
                with pytest.raises(CandidateIntegrityError):
                    await ingest_job_results(current.id, str(output), session)
        await engine.dispose()
    (tmp_path/'output-metadata.json').write_text(json.dumps(outputs, indent=2))
