"""Real compiler/selected admission/portable adapters; no scientific execution.

Archive/source and support-interpreter seams reuse the offline bundle fixture.
Selected image/weight bytes are explicit transport doubles, not model runtimes.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
from unittest.mock import AsyncMock

import pytest
from component_runtime import UnresolvedField
from services import nextflow
from services.remote_execution import bundle
from services.model_msa_handoff import prepare_launch_msa
from biomodstack_msa_handoff import digest
from biomodstack_boltz_msa import hydrate_prepared_boltz_task, hydrate_prepared_boltz_components
from tests.test_msa_bundle_integration import offline_bundle

RUNTIME_ASSETS = bundle._runtime_assets


@pytest.fixture
def placement(offline_bundle, monkeypatch):
    roots, snapshot, target, _ = offline_bundle
    from database import Job
    job = Job(**vars(snapshot))
    for key, env in [('data', 'BMS_DATA'), ('inputs', 'BMS_INPUTS'), ('results', 'BMS_RESULTS_DIR'),
                     ('weights', 'BMS_WEIGHTS'), ('containers', 'BMS_CONTAINER_DIR')]:
        monkeypatch.setenv(env, str(roots[key]))
    monkeypatch.setenv('BMS_COLABFOLD_DB', str(roots['data'] / 'absent-local-msa'))
    monkeypatch.setenv('BMS_MSA_CACHE', str(roots['data'] / 'cache'))
    # Exercise real selected runtime resolution; only support interpreter provisioning is out of scope.
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *args, **kwargs:
        RUNTIME_ASSETS(*args, **{**kwargs, 'include_support': False}))
    job.params = dict(sequence='ACDE', sequence_name='ordered', run_frustrampnn=False,
                      boltz_use_msa=False)
    return roots, job, target


def admit_current_job(job, model, mode):
    """Use the typed request, policy and canonical normalization/admission producer."""
    from copy import deepcopy
    from schemas import JobCreate
    from routers.jobs import normalize_job_request
    from services.msa_policy import apply_msa_policy
    from services.core_protein_scientific_contract import admitted_payload, admission_revision
    requested = deepcopy(job.params)
    typed = JobCreate(name='input-rectification', model_id=model, mode=mode, params=requested)
    typed.params = apply_msa_policy(model, typed.params)
    normalized = normalize_job_request(typed)
    job.params, job.provenance = admitted_payload(normalized.params, job.provenance,
                                                 admission_revision(model, mode))
    job.provenance['core_protein_requested_params'] = requested
    job.params['remote_result_policy'] = typed.execution_policy.remote_result_policy
    job.retry_count = 0


def compile_installed(placement, model='boltz2', mode='predict'):
    roots, job, _ = placement
    job.model_id, job.mode = model, mode
    invocation = nextflow.compile_job_nextflow_invocation(job, dict(job.params), job.output_dir)
    invocation.materialize_inputs(Path(job.output_dir))
    for dependency in invocation.execution_plan.dependencies:
        if dependency.kind not in {'weights', 'image'}:
            continue
        root = roots['weights' if dependency.kind == 'weights' else 'containers']
        path = root / dependency.relative_path
        if dependency.kind == 'weights':
            path.mkdir(parents=True, exist_ok=True)
            path = path / 'transport-fixture.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'explicit offline runtime transport double')
    return invocation


def pack(placement, invocation):
    _, job, target = placement
    return bundle.prepare_remote_bundle(job=job, target=target,
        command=list(invocation.command), native_invocation=invocation)


@pytest.mark.parametrize('model', ['esmfold2', 'boltz2'])
@pytest.mark.parametrize('unused_present', [False, True])
@pytest.mark.parametrize('current_contract', [False, True])
def test_selected_only_real_compiler_bundle(placement, model, unused_present, current_contract):
    roots, job, _ = placement
    if current_contract:
        admit_current_job(job, model, 'predict')
    invocation = compile_installed(placement, model)
    original = (invocation.requested_json, invocation.effective_json)
    if unused_present:
        # An unused default inside input storage must not become biological input.
        unused = Path(invocation.native_parameters['msa_local_db'])
        unused.mkdir(parents=True)
        (unused / 'never-transfer').write_text('unselected database')
    result = pack(placement, invocation)
    assert (invocation.requested_json, invocation.effective_json) == original
    assert not {'--rfd_models', '--af2_models', '--alphafold_params', '--msa_local_db'} & set(result.envelope.command)
    assert ('--boltz_models' in result.envelope.command) == (model == 'boltz2')
    assert not any('never-transfer' in r.relative_path for r in result.envelope.files)
    assert not any('weights' in t.remote_destination for t in result.input_transfers)


def prepare_boltz(placement, mode='predict', *, current_contract=False):
    roots, job, _ = placement
    alignment = roots['inputs'] / 'supplied.csv'
    alignment.write_text('key,sequence\n-1,ACDE\n7,ACDE\n')
    job.params.update(boltz_use_msa=True, msa_provider='colabfold_api', boltz_sampling_steps=77)
    if mode == 'complex':
        job.params.pop('sequence')
        job.params['complex_components'] = [
            {'type': 'protein', 'id': ['B', 'A'], 'sequence': 'ACDE', 'msa_path': str(alignment)},
            {'type': 'peptide', 'id': 'C', 'sequence': 'FGHI'}]
    else:
        job.params.update(msa_path=str(alignment), num_parallel_jobs=2)
    if current_contract:
        job.params.setdefault('sequence', 'ACDE')
        admit_current_job(job, 'boltz2', mode)
    invocation = compile_installed(placement, mode=mode)
    prepared = prepare_launch_msa('boltz2', dict(job.params), Path(job.output_dir) / 'prepared-msa',
                                  native_invocation=invocation)
    return invocation, prepared, alignment


@pytest.mark.parametrize('mode', ['predict', 'complex'])
@pytest.mark.parametrize('current_contract', [False, True])
def test_verified_boltz_admitted_and_relocated(placement, mode, current_contract):
    original, prepared, alignment = prepare_boltz(placement, mode, current_contract=current_contract)
    documents = {p: p.read_bytes() for p in Path(placement[1].output_dir).rglob('*.json')}
    assert not original.execution_plan.complete
    invocation = asyncio.run(nextflow._compile_launch_nextflow_invocation(
        AsyncMock(), placement[1], prepared, placement[1].output_dir, prepared_invocation=original))
    assert invocation.execution_plan.complete
    assert (invocation.requested_json, invocation.effective_json) == (original.requested_json, original.effective_json)
    assert all(p.read_bytes() == raw for p, raw in documents.items())
    assert nextflow._bind_protenix_msa_transport(invocation, prepared) == invocation
    result = pack(placement, invocation)
    for transfer in result.input_transfers:
        dest = Path(transfer.remote_destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if transfer.source.is_dir():
            shutil.copytree(transfer.source, dest)
        else:
            shutil.copyfile(transfer.source, dest)
    argv = result.envelope.command
    worker_root = Path(argv[argv.index('--boltz_prepared_msa_dir') + 1])
    sha = argv[argv.index('--boltz_prepared_msa_sha256') + 1]
    manifest = json.loads((worker_root / 'msa-inputs.json').read_text())
    expected = alignment.read_bytes()
    shutil.rmtree(placement[0]['inputs'])
    shutil.rmtree(placement[1].output_dir)
    for task in manifest['tasks']:
        if mode == 'complex':
            hydrated = hydrate_prepared_boltz_components({'components': task['native_task']['components']},
                worker_root, sha, task_name=task['name'])
            assert hydrated['components'][0]['id'] == ['B', 'A']
            assert Path(hydrated['components'][0]['msa_path']).read_bytes() == expected
        else:
            hydrated = hydrate_prepared_boltz_task({'sequences': [{'protein': {'id': 'A', 'sequence': 'ACDE'}}]},
                worker_root, sha, task_name=task['name'])
            assert Path(hydrated['sequences'][0]['protein']['msa']).read_bytes() == expected


@pytest.mark.parametrize('mutation', ['digest', 'tasks', 'chains', 'order', 'settings', 'empty', 'artifact'])
def test_boltz_incomplete_or_corrupt_handoff_rejected(placement, mutation):
    original, prepared, _ = prepare_boltz(placement)
    root = Path(prepared['boltz_prepared_msa_dir'])
    path = root / 'msa-inputs.json'
    manifest = json.loads(path.read_text())
    if mutation == 'digest':
        prepared['boltz_prepared_msa_sha256'] = '0' * 64
    elif mutation == 'tasks':
        manifest['tasks'].pop()
    elif mutation == 'chains':
        manifest['tasks'][0]['chains'].clear()
    elif mutation == 'order':
        manifest['tasks'].reverse()
    elif mutation == 'settings':
        manifest['settings']['boltz_sampling_steps'] = 78
    elif mutation == 'empty':
        manifest['tasks'][0]['chains'][0]['alignment'] = {'mode': 'empty'}
    else:
        (root / manifest['tasks'][0]['chains'][0]['alignment']['path']).write_text('key,sequence\n-1,ACDE\n8,ACDE\n')
    if mutation not in {'digest', 'artifact'}:
        path.write_text(json.dumps(manifest))
        prepared['boltz_prepared_msa_sha256'] = digest(path.read_bytes())
    with pytest.raises(ValueError):
        nextflow._bind_protenix_msa_transport(original, prepared)


def test_cached_boltz_roster_discharges_generate_stage(placement, monkeypatch):
    from services import msa_preparation
    roots, job, _ = placement
    job.params.update(boltz_use_msa=True, msa_provider='colabfold_api', num_parallel_jobs=2)
    calls = []
    def cached(*, sequences, params):
        # Explicit cache/provider-boundary double; no provider request is made.
        calls.append((sequences, params['msa_provider']))
        path = roots['data'] / 'cached.a3m'
        data = f'>query\n{sequences[0]}\n>hit\n{sequences[0]}\n'.encode()
        path.write_bytes(data)
        return dict(provider=params['msa_provider'], request_digest='fixture-request', cache_hit=True,
                    provenance={'offline_boundary_double': True},
                    artifacts=[dict(chain_index=0, role='unpaired', path=str(path), sha256=digest(data))])
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', cached)
    original = compile_installed(placement)
    assert 'GenerateLocalMSA' in {r.component_or_dependency_id for r in original.execution_plan.blockers}
    prepared = prepare_launch_msa('boltz2', dict(job.params), Path(job.output_dir) / 'prepared-msa',
                                  native_invocation=original)
    invocation = nextflow._bind_protenix_msa_transport(original, prepared)
    assert invocation.execution_plan.complete
    assert calls == [(['ACDE'], 'colabfold_api'), (['ACDE'], 'colabfold_api')]
    pack(placement, invocation)
    assert (invocation.requested_json, invocation.effective_json) == (original.requested_json, original.effective_json)


@pytest.mark.parametrize('mode', ['predict', 'complex'])
def test_normalized_job_only_prepared_transport_differs(placement, mode):
    from services.boltz_launch_authority import command_params, build_authority
    original, prepared, _ = prepare_boltz(placement, mode, current_contract=True)
    job = placement[1]
    bound = nextflow._bind_protenix_msa_transport(original, prepared)
    persisted = nextflow.build_job_nextflow_command(job, job.params, job.output_dir)
    actual, expected = [command_params(cmd) for cmd in (bound.command, persisted)]
    keys = {k for k in actual.keys() | expected.keys()
            if k.startswith('boltz_') or k in ('pred_method', 'num_parallel_jobs')}
    assert {k for k in keys if actual.get(k) != expected.get(k)} == {
        'boltz_prepared_msa_dir', 'boltz_prepared_msa_sha256'}
    assert build_authority(job, list(bound.command)) == build_authority(job, persisted)


def test_prepared_transport_does_not_hide_scientific_change(placement):
    original, prepared, _ = prepare_boltz(placement, current_contract=True)
    placement[1].params['boltz_sampling_steps'] = 78
    with pytest.raises(ValueError, match='Boltz compiled settings differ from persisted request'):
        asyncio.run(nextflow._compile_launch_nextflow_invocation(
            AsyncMock(), placement[1], prepared, placement[1].output_dir, prepared_invocation=original))


def test_binding_preserves_unrelated_blocker(placement):
    original, prepared, _ = prepare_boltz(placement)
    blocker = UnresolvedField('boltz2:msa', 'different_authority', 'test-negative', 'not discharged')
    plan = original.execution_plan
    original = replace(original, execution_plan=replace(plan,
        metadata=replace(plan.metadata, blockers=(*plan.metadata.blockers, blocker))))
    result = nextflow._bind_protenix_msa_transport(original, prepared)
    assert result.execution_plan.blockers == (blocker,)
    assert not result.execution_plan.complete


@pytest.mark.parametrize('missing', ['selected_runtime', 'native_input'])
def test_real_missing_selected_assets_still_rejected(placement, missing):
    invocation, _, alignment = prepare_boltz(placement)
    if missing == 'selected_runtime':
        shutil.rmtree(placement[0]['weights'] / 'boltz')
        with pytest.raises(bundle.RemoteBundleError, match='Required runtime asset'):
            RUNTIME_ASSETS('boltz2', 'predict', invocation.native_parameters, native_invocation=invocation)
    else:
        alignment.unlink()
        with pytest.raises(bundle.RemoteBundleError, match='Declared input is unavailable'):
            bundle._input_assets({'msa_path': str(alignment)}, native_invocation=invocation,
                repo_root=Path(__file__).resolve().parents[3], runtime_paths=set(),
                output_dir=Path(placement[1].output_dir))
