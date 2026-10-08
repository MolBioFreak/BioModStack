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
    shutil.copytree(sys.base_prefix, base, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'site-packages'))
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
    (roots['weights']/'protenix/model.pt').write_bytes(b'weights-not-executed')
    seq = roots['inputs']/'seq.fasta'
    seq.write_text('>A\nAAAA\n')
    (roots['repo']/'main.nf').write_text('workflow {}\n')
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
               '--af2_models', '/ignored/default/af2', '--bcp_repo_path', '/ignored/default/bcp']
    job = SimpleNamespace(id='job', model_id='protenix', mode='predict', child_output_dir=None,
                          output_dir=str(output), lineage_root_job_id=None, parent_job_id=None,
                          execution_source_revision='a'*40, execution_source_tree='b'*40,
                          params={'protenix_msa_backend':'local', 'af2_models':'/ignored/original',
                                  'bcp_repo_path':str(roots['data']/'unrelated')},
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
                  af2_models='/ignored/default/af2', bcp_repo_path='/ignored/default/bcp',
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


@pytest.mark.parametrize('shared_critical', [False, True])
def test_normalized_bundle_relocates_real_runtime(package, tmp_path, monkeypatch, shared_critical):
    roots, release, job, target, command = package
    if shared_critical:
        target.remote_root = str(tmp_path / ('remote-' + 'x' * 150 + " space '"))
        from services.remote_execution import critical_runtime as cr
        from test_managed_runtime_safety import install_critical_fixture
        launcher = roots['repo'] / 'nextflow'
        launcher.write_text('#!/bin/sh\nprintf "nextflow version 25.10.1\\n"\n')
        launcher.chmod(0o755)
        jar = roots['repo'] / 'nxf/framework/25.10.1/nextflow-25.10.1-one.jar'
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b'framework fixture, not executed')
        monkeypatch.setenv('NXF_HOME', str(roots['repo'] / 'nxf'))
        monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
        monkeypatch.setattr(nextflow, 'resolve_nextflow_version', lambda: '25.10.1')
        manifest, artifacts = cr.project_runtime(target.remote_root, tmp_path / 'projection')
        observed = dict(system='Linux', machine='x86_64', java='native Java',
                        apptainer='native Apptainer', driver='native driver')
        m, cache, managed_root = install_critical_fixture(
            (manifest, artifacts, observed, Path(target.remote_root)), monkeypatch)
        m.install(managed_root, manifest, m.boot_id(), cache)
        target.capabilities = {'critical_runtime_binding': cr.runtime_binding(target.remote_root, manifest)}
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                            native_invocation=job.native_invocation)
    envelope = prepared.envelope
    assert len(prepared.runtime_transfers) == (2 if shared_critical else 3)
    if shared_critical:
        binding = target.capabilities['critical_runtime_binding']
        assert binding['paths']['nextflow'] in envelope.command
        assert envelope.environment['NXF_HOME'] == binding['environment']['NXF_HOME']
        assert envelope.environment['NXF_OFFLINE'] == 'true'
        assert not any(r.relative_path.startswith('runtime/support-python') for r in envelope.files)
    assert len(prepared.input_transfers) == 3
    assert sum(t.remote_destination.endswith('/.bms/portable-input-bindings.json')
               for t in prepared.input_transfers) == 1
    assert sum(t.remote_destination.endswith('/component-resources.config')
               for t in prepared.input_transfers) == 1
    assert not any(flag in envelope.command for flag in ('--af2_models','--bcp_repo_path','--msa_local_db'))
    assert str(roots['results']) not in ' '.join(envelope.command)
    assert str(tmp_path/'controller') not in ' '.join(envelope.command + list(envelope.environment.values()))
    for transfer in (*prepared.runtime_transfers, *prepared.input_transfers, prepared.source_transfer):
        destination = Path(transfer.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transfer.source.is_dir():
            shutil.copytree(transfer.source, destination, symlinks=True)
        else:
            shutil.copy2(transfer.source, destination)
    attempt = Path(prepared.remote_attempt_dir)
    shutil.copytree(prepared.local_attempt_dir, attempt, dirs_exist_ok=True)
    (attempt/'bundle/source').symlink_to(prepared.remote_source_dir, target_is_directory=True)
    (attempt/'bundle/runtime').symlink_to(prepared.remote_runtime_dir, target_is_directory=True)
    # Remove the complete temporary controller, including its original base stdlib.
    shutil.rmtree(tmp_path/'controller')
    assert not release.exists()
    worker.verify_bundle(attempt)
    interpreter = envelope.environment['BMS_API_PYTHON']
    assert interpreter == envelope.command[envelope.command.index('--api_python')+1]
    if shared_critical:
        assert len(interpreter.encode()) > 255  # cannot be a Linux shebang
    probe = 'import json,ssl,sqlite3,packaging,sys; print(json.dumps({"base":sys.base_prefix,"prefix":sys.prefix,"dependency":packaging.__version__}))'
    result = subprocess.run([interpreter, '-I', '-B', '-c', probe], text=True, capture_output=True, check=True, timeout=20)
    observed = json.loads(result.stdout)
    expected_root = (str(Path(target.capabilities['critical_runtime_binding']['paths']['python']).parents[2])
                     if shared_critical else prepared.remote_runtime_dir)
    assert observed['base'].startswith(expected_root)
    assert observed['prefix'].startswith(expected_root)
    assert observed['dependency']
    entrypoint = subprocess.run([str(Path(interpreter).with_name('probe'))], text=True, capture_output=True, check=True, timeout=20)
    assert json.loads(entrypoint.stdout)['dependency'] == observed['dependency']
    print('REAL_RELOCATION', json.dumps(observed), 'controller_removed=True')


def test_runtime_config_accepts_lexical_current_alias(package, tmp_path):
    roots, release, job, target, command = package
    config = release/'venv/pyvenv.cfg'
    config.write_text(f"home = {roots['runtime']}/current/python-runtime/bin\ninclude-system-site-packages = false\n")
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                            native_invocation=job.native_invocation)
    transfer = next(t for t in prepared.runtime_transfers if t.origin == release)
    assert str(roots['runtime']) not in (transfer.source/'venv/pyvenv.cfg').read_text()


