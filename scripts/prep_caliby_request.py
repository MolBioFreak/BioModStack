#!/usr/bin/env python3
"""Snapshot an API-normalized standalone request as a portable input tree."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path


def prepare_request(request: dict, output_dir: Path, *, authorize_source=None) -> dict:
    if request.get("schema_version") != 1 or request.get("task") not in {"ensemble_design", "sidechain_pack"}:
        raise ValueError("Expected a normalized standalone Caliby v1 request; legacy design is not reinterpreted")
    output_dir.mkdir(parents=True, exist_ok=True)
    effective = copy.deepcopy(request)
    states = ([s for e in effective["ensembles"] for s in e["states"]]
              if effective["task"] == "ensemble_design" else effective["structures"])
    sources = []
    for index, state in enumerate(states):
        source = Path(state["path"]).expanduser()
        if authorize_source is not None:
            authorize_source(source)
        source = source.resolve(strict=True)
        suffix = source.suffix.lower()
        if suffix not in {".pdb", ".cif", ".mmcif"}:
            raise ValueError("Caliby inputs must be PDB or mmCIF documents")
        relative = Path("structures") / f"state_{index:06d}{suffix}"
        target = output_dir / relative
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        sources.append({"state_id": state["state_id"], "source_path": state["path"],
                        "path": relative.as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "native_example_id": target.stem})
        state["path"] = relative.as_posix()
    document = {"requested": request, "effective": effective, "sources": sources}
    (output_dir / "request.json").write_text(json.dumps(document, indent=2))
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    prepare_request(json.loads(Path(args.request).read_text()), Path(args.output_dir))


if __name__ == "__main__":
    main()
