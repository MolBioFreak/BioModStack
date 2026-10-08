from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET_LINT_FILES = [
    "modules/protein_hunter_experimental.nf",
    "workflows/protein_hunter_experimental.nf",
    "modules/boltz_cp_experimental.nf",
    "workflows/boltz_cp_experimental.nf",
    "workflows/ppiflow_generator_design.nf",
    "modules/boltzgen.nf",
    "main.nf",
]


def _run_nextflow(*args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["nextflow", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


def _format_failure(result: subprocess.CompletedProcess[str]) -> str:
    return (
        f"exit={result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def _repo_visible_tmp_dir(tmp_path: Path) -> Path:
    """Return a temporary directory under the repo so host Nextflow can see it.

    In Hermes/containerized test runs, pytest's normal /tmp path can be visible
    to Python but invisible to the host Nextflow process. Keep real input files
    under the checked-out repo path for preview smoke tests that call file(...).
    """
    safe_name = f"{tmp_path.name}-{os.getpid()}"
    path = REPO_ROOT / "tmp" / "pytest-nextflow" / safe_name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def test_targeted_nextflow_lint_has_no_errors_for_regressed_files() -> None:
    result = _run_nextflow("lint", *TARGET_LINT_FILES)

    assert result.returncode == 0, _format_failure(result)


def test_boltzgen_standalone_preview_does_not_require_skip_input_dir(tmp_path: Path) -> None:
    nextflow_home = tmp_path / "nextflow-home"
    out_dir = tmp_path / "out"
    nextflow_home.mkdir()
    out_dir.mkdir()

    result = _run_nextflow(
        "run",
        "workflows/boltzgen_design.nf",
        "-preview",
        "-offline",
        "-profile",
        "boltzgen,workstation_ryzen7960x",
        "--diffusion_method",
        "boltzgen",
        "--run_boltzgen_only",
        "true",
        "--rfd_mode",
        "enzyme",
        "--code_root",
        str(REPO_ROOT),
        "--out_dir",
        str(out_dir),
        env_overrides={"NEXTFLOW_HOME": str(nextflow_home)},
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _format_failure(result)
    assert "Argument of `file()` function cannot be null" not in combined_output


def test_boltzgen_non_standalone_preview_does_not_require_skip_input_dir(tmp_path: Path) -> None:
    nextflow_home = tmp_path / "nextflow-home"
    out_dir = tmp_path / "out"
    nextflow_home.mkdir()
    out_dir.mkdir()

    result = _run_nextflow(
        "run",
        "workflows/boltzgen_design.nf",
        "-preview",
        "-offline",
        "-profile",
        "boltzgen,workstation_ryzen7960x",
        "--diffusion_method",
        "boltzgen",
        "--rfd_mode",
        "enzyme",
        "--code_root",
        str(REPO_ROOT),
        "--out_dir",
        str(out_dir),
        env_overrides={"NEXTFLOW_HOME": str(nextflow_home)},
    )

    combined_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, _format_failure(result)
    assert "skip_input_dir is required when skip_rfd_seq_pred=true and analysis-only mode is selected" not in combined_output


def test_boltz_cp_experimental_preview_accepts_yaml_input(tmp_path: Path) -> None:
    visible_tmp = _repo_visible_tmp_dir(tmp_path)
    try:
        nextflow_home = visible_tmp / "nextflow-home"
        out_dir = visible_tmp / "out"
        input_yaml = visible_tmp / "boltz_input.yaml"
        nextflow_home.mkdir()
        out_dir.mkdir()
        input_yaml.write_text(
            "version: 1\nsequences:\n  - protein:\n      id: [A]\n      sequence: MKT\n      msa: empty\n",
            encoding="utf-8",
        )

        result = _run_nextflow(
            "run",
            "workflows/boltz_cp_experimental.nf",
            "-preview",
            "-offline",
            "-profile",
            "boltz_cp_experimental,workstation_ryzen7960x",
            "--bcp_input_path",
            str(input_yaml),
            "--bcp_size_cp",
            "4",
            "--code_root",
            str(REPO_ROOT),
            "--out_dir",
            str(out_dir),
            env_overrides={"NEXTFLOW_HOME": str(nextflow_home)},
        )

        combined_output = f"{result.stdout}\n{result.stderr}"

        assert result.returncode == 0, _format_failure(result)
        assert "bcp_input_path is required" not in combined_output
        assert "bcp_size_cp must be a perfect square" not in combined_output
    finally:
        shutil.rmtree(visible_tmp, ignore_errors=True)
