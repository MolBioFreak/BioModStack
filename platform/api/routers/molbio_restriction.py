"""Restriction catalog, exact analysis, duplex digest, and immutable persistence API."""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import hmac
import json
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Coroutine, Literal, Union, cast

import rfc8785
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import LargeBinary, String, cast as sql_cast, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_database import get_molbio_session
from molbio_models import (
    MolecularDocument, MolecularOperation, MolecularOperationInput,
    MolecularOperationOutput, MolecularRevision, RestrictionDigestResult,
)
from services.restriction_analysis import (
    MAX_EXPLICIT_ENZYME_IDS,
    MAX_INLINE_SEQUENCE_LENGTH,
    MAX_REGIONS,
    MAX_RESPONSE_BYTES,
    AnalysisLimitError,
    AnalysisResult,
    ResourcePolicyReceipt,
    InvalidDNAError,
    analyze_sequence,
    normalize_dna,
    resource_policy_receipt,
)

from services.restriction_digest import (
    MAX_FRAGMENTS,
    MAX_SAVED_OUTPUTS,
    MAX_SIMULATION_RESPONSE_BYTES,
    MAX_SELECTED_ENZYMES,
    MAX_TOTAL_FRAGMENT_BASES,
    DigestGeometryError,
    DigestLimitError,
    DigestSimulation,
    simulate_digest_canonical,
)
from services.restriction_digest_save_receipt import (
    canonical_save_request_receipt,
    load_canonical_save_request_receipt,
    save_request_fingerprint,
)

from services.restriction_catalog import (
    CURSOR_MAX_LENGTH,
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    QUERY_MAX_LENGTH,
    CatalogAuthority,
    CatalogUnavailable,
    CatalogView,
    RestrictionRecord,
    ANALYSIS_CANCELLATION_POLICY,
    ANALYSIS_QUEUE_POLICY,
    ANALYSIS_TIMEOUT_SECONDS,
    ANALYSIS_WORKER_CONCURRENCY,
    catalog_authority,
    resource_policy_sha256,
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
_analysis_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=ANALYSIS_WORKER_CONCURRENCY,
    thread_name_prefix="restriction-analysis",
)
_analysis_capacity = threading.BoundedSemaphore(ANALYSIS_WORKER_CONCURRENCY)


class AnalysisBusyError(RuntimeError):
    pass


class AnalysisTimeoutError(RuntimeError):
    pass


def _reserve_analysis_capacity() -> None:
    if not _analysis_capacity.acquire(blocking=False):
        raise AnalysisBusyError


