from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import HTTPException

from routers import conformational_mapping as cm_router
from routers.conformational_mapping import (
    _artifact_byte_range,
    _cm_job_admission,
    _open_verified_artifact_descriptor,
)
from scripts.run_conformational_mapping_analysis_plane import (
    _frustrampnn_command,
    _open_verified_container,
    _sha256_fd,
)
from services.nextflow import build_nextflow_command
from scripts.probes.conformational_mapping.phase_review_common import PhaseReviewError, adjudicate


KEY = b"current-run-evidence-key-32-bytes-minimum"
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_artifact_download_resolves_host_storage_path_before_descriptor_pinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    artifact = runtime_root / "final" / "native.cif"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"data_authoritative_structure\n")
    persisted_host_root = Path("/mnt/BioModStack")

    monkeypatch.setattr(cm_router, "get_data_root", lambda: runtime_root)
    descriptor = _open_verified_artifact_descriptor(
        storage_path=str(persisted_host_root / "final" / "native.cif"),
        root_path=str(persisted_host_root),
        size_bytes=artifact.stat().st_size,
        content_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, artifact.stat().st_size) == artifact.read_bytes()
    finally:
        os.close(descriptor)

    symlink = runtime_root / "final" / "symlink.cif"
    symlink.symlink_to(artifact)
    with pytest.raises(OSError):
        _open_verified_artifact_descriptor(
            storage_path=str(persisted_host_root / "final" / "symlink.cif"),
            root_path=str(persisted_host_root),
            size_bytes=artifact.stat().st_size,
            content_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )


