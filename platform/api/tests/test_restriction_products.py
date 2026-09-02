from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import rfc8785
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from routers import molbio_restriction
from services.restriction_catalog import CatalogAuthority

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
ARTIFACT = API_ROOT / "config/molbio/restriction/restriction_enzyme_products_v1.json"
SCHEMA = REPO_ROOT / "schemas/molbio/restriction_enzyme_products_v1.schema.json"


def _core() -> CatalogAuthority:
    return molbio_restriction.catalog_authority


def _service_module():
    from services import restriction_products
    return restriction_products


def _valid_document() -> dict[str, object]:
    return json.loads(ARTIFACT.read_bytes())


def _seal_record(record: dict[str, object]) -> dict[str, object]:
    unsigned = dict(record)
    unsigned.pop("record_sha256", None)
    record["record_sha256"] = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    return record


def _seal_document(document: dict[str, object]) -> dict[str, object]:
    document["schema_raw_sha256"] = hashlib.sha256(SCHEMA.read_bytes()).hexdigest()
    records = document["records"]
    assert isinstance(records, list)
    document["record_count"] = len(records)
    unsigned = dict(document)
    unsigned.pop("content_sha256", None)
    document["content_sha256"] = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
    return document


def _future_record(
    product_id: str,
    enzyme_id: str,
    supplier_id: str,
    supplier_name: str,
    catalog_number: str,
) -> dict[str, object]:
    source_digest = hashlib.sha256(product_id.encode()).hexdigest()
    evidence = {
        "source_id": f"fixture:{product_id}",
        "source_sha256": source_digest,
        "observed_on": "2026-08-31",
    }
    unavailable = {"state": "unavailable", "value": None, "evidence": None}
    return _seal_record({
        "product_id": product_id,
        "enzyme_id": enzyme_id,
        "supplier": {"supplier_id": supplier_id, "name": supplier_name},
        "catalog_number": catalog_number,
        "product_name": f"Fixture {enzyme_id} {catalog_number}",
        "source": {
            "url": f"https://fixtures.invalid/{product_id}",
            "retrieved_at": "2026-08-31T12:00:00Z",
            "content_sha256": source_digest,
            "manual_receipt_id": f"fixture:{product_id}",
            "manual_receipt_sha256": source_digest,
        },
        "redistribution_permission": {
            "state": "approved", "receipt_id": "fixture-permission-v1",
            "receipt_sha256": "e" * 64, "decided_on": "2026-08-30",
        },
        "availability": {"state": "available", "as_of": "2026-08-31", "evidence": dict(evidence)},
        "reaction_conditions": {
            "temperature": {"state": "available", "value": 37, "evidence": dict(evidence)},
            "heat_inactivation": dict(unavailable),
            "buffer_activity": [{"state": "available", "value": "100% in fixture buffer", "evidence": dict(evidence)}],
        },
        "methylation_effects": [dict(unavailable)],
        "star_activity_warnings": [dict(unavailable)],
        "unit_concentration": {
            "state": "available", "units": "U", "concentration": "20 U/uL", "evidence": dict(evidence),
        },
        "operational_status": "available",
    })


def _future_document() -> dict[str, object]:
    records = [
        _future_record("fixture-p3", "EcoRI", "fixture-supplier-b", "Fixture Supplier B", "CAT-3"),
        _future_record("fixture-p1", "BamHI", "fixture-supplier-a", "Fixture Supplier A", "CAT-2"),
        _future_record("fixture-p2", "EcoRI", "fixture-supplier-a", "Fixture Supplier A", "CAT-1"),
    ]
    return _seal_document({
        "schema": "bms.molbio.restriction-enzyme-products.v1",
        "schema_version": 1,
        "release_id": "fixture-permissioned-products-v1",
        "release_version": "1.1.0",
        "created_at": "2026-09-01T12:00:00Z",
        "created_at_policy": "permissioned_evidence_release_timestamp",
        "source_policy": "no_runtime_scraping_written_redistribution_permission_required",
        "redistribution_permission_state": "approved",
        "permission_receipt": {
            "receipt_id": "fixture-permission-v1", "receipt_sha256": "e" * 64,
            "decided_on": "2026-08-30",
        },
        "product_evidence_available": True,
        "record_count": len(records),
        "active_claim_count": 12,
        "core_catalog_digest_binding": "independent_no_binding",
        "product_identity_policy": "supplier_id_and_catalog_number_nfkc_casefold_trim_v1",
        "canonicalization": "RFC_8785_JCS",
        "digest_semantics": "sha256(rfc8785(document_without_content_sha256))",
        "schema_raw_sha256": "0" * 64,
        "records": records,
        "content_sha256": "0" * 64,
    })


