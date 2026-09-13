"""Actual reviewed seed-builder -> completion hook -> canonical child admission.

Only scientific CDR numbering/variant output and frozen source are doubles;
real compiler, selection materializer, SQLite connections and Job API are used.
"""
from copy import deepcopy
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

import pytest
from sqlalchemy import select
from database import Job, Design
from routers import jobs
from services import nextflow
from services.declared_job_expansion import KEY
from tests.test_remote_rectify_admission import admission


async def seed_request(admission, monkeypatch, target_kind=None):
    client, factory = admission
    from paths import get_inputs_dir
    inputs = get_inputs_dir()
    inputs.mkdir(parents=True, exist_ok=True)
    pdb = inputs / 'seed-source.pdb'
    pdb.write_bytes((Path(__file__).parent / 'fixtures/md/1AKI.pdb').read_bytes())
    target = inputs / 'target-reference.pdb'
    target.write_bytes(pdb.read_bytes())
    monkeypatch.setattr(jobs, '_resolve_design_structure_path', lambda value: Path(value))
    # Scientific numbering double: no ANARCI/model run is permitted here.
    monkeypatch.setattr(jobs, '_resolve_loop_region_map', lambda _: {'H1': (1, 2)})
    monkeypatch.setattr(jobs, '_resolve_loop_target_chain', lambda *args: 'A')
    monkeypatch.setattr(jobs, '_detect_loop_sequence_regions', lambda *args: {'H1': (1, 2)})
    async with factory() as session:
        root = Job(id='source', name='source', model_id='boltzgen', mode='antibody',
            status='completed', execution_target_id='vast:one', params={
                'workflow_type': 'antibody', 'boltz_use_msa': False,
                'seq_design_fampnn': False, 'seq_design_antifold': False,
                'seq_design_proteinmpnn': False, 'run_structure_validation': False,
                'run_frustrampnn': False, 'run_maturation': False},
            execution_source_revision='1' * 40, execution_source_tree='2' * 40,
            output_dir=str(inputs), stage_family='boltzgen', stage_mode='antibody')
        design = Design(id='source-design', name='source', job_id=root.id, pdb_path=str(pdb),
                        source_stage='post_boltzgen')
        if target_kind == 'root':
            root.params = {**root.params, 'target_pdb': str(target)}
        session.add_all([root, design])
        await session.commit()
        request, count, _ = jobs._build_cdr_indel_seeded_refinement_job(
            root, root, [design], jobs.AntibodyCdrIndelConfig(loop_ids=['H1'],
                predictor='boltz2', msa_provider='colabfold_api', variants_per_design=2),
            'seeded', {'target_pdb': str(target)} if target_kind == 'override' else {})
    return request, pdb


