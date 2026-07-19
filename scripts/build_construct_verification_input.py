#!/usr/bin/env python3
"""Build an always-emitted, digest-bound construct-verification input bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

FORBIDDEN_METHODS = frozenset({"reference_copy_fallback", "expected_reference_copy", "cp_reference"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_fasta_sha256(path: Path) -> str:
    records = 0
    chunks: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                records += 1
                if records > 1:
                    raise ValueError("FASTA must contain exactly one record")
                continue
            if records != 1:
                raise ValueError("FASTA sequence appears before header")
            chunks.append(line.upper())
    sequence = "".join(chunks)
    if records != 1 or not sequence:
        raise ValueError("FASTA must contain one non-empty record")
    invalid = sorted(set(sequence) - set("ACGTN"))
    if invalid:
        raise ValueError(f"FASTA contains unsupported symbols: {''.join(invalid)}")
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-fasta", type=Path, required=True)
    parser.add_argument("--expected-reference-sha256", default="")
    parser.add_argument("--source-reads", type=Path, required=True)
    parser.add_argument("--consensus-fasta", type=Path, required=True)
    parser.add_argument("--consensus-method", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    output_consensus = args.out_dir / "observed_consensus.fasta"
    source_reads_suffix = ".fastq.gz" if args.source_reads.name.lower().endswith((".gz", ".bgz")) else ".fastq"
    output_source_reads = args.out_dir / f"source_reads{source_reads_suffix}"
    if output_consensus.exists():
        output_consensus.unlink()
    if output_source_reads.exists():
        output_source_reads.unlink()

    actual_reference_digest = normalized_fasta_sha256(args.reference_fasta)
    declared_reference_digest = str(args.expected_reference_sha256 or "").strip().lower()
    source_reads_present = args.source_reads.is_file()
    consensus_present = args.consensus_fasta.is_file() and args.consensus_fasta.stat().st_size > 0
    forbidden_method = args.consensus_method.strip().lower() in FORBIDDEN_METHODS

    if source_reads_present:
        shutil.copyfile(args.source_reads, output_source_reads)
    source_reads_digest = sha256_file(output_source_reads) if output_source_reads.is_file() else None

    if consensus_present:
        shutil.copyfile(args.consensus_fasta, output_consensus)
        # Validate the copied object, not only its source path.
        normalized_fasta_sha256(output_consensus)
        candidate_for_recomputation = source_reads_present and not forbidden_method
        state = {
            "schema": "biomodstack.observed_sequence_state.v1",
            "state": "present",
            "reason": None if candidate_for_recomputation else "OBSERVED_EVIDENCE_NOT_RECOMPUTABLE",
            "method": args.consensus_method,
            "source_kind": "read_derived_consensus_candidate" if candidate_for_recomputation else "untrusted_consensus",
            "independent_from_expected": False,
            "independence_assertion": "pending_verifier_recomputation",
            "observed_fasta": output_consensus.name,
            "observed_sha256": sha256_file(output_consensus),
            "source_reads_path": output_source_reads.name if output_source_reads.is_file() else None,
            "source_reads_sha256": source_reads_digest,
            "source_read_provenance": {
                "binding_method": "qname_and_sequence_against_primary_bam",
                "verification_status": "pending",
            },
            "reference_sequence_sha256_actual": actual_reference_digest,
            "reference_sequence_sha256_declared": declared_reference_digest or None,
            "reference_digest_binding": (
                "match"
                if declared_reference_digest and declared_reference_digest == actual_reference_digest
                else "mismatch"
                if declared_reference_digest
                else "unbound"
            ),
        }
    else:
        state = {
            "schema": "biomodstack.observed_sequence_state.v1",
            "state": "missing",
            "reason": "CONSENSUS_NOT_PRODUCED",
            "method": args.consensus_method,
            "source_kind": "read_derived_consensus_candidate",
            "independent_from_expected": False,
            "independence_assertion": "pending_verifier_recomputation",
            "observed_fasta": None,
            "observed_sha256": None,
            "source_reads_path": output_source_reads.name if output_source_reads.is_file() else None,
            "source_reads_sha256": source_reads_digest,
            "source_read_provenance": {
                "binding_method": "qname_and_sequence_against_primary_bam",
                "verification_status": "pending",
            },
            "reference_sequence_sha256_actual": actual_reference_digest,
            "reference_sequence_sha256_declared": declared_reference_digest or None,
            "reference_digest_binding": (
                "match"
                if declared_reference_digest and declared_reference_digest == actual_reference_digest
                else "mismatch"
                if declared_reference_digest
                else "unbound"
            ),
        }

    (args.out_dir / "observed_state.json").write_text(
        json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
