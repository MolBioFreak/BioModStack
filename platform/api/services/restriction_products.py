"""Independent, bounded supplier-product evidence authority for restriction enzymes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

from services.restriction_catalog import CatalogAuthority, _bounded_regular_file, catalog_authority

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
ACTIVE_PRODUCT_PATH = API_ROOT / "config/molbio/restriction/restriction_enzyme_products_v1.json"
PRODUCT_SCHEMA_PATH = REPO_ROOT / "schemas/molbio/restriction_enzyme_products_v1.schema.json"
ACTIVE_RELEASE_ID = "bms-restriction-products-permission-pending-v1"
ACTIVE_RELEASE_VERSION = "1.0.0"
APPROVED_PRODUCT_CONTENT_SHA256 = "fa6541d1ddd992a7d98dac9e696e299e8741ac06ff05969ba301502172276b94"
APPROVED_PRODUCT_RAW_SHA256 = "9aadcbf2c64a3a64713de7ee5aa4aa0d9ccbd2c91b37fb388b18a889a2ace711"
APPROVED_PRODUCT_SCHEMA_RAW_SHA256 = "49255d6f1408a1478bd666811c784df331ec22a7c5055530e3031380d6741e59"
PRODUCT_MAX_BYTES = 512 * 1024
PRODUCT_SCHEMA_MAX_BYTES = 512 * 1024
DEFAULT_PRODUCT_LIMIT = 50
MAX_PRODUCT_LIMIT = 250
PRODUCT_QUERY_MAX_LENGTH = 128
PRODUCT_CURSOR_MAX_LENGTH = 4096
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")


class ProductEvidenceUnavailable(RuntimeError):
    """Stable fail-closed product evidence error without validation internals."""

    def __init__(self) -> None:
        super().__init__("restriction supplier product evidence is unavailable")


@dataclass(frozen=True, slots=True)
class ProductView:
    release_id: str
    release_version: str
    content_sha256: str
    raw_sha256: str
    schema_raw_sha256: str
    created_at: str | None
    created_at_policy: str
    source_policy: str
    redistribution_permission_state: str
    permission_receipt: Mapping[str, Any] | None
    product_evidence_available: bool
    record_count: int
    active_claim_count: int
    core_catalog_digest_binding: str
    product_identity_policy: str
    records: tuple[Mapping[str, Any], ...]

    def receipt(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "release_version": self.release_version,
            "content_sha256": self.content_sha256,
            "raw_sha256": self.raw_sha256,
            "schema_raw_sha256": self.schema_raw_sha256,
            "created_at": self.created_at,
            "created_at_policy": self.created_at_policy,
            "source_policy": self.source_policy,
            "redistribution_permission_state": self.redistribution_permission_state,
            "permission_receipt": self.permission_receipt,
            "product_evidence_available": self.product_evidence_available,
            "record_count": self.record_count,
            "active_claim_count": self.active_claim_count,
            "core_catalog_digest_binding": self.core_catalog_digest_binding,
            "product_identity_policy": self.product_identity_policy,
        }


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ProductEvidenceUnavailable() from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ProductEvidenceUnavailable()
    return "/".join(relative.parts)


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductEvidenceUnavailable() from exc
    if not isinstance(value, dict):
        raise ProductEvidenceUnavailable()
    return value


def _normalized_identity(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProductEvidenceUnavailable()
    return unicodedata.normalize("NFKC", value).strip().casefold()


def product_order_key(record: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Stable full product identity key; product display/scientific name is excluded."""
    return (
        _normalized_identity(record["supplier"]["supplier_id"]),
        _normalized_identity(record["catalog_number"]),
        str(record["enzyme_id"]).casefold(), str(record["enzyme_id"]),
        str(record["product_id"]).casefold(), str(record["product_id"]),
    )


def _evidence_is_bound(
    evidence: object, source_bindings: set[tuple[str, str]],
) -> bool:
    return (
        isinstance(evidence, dict)
        and all(evidence.get(key) for key in ("source_id", "source_sha256", "observed_on"))
        and (evidence["source_id"], evidence["source_sha256"]) in source_bindings
    )


