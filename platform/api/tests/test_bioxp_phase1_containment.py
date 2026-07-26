from __future__ import annotations

from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp
from routers.bioxp.dependencies import SAFE_LOCAL_MUTATIONS, require_bioxp_mutation_access
from services.bioxp.runtime import create_bioxp_runtime

def _client(tmp_path: Path) -> TestClient:
    runtime = create_bioxp_runtime(data_root=tmp_path)
    app = FastAPI()
    app.state.bioxp_runtime = runtime
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app)


def test_every_non_get_route_carries_the_global_guard() -> None:
    routes = list(bioxp.router.routes)
    non_get = [route for route in routes if route.methods and route.methods != {"GET"}]
    assert non_get
    assert len(routes) <= 21
    for route in non_get:
        assert any(dependency.dependency is require_bioxp_mutation_access for dependency in route.dependencies), f"missing mutation dependency: {route.path}"


def test_safe_local_mutations_are_exact_and_do_not_reach_robot() -> None:
    assert SAFE_LOCAL_MUTATIONS == frozenset({"/protocols/compile"})


def test_robot_command_routes_are_default_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    client = _client(tmp_path)
    command = client.post("/api/bioxp/commands", json={})
    emergency = client.post("/api/bioxp/emergency-stop", json={})
    assert command.status_code == 503
    assert emergency.status_code == 503


def test_only_offline_compile_bypasses_robot_mutation_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    client = _client(tmp_path)
    guarded = [
        client.put("/api/bioxp/profile", json={}),
        client.delete("/api/bioxp/profile"),
        client.post("/api/bioxp/connection/connect"),
        client.post("/api/bioxp/connection/disconnect"),
        client.post("/api/bioxp/connection/probe"),
        client.post("/api/bioxp/protocols/submit", json={}),
    ]
    assert all(response.status_code == 503 for response in guarded)
    assert client.post("/api/bioxp/protocols/compile", json={}).status_code != 503


def test_enabled_mutation_lane_reaches_closed_command_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    client = _client(tmp_path)
    response = client.post(
        "/api/bioxp/commands",
        json={
            "command": "initialize_motors",
            "expected_generation": 1,
            "idempotency_key": "phase1-token-test",
        },
    )
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"].lower()


def test_compact_sources_have_no_legacy_proxy_or_host_lifecycle_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in (root / "routers" / "bioxp", root / "services" / "bioxp")
        for path in sorted(directory.glob("*.py"))
    )
    forbidden = (
        "_GLOBAL_LINKAGE_URL",
        "_SESSION",
        "BMS_BIOXP_TRUSTED_NETWORKS",
        "BIOXP_LINKAGE_STATE_PATH",
        "subprocess",
        "paramiko",
        "systemctl",
        "journalctl",
        "robot-reboot",
        "runtime-reset",
        "proxy_request",
    )
    for marker in forbidden:
        assert marker not in sources


def test_current_docs_do_not_advertise_retired_bioxp_proxy_families() -> None:
    repo = Path(__file__).resolve().parents[3]
    current_docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            repo / "README.md",
            repo / "platform/api/README.md",
            repo / "platform/frontend/README.md",
            repo / "docs/README.md",
            repo / "docs/Platform_Overview.md",
            repo / "docs/Lab_Automation_MolBio_and_Sequencing.md",
            repo / "docs/BioXP_Compact_Control_Plane.md",
        )
    )
    for forbidden_phrase in (
        "cockpit/proxy surface",
        "robot-proxy routes",
        "liquid-handling route families",
        "reference-state and liquid-handling",
        "camera, vision, and protocol routes when linkage",
    ):
        assert forbidden_phrase not in current_docs


def test_historical_oem_docs_are_bannered_and_robot_host_is_redacted() -> None:
    repo = Path(__file__).resolve().parents[3]
    quarantine = (repo / "docs/oem/bioxp_phase3_robot_quarantine_gate_20260609.md").read_text(
        encoding="utf-8"
    )
    matrix = (repo / "docs/oem/bioxp_oem_source_to_target_matrix.md").read_text(encoding="utf-8")
    gripper = (repo / "docs/oem/bioxp_gripper_gap10_oem_harmonization_phase_plan_20260610.md").read_text(
        encoding="utf-8"
    )

    assert "100.124.140.56" not in quarantine
    assert "HISTORICAL EVIDENCE — NOT THE CURRENT BMS CONTRACT" in quarantine
    assert "SUPERSEDED — HISTORICAL DESIGN EVIDENCE ONLY" in matrix
    assert "SUPERSEDED — HISTORICAL PLAN ONLY" in gripper
    for text in (quarantine, matrix, gripper):
        assert "BioXP_Compact_Control_Plane.md" in text
