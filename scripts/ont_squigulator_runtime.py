#!/usr/bin/env python3
"""Bounded one-record Squigulator ideal-signal producer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Sequence

HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 32 * 1024 * 1024
OUTPUT_NAMES = frozenset({
    "simulation_input.fasta", "simulated.blow5", "simulated.blow5.idx",
    "simulated_reads.fasta", "simulated_source.paf", "simulated_source.sam",
    "simulated_read_id_map.json", "producer_manifest.json",
})


def reverse_complement(sequence: str) -> str:
    sequence = sequence.upper()
    if not sequence or re.fullmatch(r"[ACGTN]+", sequence) is None:
        raise ValueError("simulation sequence must contain only A, C, G, T, or N")
    return sequence.translate(str.maketrans("ACGTN", "TGCAN"))[::-1]


def virtual_sequence_id(reference_digest: str, contig: str, start: int, end: int, orientation: str) -> str:
    if HEX64.fullmatch(reference_digest) is None or not 1 <= start <= end or orientation not in {"forward", "reverse"}:
        raise ValueError("simulation coordinate authority is invalid")
    payload = f"{reference_digest}\0{contig}\0{start}\0{end}\0{orientation}".encode()
    return f"bms-sim-{hashlib.sha256(payload).hexdigest()[:32]}"


def build_squigulator_argv(*, profile_id: str, seed: int) -> list[str]:
    if not re.fullmatch(r"(?:dna-r9-(?:min|prom)|rna-r9-(?:min|prom)|dna-r10-(?:min|prom)|rna004-(?:min|prom))", profile_id):
        raise ValueError("unsupported Squigulator profile")
    if isinstance(seed, bool) or not 1 <= seed <= 2_147_483_647:
        raise ValueError("seed must be between 1 and 2147483647")
    return [
        "squigulator", "-x", profile_id, "--full-contigs", "--ideal",
        "--seed", str(seed), "-t", "1", "-K", "1",
        "-q", "/output/simulated_reads.fasta",
        "-c", "/output/simulated_source.paf", "--paf-ref",
        "-a", "/output/simulated_source.sam",
        "/parents/simulation_input.fasta", "-o", "/output/simulated.blow5",
    ]


def validate_output_tree(output: Path) -> dict[str, dict[str, int | str]]:
    artifacts: dict[str, dict[str, int | str]] = {}
    total = 0
    for path in output.iterdir():
        info = path.lstat()
        if path.name not in OUTPUT_NAMES or path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unexpected producer output: {path.name}")
        if info.st_size <= 0 or info.st_size > MAX_OUTPUT_FILE_BYTES:
            raise RuntimeError(f"producer output violates file-size policy: {path.name}")
        total += info.st_size
        if total > MAX_TOTAL_OUTPUT_BYTES:
            raise RuntimeError("producer total output exceeds bounded policy")
        artifacts[path.name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": info.st_size,
        }
    return artifacts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("/output"))
    parser.add_argument("--print-argv", action="store_true")
    args = parser.parse_args(argv)
    if not args.profile_id:
        parser.print_help()
        return 0
    command = build_squigulator_argv(profile_id=args.profile_id, seed=args.seed)
    if args.print_argv:
        print(json.dumps(command, separators=(",", ":")))
        return 0
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, check=False)
    if completed.returncode != 0:
        return completed.returncode
    validate_output_tree(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
