"""Read-only restriction-enzyme catalog API (Phase 1 only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from typing import Annotated, Any, Coroutine, Literal, Union

import rfc8785
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_database import get_molbio_session
from molbio_models import MolecularDocument, MolecularRevision
from services.restriction_analysis import (
    MAX_EXPLICIT_ENZYME_IDS,
    MAX_INLINE_SEQUENCE_LENGTH,
    MAX_REGIONS,
    AnalysisLimitError,
    AnalysisResult,
    InvalidDNAError,
    analyze_sequence,
    normalize_dna,
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
    analysis_occurrence_maximum: int
    analysis_event_maximum: int
    analysis_response_maximum_bytes: int


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
    analysis_enabled: Literal[True]
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


class AnalysisResponse(StrictResponse):
    schema_: Literal["bms.molbio.restriction-analysis-response.v1"] = Field(alias="schema")
    source: AnalysisSourceReceipt
    catalog: CatalogReceipt
    request_sha256: str
    result_sha256: str
    analysis: AnalysisResult


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


async def _analysis_source(
    source: AnalysisSource, session: AsyncSession
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

    document = await session.get(MolecularDocument, source.sequence_id)
    revision = await session.get(MolecularRevision, source.revision_id)
    if document is None or revision is None or revision.document_id != document.id:
        raise _error(404, "source_revision_not_found", "molecular revision was not found")
    snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
    if str(snapshot.get("sequence_type") or "").lower() != "dna":
        raise _error(422, "invalid_dna", "molecular revision is not DNA")
    try:
        sequence = normalize_dna(str(snapshot.get("sequence") or ""))
    except InvalidDNAError as exc:
        raise _error(422, "invalid_dna", "molecular revision DNA is invalid") from exc
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    if (
        revision.content_sha256 != source.expected_content_sha256
        or digest != revision.content_sha256
        or revision.content_length != len(sequence)
    ):
        raise _error(
            409, "source_revision_digest_mismatch",
            "molecular revision content digest or length does not match",
        )
    owned_topology: Literal["linear", "circular"] | None = None
    if isinstance(snapshot.get("is_circular"), bool):
        owned_topology = "circular" if snapshot["is_circular"] else "linear"
    elif snapshot.get("topology") in {"linear", "circular"}:
        owned_topology = snapshot["topology"]
    topology = owned_topology or source.topology
    if topology is None or (owned_topology is not None and source.topology not in {None, owned_topology}):
        raise _error(422, "invalid_dna", "molecular revision topology authority is invalid")
    return sequence, topology, AnalysisSourceReceipt(
        kind="molecular_revision", name=document.name, sequence_id=document.id,
        revision_id=revision.id, revision_number=revision.revision_number,
        content_sha256=digest, content_length=len(sequence), topology=topology,
    )


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_restriction_sites(
    payload: Annotated[
        AnalysisRequest,
        Body(openapi_examples={"inline_dna": {"summary": "Inline DNA", "value": _ANALYZE_EXAMPLE}}),
    ],
    authority: CatalogAuthority = Depends(get_catalog_authority),
    molbio_session: AsyncSession = Depends(get_molbio_session),
) -> AnalysisResponse:
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
    sequence, topology, source_receipt = await _analysis_source(payload.source, molbio_session)
    records = _analysis_records(view, payload.scope)
    normalized_request = payload.model_dump(mode="json", by_alias=True)
    normalized_request["source"] = {
        **normalized_request["source"],
        **({"dna": sequence, "name": source_receipt.name} if source_receipt.kind == "inline_dna" else {}),
    }
    if isinstance(payload.scope, ExplicitAnalysisScope):
        normalized_request["scope"]["enzyme_ids"] = sorted(payload.scope.enzyme_ids)
    normalized_request["regions"] = sorted(normalized_request["regions"], key=lambda row: (row["start"], row["end"]))
    request_sha256 = hashlib.sha256(rfc8785.dumps(normalized_request)).hexdigest()
    try:
        analysis = analyze_sequence(
            sequence=sequence,
            topology=topology,
            catalog=view,
            records=records,
            include_possible_sites=payload.include_possible_sites,
            regions=tuple((region.start, region.end) for region in payload.regions),
        )
    except InvalidDNAError as exc:
        raise _error(422, "invalid_dna", "DNA input or regions are invalid") from exc
    except AnalysisLimitError as exc:
        raise _error(413, "request_too_large", "restriction analysis request is too large") from exc
    catalog_receipt = _receipt(authority)
    result_preimage = {
        "source": source_receipt.model_dump(mode="json"),
        "catalog_id": view.catalog_id,
        "catalog_sha256": view.content_sha256,
        "request_sha256": request_sha256,
        "analysis": analysis.model_dump(mode="json"),
    }
    result_sha256 = hashlib.sha256(rfc8785.dumps(result_preimage)).hexdigest()
    return AnalysisResponse(
        schema="bms.molbio.restriction-analysis-response.v1",
        source=source_receipt,
        catalog=catalog_receipt,
        request_sha256=request_sha256,
        result_sha256=result_sha256,
        analysis=analysis,
    )
