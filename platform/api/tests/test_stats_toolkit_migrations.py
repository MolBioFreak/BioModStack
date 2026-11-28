"""Unit and live-service integration tests for BMS Stats Toolkit migrations."""

from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx
import pytest

from routers.analytics import CorrelationMatrix
from services.conformational_mapping.analysis import _rank_stability
from services.conformational_mapping.confornets_mds import compute_confornets_mds_via_toolkit
from services.md.results import _compute_replica_convergence_diagnostics
from services.stats_toolkit_client import StatsToolkitClient


def _mock_client(handler) -> StatsToolkitClient:
    return StatsToolkitClient(
        base_url="http://stats.test",
        transport=httpx.MockTransport(handler),
    )


def test_correlation_matrix_model_accepts_enriched_and_legacy_payloads() -> None:
    enriched_model = CorrelationMatrix.model_validate({
        "job_id": "job-1",
        "metrics": ["plddt", "ptm"],
        "matrix": [[1.0, 0.9877], [0.9877, 1.0]],
        "sample_sizes": [[5, 5], [5, 5]],
        "p_value_matrix": [[0.0, 0.0016], [0.0016, 0.0]],
        "adjusted_p_value_matrix": [[0.0, 0.0016], [0.0016, 0.0]],
        "pairs": [{"var_a": "plddt", "var_b": "ptm", "statistic": 0.98765}],
        "method": "pearson",
        "p_adjust_method": "benjamini_hochberg",
        "stats_backend": "stats_toolkit",
        "stats_tool_id": "inference.correlation_matrix",
        "stats_method_version": "1.0.0",
        "stats_run": {"run_id": "run-1", "workspace_id": "ws-1", "tool_id": "inference.correlation_matrix"},
    })
    assert enriched_model.stats_backend == "stats_toolkit"
    assert enriched_model.stats_run["run_id"] == "run-1"


@pytest.mark.parametrize("summary_offline", [False, True])
def test_cm_rank_stability_delegates_durable_run(summary_offline: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"workspaces": [{"workspace_id": "ws-1", "name": "BioModStack analyses"}]})
        body = json.loads(request.content.decode())
        seen.append(body)
        if body["tool_id"] == "descriptive.summary":
            if summary_offline:
                raise httpx.ConnectError("summary unavailable", request=request)
            assert body["inputs"]["columns"] == {"spearman": [0.9, 0.8, 0.95]}
            return httpx.Response(200, json={
                "run_id": "run-cm-summary", "workspace_id": "ws-1", "tool_id": body["tool_id"],
                "status": "completed", "result": {"columns": {"spearman": {"median": 0.875}}},
            })
        return httpx.Response(
            200,
            json={
                "run_id": "run-cm-rank-1",
                "workspace_id": "ws-1",
                "tool_id": "inference.correlation_matrix",
                "status": "completed",
                "result": {
                    "pairs": [
                        {"var_a": "1", "var_b": "2", "statistic": 0.9, "p_value": 0.01, "adjusted_p_value": 0.015},
                        {"var_a": "1", "var_b": "3", "statistic": 0.8, "p_value": 0.03, "adjusted_p_value": 0.03},
                        {"var_a": "2", "var_b": "3", "statistic": 0.95, "p_value": 0.005, "adjusted_p_value": 0.015},
                    ]
                },
            },
        )

    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: _mock_client(handler))
    items = {("t", index): {"strata": {seed: {"valid_inner": ["0"], "mean": float(10 - index)}
                                for seed in ("1", "2", "3")}} for index in range(4)}
    rank = _rank_stability(
        items,
        {seed: ["0"] for seed in ("1", "2", "3")},
        inner_minimum=1.0,
        outer_minimum=1.0,
        common_minimum=3,
    )
    assert [r["tool_id"] for r in seen] == ["inference.correlation_matrix", "descriptive.summary"]
    assert seen[0]["inputs"]["method"] == "spearman"
    assert rank.unavailable == summary_offline
    if summary_offline:
        assert rank.value is None and rank.summary_run is None
    else:
        assert rank.value == pytest.approx(0.875)  # supplied by Toolkit, not locally reconstructed
        assert rank.summary_run is not None
        assert rank.summary_run["run_id"] == "run-cm-summary"
    assert len(rank.common) == 4 and len(rank.pairwise) == 3 and not rank.excluded
    assert all(entry["stats_backend"] == "stats_toolkit" for entry in rank.pairwise)
    assert rank.pairwise[0]["stats_run"]["run_id"] == "run-cm-rank-1"


