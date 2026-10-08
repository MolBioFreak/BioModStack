"""Read-only restriction-enzyme catalog API (Phase 1 only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from typing import Annotated, Any, Coroutine, Literal

import rfc8785
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
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
_SUPPLIER_CODE_PATTERN = r"^[A-Za-z0-9._-]+$"
_SUPPLIER_CODE_MAX_LENGTH = 16
_CURSOR_PATTERN = r"^[A-Za-z0-9_-]+$"
_CURSOR = re.compile(_CURSOR_PATTERN)
_CURSOR_VERSION = 1
_CURSOR_KEY_VERSION = 1
# Development/API cursors intentionally expire whenever this process restarts.
_CURSOR_SIGNING_KEY = secrets.token_bytes(32)
_CURSOR_KEY_EPOCH = secrets.token_bytes(8)


class CatalogRoute(APIRoute):
    """Keep catalog query-validation failures on the stable public error contract."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def stable_validation_handler(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                cursor_error = any(
                    len(error.get("loc", ())) >= 2 and error["loc"][:2] == ("query", "cursor")
                    for error in exc.errors()
                )
                detail = (
                    {
                        "code": "cursor_invalid",
                        "message": "catalog cursor is invalid for this request",
                    }
                    if cursor_error
                    else {
                        "code": "invalid_catalog_query",
                        "message": "catalog query parameters are invalid",
                    }
                )
                return JSONResponse(status_code=422, content={"detail": detail})

        return stable_validation_handler


router = APIRouter(
    prefix="/api/molbio/restriction",
    tags=["molbio-restriction-catalog"],
    route_class=CatalogRoute,
)


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


def _encode_cursor(
    view: CatalogView, fingerprint: str, limit: int, record: RestrictionRecord
) -> str:
    epoch = base64.urlsafe_b64encode(_CURSOR_KEY_EPOCH).decode("ascii").rstrip("=")
    document = {
        "v": _CURSOR_VERSION,
        "key_version": _CURSOR_KEY_VERSION,
        "key_epoch": epoch,
        "catalog_sha256": view.content_sha256,
        "fingerprint": fingerprint,
        "limit": limit,
        "last_name": record.canonical_name.casefold(),
        "last_id": record.enzyme_id.casefold(),
    }
    raw = rfc8785.dumps(document)
    authenticated = bytes((_CURSOR_VERSION, _CURSOR_KEY_VERSION)) + _CURSOR_KEY_EPOCH + raw
    signature = hmac.new(_CURSOR_SIGNING_KEY, authenticated, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(authenticated + signature).decode("ascii").rstrip("=")


def _decode_cursor(
    view: CatalogView, cursor: str, fingerprint: str, limit: int
) -> tuple[str, str]:
    try:
        if len(cursor) > CURSOR_MAX_LENGTH or not _CURSOR.fullmatch(cursor):
            raise ValueError
        envelope = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
        )
        if base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=") != cursor:
            raise ValueError
        if len(envelope) <= 2 + 8 + 32:
            raise ValueError
        authenticated, signature = envelope[:-32], envelope[-32:]
        if not hmac.compare_digest(
            signature, hmac.new(_CURSOR_SIGNING_KEY, authenticated, hashlib.sha256).digest()
        ):
            raise ValueError
        version, key_version = authenticated[0], authenticated[1]
        epoch = authenticated[2:10]
        if (
            version != _CURSOR_VERSION
            or key_version != _CURSOR_KEY_VERSION
            or not hmac.compare_digest(epoch, _CURSOR_KEY_EPOCH)
        ):
            raise ValueError
        raw = authenticated[10:]
        document = json.loads(raw)
        if not isinstance(document, dict) or raw != rfc8785.dumps(document):
            raise ValueError
        if set(document) != {
            "v",
            "key_version",
            "key_epoch",
            "catalog_sha256",
            "fingerprint",
            "limit",
            "last_name",
            "last_id",
        }:
            raise ValueError
        expected_epoch = base64.urlsafe_b64encode(_CURSOR_KEY_EPOCH).decode("ascii").rstrip("=")
        if (
            document["v"] != _CURSOR_VERSION
            or document["key_version"] != _CURSOR_KEY_VERSION
            or document["key_epoch"] != expected_epoch
            or document["catalog_sha256"] != view.content_sha256
            or document["fingerprint"] != fingerprint
            or document["limit"] != limit
            or not isinstance(document["last_name"], str)
            or not isinstance(document["last_id"], str)
        ):
            raise ValueError
        return document["last_name"], document["last_id"]
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(422, "cursor_invalid", "catalog cursor is invalid for this request") from exc


