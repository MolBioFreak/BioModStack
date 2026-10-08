from __future__ import annotations

import base64
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import molbio_restriction
from services.restriction_catalog import CatalogAuthority, CatalogUnavailable

API_ROOT = Path(__file__).resolve().parents[1]
CATALOG = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_v1.json"
MANIFEST = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
SCHEMA = API_ROOT.parents[1] / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json"


def _client(authority: CatalogAuthority | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    if authority is not None:
        app.dependency_overrides[molbio_restriction.get_catalog_authority] = lambda: authority
    return TestClient(app)


def _authority() -> CatalogAuthority:
    return CatalogAuthority(CATALOG, MANIFEST, SCHEMA)


def test_catalog_page_publishes_exact_receipt_bounds_and_historical_notice() -> None:
    response = _client(_authority()).get("/api/molbio/restriction/catalog?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "bms.molbio.restriction-catalog-page.v1"
    assert body["catalog"]["catalog_id"] == "biopython-rebase-404-bms-v1"
    assert body["catalog"]["catalog_sha256"] == "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f"
    assert body["catalog"]["source_release"] == "REBASE_EMBOSS_404_2024"
    assert body["catalog"]["counts"] == {
        "total": 1092,
        "geometry_ready": 754,
        "commercial_geometry_ready": 623,
        "unknown_geometry": 334,
        "nicking": 4,
        "two_event_double_strand": 25,
    }
    assert body["catalog"]["bounds"] == {"default_limit": 50, "maximum_limit": 250, "query_max_length": 128}
    assert body["catalog"]["source_year"] == 2024
    assert "historical" in body["catalog"]["supplier_code_notice"].lower()
    assert "current" not in body["catalog"]["supplier_code_notice"].lower().replace("not current", "")
    assert [row["canonical_name"] for row in body["items"]] == ["AanI", "AarI"]
    assert body["next_cursor"]


def test_search_and_every_catalog_filter_are_deterministic() -> None:
    client = _client(_authority())
    cases = {
        "query=EcoRI": lambda row: "ecori" in row["canonical_name"].casefold(),
        "query=GAATTC": lambda row: "GAATTC" in row["recognition"]["site_alternatives_iupac"],
        "geometry_status=unknown": lambda row: row["cleavage"]["status"] == "unknown",
        "commercial=not_reported": lambda row: not row["supplier_provenance"]["reported_commercial"],
        "supplier_code=N": lambda row: "N" in row["supplier_provenance"]["historical_supplier_codes"],
        "enzyme_kind=nicking_endonuclease": lambda row: row["enzyme_kind"] == "nicking_endonuclease",
        "overhang_kind=five_prime": lambda row: any(event["overhang_kind"] == "five_prime" for event in row["cleavage"]["events"]),
        "palindromic=false": lambda row: row["recognition"]["palindromic"] is False,
    }
    for query, predicate in cases.items():
        response = client.get(f"/api/molbio/restriction/catalog?{query}&limit=25")
        assert response.status_code == 200, (query, response.text)
        rows = response.json()["items"]
        assert rows, query
        assert all(predicate(row) for row in rows), query
        assert [(r["canonical_name"].casefold(), r["enzyme_id"].casefold()) for r in rows] == sorted(
            (r["canonical_name"].casefold(), r["enzyme_id"].casefold()) for r in rows
        )


def test_keyset_cursor_is_bounded_and_rejects_malformed_cross_query_and_stale() -> None:
    client = _client(_authority())
    first = client.get("/api/molbio/restriction/catalog?query=A&limit=2").json()
    cursor = first["next_cursor"]
    second = client.get("/api/molbio/restriction/catalog", params={"query": "A", "limit": 2, "cursor": cursor})
    assert second.status_code == 200
    assert {r["enzyme_id"] for r in first["items"]}.isdisjoint(r["enzyme_id"] for r in second.json()["items"])

    for params in (
        {"query": "A", "limit": 2, "cursor": "not-a-cursor"},
        {"query": "B", "limit": 2, "cursor": cursor},
        {"query": "A", "limit": 3, "cursor": cursor},
    ):
        response = client.get("/api/molbio/restriction/catalog", params=params)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_cursor"

    payload, signature = cursor.split(".")
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    stale_payload = base64.urlsafe_b64encode(decoded.replace("e9a1e9ec", "00000000").encode()).decode().rstrip("=")
    response = client.get("/api/molbio/restriction/catalog", params={"query": "A", "limit": 2, "cursor": f"{stale_payload}.{signature}"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_cursor"


def test_detail_returns_complete_record_and_exact_receipt_or_stable_404() -> None:
    client = _client(_authority())
    found = client.get("/api/molbio/restriction/catalog/EcoRI")
    assert found.status_code == 200
    body = found.json()
    assert body["schema"] == "bms.molbio.restriction-catalog-record.v1"
    assert body["record"]["canonical_name"] == "EcoRI"
    assert body["record"]["cleavage"]["events"] == [{"top_offset": 1, "bottom_offset": 5, "overhang_kind": "five_prime", "overhang_length_nt": 4}]
    assert body["catalog"]["catalog_sha256"] == "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f"

    missing = client.get("/api/molbio/restriction/catalog/not-an-enzyme")
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "enzyme_not_found", "message": "restriction enzyme was not found"}}


def test_unknown_or_invalid_query_values_have_stable_json_4xx() -> None:
    client = _client(_authority())
    paths = [
        "/api/molbio/restriction/catalog?unknown=x",
        "/api/molbio/restriction/catalog?geometry_status=maybe",
        "/api/molbio/restriction/catalog?commercial=maybe",
        "/api/molbio/restriction/catalog?limit=0",
        "/api/molbio/restriction/catalog?limit=251",
        "/api/molbio/restriction/catalog?limit=nope",
        "/api/molbio/restriction/catalog?query=" + "x" * 129,
        "/api/molbio/restriction/catalog?supplier_code=bad%20code",
        "/api/molbio/restriction/catalog?limit=1&limit=2",
    ]
    for path in paths:
        response = client.get(path)
        assert 400 <= response.status_code < 500, (path, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_catalog_query"
        assert set(detail) == {"code", "message"}


def test_catalog_unavailable_never_serves_partial_rows_or_public_paths() -> None:
    unavailable = CatalogAuthority(CATALOG.with_name("missing-private-name.json"), MANIFEST, SCHEMA)
    response = _client(unavailable).get("/api/molbio/restriction/catalog")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "catalog_unavailable", "message": "restriction catalog is unavailable"}}
    assert str(CATALOG.parent) not in response.text


def test_openapi_examples_execute_and_only_phase1_public_routes_are_mounted() -> None:
    client = _client(_authority())
    document = client.app.openapi()
    paths = document["paths"]
    assert set(paths) == {
        "/api/molbio/restriction/catalog",
        "/api/molbio/restriction/catalog/{enzyme_id}",
    }
    assert not any("analy" in path or "digest" in path for path in paths)
    examples = paths["/api/molbio/restriction/catalog"]["get"]["parameters"]
    for parameter in examples:
        values = [parameter["example"]] if "example" in parameter else parameter.get("schema", {}).get("examples", [])
        for value in values:
            response = client.get("/api/molbio/restriction/catalog", params={parameter["name"]: value})
            assert response.status_code == 200, (parameter["name"], response.text)


def test_main_application_mounts_catalog_routes_but_not_phase2_routes() -> None:
    import main

    paths = main.app.openapi()["paths"]
    assert "/api/molbio/restriction/catalog" in paths
    assert "/api/molbio/restriction/catalog/{enzyme_id}" in paths
    assert "/api/molbio/restriction/analyze" not in paths
    assert "/api/molbio/restriction/digests/simulate" not in paths