def _write_authority(tmp_path: Path, document: dict[str, object], *, schema_bytes: bytes | None = None):
    products = tmp_path / "config/products.json"
    schema = tmp_path / "schemas/products.schema.json"
    products.parent.mkdir(parents=True)
    schema.parent.mkdir(parents=True)
    raw = rfc8785.dumps(document)
    products.write_bytes(raw)
    schema.write_bytes(schema_bytes if schema_bytes is not None else SCHEMA.read_bytes())
    module = _service_module()
    return module.ProductAuthority(
        products,
        schema,
        _core(),
        trusted_root=tmp_path,
        expected_raw_sha256=hashlib.sha256(raw).hexdigest(),
        expected_schema_raw_sha256=hashlib.sha256(schema.read_bytes()).hexdigest(),
        expected_content_sha256=str(document.get("content_sha256", "")),
    )


def test_phase5_assets_and_exact_empty_release_load() -> None:
    module = _service_module()
    view = module.product_authority.require()
    assert ARTIFACT.read_bytes() == rfc8785.dumps(json.loads(ARTIFACT.read_bytes()))
    assert view.release_id == "bms-restriction-products-permission-pending-v1"
    assert view.release_version == "1.0.0"
    assert view.record_count == 0
    assert view.active_claim_count == 0
    assert view.records == ()
    assert view.product_evidence_available is False
    assert view.redistribution_permission_state == "unavailable"
    assert view.core_catalog_digest_binding == "independent_no_binding"
    assert view.content_sha256 != _core().require().content_sha256
    receipt = view.receipt()
    assert receipt["raw_sha256"] == hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert receipt["schema_raw_sha256"] == hashlib.sha256(SCHEMA.read_bytes()).hexdigest()


def test_product_schema_is_closed_and_declares_complete_future_record_contract() -> None:
    schema = json.loads(SCHEMA.read_bytes())
    assert schema["additionalProperties"] is False
    record = schema["$defs"]["product_record"]
    assert record["additionalProperties"] is False
    required = set(record["required"])
    assert {
        "product_id", "enzyme_id", "supplier", "catalog_number", "product_name",
        "source", "redistribution_permission", "availability", "reaction_conditions",
        "methylation_effects", "star_activity_warnings", "unit_concentration",
        "operational_status", "record_sha256",
    } <= required


def test_loader_rejects_unknown_fields_noncanonical_bytes_and_forged_digest_or_counts(tmp_path: Path) -> None:
    module = _service_module()
    base = _valid_document()
    cases: list[tuple[str, dict[str, object]]] = []
    unknown = dict(base); unknown["extra"] = True; cases.append(("unknown", unknown))
    forged_digest = dict(base); forged_digest["content_sha256"] = "0" * 64; cases.append(("digest", forged_digest))
    forged_count = dict(base); forged_count["record_count"] = 1; cases.append(("count", forged_count))
    contradiction = dict(base); contradiction["product_evidence_available"] = True; cases.append(("available", contradiction))
    for label, document in cases:
        with pytest.raises(module.ProductEvidenceUnavailable):
            _write_authority(tmp_path / label, document).require()

    products = tmp_path / "pretty/config/products.json"
    schema = tmp_path / "pretty/schemas/products.schema.json"
    products.parent.mkdir(parents=True); schema.parent.mkdir(parents=True)
    pretty = json.dumps(base, indent=2).encode()
    products.write_bytes(pretty); schema.write_bytes(SCHEMA.read_bytes())
    authority = module.ProductAuthority(
        products, schema, _core(), trusted_root=tmp_path / "pretty",
        expected_raw_sha256=hashlib.sha256(pretty).hexdigest(),
        expected_schema_raw_sha256=hashlib.sha256(schema.read_bytes()).hexdigest(),
        expected_content_sha256=str(base["content_sha256"]),
    )
    with pytest.raises(module.ProductEvidenceUnavailable):
        authority.require()