def test_confornets_mds_delegates_controller_durable_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"workspaces": [{"workspace_id": "ws-1", "name": "BioModStack analyses"}]})
        body = json.loads(request.content.decode())
        called.append(body)
        return httpx.Response(
            200,
            json={
                "run_id": "run-mds-1",
                "workspace_id": "ws-1",
                "tool_id": "multivariate.distance_clustering",
                "status": "completed",
                "result": {
                    "mds_coordinates": [
                        [-1.5, -2.0],
                        [1.5, -2.0],
                        [1.5, 2.0],
                        [-1.5, 2.0],
                    ],
                    "method_version": "1.0.0",
                },
            },
        )

    monkeypatch.setattr("services.conformational_mapping.confornets_mds.get_stats_toolkit_client", lambda: _mock_client(handler))
    dm = [
        [0.0, 3.0, 5.0, 4.0],
        [3.0, 0.0, 4.0, 5.0],
        [5.0, 4.0, 0.0, 3.0],
        [4.0, 5.0, 3.0, 0.0],
    ]
    res = compute_confornets_mds_via_toolkit(dm)
    assert len(called) == 1
    assert called[0]["tool_id"] == "multivariate.distance_clustering"
    assert called[0]["inputs"]["mds_components"] == 2
    assert res["status"] == "available"
    assert res["stats_run"]["run_id"] == "run-mds-1"
    coords = res["coordinates"]
    for i in range(4):
        for j in range(4):
            dist = math.hypot(coords[i]["x"] - coords[j]["x"], coords[i]["y"] - coords[j]["y"])
            assert dist == pytest.approx(dm[i][j], abs=1e-5)


def test_md_replica_convergence_diagnostics_delegates_durable_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/workspaces":
            return httpx.Response(200, json={"workspaces": [{"workspace_id": "ws-1", "name": "BioModStack analyses"}]})
        body = json.loads(request.content.decode())
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "run_id": "run-md-conv-0",
                "workspace_id": "ws-1",
                "tool_id": "timeseries.convergence_diagnostics",
                "status": "completed",
                "result": {
                    "n": len(body["inputs"]["values"]),
                    "tau_int": 1.2,
                    "statistical_inefficiency": 3.4,
                    "effective_sample_size": 4.7,
                    "block_sem": 0.03,
                    "naive_sem": 0.015,
                    "geweke_z": 0.4,
                    "geweke_p_value": 0.69,
                    "changepoint_time": 20.0,
                    "equilibrated_after_index": 2,
                    "drift_slope": 0.0002,
                    "drift_p_value": 0.81,
                    "converged": True,
                    "method_version": "1.0.0",
                },
            },
        )

    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: _mock_client(handler))
    reports = [
        {
            "replica": 0,
            "points": [{"time_ps": float(i * 10), "rmsd_angstrom": 1.5 + 0.01 * (i % 3)} for i in range(12)],
        },
        {
            "replica": 1,
            "points": [{"time_ps": 0.0, "rmsd_angstrom": 0.0}, {"time_ps": 10.0, "rmsd_angstrom": 0.5}],
        },
    ]
    diag = _compute_replica_convergence_diagnostics(reports)
    assert diag is not None
    assert len(calls) == 1
    assert diag["status"] == "available"
    assert diag["stats_backend"] == "stats_toolkit"
    assert diag["stats_tool_id"] == "timeseries.convergence_diagnostics"
    assert diag["stats_run"]["run_id"] == "run-md-conv-0"
    assert len(diag["replicas"]) == 1
    assert diag["replicas"][0]["replica"] == 0
    assert diag["replicas"][0]["converged"] is True
    assert diag["replicas"][0]["tau_int"] == pytest.approx(1.2)
    assert diag["replicas"][0]["block_sem"] == pytest.approx(0.03)
    assert diag["replicas"][0]["stats_run"]["run_id"] == "run-md-conv-0"


