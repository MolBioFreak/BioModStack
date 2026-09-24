"""Offline bridge execution, not native/GPU campaign acceptance."""
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from component_runtime import NativeInvocation, SourceIdentity
from services.remote_execution import bundle, cache, executor
from scripts.lib.portable_inputs import discover_native_input_references
from test_remote_bundle_path_gaps import roots
from test_remote_bundle_runtime_gaps import package
from test_remote_cache_integration import cache_only_plan_fixture
from tools import bms_remote_worker as worker


@pytest.mark.parametrize('model,mode', [('binder_refinement', 'refine'), ('caliby_binder', 'design')])
def test_selected_sources_and_historical_lineage_are_distinct(roots, model, mode):
    first, second = roots['inputs'] / 'first.pdb', roots['inputs'] / 'second.pdb'
    first.write_text('selected source one')
    second.write_text('selected source two')
    manifest = roots['inputs'] / 'source_identity.json'
    manifest.write_text(json.dumps([{'staged_name': first.name, 'source_path': '/offline/parent.cif',
                                     'source_meta': {'id': 'candidate', 'structure_state': 'state-B'}}]))
    params = {'pdb_paths': f'{first},{second}', 'source_identity_json': str(manifest)}
    invocation = NativeInvocation.capture(model_id=model, mode=mode, command=['nextflow'],
        requested=params, effective=params, native_parameters=params, entrypoint='fixture.nf')
    references = []
    assets = bundle._input_assets(params, native_invocation=invocation, repo_root=roots['repo'],
        runtime_paths=set(), output_dir=roots['results'] / 'child', references=references)
    assert {p for p, _ in assets} == {first, second, manifest}
    assert {r['source_path'] for r in references} == {str(first), str(second), str(manifest)}
    assert '/offline/parent.cif' in manifest.read_text()


def compilation_tree(output):
    from services.bindcraft2_native import compile_for_native, _canonical
    root = output / 'bindcraft2'
    sources = root / 'sources'
    sources.mkdir(parents=True)
    target = sources / 'target_0.cif'
    target.write_bytes(b'fixture target; not native inference')
    compiled = compile_for_native({'max_trajectories': 2,
        'targets': [{'name': 'on', 'target_path': str(target)}]}, root / 'campaign', lambda request: dict(request))
    compiled['requested_settings'] = {key: value for key, value in compiled['native_request'].items()
                                      if key not in {'project_folder', 'resume'}}
    compiled['request_sha256'] = hashlib.sha256(_canonical(compiled['requested_settings'])).hexdigest()
    receipt = root / 'compilation.json'
    receipt.write_bytes(_canonical(compiled) + b'\n')
    # Resume state is declared by its native owner, not recognized by extensions.
    state = root / 'campaign/arm-1/.redesigned_sequences.txt'
    state.parent.mkdir(parents=True)
    state.write_bytes(b'fixture durable sequence state\n')
    (state.parent / '.campaign_state.json').write_text('{"trajectories":0}')
    return root, receipt, compiled


def test_bc2_compilation_and_state_transport_once_without_changing_science(roots):
    output = roots['results'] / 'job'
    root, receipt, compiled = compilation_tree(output)
    original = receipt.read_bytes()
    params = {'bc2_compilation': str(receipt), 'bc2_campaign_dir': str(root)}
    invocation = NativeInvocation.capture(model_id='bindcraft2', mode='resume', command=['nextflow'],
        requested=params, effective=params, native_parameters=params, entrypoint='workflows/bindcraft2.nf')
    refs = []
    assets = bundle._input_assets(params, native_invocation=invocation, repo_root=roots['repo'],
        runtime_paths=set(), output_dir=output, references=refs)
    assert assets == [(root, 'bindcraft2')]
    assert {r['source_path'] for r in refs} == {str(receipt), str(root / 'sources/target_0.cif')}
    rows = bundle._input_records(root, 'inputs/bindcraft2', native_invocation=invocation, output_dir=output)
    assert {r.relative_path for r in rows} == {
        'inputs/bindcraft2/compilation.json', 'inputs/bindcraft2/sources/target_0.cif',
        'inputs/bindcraft2/campaign/arm-1/.campaign_state.json',
        'inputs/bindcraft2/campaign/arm-1/.redesigned_sequences.txt'}
    assert receipt.read_bytes() == original
    assert json.loads(original)['request_sha256'] == compiled['request_sha256']