def test_loader_rejects_any_ungoverned_or_inconsistent_record(tmp_path: Path) -> None:
    module = _service_module()
    base = _valid_document()
    hostile_records = [
        {"product_id": "fake"},
        {
            "product_id": "fake", "enzyme_id": "not-a-core-enzyme", "supplier": {"supplier_id": "x", "name": "X"},
            "catalog_number": "1", "product_name": "Fake", "source": None,
            "redistribution_permission": {"state": "unavailable", "receipt_id": None, "receipt_sha256": None, "decided_on": None},
            "availability": {"state": "unavailable", "as_of": None, "evidence": None},
            "reaction_conditions": {"temperature": {"state": "unavailable", "value_celsius": None, "evidence": None}, "heat_inactivation": {"state": "unavailable", "value": None, "evidence": None}, "buffer_activity": []},
            "methylation_effects": [], "star_activity_warnings": [],
            "unit_concentration": {"state": "unavailable", "units": None, "concentration": None, "evidence": None},
            "operational_status": "unavailable", "record_sha256": "0" * 64,
        },
    ]
    for index, record in enumerate(hostile_records):
        document = dict(base)
        document["records"] = [record, record] if index else [record]
        document["record_count"] = len(document["records"])
        document["active_claim_count"] = 1
        unsigned = dict(document); unsigned.pop("content_sha256")
        document["content_sha256"] = hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest()
        with pytest.raises(module.ProductEvidenceUnavailable):
            _write_authority(tmp_path / str(index), document).require()


def _reseal_future(document: dict[str, object]) -> dict[str, object]:
    records = document["records"]
    assert isinstance(records, list)
    for record in records:
        assert isinstance(record, dict)
        _seal_record(record)
    return _seal_document(document)


def _schema_errors(document: dict[str, object]) -> list[object]:
    schema = json.loads(SCHEMA.read_bytes())
    return list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))


