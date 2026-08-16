#!/usr/bin/env python3
"""Build fail-closed comparison-panel attribution evidence for ONT reads.

The panel is an immutable, local, digest-bound snapshot.  FASTQ QNAMEs are
not assumed to be unique: each input occurrence gets a deterministic synthetic
QNAME before alignment, and the occurrence map binds that QNAME back to the
original QNAME and its 1-based input ordinal.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "bms.ngs.comparison-panel.v1"
SUMMARY_SCHEMA = "bms.ngs.comparison-attribution-summary.v1"
OCCURRENCE_MAP_SCHEMA = "bms.ngs.comparison-panel-occurrence-map.v1"
EXPECTED_REFERENCE_ID = "expected_plasmid"
OCCURRENCE_PREFIX = "bms_occurrence_"
CATEGORY_KEYS = (
    "expected_plasmid_unique",
    "panel_reference_unique",
    "ambiguous_multimapping",
    "unclassified",
)
ROLE_KEYS = ("intended", "host", "plasmid_decoy", "ambiguous", "unclassified")
ARTIFACT_KINDS = (
    "comparison_panel_alignment_bam",
    "comparison_panel_alignment_bai",
    "comparison_panel_occurrence_map",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
VALID_ROLES = frozenset({"host", "plasmid_decoy"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _single_fasta_record(path: Path) -> tuple[str, str]:
    """Parse one non-empty DNA FASTA record; concatenation is never implicit."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("FASTA is unavailable or not UTF-8 text") from exc
    records: list[tuple[str, str]] = []
    header: str | None = None
    bases: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(bases)))
            header, bases = line[1:].strip(), []
            if not header:
                raise ValueError("FASTA record header is empty")
        elif header is None:
            raise ValueError("FASTA sequence appears before its header")
        else:
            bases.append(line.upper())
    if header is not None:
        records.append((header, "".join(bases)))
    if len(records) != 1:
        raise ValueError("FASTA must contain exactly one record")
    name, sequence = records[0]
    if not sequence or not re.fullmatch(r"[ACGTRYSWKMBDHVN]+", sequence):
        raise ValueError("FASTA record has an empty or invalid nucleotide sequence")
    return name, sequence


def _read_fastq_records(path: Path) -> list[dict[str, str]]:
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("FASTQ is unavailable or not UTF-8 text") from exc
    if len(lines) % 4:
        raise ValueError("FASTQ must contain complete four-line records")

    records: list[dict[str, str]] = []
    for offset in range(0, len(lines), 4):
        header, sequence, separator, quality = lines[offset:offset + 4]
        read_id = header[1:].split(maxsplit=1)[0] if header.startswith("@") else ""
        if (
            not read_id
            or not separator.startswith("+")
            or not sequence
            or not quality
            or any(character.isspace() for character in sequence)
            or any(character.isspace() for character in quality)
            or len(sequence) != len(quality)
        ):
            raise ValueError("FASTQ contains an invalid record")
        records.append({"read_id": read_id, "sequence": sequence, "quality": quality})
    return records


def _fastq_read_ids(path: Path) -> list[str]:
    """Return original QNAMEs without imposing uniqueness."""
    return [record["read_id"] for record in _read_fastq_records(path)]


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _occurrence_id(ordinal: int) -> str:
    if ordinal < 1:
        raise ValueError("FASTQ occurrence ordinal must be positive")
    return f"{OCCURRENCE_PREFIX}{ordinal:012d}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    if path.is_symlink():
        raise ValueError(f"comparison output must not be a symlink: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _copy_exact(source: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise ValueError(f"comparison output must not be a symlink: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)


