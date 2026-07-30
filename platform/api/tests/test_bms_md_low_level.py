from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from scripts.bms_md.aggregate import aggregate_manifests
from scripts.bms_md.contract import MD_JOB_SCHEMA, build_run_manifest, normalize_job_config
from scripts.bms_md.cuda_contract import CudaContractError, assert_single_cuda_device
from scripts.bms_md.engine_adapters import EngineAdapterError, run_md_replica
from scripts.bms_md.gromacs import assert_cuda_enabled, build_mdrun_command
from scripts.bms_md.gromacs_pipeline import run_gromacs_job
from scripts.bms_md.runner import (
    StageLedger,
    assert_minimization_converged,
    parse_gromacs_performance,
    render_mdp,
    replica_seed,
)
from services.md.chemistry_catalog import ChemistryCatalog, RuntimeProbeResult
from services.md.launch_contract import materialize_md_job_spec


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
CATALOG_DIR = API_ROOT / "config" / "md_chemistry_profiles"
ONE_AKI = API_ROOT / "tests" / "fixtures" / "md" / "1AKI.pdb"
GROMACS_SIF_SHA256 = "97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b"
OPENMM_RUNTIME_IDENTITY = {
    "runtime_id": "openmm-md-8.5.2",
    "runtime_version": "8.5.2+gromacs-2025.3",
    "sif_sha256": "d" * 64,
    "asset_ids": ["amber99sb-ildn.ff"],
}


def _catalog() -> ChemistryCatalog:
    def probe() -> RuntimeProbeResult:
        return RuntimeProbeResult(
            runtime_id="gromacs-2025.3",
            runtime_version="2025.3",
            available=True,
            asset_ids=frozenset({"amber99sb-ildn.ff"}),
            checked_at="2026-07-19T03:30:00Z",
            sif_sha256=GROMACS_SIF_SHA256,
        )

    return ChemistryCatalog(config_dir=CATALOG_DIR, probe=probe)


def _stages() -> dict[str, dict[str, Any]]:
    return {
        "minimization": {
            "enabled": True,
            "steps": 50_000,
            "force_tolerance_kj_mol_nm": 1000.0,
        },
        "nvt": {"enabled": True, "steps": 50_000, "temperature_k": 300.0},
        "npt": {
            "enabled": True,
            "steps": 50_000,
            "temperature_k": 300.0,
            "pressure_bar": 1.0,
        },
        "production": {
            "enabled": True,
            "steps": 5_000,
            "timestep_fs": 2.0,
            "temperature_k": 300.0,
            "pressure_bar": 1.0,
            "checkpoint_interval_minutes": 15.0,
            "trajectory_interval_steps": 500,
            "energy_interval_steps": 100,
        },
    }


def _preparation() -> dict[str, Any]:
    return {
        "chemistry_assurance": "external_unreviewed",
        "force_field": "external",
        "water_model": "external",
        "box_type": "dodecahedron",
        "padding_nm": 1.0,
        "salt_molar": 0.15,
        "positive_ion": "NA",
        "negative_ion": "CL",
        "solvent_group": "SOL",
        "solvent_coordinates": "spc216.gro",
        "neutralize": True,
    }


def _execution() -> dict[str, Any]:
    return {
        "gpu_id": "0",
        "scheduler_gpu_id": "GPU-cloud-17",
        "ntmpi": 1,
        "ntomp": 8,
        "gpu_offload": "full",
        "pin": "on",
    }