def test_schema_and_runtime_accept_complete_permissioned_future_fixture(tmp_path: Path) -> None:
    document = _future_document()
    assert _schema_errors(document) == []
    view = _write_authority(tmp_path, document).require()
    assert view.record_count == 3
    assert view.active_claim_count == 12
    assert [record["product_id"] for record in view.records] == ["fixture-p2", "fixture-p1", "fixture-p3"]
    assert view.permission_receipt == document["permission_receipt"]


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("temperature unavailable value", lambda d: d["records"][0]["reaction_conditions"]["temperature"].update(state="unavailable", value=37, evidence=None)),
        ("heat unavailable value", lambda d: d["records"][0]["reaction_conditions"]["heat_inactivation"].update(value="80 C for 20 min")),
        ("buffer unavailable value", lambda d: d["records"][0]["reaction_conditions"]["buffer_activity"][0].update(state="unavailable", value="100%", evidence=None)),
        ("methylation unavailable value", lambda d: d["records"][0]["methylation_effects"][0].update(value="blocked by methylation")),
        ("star unavailable value", lambda d: d["records"][0]["star_activity_warnings"][0].update(value="star activity possible")),
        ("availability unavailable date", lambda d: d["records"][0]["availability"].update(state="unavailable", as_of="2026-08-31", evidence=None)),
        ("unit unavailable text", lambda d: d["records"][0]["unit_concentration"].update(state="unavailable", units="U", concentration=None, evidence=None)),
        ("temperature stale evidence", lambda d: d["records"][0]["reaction_conditions"]["temperature"].update(state="stale", evidence=None)),
        ("heat stale evidence", lambda d: d["records"][0]["reaction_conditions"]["heat_inactivation"].update(state="stale", value="80 C", evidence=None)),
        ("buffer stale evidence", lambda d: d["records"][0]["reaction_conditions"]["buffer_activity"][0].update(state="stale", evidence=None)),
        ("methylation stale evidence", lambda d: d["records"][0]["methylation_effects"][0].update(state="stale", value="blocked", evidence=None)),
        ("star stale evidence", lambda d: d["records"][0]["star_activity_warnings"][0].update(state="stale", value="warning", evidence=None)),
        ("availability stale date", lambda d: d["records"][0]["availability"].update(state="stale", as_of=None)),
        ("unit stale evidence", lambda d: d["records"][0]["unit_concentration"].update(state="stale", evidence=None)),
        ("record permission unavailable", lambda d: d["records"][0]["redistribution_permission"].update(state="unavailable", receipt_id=None, receipt_sha256=None, decided_on=None)),
        ("record source has no digest", lambda d: d["records"][0]["source"].update(content_sha256=None, manual_receipt_id=None, manual_receipt_sha256=None)),
        ("manual source receipt incomplete", lambda d: d["records"][0]["source"].update(manual_receipt_sha256=None)),
    ],
)
def test_claim_family_invariants_reject_in_schema_and_runtime(
    tmp_path: Path, label: str, mutate,
) -> None:
    document = _future_document()
    mutate(document)
    _reseal_future(document)
    assert _schema_errors(document), label
    module = _service_module()
    record = document["records"][0]
    assert isinstance(record, dict)
    permission = document["permission_receipt"]
    assert isinstance(permission, dict)
    with pytest.raises(module.ProductEvidenceUnavailable):
        module._validate_record_semantics(record, set(_core().require().by_id), permission)
    with pytest.raises(module.ProductEvidenceUnavailable):
        _write_authority(tmp_path / label.replace(" ", "-"), document).require()


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("foreign evidence digest", lambda row: row["reaction_conditions"]["temperature"]["evidence"].update(source_sha256="9" * 64)),
        ("foreign evidence source", lambda row: row["reaction_conditions"]["temperature"]["evidence"].update(source_id="fixture:foreign")),
        ("permission receipt mismatch", lambda row: row["redistribution_permission"].update(receipt_sha256="9" * 64)),
        ("availability observed date mismatch", lambda row: row["availability"]["evidence"].update(observed_on="2026-08-30")),
    ],
)
def test_runtime_rejects_schema_valid_relational_evidence_mismatch(
    tmp_path: Path, label: str, mutate,
) -> None:
    document = _future_document()
    record = document["records"][0]
    assert isinstance(record, dict)
    mutate(record)
    _reseal_future(document)
    assert _schema_errors(document) == []
    module = _service_module()
    permission = document["permission_receipt"]
    assert isinstance(permission, dict)
    with pytest.raises(module.ProductEvidenceUnavailable):
        module._validate_record_semantics(record, set(_core().require().by_id), permission)
    with pytest.raises(module.ProductEvidenceUnavailable):
        _write_authority(tmp_path / label.replace(" ", "-"), document).require()


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("duplicate supplier catalog", lambda rows: rows[0].update(enzyme_id=rows[2]["enzyme_id"], supplier=rows[2]["supplier"], catalog_number=rows[2]["catalog_number"])),
        ("tuple conflicting enzyme", lambda rows: rows[0].update(supplier=rows[1]["supplier"], catalog_number=rows[1]["catalog_number"])),
        ("catalog mismatched supplier identity", lambda rows: rows[0].update(supplier={"supplier_id": "fixture-supplier-new", "name": rows[1]["supplier"]["name"]}, catalog_number=rows[1]["catalog_number"])),
        ("supplier display mismatch", lambda rows: rows[0].update(supplier={"supplier_id": rows[1]["supplier"]["supplier_id"], "name": "Different Display"})),
        ("duplicate product id", lambda rows: rows[0].update(product_id=rows[1]["product_id"])),
    ],
)
def test_loader_rejects_composite_identity_collisions(
    tmp_path: Path, label: str, mutate,
) -> None:
    document = _future_document()
    rows = document["records"]
    assert isinstance(rows, list)
    mutate(rows)
    _reseal_future(document)
    with pytest.raises(_service_module().ProductEvidenceUnavailable):
        _write_authority(tmp_path / label.replace(" ", "-"), document).require()


