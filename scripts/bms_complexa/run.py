"""Thin invocation of official Complexa; no sampler or remodeling implementation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil

from scripts.lib.complexa_contract import Request, PROFILES, SOURCE_REVISION
from scripts.bms_complexa.config import compose_request, selected_dependencies
from scripts.bms_complexa.inputs import prepare_source
from scripts.bms_complexa.publication import publish
from scripts.bms_complexa.hf_cache import prepare_hf_view


def prepare_community_models(native: Path, upstream: Path, dependencies: list[str]) -> Path:
    """Expose native cwd-relative entrypoints without copying model payloads.

    ProteinMPNN resolves weights relative to realpath(__file__), so its entrypoint
    must be a private regular file, not a symlink into the immutable image.
    """
    community = native / "community_models"
    if community.exists():
        shutil.rmtree(community)
    selected = set(dependencies)
    packages = []
    if "protein_mpnn" in selected:
        packages.append(("ProteinMPNN", "protein_mpnn_run.py", {
            "ca_model_weights": os.environ.get("COMPLEXA_PROTEINMPNN_CA_WEIGHTS"),
        }))
    if selected.intersection({"ligand_mpnn", "soluble_mpnn"}):
        packages.append(("LigandMPNN", "run.py", {
            "model_params": os.environ.get("COMPLEXA_LIGANDMPNN_WEIGHTS"),
        }))
    for name, entrypoint, weights in packages:
        source = upstream.resolve() / "community_models" / name
        destination = community / name
        destination.mkdir(parents=True)
        for member in source.iterdir():
            if member.name in {"ca_model_weights", "vanilla_model_weights", "soluble_model_weights", "model_params", "__pycache__", ".git"}:
                continue
            target = destination / member.name
            if member.name == entrypoint:
                shutil.copyfile(member, target)
            else:
                target.symlink_to(member, target_is_directory=member.is_dir())
        for name, path in weights.items():
            # Keep native missing-file behavior; absence is not a new gate.
            (destination / name).symlink_to(Path(path).absolute() if path else source / name,
                                           target_is_directory=True)
    return community


def run(request: Request, *, upstream: Path, weights: Path, output: Path,
        source_path: Path | None = None, evaluation_directory: Path | None = None,
        motif_csv_path: Path | None = None,
        autoguidance_checkpoint: Path | None = None, prepare_only: bool = False) -> dict:
    from omegaconf import OmegaConf
    output = output.resolve()
    native = output / "native"
    native.mkdir(parents=True, exist_ok=True)
    # Bundled targets are read-only; all outputs/configs are invocation private.
    assets = native / "assets"
    if not assets.exists():
        assets.symlink_to(upstream.resolve() / "assets", target_is_directory=True)
    source = None
    if request.source is not None:
        if source_path is None:
            raise ValueError("source document must be bound by portable-input owner")
        source = prepare_source(request.source, source_path, native / "source", ame=request.task == "AME")
        if request.source.motif_csv_slot is not None:
            # Exact retained table, not a generated replacement or filename guess.
            target = native / 'source' / 'motif.csv'
            shutil.copyfile(motif_csv_path, target)
            source['motif_csv_path'] = str(target.resolve())
    cfg = compose_request(request, upstream.resolve(), native, weights.resolve(),
                          prepared_source=source, evaluation_directory=evaluation_directory,
                          autoguidance_checkpoint=autoguidance_checkpoint)
    config = native / "configs/bms_complexa.yaml"
    stages = {"design": ["generate", "filter", "evaluate", "analyze"],
              "analysis": ["evaluate", "analyze"]}.get(request.operation, [request.operation])
    commands = [["complexa", stage, str(config), "--verbose"] for stage in stages]
    receipt = {"request": request.model_dump(mode="json", exclude_unset=True),
               "source_revision": SOURCE_REVISION, "checkpoint_pair": PROFILES[request.task][1],
               "effective_config": OmegaConf.to_container(cfg, resolve=False),
               "selected_dependencies": selected_dependencies(request, cfg), "commands": commands, "stages": [],
               "execution": "not_started", "returncode": None, "source": source}
    (output / "request.json").write_text(json.dumps(receipt["request"], indent=2))
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2))
    community = prepare_community_models(native, upstream, receipt["selected_dependencies"])
    if prepare_only:
        return receipt
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in cfg.get("env_vars", {}).items()})
    env.setdefault("DATA_PATH", str(upstream.resolve() / "assets"))
    # The official CLI refuses every non-init command unless COMPLEXA_INIT is set;
    # BMS composes the equivalent configuration itself. Caches must also be
    # writable, because the image filesystem and its home directory are not.
    env.setdefault("COMPLEXA_INIT", "bms")
    env.setdefault("XDG_CACHE_HOME", str(native / ".cache"))
    env.setdefault("MPLCONFIGDIR", str(native / ".cache" / "matplotlib"))
    receipt["execution"] = "running"
    hf_view = native / ".cache" / "hf-evaluators"
    try:
        repositories = [repo for dependency, repo in (
            ("esmfold", "facebook/esmfold_v1"), ("esm", "facebook/esm2_t33_650M_UR50D"))
            if dependency in receipt["selected_dependencies"]]
        cache = prepare_hf_view(hf_view, env.get("CACHE_DIR") or env.get("HF_HUB_CACHE"), repositories)
        if cache is not None:
            env.update({key: str(cache) for key in ("CACHE_DIR", "HF_HUB_CACHE", "TRANSFORMERS_CACHE")})
        with (native / "complexa.log").open("w") as log:
            for command in commands:
                completed = subprocess.run(command, cwd=native, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
                receipt["stages"].append({"stage": command[1], "returncode": completed.returncode})
                receipt["returncode"] = completed.returncode
                if completed.returncode:
                    break
                if command[1] == "generate" and request.operation == "design":
                    # Native filter may delete non-top-N samples. Retain original bytes
                    # before it runs without changing native ranking/filter semantics.
                    generated = native / "inference"
                    if generated.exists():
                        shutil.copytree(generated, native / "retained_generation")
        receipt["execution"] = "completed" if receipt["returncode"] == 0 else "failed"
    except OSError as exc:
        receipt["execution"] = "failed"
        receipt["error"] = str(exc)
        raise
    finally:
        if hf_view.exists():
            shutil.rmtree(hf_view)
        if community.exists():
            shutil.rmtree(community)
        if assets.is_symlink():
            assets.unlink()
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2))
        publish(output, receipt)
    if receipt["returncode"]:
        raise subprocess.CalledProcessError(receipt["returncode"], command)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, default=Path(os.environ.get("COMPLEXA_ROOT", "/opt/complexa")))
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--evaluation-directory", type=Path)
    parser.add_argument("--autoguidance-checkpoint", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    request = Request.model_validate_json(args.request.read_text())
    run(request, upstream=args.upstream, weights=args.weights, output=args.output,
        source_path=args.source, evaluation_directory=args.evaluation_directory,
        motif_csv_path=args.request.parent / 'motif.csv' if request.source and request.source.motif_csv_slot else None,
        autoguidance_checkpoint=args.autoguidance_checkpoint, prepare_only=args.prepare_only)


if __name__ == "__main__":
    main()
