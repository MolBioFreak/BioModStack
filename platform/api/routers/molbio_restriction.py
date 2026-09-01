"""Read-only restriction-enzyme catalog API (Phase 1 only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Annotated, Literal

import rfc8785
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services.restriction_catalog import (
    CURSOR_MAX_LENGTH,
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    QUERY_MAX_LENGTH,
    CatalogAuthority,
    CatalogUnavailable,
    CatalogView,
    RestrictionRecord,
    catalog_authority,
)

router = APIRouter(prefix="/api/molbio/restriction", tags=["molbio-restriction-catalog"])
_ALLOWED_QUERY_FIELDS = {
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
_SUPPLIER_CODE = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_-]+\.[0-9a-f]{64}$")


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CatalogCounts(StrictResponse):
    total: int
    geometry_ready: int
    commercial_geometry_ready: int
    unknown_geometry: int
    nicking: int
    two_event_double_strand: int


class CatalogBounds(StrictResponse):
    default_limit: int
    maximum_limit: int
    query_max_length: int


class CatalogReceipt(StrictResponse):
    catalog_id: str
    catalog_sha256: str
    source_release: str
    counts: CatalogCounts
    source_year: int
    source_age_years: int
    source_age_notice: str
    supplier_code_notice: str
    bounds: CatalogBounds
    analysis_enabled: Literal[False]
    digest_enabled: Literal[False]


class CatalogPage(StrictResponse):
    schema_: Literal["bms.molbio.restriction-catalog-page.v1"] = Field(alias="schema")
    catalog: CatalogReceipt
    items: list[RestrictionRecord]
    next_cursor: str | None


class CatalogRecordResponse(StrictResponse):
    schema_: Literal["bms.molbio.restriction-catalog-record.v1"] = Field(alias="schema")
    catalog: CatalogReceipt
    record: RestrictionRecord


def get_catalog_authority() -> CatalogAuthority:
    return catalog_authority


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _invalid_query(message: str = "catalog query parameters are invalid") -> HTTPException:
    return _error(422, "invalid_catalog_query", message)


def _receipt(authority: CatalogAuthority) -> CatalogReceipt:
    readiness = authority.readiness()
    if not readiness.get("ready"):
        raise CatalogUnavailable()
    return CatalogReceipt.model_validate(
        {key: value for key, value in readiness.items() if key not in {"required", "ready", "status"}}
    )


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise _invalid_query("palindromic must be true or false")


def _fingerprint(filters: dict[str, object]) -> str:
    return hashlib.sha256(rfc8785.dumps(filters)).hexdigest()


def _encode_cursor(view: CatalogView, fingerprint: str, record: RestrictionRecord) -> str:
    document = {
        "v": 1,
        "catalog_sha256": view.content_sha256,
        "fingerprint": fingerprint,
        "last_name": record.canonical_name.casefold(),
        "last_id": record.enzyme_id.casefold(),
    }
    raw = rfc8785.dumps(document)
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hashlib.sha256(view.content_sha256.encode("ascii") + b":" + raw).hexdigest()
    return f"{payload}.{signature}"


def _decode_cursor(view: CatalogView, cursor: str, fingerprint: str) -> tuple[str, str]:
    try:
        if len(cursor) > CURSOR_MAX_LENGTH or not _CURSOR.fullmatch(cursor):
            raise ValueError
        payload, signature = cursor.split(".", 1)
        raw = base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
        expected = hashlib.sha256(view.content_sha256.encode("ascii") + b":" + raw).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        document = json.loads(raw)
        if raw != rfc8785.dumps(document):
            raise ValueError
        if set(document) != {"v", "catalog_sha256", "fingerprint", "last_name", "last_id"}:
            raise ValueError
        if (
            document["v"] != 1
            or document["catalog_sha256"] != view.content_sha256
            or document["fingerprint"] != fingerprint
            or not isinstance(document["last_name"], str)
            or not isinstance(document["last_id"], str)
        ):
            raise ValueError
        return document["last_name"], document["last_id"]
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(422, "invalid_cursor", "catalog cursor is invalid for this request") from exc


def _require_view(authority: CatalogAuthority) -> CatalogView:
    try:
        return authority.require()
    except CatalogUnavailable as exc:
        raise _error(503, "catalog_unavailable", "restriction catalog is unavailable") from exc


@router.get("/catalog", response_model=CatalogPage)
def list_catalog(
    request: Request,
    query: Annotated[str | None, Query(examples=["EcoRI"])] = None,
    geometry_status: Annotated[str, Query(examples=["known"])] = "all",
    commercial: Annotated[str, Query(examples=["reported"])] = "all",
    supplier_code: Annotated[str | None, Query(examples=["N"])] = None,
    enzyme_kind: Annotated[str | None, Query(examples=["double_strand_endonuclease"])] = None,
    overhang_kind: Annotated[str | None, Query(examples=["five_prime"])] = None,
    palindromic: Annotated[str | None, Query(examples=["true"])] = None,
    limit: Annotated[str | None, Query(examples=["50"])] = None,
    cursor: str | None = None,
    authority: CatalogAuthority = Depends(get_catalog_authority),
) -> CatalogPage:
    unknown = set(request.query_params) - _ALLOWED_QUERY_FIELDS
    if unknown:
        raise _invalid_query("unknown catalog query parameter")
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise _invalid_query("duplicate catalog query parameter")
    if query is not None and (not query.strip() or len(query) > QUERY_MAX_LENGTH):
        raise _invalid_query("query length is invalid")
    query = query.strip() if query is not None else None
    if geometry_status not in {"known", "unknown", "all"}:
        raise _invalid_query("geometry_status is invalid")
    if commercial not in {"reported", "not_reported", "all"}:
        raise _invalid_query("commercial is invalid")
    if supplier_code is not None and not _SUPPLIER_CODE.fullmatch(supplier_code):
        raise _invalid_query("supplier_code is invalid")
    if enzyme_kind is not None and enzyme_kind not in {
        "double_strand_endonuclease",
        "nicking_endonuclease",
        "restriction_enzyme_geometry_unresolved",
    }:
        raise _invalid_query("enzyme_kind is invalid")
    if overhang_kind is not None and overhang_kind not in {"blunt", "five_prime", "three_prime"}:
        raise _invalid_query("overhang_kind is invalid")
    palindromic_value = _parse_bool(palindromic)
    try:
        page_limit = DEFAULT_PAGE_LIMIT if limit is None else int(limit)
    except ValueError as exc:
        raise _invalid_query("limit is invalid") from exc
    if not 1 <= page_limit <= MAX_PAGE_LIMIT or (limit is not None and str(page_limit) != limit):
        raise _invalid_query("limit is invalid")
    if cursor is not None and len(cursor) > CURSOR_MAX_LENGTH:
        raise _error(422, "invalid_cursor", "catalog cursor is invalid for this request")

    view = _require_view(authority)
    filters = {
        "query": query,
        "geometry_status": geometry_status,
        "commercial": commercial,
        "supplier_code": supplier_code.upper() if supplier_code else None,
        "enzyme_kind": enzyme_kind,
        "overhang_kind": overhang_kind,
        "palindromic": palindromic_value,
        "limit": page_limit,
    }
    fingerprint = _fingerprint(filters)
    after = _decode_cursor(view, cursor, fingerprint) if cursor is not None else None
    query_folded = query.casefold() if query else None
    supplier_upper = supplier_code.upper() if supplier_code else None

    def matches(record: RestrictionRecord) -> bool:
        if query_folded is not None:
            searchable = (
                record.enzyme_id.casefold(),
                record.canonical_name.casefold(),
                *(alias.casefold() for alias in record.aliases),
                *(motif.casefold() for motif in record.recognition.site_alternatives_iupac),
                *(code.casefold() for code in record.supplier_provenance.historical_supplier_codes),
            )
            if not any(query_folded in value for value in searchable):
                return False
        if geometry_status != "all":
            known = record.cleavage.status != "unknown"
            if known != (geometry_status == "known"):
                return False
        if commercial != "all" and record.supplier_provenance.reported_commercial != (commercial == "reported"):
            return False
        if supplier_upper and supplier_upper not in {code.upper() for code in record.supplier_provenance.historical_supplier_codes}:
            return False
        if enzyme_kind and record.enzyme_kind != enzyme_kind:
            return False
        if overhang_kind and overhang_kind not in {event.overhang_kind for event in record.cleavage.events}:
            return False
        return palindromic_value is None or record.recognition.palindromic is palindromic_value

    ordered = sorted(view.records, key=lambda row: (row.canonical_name.casefold(), row.enzyme_id.casefold()))
    selected = [
        record
        for record in ordered
        if matches(record)
        and (after is None or (record.canonical_name.casefold(), record.enzyme_id.casefold()) > after)
    ]
    items = selected[:page_limit]
    next_cursor = _encode_cursor(view, fingerprint, items[-1]) if len(selected) > page_limit else None
    return CatalogPage(
        schema="bms.molbio.restriction-catalog-page.v1",
        catalog=_receipt(authority),
        items=items,
        next_cursor=next_cursor,
    )


@router.get("/catalog/{enzyme_id}", response_model=CatalogRecordResponse)
def catalog_detail(
    enzyme_id: str,
    request: Request,
    authority: CatalogAuthority = Depends(get_catalog_authority),
) -> CatalogRecordResponse:
    if request.query_params:
        raise _invalid_query("unknown catalog query parameter")
    if not enzyme_id or len(enzyme_id) > 128:
        raise _error(404, "enzyme_not_found", "restriction enzyme was not found")
    view = _require_view(authority)
    record = view.by_id.get(enzyme_id)
    if record is None:
        record = view.by_name_casefold.get(enzyme_id.casefold())
    if record is None:
        raise _error(404, "enzyme_not_found", "restriction enzyme was not found")
    return CatalogRecordResponse(
        schema="bms.molbio.restriction-catalog-record.v1",
        catalog=_receipt(authority),
        record=record,
    )