def test_loader_is_sticky_fail_closed_and_rejects_symlink_component_and_oversize(tmp_path: Path) -> None:
    module = _service_module()
    missing = tmp_path / "missing/products.json"
    authority = module.ProductAuthority(missing, SCHEMA, _core(), trusted_root=tmp_path)
    with pytest.raises(module.ProductEvidenceUnavailable): authority.require()
    missing.parent.mkdir(); missing.write_bytes(ARTIFACT.read_bytes())
    with pytest.raises(module.ProductEvidenceUnavailable): authority.require()

    real = tmp_path / "real"; real.mkdir(); (real / "products.json").write_bytes(ARTIFACT.read_bytes())
    os.symlink(real, tmp_path / "linked")
    linked = module.ProductAuthority(tmp_path / "linked/products.json", SCHEMA, _core(), trusted_root=tmp_path)
    with pytest.raises(module.ProductEvidenceUnavailable): linked.require()

    oversized = tmp_path / "large/products.json"; oversized.parent.mkdir(); oversized.write_bytes(b"x" * (module.PRODUCT_MAX_BYTES + 1))
    large = module.ProductAuthority(oversized, SCHEMA, _core(), trusted_root=tmp_path)
    with pytest.raises(module.ProductEvidenceUnavailable): large.require()


def _client(authority=None) -> TestClient:
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    if authority is not None:
        app.dependency_overrides[molbio_restriction.get_product_authority] = lambda: authority
    return TestClient(app)


def test_products_route_returns_exact_zero_release_and_openapi_contract() -> None:
    response = _client().get("/api/molbio/restriction/products")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "bms.molbio.restriction-products-page.v1"
    assert body["product_release"]["record_count"] == 0
    assert body["product_release"]["active_claim_count"] == 0
    assert body["product_release"]["product_evidence_available"] is False
    assert body["product_release"]["redistribution_permission_state"] == "unavailable"
    assert body["items"] == []
    assert body["next_cursor"] is None
    operation = _client().app.openapi()["paths"]["/api/molbio/restriction/products"]["get"]
    assert {item["name"] for item in operation["parameters"]} == {"enzyme_id", "supplier", "operational_status", "limit", "cursor"}
    record_schema = _client().app.openapi()["components"]["schemas"]["ProductRecordResponse"]
    assert record_schema["additionalProperties"] is False
    assert set(record_schema["required"]) >= {"product_id", "enzyme_id", "source", "redistribution_permission", "availability", "reaction_conditions"}


