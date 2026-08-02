from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from services.frustrampnn.contracts import AA_ORDER, canonical_json_bytes
from services.frustrampnn.runtime import FRUSTRAMPNN_RUNTIME_IDENTITY, FrustraMPNNRuntimeIdentity


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "modules" / "frustrampnn.nf"
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_frustrampnn_component.py"
CONFIG_PATH = REPO_ROOT / "nextflow.config"
API_PYTHON = Path("/home/dalab/.biomodstack-dev/runtime/cm-api-python/current/venv/bin/python")


def _component():
    assert SCRIPT_PATH.is_file(), "Phase 3 neutral CLI adapter is missing"
    return importlib.import_module("scripts.run_frustrampnn_component")


def _one_residue_pdb() -> bytes:
    lines = []
    for serial, (atom, element) in enumerate(
        (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1
    ):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} GLY A   1    "
            f"{serial:8.3f}{serial + 1:8.3f}{serial + 2:8.3f}"
            f"{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _request(source: Path, **updates: object) -> dict[str, object]:
    request: dict[str, object] = {
        "schema_name": "workflow_component_request",
        "schema_version": 1,
        "component_id": "frustrampnn",
        "component_contract_version": "1.0",
        "invocation_id": "invoke-stable-1",
        "parent_job_id": "job-stable-1",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-stable-1",
        "source_artifact": {
            "relative_path": "inputs/candidate.pdb",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "media_type": "chemical/x-pdb",
            "producer_stage": "prediction",
            "artifact_id": None,
        },
        "requiredness": "required",
        "identity_authority": "pdb_coordinates",
        "protein_selection": {"mode": "all_protein_entities"},
        "parameters": {
            "checkpoint_id": "stub.ckpt",
            "threshold_policy_id": "frustrampnn_class_v1",
            "selected_model_number": 1,
            "altloc_policy": "blank_or_explicit:<blank>",
        },
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }
    request.update(updates)
    return request


def _stub_identity(container: Path) -> FrustraMPNNRuntimeIdentity:
    return FrustraMPNNRuntimeIdentity(
        sif_name=container.name,
        configured_sif_path=str(container),
        sif_sha256=hashlib.sha256(container.read_bytes()).hexdigest(),
        executable_path="/opt/stub/bin/frustrampnn",
        executable_sha256="2" * 64,
        checkpoint_id="stub.ckpt",
        checkpoint_path="/opt/stub/weights/stub.ckpt",
        checkpoint_sha256="3" * 64,
        package_version="stub-1",
        source_commit="bbae1d03edf33dbe6f645d45c5604eb4464962ca",
        python_version="stub-python",
        pytorch_version="stub-torch",
        image_version="stub-image",
    )