@pytest.mark.parametrize('workflow, requested, selected', [
    ('structure_prediction', {}, True),
    ('structure_prediction', {'run_frustrampnn': False}, False),
    ('structure_prediction', {'run_frustrampnn': True}, True),
    ('complex_prediction', {}, False),
    ('complex_prediction', {'run_frustrampnn': False}, False),
    ('complex_prediction', {'run_frustrampnn': True}, True),
    ('protein_design', {}, False),
    ('protein_design', {'run_frustrampnn': True}, True),
])
def test_selected_native_frustra_predicates(workflow, requested, selected):
    from model_registry import selected_execution_metadata
    effective = {'pred_method': 'protenix', 'protenix_use_msa': False, **requested}
    snapshot = json.loads(json.dumps(effective))
    metadata = selected_execution_metadata('protenix', 'predict', effective, f'workflows/{workflow}.nf')
    names = {ref.relative_path for ref in metadata.dependencies}
    assert ('frustrampnn.sif' in names) is selected
    assert bool(metadata.dynamic_templates) is selected
    assert effective == snapshot  # Native default is described, not silently written into science.
    if selected:
        assert metadata.dynamic_templates[0].requiredness == 'required'
        assert any(row.component_or_dependency_id == 'frustrampnn' for row in metadata.blockers)
    assert metadata.dependency_closure_complete is (workflow != 'protein_design')
    if selected:
        assert not metadata.complete  # Missing typed Frustra settings still block launch.


@pytest.mark.parametrize('method, expected', [
    ('boltz', {'boltz2.sif', 'boltz'}),
    ('protenix', {'protenix.sif', 'protenix'}),
    ('boltz_protenix', {'boltz2.sif', 'boltz', 'protenix.sif', 'protenix'}),
    ('esmfold2', {'esmfold2.sif', 'esmfold2'}),
])
def test_selected_prediction_managed_closure_uses_native_names(method, expected, monkeypatch):
    from model_registry import get_registry, selected_execution_metadata
    get_registry()  # Existing declarations loaded before the no-IO resolution guard.
    monkeypatch.setattr(Path, 'exists', lambda *args: pytest.fail('metadata probed an input or image'))
    monkeypatch.setattr(bundle, 'resolve_image', lambda *args: pytest.fail('metadata resolved an image'))
    metadata = selected_execution_metadata('protenix', 'predict', {
        'pred_method': method, 'run_frustrampnn': False,
        'protenix_use_msa': False, 'boltz_use_msa': False,
    }, 'workflows/structure_prediction.nf')
    refs = {ref.relative_path for ref in metadata.dependencies if ref.kind in {'image', 'weights'}}
    assert refs == expected
    assert 'boltz2-v2.9.5-7ebf1be.sif' not in refs
    assert {'support_python', 'critical_runtime'} <= {ref.kind for ref in metadata.dependencies}
    assert metadata.static_components
    assert json.loads(metadata.result_contract_json)['analysis_contract_id'] == 'structure_prediction_v1'
    assert metadata.to_dict()['dependency_closure_complete'] is True
    assert not metadata.blockers_for('provision')


