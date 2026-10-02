"""Actual typed ONT route, Job persistence, replay and model-owned preset contract."""
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job
from services.ont_ngs_contract import normalize_ont_launch_params, samtools_consensus_setting
from test_ont_policy_regression import policy_context, pooled_context  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", ["ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"])
@pytest.mark.parametrize("preset", ["omitted", *samtools_consensus_setting()["enum"]])
async def test_route_saved_and_replayed_presets(policy_context, workflow, preset):
    context = policy_context
    params = {"fastq_path": str(context.fastq), "molbio_ngs_receipt_id": context.receipt_ids[0]}
    if preset != "omitted":
        params["samtools_consensus_config"] = preset
    response = await context.client.post(f"/api/ont/ngs/{workflow}/submit", json={"name": "consensus control", "params": params})
    assert response.status_code == 201, response.text
    expected = None if preset == "omitted" else preset
    assert response.json()["params"]["samtools_consensus_config"] == expected
    async with async_sessionmaker(context.engine)() as reader:
        job = await reader.get(Job, response.json()["id"])
        assert job.params["samtools_consensus_config"] == expected
        replay = normalize_ont_launch_params(workflow, json.loads(json.dumps(job.params)))
        assert replay["samtools_consensus_config"] == expected


@pytest.mark.asyncio
async def test_http_invalid_preset_is_rejected_not_replaced(policy_context):
    response = await policy_context.client.post("/api/ont/ngs/ont_fastq_qc/submit", json={
        "name": "invalid preset", "params": {"fastq_path": str(policy_context.fastq),
            "molbio_ngs_receipt_id": policy_context.receipt_ids[0], "samtools_consensus_config": "unknown"}})
    assert response.status_code == 422
    assert "samtools_consensus_config" in response.text


@pytest.mark.parametrize("preset", ["unknown", "", True, 0, "r10.4_sup --cutoff 0"])
def test_invalid_presets_are_not_replaced(preset):
    with pytest.raises(ValueError, match="samtools_consensus_config"):
        normalize_ont_launch_params("ont_fastq_qc", {"samtools_consensus_config": preset})


def test_model_and_capability_discovery_share_preset_owner():
    from model_registry import get_registry
    from services.ngs_molbio_capabilities import capability_parameter_schema, _schema_context, _validate
    setting = samtools_consensus_setting()
    model = get_registry().get_model("nanopore")
    field = next(field for field in model.params if field.name == "samtools_consensus_config")
    assert field.default is None
    assert field.enum == [value for value in setting["enum"] if value is not None]
    assert field.accepted_types == ["string", "null"]
    for mode in ("fastq_qc", "plasmid_qc", "construct_screening", "clone_validation"):
        assert "samtools_consensus_config" in next(row for row in model.modes if row.id == mode).params
        schema = capability_parameter_schema(f"ngs.ont.{mode}")
        _, registry = _schema_context()
        for value in setting["enum"]:
            _validate(value, schema["properties"]["samtools_consensus_config"], "preset", registry)


def test_check_pass_does_not_authorize_automatic_assessment():
    from services.molbio_ngs_evidence import _assessment_result, ASSESSMENT_RULE_REGISTRY
    from services.sequence_qc_manifest import VERIFICATION_SCHEMA
    rule = next(iter(ASSESSMENT_RULE_REGISTRY.values()))
    assert _assessment_result(lifecycle="completed", manifest_integrity="valid",
        manifest={"verdict": "PASS", "threshold_profile": {"calibration_status": "experimental", "public_accuracy_validated": False,
            "values": {"automatic_pass_eligible": False}}}, manifest_schema=VERIFICATION_SCHEMA, rule=rule) == "REVIEW"
