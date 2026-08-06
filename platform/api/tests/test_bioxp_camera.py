from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp
from services.bioxp.errors import ConnectionStateError


@dataclass
class FakeImage:
    content: bytes = b"jpeg-bytes"
    content_type: str = "image/jpeg"
    etag: str = '"' + "a" * 64 + '"'
    sha256: str = "a" * 64


class FakeCameraClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.frame_age_seconds = 0.25

    async def camera_status(self):
        self.calls.append("status")
        return {
            "schema_version": "bioxp.camera_status.v1",
            "available": True,
            "frame_sequence": 42,
            "frame_captured_at": "2026-07-27T12:00:00Z",
            "frame_age_seconds": self.frame_age_seconds,
            "freshness_budget_seconds": 2.0,
            "provider_generation": 7,
            "dropped_frames": 3,
            "content_sha256": "a" * 64,
            "detail": None,
        }

    async def camera_latest(self):
        self.calls.append("latest")
        return FakeImage()

    async def camera_snapshot(self):
        self.calls.append("snapshot")
        return FakeImage(content=b"snapshot-jpeg")


class FakeConnection:
    def __init__(self, *, active: bool = True, generation: int = 77) -> None:
        self.active = active
        self.generation = generation
        self.client = FakeCameraClient() if active else None
        self.lease_entries: list[tuple[int, bool]] = []

    @asynccontextmanager
    async def active_query_lease(self, *, expected_generation: int, require_fresh: bool = True):
        if not self.active or self.client is None:
            raise ConnectionStateError("BioXP saved profile is not actively connected")
        if expected_generation != self.generation:
            raise ConnectionStateError("Expected connection generation does not match the active generation")
        self.lease_entries.append((expected_generation, require_fresh))
        yield self.client


def make_client(monkeypatch, *, active: bool = True, mutations: bool = True):
    if mutations:
        monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    else:
        monkeypatch.delenv("BMS_BIOXP_MUTATIONS_ENABLED", raising=False)
    connection = FakeConnection(active=active)
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=connection)
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app), connection


def test_camera_status_is_generation_bound_and_projects_freshness(monkeypatch):
    client, connection = make_client(monkeypatch)

    response = client.get("/api/bioxp/camera/status", params={"expected_generation": 77})

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "bioxp.camera_status.v1",
        "state": "live",
        "available": True,
        "frame_sequence": 42,
        "frame_captured_at": "2026-07-27T12:00:00Z",
        "frame_age_seconds": 0.25,
        "freshness_budget_seconds": 2.0,
        "provider_generation": 7,
        "dropped_frames": 3,
        "content_sha256": "a" * 64,
        "detail": None,
        "connection_generation": 77,
    }
    assert connection.lease_entries == [(77, False)]
    assert connection.client.calls == ["status"]
    assert "robot" not in response.text.lower()


def test_camera_status_projects_provider_owned_aged_frame_as_stale(monkeypatch):
    client, connection = make_client(monkeypatch)
    assert connection.client is not None
    connection.client.frame_age_seconds = 2.001

    response = client.get("/api/bioxp/camera/status", params={"expected_generation": 77})

    assert response.status_code == 200
    assert response.json()["state"] == "stale"
    assert response.json()["available"] is True
    assert response.json()["frame_sequence"] == 42
    assert response.json()["content_sha256"] == "a" * 64


def test_camera_generation_change_and_no_active_target_fail_before_robot_io(monkeypatch):
    client, connection = make_client(monkeypatch)

    changed = client.get("/api/bioxp/camera/status", params={"expected_generation": 76})
    assert changed.status_code == 409
    assert connection.client.calls == []

    disconnected, disconnected_connection = make_client(monkeypatch, active=False)
    unavailable = disconnected.get("/api/bioxp/camera/status", params={"expected_generation": 77})
    assert unavailable.status_code == 409
    assert "actively connected" in unavailable.json()["detail"]
    assert disconnected_connection.lease_entries == []


def test_camera_latest_and_snapshot_preserve_validated_image_metadata(monkeypatch):
    client, connection = make_client(monkeypatch)

    latest = client.get("/api/bioxp/camera/frame/latest", params={"expected_generation": 77})
    snapshot = client.post("/api/bioxp/camera/snapshot", json={"expected_generation": 77})

    assert latest.status_code == 200
    assert latest.content == b"jpeg-bytes"
    assert latest.headers["content-type"] == "image/jpeg"
    assert latest.headers["etag"] == '"' + "a" * 64 + '"'
    assert latest.headers["x-content-sha256"] == "a" * 64
    assert latest.headers["x-bioxp-connection-generation"] == "77"
    assert snapshot.status_code == 200
    assert snapshot.content == b"snapshot-jpeg"
    assert connection.client.calls == ["latest", "snapshot"]
    assert connection.lease_entries == [(77, False), (77, False)]


def test_camera_snapshot_obeys_existing_mutation_guard(monkeypatch):
    client, connection = make_client(monkeypatch, mutations=False)

    response = client.post("/api/bioxp/camera/snapshot", json={"expected_generation": 77})

    assert response.status_code == 503
    assert connection.client.calls == []


def test_camera_surface_rejects_raw_camera_parameters_and_has_finite_allowlist(monkeypatch):
    client, connection = make_client(monkeypatch)

    for name in ("path", "device", "cid", "xu", "output_path", "robot_url"):
        response = client.get(
            "/api/bioxp/camera/frame/latest",
            params={"expected_generation": 77, name: "unsafe"},
        )
        assert response.status_code == 422, (name, response.text)
    rejected_body = client.post(
        "/api/bioxp/camera/snapshot",
        json={"expected_generation": 77, "output_path": "/tmp/frame.jpg"},
    )
    assert rejected_body.status_code == 422
    assert connection.client.calls == []

    schema = client.get("/openapi.json").json()
    camera_paths = {path for path in schema["paths"] if "/camera/" in path}
    assert camera_paths == {
        "/api/bioxp/camera/status",
        "/api/bioxp/camera/frame/latest",
        "/api/bioxp/camera/snapshot",
    }
    camera_schema = str({path: schema["paths"][path] for path in camera_paths}).lower()
    for forbidden in ("device", "cid", "xu", "output_path", "robot_url", "calibration", "inspect", "oem/check"):
        assert forbidden not in camera_schema