def test_products_route_serves_bounded_filtered_future_pages_with_bound_cursor(tmp_path: Path) -> None:
    authority = _write_authority(tmp_path / "release-a", _future_document())
    client = _client(authority)

    first = client.get("/api/molbio/restriction/products?limit=1")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert [row["product_id"] for row in first_body["items"]] == ["fixture-p2"]
    assert isinstance(first_body["next_cursor"], str)
    assert first_body["product_release"]["record_count"] == 3
    assert first_body["product_release"]["permission_receipt"] == {
        "receipt_id": "fixture-permission-v1", "receipt_sha256": "e" * 64,
        "decided_on": "2026-08-30",
    }

    second = client.get(
        "/api/molbio/restriction/products",
        params={"limit": "1", "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    assert [row["product_id"] for row in second.json()["items"]] == ["fixture-p1"]

    filtered = client.get(
        "/api/molbio/restriction/products",
        params={"enzyme_id": "EcoRI", "supplier": "FIXTURE-SUPPLIER-A", "operational_status": "available", "limit": "2"},
    )
    assert filtered.status_code == 200, filtered.text
    assert [row["product_id"] for row in filtered.json()["items"]] == ["fixture-p2"]
    assert filtered.json()["next_cursor"] is None

    cursor = first_body["next_cursor"]
    invalid_requests = [
        {"limit": "2", "cursor": cursor},
        {"limit": "1", "cursor": cursor, "enzyme_id": "EcoRI"},
        {"limit": "1", "cursor": cursor[:-1] + ("A" if cursor[-1] != "A" else "B")},
    ]
    for params in invalid_requests:
        response = client.get("/api/molbio/restriction/products", params=params)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "product_cursor_invalid"

    changed = _future_document()
    changed["release_id"] = "fixture-permissioned-products-v2"
    _seal_document(changed)
    other_client = _client(_write_authority(tmp_path / "release-b", changed))
    mismatch = other_client.get(
        "/api/molbio/restriction/products", params={"limit": "1", "cursor": cursor},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "product_cursor_invalid"


@pytest.mark.parametrize("query", [
    "unknown=x", "enzyme_id=a&enzyme_id=b", "supplier=x&supplier=y", "limit=0", "limit=251",
    "cursor=bad!", "cursor=Zm9yZ2Vk", "operational_status=unknown", "supplier=x%20", "enzyme_id=x%20",
])
def test_products_route_rejects_query_and_cursor_abuse(query: str) -> None:
    response = _client().get(f"/api/molbio/restriction/products?{query}")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] in {"invalid_product_query", "product_cursor_invalid"}
    assert "path" not in response.text.lower()


def test_product_readiness_reports_current_full_runtime_with_empty_evidence_plane() -> None:
    state = _service_module().product_authority.readiness()
    assert state["required"] is True
    assert state["ready"] is True
    assert state["status"] == "evidence_unavailable"
    assert state["loader_healthy"] is True
    assert state["active_claim_count"] == 0
    assert state["product_evidence_available"] is False
    assert state["require_known_policy"] == "fail_closed_product_evidence_unavailable"
    assert state["full_restriction_runtime_ready"] is True
    assert state["phase6_denominator_status"] == "current"


def test_future_product_readiness_reports_loaded_evidence_on_current_runtime(tmp_path: Path) -> None:
    state = _write_authority(tmp_path, _future_document()).readiness()
    assert state["ready"] is True
    assert state["status"] == "evidence_available"
    assert state["record_count"] == 3
    assert state["active_claim_count"] == 12
    assert state["require_known_policy"] == "governed_product_evidence_loaded"
    assert state["full_restriction_runtime_ready"] is True
    assert state["phase6_denominator_status"] == "current"


def test_require_known_fails_before_analysis_or_evidence_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False
    def forbidden(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("analysis must not run")
    monkeypatch.setattr(molbio_restriction, "analyze_sequence", forbidden)
    request = {
        "schema": "bms.molbio.restriction-analysis-request.v1",
        "source": {"kind": "inline_dna", "name": "x", "dna": "GAATTC", "topology": "linear"},
        "catalog": {"catalog_id": _core().require().catalog_id, "expected_catalog_sha256": _core().require().content_sha256},
        "scope": {"mode": "explicit", "enzyme_ids": ["EcoRI"], "commercial_only": False},
        "regions": [], "include_possible_sites": True, "methylation_policy": "require_known",
    }
    response = _client().post("/api/molbio/restriction/analyze", json=request)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "product_evidence_unavailable", "message": "approved supplier product evidence is unavailable"}}
    assert called is False
