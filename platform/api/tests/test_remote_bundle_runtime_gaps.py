"""Real, import-only runtime relocation with split controller roots."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from dataclasses import replace
from component_runtime import NativeInvocation, SourceIdentity

import pytest

from services.remote_execution import bundle, executor
from tools import bms_remote_worker as worker
from services import nextflow


def bundle_assignment_fixture(gpu_indices=(0,)):
    """Transport-only tests receive an explicit claim, not an admission bypass."""
    return {"remote_execution_assignment": {"lease_id": "fixture-lease",
        "gpu_indices": list(gpu_indices)}}


def bundle_metadata_fixture(*, runtime_assets=False):
    """Bounded transport projection of real reviewed native descriptors.

    Only the CPU preparation policy executes in these import/cache probes;
    optional image/weight leaves are transported, never scientific execution.
    Keep its actual dependency and artifact closure, not just resource fields.
    """
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('protenix', 'complex', {
        'pred_method': 'protenix', 'protenix_use_msa': False, 'run_frustrampnn': False,
    }, 'workflows/complex_prediction.nf')
    component = next(row for row in metadata.static_components if row.component_key == 'PrepProtenixComplex')
    dependencies = set(component.dependency_ids)
    roles = set((*component.input_role_ids, *component.output_role_ids))
    metadata = replace(metadata, static_components=(component,), dynamic_templates=(),
        dependencies=tuple(row for row in metadata.dependencies
            if row.logical_id in dependencies or (runtime_assets and row.kind in {'image', 'weights'})),
        artifact_roles=tuple(row for row in metadata.artifact_roles if row.role_id in roles),
        external_services=(), result_contract_json=b'{}')
    assert metadata.complete
    return metadata


@pytest.fixture(autouse=True)
def isolated_image_selection(tmp_path, monkeypatch):
    # Compiler tests must never discover a host installation release or image.
    for key in ('BMS_PROTENIX_CONTAINER_PATH', 'BMS_CM_CONFORNETS_CONTAINER_PATH',
                'BMS_FRUSTRAMPNN_SIF', 'BMS_RUNTIME_IMAGE_LANE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'isolated-store'))
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: tmp_path / 'isolated-containers')


@pytest.fixture
def package(tmp_path, monkeypatch):
    roots = {key: tmp_path / 'controller' / key for key in ('data', 'inputs', 'results', 'weights', 'containers', 'repo', 'runtime')}
    for path in roots.values():
        path.mkdir(parents=True)
    for name, key in [('get_data_root','data'), ('get_code_root','repo'), ('get_inputs_dir','inputs'),
                      ('get_results_dir','results'), ('get_weights_root','weights'), ('get_container_dir','containers')]:
        monkeypatch.setattr(bundle, name, lambda key=key: roots[key])
    release = roots['runtime']/'releases/r1'
    base = release/'python-runtime'
    # Real base interpreter and complete base stdlib, not fake executable bytes.
    from python_runtime_fixture import copy_python_base
    copy_python_base(base)
    venv = release/'venv'
    (venv/'bin').mkdir(parents=True)
    (venv/'bin/python').symlink_to(base/'bin'/f'python{sys.version_info.major}.{sys.version_info.minor}')
    (venv/'bin/python3').symlink_to('python')
    (venv/'pyvenv.cfg').write_text(f'home = {base}/bin\ninclude-system-site-packages = false\n')
    import packaging
    site = venv/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    site.mkdir(parents=True)
    shutil.copytree(Path(packaging.__file__).parent, site/'packaging', ignore=shutil.ignore_patterns('__pycache__'))
    (venv/'bin/probe').write_text(f'#!{sys.executable}\nimport json,packaging; print(json.dumps({{"dependency": packaging.__version__}}))\n')
    (venv/'bin/probe').chmod(0o755)
    current = roots['runtime']/'current'
    current.symlink_to('releases/r1', target_is_directory=True)
    # Compiler-only command identity: never discover the operator's installed
    # Nextflow. A test that actually executes this placeholder must fail.
    launcher = roots['runtime'] / 'nextflow'
    launcher.write_text('#!/bin/sh\nprintf "compiler-only Nextflow fixture must not execute\\n" >&2\nexit 125\n')
    launcher.chmod(0o755)
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', str(launcher))
    for key in ('BMS_PROTENIX_CONTAINER_PATH', 'BMS_CM_CONFORNETS_CONTAINER_PATH',
                'BMS_FRUSTRAMPNN_SIF', 'BMS_RUNTIME_IMAGE_STORE', 'BMS_RUNTIME_IMAGE_LANE'):
        monkeypatch.delenv(key, raising=False)
    from services.frustrampnn import runtime as strict
    from dataclasses import replace
    import hashlib
    monkeypatch.setattr(strict, 'FRUSTRAMPNN_RUNTIME_IDENTITY', replace(
        strict.FRUSTRAMPNN_RUNTIME_IDENTITY,
        configured_sif_path=str(roots['containers'] / 'frustrampnn.sif'),
        sif_sha256=hashlib.sha256(b'fixture-image-not-executed').hexdigest()))
    monkeypatch.setattr(strict, 'get_container_dir', lambda: roots['containers'])
    monkeypatch.setattr(strict, 'get_container_path', lambda name: roots['containers'] / name)
    monkeypatch.setenv('BMS_CM_API_RUNTIME_DIR', str(roots['runtime']))
    monkeypatch.setenv('BMS_API_PYTHON', str(current/'venv/bin/python'))
    monkeypatch.setenv('BMS_REMOTE_API_BASE_URL', 'https://bms.example.invalid')
    (roots['containers']/'protenix.sif').write_bytes(b'image-not-executed')
    (roots['weights']/'protenix').mkdir()
    for member in ('checkpoint/protenix-v2.pt', 'common/components.cif',
                   'common/components.cif.rdkit_mol.pkl', 'common/clusters-by-entity-40.txt',
                   'common/obsolete_release_date.csv'):
        path = roots['weights'] / 'protenix' / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'offline dependency fixture; not model data')
    seq = roots['inputs']/'seq.fasta'
    seq.write_text('>A\nAAAA\n')
    (roots['repo']/'main.nf').write_text('workflow {}\n')
    # Critical projection consumes the actual source runtime, not a fake CLI.
    source_root = Path(__file__).resolve().parents[3]
    for name in ('nextflow.config', 'platform/api/tools/bms_container.py', 'platform/api/tools/bms_nextflow.sh',
                 'platform/api/tools/bms_nextflow_singularity.py', 'scripts/lib/__init__.py',
                 'scripts/lib/shared_runtime_images.py', 'scripts/lib/runtime_image_lifecycle.py',
                 'scripts/lib/runtime_image_views.py', 'scripts/lib/container_runtime.py'):
        destination = roots['repo'] / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / name, destination)
    # Source archiving is an isolated seam: this test cannot mutate Git.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: ('a'*40, 'b'*40))
    from component_runtime import SourceIdentity
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: SourceIdentity('a'*40, 'b'*40))
    monkeypatch.setattr(bundle, '_git', lambda *_: 'b'*40)
    real_run = subprocess.run
    def archive(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            with tarfile.open(fileobj=kwargs['stdout'], mode='w') as tar:
                for member in sorted(roots['repo'].rglob('*')):
                    if member.is_file():
                        tar.add(member, arcname=member.relative_to(roots['repo']).as_posix())
            return SimpleNamespace(returncode=0)
        return real_run(argv, **kwargs)
    monkeypatch.setattr(bundle.subprocess, 'run', archive)
    monkeypatch.setattr(bundle, 'resolve_job_result_contract', lambda job: {})
    output = roots['results']/'job'
    command = ['nextflow', 'run', str(roots['repo']/'main.nf'), '-w', str(roots['data']/'work'),
               '--out_dir', str(output), '--input', str(seq), '--protenix_msa_backend', 'colabfold_api',
               '--weights_root', str(roots['weights']), '--container_dir', str(roots['containers']),
               '--data_root', str(roots['data']), '--code_root', str(roots['repo']),
               '--api_python', str(current/'venv/bin/python'),
               '--msa_cache_dir', str(roots['data']/'cache'),
               '--msa_local_db', str(roots['data']/'absent-offline'),
               '--af2_models', '/ignored/default/af2', '--boltz_models', str(roots['weights']/'boltz')]
    job = SimpleNamespace(id='job', model_id='protenix', mode='predict', child_output_dir=None,
                          output_dir=str(output), lineage_root_job_id=None, parent_job_id=None,
                          execution_source_revision='a'*40, execution_source_tree='b'*40,
                          params={'protenix_msa_backend':'local', 'af2_models':'/ignored/original',
                                  'boltz_models':str(roots['data']/'unrelated')},
                          provenance=bundle_assignment_fixture(), assigned_gpu=0)
    target = SimpleNamespace(id='target', remote_root=str(tmp_path/'remote'))
    for name, value in {'BMS_HOME': roots['repo'], 'BMS_DATA': roots['data'],
                        'BMS_WEIGHTS': roots['weights'], 'BMS_CONTAINER_DIR': roots['containers'],
                        'BMS_WORK': roots['data']/'work', 'BMS_MSA_CACHE': roots['data']/'cache',
                        'BMS_COLABFOLD_DB': roots['data']/'absent-offline'}.items():
        monkeypatch.setenv(name, str(value))
    from services import gpu_config, msa_server
    monkeypatch.setattr(gpu_config, 'read_scheduler_config', lambda: {})
    monkeypatch.setattr(msa_server, 'read_server_settings', lambda: {})
    # Runtime-only probes explicitly request the model-supported no-MSA path.
    # Prepared/search MSA transport is exercised by test_msa_bundle_integration.
    command.extend(['--protenix_use_msa', 'false'])
    # This fixture tests lower-layer runtime relocation, not scientific compilation.
    native = dict(input=str(seq), protenix_msa_backend='colabfold_api',
                  protenix_use_msa=False, weights_root=str(roots['weights']),
                  container_dir=str(roots['containers']), data_root=str(roots['data']),
                  code_root=str(roots['repo']), api_python=str(current/'venv/bin/python'),
                  msa_cache_dir=str(roots['data']/'cache'), msa_local_db=str(roots['data']/'absent-offline'),
                  af2_models='/ignored/default/af2', boltz_models=str(roots['weights']/'boltz'),
                  out_dir=str(output), work_dir=str(roots['data']/'work'))
    job.native_invocation = replace(NativeInvocation.capture(
        model_id=job.model_id, mode=job.mode, command=command, requested=native,
        effective=native, native_parameters=native, entrypoint='main.nf'),
        source_identity=SourceIdentity('a'*40, 'b'*40))
    # Explicit shared relocation fixture, not scientific compilation evidence.
    # Capture intentionally cannot infer a selected dependency plan from argv.
    from component_runtime import SelectedExecutionPlan
    invocation = job.native_invocation
    assert invocation.source_identity is not None and invocation.entrypoint is not None
    metadata = bundle_metadata_fixture(runtime_assets=True)
    job.native_invocation = replace(invocation, execution_plan=SelectedExecutionPlan(
        source_identity=invocation.source_identity, workflow='fixture',
        model_id=invocation.model_id, mode=invocation.mode, entrypoint=invocation.entrypoint,
        requested_json=invocation.requested_json, effective_json=invocation.effective_json,
        native_parameters_json=invocation.native_parameters_json, metadata=metadata))
    job.native_invocation.materialize_inputs(output)
    return roots, release, job, target, command


def compile_native(job, params):
    invocations = []
    argv = nextflow.build_nextflow_command(
        job.model_id, job.mode, params, job.output_dir, job_id=job.id,
        materialize_inputs=False, native_invocations=invocations)
    assert len(invocations) == 1
    job.native_invocation = replace(invocations[0], source_identity=SourceIdentity('a'*40, 'b'*40))

    # Keep the isolated source archive real for selected helper dependencies.
    plan = job.native_invocation.execution_plan
    assert plan is not None
    source_root = Path(__file__).resolve().parents[3]
    for dependency in plan.dependencies:
        if dependency.kind == 'support_tool':
            relative = dependency.relative_path
            assert relative is not None
            destination = bundle.get_code_root() / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, destination)
    job.native_invocation.materialize_inputs(Path(job.output_dir))
    assert tuple(argv) == job.native_invocation.command
    return argv
