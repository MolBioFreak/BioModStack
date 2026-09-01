"""Bounded, fail-closed loader for the checked-in restriction catalog."""
from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

import rfc8785
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict

from restriction_catalog_integrity import validate_catalog_integrity

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
ACTIVE_CATALOG_PATH = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_v1.json"
ACTIVE_MANIFEST_PATH = API_ROOT / "config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json"
ACTIVE_CATALOG_ID = "biopython-rebase-404-bms-v1"
CATALOG_MAX_BYTES = 2 * 1024 * 1024
MANIFEST_MAX_BYTES = 256 * 1024
SCHEMA_MAX_BYTES = 512 * 1024
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 250
QUERY_MAX_LENGTH = 128
CURSOR_MAX_LENGTH = 4096
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


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
    by_id: Mapping[str, RestrictionRecord]
    by_name_casefold: Mapping[str, RestrictionRecord]
    by_motif: Mapping[str, tuple[RestrictionRecord, ...]]
    by_supplier_code: Mapping[str, tuple[RestrictionRecord, ...]]
    by_kind: Mapping[str, tuple[RestrictionRecord, ...]]
    by_geometry_status: Mapping[str, tuple[RestrictionRecord, ...]]
    by_capability: Mapping[str, tuple[RestrictionRecord, ...]]
    by_overhang_kind: Mapping[str, tuple[RestrictionRecord, ...]]
    by_palindromic: Mapping[bool, tuple[RestrictionRecord, ...]]


@dataclass(frozen=True, slots=True)
class CatalogState:
    ready: bool
    status: str
    metadata: Mapping[str, object] | None


class CatalogUnavailable(RuntimeError):
    """Typed closed state whose public text never contains private failure detail."""

    def __init__(self) -> None:
        super().__init__("restriction catalog is unavailable")


def _bounded_regular_file(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CatalogUnavailable() from exc
    try:
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
        os.close(descriptor)


def _json_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogUnavailable() from exc
    if not isinstance(value, dict):
        raise CatalogUnavailable()
    return value


def _group(records: tuple[RestrictionRecord, ...], keys) -> Mapping[object, tuple[RestrictionRecord, ...]]:
    grouped: dict[object, list[RestrictionRecord]] = {}
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
        by_id=by_id,
        by_name_casefold=by_name,
        by_motif=_group(records, lambda row: row.recognition.site_alternatives_iupac),
        by_supplier_code=_group(records, lambda row: (code.upper() for code in row.supplier_provenance.historical_supplier_codes)),
        by_kind=_group(records, lambda row: (row.enzyme_kind,)),
        by_geometry_status=_group(records, lambda row: ("unknown" if row.cleavage.status == "unknown" else "known",)),
        by_capability=_group(records, lambda row: (row.analysis_capability,)),
        by_overhang_kind=_group(records, lambda row: {event.overhang_kind for event in row.cleavage.events}),
        by_palindromic=_group(records, lambda row: (row.recognition.palindromic,)),
    )


def _validate_source_authority(catalog: dict[str, object], manifest: dict[str, object]) -> None:
    source = catalog.get("source")
    manifest_source = manifest.get("source")
    if not isinstance(source, dict) or not isinstance(manifest_source, dict):
        raise CatalogUnavailable()
    expected = {
        "package": "biopython",
        "package_version": "1.87",
        "embedded_rebase_release": SOURCE_RELEASE,
        "dictionary_sha256": SOURCE_DICTIONARY_SHA256,
    }
    if any(source.get(key) != value for key, value in expected.items()):
        raise CatalogUnavailable()
    if any(manifest_source.get(key) != value for key, value in expected.items() if key in manifest_source):
        raise CatalogUnavailable()
    if catalog.get("catalog_id") != ACTIVE_CATALOG_ID or manifest.get("catalog_id") != ACTIVE_CATALOG_ID:
        raise CatalogUnavailable()
    if catalog.get("counts") != EXPECTED_COUNTS or manifest.get("counts") != EXPECTED_COUNTS:
        raise CatalogUnavailable()


def _load(catalog_path: Path, manifest_path: Path, schema_path: Path) -> CatalogView:
    catalog_raw = _bounded_regular_file(catalog_path, CATALOG_MAX_BYTES)
    manifest_raw = _bounded_regular_file(manifest_path, MANIFEST_MAX_BYTES)
    schema_raw = _bounded_regular_file(schema_path, SCHEMA_MAX_BYTES)
    catalog = _json_object(catalog_raw)
    manifest = _json_object(manifest_raw)
    schema = _json_object(schema_raw)
    try:
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

    def __init__(self, catalog_path: Path, manifest_path: Path, schema_path: Path) -> None:
        self._catalog_path = catalog_path
        self._manifest_path = manifest_path
        self._schema_path = schema_path
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
                self._view = _load(self._catalog_path, self._manifest_path, self._schema_path)
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
                    }
                ),
                "analysis_enabled": False,
                "digest_enabled": False,
            }
        )


catalog_authority = CatalogAuthority(ACTIVE_CATALOG_PATH, ACTIVE_MANIFEST_PATH, CATALOG_SCHEMA_PATH)


__all__ = [
    "CatalogAuthority",
    "CatalogState",
    "CatalogUnavailable",
    "CatalogView",
    "RestrictionRecord",
    "catalog_authority",
]
