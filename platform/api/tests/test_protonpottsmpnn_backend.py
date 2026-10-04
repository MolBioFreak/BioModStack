"""Real owning routes/compiler/portable artifact tests; no scientific worker."""
import ast
import os
import copy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from database import Design, Job, JobArtifact
from schemas import JobCreate
from routers import jobs, binder_continuation, models
from scripts.lib import protonpottsmpnn_contract as contract
from services import protonpottsmpnn_design as native
from services import protonpottsmpnn_publication as publication
from services.nextflow import compile_nextflow_invocation, compile_job_nextflow_invocation
from services.remote_execution import bundle
from test_boltzgen_generation_launch import admission, target  # noqa: F401
from test_remote_bundle_path_gaps import roots  # noqa: F401


def request(source, target_id=None):
    return JobCreate(name='native pH request', model_id='protonpottsmpnn', mode='redesign',
        execution_target_id=target_id, params={'target_pdb': str(source), 'binder_chain': 'A',
        'seed': 0, 'initial_sequences': [], 'criteria': [{'combined_lambda': 0.,
        'temperature': 0., 'record_trajectory': False, 'center_types': [], 'center_count': 1}],
        'engine_options': {'etab_source': None, 'etab_hidden': [], 'field_hidden': None},
        'write_fasta': False, 'write_structures': False})


def assert_plan(invocation):
    assert invocation.entrypoint == 'workflows/protonpottsmpnn_design.nf'
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    meta = invocation.execution_plan.metadata
    assert {row.component_key for row in meta.static_components} == {'RunProtonPottsMPNNDesign'}
    assert {row.relative_path for row in meta.dependencies if row.kind == 'image'} == {'protonpottsmpnn.sif'}
    assert not any(row.kind == 'weights' for row in meta.dependencies)
    assert not {'af2_models', 'rfd_models', 'boltz_models', 'msa_local_db', 'msa_cache_dir'} & invocation.native_parameters.keys()
    assert json.loads(meta.result_contract_json) == bundle.resolve_job_result_contract(SimpleNamespace(model_id='protonpottsmpnn', mode='redesign'))


def test_closed_full_inventory_and_exact_editable_example_defaults():
    schema = contract.parameter_schema()
    assert schema == json.loads((Path(__file__).resolve().parents[3] / 'schemas/protonpottsmpnn_parameters.v1.json').read_text())
    fields = schema['properties']['criteria']['items']['properties']
    assert set(fields) == set(contract.PHDesignCriteria.__dataclass_fields__)
    assert len(fields) == 42
    defaults = contract.normalize_params({'target_pdb': 'source.pdb', 'binder_chain': 'A'})
    criterion = defaults['criteria'][0]
    assert (criterion['method'], criterion['combined_lambda'], criterion['temperature'], criterion['block_size']) == ('block_descent', .3, .05, 3)
    assert criterion['dep_map']['HIS-P'] == ['HIS-S']
    assert fields['dep_map']['x-native-default']['HIS-P'] == ['HID', 'HIE']
    assert defaults['engine_options'] == {'extended_vocab': 'v6', 'field_source': 'self_edge', 'etab_source': None, 'etab_hidden': None, 'field_hidden': None}


@pytest.mark.parametrize('changes', [ {'unknown': 1}, {'criteria': [{'unknown': 1}]},
    {'criteria': [{'method': 'gibbs', 'backend': 'mpnn'}]}, {'engine_options': {'extended_vocab': 'v4'}},
    {'criteria': [{'temperature': True}]}, {'criteria': [{'self_weight': -1.}]},
    {'criteria': [{'selective_source': 'decoder'}]}, {'criteria': []}])
def test_unknown_and_native_invalid_combinations_reject_at_normalizer(changes):
    with pytest.raises(HTTPException) as exc:
        jobs.normalize_job_request(JobCreate(name='invalid', model_id='protonpottsmpnn', mode='redesign',
            params={'target_pdb': 'source.pdb', 'binder_chain': 'A', **changes}))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_discovery_returns_canonical_nested_schema():
    assert models._native_parameter_schema('protonpottsmpnn', 'redesign') == contract.parameter_schema()


