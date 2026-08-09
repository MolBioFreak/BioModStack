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
import rfc8785

from services.frustrampnn.configuration import execution_configuration
from services.frustrampnn.contracts import AA_ORDER, canonical_json_bytes, canonical_sha256
from services.frustrampnn.runtime import FRUSTRAMPNN_RUNTIME_IDENTITY, FrustraMPNNRuntimeIdentity
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    resolve_effective_settings,
)
from services.frustrampnn.structure import normalize_structure


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


def _multi_residue_pdb(residues: list[tuple[str, int]]) -> bytes:
    lines: list[str] = []
    serial = 1
    for chain, residue_id in residues:
        for atom, element in (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")):
            atom_field = f" {atom:<3}"
            lines.append(
                f"ATOM  {serial:5d} {atom_field} GLY {chain}{residue_id:4d}    "
                f"{serial:8.3f}{serial + 1:8.3f}{serial + 2:8.3f}"
                f"{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
            )
            serial += 1
    return "".join(lines).encode("ascii") + b"END\n"


def _v2_inputs(
    tmp_path: Path,
    *,
    residues: list[tuple[str, int]],
    selected: list[tuple[str, int]],
    thresholds: tuple[float, float] = (-0.5, 0.5),
) -> tuple[dict[str, object], Path, Path, dict[str, object]]:
    source = tmp_path / "source.pdb"
    source.write_bytes(_multi_residue_pdb(residues))
    normalized = tmp_path / "normalized_input.pdb"
    structure_map_path = tmp_path / "frustrampnn_structure_map_v1.json"
    structure_map = normalize_structure(
        input_path=source,
        output_pdb_path=normalized,
        map_path=structure_map_path,
        target_id="target-v2",
        parent_job_id="job-v2",
        candidate_id="candidate-v2",
        identity_authority={
            "kind": "pdb_self_identity_v1",
            "identity_domain": "candidate_local",
            "authority_artifact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        protein_selection={"mode": "all_protein_entities"},
        selected_model=1,
        altloc_policy="blank_or_explicit:<blank>",
    )
    by_normalized = {
        (row["pdb_chain_id"], row["model_position"]): row
        for row in structure_map["rows"]
        if row["status"] == "mapped"
    }
    selectors = []
    for key in selected:
        row = by_normalized[key]
        selectors.append({
            field: row[field]
            for field in (
                "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id",
                "auth_seq_id", "insertion_code", "sequence_index",
            )
        })
    requested = FrustraMPNNRequestedSettings.model_validate({
        "protein_selection": {
            "mode": "selected_residues",
            "entities": [],
            "residues": selectors,
        },
        "source_structure": {"selected_model_number": 1, "preferred_altloc": ""},
        "classification_policy": {
            "mode": "custom",
            "high_max": thresholds[0],
            "minimal_min": thresholds[1],
        },
    })
    effective = resolve_effective_settings(requested, structure_map)
    configuration = execution_configuration(effective)
    request: dict[str, object] = {
        "schema_name": "workflow_component_request",
        "schema_version": 2,
        "component_id": "frustrampnn",
        "component_contract_version": "2.0",
        "invocation_id": "invoke-v2",
        "parent_job_id": "job-v2",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-v2",
        "source_artifact": {
            "relative_path": "inputs/original.pdb",
            "sha256": structure_map["source_sha256"],
            "media_type": "chemical/x-pdb",
            "producer_stage": "prediction",
            "artifact_id": None,
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
            "structure_map", "raw_csv", "landscape", "summary", "execution_receipt",
        ],
    }
    return request, normalized, structure_map_path, structure_map


def _mock_v2_runtime(
    component,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    scores: dict[str, float] | None = None,
    fail_ordinal: int | None = None,
) -> list[list[str]]:
    runtime = importlib.import_module("services.frustrampnn.runtime")
    container = tmp_path / "mock.sif"
    container.write_bytes(b"mock")
    calls: list[list[str]] = []
    monkeypatch.setattr(runtime, "validate_configured_container_path", lambda *_args, **_kwargs: str(container))

    def open_container(*_args, **_kwargs):
        return runtime.PinnedContainer(
            os.open(container, os.O_RDONLY),
            FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
        )

    monkeypatch.setattr(runtime, "open_verified_container", open_container)
    monkeypatch.setattr(runtime, "verify_container_assets", lambda *_args, **_kwargs: {
        "executable_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256,
        "checkpoint_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256,
    })

    def execute(invocation, _pinned, **_kwargs):
        argv = list(invocation.argv)
        calls.append(argv)
        ordinal = len(calls) - 1
        if fail_ordinal == ordinal:
            return subprocess.CompletedProcess(argv, 17)
        binds = [argv[index + 1] for index, token in enumerate(argv) if token == "--bind"]
        output_root = Path(next(value.split(":", 1)[0] for value in binds if value.endswith(":/bms/output:rw")))
        output_name = Path(argv[argv.index("--output") + 1]).name
        chains = argv[argv.index("--chains") + 1].split(",")
        positions = [int(value) for value in argv[argv.index("--positions") + 1].split(",")]
        with (output_root / output_name).open("w", encoding="utf-8", newline="") as handle:
            writer = __import__("csv").DictWriter(
                handle,
                fieldnames=["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"],
                lineterminator="\n",
            )
            writer.writeheader()
            for chain in chains:
                for position in positions:
                    for mutation in AA_ORDER:
                        writer.writerow({
                            "frustration_pred": (scores or {}).get(mutation, 0.0),
                            "position": position,
                            "wildtype": "G",
                            "mutation": mutation,
                            "chain": chain,
                            "pdb": "normalized",
                        })
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runtime, "execute_frustrampnn", execute)
    return calls


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


@pytest.mark.parametrize("tamper", ["normalized", "structure_map"])
def test_v2_physical_input_tamper_fails_before_any_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    component = _component()
    request, normalized, structure_map_path, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
    )
    calls = _mock_v2_runtime(component, monkeypatch, tmp_path)
    target = normalized if tamper == "normalized" else structure_map_path
    target.write_bytes(target.read_bytes() + b"tampered\n")

    with pytest.raises(component.ComponentRunError, match="normalized|map|hash|tamper"):
        component.run_component(
            request=request,
            source_structure=normalized,
            structure_map=structure_map_path,
            output_dir=tmp_path / "candidate_bundle",
            container=tmp_path / "mock.sif",
            physical_gpu_id=3,
        )

    assert calls == []
    assert not (tmp_path / "candidate_bundle").exists()


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        (
            [("A", 0), ("B", 0)],
            [("A,B", "0")],
        ),
        (
            [("A", 0), ("A", 1), ("B", 0)],
            [("B", "0"), ("A", "0,1")],
        ),
    ],
)
def test_v2_exact_invocations_group_only_identical_position_tuples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: list[tuple[str, int]],
    expected: list[tuple[str, str]],
) -> None:
    component = _component()
    request, normalized, structure_map_path, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1), ("A", 2), ("B", 1), ("B", 2)],
        selected=selected,
    )
    calls = _mock_v2_runtime(component, monkeypatch, tmp_path)

    component.run_component(
        request=request,
        source_structure=normalized,
        structure_map=structure_map_path,
        output_dir=tmp_path / "candidate_bundle",
        container=tmp_path / "mock.sif",
        physical_gpu_id=3,
    )

    assert [
        (argv[argv.index("--chains") + 1], argv[argv.index("--positions") + 1])
        for argv in calls
    ] == expected
    receipt = json.loads(
        (tmp_path / "candidate_bundle/frustrampnn_execution_receipt_v2.json").read_text()
    )
    assert [record["argv"] for record in receipt["commands"]] == calls
    assert all(record["argv_sha256"] == canonical_sha256(record["argv"]) for record in receipt["commands"])


