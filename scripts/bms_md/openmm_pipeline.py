from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Mapping

from .contract import build_run_manifest, prepare_verified_worker_inputs, write_atom_order_manifest
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


def run_openmm_job(
    config_path: Path,
    output_dir: Path,
    *,
    replica_index: int = 0,
    _prepared_config: Mapping[str, Any] | None = None,
    preparation_bundle: Path | None = None,
) -> Path:
    config_path = Path(config_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = dict(_prepared_config) if _prepared_config is not None else prepare_verified_worker_inputs(
        config_path,
        output_dir / ".worker_inputs",
    )
    if config["engine"] != "openmm":
        raise ValueError("run_openmm_job requires engine=openmm")
    if replica_index < 0 or replica_index >= config["replicas"]:
        raise ValueError("replica_index is outside configured replica range")
    allocation = assert_single_cuda_device(config)
    openmm, app, unit, Platform, Integrator, Barostat, version = _require_openmm_cuda()

    preparation_artifacts: dict[str, Path] = {}
    if config.get("schema") == "bms.md.job.v2":
        if preparation_bundle is None:
            raise ValueError("bms.md.job.v2 requires an immutable preparation bundle")
        from .chemistry.prepare import verify_preparation_bundle
        chemistry = config.get("chemistry") or {}
        preparation_manifest = verify_preparation_bundle(
            preparation_bundle,
            expected_profile_id=chemistry.get("profile_id"),
            expected_profile_sha256=chemistry.get("profile_sha256"),
        )
        private_bundle = output_dir / "preparation"
        if not private_bundle.exists():
            private_bundle.mkdir()
            for record in preparation_manifest["files"]:
                shutil.copy2(Path(preparation_bundle) / record["path"], private_bundle / record["path"])
            shutil.copy2(Path(preparation_bundle) / "preparation_manifest.json", private_bundle / "preparation_manifest.json")
        consumed = verify_preparation_bundle(
            private_bundle,
            expected_profile_id=chemistry.get("profile_id"),
            expected_profile_sha256=chemistry.get("profile_sha256"),
        )
        if consumed["bundle_sha256"] != preparation_manifest["bundle_sha256"]:
            raise ValueError("OpenMM private preparation bundle identity mismatch")
        preparation_artifacts = {
            f"preparation_{index}": private_bundle / record["path"]
            for index, record in enumerate(consumed["files"])
            if record["path"] not in {"system.gro", "system.top"}
        }
        preparation_artifacts["preparation_manifest"] = private_bundle / "preparation_manifest.json"
        coordinates = private_bundle / "system.gro"
        topology = private_bundle / "system.top"
        closure: Mapping[str, Any] | None = {"root": str(private_bundle), "runtime_includes": []}
    else:
        coordinates = _resolve(str(config["input"]["coordinates"]), config_path)
        topology = _resolve(str(config["input"]["topology"]), config_path)
        closure = config["input"].get("topology_closure")
    if not isinstance(closure, Mapping):
        raise ValueError("OpenMM requires a verified private topology closure")
    private_include_root = Path(str(closure.get("root") or "")).resolve()
    if private_include_root != topology.parent or not private_include_root.is_dir():
        raise ValueError("OpenMM topology is outside its verified private include root")
    normalized = output_dir / "job.normalized.json"
    _atomic_json(normalized, config)

    gro = app.GromacsGroFile(str(coordinates))
    runtime_includes = closure.get("runtime_includes")
    include_dir = private_include_root
    if isinstance(runtime_includes, list) and runtime_includes:
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
    timestep_ps = float(production["timestep_fs"]) / 1000.0
    integrator = Integrator(temperature * unit.kelvin, 1.0 / unit.picosecond, timestep_ps * unit.picoseconds)
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
    checkpoint_seconds = float(production["checkpoint_interval_minutes"]) * 60.0

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
        checkpoint_at = time.monotonic() + checkpoint_seconds
        while remaining:
            # Observe wall-clock checkpoint cadence at bounded reporting steps;
            # energy reporting frequency is not the requested checkpoint period.
            steps = min(remaining, energy_interval)
            simulation.step(steps)
            remaining -= steps
            if time.monotonic() >= checkpoint_at:
                simulation.saveCheckpoint(str(checkpoint))
                checkpoint_at = time.monotonic() + checkpoint_seconds
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

    atom_order_manifest = output_dir / "analysis" / "atom-order-manifest.json"
    _, atom_order_identity = write_atom_order_manifest(final_coordinates, atom_order_manifest)
    manifest = build_run_manifest(
        output_dir=output_dir,
        job_config=config,
        replica_index=replica_index,
        engine_version=version,
        platform="CUDA",
        artifacts={
            **preparation_artifacts,
            "normalized_config": normalized,
            "topology": topology,
            "input_coordinates": coordinates,
            "trajectory": trajectory,
            "atom_order_manifest": atom_order_manifest,
            "checkpoint": checkpoint,
            "state": state_xml,
            "final_coordinates": final_coordinates,
            "representative_structure": final_coordinates,
            "state_data": state_data,
            "stage_ledger": output_dir / "stage_state.json",
        },
        stages=ledger.snapshot()["stages"],
    )
    manifest["status"] = "completed"
    manifest["engine"].update({"cuda_enabled": True, "precision": "mixed", "allocation": allocation})
    manifest["replica_seed"] = replica_seed(config["random_seed"], replica_index)
    manifest["artifacts"]["final_coordinates"].update({
        "semantic_role": "analysis_topology", "atom_order_identity": atom_order_identity,
    })
    manifest["artifacts"]["trajectory"].update({
        "semantic_role": "analysis_trajectory", "atom_order_identity": atom_order_identity,
    })
    manifest["artifacts"]["atom_order_manifest"].update({
        "semantic_role": "atom_order_manifest", "atom_order_identity": atom_order_identity,
    })
    manifest["artifacts"]["representative_structure"].update({
        "semantic_role": "representative_structure",
        "selection_method": "completed_production_final_coordinates",
        "source_frame": target_steps // interval - 1 if target_steps % interval == 0 else None,
        "time_ps": target_steps * timestep_ps,
        "source_trajectory_sha256": manifest["artifacts"]["trajectory"]["sha256"],
    })
    manifest_path = output_dir / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return manifest_path
