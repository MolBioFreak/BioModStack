from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from .contract import build_run_manifest, load_verified_job_config
from .cuda_contract import assert_single_cuda_device
from .runner import StageLedger, replica_seed


OPENMM_CUDA_UNAVAILABLE = "MD_OPENMM_CUDA_UNAVAILABLE"


class OpenMMCapabilityError(RuntimeError):
    code = OPENMM_CUDA_UNAVAILABLE


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _resolve(raw: str, config_path: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"OpenMM input file does not exist: {path}")
    return path


def _copy_prepared_inputs(coordinates: Path, topology: Path, output_dir: Path) -> tuple[Path, Path]:
    system_dir = output_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    for candidate in topology.parent.iterdir():
        if candidate.is_file() and candidate.suffix.lower() in {".top", ".itp"}:
            shutil.copy2(candidate, system_dir / candidate.name)
    copied_topology = system_dir / topology.name
    copied_coordinates = system_dir / coordinates.name
    if not copied_topology.is_file():
        shutil.copy2(topology, copied_topology)
    shutil.copy2(coordinates, copied_coordinates)
    return copied_coordinates, copied_topology


def _require_openmm_cuda() -> tuple[Any, Any, Any, Any, Any, Any, str]:
    try:
        import openmm
        from openmm import LangevinMiddleIntegrator, MonteCarloBarostat, Platform
        from openmm import app, unit
    except ImportError as exc:
        raise OpenMMCapabilityError(
            f"{OPENMM_CUDA_UNAVAILABLE}: OpenMM is not installed in this engine image"
        ) from exc
    names = [Platform.getPlatform(index).getName() for index in range(Platform.getNumPlatforms())]
    if "CUDA" not in names:
        raise OpenMMCapabilityError(
            f"{OPENMM_CUDA_UNAVAILABLE}: OpenMM CUDA platform is not registered; available={names}"
        )
    return openmm, app, unit, Platform, LangevinMiddleIntegrator, MonteCarloBarostat, str(openmm.__version__)


def run_openmm_job(config_path: Path, output_dir: Path, *, replica_index: int = 0) -> Path:
    config_path = Path(config_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    config = load_verified_job_config(config_path)
    if config["engine"] != "openmm":
        raise ValueError("run_openmm_job requires engine=openmm")
    if replica_index < 0 or replica_index >= config["replicas"]:
        raise ValueError("replica_index is outside configured replica range")
    allocation = assert_single_cuda_device(config)
    openmm, app, unit, Platform, Integrator, Barostat, version = _require_openmm_cuda()

    coordinates = _resolve(str(config["input"]["coordinates"]), config_path)
    topology = _resolve(str(config["input"]["topology"]), config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    coordinates, topology = _copy_prepared_inputs(coordinates, topology, output_dir)
    normalized = output_dir / "job.normalized.json"
    _atomic_json(normalized, config)

    gro = app.GromacsGroFile(str(coordinates))
    include_dir = Path(os.environ.get("OPENMM_GROMACS_INCLUDE_DIR", "/opt/conda/share/gromacs/top"))
    if not include_dir.is_dir():
        raise OpenMMCapabilityError(
            f"{OPENMM_CUDA_UNAVAILABLE}: GROMACS topology include directory is unavailable: {include_dir}"
        )
    top = app.GromacsTopFile(
        str(topology),
        periodicBoxVectors=gro.getPeriodicBoxVectors(),
        includeDir=include_dir,
    )
    production = config["stages"]["production"]
    temperature = float(production["temperature_k"])
    pressure = float(production["pressure_bar"])
    system = top.createSystem(
        nonbondedMethod=app.PME,
        constraints=app.HBonds,
        rigidWater=True,
    )
    system.addForce(Barostat(pressure * unit.bar, temperature * unit.kelvin, 25))
    integrator = Integrator(temperature * unit.kelvin, 1.0 / unit.picosecond, 0.002 * unit.picoseconds)
    integrator.setRandomNumberSeed(replica_seed(config["random_seed"], replica_index))
    platform = Platform.getPlatformByName("CUDA")
    simulation = app.Simulation(
        top.topology,
        system,
        integrator,
        platform,
        {"DeviceIndex": "0", "Precision": "mixed"},
    )

    run_dir = output_dir / "production"
    run_dir.mkdir(parents=True, exist_ok=True)
    trajectory = run_dir / "production.dcd"
    state_data = run_dir / "production.csv"
    checkpoint = run_dir / "production.chk"
    state_xml = run_dir / "production-state.xml"
    final_coordinates = run_dir / "production-final.pdb"
    interval = max(1, int(production["trajectory_interval_steps"]))
    energy_interval = max(1, int(production["energy_interval_steps"]))
    simulation.reporters.append(app.DCDReporter(str(trajectory), interval, append=checkpoint.is_file()))
    simulation.reporters.append(
        app.StateDataReporter(
            str(state_data),
            energy_interval,
            step=True,
            time=True,
            potentialEnergy=True,
            temperature=True,
            volume=True,
            speed=True,
            append=checkpoint.is_file(),
        )
    )
    simulation.reporters.append(app.CheckpointReporter(str(checkpoint), energy_interval))

    if checkpoint.is_file():
        simulation.loadCheckpoint(str(checkpoint))
    else:
        simulation.context.setPositions(gro.positions)
        simulation.context.setVelocitiesToTemperature(
            temperature * unit.kelvin,
            replica_seed(config["random_seed"], replica_index),
        )
    target_steps = int(production["steps"])
    remaining = max(0, target_steps - int(simulation.currentStep))
    ledger = StageLedger(output_dir / "stage_state.json")
    ledger.mark_running("production", ["openmm", "CUDA", "production", str(remaining)])
    try:
        if remaining:
            simulation.step(remaining)
        simulation.saveCheckpoint(str(checkpoint))
        simulation.saveState(str(state_xml))
        state = simulation.context.getState(getPositions=True)
        with final_coordinates.open("w", encoding="utf-8") as handle:
            app.PDBFile.writeFile(top.topology, state.getPositions(), handle)
        artifacts = [trajectory, state_data, checkpoint, state_xml, final_coordinates]
        ledger.mark_completed("production", artifacts, performance={"completed_steps": target_steps})
    except Exception as exc:
        ledger.mark_failed("production", str(exc))
        raise

    manifest = build_run_manifest(
        output_dir=output_dir,
        job_config=config,
        replica_index=replica_index,
        engine_version=version,
        platform="CUDA",
        artifacts={
            "normalized_config": normalized,
            "topology": topology,
            "input_coordinates": coordinates,
            "trajectory": trajectory,
            "checkpoint": checkpoint,
            "state": state_xml,
            "final_coordinates": final_coordinates,
            "state_data": state_data,
            "stage_ledger": output_dir / "stage_state.json",
        },
        stages=ledger.snapshot()["stages"],
    )
    manifest["status"] = "completed"
    manifest["engine"].update({"cuda_enabled": True, "precision": "mixed", "allocation": allocation})
    manifest["replica_seed"] = replica_seed(config["random_seed"], replica_index)
    manifest_path = output_dir / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path
