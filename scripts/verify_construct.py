#!/usr/bin/env python3
"""Fail-closed circular construct verification for BioModStack ONT/NGS runs."""

from __future__ import annotations

import argparse
import csv

import gzip
import hashlib
import html
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERIFIER_NAME = "biomodstack-construct-verifier"
VERIFIER_VERSION = "0.2.0"
SCHEMA_NAME = "biomodstack.construct_verification.v2"
DNA_COMPLEMENT = str.maketrans("ACGTN", "TGCAN")
CIGAR_TOKEN = re.compile(r"(\d+)([MIDNSHP=X])")
QUERY_CONSUMING = frozenset("MIS=X")
QUERY_LENGTH_CONSUMING = frozenset("MIS=XH")
MIN_SPLIT_MAPQ = 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def strict_nonnegative_json_integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ArithmeticError(f"topology {field} must be a non-negative JSON integer")
    return value


def strict_finite_json_number(value: Any, field: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ArithmeticError(f"topology {field} must be a finite JSON number")
    return float(value)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON number: {value}")),
        )
    except FileNotFoundError as exc:
        raise ValueError(f"missing JSON input: {path}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"malformed JSON input {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def read_single_fasta(path: Path) -> tuple[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError(f"missing FASTA input: {path}") from exc
    records: list[tuple[str, str]] = []
    name: str | None = None
    chunks: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None:
                records.append((name, "".join(chunks).upper()))
            name = line[1:].strip().split()[0] if line[1:].strip() else "unnamed"
            chunks = []
        elif name is None:
            raise ValueError(f"FASTA sequence appears before header: {path}")
        else:
            chunks.append("".join(line.split()))
    if name is not None:
        records.append((name, "".join(chunks).upper()))
    if len(records) != 1:
        raise ValueError(f"expected exactly one FASTA record in {path}; found {len(records)}")
    record_name, sequence = records[0]
    if not sequence:
        raise ValueError(f"FASTA sequence is empty: {path}")
    invalid = sorted(set(sequence) - set("ACGTN"))
    if invalid:
        raise ValueError(f"FASTA contains unsupported bases {invalid}: {path}")
    return record_name, sequence


def reverse_complement(sequence: str) -> str:
    return sequence.translate(DNA_COMPLEMENT)[::-1]


def exact_circular_equivalence(reference: str, observed: str) -> dict[str, Any] | None:
    if len(reference) != len(observed):
        return None
    doubled_reference = reference + reference
    forward_offset = doubled_reference.find(observed, 0, (2 * len(reference)) - 1)
    if 0 <= forward_offset < len(reference):
        return {"orientation": "forward", "rotation_offset": forward_offset}
    reversed_observed = reverse_complement(observed)
    reverse_offset = doubled_reference.find(reversed_observed, 0, (2 * len(reference)) - 1)
    if 0 <= reverse_offset < len(reference):
        return {"orientation": "reverse_complement", "rotation_offset": reverse_offset}
    return None


def _levenshtein_distance(reference: str, observed: str) -> int:
    """Exact unit-cost Levenshtein distance using Myers' bit-vector recurrence."""
    if not reference:
        return len(observed)
    equality_masks: dict[str, int] = {}
    for index, base in enumerate(reference):
        equality_masks[base] = equality_masks.get(base, 0) | (1 << index)
    positive = ~0
    negative = 0
    score = len(reference)
    final_bit = 1 << (len(reference) - 1)
    for base in observed:
        equal = equality_masks.get(base, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        positive_horizontal = negative | ~(horizontal | positive)
        negative_horizontal = positive & horizontal
        if positive_horizontal & final_bit:
            score += 1
        elif negative_horizontal & final_bit:
            score -= 1
        positive_horizontal = (positive_horizontal << 1) | 1
        negative_horizontal <<= 1
        positive = negative_horizontal | ~(vertical | positive_horizontal)
        negative = positive_horizontal & vertical
    return score


def _alignment_opcodes(reference: str, observed: str) -> tuple[list[tuple[str, int, int, int, int]], int, int]:
    """Return one deterministic exact minimum-edit global alignment."""
    reference_length = len(reference)
    observed_length = len(observed)
    matrix = [list(range(observed_length + 1))]
    for reference_index, reference_base in enumerate(reference, start=1):
        previous = matrix[-1]
        current = [reference_index]
        for observed_index, observed_base in enumerate(observed, start=1):
            current.append(
                min(
                    previous[observed_index - 1] + (reference_base != observed_base),
                    previous[observed_index] + 1,
                    current[observed_index - 1] + 1,
                )
            )
        matrix.append(current)

    atomic: list[tuple[str, int, int, int, int]] = []
    reference_index = reference_length
    observed_index = observed_length
    while reference_index or observed_index:
        if reference_index and observed_index:
            substitution_cost = reference[reference_index - 1] != observed[observed_index - 1]
            if matrix[reference_index][observed_index] == (
                matrix[reference_index - 1][observed_index - 1] + substitution_cost
            ):
                atomic.append(
                    (
                        "replace" if substitution_cost else "equal",
                        reference_index - 1,
                        reference_index,
                        observed_index - 1,
                        observed_index,
                    )
                )
                reference_index -= 1
                observed_index -= 1
                continue
        if observed_index and matrix[reference_index][observed_index] == matrix[reference_index][observed_index - 1] + 1:
            atomic.append(("insert", reference_index, reference_index, observed_index - 1, observed_index))
            observed_index -= 1
            continue
        if reference_index and matrix[reference_index][observed_index] == matrix[reference_index - 1][observed_index] + 1:
            atomic.append(("delete", reference_index - 1, reference_index, observed_index, observed_index))
            reference_index -= 1
            continue
        raise AssertionError("minimum-edit traceback became inconsistent")

    opcodes: list[tuple[str, int, int, int, int]] = []
    for operation in reversed(atomic):
        if (
            opcodes
            and opcodes[-1][0] == operation[0]
            and opcodes[-1][2] == operation[1]
            and opcodes[-1][4] == operation[3]
        ):
            tag, i1, _i2, j1, _j2 = opcodes[-1]
            opcodes[-1] = (tag, i1, operation[2], j1, operation[4])
        else:
            opcodes.append(operation)
    matches = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag == "equal")
    return opcodes, matches, matrix[-1][-1]


def best_circular_alignment(reference: str, observed: str) -> dict[str, Any]:
    if not observed:
        opcodes, matches, edit_cost = _alignment_opcodes(reference, observed)
        return {
            "orientation": "forward",
            "rotation_offset": 0,
            "normalized_observed": observed,
            "opcodes": opcodes,
            "matches": matches,
            "edit_cost": edit_cost,
            "identity_fraction": matches / max(len(reference), 1),
            "canonicalization": "exhaustive_minimum_edit_lexicographic_rotation_v1",
        }
    representations: dict[str, tuple[str, int]] = {}
    for orientation, oriented in (("forward", observed), ("reverse_complement", reverse_complement(observed))):
        for rotation_offset in range(len(oriented)):
            normalized = oriented[rotation_offset:] + oriented[:rotation_offset]
            metadata = (orientation, rotation_offset)
            if normalized not in representations or metadata < representations[normalized]:
                representations[normalized] = metadata
    scored = [
        (_levenshtein_distance(reference, normalized), normalized, *metadata)
        for normalized, metadata in representations.items()
    ]
    if not scored:
        raise ValueError("no circular-alignment candidates could be generated")
    minimum_cost = min(item[0] for item in scored)
    exact_candidates: list[tuple[Any, ...]] = []
    for edit_cost, normalized, orientation, rotation_offset in scored:
        if edit_cost != minimum_cost:
            continue
        opcodes, matches, traced_cost = _alignment_opcodes(reference, normalized)
        if traced_cost != edit_cost:
            raise AssertionError("bit-vector score disagrees with exact alignment traceback")
        edits = [opcode for opcode in opcodes if opcode[0] != "equal"]
        boundary_event_priority = 1
        if (
            edits
            and edits[0][1] == 0
            and edits[-1][2] == len(reference)
            and edits[0][0] in {"insert", "delete"}
            and edits[-1][0] in {"insert", "delete"}
        ):
            boundary_event_priority = 0
        event_key = tuple((tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in edits)
        exact_candidates.append(
            (
                len(edits),
                boundary_event_priority,
                event_key,
                normalized,
                orientation,
                rotation_offset,
                opcodes,
                matches,
                edit_cost,
            )
        )
    (
        _event_count,
        _boundary_priority,
        _event_key,
        normalized,
        orientation,
        rotation_offset,
        opcodes,
        matches,
        edit_cost,
    ) = min(exact_candidates, key=lambda item: item[:6])
    return {
        "orientation": orientation,
        "rotation_offset": rotation_offset,
        "normalized_observed": normalized,
        "opcodes": opcodes,
        "matches": matches,
        "edit_cost": edit_cost,
        "identity_fraction": matches / max(len(reference), len(observed), 1),
        "canonicalization": "exhaustive_minimum_edit_lexicographic_rotation_v1",
    }


def read_support_rows(path: Path) -> dict[int, dict[str, Any]]:
    required = {
        "position_1based",
        "depth",
        "a_count",
        "c_count",
        "g_count",
        "t_count",
        "n_count",
        "deletion_count",
        "insertion_count",
        "consensus_base",
        "major_allele_fraction",
    }
    parsed: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"support table missing variant columns: {sorted(missing)}")
        for row in reader:
            try:
                position = int(row["position_1based"])
                normalized = {
                    "depth": int(row["depth"]),
                    "A": int(row["a_count"]),
                    "C": int(row["c_count"]),
                    "G": int(row["g_count"]),
                    "T": int(row["t_count"]),
                    "N": int(row["n_count"]),
                    "deletion_count": int(row["deletion_count"]),
                    "insertion_count": int(row["insertion_count"]),
                    "consensus_base": str(row["consensus_base"]).strip().upper(),
                    "major_allele_fraction": float(row["major_allele_fraction"]),
                }
            except (TypeError, ValueError) as exc:
                raise ValueError("support table contains non-numeric variant evidence") from exc
            if not math.isfinite(normalized["major_allele_fraction"]):
                raise ValueError(f"support table has non-finite major fraction at position {position}")
            numeric_counts = [
                value
                for key, value in normalized.items()
                if key not in {"major_allele_fraction", "consensus_base"}
            ]
            if position in parsed or position < 1 or any(value < 0 for value in numeric_counts):
                raise ValueError(f"invalid or duplicate support row at position {position}")
            if normalized["consensus_base"] not in {"A", "C", "G", "T", "N", "-"}:
                raise ValueError(f"invalid consensus base at position {position}")
            parsed[position] = normalized
    return parsed


def _consensus_options_from_support(row: dict[str, int], reference_base: str) -> set[str]:
    depth = sum(int(row[base]) for base in "ACGTN") + int(row["deletion_count"])
    if depth <= 0:
        return {reference_base}
    counts = {base: int(row[base]) for base in "ACGTN"}
    best_count = max(counts.values())
    deletion_count = int(row["deletion_count"])
    if deletion_count > best_count:
        return {"-"}
    options = {base for base, count in counts.items() if count == best_count}
    if deletion_count == best_count:
        options.add("-")
    return options


def _consensus_base_from_support(row: dict[str, int], reference_base: str) -> str:
    options = _consensus_options_from_support(row, reference_base)
    if reference_base in options:
        return reference_base
    return next(base for base in "ACGTN-" if base in options)


def _insertion_consensus_options(row: dict[str, Any]) -> set[str]:
    alleles = row.get("insertion_alleles")
    if not isinstance(alleles, dict):
        return set()
    depth = sum(int(row[base]) for base in "ACGTN") + int(row["deletion_count"])
    insertion_count = int(row["insertion_count"])
    no_insertion_count = max(0, depth - insertion_count)
    best_count = max([no_insertion_count, *[int(count) for count in alleles.values()]], default=no_insertion_count)
    return {
        str(allele)
        for allele, count in alleles.items()
        if int(count) == best_count and int(count) > 0
    }


def validate_observed_consensus_binding(
    reference: str,
    observed: str,
    support_rows: dict[int, dict[str, Any]],
    recomputed_support: dict[int, dict[str, int]],
) -> None:
    """Bind every published/observed reference-position consensus call to BAM support."""
    contradictions: list[str] = []
    allowed_by_position: dict[int, set[str]] = {}
    for position in range(1, len(reference) + 1):
        published = support_rows.get(position)
        recomputed = recomputed_support.get(position)
        if published is None or recomputed is None:
            contradictions.append(f"position {position}: support row unavailable")
            continue
        allowed = _consensus_options_from_support(recomputed, reference[position - 1])
        allowed_by_position[position] = allowed
        published_base = str(published["consensus_base"])
        if published_base not in allowed:
            contradictions.append(
                f"position {position}: published consensus {published_base!r} not supported by BAM consensus options {sorted(allowed)!r}"
            )
        if len(contradictions) >= 20:
            break

    if not contradictions:
        alignment = best_circular_alignment(reference, observed)
        normalized_observed = str(alignment["normalized_observed"])
        observed_by_reference: dict[int, str | None] = {}
        observed_insertions: dict[int, str] = {}
        for tag, i1, i2, j1, j2 in alignment["opcodes"]:
            if tag in {"equal", "replace"}:
                for offset in range(min(i2 - i1, j2 - j1)):
                    observed_by_reference[i1 + offset + 1] = normalized_observed[j1 + offset]
            elif tag == "delete":
                for reference_index in range(i1, i2):
                    observed_by_reference[reference_index + 1] = None
            elif tag == "insert":
                anchor = i1 if i1 > 0 else 1
                observed_insertions[anchor] = normalized_observed[j1:j2]
        for position in range(1, len(reference) + 1):
            observed_base = observed_by_reference.get(position)
            normalized_base = "-" if observed_base is None else observed_base
            if normalized_base not in allowed_by_position[position]:
                contradictions.append(
                    f"position {position}: observed consensus {observed_base!r} not supported by BAM consensus options {sorted(allowed_by_position[position])!r}"
                )
            if len(contradictions) >= 20:
                break
        for anchor, inserted in observed_insertions.items():
            allowed_insertions = _insertion_consensus_options(recomputed_support[anchor])
            if inserted not in allowed_insertions:
                contradictions.append(
                    f"position {anchor}: observed insertion {inserted!r} not supported by BAM insertion consensus options {sorted(allowed_insertions)!r}"
                )
            if len(contradictions) >= 20:
                break
    if contradictions:
        raise ValueError("; ".join(contradictions))


def _variant_support(
    kind: str,
    alt: str,
    inserted_allele: str | None,
    support_positions: list[int],
    rows: dict[int, dict[str, Any]],
    profile: dict[str, Any],
) -> tuple[str, int | None, float | None]:
    evidence = [rows[position] for position in support_positions if position in rows]
    if not evidence:
        return "not_evaluated", None, None
    depths = [int(row["depth"]) for row in evidence]
    depth = min(depths)
    if depth <= 0:
        return "not_evaluated", depth, None
    if kind == "SNV" and alt in "ACGT":
        support_fraction = int(evidence[0][alt]) / max(1, int(evidence[0]["depth"]))
    elif kind == "INS":
        if not inserted_allele:
            return "not_evaluated", depth, None
        allele_counts = evidence[0].get("insertion_alleles")
        exact_count = int(allele_counts.get(inserted_allele, 0)) if isinstance(allele_counts, dict) else 0
        support_fraction = exact_count / max(1, int(evidence[0]["depth"]))
    elif kind == "DEL":
        fractions = [int(row["deletion_count"]) / max(1, int(row["depth"])) for row in evidence]
        support_fraction = min(fractions)
    else:
        support_fraction = None
    if support_fraction is None or depth < int(profile["min_depth"]):
        return "not_evaluated", depth, support_fraction
    if support_fraction >= float(profile["min_variant_support_fraction"]):
        return "supported", depth, support_fraction
    return "ambiguous", depth, support_fraction


def _left_normalize_indel(reference: str, variant: dict[str, Any]) -> None:
    """Left-align a VCF-style indel deterministically within linear reference coordinates."""
    if variant.get("kind") not in {"INS", "DEL"}:
        return
    position = int(variant["position_1based"])
    ref = str(variant["ref"])
    alt = str(variant["alt"])
    while position > 1 and ref and alt and ref[-1] == alt[-1]:
        previous = reference[position - 2]
        ref = previous + ref[:-1]
        alt = previous + alt[:-1]
        position -= 1
    variant["position_1based"] = position
    variant["ref"] = ref
    variant["alt"] = alt
    variant["end_1based"] = position if variant["kind"] == "INS" else position + len(ref) - 1


def call_variants(
    reference: str,
    observed: str,
    support_rows: dict[int, dict[str, Any]],
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    alignment = best_circular_alignment(reference, observed)
    normalized_observed = str(alignment["normalized_observed"])
    variants: list[dict[str, Any]] = []

    def add_variant(
        *,
        kind: str,
        position_1based: int,
        end_1based: int,
        ref: str,
        alt: str,
        support_positions: list[int],
        reference_start0: int,
        reference_end0: int,
        inserted_allele: str | None = None,
    ) -> None:
        support_status, depth, support_fraction = _variant_support(
            kind,
            alt[-1] if kind == "SNV" else alt,
            inserted_allele,
            support_positions,
            support_rows,
            profile,
        )
        variants.append(
            {
                "id": "",
                "kind": kind,
                "position_1based": position_1based,
                "end_1based": end_1based,
                "ref": ref,
                "alt": alt,
                "support_status": support_status,
                "depth": depth,
                "support_fraction": support_fraction,
                "circular_event_id": None,
                "_reference_start0": reference_start0,
                "_reference_end0": reference_end0,
            }
        )

    def add_snv(reference_index: int, observed_base: str) -> None:
        add_variant(
            kind="SNV",
            position_1based=reference_index + 1,
            end_1based=reference_index + 1,
            ref=reference[reference_index],
            alt=observed_base,
            support_positions=[reference_index + 1],
            reference_start0=reference_index,
            reference_end0=reference_index + 1,
        )

    def add_insertion(reference_index: int, inserted: str) -> None:
        if reference_index == 0:
            position = 1
            ref_allele = reference[0]
            alt_allele = inserted + reference[0]
        else:
            position = reference_index
            ref_allele = reference[reference_index - 1]
            alt_allele = ref_allele + inserted
        add_variant(
            kind="INS",
            position_1based=position,
            end_1based=position,
            ref=ref_allele,
            alt=alt_allele,
            support_positions=[position],
            reference_start0=reference_index,
            reference_end0=reference_index,
            inserted_allele=inserted,
        )

    def add_deletion(reference_start: int, reference_end: int) -> None:
        deleted = reference[reference_start:reference_end]
        if reference_start == 0:
            if reference_end >= len(reference):
                add_variant(
                    kind="COMPLEX",
                    position_1based=1,
                    end_1based=len(reference),
                    ref=reference,
                    alt="N",
                    support_positions=list(range(1, len(reference) + 1)),
                    reference_start0=reference_start,
                    reference_end0=reference_end,
                )
                return
            right_anchor = reference[reference_end]
            position = 1
            ref_allele = deleted + right_anchor
            alt_allele = right_anchor
        else:
            anchor = reference[reference_start - 1]
            position = reference_start
            ref_allele = anchor + deleted
            alt_allele = anchor
        add_variant(
            kind="DEL",
            position_1based=position,
            end_1based=reference_end,
            ref=ref_allele,
            alt=alt_allele,
            support_positions=list(range(reference_start + 1, reference_end + 1)),
            reference_start0=reference_start,
            reference_end0=reference_end,
        )

    for tag, i1, i2, j1, j2 in alignment["opcodes"]:
        if tag == "equal":
            continue
        if tag == "insert":
            add_insertion(i1, normalized_observed[j1:j2])
        elif tag == "delete":
            add_deletion(i1, i2)
        elif tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                add_snv(i1 + offset, normalized_observed[j1 + offset])
            if (i2 - i1) > paired:
                add_deletion(i1 + paired, i2)
            if (j2 - j1) > paired:
                add_insertion(i1 + paired, normalized_observed[j1 + paired : j2])

    start_events = [variant for variant in variants if variant["_reference_start0"] == 0 and variant["kind"] in {"DEL", "INS"}]
    end_events = [
        variant
        for variant in variants
        if variant["_reference_end0"] == len(reference) and variant["kind"] in {"DEL", "INS"}
    ]
    if start_events and end_events:
        for variant in start_events + end_events:
            variant["circular_event_id"] = "circular_event_1"

    for variant in variants:
        _left_normalize_indel(reference, variant)

    variants.sort(key=lambda variant: (variant["position_1based"], variant["kind"], variant["ref"], variant["alt"]))
    for index, variant in enumerate(variants, start=1):
        variant["id"] = f"var{index}"
        variant.pop("_reference_start0", None)
        variant.pop("_reference_end0", None)
    alignment_summary = {
        "orientation": alignment["orientation"],
        "rotation_offset": alignment["rotation_offset"],
        "identity_fraction": alignment["identity_fraction"],
        "edit_cost": alignment["edit_cost"],
        "reference_length": len(reference),
        "observed_length": len(observed),
        "canonicalization": alignment["canonicalization"],
    }
    return variants, alignment_summary


def _parse_cigar(cigar: str) -> list[tuple[int, str]]:
    if cigar in {"", "*"}:
        return []
    tokens = [(int(length), op) for length, op in CIGAR_TOKEN.findall(cigar)]
    if not tokens or "".join(f"{length}{op}" for length, op in tokens) != cigar:
        raise ValueError(f"malformed CIGAR: {cigar!r}")
    return tokens


def _alignment_segment(
    flag: int,
    start: int,
    mapq: int,
    tokens: list[tuple[int, str]],
) -> dict[str, Any]:
    reference_span = sum(length for length, op in tokens if op in "MDN=X")
    query_length = sum(length for length, op in tokens if op in QUERY_LENGTH_CONSUMING)
    leading_clip = tokens[0][0] if tokens and tokens[0][1] in {"S", "H"} else 0
    trailing_clip = tokens[-1][0] if tokens and tokens[-1][1] in {"S", "H"} else 0
    query_start = leading_clip
    query_end = query_length - trailing_clip
    reverse = bool(flag & 0x10)
    if reverse:
        query_start, query_end = query_length - query_end, query_length - query_start
    return {
        "reference_start": start,
        "reference_end": start + reference_span - 1,
        "query_start": query_start,
        "query_end": query_end,
        "query_length": query_length,
        "clipped": leading_clip > 0 or trailing_clip > 0,
        "supplementary": bool(flag & 0x800),
        "reverse": reverse,
        "mapq": mapq,
    }


def _valid_origin_wrap_pair(
    first: dict[str, Any],
    second: dict[str, Any],
    reference_length: int,
    edge: int,
) -> bool:
    if first["supplementary"] == second["supplementary"]:
        return False
    if first["reverse"] != second["reverse"] or min(first["mapq"], second["mapq"]) < MIN_SPLIT_MAPQ:
        return False
    if not first["clipped"] or not second["clipped"] or first["query_length"] != second["query_length"]:
        return False
    left, right = sorted((first, second), key=lambda segment: (segment["query_start"], segment["query_end"]))
    if not (
        left["query_start"] == 0
        and left["query_end"] == right["query_start"]
        and right["query_end"] == left["query_length"]
        and left["query_end"] > left["query_start"]
        and right["query_end"] > right["query_start"]
    ):
        return False
    start_segments = [segment for segment in (first, second) if segment["reference_start"] <= edge + 1]
    end_segments = [segment for segment in (first, second) if segment["reference_end"] >= reference_length - edge]
    if len(start_segments) != 1 or len(end_segments) != 1 or start_segments[0] is end_segments[0]:
        return False
    if first["reverse"]:
        return start_segments[0]["query_start"] < end_segments[0]["query_start"]
    return end_segments[0]["query_start"] < start_segments[0]["query_start"]


def count_valid_origin_wraps(
    segments_by_read: dict[str, list[dict[str, Any]]],
    reference_length: int,
    edge: int,
) -> int:
    return sum(
        1
        for segments in segments_by_read.values()
        if any(
            _valid_origin_wrap_pair(segments[first], segments[second], reference_length, edge)
            for first in range(len(segments))
            for second in range(first + 1, len(segments))
        )
    )


def read_alignment_records(
    bam_path: Path,
    samtools_command: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    argv = [*samtools_command, "view", str(bam_path)]
    completed = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120)
    if completed.returncode != 0:
        raise ValueError((completed.stderr or completed.stdout or "samtools view failed").strip())
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(completed.stdout.splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) < 11:
            raise ValueError(f"malformed SAM record at line {line_number}")
        try:
            flag = int(fields[1])
            position = int(fields[3])
            mapq = int(fields[4])
        except ValueError as exc:
            raise ValueError(f"non-integer SAM field at line {line_number}") from exc
        records.append(
            {
                "qname": fields[0],
                "flag": flag,
                "rname": fields[2],
                "position": position,
                "mapq": mapq,
                "cigar": fields[5],
                "sequence": fields[9].upper(),
            }
        )
    return records, {"name": "samtools_view_recompute", "argv": argv}


def recompute_alignment_semantics(
    records: list[dict[str, Any]],
    reference_name: str,
    reference: str,
) -> dict[str, Any]:
    primary_by_read: dict[str, dict[str, Any]] = {}
    support: dict[int, dict[str, Any]] = {
        position: {
            "A": 0,
            "C": 0,
            "G": 0,
            "T": 0,
            "N": 0,
            "deletion_count": 0,
            "insertion_count": 0,
            "insertion_alleles": {},
            "depth": 0,
            "forward_depth": 0,
            "reverse_depth": 0,
        }
        for position in range(1, len(reference) + 1)
    }
    segments_by_read: dict[str, list[dict[str, Any]]] = {}
    mapped_alignment_records = 0
    for record in records:
        flag = int(record["flag"])
        qname = str(record["qname"])
        if not flag & (0x100 | 0x800):
            if qname in primary_by_read:
                raise ValueError(f"multiple primary alignment records for read {qname!r}")
            primary_by_read[qname] = record
        if flag & 0x100:
            continue
        if flag & 0x4:
            continue
        if record["rname"] != reference_name:
            raise ValueError(f"alignment record targets unexpected reference {record['rname']!r}")
        tokens = _parse_cigar(str(record["cigar"]))
        reference_position = int(record["position"])
        mapq = int(record["mapq"])
        read_position = 0
        reference_span = sum(length for length, op in tokens if op in "MDN=X")
        if reference_position < 1 or reference_span <= 0 or reference_position + reference_span - 1 > len(reference):
            raise ValueError(f"alignment coordinates outside reference for read {qname!r}")
        segments_by_read.setdefault(qname, []).append(_alignment_segment(flag, reference_position, mapq, tokens))
        mapped_alignment_records += 1
        sequence = str(record["sequence"])
        for length, op in tokens:
            if op in {"M", "=", "X"}:
                for offset in range(length):
                    position = reference_position + offset
                    base = sequence[read_position + offset] if read_position + offset < len(sequence) else "N"
                    base = base if base in "ACGTN" else "N"
                    support[position][base] += 1
                    support[position]["depth"] += 1
                    support[position]["reverse_depth" if flag & 0x10 else "forward_depth"] += 1
                reference_position += length
                read_position += length
            elif op == "I":
                anchor = reference_position - 1 if reference_position > 1 else reference_position
                if 1 <= anchor <= len(reference):
                    support[anchor]["insertion_count"] += 1
                    inserted = sequence[read_position : read_position + length]
                    if len(inserted) != length:
                        raise ValueError(f"CIGAR insertion exceeds query sequence for read {qname!r}")
                    alleles = support[anchor]["insertion_alleles"]
                    alleles[inserted] = int(alleles.get(inserted, 0)) + 1
                read_position += length
            elif op == "D":
                for offset in range(length):
                    position = reference_position + offset
                    support[position]["deletion_count"] += 1
                    support[position]["depth"] += 1
                    support[position]["reverse_depth" if flag & 0x10 else "forward_depth"] += 1
                reference_position += length
            elif op == "N":
                reference_position += length
            elif op == "S":
                read_position += length

    mapped_reads = sum(1 for record in primary_by_read.values() if not int(record["flag"]) & 0x4)
    unmapped_reads = sum(1 for record in primary_by_read.values() if int(record["flag"]) & 0x4)
    return {
        "total_reads": len(primary_by_read),
        "mapped_reads": mapped_reads,
        "unmapped_reads": unmapped_reads,
        "alignment_records": mapped_alignment_records,
        "primary_by_read": primary_by_read,
        "support": support,
        "segments_by_read": segments_by_read,
    }


def _read_fastq(path: Path) -> dict[str, str]:
    with path.open("rb") as raw:
        magic = raw.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        lines = [line.rstrip("\r\n") for line in handle]
    if len(lines) % 4:
        raise ValueError("source reads are not four-line FASTQ records")
    records: dict[str, str] = {}
    for offset in range(0, len(lines), 4):
        header, sequence, plus, qualities = lines[offset : offset + 4]
        if not header.startswith("@") or not plus.startswith("+") or len(sequence) != len(qualities):
            raise ValueError(f"malformed FASTQ record {offset // 4 + 1}")
        qname = header[1:].split()[0]
        if not qname or qname in records:
            raise ValueError(f"empty or duplicate FASTQ read name {qname!r}")
        records[qname] = sequence.upper()
    if not records:
        raise ValueError("source reads FASTQ is empty")
    return records


def validate_source_read_binding(
    state: dict[str, Any],
    observed_state_path: Path,
    alignment_semantics: dict[str, Any] | None,
) -> tuple[Path | None, dict[str, Any]]:
    raw_path = state.get("source_reads_path")
    provenance = state.get("source_read_provenance")
    if not isinstance(raw_path, str) or not raw_path or not isinstance(provenance, dict):
        return None, semantic_validation("invalid", "source FASTQ to primary BAM recomputation v1", "source-read provenance is missing")
    if provenance.get("binding_method") != "qname_and_sequence_against_primary_bam":
        return None, semantic_validation("invalid", "source FASTQ to primary BAM recomputation v1", "unsupported binding method")
    bundle_root = observed_state_path.parent.resolve()
    source_path = (bundle_root / raw_path).resolve()
    if not source_path.is_relative_to(bundle_root) or not source_path.is_file():
        return source_path, semantic_validation("invalid", "source FASTQ to primary BAM recomputation v1", "retained source reads are missing")
    if state.get("source_reads_sha256") != sha256_file(source_path):
        return source_path, semantic_validation("invalid", "source FASTQ to primary BAM recomputation v1", "source reads digest mismatch")
    if alignment_semantics is None:
        return source_path, semantic_validation("invalid", "source FASTQ to primary BAM recomputation v1", "alignment semantics unavailable")
    try:
        source_reads = _read_fastq(source_path)
        primary = alignment_semantics["primary_by_read"]
        if set(source_reads) != set(primary):
            raise ValueError("source FASTQ and BAM primary read names differ")
        for qname, source_sequence in source_reads.items():
            bam_sequence = str(primary[qname]["sequence"])
            if bam_sequence == "*" or bam_sequence not in {source_sequence, reverse_complement(source_sequence)}:
                raise ValueError(f"source FASTQ and BAM sequence differ for read {qname!r}")
    except (OSError, TypeError, ValueError) as exc:
        return source_path, semantic_validation("invalid", "source FASTQ to primary BAM recomputation v1", str(exc))
    return source_path, semantic_validation("valid", "source FASTQ to primary BAM recomputation v1")


def file_evidence(
    path: Path | None,
    *,
    role: str,
    state: str | None = None,
    source_kind: str | None = None,
    independent_from_expected: bool | None = None,
    reason: str | None = None,
    validation: dict[str, Any] | None = None,
    normalized_sequence_sha256: str | None = None,
    declared_sequence_sha256: str | None = None,
) -> dict[str, Any]:
    present_path = path if path is not None and path.is_file() else None
    effective_state = state or ("present" if present_path is not None else "missing")
    if validation is None:
        if present_path is not None and effective_state == "present":
            validation = semantic_validation("valid", "file presence and digest")
        elif present_path is not None:
            validation = semantic_validation("invalid", "file presence and digest", reason or effective_state)
        else:
            validation = semantic_validation("unavailable", "file presence and digest", reason or "file is missing")
    return {
        "state": effective_state,
        "role": role,
        "declared_path": path.name if path is not None else None,
        "sha256": sha256_file(present_path) if present_path is not None else None,
        "size_bytes": present_path.stat().st_size if present_path is not None else None,
        "source_kind": source_kind,
        "independent_from_expected": independent_from_expected,
        "reason": reason,
        "semantic_validation": validation,
        "normalized_sequence_sha256": normalized_sequence_sha256,
        "declared_sequence_sha256": declared_sequence_sha256,
    }


def read_support_metrics(
    path: Path,
    reference_name: str,
    reference: str,
    profile: dict[str, Any],
    recomputed_support: dict[int, dict[str, int]] | None,
) -> dict[str, Any]:
    required_columns = {
        "chrom",
        "position_1based",
        "reference_base",
        "depth",
        "forward_depth",
        "reverse_depth",
        "a_count",
        "c_count",
        "g_count",
        "t_count",
        "n_count",
        "insertion_count",
        "deletion_count",
        "consensus_base",
        "major_allele_fraction",
    }
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"support table missing columns: {sorted(missing)}")
        rows = list(reader)
    positions: set[int] = set()
    covered = 0
    low_depth = 0
    mixed = 0
    strand_imbalanced = 0
    min_depth_seen: int | None = None
    reference_length = len(reference)
    for row in rows:
        try:
            position = int(row["position_1based"])
            depth = int(row["depth"])
            forward = int(row["forward_depth"])
            reverse = int(row["reverse_depth"])
            counts = {base: int(row[f"{base.lower()}_count"]) for base in "ACGTN"}
            deletion_count = int(row["deletion_count"])
            insertion_count = int(row["insertion_count"])
            major_fraction = float(row["major_allele_fraction"])
        except (TypeError, ValueError) as exc:
            raise ValueError("support table contains a non-numeric required field") from exc
        if position < 1 or position > reference_length or position in positions:
            raise ValueError(f"support table has invalid or duplicate position: {position}")
        if not math.isfinite(major_fraction):
            raise ValueError(f"support table has non-finite major fraction at position {position}")
        if str(row["chrom"]) != reference_name or str(row["reference_base"]).upper() != reference[position - 1]:
            raise ValueError(f"support table reference identity mismatch at position {position}")
        if min(depth, forward, reverse, deletion_count, insertion_count, *counts.values()) < 0:
            raise ValueError(f"support table has invalid counts at position {position}")
        if sum(counts.values()) + deletion_count != depth or forward + reverse != depth:
            raise ValueError(f"support table count arithmetic mismatch at position {position}")
        if not 0 <= major_fraction <= 1:
            raise ValueError(f"support table has invalid major fraction at position {position}")
        expected_major_fraction = max([*counts.values(), deletion_count]) / depth if depth else 0.0
        # Production support tables publish this fraction to four decimal places.
        if not math.isclose(major_fraction, expected_major_fraction, rel_tol=0.0, abs_tol=5.0001e-5):
            raise ValueError(f"support table major fraction mismatch at position {position}")
        if insertion_count > max(0, forward + reverse):
            raise ValueError(f"support table insertion count exceeds eligible reads at position {position}")
        if recomputed_support is not None:
            expected = recomputed_support[position]
            observed_counts = {
                **counts,
                "deletion_count": deletion_count,
                "insertion_count": insertion_count,
                "forward_depth": forward,
                "reverse_depth": reverse,
            }
            expected_counts = {key: int(expected[key]) for key in observed_counts}
            if observed_counts != expected_counts:
                raise ValueError(f"support table does not match BAM-derived counts at position {position}")
        positions.add(position)
        min_depth_seen = depth if min_depth_seen is None else min(min_depth_seen, depth)
        if depth > 0:
            covered += 1
        if depth < int(profile["min_depth"]):
            low_depth += 1
        if depth >= int(profile["min_depth"]) and major_fraction < float(profile["min_major_allele_fraction"]):
            mixed += 1
        if depth > 0:
            dominance = max(forward, reverse) / max(1, forward + reverse)
            if dominance > float(profile["max_strand_dominance_fraction"]):
                strand_imbalanced += 1
    missing_positions = reference_length - len(positions)
    low_depth += max(0, missing_positions)
    coverage_fraction = covered / reference_length if reference_length else 0.0
    low_depth_fraction = low_depth / reference_length if reference_length else 1.0
    return {
        "row_count": len(rows),
        "coverage_fraction": coverage_fraction,
        "low_depth_positions": low_depth,
        "low_depth_fraction": low_depth_fraction,
        "mixed_allele_positions": mixed,
        "strand_imbalanced_positions": strand_imbalanced,
        "minimum_depth": min_depth_seen if min_depth_seen is not None else 0,
    }


def read_metric_table(path: Path) -> dict[str, float]:
    required_numeric = {"total_reads", "mapped_reads", "unmapped_reads"}
    metrics: dict[str, float] = {}
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["metric", "value"]:
            raise ValueError("alignment stats must contain exactly metric and value columns")
        for row in reader:
            name = str(row.get("metric", "")).strip()
            if not name or name in seen:
                raise ValueError("alignment stats contain an empty or duplicate metric")
            seen.add(name)
            if name not in required_numeric:
                continue
            try:
                numeric = float(row["value"])
                if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
                    raise ValueError
                metrics[name] = int(numeric)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"alignment metric '{name}' is not numeric") from exc
    missing = required_numeric - set(metrics)
    if missing:
        raise ValueError(f"alignment stats missing required metrics: {sorted(missing)}")
    return metrics


def semantic_validation(status: str, validator: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "validator": validator,
        "reason": reason,
    }


def validate_alignment_artifacts(
    bam_path: Path | None,
    index_path: Path | None,
    samtools_command: list[str],
) -> tuple[dict[str, Any], dict[str, Any], str | None, list[dict[str, Any]]]:
    bam_validation = semantic_validation("invalid", "samtools quickcheck", "alignment BAM is missing")
    index_validation = semantic_validation("invalid", "samtools idxstats -X", "alignment index is missing")
    commands: list[dict[str, Any]] = []
    if bam_path is None or not bam_path.is_file() or index_path is None or not index_path.is_file():
        return bam_validation, index_validation, None, commands

    version: str | None = None
    try:
        version_result = subprocess.run(
            [*samtools_command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if version_result.returncode == 0:
            version = version_result.stdout.splitlines()[0].strip() or None

        quickcheck_argv = [*samtools_command, "quickcheck", "-v", str(bam_path)]
        commands.append({"name": "samtools_quickcheck", "argv": quickcheck_argv})
        quickcheck = subprocess.run(
            quickcheck_argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if quickcheck.returncode != 0:
            reason = (quickcheck.stderr or quickcheck.stdout or "samtools quickcheck failed").strip()
            bam_validation = semantic_validation("invalid", "samtools quickcheck", reason)
            index_validation = semantic_validation("invalid", "samtools idxstats private exact-index sidecar", "BAM validation failed")
            return bam_validation, index_validation, version, commands
        bam_validation = semantic_validation("valid", "samtools quickcheck")

        try:
            index_magic = index_path.read_bytes()[:4]
        except OSError as exc:
            index_validation = semantic_validation("invalid", "BAI/CSI magic and samtools idxstats private exact-index sidecar", str(exc))
            return bam_validation, index_validation, version, commands
        if index_magic not in {b"BAI\x01", b"CSI\x01"}:
            index_validation = semantic_validation(
                "invalid",
                "BAI/CSI magic and samtools idxstats private exact-index sidecar",
                f"unexpected alignment index magic: {index_magic.hex() or 'empty'}",
            )
            return bam_validation, index_validation, version, commands

        with tempfile.TemporaryDirectory(prefix="biomodstack-index-check-") as temporary_directory:
            private_root = Path(temporary_directory)
            private_bam = private_root / "alignment.bam"
            private_index = private_root / ("alignment.bam.csi" if index_magic == b"CSI\x01" else "alignment.bam.bai")
            private_bam.symlink_to(bam_path.resolve())
            private_index.symlink_to(index_path.resolve())
            idxstats_argv = [*samtools_command, "idxstats", str(private_bam)]
            commands.append({"name": "samtools_idxstats_private_exact_index", "argv": idxstats_argv})
            idxstats = subprocess.run(
                idxstats_argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        if idxstats.returncode != 0:
            reason = (idxstats.stderr or idxstats.stdout or "samtools idxstats failed").strip()
            index_validation = semantic_validation("invalid", "samtools idxstats private exact-index sidecar", reason)
        else:
            index_validation = semantic_validation("valid", "samtools idxstats private exact-index sidecar")
    except (OSError, subprocess.SubprocessError) as exc:
        reason = f"samtools validation unavailable: {exc}"
        bam_validation = semantic_validation("invalid", "samtools quickcheck", reason)
        index_validation = semantic_validation("invalid", "samtools idxstats private exact-index sidecar", reason)
    return bam_validation, index_validation, version, commands


def make_check(status: str, reason_codes: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    return {"status": status, "reason_codes": sorted(set(reason_codes)), "metrics": metrics}


def write_vcf(path: Path, reference_name: str, reference_length: int, variants: list[dict[str, Any]]) -> None:
    lines = [
        "##fileformat=VCFv4.2",
        "##source=biomodstack-construct-verifier-" + VERIFIER_VERSION,
        f"##contig=<ID={reference_name},length={reference_length},topology=circular>",
        '##INFO=<ID=SUPPORT,Number=1,Type=Float,Description="Observed allele support fraction">',
        '##INFO=<ID=SUPPORT_STATUS,Number=1,Type=String,Description="Verifier support classification">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
    ]
    for variant in variants:
        support = variant.get("support_fraction")
        support_text = "." if support is None else f"{support:.6f}"
        lines.append(
            "\t".join(
                [
                    reference_name,
                    str(variant["position_1based"]),
                    variant["id"],
                    variant["ref"],
                    variant["alt"],
                    ".",
                    "PASS" if variant["support_status"] == "supported" else "REVIEW",
                    f"SUPPORT={support_text};SUPPORT_STATUS={variant['support_status']}",
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary(path: Path, manifest: dict[str, Any]) -> None:
    rows = [
        ("verdict", manifest["verdict"]),
        ("reason_codes", ",".join(manifest["reason_codes"])),
        ("sequence_identity_fraction", manifest["summary"].get("sequence_identity_fraction")),
        ("variant_count", manifest["summary"].get("variant_count")),
        ("coverage_fraction", manifest["summary"].get("coverage_fraction")),
        ("unmapped_fraction", manifest["summary"].get("unmapped_fraction")),
        ("topology_status", manifest["checks"]["topology"]["status"]),
    ]
    path.write_text(
        "metric\tvalue\n" + "".join(f"{key}\t{'' if value is None else value}\n" for key, value in rows),
        encoding="utf-8",
    )


def write_evidence_html(path: Path, manifest: dict[str, Any]) -> None:
    verdict = html.escape(str(manifest["verdict"]))
    reasons = "".join(f"<li><code>{html.escape(code)}</code></li>" for code in manifest["reason_codes"])
    summary = manifest.get("summary", {})
    sequence_check = manifest.get("checks", {}).get("sequence_identity", {})
    variant_analysis_complete = "VARIANT_ANALYSIS_PENDING" not in sequence_check.get("reason_codes", [])
    topology_check = manifest.get("checks", {}).get("topology", {})
    topology_state = summary.get("topology_status") or topology_check.get("metrics", {}).get("state") or "unavailable"

    def percent(value: Any) -> str:
        return "unavailable" if value is None else f"{float(value) * 100:.2f}%"

    decision_rows = (
        ("Sequence identity", percent(summary.get("sequence_identity_fraction"))),
        (
            "Observed variants",
            str(summary.get("variant_count", len(manifest.get("variants", []))))
            if variant_analysis_complete
            else "not assessed",
        ),
        ("Coverage", percent(summary.get("coverage_fraction"))),
        ("Unmapped reads", percent(summary.get("unmapped_fraction"))),
        ("Topology", str(topology_state)),
    )
    decision = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in decision_rows
    )

    variants = manifest.get("variants", [])
    variant_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(variant.get('id', '')))}</td>"
        f"<td>{html.escape(str(variant.get('kind', '')))}</td>"
        f"<td>{html.escape(str(variant.get('position_1based', '')))}</td>"
        f"<td>{html.escape(str(variant.get('ref', '')))}</td>"
        f"<td>{html.escape(str(variant.get('alt', '')))}</td>"
        f"<td>{percent(variant.get('support_fraction'))}</td>"
        f"<td>{html.escape(str(variant.get('support_status', '')))}</td>"
        f"<td>{html.escape(str(variant.get('depth', '')))}</td>"
        "</tr>"
        for variant in variants
    ) or (
        "<tr><td colspan='8'>No observed variants.</td></tr>"
        if variant_analysis_complete
        else "<tr><td colspan='8'>Variant analysis was not completed.</td></tr>"
    )

    def metric_value(value: Any) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            return f"{value:.6g}"
        if isinstance(value, (dict, list)):
            return html.escape(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return html.escape(str(value))

    check_sections = []
    for name, check in manifest["checks"].items():
        metric_rows = "".join(
            f"<tr><th>{html.escape(metric.replace('_', ' ').title())}</th><td>{metric_value(value)}</td></tr>"
            for metric, value in check.get("metrics", {}).items()
            if metric != "provenance"
        )
        check_sections.append(
            f"<section><h3>{html.escape(name.replace('_', ' ').title())}: "
            f"{html.escape(str(check['status']).upper())}</h3>"
            f"<table><tbody>{metric_rows}</tbody></table></section>"
        )
    checks = "".join(
        f"<tr><th>{html.escape(name.replace('_', ' ').title())}</th><td>{html.escape(check['status'])}</td>"
        f"<td>{html.escape(', '.join(check['reason_codes']) or 'none')}</td></tr>"
        for name, check in manifest["checks"].items()
    )
    path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Construct verification evidence</title>"
        "<style>body{font-family:system-ui;margin:2rem auto;max-width:1200px;background:#111827;color:#e5e7eb}"
        "table{border-collapse:collapse;width:100%;margin-bottom:1rem}th,td{border:1px solid #374151;padding:.5rem;text-align:left}"
        ".metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.75rem}.metric{background:#1f2937;padding:1rem;border-radius:.5rem}"
        ".metric span{display:block;color:#9ca3af}.metric strong{font-size:1.35rem}section{margin:1rem 0;padding:1rem;background:#1f2937;border-radius:.5rem}"
        "code{color:#93c5fd}</style></head><body>"
        f"<h1>Construct verification: {verdict}</h1><h2>Decision summary</h2><div class='metrics'>{decision}</div>"
        f"<h2>Reason codes</h2><ul>{reasons}</ul>"
        f"<h2>Independent checks</h2><table><tr><th>Check</th><th>Status</th><th>Reasons</th></tr>{checks}</table>"
        "<h2>Observed variants</h2><table><thead><tr><th>ID</th><th>Type</th><th>Position</th><th>Reference</th><th>Observed</th><th>Support</th><th>Classification</th><th>Depth</th></tr></thead>"
        f"<tbody>{variant_rows}</tbody></table><h2>Check evidence</h2>{''.join(check_sections)}"
        "<p>This portable report contains the decision-relevant machine evidence. The bound JSON manifest remains the complete provenance authority.</p>"
        "</body></html>\n",
        encoding="utf-8",
    )


def artifact_record(
    path: Path | None,
    *,
    kind: str,
    required: bool,
    reason: str | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    present_path = path if path is not None and path.is_file() else None
    if present_path is not None:
        state = "present"
    elif required:
        state = "missing_required"
    else:
        state = "not_produced"
    if validation is None:
        validation = (
            semantic_validation("valid", f"{kind} structural validation")
            if present_path is not None
            else semantic_validation("unavailable", f"{kind} structural validation", reason or state)
        )
    return {
        "kind": kind,
        "path": present_path.name if present_path is not None else None,
        "required": required,
        "state": state,
        "sha256": sha256_file(present_path) if present_path is not None else None,
        "size_bytes": present_path.stat().st_size if present_path is not None else None,
        "reason": reason,
        "semantic_validation": validation,
    }


def run_verification(args: argparse.Namespace) -> dict[str, Any]:
    reference_path = args.reference_fasta.resolve()
    observed_state_path = args.observed_state.resolve()
    observed_path = args.observed_fasta.resolve()
    support_path = args.per_base_support.resolve()
    stats_path = args.alignment_stats.resolve()
    topology_path = args.topology_evidence.resolve()
    breakpoint_call_path = args.breakpoint_call.resolve() if args.breakpoint_call is not None else None
    secondary_summary_path = args.secondary_summary.resolve() if args.secondary_summary is not None else None
    profile_path = args.profile_config.resolve()
    alignment_bam_path = args.alignment_bam.resolve() if args.alignment_bam is not None else None
    alignment_index_path = args.alignment_index.resolve() if args.alignment_index is not None else None
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    reference_name, reference = read_single_fasta(reference_path)
    observed_state_error: str | None = None
    try:
        state = read_json(observed_state_path)
    except ValueError as exc:
        state = {}
        observed_state_error = str(exc)
    profile_document = read_json(profile_path)
    profiles = profile_document.get("profiles")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(args.profile), dict):
        raise ValueError(f"unknown threshold profile: {args.profile}")
    profile = dict(profiles[args.profile])

    checks: dict[str, dict[str, Any]] = {}
    aggregate_reasons: list[str] = []
    samtools_command = args.samtools_command or [args.samtools_bin]
    (
        alignment_bam_validation,
        alignment_index_validation,
        samtools_version,
        alignment_commands,
    ) = validate_alignment_artifacts(
        alignment_bam_path,
        alignment_index_path,
        samtools_command,
    )
    alignment_evidence_valid = (
        alignment_bam_validation["status"] == "valid"
        and alignment_index_validation["status"] == "valid"
    )
    if not alignment_evidence_valid:
        aggregate_reasons.append("ALIGNMENT_EVIDENCE_INVALID")
    alignment_semantics: dict[str, Any] | None = None
    if alignment_evidence_valid and alignment_bam_path is not None:
        try:
            alignment_records, recompute_command = read_alignment_records(alignment_bam_path, samtools_command)
            alignment_commands.append(recompute_command)
            alignment_semantics = recompute_alignment_semantics(alignment_records, reference_name, reference)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            alignment_bam_validation = semantic_validation("invalid", "BAM semantic recomputation v1", str(exc))
            alignment_evidence_valid = False
            aggregate_reasons.append("ALIGNMENT_EVIDENCE_INVALID")
    source_reads_path, source_reads_validation = validate_source_read_binding(
        state,
        observed_state_path,
        alignment_semantics,
    )
    source_reads_valid = source_reads_validation["status"] == "valid"
    if not source_reads_valid:
        aggregate_reasons.append("SOURCE_READ_PROVENANCE_INVALID")
    actual_reference_sequence_sha256 = hashlib.sha256(reference.encode("ascii")).hexdigest()
    declared_reference_sequence_sha256 = args.expected_reference_sha256
    reference_binding_reason: str | None = None
    if declared_reference_sequence_sha256 is None:
        reference_binding_reason = "REFERENCE_DIGEST_UNBOUND"
    elif declared_reference_sequence_sha256 != actual_reference_sequence_sha256:
        reference_binding_reason = "REFERENCE_DIGEST_MISMATCH"
    if reference_binding_reason is not None:
        aggregate_reasons.append(reference_binding_reason)
    observed: str | None = None
    observed_name: str | None = None
    observed_input_state = "missing"
    observed_reason = observed_state_error or state.get("reason")
    observed_digest_actual: str | None = None
    observed_trusted = False
    variant_analysis_pending = False
    variants: list[dict[str, Any]] = []
    variant_alignment: dict[str, Any] = {}

    if observed_state_error is not None:
        observed_input_state = "malformed"
        aggregate_reasons.append("MALFORMED_OBSERVED_STATE")
        checks["sequence_identity"] = make_check(
            "review",
            ["MALFORMED_OBSERVED_STATE"],
            {"error": observed_state_error},
        )
    elif state.get("state") != "present" or not observed_path.is_file():
        aggregate_reasons.append("MISSING_OBSERVED_CONSENSUS")
        checks["sequence_identity"] = make_check("review", ["MISSING_OBSERVED_CONSENSUS"], {})
    else:
        observed_digest_actual = sha256_file(observed_path)
        declared_digest = state.get("observed_sha256")
        is_fallback = bool(state.get("fallback")) or state.get("method") == "reference_copy_fallback"
        if declared_digest != observed_digest_actual:
            observed_input_state = "digest_mismatch"
            aggregate_reasons.append("OBSERVED_DIGEST_MISMATCH")
            checks["sequence_identity"] = make_check("review", ["OBSERVED_DIGEST_MISMATCH"], {})
        elif is_fallback:
            observed_input_state = "present"
            aggregate_reasons.append("FALLBACK_OBSERVED_EVIDENCE")
            checks["sequence_identity"] = make_check("review", ["FALLBACK_OBSERVED_EVIDENCE"], {})
        elif not source_reads_valid:
            observed_input_state = "present"
            checks["sequence_identity"] = make_check(
                "review",
                ["SOURCE_READ_PROVENANCE_INVALID"],
                {"source_read_validation": source_reads_validation},
            )
        else:
            observed_input_state = "present"
            observed_name, observed = read_single_fasta(observed_path)
            observed_trusted = True
            circular_match = exact_circular_equivalence(reference, observed)
            if circular_match is None:
                variant_analysis_pending = True
                checks["sequence_identity"] = make_check(
                    "review",
                    ["VARIANT_ANALYSIS_PENDING"],
                    {
                        "reference_length": len(reference),
                        "observed_length": len(observed),
                        "identity_fraction": None,
                    },
                )
            else:
                checks["sequence_identity"] = make_check(
                    "pass",
                    [],
                    {
                        "reference_length": len(reference),
                        "observed_length": len(observed),
                        "identity_fraction": 1.0,
                        **circular_match,
                    },
                )

    support_rows: dict[int, dict[str, Any]] = {}
    support_validation = semantic_validation("invalid", "construct support table v2", "support table was not validated")
    try:
        support_metrics = read_support_metrics(
            support_path,
            reference_name,
            reference,
            profile,
            alignment_semantics["support"] if alignment_semantics is not None else None,
        )
        support_rows = read_support_rows(support_path)
        if observed_trusted and observed is not None and alignment_semantics is not None:
            try:
                validate_observed_consensus_binding(
                    reference,
                    observed,
                    support_rows,
                    alignment_semantics["support"],
                )
                checks["sequence_identity"]["metrics"]["consensus_support_validation"] = semantic_validation(
                    "valid",
                    "observed consensus to BAM-derived support v1",
                )
            except ValueError as exc:
                reason = "OBSERVED_CONSENSUS_SUPPORT_CONTRADICTION"
                observed_trusted = False
                checks["sequence_identity"]["status"] = "fail"
                checks["sequence_identity"]["reason_codes"] = sorted(
                    set([*checks["sequence_identity"]["reason_codes"], reason])
                )
                checks["sequence_identity"]["metrics"]["consensus_support_validation"] = semantic_validation(
                    "invalid",
                    "observed consensus to BAM-derived support v1",
                    str(exc),
                )
                aggregate_reasons.append(reason)
        support_validation = semantic_validation("valid", "construct support table and BAM recomputation v2")
        support_reasons: list[str] = []
        if support_metrics["mixed_allele_positions"] > 0:
            support_reasons.append("MIXED_ALLELES_DETECTED")
        if support_metrics["strand_imbalanced_positions"] > 0:
            support_reasons.append("STRAND_IMBALANCE_DETECTED")
        support_status = "review" if support_reasons else "pass"
        checks["read_support"] = make_check(support_status, support_reasons, support_metrics)
        aggregate_reasons.extend(support_reasons)
        coverage_reasons: list[str] = []
        if support_metrics["coverage_fraction"] < float(profile["min_coverage_fraction"]):
            coverage_reasons.append("INSUFFICIENT_COVERAGE")
        if support_metrics["low_depth_fraction"] > float(profile["max_low_depth_fraction"]):
            coverage_reasons.append("INSUFFICIENT_DEPTH")
        coverage_status = "review" if coverage_reasons else "pass"
        checks["coverage"] = make_check(coverage_status, coverage_reasons, support_metrics)
        aggregate_reasons.extend(coverage_reasons)
    except (OSError, ValueError) as exc:
        support_metrics = {}
        reason = "MALFORMED_SUPPORT_TABLE"
        support_validation = semantic_validation("invalid", "construct support table v2", str(exc))
        checks["read_support"] = make_check("review", [reason], {"error": str(exc)})
        checks["coverage"] = make_check("review", [reason], {"error": str(exc)})
        aggregate_reasons.append(reason)

    if (
        variant_analysis_pending
        and observed is not None
        and "OBSERVED_CONSENSUS_SUPPORT_CONTRADICTION" not in aggregate_reasons
    ):
        try:
            variant_support_rows = alignment_semantics["support"] if alignment_semantics is not None else {}
            variants, variant_alignment = call_variants(reference, observed, variant_support_rows, profile)
            support_states = {variant["support_status"] for variant in variants}
            if not variants:
                sequence_status = "review"
                sequence_reasons = ["VARIANT_CALLING_UNRESOLVED"]
            elif support_states == {"supported"}:
                sequence_status = "fail"
                sequence_reasons = ["VARIANTS_DETECTED"]
            else:
                sequence_status = "review"
                sequence_reasons = ["VARIANT_SUPPORT_AMBIGUOUS"]
            prior_consensus_validation = checks.get("sequence_identity", {}).get("metrics", {}).get(
                "consensus_support_validation"
            )
            if prior_consensus_validation is not None:
                variant_alignment["consensus_support_validation"] = prior_consensus_validation
            checks["sequence_identity"] = make_check(sequence_status, sequence_reasons, variant_alignment)
            aggregate_reasons.extend(sequence_reasons)
        except (KeyError, TypeError, ValueError) as exc:
            reason = "VARIANT_CALLING_UNAVAILABLE"
            checks["sequence_identity"] = make_check("review", [reason], {"error": str(exc)})
            aggregate_reasons.append(reason)

    try:
        alignment_metrics = read_metric_table(stats_path)
        total_reads = alignment_metrics.get("total_reads", 0.0)
        unmapped_reads = alignment_metrics.get("unmapped_reads", 0.0)
        mapped_reads = alignment_metrics.get("mapped_reads", 0.0)
        if total_reads <= 0 or min(unmapped_reads, mapped_reads) < 0 or mapped_reads + unmapped_reads != total_reads:
            raise ValueError("alignment stats contain inconsistent read counts")
        if alignment_semantics is None or any(
            int(alignment_metrics[name]) != int(alignment_semantics[name])
            for name in ("total_reads", "mapped_reads", "unmapped_reads")
        ):
            raise RuntimeError("alignment stats do not match BAM-derived primary read counts")
        unmapped_fraction = unmapped_reads / total_reads
        contamination_reasons = []
        if unmapped_fraction > float(profile["max_unmapped_fraction"]):
            contamination_reasons.append("CONTAMINATION_SCREEN_FAILED")
        contamination_status = "fail" if contamination_reasons else "pass"
        checks["contamination"] = make_check(
            contamination_status,
            contamination_reasons,
            {
                **alignment_metrics,
                "unmapped_fraction": unmapped_fraction,
                "screen_basis": "expected_reference_mapping_only",
                "organism_identity_claimed": False,
            },
        )
        aggregate_reasons.extend(contamination_reasons)
    except RuntimeError as exc:
        alignment_metrics = {}
        reason = "ALIGNMENT_STATS_INCONSISTENT"
        checks["contamination"] = make_check("review", [reason], {"error": str(exc)})
        aggregate_reasons.append(reason)
    except (OSError, ValueError) as exc:
        alignment_metrics = {}
        reason = "CONTAMINATION_SCREEN_UNAVAILABLE"
        checks["contamination"] = make_check("review", [reason], {"error": str(exc)})
        aggregate_reasons.append(reason)

    if not alignment_evidence_valid:
        contamination_check = checks["contamination"]
        contamination_check["reason_codes"] = sorted(
            set([*contamination_check["reason_codes"], "ALIGNMENT_EVIDENCE_INVALID"])
        )
        contamination_check["metrics"]["alignment_bam_validation"] = alignment_bam_validation
        contamination_check["metrics"]["alignment_index_validation"] = alignment_index_validation
        if contamination_check["status"] == "pass":
            contamination_check["status"] = "review"

    topology_validation = semantic_validation("invalid", "construct topology evidence v1", "topology was not validated")
    try:
        topology = read_json(topology_path)
        if topology.get("state") == "malformed":
            raise ArithmeticError(str(topology.get("reason") or "topology evidence is malformed"))
        if topology.get("state") != "present":
            raise ValueError(str(topology.get("reason") or "topology evidence is unavailable"))
        provenance = topology.get("provenance")
        expected_topology_digests = {
            "reference_sha256": sha256_file(reference_path),
            "alignment_bam_sha256": sha256_file(alignment_bam_path) if alignment_bam_path and alignment_bam_path.is_file() else None,
            "breakpoint_call_sha256": (
                sha256_file(breakpoint_call_path)
                if breakpoint_call_path is not None and breakpoint_call_path.is_file()
                else None
            ),
            "secondary_summary_sha256": (
                sha256_file(secondary_summary_path)
                if secondary_summary_path is not None and secondary_summary_path.is_file()
                else None
            ),
        }
        if not isinstance(provenance, dict) or any(
            provenance.get(name) != digest for name, digest in expected_topology_digests.items()
        ):
            raise LookupError("topology reference/BAM provenance digests are missing or inconsistent")
        spanning = strict_nonnegative_json_integer(topology.get("origin_spanning_reads"), "origin_spanning_reads")
        anomaly_fraction = strict_finite_json_number(
            topology.get("secondary_anomaly_fraction"),
            "secondary_anomaly_fraction",
        )
        if not 0.0 <= anomaly_fraction <= 1.0:
            raise ArithmeticError("topology secondary_anomaly_fraction must be between zero and one")
        if alignment_semantics is None:
            raise LookupError("BAM semantics are unavailable for topology recomputation")
        edge = strict_nonnegative_json_integer(topology.get("edge_window_bp"), "edge_window_bp")
        if edge <= 0 or edge > max(1, len(reference) // 2):
            raise ArithmeticError("topology edge_window_bp is outside the valid reference range")
        recomputed_spanning = count_valid_origin_wraps(
            alignment_semantics["segments_by_read"],
            len(reference),
            edge,
        )
        if spanning != recomputed_spanning:
            raise LookupError("topology origin-spanning count does not match BAM-derived split/wrap evidence")
        for field, expected in (
            ("mapped_unique_reads", alignment_semantics["mapped_reads"]),
            ("alignment_records", alignment_semantics["alignment_records"]),
        ):
            actual = strict_nonnegative_json_integer(topology.get(field), field)
            if actual != expected:
                raise LookupError(f"topology {field} does not match BAM recomputation")
        non_boundary_split = strict_nonnegative_json_integer(
            topology.get("non_boundary_split_reads"),
            "non_boundary_split_reads",
        )
        aligned_dimer_reads = strict_nonnegative_json_integer(
            topology.get("aligned_dimer_reads"),
            "aligned_dimer_reads",
        )
        denominator = aligned_dimer_reads or alignment_semantics["mapped_reads"]
        if min(non_boundary_split, aligned_dimer_reads) < 0 or non_boundary_split > denominator:
            raise ValueError("topology anomaly counts are inconsistent")
        expected_anomaly = non_boundary_split / denominator if denominator else 0.0
        if not math.isclose(anomaly_fraction, expected_anomaly, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("topology anomaly fraction does not match count arithmetic")
        topology_validation = semantic_validation("valid", "construct topology evidence and BAM provenance v1")
        if topology.get("expected_topology") != profile["expected_topology"]:
            reason = "TOPOLOGY_EXPECTATION_MISMATCH"
            checks["topology"] = make_check("fail", [reason], topology)
            aggregate_reasons.append(reason)
        else:
            topology_reasons: list[str] = []
            topology_status = "pass"
            if spanning < int(profile["min_origin_spanning_reads"]):
                topology_reasons.append("TOPOLOGY_EVIDENCE_INSUFFICIENT")
                topology_status = "review"
            if topology.get("contradictory_breakpoint_evidence") is True or anomaly_fraction > float(
                profile["max_secondary_anomaly_fraction"]
            ):
                topology_reasons.append("TOPOLOGY_CONTRADICTED")
                topology_status = "fail"
            checks["topology"] = make_check(topology_status, topology_reasons, topology)
            aggregate_reasons.extend(topology_reasons)
    except ArithmeticError as exc:
        reason = "MALFORMED_NUMERIC_EVIDENCE"
        topology_validation = semantic_validation("invalid", "construct topology evidence v1", str(exc))
        checks["topology"] = make_check("review", [reason], {"error": str(exc)})
        aggregate_reasons.append(reason)
    except LookupError as exc:
        reason = "TOPOLOGY_PROVENANCE_INVALID"
        topology_validation = semantic_validation("invalid", "construct topology evidence v1", str(exc))
        checks["topology"] = make_check("review", [reason], {"error": str(exc)})
        aggregate_reasons.append(reason)
    except (OSError, TypeError, ValueError) as exc:
        reason = "MALFORMED_NUMERIC_EVIDENCE" if "non-finite" in str(exc) else "TOPOLOGY_EVIDENCE_UNAVAILABLE"
        topology_validation = semantic_validation("invalid", "construct topology evidence v1", str(exc))
        checks["topology"] = make_check("review", [reason], {"error": str(exc)})
        aggregate_reasons.append(reason)

    if reference_binding_reason is not None:
        sequence_check = checks["sequence_identity"]
        sequence_check["reason_codes"] = sorted(
            set([*sequence_check["reason_codes"], reference_binding_reason])
        )
        sequence_check["metrics"]["declared_reference_sequence_sha256"] = declared_reference_sequence_sha256
        sequence_check["metrics"]["actual_reference_sequence_sha256"] = actual_reference_sequence_sha256
        if sequence_check["status"] == "pass":
            sequence_check["status"] = "review"

    check_statuses = {check["status"] for check in checks.values()}
    if "fail" in check_statuses:
        verdict = "FAIL"
    elif "review" in check_statuses or "not_evaluated" in check_statuses:
        verdict = "REVIEW"
    else:
        verdict = "PASS"
    profile_is_qualified = (
        profile.get("calibration_status") == "calibrated"
        and profile.get("public_accuracy_validated") is True
        and profile.get("automatic_pass_eligible") is True
    )
    if verdict == "PASS" and not profile_is_qualified:
        verdict = "REVIEW"
        aggregate_reasons.append("UNCALIBRATED_PROFILE")
    reason_codes = sorted(set(aggregate_reasons)) if aggregate_reasons else ["ALL_CHECKS_PASS"]

    normalized_observed_path: Path | None = None
    if observed_trusted and observed is not None and observed_name is not None:
        trusted_observed_output = out_dir / "observed_consensus.fasta"
        shutil.copyfile(observed_path, trusted_observed_output)
        normalized_observed_path = trusted_observed_output

    per_base_output = out_dir / "per_base_metrics.tsv"
    shutil.copyfile(support_path, per_base_output)
    variants_path = out_dir / "variants.vcf"
    write_vcf(variants_path, reference_name, len(reference), variants)

    reference_validation = semantic_validation("valid", "single-record normalized FASTA v1")
    observed_validation = (
        semantic_validation("valid", "independent observed FASTA provenance v1")
        if observed_trusted
        else semantic_validation("invalid", "independent observed FASTA provenance v1", observed_reason or observed_input_state)
    )
    manifest: dict[str, Any] = {
        "artifact_schema_version": 2,
        "schema": SCHEMA_NAME,
        "execution": {"status": "SUCCEEDED", "exit_code": 0, "reason_codes": []},
        "verdict": verdict,
        "reason_codes": reason_codes,
        "threshold_profile": {
            "id": args.profile,
            "version": str(profile.get("version", "unknown")),
            "sha256": canonical_sha256(profile),
            "calibration_status": str(profile.get("calibration_status", "unknown")),
            "public_accuracy_validated": bool(profile.get("public_accuracy_validated", False)),
            "values": profile,
        },
        "inputs": {
            "reference": file_evidence(
                reference_path,
                role="expected_reference",
                source_kind="operator_supplied",
                validation=reference_validation,
                normalized_sequence_sha256=actual_reference_sequence_sha256,
                declared_sequence_sha256=declared_reference_sequence_sha256,
            ),
            "observed": file_evidence(
                observed_path if observed_path.is_file() else None,
                role="independently_observed_consensus",
                state=observed_input_state,
                source_kind=state.get("source_kind"),
                independent_from_expected=observed_trusted,
                reason=observed_reason,
                validation=observed_validation,
            ),
            "source_reads": file_evidence(
                source_reads_path,
                role="source_reads",
                state="present" if source_reads_path is not None and source_reads_path.is_file() else "missing",
                source_kind="retained_pipeline_input",
                independent_from_expected=None,
                reason=source_reads_validation.get("reason"),
                validation=source_reads_validation,
            ),
            "support": file_evidence(
                support_path,
                role="per_base_read_support",
                validation=support_validation,
            ),
            "alignment": file_evidence(
                alignment_bam_path,
                role="read_alignment_evidence",
                validation=alignment_bam_validation,
            ),
            "alignment_index": file_evidence(
                alignment_index_path,
                role="read_alignment_index",
                validation=alignment_index_validation,
            ),
            "alignment_stats": file_evidence(
                stats_path,
                role="alignment_read_count_statistics",
                validation=(
                    semantic_validation("valid", "alignment stats and BAM read-count recomputation v1")
                    if checks["contamination"]["status"] in {"pass", "fail"}
                    else semantic_validation(
                        "invalid",
                        "alignment stats and BAM read-count recomputation v1",
                        ",".join(checks["contamination"]["reason_codes"]),
                    )
                ),
            ),
            "topology": file_evidence(
                topology_path,
                role="topology_evidence",
                validation=topology_validation,
            ),
        },
        "checks": checks,
        "variants": variants,
        "summary": {
            "reference_name": reference_name,
            "reference_length": len(reference),
            "reference_topology": profile["expected_topology"],
            "observed_length": len(observed) if observed is not None else None,
            "sequence_identity_fraction": checks["sequence_identity"]["metrics"].get("identity_fraction"),
            "variant_count": len(variants),
            "coverage_fraction": support_metrics.get("coverage_fraction"),
            "unmapped_fraction": checks["contamination"]["metrics"].get("unmapped_fraction"),
        },
        "provenance": {
            "verifier": {"name": VERIFIER_NAME, "version": VERIFIER_VERSION},
            "workflow": {
                "name": "ConstructVerify",
                "module": "modules/ngs/construct_verify.nf",
                "version": "2",
            },
            "tool_versions": {
                "python": sys.version.split()[0],
                "samtools": samtools_version,
            },
            "commands": [
                {"name": "construct_verifier", "argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]},
                *alignment_commands,
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "artifacts": [],
    }

    summary_path = out_dir / "verification_summary.tsv"
    evidence_path = out_dir / "evidence.html"
    write_summary(summary_path, manifest)
    write_evidence_html(evidence_path, manifest)
    manifest["artifacts"] = [
        artifact_record(
            summary_path,
            kind="verification_summary",
            required=True,
            validation=semantic_validation("valid", "verification summary TSV v2"),
        ),
        artifact_record(
            variants_path,
            kind="normalized_variants",
            required=True,
            validation=semantic_validation("valid", "VCF 4.3 structural writer"),
        ),
        artifact_record(
            per_base_output,
            kind="per_base_metrics",
            required=True,
            validation=support_validation,
        ),
        artifact_record(
            evidence_path,
            kind="human_evidence_report",
            required=True,
            validation=semantic_validation("valid", "construct evidence HTML v2"),
        ),
        artifact_record(
            normalized_observed_path,
            kind="observed_consensus",
            required=False,
            reason=None if normalized_observed_path else "no trusted independently observed consensus was available",
            validation=observed_validation,
        ),
    ]
    manifest_path = out_dir / "qc_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERIFIER_VERSION}")
    parser.add_argument("--reference-fasta", type=Path, required=True)
    parser.add_argument(
        "--expected-reference-sha256",
        help="trusted SHA-256 of the normalized expected reference sequence",
    )
    parser.add_argument("--observed-state", type=Path, required=True)
    parser.add_argument("--observed-fasta", type=Path, required=True)
    parser.add_argument("--per-base-support", type=Path, required=True)
    parser.add_argument("--alignment-bam", type=Path)
    parser.add_argument("--alignment-index", type=Path)
    parser.add_argument("--samtools-bin", default="samtools")
    parser.add_argument("--samtools-command", action="append", default=[])
    parser.add_argument("--alignment-stats", type=Path, required=True)
    parser.add_argument("--topology-evidence", type=Path, required=True)
    parser.add_argument("--breakpoint-call", type=Path)
    parser.add_argument("--secondary-summary", type=Path)
    parser.add_argument("--profile-config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run_verification(args)
    except Exception as exc:
        print(f"construct verification failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
