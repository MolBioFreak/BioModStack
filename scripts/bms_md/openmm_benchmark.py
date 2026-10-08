from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import openmm
from openmm import app, unit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark OpenMM on a prepared GROMACS system")
    parser.add_argument("--gro", type=Path, required=True)
    parser.add_argument("--top", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=50_000)
    parser.add_argument("--warmup-steps", type=int, default=1_000)
    parser.add_argument("--device-index", default="0")
    parser.add_argument("--precision", choices=("single", "mixed", "double"), default="mixed")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.steps < 1 or args.warmup_steps < 0:
        raise SystemExit("steps must be positive and warmup steps non-negative")

    gro = app.GromacsGroFile(str(args.gro))
    top = app.GromacsTopFile(
        str(args.top),
        periodicBoxVectors=gro.getPeriodicBoxVectors(),
        includeDir=str(args.top.parent),
    )
    system = top.createSystem(
        nonbondedMethod=app.PME,
        nonbondedCutoff=0.9 * unit.nanometer,
        constraints=app.HBonds,
        rigidWater=True,
        ewaldErrorTolerance=5e-4,
    )
    integrator = openmm.LangevinMiddleIntegrator(
        300 * unit.kelvin,
        1 / unit.picosecond,
        2 * unit.femtoseconds,
    )
    platform = openmm.Platform.getPlatformByName("CUDA")
    properties = {
        "DeviceIndex": str(args.device_index),
        "Precision": args.precision,
        "UseCpuPme": "false",
    }
    simulation = app.Simulation(top.topology, system, integrator, platform, properties)
    simulation.context.setPositions(gro.getPositions())
    simulation.context.setVelocitiesToTemperature(300 * unit.kelvin, 20260717)

    if args.warmup_steps:
        simulation.step(args.warmup_steps)
    started = time.perf_counter()
    simulation.step(args.steps)
    elapsed_seconds = time.perf_counter() - started

    simulated_ns = args.steps * 0.002 / 1000.0
    result = {
        "engine": "openmm",
        "openmm_version": openmm.__version__,
        "platform": platform.getName(),
        "platform_properties": {
            name: platform.getPropertyValue(simulation.context, name)
            for name in platform.getPropertyNames()
        },
        "atoms": system.getNumParticles(),
        "steps": args.steps,
        "timestep_fs": 2.0,
        "elapsed_seconds": elapsed_seconds,
        "ns_per_day": simulated_ns * 86400.0 / elapsed_seconds,
        "hours_per_ns": elapsed_seconds / simulated_ns / 3600.0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
