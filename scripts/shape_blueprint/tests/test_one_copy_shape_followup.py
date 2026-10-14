"""Real offline Nextflow publication/ingestion; native emissions are inert fixtures."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from test_one_copy_shape_handoffs import ROOT, SCRIPTS, document, descriptor, sha


def nextflow(tmp_path, workflow, *, module_root=ROOT):
    jar = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    assert jar.is_file(), 'offline installed Nextflow required'
    binary = tmp_path / 'bin'
    binary.mkdir()
    shim = binary / 'python3'
    shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    shim.chmod(0o755)
    script = tmp_path / 'fixture.nf'
    script.write_text(workflow)
    config = tmp_path / 'fixture.config'
    config.write_text("process.executor='local'\napptainer.enabled=false\ndocker.enabled=false\n")
    environment = {**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH'],
                   'JAVA_TOOL_OPTIONS': '--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED',
                   'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true'}
    command = ['java', '-jar', str(jar), '-C', str(config), 'run', str(script),
               '--code_root', str(module_root), '--out_dir', str(tmp_path / 'published'),
               '-work-dir', str(tmp_path / 'work')]
    def run(*extra):
        trace = tmp_path / ('resume-trace.tsv' if '-resume' in extra else 'trace.tsv')
        result = subprocess.run(command + ['-with-trace', str(trace), *extra], cwd=tmp_path,
                                env=environment, text=True, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        with trace.open() as stream:
            return list(csv.DictReader(stream, delimiter='\t'))
    return run


def test_all_native_publishers_real_nextflow_cached_republish(tmp_path):
    # Only native emission is substituted with labelled tiny fixtures. Process
    # declarations, staging, output globbing, publishDir and saveAs are unchanged.
    module = (ROOT / 'modules/shape_blueprint.nf').read_text()
    sections = re.split(r'(?=^process )', module, flags=re.M)
    selected = {'RunShapeRFD3', 'RunShapeProteinMPNN', 'RunShapeFAMPNN', 'RunShapeCaliby', 'AdmitRFD3InitialCandidate'}
    fixture = sections[0]
    for section in sections[1:]:
        name = section.split()[1]
        if name not in selected:
            continue
        if name == 'RunShapeRFD3':
            emission = '''mkdir -p rfd3_results
printf 'INERT gzip fixture' > rfd3_results/native.cif.gz
printf '{"native_sample_index":0,"seed":7}' > rfd3_results/native.json
printf '{}\\n' > rfd3_results/shape_guidance_steps.jsonl
printf '{}\\n' > shape_rfd3_runtime_receipt.json
printf '{}\\n' > shape_rfd3_input.json
printf 'INERT native log\\n' > shape_rfd3.log'''
        elif name == 'AdmitRFD3InitialCandidate':
            emission = "printf '{}\\n' > ${candidate.simpleName}.initial_admission.json"
        else:
            engine = {'RunShapeProteinMPNN': 'proteinmpnn', 'RunShapeFAMPNN': 'fampnn',
                      'RunShapeCaliby': 'caliby_experimental'}[name]
            emission = f'''mkdir -p candidate_{engine}/native candidate_{engine}/input
printf 'data_INERT_{engine}\\n' > candidate_{engine}/native/sample.cif
printf '{{"seed":7,"temperature":0.1}}\\n' > candidate_{engine}/sequence_records.json
printf '>INERT\\nAAAA\\n' > candidate_{engine}/sequences.fasta'''
            if engine == 'fampnn':
                emission += f'''\nln -s '${{backbone.toRealPath()}}' candidate_{engine}/input/staged.pdb
python3 -c "import sys; sys.path.insert(0, '{SCRIPTS}'); from build_shape_result import retain_file; from pathlib import Path; p=Path('candidate_{engine}/input/staged.pdb'); retain_file(p,p)"'''
        fixture += section.rstrip().removesuffix('}') + '\n    stub:\n    """\n' + emission + '\n    """\n}\n'
    (tmp_path / 'native.nf').write_text(fixture)
    for name in ('request.json', 'geometry.json', 'points.bin', 'sdf.bin', 'receipt.json', 'backbone.pdb'):
        (tmp_path / name).write_text('EXPLICITLY INERT INPUT\n')
    workflow = f"""
