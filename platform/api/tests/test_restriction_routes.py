from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path

import rfc8785
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
        assert response.json()["detail"]["code"] == "cursor_invalid"


def _legacy_public_forgery(*, query: str, limit: int, last_name: str = "", last_id: str = "") -> str:
    view = _authority().require()
    filters = {
        "query": query,
        "geometry_status": "all",
        "commercial": "all",
        "supplier_code": None,
        "enzyme_kind": None,
        "overhang_kind": None,
        "palindromic": None,
        "limit": limit,
    }
    document = {
        "v": 1,
        "catalog_sha256": view.content_sha256,
        "fingerprint": molbio_restriction._fingerprint(filters),
        "last_name": last_name,
        "last_id": last_id,
    }
    raw = rfc8785.dumps(document)
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    public_signature = hashlib.sha256(view.content_sha256.encode("ascii") + b":" + raw).hexdigest()
    return f"{payload}.{public_signature}"


def test_caller_cannot_forge_cursor_keyset_fingerprint_or_limit_from_public_data() -> None:
    client = _client(_authority())
    for query, limit, last_name, last_id in (
        ("A", 2, "zzzz", "zzzz"),
        ("B", 2, "", ""),
        ("A", 3, "", ""),
    ):
        forged = _legacy_public_forgery(
            query=query, limit=limit, last_name=last_name, last_id=last_id
        )
        response = client.get(
            "/api/molbio/restriction/catalog",
            params={"query": query, "limit": limit, "cursor": forged},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "cursor_invalid"


def test_cursor_rejects_one_bit_tampering_foreign_process_key_and_oversize(monkeypatch) -> None:
    authority = _authority()
    client = _client(authority)
    cursor = client.get("/api/molbio/restriction/catalog?query=A&limit=2").json()["next_cursor"]
    replacement = "A" if cursor[-1] != "A" else "B"
    tampered = cursor[:-1] + replacement
    oversized = "A" * 4097

    with monkeypatch.context() as scoped:
        scoped.setattr(molbio_restriction, "_CURSOR_SIGNING_KEY", b"f" * 32, raising=False)
        scoped.setattr(molbio_restriction, "_CURSOR_KEY_EPOCH", b"foreign!", raising=False)
        foreign = _client(authority).get(
            "/api/molbio/restriction/catalog?query=A&limit=2"
        ).json()["next_cursor"]

    for token in (tampered, foreign, oversized):
        response = client.get(
            "/api/molbio/restriction/catalog",
            params={"query": "A", "limit": 2, "cursor": token},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == {
            "code": "cursor_invalid",
            "message": "catalog cursor is invalid for this request",
        }
        assert token not in response.text


def test_cursor_rejects_correctly_signed_unknown_key_version_and_stale_catalog() -> None:
    authority = _authority()
    view = authority.require()
    filters = {
        "query": "A",
        "geometry_status": "all",
        "commercial": "all",
        "supplier_code": None,
        "enzyme_kind": None,
        "overhang_kind": None,
        "palindromic": None,
        "limit": 2,
    }
    fingerprint = molbio_restriction._fingerprint(filters)
    record = sorted(
        view.records, key=lambda row: (row.canonical_name.casefold(), row.enzyme_id.casefold())
    )[1]
    stale = molbio_restriction._encode_cursor(
        replace(view, content_sha256="0" * 64), fingerprint, 2, record
    )
    current = molbio_restriction._encode_cursor(view, fingerprint, 2, record)
    envelope = base64.urlsafe_b64decode(current + "=" * (-len(current) % 4))
    authenticated = bytearray(envelope[:-32])
    authenticated[1] = 99
    signature = hmac.new(
        molbio_restriction._CURSOR_SIGNING_KEY, bytes(authenticated), hashlib.sha256
    ).digest()
    unknown_version = base64.urlsafe_b64encode(bytes(authenticated) + signature).decode(
        "ascii"
    ).rstrip("=")

    client = _client(authority)
    for token in (unknown_version, stale):
        response = client.get(
            "/api/molbio/restriction/catalog",
            params={"query": "A", "limit": 2, "cursor": token},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "cursor_invalid"


def _all_catalog_ids(client: TestClient, params: dict[str, object]) -> list[str]:
    ids: list[str] = []
    cursor = None
    while True:
        request_params = {**params, "limit": 37}
        if cursor is not None:
            request_params["cursor"] = cursor
        response = client.get("/api/molbio/restriction/catalog", params=request_params)
        assert response.status_code == 200, response.text
        page = response.json()
        ids.extend(row["enzyme_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return ids


def test_real_cursor_full_pagination_matches_raw_catalog_predicates_without_gaps() -> None:
    raw_records = json.loads(CATALOG.read_bytes())["records"]
    cases = [
        ({"geometry_status": "unknown"}, lambda row: row["cleavage"]["status"] == "unknown"),
        (
            {"supplier_code": "N"},
            lambda row: "N" in row["supplier_provenance"]["historical_supplier_codes"],
        ),
        ({"palindromic": "false"}, lambda row: row["recognition"]["palindromic"] is False),
        (
            {
                "geometry_status": "known",
                "commercial": "reported",
                "overhang_kind": "five_prime",
                "palindromic": "true",
            },
            lambda row: (
                row["cleavage"]["status"] != "unknown"
                and row["supplier_provenance"]["reported_commercial"]
                and any(
                    event["overhang_kind"] == "five_prime"
                    for event in row["cleavage"]["events"]
                )
                and row["recognition"]["palindromic"] is True
            ),
        ),
    ]
    client = _client(_authority())
    for params, predicate in cases:
        expected = [
            row["enzyme_id"]
            for row in sorted(
                (row for row in raw_records if predicate(row)),
                key=lambda row: (row["canonical_name"].casefold(), row["enzyme_id"].casefold()),
            )
        ]
        actual = _all_catalog_ids(client, params)
        assert actual == expected, params
        assert len(actual) == len(set(actual)), params


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


def test_typed_catalog_query_rejections_preserve_stable_public_errors() -> None:
    client = _client(_authority())
    cases = {
        "query=": "invalid_catalog_query",
        "query=%20%20": "invalid_catalog_query",
        "enzyme_kind=unsupported": "invalid_catalog_query",
        "overhang_kind=unsupported": "invalid_catalog_query",
        "palindromic=1": "invalid_catalog_query",
        "limit=1.0": "invalid_catalog_query",
        "limit=01": "invalid_catalog_query",
        "cursor=": "cursor_invalid",
        "cursor=not%2Ba%2Furl-safe-token": "cursor_invalid",
    }
    for query_string, code in cases.items():
        response = client.get(f"/api/molbio/restriction/catalog?{query_string}")
        assert response.status_code == 422, (query_string, response.text)
        assert response.json()["detail"]["code"] == code
        assert set(response.json()["detail"]) == {"code", "message"}


def test_catalog_unavailable_never_serves_partial_rows_or_public_paths() -> None:
    unavailable = CatalogAuthority(CATALOG.with_name("missing-private-name.json"), MANIFEST, SCHEMA)
    response = _client(unavailable).get("/api/molbio/restriction/catalog")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "catalog_unavailable", "message": "restriction catalog is unavailable"}}
    assert str(CATALOG.parent) not in response.text


def _non_null_parameter_schema(parameter: dict[str, object]) -> dict[str, object]:
    schema = parameter["schema"]
    assert isinstance(schema, dict)
    variants = schema.get("anyOf")
    if variants is None:
        return schema
    assert isinstance(variants, list)
    non_null = [variant for variant in variants if variant != {"type": "null"}]
    assert len(non_null) == 1
    assert isinstance(non_null[0], dict)
    return non_null[0]


def test_catalog_openapi_query_parameters_publish_exact_runtime_constraints() -> None:
    operation = _client(_authority()).app.openapi()["paths"]["/api/molbio/restriction/catalog"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert set(parameters) == {
        "query",
        "geometry_status",
        "commercial",
        "supplier_code",
        "enzyme_kind",
        "overhang_kind",
        "palindromic",
        "limit",
        "cursor",
    }
    assert _non_null_parameter_schema(parameters["query"]) == {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r".*\S.*",
    }
    assert _non_null_parameter_schema(parameters["geometry_status"])["enum"] == [
        "known",
        "unknown",
        "all",
    ]
    assert _non_null_parameter_schema(parameters["commercial"])["enum"] == [
        "reported",
        "not_reported",
        "all",
    ]
    assert _non_null_parameter_schema(parameters["supplier_code"]) == {
        "type": "string",
        "minLength": 1,
        "maxLength": 16,
        "pattern": r"^[A-Za-z0-9._-]+$",
    }
    assert _non_null_parameter_schema(parameters["enzyme_kind"])["enum"] == [
        "double_strand_endonuclease",
        "nicking_endonuclease",
        "restriction_enzyme_geometry_unresolved",
    ]
    assert _non_null_parameter_schema(parameters["overhang_kind"])["enum"] == [
        "blunt",
        "five_prime",
        "three_prime",
    ]
    assert _non_null_parameter_schema(parameters["palindromic"])["enum"] == ["true", "false"]
    assert _non_null_parameter_schema(parameters["limit"]) == {
        "type": "integer",
        "maximum": 250,
        "minimum": 1,
    }
    assert _non_null_parameter_schema(parameters["cursor"]) == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
        "pattern": r"^[A-Za-z0-9_-]+$",
    }


def test_openapi_examples_execute_and_only_phase1_public_routes_are_mounted() -> None:
    client = _client(_authority())
    document = client.app.openapi()
    paths = document["paths"]
    assert set(paths) == {
        "/api/molbio/restriction/catalog",
        "/api/molbio/restriction/catalog/{enzyme_id}",
    }
    assert not any("analy" in path or "digest" in path for path in paths)
    parameters = paths["/api/molbio/restriction/catalog"]["get"]["parameters"]
    for parameter in parameters:
        values = [parameter["example"]] if "example" in parameter else parameter.get("schema", {}).get("examples", [])
        if parameter["name"] != "cursor":
            assert values, f"missing executable OpenAPI example for {parameter['name']}"
        for value in values:
            response = client.get("/api/molbio/restriction/catalog", params={parameter["name"]: value})
            assert response.status_code == 200, (parameter["name"], response.text)

    first_page = client.get("/api/molbio/restriction/catalog", params={"limit": 1})
    assert first_page.status_code == 200
    cursor = first_page.json()["next_cursor"]
    cursor_response = client.get(
        "/api/molbio/restriction/catalog",
        params={"limit": 1, "cursor": cursor},
    )
    assert cursor_response.status_code == 200, cursor_response.text


def test_main_application_mounts_catalog_routes_but_not_phase2_routes() -> None:
    import main

    paths = main.app.openapi()["paths"]
    assert "/api/molbio/restriction/catalog" in paths
    assert "/api/molbio/restriction/catalog/{enzyme_id}" in paths
    assert "/api/molbio/restriction/analyze" not in paths
    assert "/api/molbio/restriction/digests/simulate" not in paths
