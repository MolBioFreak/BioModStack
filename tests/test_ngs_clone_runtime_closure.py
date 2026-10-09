"""Selected native closure and genuine released-asset relocation controls.

Set BMS_TEST_NGS_RELEASE_ASSETS to the qualified science assets directory for
real Git/image/JAR/model controls; no downloaded or fabricated runtime fixture.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess

import pytest
from test_ngs_native_components import native, metadata, ROOT


@pytest.mark.parametrize('tool,count', [('flye', 4), ('canu', 5)])
def test_clone_selected_closure(native, tool, count):
    graph, deps, _, blockers = metadata(native, 'wf_clone_validation', {
        'fastq_path': '/reads.fastq', 'reference_fasta': '/reference.fa',
        'wf_clone_assembly_tool': tool})
    assert not blockers
    selected = [d for d in deps.values() if '/images/' in (d.relative_path or '')]
    assert len(selected) == count
    assert all(d.semantic_release.startswith('sha256:') for d in selected)
    assert all(d.logical_id in graph['RunCloneValidation'].dependency_ids for d in selected)
    assert not any(d.kind == 'weights' for d in deps.values())
    assert any(d.relative_path.endswith('/source') for d in deps.values() if d.relative_path)


@pytest.mark.parametrize('molecule', ['dna', 'rna'])
@pytest.mark.parametrize('quality', ['fast', 'hac', 'sup'])
def test_current_dorado_selected_model(native, molecule, quality):
    graph, deps, _, blockers = metadata(native, 'ont_basecall_' + molecule, {
        'pod5_dir': '/reads', 'ont_molecule_type': molecule, 'dorado_quality_mode': quality})
    assert not blockers
    selected = [d for d in deps.values() if d.kind == 'weights']
    lock = json.loads((ROOT / 'config/ngs/dorado_v2.1.2.lock.json').read_text())
    assert len(selected) == 1
    assert selected[0].selector_subpath == lock['models'][molecule][quality]['id']
    assert selected[0].relative_path.startswith('dorado/2.1.2/')
    assert selected[0].logical_id in graph['DoradoBasecall'].dependency_ids


@pytest.mark.parametrize('params,count', [({'dorado_basecall_mode': 'duplex'}, 2),
    ({'modified_bases': '5mC_5hmC'}, 2), ({'modified_bases': '6mA'}, 2)])
def test_dorado_only_selected_extra_models(native, params, count):
    rows = native.ngs_dorado_model_dependencies({'dorado_quality_mode': 'hac', **params}, 'ont_basecall_dna')
    assert len(rows) == count
    assert len({row.selector_subpath for row in rows}) == count


def test_ordinary_cpu_and_assembly_off_are_weight_free(native):
    _, deps, _, blockers = metadata(native, 'ont_construct_screening', {
        'fastq_path': '/reads.fastq', 'reference_fasta': '/reference.fa', 'run_assembly': False})
    assert not blockers
    assert not any(d.kind == 'weights' or 'wf-clone-validation' in d.logical_id for d in deps.values())


def test_system_clone_lock_environment_is_not_scientific_input(native, tmp_path, monkeypatch):
    lock = ROOT / 'config/ngs/wf_clone_validation_v1.8.4.lock.json'
    selected = tmp_path / 'system.lock.json'
    selected.write_bytes(lock.read_bytes())
    monkeypatch.setenv('BMS_WF_CLONE_RUNTIME_LOCK', str(selected))
    dependencies, paths = native.ngs_clone_runtime_dependencies({})
    key = 'runtime_data:ngs/wf-clone-validation/runtime.lock.json'
    assert paths[key] == selected
    assert next(d for d in dependencies if d.logical_id == key).semantic_release == (
        'sha256:' + hashlib.sha256(selected.read_bytes()).hexdigest())


@pytest.fixture
def released(tmp_path, monkeypatch, native):
    asset_setting = os.environ.get('BMS_TEST_NGS_RELEASE_ASSETS')
    if not asset_setting:
        pytest.skip('set BMS_TEST_NGS_RELEASE_ASSETS for genuine released-byte qualification')
    assets = Path(asset_setting).resolve()
    assert assets.is_dir()
    from services.remote_execution import bundle
    prepared = os.environ.get('BMS_TEST_NGS_PREPARED_ROOT')
    installed = Path(prepared) if prepared else tmp_path / 'installed'
    installed.mkdir(exist_ok=bool(prepared))
    selected_lock = Path(os.environ.get('BMS_TEST_NGS_CLONE_LOCK') or
                         ROOT / 'config/ngs/wf_clone_validation_v1.8.4.lock.json')
    lock = json.loads(selected_lock.read_text())
    from scripts.validate_wf_clone_runtime import resolve_lock_path
    for source, name in ((resolve_lock_path(selected_lock, lock['patched_source']['path']), 'source'),
                         (assets / 'selected-flye-images', 'selected-flye-images'),
                         (assets / 'nxf-home', 'nxf-home')):
        if not prepared:
            subprocess.run(['cp', '-a', '--reflink=auto', str(source), str(installed / name)], check=True)
    if not prepared:
        subprocess.run(['cp', '--reflink=auto', str(assets / 'dorado-2.1.2.sif'), str(installed / 'dorado.sif')], check=True)
    launcher = installed / 'nextflow'
    shutil.copy2(assets / 'nextflow', launcher)
    # Use the shipped lock, not an invented release identity.
    lock['patched_source']['path'] = str(installed / 'source')
    lock['containers']['cache_dir'] = str(installed / 'selected-flye-images')
    lock['nextflow']['executable'] = str(launcher)
    lock['compatibility_patch']['path'] = str(ROOT / 'patches/wf-clone-validation-v1.8.4-nextflow25.patch')
    for image in lock['containers']['images']:
        if image.get('definition'):
            image['definition'] = str(ROOT / 'apptainer/medaka.def')
    lock_path = installed / 'runtime.lock.json'
    lock_path.write_text(json.dumps(lock))
    params = {'ont_workflow_id': 'wf_clone_validation', 'ont_input_mode': 'fastq',
              'fastq_path': str(tmp_path / 'reads.fastq'),
              'reference_fasta': str(tmp_path / 'reference.fa'),
              'wf_clone_runtime_lock': str(lock_path),
              'wf_clone_nxf_home': str(installed / 'nxf-home'),
              'wf_clone_assembly_tool': 'flye', 'wf_clone_min_quality': 8,
              'wf_clone_basecaller_model': lock['models']['default'],
              'dorado_runtime_sif': str(installed / 'dorado.sif')}
    Path(params['fastq_path']).write_text('@r\nACGT\n+\nIIII\n')
    Path(params['reference_fasta']).write_text('>ref\nACGT\n')
    monkeypatch.setattr(bundle, 'get_data_root', lambda: installed.parent.parent if prepared else tmp_path)
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: installed)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: installed / 'models')
    monkeypatch.setattr(bundle, 'get_code_root', lambda: ROOT)
    monkeypatch.setenv('NXF_HOME', str(installed / 'nxf-home'))
    monkeypatch.setenv('NXF_OFFLINE', 'true')
    monkeypatch.setenv('NXF_VER', lock['nextflow']['version'])
    monkeypatch.setenv('BMS_NEXTFLOW_EXECUTABLE', str(launcher))
    monkeypatch.setenv('BMS_NGS_RUNTIME_SIF', str(installed / 'dorado.sif'))
    return bundle, installed, lock, params


def test_released_closure_real_compiler_relocation_and_native_validation(released, tmp_path, monkeypatch):
    bundle, installed, lock, params = released
    from services.nextflow import compile_nextflow_invocation
    invocation = compile_nextflow_invocation('nanopore', 'clone_validation', params,
                                             str(tmp_path / 'out'), job_id='closure-control')
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    command, effective = bundle.compile_remote_dependencies('nanopore', 'clone_validation',
        list(invocation.command), native_invocation=invocation)
    assert effective['wf_clone_min_quality'] == 8
    monkeypatch.setenv('BMS_WF_CLONE_RUNTIME_LOCK', params['wf_clone_runtime_lock'])
    env_params = {key: value for key, value in params.items()
                  if key not in {'wf_clone_runtime_lock', 'dorado_runtime_sif'}}
    env_invocation = compile_nextflow_invocation('nanopore', 'clone_validation', env_params,
        str(tmp_path / 'env-out'), job_id='environment-control')
    assert env_invocation.execution_plan.complete
    _, env_effective = bundle.compile_remote_dependencies('nanopore', 'clone_validation',
        list(env_invocation.command), native_invocation=env_invocation)
    assert env_effective['wf_clone_runtime_lock'] == params['wf_clone_runtime_lock']
    assert env_effective['dorado_runtime_sif'] == params['dorado_runtime_sif']
    assets = bundle._runtime_assets('nanopore', 'clone_validation', effective,
                                    native_invocation=invocation, publication={})
    selected = [(path, name) for path, name in assets if 'wf-clone-validation' in name]
    assert len([1 for _, name in selected if '/images/' in name]) == 4
    assert all(not bundle._is_runtime_image(path, name)
               for path, name in selected if '/images/' in name)
    assert not any('canu' in name or name.startswith('weights/') for _, name in assets)
    remote = tmp_path / 'relocated'
    hashes = {}
    cache_events = []
    from tools.bms_artifact_cache import Cache
    cache_root = tmp_path / 'worker/cache/artifacts/v1'
    cache = Cache(cache_root, events=cache_events.append)
    for source, name in selected:
        destination = remote / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith('/medaka-2.2.2.img'):
            image = next(image for image in lock['containers']['images'] if image['label'] == 'medaka')
            item = {'sha256': image['sha256'], 'size_bytes': source.stat().st_size}
            incoming = cache_root / 'incoming' / source.name
            subprocess.run(['cp', '--reflink=auto', str(source), str(incoming)], check=True)
            assert cache.ingest(item, incoming)['state'] == 'ready'
            assert cache.ingest(item, incoming)['cache_hit'] is True
            assert cache.materialize(item, destination, remote)['state'] == 'ready'
            assert destination.is_file() and not destination.is_symlink()
        elif source.is_dir():
            subprocess.run(['cp', '-a', '--reflink=auto', str(source), str(destination)], check=True)
        else:
            subprocess.run(['cp', '--reflink=auto', str(source), str(destination)], check=True)
        rows = bundle._records_for_source(source, 'runtime/' + name, 'runtime')
        hashes.update({r.relative_path.removeprefix('runtime/'): r.sha256 for r in rows if r.link_target is None})
    bundle.verify_selected_runtime_hashes(invocation.execution_plan, hashes)
    # The selected critical launcher remains the actual engine entrypoint. JVM is
    # supplied by the existing native worker prerequisite, never a controller copy.
    engine = remote / 'critical-nextflow'
    shutil.copy2(installed / 'nextflow', engine)
    transfer, record, home, version = bundle._relocate_ngs_clone_lock(effective,
        staging_root=tmp_path, remote_runtime=str(remote), remote_source=str(ROOT),
        nextflow_executable=str(engine),
        expected_lock_sha256=hashes['data/ngs/wf-clone-validation/runtime.lock.json'])
    Path(transfer.remote_destination).write_bytes(transfer.source.read_bytes())
    assert bundle._sha256_file(Path(transfer.remote_destination)) == record.sha256
    monkeypatch.setenv('NXF_HOME', home)
    monkeypatch.setenv('NXF_VER', version)
    monkeypatch.setenv('BMS_NEXTFLOW_EXECUTABLE', str(engine))
    from scripts.validate_wf_clone_runtime import validate_runtime
    result = validate_runtime(Path(transfer.remote_destination), lock['models']['default'], 'flye')
    assert result['validation_status'] == 'valid'
    assert any(event['state'] == 'cache_hit' for event in cache_events)
    assert result['patched_source']['commit'] == lock['patched_source']['commit']
    assert result['nextflow']['executable'] == str(engine)
    assert len(result['images']) == 4
    from scripts.lib.component_adapter import wf_clone_container_config
    config = wf_clone_container_config(transfer.remote_destination, 'flye')
    assert 'medaka-2.2.2.img' in config and 'canu' not in config
    (tmp_path / 'relocation-provenance.json').write_text(json.dumps(result, indent=2))
    with pytest.raises(bundle.RemoteBundleError, match='lock changed'):
        bundle._relocate_ngs_clone_lock(effective, staging_root=tmp_path,
            remote_runtime=str(remote), remote_source=str(ROOT), nextflow_executable=str(engine),
            expected_lock_sha256='0' * 64)


def test_released_dorado_models_selected_and_verified(native, tmp_path, monkeypatch):
    setting = os.environ.get('BMS_TEST_NGS_RELEASE_ASSETS')
    if not setting:
        pytest.skip('set BMS_TEST_NGS_RELEASE_ASSETS for genuine model verification')
    installed = Path(setting)
    from scripts.dorado_p4_preflight import verify_model_identity
    lock = json.loads((ROOT / 'config/ngs/dorado_v2.1.2.lock.json').read_text())
    for molecule in ('dna', 'rna'):
        for quality in ('fast', 'hac', 'sup'):
            rows = native.ngs_dorado_model_dependencies({'ont_molecule_type': molecule,
                'dorado_quality_mode': quality}, 'ont_basecall_' + molecule)
            assert len(rows) == 1
            model = lock['models'][molecule][quality]
            verify_model_identity(model, installed / 'models' / rows[0].selector_subpath)
    for model in [lock['models']['stereo'], *lock['models']['modified_bases'].values()]:
        verify_model_identity(model, installed / 'models' / model['id'])
    # Exercise the existing custom-root/member projection with actual model bytes,
    # not only dependency dictionaries or a renamed old-version directory.
    from services.remote_execution import bundle
    from services.nextflow import compile_nextflow_invocation
    model = lock['models']['dna']['fast']
    models = tmp_path / 'selected-models'
    models.mkdir()
    shutil.copytree(installed / 'models' / model['id'], models / model['id'])
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: models)
    params = {'ont_workflow_id': 'ont_basecall_dna', 'ont_input_mode': 'pod5',
              'pod5_dir': str(tmp_path / 'pod5'), 'dorado_quality_mode': 'fast',
              'dorado_model_root': str(models)}
    invocation = compile_nextflow_invocation('nanopore', 'basecall_dna', params,
        str(tmp_path / 'out'), job_id='model-root-control')
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    selected = bundle._runtime_assets('nanopore', 'basecall_dna', invocation.native_parameters,
        native_invocation=invocation, only_kinds=frozenset({'weights'}))
    assert selected == [(models / model['id'], 'weights/dorado/2.1.2/' + model['id'])]
    verify_model_identity(model, selected[0][0])