def prepare_occurrences(source_fastq: Path, normalized_fastq: Path, occurrence_map: Path) -> dict[str, Any]:
    """Write deterministic normalized reads and their source-bound map."""
    records = _read_fastq_records(source_fastq)
    source_sha256 = sha256_file(source_fastq)
    normalized_fastq.parent.mkdir(parents=True, exist_ok=True)
    if normalized_fastq.is_symlink():
        raise ValueError(f"normalized FASTQ must not be a symlink: {normalized_fastq.name}")

    occurrences: list[dict[str, Any]] = []
    with normalized_fastq.open("w", encoding="utf-8", newline="\n") as handle:
        for ordinal, record in enumerate(records, start=1):
            occurrence_id = _occurrence_id(ordinal)
            handle.write(f"@{occurrence_id}\n{record['sequence']}\n+\n{record['quality']}\n")
            occurrences.append({
                "occurrence_id": occurrence_id,
                "read_id": record["read_id"],
                "ordinal": ordinal,
            })

    normalized_sha256 = sha256_file(normalized_fastq)
    map_payload = {
        "schema": OCCURRENCE_MAP_SCHEMA,
        "source_fastq_sha256": source_sha256,
        "source_fastq_size_bytes": source_fastq.stat().st_size,
        "normalized_fastq_sha256": normalized_sha256,
        "input_read_count": len(occurrences),
        "occurrences": occurrences,
    }
    _write_json(occurrence_map, map_payload)
    return {
        "source_fastq_sha256": source_sha256,
        "source_fastq_size_bytes": source_fastq.stat().st_size,
        "normalized_fastq_sha256": normalized_sha256,
        "normalized_fastq_size_bytes": normalized_fastq.stat().st_size,
        "occurrence_map_sha256": sha256_file(occurrence_map),
        "occurrence_map_size_bytes": occurrence_map.stat().st_size,
        "input_read_count": len(occurrences),
        "occurrences": occurrences,
    }