def _live_stats_toolkit_reachable() -> bool:
    url = (os.environ.get("BMS_STATS_TOOLKIT_URL") or "http://127.0.0.1:18181").rstrip("/")
    try:
        resp = httpx.get(f"{url}/health/ready", timeout=1.5)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(
    not _live_stats_toolkit_reachable(),
    reason="Live Stats Toolkit service not reachable in this test namespace",
)
def test_live_stats_toolkit_end_to_end_migrations() -> None:
    items = {("t", idx): {"strata": {seed: {"valid_inner": ["0"], "mean": float(10 - idx)}
                                for seed in ("1", "2", "3")}} for idx in range(5)}
    rank = _rank_stability(
        items,
        {seed: ["0"] for seed in ("1", "2", "3")},
        inner_minimum=1.0,
        outer_minimum=1.0,
        common_minimum=3,
    )
    assert rank.value == pytest.approx(1.0)
    client = StatsToolkitClient(base_url=os.environ.get("BMS_STATS_TOOLKIT_URL", "http://127.0.0.1:18181"))
    assert rank.summary_run is not None
    assert client.read_run_outputs(rank.summary_run["run_id"])["columns"]["spearman"]["median"] == rank.value
    assert len(rank.common) == 5 and len(rank.pairwise) == 3 and not rank.excluded
    assert all(item["stats_backend"] == "stats_toolkit" for item in rank.pairwise)
    assert all(item["stats_run"]["run_id"] for item in rank.pairwise)

    dm = [
        [0.0, 3.0, 5.0, 4.0],
        [3.0, 0.0, 4.0, 5.0],
        [5.0, 4.0, 0.0, 3.0],
        [4.0, 5.0, 3.0, 0.0],
    ]
    mds = compute_confornets_mds_via_toolkit(dm)
    assert mds["status"] == "available"
    assert mds["stats_run"]["run_id"]
    coords = mds["coordinates"]
    for i in range(4):
        for j in range(4):
            dist = math.hypot(coords[i]["x"] - coords[j]["x"], coords[i]["y"] - coords[j]["y"])
            assert dist == pytest.approx(dm[i][j], abs=1e-4)

    reports = [{
        "replica": 0,
        "points": [
            {"time_ps": float(i * 10), "rmsd_angstrom": 1.5 + (0.04 if i % 2 == 0 else -0.04)}
            for i in range(20)
        ],
    }]
    diag = _compute_replica_convergence_diagnostics(reports)
    assert diag is not None
    assert diag["status"] == "available"
    assert diag["stats_backend"] == "stats_toolkit"
    assert diag["stats_run"]["run_id"]
    assert diag["replicas"][0]["converged"] is True


@pytest.mark.skipif(not _live_stats_toolkit_reachable(), reason="Live Stats Toolkit service not reachable in this test namespace")
def test_live_cm_report_references_final_statistic() -> None:
    from services.conformational_mapping.analysis import analyze_landscapes
    from test_step5_cm_md_confornets import _make_cm_fixture

    ensemble, landscapes, policy = _make_cm_fixture()
    report = analyze_landscapes(ensemble, landscapes, policy=policy)
    refs = {ref["role"]: ref for ref in report["stats_runs"]}
    client = StatsToolkitClient(base_url=os.environ.get("BMS_STATS_TOOLKIT_URL", "http://127.0.0.1:18181"))
    summary = refs["rank_stability:t1"]
    correlation = refs["rank_correlations:t1"]
    assert summary["tool_id"] == "descriptive.summary"
    assert correlation["tool_id"] == "inference.correlation_matrix"
    stored = client.read_run_outputs(summary["run_id"])["columns"]["spearman"]["median"]
    assert stored == pytest.approx(1.0)
    assert report["results"]
    for row in report["results"]:
        assert row["components"]["rank_stability"] == stored
        pairs = row["components"]["pairwise_rank_correlations"]
        assert len(pairs) == 3
        assert {p["stats_run"]["run_id"] for p in pairs} == {correlation["run_id"]}
