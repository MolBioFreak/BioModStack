from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contract import build_run_manifest, prepare_verified_worker_inputs, write_atom_order_manifest
from .gromacs import assert_cuda_enabled, build_mdrun_command
from .runner import (
    StageLedger,
    assert_minimization_converged,
    parse_gromacs_performance,
    render_mdp,
)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _portable_stage_snapshot(stages: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    portable = json.loads(json.dumps(stages))
    root = output_dir.resolve()
    for stage_name, stage in portable.items():
        for artifact in stage.get("artifacts", []):
            path = Path(artifact["path"]).resolve()
            try:
                artifact["path"] = str(path.relative_to(root))
            except ValueError as exc:
                raise RuntimeError(
                    f"stage {stage_name} artifact escapes the replica output directory: {path}"
                ) from exc
    return portable


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    stdin_text: str | None = None,
) -> str:
    cwd.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        list(command),
        cwd=cwd,
        input=stdin_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(process.stdout, encoding="utf-8")
    if process.returncode != 0:
        excerpt = "\n".join(process.stdout.splitlines()[-30:])
        raise RuntimeError(
            f"MD command failed with exit code {process.returncode}: {' '.join(command)}\n{excerpt}"
        )
    return process.stdout


def _resolve_input_path(raw_path: str, config_path: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"MD input file does not exist: {path}")
    return path


def _version_summary(version_output: str) -> str:
    match = re.search(r"^GROMACS version:\s*(.+)$", version_output, re.MULTILINE)
    return match.group(1).strip() if match else version_output.strip().splitlines()[0]


def _ions_mdp() -> str:
    return "\n".join(
        (
            "integrator = steep",
            "nsteps = 500",
            "emtol = 1000.0",
            "cutoff-scheme = Verlet",
            "nstlist = 20",
            "rlist = 1.0",
            "coulombtype = PME",
            "rcoulomb = 1.0",
            "vdwtype = Cut-off",
            "rvdw = 1.0",
            "constraints = none",
            "pbc = xyz",
            "",
        )
    )


def _prepare_system(
    config: Mapping[str, Any],
    *,
    config_path: Path,
    output_dir: Path,
    gmx_binary: str,
    ledger: StageLedger,
) -> tuple[Path, Path, list[Path]]:
    system_dir = output_dir / "system"
    prep_dir = output_dir / "preparation"
    topology = system_dir / "topol.top"
    coordinates = system_dir / "solvated_ions.gro"
    required = [topology, coordinates]
    if ledger.is_complete("preparation", required):
        return coordinates, topology, required

    input_config = config["input"]
    if "structure" not in input_config:
        raise ValueError("phase-1 GROMACS preparation requires input.structure")
    structure = _resolve_input_path(str(input_config["structure"]), config_path)
    system_dir.mkdir(parents=True, exist_ok=True)
    prep_dir.mkdir(parents=True, exist_ok=True)
    copied_structure = system_dir / f"input{structure.suffix.lower() or '.pdb'}"
    shutil.copy2(structure, copied_structure)

    preparation = config["preparation"]
    processed = system_dir / "processed.gro"
    boxed = system_dir / "boxed.gro"
    solvated = system_dir / "solvated.gro"
    posre = system_dir / "posre.itp"
    ions_mdp = prep_dir / "ions.mdp"
    ions_tpr = prep_dir / "ions.tpr"
    ions_mdp.write_text(_ions_mdp(), encoding="utf-8")

    commands: list[tuple[str, list[str], str | None]] = [
        (
            "pdb2gmx",
            [
                gmx_binary,
                "pdb2gmx",
                "-f",
                str(copied_structure),
                "-o",
                str(processed),
                "-p",
                str(topology),
                "-i",
                str(posre),
                "-ff",
                str(preparation["force_field"]),
                "-water",
                str(preparation["water_model"]),
                "-ignh",
            ],
            None,
        ),
        (
            "editconf",
            [
                gmx_binary,
                "editconf",
                "-f",
                str(processed),
                "-o",
                str(boxed),
                "-bt",
                str(preparation["box_type"]),
                "-d",
                str(preparation["padding_nm"]),
            ],
            None,
        ),
        (
            "solvate",
            [
                gmx_binary,
                "solvate",
                "-cp",
                str(boxed),
                "-cs",
                str(preparation["solvent_coordinates"]),
                "-o",
                str(solvated),
                "-p",
                str(topology),
            ],
            None,
        ),
        (
            "ions_grompp",
            [
                gmx_binary,
                "grompp",
                "-f",
                str(ions_mdp),
                "-c",
                str(solvated),
                "-p",
                str(topology),
                "-o",
                str(ions_tpr),
                "-maxwarn",
                # The pre-ionization topology is intentionally charged; only this
                # canonical genion-preparation warning is admitted. All scientific
                # stages remain fail-closed at maxwarn=0.
                "1",
            ],
            None,
        ),
    ]
    genion = [
        gmx_binary,
        "genion",
        "-s",
        str(ions_tpr),
        "-o",
        str(coordinates),
        "-p",
        str(topology),
        "-pname",
        str(preparation["positive_ion"]),
        "-nname",
        str(preparation["negative_ion"]),
        "-conc",
        str(preparation["salt_molar"]),
    ]
    if preparation["neutralize"]:
        genion.append("-neutral")
    commands.append(("genion", genion, f"{preparation['solvent_group']}\n"))

    ledger.mark_running("preparation", [item for _, command, _ in commands for item in command])
    try:
        for label, command, stdin_text in commands:
            _run_command(
                command,
                cwd=system_dir,
                log_path=prep_dir / f"{label}.command.log",
                stdin_text=stdin_text,
            )
        ledger.mark_completed("preparation", required)
    except Exception as exc:
        ledger.mark_failed("preparation", str(exc))
        raise
    return coordinates, topology, required


