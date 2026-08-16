#!/usr/bin/env python3
"""Normalize pinned wf-clone-validation outputs without assigning a scientific PASS."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, NoReturn


ADAPTER_SCHEMA = "biomodstack.wf_clone_validation_adapter.v1"
PROVENANCE_SCHEMA = "biomodstack.wf_clone_validation_runtime_provenance.v1"


class AdapterFailure(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def fail(reason_code: str, message: str) -> NoReturn:
    raise AdapterFailure(reason_code, message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_file(root: Path, path: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError:
        fail("ARTIFACT_PATH_ESCAPE", f"artifact is outside result root: {path}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail("ARTIFACT_SYMLINK_FORBIDDEN", f"artifact path contains a symlink: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        fail("ARTIFACT_PATH_ESCAPE", f"artifact resolves outside result root: {path}")
    if not path.is_file():
        fail("REQUIRED_ARTIFACT_MISSING", f"required artifact is not a regular file: {path}")
    return path


def candidates(root: Path, pattern: str) -> list[Path]:
    return sorted((safe_file(root, path) for path in root.rglob(pattern)), key=lambda item: item.as_posix())


def exactly_one(root: Path, pattern: str, kind: str) -> Path:
    found = candidates(root, pattern)
    if not found:
        fail("REQUIRED_ARTIFACT_MISSING", f"missing required {kind} matching {pattern!r}")
    if len(found) != 1:
        fail("AMBIGUOUS_ARTIFACT", f"expected one {kind} matching {pattern!r}; found {len(found)}")
    return found[0]


def optional_one(root: Path, pattern: str, kind: str) -> Path | None:
    found = candidates(root, pattern)
    if len(found) > 1:
        fail("AMBIGUOUS_ARTIFACT", f"expected at most one {kind} matching {pattern!r}; found {len(found)}")
    return found[0] if found else None


def read_single_fasta(path: Path) -> tuple[str, str]:
    records: list[tuple[str, str]] = []
    name: str | None = None
    chunks: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        fail("MALFORMED_FASTA", f"cannot read final FASTA: {exc}")
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None:
                records.append((name, "".join(chunks)))
            name = line[1:].strip().split()[0] if line[1:].strip() else ""
            chunks = []
        elif name is None:
            fail("MALFORMED_FASTA", "final FASTA sequence appears before its header")
        else:
            chunks.append("".join(line.split()).upper())
    if name is not None:
        records.append((name, "".join(chunks)))
    if len(records) != 1 or not records[0][0] or not records[0][1]:
        fail("MALFORMED_FASTA", f"final FASTA must contain exactly one named non-empty record; found {len(records)}")
    invalid = sorted(set(records[0][1]) - set("ACGTN"))
    if invalid:
        fail("MALFORMED_FASTA", f"final FASTA contains unsupported bases: {''.join(invalid)}")
    return records[0]


def read_rows(path: Path, *, delimiter: str) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if not reader.fieldnames or any(not str(field or "").strip() for field in reader.fieldnames):
                raise ValueError("missing or blank header")
            rows = [{str(key): str(value or "").strip() for key, value in row.items() if key is not None} for row in reader]
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        fail("MALFORMED_TABULAR_OUTPUT", f"cannot parse {path.name}: {exc}")
    if len(rows) != 1:
        fail("MALFORMED_TABULAR_OUTPUT", f"{path.name} must contain exactly one sample row; found {len(rows)}")
    return rows


def artifact(root: Path, path: Path, kind: str, required: bool = True) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path.relative_to(root).as_posix(),
        "required": required,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def authoritative_input(path: Path, kind: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        fail("AUTHORITATIVE_INPUT_INVALID", f"{kind} must be a non-symlink regular file: {path}")
    resolved = path.resolve()
    return {
        "kind": kind,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def normalize(args: argparse.Namespace) -> dict[str, Any]:
    root = args.result_root.absolute()
    if not root.is_dir() or root.is_symlink():
        fail("RESULT_ROOT_INVALID", f"result root must be a non-symlink directory: {root}")
    provenance_path = args.runtime_provenance.resolve()
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("RUNTIME_PROVENANCE_INVALID", f"runtime provenance cannot be parsed: {exc}")
    if not isinstance(provenance, dict) or provenance.get("schema") != PROVENANCE_SCHEMA or provenance.get("validation_status") != "valid":
        fail("RUNTIME_PROVENANCE_INVALID", "runtime provenance does not record a valid locked runtime")
    authoritative_inputs = [
        authoritative_input(args.source_bam, "authoritative_analysis_bam"),
        authoritative_input(args.source_bai, "authoritative_analysis_bai"),
    ]

    final_fasta = exactly_one(root, "*.final.fasta", "final FASTA")
    fasta_name, sequence = read_single_fasta(final_fasta)
    sample = args.sample
    if fasta_name != sample or final_fasta.name != f"{sample}.final.fasta":
        fail("FASTA_SAMPLE_MISMATCH", f"final FASTA identity {fasta_name!r}/{final_fasta.name!r} does not match sample {sample!r}")
    final_fastq = exactly_one(root, f"{sample}.final.fastq", "final FASTQ")
    if final_fastq.stat().st_size == 0:
        fail("MALFORMED_FASTQ", "final FASTQ is empty")
    stats = exactly_one(root, f"{sample}.assembly_stats.tsv", "assembly stats")
    stats_row = read_rows(stats, delimiter="\t")[0]
    required_stats = {"read_id", "sample_name", "read_length"}
    if not required_stats <= set(stats_row):
        fail("MALFORMED_TABULAR_OUTPUT", f"assembly stats lacks columns: {sorted(required_stats - set(stats_row))}")
    try:
        stats_length = int(stats_row["read_length"])
    except ValueError:
        fail("MALFORMED_TABULAR_OUTPUT", "assembly stats read_length must be an integer")
    if stats_row["read_id"] != sample or stats_row["sample_name"] != sample or stats_length != len(sequence):
        fail("STATS_EVIDENCE_CONTRADICTION", "assembly stats sample or length contradicts final FASTA")

    bam = exactly_one(root, f"{sample}.bam", "assembly-aligned BAM")
    bai = exactly_one(root, f"{sample}.bam.bai", "assembly-aligned BAM index")
    status_path = exactly_one(root, "sample_status.txt", "sample status")
    status_row = read_rows(status_path, delimiter=",")[0]
    status_key = "Assembly completed / failed reason"
    if not {"Sample", status_key, "Length"} <= set(status_row):
        fail("MALFORMED_TABULAR_OUTPUT", "sample status lacks required columns")
    try:
        status_length = int(status_row["Length"])
    except ValueError:
        fail("MALFORMED_TABULAR_OUTPUT", "sample status Length must be an integer")
    status_text = status_row[status_key]
    completed = status_text == "Completed successfully"
    if status_row["Sample"] != sample or status_length != len(sequence) or completed != (args.execution_exit_code == 0):
        fail("STATUS_EVIDENCE_CONTRADICTION", "sample status identity, length, or completion contradicts execution/final FASTA")
    report = exactly_one(root, "wf-clone-validation-report.html", "upstream report")
    plannotate_json = exactly_one(root, "plannotate.json", "plannotate JSON")
    try:
        json.loads(plannotate_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail("MALFORMED_PLANNOTATE_JSON", f"plannotate JSON cannot be parsed: {exc}")

    artifacts = [
        artifact(root, final_fasta, "final_fasta"),
        artifact(root, final_fastq, "final_fastq"),
        artifact(root, stats, "assembly_stats"),
        artifact(root, bam, "bam"),
        artifact(root, bai, "bai"),
        artifact(root, status_path, "sample_status"),
        artifact(root, report, "upstream_report"),
        artifact(root, plannotate_json, "plannotate_json"),
    ]
    if args.full_reference_provided:
        bcf = exactly_one(root, f"{sample}.full_construct.calls.bcf", "full-reference BCF")
        csi = exactly_one(root, f"{sample}.full_construct.calls.bcf.csi", "full-reference BCF index")
        full_reference_stats = exactly_one(root, f"{sample}.full_construct.stats", "full-reference variant stats")
        if full_reference_stats.stat().st_size == 0:
            fail("MALFORMED_TABULAR_OUTPUT", "full-reference variant stats are empty")
        header = bcf.read_bytes()[:5]
        if header[:2] == b"\x1f\x8b":
            try:
                with gzip.open(bcf, "rb") as handle:
                    header = handle.read(5)
            except OSError as exc:
                fail("MALFORMED_BCF", f"full-reference BCF compression is malformed: {exc}")
        if header != b"BCF\x02\x02":
            fail("MALFORMED_BCF", "full-reference BCF does not have a BCF 2.2 header")
        artifacts.extend((
            artifact(root, bcf, "full_reference_bcf"),
            artifact(root, csi, "full_reference_csi"),
            artifact(root, full_reference_stats, "full_reference_stats"),
        ))
    elif (
        candidates(root, "*.full_construct.calls.bcf")
        or candidates(root, "*.full_construct.calls.bcf.csi")
        or candidates(root, "*.full_construct.stats")
    ):
        fail("UNEXPECTED_REFERENCE_EVIDENCE", "full-reference evidence exists although no full reference was declared")

    optional_specs = (
        ("plannotate_report.json", "plannotate_report_json"),
        (f"{sample}.annotations.bed", "plannotate_bed"),
        (f"{sample}.annotations.gbk", "plannotate_genbank"),
        ("feature_table.txt", "plannotate_feature_table"),
    )
    for pattern, kind in optional_specs:
        path = optional_one(root, pattern, kind)
        if path is not None:
            if kind in {"plannotate_json", "plannotate_report_json"}:
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    fail("MALFORMED_PLANNOTATE_JSON", f"plannotate JSON cannot be parsed: {exc}")
            artifacts.append(artifact(root, path, kind, required=False))

    inventoried_paths = {item["path"] for item in artifacts}
    for output_path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if output_path.is_dir() and not output_path.is_symlink():
            continue
        safe_file(root, output_path)
        relative_output = output_path.relative_to(root).as_posix()
        if relative_output not in inventoried_paths:
            artifacts.append(artifact(root, output_path, "supporting_upstream_output", required=False))
            inventoried_paths.add(relative_output)

    runtime_record = {
        "kind": "runtime_provenance",
        "path": str(provenance_path),
        "required": True,
        "size_bytes": provenance_path.stat().st_size,
        "sha256": sha256_file(provenance_path),
    }
    artifacts.append(runtime_record)
    artifacts.sort(key=lambda item: (item["kind"], item["path"]))
    return {
        "schema": ADAPTER_SCHEMA,
        "adapter_version": "1.0.0",
        "execution": {
            "status": "SUCCEEDED" if args.execution_exit_code == 0 else "FAILED",
            "exit_code": args.execution_exit_code,
            "reason_codes": [] if args.execution_exit_code == 0 else ["UPSTREAM_EXECUTION_FAILED"],
        },
        "upstream_sample_status": {"status": "completed" if completed else "failed", "message": status_text, "length_bp": status_length},
        "scientific_verdict": "REVIEW",
        "scientific_reason_codes": ["CANONICAL_PHASE2_VERIFICATION_REQUIRED"],
        "sample": {"id": sample, "final_fasta_length_bp": len(sequence)},
        "runtime_provenance": provenance,
        "authoritative_inputs": authoritative_inputs,
        "artifacts": artifacts,
        "missing_evidence_reasons": [],
        "contradictory_evidence_reasons": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--runtime-provenance", required=True, type=Path)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--execution-exit-code", required=True, type=int)
    parser.add_argument("--full-reference-provided", action="store_true")
    parser.add_argument("--source-bam", required=True, type=Path)
    parser.add_argument("--source-bai", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = normalize(args)
    except AdapterFailure as exc:
        print(json.dumps({"schema": ADAPTER_SCHEMA, "validation_status": "invalid", "reason_code": exc.reason_code, "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
