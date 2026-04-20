from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import system


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    return TestClient(app)


def test_runtime_state_endpoint_returns_runtime_descriptor(monkeypatch) -> None:
    descriptor = {"runtime_mode": "container", "paths": {"data_root": "/srv/biomodstack"}}
    monkeypatch.setattr(system, "runtime_descriptor", lambda project_root=None, runtime_mode=None: descriptor, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state", params={"runtime": "container"})

    assert response.status_code == 200
    assert response.json() == descriptor


def test_runtime_start_endpoint_invokes_service_layer(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(system, "start_all", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"), raising=False)
    monkeypatch.setattr(
        system,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {"runtime_mode": runtime_mode or "dev", "runtime_active": True},
        raising=False,
    )

    with build_client() as client:
        response = client.post("/api/system/runtime/start", params={"runtime": "container"})

    assert response.status_code == 200
    assert started == ["container"]
    assert response.json()["runtime_mode"] == "container"
    assert response.json()["runtime_active"] is True


def test_install_profile_put_persists_and_returns_snapshot(monkeypatch) -> None:
    saved_payloads: list[dict[str, object]] = []

    def fake_save_install_profile(payload: dict[str, object]) -> dict[str, object]:
        saved_payloads.append(payload)
        return {"data_root": "/srv/biomodstack"}

    snapshot = {
        "profile_path": "/home/christian/.config/biomodstack/install_profile.json",
        "compat_env_path": "/home/christian/.biomodstack/env.sh",
        "core_runtime_env_path": "/home/christian/.config/biomodstack/core-runtime.env",
        "profile": {"data_root": "/srv/biomodstack"},
        "resolved": {"data_root": "/srv/biomodstack", "db_path": "/srv/biomodstack/biomodstack.db"},
    }
    monkeypatch.setattr(system, "save_install_profile", fake_save_install_profile, raising=False)
    monkeypatch.setattr(system, "install_profile_snapshot", lambda profile=None: snapshot, raising=False)

    with build_client() as client:
        response = client.put("/api/system/install-profile", json={"data_root": "/srv/biomodstack"})

    assert response.status_code == 200
    assert saved_payloads == [{"data_root": "/srv/biomodstack"}]
    assert response.json() == snapshot