def test_normalizer_clone_and_real_preview_preserve_falsey_fields(tmp_path):
    source = tmp_path / 'source.pdb'
    source.write_bytes(b'REMARK inert\n')
    normalized = jobs.normalize_job_request(request(source))
    replay = jobs.normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert normalized == replay
    preview = compile_nextflow_invocation(replay.model_id, replay.mode, replay.params, str(tmp_path/'absent'), _preview_only=True)
    assert_plan(preview)
    document = json.loads(preview.generated_inputs[0].payload)
    assert document['options']['criteria'][0]['combined_lambda'] == 0.
    assert document['options']['criteria'][0]['temperature'] == 0.
    assert document['options']['initial_sequences'] == []
    assert document['options']['engine_options']['etab_hidden'] == []
    assert document['options']['write_structures'] is False
    assert not (tmp_path/'absent').exists()


@pytest.mark.asyncio
async def test_real_local_job_insertion_clone_replays_after_original_removed(admission, target):
    first = await jobs._create_job(request(target), BackgroundTasks(), admission)
    job = await admission.get(Job, first.id)
    assert job.vram_estimate_mb == 0
    before = native.read_prepared_request(job.mode, job.params)
    source = native.prepared_source_path(job)
    assert source.read_bytes() == target.read_bytes()
    target.unlink()
    invocation = compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert_plan(invocation)
    assert '-profile' in invocation.command
    assert invocation.command[invocation.command.index('-profile')+1].startswith('protonpottsmpnn_design,')
    replay = request('/not-opened.pdb')
    replay.name = 'native clone'
    replay.params = copy.deepcopy(job.params)
    second = await jobs._create_job(replay, BackgroundTasks(), admission)
    clone = await admission.get(Job, second.id)
    assert native.read_prepared_request(clone.mode, clone.params) == before
    assert native.prepared_source_path(clone) != source
    assert native.prepared_source_path(clone).read_bytes() == source.read_bytes()


@pytest.mark.asyncio
async def test_selected_non_antibody_route_actual_child_insertion_explicit_local(admission, target, monkeypatch):
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: target.parent)
    parent = Job(id='generic-source', name='generic source', model_id='proteinmpnn', mode='design', status='completed',
        execution_target_id='archived-worker', params={}, provenance={})
    design = Design(id='selected-design', job_id=parent.id, name='retained candidate',
        pdb_path=str(target), provenance={})
    admission.add_all([parent, design])
    await admission.commit()
    result = await binder_continuation.launch_selected(binder_continuation.SelectedOperationRequest(
        source_job_id=parent.id, design_ids=[design.id], operation='protonpottsmpnn',
        params={'binder_chain': 'A'}, execution_target_id=None), BackgroundTasks(), admission, admission)
    assert len(result['launched_jobs']) == 1
    child = await admission.get(Job, result['launched_jobs'][0].id)
    assert (child.model_id, child.mode, child.execution_target_id) == ('protonpottsmpnn', 'redesign', None)
    assert child.params['iteration_source_design_ids'] == [design.id]
    assert child.params['selection_source_job_id'] == parent.id
    assert child.params['lineage_root_job_id'] == parent.id
    source = native.prepared_source_path(child)
    target.unlink()
    assert source.is_file()
    assert_plan(compile_job_nextflow_invocation(child, child.params, child.output_dir))


def test_real_portable_bundle_uses_owned_source_and_offline_bindings(roots, tmp_path, monkeypatch):
    import services.nextflow as nextflow
    monkeypatch.setattr(nextflow, 'get_data_root', lambda: roots['data'])
    source = roots['inputs']/'source.pdb'
    source.write_bytes(b'REMARK original source bytes\n')
    normalized = jobs.normalize_job_request(request(source))
    normalized.params.update(native.prepare_for_job('redesign', normalized.params, roots['inputs']/'prepared', allowed_roots=roots.values()))
    source.unlink()
    invocation = compile_nextflow_invocation('protonpottsmpnn', 'redesign', normalized.params, str(roots['results']/'job'))
    refs = []
    assets = bundle._input_assets(normalized.params, native_invocation=invocation, repo_root=roots['repo'],
        runtime_paths=set(), output_dir=roots['results']/'job', references=refs)
    assert {tuple(ref['selector']) for ref in refs} == {(native.REQUEST_FIELD,), (native.INPUT_FIELD,)}
    transfers, records = [], []
    worker = tmp_path/'worker'
    for path, relative in assets:
        prefix = 'inputs/'+relative
        records.extend(bundle._input_records(path, prefix, native_invocation=invocation, output_dir=roots['results']/'job'))
        remote = worker/'bundle'/prefix
        remote.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, remote) if path.is_dir() else shutil.copyfile(path, remote)
        transfers.append(bundle.TransferPlan(path, str(remote)))
    staging = tmp_path/'bindings'
    staging.mkdir()
    binding, _ = bundle._write_portable_bindings(staging_root=staging, remote_attempt=str(worker), references=refs,
        input_transfers=transfers, input_records=records, remote_runtime=str(worker/'runtime'), remote_results=str(worker/'results'))
    monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', str(binding.source))
    from scripts.lib.portable_inputs import resolve_input_path
    owned_source = Path(normalized.params[native.INPUT_FIELD])
    expected = native.read_prepared_request('redesign', normalized.params)
    shutil.rmtree(roots['inputs']/'prepared')
    assert resolve_input_path(owned_source).read_bytes() == b'REMARK original source bytes\n'
    assert json.loads(resolve_input_path(normalized.params[native.REQUEST_FIELD]).read_bytes()) == expected