def test_v2_shard_failure_publishes_no_partial_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    request, normalized, structure_map_path, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1), ("A", 2), ("B", 1)],
        selected=[("A", 0), ("A", 1), ("B", 0)],
    )
    calls = _mock_v2_runtime(component, monkeypatch, tmp_path, fail_ordinal=1)

    with pytest.raises(component.ComponentRunError, match="nonzero|shard|inference"):
        component.run_component(
            request=request,
            source_structure=normalized,
            structure_map=structure_map_path,
            output_dir=tmp_path / "candidate_bundle",
            container=tmp_path / "mock.sif",
            physical_gpu_id=3,
        )

    assert len(calls) == 2
    assert not (tmp_path / "candidate_bundle").exists()
    assert not list(tmp_path.rglob("frustrampnn_result_manifest_v2.json"))
    assert not list(tmp_path.rglob("frustrampnn_statistics_v1.json"))


def test_v2_complete_bundle_classifies_custom_threshold_boundaries_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    request, normalized, structure_map_path, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
        thresholds=(-0.5, 0.5),
    )
    calls = _mock_v2_runtime(
        component,
        monkeypatch,
        tmp_path,
        scores={"A": -0.5, "C": 0.5},
    )
    output = tmp_path / "candidate_bundle"

    manifest = component.run_component(
        request=request,
        source_structure=normalized,
        structure_map=structure_map_path,
        output_dir=output,
        container=tmp_path / "mock.sif",
        physical_gpu_id=3,
    )

    from services.frustrampnn.manifests import (
        V2_CANONICAL_ARTIFACT_PATHS,
        V2_MANIFEST_PATH,
        validate_result_manifest,
    )

    assert len(calls) == 1
    assert sorted(path.name for path in output.iterdir()) == sorted(
        (*V2_CANONICAL_ARTIFACT_PATHS, V2_MANIFEST_PATH)
    )
    assert (output / "frustrampnn_structure_map_v1.json").is_file()
    assert not {
        "workflow_component_request_v1.json",
        "frustrampnn_landscape_v1.json",
        "frustrampnn_summary_v1.json",
        "frustrampnn_execution_receipt_v1.json",
        "workflow_component_result_v1.json",
        "frustrampnn_result_manifest_v1.json",
    }.intersection(path.name for path in output.iterdir())
    landscape = json.loads((output / "frustrampnn_landscape_v2.json").read_text())
    classes = {slot["mutation_aa"]: slot["class"] for slot in landscape["residues"][0]["slots"]}
    assert classes["A"] == "high"
    assert classes["C"] == "minimal"
    validate_result_manifest(output, manifest)


