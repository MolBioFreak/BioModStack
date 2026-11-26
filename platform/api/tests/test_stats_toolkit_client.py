"""Unit tests for BMS Stats Toolkit client and standalone script RPC helper."""

from __future__ import annotations

import io
import json
import urllib.error

import httpx
import pytest

from services.stats_toolkit_client import (
    StatsToolkitClient,
    StatsToolkitNotFoundError,
    StatsToolkitUnavailableError,
    StatsToolkitValidationError,
)


def test_client_execute_tool_unwraps_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/tools/inference.binomial_exact/execute"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"successes": 18, "trials": 100, "null_probability": 0.1}
        return httpx.Response(
            200,
            json={"tool_id": "inference.binomial_exact", "result": {"p_value": 0.014, "fold_enrichment": 1.8}},
        )

    client = StatsToolkitClient(base_url="http://stats.test", transport=httpx.MockTransport(handler))
    res = client.execute_tool("inference.binomial_exact", {"successes": 18, "trials": 100, "null_probability": 0.1})
    assert res == {"p_value": 0.014, "fold_enrichment": 1.8}


def test_client_maps_422_404_503_and_connect_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bad/execute"):
            return httpx.Response(422, json={"detail": "invalid input"})
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"detail": "run not found"})
        if request.url.path.endswith("/health/ready"):
            return httpx.Response(503, json={"detail": "not ready"})
        raise httpx.ConnectError("connection refused", request=request)

    client = StatsToolkitClient(base_url="http://stats.test", transport=httpx.MockTransport(handler))
    with pytest.raises(StatsToolkitValidationError, match="invalid input"):
        client.execute_tool("bad", {})
    with pytest.raises(StatsToolkitNotFoundError, match="run not found"):
        client.get_run("missing")
    with pytest.raises(StatsToolkitUnavailableError, match="503"):
        client.health_ready()
    with pytest.raises(StatsToolkitUnavailableError, match="unavailable"):
        client.list_capabilities()


def test_client_create_run_and_list_tools() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runs":
            return httpx.Response(200, json={"run_id": "run_123", "status": "completed"})
        if request.url.path == "/api/v1/tools":
            return httpx.Response(200, json={"tools": [{"id": "inference.binomial_exact"}]})
        return httpx.Response(404)

    client = StatsToolkitClient(base_url="http://stats.test", transport=httpx.MockTransport(handler))
    run = client.create_run("default", "inference.binomial_exact", {"successes": 5, "trials": 10, "null_probability": 0.5})
    assert run["run_id"] == "run_123"
    tools = client.list_tools()
    assert len(tools["tools"]) == 1