def _claim_is_governed(
    claim: Mapping[str, Any], source_bindings: set[tuple[str, str]],
) -> bool:
    state = claim.get("state")
    evidence = claim.get("evidence")
    if state == "unavailable":
        return claim.get("value") is None and evidence is None
    return (
        state in {"available", "stale"}
        and claim.get("value") is not None
        and _evidence_is_bound(evidence, source_bindings)
    )


def _validate_record_semantics(
    record: Mapping[str, Any], core_ids: set[str], permission_receipt: Mapping[str, Any],
) -> int:
    if record.get("enzyme_id") not in core_ids:
        raise ProductEvidenceUnavailable()
    source = record.get("source")
    permission = record.get("redistribution_permission")
    if not isinstance(source, dict) or not isinstance(permission, dict):
        raise ProductEvidenceUnavailable()
    has_exact_source = bool(source.get("content_sha256")) or bool(source.get("manual_receipt_sha256"))
    if not source.get("url") or not source.get("retrieved_at") or not has_exact_source:
        raise ProductEvidenceUnavailable()
    if bool(source.get("manual_receipt_id")) is not bool(source.get("manual_receipt_sha256")):
        raise ProductEvidenceUnavailable()
    if permission.get("state") != "approved" or not all(
        permission.get(key) for key in ("receipt_id", "receipt_sha256", "decided_on")
    ):
        raise ProductEvidenceUnavailable()
    if any(permission.get(key) != permission_receipt.get(key) for key in ("receipt_id", "receipt_sha256", "decided_on")):
        raise ProductEvidenceUnavailable()
    source_bindings: set[tuple[str, str]] = set()
    if source.get("content_sha256"):
        source_bindings.add((source["url"], source["content_sha256"]))
    if source.get("manual_receipt_id") and source.get("manual_receipt_sha256"):
        source_bindings.add((source["manual_receipt_id"], source["manual_receipt_sha256"]))

    claims: list[Mapping[str, Any]] = []
    availability = record.get("availability")
    if not isinstance(availability, dict):
        raise ProductEvidenceUnavailable()
    availability_state = availability.get("state")
    if availability_state in {"available", "stale"}:
        evidence = availability.get("evidence")
        if (
            not availability.get("as_of")
            or not _evidence_is_bound(evidence, source_bindings)
            or not isinstance(evidence, dict)
            or evidence.get("observed_on") != availability.get("as_of")
        ):
            raise ProductEvidenceUnavailable()
        claims.append(availability)
    elif availability_state != "unavailable" or availability.get("as_of") is not None or availability.get("evidence") is not None:
        raise ProductEvidenceUnavailable()

    reaction = record.get("reaction_conditions")
    if not isinstance(reaction, dict):
        raise ProductEvidenceUnavailable()
    for key in ("temperature", "heat_inactivation"):
        value = reaction.get(key)
        if not isinstance(value, dict):
            raise ProductEvidenceUnavailable()
        claims.append(value)
    values = reaction.get("buffer_activity")
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise ProductEvidenceUnavailable()
    claims.extend(values)
    for key in ("methylation_effects", "star_activity_warnings"):
        values = record.get(key)
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise ProductEvidenceUnavailable()
        claims.extend(values)

    unit = record.get("unit_concentration")
    if not isinstance(unit, dict):
        raise ProductEvidenceUnavailable()
    unit_state = unit.get("state")
    if unit_state == "unavailable":
        if unit.get("units") is not None or unit.get("concentration") is not None or unit.get("evidence") is not None:
            raise ProductEvidenceUnavailable()
    elif unit_state in {"available", "stale"}:
        if not unit.get("units") or not unit.get("concentration") or not _evidence_is_bound(unit.get("evidence"), source_bindings):
            raise ProductEvidenceUnavailable()
    else:
        raise ProductEvidenceUnavailable()
    claims.append(unit)

    scalar_claims = [claim for claim in claims if "value" in claim]
    if not all(_claim_is_governed(claim, source_bindings) for claim in scalar_claims):
        raise ProductEvidenceUnavailable()
    if record.get("operational_status") != availability_state:
        raise ProductEvidenceUnavailable()
    unsigned = dict(record)
    record_digest = unsigned.pop("record_sha256", None)
    if record_digest != hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest():
        raise ProductEvidenceUnavailable()
    return sum(claim.get("state") == "available" for claim in claims)


