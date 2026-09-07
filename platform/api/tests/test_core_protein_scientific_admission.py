"""Real admission seam with disposable ORM state; never start application/services."""
from copy import deepcopy
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from model_registry import ModelRegistry
from routers import jobs
from schemas import JobCreate
from services import core_protein_scientific_contract as contract

KEY = contract.REVISION_KEY


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["second_sequence", "settings", None])
async def test_marked_variants_all_validate_before_any_row_mutation(admission, monkeypatch, invalid):
    # A registry-owned sequence rule makes the second generated request fail
    # independently. Production registry hardening is explicitly outside scope.
    registry = jobs.get_registry()
    model = registry.get_model("boltz2")
    assert model is not None
    sequence_param = next(p for p in model.params if p.name == "sequence")
    monkeypatch.setattr(sequence_param, "pattern", "[ACDEFGHIKLMNPQRSTVWY]+")
    monkeypatch.setattr(registry, "reload", lambda: None)  # Keep this fixture-owned rule.
    payload = request()
    payload.params = {"msa_provider": "local", "boltz_use_msa": True,
                      "use_msa": False, "seed": 0,
                      "boltz_recycling_steps": -1 if invalid == "settings" else 1,
                      "mutagenesis_variants": [{"name": "one", "sequence": "ACDE"},
                                              {"name": "two", "sequence": None if invalid == "second_sequence" else "FGHI"}]}
    if invalid:
        def no_add(*args, **kwargs):
            raise AssertionError("Job row mutation before all variant validation")
        monkeypatch.setattr(admission, "add", no_add)
        with pytest.raises(HTTPException) as exc:
            await jobs._create_job(payload, BackgroundTasks(), admission)
        assert exc.value.status_code == 422
        assert list((await admission.execute(select(Job))).scalars()) == []
        assert not admission.new
    else:
        await jobs._create_job(payload, BackgroundTasks(), admission)
        rows = list((await admission.execute(select(Job))).scalars())
        variants = [row for row in rows if row.model_id == "boltz2"]
        assert len(variants) == 2
        assert {row.params["sequence"] for row in variants} == {"ACDE", "FGHI"}
        for row in variants:
            assert contract.revision_for_job(row) == 1
            assert row.params["seed"] == 0
            assert row.params["use_msa"] is False



@pytest.mark.asyncio
async def test_forged_child_stage_cannot_suppress_current_revision(admission):
    result = await jobs._create_job(request(child_stage="boltz2"), BackgroundTasks(), admission)
    assert contract.revision_for_job(await admission.get(Job, result.id)) == 1


@pytest.mark.asyncio
async def test_marked_parent_does_not_activate_inactive_caller(admission, monkeypatch):
    monkeypatch.setattr(contract, "ACTIVATED_CALLERS", frozenset())
    parent = Job(id="parent", name="parent", model_id="boltz2", mode="predict",
                 status="running", params={}, provenance={KEY: 1})
    admission.add(parent)
    await admission.commit()
    result = await jobs._create_job(request(parent_job_id="parent", child_stage="boltz2"),
                                    BackgroundTasks(), admission)
    assert contract.revision_for_job(await admission.get(Job, result.id)) is None


@pytest.mark.asyncio
async def test_reused_legacy_child_is_unchanged(admission, monkeypatch):
    parent = Job(id="parent", name="parent", model_id="boltz2", mode="predict",
                 status="running", params={}, provenance={})
    child = Job(id="child", name="sci-contract", model_id="boltz2", mode="predict",
                parent_job_id="parent", child_stage="boltz2", status="completed",
                params={"legacy": 0}, provenance={"legacy": False})
    admission.add_all([parent, child])
    await admission.commit()
    before = deepcopy((child.params, child.provenance))
    monkeypatch.setattr(jobs, "_child_job_has_reusable_outputs", lambda job: True)
    result = await jobs._create_job(request(parent_job_id="parent", child_stage="boltz2"),
                                    BackgroundTasks(), admission)
    assert result.id == "child"
    assert (child.params, child.provenance) == before
    assert len(list((await admission.execute(select(Job))).scalars())) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_fresh_legacy_child_resubmit_uses_current_validation(admission, invalid):
    from fastapi import Request, Response
    source = Job(id="source", name="source", model_id="boltz2", mode="predict",
                 child_stage="boltz2", status="failed",
                 params={"sequence": "ACDE", "use_msa": False,
                         "boltz_recycling_steps": -1 if invalid else 1, "seed": 0}, provenance={})
    admission.add(source)
    await admission.commit()
    before = deepcopy((source.params, source.provenance))
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    if invalid:
        with pytest.raises(HTTPException) as exc:
            await jobs.resubmit_job("source", req, Response(), admission)
        assert exc.value.status_code == 422
        assert len(list((await admission.execute(select(Job))).scalars())) == 1
    else:
        result = await jobs.resubmit_job("source", req, Response(), admission)
        new = await admission.get(Job, result["new_job_id"])
        assert contract.revision_for_job(new) == 1
        assert new.params["seed"] == 0
        assert new.params["use_msa"] is False
    assert (source.params, source.provenance) == before