def _consume_preparation_bundle(
    config: Mapping[str, Any], bundle: Path, output_dir: Path, ledger: StageLedger,
) -> tuple[Path, Path, list[Path]]:
    from .chemistry.prepare import verify_preparation_bundle

    chemistry = config.get("chemistry") or {}
    manifest = verify_preparation_bundle(
        bundle,
        expected_profile_id=chemistry.get("profile_id"),
        expected_profile_sha256=chemistry.get("profile_sha256"),
    )
    system_dir = output_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    coordinates = system_dir / "prepared.gro"
    topology = system_dir / "topol.top"
    restraint_names = sorted(
        record["path"] for record in manifest["files"]
        if isinstance(record, Mapping)
        and isinstance(record.get("path"), str)
        and record["path"].startswith("posre")
        and record["path"].endswith(".itp")
    )
    position_restraints = [system_dir / name for name in restraint_names]
    required = [coordinates, topology, *position_restraints]
    if not ledger.is_complete("preparation", required):
        ledger.mark_running("preparation", ["consume-immutable-bundle", manifest["bundle_sha256"]])
        shutil.copy2(Path(bundle) / "system.gro", coordinates)
        shutil.copy2(Path(bundle) / "system.top", topology)
        for source_name, destination_path in zip(restraint_names, position_restraints, strict=True):
            shutil.copy2(Path(bundle) / source_name, destination_path)
        ledger.mark_completed(
            "preparation", required,
            performance={"bundle_sha256": manifest["bundle_sha256"]},
        )
    return coordinates, topology, required


