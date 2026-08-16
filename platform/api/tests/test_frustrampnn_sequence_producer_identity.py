from __future__ import annotations

import json
import os
import shutil
import stat
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


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_sequence_boundary(run_root: Path, names: list[str]) -> list[dict[str, object]]:
    run_root.mkdir(parents=True)
    for directory in ("bin", "fakepy", "out", "work"):
        (run_root / directory).mkdir()
    _write_executable(
        run_root / "bin" / "boltz",
        """#!/bin/sh
set -eu
mkdir -p boltz_results_yamls/predictions/submission
cat > boltz_results_yamls/predictions/submission/model_0.pdb <<'EOF'
ATOM      1  N   GLY A   1      11.000  12.000  13.000  1.00 20.00           N
ATOM      2  CA  GLY A   1      12.000  12.000  13.000  1.00 20.00           C
ATOM      3  C   GLY A   1      13.000  12.000  13.000  1.00 20.00           C
ATOM      4  O   GLY A   1      14.000  12.000  13.000  1.00 20.00           O
END
EOF
""",
    )
    (run_root / "fakepy" / "yaml.py").write_text(
        "def dump(value, *args, **kwargs):\n    return 'version: 1\\nsequences: []\\n'\n",
        encoding="utf-8",
    )
    tuples = ",\n        ".join(
        f"tuple('ACDEFGHIK', {json.dumps(name)})" for name in names
    )
    (run_root / "main.nf").write_text(
        f"""nextflow.enable.dsl = 2
import groovy.json.JsonOutput
include {{ structure_prediction_wf }} from '/workspace/modules/structure_prediction.nf'
workflow {{
    inputs = Channel.of(
        {tuples}
    )
    structure_prediction_wf(inputs)
    structure_prediction_wf.out.canonical_structures
        .map {{ producer_meta, predicted -> producer_meta }}
        .collect()
        .view {{ records -> 'CANONICAL_RECORDS:' + JsonOutput.toJson(records) }}
}}
""",
        encoding="utf-8",
    )
    (run_root / "nextflow.config").write_text(
        """params.out_dir = '/run/out'
params.code_root = '/workspace'
params.pred_method = 'boltz'
params.boltz_use_msa = false
params.boltz_num_samples = 1
process.executor = 'local'
env {
    PATH = "/run/bin:${System.getenv('PATH')}"
    PYTHONPATH = '/run/fakepy'
}
docker.enabled = false
singularity.enabled = false
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
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
            "/run/main.nf",
            "-c",
            "/run/nextflow.config",
            "-offline",
            "-w",
            "/run/work",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    payloads = [
        line.split("CANONICAL_RECORDS:", 1)[1]
        for line in completed.stdout.splitlines()
        if "CANONICAL_RECORDS:" in line
    ]
    assert payloads, output
    assert len(set(payloads)) == 1, output
    records = json.loads(payloads[0])
    assert isinstance(records, list)
    return records


def _run_stubbed_nonboltz_boundary(
    run_root: Path, predictor: str, names: list[str]
) -> list[dict[str, object]]:
    run_root.mkdir(parents=True)
    for directory in ("bin", "out", "work"):
        (run_root / directory).mkdir()
    _write_executable(
        run_root / "bin" / "python3",
        """#!/usr/bin/python3
import pathlib, sys
args = sys.argv[1:]
if not args or args[0] == '-' or any(pathlib.Path(arg).name == 'run_inference.py' for arg in args):
    sys.stdin.read()
    target = pathlib.Path('output/submission/model.pdb')
elif any(pathlib.Path(arg).name == 'run_protenix_inference.py' for arg in args):
    target = pathlib.Path('predictions/submission/model.cif')
    target.parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path('predictions/submission/model_full_data.json').write_text('{}\\n')
elif any(pathlib.Path(arg).name == 'bms_gpu_run_telemetry.py' for arg in args):
    target = pathlib.Path('esmfold2_results/model.cif')
    target.parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path('esmfold2_results/model.metrics.json').write_text('{}\\n')
    pathlib.Path('esmfold2_results/model.telemetry.json').write_text('{}\\n')
    pathlib.Path('esmfold2_results/manifest.json').write_text('{}\\n')
    pathlib.Path('esmfold2_results/summary.tsv').write_text('metric\\tvalue\\n')
else:
    raise SystemExit(0)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text('EQUAL STRUCTURE BYTES\\n')
""",
    )
    _write_executable(
        run_root / "bin" / "find",
        """#!/bin/sh
case "$*" in
  *"*full_data*.json"*) printf '%s\\n' predictions/submission/model_full_data.json ;;
  *"*.cif"*) printf '%s\\n' predictions/submission/model.cif ;;
esac
""",
    )
    (run_root / "NO_MSA").write_text("", encoding="utf-8")
    tuples = ",\n        ".join(
        f"tuple('ACDEFGHIK', {json.dumps(name)})" for name in names
    )
    (run_root / "main.nf").write_text(
        f"""nextflow.enable.dsl = 2