def test_selected_msa_intent_preserves_provider_settings_without_submission():
    from model_registry import selected_execution_metadata
    settings = {'pred_method': 'protenix', 'protenix_use_msa': True, 'run_frustrampnn': False,
                'msa_provider': 'neurosnap', 'msa_neurosnap_max_sequences': 0,
                'colabfold_use_templates': False, 'protenix_seeds': [0, 3],
                'msa_cache_only': True}
    metadata = selected_execution_metadata('protenix', 'predict', settings, 'workflows/structure_prediction.nf')
    service, = metadata.external_services
    assert service.provider == 'neurosnap'
    assert service.operation_identity is None
    assert service.state == 'planned'
    saved = json.loads(service.settings_json)
    assert saved['msa_neurosnap_max_sequences'] == 0
    assert saved['colabfold_use_templates'] is False
    assert saved['protenix_seeds'] == [0, 3]
    assert saved['msa_cache_only'] is True
    assert any(row.field == 'external_service_roles' for row in metadata.blockers)
    supplied = selected_execution_metadata('protenix', 'predict', {**settings, 'msa_path': '/not-probed.a3m'},
                                          'workflows/structure_prediction.nf')
    assert supplied.external_services[0].state == 'supplied_or_prepared'
    assert supplied.blockers  # A string path is not validated alignment identity.


@pytest.mark.parametrize('msa, supplied, generated', [
    (False, None, False), (True, None, True), (True, '/not-probed.a3m', False),
])
def test_selected_boltz_msa_stage_is_native_condition(msa, supplied, generated):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('boltz2', 'predict', {
        'pred_method': 'boltz', 'boltz_use_msa': msa, 'msa_path': supplied,
        'run_frustrampnn': False, 'msa_provider': 'colabfold_api',
    }, 'workflows/structure_prediction.nf')
    stages = {component.component_key: component for component in metadata.static_components}
    assert ('GenerateLocalMSA' in stages) is generated
    predictor = 'BoltzFromSequenceWithMSATask' if msa else 'BoltzFromSequenceTask'
    assert predictor in stages
    if generated:
        assert 'GenerateLocalMSA' in stages[predictor].depends_on
        assert any(ref.relative_path == 'scripts/run_local_msa.py' for ref in metadata.dependencies)
    assert all(ref.kind != 'reference_database' for ref in metadata.dependencies)
    assert metadata.dependency_closure_complete
    assert not metadata.blockers_for('provision')
    if msa:
        assert not metadata.complete  # Alignment identity remains a launch binding.


def test_selected_runtime_catalog_includes_native_preparation():
    from model_registry import model_runtime_dependencies
    expected = {
        'boltz2': {('image', 'boltz2.sif'), ('weights', 'boltz')},
        'af2': {('image', 'af2.sif'), ('weights', 'alphafold'), ('image', 'pyrosetta_tools.sif')},
        'proteinmpnn': {('image', 'dl_binder_design.sif'), ('image', 'pyrosetta_tools.sif')},
        'fampnn': {('image', 'fampnn.sif'), ('image', 'pyrosetta_tools.sif')},
        'unidock': {('image', 'unidock.sif')},
    }
    for model, refs in expected.items():
        assert {(row.kind, row.relative_path) for row in model_runtime_dependencies(model)} == refs
    # Runtime downloads are not magically made complete by an image declaration.
    for model in ('diffdock', 'boltzgen'):
        with pytest.raises(ValueError):
            model_runtime_dependencies(model)