def _load(
    *,
    trusted_root: Path,
    product_path: Path,
    schema_path: Path,
    core_authority: CatalogAuthority,
    expected_raw_sha256: str,
    expected_schema_raw_sha256: str,
    expected_content_sha256: str,
    maximum_bytes: int,
) -> ProductView:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_descriptor = os.open(trusted_root, flags)
    except OSError as exc:
        raise ProductEvidenceUnavailable() from exc
    try:
        product_raw = _bounded_regular_file(root_descriptor, _relative(trusted_root, product_path), maximum_bytes)
        schema_raw = _bounded_regular_file(root_descriptor, _relative(trusted_root, schema_path), PRODUCT_SCHEMA_MAX_BYTES)
    except Exception as exc:
        raise ProductEvidenceUnavailable() from exc
    finally:
        os.close(root_descriptor)
    document = _object(product_raw)
    schema = _object(schema_raw)
    try:
        if hashlib.sha256(product_raw).hexdigest() != expected_raw_sha256:
            raise ProductEvidenceUnavailable()
        if hashlib.sha256(schema_raw).hexdigest() != expected_schema_raw_sha256:
            raise ProductEvidenceUnavailable()
        if document.get("schema_raw_sha256") != expected_schema_raw_sha256:
            raise ProductEvidenceUnavailable()
        if product_raw != rfc8785.dumps(document):
            raise ProductEvidenceUnavailable()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
        unsigned = dict(document)
        content_digest = unsigned.pop("content_sha256")
        if content_digest != expected_content_sha256 or content_digest != hashlib.sha256(rfc8785.dumps(unsigned)).hexdigest():
            raise ProductEvidenceUnavailable()
        records = document["records"]
        if not isinstance(records, list) or document["record_count"] != len(records):
            raise ProductEvidenceUnavailable()
        product_ids = [record.get("product_id") for record in records if isinstance(record, dict)]
        if len(product_ids) != len(records) or len(set(product_ids)) != len(product_ids):
            raise ProductEvidenceUnavailable()
        if document.get("product_identity_policy") != "supplier_id_and_catalog_number_nfkc_casefold_trim_v1":
            raise ProductEvidenceUnavailable()
        permission_receipt = document.get("permission_receipt")
        if records and (
            document.get("redistribution_permission_state") != "approved"
            or not isinstance(permission_receipt, dict)
        ):
            raise ProductEvidenceUnavailable()

        supplier_names: dict[str, str] = {}
        supplier_name_catalogs: dict[tuple[str, str], str] = {}
        product_tuples: set[tuple[str, str]] = set()
        for record in records:
            supplier = record.get("supplier")
            if not isinstance(supplier, dict):
                raise ProductEvidenceUnavailable()
            supplier_id = _normalized_identity(supplier.get("supplier_id"))
            catalog_number = _normalized_identity(record.get("catalog_number"))
            supplier_name = supplier.get("name")
            if not isinstance(supplier_name, str) or supplier_name != supplier_name.strip():
                raise ProductEvidenceUnavailable()
            if supplier_id in supplier_names and supplier_names[supplier_id] != supplier_name:
                raise ProductEvidenceUnavailable()
            supplier_names[supplier_id] = supplier_name
            name_catalog = (_normalized_identity(supplier_name), catalog_number)
            if name_catalog in supplier_name_catalogs and supplier_name_catalogs[name_catalog] != supplier_id:
                raise ProductEvidenceUnavailable()
            supplier_name_catalogs[name_catalog] = supplier_id
            product_tuple = (supplier_id, catalog_number)
            if product_tuple in product_tuples:
                raise ProductEvidenceUnavailable()
            product_tuples.add(product_tuple)

        core_ids = set(core_authority.require().by_id)
        active_claims = sum(
            _validate_record_semantics(record, core_ids, permission_receipt)
            for record in records
        ) if isinstance(permission_receipt, dict) else 0
        if document["active_claim_count"] != active_claims:
            raise ProductEvidenceUnavailable()
        if document["release_id"] == ACTIVE_RELEASE_ID:
            if (
                document["release_version"] != ACTIVE_RELEASE_VERSION
                or records
                or document["record_count"] != 0
                or document["active_claim_count"] != 0
                or document["product_evidence_available"] is not False
                or document["redistribution_permission_state"] != "unavailable"
                or document["permission_receipt"] is not None
            ):
                raise ProductEvidenceUnavailable()
        elif document["product_evidence_available"] is not bool(records and active_claims):
            raise ProductEvidenceUnavailable()
        ordered_records = sorted(records, key=product_order_key)
        return ProductView(
            release_id=document["release_id"], release_version=document["release_version"],
            content_sha256=content_digest, raw_sha256=expected_raw_sha256,
            schema_raw_sha256=expected_schema_raw_sha256, created_at=document["created_at"],
            created_at_policy=document["created_at_policy"], source_policy=document["source_policy"],
            redistribution_permission_state=document["redistribution_permission_state"],
            permission_receipt=document["permission_receipt"],
            product_evidence_available=document["product_evidence_available"],
            record_count=document["record_count"], active_claim_count=document["active_claim_count"],
            core_catalog_digest_binding=document["core_catalog_digest_binding"],
            product_identity_policy=document["product_identity_policy"],
            records=tuple(MappingProxyType(record) for record in ordered_records),
        )
    except ProductEvidenceUnavailable:
        raise
    except Exception as exc:  # all asset/schema details collapse to a stable closed state
        raise ProductEvidenceUnavailable() from exc


