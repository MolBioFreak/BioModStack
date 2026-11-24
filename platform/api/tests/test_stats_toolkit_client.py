"""Unit tests for BMS Stats Toolkit client and standalone script RPC helper."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

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


def test_stdlib_rpc_helper_roundtrip_and_errors() -> None:
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts._stats_toolkit_rpc import StatsToolkitRpcError, execute_stats_tool

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            if self.path == "/api/v1/tools/inference.binomial_exact/execute":
                payload = json.dumps({"result": {"p_value": 0.05}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(422)
            self.end_headers()
            self.wfile.write(b'{"detail": "bad request"}')

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        res = execute_stats_tool(
            "inference.binomial_exact",
            {"successes": 5, "trials": 10, "null_probability": 0.5},
            base_url=f"http://127.0.0.1:{port}",
        )
        assert res == {"p_value": 0.05}
        with pytest.raises(StatsToolkitRpcError) as exc_info:
            execute_stats_tool("bad.tool", {}, base_url=f"http://127.0.0.1:{port}")
        assert exc_info.value.status_code == 422
    finally:
        server.shutdown()
        server.server_close()
