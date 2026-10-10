"""Ordered native stages using the existing GROMACS command and ledger owners."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .contract import build_run_manifest, write_atom_order_manifest
from .gromacs import build_mdrun_command
from .native_config import render_mdp
from .runner import StageLedger, parse_gromacs_performance

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def native_frame_map(gmx: str, trajectory: Path, replica: int) -> dict[str, Any]:
    """Read native frame steps/times, not time/dt guesses (tinit may be nonzero)."""
    frames = []
    atom_count = None
    pending = None
    source_frame = 0
    # Stream the dump: native coordinates can dwarf the small frame index.
    with subprocess.Popen([gmx, "dump", "-f", str(trajectory)], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True) as process:
        assert process.stdout is not None
        for line in process.stdout:
            header = re.search(r"\bframe\s+(\d+):", line)
            if header:
                source_frame = int(header[1])
                pending = None
            atoms = re.search(r"\bnatoms\s*=\s*(\d+)", line)
            if atoms:
                atom_count = int(atoms[1])
            step = re.search(r"\bstep\s*=\s*(-?\d+)", line)
            time = re.search(r"\btime\s*=\s*(" + _NUMBER + r")", line)
            if step and time:
                pending = {"source_frame": source_frame, "step": int(step[1]), "time_ps": float(time[1])}
            # TRR may contain only velocities or forces at a reported step.
            if pending is not None and re.match(r"\s*x \(\d+x3\):", line):
                frames.append({"display_frame": len(frames), **pending})
                pending = None
        returncode = process.wait()
    if returncode:
        raise RuntimeError(f"GROMACS trajectory dump failed with exit code {returncode}")
    return {"schema": "bms.md.trajectory-frame-map.v1", "replica": replica,
            "trajectory_sha256": _digest(trajectory), "frames": frames, "atom_count": atom_count}


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run_native_stages(config: Mapping[str, Any], *, config_path: Path, output_dir: Path,
                      replica_index: int, gmx_binary: str, version_output: str,
                      preparation_bundle: Path | None, ledger: StageLedger) -> Path:
    from .gromacs_pipeline import (
        _atomic_json, _consume_preparation_bundle, _portable_stage_snapshot,
        _resolve_input_path, _run_command, _version_summary,
    )

    inputs = config["input"]
    files = {key: _resolve_input_path(str(inputs[key]), config_path)
             for key in ("coordinates", "topology", "tpr", "checkpoint", "index", "restraint_reference")
             if inputs.get(key)}
    coordinates, topology = files.get("coordinates"), files.get("topology")
    if inputs.get("structure"):
        if preparation_bundle is None:
            raise ValueError("guided native input requires its preparation bundle")
        coordinates, topology, _ = _consume_preparation_bundle(config, preparation_bundle, output_dir, ledger)
    previous_checkpoint = files.get("checkpoint")
    stages = config["stages"] if "tpr" not in files else [{"name": "production"}]
    artifacts: dict[str, Path] = {"normalized_config": output_dir / "job.normalized.json",
                                "stage_ledger": output_dir / "stage_state.json",
                                "engine_version": output_dir / "gromacs_version.txt"}
    endpoints = {}
    publication_errors = {}

    def observe_dump(flag: str, path: Path, log_path: Path) -> str:
        try:
            return _run_command([gmx_binary, "dump", flag, str(path)], cwd=path.parent, log_path=log_path)
        except RuntimeError as exc:
            publication_errors[str(log_path.relative_to(output_dir))] = str(exc)
            return ""
    for stage in stages:
        name = stage["name"]
        directory = output_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        prefix = directory / name
        tpr, checkpoint = prefix.with_suffix(".tpr"), prefix.with_suffix(".cpt")
        final_coordinates = prefix.with_suffix(".gro")
        # Reuse only the exact completed artifacts recorded by the existing ledger.
        recorded = ledger.snapshot()["stages"].get(name, {}).get("artifacts", [])
        complete = bool(recorded) and ledger.is_complete(name, [Path(x["path"]) for x in recorded])
        if not complete:
            if not checkpoint.is_file():
                if "tpr" in files:
                    shutil.copy2(files["tpr"], tpr)
                else:
                    mdp = prefix.with_suffix(".mdp")
                    mdp.write_text(render_mdp(stage["mdp"]), encoding="utf-8")
                    command = [gmx_binary, "grompp", "-f", str(mdp), "-c", str(coordinates),
                               "-p", str(topology), "-o", str(tpr), "-po", str(directory / "effective.mdp")]
                    for key, flag in (("index", "-n"), ("restraint_reference", "-r")):
                        if key in files:
                            command.extend([flag, str(files[key])])
                    if previous_checkpoint is not None:
                        command.extend(["-t", str(previous_checkpoint)])
                    ledger.mark_running(name, command)
                    try:
                        _run_command(command, cwd=topology.parent, log_path=directory / "grompp.command.log")
                    except Exception as exc:
                        ledger.mark_failed(name, str(exc))
                        raise
            execution = config["execution"]
            command = build_mdrun_command(
                gmx=gmx_binary, deffnm=str(prefix), gpu_id=execution["gpu_id"],
                ntmpi=execution["ntmpi"], ntomp=execution["ntomp"],
                gpu_offload=execution["gpu_offload"], pin=execution["pin"], checkpoint=checkpoint,
                checkpoint_interval_minutes=stage.get("checkpoint_interval_minutes"),
            )
            command.extend(["-px", str(directory / "pullx.xvg"), "-pf", str(directory / "pullf.xvg")])
            # External CPT starts a new output set; same-output continuation appends.
            if "tpr" in files and previous_checkpoint is not None and not checkpoint.is_file():
                command.extend(["-cpi", str(previous_checkpoint), "-noappend"])
            ledger.mark_running(name, command)
            try:
                console = _run_command(command, cwd=directory, log_path=directory / "mdrun.command.log")
                emitted = [p for p in directory.iterdir() if p.is_file() and not p.name.startswith("#")]
                ledger.mark_completed(name, emitted, performance=parse_gromacs_performance(console))
            except Exception as exc:
                ledger.mark_failed(name, str(exc))
                raise
        # Native -noappend uses partNNNN output names. Select this stage's last part.
        def product(suffix: str) -> Path | None:
            candidates = sorted(directory.glob(f"{name}.part*{suffix}"))
            path = candidates[-1] if candidates else prefix.with_suffix(suffix)
            return path if path.is_file() else None

        coordinates = product(".gro") or final_coordinates
        previous_checkpoint = checkpoint if checkpoint.is_file() else None
        stage_products = {"coordinates": coordinates, "run_input": tpr,
                          "checkpoint": previous_checkpoint, "energy": product(".edr"),
                          "engine_log": product(".log"), "trajectory": product(".xtc") or product(".trr")}
        pull = sorted(directory.glob("pullx*.xvg"))
        if pull:
            stage_products["pull_coordinates"] = pull[-1]
        for key, path in stage_products.items():
            if path is not None and path.is_file():
                artifacts[f"stage_{name}_{key}"] = path
        dump = observe_dump("-s", tpr, directory / "tpr.dump.txt")
        native = {}
        for key in ("integrator", "nsteps", "init-step", "dt", "tinit", "ld-seed", "pull-ncoords"):
            match = re.search(r"^\s*" + key + r"\s*=\s*(\S+)", dump, re.MULTILINE)
            if match:
                native[key] = match[1]
        endpoint: dict[str, Any] = {"stage": name, "native_tpr": native}
        if previous_checkpoint is not None:
            text = observe_dump("-cp", previous_checkpoint, directory / "checkpoint.dump.txt")
            for key, target, cast in (("step", "step", int), ("t", "time_ps", float)):
                match = re.search(r"^\s*" + key + r"\s*=\s*(" + _NUMBER + r")", text, re.MULTILINE)
                if match:
                    endpoint[target] = cast(match[1])
        endpoints[name] = endpoint
    # These aliases describe the final authored stage, not a substituted protocol.
    artifacts.update({key: path for key, path in stage_products.items()
                      if path is not None and path.is_file()})
    artifacts["production_tpr"] = tpr
    if topology is not None:
        artifacts["topology"] = topology
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    representative = directory / f"{name}-final.pdb"
    _run_command([gmx_binary, "editconf", "-f", str(coordinates), "-o", str(representative)],
                 cwd=directory, log_path=analysis_dir / "representative_structure.command.log")
    atom_map = analysis_dir / "atom-order-manifest.json"
    _, atom_identity = write_atom_order_manifest(coordinates, atom_map)
    artifacts.update(representative_structure=representative, atom_order_manifest=atom_map)
    trajectory = artifacts.get("trajectory")
    frame_map = None
    if trajectory is not None:
        try:
            frame_map = native_frame_map(gmx_binary, trajectory, replica_index)
            if not frame_map["frames"]:
                artifacts.pop("trajectory")
                frame_map = None
        except RuntimeError as exc:
            publication_errors["trajectory_frame_map"] = str(exc)
    if frame_map is not None:
        frame_map["stage"] = name
        for key in ("window_id", "replicate_index"):
            if key in config:
                frame_map[key] = config[key]
        frame_path = analysis_dir / "trajectory-frame-map.json"
        _atomic_json(frame_path, frame_map)
        artifacts["trajectory_frame_map"] = frame_path
    manifest = build_run_manifest(
        output_dir=output_dir, job_config=config, replica_index=replica_index,
        engine_version=_version_summary(version_output),
        platform="CPU" if config["execution"]["gpu_offload"] == "none" else "CUDA",
        artifacts=artifacts, stages=_portable_stage_snapshot(ledger.snapshot()["stages"], output_dir),
    )
    manifest.update(status="completed", native_endpoints=endpoints, final_stage=name,
                    orchestration_seed=config["random_seed"])
    if publication_errors:
        manifest["publication_errors"] = publication_errors
    for key in ("window_id", "replicate_index"):
        if key in config:
            manifest[key] = config[key]
    roles = {"coordinates": "analysis_topology", "trajectory": "analysis_trajectory",
             "atom_order_manifest": "atom_order_manifest", "production_tpr": "production_tpr",
             "pull_coordinates": "pull_coordinates", "trajectory_frame_map": "trajectory_frame_map"}
    for key, role in roles.items():
        if key in manifest["artifacts"]:
            manifest["artifacts"][key]["semantic_role"] = role
            if key in ("coordinates", "atom_order_manifest"):
                manifest["artifacts"][key]["atom_order_identity"] = atom_identity
            if key == "trajectory" and frame_map is not None:
                atom_count = len(json.loads(atom_map.read_text())["atoms"])
                if frame_map["atom_count"] == atom_count:
                    manifest["artifacts"][key]["atom_order_identity"] = atom_identity
    representative_record = manifest["artifacts"]["representative_structure"]
    representative_record.update(semantic_role="representative_structure",
                                 selection_method="completed_production_final_coordinates", source_frame=None)
    endpoint = endpoints[name]
    if "time_ps" in endpoint:
        representative_record["time_ps"] = endpoint["time_ps"]
    if frame_map is not None:
        manifest["artifacts"]["trajectory_frame_map"]["source_trajectory_sha256"] = frame_map["trajectory_sha256"]
        # An endpoint need not coincide with the last saved trajectory frame.
        for frame in reversed(frame_map["frames"]):
            if frame["step"] == endpoint.get("step"):
                representative_record.update(source_frame=frame["source_frame"],
                                             source_trajectory_sha256=frame_map["trajectory_sha256"])
                break
    manifest_path = output_dir / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path
