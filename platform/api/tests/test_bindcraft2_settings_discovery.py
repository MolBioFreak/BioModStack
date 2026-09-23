"""BC2 inventory discovery does not itself register an executable model."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from model_registry import get_registry
from routers.models import router as models_router
from services.bindcraft2_typed import schema
from services.bindcraft2_native import PIN


def test_native_settings_discovery_uses_same_pinned_contract_as_typed_adapter():
    app = FastAPI()
    app.include_router(models_router, prefix="/api/models")
    client = TestClient(app)

    response = client.get("/api/models/bindcraft2/native-settings")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["model_id"] == "bindcraft2"
    assert data["settings"] == schema()
    assert data["settings"]["upstream_commit"] == PIN
    assert "max_trajectories" in data["settings"]["fields"]
    assert data["launch_available"] is (get_registry().get_model("bindcraft2") is not None)

    # Discovery cannot be mistaken for a write/launch endpoint.
    assert client.post("/api/models/bindcraft2/native-settings", json={}).status_code == 405
    assert {m["id"] for m in client.get("/api/models").json()} == {
        m.id for m in get_registry().list_models() if not m.experimental
    }