class ProductAuthority:
    """Load exactly one immutable release; both success and failure are sticky."""

    def __init__(
        self,
        product_path: Path,
        schema_path: Path,
        core_authority: CatalogAuthority,
        *,
        trusted_root: Path | None = None,
        expected_raw_sha256: str = APPROVED_PRODUCT_RAW_SHA256,
        expected_schema_raw_sha256: str = APPROVED_PRODUCT_SCHEMA_RAW_SHA256,
        expected_content_sha256: str = APPROVED_PRODUCT_CONTENT_SHA256,
        maximum_bytes: int = PRODUCT_MAX_BYTES,
    ) -> None:
        self._product_path = product_path
        self._schema_path = schema_path
        self._core_authority = core_authority
        self._trusted_root = trusted_root or REPO_ROOT
        self._expected_raw_sha256 = expected_raw_sha256
        self._expected_schema_raw_sha256 = expected_schema_raw_sha256
        self._expected_content_sha256 = expected_content_sha256
        self._maximum_bytes = maximum_bytes
        self._lock = threading.Lock()
        self._attempted = False
        self._view: ProductView | None = None

    def _ensure(self) -> None:
        if self._attempted:
            return
        with self._lock:
            if self._attempted:
                return
            try:
                self._view = _load(
                    trusted_root=self._trusted_root, product_path=self._product_path,
                    schema_path=self._schema_path, core_authority=self._core_authority,
                    expected_raw_sha256=self._expected_raw_sha256,
                    expected_schema_raw_sha256=self._expected_schema_raw_sha256,
                    expected_content_sha256=self._expected_content_sha256,
                    maximum_bytes=self._maximum_bytes,
                )
            except ProductEvidenceUnavailable:
                self._view = None
            finally:
                self._attempted = True

    def require(self) -> ProductView:
        self._ensure()
        if self._view is None:
            raise ProductEvidenceUnavailable()
        return self._view

    def readiness(self) -> dict[str, object]:
        try:
            view = self.require()
        except ProductEvidenceUnavailable:
            return {"required": True, "ready": False, "status": "product_loader_unavailable", "loader_healthy": False}
        available = view.product_evidence_available
        return {
            "required": True, "ready": True,
            "status": "evidence_available" if available else "evidence_unavailable",
            "loader_healthy": True, **view.receipt(),
            "require_known_policy": (
                "governed_product_evidence_loaded"
                if available else "fail_closed_product_evidence_unavailable"
            ),
            "full_restriction_runtime_ready": True,
            "phase6_denominator_status": "current",
        }


product_authority = ProductAuthority(
    ACTIVE_PRODUCT_PATH, PRODUCT_SCHEMA_PATH, catalog_authority,
    trusted_root=REPO_ROOT,
)

__all__ = [
    "ProductAuthority", "ProductEvidenceUnavailable", "ProductView",
    "product_authority", "product_order_key", "_normalized_identity",
]
