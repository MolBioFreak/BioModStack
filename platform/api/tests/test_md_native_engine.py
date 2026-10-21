"""Engine handoff tests; native scientific execution is qualified separately."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.bms_md import gromacs_pipeline as pipeline
from scripts.bms_md.gromacs import build_mdrun_command
from scripts.bms_md.runner import render_mdp


def config(tmp_path):
    source = tmp_path / "system"
    source.mkdir()
    for name in ("input.gro", "topol.top", "groups.ndx", "reference.gro"):
        (source / name).write_text("inert input\n")
    return {
        "schema": "bms.md.job.v3", "job_id": "native-engine", "engine": "gromacs",
        "replicas": 1, "random_seed": 123, "window_id": "w0", "replicate_index": 0,
        "engine_runtime": {"sif_sha256": "a" * 64, "image_name": "fixture.sif"},
        "input": {"coordinates": str(source / "input.gro"), "topology": str(source / "topol.top"),
                  "index": str(source / "groups.ndx"), "restraint_reference": str(source / "reference.gro")},
        "execution": {"gpu_id": "0", "ntmpi": 1, "ntomp": 2, "gpu_offload": "none", "pin": "off"},
        "stages": [{"name": "warmup", "mdp": {"integrator": "steep", "nsteps": 3}},
                   {"name": "sample", "mdp": {"integrator": "md", "nsteps": 7, "dt": ".001",
                                                "gen-seed": -1, "ld-seed": 73, "native-new-option": "keep"}}],
    }


@pytest.fixture
def native_double(monkeypatch, tmp_path):
    calls = []
    # Metadata now streams through Popen rather than the captured-log helper.
    # Keep this explicitly inert command fixture at the existing boundary.
    import os
    import sys
    executable = tmp_path / "fixture-gmx"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:3] == ['dump', '-s']:\n"
        " print('dt = 0.001\\ntinit = 7\\nnsteps = 7\\nld-seed = 73')\n"
        "elif sys.argv[1:3] == ['dump', '-cp']:\n"
        " print('step = 7\\nt = 7.007')\n"
        "else: sys.exit(2)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    def run(command, *, cwd, log_path, stdin_text=None):
        calls.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        text = ""
        if command[1:] == ["mdrun", "-version"]:
            text = "GROMACS version: fixture\nGPU support: CUDA\n"
        elif command[1] == "grompp":
            Path(command[command.index("-o") + 1]).write_text("inert TPR")
        elif command[1] == "mdrun":
            prefix = Path(command[command.index("-deffnm") + 1])
            # Created only in private tmp_path; not a scientific input fixture.
            prefix.with_suffix(".gro").write_text("inert\n1\n    1ARG     AR    1   0.000   0.000   0.000\n   3.0   3.0   3.0\n")
            for suffix in (".cpt", ".edr", ".log"):
                prefix.with_suffix(suffix).write_text("inert native output")
            if prefix.name == "sample":
                (prefix.parent / "pullx.xvg").write_text("7.007 0.6\n")
        elif command[1] == "editconf":
            Path(command[command.index("-o") + 1]).write_text("inert PDB\n")
        else:
            pytest.fail(f"unexpected native command: {command}")
        log_path.write_text(text)
        return text

    monkeypatch.setattr(pipeline, "_run_command", run)
    return calls


def test_ordered_native_roles_and_no_invented_trajectory(tmp_path, native_double):
    cfg = config(tmp_path)
    path = pipeline.run_gromacs_job(tmp_path / "unused", tmp_path,
                                    _prepared_config=cfg, gmx_binary="fixture-gmx")
    manifest = json.loads(path.read_text())
    grompp = [c for c in native_double if c[1] == "grompp"]
    assert len(grompp) == 2
    assert grompp[0][grompp[0].index("-c") + 1] == cfg["input"]["coordinates"]
    assert grompp[1][grompp[1].index("-c") + 1] == str(tmp_path / "warmup/warmup.gro")
    assert grompp[1][grompp[1].index("-t") + 1] == str(tmp_path / "warmup/warmup.cpt")
    for command in grompp:
        assert command[command.index("-n") + 1] == cfg["input"]["index"]
        assert command[command.index("-r") + 1] == cfg["input"]["restraint_reference"]
        assert "-maxwarn" not in command
    assert (tmp_path / "sample/sample.mdp").read_text() == render_mdp("sample", cfg, 0)
    assert manifest["final_stage"] == "sample"
    assert not list(tmp_path.glob("*/*.dump.txt"))
    assert manifest["window_id"] == "w0" and manifest["replicate_index"] == 0
    assert manifest["native_endpoints"]["sample"]["step"] == 7
    assert manifest["native_endpoints"]["sample"]["time_ps"] == 7.007
    assert manifest["orchestration_seed"] == 123 and "replica_seed" not in manifest
    assert "trajectory" not in manifest["artifacts"] and "trajectory_frame_map" not in manifest["artifacts"]
    assert manifest["artifacts"]["representative_structure"]["source_frame"] is None
    assert manifest["artifacts"]["pull_coordinates"]["semantic_role"] == "pull_coordinates"
    assert manifest["artifacts"]["production_tpr"]["path"] == "sample/sample.tpr"
    import jsonschema
    schema = json.loads((Path(__file__).resolve().parents[3] / "schemas/md_run_v1.schema.json").read_text())
    jsonschema.validate(manifest, schema)
    # Native tinit may be negative; the result schema must not invent a time gate.
    manifest["artifacts"]["representative_structure"]["time_ps"] = -2.5
    jsonschema.validate(manifest, schema)


def test_compiled_tpr_checkpoint_never_grompp(tmp_path, native_double):
    cfg = config(tmp_path)
    tpr, cpt = tmp_path / "source.tpr", tmp_path / "source.cpt"
    tpr.write_text("inert compiled input")
    cpt.write_text("inert checkpoint")
    cfg.update(input={"tpr": str(tpr), "checkpoint": str(cpt)}, stages=[])
    pipeline.run_gromacs_job(tmp_path / "unused", tmp_path, _prepared_config=cfg)
    assert not any(c[1] == "grompp" for c in native_double)
    command = next(c for c in native_double if c[1] == "mdrun" and "-version" not in c)
    assert command[command.index("-cpi") + 1] == str(cpt)
    assert "-noappend" in command and "-append" not in command
    assert (tmp_path / "production/production.tpr").read_bytes() == tpr.read_bytes()


def test_native_resume_reuses_tpr_and_appends(tmp_path, native_double):
    cfg = config(tmp_path)
    cfg["stages"] = cfg["stages"][1:]
    stage = tmp_path / "sample"
    stage.mkdir()
    (stage / "sample.tpr").write_text("original compiled physics")
    checkpoint = stage / "sample.cpt"
    checkpoint.write_text("native checkpoint")
    pipeline.run_gromacs_job(tmp_path / "unused", tmp_path, _prepared_config=cfg,
                             resume_checkpoint=checkpoint)
    assert not any(c[1] == "grompp" for c in native_double)
    command = next(c for c in native_double if c[1] == "mdrun" and "-version" not in c)
    assert "-append" in command and command[command.index("-cpi") + 1] == str(checkpoint)
    assert (stage / "sample.tpr").read_text() == "original compiled physics"


def test_guided_native_consumes_existing_bundle(tmp_path, monkeypatch, native_double):
    cfg = config(tmp_path)
    coordinates, topology = Path(cfg["input"]["coordinates"]), Path(cfg["input"]["topology"])
    cfg["input"] = {"structure": str(coordinates)}
    bundle = tmp_path / "bundle"
    seen = []
    def consume(config, selected_bundle, output, ledger):
        seen.append(selected_bundle)
        return coordinates, topology, [coordinates, topology]
    monkeypatch.setattr(pipeline, "_consume_preparation_bundle", consume)
    pipeline.run_gromacs_job(tmp_path / "unused", tmp_path, _prepared_config=cfg, preparation_bundle=bundle)
    assert seen == [bundle]
    assert not any(c[1] in {"pdb2gmx", "solvate", "genion"} for c in native_double)


def test_native_observation_failure_is_not_a_dynamics_gate(tmp_path, monkeypatch, native_double):
    cfg = config(tmp_path)
    original = pipeline._run_command
    def run(command, **kwargs):
        if command[1] == "dump":
            raise RuntimeError("native dump unavailable")
        return original(command, **kwargs)
    monkeypatch.setattr(pipeline, "_run_command", run)
    manifest = json.loads(pipeline.run_gromacs_job(
        tmp_path / "unused", tmp_path, _prepared_config=cfg,
    ).read_text())
    assert manifest["status"] == "completed"
    assert manifest["publication_errors"]
    assert "step" not in manifest["native_endpoints"]["sample"]
    assert manifest["artifacts"]["representative_structure"]["source_frame"] is None


def test_native_frames_use_stored_steps_and_only_coordinates(tmp_path, monkeypatch):
    import io
    from scripts.bms_md import native_pipeline
    trajectory = tmp_path / "inert.trr"
    trajectory.write_bytes(b"inert")
    class Dump:
        stdout = io.StringIO("inert frame 0:\n natoms=2 step=40 time=7.004e+00\n v (2x3):\n"
                             "inert frame 1:\n natoms=2 step=45 time=7.009e+00\n x (2x3):\n")
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def wait(self): return 0
    monkeypatch.setattr(native_pipeline.subprocess, "Popen", lambda *a, **kw: Dump())
    result = native_pipeline.native_frame_map("gmx", trajectory, 3)
    assert result["atom_count"] == 2
    assert result["frames"] == [{"display_frame": 0, "source_frame": 1, "step": 45, "time_ps": 7.009}]


def test_cpu_mdrun_does_not_require_visible_gpu(tmp_path):
    command = build_mdrun_command(gmx="gmx", deffnm="production", gpu_id="0", ntmpi=1,
                                  ntomp=1, gpu_offload="none", pin="off", checkpoint=tmp_path / "absent.cpt")
    assert "-gpu_id" not in command
    assert command[command.index("-nb") + 1] == "cpu"

@pytest.mark.parametrize("offload", ["full", "full_forces", "auto", "none"])
@pytest.mark.parametrize("compiled", [False, True])
def test_native_integrator_placement_not_stage_name(tmp_path, monkeypatch, native_double, offload, compiled):
    import copy
    import io
    from scripts.bms_md import native_pipeline
    cfg = config(tmp_path)
    cfg["execution"]["gpu_offload"] = offload
    cfg["stages"] = [{"name": "not_a_minimization_name", "mdp": {"integrator": "md", "nsteps": 0}}]
    if compiled:
        tpr = tmp_path / "original.tpr"; tpr.write_bytes(b"exact compiled physics")
        cfg.update(input={"tpr": str(tpr)}, stages=[])
    original = copy.deepcopy(cfg)
    dumps = []
    class Dump:
        def __init__(self, command):
            dumps.append(command)
            self.stdout = io.StringIO("integrator = steep\nnsteps = 0\n" if command[2] == "-s" else "step = 0\nt = 0\n")
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def wait(self): return 0
    monkeypatch.setattr(native_pipeline.subprocess, "Popen", lambda command, **kw: Dump(command))
    manifest = json.loads(pipeline.run_gromacs_job(tmp_path / "unused", tmp_path, _prepared_config=cfg).read_text())
    mdrun = next(c for c in native_double if c[1] == "mdrun" and "-version" not in c)
    assert mdrun[mdrun.index("-nb") + 1] == "cpu"
    assert "-gpu_id" not in mdrun
    assert cfg == original
    assert len([c for c in dumps if c[2] == "-s"]) == 1
    assert manifest["native_endpoints"][manifest["final_stage"]]["native_tpr"]["integrator"] == "steep"
    assert bool([c for c in native_double if c[1] == "grompp"]) is not compiled


def test_compiled_unknown_integrator_retains_requested_offload(tmp_path, monkeypatch, native_double):
    from scripts.bms_md import native_pipeline
    cfg = config(tmp_path)
    tpr = tmp_path / "source.tpr"; tpr.write_bytes(b"inert native TPR")
    cfg.update(input={"tpr": str(tpr)}, stages=[])
    cfg["execution"]["gpu_offload"] = "full"
    def unavailable(*args, **kwargs):
        raise OSError("metadata unavailable")
    monkeypatch.setattr(native_pipeline.subprocess, "Popen", unavailable)
    manifest = json.loads(pipeline.run_gromacs_job(tmp_path / "unused", tmp_path, _prepared_config=cfg).read_text())
    command = next(c for c in native_double if c[1] == "mdrun" and "-version" not in c)
    assert command[command.index("-nb") + 1] == "gpu"
    assert manifest["status"] == "completed" and manifest["publication_errors"]
    assert not any(c[1] == "grompp" for c in native_double)


def test_legacy_guided_native_dispatch_preserves_authored_stages(tmp_path, monkeypatch, native_double):
    import copy
    cfg = config(tmp_path)
    coordinates, topology = Path(cfg["input"]["coordinates"]), Path(cfg["input"]["topology"])
    cfg["input"] = {"structure": str(coordinates)}
    cfg["chemistry"] = {"profile_id": "gmx_amber99sb_ildn_tip3p_smoke_v1", "resolved_preparation": {"force_field": "amber99sb-ildn", "water_model": "tip3p"}}
    cfg["preparation"] = {"box_type": "dodecahedron", "padding_nm": 1.0, "salt_molar": 0.15, "neutralize": True}
    original = copy.deepcopy(cfg)
    seen = []
    def prepare(config, **kwargs):
        seen.append(copy.deepcopy(config))
        return coordinates, topology, [coordinates, topology]
    monkeypatch.setattr(pipeline, "_prepare_system", prepare)
    monkeypatch.setattr(pipeline, "_consume_preparation_bundle", lambda *a: pytest.fail("legacy recipe must not use Amber bundle"))
    pipeline.run_gromacs_job(tmp_path / "unused", tmp_path, _prepared_config=cfg)
    assert seen == [original] and cfg == original
    assert [Path(c[c.index("-f") + 1]).stem for c in native_double if c[1] == "grompp"] == ["warmup", "sample"]