def _load_occurrence_map(
    path: Path,
    *,
    source_fastq_sha256: str,
    normalized_fastq_sha256: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("occurrence map is unavailable or malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != OCCURRENCE_MAP_SCHEMA:
        raise ValueError("occurrence map has an unsupported schema")
    if payload.get("source_fastq_sha256") != source_fastq_sha256:
        raise ValueError("occurrence map is not bound to the source FASTQ")
    if payload.get("normalized_fastq_sha256") != normalized_fastq_sha256:
        raise ValueError("occurrence map is not bound to the normalized FASTQ")
    occurrences = payload.get("occurrences")
    count = payload.get("input_read_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError("occurrence map input count is malformed")
    if not isinstance(occurrences, list) or len(occurrences) != count:
        raise ValueError("occurrence map count mismatch")

    by_occurrence: dict[str, dict[str, Any]] = {}
    by_ordinal: dict[int, dict[str, Any]] = {}
    for expected_ordinal, raw in enumerate(occurrences, start=1):
        if not isinstance(raw, dict) or set(raw) != {"occurrence_id", "read_id", "ordinal"}:
            raise ValueError("occurrence map row is malformed")
        occurrence_id = raw.get("occurrence_id")
        original_read_id = raw.get("read_id")
        ordinal = raw.get("ordinal")
        if (
            not isinstance(occurrence_id, str)
            or not occurrence_id
            or not isinstance(original_read_id, str)
            or not original_read_id
            or not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal != expected_ordinal
            or occurrence_id != _occurrence_id(ordinal)
            or occurrence_id in by_occurrence
            or ordinal in by_ordinal
        ):
            raise ValueError("occurrence map rows must have unique 1-based deterministic ordinals")
        by_occurrence[occurrence_id] = raw
        by_ordinal[ordinal] = raw
    return {
        "schema": OCCURRENCE_MAP_SCHEMA,
        "source_fastq_sha256": source_fastq_sha256,
        "normalized_fastq_sha256": normalized_fastq_sha256,
        "input_read_count": count,
        "occurrences": occurrences,
        "by_occurrence": by_occurrence,
        "by_ordinal": by_ordinal,
    }


def load_panel_snapshot(path: Path) -> dict[str, Any]:
    """Validate a self-contained snapshot; paths may never escape its directory."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("comparison panel snapshot is unavailable or malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("comparison panel snapshot has an unsupported schema")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("comparison panel snapshot requires non-empty entries")
    normalized: list[dict[str, str]] = []
    root = path.parent.resolve()
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("comparison panel entry must be an object")
        entry_id, role, label = entry.get("id"), entry.get("role"), entry.get("label")
        raw_fasta, digest = entry.get("fasta_path"), entry.get("fasta_sha256")
        if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id) or entry_id == EXPECTED_REFERENCE_ID or entry_id in seen:
            raise ValueError("comparison panel entry id must be a unique stable identifier")
        if not isinstance(role, str) or role not in VALID_ROLES:
            raise ValueError("comparison panel entry role must be host or plasmid_decoy")
        if not isinstance(label, str) or not label.strip() or len(label) > 256:
            raise ValueError("comparison panel entry requires a stable non-empty label")
        if not isinstance(raw_fasta, str) or not raw_fasta.strip() or Path(raw_fasta).is_absolute():
            raise ValueError("comparison panel FASTA path must be snapshot-relative")
        if not _is_sha256(digest):
            raise ValueError("comparison panel FASTA requires a sha256 digest")
        fasta = (root / raw_fasta).resolve()
        try:
            fasta.relative_to(root)
        except ValueError as exc:
            raise ValueError("comparison panel FASTA path escapes snapshot directory") from exc
        if not fasta.is_file() or fasta.is_symlink() or sha256_file(fasta) != digest:
            raise ValueError("comparison panel FASTA is unavailable or digest-mismatched")
        _single_fasta_record(fasta)
        normalized.append({
            "id": entry_id,
            "role": role,
            "label": label.strip(),
            "fasta_path": str(fasta),
            "fasta_sha256": digest,
        })
        seen.add(entry_id)
    identity = {key: payload[key] for key in ("panel_id", "panel_version", "panel_manifest_sha256") if key in payload}
    return {"schema": SCHEMA, "snapshot_sha256": sha256_file(path), **identity, "entries": normalized}


def classify_primary_read(targets: str | Iterable[str]) -> str:
    """Map explicit accepted target IDs to the bounded public categories."""
    if isinstance(targets, str):
        selected = {value for value in targets.split(",") if value and value != "*"}
    else:
        selected = {str(value) for value in targets if str(value) and str(value) != "*"}
    if not selected:
        return "unclassified"
    if len(selected) != 1:
        return "ambiguous_multimapping"
    return "expected_plasmid_unique" if selected == {EXPECTED_REFERENCE_ID} or selected == {"expected"} else "panel_reference_unique"


def _write_combined_reference(expected: Path, panel: dict[str, Any], out: Path) -> None:
    _expected_name, expected_sequence = _single_fasta_record(expected)
    chunks = [f">{EXPECTED_REFERENCE_ID}\n", expected_sequence, "\n"]
    for entry in panel["entries"]:
        _panel_name, sequence = _single_fasta_record(Path(entry["fasta_path"]))
        chunks.extend([f">panel__{entry['id']}\n", sequence, "\n"])
    if out.is_symlink():
        raise ValueError(f"combined reference must not be a symlink: {out.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(chunks), encoding="utf-8")


def _panel_summary(panel: dict[str, Any]) -> dict[str, Any]:
    """Keep receipt identity and entry digests, never publish staging paths."""
    return {
        key: panel[key]
        for key in ("schema", "snapshot_sha256", "panel_id", "panel_version", "panel_manifest_sha256")
        if key in panel
    } | {
        "entries": [
            {key: entry[key] for key in ("id", "role", "label", "fasta_sha256")}
            for entry in panel["entries"]
        ]
    }


def _reference_descriptor(path: Path, root: Path, *, source_sha256: str) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("reference artifact must be below the comparison output root") from exc
    return {
        "id": EXPECTED_REFERENCE_ID,
        "role": "intended",
        "path": relative,
        "source_file_sha256": source_sha256,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _artifact_descriptor(kind: str, path: Path, root: Path) -> dict[str, Any]:
    if kind not in ARTIFACT_KINDS:
        raise ValueError("comparison artifact kind is unsupported")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"comparison artifact is missing or unsafe: {path.name}")
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("comparison artifact path escapes the comparison output root") from exc
    return {"kind": kind, "path": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _summarize_sam(
    samtools: str,
    bam: Path,
    threshold: int,
    score_margin: int,
    panel: dict[str, Any],
    normalized_fastq: Path,
    occurrence_map: Path | None = None,
    *,
    source_fastq_sha256: str | None = None,
) -> dict[str, Any]:
    """Classify every mapped occurrence exactly once, retaining competitors."""
    if occurrence_map is None:
        raise ValueError("comparison attribution requires an occurrence map")
    if source_fastq_sha256 is None:
        raise ValueError("comparison attribution requires the source FASTQ digest")
    normalized_sha256 = sha256_file(normalized_fastq)
    occurrence = _load_occurrence_map(
        occurrence_map,
        source_fastq_sha256=source_fastq_sha256,
        normalized_fastq_sha256=normalized_sha256,
    )
    panel_entries = panel.get("entries")
    if not isinstance(panel_entries, list) or any(
        not isinstance(entry, dict) or entry.get("role") not in VALID_ROLES for entry in panel_entries
    ):
        raise ValueError("comparison panel roles must be exactly host or plasmid_decoy")
    panel_ids = {entry["id"] for entry in panel_entries}
    reference_roles = {EXPECTED_REFERENCE_ID: "intended"} | {entry["id"]: entry["role"] for entry in panel_entries}
    known_occurrences = set(occurrence["by_occurrence"])
    accepted: dict[str, list[tuple[str, int]]] = defaultdict(list)
    primary_counts: dict[str, int] = defaultdict(int)
    result = subprocess.run([samtools, "view", str(bam)], text=True, capture_output=True, check=True)
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) < 11:
            raise ValueError(f"samtools returned malformed SAM at line {line_number}")
        name = fields[0]
        try:
            flag = int(fields[1])
            reference = fields[2]
            position = int(fields[3])
            mapq = int(fields[4])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"samtools returned malformed SAM at line {line_number}") from exc
        if not name or name not in known_occurrences or flag < 0 or flag > 0xFFFF or mapq < 0 or mapq > 255 or position < 0:
            raise ValueError("alignment contains a malformed or absent primary occurrence")
        sequence, quality = fields[9], fields[10]
        if (sequence == "*") != (quality == "*") or (sequence != "*" and len(sequence) != len(quality)):
            raise ValueError("samtools returned malformed SAM sequence or quality fields")

        is_unmapped = bool(flag & 0x4)
        is_secondary_or_supplementary = bool(flag & (0x100 | 0x800))
        if not is_secondary_or_supplementary:
            primary_counts[name] += 1

        if is_unmapped:
            if reference != "*":
                raise ValueError("unmapped SAM record names an unknown reference")
            continue
        if reference == "*":
            raise ValueError("alignment contains an unknown comparison reference")
        if reference == EXPECTED_REFERENCE_ID:
            target_reference = EXPECTED_REFERENCE_ID
        elif reference.startswith("panel__") and reference[7:] in panel_ids:
            target_reference = reference[7:]
        else:
            raise ValueError("alignment contains an unknown comparison reference")

        scores = [field[5:] for field in fields[11:] if field.startswith("AS:i:")]
        if len(scores) != 1 or not scores[0]:
            raise ValueError("mapped SAM alignment is missing a unique AS tag")
        try:
            alignment_score = int(scores[0])
        except ValueError as exc:
            raise ValueError("SAM alignment score is malformed") from exc
        if mapq < threshold:
            continue
        accepted[name].append((target_reference, alignment_score))

    if set(primary_counts) != known_occurrences or any(primary_counts[name] != 1 for name in known_occurrences):
        raise ValueError("each normalized read must have exactly one primary occurrence")

    categories = {key: 0 for key in CATEGORY_KEYS}
    role_counts = {key: 0 for key in ROLE_KEYS}
    reference_counts = {key: 0 for key in reference_roles}
    rows: list[dict[str, Any]] = []
    for raw in occurrence["occurrences"]:
        occurrence_id = raw["occurrence_id"]
        candidates = accepted[occurrence_id]
        best_by_ref: dict[str, int] = {}
        for reference, score in candidates:
            best_by_ref[reference] = max(score, best_by_ref.get(reference, score))
        ranked = sorted(best_by_ref.items(), key=lambda item: (-item[1], item[0]))
        accepted_references = [reference for reference, _score in ranked]
        for reference in accepted_references:
            reference_counts[reference] += 1
        if not ranked:
            category = "unclassified"
            role = "unclassified"
        else:
            best_reference, best_score = ranked[0]
            close_competitors = [
                reference for reference, score in ranked[1:]
                if best_score - score < score_margin
            ]
            category = "ambiguous_multimapping" if close_competitors else classify_primary_read([best_reference])
            role = "ambiguous" if category == "ambiguous_multimapping" else reference_roles[best_reference]
        categories[category] += 1
        role_counts[role] += 1
        rows.append({
            "read_id": raw["read_id"],
            "ordinal": raw["ordinal"],
            "occurrence_id": occurrence_id,
            "accepted_references": accepted_references,
            "category": category,
            "role": role,
        })

    input_count = occurrence["input_read_count"]
    if len(rows) != input_count or sum(categories.values()) != input_count or sum(role_counts.values()) != input_count:
        raise ValueError("comparison attribution count closure failed")
    return {
        "input_read_count": input_count,
        "classified_read_count": len(rows),
        "categories": categories,
        "role_counts": role_counts,
        "reference_counts": reference_counts,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--expected-fasta", required=True, type=Path)
    parser.add_argument("--fastq", required=True, type=Path)
    parser.add_argument("--normalized-fastq", type=Path)
    parser.add_argument("--occurrence-map", type=Path)
    parser.add_argument("--source-fastq-artifact", type=Path)
    parser.add_argument("--expected-reference-artifact", type=Path)
    parser.add_argument("--combined-fasta", required=True, type=Path)
    parser.add_argument("--panel-bam", type=Path)
    parser.add_argument("--samtools", default="samtools")
    parser.add_argument("--min-mapq", type=int, default=20)
    parser.add_argument("--min-score-margin", type=int, default=10)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    if not 0 <= args.min_mapq <= 60:
        raise ValueError("comparison panel min MAPQ must be between 0 and 60")
    if args.min_score_margin < 0:
        raise ValueError("comparison panel score margin must be non-negative")

    root = args.summary.parent.resolve()
    normalized_fastq = args.normalized_fastq or root / "comparison_panel_normalized.fastq"
    occurrence_map = args.occurrence_map or root / "comparison_panel_occurrence_map.json"
    source_artifact = args.source_fastq_artifact or root / "comparison_panel_source.fastq"
    expected_artifact = args.expected_reference_artifact or root / "comparison_panel_expected_reference.fasta"
    for output in (args.summary, normalized_fastq, occurrence_map, source_artifact, expected_artifact):
        if output.is_symlink():
            raise ValueError(f"comparison output must not be a symlink: {output.name}")

    panel = load_panel_snapshot(args.snapshot)
    source_metadata = prepare_occurrences(args.fastq, normalized_fastq, occurrence_map)
    _copy_exact(args.fastq, source_artifact)
    _copy_exact(args.expected_fasta, expected_artifact)
    _write_combined_reference(args.expected_fasta, panel, args.combined_fasta)

    if args.panel_bam:
        evidence = _summarize_sam(
            args.samtools,
            args.panel_bam,
            args.min_mapq,
            args.min_score_margin,
            panel,
            normalized_fastq,
            occurrence_map,
            source_fastq_sha256=source_metadata["source_fastq_sha256"],
        )
    else:
        evidence = {
            "input_read_count": source_metadata["input_read_count"],
            "classified_read_count": 0,
            "categories": {key: 0 for key in CATEGORY_KEYS},
            "role_counts": {key: 0 for key in ROLE_KEYS},
            "reference_counts": {EXPECTED_REFERENCE_ID: 0} | {entry["id"]: 0 for entry in panel["entries"]},
            "rows": [],
        }

    artifacts: list[dict[str, Any]] = [
        _artifact_descriptor("comparison_panel_occurrence_map", occurrence_map, root),
    ]
    if args.panel_bam:
        bai = Path(f"{args.panel_bam}.bai")
        artifacts = [
            _artifact_descriptor("comparison_panel_alignment_bam", args.panel_bam, root),
            _artifact_descriptor("comparison_panel_alignment_bai", bai, root),
            artifacts[0],
        ]

    source_path = source_artifact.resolve().relative_to(root).as_posix()
    normalized_path = normalized_fastq.resolve().relative_to(root).as_posix()
    map_path = occurrence_map.resolve().relative_to(root).as_posix()
    payload = {
        "schema": SUMMARY_SCHEMA,
        "status": "review_required" if args.panel_bam else "preparation_only",
        "min_mapq": args.min_mapq,
        "min_score_margin": args.min_score_margin,
        "reference": _reference_descriptor(
            expected_artifact,
            root,
            source_sha256=sha256_file(args.expected_fasta),
        ),
        "panel": _panel_summary(panel),
        "source_fastq": {
            "path": source_path,
            "sha256": source_metadata["source_fastq_sha256"],
            "size_bytes": source_metadata["source_fastq_size_bytes"],
        },
        "source_fastq_sha256": source_metadata["source_fastq_sha256"],
        "normalized_fastq": {
            "path": normalized_path,
            "sha256": source_metadata["normalized_fastq_sha256"],
            "size_bytes": source_metadata["normalized_fastq_size_bytes"],
        },
        "occurrence_map": {
            "path": map_path,
            "sha256": source_metadata["occurrence_map_sha256"],
            "size_bytes": source_metadata["occurrence_map_size_bytes"],
        },
        "occurrence_map_sha256": source_metadata["occurrence_map_sha256"],
        "input_read_count": evidence["input_read_count"],
        "classified_read_count": evidence["classified_read_count"],
        "category_closure": list(CATEGORY_KEYS),
        "categories": evidence["categories"],
        "role_counts": evidence["role_counts"],
        "reference_counts": evidence["reference_counts"],
        "reads": evidence["rows"],
        "unclassified_attribution": "none",
        "artifacts": artifacts,
    }
    _write_json(args.summary, payload)


if __name__ == "__main__":
    main()