def test_v2_successful_bundle_emits_valid_attested_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.frustrampnn.analytics import (
        comparison_compatibility_id,
        validate_statistics_receipt,
    )

    component = _component()
    request, normalized, structure_map_path, structure_map = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
    )
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    output = tmp_path / "candidate_bundle"

    manifest = component.run_component(
        request=request,
        request_payload=canonical_json_bytes(request),
        source_structure=normalized,
        structure_map=structure_map_path,
        output_dir=output,
        container=tmp_path / "mock.sif",
        physical_gpu_id=3,
    )

    statistics_path = output / "frustrampnn_statistics_v1.json"
    statistics_bytes = statistics_path.read_bytes()
    statistics = json.loads(statistics_bytes)
    validate_statistics_receipt(statistics)
    assert statistics_bytes == rfc8785.dumps(statistics)
    without_self_hash = {
        key: value for key, value in statistics.items() if key != "statistics_sha256"
    }
    assert statistics["statistics_sha256"] == hashlib.sha256(
        rfc8785.dumps(without_self_hash)
    ).hexdigest()
    assert statistics["comparison_compatibility_id"] == comparison_compatibility_id(
        statistics["comparison_compatibility_basis"]
    )
    assert statistics["source_artifact_sha256"] == request["source_artifact"]["sha256"]
    assert statistics["structure_map"]["sha256"] == canonical_sha256(structure_map)

    records = {
        record["relative_path"]: record for record in manifest["artifacts"]
    }
    assert records["frustrampnn_statistics_v1.json"] == {
        "relative_path": "frustrampnn_statistics_v1.json",
        "schema_name": "frustrampnn_statistics",
        "schema_version": 1,
        "sha256": hashlib.sha256(statistics_bytes).hexdigest(),
        "bytes": len(statistics_bytes),
        "cardinality": {"kind": "records", "count": 1},
    }
    assert manifest["statistics_sha256"] == statistics["statistics_sha256"]
    assert manifest["comparison_compatibility_id"] == statistics[
        "comparison_compatibility_id"
    ]


