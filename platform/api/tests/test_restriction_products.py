from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import rfc8785
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


@pytest.mark.parametrize("query", [
    "unknown=x", "enzyme_id=a&enzyme_id=b", "supplier=x&supplier=y", "limit=0", "limit=251",
    "cursor=bad!", "cursor=Zm9yZ2Vk", "operational_status=available",
])
def test_products_route_rejects_query_and_cursor_abuse(query: str) -> None:
    response = _client().get(f"/api/molbio/restriction/products?{query}")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] in {"invalid_product_query", "product_cursor_invalid"}
    assert "path" not in response.text.lower()


def test_product_readiness_is_truthful_without_claiming_full_runtime_ready() -> None:
    state = _service_module().product_authority.readiness()
    assert state["required"] is True
    assert state["ready"] is True
    assert state["status"] == "evidence_unavailable"
    assert state["loader_healthy"] is True
    assert state["active_claim_count"] == 0
    assert state["product_evidence_available"] is False
    assert state["require_known_policy"] == "fail_closed_product_evidence_unavailable"
    assert state["full_restriction_runtime_ready"] is False
    assert state["phase6_denominator_status"] == "stale"


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