def _materialized_gromacs(tmp_path: Path, *, job_id: str = "gromacs-low-level") -> tuple[Path, dict[str, Any]]:
    catalog = _catalog()
    profile = catalog.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    assert profile is not None
    preparation = {
        **_preparation(),
        "chemistry_assurance": "smoke_fixture",
        "chemistry_profile_id": profile["id"],
        "chemistry_profile_sha256": profile["profile_sha256"],
        "chemistry_profile_scope": profile["scientific_validation"]["scope"]["launch_scope"],
        "force_field": "amber99sb-ildn",
        "water_model": "tip3p",
    }
    spec = {
        "schema": MD_JOB_SCHEMA,
        "job_id": "replaced-by-materializer",
        "engine": "gromacs",
        "replicas": 1,
        "random_seed": 20260717,
        "input": {"structure": "uploads/md/1AKI.pdb"},
        "preparation": preparation,
        "stages": _stages(),
        "execution": _execution(),
    }
    materialized = materialize_md_job_spec(
        params={"md_job_spec": spec},
        job_id=job_id,
        output_dir=tmp_path / "gromacs-contract",
        resolve_runtime_path=lambda _value: str(ONE_AKI),
        chemistry_catalog=catalog,
    )
    config_path = Path(materialized["md_job_config"])
    return config_path, materialized["md_job_spec"]


def _materialized_openmm(
    tmp_path: Path,
    *,
    job_id: str = "openmm-low-level",
    replicas: int = 1,
) -> tuple[Path, dict[str, Any]]:
    source = tmp_path / f"{job_id}-source"
    source.mkdir(parents=True)
    coordinates = source / "system.gro"
    topology = source / "topol.top"
    sidecar = source / "molecule.itp"
    coordinates.write_text("prepared coordinates\n", encoding="utf-8")
    topology.write_text(
        '#include "amber99sb-ildn.ff/forcefield.itp"\n'
        '#include "molecule.itp"\n'
        "[ system ]\nPrepared fixture\n",
        encoding="utf-8",
    )
    sidecar.write_text("[ moleculetype ]\nProtein 3\n", encoding="utf-8")
    stages = _stages()
    for stage_name in ("minimization", "nvt", "npt"):
        stages[stage_name]["enabled"] = False
    spec = {
        "schema": MD_JOB_SCHEMA,
        "job_id": "replaced-by-materializer",
        "engine": "openmm",
        "replicas": replicas,
        "random_seed": 20260717,
        "input": {"coordinates": "prepared.gro", "topology": "topol.top"},
        "preparation": _preparation(),
        "stages": stages,
        "execution": _execution(),
    }
    materialized = materialize_md_job_spec(
        params={"md_job_spec": spec},
        job_id=job_id,
        output_dir=tmp_path / f"{job_id}-contract",
        resolve_runtime_path=lambda value: str(coordinates if value.endswith(".gro") else topology),
        chemistry_catalog=_catalog(),
        runtime_identity_resolver=lambda: OPENMM_RUNTIME_IDENTITY,
    )
    config_path = Path(materialized["md_job_config"])
    config = materialized["md_job_spec"]
    input_config = config["input"]
    closure = input_config["topology_closure"]
    assert closure["runtime_identity"] == OPENMM_RUNTIME_IDENTITY
    assert closure["files"][0]["path"] == "molecule.itp"
    for field in ("coordinates", "topology"):
        snapshot = Path(input_config[field])
        assert input_config[f"{field}_bytes"] == snapshot.stat().st_size
        assert input_config[f"{field}_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    return config_path, config


def _write_run_manifest(
    run_dir: Path,
    config: dict[str, Any],
    *,
    replica_index: int,
    status: str = "completed",
) -> Path:
    run_dir.mkdir(parents=True)
    trajectory = run_dir / "production.xtc"
    checkpoint = run_dir / "production.cpt"
    trajectory.write_bytes(f"trajectory-{replica_index}".encode())
    checkpoint.write_bytes(f"checkpoint-{replica_index}".encode())
    manifest = build_run_manifest(
        output_dir=run_dir,
        job_config=config,
        replica_index=replica_index,
        engine_version="8.5.2",
        platform="CUDA",
        artifacts={"trajectory": trajectory, "checkpoint": checkpoint},
        stages={"production": {"status": status}},
    )
    manifest.update(
        status=status,
        replica_seed=replica_seed(config["random_seed"], replica_index),
    )
    manifest["engine"]["cuda_enabled"] = True
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_aggregate_emits_sorted_replica_provenance_from_final_manifests(tmp_path: Path) -> None:
    _config_path, config = _materialized_openmm(tmp_path, replicas=2)
    replica_1 = _write_run_manifest(tmp_path / "replica-1", config, replica_index=1)
    replica_0 = _write_run_manifest(tmp_path / "replica-0", config, replica_index=0)

    result = aggregate_manifests([replica_1, replica_0])

    assert result == {
        "schema": "bms.md.aggregate.v1",
        "status": "completed",
        "job_id": "openmm-low-level",
        "replicas": [
            {
                "replica_index": 0,
                "replica_seed": 20260717,
                "manifest": "replicas/replica_0/manifest.json",
                "engine": json.loads(replica_0.read_text())["engine"],
                "artifacts": json.loads(replica_0.read_text())["artifacts"],
            },
            {
                "replica_index": 1,
                "replica_seed": 20260718,
                "manifest": "replicas/replica_1/manifest.json",
                "engine": json.loads(replica_1.read_text())["engine"],
                "artifacts": json.loads(replica_1.read_text())["artifacts"],
            },
        ],
        "artifact_classes": ["checkpoints", "replica_manifests", "trajectories"],
    }


def test_aggregate_fails_closed_on_mixed_jobs_or_incomplete_replica(tmp_path: Path) -> None:
    _config_path, config = _materialized_openmm(tmp_path, replicas=2)
    complete = _write_run_manifest(tmp_path / "complete", config, replica_index=0)
    other_job = _write_run_manifest(tmp_path / "other", config, replica_index=1)
    other_payload = json.loads(other_job.read_text())
    other_payload["job_id"] = "other-job"
    other_job.write_text(json.dumps(other_payload), encoding="utf-8")
    incomplete = _write_run_manifest(
        tmp_path / "incomplete",
        config,
        replica_index=1,
        status="failed",
    )

    with pytest.raises(ValueError, match="same job_id"):
        aggregate_manifests([complete, other_job])
    with pytest.raises(ValueError, match="not completed"):
        aggregate_manifests([complete, incomplete])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda config: config.update(engine="namd"), "unsupported MD engine"),
        (lambda config: config.update(replicas=0), "replicas must be >= 1"),
    ],
)
def test_job_contract_rejects_invalid_engine_and_replica(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)
    invalid = copy.deepcopy(config)
    mutation(invalid)

    with pytest.raises(ValueError, match=message):
        normalize_job_config(invalid)


