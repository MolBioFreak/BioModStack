from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "scripts" / "validate_frustrampnn_publication_markers.py"
WORKFLOWS = (
    REPO_ROOT / "workflows" / "frustrampnn_analysis.nf",
    REPO_ROOT / "workflows" / "structure_prediction.nf",
)


def _run_validator(
    tmp_path: Path,
    job_root: Path,
    payload: dict[str, str],
    *,
    bare_marker: bool = False,
    status_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    marker = tmp_path / "published_candidate.json"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    marker_argument = marker.name if bare_marker else str(marker)
    command = [
        sys.executable,
        str(VALIDATOR),
        "--job-root",
        str(job_root),
    ]
    if status_output is not None:
        command.extend(["--status-output", str(status_output)])
    command.append(marker_argument)
    return subprocess.run(
        command,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )


def _published_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, str]]:
    from test_frustrampnn_manifests import _v2_bundle

    job_root = tmp_path / "job"
    bundle = job_root / "frustrampnn/results/candidate"
    bundle.parent.mkdir(parents=True)
    _, original_source = _v2_bundle(bundle, monkeypatch)
    canonical_source = job_root / "inputs/original.pdb"
    canonical_source.parent.mkdir(parents=True)
    canonical_source.write_bytes(original_source.read_bytes())
    return job_root, {
        "manifest": "frustrampnn/results/candidate/frustrampnn_result_manifest_v2.json",
        "result": "frustrampnn/results/candidate/workflow_component_result_v2.json",
        "source": "inputs/original.pdb",
        "statistics": "frustrampnn/results/candidate/frustrampnn_statistics_v1.json",
    }


def test_v2_publication_marker_validator_accepts_closed_publisher_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_root, payload = _published_v2(tmp_path, monkeypatch)
    status_output = tmp_path / "stage-status"

    completed = _run_validator(
        tmp_path, job_root, payload, status_output=status_output
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        payload["result"],
        payload["manifest"],
        payload["source"],
        payload["statistics"],
    ]
    assert status_output.read_text(encoding="utf-8") == "complete\n"


def test_v2_publication_marker_validator_accepts_bare_marker_from_work_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_root, payload = _published_v2(tmp_path, monkeypatch)

    completed = _run_validator(tmp_path, job_root, payload, bare_marker=True)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        payload["result"],
        payload["manifest"],
        payload["source"],
        payload["statistics"],
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_statistics",
        "extra",
        "wrong_manifest",
        "wrong_result",
        "wrong_statistics",
        "unsafe_source",
        "other_bundle_result",
    ],
)
def test_v2_publication_marker_validator_rejects_nonexact_or_misnamed_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    job_root, payload = _published_v2(tmp_path, monkeypatch)
    if mutation == "missing_statistics":
        payload.pop("statistics")
    elif mutation == "extra":
        payload["path"] = "/private/runtime"
    elif mutation == "wrong_manifest":
        payload["manifest"] = "frustrampnn/results/candidate/frustrampnn_result_manifest_v1.json"
    elif mutation == "wrong_result":
        payload["result"] = "frustrampnn/results/candidate/workflow_component_result_v1.json"
    elif mutation == "wrong_statistics":
        payload["statistics"] = "frustrampnn/results/candidate/statistics.json"
    elif mutation == "unsafe_source":
        payload["source"] = "../outside.pdb"
    else:
        payload["result"] = "frustrampnn/results/other/workflow_component_result_v2.json"

    completed = _run_validator(tmp_path, job_root, payload)

    assert completed.returncode != 0
    assert "invalid FrustraMPNN publication marker" in completed.stderr


@pytest.mark.parametrize("artifact", ["manifest", "result", "statistics", "source"])
def test_v2_publication_marker_validator_rejects_post_publication_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: str,
) -> None:
    job_root, payload = _published_v2(tmp_path, monkeypatch)
    target = job_root / payload[artifact]
    target.write_bytes(target.read_bytes() + b" ")

    completed = _run_validator(tmp_path, job_root, payload)

    assert completed.returncode != 0
    assert completed.stdout == ""


def test_v2_publication_marker_validator_rejects_symlinked_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_root, payload = _published_v2(tmp_path, monkeypatch)
    source = job_root / payload["source"]
    outside = tmp_path / "outside.pdb"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    os.symlink(outside, source)

    completed = _run_validator(tmp_path, job_root, payload)

    assert completed.returncode != 0
    assert completed.stdout == ""


def test_v2_workflow_reporters_execute_validator_before_stage_complete_callback() -> None:
    for workflow in WORKFLOWS:
        source = workflow.read_text(encoding="utf-8")
        validator = source.index("validate_frustrampnn_publication_markers.py")
        reporter = source.index("stage_reporter.py", validator)
        assert validator < reporter
        assert "--job-root '${params.out_dir}'" in source
    standalone = WORKFLOWS[0].read_text(encoding="utf-8")
    assert "--status-output frustrampnn_stage_status" in standalone
    assert "'${params.job_id}' frustrampnn" in standalone
    assert "\\${status}" in standalone
    assert "test \\\"\\${status}\\\" = complete" in standalone
    assert "frustrampnn complete" in WORKFLOWS[1].read_text(encoding="utf-8")