def _run_stage(
    stage_name: str,
    config: Mapping[str, Any],
    *,
    replica_index: int,
    output_dir: Path,
    system_dir: Path,
    coordinates: Path,
    topology: Path,
    previous_checkpoint: Path | None,
    restraint_reference: Path,
    gmx_binary: str,
    ledger: StageLedger,
) -> tuple[Path, Path | None, list[Path]]:
    stage_config = config["stages"][stage_name]
    if not stage_config["enabled"]:
        return coordinates, previous_checkpoint, []

    stage_dir = output_dir / stage_name
    stage_dir.mkdir(parents=True, exist_ok=True)
    mdp = stage_dir / f"{stage_name}.mdp"
    tpr = stage_dir / f"{stage_name}.tpr"
    prefix = stage_dir / stage_name
    output_coordinates = prefix.with_suffix(".gro")
    checkpoint = prefix.with_suffix(".cpt")
    energy = prefix.with_suffix(".edr")
    engine_log = prefix.with_suffix(".log")
    required = [output_coordinates, energy, engine_log]
    if stage_name != "minimization":
        required.append(checkpoint)
    if stage_name == "production":
        required.append(prefix.with_suffix(".xtc"))
    completed_checkpoint = checkpoint if stage_name != "minimization" else None
    if ledger.is_complete(stage_name, required):
        return output_coordinates, completed_checkpoint, required

    mdp.write_text(render_mdp(stage_name, config, replica_index), encoding="utf-8")
    grompp = [
        gmx_binary,
        "grompp",
        "-f",
        str(mdp),
        "-c",
        str(coordinates),
        "-p",
        str(topology),
        "-o",
        str(tpr),
        "-maxwarn",
        "0",
    ]
    if stage_name in {"nvt", "npt"}:
        grompp.extend(["-r", str(restraint_reference)])
    if previous_checkpoint is not None and previous_checkpoint.is_file():
        grompp.extend(["-t", str(previous_checkpoint)])

    execution = config["execution"]
    mdrun = build_mdrun_command(
        gmx=gmx_binary,
        deffnm=str(prefix),
        gpu_id=execution["gpu_id"],
        ntmpi=execution["ntmpi"],
        ntomp=execution["ntomp"],
        gpu_offload=("none" if stage_name == "minimization" else execution["gpu_offload"]),
        pin=execution["pin"],
        checkpoint=checkpoint,
        checkpoint_interval_minutes=(
            float(stage_config["checkpoint_interval_minutes"])
            if stage_name == "production"
            else 15.0
        ),
    )
    ledger.mark_running(stage_name, mdrun)
    try:
        _run_command(grompp, cwd=system_dir, log_path=stage_dir / "grompp.command.log")
        console = _run_command(mdrun, cwd=system_dir, log_path=stage_dir / "mdrun.command.log")
        engine_log_text = engine_log.read_text(encoding="utf-8", errors="replace")
        if stage_name == "minimization":
            assert_minimization_converged(engine_log_text)
        performance = parse_gromacs_performance(engine_log_text)
        if not performance:
            performance = parse_gromacs_performance(console)
        ledger.mark_completed(stage_name, required, performance=performance)
    except Exception as exc:
        ledger.mark_failed(stage_name, str(exc))
        raise
    return output_coordinates, completed_checkpoint, required


def _validate_outputs(
    *,
    output_dir: Path,
    trajectory: Path,
    energy: Path,
    gmx_binary: str,
    ledger: StageLedger,
) -> Path:
    analysis_dir = output_dir / "analysis"
    report = analysis_dir / "gromacs_check.txt"
    required = [report]
    if ledger.is_complete("analysis", required):
        return report
    analysis_dir.mkdir(parents=True, exist_ok=True)
    ledger.mark_running("analysis", [gmx_binary, "check"])
    try:
        trajectory_report = _run_command(
            [gmx_binary, "check", "-f", str(trajectory)],
            cwd=analysis_dir,
            log_path=analysis_dir / "trajectory_check.command.log",
        )
        energy_report = _run_command(
            [gmx_binary, "check", "-e", str(energy)],
            cwd=analysis_dir,
            log_path=analysis_dir / "energy_check.command.log",
        )
        report.write_text(
            "[trajectory]\n" + trajectory_report + "\n[energy]\n" + energy_report,
            encoding="utf-8",
        )
        ledger.mark_completed("analysis", required)
    except Exception as exc:
        ledger.mark_failed("analysis", str(exc))
        raise
    return report


_TRAJECTORY_FRAME = re.compile(
    r"^Reading frame\s+(?P<frame>\d+)\s+time\s+(?P<time>-?\d+(?:\.\d+)?)\s*$",
    re.MULTILINE,
)


def _build_trajectory_frame_map(
    *, trajectory_report: str, trajectory_sha256: str, replica_index: int,
    timestep_fs: float,
) -> dict[str, Any]:
    if timestep_fs <= 0:
        raise ValueError("production timestep_fs must be positive")
    frames: list[dict[str, Any]] = []
    previous_time = -1.0
    for display_frame, match in enumerate(_TRAJECTORY_FRAME.finditer(trajectory_report)):
        source_frame = int(match.group("frame"))
        time_ps = float(match.group("time"))
        if source_frame != display_frame or time_ps < 0 or (display_frame and time_ps <= previous_time):
            raise ValueError("GROMACS trajectory frame receipt is noncontiguous or nonmonotonic")
        raw_step = time_ps * 1000.0 / timestep_fs
        step = round(raw_step)
        if abs(raw_step - step) > 1e-6:
            raise ValueError("GROMACS trajectory time does not map to an exact integration step")
        frames.append({
            "display_frame": display_frame,
            "source_frame": source_frame,
            "time_ps": time_ps,
            "step": step,
        })
        previous_time = time_ps
    if not frames:
        raise ValueError("GROMACS trajectory frame receipt is empty")
    return {
        "schema": "bms.md.trajectory-frame-map.v1",
        "replica": replica_index,
        "trajectory_sha256": trajectory_sha256,
        "frames": frames,
    }