@pytest.mark.parametrize('settings, protocol', [
    ({}, 'protein-anything'),
    ({'boltzgen_ligand_smiles': 'CCO'}, 'protein-small_molecule'),
    ({'boltzgen_ntp_type': 'ATP'}, 'protein-small_molecule'),
    ({'boltzgen_catalytic_site': True}, 'protein-small_molecule'),
    ({'boltzgen_protein_sequence': 'AAA', 'boltzgen_dna_template_seq': 'AT',
      'boltzgen_ligand_smiles': 'CCO', 'boltzgen_catalytic_site': True}, 'protein-anything'),
    ({'boltzgen_nanobody_framework': 'AXXX'}, 'protein-anything'),
])
def test_boltzgen_auto_native_preparation_metadata(settings, protocol):
    from scripts.lib.boltzgen_native import preview_protocol_metadata
    from model_registry import native_checkpoint_dependencies
    params = {'boltzgen_protocol': 'auto', **settings}
    before = dict(params)
    metadata = preview_protocol_metadata(params)
    assert params == before
    assert metadata['state'] == 'ok'
    assert metadata['effective_protocol'] == protocol
    assert len(metadata['config_identity']['yaml_sha256']) == 64
    dependencies, blockers = native_checkpoint_dependencies('RunBoltzGen',
        {**params, '_boltzgen_protocol_metadata': metadata})
    assert not blockers
    assert ('boltzgen/boltz2_aff.ckpt' in {d.relative_path for d in dependencies}) == (protocol == 'protein-small_molecule')
    # Browser JSON cannot masquerade as trusted in-process compiler metadata.
    _, forged = native_checkpoint_dependencies('RunBoltzGen',
        {**params, '_boltzgen_protocol_metadata': dict(metadata)})
    assert forged
    _, stale = native_checkpoint_dependencies('RunBoltzGen',
        {**params, 'boltzgen_ligand_smiles': 'changed', '_boltzgen_protocol_metadata': metadata})
    assert stale


@pytest.mark.parametrize('key', ['input_pdb', 'ligand_pdb', 'dna_structure', 'target_pdb_path'])
def test_boltzgen_auto_file_authority_is_explicit(key):
    from scripts.lib.boltzgen_native import preview_protocol_metadata
    metadata = preview_protocol_metadata({'boltzgen_protocol': 'auto', 'boltzgen_' + key: '/not/read'})
    assert metadata['state'] == 'unresolved'
    assert metadata['unresolved_inputs'] == ['target_pdb' if key == 'target_pdb_path' else key]
    assert metadata['config_identity']['generated_input'] == 'boltzgen_input.yaml'


@pytest.mark.parametrize('enabled, threshold, selected', [(False, 1, False), (True, None, False), (True, 0, True)])
def test_selected_fampnn_child_keeps_native_filter_predicate(enabled, threshold, selected):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('fampnn', 'design', {
        'enable_fampnn_filter': enabled, 'fampnn_max_psce': threshold,
    }, 'workflows/fampnn_child.nf')
    stages = {row.component_key: row for row in metadata.static_components}
    assert ('FilterFAMPNN' in stages) is selected
    assert stages['RunFAMPNN'].depends_on == ('PrepFAMPNN',)
    assert metadata.dependency_closure_complete
    assert not metadata.blockers_for('provision')
    assert stages['RunFAMPNN'].resources_json is not None
    assert json.loads(stages['RunFAMPNN'].resources_json)['memory']['value'] == '16GB'


def test_selected_docking_graph_does_not_invent_diffdock_filter_or_weight_closure():
    from model_registry import selected_execution_metadata
    dual = selected_execution_metadata('docking', 'compare', {'docking_engine': 'compare'}, 'workflows/docking.nf')
    assert {row.component_key for row in dual.static_components} == {
        'PrepDiffDock', 'RunDiffDock', 'PrepUniDock', 'RunUniDock', 'FilterUniDock'}
    assert not dual.dependency_closure_complete
    assert any(row.component_or_dependency_id == 'weights:diffdock:workdir/v1.1/score_model/model_parameters.yml'
               for row in dual.blockers_for('provision'))
    unidock = selected_execution_metadata('unidock', 'dock', {'docking_engine': 'unidock'}, 'workflows/docking.nf')
    assert unidock.dependency_closure_complete
    assert not unidock.blockers_for('provision')
    flex = next(row for row in unidock.artifact_roles if row.role_id == 'RunUniDock:input:flex_receptor')
    assert flex.requiredness == 'optional'


def test_selected_frustra_expansion_consumes_only_canonical_candidates():
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('protenix', 'predict', {
        'pred_method': 'protenix', 'protenix_use_msa': False, 'run_frustrampnn': True,
        'frustrampnn_settings': '{}',
    }, 'workflows/structure_prediction.nf')
    stage, = metadata.dynamic_templates
    assert stage.input_role_ids == ('protenix:canonical_structures',)
    assert stage.expansion_authority is not None and stage.lifecycle_json is not None
    assert stage.expansion_authority.endswith(':plan_frustrampnn')
    assert json.loads(stage.lifecycle_json)['max_retries'] == 0
    assert any(row.role_id == 'protenix:logs' and row.requiredness == 'optional'
               for row in metadata.artifact_roles)
    assert metadata.dependency_closure_complete
    assert not metadata.blockers_for('provision')
    with pytest.raises(ValueError):
        metadata.blockers_for('unknown')


