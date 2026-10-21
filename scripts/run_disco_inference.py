#!/usr/bin/env python3
from __future__ import annotations

import argparse
import inspect
import json
import os
import runpy
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from lib.disco_contract import native_overrides, parse_native_records
from lib.portable_inputs import bind_native_document, resolve_input_path


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _copy(source: Path, directory: Path) -> str | None:
    if not source.is_file():
        return None
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    shutil.copy2(source, target)
    return str(target)


def observe_native(repo: Path, output_dir: Path, overrides: list[str]) -> None:
    """Observe the pinned native batch/writer boundaries, without resampling.

    The native CIF writer reuses names across seeds. Snapshot each actual writer
    return while its input/seed context is live, rather than reverse-engineering
    flattened filenames afterwards. The model still loads once per invocation.
    """
    from omegaconf import OmegaConf

    sys.path.insert(0, str(repo))
    sys.argv = [str(repo / "runner/inference.py"), *overrides]
    entry = runpy.run_path(sys.argv[0], run_name="bms_disco_observed")
    native_main = entry["main"]
    namespace = inspect.unwrap(native_main).__globals__
    get_loader = namespace["get_inference_dataloader"]
    dumper = namespace["DataDumper"]
    save_structure = dumper._save_structure
    save_confidence = dumper._save_confidence
    context: dict = {}
    index_path = output_dir / "native_events.jsonl"

    def observed_structure(self, *args, **kwargs):
        # Signature binding uses the installed writer, not a second sampler.
        bound = inspect.signature(save_structure).bind(self, *args, **kwargs)
        bound.apply_defaults()
        paths = save_structure(self, *args, **kwargs)
        if context:
            for sample, path in enumerate(paths):
                context["structures"].append({
                    "sample_id": sample,
                    "native_writer_index": sample + bound.arguments["idx_offset"],
                    "native_path": str(path),
                    "path": _copy(Path(path), Path(context["directory"]) / "structures"),
                })
        return paths

    def observed_confidence(self, *args, **kwargs):
        bound = inspect.signature(save_confidence).bind(self, *args, **kwargs)
        bound.apply_defaults()
        result = save_confidence(self, *args, **kwargs)
        if context:
            values = bound.arguments
            directory = Path(values["prediction_save_dir"])
            for sample in range(len(values["data"]["summary_confidence"])):
                for role in ("summary_confidence", "full_data"):
                    path = directory / f"{values['sample_name']}_{role}_sample_{sample}.json"
                    saved = _copy(path, Path(context["directory"]) / "confidence")
                    if saved:
                        context["confidence"].append(dict(sample_id=sample, role=role, path=saved))
        return result

    def observed_loader(*args, **kwargs):
        configs = kwargs["configs"]
        effective = OmegaConf.to_container(configs, resolve=True)
        _write_json(output_dir / "disco_effective_config.json", effective)
        loader = get_loader(*args, **kwargs)

        class ObservedLoader:
            dataset = loader.dataset

            def __iter__(self):
                for batch in loader:
                    data = batch[0][0]
                    # sample_index is native input-document index, NOT output rank.
                    input_index = int(data["sample_index"])
                    seed = int(data["seed"])
                    name = data["sample_name"]
                    directory = output_dir / "native" / f"input_{input_index}" / f"seed_{seed}"
                    context.update(input_index=input_index, input_name=name, seed=seed,
                                   directory=str(directory), structures=[], confidence=[])
                    root = Path(configs.dump_dir)
                    stem = f"{name}_sample_{seed}"
                    sidecars = {"sequence_path": root / "sequences" / f"{stem}.txt",
                                "ligand_path": root / "pdbs" / f"{stem}_ligands.txt"}

                    def stamp(path):
                        if not path.is_file():
                            return None
                        stat = path.stat()
                        return stat.st_ino, stat.st_size, stat.st_mtime_ns

                    before = {key: stamp(path) for key, path in sidecars.items()}
                    try:
                        yield batch
                    finally:
                        # Duplicate native names can leave an earlier record on
                        # disk after a later failure. Only collect this write.
                        for key, path in sidecars.items():
                            context[key] = (_copy(path, directory)
                                            if stamp(path) != before[key] else None)
                        # ERR filenames are input-only, never seed-associated.
                        with index_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(context) + "\n")
                        context.clear()
        return ObservedLoader()

    namespace["get_inference_dataloader"] = observed_loader
    dumper._save_structure = observed_structure
    dumper._save_confidence = observed_confidence
    try:
        native_main()
    finally:
        namespace["get_inference_dataloader"] = get_loader
        dumper._save_structure = save_structure
        dumper._save_confidence = save_confidence