def test_pinned_native_full_inventory_and_inherited_constructor_coverage():
    root = os.environ.get('BMS_PROTON_NATIVE_SOURCE')
    if not root:
        pytest.skip('Set BMS_PROTON_NATIVE_SOURCE to the pinned upstream checkout')
    native_root = Path(root)/'foundry/models/mpnn/src/mpnn/inference_engines'
    original = ast.parse((native_root/'potts_mpnn_ph.py').read_text())
    criteria = next(n for n in original.body if isinstance(n, ast.ClassDef) and n.name == 'PHDesignCriteria')
    expected = {n.target.id for n in criteria.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    schema = contract.parameter_schema()
    assert set(schema['properties']['criteria']['items']['properties']) == expected
    base = ast.parse((native_root/'potts_mpnn.py').read_text())
    engine = next(n for n in base.body if isinstance(n, ast.ClassDef) and n.name == 'MPNNInferenceEngine')
    init = next(n for n in engine.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    runtime_owned = {'checkpoint_path', 'out_directory', 'device'}
    output_controls = {'write_fasta', 'write_structures'}
    assert {arg.arg for arg in init.args.kwonlyargs} == set(schema['properties']['engine_options']['properties']) | runtime_owned | output_controls
    assert output_controls <= set(schema['properties'])
    local = ast.parse(Path(contract.__file__).read_text())
    mirror = next(n for n in local.body if isinstance(n, ast.ClassDef) and n.name == 'PHDesignCriteria')
    assert [ast.dump(n) for n in criteria.body if isinstance(n, ast.AnnAssign)] == [ast.dump(n) for n in mirror.body if isinstance(n, ast.AnnAssign)]
    original_validation = next(n for n in criteria.body if isinstance(n, ast.FunctionDef) and n.name == '__post_init__')
    mirror_validation = next(n for n in mirror.body if isinstance(n, ast.FunctionDef) and n.name == '__post_init__')
    assert ast.dump(original_validation) == ast.dump(mirror_validation)


@pytest.mark.asyncio
async def test_native_publication_real_artifact_rows_exact_endpoint_shape_no_fake_design(admission, target):
    response = await jobs._create_job(request(target), BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    folder = Path(job.output_dir)/native.DIRECTORY
    folder.mkdir(parents=True)
    document = {'contract': contract.CONTRACT, 'request': native.read_prepared_request(job.mode, job.params),
        'designs': [{'design_id': 'source:criteria:sample', 'criteria_index': 0, 'native_design_id': 'native-id',
                    'native': {'canonical_sequence': 'AAA', 'extended_tokens': ['ALA']*3, 'final_potts_energy': -2.5,
                        'selective_energy': None, 'energy_trajectory': []}}], 'seed_energies': [],
        'runtime': {'upstream_revision': '09682abfa7d20e0abcdeea0490b7a4b1c190aee3'}, 'artifacts': ['manifest.json']}
    document['source'] = document['request']['source']
    (folder/'manifest.json').write_text(json.dumps(document))
    assert await publication.publish_native_results(job, Path(job.output_dir), admission) == 0
    await admission.commit()
    target.unlink()
    from routers.protonpottsmpnn import get_results
    assert await get_results(job.id, admission) == document
    assert await native.read_result(admission, job.id) == document
    assert list(await admission.scalars(select(Design).where(Design.job_id == job.id))) == []
    artifacts = list(await admission.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id)))
    assert len(artifacts) == 1
    assert artifacts[0].sha256 == hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest()
    (folder/'manifest.json').write_text('{}')
    with pytest.raises(HTTPException) as exc:
        await get_results(job.id, admission)
    assert exc.value.status_code == 409