@pytest.mark.asyncio
async def test_real_bundle_staging_and_worker_prepare_reconnect(package, tmp_path, monkeypatch):
    roots, _, job, target, _ = package
    root, receipt, compiled = compilation_tree(Path(job.output_dir))
    job.model_id, job.mode = 'bindcraft2', 'campaign'
    params = {'out_dir': job.output_dir, 'bc2_compilation': str(receipt), 'bc2_campaign_dir': str(root)}
    command = ['nextflow', 'run', str(roots['repo'] / 'main.nf')]
    for key, value in params.items():
        command.extend(['--' + key, value])
    # Source/support-only transport projection. Selected model dependency tests
    # separately exercise exact assets; no fixture image is ever executed.
    invocation = cache_only_plan_fixture(replace(NativeInvocation.capture(model_id=job.model_id,
        mode=job.mode, command=command, requested=params, effective=params, native_parameters=params,
        entrypoint='main.nf'), source_identity=SourceIdentity('a' * 40, 'b' * 40)))
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command, native_invocation=invocation)
    uploads, protocols = [], []

    async def run(connection, argv, input_bytes=None, **kwargs):
        if input_bytes is not None:
            protocols.append(input_bytes)
        result = subprocess.run(argv, input=input_bytes, capture_output=True, check=True)
        return SimpleNamespace(stdout=result.stdout.decode(), stderr=result.stderr.decode(), returncode=result.returncode)

    async def transfer(connection, source, destination, **kwargs):
        source, destination = Path(source), Path(destination)
        uploads.append(str(source))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(source, destination)

    for owner in (cache, executor):
        monkeypatch.setattr(owner, 'run_remote', run)
        monkeypatch.setattr(owner, 'rsync_to_remote', transfer)
    connection = SimpleNamespace(remote_root=target.remote_root)
    await executor._stage_bundle(connection, prepared)
    argv = prepared.envelope.command
    worker_receipt = Path(argv[argv.index('--bc2_compilation') + 1])
    worker_output = Path(argv[argv.index('--bc2_campaign_dir') + 1])
    assert worker_receipt.parent == Path(prepared.remote_attempt_dir) / 'bundle/inputs/bindcraft2'
    assert worker_output == Path(prepared.remote_output_alias) / 'bindcraft2'
    assert worker_receipt.read_bytes() == receipt.read_bytes()
    attempt = Path(prepared.remote_attempt_dir)
    assert worker.prepare(attempt)['state'] == 'prepared'
    cold_uploads = len(uploads)
    cold_protocols = len(protocols)
    # Reconnect prepares the same owned attempt without replaying staging.
    assert worker.prepare(attempt)['state'] == 'prepared'
    # A fresh attempt reuses the same asset cache. Keep the existing prohibition
    # against merging staging into an already materialized generation intact.
    warm = bundle.prepare_remote_bundle(job=job, target=target, command=command, native_invocation=invocation)
    await executor._stage_bundle(connection, warm)
    assert worker.prepare(Path(warm.remote_attempt_dir))['state'] == 'prepared'
    # Warm staging sends owned inputs, not the cached source archive again.
    assert not any(name.endswith('.bms-source.tar.gz') for name in uploads[cold_uploads:])
    assert len(protocols) > cold_protocols  # real cache protocol, not a fake response
    assert json.loads(worker_receipt.read_bytes())['request_sha256'] == compiled['request_sha256']
    assert (worker_receipt.parent / 'campaign/arm-1/.redesigned_sequences.txt').is_file()
    # Hand-authored native output fixture: exercise real transport manifest and
    # model-owned publication, never claim native inference was performed.
    from test_bindcraft2_publication import campaign
    from test_bindcraft2_nested_publication import ancillary
    from services.bindcraft2_native_results import read_native_publication
    output = Path(prepared.envelope.output_directory)
    native = output / 'bindcraft2/campaign/arm-1'
    campaign(native)
    extras = ancillary(native)
    nested = native / 'result-manifest.json'
    nested.write_text('{"native_document":true}')
    (output / 'result-manifest.json').write_text('{}')
    manifest = worker.build_result_manifest(attempt, json.loads(worker.envelope_path(attempt).read_text()), 0)
    names = {row['relative_path'] for row in manifest['artifacts']}
    assert 'result-manifest.json' not in names
    assert 'bindcraft2/campaign/arm-1/result-manifest.json' in names
    assert {'bindcraft2/campaign/arm-1/' + name for name in extras} <= names
    returned = tmp_path / 'returned'
    for row in manifest['artifacts']:
        source, destination = output / row['relative_path'], returned / row['relative_path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == row['sha256']
    before = read_native_publication(returned / 'bindcraft2/campaign')
    shutil.rmtree(Path(target.remote_root))
    assert read_native_publication(returned / 'bindcraft2/campaign') == before
    assert (returned / 'bindcraft2/campaign/arm-1/.redesigned_sequences.txt').read_text() == 'CCC\n'



@pytest.mark.parametrize('flags', tuple(__import__('itertools').product((False, True), repeat=4)))
def test_refinement_assets_are_exactly_selected(flags):
    from model_registry import selected_execution_metadata
    repack, anchors, flow, redesign = flags
    params = dict(zip(('maturation_repack_enabled', 'maturation_anchors_enabled',
                       'maturation_flow_enabled', 'maturation_redesign_enabled'), flags))
    metadata = selected_execution_metadata('binder_refinement', 'refine', params, 'workflows/binder_refinement.nf')
    stages = {row.component_key for row in metadata.static_components}
    assets = {row.logical_id for row in metadata.dependencies if row.kind in {'image', 'weights'}}
    expected = set()
    if repack or anchors or redesign:
        expected.add('image:pyrosetta_tools.sif')
    if flow:
        expected.update({'image:ppiflow.sif', 'weights:ppiflow'})
    if redesign:
        expected.add('image:fampnn.sif')
    assert assets == expected
    assert ('IdentifyAnchorResidues' in stages) == (repack or anchors)
    assert ('PrepareBinderRefinementRegions' in stages) == (not (repack or anchors) and (flow or redesign))
    assert ('RunPartialFlow' in stages) == flow
    assert ('RunMaturationFAMPNN' in stages) == redesign
    assert not {'ScorePartialFlowImprovement', 'FilterByMaturation', 'ANARCII'} & stages


@pytest.mark.parametrize('self_consistency', [False, True])
def test_caliby_selects_one_checkpoint_and_optional_af2_only(self_consistency):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('caliby_binder', 'design',
        {'caliby_model_name': 'soluble_caliby_v1', 'caliby_run_self_consistency_eval': self_consistency},
        'workflows/caliby_binder.nf')
    assets = {row.logical_id for row in metadata.dependencies if row.kind in {'image', 'weights'}}
    expected = {'image:caliby.sif', 'weights:caliby/model_params/caliby/soluble_caliby_v1.ckpt'}
    if self_consistency:
        expected.add('weights:caliby/model_params/af2')
    assert assets == expected
    assert [row.component_key for row in metadata.static_components] == ['RunCalibyBinder']


@pytest.mark.parametrize('mode', ['campaign', 'resume', 'rank', 'filter', 'campaign_output', 'archive', 'unarchive', 'score'])
def test_bc2_action_assets_exclude_unselected_predictor_weights(mode):
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('bindcraft2', mode, {}, 'workflows/bindcraft2.nf')
    assets = {row.logical_id for row in metadata.dependencies if row.kind in {'image', 'weights'}}
    expected = {'image:bindcraft2.sif'}
    if mode in {'campaign', 'resume'}:
        expected.add('weights:alphafold/params')
    assert assets == expected


def test_selected_gromacs_closure_does_not_select_openmm_or_binder_engines():
    from model_registry import selected_execution_metadata
    metadata = selected_execution_metadata('molecular_dynamics', 'simulate',
        {'md_engine': 'gromacs', 'md_config': {'engine': 'gromacs', 'replicas': 1}},
        'workflows/experimental/molecular_dynamics/orchestrator.nf')
    assets = {row.logical_id for row in metadata.dependencies if row.kind in {'image', 'weights'}}
    assert assets == {'image:md-preparation-v1.sif', 'image:gromacs-md-2025.3.sif',
                      'image:md-analysis-1.0.0.sif'}
