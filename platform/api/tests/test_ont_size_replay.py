"""Historical size replay through HTTP persistence and the retry compiler owner."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job
from services import alignment_access, nextflow
from test_ont_policy_regression import policy_context
from test_ont_pooled_reference_assignment import pooled_context


CASES = [
    pytest.param({}, {}, 7000, id="legacy-missing"),
    pytest.param({}, {"execution_plan_approval": "unavailable"}, 7000, id="unavailable-plan"),
    pytest.param({}, {"execution_plan_approval": {"plan": {"native_parameters_json": "unavailable"}}},
                 7000, id="unavailable-native-observation"),
    pytest.param({}, {"execution_plan_approval": {"plan": {
        "native_parameters_json": {"expected_plasmid_size": 4321}}}}, 4321, id="retained-native"),
    pytest.param({"expected_plasmid_size": 7000}, {}, 7000, id="explicit-7000"),
    pytest.param({"expected_plasmid_size": 9000}, {}, 9000, id="explicit-override"),
    pytest.param({"expected_plasmid_size": 3200, "requested_expected_plasmid_size": None},
                 {}, 3200, id="resolved-auto"),
    pytest.param({"expected_plasmid_size": None}, {}, None, id="explicit-auto"),
    pytest.param({"expected_plasmid_size": 9000}, {"execution_plan_approval": {"plan": {
        "native_parameters_json": {"expected_plasmid_size": 4321}}}}, 9000, id="saved-effective-wins"),
]
PATHS = [("fastq_qc", "ont_fastq_qc", "fastq"),
         ("plasmid_qc", "ont_plasmid_qc", "bam"),
         ("construct_screening", "ont_construct_screening", "pod5"),
         ("wf_clone_validation", "wf_clone_validation", "fastq")]


async def seed(context, params, provenance, mode="fastq_qc", status="failed"):
    token, digest = alignment_access.issue_alignment_access_token()
    original = Job(id="size-original", name="size original", model_id="nanopore",
                   mode=mode, status=status, queue_status="failed", params=deepcopy(params),
                   provenance={**deepcopy(provenance), alignment_access.PROVENANCE_DIGEST_KEY: digest},
                   output_dir=str(context.results_root / "original"), retry_count=1)
    context.session.add(original)
    await context.session.commit()
    context.client.cookies.set(alignment_access.cookie_name(original.id, secure=True), token)
    return original


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["resubmit", "resume"])
@pytest.mark.parametrize("mode,workflow,input_mode", PATHS)
@pytest.mark.parametrize("size,provenance,expected", CASES)
async def test_resubmit_and_retry_compiler_preserve_size(policy_context, mode, workflow, input_mode,
                                                         size, provenance, expected, action):
    context = policy_context
    reference = context.inputs_root / "reference.fasta"
    reference.write_text(">reference\n" + "ACGT" * 800 + "\n")
    bam = context.inputs_root / "reads.bam"
    bam.write_bytes(b"inert compiler input")
    params = {"ont_workflow_id": workflow, "ont_input_mode": input_mode,
              "reference_fasta": str(reference), "remote_result_policy": "automatic",
              "fastq_input": str(context.fastq), **size}
    if input_mode == "bam":
        params["bam_input"] = str(bam)
    if input_mode == "pod5":
        params["pod5_dir"] = str(context.pod5)
    original = await seed(context, params, provenance, mode)
    saved_provenance = deepcopy(original.provenance)

    # Same owner called by local automatic retry and ordinary remote launch;
    # real native compiler/plan/materialization, but no worker/process execution.
    invocation = await nextflow._compile_launch_nextflow_invocation(
        context.session, original, deepcopy(original.params), original.output_dir)
    assert invocation.native_parameters.get("expected_plasmid_size") == expected
    assert json.loads(invocation.requested_json) == params
    assert original.params == params
    assert original.provenance == saved_provenance

    response = await context.client.post(f"/api/jobs/{original.id}/{action}")
    assert response.status_code == 200, response.text
    async with async_sessionmaker(context.engine)() as reader:
        child = await reader.get(Job, response.json()["new_job_id"])
        persisted_original = await reader.get(Job, original.id)
        assert child.params["expected_plasmid_size"] == expected
        assert child.params["remote_result_policy"] == "automatic"
        for key, value in params.items():
            assert child.params[key] == value
        assert child.status == "queued"
        assert persisted_original.params == params
        assert persisted_original.provenance == saved_provenance
        replay = await nextflow._compile_launch_nextflow_invocation(
            reader, child, deepcopy(child.params), child.output_dir)
        assert replay.native_parameters.get("expected_plasmid_size") == expected
        assert json.loads(replay.requested_json) == child.params


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unauthorized", "project-bound", "running", "resume"])
async def test_size_compatibility_preserves_refusals(policy_context, case):
    context = policy_context
    params = {"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"}
    provenance = {"launch_context_id": "retained-context"} if case == "project-bound" else {}
    original = await seed(context, params, provenance, status="running" if case == "running" else "failed")
    if case == "unauthorized":
        context.client.cookies.clear()
    action = "resume" if case == "resume" else "resubmit"
    payload = {"param_overrides": {"expected_plasmid_size": None}} if case == "resume" else None
    response = await context.client.post(f"/api/jobs/{original.id}/{action}", json=payload)
    assert response.status_code == {"unauthorized": 403, "project-bound": 409,
                                    "running": 400, "resume": 422}[case], response.text
    async with async_sessionmaker(context.engine)() as reader:
        assert len((await reader.execute(select(Job))).scalars().all()) == 1
        assert (await reader.get(Job, original.id)).params == params


def test_new_direct_compilation_keeps_auto(tmp_path):
    params = {"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq",
              "fastq_input": str(tmp_path / "reads.fastq")}
    invocation = nextflow.compile_nextflow_invocation("nanopore", "fastq_qc", params,
                                                      str(tmp_path / "output"))
    assert invocation.native_parameters.get("expected_plasmid_size") is None
    assert "expected_plasmid_size" not in params


@pytest.mark.parametrize("mode", ["ont_basecall_dna", "basecall_dna", "ont_basecall_rna"])
def test_replay_does_not_add_size_to_unrelated_modes(mode):
    from services.ont_ngs_contract import replay_expected_plasmid_size
    assert replay_expected_plasmid_size({}, mode=mode) == {}


def test_recorded_size_uses_real_selected_plan_serialization(tmp_path):
    from services.ont_ngs_contract import replay_expected_plasmid_size
    invocation = nextflow.compile_nextflow_invocation("nanopore", "fastq_qc",
        {"ont_workflow_id": "ont_fastq_qc", "fastq_input": str(tmp_path / "reads.fastq"),
         "expected_plasmid_size": 4321}, str(tmp_path / "output"))
    provenance = {"execution_plan_approval": {"plan": invocation.execution_plan.to_dict()}}
    assert replay_expected_plasmid_size({}, provenance, mode="fastq_qc")["expected_plasmid_size"] == 4321
