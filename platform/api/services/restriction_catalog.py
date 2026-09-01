"""Bounded, fail-closed loader for the checked-in restriction catalog."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, TypeVar

import rfc8785
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from restriction_catalog_integrity import validate_catalog_integrity

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
ACTIVE_CATALOG_PATH = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_v1.json"
ACTIVE_MANIFEST_PATH = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json"
ACTIVE_CATALOG_ID = "biopython-rebase-404-bms-v1"
APPROVED_CATALOG_CONTENT_SHA256 = "e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f"
APPROVED_CATALOG_RAW_SHA256 = "afa440bbbf47d9368e85f300e5abcb4f630b5cb3828e832feaa9e13f71d520c8"
APPROVED_MANIFEST_RAW_SHA256 = "f79eb4a611b8e6bc28e906afafc24c7e38723b4d0b8aeb40dbc541ea8c19e983"
APPROVED_SCHEMA_RAW_SHA256 = "7bdbd4d1bd7206eba5b5efdf1da050580ac73a5f0d4014233c3394eabfe2d346"
CATALOG_MAX_BYTES = 2 * 1024 * 1024
MANIFEST_MAX_BYTES = 256 * 1024
SCHEMA_MAX_BYTES = 512 * 1024
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 250
QUERY_MAX_LENGTH = 128
CURSOR_MAX_LENGTH = 4096
ANALYSIS_INLINE_SEQUENCE_MAX_LENGTH = 5_000_000
ANALYSIS_EXPLICIT_ENZYME_MAXIMUM = 256
ANALYSIS_REGION_MAXIMUM = 128
ANALYSIS_PATTERN_MAXIMUM = 619
ANALYSIS_SCAN_WORK_MAXIMUM = 100_000_000
ANALYSIS_OCCURRENCE_MAXIMUM = 25_000
ANALYSIS_EVENT_MAXIMUM = 50_000
ANALYSIS_RESPONSE_MAXIMUM_BYTES = 32 * 1024 * 1024
ANALYSIS_CACHE_MAXIMUM_ENTRIES = 32
SOURCE_YEAR = 2024
SOURCE_RELEASE = "REBASE_EMBOSS_404_2024"
SOURCE_DICTIONARY_SHA256 = "2a79099295dbad6061ea67a11e053787c591fcb2eb10fc8c0f89ead908dfa02b"
EXPECTED_COUNTS = {
    "biopython_source_records": 1088,
    "curated_nickase_supplement_records": 4,
    "total_discoverable": 1092,
    "geometry_ready_double_strand": 754,
    "commercial_geometry_ready_double_strand": 623,
    "recognition_only": 334,
    "nicking_analysis_only": 4,
}
SUPPLIER_CODE_NOTICE = (
    "Supplier codes are historical source metadata only; they are not current availability claims."
)
SOURCE_AGE_NOTICE = (
    "The catalog derives from Biopython 1.87 and REBASE EMBOSS release 404 (2024); "
    "review a newer checked-in release before treating it as up-to-date source coverage."
)
EXPECTED_MANIFEST_NOTICES = (
    "Biopython 1.87 Restriction_Dictionary data are derived from REBASE EMBOSS release 404 (2024).",
    "Biopython copyright and permission notices are retained in docs/scientific-sources/restriction-enzyme-catalog-attribution.md.",
    "Four BMS-curated nickase records are bound to separately reviewed official REBASE page receipts retrieved 2026-08-31.",
    "Historical supplier codes are provenance only and do not claim current product availability.",
)


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ManifestSource(StrictManifestModel):
    package: Literal["biopython"]
    module: Literal["Bio.Restriction.Restriction_Dictionary"]
    package_version: Literal["1.87"]
    embedded_rebase_release: Literal["REBASE_EMBOSS_404_2024"]
    dictionary_sha256: Literal[
        "2a79099295dbad6061ea67a11e053787c591fcb2eb10fc8c0f89ead908dfa02b"
    ]
    supplement_policy: Literal["reviewed_static_rebase_page_receipts_only"]


class ManifestCounts(StrictManifestModel):
    biopython_source_records: Literal[1088]
    curated_nickase_supplement_records: Literal[4]
    total_discoverable: Literal[1092]
    geometry_ready_double_strand: Literal[754]
    commercial_geometry_ready_double_strand: Literal[623]
    recognition_only: Literal[334]
    nicking_analysis_only: Literal[4]


class ManifestRecord(StrictManifestModel):
    enzyme_id: str
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CatalogManifest(StrictManifestModel):
    schema_: Literal["bms.molbio.restriction-enzyme-catalog-manifest.v1"] = Field(alias="schema")
    schema_version: Literal[1]
    catalog_schema: Literal["bms.molbio.restriction-enzyme-catalog.v1"]
    catalog_id: Literal["biopython-rebase-404-bms-v1"]
    catalog_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: Literal["bms-restriction-catalog-generator-v1"]
    canonicalization: Literal["RFC_8785_JCS"]
    digest_semantics: Literal["sha256(rfc8785(document_without_content_sha256))"]
    generated_timestamp: None
    generated_timestamp_policy: Literal["omitted_for_deterministic_release_bytes"]
    source: ManifestSource
    counts: ManifestCounts
    records: list[ManifestRecord]
    notices: list[str]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Recognition(StrictFrozenModel):
    site_iupac: str
    site_alternatives_iupac: tuple[str, ...]
    source_notation: str
    reverse_complement_iupac: str
    reverse_complement_alternatives_iupac: tuple[str, ...]
    length_bp: int
    palindromic: bool


class CleavageEvent(StrictFrozenModel):
    top_offset: int
    bottom_offset: int
    overhang_kind: Literal["blunt", "five_prime", "three_prime"]
    overhang_length_nt: int


class NickOrientation(StrictFrozenModel):
    strand: Literal["top", "bottom"]
    boundary_offset: int


class Nick(StrictFrozenModel):
    strand: Literal["top", "bottom"]
    boundary_offset: int
    reverse_orientation: NickOrientation


class SourceFields(StrictFrozenModel):
    fst5: int | None
    fst3: int | None
    scd5: int | None
    scd3: int | None


class Cleavage(StrictFrozenModel):
    status: Literal["known_double_strand", "known_single_strand_nick", "unknown"]
    events: tuple[CleavageEvent, ...]
    nick: Nick | None
    source_fields: SourceFields


class SupplierProvenance(StrictFrozenModel):
    reported_commercial: bool
    historical_supplier_codes: tuple[str, ...]
    availability_claim: Literal["not_evaluated"]


class Relationships(StrictFrozenModel):
    isoschizomer_group_id: str
    equischizomer_group_id: str | None
    equischizomer_ids: tuple[str, ...]
    neoschizomer_ids: tuple[str, ...]


class RecordSource(StrictFrozenModel):
    kind: Literal["biopython_restriction_dictionary", "bms_curated_rebase_nickase"]
    record_id: int | str | None
    canonical_name: str
    uri: str | None
    package: str | None
    package_version: str | None
    embedded_rebase_release: str | None
    dictionary_sha256: str | None
    page_sha256: str | None
    retrieved_on: str | None
    record_modified_on: str | None
    source_notation: str | None


class RestrictionRecord(StrictFrozenModel):
    enzyme_id: str
    id_policy: Literal["canonical_name_v1_casefold_unique"]
    canonical_name: str
    aliases: tuple[str, ...]
    recognition: Recognition
    cleavage: Cleavage
    enzyme_kind: Literal[
        "double_strand_endonuclease",
        "nicking_endonuclease",
        "restriction_enzyme_geometry_unresolved",
    ]
    analysis_capability: Literal["digest_simulation", "nicking_analysis", "recognition_only"]
    exclusion_reason: str | None
    supplier_provenance: SupplierProvenance
    relationships: Relationships
    source: RecordSource
    record_sha256: str


@dataclass(frozen=True, slots=True)
class CatalogView:
    catalog_id: str
    content_sha256: str
    source_release: str
    counts: Mapping[str, int]
    records: tuple[RestrictionRecord, ...]
    ordered_records: tuple[RestrictionRecord, ...]
    order_rank: Mapping[str, int]
    by_id: Mapping[str, RestrictionRecord]
    by_name_casefold: Mapping[str, RestrictionRecord]
    by_motif: Mapping[str, tuple[RestrictionRecord, ...]]
    by_supplier_code: Mapping[str, tuple[RestrictionRecord, ...]]
    by_kind: Mapping[str, tuple[RestrictionRecord, ...]]
    by_geometry_status: Mapping[str, tuple[RestrictionRecord, ...]]
    by_capability: Mapping[str, tuple[RestrictionRecord, ...]]
    by_overhang_kind: Mapping[str, tuple[RestrictionRecord, ...]]
    by_palindromic: Mapping[bool, tuple[RestrictionRecord, ...]]
    by_commercial: Mapping[bool, tuple[RestrictionRecord, ...]]


@dataclass(frozen=True, slots=True)
class CatalogState:
    ready: bool
    status: str
    metadata: Mapping[str, object] | None


class CatalogUnavailable(RuntimeError):
    """Typed closed state whose public text never contains private failure detail."""

    def __init__(self) -> None:
        super().__init__("restriction catalog is unavailable")


def _bounded_regular_file(root_descriptor: int, relative_path: str, maximum: int) -> bytes:
    components = relative_path.split("/")
    if (
        not relative_path
        or relative_path.startswith("/")
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise CatalogUnavailable()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | os.O_NONBLOCK
    file_flags |= getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NOFOLLOW", 0)
    current = os.dup(root_descriptor)
    descriptor: int | None = None
    try:
        for component in components[:-1]:
            parent = os.open(component, directory_flags, dir_fd=current)
            os.close(current)
            current = parent
        descriptor = os.open(components[-1], file_flags, dir_fd=current)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > maximum:
            raise CatalogUnavailable()
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != info.st_size or len(raw) > maximum:
            raise CatalogUnavailable()
        return raw
    except OSError as exc:
        raise CatalogUnavailable() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(current)


def _json_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogUnavailable() from exc
    if not isinstance(value, dict):
        raise CatalogUnavailable()
    return value


IndexKey = TypeVar("IndexKey", bound=Hashable)


def _group(
    records: tuple[RestrictionRecord, ...],
    keys: Callable[[RestrictionRecord], Iterable[IndexKey]],
) -> Mapping[IndexKey, tuple[RestrictionRecord, ...]]:
    grouped: dict[IndexKey, list[RestrictionRecord]] = {}
    for record in records:
        for key in keys(record):
            grouped.setdefault(key, []).append(record)
    return MappingProxyType({key: tuple(rows) for key, rows in grouped.items()})


def _public_counts(counts: Mapping[str, int], records: tuple[RestrictionRecord, ...]) -> Mapping[str, int]:
    return MappingProxyType(
        {
            "total": counts["total_discoverable"],
            "geometry_ready": counts["geometry_ready_double_strand"],
            "commercial_geometry_ready": counts["commercial_geometry_ready_double_strand"],
            "unknown_geometry": counts["recognition_only"],
            "nicking": counts["nicking_analysis_only"],
            "two_event_double_strand": sum(len(record.cleavage.events) == 2 for record in records),
        }
    )


def _build_view(catalog: dict[str, object]) -> CatalogView:
    records = tuple(RestrictionRecord.model_validate(row) for row in catalog["records"])  # type: ignore[arg-type]
    ordered_records = tuple(
        sorted(records, key=lambda row: (row.canonical_name.casefold(), row.enzyme_id.casefold()))
    )
    raw_counts = catalog["counts"]
    assert isinstance(raw_counts, dict)
    counts = _public_counts({str(key): int(value) for key, value in raw_counts.items()}, records)
    by_id = MappingProxyType({record.enzyme_id: record for record in records})
    by_name = MappingProxyType({record.canonical_name.casefold(): record for record in records})
    return CatalogView(
        catalog_id=str(catalog["catalog_id"]),
        content_sha256=str(catalog["content_sha256"]),
        source_release=SOURCE_RELEASE,
        counts=counts,
        records=records,
        ordered_records=ordered_records,
        order_rank=MappingProxyType(
            {record.enzyme_id: rank for rank, record in enumerate(ordered_records)}
        ),
        by_id=by_id,
        by_name_casefold=by_name,
        by_motif=_group(ordered_records, lambda row: row.recognition.site_alternatives_iupac),
        by_supplier_code=_group(ordered_records, lambda row: (code.upper() for code in row.supplier_provenance.historical_supplier_codes)),
        by_kind=_group(ordered_records, lambda row: (row.enzyme_kind,)),
        by_geometry_status=_group(ordered_records, lambda row: ("unknown" if row.cleavage.status == "unknown" else "known",)),
        by_capability=_group(ordered_records, lambda row: (row.analysis_capability,)),
        by_overhang_kind=_group(ordered_records, lambda row: {event.overhang_kind for event in row.cleavage.events}),
        by_palindromic=_group(ordered_records, lambda row: (row.recognition.palindromic,)),
        by_commercial=_group(
            ordered_records, lambda row: (row.supplier_provenance.reported_commercial,)
        ),
    )


def _validate_source_authority(catalog: dict[str, object], manifest: dict[str, object]) -> None:
    validated_manifest = CatalogManifest.model_validate(manifest)
    source = catalog.get("source")
    if not isinstance(source, dict):
        raise CatalogUnavailable()
    ManifestSource.model_validate(source)
    if (
        catalog.get("schema") != "bms.molbio.restriction-enzyme-catalog.v1"
        or catalog.get("catalog_id") != ACTIVE_CATALOG_ID
        or catalog.get("generator_version") != "bms-restriction-catalog-generator-v1"
        or catalog.get("supplier_metadata_policy")
        != "historical_codes_are_provenance_not_current_availability"
        or catalog.get("counts") != EXPECTED_COUNTS
        or validated_manifest.catalog_id != ACTIVE_CATALOG_ID
        or validated_manifest.counts.model_dump() != EXPECTED_COUNTS
        or tuple(validated_manifest.notices) != EXPECTED_MANIFEST_NOTICES
    ):
        raise CatalogUnavailable()


def _asset_relative_path(trusted_root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(trusted_root)
    except ValueError as exc:
        raise CatalogUnavailable() from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise CatalogUnavailable()
    return "/".join(relative.parts)


def _load(
    trusted_root: Path, catalog_path: Path, manifest_path: Path, schema_path: Path
) -> CatalogView:
    root_flags = os.O_RDONLY | os.O_DIRECTORY
    root_flags |= getattr(os, "O_CLOEXEC", 0)
    root_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(trusted_root, root_flags)
    except OSError as exc:
        raise CatalogUnavailable() from exc
    try:
        catalog_raw = _bounded_regular_file(
            root_descriptor, _asset_relative_path(trusted_root, catalog_path), CATALOG_MAX_BYTES
        )
        manifest_raw = _bounded_regular_file(
            root_descriptor, _asset_relative_path(trusted_root, manifest_path), MANIFEST_MAX_BYTES
        )
        schema_raw = _bounded_regular_file(
            root_descriptor, _asset_relative_path(trusted_root, schema_path), SCHEMA_MAX_BYTES
        )
    finally:
        os.close(root_descriptor)
    catalog = _json_object(catalog_raw)
    manifest = _json_object(manifest_raw)
    schema = _json_object(schema_raw)
    try:
        if (
            hashlib.sha256(catalog_raw).hexdigest() != APPROVED_CATALOG_RAW_SHA256
            or hashlib.sha256(manifest_raw).hexdigest() != APPROVED_MANIFEST_RAW_SHA256
            or hashlib.sha256(schema_raw).hexdigest() != APPROVED_SCHEMA_RAW_SHA256
            or catalog.get("catalog_id") != ACTIVE_CATALOG_ID
            or catalog.get("content_sha256") != APPROVED_CATALOG_CONTENT_SHA256
        ):
            raise CatalogUnavailable()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(catalog)
        validate_catalog_integrity(catalog, manifest)  # type: ignore[arg-type]
        _validate_source_authority(catalog, manifest)
        if catalog_raw != rfc8785.dumps(catalog) or manifest_raw != rfc8785.dumps(manifest):
            raise ValueError("non-canonical active asset bytes")
        return _build_view(catalog)
    except CatalogUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - all asset defects collapse to one typed closed state.
        raise CatalogUnavailable() from exc


class CatalogAuthority:
    """Load one immutable catalog view once; success and failure are both sticky."""

    def __init__(
        self,
        catalog_path: Path,
        manifest_path: Path,
        schema_path: Path,
        *,
        trusted_root: Path | None = None,
    ) -> None:
        self._catalog_path = catalog_path
        self._manifest_path = manifest_path
        self._schema_path = schema_path
        self._trusted_root = trusted_root or Path(
            os.path.commonpath(
                [
                    os.fspath(catalog_path.parent),
                    os.fspath(manifest_path.parent),
                    os.fspath(schema_path.parent),
                ]
            )
        )
        self._lock = threading.Lock()
        self._attempted = False
        self._view: CatalogView | None = None

    def _ensure(self) -> None:
        if self._attempted:
            return
        with self._lock:
            if self._attempted:
                return
            try:
                self._view = _load(
                    self._trusted_root,
                    self._catalog_path,
                    self._manifest_path,
                    self._schema_path,
                )
            except CatalogUnavailable:
                self._view = None
            finally:
                self._attempted = True

    def require(self) -> CatalogView:
        self._ensure()
        if self._view is None:
            raise CatalogUnavailable()
        return self._view

    def state(self) -> CatalogState:
        self._ensure()
        if self._view is None:
            return CatalogState(False, "catalog_unavailable", None)
        return CatalogState(True, "ready", self._metadata(self._view))

    def readiness(self) -> dict[str, object]:
        state = self.state()
        if not state.ready or state.metadata is None:
            return {"required": True, "ready": False, "status": "catalog_unavailable"}
        return {"required": True, "ready": True, "status": "ready", **state.metadata}

    @staticmethod
    def _metadata(view: CatalogView) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "catalog_id": view.catalog_id,
                "catalog_sha256": view.content_sha256,
                "source_release": view.source_release,
                "source_year": SOURCE_YEAR,
                "source_age_years": max(0, date.today().year - SOURCE_YEAR),
                "source_age_notice": SOURCE_AGE_NOTICE,
                "supplier_code_notice": SUPPLIER_CODE_NOTICE,
                "counts": view.counts,
                "bounds": MappingProxyType(
                    {
                        "default_limit": DEFAULT_PAGE_LIMIT,
                        "maximum_limit": MAX_PAGE_LIMIT,
                        "query_max_length": QUERY_MAX_LENGTH,
                        "analysis_inline_sequence_max_length": ANALYSIS_INLINE_SEQUENCE_MAX_LENGTH,
                        "analysis_explicit_enzyme_maximum": ANALYSIS_EXPLICIT_ENZYME_MAXIMUM,
                        "analysis_region_maximum": ANALYSIS_REGION_MAXIMUM,
                        "analysis_pattern_maximum": ANALYSIS_PATTERN_MAXIMUM,
                        "analysis_scan_work_maximum": ANALYSIS_SCAN_WORK_MAXIMUM,
                        "analysis_occurrence_maximum": ANALYSIS_OCCURRENCE_MAXIMUM,
                        "analysis_event_maximum": ANALYSIS_EVENT_MAXIMUM,
                        "analysis_response_maximum_bytes": ANALYSIS_RESPONSE_MAXIMUM_BYTES,
                        "analysis_cache_maximum_entries": ANALYSIS_CACHE_MAXIMUM_ENTRIES,
                    }
                ),
                "analysis_enabled": True,
                "digest_enabled": False,
            }
        )


catalog_authority = CatalogAuthority(
    ACTIVE_CATALOG_PATH,
    ACTIVE_MANIFEST_PATH,
    CATALOG_SCHEMA_PATH,
    trusted_root=REPO_ROOT,
)


__all__ = [
    "CatalogAuthority",
    "CatalogState",
    "CatalogUnavailable",
    "CatalogView",
    "RestrictionRecord",
    "catalog_authority",
]