async def _run_capacity_owned(function: Callable[..., Any], **kwargs: Any) -> Any:
    """Run one already-admitted worker and retain its slot until it really ends."""
    try:
        future = _analysis_executor.submit(function, **kwargs)
    except BaseException:
        _analysis_capacity.release()
        raise
    future.add_done_callback(lambda _done: _analysis_capacity.release())
    wrapped = asyncio.wrap_future(future)
    try:
        return await asyncio.wait_for(
            asyncio.shield(wrapped), timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise AnalysisTimeoutError from exc


async def _run_analysis(**kwargs: Any) -> AnalysisResult:
    """Compatibility helper for focused analysis-lane lifecycle tests."""
    _reserve_analysis_capacity()
    return await _run_capacity_owned(analyze_sequence, **kwargs)


class CatalogRoute(APIRoute):
    """Keep catalog query-validation failures on the stable public error contract."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def stable_validation_handler(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                if request.method == "POST" and "/digests" in request.url.path:
                    oversized = any(
                        error.get("type") in {"too_long", "string_too_long"}
                        for error in exc.errors()
                    )
                    return JSONResponse(
                        status_code=413 if oversized else 422,
                        content={"detail": {
                            "code": "request_too_large" if oversized else "invalid_digest_request",
                            "message": (
                                "restriction digest request is too large"
                                if oversized else "restriction digest request is invalid"
                            ),
                        }},
                    )
                if request.method == "POST" and request.url.path.endswith("/analyze"):
                    oversized_locations = {
                        ("body", "source", "inline_dna", "dna"),
                        ("body", "scope", "explicit", "enzyme_ids"),
                        ("body", "regions"),
                    }
                    request_too_large = any(
                        tuple(error.get("loc", ())) in oversized_locations
                        and error.get("type") in {"too_long", "string_too_long"}
                        for error in exc.errors()
                    )
                    return JSONResponse(
                        status_code=413 if request_too_large else 422,
                        content={"detail": {
                            "code": "request_too_large" if request_too_large else "invalid_analysis_request",
                            "message": (
                                "restriction analysis request is too large"
                                if request_too_large
                                else "restriction analysis request is invalid"
                            ),
                        }},
                    )
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
    analysis_inline_sequence_max_length: int
    analysis_explicit_enzyme_maximum: int
    analysis_region_maximum: int
    analysis_scan_pattern_maximum: int
    analysis_scan_work_maximum: int
    analysis_occurrence_maximum: int
    analysis_event_maximum: int
    analysis_response_maximum_bytes: int
    analysis_cache_maximum_entries: int
    analysis_cache_maximum_total_weight_bytes: int
    analysis_cache_maximum_result_weight_bytes: int


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
    resource_policy: ResourcePolicyReceipt
    resource_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_enabled: Literal[True]
    digest_enabled: Literal[True]


class CatalogPage(StrictResponse):
    schema_: Literal["bms.molbio.restriction-catalog-page.v1"] = Field(alias="schema")
    catalog: CatalogReceipt
    items: list[RestrictionRecord]
    next_cursor: str | None


class CatalogRecordResponse(StrictResponse):
    schema_: Literal["bms.molbio.restriction-catalog-record.v1"] = Field(alias="schema")
    catalog: CatalogReceipt
    record: RestrictionRecord


class InlineDNASource(StrictResponse):
    kind: Literal["inline_dna"]
    name: str = Field(min_length=1, max_length=128, pattern=r".*\S.*")
    dna: str = Field(min_length=1, max_length=MAX_INLINE_SEQUENCE_LENGTH)
    topology: Literal["linear", "circular"]


class MolecularRevisionSource(StrictResponse):
    kind: Literal["molecular_revision"]
    sequence_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)
    expected_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    topology: Literal["linear", "circular"] | None = None


AnalysisSource = Annotated[
    Union[InlineDNASource, MolecularRevisionSource], Field(discriminator="kind")
]


class AnalysisCatalogBinding(StrictResponse):
    catalog_id: str = Field(min_length=1, max_length=128)
    expected_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AllGeometryReadyScope(StrictResponse):
    mode: Literal["all_geometry_ready"]
    commercial_only: bool = False


class AllAnalysisCapableScope(StrictResponse):
    mode: Literal["all_analysis_capable"]
    commercial_only: bool = False


class ExplicitAnalysisScope(StrictResponse):
    mode: Literal["explicit"]
    enzyme_ids: list[str] = Field(
        min_length=1, max_length=MAX_EXPLICIT_ENZYME_IDS,
        json_schema_extra={"maxItems": MAX_EXPLICIT_ENZYME_IDS},
    )
    commercial_only: bool = False

    @field_validator("enzyme_ids")
    @classmethod
    def unique_bounded_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 128 for item in value) or len(set(value)) != len(value):
            raise ValueError("enzyme IDs must be unique bounded nonempty strings")
        return value


AnalysisScope = Annotated[
    Union[AllGeometryReadyScope, AllAnalysisCapableScope, ExplicitAnalysisScope],
    Field(discriminator="mode"),
]


class AnalysisRegion(StrictResponse):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "AnalysisRegion":
        if self.end <= self.start:
            raise ValueError("region end must be greater than start")
        return self


class AnalysisRequest(StrictResponse):
    schema_: Literal["bms.molbio.restriction-analysis-request.v1"] = Field(alias="schema")
    source: AnalysisSource
    catalog: AnalysisCatalogBinding
    scope: AnalysisScope
    regions: list[AnalysisRegion] = Field(default_factory=list, max_length=MAX_REGIONS)
    include_possible_sites: bool = True
    methylation_policy: Literal["report_only", "require_known"] = "report_only"


class AnalysisSourceReceipt(StrictResponse):
    kind: Literal["inline_dna", "molecular_revision"]
    name: str | None
    sequence_id: str | None
    revision_id: str | None
    revision_number: int | None
    content_sha256: str
    content_length: int
    topology: Literal["linear", "circular"]


class UnsignedAnalysisResponse(StrictResponse):
    schema_: Literal["bms.molbio.restriction-analysis-response.v1"] = Field(alias="schema")
    source: AnalysisSourceReceipt
    catalog: CatalogReceipt
    request_sha256: str
    analysis: AnalysisResult


class AnalysisResponse(UnsignedAnalysisResponse):
    result_sha256: str


class DigestSimulationRequest(StrictResponse):
    schema_: Literal["bms.molbio.restriction-digest-simulation-request.v1"] = Field(alias="schema")
    source: AnalysisSource
    catalog: AnalysisCatalogBinding
    enzyme_ids: list[str] = Field(
        min_length=1, max_length=MAX_SELECTED_ENZYMES,
        json_schema_extra={"maxItems": MAX_SELECTED_ENZYMES},
    )

    @field_validator("enzyme_ids")
    @classmethod
    def unique_enzyme_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item or len(item) > 128 for item in value):
            raise ValueError("enzyme IDs must be unique bounded nonempty strings")
        return value


class DigestSaveRequest(StrictResponse):
    schema_: Literal["bms.molbio.restriction-digest-save-request.v1"] = Field(alias="schema")
    source: MolecularRevisionSource
    catalog: AnalysisCatalogBinding
    enzyme_ids: list[str] = Field(
        min_length=1, max_length=MAX_SELECTED_ENZYMES,
        json_schema_extra={"maxItems": MAX_SELECTED_ENZYMES},
    )
    simulation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    persistence_mode: Literal["operation_only", "operation_and_fragments"]
    fragment_name_prefix: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r".*\S.*",
    )

    @field_validator("enzyme_ids")
    @classmethod
    def unique_enzyme_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item or len(item) > 128 for item in value):
            raise ValueError("enzyme IDs must be unique bounded nonempty strings")
        return value


class DigestOutputIdentity(StrictResponse):
    fragment_index: int
    document_id: str
    revision_id: str
    output_edge_id: str
    name: str
    topology: Literal["linear", "circular"]
    content_sha256: str
    content_length: int


class SavedDigestResponse(StrictResponse):
    schema_: Literal["bms.molbio.restriction-digest-saved-result.v1"] = Field(alias="schema")
    operation_id: str
    source_revision_id: str
    catalog_id: str
    catalog_sha256: str
    request_sha256: str
    result_sha256: str
    simulation: DigestSimulation
    outputs: list[DigestOutputIdentity]


_ANALYZE_EXAMPLE = {
    "schema": "bms.molbio.restriction-analysis-request.v1",
    "source": {
        "kind": "inline_dna", "name": "EcoRI example",
        "dna": "TTGAATTCAA", "topology": "linear",
    },
    "catalog": {
        "catalog_id": "biopython-rebase-404-bms-v1",
        "expected_catalog_sha256": "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f",
    },
    "scope": {"mode": "explicit", "enzyme_ids": ["EcoRI"], "commercial_only": False},
    "regions": [], "include_possible_sites": True, "methylation_policy": "report_only",
}


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
    payload = {key: value for key, value in readiness.items() if key not in {"required", "ready", "status"}}
    payload["digest_enabled"] = True
    return CatalogReceipt.model_validate(payload)


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


def _analysis_records(view: CatalogView, scope: AnalysisScope) -> tuple[RestrictionRecord, ...]:
    if isinstance(scope, ExplicitAnalysisScope):
        missing = [enzyme_id for enzyme_id in scope.enzyme_ids if enzyme_id not in view.by_id]
        if missing:
            raise _error(404, "enzyme_not_found", "restriction enzyme was not found")
        records = tuple(view.by_id[enzyme_id] for enzyme_id in scope.enzyme_ids)
    elif isinstance(scope, AllGeometryReadyScope):
        records = view.by_capability.get("digest_simulation", ())
    else:
        records = view.ordered_records
    if scope.commercial_only:
        records = tuple(row for row in records if row.supplier_provenance.reported_commercial)
    return tuple(sorted(records, key=lambda row: (
        row.canonical_name.casefold(), row.enzyme_id.casefold()
    )))


@dataclass(frozen=True, slots=True)
class _ResolvedRevisionSource:
    document_id: str
    document_name: str
    revision_id: str
    revision_number: int
    stored_content_sha256: str
    stored_content_length: int
    sequence_type: str
    sequence: str
    is_circular: bool | None
    topology: str | None


@dataclass(frozen=True, slots=True)
class _CanonicalAnalysisOutput:
    response: AnalysisResponse
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class _CanonicalSimulationOutput:
    simulation: DigestSimulation
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class _FragmentIds:
    document_id: str
    revision_id: str
    edge_id: str


@dataclass(frozen=True, slots=True)
class _PreparedFragment:
    document_id: str
    revision_id: str
    edge_id: str
    fragment_index: int
    name: str
    content_sha256: str
    content_length: int
    revision_snapshot_json: str
    revision_provenance_json: str
    edge_snapshot_json: str


@dataclass(frozen=True, slots=True)
class _PreparedSavedDigest:
    fragments: tuple[_PreparedFragment, ...]
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class _OperationSnapshot:
    operation_kind: object
    implementation: object
    implementation_version: object
    status: object
    parameters: object
    warnings: object
    provenance: object
    idempotency_key: object
    request_fingerprint: object


@dataclass(frozen=True, slots=True)
class _RevisionSnapshot:
    id: object
    document_id: object
    revision_number: object
    change_kind: object
    content_sha256: object
    content_length: object
    snapshot: object
    provenance: object
    operation_id: object
    created_by: object


@dataclass(frozen=True, slots=True)
class _DocumentSnapshot:
    id: object
    document_kind: object


@dataclass(frozen=True, slots=True)
class _InputSnapshot:
    position: object
    role: object
    revision_id: object
    snapshot: object


@dataclass(frozen=True, slots=True)
class _OutputSnapshot:
    id: object
    position: object
    role: object
    revision_id: object
    snapshot: object


@dataclass(frozen=True, slots=True)
class _SavedDigestSnapshot:
    raw_text: object
    operation_id: object
    source_revision_id: object
    catalog_id: object
    catalog_sha256: object
    request_sha256: object
    result_sha256: object
    operation: _OperationSnapshot | None
    source_revision: _RevisionSnapshot | None
    source_document: _DocumentSnapshot | None
    inputs: tuple[_InputSnapshot, ...]
    outputs: tuple[_OutputSnapshot, ...]
    output_revisions: tuple[_RevisionSnapshot, ...]
    output_documents: tuple[_DocumentSnapshot, ...]


async def _resolve_revision_source(
    source: MolecularRevisionSource, session: AsyncSession,
) -> _ResolvedRevisionSource:
    """Snapshot ORM-owned revision fields without sharing a Session with workers."""
    document = await session.get(MolecularDocument, source.sequence_id)
    revision = await session.get(MolecularRevision, source.revision_id)
    if document is None or revision is None or revision.document_id != document.id:
        raise _error(404, "source_revision_not_found", "molecular revision was not found")
    snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
    return _ResolvedRevisionSource(
        document_id=str(document.id),
        document_name=str(document.name),
        revision_id=str(revision.id),
        revision_number=int(revision.revision_number),
        stored_content_sha256=str(revision.content_sha256),
        stored_content_length=int(revision.content_length),
        sequence_type=str(snapshot.get("sequence_type") or ""),
        sequence=str(snapshot.get("sequence") or ""),
        is_circular=(snapshot.get("is_circular") if isinstance(snapshot.get("is_circular"), bool) else None),
        topology=(snapshot.get("topology") if snapshot.get("topology") in {"linear", "circular"} else None),
    )


def _analysis_source(
    source: AnalysisSource,
    resolved_revision: _ResolvedRevisionSource | None,
) -> tuple[str, Literal["linear", "circular"], AnalysisSourceReceipt]:
    if isinstance(source, InlineDNASource):
        try:
            sequence = normalize_dna(source.dna)
        except InvalidDNAError as exc:
            raise _error(422, "invalid_dna", "DNA input is invalid") from exc
        digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        return sequence, source.topology, AnalysisSourceReceipt(
            kind="inline_dna", name=source.name.strip(), sequence_id=None, revision_id=None,
            revision_number=None, content_sha256=digest, content_length=len(sequence),
            topology=source.topology,
        )

    if resolved_revision is None:
        raise RuntimeError("resolved molecular revision is missing")
    if resolved_revision.sequence_type.lower() != "dna":
        raise _error(422, "invalid_dna", "molecular revision is not DNA")
    try:
        sequence = normalize_dna(resolved_revision.sequence)
    except InvalidDNAError as exc:
        raise _error(422, "invalid_dna", "molecular revision DNA is invalid") from exc
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    if (
        resolved_revision.stored_content_sha256 != source.expected_content_sha256
        or digest != resolved_revision.stored_content_sha256
        or resolved_revision.stored_content_length != len(sequence)
    ):
        raise _error(
            409, "source_revision_digest_mismatch",
            "molecular revision content digest or length does not match",
        )
    owned_topology: Literal["linear", "circular"] | None = None
    if resolved_revision.is_circular is not None:
        owned_topology = "circular" if resolved_revision.is_circular else "linear"
    elif resolved_revision.topology in {"linear", "circular"}:
        owned_topology = cast(Literal["linear", "circular"], resolved_revision.topology)
    topology = owned_topology
    if topology is None or source.topology not in {None, owned_topology}:
        raise _error(422, "invalid_dna", "molecular revision topology authority is invalid")
    return sequence, topology, AnalysisSourceReceipt(
        kind="molecular_revision", name=resolved_revision.document_name,
        sequence_id=resolved_revision.document_id, revision_id=resolved_revision.revision_id,
        revision_number=resolved_revision.revision_number, content_sha256=digest,
        content_length=len(sequence), topology=topology,
    )


def _complete_analysis_pipeline(
    *,
    payload: AnalysisRequest,
    authority: CatalogAuthority,
    resolved_revision: _ResolvedRevisionSource | None,
) -> _CanonicalAnalysisOutput:
    """Own preprocessing, analysis, model authority, hashing, and final bytes."""
    view = _require_view(authority)
    if payload.catalog.catalog_id != view.catalog_id:
        raise _error(404, "catalog_not_found", "restriction catalog was not found")
    if payload.catalog.expected_catalog_sha256 != view.content_sha256:
        raise _error(409, "catalog_digest_mismatch", "restriction catalog digest does not match")
    if payload.methylation_policy == "require_known":
        raise _error(
            409, "product_evidence_unavailable",
            "approved product evidence is unavailable in Phase 2",
        )
    sequence, topology, source_receipt = _analysis_source(payload.source, resolved_revision)
    records = _analysis_records(view, payload.scope)
    normalized_request = payload.model_dump(mode="json", by_alias=True)
    normalized_request["source"] = {
        **normalized_request["source"],
        **({"dna": sequence, "name": source_receipt.name} if source_receipt.kind == "inline_dna" else {}),
    }
    if isinstance(payload.scope, ExplicitAnalysisScope):
        normalized_request["scope"]["enzyme_ids"] = sorted(payload.scope.enzyme_ids)
    normalized_request["regions"] = sorted(
        normalized_request["regions"], key=lambda row: (row["start"], row["end"]),
    )
    policy_receipt = resource_policy_receipt()
    policy_sha256 = resource_policy_sha256(
        policy_receipt.model_dump(mode="json", by_alias=True)
    )
    request_sha256 = hashlib.sha256(rfc8785.dumps({
        "request": normalized_request,
        "resource_policy_sha256": policy_sha256,
    })).hexdigest()
    analysis = analyze_sequence(
        sequence=sequence,
        topology=topology,
        catalog=view,
        records=records,
        include_possible_sites=payload.include_possible_sites,
        regions=tuple((region.start, region.end) for region in payload.regions),
    )
    catalog_receipt = _receipt(authority)
    unsigned_response = UnsignedAnalysisResponse(
        schema="bms.molbio.restriction-analysis-response.v1",
        source=source_receipt,
        catalog=catalog_receipt,
        request_sha256=request_sha256,
        analysis=analysis,
    )
    unsigned_document = unsigned_response.model_dump(mode="json", by_alias=True)
    result_sha256 = hashlib.sha256(rfc8785.dumps(unsigned_document)).hexdigest()
    response = AnalysisResponse.model_validate({
        **unsigned_document,
        "result_sha256": result_sha256,
    })
    canonical_bytes = rfc8785.dumps(response.model_dump(mode="json", by_alias=True))
    if len(canonical_bytes) > MAX_RESPONSE_BYTES:
        raise AnalysisLimitError("analysis response exceeds byte limit")
    return _CanonicalAnalysisOutput(response=response, canonical_bytes=canonical_bytes)


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_restriction_sites(
    payload: Annotated[
        AnalysisRequest,
        Body(openapi_examples={"inline_dna": {"summary": "Inline DNA", "value": _ANALYZE_EXAMPLE}}),
    ],
    authority: CatalogAuthority = Depends(get_catalog_authority),
    molbio_session: AsyncSession = Depends(get_molbio_session),
) -> Response:
    try:
        _reserve_analysis_capacity()
    except AnalysisBusyError as exc:
        raise _error(503, "analysis_busy", "restriction analysis capacity is busy") from exc

    try:
        resolved_revision = (
            await _resolve_revision_source(payload.source, molbio_session)
            if isinstance(payload.source, MolecularRevisionSource)
            else None
        )
    except BaseException:
        _analysis_capacity.release()
        raise

    try:
        output = await _run_capacity_owned(
            _complete_analysis_pipeline,
            payload=payload,
            authority=authority,
            resolved_revision=resolved_revision,
        )
    except HTTPException:
        raise
    except InvalidDNAError as exc:
        raise _error(422, "invalid_dna", "DNA input or regions are invalid") from exc
    except AnalysisLimitError as exc:
        raise _error(413, "request_too_large", "restriction analysis request is too large") from exc
    except AnalysisTimeoutError as exc:
        raise _error(504, "analysis_timeout", "restriction analysis exceeded its wait timeout") from exc
    except Exception as exc:
        raise _error(500, "analysis_failed", "restriction analysis failed") from exc
    return Response(content=output.canonical_bytes, media_type="application/json")


def _digest_records(view: CatalogView, enzyme_ids: list[str]) -> tuple[RestrictionRecord, ...]:
    missing = [enzyme_id for enzyme_id in enzyme_ids if enzyme_id not in view.by_id]
    if missing:
        raise _error(404, "enzyme_not_found", "restriction enzyme was not found")
    return tuple(view.by_id[enzyme_id] for enzyme_id in enzyme_ids)


def _complete_digest_pipeline(
    *, payload: DigestSimulationRequest, authority: CatalogAuthority,
    resolved_revision: _ResolvedRevisionSource | None,
) -> _CanonicalSimulationOutput:
    view = _require_view(authority)
    if payload.catalog.catalog_id != view.catalog_id:
        raise _error(404, "catalog_not_found", "restriction catalog was not found")
    if payload.catalog.expected_catalog_sha256 != view.content_sha256:
        raise _error(409, "catalog_digest_mismatch", "restriction catalog digest does not match")
    sequence, topology, source_receipt = _analysis_source(payload.source, resolved_revision)
    simulation, canonical = simulate_digest_canonical(
        sequence=sequence, topology=topology, catalog=view,
        records=_digest_records(view, payload.enzyme_ids),
        selected_enzyme_ids=tuple(payload.enzyme_ids),
        source_receipt=source_receipt.model_dump(mode="json", by_alias=True),
        catalog_receipt=_receipt(authority).model_dump(mode="json", by_alias=True),
    )
    return _CanonicalSimulationOutput(simulation=simulation, canonical_bytes=canonical)


def _digest_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DigestGeometryError):
        return _error(409, exc.code, str(exc))
    if isinstance(exc, (DigestLimitError, AnalysisLimitError)):
        return _error(413, "request_too_large", "restriction digest request is too large")
    if isinstance(exc, InvalidDNAError):
        return _error(422, "invalid_dna", "DNA input is invalid")
    if isinstance(exc, AnalysisTimeoutError):
        return _error(504, "analysis_timeout", "restriction digest exceeded its wait timeout")
    return _error(500, "digest_failed", "restriction digest failed")


@router.post("/digests/simulate", response_model=DigestSimulation)
async def simulate_restriction_digest(
    payload: DigestSimulationRequest,
    authority: CatalogAuthority = Depends(get_catalog_authority),
    molbio_session: AsyncSession = Depends(get_molbio_session),
) -> Response:
    try:
        _reserve_analysis_capacity()
    except AnalysisBusyError as exc:
        raise _error(503, "analysis_busy", "restriction digest capacity is busy") from exc
    try:
        resolved = (
            await _resolve_revision_source(payload.source, molbio_session)
            if isinstance(payload.source, MolecularRevisionSource) else None
        )
    except BaseException:
        _analysis_capacity.release()
        raise
    try:
        output = await _run_capacity_owned(
            _complete_digest_pipeline, payload=payload, authority=authority,
            resolved_revision=resolved,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _digest_http_error(exc) from exc
    return Response(content=output.canonical_bytes, media_type="application/json")


def _save_receipt(payload: DigestSaveRequest) -> str:
    return canonical_save_request_receipt(payload.model_dump(mode="json", by_alias=True))


def _save_fingerprint(payload: DigestSaveRequest) -> str:
    return save_request_fingerprint(_save_receipt(payload))


def _digest_persistence_stage_hook(_stage: str) -> None:
    """Transaction-bound fault-injection seam; production behavior is a no-op."""


def _parse_saved_digest_result(
    raw_text: str,
    *,
    operation_id: str,
    source_revision_id: str,
    catalog_id: str,
    catalog_sha256: str,
    request_sha256: str,
    result_sha256: str,
) -> SavedDigestResponse:
    raw = raw_text.encode("utf-8")
    document = json.loads(raw)
    if raw != rfc8785.dumps(document):
        raise ValueError("noncanonical result")
    response = SavedDigestResponse.model_validate_json(raw, strict=True)
    if (
        response.operation_id != operation_id
        or response.source_revision_id != source_revision_id
        or response.catalog_id != catalog_id
        or response.catalog_sha256 != catalog_sha256
        or response.request_sha256 != request_sha256
        or response.result_sha256 != result_sha256
        or response.simulation.simulation_sha256 != response.result_sha256
        or hashlib.sha256(response.simulation.canonical_unsigned_bytes()).hexdigest()
        != response.result_sha256
    ):
        raise ValueError("row binding mismatch")
    return response


def _serialize_saved_digest_response(response: SavedDigestResponse) -> bytes:
    canonical = rfc8785.dumps(response.model_dump(mode="json", by_alias=True))
    if len(canonical) > MAX_SIMULATION_RESPONSE_BYTES:
        raise DigestLimitError("saved digest response exceeds digest byte limit")
    return canonical


def _prepare_saved_digest_fragments(
    *, simulation: DigestSimulation, fragment_ids: tuple[_FragmentIds, ...],
    operation_id: str, source_revision_id: str, catalog_id: str,
    catalog_sha256: str, fragment_name_prefix: str | None,
) -> _PreparedSavedDigest:
    """Prepare every output record and the exact response in one CPU worker."""
    fragments = simulation.fragments
    if (
        len(fragments) > MAX_FRAGMENTS
        or len(fragment_ids) > MAX_SAVED_OUTPUTS
    ):
        raise DigestLimitError("saved digest output count exceeds digest limit")
    if len(fragment_ids) not in {0, len(fragments)}:
        raise ValueError("fragment identity cardinality mismatch")
    if sum(len(fragment.top_strand_sequence) for fragment in fragments) > MAX_TOTAL_FRAGMENT_BASES:
        raise DigestLimitError("saved digest output bases exceed digest limit")

    prefix = fragment_name_prefix or f"{simulation.source.name} digest fragment"
    prepared: list[_PreparedFragment] = []
    identities: list[DigestOutputIdentity] = []
    fragment_pairs = zip(fragments, fragment_ids, strict=True) if fragment_ids else ()
    for fragment, ids in fragment_pairs:
        ordinal = fragment.fragment_index
        name = f"{prefix} {ordinal + 1}"
        sequence = fragment.top_strand_sequence
        content_sha256 = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        revision_snapshot: dict[str, object] = {
            "sequence_type": "dna", "sequence": sequence,
            "is_circular": fragment.topology == "circular",
            "topology": fragment.topology, "name": name,
        }
        revision_provenance: dict[str, object] = {
            "schema": "bms.molbio.restriction-digest-fragment-provenance.v1",
            "source_revision_id": source_revision_id, "operation_id": operation_id,
            "simulation_sha256": simulation.simulation_sha256,
            "fragment_index": ordinal,
            "geometry": fragment.model_dump(mode="json", by_alias=True),
        }
        edge_snapshot: dict[str, object] = {
            "fragment_index": ordinal, "name": name,
            "simulation_sha256": simulation.simulation_sha256,
        }
        prepared.append(_PreparedFragment(
            document_id=ids.document_id, revision_id=ids.revision_id, edge_id=ids.edge_id,
            fragment_index=ordinal, name=name, content_sha256=content_sha256,
            content_length=len(sequence),
            revision_snapshot_json=rfc8785.dumps(revision_snapshot).decode("utf-8"),
            revision_provenance_json=rfc8785.dumps(revision_provenance).decode("utf-8"),
            edge_snapshot_json=rfc8785.dumps(edge_snapshot).decode("utf-8"),
        ))
        identities.append(DigestOutputIdentity(
            fragment_index=ordinal, document_id=ids.document_id,
            revision_id=ids.revision_id, output_edge_id=ids.edge_id, name=name,
            topology=fragment.topology, content_sha256=content_sha256,
            content_length=len(sequence),
        ))
    response = SavedDigestResponse(
        schema="bms.molbio.restriction-digest-saved-result.v1",
        operation_id=operation_id, source_revision_id=source_revision_id,
        catalog_id=catalog_id, catalog_sha256=catalog_sha256,
        request_sha256=simulation.request_sha256,
        result_sha256=simulation.simulation_sha256,
        simulation=simulation, outputs=identities,
    )
    return _PreparedSavedDigest(
        fragments=tuple(prepared),
        canonical_bytes=_serialize_saved_digest_response(response),
    )


def _strict_json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        assert isinstance(right, dict)
        return left.keys() == right.keys() and all(
            _strict_json_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        assert isinstance(right, list)
        return len(left) == len(right) and all(
            _strict_json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _load_json_value(raw: object, expected_type: type, label: str) -> object:
    if not isinstance(raw, str):
        raise ValueError(f"invalid {label} JSON storage")

    value = json.loads(raw)
    if type(value) is not expected_type:
        raise ValueError(f"invalid {label} JSON shape")
    return value


def _validate_saved_digest_snapshot(*, snapshot: _SavedDigestSnapshot) -> _PreparedSavedDigest:
    """Validate an ORM-free immutable snapshot and return exact canonical bytes."""
    result_scalars = (
        snapshot.raw_text, snapshot.operation_id, snapshot.source_revision_id,
        snapshot.catalog_id, snapshot.catalog_sha256, snapshot.request_sha256,
        snapshot.result_sha256,
    )
    if not all(isinstance(value, str) for value in result_scalars):
        raise ValueError("invalid saved digest scalar evidence")
    response = _parse_saved_digest_result(
        snapshot.raw_text, operation_id=snapshot.operation_id,
        source_revision_id=snapshot.source_revision_id, catalog_id=snapshot.catalog_id,
        catalog_sha256=snapshot.catalog_sha256, request_sha256=snapshot.request_sha256,
        result_sha256=snapshot.result_sha256,
    )
    operation = snapshot.operation
    source_revision = snapshot.source_revision
    source_document = snapshot.source_document
    if operation is None:
        raise ValueError("missing operation evidence")
    operation_parameters = cast(
        dict[str, object], _load_json_value(operation.parameters, dict, "operation parameters")
    )
    operation_warnings = tuple(cast(list[object], _load_json_value(
        operation.warnings, list, "operation warnings"
    )))
    operation_provenance = cast(
        dict[str, object], _load_json_value(operation.provenance, dict, "operation provenance")
    )
    save_receipt = load_canonical_save_request_receipt(operation_parameters.get("save_request_receipt"))
    fragment_name_prefix = operation_parameters.get("fragment_name_prefix")
    if fragment_name_prefix is not None and (
        not isinstance(fragment_name_prefix, str) or not fragment_name_prefix.strip()
    ):
        raise ValueError("invalid fragment name prefix")
    fragment_name_prefix = fragment_name_prefix.strip() if isinstance(fragment_name_prefix, str) else None
    expected_operation_parameters = {
        "schema": "bms.molbio.restriction-digest-operation-parameters.v1",
        "selected_enzyme_ids": list(response.simulation.selected_enzyme_ids),
        "persistence_mode": operation_parameters.get("persistence_mode"),
        "fragment_name_prefix": fragment_name_prefix,
        "simulation_sha256": response.simulation.simulation_sha256,
        "save_request_receipt": operation_parameters.get("save_request_receipt"),
    }
    expected_operation_provenance = {
        "source_revision_id": response.source_revision_id, "catalog_id": response.catalog_id,
        "catalog_sha256": response.catalog_sha256, "request_sha256": response.request_sha256,
    }
    persistence_mode = operation_parameters.get("persistence_mode")
    expected_output_count = (
        0 if persistence_mode == "operation_only" else len(response.simulation.fragments)
        if persistence_mode == "operation_and_fragments" else -1
    )
    source_data = (
        cast(dict[str, object], _load_json_value(
            source_revision.snapshot, dict, "source revision snapshot"
        ))
        if source_revision is not None else {}
    )
    if isinstance(source_data.get("is_circular"), bool):
        source_topology = "circular" if source_data["is_circular"] else "linear"
    elif source_data.get("topology") in {"linear", "circular"}:
        source_topology = source_data["topology"]
    else:
        source_topology = None
    source_sequence = source_data.get("sequence")
    source_bytes = source_sequence.encode("ascii") if isinstance(source_sequence, str) else b""
    revisions_by_id = {row.id: row for row in snapshot.output_revisions}
    documents_by_id = {row.id: row for row in snapshot.output_documents}
    expected_revision_ids = {identity.revision_id for identity in response.outputs}
    expected_document_ids = {identity.document_id for identity in response.outputs}
    input_snapshot = (
        cast(dict[str, object], _load_json_value(
            snapshot.inputs[0].snapshot, dict, "operation input snapshot"
        ))
        if len(snapshot.inputs) == 1 else {}
    )
    simulation_fragment_count = len(response.simulation.fragments)

    simulation_total_bases = sum(
        len(fragment.top_strand_sequence) for fragment in response.simulation.fragments
    )
    if (
        simulation_fragment_count > MAX_FRAGMENTS
        or simulation_total_bases > MAX_TOTAL_FRAGMENT_BASES
    ):
        raise DigestLimitError("saved digest simulation exceeds digest limits")
    if len(response.outputs) > MAX_SAVED_OUTPUTS:
        raise DigestLimitError("saved digest output count exceeds digest limit")
    if (
        operation is None or operation.operation_kind != "restriction_digest"
        or operation.implementation != "services.restriction_digest.simulate_digest"
        or operation.implementation_version != response.simulation.digest_algorithm_version
        or operation.status != "completed"
        or not _strict_json_equal(operation_parameters, expected_operation_parameters)
        or operation_warnings != tuple(response.simulation.warnings)
        or not _strict_json_equal(operation_provenance, expected_operation_provenance)
        or not isinstance(operation.idempotency_key, str) or not operation.idempotency_key.strip()
        or not isinstance(operation.request_fingerprint, str)
        or operation.request_fingerprint != save_request_fingerprint(save_receipt)
        or save_receipt.source.sequence_id != response.simulation.source.sequence_id
        or save_receipt.source.revision_id != response.source_revision_id
        or save_receipt.source.expected_content_sha256 != response.simulation.source.content_sha256
        or save_receipt.source.topology not in {None, response.simulation.source.topology}
        or save_receipt.catalog.catalog_id != response.catalog_id
        or save_receipt.catalog.expected_catalog_sha256 != response.catalog_sha256
        or save_receipt.enzyme_ids != tuple(response.simulation.selected_enzyme_ids)
        or save_receipt.simulation_sha256 != response.result_sha256
        or save_receipt.idempotency_key != operation.idempotency_key
        or save_receipt.persistence_mode != persistence_mode
        or save_receipt.fragment_name_prefix != fragment_name_prefix
        or source_revision is None or source_document is None
        or source_revision.document_id != response.simulation.source.sequence_id
        or source_revision.revision_number != response.simulation.source.revision_number
        or source_revision.content_sha256 != response.simulation.source.content_sha256
        or source_revision.content_length != response.simulation.source.content_length
        or source_document.document_kind != "dna"
        or not isinstance(source_data.get("sequence_type"), str)
        or source_data["sequence_type"].lower() != "dna"
        or not isinstance(source_sequence, str)
        or hashlib.sha256(source_bytes).hexdigest() != response.simulation.source.content_sha256
        or len(source_sequence) != response.simulation.source.content_length
        or source_topology != response.simulation.source.topology
        or len(snapshot.inputs) != 1 or snapshot.inputs[0].position != 0
        or snapshot.inputs[0].role != "digest_source"
        or snapshot.inputs[0].revision_id != response.source_revision_id
        or not _strict_json_equal(input_snapshot, {
            "content_sha256": response.simulation.source.content_sha256,
            "name": response.simulation.source.name,
            "sequence_id": response.simulation.source.sequence_id,
        })
        or len(response.outputs) != expected_output_count
        or len(snapshot.outputs) != expected_output_count
        or len(revisions_by_id) != len(snapshot.output_revisions)
        or len(documents_by_id) != len(snapshot.output_documents)
        or set(revisions_by_id) != expected_revision_ids
        or set(documents_by_id) != expected_document_ids
        or expected_output_count > MAX_SAVED_OUTPUTS
    ):
        raise ValueError("lineage cardinality mismatch")
    total_bases = 0
    for ordinal, (edge, identity) in enumerate(zip(snapshot.outputs, response.outputs, strict=True)):
        revision = revisions_by_id.get(identity.revision_id)
        document = documents_by_id.get(identity.document_id)
        fragment = response.simulation.fragments[ordinal]
        edge_snapshot = cast(dict[str, object], _load_json_value(
            edge.snapshot, dict, "operation output snapshot"
        ))
        revision_snapshot = (
            cast(dict[str, object], _load_json_value(
                revision.snapshot, dict, "output revision snapshot"
            )) if revision is not None else {}
        )
        revision_provenance = (
            cast(dict[str, object], _load_json_value(
                revision.provenance, dict, "output revision provenance"
            )) if revision is not None else {}
        )
        prefix = fragment_name_prefix or f"{response.simulation.source.name} digest fragment"
        expected_name = f"{prefix} {ordinal + 1}"
        sequence = fragment.top_strand_sequence
        total_bases += len(sequence)
        content_sha256 = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        expected_snapshot = {
            "sequence_type": "dna", "sequence": sequence,
            "is_circular": fragment.topology == "circular",
            "topology": fragment.topology, "name": expected_name,
        }
        expected_provenance = {
            "schema": "bms.molbio.restriction-digest-fragment-provenance.v1",
            "source_revision_id": response.source_revision_id,
            "operation_id": snapshot.operation_id,
            "simulation_sha256": response.result_sha256, "fragment_index": ordinal,
            "geometry": fragment.model_dump(mode="json", by_alias=True),
        }
        expected_identity = {
            "fragment_index": ordinal, "document_id": document.id if document else "",
            "revision_id": revision.id if revision else "", "output_edge_id": edge.id,
            "name": expected_name, "topology": fragment.topology,
            "content_sha256": content_sha256, "content_length": len(sequence),
        }
        if (
            edge.position != ordinal or edge.role != "digest_fragment"
            or edge.id != identity.output_edge_id or edge.revision_id != identity.revision_id
            or not _strict_json_equal(edge_snapshot, {
                "fragment_index": ordinal, "name": expected_name,
                "simulation_sha256": response.result_sha256,
            })
            or revision is None or document is None
            or revision.document_id != identity.document_id or document.document_kind != "dna"
            or revision.operation_id != snapshot.operation_id or revision.revision_number != 1
            or revision.change_kind != "restriction_digest_fragment"
            or revision.content_sha256 != identity.content_sha256
            or revision.content_length != identity.content_length or revision.created_by is not None
            or not _strict_json_equal(revision_snapshot, expected_snapshot)
            or not _strict_json_equal(revision_provenance, expected_provenance)
            or not _strict_json_equal(identity.model_dump(mode="json"), expected_identity)
        ):
            raise ValueError("fragment lineage mismatch")
    if total_bases > MAX_TOTAL_FRAGMENT_BASES:
        raise DigestLimitError("saved digest output bases exceed digest limit")
    return _PreparedSavedDigest(
        fragments=(), canonical_bytes=_serialize_saved_digest_response(response)
    )


async def _run_digest_cpu(function: Callable[..., Any], **kwargs: Any) -> Any:
    try:
        _reserve_analysis_capacity()
    except AnalysisBusyError as exc:
        raise _error(503, "analysis_busy", "restriction digest capacity is busy") from exc
    try:
        return await _run_capacity_owned(function, **kwargs)
    except AnalysisTimeoutError as exc:
        raise _error(504, "analysis_timeout", "restriction digest exceeded its wait timeout") from exc
    except DigestLimitError as exc:
        raise _error(413, "request_too_large", "restriction digest request is too large") from exc


async def _load_saved_digest(
    session: AsyncSession, operation_id: str,
) -> bytes:
    result = (
        await session.execute(
            select(
                RestrictionDigestResult.operation_id,
                RestrictionDigestResult.source_revision_id, RestrictionDigestResult.catalog_id,
                RestrictionDigestResult.catalog_sha256, RestrictionDigestResult.request_sha256,
                RestrictionDigestResult.result_sha256,
                func.length(sql_cast(RestrictionDigestResult.result, LargeBinary)).label(
                    "result_bytes"
                ),
            ).where(RestrictionDigestResult.operation_id == operation_id)
        )
    ).mappings().one_or_none()
    if result is None:
        raise _error(404, "digest_operation_not_found", "saved restriction digest was not found")
    try:
        result_bytes = result["result_bytes"]
        if not isinstance(result_bytes, int) or result_bytes < 0:
            raise ValueError("invalid saved digest result size")
        if result_bytes > MAX_SIMULATION_RESPONSE_BYTES:
            raise _error(
                413, "request_too_large", "restriction digest request is too large"
            )
        raw_text = await session.scalar(select(RestrictionDigestResult.result).where(
            RestrictionDigestResult.operation_id == operation_id,
            func.length(sql_cast(RestrictionDigestResult.result, LargeBinary))
            <= MAX_SIMULATION_RESPONSE_BYTES,
        ))
        if raw_text is None:
            raise ValueError("saved digest result changed during bounded load")
        operation = (
            await session.execute(
                select(
                    MolecularOperation.operation_kind, MolecularOperation.implementation,
                    MolecularOperation.implementation_version, MolecularOperation.status,
                    sql_cast(MolecularOperation.parameters, String).label("parameters"),
                    sql_cast(MolecularOperation.warnings, String).label("warnings"),
                    sql_cast(MolecularOperation.provenance, String).label("provenance"),
                    MolecularOperation.idempotency_key, MolecularOperation.request_fingerprint,
                ).where(MolecularOperation.id == operation_id)
            )
        ).mappings().one_or_none()
        source_revision = (
            await session.execute(
                select(
                    MolecularRevision.id, MolecularRevision.document_id,
                    MolecularRevision.revision_number, MolecularRevision.change_kind,
                    MolecularRevision.content_sha256, MolecularRevision.content_length,
                    sql_cast(MolecularRevision.snapshot, String).label("snapshot"),
                    sql_cast(MolecularRevision.provenance, String).label("provenance"),
                    MolecularRevision.operation_id, MolecularRevision.created_by,
                ).where(MolecularRevision.id == result["source_revision_id"])
            )
        ).mappings().one_or_none()
        source_document = (
            (await session.execute(
                select(MolecularDocument.id, MolecularDocument.document_kind).where(
                    MolecularDocument.id == source_revision["document_id"]
                )
            )).mappings().one_or_none()
            if source_revision is not None else None
        )
        inputs = (await session.execute(
            select(
                MolecularOperationInput.position, MolecularOperationInput.role,
                MolecularOperationInput.revision_id,
                sql_cast(MolecularOperationInput.snapshot, String).label("snapshot"),
            ).where(MolecularOperationInput.operation_id == operation_id).order_by(
                MolecularOperationInput.position
            ).limit(2)
        )).mappings().all()
        outputs = (await session.execute(
            select(
                MolecularOperationOutput.id, MolecularOperationOutput.position,
                MolecularOperationOutput.role, MolecularOperationOutput.revision_id,
                sql_cast(MolecularOperationOutput.snapshot, String).label("snapshot"),
            ).where(MolecularOperationOutput.operation_id == operation_id).order_by(
                MolecularOperationOutput.position
            ).limit(MAX_SAVED_OUTPUTS + 1)
        )).mappings().all()
        output_revisions = (await session.execute(
            select(
                MolecularRevision.id, MolecularRevision.document_id,
                MolecularRevision.revision_number, MolecularRevision.change_kind,
                MolecularRevision.content_sha256, MolecularRevision.content_length,
                sql_cast(MolecularRevision.snapshot, String).label("snapshot"),
                sql_cast(MolecularRevision.provenance, String).label("provenance"),
                MolecularRevision.operation_id, MolecularRevision.created_by,
            ).where(MolecularRevision.operation_id == operation_id).limit(
                MAX_SAVED_OUTPUTS + 1
            )
        )).mappings().all()
        output_documents = (await session.execute(
            select(MolecularDocument.id, MolecularDocument.document_kind).join(
                MolecularRevision, MolecularRevision.document_id == MolecularDocument.id,
            ).where(MolecularRevision.operation_id == operation_id).limit(
                MAX_SAVED_OUTPUTS + 1
            )
        )).mappings().all()

        def revision_snapshot(row: Mapping[str, object]) -> _RevisionSnapshot:
            return _RevisionSnapshot(
                id=row["id"], document_id=row["document_id"],
                revision_number=row["revision_number"], change_kind=row["change_kind"],
                content_sha256=row["content_sha256"], content_length=row["content_length"],
                snapshot=row["snapshot"], provenance=row["provenance"],
                operation_id=row["operation_id"], created_by=row["created_by"],
            )

        snapshot = _SavedDigestSnapshot(
            raw_text=raw_text, operation_id=result["operation_id"],
            source_revision_id=result["source_revision_id"], catalog_id=result["catalog_id"],
            catalog_sha256=result["catalog_sha256"], request_sha256=result["request_sha256"],
            result_sha256=result["result_sha256"],
            operation=(
                _OperationSnapshot(
                    operation_kind=operation["operation_kind"],
                    implementation=operation["implementation"],
                    implementation_version=operation["implementation_version"],
                    status=operation["status"], parameters=operation["parameters"],
                    warnings=operation["warnings"], provenance=operation["provenance"],
                    idempotency_key=operation["idempotency_key"],
                    request_fingerprint=operation["request_fingerprint"],
                ) if operation is not None else None
            ),
            source_revision=revision_snapshot(source_revision) if source_revision is not None else None,
            source_document=(
                _DocumentSnapshot(
                    id=source_document["id"], document_kind=source_document["document_kind"],
                ) if source_document is not None else None
            ),
            inputs=tuple(
                _InputSnapshot(
                    position=row["position"], role=row["role"],
                    revision_id=row["revision_id"], snapshot=row["snapshot"],
                ) for row in inputs
            ),
            outputs=tuple(
                _OutputSnapshot(
                    id=row["id"], position=row["position"], role=row["role"],
                    revision_id=row["revision_id"], snapshot=row["snapshot"],
                ) for row in outputs
            ),
            output_revisions=tuple(revision_snapshot(row) for row in output_revisions),
            output_documents=tuple(
                _DocumentSnapshot(id=row["id"], document_kind=row["document_kind"])
                for row in output_documents
            ),
        )
        validated = await _run_digest_cpu(_validate_saved_digest_snapshot, snapshot=snapshot)
        return validated.canonical_bytes
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(
            409, "digest_result_integrity_error",
            "saved restriction digest evidence failed integrity verification",
        ) from exc


@router.post("/digests", response_model=SavedDigestResponse)
async def save_restriction_digest(
    payload: DigestSaveRequest,
    authority: CatalogAuthority = Depends(get_catalog_authority),
    molbio_session: AsyncSession = Depends(get_molbio_session),
) -> Response:
    save_receipt = _save_receipt(payload)
    fingerprint = save_request_fingerprint(save_receipt)
    existing = (
        await molbio_session.execute(
            select(MolecularOperation).where(
                MolecularOperation.idempotency_key == payload.idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.operation_kind != "restriction_digest" or existing.request_fingerprint != fingerprint:
            raise _error(409, "idempotency_conflict", "idempotency key is bound to another request")
        canonical = await _load_saved_digest(molbio_session, str(existing.id))
        return Response(content=canonical, media_type="application/json")

    try:
        _reserve_analysis_capacity()
    except AnalysisBusyError as exc:
        raise _error(503, "analysis_busy", "restriction digest capacity is busy") from exc
    try:
        resolved = await _resolve_revision_source(payload.source, molbio_session)
    except BaseException:
        _analysis_capacity.release()
        raise
    simulation_request = DigestSimulationRequest(
        schema="bms.molbio.restriction-digest-simulation-request.v1",
        source=payload.source, catalog=payload.catalog, enzyme_ids=payload.enzyme_ids,
    )
    try:
        simulation_output = await _run_capacity_owned(
            _complete_digest_pipeline, payload=simulation_request, authority=authority,
            resolved_revision=resolved,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _digest_http_error(exc) from exc
    simulation = simulation_output.simulation
    if simulation.simulation_sha256 != payload.simulation_sha256:
        raise _error(409, "simulation_digest_mismatch", "digest simulation digest does not match")

    operation_id = str(uuid.uuid4())
    result_id = str(uuid.uuid4())
    normalized_fragment_name_prefix = (
        payload.fragment_name_prefix.strip()
        if payload.fragment_name_prefix is not None else None
    )
    fragment_ids = (
        tuple(
            _FragmentIds(
                document_id=str(uuid.uuid4()), revision_id=str(uuid.uuid4()),
                edge_id=str(uuid.uuid4()),
            )
            for _fragment in simulation.fragments
        )
        if payload.persistence_mode == "operation_and_fragments" else ()
    )
    prepared = await _run_digest_cpu(
        _prepare_saved_digest_fragments,
        simulation=simulation,
        fragment_ids=fragment_ids,
        operation_id=operation_id,
        source_revision_id=payload.source.revision_id,
        catalog_id=payload.catalog.catalog_id,
        catalog_sha256=payload.catalog.expected_catalog_sha256,
        fragment_name_prefix=normalized_fragment_name_prefix,
    )
    canonical = prepared.canonical_bytes
    operation = MolecularOperation(
        id=operation_id, operation_kind="restriction_digest",
        implementation="services.restriction_digest.simulate_digest",
        implementation_version=simulation.digest_algorithm_version,
        status="completed",
        parameters={
            "schema": "bms.molbio.restriction-digest-operation-parameters.v1",
            "selected_enzyme_ids": list(payload.enzyme_ids),
            "persistence_mode": payload.persistence_mode,
            "fragment_name_prefix": normalized_fragment_name_prefix,
            "simulation_sha256": payload.simulation_sha256,
            "save_request_receipt": save_receipt,
        },
        warnings=list(simulation.warnings),
        provenance={
            "source_revision_id": payload.source.revision_id,
            "catalog_id": payload.catalog.catalog_id,
            "catalog_sha256": payload.catalog.expected_catalog_sha256,
            "request_sha256": simulation.request_sha256,
        },
        idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint,
    )
    try:
        molbio_session.add(operation)
        molbio_session.add(MolecularOperationInput(
            id=str(uuid.uuid4()), operation_id=operation_id,
            revision_id=payload.source.revision_id, role="digest_source", position=0,
            snapshot={
                "content_sha256": payload.source.expected_content_sha256,
                "name": simulation.source.name,
                "sequence_id": payload.source.sequence_id,
            },
        ))
        await molbio_session.flush()
        _digest_persistence_stage_hook("operation")
        if payload.persistence_mode == "operation_and_fragments":
            documents = [
                MolecularDocument(
                    id=fragment.document_id, document_kind="dna", name=fragment.name,
                    current_revision_id=None,
                )
                for fragment in prepared.fragments
            ]
            revisions = [
                MolecularRevision(
                    id=fragment.revision_id, document_id=fragment.document_id,
                    revision_number=1, change_kind="restriction_digest_fragment",
                    content_sha256=fragment.content_sha256,
                    content_length=fragment.content_length,
                    snapshot=literal(fragment.revision_snapshot_json, type_=String),
                    provenance=literal(fragment.revision_provenance_json, type_=String),
                    operation_id=operation_id, created_by=None,
                )
                for fragment in prepared.fragments
            ]
            edges = [
                MolecularOperationOutput(
                    id=fragment.edge_id, operation_id=operation_id,
                    revision_id=fragment.revision_id, role="digest_fragment",
                    position=fragment.fragment_index,
                    snapshot=literal(fragment.edge_snapshot_json, type_=String),
                )
                for fragment in prepared.fragments
            ]
            molbio_session.add_all(documents)
            await molbio_session.flush()
            _digest_persistence_stage_hook("document")
            molbio_session.add_all(revisions)
            await molbio_session.flush()
            _digest_persistence_stage_hook("revision")
            for document_row, revision in zip(documents, revisions, strict=True):
                document_row.current_revision_id = revision.id
            molbio_session.add_all(edges)
            await molbio_session.flush()
            _digest_persistence_stage_hook("edge")
        molbio_session.add(RestrictionDigestResult(
            id=result_id, operation_id=operation_id,
            source_revision_id=payload.source.revision_id,
            catalog_id=payload.catalog.catalog_id,
            catalog_sha256=payload.catalog.expected_catalog_sha256,
            request_sha256=simulation.request_sha256,
            result_sha256=simulation.simulation_sha256,
            result=canonical.decode("utf-8"),
        ))
        await molbio_session.flush()
        _digest_persistence_stage_hook("result")
        await molbio_session.commit()
    except IntegrityError as exc:
        await molbio_session.rollback()
        concurrent = (
            await molbio_session.execute(
                select(MolecularOperation).where(
                    MolecularOperation.idempotency_key == payload.idempotency_key
                )
            )
        ).scalar_one_or_none()
        if (
            concurrent is None
            or concurrent.operation_kind != "restriction_digest"
            or concurrent.request_fingerprint != fingerprint
        ):
            raise _error(409, "idempotency_conflict", "idempotency key is bound to another request") from exc
        canonical = await _load_saved_digest(molbio_session, str(concurrent.id))
    except BaseException:
        await molbio_session.rollback()
        raise
    return Response(content=canonical, media_type="application/json")


@router.get("/digests/{operation_id}", response_model=SavedDigestResponse)
async def get_saved_restriction_digest(
    operation_id: str,
    request: Request,
    molbio_session: AsyncSession = Depends(get_molbio_session),
) -> Response:
    if request.query_params:
        raise _error(422, "invalid_digest_request", "restriction digest request is invalid")
    if not operation_id or len(operation_id) > 128:
        raise _error(404, "digest_operation_not_found", "saved restriction digest was not found")
    canonical = await _load_saved_digest(molbio_session, operation_id)
    return Response(content=canonical, media_type="application/json")
