"""Unit and live-service integration tests for BMS Stats Toolkit migrations (Phase 3b)."""

from __future__ import annotations

import json
import math
import os
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from routers.analytics import CorrelationMatrix
from services.conformational_mapping.analysis import _rank_stability
from services.md.results import _compute_replica_convergence_diagnostics
from services.scientific_analytics import MetricDescriptor, metric_state, summarize
from services.stats_toolkit_client import (
    StatsToolkitClient,
    StatsToolkitUnavailableError,
    stats_toolkit_required,
)


def _mock_client(handler) -> StatsToolkitClient:
    return StatsToolkitClient(
        base_url="http://stats.test",
        transport=httpx.MockTransport(handler),
    )


def test_stats_toolkit_required_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_STATS_TOOLKIT_REQUIRED", raising=False)
    assert stats_toolkit_required() is False
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("BMS_STATS_TOOLKIT_REQUIRED", truthy)
        assert stats_toolkit_required() is True
    monkeypatch.setenv("BMS_STATS_TOOLKIT_REQUIRED", "0")
    assert stats_toolkit_required() is False


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
    legacy_model = CorrelationMatrix.model_validate({
        "job_id": "job-legacy",
        "metrics": ["plddt", "ptm"],
        "matrix": [[1.0, 0.9], [0.9, 1.0]],
        "sample_sizes": [[5, 5], [5, 5]],
    })
    assert legacy_model.p_value_matrix is None

