#!/usr/bin/env python3
"""Inject exact staged target templates into Protenix input JSON.

Protenix inference consumes per-chain ``proteinChain.templatesPath`` files
containing HHR/A3M template hits. For anchored target conditioning, the generic
template search can rank homologs ahead of the exact staged target template,
causing the exact target to be excluded within Protenix's internal candidate
cap. This helper bypasses that ambiguity by writing an exact single-hit A3M for
the target chain such as ``2lgv_A`` and attaching it directly to the target
protein chain(s) before inference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_a3m(path: Path, records: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for header, sequence in records:
            handle.write(f">{header}\n{sequence}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Force exact staged target templates into Protenix input JSON")
    parser.add_argument("--input_json", required=True)
    parser.add_argument("--output_json", required=True)
    parser.add_argument("--target_sequence", required=True)
    parser.add_argument("--template_pdb_id", required=True)
    parser.add_argument("--template_chains", required=True, help="Comma-separated target chain IDs, e.g. A or A,B")
    parser.add_argument("--out_dir", required=True)
    args = parser.parse_args()

    input_path = Path(args.input_json).expanduser().resolve()
    output_path = Path(args.output_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    payload = _load_json(input_path)
    if not isinstance(payload, list):
        raise ValueError(f"Expected top-level list in {input_path}")

    target_sequence = args.target_sequence.strip()
    if not target_sequence:
        raise ValueError("target_sequence is required")

    template_pdb_id = args.template_pdb_id.strip().lower()
    template_chains = [token.strip() for token in args.template_chains.split(",") if token.strip()]
    if not template_chains:
        raise ValueError("At least one template chain ID is required")

    rewired = 0
    for task_idx, task in enumerate(payload):
        sequences = task.get("sequences", [])
        if not isinstance(sequences, list):
            continue
        for seq_idx, wrapper in enumerate(sequences):
            if not isinstance(wrapper, dict):
                continue
            chain = wrapper.get("proteinChain")
            if not isinstance(chain, dict):
                continue
            if str(chain.get("sequence", "") or "").strip() != target_sequence:
                continue

            exact_path = out_dir / f"task{task_idx:03d}_seq{seq_idx:03d}_{template_pdb_id}_exact.a3m"
            exact_records = [
                (
                    f"{template_pdb_id}_{chain_id}/1-{len(target_sequence)} mol:protein length:{len(target_sequence)} exact_target_template",
                    target_sequence,
                )
                for chain_id in template_chains
            ]
            _write_a3m(exact_path, exact_records)
            chain["templatesPath"] = str(exact_path)
            rewired += 1

    if rewired == 0:
        raise ValueError("No target protein chains matched target_sequence; nothing was rewired")

    _write_json(output_path, payload)
    print(
        f"[prepare_protenix_exact_templates] Rewired {rewired} target protein chains "
        f"to exact templates for {template_pdb_id}"
    )


if __name__ == "__main__":
    main()
