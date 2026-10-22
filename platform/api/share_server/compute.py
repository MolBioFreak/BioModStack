"""Pure restriction computation for the share server.

Loads only the pure restriction catalog/analysis/digest modules. The BMS
``services`` package ``__init__`` eagerly imports workflow/Nextflow modules, so
when this process has not already imported it we register an inert namespace
stub that lets ``services.restriction_*`` resolve without executing it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

_API_ROOT = Path(__file__).resolve().parents[1]


def _install_services_namespace() -> None:
    if "services" in sys.modules:
        return
    stub = types.ModuleType("services")
    stub.__path__ = [str(_API_ROOT / "services")]  # type: ignore[attr-defined]
    stub.__package__ = "services"
    stub.__doc__ = "Inert namespace stub installed by share_server.compute"
    sys.modules["services"] = stub


_install_services_namespace()
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from pydantic import BaseModel  # noqa: E402

from services.restriction_analysis import (  # noqa: E402
    AnalysisLimitError,
    InvalidDNAError,
    MAX_RESPONSE_BYTES,
    _analyze_normalized_sequence,
    normalize_dna,
)
from services.restriction_catalog import (  # noqa: E402
    CatalogUnavailable,
    CatalogView,
    RestrictionRecord,
    catalog_authority,
)
from services.restriction_digest import (  # noqa: E402
    MAX_SELECTED_ENZYMES,
    DigestGeometryError,
    DigestLimitError,
    _simulate_normalized_digest_canonical,
)

# Guest-specific bounds (stricter than the tailnet API).
SHARE_MAX_SEQUENCE_LENGTH = 1_000_000
SHARE_MAX_EXPLICIT_ENZYMES = 256
SHARE_CATALOG_PAGE_LIMIT = 200
SHARE_CATALOG_MAX_LIMIT = 250

__all__ = [
    "AnalysisLimitError", "CatalogUnavailable", "ComputeError", "DigestGeometryError",
    "DigestLimitError", "InvalidDNAError", "analyze", "catalog_page", "catalog_receipt",
    "simulate_digest",
]


class ComputeError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, (dict, MappingProxyType)):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _view() -> CatalogView:
    try:
        return catalog_authority.require()
    except CatalogUnavailable as exc:
        raise ComputeError(503, "catalog_unavailable", "restriction catalog is unavailable") from exc


def catalog_receipt() -> dict[str, Any]:
    readiness = catalog_authority.readiness()
    if not readiness.get("ready"):
        raise ComputeError(503, "catalog_unavailable", "restriction catalog is unavailable")
    payload = {k: v for k, v in readiness.items() if k not in {"required", "ready", "status"}}
    payload["digest_enabled"] = True
    return _plain(payload)


def _browse_item(record: RestrictionRecord) -> dict[str, Any]:
    return {
        "enzyme_id": record.enzyme_id, "canonical_name": record.canonical_name,
        "aliases": list(record.aliases), "site_iupac": record.recognition.site_iupac,
        "site_alternatives_iupac": list(record.recognition.site_alternatives_iupac),
        "palindromic": record.recognition.palindromic, "cleavage_status": record.cleavage.status,
        "overhang_kinds": list(dict.fromkeys(event.overhang_kind for event in record.cleavage.events)),
        "nick_strand": record.cleavage.nick.strand if record.cleavage.nick else None,
        "enzyme_kind": record.enzyme_kind, "analysis_capability": record.analysis_capability,
        "golden_gate_compatible": record.golden_gate_compatible, "exclusion_reason": record.exclusion_reason,
        "reported_commercial": record.supplier_provenance.reported_commercial,
        "historical_supplier_codes": list(record.supplier_provenance.historical_supplier_codes),
    }


def catalog_page(
    *, response_view: str, enzyme_ids: list[str] | None, limit: int, offset: int,
) -> dict[str, Any]:
    if response_view not in {"full", "compact"}:
        raise ComputeError(422, "invalid_catalog_query", "catalog query parameters are invalid")
    if limit < 1 or limit > SHARE_CATALOG_MAX_LIMIT or offset < 0:
        raise ComputeError(422, "invalid_catalog_query", "catalog query parameters are invalid")
    view = _view()
    if enzyme_ids is not None:
        if len(enzyme_ids) > SHARE_MAX_EXPLICIT_ENZYMES or any(not e or len(e) > 128 for e in enzyme_ids):
            raise ComputeError(422, "invalid_catalog_query", "catalog query parameters are invalid")
        wanted = set(enzyme_ids)
        records = tuple(record for record in view.ordered_records if record.enzyme_id in wanted)
    else:
        records = view.ordered_records
    page = records[offset:offset + limit]
    next_offset = offset + limit if offset + limit < len(records) else None
    receipt = catalog_receipt()
    if response_view == "compact":
        return {
            "schema": "bms.molbio.restriction-catalog-browse-page.v1", "catalog": receipt,
            "items": [_browse_item(record) for record in page],
            "next_cursor": None if next_offset is None else f"o{next_offset}",
        }
    return {
        "schema": "bms.molbio.restriction-catalog-page.v1", "catalog": receipt,
        "items": [record.model_dump(mode="json", by_alias=True) for record in page],
        "next_cursor": None if next_offset is None else f"o{next_offset}",
    }


def _check_catalog_binding(view: CatalogView, binding: Any) -> None:
    if not isinstance(binding, dict) or set(binding) != {"catalog_id", "expected_catalog_sha256"}:
        raise ComputeError(422, "invalid_request", "catalog binding is invalid")
    if binding["catalog_id"] != view.catalog_id:
        raise ComputeError(404, "catalog_not_found", "restriction catalog was not found")
    if binding["expected_catalog_sha256"] != view.content_sha256:
        raise ComputeError(409, "catalog_digest_mismatch", "restriction catalog digest does not match")


def _inline_source(source: Any) -> tuple[str, Literal["linear", "circular"], dict[str, Any]]:
    if not isinstance(source, dict) or source.get("kind") != "inline_dna" or set(source) != {"kind", "name", "dna", "topology"}:
        raise ComputeError(422, "invalid_request", "only inline DNA sources are accepted")
    name, dna, topology = source["name"], source["dna"], source["topology"]
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise ComputeError(422, "invalid_request", "source name is invalid")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ComputeError(422, "invalid_request", "source name is invalid") from exc
    if not isinstance(topology, str) or topology not in {"linear", "circular"}:
        raise ComputeError(422, "invalid_request", "topology is invalid")
    if not isinstance(dna, str) or not dna or len(dna) > SHARE_MAX_SEQUENCE_LENGTH:
        raise ComputeError(413, "request_too_large", "sequence exceeds the guest limit")
    try:
        sequence = normalize_dna(dna)
    except InvalidDNAError as exc:
        raise ComputeError(422, "invalid_dna", "DNA input is invalid") from exc
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    receipt = {
        "kind": "inline_dna", "name": name.strip(), "sequence_id": None, "revision_id": None,
        "revision_number": None, "content_sha256": digest, "content_length": len(sequence),
        "topology": topology,
    }
    return sequence, cast(Literal["linear", "circular"], topology), receipt


def _enzyme_ids(value: Any, maximum: int) -> list[str]:
    if (
        not isinstance(value, list) or not value or len(value) > maximum
        or any(not isinstance(item, str) or not item or len(item) > 128 for item in value)
        or len(set(value)) != len(value)
    ):
        raise ComputeError(422, "invalid_request", "enzyme IDs are invalid")
    return value


def analyze(payload: Any) -> bytes:
    if not isinstance(payload, dict) or payload.get("schema") != "bms.molbio.restriction-analysis-request.v1":
        raise ComputeError(422, "invalid_request", "analysis request is invalid")
    allowed = {"schema", "source", "catalog", "scope", "regions", "include_possible_sites", "methylation_policy"}
    if set(payload) - allowed:
        raise ComputeError(422, "invalid_request", "analysis request is invalid")
    view = _view()
    _check_catalog_binding(view, payload.get("catalog"))
    scope = payload.get("scope")
    if not isinstance(scope, dict) or scope.get("mode") != "explicit" or set(scope) - {"mode", "enzyme_ids", "commercial_only"}:
        raise ComputeError(422, "invalid_request", "only explicit enzyme scopes are accepted")
    ids = _enzyme_ids(scope.get("enzyme_ids"), SHARE_MAX_EXPLICIT_ENZYMES)
    if payload.get("regions", []) != []:
        raise ComputeError(422, "invalid_request", "regions are not supported for guests")
    if payload.get("methylation_policy", "report_only") != "report_only":
        raise ComputeError(409, "product_evidence_unavailable", "approved supplier product evidence is unavailable")
    include_possible = payload.get("include_possible_sites", True)
    if not isinstance(include_possible, bool):
        raise ComputeError(422, "invalid_request", "analysis request is invalid")
    sequence, topology, source_receipt = _inline_source(payload.get("source"))
    missing = [item for item in ids if item not in view.by_id]
    if missing:
        raise ComputeError(404, "enzyme_not_found", "restriction enzyme was not found")
    records = tuple(view.by_id[item] for item in ids)
    if scope.get("commercial_only") is True:
        records = tuple(row for row in records if row.supplier_provenance.reported_commercial)
    records = tuple(sorted(records, key=lambda row: (row.canonical_name.casefold(), row.enzyme_id.casefold())))
    analysis = _analyze_normalized_sequence(
        sequence=sequence, source_sha=source_receipt["content_sha256"], topology=topology,
        catalog=view, records=records, include_possible_sites=include_possible, regions=(),
        persisted_identity=False,
    )
    response = {
        "schema": "bms.molbio.restriction-analysis-response.v1", "source": source_receipt,
        "catalog": catalog_receipt(), "request_sha256": None,
        "analysis": analysis.model_dump(mode="json", by_alias=True), "result_sha256": None,
    }
    encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise AnalysisLimitError("analysis response exceeds byte limit")
    return encoded


def simulate_digest(payload: Any) -> bytes:
    if not isinstance(payload, dict) or payload.get("schema") != "bms.molbio.restriction-digest-simulation-request.v1":
        raise ComputeError(422, "invalid_request", "digest request is invalid")
    if set(payload) != {"schema", "source", "catalog", "enzyme_ids"}:
        raise ComputeError(422, "invalid_request", "digest request is invalid")
    view = _view()
    _check_catalog_binding(view, payload["catalog"])
    ids = _enzyme_ids(payload["enzyme_ids"], MAX_SELECTED_ENZYMES)
    sequence, topology, source_receipt = _inline_source(payload["source"])
    if any(item not in view.by_id for item in ids):
        raise ComputeError(404, "enzyme_not_found", "restriction enzyme was not found")
    _simulation, canonical = _simulate_normalized_digest_canonical(
        sequence=sequence, source_sha=source_receipt["content_sha256"], topology=topology,
        catalog=view, records=tuple(view.by_id[item] for item in ids), selected_enzyme_ids=tuple(ids),
        source_receipt=source_receipt, catalog_receipt=catalog_receipt(), persisted_identity=False,
    )
    return canonical