def test_scientific_analytics_summarize_delegates_correlations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append((request.url.path, body))
        if request.url.path.endswith("descriptive.summary/execute"):
            cols = {k: {"count": len(v), "min": min(v), "max": max(v), "mean": sum(v) / len(v), "median": sorted(v)[len(v) // 2], "std": 1.0} for k, v in body.get("columns", {}).items()}
            return httpx.Response(200, json={"result": {"columns": cols}})
        return httpx.Response(
            200,
            json={
                "result": {
                    "pairs": [
                        {"var_a": "a", "var_b": "b", "statistic": 0.99},
                        {"var_a": "a", "var_b": "c", "statistic": None},
                        {"var_a": "b", "var_b": "c", "statistic": None},
                    ]
                }
            },
        )

    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: _mock_client(handler))
    desc_a = MetricDescriptor(
        metric_id="a",
        source="canonical_artifact",
        scope="design",
        unit="unit",
        direction="higher",
        producer_version="1.0",
        derivation_version="1.0",
    )
    desc_b = MetricDescriptor(
        metric_id="b",
        source="canonical_artifact",
        scope="design",
        unit="unit",
        direction="higher",
        producer_version="1.0",
        derivation_version="1.0",
    )
    desc_c = MetricDescriptor(
        metric_id="c",
        source="canonical_artifact",
        scope="design",
        unit="unit",
        direction="higher",
        producer_version="1.0",
        derivation_version="1.0",
    )
    rows = [
        (
            f"d{i}",
            {
                "metrics": {"a": float(i), "b": float(i * 2), "c": 5.0},
                "metric_states": {
                    "a": metric_state(float(i)),
                    "b": metric_state(float(i * 2)),
                    "c": metric_state(5.0),
                },
                "metric_descriptors": {"a": desc_a, "b": desc_b, "c": desc_c},
            },
        )
        for i in range(1, 5)
    ]
    summary = summarize(rows)
    assert any(p.endswith("inference.correlation_matrix/execute") for p, _ in calls)
    assert summary["pairs"]["a_vs_b"]["correlation"].value == pytest.approx(0.99)
    assert summary["pairs"]["a_vs_c"]["correlation"].state == "unavailable"
    assert summary["pairs"]["a_vs_c"]["correlation"].reason_code == "constant_metric"


def test_conformational_mapping_rank_stability_delegates_spearman(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "result": {
                    "pairs": [
                        {"var_a": "1", "var_b": "2", "statistic": 0.9, "p_value": 0.01, "adjusted_p_value": 0.015},
                        {"var_a": "1", "var_b": "3", "statistic": 0.8, "p_value": 0.03, "adjusted_p_value": 0.03},
                        {"var_a": "2", "var_b": "3", "statistic": 0.95, "p_value": 0.005, "adjusted_p_value": 0.015},
                    ]
                }
            },
        )

    monkeypatch.setattr("services.stats_toolkit_client.get_stats_toolkit_client", lambda **_: _mock_client(handler))
    items = {("t", index): {(seed, "0"): float(10 - index) for seed in ("1", "2", "3")} for index in range(4)}
    stability, common, pairwise, excluded = _rank_stability(
        items,
        {seed: ["0"] for seed in ("1", "2", "3")},
        inner_minimum=1.0,
        outer_minimum=1.0,
        common_minimum=3,
    )
    assert len(seen) == 1
    assert seen[0]["method"] == "spearman"
    assert stability == pytest.approx(0.9)
    assert len(common) == 4 and len(pairwise) == 3 and not excluded
    assert all(entry["stats_backend"] == "stats_toolkit" for entry in pairwise)
    assert pairwise[0]["adjusted_p_value"] == pytest.approx(0.015)


def test_confornets_mds_delegates_via_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    import _stats_toolkit_rpc
    import run_confornets_inference as confornets

    called: list[tuple[str, dict[str, Any]]] = []

    def fake_exec(tool_id: str, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        called.append((tool_id, payload))
        return {
            "mds_coordinates": [
                [-1.5, -2.0],
                [1.5, -2.0],
                [1.5, 2.0],
                [-1.5, 2.0],
            ]
        }

    monkeypatch.setattr(_stats_toolkit_rpc, "execute_stats_tool", fake_exec)
    # 3x4 rectangle distance matrix
    dm = [
        [0.0, 3.0, 5.0, 4.0],
        [3.0, 0.0, 4.0, 5.0],
        [5.0, 4.0, 0.0, 3.0],
        [4.0, 5.0, 3.0, 0.0],
    ]
    coords = confornets._mds_coordinates(dm)
    assert len(called) == 1
    assert called[0][0] == "multivariate.distance_clustering"
    assert called[0][1]["mds_components"] == 2
    for i in range(4):
        for j in range(4):
            dist = math.hypot(coords[i]["x"] - coords[j]["x"], coords[i]["y"] - coords[j]["y"])
            assert dist == pytest.approx(dm[i][j], abs=1e-5)


def test_md_replica_convergence_diagnostics_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "result": {
                    "n": len(body["values"]),
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
                }
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
    assert diag["stats_backend"] == "stats_toolkit"
    assert diag["stats_tool_id"] == "timeseries.convergence_diagnostics"
    assert len(diag["replicas"]) == 1
    assert diag["replicas"][0]["replica"] == 0
    assert diag["replicas"][0]["converged"] is True
    assert diag["replicas"][0]["tau_int"] == pytest.approx(1.2)


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
def test_live_stats_toolkit_end_to_end_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    import run_confornets_inference as confornets

    monkeypatch.setenv("BMS_STATS_TOOLKIT_REQUIRED", "1")

    # 1. Job correlation matrix
    designs = [
        SimpleNamespace(plddt=70.0 + i * 3.0, ptm=0.55 + i * 0.07, iptm=0.80 - i * 0.04, metrics=None)
        for i in range(6)
    ]
    corr = _compute_correlation_matrix_via_stats_toolkit(designs, ["plddt", "ptm", "iptm"])
    assert corr is not None
    assert corr["stats_backend"] == "stats_toolkit"
    assert corr["matrix"][0][1] == pytest.approx(1.0, abs=1e-4)
    assert corr["matrix"][0][2] == pytest.approx(-1.0, abs=1e-4)
    assert corr["adjusted_p_value_matrix"][0][1] < 0.001

    # 2. Conformational mapping rank stability (Spearman)
    items = {("t", idx): {(seed, "0"): float(10 - idx) for seed in ("1", "2", "3")} for idx in range(5)}
    stability, common, pairwise, excluded = _rank_stability(
        items,
        {seed: ["0"] for seed in ("1", "2", "3")},
        inner_minimum=1.0,
        outer_minimum=1.0,
        common_minimum=3,
    )
    assert stability == pytest.approx(1.0)
    assert len(common) == 5 and len(pairwise) == 3 and not excluded
    assert all(item["stats_backend"] == "stats_toolkit" for item in pairwise)

    # 3. ConforNets classical metric MDS
    dm = [
        [0.0, 3.0, 5.0, 4.0],
        [3.0, 0.0, 4.0, 5.0],
        [5.0, 4.0, 0.0, 3.0],
        [4.0, 5.0, 3.0, 0.0],
    ]
    coords = confornets._mds_coordinates(dm)
    for i in range(4):
        for j in range(4):
            dist = math.hypot(coords[i]["x"] - coords[j]["x"], coords[i]["y"] - coords[j]["y"])
            assert dist == pytest.approx(dm[i][j], abs=1e-4)

    # 4. MD convergence diagnostics
    reports = [{
        "replica": 0,
        "points": [
            {"time_ps": float(i * 10), "rmsd_angstrom": 1.5 + (0.04 if i % 2 == 0 else -0.04)}
            for i in range(20)
        ],
    }]
    diag = _compute_replica_convergence_diagnostics(reports)
    assert diag is not None
    assert diag["stats_backend"] == "stats_toolkit"
    assert diag["replicas"][0]["converged"] is True