def publish_outputs(output_dir: Path, request: dict, native_returncode: int) -> list[dict]:
    """Publish observed exact identities; unknown/missing records remain missing."""
    index = output_dir / "native_events.jsonl"
    events = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    manifest = []
    diagnostics = []

    def relative(path):
        return str(Path(path).relative_to(output_dir)) if path else None

    for event in events:
        sequence_path = event.get("sequence_path")
        text = Path(sequence_path).read_text() if sequence_path else ""
        records = parse_native_records(text)
        # Preserve duplicate/headerless records but do not guess their association.
        record_ids = [r["sample_id"] for r in records]
        structures = {s["sample_id"]: s for s in event["structures"]}
        sample_ids = sorted(set(structures) | {i for i in record_ids if i is not None})
        if not sample_ids:
            diagnostics.append(dict(input_index=event["input_index"], input_name=event["input_name"],
                                    seed=event["seed"], status="no_sample_emitted"))
        for sample in sample_ids:
            matches = [r for r in records if r["sample_id"] == sample]
            record = matches[0] if len(matches) == 1 else None
            structure = structures.get(sample)
            # Exact observed input index, seed and native sample index, no prefix
            # fallback or ordinal pairing to some other file/sequence record.
            prefix = "".join(ch if ch.isalnum() else "_" for ch in request.get("job_name", "pcad")).strip("_") or "pcad"
            design_id = f"{prefix}_disco_input_{event['input_index']}_seed_{event['seed']}_sample_{sample}"
            summary_items = [c for c in event["confidence"]
                             if c["sample_id"] == sample and c["role"] == "summary_confidence"]
            summary = None
            summary_error = None
            if len(summary_items) == 1:
                try:
                    summary = json.loads(Path(summary_items[0]["path"]).read_text())
                except (ValueError, OSError) as exc:
                    summary_error = str(exc)
            structure_path = structure["path"] if structure else None
            # Preserve the old CAD PDB projection for actual native PDBs only.
            projected = None
            if structure_path and Path(structure_path).suffix == ".pdb":
                target = output_dir / "raw/pdbs" / f"{design_id}.pdb"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(structure_path, target)
                projected = str(target)
            metadata = {
                "design_id": design_id, "source": "disco", "source_model": "DISCO",
                "generator_family": "protein_cad_experimental", "generator_mode": request["task"],
                "designed_sequence": record["protein_sequence"] if record else None,
                "native_identity": {key: event[key] for key in ("input_index", "input_name", "seed")}
                                   | {"sample_id": sample},
                "native_record": record,
                "native_summary_confidence": summary,
                "summary_read_error": summary_error,
                "native_structure_path": relative(structure_path),
                "native_sequence_path": relative(sequence_path),
                "native_ligand_path": relative(event.get("ligand_path")),
                "native_confidence_paths": [relative(c["path"]) for c in event["confidence"]
                                            if c["sample_id"] == sample],
                "missing": [name for name, value in (("structure", structure_path),
                            ("sequence_record", record), ("summary_confidence", summary)) if value is None],
            }
            # Confidence cannot overwrite sequence, custody, source or identity.
            for key in ("ranking_score", "ptm", "plddt"):
                metadata[key] = summary.get(key) if isinstance(summary, dict) else None
            meta_path = output_dir / "raw/metadata" / f"generator_{design_id}.json"
            _write_json(meta_path, metadata)
            manifest.append(dict(design_id=design_id, sequence=metadata["designed_sequence"],
                                 structure_path=relative(projected or structure_path),
                                 metadata_path=relative(meta_path), native_identity=metadata["native_identity"],
                                 missing=metadata["missing"]))
        if any(i is None or record_ids.count(i) != 1 for i in record_ids):
            diagnostics.append(dict(input_index=event["input_index"], seed=event["seed"],
                                    status="unassociated_sequence_records", records=records))
    _write_json(output_dir / "design_manifest.json", manifest)
    accounting = None
    config_path = output_dir / "disco_effective_config.json"
    input_path = output_dir / "disco_execution_input.json"
    if config_path.is_file() and input_path.is_file():
        effective = json.loads(config_path.read_text())
        jobs = json.loads(input_path.read_text())
        # Captured after native num_inference_seeds -> seeds normalization.
        seeds = effective["seeds"]
        counts = Counter((row["native_identity"]["input_index"], row["native_identity"]["seed"])
                         for row in manifest)
        accounting = [{"input_index": i, "input_name": job["name"], "seed": seed,
                       "emitted_samples": counts[(i, seed)]}
                      for i, job in enumerate(jobs) for seed in seeds]
    errors = sorted(str(p.relative_to(output_dir)) for p in (output_dir / "disco_output/ERR").glob("*.txt"))
    _write_json(output_dir / "disco_publication.json", {
        "schema": "disco_native_v1", "native_returncode": native_returncode,
        "input_jobs": request.get("disco", {}).get("input_job_count"),
        "input_seed_accounting": accounting,
        "requested_input_seed_count": len(accounting) if accounting is not None else None,
        "observed_input_seed_count": len(events), "emitted_sample_count": len(manifest),
        "errors": errors, "diagnostics": diagnostics,
        "execution_failed": native_returncode != 0,
        "native_errors_present": bool(errors),
        "raw_root": "disco_output", "observed_root": "native",
        "effective_config": "disco_effective_config.json",
    })
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DISCO and retain native output identities.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    request_path = resolve_input_path(args.request)
    request = bind_native_document(json.loads(request_path.read_text()), "protein-cad", owner=request_path)
    disco = request["disco"]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / "disco_output"
    run_dir.mkdir(parents=True, exist_ok=True)
    input_json_path = disco.get("input_json_path") or disco.get("compiled_input_json")
    if not input_json_path:
        raise ValueError("DISCO request does not contain an input_json_path")
    source_json = resolve_input_path(input_json_path)
    bound_jobs = bind_native_document(json.loads(source_json.read_text()), "disco-json", owner=source_json)
    derived_json = output_dir / "disco_execution_input.json"
    _write_json(derived_json, bound_jobs)
    disco["input_job_count"] = len(bound_jobs)
    repo = Path(os.environ.get("DISCO_REPO", "/opt/disco")).resolve()
    work_dir = output_dir / "disco_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Path(disco.get("checkpoint_path") or "/weights/disco/DISCO.pt").absolute()
    overrides = native_overrides(disco) + [f"input_json_path={derived_json}",
                f"dump_dir={run_dir}", f"load_checkpoint_path={checkpoint}"]
    cmd = [sys.executable, str(Path(__file__).resolve()), "--observe-native", str(repo), str(output_dir), *overrides]
    _write_json(output_dir / "disco_requested_settings.json", {"requested": disco, "native_overrides": overrides})
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(repo), env.get("PYTHONPATH"))))
    hf_cache = Path(disco.get("hf_cache_path") or checkpoint.parent / "huggingface")
    env["HF_HOME"] = str(hf_cache)
    env["HF_HUB_CACHE"] = str(hf_cache / "hub")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    cache_root = Path(env.get("XDG_CACHE_HOME") or work_dir / "cache")
    for key, name in (("XDG_CACHE_HOME", "general"), ("TRITON_CACHE_DIR", "triton"),
                      ("TORCH_EXTENSIONS_DIR", "torch_extensions")):
        cache = Path(env.get(key) or (cache_root if key == "XDG_CACHE_HOME" else cache_root / name))
        cache.mkdir(parents=True, exist_ok=True)
        env[key] = str(cache)
    if disco.get("cutlass_path"):
        env["CUTLASS_PATH"] = disco["cutlass_path"]
    with (output_dir / "disco.log").open("w") as log:
        result = subprocess.run(cmd, cwd=work_dir, env=env, check=False, stdout=log, stderr=subprocess.STDOUT)
    publish_outputs(output_dir, request, result.returncode)
    # Preserve native process failure AFTER making partial data inspectable.
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--observe-native":
        observe_native(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
    else:
        main()
