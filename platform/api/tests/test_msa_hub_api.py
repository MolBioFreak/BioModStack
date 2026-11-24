"""MSA hub: provider credential store, credential routes and the local_msa switch."""
import json
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import msa
from services import msa_provider_credentials as store
from services import msa_provider_setup, msa_server

SECRET = "ns-test-key-0123456789abcdef"


@pytest.fixture()
def hub(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("BMS_MSA_API_STATE_ROOT", str(tmp_path / "state"))
    for name in ("BMS_NEUROSNAP_API_KEY_FILE", "BMS_FEATURE_LOCAL_MSA", "BMS_MSA_CONTROLLER_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(msa_provider_setup, "cache_root", lambda: tmp_path / "cache")
    app = FastAPI()
    app.include_router(msa.router)
    return TestClient(app)


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_store_round_trip_is_private(hub, tmp_path):
    assert store.credential_state("neurosnap_api") == {"configured": False, "source": None}
    assert store.credential_file("neurosnap_api") is None
    store.save_credential("neurosnap_api", SECRET)
    path = tmp_path / "config" / "biomodstack" / "msa-providers" / "neurosnap_api.key"
    assert path.read_text() == SECRET
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert [item.name for item in path.parent.iterdir()] == ["neurosnap_api.key"]
    assert store.credential_state("neurosnap_api") == {"configured": True, "source": "saved"}
    assert store.clear_credential("neurosnap_api") is True
    assert not path.exists()
    assert store.clear_credential("neurosnap_api") is False


def test_store_rejects_bad_values(hub):
    with pytest.raises(store.CredentialError):
        store.save_credential("neurosnap_api", "   ")
    with pytest.raises(store.CredentialError):
        store.save_credential("neurosnap_api", "two words")
    with pytest.raises(store.CredentialError):
        store.save_credential("colabfold_api", SECRET)
    with pytest.raises(store.CredentialError):
        store.save_credential("neurosnap_api", SECRET, endpoint="https://example.org")
    with pytest.raises(KeyError):
        store.save_credential("nope", SECRET)
    assert store.credential_state("neurosnap_api")["configured"] is False


def test_reader_picks_up_saved_key_and_environment_wins(hub, monkeypatch, tmp_path):
    from biomodstack_msa_api import _credential
    store.save_credential("neurosnap_api", SECRET)
    saved = msa_provider_setup.credential_file()
    assert saved == store.saved_key_path("neurosnap_api")
    assert _credential(saved) == SECRET
    assert msa_provider_setup._credential_configured() is True

    other = tmp_path / "deployment.key"
    other.write_text("deployment-owned-key")
    other.chmod(0o600)
    monkeypatch.setenv("BMS_NEUROSNAP_API_KEY_FILE", str(other))
    assert msa_provider_setup.credential_file() == other
    assert store.credential_state("neurosnap_api") == {"configured": True, "source": "environment"}
    other.unlink()
    assert store.credential_state("neurosnap_api") == {"configured": False, "source": "environment"}


def test_routes_never_return_the_key(hub):
    listing = hub.get("/api/msa/providers")
    assert listing.status_code == 200
    items = {item["id"]: item for item in listing.json()["items"]}
    assert list(items) == ["colabfold_api", "neurosnap_api"]
    assert items["colabfold_api"]["needs_key"] is False
    assert items["neurosnap_api"] == {
        "id": "neurosnap_api", "label": "Neurosnap API", "needs_key": True, "configured": False,
        "source": None, "endpoint_configurable": False, "needs_controller": False,
        "controller_enabled": False, "ready": False,
        "message": items["neurosnap_api"]["message"],
    }
    assert "Neurosnap API key" in items["neurosnap_api"]["message"]

    saved = hub.put("/api/msa/providers/neurosnap_api/credential", json={"api_key": SECRET})
    assert saved.status_code == 200
    assert saved.json()["provider"]["configured"] is True
    assert saved.json()["provider"]["source"] == "saved"
    after = hub.get("/api/msa/providers")
    assert {item["id"]: item["ready"] for item in after.json()["items"]}["neurosnap_api"] is True
    cleared = hub.delete("/api/msa/providers/neurosnap_api/credential")
    assert cleared.status_code == 200
    assert cleared.json()["removed"] is True
    assert cleared.json()["provider"]["configured"] is False
    for response in (listing, saved, after, cleared):
        assert SECRET not in response.text
        assert "api_key" not in json.dumps(response.json())


def test_rejections_do_not_echo_or_save(hub):
    assert hub.put("/api/msa/providers/nope/credential", json={"api_key": SECRET}).status_code == 404
    assert hub.delete("/api/msa/providers/nope/credential").status_code == 404
    for body in ({"api_key": ""}, {"api_key": "   "}, {}, {"api_key": 5}):
        response = hub.put("/api/msa/providers/neurosnap_api/credential", json=body)
        assert response.status_code == 422
    bad = hub.put("/api/msa/providers/neurosnap_api/credential", json={"api_key": SECRET + " x"})
    assert bad.status_code == 422
    assert SECRET not in bad.text
    keyless = hub.put("/api/msa/providers/colabfold_api/credential", json={"api_key": SECRET})
    assert keyless.status_code == 422
    assert SECRET not in keyless.text
    assert store.credential_state("neurosnap_api")["configured"] is False


def test_remote_clients_cannot_change_credentials(hub, monkeypatch):
    store.save_credential("neurosnap_api", SECRET)
    monkeypatch.setattr(msa, "LOCAL_ADMIN_HOSTS", {"127.0.0.1"})
    assert hub.put("/api/msa/providers/neurosnap_api/credential", json={"api_key": "other"}).status_code == 403
    assert hub.delete("/api/msa/providers/neurosnap_api/credential").status_code == 403
    assert hub.get("/api/msa/providers").status_code == 200
    assert store.saved_key_path("neurosnap_api").read_text() == SECRET


def test_local_start_refused_when_feature_off(hub, monkeypatch):
    monkeypatch.setattr(msa, "ensure_server_for_db", lambda **_: pytest.fail("local server started"))
    assert msa_server.local_msa_enabled() is False
    response = hub.post("/api/msa/server/start", json={})
    assert response.status_code == 422
    assert response.json()["detail"] == "Local MSA is not enabled for this installation"
    with pytest.raises(ValueError, match="Local MSA is not enabled for this installation"):
        msa_server.ensure_server_for_db("uniref", 0)


def test_local_start_allowed_when_feature_on(hub, monkeypatch):
    monkeypatch.setenv("BMS_FEATURE_LOCAL_MSA", "1")
    started = []
    monkeypatch.setattr(msa, "read_server_settings", lambda: {})
    monkeypatch.setattr(msa, "resolve_msa_gpu_id", lambda requested: 0)
    monkeypatch.setattr(msa, "touch_query_activity", lambda metadata=None: {})
    monkeypatch.setattr(msa, "ensure_server_for_db",
                        lambda **kwargs: started.append(kwargs["db_alias"]) or {"db_alias": kwargs["db_alias"]})
    assert msa_server.local_msa_enabled() is True
    response = hub.post("/api/msa/server/start", json={})
    assert response.status_code == 200, response.text
    assert started == ["uniref"]
    msa_server.require_local_msa_enabled()


def test_colabfold_is_enabled_per_computer_from_the_hub(hub, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    path = tmp_path / "config" / "biomodstack" / "msa-controller.json"
    row = lambda: next(i for i in hub.get("/api/msa/providers").json()["items"] if i["id"] == "colabfold_api")
    assert row()["needs_controller"] is True and row()["controller_enabled"] is False and row()["ready"] is False
    assert "MSA hub" in row()["message"]
    assert hub.put("/api/msa/providers/neurosnap_api/controller").status_code == 422
    assert hub.put("/api/msa/providers/nope/controller").status_code == 404
    response = hub.put("/api/msa/providers/colabfold_api/controller")
    assert response.status_code == 200 and response.json()["provider"]["controller_enabled"] is True
    written = json.loads(path.read_text())
    assert written["machine_id"] == Path("/etc/machine-id").read_text().strip()
    assert written["state_dir"] == str((tmp_path / "xdg-state").resolve() / "biomodstack" / "msa-controller")
    assert _mode(path) == 0o600 and row()["ready"] is True
    assert not Path(written["state_dir"]).exists()
    # A declaration copied from another computer is off here and is rebound by repeating the action.
    path.write_text(json.dumps({**written, "machine_id": "another-computer", "egress_identity": "kept-label"}))
    assert row()["controller_enabled"] is False
    assert hub.put("/api/msa/providers/colabfold_api/controller").status_code == 200
    rebound = json.loads(path.read_text())
    assert rebound["machine_id"] == written["machine_id"] and rebound["egress_identity"] == "kept-label"
    assert hub.delete("/api/msa/providers/colabfold_api/controller").json()["removed"] is True
    assert not path.exists() and row()["controller_enabled"] is False


def test_controller_switch_is_local_only(hub, monkeypatch):
    monkeypatch.setattr(msa, "LOCAL_ADMIN_HOSTS", {"127.0.0.1"})
    assert hub.put("/api/msa/providers/colabfold_api/controller").status_code == 403
    assert hub.delete("/api/msa/providers/colabfold_api/controller").status_code == 403
