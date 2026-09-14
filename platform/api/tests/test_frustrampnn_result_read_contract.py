"""Core reads must not hydrate optional derived scientific artifacts."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from database import FrustraMPNNResult
from routers import frustrampnn as routes
from services.frustrampnn.analytics import _base_identity
from test_frustrampnn_results_api import api, _effective_settings_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", ["1.0", "2.0", "3.0", "9.0"])
@pytest.mark.parametrize("statistics", [None, {"malformed": True}, {
    "schema": "bms.scientific-artifact-reference.v1", "missing": "artifact",
}])
async def test_core_http_reads_do_not_resolve_statistics(api, monkeypatch, generation, statistics):
    client, sessions, _ = api
    settings = _effective_settings_fixture()
    async with sessions() as session:
        result = (await session.execute(select(FrustraMPNNResult).where(
            FrustraMPNNResult.parent_job_id == "job-1"
        ))).scalar_one()
        invocation = result.invocation_id
        result.terminal_result_json = {
            **result.terminal_result_json, "component_contract_version": generation,
        }
        result.settings_sha256 = "1" * 64
        result.effective_settings_sha256 = "2" * 64
        result.effective_settings_json = settings
        result.capability_inventory_sha256 = "3" * 64
        result.statistics_sha256 = "4" * 64 if statistics is not None else None
        result.comparison_compatibility_id = "5" * 64 if statistics is not None else None
        result.runtime_identity_json = {
            "schema_name": "frustrampnn_execution_receipt", "schema_version": 3,
            "runtime_identity_sha256": "6" * 64,
            "execution_configuration_sha256": "7" * 64,
            "commands": [{"argv": ["/private/executable"]}],
            "configured_sif_path": "/private/image.sif",
        }
        await session.commit()
        # Deliberately retain an unreadable artifact reference without invoking
        # the scientific publisher. Core reads must never touch that reference.
        await session.execute(text(
            "UPDATE frustrampnn_results SET statistics_json=:payload WHERE parent_job_id='job-1'"
        ), {"payload": json.dumps(statistics)})
        await session.commit()

    def forbidden_resolution(*args, **kwargs):
        raise AssertionError("core read resolved derived statistics")

    monkeypatch.setattr(routes, "resolve_json_value", forbidden_resolution)
    monkeypatch.setattr("services.scientific_artifacts.resolve_json_value", forbidden_resolution)
    listing = await client.get("/api/frustrampnn/jobs/job-1/results", params={"limit": 1})
    assert listing.status_code == 200, listing.text
    listed = listing.json()
    assert listed["total"] == 1 and listed["offset"] == 0 and listed["limit"] == 1
    item = listed["items"][0]
    assert "statistics_json" not in item and "effective_settings_json" not in item
    assert item["availability"] is True
    assert item["statistics_available"] is (statistics is not None)
    assert ("statistics_json" in item["missing_fields"]) is (statistics is None)
    assert item["component_contract_version"] == generation
    assert item["runtime_identity_sha256"] == "6" * 64
    detail = await client.get(f"/api/frustrampnn/results/{invocation}", params={"job_id": "job-1"})
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert "statistics_json" not in payload
    assert payload["effective_settings_json"] == settings
    assert payload["execution_receipt"]["runtime_identity_sha256"] == "6" * 64
    assert payload["execution_receipt"]["execution_configuration_sha256"] == "7" * 64
    assert payload["runtime_identity_sha256"] != payload["request_sha256"]
    assert "/private" not in detail.text and "argv" not in detail.text
    assert (await client.get(f"/api/frustrampnn/results/{invocation}", params={"job_id": "unknown"})).status_code == 404


@pytest.mark.parametrize("missing", ["settings_sha256", "effective_settings_sha256", "effective_settings_json", "capability_inventory_sha256"])
def test_core_presence_stays_required(missing):
    values = {field: "a" * 64 for field in routes._PHASE4_FIELDS}
    values.update(terminal_result_json={"component_contract_version": "3.0"})
    values[missing] = None
    authority = routes._result_authority(SimpleNamespace(**values), include_documents=False)
    assert authority["availability"] is False
    assert missing in authority["missing_fields"]


@pytest.mark.parametrize("names", ["execution_configuration", "configuration"])
def test_analytics_uses_retained_configuration_identity(names):
    row = SimpleNamespace(
        parent_job_id="job", parent_metadata_json={}, job_params={}, parent_workflow_id="structure_prediction",
        design_id="design", candidate_id="candidate", invocation_id="invocation",
        source_artifact_sha256="a" * 64, runtime_identity_json={},
        summary_json={names + "_id": "configuration", names + "_sha256": "b" * 64, "threshold_policy_id": "saved-threshold-policy"},
    )
    identity = _base_identity(row)
    assert identity["configuration_id"] == "configuration"
    assert identity["configuration_sha256"] == "b" * 64
    assert identity["checkpoint_sha256"] is None
    assert identity["threshold_policy_id"] == "saved-threshold-policy"