include {{ RunShapeRFD3; RunShapeProteinMPNN; RunShapeFAMPNN; RunShapeCaliby; AdmitRFD3InitialCandidate }} from './native'
workflow {{
    RunShapeRFD3(file('{tmp_path}/request.json'), file('{tmp_path}/geometry.json'),
        file('{tmp_path}/points.bin'), file('{tmp_path}/sdf.bin'), file('{tmp_path}/receipt.json'))
    AdmitRFD3InitialCandidate(RunShapeRFD3.out.structures.flatten(), file('{tmp_path}/request.json'),
        file('{tmp_path}/geometry.json'), file('{tmp_path}/points.bin'), file('{tmp_path}/sdf.bin'))
    RunShapeProteinMPNN(Channel.of(tuple('candidate', file('{tmp_path}/backbone.pdb'))), 1, 7, file('{tmp_path}/request.json'))
    RunShapeFAMPNN(Channel.of(tuple('candidate', file('{tmp_path}/backbone.pdb'))), 1, 7, file('{tmp_path}/request.json'))
    RunShapeCaliby(Channel.of(tuple('candidate', file('{tmp_path}/backbone.pdb'))), 1, 7, file('{tmp_path}/request.json'))
}}
"""
    run = nextflow(tmp_path, workflow)
    rows = run('-stub-run')
    assert len(rows) == 5 and {row['status'] for row in rows} == {'COMPLETED'}
    published = tmp_path / 'published'
    bodies = {}
    for path in published.rglob('*'):
        if path.is_file():
            assert not path.is_symlink(), path
            bodies[path.relative_to(published)] = path.read_bytes()
            assert path.stat().st_nlink > 1, path
    assert len(bodies) == 6 + 3 * 3 + 1 + 1
    stats = [(published / relative).stat() for relative in bodies]
    identities = {(s.st_dev, s.st_ino): s for s in stats}
    print('SHAPE_NATIVE_PUBLICATION_EVIDENCE ' + json.dumps({
        'published_regular_leaves': len(stats), 'unique_payload_inodes': len(identities),
        'logical_bytes': sum(s.st_size for s in stats),
        'unique_allocated_bytes': sum(s.st_blocks * 512 for s in identities.values()),
        'all_shared_with_work_or_retained_input': all(s.st_nlink > 1 for s in stats),
    }, sort_keys=True))
    shutil.rmtree(published)
    rows = run('-stub-run', '-resume')
    assert len(rows) == 5 and {row['status'] for row in rows} == {'CACHED'}
    for relative, body in bodies.items():
        path = published / relative
        assert path.read_bytes() == body and path.stat().st_nlink > 1
    shutil.rmtree(tmp_path / 'work')
    (tmp_path / 'backbone.pdb').unlink()
    assert {p.relative_to(published): p.read_bytes() for p in published.rglob('*') if p.is_file()} == bodies


def test_real_cached_result_then_actual_database_ingestion(tmp_path):
    # This selection is run outside API conftest (which stubs Popen). Imports
    # below still use the task launcher's private DB/results/HOME and locked Python.
    sys.path.insert(0, str(ROOT / 'platform/api/tests'))
    from test_shape_result_ingestion import _request_spec, _register, _session_factory
    from scripts.shape_blueprint.plan_rfd3_batches import plan_batches
    from scripts.shape_blueprint.build_rfd3_aggregate import build_aggregate
    from services.result_ingester import _ingest_shape_result_manifest
    from database import Design
    from sqlalchemy import select

    request = _request_spec()
    request['request_sha256'] = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    document(tmp_path / 'request.json', request)
    plan = plan_batches(request, gpu_memory_gib=32)
    backbone_id = plan['batches'][0]['candidates'][0]['candidate_id']
    aggregate = build_aggregate(plan=plan, admission_records=[{'candidate_id': backbone_id, 'status': 'accepted', 'reason': None}])
    document(tmp_path / 'aggregate.json', aggregate)
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    structure = candidate / 'structure.cif'
    structure.write_text('data_EXPLICITLY_INERT\n')
    source = candidate / 'source.pdb'
    source.write_text('REMARK EXPLICITLY_INERT BACKBONE\n')
    metrics = document(candidate / 'metrics.json', dict(schema='bms_shape_candidate_metrics_v1',
        candidate_id='candidate', geometry_sha256=request['geometry_sha256'], point_pool_sha256=request['point_pool_sha256'],
        sdf_sha256=request['sdf_sha256'], source_backbone_sha256=sha(source)))
    sequence_name = f'{backbone_id}__proteinmpnn__001'
    suite = document(candidate / 'suite.json', dict(schema='bms_shape_validator_suite_v2', sequence_name=sequence_name,
        validators=['esmfold2'], records={'esmfold2': {'artifacts': [descriptor(structure)],
        'effective_settings': {'seed': 7, 'use_templates': False}, 'native_metrics': {'sample_id': 'sample_000'}}}))
    document(candidate / 'candidate_bundle.json', dict(schema='bms_shape_candidate_bundle_v1',
        candidate_id='candidate', name='candidate', status='accepted', structure=descriptor(structure),
        source_backbone=descriptor(source), metrics=descriptor(metrics), validator_evidence=descriptor(suite),
        validator_artifacts=[{**descriptor(structure), 'validator': 'esmfold2', 'native_path': 'structure.cif'}],
        provenance={'sequence_name': sequence_name, 'predictor': 'esmfold2', 'sequence_engine': 'proteinmpnn'}))
    workflow = f"""
