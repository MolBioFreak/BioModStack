#!/usr/bin/env python3
"""Build RFdiffusion3 input JSON for constrained local protein redesign."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _normalize_contig(contig: str) -> str:
    text = str(contig or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text.strip()
    if not text:
        return text

    # Foundry's dialect-2 contig parser expects comma-delimited tokens. The
    # PLR workflow resolves legacy RFdiffusion-style slash-delimited spans.
    tokens = [token.strip() for token in re.split(r"[\/\s]+", text) if token.strip()]
    normalized_tokens = ["/0" if token == "0" else token for token in tokens]
    return ",".join(normalized_tokens)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare RFD3 JSON input for protein-local redesign")
    parser.add_argument("--seed-pdb", required=True, help="Design-chain-only seed PDB")
    parser.add_argument("--manifest", required=True, help="Region manifest JSON")
    parser.add_argument("--num-designs", type=int, default=1, help="Number of redesigns to request")
    parser.add_argument("--design-startnum", type=int, default=0, help="Starting design index")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    seed_pdb = Path(args.seed_pdb).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    design_key = f"protein_local_redesign_{args.design_startnum}"
    spec = {
        "dialect": 2,
        "contig": _normalize_contig(manifest.get("contig_spec", "")),
        "input": str(seed_pdb),
    }

    output_data = {design_key: spec}
    output_path = Path(args.output)
    output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

    print(f"Created RFD3 input JSON at {output_path}")
    print(json.dumps(output_data, indent=2))


if __name__ == "__main__":
    main()
