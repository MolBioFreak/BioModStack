from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contract import normalize_job_config

STAGE_LEDGER_SCHEMA = "bms.md.stage-ledger.v1"
_GROMACS_MAX_SEED = 2_147_483_647


def replica_seed(base_seed: int, replica_index: int) -> int:
    if replica_index < 0:
        raise ValueError("replica_index must be >= 0")
    return ((int(base_seed) + replica_index - 1) % _GROMACS_MAX_SEED) + 1


def _mdp_lines(values: Sequence[tuple[str, Any]]) -> str:
    return "\n".join(f"{key} = {value}" for key, value in values) + "\n"


def render_mdp(stage_name: str, job_config: Mapping[str, Any], replica_index: int) -> str:
    config = normalize_job_config(job_config)
    if replica_index < 0 or replica_index >= config["replicas"]:
        raise ValueError("replica_index is outside configured replica range")
    if stage_name not in config["stages"]:
        raise ValueError(f"unsupported MD stage: {stage_name}")
    stage = config["stages"][stage_name]

    shared: list[tuple[str, Any]] = [
        ("nsteps", stage["steps"]),
        ("cutoff-scheme", "Verlet"),
        ("nstlist", 20),
        ("rlist", 1.0),
        ("coulombtype", "PME"),
        ("rcoulomb", 1.0),
        ("vdwtype", "Cut-off"),
        ("rvdw", 1.0),
        ("pbc", "xyz"),
    ]
    if stage_name == "minimization":
        return _mdp_lines(
            [
                ("integrator", "steep"),
                *shared,
                ("emtol", stage["force_tolerance_kj_mol_nm"]),
                ("emstep", 0.01),
                ("constraints", "none"),
                ("nstenergy", 100),
                ("nstlog", 100),
            ]
        )

    temperature = stage["temperature_k"]
    values: list[tuple[str, Any]] = [
        ("integrator", "md"),
        ("dt", 0.002),
        *shared,
        ("constraints", "h-bonds"),
        ("constraint_algorithm", "lincs"),
        ("lincs_iter", 1),
        ("lincs_order", 4),
        ("DispCorr", "EnerPres"),
        ("tcoupl", "V-rescale"),
        ("tc-grps", "System"),
        ("tau_t", 1.0),
        ("ref_t", temperature),
    ]
    if stage_name == "nvt":
        values.extend(
            [
                ("define", "-DPOSRES"),
                ("continuation", "no"),
                ("gen_vel", "yes"),
                ("gen_temp", temperature),
                ("gen_seed", replica_seed(config["random_seed"], replica_index)),
                ("pcoupl", "no"),
                ("nstxout-compressed", max(1, stage["steps"] // 10)),
                ("nstenergy", max(1, stage["steps"] // 100)),
                ("nstlog", max(1, stage["steps"] // 100)),
            ]
        )
    elif stage_name == "npt":
        values.extend(
            [
                ("define", "-DPOSRES"),
                # Pressure coupling with absolute restraints is a scientific
                # warning in GROMACS and must not be bypassed with -maxwarn.
                # Scale the restraint reference with the box center of mass.
                ("refcoord_scaling", "com"),
                ("continuation", "yes"),
                ("gen_vel", "no"),
                ("pcoupl", "C-rescale"),
                ("pcoupltype", "isotropic"),
                ("tau_p", 5.0),
                ("ref_p", stage["pressure_bar"]),
                ("compressibility", "4.5e-5"),
                ("nstxout-compressed", max(1, stage["steps"] // 10)),
                ("nstenergy", max(1, stage["steps"] // 100)),
                ("nstlog", max(1, stage["steps"] // 100)),
            ]
        )
    elif stage_name == "production":
        values.extend(
            [
                ("continuation", "yes"),
                ("gen_vel", "no"),
                ("pcoupl", "Parrinello-Rahman"),
                ("pcoupltype", "isotropic"),
                ("tau_p", 5.0),
                ("ref_p", stage["pressure_bar"]),
                ("compressibility", "4.5e-5"),
                ("nstxout", 0),
                ("nstvout", 0),
                ("nstfout", 0),
                ("nstxout-compressed", stage["trajectory_interval_steps"]),
                ("compressed-x-grps", "System"),
                ("nstenergy", stage["energy_interval_steps"]),
                ("nstlog", stage["energy_interval_steps"]),
            ]
        )
    else:
        raise ValueError(f"unsupported MD stage: {stage_name}")
    return _mdp_lines(values)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StageLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": STAGE_LEDGER_SCHEMA, "stages": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != STAGE_LEDGER_SCHEMA or not isinstance(data.get("stages"), dict):
            raise RuntimeError(f"invalid MD stage ledger: {self.path}")
        return data

    def _write(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def mark_running(self, stage_name: str, command: Sequence[str]) -> None:
        data = self._read()
        data["stages"][stage_name] = {
            "status": "running",
            "started_at": _utc_now(),
            "command": list(command),
        }
        self._write(data)

    def mark_completed(
        self,
        stage_name: str,
        artifacts: Sequence[Path],
        *,
        performance: Mapping[str, Any] | None = None,
    ) -> None:
        records = []
        for artifact in artifacts:
            path = Path(artifact)
            if not path.is_file():
                raise RuntimeError(f"cannot complete {stage_name}; artifact is missing: {path}")
            records.append({"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": _sha256(path)})
        data = self._read()
        previous = data["stages"].get(stage_name, {})
        data["stages"][stage_name] = {
            **previous,
            "status": "completed",
            "completed_at": _utc_now(),
            "artifacts": records,
            "performance": dict(performance or {}),
        }
        self._write(data)

    def mark_failed(self, stage_name: str, error: str) -> None:
        data = self._read()
        previous = data["stages"].get(stage_name, {})
        data["stages"][stage_name] = {
            **previous,
            "status": "failed",
            "failed_at": _utc_now(),
            "error": str(error),
        }
        self._write(data)

    def is_complete(self, stage_name: str, required_artifacts: Sequence[Path]) -> bool:
        stage = self._read()["stages"].get(stage_name, {})
        if stage.get("status") != "completed":
            return False
        recorded = {entry.get("path"): entry for entry in stage.get("artifacts", [])}
        for artifact in required_artifacts:
            path = Path(artifact)
            entry = recorded.get(str(path.resolve()))
            if not path.is_file() or not entry:
                return False
            if path.stat().st_size != entry.get("bytes") or _sha256(path) != entry.get("sha256"):
                return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return self._read()


def assert_minimization_converged(log_text: str) -> None:
    normalized = " ".join(str(log_text).lower().split())
    if "did not converge to fmax" in normalized:
        raise RuntimeError("energy minimization did not converge to the configured Fmax")
    if "converged to fmax" not in normalized:
        raise RuntimeError("energy minimization convergence could not be verified from the GROMACS log")


def parse_gromacs_performance(log_text: str) -> dict[str, float]:
    performance = re.search(r"^Performance:\s+([0-9.]+)\s+([0-9.]+)", log_text, re.MULTILINE)
    wall = re.search(r"^\s*Time:\s+[0-9.]+\s+([0-9.]+)\s+[0-9.]+", log_text, re.MULTILINE)
    result: dict[str, float] = {}
    if performance:
        result["ns_per_day"] = float(performance.group(1))
        result["hours_per_ns"] = float(performance.group(2))
    if wall:
        result["wall_seconds"] = float(wall.group(1))
    return result
