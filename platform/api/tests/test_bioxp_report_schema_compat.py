"""Database schema metadata is independent of the unchanged report protocol."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.bioxp.operator_models import OperatorReportExportMetadataV1
from tests.test_bioxp_operator_reports import EXPORT_RECEIPT, FakeConnection, app_for


class MetadataConnection(FakeConnection):
    async def request_active_query(self, *args, **kwargs):
        payload = deepcopy(await super().request_active_query(*args, **kwargs))
        self.mutate(payload)
        return payload


@pytest.mark.parametrize("version", [5, 14])
def test_database_schema_metadata_roundtrips_without_receipt_changes(version):
    connection = MetadataConnection()
    def mutate(payload):
        for field in ("receipt", "snapshot"):
            payload[field]["schema_identity"]["schema_version"] = version
    connection.mutate = mutate
    response = TestClient(app_for(connection)).get("/operator-controls/reports/exports/export-1")
    assert response.status_code == 200, response.text
    expected = deepcopy(EXPORT_RECEIPT)
    expected["schema_identity"]["schema_version"] = version
    assert response.json()["receipt"] == expected
    assert response.json()["snapshot"] == expected


@pytest.mark.parametrize("version", [True, False, "14", 14.0, None, 0, -1, {}, [], "bioxp.operator_report_export.v14"])
def test_malformed_database_version_retains_502(version):
    connection = MetadataConnection()
    def mutate(payload):
        for field in ("receipt", "snapshot"):
            payload[field]["schema_identity"]["schema_version"] = version
    connection.mutate = mutate
    response = TestClient(app_for(connection)).get("/operator-controls/reports/exports/export-1")
    assert response.status_code == 502
    assert response.json() == {"detail": "BioXP robot returned an invalid operator-control contract"}


@pytest.mark.parametrize("case", ["missing", "unknown", "database_identity", "digest", "row_count", "legal_hold", "identity_version", "filter_channel"])
def test_schema14_keeps_other_metadata_validation(case):
    connection = MetadataConnection()
    def mutate(payload):
        for field in ("receipt", "snapshot"):
            receipt = payload[field]
            receipt["schema_identity"]["schema_version"] = 14
            if case == "missing":
                del receipt["schema_identity"]["schema_version"]
            elif case == "unknown":
                receipt["schema_identity"]["invented_authority"] = True
            elif case == "database_identity":
                receipt["schema_identity"]["database_identity"] = "invented"
            elif case == "digest":
                receipt["filter_sha256"] = "bad"
            elif case == "row_count":
                receipt["row_count"] = -1
            elif case == "legal_hold":
                receipt["legal_hold"] = "false"
            elif case == "identity_version":
                receipt["schema_identity"]["identity_version"] = True
            elif case == "filter_channel":
                receipt["normalized_filters"]["channel"] = 4
    connection.mutate = mutate
    response = TestClient(app_for(connection)).get("/operator-controls/reports/exports/export-1")
    assert response.status_code == 502


def test_actual_robot_new_export_and_metadata_http_readback(tmp_path, monkeypatch):
    """Real report publication on a writable COPY of the retained robot DB.

    No robot lifespan/hardware, no fabricated producer receipts, no original DB writes.
    The only transport double connects the two actual HTTP owners in-process.
    """
    source = os.environ.get("BIOXP_TEST_SOURCE")
    captured = os.environ.get("BIOXP_REPORT_CAPTURED_DB")
    if not source or not captured:
        pytest.skip("requires current robot source and retained DB capture")
    monkeypatch.syspath_prepend(str(Path(source) / "src"))
    from bioxp import runtime_audit_store
    from bioxp.operator_receipt_store import OperatorReceiptStore
    from bioxp.operator_reports import create_operator_reports_router

    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "bioxp_runtime.db"
    with sqlite3.connect(Path(captured).resolve().as_uri() + "?mode=ro", uri=True) as original:
        assert original.execute("PRAGMA user_version").fetchone()[0] == 14
        with sqlite3.connect(target) as copied:
            original.backup(copied)
    for name in runtime_audit_store.RUNTIME_ROOT_ENV_NAMES:
        monkeypatch.setenv(name, str(root))
    monkeypatch.setattr(runtime_audit_store, "CANONICAL_RUNTIME_ROOT", root)
    store = OperatorReceiptStore(root)
    robot_app = FastAPI()
    robot_app.include_router(create_operator_reports_router(store))
    robot = TestClient(robot_app)

    class HttpConnection:
        generation = 9
        calls = []
        async def request_active(self, name, **kwargs):
            assert name == "operator_report_export_create"
            assert kwargs["expected_generation"] == 9
            self.calls.append(name)
            result = robot.post("/operator/reports/exports", json=kwargs["json_data"])
            assert result.status_code == 200, result.text
            self.created = result.json()
            return self.created
        async def request_active_query(self, name, **kwargs):
            assert name == "operator_report_export_detail"
            assert kwargs["expected_generation"] == 9
            self.calls.append(name)
            result = robot.get("/operator/reports/exports/" + kwargs["path_params"]["export_id"])
            assert result.status_code == 200, result.text
            self.metadata = result.json()
            return self.metadata
        async def request_active_bytes(self, name, **kwargs):
            assert name == "operator_report_export_download"
            assert kwargs["expected_generation"] == 9
            self.calls.append(name)
            result = robot.get("/operator/reports/exports/" + kwargs["path_params"]["export_id"] + "/download")
            assert result.status_code == 200, result.text
            self.download = result.content
            return SimpleNamespace(content=result.content, content_type="application/json", sha256=hashlib.sha256(result.content).hexdigest())

    connection = HttpConnection()
    try:
        with TestClient(app_for(connection)) as client:
            created = client.post("/operator-controls/reports/exports", json={"format": "json", "limit": 100})
            assert created.status_code == 200, created.text
            eid = created.json()["export_id"]
            metadata = client.get("/operator-controls/reports/exports/" + eid)
            assert metadata.status_code == 200, metadata.text
            raw = connection.metadata
            OperatorReportExportMetadataV1.model_validate(raw)
            assert raw["receipt"]["schema_identity"]["schema_version"] == 14
            assert raw["receipt"]["receipt_schema"] == "bioxp.operator_report_export_receipt.v1"
            for key in raw:
                if key != "download":
                    assert metadata.json()[key] == raw[key]
            download = client.get("/operator-controls/reports/exports/" + eid + "/download")
            assert download.status_code == 200
            assert download.content == connection.download
            assert hashlib.sha256(download.content).hexdigest() == raw["sha256"] == raw["receipt"]["artifact"]["sha256"]
            assert len(download.content) == raw["byte_count"]
            envelope = json.loads(download.content)
            assert envelope["schema_version"] == "bioxp.operator_report_export.v1"
            assert envelope["snapshot"]["schema_identity"] == raw["receipt"]["schema_identity"]
            # The retained DB capture can contain zero indexed commands; an empty
            # export is still a real publication, not fabricated execution evidence.
            assert len(envelope["commands"]) == raw["row_count"] == raw["receipt"]["row_count"]
            assert raw["receipt"]["retention_deadline"] > raw["receipt"]["created_at"]
            assert connection.calls == ["operator_report_export_create", "operator_report_export_detail", "operator_report_export_download"]
            output = os.environ.get("BMS_REPORT_COMPAT_EXPORT")
            if output:
                destination = Path(output)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "new-producer-metadata.json").write_text(json.dumps(raw, indent=2))
                (destination / "new-relay-metadata.json").write_text(metadata.text)
                (destination / "new-producer-download.json").write_bytes(download.content)
    finally:
        robot.close()
        store._audit_database.close()