def _require_view(authority: CatalogAuthority) -> CatalogView:
    try:
        return authority.require()
    except CatalogUnavailable as exc:
        raise _error(503, "catalog_unavailable", "restriction catalog is unavailable") from exc


@router.get("/catalog", response_model=CatalogPage)
def list_catalog(
    request: Request,
    query: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=QUERY_MAX_LENGTH,
            pattern=r".*\S.*",
            examples=["EcoRI"],
        ),
    ] = None,
    geometry_status: Annotated[
        Literal["known", "unknown", "all"], Query(examples=["known"])
    ] = "all",
    commercial: Annotated[
        Literal["reported", "not_reported", "all"], Query(examples=["reported"])
    ] = "all",
    supplier_code: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=_SUPPLIER_CODE_MAX_LENGTH,
            pattern=_SUPPLIER_CODE_PATTERN,
            examples=["N"],
        ),
    ] = None,
    enzyme_kind: Annotated[
        Literal[
            "double_strand_endonuclease",
            "nicking_endonuclease",
            "restriction_enzyme_geometry_unresolved",
        ]
        | None,
        Query(examples=["double_strand_endonuclease"]),
    ] = None,
    overhang_kind: Annotated[
        Literal["blunt", "five_prime", "three_prime"] | None,
        Query(examples=["five_prime"]),
    ] = None,
    palindromic: Annotated[
        Literal["true", "false"] | None, Query(examples=["true"])
    ] = None,
    limit: Annotated[
        int | None, Query(ge=1, le=MAX_PAGE_LIMIT, examples=[DEFAULT_PAGE_LIMIT])
    ] = None,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=CURSOR_MAX_LENGTH, pattern=_CURSOR_PATTERN),
    ] = None,
    authority: CatalogAuthority = Depends(get_catalog_authority),
) -> CatalogPage:
    unknown = set(request.query_params) - _ALLOWED_QUERY_FIELDS
    if unknown:
        raise _invalid_query("unknown catalog query parameter")
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise _invalid_query("duplicate catalog query parameter")
    query = query.strip() if query is not None else None
    palindromic_value = _parse_bool(palindromic)
    page_limit = DEFAULT_PAGE_LIMIT if limit is None else limit
    raw_limit = request.query_params.get("limit")
    if raw_limit is not None and str(page_limit) != raw_limit:
        raise _invalid_query("limit is invalid")

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
    after = _decode_cursor(view, cursor, fingerprint, page_limit) if cursor is not None else None
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

    indexed_groups: list[tuple[RestrictionRecord, ...]] = []
    if geometry_status != "all":
        indexed_groups.append(view.by_geometry_status.get(geometry_status, ()))
    if commercial != "all":
        indexed_groups.append(view.by_commercial.get(commercial == "reported", ()))
    if supplier_upper:
        indexed_groups.append(view.by_supplier_code.get(supplier_upper, ()))
    if enzyme_kind:
        indexed_groups.append(view.by_kind.get(enzyme_kind, ()))
    if overhang_kind:
        indexed_groups.append(view.by_overhang_kind.get(overhang_kind, ()))
    if palindromic_value is not None:
        indexed_groups.append(view.by_palindromic.get(palindromic_value, ()))
    if indexed_groups:
        candidate_ids = set.intersection(
            *({record.enzyme_id for record in group} for group in indexed_groups)
        )
        ordered = tuple(
            view.by_id[enzyme_id]
            for enzyme_id in sorted(candidate_ids, key=view.order_rank.__getitem__)
        )
    else:
        ordered = view.ordered_records
    selected = [
        record
        for record in ordered
        if matches(record)
        and (after is None or (record.canonical_name.casefold(), record.enzyme_id.casefold()) > after)
    ]
    items = selected[:page_limit]
    next_cursor = (
        _encode_cursor(view, fingerprint, page_limit, items[-1])
        if len(selected) > page_limit
        else None
    )
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