import groovy.json.JsonOutput
include {{ structure_prediction_wf }} from '/workspace/modules/structure_prediction.nf'
workflow {{
    inputs = Channel.of(
        {tuples}
    )
    structure_prediction_wf(inputs)
    structure_prediction_wf.out.canonical_structures
        .map {{ producer_meta, predicted -> producer_meta }}
        .collect()
        .view {{ records -> 'CANONICAL_RECORDS:' + JsonOutput.toJson(records) }}
}}
""",
        encoding="utf-8",
    )
    (run_root / "nextflow.config").write_text(
        f"""params.out_dir = '/run/out'
params.code_root = '/run'
params.pred_method = {json.dumps(predictor)}
params.rf3_use_msa = false
params.protenix_use_msa = false
process.executor = 'local'
env.PATH = "/run/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
docker.enabled = false
singularity.enabled = false
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-e", "NXF_OFFLINE=true", "-e", "NXF_DISABLE_CHECK_LATEST=true",
            "-v", f"{REPO_ROOT}:/workspace:ro", "-v", f"{run_root}:/run:rw",
            "-w", "/run", NEXTFLOW_IMAGE, "nextflow", "run", "/run/main.nf",
            "-c", "/run/nextflow.config", "-stub-run", "-offline", "-w", "/run/work",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    payloads = [
        line.split("CANONICAL_RECORDS:", 1)[1]
        for line in completed.stdout.splitlines()
        if "CANONICAL_RECORDS:" in line
    ]
    assert payloads, output
    assert len(set(payloads)) == 1, output
    records = json.loads(payloads[0])
    assert isinstance(records, list)
    return records


def _preview_invalid_batch(run_root: Path, entries: list[dict[str, object]]) -> str:
    run_root.mkdir(parents=True)
    (run_root / "out").mkdir()
    (run_root / "work").mkdir()
    (run_root / "batch.json").write_text(json.dumps(entries), encoding="utf-8")
    completed = subprocess.run(
        [
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
            "--code_root",
            "/workspace",
            "--out_dir",
            "/run/out",
            "--job_id",
            "invalid-sequence-batch",
            "--pred_method",
            "boltz",
            "--run_frustrampnn",
            "false",
            "--sequence_batch_json_path",
            "/run/batch.json",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    return f"{completed.stdout}\n{completed.stderr}"


@pytest.mark.runtime_integration
def test_boltz_sequence_boundary_preserves_duplicate_sequence_submission_identity_and_reordering(
    tmp_path: Path,
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")

    forward = _run_sequence_boundary(tmp_path / "forward", ["sample-A", "sample-B"])
    reverse = _run_sequence_boundary(tmp_path / "reverse", ["sample-B", "sample-A"])

    assert len(forward) == 2
    assert len(reverse) == 2
    assert {record["producer_artifact_id"] for record in forward} == {
        "sample-A",
        "sample-B",
    }
    assert {record["producer_artifact_key"] for record in forward} == {
        "sample-A",
        "sample-B",
    }
    assert {record["producer_sample"] for record in forward} == {
        "sample-A",
        "sample-B",
    }
    assert {
        record["producer_artifact_id"] for record in forward
    } == {record["producer_artifact_id"] for record in reverse}


@pytest.mark.runtime_integration
@pytest.mark.parametrize("predictor", ["rf3", "protenix", "esmfold2"])
def test_nonboltz_sequence_boundaries_preserve_equal_byte_submission_identity_and_reordering(
    tmp_path: Path, predictor: str
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")

    forward = _run_stubbed_nonboltz_boundary(
        tmp_path / "forward", predictor, ["sample-A", "sample-B"]
    )
    reverse = _run_stubbed_nonboltz_boundary(
        tmp_path / "reverse", predictor, ["sample-B", "sample-A"]
    )

    assert len(forward) == 2
    assert len(reverse) == 2
    assert {record["producer_artifact_id"] for record in forward} == {
        "sample-A", "sample-B"
    }
    assert {record["producer_submission_id"] for record in forward} == {
        "sample-A", "sample-B"
    }
    assert {record["producer_method"] for record in forward} == {predictor}
    assert {record["producer_artifact_id"] for record in forward} == {
        record["producer_artifact_id"] for record in reverse
    }


@pytest.mark.runtime_integration
@pytest.mark.parametrize(
    "entries",
    [
        [
            {"id": "sample-A", "name": "first", "sequence": "ACDE"},
            {"id": "sample-A", "name": "second", "sequence": "ACDE"},
        ],
        [{"id": "", "name": "sample-A", "sequence": "ACDE"}],
    ],
    ids=["duplicate-entry-id", "empty-entry-id"],
)
def test_protein_design_rejects_indistinguishable_sequence_batch_entries_before_prediction(
    tmp_path: Path, entries: list[dict[str, object]]
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")

    output = _preview_invalid_batch(tmp_path / "preview", entries)

    assert "protein_design:invalid_sequence_batch_identity" in output
    assert "PROTEIN_DESIGN:structure_prediction_wf" not in output