async def admit_seed(admission, monkeypatch, target_kind=None):
    client, factory = admission
    request, pdb = await seed_request(admission, monkeypatch, target_kind)
    payload = request.model_dump(mode='json')
    preview = await client.post('/jobs/execution-plan/preview', json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()['admissible'], preview.text
    assert preview.json()['declared_expansions'][0]['max_children'] == 1
    if target_kind:
        assert preview.json()['declared_expansions'][0]['child_static_inputs']
    response = await client.post('/jobs', json={**payload, 'execution_plan_approval': preview.json()['approval_digest']})
    assert response.status_code == 201, response.text
    async with factory() as session:
        rows = list((await session.scalars(select(Job).where(Job.batch_id == response.json()['batch_id'])
                    .order_by(Job.created_at, Job.id))).all())
        owner = next(row for row in rows if KEY in (row.provenance or {}))
        return owner.id, [row.id for row in rows], pdb


async def complete(factory, members, pdb, *, omit_last=False):
    async with factory() as session:
        for index, key in enumerate(members):
            row = await session.get(Job, key)
            row.status = 'completed'
            if omit_last and index == len(members) - 1:
                continue
            session.add(Design(id=f'rebuilt-{index}', name=f'rebuilt-{index}', job_id=key,
                               pdb_path=str(pdb), source_stage='post_structure_validation'))
        await session.commit()


async def trigger(factory, key):
    async with factory() as session:
        await nextflow.maybe_trigger_mutation_seed_refinement(await session.get(Job, key), session)


def child_id(owner):
    return str(uuid5(NAMESPACE_URL, f'bms:mutation-seed-refinement:{owner}'))


@pytest.mark.asyncio
async def test_reviewed_parent_to_automatic_child_and_recovery(admission, monkeypatch):
    client, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    await trigger(factory, members[-1])
    async with factory() as session:
        child = await session.get(Job, child_id(owner))
        assert child is not None
        assert child.execution_source_revision == 'a' * 40  # not historical root source
        receipt = child.provenance['execution_plan_approval']['expansion_approval']
        assert receipt['parent_job_id'] == owner
        assert receipt['derived_input_identities']
        assert child.params['manual_mutation_mode'] == 'seeded_refinement'
        selection = Path(child.params['iteration_selection_dir'])
        before = {p.name: p.read_bytes() for p in selection.iterdir()}
        assert all(not p.is_symlink() for p in selection.iterdir())
        # Exercise the actual bundle input-closure/sealing consumer, not just
        # preview hashes. No runtime image, provider, worker or inference call.
        from services.remote_execution.bundle import _input_assets, _input_records, compile_remote_dependencies
        invocation = nextflow.compile_job_nextflow_invocation(child, child.params, child.output_dir)
        invocation.materialize_inputs(Path(child.output_dir))
        _, effective = compile_remote_dependencies(child.model_id, child.mode, list(invocation.command),
                                                    native_invocation=invocation)
        assets = _input_assets(effective, native_invocation=invocation,
            repo_root=Path(__file__).resolve().parents[3], runtime_paths=set(), output_dir=Path(child.output_dir))
        assert selection in {path for path, _ in assets}
        records = _input_records(selection, 'inputs/seeds', native_invocation=invocation, output_dir=Path(child.output_dir))
        assert len(records) == len(before)
        assert all(row.link_target is None for row in records)
    await trigger(factory, members[0])  # fresh session/recovery, not a second child
    async with factory() as session:
        assert len(list((await session.scalars(select(Job))).all())) == len(members) + 2
    assert {p.name: p.read_bytes() for p in selection.iterdir()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['root_settings', 'root_source', 'root_target', 'variant_settings',
                                     'variant_source', 'variant_target', 'trigger', 'forged_authority'])
async def test_expansion_invalidation_before_preparation(admission, monkeypatch, change):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    async with factory() as session:
        row = await session.get(Job, 'source' if change.startswith('root_') else owner)
        if change.endswith('settings'):
            row.params = {**row.params, 'boltz_sampling_steps': 37}
        elif change.endswith('source'):
            row.execution_source_revision = 'f' * 40
        elif change.endswith('target'):
            row.execution_target_id = 'vast:two'
        elif change == 'trigger':
            row.params = {**row.params, 'mutation_seed_refinement_trigger': {
                **row.params['mutation_seed_refinement_trigger'], 'param_overrides': {'seq_design_fampnn': True}}}
        else:
            row.provenance = {key: value for key, value in row.provenance.items() if key != KEY}
        await session.commit()
    await trigger(factory, members[-1])
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is None
    from paths import get_inputs_dir
    assert not (get_inputs_dir() / 'design_selections/antibody' / child_id(owner)).exists()


@pytest.mark.asyncio
async def test_parent_edit_invalidates_review_before_queue(admission, monkeypatch):
    client, factory = admission
    request, _ = await seed_request(admission, monkeypatch)
    payload = request.model_dump(mode='json')
    preview = await client.post('/jobs/execution-plan/preview', json=payload)
    assert preview.status_code == 200, preview.text
    async with factory() as session:
        root = await session.get(Job, 'source')
        root.params = {**root.params, 'boltz_sampling_steps': 13}
        await session.commit()
    response = await client.post('/jobs', json={**payload, 'execution_plan_approval': preview.json()['approval_digest']})
    assert response.status_code == 409, response.text
    async with factory() as session:
        assert len(list((await session.scalars(select(Job))).all())) == 1


@pytest.mark.asyncio
async def test_waits_for_all_native_imports(admission, monkeypatch):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb, omit_last=True)
    await trigger(factory, members[0])
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is None
        session.add(Design(id='last-native-import', name='last', job_id=members[-1],
                           pdb_path=str(pdb), source_stage='post_structure_validation'))
        await session.commit()
    await trigger(factory, members[-1])
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is not None


@pytest.mark.asyncio
async def test_rollback_recovery_reuses_prepared_selection(admission, monkeypatch):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    original = jobs.create_job
    original_link = jobs.shutil.copyfile
    links = []
    def record_link(source, destination, *args, **kwargs):
        if child_id(owner) in str(destination):
            links.append(str(destination))
        return original_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(jobs.shutil, 'copyfile', record_link)
    async def fail_before_insertion(*args, **kwargs):
        raise RuntimeError('controlled prequeue interruption')
    monkeypatch.setattr(jobs, 'create_job', fail_before_insertion)
    await trigger(factory, members[0])
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is None
        assert not (await session.get(Job, owner)).params.get('_mutation_seed_refinement_triggered')
    assert len(links) == len(members)
    monkeypatch.setattr(jobs, 'create_job', original)
    await trigger(factory, members[-1])
    assert len(links) == len(members)
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('forgery', ['child_settings', 'seed_bytes', 'fixed_positions', 'current_source'])
async def test_derived_child_forgery_rejected_at_canonical_admission(admission, monkeypatch, forgery):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    original = jobs.create_job
    reached = []
    async def forge_before_admission(request, *args, **kwargs):
        reached.append(True)
        if forgery == 'child_settings':
            request.params['seq_design_fampnn'] = True
        elif forgery == 'current_source':
            from component_runtime import SourceIdentity
            monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: SourceIdentity('c' * 40, 'd' * 40))
        elif forgery == 'fixed_positions':
            Path(request.params['manual_mutation_fixed_positions_json']).write_text('{}')
        else:
            selected = next(Path(request.params['iteration_selection_dir']).glob('*.pdb'))
            selected.unlink()  # do not edit the native source through its symlink
            selected.write_text('HEADER forged seed\n')
        return await original(request, *args, **kwargs)
    monkeypatch.setattr(jobs, 'create_job', forge_before_admission)
    await trigger(factory, members[0])
    assert reached == [True]
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is None
        assert not (await session.get(Job, owner)).params.get('_mutation_seed_refinement_triggered')


