#!/usr/bin/env python3
"""Thin native ProtonPottsMPNN pH-redesign adapter (no replacement science)."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import sys

CONTRACT = "protonpottsmpnn_design.v1"
SOURCE_REVISION = "09682abfa7d20e0abcdeea0490b7a4b1c190aee3"
CHECKPOINT_RELATIVE = "checkpoints/potts_v6_afdb_edge_his0.3_acid0.06/epoch-0125.ckpt"
ENGINE_KEYS = {"field_source", "etab_source", "etab_hidden", "field_hidden"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_structure(path: Path):
    if path.suffix.lower() in {".cif", ".mmcif"}:
        from biotite.structure.io.pdbx import CIFFile, get_structure
        return get_structure(CIFFile.read(path), model=1, use_author_fields=True)
    from biotite.structure.io.pdb import PDBFile
    return PDBFile.read(path).get_structure(model=1)


def design_identifier(source_sha: str, criteria_index: int, native_id: str) -> str:
    digest = hashlib.sha256(f"{source_sha}\0{criteria_index}\0{native_id}".encode()).hexdigest()
    return f"pph_c{criteria_index}_{digest[:24]}"


def run(request: dict, input_path: Path, output: Path, native_root: Path,
        n_jobs: int = 1, device: str | None = None) -> dict:
    from mpnn.inference_engines.potts_mpnn_ph import PHDesignCriteria, PottsMPNNPHEngine
    import torch

    if request["contract"] != CONTRACT:
        raise ValueError(f"Unsupported contract: {request['contract']}")
    options = request["options"]
    engine_options = dict(options.get("engine_options", {}))
    # Profile-fixed vocabulary remains visible; never silently substitute another.
    vocab = engine_options.pop("extended_vocab", "v6")
    if vocab != "v6":
        raise ValueError("The shipped checkpoint profile uses extended_vocab='v6'")
    unknown = set(engine_options) - ENGINE_KEYS
    if unknown:
        raise TypeError(f"Unknown native engine options: {sorted(unknown)}")
    criteria = [PHDesignCriteria(**item) for item in options["criteria"]]
    result_dir = output / "protonpottsmpnn_design"
    result_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = native_root / CHECKPOINT_RELATIVE
    # run_ph_redesign returns sequences/energies, not structures. The inherited
    # flags are passed unchanged; they do not cause this native method to export.
    engine = PottsMPNNPHEngine(
        checkpoint_path=str(checkpoint), extended_vocab=vocab,
        out_directory=str(result_dir),
        write_fasta=options.get("write_fasta", True),
        write_structures=options.get("write_structures", False),
        device=device, **engine_options,
    )
    native_results = engine.run_ph_redesign(
        atom_array=load_structure(input_path), binder_chain=options["binder_chain"],
        criteria_list=criteria, seed=options.get("seed", 0),
        initial_sequences=options.get("initial_sequences"), n_jobs=n_jobs,
    )
    designs = []
    for design in native_results:
        ci = design.criteria_index
        designs.append({
            "design_id": design_identifier(request["source"]["sha256"], ci, design.design_id()),
            "criteria_index": ci, "native_design_id": design.design_id(),
            "native": dataclasses.asdict(design),
        })
    if options.get("write_fasta", True):
        with (result_dir / "designs.fasta").open("w") as stream:
            for row in designs:
                stream.write(f">{row['design_id']}\n{row['native']['canonical_sequence']}\n")
    if options.get("write_states_fasta", True):
        with (result_dir / "designs_states.fasta").open("w") as stream:
            for row in designs:
                stream.write(f">{row['design_id']}\n{' '.join(row['native']['extended_tokens'])}\n")
    effective_engine = {
        name: inspect.signature(PottsMPNNPHEngine).parameters[name].default
        for name in ENGINE_KEYS
    }
    effective_engine.update(engine_options)
    effective_engine["extended_vocab"] = vocab
    identity_path = native_root / "bms-runtime-identity.json"
    identity = json.loads(identity_path.read_text()) if identity_path.exists() else {}
    native_module = Path(inspect.getfile(PottsMPNNPHEngine))
    runtime = {
        **identity, "source_url": "https://github.com/christian-creator/ProtonPottsMPNN",
        "source_revision": SOURCE_REVISION,
        "native_module_sha256": sha256(native_module),
        "checkpoint": {"relative_path": CHECKPOINT_RELATIVE, "sha256": sha256(checkpoint)},
        "device": str(engine.device), "n_jobs": n_jobs,
        "python": sys.version, "torch": torch.__version__, "torch_cuda": torch.version.cuda,
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "effective_engine_options": effective_engine,
        "effective_criteria": [dataclasses.asdict(c) for c in criteria],
        "input_sha256": sha256(input_path),
        "structure_output": {"supported_by_run_ph_redesign": False,
                             "requested": options.get("write_structures", False),
                             "produced": False, "refolded_or_validated": False},
    }
    manifest = {"contract": CONTRACT, "source": request["source"], "request": request,
                "designs": designs, "seed_energies": native_results.seed_energies,
                "runtime": runtime,
                "artifacts": sorted(str(p.relative_to(result_dir)) for p in result_dir.iterdir()
                                    if p.is_file() and p.name != "manifest.json")}
    manifest["artifacts"].append("manifest.json")
    (result_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n-jobs", type=int, default=1, help="Scheduler-assigned CPU solve concurrency")
    parser.add_argument("--device", default=None, help="Scheduler-selected native device, e.g. cpu")
    parser.add_argument("--native-root", type=Path,
                        default=Path(os.environ.get("PROTONPOTTSMPNN_ROOT", "/opt/ProtonPottsMPNN")))
    args = parser.parse_args()
    result = run(json.loads(args.request.read_text()), args.input, args.out,
                 args.native_root, args.n_jobs, args.device)
    print(json.dumps({"manifest": str(args.out / "protonpottsmpnn_design/manifest.json"),
                      "design_count": len(result["designs"]),
                      "checkpoint_sha256": result["runtime"]["checkpoint"]["sha256"]}))


if __name__ == "__main__":
    main()
