"""Database schema metadata is independent of the unchanged report protocol."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

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
