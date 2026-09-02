from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from molbio_database import get_molbio_session
from routers import molbio_ops
from services.assembly import golden_gate
from services.assembly.golden_gate import (
    GoldenGateAnalysisLimitError,
    GoldenGateInvalidDNAError,
)
from services.assembly.types import AssemblyProduct, GoldenGateCatalogAuthority
from services.restriction_catalog import catalog_authority
from services.restriction_analysis import MAX_INLINE_SEQUENCE_LENGTH


def _catalog_binding() -> dict[str, str]:
    catalog = catalog_authority.require()
    return {
        "catalog_id": catalog.catalog_id,
        "expected_catalog_sha256": catalog.content_sha256,
    }


async def _unused_session() -> AsyncIterator[object]:
    yield object()


@pytest.mark.parametrize("route", ["simulate", "save"])
@pytest.mark.parametrize(
    "invalid_patch",
    [
        {},
        {"enzyme_name": "BsmBI", **_catalog_binding()},
        {"enzyme_id": "BsaI", "recognition_site": "GGTCTC", **_catalog_binding()},
        {"enzyme_id": "BsaI", "site": "GGTCTC", **_catalog_binding()},
        {"enzyme_id": "BsaI", "cut_index": 1, **_catalog_binding()},
        {"enzyme_id": "BsaI", "top_offset": 7, "bottom_offset": 11, **_catalog_binding()},
    ],
)
def test_public_golden_gate_routes_reject_missing_legacy_and_geometry_authority_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    invalid_patch: dict[str, object],
) -> None:
    calls = {"simulate": 0, "persist": 0}

    def forbidden_simulation(*_args: object, **_kwargs: object) -> object:
        calls["simulate"] += 1
        raise AssertionError("simulation must not be reached")

    async def forbidden_persistence(*_args: object, **_kwargs: object) -> object:
        calls["persist"] += 1
        raise AssertionError("persistence must not be reached")

    monkeypatch.setattr(molbio_ops, "simulate_golden_gate", forbidden_simulation)
    monkeypatch.setattr(molbio_ops, "persist_assembly_product", forbidden_persistence)
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.dependency_overrides[get_molbio_session] = _unused_session
    payload: dict[str, object] = {"fragments": [], "circular": False, **invalid_patch}

    response = TestClient(app).post(
        f"/api/molbio/assembly/golden-gate/{route}", json=payload
    )

    assert response.status_code == 422, response.text
    assert calls == {"simulate": 0, "persist": 0}