def test_artifact_download_resolves_custom_state_root_and_http_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    artifact = runtime_root / "final" / "native.cif"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"0123456789")
    custom_host_root = Path("/srv/custom-biomodstack-state")
    monkeypatch.setattr(cm_router, "get_data_root", lambda: runtime_root)
    monkeypatch.setenv("BMS_STATE_DIR", str(custom_host_root))

    descriptor = _open_verified_artifact_descriptor(
        storage_path=str(custom_host_root / "final" / "native.cif"),
        root_path=str(custom_host_root),
        size_bytes=artifact.stat().st_size,
        content_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    os.close(descriptor)

    assert _artifact_byte_range(None, 10) == (0, 9, 200)
    assert _artifact_byte_range("bytes=2-5", 10) == (2, 5, 206)
    assert _artifact_byte_range("bytes=2-", 10) == (2, 9, 206)
    assert _artifact_byte_range("bytes=-3", 10) == (7, 9, 206)
    assert _artifact_byte_range("bytes=2-999", 10) == (2, 9, 206)
    with pytest.raises(HTTPException) as raised:
        _artifact_byte_range("bytes=10-12", 10)
    assert raised.value.status_code == 416
    assert raised.value.headers == {"Content-Range": "bytes */10"}


def test_cm_analysis_runs_contracts_on_host_and_only_scores_in_global_component() -> None:
    cm_module = (REPO_ROOT / "modules/conformational_mapping_frustrampnn.nf").read_text()
    global_module = (REPO_ROOT / "modules/frustrampnn.nf").read_text()
    config = (REPO_ROOT / "nextflow.config").read_text()

    assert "label 'CPU'" in cm_module
    assert "postprocess_conformational_mapping_frustrampnn_v2.py" in cm_module
    assert "run_conformational_mapping_analysis_plane.py" not in cm_module
    assert "--container" not in cm_module
    assert "--gpu-id" not in cm_module
    assert "label 'frustrampnn_gpu'" in global_module
    assert "run_frustrampnn_component.py" in global_module
    assert "--physical-gpu-id '${assigned_gpu}'" in global_module
    assert "export CUDA_VISIBLE_DEVICES='${assigned_gpu}'" in global_module
    assert "api_python = System.getenv('BMS_API_PYTHON')" in config


def test_cm_scheduler_admission_and_nonzero_gpu_reach_contained_frustrampnn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _cm_job_admission(
        "external_import", {"targets": [{"target_id": "imported"}]}
    )
    assert admission == {"vram_estimate_mb": 12_000, "sequence_length": 300}

    monkeypatch.setenv("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow")
    command = build_nextflow_command(
        "conformational_mapping", "map",
        {"cm_request_path": "/srv/request/cm_request_v1.json", "gpu_id": 3, "run_frustrampnn": True},
        "/srv/results", job_id="cm-job",
    )
    assert command[command.index("--gpu_id") + 1] == "3"

    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    output_root.mkdir()
    normalized = input_root / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")
    argv = _frustrampnn_command(
        apptainer="apptainer", container=tmp_path / "frustrampnn.sif",
        tool="/opt/venv/bin/frustrampnn", normalized=normalized,
        checkpoint=Path("/opt/frustrampnn_weights/megascale.ckpt"),
        raw=output_root / "raw.csv", output_root=output_root, gpu_id=3,
    )
    assert argv[argv.index("CUDA_VISIBLE_DEVICES=3") - 1] == "--env"
    assert "--containall" in argv
    assert f"{normalized.resolve()}:/bms/input/normalized.pdb:ro" in argv
    assert f"{output_root.resolve()}:/bms/output:rw" in argv
    assert argv[-2:] == ["--device", "cuda"]
    assert "--gpu_id" not in argv
    assert "--gpu-id" not in argv


def test_cm_canonical_component_requires_scheduler_assigned_gpu(
    tmp_path: Path,
) -> None:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"real Nextflow launcher unavailable: {nextflow}")

    request = tmp_path / "workflow_component_request_v2.json"
    source = tmp_path / "canonical_source.pdb"
    structure_map = tmp_path / "frustrampnn_structure_map_v1.json"
    request.write_text(json.dumps({
        "candidate_id": "candidate-a",
        "invocation_id": "frustrampnn:cm-job:candidate-a",
    }), encoding="utf-8")
    source.write_text("ATOM\n", encoding="utf-8")
    structure_map.write_text("{}", encoding="utf-8")
    harness = tmp_path / "harness.nf"
    module = (REPO_ROOT / "modules/frustrampnn.nf").as_posix()
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        f"include {{ CanonicalFrustraMPNNV2 }} from '{module}'\n"
        "workflow {\n"
        "  inputs = Channel.of(tuple(file(params.request), file(params.source), file(params.structure_map)))\n"
        "  CanonicalFrustraMPNNV2(inputs)\n"
        "}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["NXF_HOME"] = str(Path.home() / ".nextflow")
    env["NXF_OFFLINE"] = "true"
    env.pop("SSL_CERT_FILE", None)
    env.pop("CURL_CA_BUNDLE", None)
    base = [
        str(nextflow), "run", str(harness), "-stub-run",
        "--request", str(request), "--source", str(source),
        "--structure_map", str(structure_map), "--api_python", sys.executable,
        "-work-dir", str(tmp_path / "work"),
    ]
    accepted = subprocess.run(
        [*base, "--frustrampnn_physical_gpu_id", "3"],
        check=False, cwd=tmp_path, env=env, text=True, capture_output=True,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    injection_marker = tmp_path / "gpu-injection-proof"
    malicious_gpu = f"3'; touch {injection_marker}; #"
    rejected = subprocess.run(
        [*base[:-2], "-work-dir", str(tmp_path / "rejected-work"),
         "--frustrampnn_physical_gpu_id", malicious_gpu],
        check=False, cwd=tmp_path, env=env, text=True, capture_output=True,
    )
    assert rejected.returncode != 0
    assert "explicit scheduler-assigned" in (rejected.stdout + rejected.stderr)
    assert not injection_marker.exists()


@pytest.mark.parametrize("gpu_id", [True, -1, 3.0, 3.7, "3.7", "-1", " 3"])
def test_cm_scheduler_gpu_id_rejects_noncanonical_values(
    monkeypatch: pytest.MonkeyPatch, gpu_id: object
) -> None:
    monkeypatch.setenv("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow")
    with pytest.raises(ValueError, match="non-negative integer"):
        build_nextflow_command(
            "conformational_mapping", "map",
            {"cm_request_path": "/tmp/cm_request_v1.json", "gpu_id": gpu_id, "run_frustrampnn": True},
            "/tmp/cm-output", "cm-job",
        )

def test_cm_frustrampnn_image_digest_is_verified_before_execution(tmp_path: Path) -> None:
    image = tmp_path / "frustrampnn.sif"
    image.write_bytes(b"qualified-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    fd, actual = _open_verified_container(image, digest)
    assert actual == digest
    os.close(fd)
    with pytest.raises(RuntimeError, match="does not match installed bytes"):
        _open_verified_container(image, "0" * 64)
    with pytest.raises(RuntimeError, match="SHA-256 is malformed"):
        _open_verified_container(image, "not-a-digest")


def test_cm_frustrampnn_image_rejects_all_symlinks_and_pins_open_generation(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    image = real_parent / "frustrampnn.sif"
    image.write_bytes(b"qualified-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    leaf_link = real_parent / "linked.sif"
    leaf_link.symlink_to(image)
    parent_link = tmp_path / "linked-parent"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    for linked in (leaf_link, parent_link / image.name):
        with pytest.raises(RuntimeError, match="symlink|without following"):
            _open_verified_container(linked, digest)

    fd, actual = _open_verified_container(image, digest)
    assert actual == digest
    image.unlink()
    image.write_bytes(b"replacement-image")
    try:
        assert _sha256_fd(fd) == digest
        assert _sha256_fd(fd) != hashlib.sha256(image.read_bytes()).hexdigest()
        input_root = tmp_path / "descriptor-input"
        output_root = tmp_path / "descriptor-output"
        input_root.mkdir()
        output_root.mkdir()
        normalized = input_root / "normalized.pdb"
        normalized.write_text("ATOM\n", encoding="utf-8")
        argv = _frustrampnn_command(
            apptainer="apptainer", container=Path(f"/proc/self/fd/{fd}"),
            tool="/opt/venv/bin/frustrampnn", normalized=normalized,
            checkpoint=Path("/opt/frustrampnn_weights/megascale.ckpt"),
            raw=output_root / "raw.csv", output_root=output_root, gpu_id=3,
        )
        assert f"/proc/self/fd/{fd}" in argv
        assert argv[-2:] == ["--device", "cuda"]
    finally:
        os.close(fd)


def test_cm_frustrampnn_image_openat_walk_survives_interposed_ancestor_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    image = trusted / "frustrampnn.sif"
    image.write_bytes(b"qualified-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / image.name).write_bytes(b"attacker-image")
    moved = tmp_path / "trusted-open-generation"
    real_open = os.open
    swapped = False

    def interposed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == image.name and dir_fd is not None and not swapped:
            trusted.rename(moved)
            trusted.symlink_to(attacker, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", interposed_open)
    fd, actual = _open_verified_container(image, digest)
    try:
        assert swapped
        assert trusted.is_symlink()
        assert actual == digest
        assert _sha256_fd(fd) == digest
        assert hashlib.sha256((trusted / image.name).read_bytes()).hexdigest() != digest
    finally:
        os.close(fd)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_report(root: Path, role: str, run_id: str, *, status: str = "PASS") -> dict:
    path = root / f"{role}.json"
    path.write_bytes(_canonical({"current_run_id": run_id, "status": status, "remaining_production_gaps": []}))
    return {"role": role, "relative_path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _manifest(root: Path, run_id: str, records: list[dict]) -> Path:
    unsigned = {
        "schema_name": "cm_phase_evidence", "schema_version": 1, "phase": 12,
        "current_run_id": run_id, "principal_id": "operator", "captured_at": "2026-07-19T00:00:00Z",
        "command": ["bounded-current-run"], "exit_code": 0, "artifacts": records,
    }
    payload = {**unsigned, "authentication_hmac_sha256": hmac.new(KEY, _canonical(unsigned), hashlib.sha256).hexdigest()}
    path = root / "manifest.json"
    path.write_bytes(_canonical(payload))
    return path


def test_cm12_release_manifest_requires_all_authenticated_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_EVIDENCE_HMAC_KEY", KEY.decode())
    roles = ["api_contract_report", "persistence_report", "workflow_report", "security_report", "independent_review"]
    manifest = _manifest(tmp_path, "run-1", [_write_report(tmp_path, role, "run-1") for role in roles])
    review = adjudicate(12, tmp_path, manifest, tmp_path / "review.json")
    assert review["decision"] == "GO"
    assert all(item["passed"] for item in review["checks"])


def test_cm12_release_manifest_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_EVIDENCE_HMAC_KEY", KEY.decode())
    roles = ["api_contract_report", "persistence_report", "workflow_report", "security_report", "independent_review"]
    records = [_write_report(tmp_path, role, "run-1") for role in roles]
    manifest = _manifest(tmp_path, "run-1", records)
    (tmp_path / "workflow_report.json").write_text("{}")
    with pytest.raises(PhaseReviewError, match="byte identity"):
        adjudicate(12, tmp_path, manifest, tmp_path / "review.json")


def test_cm12_release_manifest_records_stop_for_factual_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_EVIDENCE_HMAC_KEY", KEY.decode())
    roles = ["api_contract_report", "persistence_report", "workflow_report", "security_report", "independent_review"]
    records = [_write_report(tmp_path, role, "run-1", status="STOP" if role == "workflow_report" else "PASS") for role in roles]
    review = adjudicate(12, tmp_path, _manifest(tmp_path, "run-1", records), tmp_path / "review.json")
    assert review["decision"] == "STOP"
    assert {item["check"] for item in review["checks"] if not item["passed"]} == {"workflow_report"}
