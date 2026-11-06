"""Typed child preparation -> native terminal receipt; inert producer files only."""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from database import Design, Job
from experiment_models import ExperimentRunAttempt, ExperimentExternalEntityReceipt
from schemas import JobCreate
from services.global_experiments.launch_contexts import prepare_child_launch_contexts
from services.global_experiments.receipts import verify_and_link_terminal_outputs
from test_project_workflow_setups import setup_store  # noqa: F401
from test_native_protein_launch_authority_regression import native_core_store, policy_cache  # noqa: F401
from test_project_normalized_child_requests import destination


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', [('proteinmpnn', 'design'), ('fampnn', 'design'),
    ('protonpottsmpnn', 'redesign'), ('caliby_experimental', 'ensemble_design'), ('ligandmpnn', 'ligand_aware')])
async def test_designer_child_plan_terminal_native_results_without_design_rows(
        native_core_store, setup_store, tmp_path, monkeypatch, model, mode):
    from services import job_result_roots
    monkeypatch.setattr(job_result_roots, 'get_results_dir', lambda: tmp_path)
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'project-artifacts'))
    source = tmp_path / 'source.pdb'
    source.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \nEND\n')
    output = tmp_path / 'output'
    output.mkdir()
    if model in {'caliby_experimental', 'ligandmpnn'}:
        from tests.test_sequence_native_publication import emitted_result
        output, _, _, _, params = emitted_result(tmp_path, monkeypatch, model, mode, empty=False)
    elif model == 'protonpottsmpnn':
        from test_protonpottsmpnn_backend import request
        from services import protonpottsmpnn_design as native
        params = request(source).params
        params.update(native.prepare_for_job(mode, params, tmp_path / 'prepared', allowed_roots=[tmp_path]))
    else:
        params = {'input_pdb': str(source), 'design_chain': 'A'}
    request = JobCreate(name='Inert native result', model_id=model, mode=mode, params=params)
    async with setup_store() as projects, native_core_store() as core:
        dest = await destination(projects)
        prepared = await prepare_child_launch_contexts(projects,
            destination_launch_context_id=dest['launch_context_id'], job_requests=[request],
            idempotency_key='native-child', core_session=core)
        child = prepared['children'][0]
        attempt = await projects.get(ExperimentRunAttempt, child['run_attempt_id'])
        job = Job(id=attempt.scheduler_job_id, name=request.name, model_id=model, mode=mode,
                  params=child['job_request']['params'], status='completed', output_dir=str(output), provenance={})
        core.add(job)
        await core.flush()
        if model in {'proteinmpnn', 'fampnn'}:
            from scripts.sequence_design_results import build_result_index, load_result_index, RESULT_PATH
            from services.binder_retained_publication import publish_sequence_index
            raw = output / 'pdb_files'; raw.mkdir()
            filtered = output / 'collected/mpnn_filtered'; filtered.mkdir(parents=True)
            (raw / 'emitted.pdb').write_bytes(source.read_bytes())
            (raw / 'emitted.json').write_text(json.dumps({'design': 'emitted', 'sequence': 'A', 'score': 0.5}))
            settings = tmp_path / 'settings.json'
            settings.write_text(json.dumps({'sequence_design_engine': model, 'sequence_design_mode': mode}))
            index = build_result_index(engine=model, mode=mode, source=source, settings=settings,
                                       unfiltered_dir=raw, filtered_dir=filtered)
            (output / 'results').mkdir()
            (output / RESULT_PATH).write_text(json.dumps(index))
            await publish_sequence_index(core, job, output, load_result_index(output, model, mode))
        elif model == 'protonpottsmpnn':
            from services import protonpottsmpnn_design as native, protonpottsmpnn_publication as publication
            from scripts.lib.protonpottsmpnn_contract import CONTRACT
            folder = output / native.DIRECTORY; folder.mkdir()
            document = {'contract': CONTRACT, 'request': native.read_prepared_request(mode, job.params),
                'designs': [{'design_id': 'source:criteria:sample', 'criteria_index': 0, 'native_design_id': 'native-id',
                            'native': {'canonical_sequence': 'AAA', 'extended_tokens': ['ALA'] * 3,
                                       'final_potts_energy': -2.5, 'selective_energy': None, 'energy_trajectory': []}}],
                'seed_energies': [], 'runtime': {'upstream_revision': '09682abfa7d20e0abcdeea0490b7a4b1c190aee3'},
                'artifacts': ['manifest.json']}
            document['source'] = document['request']['source']
            (folder / 'manifest.json').write_text(json.dumps(document))
            await publication.publish_native_results(job, output, core)
        else:
            if model == 'caliby_experimental':
                from services.caliby_native_publication import publish_native_results
            else:
                from services.ligandmpnn_design_publication import publish_native_results
            await publish_native_results(job, output, core)
        await core.commit()
        await projects.commit()
    await setup_store.kw['bind'].dispose()
    await native_core_store.kw['bind'].dispose()
    async with setup_store() as projects, native_core_store() as core:
        receipt = await verify_and_link_terminal_outputs(projects, core, attempt_id=child['run_attempt_id'])
        assert receipt['adapter_id'] == f'bms.native-binder.{model}.adapter.v1'
        assert receipt['output_count'] == 1
        assert list(await core.scalars(select(Design))) == []
        await projects.commit()
    async with setup_store() as projects:
        row = await projects.scalar(select(ExperimentExternalEntityReceipt))
        ack = json.loads(row.acknowledgement_json)
        assert ack['metadata']['artifact_authority'] == 'verified_native_publication'
        assert ack['metadata']['retained_artifacts']