def test_job_contract_rejects_nonpositive_stage_steps(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)
    config["stages"]["production"]["steps"] = 0

    with pytest.raises(ValueError, match=r"production\.steps must be >= 1"):
        normalize_job_config(config)


def test_runner_rejects_unknown_stage(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)

    with pytest.raises(ValueError, match="unsupported MD stage"):
        render_mdp("annealing", config, replica_index=0)


def test_replica_seeds_are_deterministic_unique_and_gromacs_safe() -> None:
    seeds = [replica_seed(2_026_071_700, replica) for replica in range(4)]

    assert seeds == [2026071700, 2026071701, 2026071702, 2026071703]
    assert len(set(seeds)) == 4
    assert all(1 <= seed <= 2_147_483_647 for seed in seeds)


def test_renderer_emits_explicit_minimization_contract(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)

    mdp = render_mdp("minimization", config, replica_index=0)

    assert "integrator = steep" in mdp
    assert "nsteps = 50000" in mdp
    assert "emtol = 1000.0" in mdp
    assert "constraints = none" in mdp
    assert "coulombtype = PME" in mdp


def test_renderer_emits_seeded_restrained_nvt_contract(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)

    mdp = render_mdp("nvt", config, replica_index=0)

    assert "define = -DPOSRES" in mdp
    assert "continuation = no" in mdp
    assert "gen_vel = yes" in mdp
    assert "gen_seed = 20260717" in mdp
    assert "ref_t = 300.0" in mdp
    assert "pcoupl = no" in mdp