@pytest.mark.asyncio
async def test_fresh_marked_source_cannot_downgrade_inactive_caller(admission, monkeypatch):
    from fastapi import Request, Response
    source = Job(id="source", name="source", model_id="boltz2", mode="predict",
                 status="failed", params={"sequence": "ACDE", KEY: 1}, provenance={KEY: 1})
    admission.add(source)
    await admission.commit()
    monkeypatch.setattr(contract, "ACTIVATED_CALLERS", frozenset())
    with pytest.raises(HTTPException) as exc:
        await jobs.resubmit_job("source", Request({"type": "http", "headers": []}), Response(), admission)
    assert exc.value.status_code == 422
    assert len(list((await admission.execute(select(Job))).scalars())) == 1


@pytest.mark.parametrize("value", [None, "1", True, False, 1])
@pytest.mark.parametrize("location", ["top", "extra_dict", "extra_list", "overrides"])
def test_resume_raw_input_rejects_reserved_marker(value, location):
    from pydantic import ValidationError
    marker = {KEY: value}
    payload = {
        "top": marker,
        "extra_dict": {"ignored_extra": {"nested": marker}},
        "extra_list": {"ignored_extra": [{"nested": [marker]}]},
        "overrides": {"param_overrides": {"nested": [marker]}},
    }[location]
    with pytest.raises(ValidationError, match="server-owned"):
        jobs.ResumeJobRequest.model_validate(payload)


def test_resume_preserves_known_overrides_and_legacy_extra_policy():
    payload = {"from_stage": "validation", "name_suffix": "retry",
               "param_overrides": {"sequence": "ACDE", "nested": [{"ordinary": None}]}}
    parsed = jobs.ResumeJobRequest.model_validate({**payload, "ignored_extra": {"items": [1, None]}})
    assert parsed.model_dump() == payload


@pytest_asyncio.fixture
async def admission(monkeypatch, tmp_path):
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "0")
    monkeypatch.setattr(jobs, "get_results_dir", lambda: tmp_path / "results")
    registry = ModelRegistry(Path(__file__).resolve().parents[1] / "config" / "models")
    monkeypatch.setattr(jobs, "get_registry", lambda: registry)
    monkeypatch.setattr(contract, "ACTIVATED_CALLERS", frozenset({("boltz2", "predict")}))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def request(**kwargs):
    return JobCreate(name="sci-contract", model_id="boltz2", mode="predict",
                     params={"sequence": "ACDEFGHIK", "use_msa": False}, **kwargs)


@pytest.mark.asyncio
async def test_admission_persists_server_revision_and_keeps_old_rows(admission):
    old = Job(id="old", name="old", model_id="boltz2", mode="predict", status="completed",
              params={"sequence": "ACDEFGHIK", "use_msa": False}, provenance={"legacy": "keep"})
    admission.add(old)
    await admission.commit()
    original = deepcopy((old.params, old.provenance))
    response = await jobs._create_job(request(), BackgroundTasks(), admission)
    admission.expire_all()
    new = await admission.get(Job, response.id)
    historical = await admission.get(Job, "old")
    assert new.provenance[KEY] == 1
    assert new.params[KEY] == 1
    assert (historical.params, historical.provenance) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{KEY: 0}, {"mutagenesis_variants": [{"extras": {KEY: 1}}]}])
