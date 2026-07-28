#!/usr/bin/env python3
"""Fail-closed local comparison-panel attribution for primary ONT reads.

This deliberately does not perform taxonomy lookup.  A panel is an immutable,
local, digest-bound snapshot and classification names only its supplied IDs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "bms.ngs.comparison-panel.v1"
SUMMARY_SCHEMA = "bms.ngs.comparison-attribution-summary.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


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


def _fastq_read_ids(path: Path) -> list[str]:
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("FASTQ is unavailable or not UTF-8 text") from exc
    if len(lines) % 4:
        raise ValueError("FASTQ must contain complete four-line records")
    ids: list[str] = []
    for offset in range(0, len(lines), 4):
        header, sequence, separator, quality = lines[offset:offset + 4]
        read_id = header[1:].split(maxsplit=1)[0] if header.startswith("@") else ""
        if not read_id or not separator.startswith("+") or not sequence or len(sequence) != len(quality):
            raise ValueError("FASTQ contains an invalid record")
        ids.append(read_id)
    if len(ids) != len(set(ids)):
        raise ValueError("FASTQ read identifiers must be unique for conservative attribution")
    return ids


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
        entry_id, label = entry.get("id"), entry.get("label")
        raw_fasta, digest = entry.get("fasta_path"), entry.get("fasta_sha256")
        if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id) or entry_id in seen:
            raise ValueError("comparison panel entry id must be a unique stable identifier")
        if not isinstance(label, str) or not label.strip() or len(label) > 256:
            raise ValueError("comparison panel entry requires a stable non-empty label")
        if not isinstance(raw_fasta, str) or not raw_fasta.strip() or Path(raw_fasta).is_absolute():
            raise ValueError("comparison panel FASTA path must be snapshot-relative")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("comparison panel FASTA requires a sha256 digest")
        fasta = (root / raw_fasta).resolve()
        try:
            fasta.relative_to(root)
        except ValueError as exc:
            raise ValueError("comparison panel FASTA path escapes snapshot directory") from exc
        if not fasta.is_file() or fasta.is_symlink() or sha256_file(fasta) != digest:
            raise ValueError("comparison panel FASTA is unavailable or digest-mismatched")
        _single_fasta_record(fasta)
        normalized.append({"id": entry_id, "label": label.strip(), "fasta_path": str(fasta), "fasta_sha256": digest})
        seen.add(entry_id)
    identity = {key: payload[key] for key in ("panel_id", "panel_version", "panel_manifest_sha256") if key in payload}
    return {"schema": SCHEMA, "snapshot_sha256": sha256_file(path), **identity, "entries": normalized}


def classify_primary_read(targets: str) -> str:
    """Map explicit accepted target IDs to the bounded public categories."""
    selected = {value for value in str(targets).split(",") if value and value != "*"}
    if not selected:
        return "unclassified"
    if len(selected) != 1:
        return "ambiguous_multimapping"
    return "expected_plasmid_unique" if "expected" in selected else "panel_reference_unique"


def _write_combined_reference(expected: Path, panel: dict[str, Any], out: Path) -> None:
    _expected_name, expected_sequence = _single_fasta_record(expected)
    chunks = [">expected_plasmid\n", expected_sequence, "\n"]
    for entry in panel["entries"]:
        _panel_name, sequence = _single_fasta_record(Path(entry["fasta_path"]))
        chunks.extend([f">panel__{entry['id']}\n", sequence, "\n"])
    out.write_text("".join(chunks), encoding="utf-8")


def _summarize_sam(samtools: str, bam: Path, threshold: int, score_margin: int, panel: dict[str, Any], fastq: Path) -> dict[str, Any]:
    accepted: dict[str, list[tuple[str, int]]] = defaultdict(list)
    read_ids = _fastq_read_ids(fastq)
    known = set(read_ids)
    panel_ids = {entry["id"] for entry in panel["entries"]}
    result = subprocess.run([samtools, "view", str(bam)], text=True, capture_output=True, check=True)
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 11:
            raise ValueError("samtools returned malformed SAM")
        name, flag, reference, mapq = fields[0], int(fields[1]), fields[2], int(fields[4])
        if name not in known:
            raise ValueError("alignment contains a read absent from the submitted FASTQ")
        # Secondary and supplementary records remain eligible competitors.  A
        # target can only be unique after all eligible target hits are counted.
        if flag & 0x4 or mapq < threshold:
            continue
        score = next((field[5:] for field in fields[11:] if field.startswith("AS:i:")), None)
        if score is None:
            continue
        try:
            alignment_score = int(score)
        except ValueError as exc:
            raise ValueError("SAM alignment score is malformed") from exc
        if reference == "expected_plasmid":
            accepted[name].append(("expected", alignment_score))
        elif reference.startswith("panel__") and reference[7:] in panel_ids:
            accepted[name].append((reference[7:], alignment_score))
    counts = {key: 0 for key in ("expected_plasmid_unique", "panel_reference_unique", "ambiguous_multimapping", "unclassified")}
    rows = []
    for read_id in read_ids:
        candidates = accepted[read_id]
        # A unique call needs a sufficient MAPQ (above) and no competing target
        # within the explicit score margin.  Multiple SAM records alone are not
        # an ambiguity call: secondary/supplementary records are competitors
        # only when their target score is close enough.
        best_by_ref: dict[str, int] = {}
        for reference, score in candidates:
            best_by_ref[reference] = max(score, best_by_ref.get(reference, score))
        ranked = sorted(best_by_ref.items(), key=lambda item: (-item[1], item[0]))
        if not ranked:
            classification = "unclassified"
        else:
            best_ref, best_score = ranked[0]
            close_competitors = [ref for ref, score in ranked[1:] if best_score - score < score_margin]
            classification = "ambiguous_multimapping" if close_competitors else classify_primary_read(best_ref)
        counts[classification] += 1
        rows.append({"read_id": read_id, "classification": classification, "accepted_references": [ref for ref, _score in ranked]})
    return {"counts": counts, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--expected-fasta", required=True, type=Path)
    parser.add_argument("--fastq", required=True, type=Path)
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
    panel = load_panel_snapshot(args.snapshot)
    _write_combined_reference(args.expected_fasta, panel, args.combined_fasta)
    evidence = _summarize_sam(args.samtools, args.panel_bam, args.min_mapq, args.min_score_margin, panel, args.fastq) if args.panel_bam else {
        "counts": {key: 0 for key in ("expected_plasmid_unique", "panel_reference_unique", "ambiguous_multimapping", "unclassified")},
        "rows": [],
    }
    artifacts = []
    if args.panel_bam:
        bai = Path(f"{args.panel_bam}.bai")
        for kind, artifact in (("comparison_panel_alignment_bam", args.panel_bam), ("comparison_panel_alignment_bai", bai)):
            if not artifact.is_file():
                raise ValueError(f"comparison artifact is missing: {artifact.name}")
            artifacts.append({"kind": kind, "path": artifact.name, "sha256": sha256_file(artifact), "size_bytes": artifact.stat().st_size})
    payload = {
        "schema": SUMMARY_SCHEMA, "status": "review_required", "min_mapq": args.min_mapq, "min_score_margin": args.min_score_margin,
        "reference": {"path": str(args.expected_fasta), "source_file_sha256": sha256_file(args.expected_fasta)},
        "panel": panel, "categories": evidence["counts"], "reads": evidence["rows"],
        "unclassified_attribution": "none",
        "artifacts": artifacts,
    }
    args.summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
