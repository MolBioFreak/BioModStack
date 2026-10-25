#!/usr/bin/env python3
"""Invoke pinned native La-Proteina in a private configuration/output namespace."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.laproteina_contract import (
    MOTIF_CONTROLS, RUNTIME_RELEASE, SOURCE_REVISION, apply_controls, resolve_profile,
)
from lib.portable_inputs import bind_native_document, resolve_input_path


def _sanitize_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_") or "pcad"


def _configure_laproteina(request: dict, output_dir: Path) -> dict:
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf

    upstream = Path(os.environ.get("LAPROTEINA_REPO", "/opt/laproteina")).resolve()
    settings = request["laproteina"]
    profile = resolve_profile(settings["preset"])
    if request["task"] != profile["task"]:
        raise ValueError(f"La-Proteina preset {settings['preset']} is for {profile['task']}, not {request['task']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="invocation_", dir=output_dir)).resolve()
    config_root = attempt / "configs"
    # Copy only small native configs, including inference_base and all transitive
    # groups. Never copy the source tree/checkpoints or mutate an installed SIF.
    shutil.copytree(upstream / "configs", config_root)
    with initialize_config_dir(config_dir=str(config_root), version_base="1.3"):
        cfg = compose(config_name=profile["config_name"])
    cfg.ckpt_path = str(Path(settings.get("checkpoint_dir") or upstream / "checkpoints_laproteina").resolve())
    cfg.ckpt_name = profile["ld"]
    cfg.autoencoder_ckpt_path = str(Path(cfg.ckpt_path) / profile["ae"])
    apply_controls(cfg, settings)
    if profile["task"] == "unconditional":
        cfg.generation.dataset.nlens_cfg.nres_lens = request["target_lengths"]
        cfg.generation.dataset.nsamples = settings["samples_per_length"]
    else:
        cfg.generation.dataset.nsamples = request["num_designs"]
        motif_name = settings.get("motif_task_name") or "BMS_CUSTOM"
        cfg.generation.dataset.motif_task_name = motif_name
        if settings.get("motif_pdb"):
            motif = {"motif_pdb_path": settings["motif_pdb"], "motif_only": True,
                     "motif_min_length": min(request["target_lengths"]),
                     "motif_max_length": max(request["target_lengths"]),
                     "segment_order": "A", "atom_selection_mode": "all_atom"}
            for key in MOTIF_CONTROLS:
                explicit = key in request.get("native_settings_fields", {}).get("laproteina", [])
                if key in settings and (explicit or (settings[key] is not None and settings[key] != "")):
                    motif[key] = settings[key]
            OmegaConf.update(cfg, "generation.dataset.motif_dict_cfg." + motif_name, motif, merge=False, force_add=True)
        elif motif_name not in cfg.generation.dataset.motif_dict_cfg:
            raise ValueError(f"La-Proteina motif task not found: {motif_name}")
    # Native defaults and LD3 overrides are already composed; explicit controls
    # therefore win over profile values rather than being shadowed by _self_.
    config_name = "inference_bms"
    OmegaConf.save(cfg, config_root / (config_name + ".yaml"))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(upstream) + os.pathsep + env.get("PYTHONPATH", "")
    env["LAPROTEINA_MOTIF_FEATURE"] = profile["feature_mode"]
    if settings.get("data_path"):
        env["DATA_PATH"] = settings["data_path"]
    # hydra.initialize resolves relative to the absolute generate.py, not cwd.
    # A relative traversal points at this attempt without modifying native code.
    config_subdir = os.path.relpath(config_root, upstream / "configs")
    cmd = [sys.executable, str(upstream / "proteinfoundation/generate.py"),
           "--config_name", config_name, "--config_subdir", config_subdir]
    inference_root = attempt / "inference" / (
        config_name if profile["task"] == "unconditional"
        else config_name + "_" + cfg.generation.dataset.motif_task_name)
    receipt = {"profile": profile, "requested": settings, "command": cmd,
               "cwd": str(attempt), "config_root": str(config_root),
               "inference_root": str(inference_root),
               "effective_config": OmegaConf.to_container(cfg, resolve=False),
               "runtime_release": RUNTIME_RELEASE, "source_revision": SOURCE_REVISION}
    installed_receipt = upstream / "bms-runtime.json"
    receipt["installed_runtime"] = json.loads(installed_receipt.read_text()) if installed_receipt.exists() else None
    (attempt / "invocation.json").write_text(json.dumps(receipt, indent=2))
    return {"attempt": attempt, "inference_root": inference_root, "command": cmd, "env": env,
            "receipt": receipt}


def collect_outputs(request: dict, invocation: dict, output_dir: Path, native_returncode: int = 0) -> list[dict]:
    """Receive only native writer-indexed final samples, never directory guesses."""
    root = invocation["inference_root"]
    index_path = root / "sample_index.jsonl"
    records = [json.loads(line) for line in index_path.read_text().splitlines() if line] if index_path.exists() else []
    raw_pdb = output_dir / "raw/pdbs"
    raw_meta = output_dir / "raw/metadata"
    raw_pdb.mkdir(parents=True, exist_ok=True)
    raw_meta.mkdir(parents=True, exist_ok=True)
    manifest = []
    def relative(path):
        return str(Path(path).relative_to(output_dir))
    prefix = _sanitize_name(request["job_name"])
    for record in records:
        native_id = record["native_sample_id"]
        design_id = f"{prefix}_laproteina_{invocation['attempt'].name}_{_sanitize_name(native_id)}"
        source = root / record["structure_path"]
        target = raw_pdb / f"{design_id}.pdb"
        if source.is_file():
            shutil.copy2(source, target)
        sequence = record["sequence"]
        metadata_path = raw_meta / f"generator_{design_id}.json"
        trajectory = invocation["attempt"] / "native_trajectories" / f"batch_{record['batch_index']}.pt"
        metadata = {"design_id": design_id, "designed_sequence": sequence,
                    "source": "laproteina", "source_model": "La-Proteina",
                    "generator_family": "protein_cad_experimental", "generator_mode": request["task"],
                    "length": len(sequence), "native_sample": record,
                    "structure_present": source.is_file(),
                    "source_sample_dir": relative(source.parent),
                    "invocation_path": relative(invocation["attempt"] / "invocation.json"),
                    "trajectory_path": relative(trajectory) if trajectory.exists() else None,
                    "trajectory_sample_index": record["sample_index"]}
        metadata_path.write_text(json.dumps(metadata, indent=2))
        manifest.append({"design_id": design_id, "sequence": sequence,
                         "structure_path": relative(target) if source.is_file() else None,
                         "metadata_path": relative(metadata_path), "source_sample_dir": relative(source.parent),
                         "native_sample_id": native_id,
                         "invocation_id": invocation["attempt"].name})
    (output_dir / "design_manifest.json").write_text(json.dumps(manifest, indent=2))
    (output_dir / "laproteina_publication.json").write_text(json.dumps({
        "schema": "laproteina_native_v1", "native_returncode": native_returncode,
        "execution_failed": native_returncode != 0, "emitted_sample_count": len(manifest),
        "manifest": "design_manifest.json", "invocation": relative(invocation["attempt"] / "invocation.json"),
        "sample_index": relative(index_path) if index_path.is_file() else None,
    }, indent=2))
    return manifest


def evaluate_native(request: dict, output_dir: Path, *, prepare_only=False):
    """Separate native evaluation of retained outputs; never invokes generation.

    Preserve native filenames and motif tables in a private copy, because the
    native evaluator creates per-sample folding directories beside its inputs.
    """
    import hashlib
    from omegaconf import OmegaConf
    from lib.laproteina_contract import EVALUATION_CONTROLS
    source = Path(request['source_directory']).resolve()
    config_path = Path(request['config_path']).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='evaluation_', dir=output_dir)).resolve()
    samples = attempt / 'samples'
    shutil.copytree(source, samples)
    cfg = OmegaConf.load(config_path)
    for key, value in request.get('metrics', {}).items():
        if key not in EVALUATION_CONTROLS:
            raise ValueError(f'Unsupported native evaluation control: {key}')
        OmegaConf.update(cfg, 'generation.metric.' + key, value, merge=False, force_add=True)
    OmegaConf.save(cfg, attempt / 'effective_config.yaml')
    receipt = {'operation': 'evaluate', 'source_revision': SOURCE_REVISION,
               'requested': request, 'effective_config': OmegaConf.to_container(cfg, resolve=False),
               'source_config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
               'source_directory': str(source), 'consumed_directory': str(samples),
               'execution_status': 'prepared'}
    receipt_path = attempt / 'evaluation.json'
    receipt_path.write_text(json.dumps(receipt, indent=2))
    if prepare_only:
        return receipt
    # Use the actual native function, not CLI main's compute_designability-only
    # dispatch (which leaves df undefined for codesignability-only requests).
    upstream = Path(os.environ.get('LAPROTEINA_REPO', '/opt/laproteina')).resolve()
    sys.path.insert(0, str(upstream))
    from proteinfoundation import evaluate
    evaluate.cfg = cfg
    evaluate.root_path = str(samples)
    try:
        paths = evaluate.split_by_job(cfg, request['job_id'], is_des=True)
        frame = evaluate.compute_traditional_metrics(cfg.generation.metric, paths,
                                                     request['job_id'], request['ncpus'])
        result = attempt / 'results.csv'
        frame.to_csv(result, index=False)
        receipt.update(execution_status='completed', result_path=str(result), sample_count=len(paths))
    except Exception:
        receipt['execution_status'] = 'failed'
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prepare-only", action="store_true", help="CPU-only native Hydra composition; no weights loaded")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate retained native outputs; no generation")
    args = parser.parse_args()
    request_path = resolve_input_path(args.request)
    if args.evaluate:
        print(json.dumps(evaluate_native(json.loads(request_path.read_text()),
                                        Path(args.output_dir).resolve(), prepare_only=args.prepare_only)))
        return
    request = bind_native_document(json.loads(request_path.read_text()), "protein-cad", owner=request_path)
    output_dir = Path(args.output_dir).resolve()
    invocation = _configure_laproteina(request, output_dir)
    if args.prepare_only:
        print(json.dumps(invocation["receipt"], indent=2))
        return
    with (output_dir / "laproteina.log").open("w") as log:
        result = subprocess.run(invocation["command"], cwd=invocation["attempt"], env=invocation["env"],
                                check=False, stdout=log, stderr=subprocess.STDOUT)
    # Retain emitted partial output even if native generation later failed.
    collect_outputs(request, invocation, output_dir, result.returncode)
    result.check_returncode()


if __name__ == "__main__":
    main()