def test_renderer_distinguishes_npt_and_production_cadence(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)

    npt = render_mdp("npt", config, replica_index=0)
    production = render_mdp("production", config, replica_index=0)

    assert "pcoupl = C-rescale" in npt
    assert "define = -DPOSRES" in npt
    assert "refcoord_scaling = com" in npt
    assert "pcoupl = Parrinello-Rahman" in production
    assert "define = -DPOSRES" not in production
    assert "nsteps = 5000" in production
    assert "nstxout-compressed = 500" in production
    assert "nstenergy = 100" in production


def test_stage_ledger_skips_only_hash_verified_outputs(tmp_path: Path) -> None:
    ledger = StageLedger(tmp_path / "stage_state.json")
    artifact = tmp_path / "minimization.gro"
    artifact.write_text("coordinates\n", encoding="utf-8")
    ledger.mark_running("minimization", ["gmx", "mdrun"])
    ledger.mark_completed("minimization", [artifact], performance={"ns_per_day": 123.4})

    assert ledger.is_complete("minimization", [artifact]) is True
    artifact.write_text("tampered bytes\n", encoding="utf-8")
    assert ledger.is_complete("minimization", [artifact]) is False
    on_disk = json.loads((tmp_path / "stage_state.json").read_text())
    assert on_disk["schema"] == "bms.md.stage-ledger.v1"
    assert on_disk["stages"]["minimization"]["status"] == "completed"


def test_stage_ledger_retry_clears_stale_failure_metadata(tmp_path: Path) -> None:
    ledger = StageLedger(tmp_path / "stage_state.json")
    artifact = tmp_path / "analysis.txt"
    ledger.mark_running("analysis", ["gmx", "check"])
    ledger.mark_failed("analysis", "old failure")
    ledger.mark_running("analysis", ["gmx", "check"])
    artifact.write_text("valid\n", encoding="utf-8")
    ledger.mark_completed("analysis", [artifact])

    stage = ledger.snapshot()["stages"]["analysis"]
    assert stage["status"] == "completed"
    assert "error" not in stage
    assert "failed_at" not in stage


def test_performance_parser_extracts_machine_metrics() -> None:
    parsed = parse_gromacs_performance(
        """
               Core t (s)   Wall t (s)        (%)
       Time:      160.000       20.000      800.0
Performance:      432.000        0.056
        """
    )

    assert parsed == {"ns_per_day": 432.0, "hours_per_ns": 0.056, "wall_seconds": 20.0}


def test_minimization_requires_verified_force_convergence() -> None:
    assert_minimization_converged(
        "Steepest Descents converged to Fmax < 1000 in 630 steps.\nMaximum force = 9.9e+02"
    )
    with pytest.raises(RuntimeError, match="did not converge"):
        assert_minimization_converged(
            "Steepest Descents did not converge to Fmax < 1000 in 501 steps."
        )
    with pytest.raises(RuntimeError, match="could not be verified"):
        assert_minimization_converged("Finished mdrun without a convergence line")


def test_gromacs_command_uses_full_gpu_offload_and_restart_flags(tmp_path: Path) -> None:
    checkpoint = tmp_path / "production.cpt"
    checkpoint.write_bytes(b"checkpoint")

    command = build_mdrun_command(
        gmx="gmx",
        deffnm="production",
        gpu_id="0",
        ntmpi=1,
        ntomp=12,
        gpu_offload="full",
        pin="on",
        checkpoint=checkpoint,
        checkpoint_interval_minutes=15.0,
    )

    assert command[:4] == ["gmx", "mdrun", "-deffnm", "production"]
    assert command[command.index("-gpu_id") + 1] == "0"
    assert command[command.index("-ntmpi") + 1] == "1"
    assert command[command.index("-ntomp") + 1] == "12"
    for flag in ("-nb", "-pme", "-bonded", "-update"):
        assert command[command.index(flag) + 1] == "gpu"
    assert command[command.index("-cpi") + 1] == str(checkpoint)
    assert command[command.index("-cpo") + 1] == str(checkpoint)
    assert command[command.index("-cpt") + 1] == "15.0"
    assert "-append" in command