def test_golden_gate_openapi_requires_stable_id_and_forbids_unknown_fields() -> None:
    app = FastAPI()
    app.include_router(molbio_ops.router)
    schema = app.openapi()["components"]["schemas"]["GoldenGateAssemblyRequest"]

    assert "enzyme_id" in schema["required"]
    assert {"catalog_id", "expected_catalog_sha256"} <= set(schema["required"])
    assert schema["properties"]["expected_catalog_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert schema["properties"]["enzyme_id"]["minLength"] == 1
    assert schema["properties"]["enzyme_id"]["maxLength"] == 128
    assert schema["additionalProperties"] is False
    assert "enzyme_name" not in schema["properties"]


@pytest.mark.parametrize("route", ["simulate", "save"])
@pytest.mark.parametrize(
    ("binding_patch", "status", "detail"),
    [
        ({"catalog_id": "retired-catalog"}, 404, "restriction catalog was not found"),
        ({"expected_catalog_sha256": "0" * 64}, 409, "restriction catalog digest does not match"),
    ],
)
def test_public_routes_reject_stale_catalog_binding_before_simulation_or_persistence(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    binding_patch: dict[str, str],
    status: int,
    detail: str,
) -> None:
    calls = {"simulate": 0, "persist": 0}

    def forbidden_simulation(*_args: object, **_kwargs: object) -> object:
        calls["simulate"] += 1
        raise AssertionError("simulation must not be reached")

    async def forbidden_persistence(*_args: object, **_kwargs: object) -> object:
        calls["persist"] += 1
        raise AssertionError("persistence must not be reached")

    monkeypatch.setattr(molbio_ops, "simulate_golden_gate", forbidden_simulation)
    monkeypatch.setattr(molbio_ops, "persist_assembly_product", forbidden_persistence)
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.dependency_overrides[get_molbio_session] = _unused_session
    response = TestClient(app).post(
        f"/api/molbio/assembly/golden-gate/{route}",
        json={"fragments": [], "circular": False, "enzyme_id": "BsaI", **_catalog_binding(), **binding_patch},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert calls == {"simulate": 0, "persist": 0}


def test_catalog_projects_backend_owned_golden_gate_compatibility_and_rejects_browser_heuristic() -> None:
    catalog = catalog_authority.require()
    compatible = catalog.by_id["BsaI"]
    compatible_payload = compatible.model_dump(mode="json")
    assert compatible_payload["golden_gate_compatible"] is True

    event = compatible.cleavage.events[0].model_copy(
        update={"top_offset": 1, "bottom_offset": 5}
    )
    discriminator_payload = compatible.model_dump(mode="json")
    discriminator_payload["cleavage"]["events"] = [event.model_dump(mode="json")]
    discriminator_payload["golden_gate_compatible"] = False
    discriminator = type(compatible).model_validate(discriminator_payload).model_dump(mode="json")

    assert discriminator["recognition"]["palindromic"] is False
    assert discriminator["cleavage"]["events"][0]["top_offset"] != discriminator[
        "cleavage"
    ]["events"][0]["bottom_offset"]
    assert discriminator["golden_gate_compatible"] is False


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (GoldenGateInvalidDNAError("Golden Gate fragment DNA is invalid"), 422, "Golden Gate fragment DNA is invalid"),
        (
            GoldenGateAnalysisLimitError("Golden Gate fragment exceeds supported analysis limits"),
            413,
            "Golden Gate fragment exceeds supported analysis limits",
        ),
    ],
)
@pytest.mark.parametrize("route", ["simulate", "save"])
def test_public_golden_gate_routes_translate_typed_dna_failures_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    detail: str,
    route: str,
) -> None:
    persisted = 0

    def fail(*_args: object, **_kwargs: object) -> object:
        raise error

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        nonlocal persisted
        persisted += 1
        raise AssertionError("persistence must not be reached")

    monkeypatch.setattr(molbio_ops, "simulate_golden_gate", fail)
    monkeypatch.setattr(molbio_ops, "persist_assembly_product", forbidden)
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.dependency_overrides[get_molbio_session] = _unused_session

    response = TestClient(app).post(
        f"/api/molbio/assembly/golden-gate/{route}",
        json={"fragments": [], "circular": False, "enzyme_id": "BsaI", **_catalog_binding()},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert persisted == 0
    assert "Traceback" not in response.text


def test_save_persists_structured_authority_from_the_simulated_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = GoldenGateCatalogAuthority(
        enzyme_id="BsmBI",
        catalog_id="catalog-v1",
        catalog_sha256="a" * 64,
    )
    product = AssemblyProduct(
        mode="golden_gate",
        sequence="ACGT",
        circular=False,
        fragments=[],
        junctions=[],
        golden_gate_authority=authority,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(molbio_ops, "simulate_golden_gate", lambda *_args, **_kwargs: product)

    async def persist(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(name="saved")

    monkeypatch.setattr(molbio_ops, "persist_assembly_product", persist)
    monkeypatch.setattr(molbio_ops, "assembly_product_to_response", lambda _product: object())
    monkeypatch.setattr(molbio_ops, "AssemblyOperationResponse", lambda **kwargs: kwargs)
    request = molbio_ops.GoldenGateAssemblyRequest(
        fragments=[], circular=False, enzyme_id="BsmBI", **_catalog_binding()
    )

    result = asyncio.run(molbio_ops.save_golden_gate_assembly(request, session=object()))

    assert result["message"] == "Saved Golden Gate product 'saved'"
    assert captured["extra_operation_params"] == {
        "enzyme_id": "BsmBI",
        "catalog_id": "catalog-v1",
        "catalog_sha256": "a" * 64,
    }


def _fragment_payload(fragment_id: str, sequence: str) -> dict[str, object]:
    return {
        "id": fragment_id,
        "name": fragment_id,
        "sequence": sequence,
        "left_end": {"type": "sticky_5", "overhang": "AAAA"},
        "right_end": {"type": "sticky_5", "overhang": "TTTT"},
    }


@pytest.mark.parametrize("route", ["simulate", "save"])
@pytest.mark.parametrize(
    ("sequence", "status", "detail"),
    [
        ("ACGTX", 422, "Golden Gate fragment DNA is invalid"),
        (
            "A" * (MAX_INLINE_SEQUENCE_LENGTH + 1),
            413,
            "Golden Gate fragment exceeds supported analysis limits",
        ),
    ],
)
def test_public_routes_bound_real_invalid_and_oversized_dna_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    sequence: str,
    status: int,
    detail: str,
) -> None:
    persisted = 0

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        nonlocal persisted
        persisted += 1
        raise AssertionError("persistence must not be reached")

    monkeypatch.setattr(molbio_ops, "persist_assembly_product", forbidden)
    app = FastAPI()
    app.include_router(molbio_ops.router)
    app.dependency_overrides[get_molbio_session] = _unused_session

    response = TestClient(app).post(
        f"/api/molbio/assembly/golden-gate/{route}",
        json={
            "fragments": [
                _fragment_payload("first", sequence),
                _fragment_payload("second", "ACGT"),
            ],
            "circular": False,
            "enzyme_id": "BsaI",
            **_catalog_binding(),
        },
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert persisted == 0


def test_simulate_response_and_saved_reload_share_exact_catalog_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = catalog_authority.require()
    authority = GoldenGateCatalogAuthority(
        enzyme_id="BsaI",
        catalog_id=catalog.catalog_id,
        catalog_sha256=catalog.content_sha256,
    )
    product = AssemblyProduct(
        mode="golden_gate",
        sequence="ACGT",
        circular=False,
        fragments=[],
        junctions=[],
        golden_gate_authority=authority,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(molbio_ops, "simulate_golden_gate", lambda *_args, **_kwargs: product)

    async def persist(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(name="saved")

    monkeypatch.setattr(molbio_ops, "persist_assembly_product", persist)
    monkeypatch.setattr(
        molbio_ops, "AssemblyOperationResponse", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    request = molbio_ops.GoldenGateAssemblyRequest(
        fragments=[], circular=False, enzyme_id="BsaI", **_catalog_binding()
    )

    simulated = asyncio.run(molbio_ops.simulate_golden_gate_assembly(request))
    saved = asyncio.run(molbio_ops.save_golden_gate_assembly(request, session=object()))
    expected = {
        "enzyme_id": authority.enzyme_id,
        "catalog_id": authority.catalog_id,
        "catalog_sha256": authority.catalog_sha256,
    }

    assert simulated.product.golden_gate_authority.model_dump() == expected
    assert saved.product.golden_gate_authority.model_dump() == expected
    assert captured["extra_operation_params"] == expected


def test_non_golden_gate_product_response_has_closed_null_authority() -> None:
    response = molbio_ops.assembly_product_to_response(
        AssemblyProduct(
            mode="ligation",
            sequence="ACGT",
            circular=False,
            fragments=[],
            junctions=[],
        )
    )

    assert response.golden_gate_authority is None


def test_catalog_view_is_resolved_once_and_drives_the_selected_enzyme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = catalog_authority.require()
    calls = 0

    def require_once():
        nonlocal calls
        calls += 1
        return catalog

    monkeypatch.setattr("services.assembly.golden_gate.catalog_authority.require", require_once)
    enzyme = golden_gate.resolve_golden_gate_enzyme(
        enzyme_id="BsaI",
        catalog_id=catalog.catalog_id,
        expected_catalog_sha256=catalog.content_sha256,
    )

    assert calls == 1
    assert (enzyme.enzyme_id, enzyme.catalog_id, enzyme.catalog_sha256) == (
        "BsaI",
        catalog.catalog_id,
        catalog.content_sha256,
    )