def _write_stub_apptainer(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import csv, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if len(args) >= 4 and args[0] == 'exec' and args[2] == 'sha256sum':\n"
        "    assert pathlib.Path(args[1]).read_bytes()\n"
        "    target = args[3]\n"
        "    digest = os.environ['STUB_EXEC_SHA'] if target.endswith('/frustrampnn') else os.environ['STUB_CHECKPOINT_SHA']\n"
        "    print(digest, target)\n"
        "    raise SystemExit(0)\n"
        "capture = os.environ.get('STUB_CAPTURE')\n"
        "if capture:\n"
        "    with open(capture, 'a', encoding='utf-8') as handle: handle.write('model\\n')\n"
        "mode = os.environ.get('STUB_MODE', 'complete')\n"
        "if mode == 'nonzero':\n"
        "    print('intentional model failure', file=sys.stderr)\n"
        "    raise SystemExit(17)\n"
        "binds = [args[index + 1] for index, token in enumerate(args) if token == '--bind']\n"
        "output_root = pathlib.Path(next(value.split(':', 1)[0] for value in binds if value.endswith(':/bms/output:rw')))\n"
        "contained = pathlib.PurePosixPath(args[args.index('--output') + 1])\n"
        "output = output_root / contained.name\n"
        "fieldnames = ['frustration_pred','position','wildtype','mutation','chain','pdb']\n"
        "rows = [dict(frustration_pred='0.0', position='0', wildtype='G', mutation=aa, chain='A', pdb='normalized') for aa in 'ACDEFGHIKLMNPQRSTVWY']\n"
        "if mode == 'header': rows = []\n"
        "elif mode == 'partial': rows = rows[:-1]\n"
        "elif mode == 'nan': rows[0]['frustration_pred'] = 'NaN'\n"
        "elif mode == 'duplicate': rows.append(dict(rows[0]))\n"
        "with output.open('w', encoding='utf-8', newline='') as handle:\n"
        "    writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator='\\n')\n"
        "    writer.writeheader(); writer.writerows(rows)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.fixture
def stub_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    container = tmp_path / "frustrampnn-stub.sif"
    container.write_bytes(b"stub-sif-generation\n")
    apptainer = tmp_path / "apptainer"
    _write_stub_apptainer(apptainer)
    identity = _stub_identity(container)
    monkeypatch.setenv("STUB_EXEC_SHA", identity.executable_sha256)
    monkeypatch.setenv("STUB_CHECKPOINT_SHA", identity.checkpoint_sha256)
    runtime = importlib.import_module("services.frustrampnn.runtime")
    monkeypatch.setattr(runtime, "FRUSTRAMPNN_RUNTIME_IDENTITY", identity)
    return apptainer, container, identity


def test_phase3_cli_and_module_future_contract_exists() -> None:
    _component()
    module = MODULE_PATH.read_text(encoding="utf-8")
    config = CONFIG_PATH.read_text(encoding="utf-8")

    assert "process CanonicalFrustraMPNNTask" in module
    assert "workflow CanonicalFrustraMPNN" in module
    assert "tuple val(component_request_meta), path(source_structure)" in module
    assert "def component_result_meta = new JsonSlurper().parse(result_path)" in module
    assert "tuple(component_result_meta, candidate_bundle, result_manifest)" in module
    output_block = module.split("output:", 1)[1].split("script:", 1)[0]
    assert "val(component_request_meta)" not in output_block
    assert "path('candidate_bundle')" in module
    assert "path('candidate_bundle/frustrampnn_result_manifest_v1.json')" in module
    assert "label 'frustrampnn_gpu'" in module
    assert "${params.api_python}" in module
    assert "run_frustrampnn_component.py" in module
    assert "_runtime._open_regular_no_follow" not in SCRIPT_PATH.read_text(encoding="utf-8")
    assert "FrustrampnnQC" not in module
    assert "AggregateFrustrationReports" not in module
    assert "pandas" not in module
    assert "frustration_pred" not in module
    assert "MMCIFParser" not in module
    assert "withLabel: frustrampnn_gpu" in config


def test_preflight_rejects_unregistered_same_basename_host_sif(
    tmp_path: Path, stub_runtime,
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    alternate = tmp_path / "alternate" / container.name
    alternate.parent.mkdir()
    alternate.write_bytes(container.read_bytes())

    with pytest.raises(component.ComponentRunError, match="configured|registered|runtime_unavailable"):
        component.preflight_runtime(
            container=alternate,
            apptainer=apptainer,
            runtime_identity=identity,
        )


def test_invalid_request_fails_before_runtime_or_inference(
    tmp_path: Path, stub_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    request = _request(source, candidate_id="")
    capture = tmp_path / "capture.txt"
    monkeypatch.setenv("STUB_CAPTURE", str(capture))

    with pytest.raises(component.ComponentRunError, match="request_invalid"):
        component.run_component(
            request=request,
            source_structure=source,
            output_dir=tmp_path / "candidate_bundle",
            container=container,
            apptainer=apptainer,
            physical_gpu_id=3,
            runtime_identity=identity,
        )

    assert not capture.exists()
    assert not (tmp_path / "candidate_bundle").exists()


@pytest.mark.parametrize("mode", ["nonzero", "header", "partial", "nan", "duplicate"])
def test_stub_model_failures_never_publish_a_result_manifest(
    mode: str,
    tmp_path: Path,
    stub_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    monkeypatch.setenv("STUB_MODE", mode)

    with pytest.raises(component.ComponentRunError):
        component.run_component(
            request=_request(source),
            source_structure=source,
            output_dir=tmp_path / "candidate_bundle",
            container=container,
            apptainer=apptainer,
            physical_gpu_id=3,
            runtime_identity=identity,
        )

    assert not (tmp_path / "candidate_bundle").exists()
    assert not list(tmp_path.rglob("frustrampnn_result_manifest_v1.json"))


def test_stub_complete_output_publishes_closed_canonical_bundle(
    tmp_path: Path, stub_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    capture = tmp_path / "capture.txt"
    monkeypatch.setenv("STUB_CAPTURE", str(capture))
    output = tmp_path / "candidate_bundle"

    manifest = component.run_component(
        request=_request(source),
        source_structure=source,
        output_dir=output,
        container=container,
        apptainer=apptainer,
        physical_gpu_id=3,
        runtime_identity=identity,
    )

    from services.frustrampnn.contracts import canonical_json_loads
    from services.frustrampnn.manifests import (
        CANONICAL_ARTIFACT_PATHS,
        MANIFEST_PATH,
        validate_result_manifest,
    )

    validate_result_manifest(output, manifest)
    assert sorted(path.name for path in output.iterdir()) == sorted(
        (*CANONICAL_ARTIFACT_PATHS, MANIFEST_PATH)
    )
    request_on_disk = canonical_json_loads(
        (output / "workflow_component_request_v1.json").read_bytes()
    )
    result = canonical_json_loads(
        (output / "workflow_component_result_v1.json").read_bytes()
    )
    receipt = canonical_json_loads(
        (output / "frustrampnn_execution_receipt_v1.json").read_bytes()
    )
    landscape = canonical_json_loads(
        (output / "frustrampnn_landscape_v1.json").read_bytes()
    )
    assert request_on_disk["candidate_id"] == "candidate-stable-1"
    assert result["candidate_id"] == "candidate-stable-1"
    assert manifest["candidate_id"] == "candidate-stable-1"
    assert receipt["assigned_physical_gpu_id"] == "3"
    assert receipt["task_visible_device_index"] == 0
    assert "CUDA_VISIBLE_DEVICES=3" in receipt["argv"]
    assert receipt["argv"][-2:] == ["--device", "cuda"]
    assert "--gpu_id" not in receipt["argv"]
    assert "--gpu-id" not in receipt["argv"]
    assert len(landscape["residues"]) == 1
    assert [slot["mutation_aa"] for slot in landscape["residues"][0]["slots"]] == list(AA_ORDER)
    assert capture.read_text(encoding="utf-8").splitlines() == ["model"]


@pytest.mark.parametrize(
    "failure_target", ["published_log", "staging_directory", "output_parent_directory"],
)
def test_durability_failure_never_leaves_final_bundle(
    failure_target: str,
    tmp_path: Path,
    stub_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    output = tmp_path / "candidate_bundle"
    real_fsync = component.os.fsync
    injected = False

    def fail_selected_fsync(descriptor: int) -> None:
        nonlocal injected
        target = os.readlink(f"/proc/self/fd/{descriptor}")
        selected = (
            (failure_target == "published_log" and target.endswith("/frustrampnn_stdout.log"))
            or (failure_target == "staging_directory" and target.endswith("/candidate_bundle"))
            or (failure_target == "output_parent_directory" and target == str(tmp_path))
        )
        if selected and not injected:
            injected = True
            raise OSError(f"injected {failure_target} fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(component.os, "fsync", fail_selected_fsync)
    with pytest.raises(component.ComponentRunError, match="publication_failed"):
        component.run_component(
            request=_request(source),
            source_structure=source,
            output_dir=output,
            container=container,
            apptainer=apptainer,
            physical_gpu_id=3,
            runtime_identity=identity,
        )
    assert injected is True
    assert not output.exists()


def test_external_authority_bytes_are_request_bound_and_published_exactly(
    tmp_path: Path, stub_runtime
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    authority_bytes = canonical_json_bytes({
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "entities": [{
            "entity_type": "protein",
            "entity_instance_id": "producer:protein-1",
            "source_entity_id": "1",
            "label_asym_id": "AA",
            "auth_asym_id": "A",
            "sequence": "G",
            "residue_mappings": [{
                "auth_seq_id": 1,
                "insertion_code": "",
                "label_seq_id": 1,
            }],
        }],
    })
    request = _request(
        source,
        identity_authority="producer_manifest",
        identity_authority_artifact={
            "relative_path": "authority_artifact_v1.json",
            "media_type": "application/json",
            "sha256": hashlib.sha256(authority_bytes).hexdigest(),
            "canonical_json_base64": base64.b64encode(authority_bytes).decode("ascii"),
        },
    )
    output = tmp_path / "candidate_bundle"

    manifest = component.run_component(
        request=request,
        source_structure=source,
        output_dir=output,
        container=container,
        apptainer=apptainer,
        physical_gpu_id=3,
        runtime_identity=identity,
    )

    from services.frustrampnn.manifests import validate_result_manifest

    assert (output / "authority_artifact_v1.json").read_bytes() == authority_bytes
    assert manifest["artifact_count"] == 11
    validate_result_manifest(output, manifest)


@pytest.mark.parametrize(
    "mutation", [
        "digest", "noncanonical", "duplicate_key", "source", "self_extra", "self_null",
    ],
)
def test_invalid_request_bound_authority_never_reaches_runtime(
    mutation: str, tmp_path: Path, stub_runtime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    authority = {
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": "f" * 64 if mutation == "source" else source_hash,
        "entities": [],
    }
    if mutation == "noncanonical":
        payload = json.dumps(authority, indent=2).encode("utf-8")
    elif mutation == "duplicate_key":
        payload = (
            b'{"entities":[],"schema_name":"producer_manifest",'
            b'"schema_name":"producer_manifest","schema_version":1,'
            + f'"source_sha256":"{source_hash}"'.encode("ascii") + b"}\n"
        )
    else:
        payload = canonical_json_bytes(authority)
    envelope = {
        "relative_path": "authority_artifact_v1.json",
        "media_type": "application/json",
        "sha256": "0" * 64 if mutation == "digest" else hashlib.sha256(payload).hexdigest(),
        "canonical_json_base64": base64.b64encode(payload).decode("ascii"),
    }
    if mutation in {"self_extra", "self_null"}:
        request = _request(
            source,
            identity_authority_artifact=None if mutation == "self_null" else envelope,
        )
    else:
        request = _request(
            source,
            identity_authority="producer_manifest",
            identity_authority_artifact=envelope,
        )
    capture = tmp_path / "capture.txt"
    monkeypatch.setenv("STUB_CAPTURE", str(capture))

    with pytest.raises(component.ComponentRunError, match="request_invalid"):
        component.run_component(
            request=request,
            source_structure=source,
            output_dir=tmp_path / "candidate_bundle",
            container=container,
            apptainer=apptainer,
            physical_gpu_id=3,
            runtime_identity=identity,
        )

    assert not capture.exists()
    assert not (tmp_path / "candidate_bundle").exists()


def test_nextflow_stub_smoke_preserves_candidate_identity(tmp_path: Path) -> None:
    _component()
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", str(Path.home() / ".local/bin/nextflow")))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"real Nextflow launcher unavailable: {nextflow}")

    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    request = _request(source)
    request_literal = json.dumps(request, sort_keys=True, separators=(",", ":"))
    local_module = tmp_path / "frustrampnn.nf"
    local_module.write_bytes(MODULE_PATH.read_bytes())
    harness = tmp_path / "harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        "include { CanonicalFrustraMPNN } from './frustrampnn'\n"
        "workflow {\n"
        f"  request = new groovy.json.JsonSlurper().parseText({request_literal!r})\n"
        "  inputs = Channel.of(tuple(request, file(params.source)))\n"
        "  CanonicalFrustraMPNN(inputs)\n"
        "  CanonicalFrustraMPNN.out.result.view { result_meta, bundle, manifest -> "
        "\"PHASE3_RESULT=${result_meta.candidate_id}|${bundle.name}|${manifest.name}\" }\n"
        "}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["NXF_HOME"] = str(Path.home() / ".nextflow")
    env["NXF_OFFLINE"] = "true"
    env.pop("SSL_CERT_FILE", None)
    env.pop("CURL_CA_BUNDLE", None)
    completed = subprocess.run(
        [
            str(nextflow),
            "run",
            str(harness),
            "-stub-run",
            "--source",
            str(source),
            "--frustrampnn_physical_gpu_id",
            "3",
            "--api_python",
            str(API_PYTHON),
            "--code_root",
            str(REPO_ROOT),
            "--container_dir",
            str(tmp_path),
            "-work-dir",
            str(tmp_path / "work"),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PHASE3_RESULT=candidate-stable-1|candidate_bundle|frustrampnn_result_manifest_v1.json" in completed.stdout

    unassigned_args = list(completed.args)
    option_index = unassigned_args.index("--frustrampnn_physical_gpu_id")
    del unassigned_args[option_index:option_index + 2]
    unassigned_args[unassigned_args.index(str(tmp_path / "work"))] = str(tmp_path / "work-unassigned")
    unassigned = subprocess.run(
        unassigned_args,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert unassigned.returncode != 0
    assert "explicit scheduler-assigned frustrampnn_physical_gpu_id" in (
        unassigned.stdout + unassigned.stderr
    )


def test_preflight_uses_provisioned_api_python_without_model_inference() -> None:
    assert API_PYTHON.is_file() and os.access(API_PYTHON, os.X_OK)
    container = Path("/mnt/BioModStack/apptainer/frustrampnn.sif")
    apptainer = shutil.which("apptainer")
    assert container.is_file() and apptainer is not None
    completed = subprocess.run(
        [
            str(API_PYTHON), str(SCRIPT_PATH),
            "--preflight-only",
            "--container", str(container),
            "--apptainer", apptainer,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt == {
        "checkpoint_id": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id,
        "checkpoint_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256,
        "executable_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256,
        "schema_name": "frustrampnn_runtime_preflight",
        "schema_version": 1,
        "sif_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
        "status": "ready",
    }