def test_v1_rejects_v2_structure_map_argument_before_runtime(
    tmp_path: Path,
    stub_runtime,
) -> None:
    component = _component()
    apptainer, container, identity = stub_runtime
    source = tmp_path / "candidate.pdb"
    source.write_bytes(_one_residue_pdb())
    structure_map = tmp_path / "frustrampnn_structure_map_v1.json"
    structure_map.write_text("{}", encoding="utf-8")

    with pytest.raises(component.ComponentRunError, match="structure.map|v1|ambiguous"):
        component.run_component(
            request=_request(source),
            source_structure=source,
            structure_map=structure_map,
            output_dir=tmp_path / "candidate_bundle",
            container=container,
            apptainer=apptainer,
            physical_gpu_id=3,
            runtime_identity=identity,
        )

    assert not (tmp_path / "candidate_bundle").exists()


def test_v2_rejects_empty_request_payload_before_any_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    request, normalized, structure_map, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
    )
    calls = _mock_v2_runtime(component, monkeypatch, tmp_path)

    with pytest.raises(component.ComponentRunError, match="request_invalid|canonical JSON"):
        component.run_component(
            request=request,
            request_payload=b"",
            source_structure=normalized,
            structure_map=structure_map,
            output_dir=tmp_path / "candidate_bundle",
            container=tmp_path / "mock.sif",
            physical_gpu_id=3,
        )

    assert calls == []
    assert not (tmp_path / "candidate_bundle").exists()


def test_v2_oversized_runtime_log_publishes_no_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    request, normalized, structure_map, _ = _v2_inputs(
        tmp_path,
        residues=[("A", 1)],
        selected=[("A", 0)],
    )
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    runtime = importlib.import_module("services.frustrampnn.runtime")
    execute = runtime.execute_frustrampnn

    def execute_with_oversized_log(invocation, pinned, **kwargs):
        kwargs["stdout"].write(b"x" * (4 * 1024 * 1024 + 1))
        return execute(invocation, pinned, **kwargs)

    monkeypatch.setattr(runtime, "execute_frustrampnn", execute_with_oversized_log)
    output = tmp_path / "candidate_bundle"

    with pytest.raises(component.ComponentRunError, match="runtime_log_too_large"):
        component.run_component(
            request=request,
            source_structure=normalized,
            structure_map=structure_map,
            output_dir=output,
            container=tmp_path / "mock.sif",
            physical_gpu_id=3,
        )

    assert not output.exists()


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


def test_component_regular_reader_rejects_by_role_limit_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * 9)
    pread_calls: list[tuple[int, int]] = []
    real_pread = component.os.pread

    def recording_pread(descriptor: int, count: int, offset: int) -> bytes:
        pread_calls.append((count, offset))
        return real_pread(descriptor, count, offset)

    monkeypatch.setattr(component.os, "pread", recording_pread)

    with pytest.raises(component.ComponentRunError, match="too.large|read limit|9"):
        component._read_regular(oversized, label="raw shard", max_bytes=8)

    assert pread_calls == []


def test_component_regular_reader_detects_growth_without_allocating_limit_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = _component()
    path = tmp_path / "growing.bin"
    path.write_bytes(b"1234")
    real_pread = component.os.pread
    requested: list[int] = []

    def growing_pread(descriptor: int, count: int, offset: int) -> bytes:
        requested.append(count)
        payload = real_pread(descriptor, count, offset)
        if offset == 0:
            with path.open("ab") as handle:
                handle.write(b"5")
        return payload

    monkeypatch.setattr(component.os, "pread", growing_pread)

    with pytest.raises(component.ComponentRunError, match="changed|grew|identity"):
        component._read_regular(path, label="component request", max_bytes=4)

    assert max(requested) <= 4