async def test_common_boundary_rejects_post_schema_mutation_before_handoff(monkeypatch, params):
    payload = request()
    payload.params.update(params)
    def never(*args, **kwargs):
        raise AssertionError("trusted handoff/registry must not execute")
    monkeypatch.setattr(jobs, "get_registry", never)
    monkeypatch.setattr(jobs, "_raise_if_workflow_launches_disabled", never)
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(payload, BackgroundTasks(), object())
    assert exc.value.status_code == 422
    assert "server-owned" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_governing_validation_precedes_assignment(admission, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("revision assigned before governing validation")
    monkeypatch.setattr(contract, "admission_revision", never)
    payload = request()
    payload.mode = "not_an_admitted_mode"
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(payload, BackgroundTasks(), admission)
    assert exc.value.status_code == 422
    assert list((await admission.execute(select(Job))).scalars()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_revision", [None, 1])
async def test_new_scientific_child_uses_current_admission(admission, parent_revision):
    parent = Job(id="parent", name="parent", model_id="antibody_denovo",
                 mode="antibody_denovo_pipeline", status="running", params={},
                 provenance={} if parent_revision is None else {KEY: parent_revision})
    admission.add(parent)
    await admission.commit()
    response = await jobs._create_job(request(parent_job_id="parent", child_stage="boltz2"),
                                      BackgroundTasks(), admission)
    child = await admission.get(Job, response.id)
    assert contract.revision_for_job(child) == 1
    assert contract.revision_for_job(parent) == parent_revision


def test_nextflow_transports_exact_integer_marker_without_parameter_changes(monkeypatch, tmp_path):
    from services import nextflow
    params = {"sequence": "ACDEFGHIK", "use_msa": False, KEY: 1}
    original = deepcopy(params)
    cmd = nextflow.build_nextflow_command("boltz2", "predict", params, str(tmp_path), job_id="fixture")
    assert cmd[cmd.index("--" + KEY) + 1] == "1"
    assert params == original


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, 1])
async def test_launch_rebuilds_transport_from_loaded_job(admission, monkeypatch, tmp_path, revision):
    import database
    from services import nextflow
    parent = Job(id="launch", name="launch", model_id="boltz2", mode="predict", status="queued",
                 params={}, provenance={} if revision is None else {KEY: revision})
    admission.add(parent)
    await admission.commit()
    monkeypatch.setattr(database, "async_session", async_sessionmaker(admission.bind, expire_on_commit=False))
    monkeypatch.setattr(nextflow, "configured_lane", lambda **kwargs: None)
    monkeypatch.setattr(nextflow, "transient_workflow_runner_mode", lambda: False)
    async def prepare(params):
        return params, []
    monkeypatch.setattr(nextflow, "prepare_boltzgen_params_for_launch", prepare)
    class StopBeforeExecution(Exception):
        pass
    async def capture(_session, _job, params):
        assert params.get(KEY) == revision
        assert params["sequence"] == "ACDEFGHIK"
        raise StopBeforeExecution
    monkeypatch.setattr(nextflow, "_resolve_dynamic_gpu_cpu_share", capture)
    with pytest.raises(StopBeforeExecution):
        await nextflow.launch_nextflow_job("launch", "boltz2", "predict",
                                          {KEY: 0, "sequence": "ACDEFGHIK"}, str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resubmit", "resume"])
@pytest.mark.parametrize("revision", [None, 1])
async def test_direct_job_writers_preserve_or_freshly_admit(admission, tmp_path, operation, revision):
    from fastapi import Request, Response
    original = Job(id="retry", name="retry", model_id="boltz2", mode="predict", status="failed",
                   params={"sequence": "ACDEFGHIK", "use_msa": False, **({KEY: 1} if revision else {})},
                   provenance={} if revision is None else {KEY: revision}, output_dir=str(tmp_path / "old"))
    admission.add(original)
    await admission.commit()
    before = deepcopy((original.params, original.provenance))
    req = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/", "headers": []})
    if operation == "resubmit":
        result = await jobs.resubmit_job("retry", req, Response(), admission)
        expected = 1  # Fresh validated submission, unlike cached true resume.
    else:
        result = await jobs.resume_job("retry", req, Response(), request=None, session=admission)
        expected = revision
    new = await admission.get(Job, result["new_job_id"])
    assert contract.revision_for_job(new) == expected
    assert new.params.get(KEY) == expected
    await admission.refresh(original)
    assert (original.params, original.provenance) == before


@pytest.mark.asyncio
async def test_resume_rejects_reserved_overrides_before_lookup(monkeypatch):
    from fastapi import Request, Response
    class NoLookup:
        async def execute(self, *args):
            raise AssertionError("must reject before DB lookup")
    request = jobs.ResumeJobRequest()
    # Retain endpoint defense for internal post-validation mutation.
    request.param_overrides = {"extras": {KEY: 0}}
    with pytest.raises(HTTPException) as exc:
        await jobs.resume_job("unknown", Request({"type": "http"}), Response(), request=request, session=NoLookup())
    assert exc.value.status_code == 422