include {{ PlanRFD3Batches; BuildShapeResult }} from '{ROOT}/modules/shape_blueprint'
workflow {{
    PlanRFD3Batches(file('{tmp_path}/request.json'))
    BuildShapeResult(Channel.of(file('{candidate}')).collect(), file('{tmp_path}/request.json'), file('{tmp_path}/aggregate.json'), 'job-one-copy')
}}
"""
    run = nextflow(tmp_path, workflow)
    assert {row['status'] for row in run()} == {'COMPLETED'}
    published = tmp_path / 'published'
    manifest_path = published / 'results/shape_result_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    row, = manifest['candidates']
    assert (published / row['structure']['relative_path']).samefile(structure)
    sibling = tmp_path / 'sibling.cif'
    os.link(structure, sibling)
    shutil.rmtree(published)
    rows = run('-resume')
    assert len(rows) == 2 and {row['status'] for row in rows} == {'CACHED'}
    assert json.loads(manifest_path.read_text()) == manifest
    # Publication/ingestion after original native/work owners have disappeared.
    shutil.rmtree(tmp_path / 'work')
    shutil.rmtree(candidate)
    (tmp_path / 'aggregate.json').unlink()
    before = {p: p.read_bytes() for p in published.rglob('*') if p.is_file()}
    async def ingest():
        factory, engine = await _session_factory(tmp_path)
        try:
            async with factory() as session:
                job = await _register(session, 'job-one-copy', published)
                from services.result_state_integrity import finalize_successful_job
                result = await finalize_successful_job(job, str(published), session)
                assert result.completed is True, job.error_message
                await session.commit()
            async with factory() as session:
                from database import Job
                job = await session.get(Job, 'job-one-copy')
                designs = (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all()
                design, = designs
                assert Path(design.pdb_path).samefile(sibling)
                assert design.provenance['candidate_id'] == 'candidate'
                evidence = json.loads(Path(design.provenance['shape_validator_evidence']['path']).read_text())
                assert evidence['records']['esmfold2']['effective_settings'] == {'seed': 7, 'use_templates': False}
                assert await _ingest_shape_result_manifest(job, published, session, commit=True) == 0
        finally:
            await engine.dispose()
    asyncio.run(ingest())
    assert {p: p.read_bytes() for p in before} == before, 'ingestion must not write shared structure/metadata bytes'
    shutil.rmtree(published)
    assert sibling.read_text() == 'data_EXPLICITLY_INERT\n'
