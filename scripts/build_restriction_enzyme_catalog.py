#!/usr/bin/env python3
"""Build the deterministic BioModStack restriction-enzyme catalog release.

The generator reads only the installed, pinned Biopython 1.87
``Bio.Restriction.Restriction_Dictionary`` module plus the reviewed static
REBASE nickase receipts below. It performs no network access.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import stat
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import rfc8785
from Bio.Restriction import Restriction_Dictionary as restriction_dictionary
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform/api"))
from restriction_catalog_integrity import validate_catalog_integrity

DEFAULT_CATALOG = ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_v1.json"
DEFAULT_MANIFEST = ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_manifest_v1.json"
DEFAULT_CHANGE_REPORT = ROOT / "platform/api/config/molbio/restriction/restriction_enzyme_catalog_change_report_v1.json"
PACKAGE_VERSION = "1.87"
DICTIONARY_SHA256 = "2a79099295dbad6061ea67a11e053787c591fcb2eb10fc8c0f89ead908dfa02b"
CATALOG_ID = "biopython-rebase-404-bms-v1"
GENERATOR_VERSION = "bms-restriction-catalog-generator-v1"
IUPAC_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")
IUPAC = frozenset("ACGTRYSWKMBDHVN")
MAX_PRIOR_CATALOG_BYTES = 64 * 1024 * 1024

NICKASE_SUPPLEMENT: tuple[dict[str, Any], ...] = (
    {
        "name": "Nt.BbvCI",
        "site": "CCTCAGC",
        "strand": "top",
        "boundary_offset": 2,
        "source_notation": "CCTCAGC (-5/none)",
        "record_id": "5790",
        "uri": "https://rebase.neb.com/rebase/enz/Nt.BbvCI.html",
        "page_sha256": "b592320c5331e8c12f397fbd1040109848acdec219b98382523f22db554fed82",
        "record_modified_on": "2017-08-10",
    },
    {
        "name": "Nb.BbvCI",
        "site": "CCTCAGC",
        "strand": "bottom",
        "boundary_offset": 5,
        "source_notation": "CCTCAGC (none/-2)",
        "record_id": "5789",
        "uri": "https://rebase.neb.com/rebase/enz/Nb.BbvCI.html",
        "page_sha256": "9c19e7dddb53bc46f41a26ad93dda5369fd44ee16a36e205f9ce18729806b658",
        "record_modified_on": "2017-08-10",
    },
    {
        "name": "Nt.BspQI",
        "site": "GCTCTTC",
        "strand": "top",
        "boundary_offset": 8,
        "source_notation": "GCTCTTC (1/none)",
        "record_id": "16997",
        "uri": "https://rebase.neb.com/rebase/enz/Nt.BspQI.html",
        "page_sha256": "b5d368dbadb89844ac4b4fc1c1883f8b2297294b28ba18dd37b90b0a5c957fc9",
        "record_modified_on": "2011-06-29",
    },
    {
        "name": "Nb.BssSI",
        "site": "CACGAG",
        "strand": "bottom",
        "boundary_offset": 5,
        "source_notation": "CACGAG (none/-1)",
        "record_id": "140982",
        "uri": "https://rebase.neb.com/rebase/enz/Nb.BssSI.html",
        "page_sha256": "96b295677ebf2535b3687a2dc47cc26017db7f758ee9809abdafe6510c542bbd",
        "record_modified_on": "2018-02-21",
    },
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def with_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result[field] = canonical_sha256(result)
    return result


def reverse_complement(site: str) -> str:
    return site.translate(IUPAC_COMPLEMENT)[::-1]


def recognition_projection(source_notation: str) -> dict[str, Any]:
    alternatives = source_notation.upper().split("|")
    if any(not site or not set(site) <= IUPAC for site in alternatives):
        raise ValueError("recognition alternatives must contain only non-empty DNA IUPAC strings")
    lengths = {len(site) for site in alternatives}
    if len(lengths) != 1:
        raise ValueError("recognition alternatives must have equal length")
    reverse_alternatives = [reverse_complement(site) for site in alternatives]
    primary = alternatives[0]
    return {
        "site_iupac": primary,
        "site_alternatives_iupac": alternatives,
        "source_notation": source_notation.upper(),
        "reverse_complement_iupac": reverse_alternatives[0],
        "reverse_complement_alternatives_iupac": reverse_alternatives,
        "length_bp": next(iter(lengths)),
        "palindromic": len(alternatives) == 1 and primary == reverse_alternatives[0],
    }


def group_id(kind: str, value: Any) -> str:
    return f"sha256:{canonical_sha256({'kind': kind, 'value': value})}"


def overhang(top_offset: int, bottom_offset: int) -> tuple[str, int]:
    delta = bottom_offset - top_offset
    if delta == 0:
        return "blunt", 0
    if delta > 0:
        return "five_prime", delta
    return "three_prime", abs(delta)


def double_strand_event(top_offset: int, opposite_end_relative: int, site_length: int) -> dict[str, Any]:
    bottom_offset = site_length + opposite_end_relative
    kind, length = overhang(top_offset, bottom_offset)
    return {
        "top_offset": top_offset,
        "bottom_offset": bottom_offset,
        "overhang_kind": kind,
        "overhang_length_nt": length,
    }


def stable_ids(names: list[str]) -> dict[str, str]:
    collisions: dict[str, list[str]] = defaultdict(list)
    for name in names:
        collisions[name.casefold()].append(name)
    collided = [sorted(values) for values in collisions.values() if len(values) > 1]
    if collided:
        raise ValueError(f"canonical-name case-fold collision requires a reviewed persistent ID mapping: {collided!r}")
    return {name: name for name in names}


def base_record(name: str, source: dict[str, Any], enzyme_id: str) -> dict[str, Any]:
    site = str(source["site"]).upper()
    recognition = recognition_projection(site)
    site_length = recognition["length_bp"]
    fst5 = source.get("fst5")
    fst3 = source.get("fst3")
    scd5 = source.get("scd5")
    scd3 = source.get("scd3")
    known = fst5 is not None and fst3 is not None
    events: list[dict[str, Any]] = []
    if known:
        events.append(double_strand_event(int(fst5), int(fst3), site_length))
        if scd5 is not None and scd3 is not None:
            events.append(double_strand_event(int(scd5), int(scd3), site_length))
    record = {
        "enzyme_id": enzyme_id,
        "id_policy": "canonical_name_v1_casefold_unique",
        "canonical_name": name,
        "aliases": [],
        "recognition": recognition,
        "cleavage": {
            "status": "known_double_strand" if known else "unknown",
            "events": events,
            "nick": None,
            "source_fields": {
                "fst5": fst5,
                "fst3": fst3,
                "scd5": scd5,
                "scd3": scd3,
            },
        },
        "enzyme_kind": "double_strand_endonuclease" if known else "restriction_enzyme_geometry_unresolved",
        "analysis_capability": "digest_simulation" if known else "recognition_only",
        "exclusion_reason": None if known else "primary_cut_geometry_incomplete",
        "supplier_provenance": {
            "reported_commercial": bool(source.get("suppl")),
            "historical_supplier_codes": sorted(source.get("suppl") or []),
            "availability_claim": "not_evaluated",
        },
        "relationships": {},
        "source": {
            "kind": "biopython_restriction_dictionary",
            "record_id": source.get("id"),
            "canonical_name": name,
            "uri": source.get("uri"),
            "package": "biopython",
            "package_version": PACKAGE_VERSION,
            "embedded_rebase_release": "REBASE_EMBOSS_404_2024",
            "dictionary_sha256": DICTIONARY_SHA256,
            "page_sha256": None,
            "retrieved_on": None,
            "record_modified_on": None,
            "source_notation": None,
        },
    }
    return record


def nickase_record(source: dict[str, Any], enzyme_id: str) -> dict[str, Any]:
    name = source["name"]
    site = source["site"]
    strand = source["strand"]
    boundary_offset = source["boundary_offset"]
    recognition = recognition_projection(site)
    return {
        "enzyme_id": enzyme_id,
        "id_policy": "canonical_name_v1_casefold_unique",
        "canonical_name": name,
        "aliases": [],
        "recognition": recognition,
        "cleavage": {
            "status": "known_single_strand_nick",
            "events": [],
            "nick": {
                "strand": strand,
                "boundary_offset": boundary_offset,
                "reverse_orientation": {
                    "strand": "bottom" if strand == "top" else "top",
                    "boundary_offset": recognition["length_bp"] - boundary_offset,
                },
            },
            "source_fields": {"fst5": None, "fst3": None, "scd5": None, "scd3": None},
        },
        "enzyme_kind": "nicking_endonuclease",
        "analysis_capability": "nicking_analysis",
        "exclusion_reason": "nicking_enzyme_not_digestible",
        "supplier_provenance": {
            "reported_commercial": False,
            "historical_supplier_codes": [],
            "availability_claim": "not_evaluated",
        },
        "relationships": {},
        "source": {
            "kind": "bms_curated_rebase_nickase",
            "record_id": source["record_id"],
            "canonical_name": name,
            "uri": source["uri"],
            "package": None,
            "package_version": None,
            "embedded_rebase_release": None,
            "dictionary_sha256": None,
            "page_sha256": source["page_sha256"],
            "retrieved_on": "2026-08-31",
            "record_modified_on": source["record_modified_on"],
            "source_notation": source["source_notation"],
        },
    }


def geometry_key(record: dict[str, Any]) -> Any | None:
    cleavage = record["cleavage"]
    source_fields = cleavage["source_fields"]
    secondary_complete = (source_fields["scd5"] is None) == (source_fields["scd3"] is None)
    if (
        cleavage["status"] == "known_double_strand"
        and record["enzyme_kind"] == "double_strand_endonuclease"
        and record["analysis_capability"] == "digest_simulation"
        and source_fields["fst5"] is not None
        and source_fields["fst3"] is not None
        and secondary_complete
        and cleavage["events"]
    ):
        return [
            [event["top_offset"], event["bottom_offset"]]
            for event in cleavage["events"]
        ]
    return None


def apply_relationships(records: list[dict[str, Any]]) -> None:
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        recognition_key = "|".join(record["recognition"]["site_alternatives_iupac"])
        by_site[recognition_key].append(record)
    for site_records in by_site.values():
        alternatives = site_records[0]["recognition"]["site_alternatives_iupac"]
        iso_group = group_id("recognition_alternatives", alternatives)
        comparable = [record for record in site_records if geometry_key(record) is not None]
        geometry_groups: dict[bytes, list[dict[str, Any]]] = defaultdict(list)
        for record in comparable:
            geometry_groups[rfc8785.dumps(geometry_key(record))].append(record)
        for record in site_records:
            geometry = geometry_key(record)
            key = rfc8785.dumps(geometry) if geometry is not None else None
            same_geometry = geometry_groups[key] if key is not None else []
            different_geometry = [
                other["enzyme_id"]
                for other in comparable
                if rfc8785.dumps(geometry_key(other)) != key
            ] if key is not None else []
            record["relationships"] = {
                "isoschizomer_group_id": iso_group,
                "equischizomer_group_id": (
                    group_id("recognition_and_geometry", [alternatives, geometry])
                    if geometry is not None
                    else None
                ),
                "equischizomer_ids": sorted(
                    (other["enzyme_id"] for other in same_geometry if other is not record),
                    key=str.casefold,
                ),
                "neoschizomer_ids": sorted(different_geometry, key=str.casefold),
            }


def _catalog_identity(catalog: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "catalog_id": catalog["catalog_id"],
        "content_sha256": catalog["content_sha256"],
    }
    counts = catalog.get("counts")
    if isinstance(counts, dict):
        for field in (
            "biopython_source_records",
            "curated_nickase_supplement_records",
            "total_discoverable",
        ):
            identity[field] = counts[field]
    return identity


def _canonical_record(record: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(record)
    value.pop("record_sha256", None)
    return value


def _changed_fields(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        fields: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                fields.append(child)
            else:
                fields.extend(_changed_fields(before[key], after[key], child))
        return fields
    return [] if before == after else [prefix]


def build_change_report(
    prior_catalog: dict[str, Any] | None,
    current_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Return an exhaustive canonical review report for one catalog transition."""
    prior_records = {
        row["enzyme_id"]: row for row in (prior_catalog or {}).get("records", [])
    }
    current_records = {
        row["enzyme_id"]: row for row in current_catalog["records"]
    }
    prior_ids = set(prior_records)
    current_ids = set(current_records)
    shared_ids = prior_ids & current_ids

    def category(*fields: str) -> list[str]:
        return sorted(
            (
                enzyme_id
                for enzyme_id in shared_ids
                if any(
                    _canonical_record(prior_records[enzyme_id]).get(field)
                    != _canonical_record(current_records[enzyme_id]).get(field)
                    for field in fields
                )
            ),
            key=str.casefold,
        )

    record_changes = []
    for enzyme_id in sorted(shared_ids, key=str.casefold):
        changed = _changed_fields(
            _canonical_record(prior_records[enzyme_id]),
            _canonical_record(current_records[enzyme_id]),
        )
        if changed:
            record_changes.append({"enzyme_id": enzyme_id, "changed_fields": changed})
    changes: dict[str, Any] = {
        "additions": sorted(current_ids - prior_ids, key=str.casefold),
        "removals": sorted(prior_ids - current_ids, key=str.casefold),
        "recognition_changes": category("recognition"),
        "identity_changes": category("canonical_name", "id_policy"),
        "cleavage_geometry_changes": category("cleavage"),
        "relationship_changes": category("relationships"),
        "source_provenance_changes": category("source"),
        "historical_supplier_code_commercial_report_changes": category("supplier_provenance"),
        "enzyme_kind_capability_exclusion_changes": category(
            "enzyme_kind", "analysis_capability", "exclusion_reason"
        ),
        "record_changes": record_changes,
    }
    return with_digest(
        {
            "schema": "bms.molbio.restriction-enzyme-catalog-change-report.v1",
            "comparison_policy": "all_canonical_record_fields_except_record_sha256",
            "prior_catalog": _catalog_identity(prior_catalog) if prior_catalog is not None else None,
            "current_catalog": _catalog_identity(current_catalog),
            "summary": {name: len(entries) for name, entries in changes.items()},
            "changes": changes,
        },
        "content_sha256",
    )


