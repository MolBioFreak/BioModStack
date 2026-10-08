"""Receive the current mounted canvas exports through real HTTP/store/compiler owners.

No scientific reconstruction: caller supplies the exact UI-produced requests/results.
Run with BIOXP_CANVAS_THERMAL_EXPORT and/or BIOXP_WHOLE_UI_EXPORT from the mounted
frontend suites. Isolated SQLite and an inert native boundary come from the existing
API fixture. Physical execution and native controller qualification are not claimed.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3

import pytest

from test_bioxp_methods_api import BASE, create, store


def _cases():
    result = []
    for key in ("BIOXP_CANVAS_THERMAL_EXPORT", "BIOXP_WHOLE_UI_EXPORT"):
        if path := os.environ.get(key):
            rows = json.loads(Path(path).read_text())
            assert isinstance(rows, list) and rows, key
            result.extend((row.get("request", row.get("input")), row["result"]) for row in rows)
    if path := os.environ.get("BIOXP_CANVAS_UI_EXPORT"):
        row = json.loads(Path(path).read_text())
        result.extend(({"method": row[key], "bindings": {}, "dependencies": {}}, row[compiled]) for key, compiled in (("original", "before"), ("edited", "after")))
    return result


@pytest.mark.asyncio
async def test_exact_canvas_payloads_cold_database_revision_and_real_compiler(store, monkeypatch):
    cases = _cases()
    if not cases:
        pytest.skip("Export actual mounted canvas inputs first; no reconstructed UI fixture")
    client, native, path, _ = store
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    receipts = []
    for index, (request, expected) in enumerate(cases):
        assert isinstance(request, dict) and expected["document"], expected.get("issues")
        # Independent mounted scenarios can use equal method names. Give only the
        # library envelope a unique name; never alter the captured method bytes.
        saved = await create(client, request["method"], name=f"Canvas receiving case {index}")
        exact_url = f"{BASE}/library/{saved['id']}/revisions/1"
        exact = (await client.get(exact_url)).json()
        assert exact["method"] == request["method"]
        # Independent SQLite connection, not a reused ORM object or browser cache.
        with sqlite3.connect(path) as database:
            persisted = database.execute("SELECT params FROM user_templates WHERE id=?", (saved["id"],)).fetchone()
            assert persisted and json.loads(persisted[0]) == request["method"]
        response = await client.post(BASE + "/compile", json={**request, "method": exact["method"]})
        assert response.status_code == 200, response.text
        actual = response.json()
        assert actual == expected
        updated = deepcopy(exact["method"])
        updated.setdefault("editor_state", {})["receiving_note"] = {"raw": "000.1250", "null": None, "blank": ""}
        revision = await client.put(BASE + "/library/" + saved["id"], json={"method": updated, "name": saved["name"], "expected_base_revision": 1})
        assert revision.status_code == 200, revision.text
        assert (await client.get(exact_url)).json() == exact
        assert (await client.get(f"{BASE}/library/{saved['id']}/revisions/2")).json()["method"] == updated
        receipts.append({"id": saved["id"], "request": request, "received": actual, "cold_store_equal": True})
    assert not native.calls
    if output := os.environ.get("BIOXP_CANVAS_RECEIVING_EXPORT"):
        Path(output).write_text(json.dumps({"cases": receipts, "native_calls": native.calls}, indent=2))
