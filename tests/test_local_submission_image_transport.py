"""LOCAL transport acceptance, not inference: real compiler/workflows, fake Apptainer.

Run from root, separately from API pytest (its BioXP guard blocks subprocesses).
No images are mounted and no scientific outputs are fabricated. The recorder
exits 73 at the first container command; Nextflow failure is the expected boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def local_install(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('BMS_', 'NXF_', 'APPTAINER_', 'SINGULARITY_')) or key in ('DATABASE_URL', 'BIOXP_LIVE_TESTS'):
            monkeypatch.delenv(key)
    for key, value in {
        'BMS_HOME': REPO, 'BMS_DATA': tmp_path / 'data',
        'BMS_CONTAINER_DIR': tmp_path / 'containers',
        'BMS_WORK_DIR': tmp_path / 'work',
        'BMS_RUNTIME_IMAGE_STORE': tmp_path / 'store',
        'BMS_SCIENTIFIC_ARTIFACT_ROOT': tmp_path / 'artifacts',
        'XDG_CONFIG_HOME': tmp_path / 'config', 'XDG_CACHE_HOME': tmp_path / 'cache',
        'XDG_STATE_HOME': tmp_path / 'state', 'NXF_HOME': tmp_path / 'nxf',
        'NXF_OFFLINE': 'true', 'NXF_ANSI_LOG': 'false',
    }.items():
        monkeypatch.setenv(key, str(value))
    for path in (REPO, REPO / 'platform/api', REPO / 'scripts'):
        monkeypatch.syspath_prepend(str(path))
    from lib.shared_runtime_images import publish_image, verify_image
    source = tmp_path / 'synthetic-source.sif'
    source.write_bytes(b'SYNTHETIC IMAGE TRANSPORT FIXTURE; NOT A SCIENTIFIC IMAGE')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    image = publish_image(source, tmp_path / 'store', digest)
    source.unlink()
    verify_image(image, digest)
    binary = tmp_path / 'bin'
    binary.mkdir()
    recorder = binary / 'apptainer'
    recorder.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if '--version' in args or 'version' in args:
    print('apptainer version 1.4.0')
    raise SystemExit(0)
image = next(a for a in args if a.endswith('.sif') or a.startswith('/proc/self/fd/'))
s = os.stat(image)
record = dict(argv=args, image=image, resolved=os.path.realpath(image), device=s.st_dev,
              inode=s.st_ino, cwd=os.getcwd())
with open({str(tmp_path / 'invocations.jsonl')!r}, 'a') as f:
    f.write(json.dumps(record) + '\\n')
print('LOCAL_TRANSPORT_STOP: no scientific command executed', file=sys.stderr)
raise SystemExit(73)
''')
    recorder.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary) + os.pathsep + os.environ['PATH'])
    return image, digest, recorder


@pytest.mark.parametrize('lane', ['protenix', 'confornets'])
def test_job_compiler_to_production_nextflow_apptainer(tmp_path, monkeypatch, local_install, lane):
    image, digest, recorder = local_install
    jars = sorted((Path.home() / '.nextflow/framework').glob('*/nextflow-*-one.jar'))
    if not jars or not shutil.which('java'):
        pytest.skip('installed Nextflow JAR and Java required; no downloads')
    from services import nextflow
    monkeypatch.setattr(nextflow, 'get_work_dir', lambda: tmp_path / 'work')
    # The executable shim only selects the installed offline JAR. Compiler argv,
    # registered entrypoint, production modules and image selectors are unchanged.
    launcher = recorder.with_name('nextflow')
    launcher.write_text('#!/bin/sh\nexec java -jar ' + str(jars[-1]) + ' "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
    if lane == 'protenix':
        monkeypatch.setenv('BMS_PROTENIX_CONTAINER_PATH', str(image))
        from services.frustrampnn.settings import default_settings
        settings = default_settings().model_dump(mode='json', exclude_none=False)
        params = dict(sequence='ACDE', sequence_name='transport', gpu_id=0,
                      protenix_use_msa=False, run_frustrampnn=True,
                      frustrampnn_settings=settings, frustrampnn_settings_value_origin='bms_default')
        model, mode, entrypoint, process_name = ('protenix', 'predict', 'structure_prediction.nf', 'ProtenixPredict')
    else:
        monkeypatch.setenv('BMS_CM_CONFORNETS_CONTAINER_PATH', str(image))
        request = tmp_path / 'request/cm_request_v1.json'
        request.parent.mkdir()
        # Minimal routing input: not claimed to be a scientifically valid request.
        # The first canonical container (request prep) is stopped before reading it.
        request.write_text(json.dumps(dict(schema_name='cm_request', schema_version=1,
            request_id='transport', backend='confornets', frustrampnn_requiredness='required',
            frustrampnn_settings={}, targets=[dict(target_id='transport', target_order=0)], ordered_seeds=[42])))
        params = dict(cm_request_path=str(request), gpu_id=0, run_frustrampnn=True)
        model, mode, entrypoint, process_name = ('conformational_mapping', 'map', 'conformational_mapping.nf', 'PrepCanonicalConforNetsRequest')
    job = SimpleNamespace(id='local-transport', model_id=model, mode=mode, params=params, provenance={})
    command = nextflow.build_job_nextflow_command(job, params, str(tmp_path / 'output'))
    assert command[2] == 'workflows/' + entrypoint
    # No selector flag is manually injected. Local inheritance is supported;
    # if the compiler later makes selection explicit it must preserve authority.
    for flag in ('--protenix_container_path', '--cm_confornets_container_path'):
        if flag in command:
            assert command[command.index(flag) + 1] == str(image)
    # Preserve the compiler's relative entrypoint using unmodified source symlinks.
    (tmp_path / 'workflows').symlink_to(REPO / 'workflows', target_is_directory=True)
    (tmp_path / 'modules').symlink_to(REPO / 'modules', target_is_directory=True)
    (tmp_path / 'conf').symlink_to(REPO / 'conf', target_is_directory=True)
    (tmp_path / 'nextflow.config').write_text((REPO / 'nextflow.config').read_text() + '''
process.executor = 'local'
process.cpus = 1
process.memory = '256 MB'
process.errorStrategy = 'terminate'
process.maxRetries = 0
apptainer.enabled = true
apptainer.autoMounts = true
''')
    result = subprocess.run(command + ['-offline'], cwd=tmp_path, capture_output=True, text=True, timeout=150)
    diagnostic = result.stdout + result.stderr
    log = tmp_path / 'invocations.jsonl'
    assert log.exists(), diagnostic
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert result.returncode != 0, diagnostic
    assert 'LOCAL_TRANSPORT_STOP' in diagnostic, diagnostic
    assert process_name in diagnostic, diagnostic
    assert len(records) == 1, records
    assert records[0]['image'] == str(image)
    assert (records[0]['device'], records[0]['inode']) == (image.stat().st_dev, image.stat().st_ino)
    task = Path(records[0]['cwd'])
    assert task.is_relative_to(tmp_path / 'work')
    assert not list((tmp_path / 'work').rglob('*.sif'))
    assert not (tmp_path / 'containers/protenix.sif').exists()
    assert not (tmp_path / 'containers/confornets-canonical.sif').exists()
    from lib.shared_runtime_images import verify_image
    verify_image(image, digest)
    print(f'{lane}: actual Job compiler -> {entrypoint} -> {process_name} -> fake Apptainer; canonical inode={image.stat().st_ino}; task SIFs=0; intentionally stopped before science')


def test_registered_frustra_component_to_pinned_apptainer_and_digest_rejection(tmp_path, monkeypatch, local_install):
    """Registered structure-prediction v3 candidate -> real component -> pinned FD.

    Fixture registry/inventory identify synthetic bytes, never the installed model.
    Strict readers, closure checks, asset hashing, argv builder and execution are
    not mocked. The fake hashes real synthetic asset files, then exits 73 before
    predict. The source PDB is explicitly synthetic input, not model output.
    """
    from dataclasses import replace
    import rfc8785
    from services.frustrampnn import runtime, settings, configuration
    from services.frustrampnn.contracts import canonical_json_bytes
    from scripts import run_frustrampnn_component as component
    image, digest, recorder = local_install
    executable = tmp_path / 'synthetic-executable'
    checkpoint = tmp_path / 'synthetic-checkpoint'
    executable.write_bytes(b'SYNTHETIC EXECUTABLE ASSET; NEVER EXECUTED')
    checkpoint.write_bytes(b'SYNTHETIC CHECKPOINT ASSET; NEVER LOADED')
    identity = replace(runtime.FRUSTRAMPNN_RUNTIME_IDENTITY,
        configured_sif_path=str(image), sif_sha256=digest,
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
    monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(image))
    monkeypatch.setattr(runtime, 'FRUSTRAMPNN_RUNTIME_IDENTITY', identity)
    monkeypatch.setattr(runtime.runtime_identity_dict, '__defaults__', (identity,))
    monkeypatch.setattr(settings, 'FRUSTRAMPNN_RUNTIME_IDENTITY', identity)
    monkeypatch.setattr(configuration, 'FRUSTRAMPNN_RUNTIME_IDENTITY', identity)
    monkeypatch.setattr(configuration, '_RUNTIME', runtime.runtime_identity_dict(identity))
    inventory = json.loads(settings._CAPABILITY_INVENTORY_PATH.read_bytes())
    inventory['runtime_identity'].update(image_sha256=digest,
        executable_sha256=identity.executable_sha256, checkpoint_sha256=identity.checkpoint_sha256)
    inventory.pop('content_sha256')
    inventory['content_sha256'] = hashlib.sha256(rfc8785.dumps(inventory)).hexdigest()
    inventory_path = tmp_path / 'synthetic-capability-inventory.json'
    inventory_path.write_bytes(canonical_json_bytes(inventory))
    monkeypatch.setattr(settings, '_CAPABILITY_INVENTORY_PATH', inventory_path)
    # Preserve the recorder's stat of the inherited FD. Only sha256sum is served;
    # its answers are hashes of actual declared fixture assets, not invented data.
    source = recorder.read_text()
    assets = {identity.executable_path: str(executable), identity.checkpoint_path: str(checkpoint)}
    source = source.replace("print('LOCAL_TRANSPORT_STOP:", f'''if 'sha256sum' in args:
    import hashlib
    assets = {assets!r}
    asset = Path(assets[args[-1]])
    print(hashlib.sha256(asset.read_bytes()).hexdigest() + '  ' + args[-1])
    raise SystemExit(0)
print('LOCAL_TRANSPORT_STOP:''')
    recorder.write_text(source)
    monkeypatch.syspath_prepend(str(REPO / 'platform/api/tests'))
    from test_structure_prediction_frustrampnn_v2_transport import (
        _prepare_module, _selected_settings, _settings_transport_bytes, _two_model_pdb,
    )
    import base64
    from services.frustrampnn.settings import requested_settings_sha256
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    structure = candidate / 'synthetic-input.pdb'
    structure.write_bytes(_two_model_pdb())
    preparer = _prepare_module()
    producer = dict(producer_method='protenix', producer_sample='transport',
        producer_rank=0, producer_output_key='protenix/transport/model.pdb')
    metadata = dict(parent_job_id='local-transport', parent_workflow_id='structure_prediction',
        producer_stage='structure_prediction:protenix',
        producer_candidate_key='frustrampnn/sources/protenix/transport.normalized.pdb',
        requiredness='required', **producer,
        producer_identity_sha256=preparer.producer_identity_sha256(producer),
        producer_artifact_sha256=hashlib.sha256(structure.read_bytes()).hexdigest(), source_format='pdb')
    selected_settings = _selected_settings()
    decoded = preparer._decode_metadata(base64.b64encode(canonical_json_bytes(metadata)).decode(),
        source=structure, request_version=3)
    request_path = candidate / 'workflow_component_request_v3.json'
    normalized = candidate / 'normalized.pdb'
    structure_map = candidate / 'structure_map.json'
    preparer.prepare_candidate(source=structure, output_pdb=normalized,
        request_path=request_path, metadata=decoded, request_version=3,
        structure_map_path=structure_map, settings_payload=_settings_transport_bytes(selected_settings),
        settings_sha256=requested_settings_sha256(selected_settings), settings_value_origin='operator_request')
    request = json.loads(request_path.read_bytes())
    assert request['parent_workflow_id'] == 'structure_prediction'
    legacy = tmp_path / 'containers/frustrampnn.sif'
    monkeypatch.setattr(runtime, 'get_container_path', lambda name: legacy.parent / name)
    monkeypatch.setattr(runtime, 'get_container_dir', lambda: legacy.parent)
    kwargs = dict(request=request, request_payload=canonical_json_bytes(request),
        source_structure=normalized, structure_map=structure_map,
        output_dir=tmp_path / 'result', container=legacy, physical_gpu_id=0,
        apptainer=recorder, runtime_identity=identity)
    with pytest.raises(component.ComponentRunError, match='nonzero exit 73'):
        component.run_component(**kwargs)
    log = tmp_path / 'invocations.jsonl'
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 3, records
    assert all(r['image'].startswith('/proc/self/fd/') for r in records)
    assert all(r['resolved'] == str(image) for r in records)
    assert all((r['device'], r['inode']) == (image.stat().st_dev, image.stat().st_ino) for r in records)
    assert 'predict' in records[-1]['argv']
    assert not legacy.exists()
    assert list(tmp_path.rglob('*.sif')) == [image]
    assert not (tmp_path / 'result').exists()
    # A new scientifically different runtime is NOT compatible with the retained
    # request. No selector is added and no digest/closure validator is replaced.
    with pytest.raises(component.ComponentRunError, match='runtime does not match'):
        component.run_component(**(kwargs | {'runtime_identity': replace(identity, sif_sha256='0' * 64)}))
    assert len(log.read_text().splitlines()) == 3
    # Also reject actual tampering of the selected object before any Apptainer.
    image.chmod(0o600)
    image.write_bytes(b'CHANGED SYNTHETIC SCIENTIFIC IMAGE BYTES')
    image.chmod(0o400)
    with pytest.raises(component.ComponentRunError, match='SHA-256 differs from expected digest') as rejected:
        component.run_component(**kwargs)
    assert rejected.value.failure_class == 'runtime_identity_mismatch'
    assert len(log.read_text().splitlines()) == 3
    print('Frustra: registered structure_prediction v3 preparation -> component closure -> strict CAS reader -> two asset hashes + predict argv via same-inode pinned FD; digest changes rejected; no science executed')