def test_selected_unknown_declarations_never_become_empty_complete():
    from dataclasses import replace
    from model_registry import get_registry, selected_execution_metadata, model_runtime_dependencies
    registry = get_registry()
    # Raw definitions retain disabled/internal entries rather than public filtering.
    for model in registry._models.values():
        for mode in model.modes:
            metadata = selected_execution_metadata(model.id, mode.id, {}, model.workflow or
                'workflows/unreviewed_native_workflow.nf')
            assert metadata.blockers and not metadata.complete
            assert not metadata.dependency_closure_complete
    unknown = selected_execution_metadata('not-a-model', 'required', {}, 'workflows/unreviewed.nf')
    assert unknown.availability == 'unknown' and not unknown.static_components
    assert any(row.field == 'dependency_closure' for row in unknown.blockers)
    assert not replace(unknown, dependencies=(), blockers=(), closure_reviewed=True,
                       descriptors_reviewed=True).complete
    internal = selected_execution_metadata('frustrampnn', 'analyze', {}, 'workflows/unreviewed.nf')
    assert internal.availability == 'internal'
    assert registry.get_model('frustrampnn') is None
    with pytest.raises(ValueError):
        model_runtime_dependencies('frustrampnn')
    assert model_runtime_dependencies('frustrampnn', internal=True)[0].relative_path == 'frustrampnn.sif'


def test_effective_dependency_inventory_omits_other_model_runtime_defaults(package):
    roots, release, job, target, _ = package
    from services.frustrampnn.settings import default_settings
    command = compile_native(job, dict(sequence='AAAA', protenix_use_msa=False,
        msa_provider='colabfold_api', rf3_container_path='/unrelated/rf3.sif', run_frustrampnn=True,
        frustrampnn_settings=default_settings().model_dump_json(), gpu_id=0))
    compiled, params = bundle.compile_remote_dependencies('protenix', 'predict', command,
                                                         native_invocation=job.native_invocation)
    assert '--rf3_container_path' not in compiled
    assert params['run_frustrampnn'] is True


def test_actual_normalized_nextflow_command_omits_unrelated_original_params(package):
    roots, release, job, target, command = package
    normalized = dict(job.params, protenix_msa_backend='colabfold_api', sequence='AAAA', protenix_use_msa=False,
                      api_python=str(roots['runtime']/'current/venv/bin/python'), run_frustrampnn=False)
    argv = compile_native(job, normalized)
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=argv,
                                            native_invocation=job.native_invocation)
    assert len(prepared.runtime_transfers) == 3
    assert len(prepared.input_transfers) == 2
    assert prepared.input_transfers[0].remote_destination.endswith('/.bms/portable-input-bindings.json')
    assert prepared.input_transfers[1].remote_destination.endswith('/component-resources.config')
    assert '--msa_local_db' not in prepared.envelope.command
    assert '--af2_models' not in prepared.envelope.command
    assert str(roots['data']) not in ' '.join(prepared.envelope.command)
    store = prepared.envelope.command[prepared.envelope.command.index('--runtime_image_store') + 1]
    assert store == prepared.envelope.environment['BMS_RUNTIME_IMAGE_STORE']
    assert store.endswith('/cache/runtime-images')
    assert '/attempts/' not in store


