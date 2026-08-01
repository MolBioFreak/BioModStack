from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.1"


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    return subprocess.run(
        [docker, "image", "inspect", NEXTFLOW_IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _preview_early_sequence_workflow(
    run_root: Path,
    *,
    batch: bool,
    enabled: bool,
) -> tuple[str, str]:
    run_root.mkdir(parents=True)
    (run_root / "out").mkdir()
    (run_root / "work").mkdir()
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-e",
        "NXF_OFFLINE=true",
        "-e",
        "NXF_DISABLE_CHECK_LATEST=true",
        "-v",
        f"{REPO_ROOT}:/workspace:ro",
        "-v",
        f"{run_root}:/run:rw",
        "-w",
        "/run",
        NEXTFLOW_IMAGE,
        "nextflow",
        "run",
        "/workspace/workflows/protein_design.nf",
        "-c",
        "/workspace/nextflow.config",
        "-preview",
        "-offline",
        "-profile",
        "boltz,workstation_ryzen7960x",
        "-w",
        "/run/work",
        "-with-dag",
        "/run/dag.dot",
        "--code_root",
        "/workspace",
        "--out_dir",
        "/run/out",
        "--job_id",
        f"early-{'batch' if batch else 'single'}-{'enabled' if enabled else 'disabled'}",
        "--pred_method",
        "boltz",
        "--run_frustrampnn",
        str(enabled).lower(),
    ]
    if batch:
        batch_manifest = run_root / "batch.json"
        batch_manifest.write_text(
            json.dumps(
                [
                    {"name": "batch-a", "sequence": "ACDEFGHIK"},
                    {"name": "batch-b", "sequence": "ACDEFGHIK"},
                ]
            ),
            encoding="utf-8",
        )
        args.extend(["--sequence_batch_json_path", "/run/batch.json"])
    else:
        args.extend(
            [
                "--sequence_input",
                "ACDEFGHIK",
                "--sequence_name",
                "single-probe",
            ]
        )

    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    output = f"{completed.stdout}\n{completed.stderr}"
    dag_path = run_root / "dag.dot"
    dag = dag_path.read_text(encoding="utf-8") if dag_path.is_file() else ""
    # Nextflow runs as root in the pinned image because its embedded framework
    # JAR is root-readable only. Remove root-owned temporary state in-container.
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{run_root}:/run:rw",
            "--entrypoint",
            "sh",
            NEXTFLOW_IMAGE,
            "-c",
            "rm -rf /run/.nextflow /run/.nextflow.log /run/work",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert completed.returncode == 0, output
    return dag, output


@pytest.mark.runtime_integration
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
def test_early_sequence_branches_schedule_shared_terminal_publication(
    tmp_path: Path,
    batch: bool,
    enabled: bool,
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")
    dag, output = _preview_early_sequence_workflow(
        tmp_path / "nextflow-run",
        batch=batch,
        enabled=enabled,
    )

    assert "PROTEIN_DESIGN:structure_prediction_wf:BoltzFromSequence" in dag
    assert "PROTEIN_DESIGN:BindProteinDesignTerminalMetadata" in dag
    assert "PROTEIN_DESIGN:ProjectProteinDesignMetadata" in dag
    assert "PROTEIN_DESIGN:PublishResults" in dag
    if enabled:
        assert "PROTEIN_DESIGN:CanonicalFrustraMPNN:CanonicalFrustraMPNNTask" in dag
        assert "PROTEIN_DESIGN:ReportProteinDesignFrustraMPNNNotRequested" not in dag
    else:
        assert "PROTEIN_DESIGN:CanonicalFrustraMPNN:CanonicalFrustraMPNNTask" not in dag
        assert "PROTEIN_DESIGN:ReportProteinDesignFrustraMPNNNotRequested" in dag
    assert "Process 'CanonicalFrustraMPNNTask' has been already used" not in output
