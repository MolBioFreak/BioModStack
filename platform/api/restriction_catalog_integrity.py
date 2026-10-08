"""Side-effect-free scientific integrity checks for restriction catalogs.

This module deliberately does not import FastAPI, application configuration, or
the catalog generator.  Build tooling and the future Phase 1 loader can share the
same independently recomputed invariants.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import defaultdict
from typing import Any

import rfc8785

IUPAC = frozenset("ACGTRYSWKMBDHVN")
IUPAC_COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _digest_without(document: dict[str, Any], field: str) -> str:
    value = copy.deepcopy(document)
    value.pop(field, None)
    return _canonical_sha256(value)


def _reverse_complement(site: str) -> str:
    return site.translate(IUPAC_COMPLEMENT)[::-1]


def _group_id(kind: str, value: Any) -> str:
    return f"sha256:{_canonical_sha256({'kind': kind, 'value': value})}"


def _motif_key(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(record["recognition"]["site_alternatives_iupac"])


def _known_dsb_geometry(record: dict[str, Any]) -> tuple[tuple[int, int], ...] | None:
    if (
        record.get("enzyme_kind") != "double_strand_endonuclease"
        or record.get("cleavage", {}).get("status") != "known_double_strand"
    ):
        return None
    events = record["cleavage"].get("events")
    if not isinstance(events, list) or not events:
        return None
    return tuple((event["top_offset"], event["bottom_offset"]) for event in events)


def _validate_recognition(record: dict[str, Any]) -> None:
    enzyme_id = record["enzyme_id"]
    recognition = record["recognition"]
    alternatives = recognition["site_alternatives_iupac"]
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError(f"{enzyme_id}: recognition alternatives are empty")
    if any(not isinstance(site, str) or not site or not set(site) <= IUPAC for site in alternatives):
        raise ValueError(f"{enzyme_id}: invalid IUPAC recognition alternative")
    lengths = {len(site) for site in alternatives}
    if len(lengths) != 1:
        raise ValueError(f"{enzyme_id}: recognition alternatives must have equal length")
    length = next(iter(lengths))
    if recognition["site_iupac"] != alternatives[0]:
        raise ValueError(f"{enzyme_id}: primary recognition must be the first alternative")
    if recognition["source_notation"] != "|".join(alternatives):
        raise ValueError(f"{enzyme_id}: recognition source notation disagrees with alternatives")
    if recognition["length_bp"] != length:
        raise ValueError(f"{enzyme_id}: normalized primary recognition length is incorrect")
    reverse = [_reverse_complement(site) for site in alternatives]
    if (
        recognition["reverse_complement_iupac"] != reverse[0]
        or recognition["reverse_complement_alternatives_iupac"] != reverse
    ):
        raise ValueError(f"{enzyme_id}: reverse complement is incorrect")
    expected_palindromic = len(alternatives) == 1 and alternatives[0] == reverse[0]
    if recognition["palindromic"] is not expected_palindromic:
        raise ValueError(f"{enzyme_id}: palindromicity is incorrect")


def _validate_cleavage(record: dict[str, Any]) -> None:
    enzyme_id = record["enzyme_id"]
    cleavage = record["cleavage"]
    status = cleavage["status"]
    events = cleavage["events"]
    source_fields = cleavage["source_fields"]
    motif_length = record["recognition"]["length_bp"]
    if status == "known_double_strand":
        if record["enzyme_kind"] != "double_strand_endonuclease":
            raise ValueError(f"{enzyme_id}: known DSB has incompatible enzyme kind")
        if record["analysis_capability"] != "digest_simulation":
            raise ValueError(f"{enzyme_id}: known DSB capability is inconsistent")
        if record["exclusion_reason"] is not None or cleavage["nick"] is not None:
            raise ValueError(f"{enzyme_id}: known DSB exclusion or nick state is inconsistent")
        if source_fields["fst5"] is None or source_fields["fst3"] is None:
            raise ValueError(f"{enzyme_id}: known DSB primary event is incomplete")
        secondary_present = (source_fields["scd5"] is not None, source_fields["scd3"] is not None)
        if secondary_present[0] != secondary_present[1]:
            raise ValueError(f"{enzyme_id}: known DSB secondary event is incomplete")
        expected_pairs = [
            (int(source_fields["fst5"]), motif_length + int(source_fields["fst3"]))
        ]
        if secondary_present[0]:
            expected_pairs.append(
                (int(source_fields["scd5"]), motif_length + int(source_fields["scd3"]))
            )
        if len(events) != len(expected_pairs):
            raise ValueError(f"{enzyme_id}: known DSB event completeness is incorrect")
        for event, (top, bottom) in zip(events, expected_pairs, strict=True):
            if (event["top_offset"], event["bottom_offset"]) != (top, bottom):
                raise ValueError(f"{enzyme_id}: known DSB signed boundaries are incorrect")
            delta = bottom - top
            expected_kind = "blunt" if delta == 0 else "five_prime" if delta > 0 else "three_prime"
            if event["overhang_kind"] != expected_kind or event["overhang_length_nt"] != abs(delta):
                raise ValueError(f"{enzyme_id}: overhang kind or length is incorrect")
    elif status == "known_single_strand_nick":
        nick = cleavage["nick"]
        if (
            record["enzyme_kind"] != "nicking_endonuclease"
            or record["analysis_capability"] != "nicking_analysis"
            or record["exclusion_reason"] != "nicking_enzyme_not_digestible"
            or events
            or not isinstance(nick, dict)
            or any(value is not None for value in source_fields.values())
        ):
            raise ValueError(f"{enzyme_id}: nick cleavage shape is inconsistent")
        reverse = nick["reverse_orientation"]
        source_notation = record["source"].get("source_notation")
        notation = re.fullmatch(
            r"([ACGTRYSWKMBDHVN]+(?:\|[ACGTRYSWKMBDHVN]+)*) \(([^/]+)/([^()]+)\)",
            source_notation or "",
        )
        if notation is None or notation.group(1) != record["recognition"]["source_notation"]:
            raise ValueError(f"{enzyme_id}: nick canonical source notation is invalid")
        top_token, bottom_token = notation.group(2), notation.group(3)
        canonical_token = top_token if nick["strand"] == "top" else bottom_token
        opposite_token = bottom_token if nick["strand"] == "top" else top_token
        if opposite_token != "none":
            raise ValueError(f"{enzyme_id}: nick canonical source notation cuts both strands")
        try:
            canonical_boundary = motif_length + int(canonical_token)
        except ValueError as exc:
            raise ValueError(f"{enzyme_id}: nick canonical source notation is invalid") from exc
        if nick["boundary_offset"] != canonical_boundary:
            raise ValueError(f"{enzyme_id}: nick canonical boundary disagrees with source notation")
        expected_strand = "bottom" if nick["strand"] == "top" else "top"
        if reverse["strand"] != expected_strand:
            raise ValueError(f"{enzyme_id}: nick reverse strand does not swap")
        if reverse["boundary_offset"] != motif_length - nick["boundary_offset"]:
            raise ValueError(f"{enzyme_id}: nick reverse arithmetic is incorrect")
    elif status == "unknown":
        if record["enzyme_kind"] != "restriction_enzyme_geometry_unresolved":
            raise ValueError(f"{enzyme_id}: unknown cleavage enzyme kind is inconsistent")
        if record["analysis_capability"] != "recognition_only":
            raise ValueError(f"{enzyme_id}: unknown cleavage capability is inconsistent")
        if record["exclusion_reason"] != "primary_cut_geometry_incomplete":
            raise ValueError(f"{enzyme_id}: unknown cleavage exclusion is inconsistent")
        if events or cleavage["nick"] is not None or any(
            value is not None for value in source_fields.values()
        ):
            raise ValueError(f"{enzyme_id}: unknown cleavage geometry contains executable events")
    else:
        raise ValueError(f"{enzyme_id}: unsupported cleavage status")


def _validate_relationships(records: list[dict[str, Any]]) -> None:
    by_id = {record["enzyme_id"]: record for record in records}
    by_motif: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_motif[_motif_key(record)].append(record)
        relationships = record["relationships"]
        related = relationships["equischizomer_ids"] + relationships["neoschizomer_ids"]
        if record["enzyme_id"] in related:
            raise ValueError(f"{record['enzyme_id']}: self relationship is forbidden")
        if len(related) != len(set(related)):
            raise ValueError(f"{record['enzyme_id']}: duplicate relationship reference")
        missing = [enzyme_id for enzyme_id in related if enzyme_id not in by_id]
        if missing:
            raise ValueError(f"{record['enzyme_id']}: relationship reference does not exist: {missing[0]}")

    for motif, motif_records in by_motif.items():
        expected_iso = _group_id("recognition_alternatives", list(motif))
        comparable = [record for record in motif_records if _known_dsb_geometry(record) is not None]
        for record in motif_records:
            relationships = record["relationships"]
            if relationships["isoschizomer_group_id"] != expected_iso:
                raise ValueError(f"{record['enzyme_id']}: isoschizomer group ID or membership is incorrect")
            geometry = _known_dsb_geometry(record)
            if geometry is None:
                if (
                    relationships["equischizomer_group_id"] is not None
                    or relationships["equischizomer_ids"]
                    or relationships["neoschizomer_ids"]
                ):
                    raise ValueError(f"{record['enzyme_id']}: unsupported geometry relationship")
                continue
            expected_equi_group = _group_id(
                "recognition_and_geometry", [list(motif), [list(pair) for pair in geometry]]
            )
            if relationships["equischizomer_group_id"] != expected_equi_group:
                raise ValueError(f"{record['enzyme_id']}: equischizomer group ID is incorrect")
            expected_equi = sorted(
                (
                    other["enzyme_id"]
                    for other in comparable
                    if other is not record and _known_dsb_geometry(other) == geometry
                ),
                key=str.casefold,
            )
            expected_neo = sorted(
                (
                    other["enzyme_id"]
                    for other in comparable
                    if other is not record and _known_dsb_geometry(other) != geometry
                ),
                key=str.casefold,
            )
            if relationships["equischizomer_ids"] != expected_equi:
                raise ValueError(f"{record['enzyme_id']}: equischizomer membership is incorrect")
            if relationships["neoschizomer_ids"] != expected_neo:
                raise ValueError(f"{record['enzyme_id']}: neoschizomer membership is incorrect")


def _expected_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    base_count = sum(
        record["source"]["kind"] == "biopython_restriction_dictionary" for record in records
    )
    return {
        "biopython_source_records": base_count,
        "curated_nickase_supplement_records": len(records) - base_count,
        "total_discoverable": len(records),
        "geometry_ready_double_strand": sum(
            record["analysis_capability"] == "digest_simulation" for record in records
        ),
        "commercial_geometry_ready_double_strand": sum(
            record["analysis_capability"] == "digest_simulation"
            and record["supplier_provenance"]["reported_commercial"]
            for record in records
        ),
        "recognition_only": sum(
            record["analysis_capability"] == "recognition_only" for record in records
        ),
        "nicking_analysis_only": sum(
            record["analysis_capability"] == "nicking_analysis" for record in records
        ),
    }


def validate_catalog_integrity(
    catalog: dict[str, Any], manifest: dict[str, Any] | None = None
) -> None:
    """Recompute and enforce catalog and optional manifest scientific integrity."""
    if not isinstance(catalog, dict) or not isinstance(catalog.get("records"), list):
        raise ValueError("catalog records must be an array")
    records = catalog["records"]
    ids = [record["enzyme_id"] for record in records]
    names = [record["canonical_name"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate enzyme_id")
    if len(names) != len(set(names)):
        raise ValueError("duplicate canonical_name")
    if len(ids) != len({value.casefold() for value in ids}):
        raise ValueError("case-fold enzyme_id collision")
    if len(names) != len({value.casefold() for value in names}):
        raise ValueError("case-fold canonical_name collision")
    if ids != sorted(ids, key=str.casefold):
        raise ValueError("catalog sorted order is incorrect")

    canonical_records: set[bytes] = set()
    for record in records:
        if record["enzyme_id"] != record["canonical_name"]:
            raise ValueError(f"{record['enzyme_id']}: durable v1 identity differs from canonical name")
        if record["source"]["canonical_name"] != record["canonical_name"]:
            raise ValueError(f"{record['enzyme_id']}: source canonical identity mismatch")
        _validate_recognition(record)
        _validate_cleavage(record)
        canonical_without_digest = copy.deepcopy(record)
        canonical_without_digest.pop("record_sha256", None)
        canonical_bytes = rfc8785.dumps(canonical_without_digest)
        if canonical_bytes in canonical_records:
            raise ValueError("duplicate canonical record")
        canonical_records.add(canonical_bytes)

    _validate_relationships(records)
    for record in records:
        if record.get("record_sha256") != _digest_without(record, "record_sha256"):
            raise ValueError(f"{record['enzyme_id']}: record_sha256 mismatch")
    if catalog.get("counts") != _expected_counts(records):
        raise ValueError("catalog count summary is incorrect")
    if catalog.get("content_sha256") != _digest_without(catalog, "content_sha256"):
        raise ValueError("catalog content_sha256 mismatch")

    if manifest is None:
        return
    if manifest.get("catalog_id") != catalog.get("catalog_id"):
        raise ValueError("manifest catalog identity mismatch")
    if manifest.get("catalog_content_sha256") != catalog.get("content_sha256"):
        raise ValueError("manifest catalog digest mismatch")
    if manifest.get("counts") != catalog.get("counts"):
        raise ValueError("manifest count summary mismatch")
    expected_records = [
        {"enzyme_id": record["enzyme_id"], "record_sha256": record["record_sha256"]}
        for record in records
    ]
    if manifest.get("records") != expected_records:
        raise ValueError("manifest record order or digest summary mismatch")
    if manifest.get("content_sha256") != _digest_without(manifest, "content_sha256"):
        raise ValueError("manifest content_sha256 mismatch")