def test_gromacs_command_supports_gpu_forces_with_cpu_update_for_virtual_sites(tmp_path: Path) -> None:
    command = build_mdrun_command(
        gmx="gmx",
        deffnm="production",
        gpu_id="0",
        ntmpi=1,
        ntomp=8,
        gpu_offload="full_forces",
        pin="on",
        checkpoint=tmp_path / "missing.cpt",
    )

    assert command[command.index("-nb") + 1] == "gpu"
    assert command[command.index("-pme") + 1] == "gpu"
    assert command[command.index("-bonded") + 1] == "gpu"
    assert command[command.index("-update") + 1] == "cpu"


def test_gromacs_command_omits_restart_flags_without_checkpoint(tmp_path: Path) -> None:
    command = build_mdrun_command(
        gmx="gmx",
        deffnm="production",
        gpu_id="0",
        ntmpi=1,
        ntomp=8,
        gpu_offload="auto",
        pin="on",
        checkpoint=tmp_path / "missing.cpt",
    )

    assert "-cpi" not in command
    assert "-append" not in command
    assert "-nb" not in command


def test_gromacs_cuda_preflight_fails_closed() -> None:
    assert_cuda_enabled("GROMACS version: 2025.3\nGPU support: CUDA\n")

    with pytest.raises(RuntimeError, match="CUDA-enabled GROMACS"):
        assert_cuda_enabled("GROMACS version: 2025.3\nGPU support: disabled\n")


def test_run_manifest_has_relative_hashes_and_validates_md_run_v1_schema(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    trajectory = output_dir / "production.xtc"
    checkpoint = output_dir / "production.cpt"
    trajectory.write_bytes(b"trajectory")
    checkpoint.write_bytes(b"checkpoint")

    manifest = build_run_manifest(
        output_dir=output_dir,
        job_config=config,
        replica_index=0,
        engine_version="2025.3",
        platform="CUDA",
        artifacts={"trajectory": trajectory, "checkpoint": checkpoint},
        stages={"production": {"status": "completed"}},
    )
    manifest.update(status="completed", replica_seed=replica_seed(config["random_seed"], 0))
    manifest["engine"]["cuda_enabled"] = True

    assert manifest["artifacts"]["trajectory"] == {
        "path": "production.xtc",
        "bytes": len(b"trajectory"),
        "sha256": hashlib.sha256(b"trajectory").hexdigest(),
    }
    assert manifest["artifacts"]["checkpoint"]["bytes"] == len(b"checkpoint")
    schema = json.loads((REPO_ROOT / "schemas" / "md_run_v1.schema.json").read_text())
    Draft202012Validator(schema).validate(manifest)


def test_run_manifest_rejects_artifacts_outside_run_root(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    outside = tmp_path / "outside.xtc"
    outside.write_bytes(b"nope")

    with pytest.raises(ValueError, match="outside output directory"):
        build_run_manifest(
            output_dir=output_dir,
            job_config=config,
            replica_index=0,
            engine_version="2025.3",
            platform="CUDA",
            artifacts={"trajectory": outside},
            stages={},
        )


def test_cuda_contract_requires_one_scheduler_device_remapped_to_zero(tmp_path: Path) -> None:
    _config_path, config = _materialized_openmm(tmp_path)

    allocation = assert_single_cuda_device(
        config,
        environ={"CUDA_VISIBLE_DEVICES": "GPU-cloud-17"},
    )

    assert allocation["container_device_index"] == "0"
    assert allocation["scheduler_device_id"] == "GPU-cloud-17"
    assert allocation["visible_device_token"] == "GPU-cloud-17"
    for environ in ({}, {"CUDA_VISIBLE_DEVICES": "0,1"}):
        with pytest.raises(CudaContractError, match="exactly one"):
            assert_single_cuda_device(config, environ=environ)


def test_cuda_contract_accepts_opc_full_forces_mode_with_cpu_update(tmp_path: Path) -> None:
    _config_path, config = _materialized_gromacs(tmp_path)
    config["execution"]["gpu_offload"] = "full_forces"

    normalized = normalize_job_config(config)
    allocation = assert_single_cuda_device(
        normalized,
        environ={"CUDA_VISIBLE_DEVICES": "GPU-opc-fixture"},
    )

    assert normalized["execution"]["gpu_offload"] == "full_forces"
    assert allocation["container_device_index"] == "0"


def test_adapter_dispatch_is_engine_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gromacs_path, _gromacs_config = _materialized_gromacs(
        tmp_path / "gromacs",
        job_id="dispatch-gromacs",
    )
    openmm_path, _openmm_config = _materialized_openmm(
        tmp_path / "openmm",
        job_id="dispatch-openmm",
    )
    calls: list[str] = []

    def fake_gromacs(
        _config_path: Path,
        output_dir: Path,
        **_kwargs: Any,
    ) -> Path:
        calls.append("gromacs")
        manifest = output_dir / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"engine": {"name": "gromacs"}}), encoding="utf-8")
        return manifest

    def fake_openmm(
        _config_path: Path,
        output_dir: Path,
        **_kwargs: Any,
    ) -> Path:
        calls.append("openmm")
        manifest = output_dir / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"engine": {"name": "openmm"}}), encoding="utf-8")
        return manifest

    monkeypatch.setattr("scripts.bms_md.adapters.gromacs.run_gromacs_job", fake_gromacs)
    monkeypatch.setattr("scripts.bms_md.adapters.openmm.run_openmm_job", fake_openmm)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-cloud-17")

    gromacs_manifest = run_md_replica(gromacs_path, tmp_path / "gromacs-output", replica_index=0)
    openmm_manifest = run_md_replica(openmm_path, tmp_path / "openmm-output", replica_index=0)

    assert json.loads(gromacs_manifest.read_text())["engine"]["name"] == "gromacs"
    assert json.loads(openmm_manifest.read_text())["engine"]["name"] == "openmm"
    assert calls == ["gromacs", "openmm"]