def verify_source() -> Path:
    version = importlib.metadata.version("biopython")
    if version != PACKAGE_VERSION:
        raise RuntimeError(f"requires biopython=={PACKAGE_VERSION}; found {version}")
    source_path = Path(restriction_dictionary.__file__).resolve()
    observed = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if observed != DICTIONARY_SHA256:
        raise RuntimeError(
            "installed Restriction_Dictionary.py digest does not match the reviewed source snapshot"
        )
    if "Used REBASE emboss files version 404 (2024)." not in (restriction_dictionary.__doc__ or ""):
        raise RuntimeError("installed Biopython dictionary does not identify REBASE EMBOSS release 404")
    return source_path


def build_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    verify_source()
    names = list(restriction_dictionary.rest_dict) + [row["name"] for row in NICKASE_SUPPLEMENT]
    ids = stable_ids(names)
    records = [
        base_record(name, restriction_dictionary.rest_dict[name], ids[name])
        for name in restriction_dictionary.rest_dict
    ]
    records.extend(nickase_record(source, ids[source["name"]]) for source in NICKASE_SUPPLEMENT)
    apply_relationships(records)
    for record in records:
        record["record_sha256"] = canonical_sha256(record)
    records.sort(key=lambda row: row["enzyme_id"].casefold())
    base_records = [row for row in records if row["source"]["kind"] == "biopython_restriction_dictionary"]
    counts = {
        "biopython_source_records": len(base_records),
        "curated_nickase_supplement_records": len(records) - len(base_records),
        "total_discoverable": len(records),
        "geometry_ready_double_strand": sum(row["analysis_capability"] == "digest_simulation" for row in records),
        "commercial_geometry_ready_double_strand": sum(
            row["analysis_capability"] == "digest_simulation"
            and row["supplier_provenance"]["reported_commercial"]
            for row in records
        ),
        "recognition_only": sum(row["analysis_capability"] == "recognition_only" for row in records),
        "nicking_analysis_only": sum(row["analysis_capability"] == "nicking_analysis" for row in records),
    }
    expected_counts = {
        "biopython_source_records": 1088,
        "curated_nickase_supplement_records": 4,
        "total_discoverable": 1092,
        "geometry_ready_double_strand": 754,
        "commercial_geometry_ready_double_strand": 623,
        "recognition_only": 334,
        "nicking_analysis_only": 4,
    }
    if counts != expected_counts:
        raise RuntimeError(f"pinned catalog counts drifted: {counts!r}")
    catalog = with_digest(
        {
            "schema": "bms.molbio.restriction-enzyme-catalog.v1",
            "catalog_id": CATALOG_ID,
            "generator_version": GENERATOR_VERSION,
            "source": {
                "package": "biopython",
                "package_version": PACKAGE_VERSION,
                "module": "Bio.Restriction.Restriction_Dictionary",
                "dictionary_sha256": DICTIONARY_SHA256,
                "embedded_rebase_release": "REBASE_EMBOSS_404_2024",
                "supplement_policy": "reviewed_static_rebase_page_receipts_only",
            },
            "supplier_metadata_policy": "historical_codes_are_provenance_not_current_availability",
            "counts": counts,
            "records": records,
        },
        "content_sha256",
    )
    manifest = with_digest(
        {
            "schema": "bms.molbio.restriction-enzyme-catalog-manifest.v1",
            "schema_version": 1,
            "catalog_id": CATALOG_ID,
            "catalog_schema": catalog["schema"],
            "catalog_content_sha256": catalog["content_sha256"],
            "generator_version": GENERATOR_VERSION,
            "generated_timestamp": None,
            "generated_timestamp_policy": "omitted_for_deterministic_release_bytes",
            "source": catalog["source"],
            "counts": counts,
            "canonicalization": "RFC_8785_JCS",
            "digest_semantics": "sha256(rfc8785(document_without_content_sha256))",
            "records": [
                {"enzyme_id": row["enzyme_id"], "record_sha256": row["record_sha256"]}
                for row in records
            ],
            "notices": [
                "Biopython 1.87 Restriction_Dictionary data are derived from REBASE EMBOSS release 404 (2024).",
                "Biopython copyright and permission notices are retained in docs/scientific-sources/restriction-enzyme-catalog-attribution.md.",
                "Four BMS-curated nickase records are bound to separately reviewed official REBASE page receipts retrieved 2026-08-31.",
                "Historical supplier codes are provenance only and do not claim current product availability.",
            ],
        },
        "content_sha256",
    )
    catalog_schema = json.loads(
        (ROOT / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(catalog_schema).validate(catalog)
    validate_catalog_integrity(catalog, manifest)
    return catalog, manifest


def write_exact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_prior_catalog(path: Path) -> dict[str, Any]:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise ValueError("prior catalog must be a regular file")
            chunks: list[bytes] = []
            remaining = MAX_PRIOR_CATALOG_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_PRIOR_CATALOG_BYTES:
                raise ValueError("prior catalog is too large")
        finally:
            os.close(descriptor)
        prior = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("prior catalog is unreadable or malformed JSON") from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError("prior catalog is unreadable or malformed JSON") from exc
    schema = json.loads(
        (ROOT / "schemas/molbio/restriction_enzyme_catalog_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    error = next(Draft202012Validator(schema).iter_errors(prior), None)
    if error is not None:
        raise ValueError(f"prior catalog schema validation failed: {error.message}")
    validate_catalog_integrity(prior)
    return prior


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-output", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--change-report-output", type=Path, default=DEFAULT_CHANGE_REPORT)
    parser.add_argument(
        "--prior-catalog",
        type=Path,
        default=None,
        help="Required prior canonical catalog for every non-initial release.",
    )
    parser.add_argument(
        "--initial-release",
        action="store_true",
        help="Explicitly authorize a change report with no prior catalog.",
    )
    arguments = parser.parse_args()
    if arguments.initial_release == (arguments.prior_catalog is not None):
        parser.error("choose exactly one of --initial-release or --prior-catalog")
    paths = [arguments.catalog_output, arguments.manifest_output, arguments.change_report_output]
    if arguments.prior_catalog is not None:
        paths.append(arguments.prior_catalog)
    resolved = [path.resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        parser.error("catalog, manifest, change report, and prior catalog paths must be distinct")
    existing_identities: list[tuple[int, int]] = []
    for path in resolved:
        try:
            observed = path.stat()
        except FileNotFoundError:
            continue
        identity = (observed.st_dev, observed.st_ino)
        if identity in existing_identities:
            parser.error("catalog, manifest, change report, and prior catalog paths must be distinct")
        existing_identities.append(identity)
    try:
        prior_catalog = (
            load_prior_catalog(arguments.prior_catalog)
            if arguments.prior_catalog is not None
            else None
        )
        catalog, manifest = build_documents()
        change_report = build_change_report(prior_catalog, catalog)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    write_exact(arguments.catalog_output, rfc8785.dumps(catalog))
    write_exact(arguments.manifest_output, rfc8785.dumps(manifest))
    write_exact(arguments.change_report_output, rfc8785.dumps(change_report))
    print(
        json.dumps(
            {
                "catalog": str(arguments.catalog_output),
                "catalog_content_sha256": catalog["content_sha256"],
                "manifest": str(arguments.manifest_output),
                "manifest_content_sha256": manifest["content_sha256"],
                "change_report": str(arguments.change_report_output),
                "change_report_content_sha256": change_report["content_sha256"],
                "counts": catalog["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
