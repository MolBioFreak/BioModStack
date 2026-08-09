from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from services.frustrampnn.configuration import execution_configuration, request_parameters
from services.frustrampnn.contracts import canonical_json_bytes, canonical_sha256
from services.frustrampnn.settings import default_settings, resolve_effective_settings
from services.frustrampnn.structure import normalize_structure


REPO_ROOT = Path(__file__).resolve().parents[3]
PREPARE_PATH = REPO_ROOT / "scripts" / "prepare_persisted_frustrampnn_candidate.py"
MODULE_PATH = REPO_ROOT / "modules" / "frustrampnn.nf"
WORKFLOW_PATH = REPO_ROOT / "workflows" / "frustrampnn_analysis.nf"
NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.1"


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(name, None)
    return env


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    return subprocess.run(
        [docker, "image", "inspect", NEXTFLOW_IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_subprocess_env(),
        check=False,
    ).returncode == 0


def _prepare_module():
    spec = importlib.util.spec_from_file_location("prepare_persisted_frustrampnn_candidate", PREPARE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdb() -> bytes:
    return (
        b"ATOM      1  N   GLY A   1       1.000   2.000   3.000  1.00 20.00           N  \n"
        b"ATOM      2  CA  GLY A   1       2.000   3.000   4.000  1.00 20.00           C  \n"
        b"ATOM      3  C   GLY A   1       3.000   4.000   5.000  1.00 20.00           C  \n"
        b"ATOM      4  O   GLY A   1       4.000   5.000   6.000  1.00 20.00           O  \n"
        b"END\n"
    )


def _encoded(record: dict[str, object]) -> str:
    return base64.b64encode(canonical_json_bytes(record)).decode("ascii")


def _v1_packet(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    source = tmp_path / "v1-source.pdb"
    source.write_bytes(_pdb())
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    request = {
        "schema_name": "workflow_component_request",
        "schema_version": 1,
        "component_id": "frustrampnn",
        "component_contract_version": "1.0",
        "invocation_id": "historical-v1-invocation",
        "parent_job_id": "historical-v1-job",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "historical-v1-candidate",
        "source_artifact": {
            "relative_path": "inputs/sources/0000.pdb",
            "sha256": source_sha256,
            "media_type": "chemical/x-pdb",
            "producer_stage": "historical_prediction",
            "artifact_id": None,
        },
        "requiredness": "required",
        "identity_authority": "pdb_coordinates",
        "protein_selection": {"mode": "all_protein_entities"},
        "parameters": request_parameters(),
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }
    request_path = tmp_path / "historical-request.json"
    request_payload = canonical_json_bytes(request)
    request_path.write_bytes(request_payload)
    record: dict[str, object] = {
        "ordinal": 0,
        "candidate_id": request["candidate_id"],
        "invocation_id": request["invocation_id"],
        "request_relative_path": "inputs/requests/0000.json",
        "request_sha256": hashlib.sha256(request_payload).hexdigest(),
        "request_size_bytes": len(request_payload),
        "source_relative_path": request["source_artifact"]["relative_path"],
        "source_sha256": source_sha256,
        "source_size_bytes": source.stat().st_size,
        "launch_authority": {
            "schema_name": "frustrampnn_launch_authority",
            "schema_version": 1,
            "historical_provenance_only": True,
        },
    }
    return record, request_path, source


def _v2_packet(tmp_path: Path) -> tuple[dict[str, object], Path, Path, Path]:
    original = tmp_path / "original.pdb"
    original.write_bytes(_pdb())
    normalized = tmp_path / "normalized.pdb"
    structure_map_path = tmp_path / "map.json"
    structure_map = normalize_structure(
        input_path=original,
        output_pdb_path=normalized,
        map_path=structure_map_path,
        target_id="standalone-v2-job",
        parent_job_id="standalone-v2-job",
        candidate_id="standalone-v2-candidate",
        identity_authority={
            "kind": "pdb_self_identity_v1",
            "identity_domain": "candidate_local",
            "authority_artifact_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        },
        protein_selection={"mode": "all_protein_entities"},
        selected_model=1,
        altloc_policy="blank_or_explicit:<blank>",
    )
    requested = default_settings()
    effective = resolve_effective_settings(requested, structure_map)
    configuration = execution_configuration(effective)
    request = {
        "schema_name": "workflow_component_request",
        "schema_version": 2,
        "component_id": "frustrampnn",
        "component_contract_version": "2.0",
        "invocation_id": "standalone-v2-invocation",
        "parent_job_id": "standalone-v2-job",
        "parent_workflow_id": "frustrampnn_analysis",
        "candidate_id": "standalone-v2-candidate",
        "source_artifact": {
            "relative_path": "inputs/originals/0000.pdb",
            "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "media_type": "chemical/x-pdb",
            "producer_stage": "structure_prediction",
            "artifact_id": "design-1",
        },
        "requiredness": "required",
        "identity_authority": "pdb_coordinates",
        "settings_value_origin": requested.settings_value_origin,
        "requested_settings": requested.model_dump(mode="json", exclude_none=False),
        "requested_settings_sha256": effective.settings_sha256,
        "effective_settings": effective.model_dump(mode="json", exclude_none=False),
        "effective_settings_sha256": effective.effective_settings_sha256,
        "classification_policy_sha256": effective.threshold_policy_sha256,
        "capability_inventory_byte_sha256": effective.capability_inventory_byte_sha256,
        "runtime_identity_sha256": configuration.runtime_identity_sha256,
        "structure_map_sha256": canonical_sha256(structure_map),
        "normalized_pdb_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest(),
        "execution_configuration": configuration.model_dump(mode="json", exclude_none=False),
        "execution_configuration_sha256": configuration.configuration_sha256,
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }
    request_path = tmp_path / "request-v2.json"
    request_payload = canonical_json_bytes(request)
    request_path.write_bytes(request_payload)
    map_payload = structure_map_path.read_bytes()
    record: dict[str, object] = {
        "record_schema_name": "bms_frustrampnn_scheduler_record",
        "record_schema_version": 2,
        "ordinal": 0,
        "candidate_id": request["candidate_id"],
        "invocation_id": request["invocation_id"],
        "request_relative_path": "inputs/requests/0000/workflow_component_request_v2.json",
        "request_sha256": hashlib.sha256(request_payload).hexdigest(),
        "request_size_bytes": len(request_payload),
        "source_relative_path": "inputs/sources/0000/canonical_source.pdb",
        "source_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest(),
        "source_size_bytes": normalized.stat().st_size,
        "structure_map_relative_path": "inputs/maps/0000/frustrampnn_structure_map_v1.json",
        "structure_map_sha256": hashlib.sha256(map_payload).hexdigest(),
        "structure_map_size_bytes": len(map_payload),
    }
    return record, request_path, normalized, structure_map_path


def test_prepare_persisted_dispatches_historical_v1_and_exact_v2_files(tmp_path: Path) -> None:
    prepare = _prepare_module()
    v1_record, v1_request, v1_source = _v1_packet(tmp_path)
    v1_out_request = tmp_path / "v1-out" / "workflow_component_request_v1.json"
    v1_out_source = tmp_path / "v1-out" / "canonical_source.pdb"
    v1_out_request.parent.mkdir()
    prepare.prepare(
        record_base64=_encoded(v1_record),
        request_path=v1_request,
        source_path=v1_source,
        structure_map_path=None,
        output_request=v1_out_request,
        output_source=v1_out_source,
        output_structure_map=None,
    )
    assert v1_out_request.read_bytes() == v1_request.read_bytes()
    assert v1_out_source.read_bytes() == v1_source.read_bytes()

    v2_record, v2_request, v2_source, v2_map = _v2_packet(tmp_path)
    v2_out = tmp_path / "v2-out"
    v2_out.mkdir()
    outputs = (
        v2_out / "workflow_component_request_v2.json",
        v2_out / "canonical_source.pdb",
        v2_out / "frustrampnn_structure_map_v1.json",
    )
    prepare.prepare(
        record_base64=_encoded(v2_record),
        request_path=v2_request,
        source_path=v2_source,
        structure_map_path=v2_map,
        output_request=outputs[0],
        output_source=outputs[1],
        output_structure_map=outputs[2],
    )
    assert [path.read_bytes() for path in outputs] == [
        v2_request.read_bytes(),
        v2_source.read_bytes(),
        v2_map.read_bytes(),
    ]


def test_prepare_persisted_rejects_v2_map_tampering_before_staging(tmp_path: Path) -> None:
    prepare = _prepare_module()
    record, request, source, structure_map = _v2_packet(tmp_path)
    structure_map.write_bytes(structure_map.read_bytes() + b" ")
    output_root = tmp_path / "tampered-output"
    output_root.mkdir()

    with pytest.raises(ValueError, match="structure map (size|digest) binding"):
        prepare.prepare(
            record_base64=_encoded(record),
            request_path=request,
            source_path=source,
            structure_map_path=structure_map,
            output_request=output_root / "workflow_component_request_v2.json",
            output_source=output_root / "canonical_source.pdb",
            output_structure_map=output_root / "frustrampnn_structure_map_v1.json",
        )

    assert list(output_root.iterdir()) == []


def test_module_keeps_v1_consumer_and_adds_exact_file_only_v2_transport() -> None:
    module = MODULE_PATH.read_text(encoding="utf-8")
    assert "workflow CanonicalFrustraMPNN" in module
    assert "tuple val(component_request_meta), path(source_structure)" in module
    assert "--request-base64 '${request_base64}'" in module

    v2_process = module.split("process CanonicalFrustraMPNNV2Task", 1)[1].split(
        "workflow CanonicalFrustraMPNNV2", 1
    )[0]
    assert "tuple path(component_request), path(source_structure), path(structure_map)" in v2_process
    assert "--request '${component_request}'" in v2_process
    assert "--structure '${source_structure}'" in v2_process
    assert "--structure-map '${structure_map}'" in v2_process
    assert "--request-base64" not in v2_process
    assert "JsonSlurper" not in v2_process
    assert "frustrampnn_result_manifest_v2.json" in v2_process
    assert "workflow_component_result_v2.json" in v2_process
    assert "CUDA_VISIBLE_DEVICES='${assigned_gpu}'" in v2_process


def test_standalone_workflow_uses_exact_v2_three_file_tuple_and_terminal_names() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn'" in workflow
    assert (
        "tuple val(record_base64), path(request_snapshot), path(source_snapshot), "
        "path(structure_map_snapshot)"
    ) in workflow
    assert "tuple path('workflow_component_request_v2.json'), path('canonical_source.pdb')" in workflow
    assert "path('frustrampnn_structure_map_v1.json'), emit: prepared" in workflow
    assert "CanonicalFrustraMPNNV2(PreparePersistedFrustraMPNNCandidate.out.prepared)" in workflow
    assert "record.record_schema_version != 2" in workflow
    assert "record.launch_authority" not in workflow
    assert "workflow_component_request_v1.json" not in workflow
    module = MODULE_PATH.read_text(encoding="utf-8")
    assert "workflow_component_result_v2.json" in module
    assert "frustrampnn_result_manifest_v2.json" in module


@pytest.mark.runtime_integration
def test_v2_nextflow_stub_accepts_exact_three_file_tuple(tmp_path: Path) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned Nextflow image {NEXTFLOW_IMAGE}")

    _record, request, source, structure_map = _v2_packet(tmp_path)
    harness = tmp_path / "module-harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        "include { CanonicalFrustraMPNNV2 } from '/workspace/modules/frustrampnn'\n"
        "workflow {\n"
        "  inputs = Channel.of(tuple(file(params.request), file(params.source), file(params.structure_map)))\n"
        "  CanonicalFrustraMPNNV2(inputs)\n"
        "  CanonicalFrustraMPNNV2.out.result.view { result_meta, bundle, manifest -> "
        "\"PHASE3C_RESULT=${result_meta.candidate_id}|${bundle.name}|${manifest.name}\" }\n"
        "}\n",
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
            f"{tmp_path}:/run:rw",
            "-w",
            "/run",
            NEXTFLOW_IMAGE,
            "nextflow",
            "-log",
            "/run/module-nextflow.log",
            "run",
            "module-harness.nf",
            "-stub-run",
            "-offline",
            "--request",
            f"/run/{request.name}",
            "--source",
            f"/run/{source.name}",
            "--structure_map",
            f"/run/{structure_map.name}",
            "--frustrampnn_physical_gpu_id",
            "3",
            "--api_python",
            "/usr/bin/python3",
            "--code_root",
            "/workspace",
            "--container_dir",
            "/run",
            "-work-dir",
            "/run/work",
        ],
        env=_subprocess_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (
        "PHASE3C_RESULT=standalone-v2-candidate|candidate_bundle|frustrampnn_result_manifest_v2.json"
        in completed.stdout
    )


@pytest.mark.runtime_integration
def test_standalone_v2_nextflow_preview_compiles_exact_persisted_wiring(
    tmp_path: Path,
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned Nextflow image {NEXTFLOW_IMAGE}")

    record, request, source, structure_map = _v2_packet(tmp_path)
    job_root = tmp_path / "standalone-v2-job"
    for relative, payload_path in (
        (record["request_relative_path"], request),
        (record["source_relative_path"], source),
        (record["structure_map_relative_path"], structure_map),
    ):
        target = job_root / str(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload_path.read_bytes())
    manifest = job_root / "inputs" / "frustrampnn_scheduler_batch_v1.json"
    manifest.write_bytes(
        canonical_json_bytes(
            {
                "schema_name": "bms_frustrampnn_scheduler_batch",
                "schema_version": 2,
                "execution_owner_job_id": "standalone-v2-job",
                "records": [record],
            }
        )
    )
    parse_root = tmp_path / "parse-repo"
    (parse_root / "workflows").mkdir(parents=True)
    (parse_root / "modules").mkdir()
    (parse_root / "workflows" / WORKFLOW_PATH.name).write_bytes(WORKFLOW_PATH.read_bytes())
    (parse_root / "modules" / MODULE_PATH.name).write_bytes(MODULE_PATH.read_bytes())
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
            f"{tmp_path}:/run:rw",
            "-w",
            "/run/parse-repo",
            NEXTFLOW_IMAGE,
            "nextflow",
            "-log",
            "/run/standalone-nextflow.log",
            "run",
            "workflows/frustrampnn_analysis.nf",
            "-ansi-log",
            "false",
            "-preview",
            "-offline",
            "--job_id",
            "standalone-v2-job",
            "--frustrampnn_batch_manifest_path",
            "/run/standalone-v2-job/inputs/frustrampnn_scheduler_batch_v1.json",
            "--frustrampnn_physical_gpu_id",
            "2",
            "--out_dir",
            "/run/standalone-v2-job",
            "--api_python",
            "/usr/bin/python3",
            "--code_root",
            "/workspace",
            "--container_dir",
            "/run",
            "-work-dir",
            "/run/preview-work",
        ],
        env=_subprocess_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "ERROR ~" not in output
    assert "Cannot find script file" not in output
    parser_log = (tmp_path / "standalone-nextflow.log").read_text(encoding="utf-8")
    assert "Creating process 'PreparePersistedFrustraMPNNCandidate'" in parser_log
    assert (
        "Creating process 'CanonicalFrustraMPNNV2:CanonicalFrustraMPNNV2Task'"
        in parser_log
    )
    assert "/run/parse-repo/workflows/frustrampnn_analysis.nf" in parser_log
    assert "/run/parse-repo/modules/frustrampnn.nf" in parser_log