def test_openmm_adapter_rejects_invalid_stage_before_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, config = _materialized_openmm(tmp_path)
    config["stages"]["nvt"]["enabled"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-cloud-17")
    monkeypatch.setattr(
        "scripts.bms_md.adapters.openmm.run_openmm_job",
        lambda *_args, **_kwargs: pytest.fail("OpenMM runner was called"),
    )

    with pytest.raises(EngineAdapterError, match="production-only"):
        run_md_replica(config_path, tmp_path / "output", replica_index=0)


FAKE_GMX = r'''#!/usr/bin/env python3
from pathlib import Path
import os
import sys

args = sys.argv[1:]
subcommand = args[0]
args = args[1:]
calls = Path(os.environ["FAKE_GMX_CALLS"])
calls.parent.mkdir(parents=True, exist_ok=True)
with calls.open("a", encoding="utf-8") as handle:
    handle.write(subcommand + " " + " ".join(args) + "\n")

def value(flag):
    return args[args.index(flag) + 1]

def write(flag, content):
    path = Path(value(flag))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

if subcommand == "mdrun" and "-version" in args:
    print("GROMACS version: 2025.3")
    print("GPU support:      CUDA")
elif subcommand == "pdb2gmx":
    write("-o", "processed coordinates\n")
    write("-p", '#include "posre.itp"\n')
    write("-i", "position restraints\n")
elif subcommand in {"editconf", "solvate", "genion"}:
    write("-o", subcommand + " coordinates\n")
elif subcommand == "grompp":
    write("-o", "portable tpr\n")
elif subcommand == "mdrun":
    prefix = Path(value("-deffnm"))
    prefix.parent.mkdir(parents=True, exist_ok=True)
    outputs = {
        ".gro": "fixture\n1\n    1ALA      N    1   0.000   0.000   0.000\n   1.0   1.0   1.0\n",
        ".edr": "energy\n",
        ".log": "Time: 80.000 10.000 800.0\nPerformance: 432.000 0.056\nFinished mdrun\n",
    }
    for suffix, content in outputs.items():
        prefix.with_suffix(suffix).write_text(content, encoding="utf-8")
    if prefix.name == "minimization":
        prefix.with_suffix(".log").write_text(
            "Steepest Descents converged to Fmax < 1000 in 42 steps.\nFinished mdrun\n",
            encoding="utf-8",
        )
    else:
        prefix.with_suffix(".cpt").write_text("checkpoint\n", encoding="utf-8")
    if prefix.name == "production":
        prefix.with_suffix(".xtc").write_text("trajectory\n", encoding="utf-8")
elif subcommand == "check":
    print("Checking file; Last frame 10 time 1.000")
else:
    raise SystemExit("unsupported fake gmx command: " + subcommand)
'''


def test_fake_gromacs_full_run_verifies_outputs_and_resumes_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _config = _materialized_gromacs(tmp_path, job_id="functional-smoke")
    fake_gmx = tmp_path / "fake_gmx.py"
    fake_gmx.write_text(FAKE_GMX, encoding="utf-8")
    fake_gmx.chmod(0o755)
    calls_path = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_GMX_CALLS", str(calls_path))
    output = tmp_path / "output"

    manifest_path = run_gromacs_job(
        config_path,
        output,
        replica_index=0,
        gmx_binary=str(fake_gmx),
    )
    manifest = json.loads(manifest_path.read_text())
    first_calls = calls_path.read_text().splitlines()

    assert manifest["schema"] == "bms.md.run.v1"
    assert manifest["status"] == "completed"
    assert manifest["engine"]["name"] == "gromacs"
    assert manifest["engine"]["cuda_enabled"] is True
    assert manifest["replica_index"] == 0
    assert manifest["replica_seed"] == 20260717
    assert (output / "production" / "production.xtc").is_file()
    assert (output / "production" / "production.cpt").is_file()
    assert "Last frame 10" in (output / "analysis" / "gromacs_check.txt").read_text()
    for record in manifest["artifacts"].values():
        artifact = output / record["path"]
        assert not Path(record["path"]).is_absolute()
        assert record["bytes"] == artifact.stat().st_size
        assert record["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    for stage in manifest["stages"].values():
        for artifact in stage.get("artifacts", []):
            assert not Path(artifact["path"]).is_absolute()

    grompp_calls = [line for line in first_calls if line.startswith("grompp ")]
    assert grompp_calls[0].endswith("-maxwarn 1")
    assert all("-maxwarn 0" in line for line in grompp_calls[1:])
    stage_mdruns = [
        line
        for line in first_calls
        if line.startswith("mdrun ") and "-version" not in line
    ]
    assert len(stage_mdruns) == 4
    assert all(f"-{flag} cpu" in stage_mdruns[0] for flag in ("nb", "pme", "update"))
    assert all("-pme gpu" in line and "-update gpu" in line for line in stage_mdruns[1:])
    check_calls = [line for line in first_calls if line.startswith("check ")]
    assert any(" -f " in f" {line} " and "production.xtc" in line for line in check_calls)
    assert any(" -e " in f" {line} " and "production.edr" in line for line in check_calls)

    second_manifest = run_gromacs_job(
        config_path,
        output,
        replica_index=0,
        gmx_binary=str(fake_gmx),
    )
    second_calls = calls_path.read_text().splitlines()
    assert second_manifest == manifest_path
    assert second_calls[len(first_calls) :] == ["mdrun -version"]

    (output / "production" / "production.xtc").write_text("tampered\n", encoding="utf-8")
    run_gromacs_job(
        config_path,
        output,
        replica_index=0,
        gmx_binary=str(fake_gmx),
    )
    retry_calls = calls_path.read_text().splitlines()[len(second_calls) :]
    assert retry_calls[0] == "mdrun -version"
    assert retry_calls[1].startswith("grompp ")
    assert retry_calls[2].startswith("mdrun ")
    assert "production/production.cpt" in retry_calls[2]
    assert "-cpi" in retry_calls[2]
    assert "-append" in retry_calls[2]