@pytest.mark.asyncio
async def test_concurrent_completion_creates_one_child(admission, monkeypatch):
    import asyncio
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    # Force both real finalizers past approval validation before either may
    # claim. Retain the physical driver identities: distinct Sessions alone
    # would not prove a race if a fixture regressed to StaticPool.
    import services.declared_job_expansion as expansion
    original_validate = expansion.validate
    participants = []
    ready = asyncio.Event()
    async def rendezvous(coordinator, session):
        result = await original_validate(coordinator, session)
        if len(participants) < len(members):
            connection = await session.connection()
            raw = await connection.get_raw_connection()
            participants.append(raw.driver_connection)
            if len(participants) == len(members):
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
        return result
    monkeypatch.setattr(expansion, 'validate', rendezvous)
    await asyncio.gather(*(trigger(factory, key) for key in members))
    assert len(participants) == len(members) and len(members) > 1
    assert len({id(connection) for connection in participants}) == len(members)
    await trigger(factory, members[0])
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is not None
        assert len(list((await session.scalars(select(Job))).all())) == len(members) + 2


@pytest.mark.asyncio
@pytest.mark.parametrize('target_kind', ['root', 'override'])
async def test_static_input_same_path_mutation_invalidates_followon(admission, monkeypatch, target_kind):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch, target_kind)
    await complete(factory, members, pdb)
    from paths import get_inputs_dir
    target = get_inputs_dir() / 'target-reference.pdb'
    target.write_bytes(target.read_bytes() + b'REMARK changed after explicit review\n')
    await trigger(factory, members[-1])
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is None
        assert not (await session.get(Job, owner)).params.get('_mutation_seed_refinement_triggered')
    assert not (get_inputs_dir() / 'design_selections/antibody' / child_id(owner)).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['first_copy', 'foreign_file', 'changed_copy'])
async def test_partial_preparation_recovery(admission, monkeypatch, interruption):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    from paths import get_inputs_dir
    selection = get_inputs_dir() / 'design_selections/antibody' / child_id(owner)
    selection.mkdir(parents=True)
    async with factory() as session:
        coordinator = await session.get(Job, owner)
        first_member = coordinator.provenance[KEY]['members'][0]['id']
        design = (await session.scalars(select(Design).where(Design.job_id == first_member))).one()
        retained = selection / f'001_{design.id}.pdb'
    retained.write_bytes(pdb.read_bytes() if interruption != 'changed_copy' else b'corrupt')
    before = retained.read_bytes()
    inode = retained.stat().st_ino
    if interruption == 'foreign_file':
        (selection / 'foreign.pdb').write_bytes(pdb.read_bytes())
    await trigger(factory, members[-1])
    async with factory() as session:
        child = await session.get(Job, child_id(owner))
        assert (child is not None) == (interruption == 'first_copy')
    assert retained.read_bytes() == before and retained.stat().st_ino == inode


@pytest.mark.asyncio
async def test_mid_copy_interruption_never_publishes_partial_seed(admission, monkeypatch):
    _, factory = admission
    owner, members, pdb = await admit_seed(admission, monkeypatch)
    await complete(factory, members, pdb)
    from paths import get_inputs_dir
    selection = get_inputs_dir() / 'design_selections/antibody' / child_id(owner)

    def interrupted_copy(source, destination, *args, **kwargs):
        Path(destination).write_bytes(Path(source).read_bytes()[:17])
        raise RuntimeError('controlled interruption during native seed copy')

    with monkeypatch.context() as transient:
        transient.setattr(jobs.shutil, 'copyfile', interrupted_copy)
        await trigger(factory, members[0])
    assert selection.is_dir() and not list(selection.iterdir())
    async with factory() as session:
        assert await session.get(Job, child_id(owner)) is None
        assert not (await session.get(Job, owner)).params.get('_mutation_seed_refinement_triggered')
    await trigger(factory, members[-1])
    async with factory() as session:
        child = await session.get(Job, child_id(owner))
        assert child is not None
        assert all(p.read_bytes() == pdb.read_bytes() for p in selection.glob('*.pdb'))
    before = {p.name: p.read_bytes() for p in selection.iterdir()}
    await trigger(factory, members[0])
    assert {p.name: p.read_bytes() for p in selection.iterdir()} == before