@pytest.mark.parametrize('native', [None, 'auto', 'colabfold_api'])
@pytest.mark.parametrize('use_msa', [False, True])
def test_remote_consumes_compiler_resolved_backend_without_mutation(package, monkeypatch, native, use_msa):
    roots, release, job, target, _ = package
    params = dict(sequence='AAAA', msa_provider='colabfold_api', protenix_use_msa=use_msa,
                  run_frustrampnn=False)
    if native is not None:
        params['protenix_msa_backend'] = native
    command = compile_native(job, params)
    if use_msa:
        from services import msa_preparation
        from services.model_msa_handoff import prepare_launch_msa
        from prepare_protenix_msa import iter_protein_chains
        supplied = roots['inputs'] / 'supplied.a3m'
        supplied.write_text('>query\nAAAA\n>hit\nAA-A\n')
        real_prepare = msa_preparation.prepare_protenix_inputs
        def supplied_input(config, input_json, destination, settings):
            # Explicit supplied-alignment seam; keep real packaging and binding.
            payload = json.loads(input_json.read_text())
            for _, _, chain in iter_protein_chains(payload):
                chain['unpairedMsaPath'] = str(supplied)
            input_json.write_text(json.dumps(payload))
            return real_prepare(config, input_json, destination, settings)
        monkeypatch.setattr(msa_preparation, 'prepare_protenix_inputs', supplied_input)
        prepared = prepare_launch_msa(job.model_id, job.native_invocation.native_parameters,
                                     Path(job.output_dir) / 'prepared-msa')
        job.native_invocation = nextflow._bind_protenix_msa_transport(job.native_invocation, prepared)
        command = list(job.native_invocation.command)
    assert job.native_invocation.execution_plan.complete
    original = list(command)
    before = job.native_invocation.native_parameters
    compiled, effective = bundle.compile_remote_dependencies('protenix', 'predict', command,
                                                             native_invocation=job.native_invocation)
    assert command == original
    assert job.native_invocation.native_parameters == before
    assert effective['protenix_msa_backend'] == before['protenix_msa_backend'] == 'colabfold_api'
    assert '--msa_local_db' not in compiled


def test_remote_rejects_command_mismatch(package):
    _, _, job, _, command = package
    with pytest.raises(bundle.RemoteBundleError, match='does not match'):
        bundle.compile_remote_dependencies(job.model_id, job.mode, command + ['--changed'],
                                           native_invocation=job.native_invocation)


def test_remote_rejects_invocation_source_mismatch(package):
    _, _, job, target, command = package
    invocation = replace(job.native_invocation, source_identity=SourceIdentity('c'*40, 'd'*40))
    with pytest.raises(bundle.RemoteBundleError, match='source identity'):
        bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                     native_invocation=invocation)


def test_cancelled_smaller_request_shape_keeps_enabled_stage_assets(package):
    roots, release, job, target, command = package
    (roots['containers']/'frustrampnn.sif').write_bytes(b'fixture-image-not-executed')
    from services.frustrampnn.settings import default_settings
    normalized = dict(
        frustrampnn_settings=default_settings().model_dump_json(),
        msa_provider='colabfold_api', allow_retries=False, sequence='AAAA', gpu_id=0,
        protenix_n_sample=5, protenix_n_cycle=10, protenix_n_step=200,
        protenix_seeds='42', protenix_use_msa=False, protenix_use_template=False,
        run_frustrampnn=True, pred_method='protenix', structure_validator='protenix',
        api_python=str(roots['runtime']/'current/venv/bin/python'),
    )
    argv = compile_native(job, normalized)
    _, effective = bundle.compile_remote_dependencies('protenix', 'predict', argv,
                                                       native_invocation=job.native_invocation)
    assert effective.get('protenix_msa_backend') == 'colabfold_api', {
        key: value for key, value in effective.items() if 'msa' in key
    }
    # Do not silently drop an enabled stage or feed an alias to its strict registry.
    assets = bundle._runtime_assets('protenix', 'predict', effective,
                                    native_invocation=job.native_invocation)
    assert {'containers/protenix.sif', 'containers/frustrampnn.sif'} <= {relative for _, relative in assets}
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=argv,
                                            native_invocation=job.native_invocation)
    sif_records = [r for r in prepared.envelope.files if r.relative_path.endswith('.sif')]
    assert sif_records == []
    assert len(prepared.runtime_images) == 2
    selected = prepared.envelope.environment['BMS_FRUSTRAMPNN_SIF']
    assert any(image.remote_destination == selected and
               any(alias.endswith('/frustrampnn.sif') for alias in image.aliases)
               for image in prepared.runtime_images)


@pytest.mark.asyncio
async def test_staging_has_no_controller_aliases(package, monkeypatch):
    roots, release, job, target, command = package
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                            native_invocation=job.native_invocation)
    calls = []
    async def remote(connection, argv, **kwargs):
        calls.append(argv)
    async def transfer(*args, **kwargs):
        pass
    monkeypatch.setattr(executor, 'run_remote', remote)
    monkeypatch.setattr(executor, '_transfer_plan', transfer)
    monkeypatch.setattr(executor, 'rsync_to_remote', transfer)
    monkeypatch.setattr("services.remote_execution.cache.stage_cached_bundle", transfer)
    await executor._stage_bundle(None, prepared)
    assert str(roots['results']) not in str(calls)
    assert str(roots['data']) not in str(calls)
    assert any(prepared.envelope.environment['BMS_MSA_CACHE'] in argv for argv in calls)