def run_gromacs_job(
    config_path: Path,
    output_dir: Path,
    *,
    replica_index: int = 0,
    gmx_binary: str = "gmx",
    _prepared_config: Mapping[str, Any] | None = None,
    preparation_bundle: Path | None = None,
    resume_checkpoint: Path | None = None,
) -> Path:
    config_path = Path(config_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if resume_checkpoint is not None:
        resume_checkpoint = Path(resume_checkpoint).expanduser().resolve(strict=True)
        try:
            resume_relative = resume_checkpoint.relative_to(output_dir)
        except ValueError as exc:
            raise ValueError("resume checkpoint must belong to the resumed replica output") from exc
        allowed_stages = {"minimization", "nvt", "npt", "production"}
        canonical_stage_checkpoint = (
            len(resume_relative.parts) == 2
            and resume_relative.parts[0] in allowed_stages
            and resume_relative.name == f"{resume_relative.parts[0]}.cpt"
        )
        snapshot_bytes: bytes | None = None
        immutable_snapshot = False
        if (
            len(resume_relative.parts) == 3
            and resume_relative.parts[0] == ".bms-checkpoints"
            and re.fullmatch(r"[0-9a-f]{64}\.cpt", resume_relative.name) is not None
        ):
            snapshot_bytes = resume_checkpoint.read_bytes()
            immutable_snapshot = hashlib.sha256(snapshot_bytes).hexdigest() == resume_checkpoint.stem
        if not canonical_stage_checkpoint and not immutable_snapshot:
            raise ValueError(
                "resume checkpoint path does not match a canonical stage checkpoint or immutable snapshot"
            )
        if immutable_snapshot:
            assert snapshot_bytes is not None
            canonical_resume = output_dir / "production" / "production.cpt"
            canonical_resume.parent.mkdir(parents=True, exist_ok=True)
            previous_mode = canonical_resume.stat().st_mode & 0o777 if canonical_resume.exists() else 0o664
            temporary_resume = canonical_resume.with_suffix(".cpt.resume.tmp")
            temporary_resume.write_bytes(snapshot_bytes)
            os.chmod(temporary_resume, previous_mode)
            os.replace(temporary_resume, canonical_resume)
    config = dict(_prepared_config) if _prepared_config is not None else prepare_verified_worker_inputs(
        config_path,
        output_dir / ".worker_inputs",
    )
    if config["engine"] != "gromacs":
        raise ValueError("run_gromacs_job requires engine=gromacs")
    if replica_index < 0 or replica_index >= config["replicas"]:
        raise ValueError("replica_index is outside configured replica range")
    version_output = _run_command(
        [gmx_binary, "mdrun", "-version"],
        cwd=output_dir,
        log_path=output_dir / "gromacs_version.txt",
    )
    if config["execution"]["gpu_offload"] != "none":
        assert_cuda_enabled(version_output)
    normalized_config = output_dir / "job.normalized.json"
    _atomic_json(normalized_config, config)
    ledger = StageLedger(output_dir / "stage_state.json")

    if config.get("schema") == "bms.md.job.v2":
        if preparation_bundle is None:
            raise ValueError("bms.md.job.v2 requires an immutable preparation bundle")
        coordinates, topology, _ = _consume_preparation_bundle(
            config, preparation_bundle, output_dir, ledger,
        )
    else:
        coordinates, topology, _ = _prepare_system(
            config,
            config_path=config_path,
            output_dir=output_dir,
            gmx_binary=gmx_binary,
            ledger=ledger,
        )
    restraint_reference = coordinates
    previous_checkpoint: Path | None = None
    stage_artifacts: dict[str, list[Path]] = {}
    for stage_name in ("minimization", "nvt", "npt", "production"):
        coordinates, previous_checkpoint, artifacts = _run_stage(
            stage_name,
            config,
            replica_index=replica_index,
            output_dir=output_dir,
            system_dir=output_dir / "system",
            coordinates=coordinates,
            topology=topology,
            previous_checkpoint=previous_checkpoint,
            restraint_reference=restraint_reference,
            gmx_binary=gmx_binary,
            ledger=ledger,
        )
        stage_artifacts[stage_name] = artifacts

    production = output_dir / "production" / "production"
    trajectory = production.with_suffix(".xtc")
    energy = production.with_suffix(".edr")
    checkpoint = production.with_suffix(".cpt")
    validation = _validate_outputs(
        output_dir=output_dir,
        trajectory=trajectory,
        energy=energy,
        gmx_binary=gmx_binary,
        ledger=ledger,
    )
    trajectory_sha256 = hashlib.sha256(trajectory.read_bytes()).hexdigest()
    production_config = config["stages"]["production"]
    timestep_fs = float(production_config.get("timestep_fs", 2.0))
    trajectory_frame_map = output_dir / "analysis" / "trajectory-frame-map.json"
    frame_map_payload = _build_trajectory_frame_map(
        trajectory_report=(output_dir / "analysis" / "trajectory_check.command.log").read_text(
            encoding="utf-8", errors="replace",
        ),
        trajectory_sha256=trajectory_sha256,
        replica_index=replica_index,
        timestep_fs=timestep_fs,
    )
    _atomic_json(trajectory_frame_map, frame_map_payload)
    representative_structure = output_dir / "production" / "production-final.pdb"
    (output_dir / "analysis").mkdir(parents=True, exist_ok=True)
    representative_is_valid = False
    previous_manifest_path = output_dir / "manifest.json"
    if representative_structure.is_file() and previous_manifest_path.is_file():
        try:
            previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
            previous_record = previous_manifest["artifacts"]["representative_structure"]
            representative_is_valid = (
                previous_record["bytes"] == representative_structure.stat().st_size
                and previous_record["sha256"] == hashlib.sha256(representative_structure.read_bytes()).hexdigest()
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            representative_is_valid = False
    if not representative_is_valid:
        _run_command(
            [gmx_binary, "editconf", "-f", str(coordinates), "-o", str(representative_structure)],
            cwd=output_dir / "production",
            log_path=output_dir / "analysis" / "representative_structure.command.log",
        )
    atom_order_manifest = output_dir / "analysis" / "atom-order-manifest.json"
    _, atom_order_identity = write_atom_order_manifest(coordinates, atom_order_manifest)
    manifest = build_run_manifest(
        output_dir=output_dir,
        job_config=config,
        replica_index=replica_index,
        engine_version=_version_summary(version_output),
        platform="CUDA" if config["execution"]["gpu_offload"] != "none" else "CPU",
        artifacts={
            "normalized_config": normalized_config,
            "topology": topology,
            "coordinates": coordinates,
            "run_input": production.with_suffix(".tpr"),
            "trajectory": trajectory,
            "trajectory_frame_map": trajectory_frame_map,
            "atom_order_manifest": atom_order_manifest,
            "representative_structure": representative_structure,
            "checkpoint": checkpoint,
            "energy": energy,
            "engine_log": production.with_suffix(".log"),
            "validation": validation,
            "stage_ledger": output_dir / "stage_state.json",
            "engine_version": output_dir / "gromacs_version.txt",
        },
        stages=_portable_stage_snapshot(ledger.snapshot()["stages"], output_dir),
    )
    manifest["status"] = "completed"
    normalized_version_output = " ".join(version_output.lower().split())
    manifest["engine"]["cuda_enabled"] = "gpu support: cuda" in normalized_version_output
    manifest["engine"]["binary"] = gmx_binary
    manifest["replica_seed"] = int(config["random_seed"]) + replica_index
    manifest["artifacts"]["coordinates"].update({
        "semantic_role": "analysis_topology", "atom_order_identity": atom_order_identity,
    })
    manifest["artifacts"]["trajectory"].update({
        "semantic_role": "analysis_trajectory", "atom_order_identity": atom_order_identity,
    })
    manifest["artifacts"]["trajectory_frame_map"].update({
        "semantic_role": "trajectory_frame_map",
        "source_trajectory_sha256": trajectory_sha256,
    })
    manifest["artifacts"]["atom_order_manifest"].update({
        "semantic_role": "atom_order_manifest", "atom_order_identity": atom_order_identity,
    })
    final_frame = frame_map_payload["frames"][-1]
    manifest["artifacts"]["representative_structure"].update({
        "semantic_role": "representative_structure",
        "selection_method": "completed_production_final_coordinates",
        "source_frame": final_frame["source_frame"],
        "time_ps": final_frame["time_ps"],
        "source_trajectory_sha256": manifest["artifacts"]["trajectory"]["sha256"],
    })
    manifest_path = output_dir / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path
