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


def _client(authority: CatalogAuthority | None = None, session=None) -> TestClient:
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    if authority is not None:
        app.dependency_overrides[molbio_restriction.get_catalog_authority] = lambda: authority
    if session is not None:
        app.dependency_overrides[molbio_restriction.get_molbio_session] = lambda: session
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
    assert body["catalog"]["bounds"] == {
        "default_limit": 50, "maximum_limit": 250, "query_max_length": 128,
        "analysis_inline_sequence_max_length": 5_000_000,
        "analysis_explicit_enzyme_maximum": 256,
        "analysis_region_maximum": 128,
        "analysis_occurrence_maximum": 25_000,
        "analysis_event_maximum": 50_000,
        "analysis_response_maximum_bytes": 32 * 1024 * 1024,
    }
    assert body["catalog"]["analysis_enabled"] is True
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


def test_openapi_examples_execute_and_phase2_analyze_is_mounted() -> None:
    client = _client(_authority())
    document = client.app.openapi()
    paths = document["paths"]
    assert set(paths) == {
        "/api/molbio/restriction/catalog",
        "/api/molbio/restriction/catalog/{enzyme_id}",
        "/api/molbio/restriction/analyze",
    }
    assert not any("digest" in path for path in paths)
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


def test_main_application_mounts_phase2_analyze_but_not_digest_routes() -> None:
    import main

    paths = main.app.openapi()["paths"]
    assert "/api/molbio/restriction/catalog" in paths
    assert "/api/molbio/restriction/catalog/{enzyme_id}" in paths
    assert "/api/molbio/restriction/analyze" in paths
    assert "/api/molbio/restriction/digests/simulate" not in paths


def _inline_request(**updates):
    payload = {
        "schema": "bms.molbio.restriction-analysis-request.v1",
        "source": {"kind": "inline_dna", "name": "example", "dna": "TTGAATTCAA", "topology": "linear"},
        "catalog": {
            "catalog_id": "biopython-rebase-404-bms-v1",
            "expected_catalog_sha256": "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f",
        },
        "scope": {"mode": "explicit", "enzyme_ids": ["EcoRI"], "commercial_only": False},
        "regions": [],
        "include_possible_sites": True,
        "methylation_policy": "report_only",
    }
    payload.update(updates)
    return payload


def test_analyze_inline_contract_is_strict_deterministic_and_has_no_db_write() -> None:
    client = _client(_authority())
    first = client.post("/api/molbio/restriction/analyze", json=_inline_request())
    second = client.post("/api/molbio/restriction/analyze", json=_inline_request())
    assert first.status_code == second.status_code == 200, first.text
    body = first.json()
    assert body == second.json()
    assert body["schema"] == "bms.molbio.restriction-analysis-response.v1"
    assert body["source"]["kind"] == "inline_dna"
    assert body["source"]["content_sha256"] == hashlib.sha256(b"TTGAATTCAA").hexdigest()
    assert body["analysis"]["counts"]["double_strand_break_count"] == 1
    assert len(body["request_sha256"]) == len(body["result_sha256"]) == 64


def test_analyze_rejects_hostile_geometry_fields_stale_catalog_unknown_enzyme_and_products() -> None:
    client = _client(_authority())
    hostile = _inline_request()
    hostile["scope"]["site"] = "GAATTC"
    hostile["scope"]["cut_index"] = 3
    hostile["chemistry"] = {"buffer": "client-controlled"}
    response = client.post("/api/molbio/restriction/analyze", json=hostile)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_analysis_request"

    stale = _inline_request(catalog={
        "catalog_id": "biopython-rebase-404-bms-v1", "expected_catalog_sha256": "0" * 64
    })
    assert client.post("/api/molbio/restriction/analyze", json=stale).json()["detail"]["code"] == "catalog_digest_mismatch"

    unknown = _inline_request()
    unknown["scope"]["enzyme_ids"] = ["not-an-enzyme"]
    assert client.post("/api/molbio/restriction/analyze", json=unknown).json()["detail"]["code"] == "enzyme_not_found"

    products = _inline_request(methylation_policy="require_known")
    assert client.post("/api/molbio/restriction/analyze", json=products).json()["detail"]["code"] == "product_evidence_unavailable"

    oversized = _inline_request()
    oversized["scope"]["enzyme_ids"] = [f"E{index}" for index in range(257)]
    too_large = client.post("/api/molbio/restriction/analyze", json=oversized)
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "request_too_large"


def test_analyze_openapi_request_example_executes_and_constraints_match_runtime() -> None:
    client = _client(_authority())
    operation = client.app.openapi()["paths"]["/api/molbio/restriction/analyze"]["post"]
    example = operation["requestBody"]["content"]["application/json"]["examples"]["inline_dna"]["value"]
    assert client.post("/api/molbio/restriction/analyze", json=example).status_code == 200
    schemas = client.app.openapi()["components"]["schemas"]
    assert schemas["InlineDNASource"]["properties"]["dna"]["maxLength"] == 5_000_000
    assert schemas["ExplicitAnalysisScope"]["properties"]["enzyme_ids"]["maxItems"] == 256


def test_analyze_binds_exact_immutable_revision_and_rejects_stale_digest() -> None:
    from types import SimpleNamespace

    sequence = "TTGAATTCAA"
    digest = hashlib.sha256(sequence.encode()).hexdigest()
    document = SimpleNamespace(id="sequence-1", name="saved", current_revision_id="newer")
    revision = SimpleNamespace(
        id="revision-1", document_id="sequence-1", revision_number=1,
        content_sha256=digest, content_length=len(sequence),
        snapshot={"sequence_type": "dna", "sequence": sequence, "is_circular": False},
    )

    class Session:
        async def get(self, model, identity):
            return document if identity == "sequence-1" else revision if identity == "revision-1" else None

    request = _inline_request()
    request["source"] = {
        "kind": "molecular_revision", "sequence_id": "sequence-1",
        "revision_id": "revision-1", "expected_content_sha256": digest,
    }
    client = _client(_authority(), Session())
    response = client.post("/api/molbio/restriction/analyze", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["source"]["revision_id"] == "revision-1"
    assert response.json()["source"]["topology"] == "linear"

    request["source"]["expected_content_sha256"] = "0" * 64
    stale = client.post("/api/molbio/restriction/analyze", json=request)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "source_revision_digest_mismatch"
