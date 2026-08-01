from __future__ import annotations

import copy
import base64
import csv
import hashlib
import importlib
import importlib.util
import inspect
import io
from pathlib import Path

import pytest

from services.frustrampnn.contracts import (
    AA_ORDER,
    canonical_json_bytes,
    canonical_sha256,
    request_sha256,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _manifests():
    path = REPO_ROOT / "platform/api/services/frustrampnn/manifests.py"
    assert path.is_file(), "neutral FrustraMPNN manifest core is missing"
    return importlib.import_module("services.frustrampnn.manifests")


def _pdb() -> bytes:
    lines = []
    for serial, (atom, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} GLY A   1    {serial:8.3f}{serial + 1:8.3f}"
            f"{serial + 2:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _write_json(root: Path, name: str, value: dict) -> bytes:
    payload = canonical_json_bytes(value)
    (root / name).write_bytes(payload)
    return payload


def _bundle(root: Path) -> None:
    source_hash = hashlib.sha256(_pdb()).hexdigest()
    request = {
        "schema_name": "workflow_component_request", "schema_version": 1,
        "component_id": "frustrampnn", "component_contract_version": "1.0",
        "invocation_id": "invoke-1", "parent_job_id": "job-1",
        "parent_workflow_id": "structure_prediction", "candidate_id": "candidate-1",
        "source_artifact": {"relative_path": "inputs/candidate.pdb", "sha256": source_hash,
                            "media_type": "chemical/x-pdb", "producer_stage": "prediction",
                            "artifact_id": None},
        "requiredness": "required", "identity_authority": "pdb_coordinates",
        "protein_selection": {"mode": "all_protein_entities"},
        "parameters": {"checkpoint_id": "megascale.ckpt",
                       "threshold_policy_id": "frustrampnn_threshold_v1",
                       "selected_model_number": 1, "altloc_policy": "blank_or_explicit:A"},
        "requested_outputs": ["structure_map", "raw_csv", "landscape", "summary", "execution_receipt"],
    }
    _write_json(root, "workflow_component_request_v1.json", request)

    pdb = _pdb()
    (root / "normalized_input.pdb").write_bytes(pdb)
    normalized_hash = hashlib.sha256(pdb).hexdigest()
    row = {
        "entity_instance_id": "pdb:A", "source_entity_id": None, "label_asym_id": None,
        "auth_asym_id": "A", "label_seq_id": None, "auth_seq_id": 1,
        "insertion_code": "", "sequence_index": 1, "pdb_chain_id": "A",
        "pdb_residue_id": 1, "pdb_insertion_code": "", "model_position": 0,
        "residue_name": "GLY", "wt": "G", "selected_model": 1,
        "selected_altloc": "", "backbone_complete": True,
        "backbone_atoms": {name: f"pdb:{name}" for name in ("N", "CA", "C", "O")},
        "status": "mapped", "reason": None,
    }
    sequence_hash = hashlib.sha256(b"G").hexdigest()
    structure_map = {
        "schema_name": "frustrampnn_structure_map", "schema_version": 1,
        "target_id": "target-1", "parent_job_id": "job-1", "candidate_id": "candidate-1",
        "source_format": "pdb", "source_sha256": source_hash, "source_bytes": 400,
        "identity_authority": "pdb_self_identity_v1", "identity_domain": "candidate_local",
        "authority_artifact_sha256": source_hash, "normalized_pdb_sha256": normalized_hash,
        "selected_source_model": 1, "altloc_policy": "blank_or_explicit:A",
        "normalizer_version": "frustrampnn_structure_normalizer_v1",
        "model_ready_sequence": "G", "model_ready_sequence_sha256": sequence_hash,
        "excluded_records": [], "rows": [row],
    }
    _write_json(root, "frustrampnn_structure_map_v1.json", structure_map)

    raw_handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        raw_handle,
        fieldnames=["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows({"frustration_pred": 0.0, "position": 0, "wildtype": "G",
                      "mutation": aa, "chain": "A", "pdb": "normalized_input"} for aa in AA_ORDER)
    raw = raw_handle.getvalue().encode()
    (root / "raw_frustrampnn.csv").write_bytes(raw)
    raw_hash = hashlib.sha256(raw).hexdigest()

    slots = [{"mutation_aa": aa, "score": 0.0, "class": "neutral", "scoreable": True,
              "status": "ok", "reason": None, "native": aa == "G"} for aa in AA_ORDER]
    landscape_row = {key: value for key, value in row.items()
                     if key not in {"selected_model", "selected_altloc", "backbone_complete",
                                    "backbone_atoms", "status", "reason"}}
    landscape_row["slots"] = slots
    policy = {"id": "frustrampnn_threshold_v1", "high_max": -1.0, "minimal_min": 0.58}
    landscape = {
        "schema_name": "frustrampnn_landscape", "schema_version": 1,
        "target_id": "target-1", "parent_job_id": "job-1", "candidate_id": "candidate-1",
        "structure_map_sha256": canonical_sha256(structure_map),
        "normalized_pdb_sha256": normalized_hash, "model_ready_sequence_sha256": sequence_hash,
        "raw_csv_sha256": raw_hash, "threshold_policy": policy,
        "threshold_policy_sha256": canonical_sha256(policy), "residues": [landscape_row],
    }
    _write_json(root, "frustrampnn_landscape_v1.json", landscape)

    summary = {
        "schema_name": "frustrampnn_summary", "schema_version": 1,
        "target_id": "target-1", "parent_job_id": "job-1", "candidate_id": "candidate-1",
        "landscape_sha256": canonical_sha256(landscape),
        "residue_support": {"expected": 1, "mapped": 1, "scoreable": 1, "excluded": 0, "ambiguous": 0},
        "slot_support": {"expected": 20, "observed": 20, "scoreable": 20},
        "missingness_by_reason": {},
        "native_slot_counts": {"high": 0, "neutral": 1, "minimal": 0},
        "native_slot_fractions": {"high": 0.0, "neutral": 1.0, "minimal": 0.0},
        "complete_landscape_counts": {"high": 0, "neutral": 20, "minimal": 0},
        "complete_landscape_fractions": {"high": 0.0, "neutral": 1.0, "minimal": 0.0},
        "support_by_entity_chain": [{"entity_instance_id": "pdb:A", "auth_asym_id": "A",
            "expected_residues": 1, "mapped_residues": 1, "scoreable_residues": 1,
            "expected_slots": 20, "observed_slots": 20, "scoreable_slots": 20}],
        "threshold_policy": policy, "threshold_policy_sha256": canonical_sha256(policy),
    }
    _write_json(root, "frustrampnn_summary_v1.json", summary)

    receipt = {
        "schema_name": "frustrampnn_execution_receipt", "schema_version": 1,
        "invocation_id": "invoke-1", "argv": [
            "apptainer", "exec", "--containall", "--writable-tmpfs", "--nv",
            "--env", "CUDA_DEVICE_ORDER=PCI_BUS_ID",
            "--env", "CUDA_VISIBLE_DEVICES=3",
            "--bind", "/work/normalized_input.pdb:/bms/input/normalized.pdb:ro",
            "--bind", "/work/output:/bms/output:rw",
            "/proc/self/fd/41", "/opt/venv/bin/frustrampnn", "predict",
            "--pdb", "/bms/input/normalized.pdb",
            "--checkpoint", "/opt/frustrampnn_weights/megascale.ckpt",
            "--output", "/bms/output/raw_frustrampnn.csv", "--device", "cuda",
        ],
        "working_directory_policy": "apptainer_containall_v1",
        "bind_policy": [
            "/work/normalized_input.pdb:/bms/input/normalized.pdb:ro",
            "/work/output:/bms/output:rw",
        ],
        "sif_path": "/proc/self/fd/41",
        "configured_sif_path": "/mnt/BioModStack/apptainer/frustrampnn.sif",
        "sif_sha256": "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da",
        "executable_path": "/opt/venv/bin/frustrampnn",
        "executable_sha256": "32089d959f619c08a550c0e7d0fc7b66b508d009ec3179d007f13773a170212f",
        "checkpoint_path": "/opt/frustrampnn_weights/megascale.ckpt",
        "checkpoint_id": "megascale.ckpt",
        "checkpoint_sha256": "eaee71adb7eec366fc672d2aadef87f2c51243042a4518cd897634784dc2da3b",
        "input_sha256": source_hash,
        "normalized_pdb_sha256": normalized_hash, "raw_csv_sha256": raw_hash,
        "landscape_sha256": canonical_sha256(landscape), "summary_sha256": canonical_sha256(summary),
        "assigned_physical_gpu_id": "3", "task_visible_device_index": 0, "exit_code": 0,
        "stdout_artifact": "frustrampnn_stdout.log",
        "stderr_artifact": "frustrampnn_stderr.log",
        "started_at": "2026-07-30T12:00:00Z", "ended_at": "2026-07-30T12:00:01Z",
        "duration_seconds": 1.0, "software_versions": {
            "frustrampnn": "1.0.0", "adapter": "run_frustrampnn_component_v1",
            "normalizer": "frustrampnn_structure_normalizer_v1",
            "finalizer": "frustrampnn_landscape_finalizer_v1",
            "source_commit": "bbae1d03edf33dbe6f645d45c5604eb4464962ca",
            "python": "3.10.12", "pytorch": "2.11.0.dev20260126+cu128",
            "image": "1.3"},
    }
    (root / "frustrampnn_stdout.log").write_bytes(b"model stdout\n")
    (root / "frustrampnn_stderr.log").write_bytes(b"")
    _write_json(root, "frustrampnn_execution_receipt_v1.json", receipt)

    result_artifact_specs = (
        ("normalized_input.pdb", None, None, {"kind": "residues", "count": 1}),
        ("frustrampnn_structure_map_v1.json", "frustrampnn_structure_map", 1, {"kind": "residues", "count": 1}),
        ("raw_frustrampnn.csv", None, None, {"kind": "rows", "count": 20}),
        ("frustrampnn_landscape_v1.json", "frustrampnn_landscape", 1, {"kind": "residues", "count": 1}),
        ("frustrampnn_summary_v1.json", "frustrampnn_summary", 1, {"kind": "records", "count": 1}),
        ("frustrampnn_stdout.log", None, None, None),
        ("frustrampnn_stderr.log", None, None, None),
        ("frustrampnn_execution_receipt_v1.json", "frustrampnn_execution_receipt", 1, {"kind": "records", "count": 1}),
    )
    artifacts = []
    for path, schema_name, schema_version, cardinality in result_artifact_specs:
        payload = (root / path).read_bytes()
        artifacts.append({"relative_path": path, "schema_name": schema_name,
                          "schema_version": schema_version, "sha256": hashlib.sha256(payload).hexdigest(),
                          "bytes": len(payload),
                          "cardinality": dict(cardinality) if cardinality is not None else None})
    result = {
        "schema_name": "workflow_component_result", "schema_version": 1,
        "request_sha256": request_sha256(request), "invocation_id": "invoke-1", "component_id": "frustrampnn",
        "component_contract_version": "1.0", "candidate_id": "candidate-1",
        "parent_job_id": "job-1", "parent_workflow_id": "structure_prediction",
        "status": "succeeded", "failure_class": None, "diagnostic": None,
        "source_artifact": request["source_artifact"],
        "runtime_identity": {
            "sif_sha256": receipt["sif_sha256"],
            "executable_sha256": receipt["executable_sha256"],
            "checkpoint_id": receipt["checkpoint_id"],
            "checkpoint_sha256": receipt["checkpoint_sha256"],
        },
        "artifacts": artifacts,
        "result_payload": {"schema_name": "frustrampnn_summary", "schema_version": 1},
        "started_at": receipt["started_at"], "ended_at": receipt["ended_at"],
        "duration_seconds": receipt["duration_seconds"],
        "assigned_gpu": {"physical_device_id": "3", "task_visible_device_index": 0},
    }
    _write_json(root, "workflow_component_result_v1.json", result)


def _build(root: Path):
    return _manifests().build_result_manifest(root)


def _rehash_bundle(root: Path) -> None:
    """Rebind all cryptographic links after a hostile physical/semantic mutation."""
    from services.frustrampnn.contracts import canonical_json_loads

    def load(name: str) -> dict:
        return canonical_json_loads((root / name).read_bytes())

    request = load("workflow_component_request_v1.json")
    structure = load("frustrampnn_structure_map_v1.json")
    structure["normalized_pdb_sha256"] = hashlib.sha256((root / "normalized_input.pdb").read_bytes()).hexdigest()
    _write_json(root, "frustrampnn_structure_map_v1.json", structure)
    landscape = load("frustrampnn_landscape_v1.json")
    landscape["structure_map_sha256"] = canonical_sha256(structure)
    landscape["normalized_pdb_sha256"] = structure["normalized_pdb_sha256"]
    landscape["raw_csv_sha256"] = hashlib.sha256((root / "raw_frustrampnn.csv").read_bytes()).hexdigest()
    _write_json(root, "frustrampnn_landscape_v1.json", landscape)
    summary = load("frustrampnn_summary_v1.json")
    summary["landscape_sha256"] = canonical_sha256(landscape)
    _write_json(root, "frustrampnn_summary_v1.json", summary)
    receipt = load("frustrampnn_execution_receipt_v1.json")
    receipt["normalized_pdb_sha256"] = structure["normalized_pdb_sha256"]
    receipt["raw_csv_sha256"] = landscape["raw_csv_sha256"]
    receipt["landscape_sha256"] = canonical_sha256(landscape)
    receipt["summary_sha256"] = canonical_sha256(summary)
    _write_json(root, "frustrampnn_execution_receipt_v1.json", receipt)
    result = load("workflow_component_result_v1.json")
    result["request_sha256"] = request_sha256(request)
    result["source_artifact"] = request["source_artifact"]
    for record in result["artifacts"]:
        payload = (root / record["relative_path"]).read_bytes()
        record["sha256"] = hashlib.sha256(payload).hexdigest()
        record["bytes"] = len(payload)
    _write_json(root, "workflow_component_result_v1.json", result)


def test_atomic_publisher_replays_exact_bundle_and_rejects_contradiction(tmp_path: Path) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    script = REPO_ROOT / "scripts" / "publish_frustrampnn_bundle.py"
    spec = importlib.util.spec_from_file_location("publish_frustrampnn_bundle_test", script)
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)

    source = tmp_path / "source"
    source.mkdir()
    _bundle(source)
    (source / publisher.MANIFEST_PATH).write_bytes(canonical_json_bytes(_build(source)))
    allowed = tmp_path / "job"
    allowed.mkdir()
    destination = allowed / "frustrampnn" / "results" / "candidate-1"
    marker = tmp_path / "published.json"
    first = publisher.publish(
        source_bundle=source, allowed_root=allowed, destination=destination, marker=marker,
    )
    assert canonical_json_loads(marker.read_bytes()) == first
    assert Path(first["source"]).read_bytes() == (source / "normalized_input.pdb").read_bytes()
    publisher.publish(
        source_bundle=source, allowed_root=allowed, destination=destination, marker=marker,
    )

    contradictory = tmp_path / "contradictory"
    contradictory.mkdir()
    _bundle(contradictory)
    (contradictory / "frustrampnn_stdout.log").write_text("different immutable bytes\n")
    _rehash_bundle(contradictory)
    (contradictory / publisher.MANIFEST_PATH).write_bytes(
        canonical_json_bytes(_build(contradictory))
    )
    with pytest.raises(ValueError, match="contradicts immutable candidate authority"):
        publisher.publish(
            source_bundle=contradictory,
            allowed_root=allowed,
            destination=destination,
            marker=tmp_path / "contradictory-marker.json",
        )


def test_atomic_publisher_removes_new_bundle_when_source_conflicts(tmp_path: Path) -> None:
    from services.frustrampnn.contracts import canonical_json_bytes

    source = tmp_path / "bundle"
    source.mkdir()
    _bundle(source)
    _write_json(source, "frustrampnn_result_manifest_v1.json", _build(source))
    publisher_path = Path(__file__).resolve().parents[3] / "scripts" / "publish_frustrampnn_bundle.py"
    spec = importlib.util.spec_from_file_location("publish_frustrampnn_bundle_conflict", publisher_path)
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)

    allowed = tmp_path / "published"
    conflicting_source = allowed / "inputs" / "candidate.pdb"
    conflicting_source.parent.mkdir(parents=True)
    conflicting_source.write_bytes(b"conflicting canonical source\n")
    destination = allowed / "frustrampnn" / "results" / "candidate-1"
    marker = tmp_path / "must-not-exist.json"

    with pytest.raises(ValueError, match="canonical source contradicts"):
        publisher.publish(
            source_bundle=source,
            allowed_root=allowed,
            destination=destination,
            marker=marker,
        )
    assert not destination.exists()
    assert conflicting_source.read_bytes() == b"conflicting canonical source\n"
    assert not marker.exists()


def test_atomic_publisher_rolls_back_new_source_and_bundle_when_marker_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    _bundle(source)
    _write_json(source, "frustrampnn_result_manifest_v1.json", _build(source))
    publisher_path = REPO_ROOT / "scripts" / "publish_frustrampnn_bundle.py"
    spec = importlib.util.spec_from_file_location("publish_frustrampnn_bundle_marker_failure", publisher_path)
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    allowed = tmp_path / "published"
    allowed.mkdir()
    destination = allowed / "frustrampnn" / "results" / "candidate-1"
    canonical_source = allowed / "inputs" / "candidate.pdb"
    marker = tmp_path / "must-not-exist.json"

    monkeypatch.setattr(publisher.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("marker failure")))
    with pytest.raises(OSError, match="marker failure"):
        publisher.publish(
            source_bundle=source, allowed_root=allowed, destination=destination, marker=marker,
        )
    assert not destination.exists()
    assert not canonical_source.exists()
    assert not marker.exists()
    assert not list(tmp_path.glob(".must-not-exist.json.tmp-*"))


@pytest.mark.parametrize(
    "mutation",
    ["identity_authority", "media_type", "selected_model", "altloc", "sequence_hash"],
)
def test_manifest_rejects_fully_rehashed_request_to_artifact_contradictions(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    if mutation in {"identity_authority", "media_type", "selected_model", "altloc"}:
        path = tmp_path / "workflow_component_request_v1.json"
        request = canonical_json_loads(path.read_bytes())
        if mutation == "identity_authority":
            request["identity_authority"] = "producer_manifest"
        elif mutation == "media_type":
            request["source_artifact"]["media_type"] = "chemical/x-mmcif"
        elif mutation == "selected_model":
            request["parameters"]["selected_model_number"] = 2
        else:
            request["parameters"]["altloc_policy"] = "blank_or_explicit:B"
        path.write_bytes(canonical_json_bytes(request))
    else:
        path = tmp_path / "frustrampnn_landscape_v1.json"
        landscape = canonical_json_loads(path.read_bytes())
        landscape["model_ready_sequence_sha256"] = hashlib.sha256(b"A").hexdigest()
        path.write_bytes(canonical_json_bytes(landscape))
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="authority|media|format|model|altloc|sequence"):
        _build(tmp_path)


def test_manifest_rejects_fully_rehashed_receipt_with_unbound_runtime_argv(tmp_path: Path) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    path = tmp_path / "frustrampnn_execution_receipt_v1.json"
    receipt = canonical_json_loads(path.read_bytes())
    receipt["argv"] = ["/bin/false", "--not-the-frustrampnn-runtime"]
    path.write_bytes(canonical_json_bytes(receipt))
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="argv|command|executable|runtime"):
        _build(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "containall", "physical_env", "bind_mode", "gpu_id", "working_policy",
        "bind_policy", "checkpoint_path", "checkpoint_id", "checkpoint_hash",
        "stdout", "physical_receipt", "sif_path", "configured_sif_path", "executable",
    ],
)
def test_manifest_rejects_fully_rehashed_receipt_outside_exact_launcher_grammar(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    receipt_path = tmp_path / "frustrampnn_execution_receipt_v1.json"
    result_path = tmp_path / "workflow_component_result_v1.json"
    request_path = tmp_path / "workflow_component_request_v1.json"
    receipt = canonical_json_loads(receipt_path.read_bytes())
    result = canonical_json_loads(result_path.read_bytes())
    request = canonical_json_loads(request_path.read_bytes())
    if mutation == "containall":
        receipt["argv"].remove("--containall")
    elif mutation == "physical_env":
        index = receipt["argv"].index("CUDA_VISIBLE_DEVICES=3")
        receipt["argv"][index] = "CUDA_VISIBLE_DEVICES=4"
    elif mutation == "bind_mode":
        index = receipt["argv"].index(receipt["bind_policy"][0])
        receipt["argv"][index] = receipt["bind_policy"][0].removesuffix(":ro") + ":rw"
    elif mutation == "gpu_id":
        receipt["argv"].extend(["--gpu_id", "3"])
    elif mutation == "working_policy":
        receipt["working_directory_policy"] = "isolated_workdir_v1"
    elif mutation == "bind_policy":
        receipt["bind_policy"] = list(reversed(receipt["bind_policy"]))
    elif mutation == "checkpoint_path":
        old = receipt["checkpoint_path"]
        receipt["checkpoint_path"] = "/tmp/forged.ckpt"
        receipt["argv"][receipt["argv"].index(old)] = receipt["checkpoint_path"]
    elif mutation == "checkpoint_id":
        receipt["checkpoint_id"] = "forged.ckpt"
        request["parameters"]["checkpoint_id"] = receipt["checkpoint_id"]
        result["runtime_identity"]["checkpoint_id"] = receipt["checkpoint_id"]
    elif mutation == "checkpoint_hash":
        receipt["checkpoint_sha256"] = "f" * 64
        result["runtime_identity"]["checkpoint_sha256"] = receipt["checkpoint_sha256"]
    elif mutation == "stdout":
        receipt["stdout_artifact"] = "logs/stdout.txt"
    elif mutation == "physical_receipt":
        receipt["assigned_physical_gpu_id"] = "4"
        result["assigned_gpu"]["physical_device_id"] = "4"
    elif mutation == "sif_path":
        old = receipt["sif_path"]
        receipt["sif_path"] = "/tmp/attacker-controlled.sif"
        receipt["argv"][receipt["argv"].index(old)] = receipt["sif_path"]
    elif mutation == "configured_sif_path":
        receipt["configured_sif_path"] = "/tmp/attacker-controlled/frustrampnn.sif"
    else:
        old = receipt["executable_path"]
        receipt["executable_path"] = "/tmp/frustrampnn"
        receipt["argv"][receipt["argv"].index(old)] = receipt["executable_path"]
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    result_path.write_bytes(canonical_json_bytes(result))
    request_path.write_bytes(canonical_json_bytes(request))
    _rehash_bundle(tmp_path)
    with pytest.raises(
        module.ManifestValidationError,
        match="argv|launcher|checkpoint|runtime|bind|working|stdout|GPU|executable|SIF|schema",
    ):
        _build(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "ca_only", "malformed_width", "wrong_element", "duplicate_atom",
        "backbone_order", "position_origin",
    ],
)
def test_manifest_rejects_fully_rehashed_physical_pdb_lies(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    pdb_path = tmp_path / "normalized_input.pdb"
    lines = pdb_path.read_text("ascii").splitlines()
    atom_lines = [line for line in lines if line.startswith("ATOM")]
    if mutation == "ca_only":
        lines = [line for line in atom_lines if line[12:16].strip() == "CA"] + ["END"]
    elif mutation == "malformed_width":
        lines[0] = lines[0][:-1]
    elif mutation == "wrong_element":
        lines[0] = lines[0][:76] + " C" + lines[0][78:]
    elif mutation == "duplicate_atom":
        lines.insert(1, atom_lines[0])
    elif mutation == "backbone_order":
        by_name = {line[12:16].strip(): line for line in atom_lines}
        lines = [by_name[name] for name in ("CA", "N", "C", "O")] + ["END"]
    else:
        structure_path = tmp_path / "frustrampnn_structure_map_v1.json"
        structure = canonical_json_loads(structure_path.read_bytes())
        structure["rows"][0]["model_position"] = 1
        structure_path.write_bytes(canonical_json_bytes(structure))
        landscape_path = tmp_path / "frustrampnn_landscape_v1.json"
        landscape = canonical_json_loads(landscape_path.read_bytes())
        landscape["residues"][0]["model_position"] = 1
        landscape_path.write_bytes(canonical_json_bytes(landscape))
        raw_path = tmp_path / "raw_frustrampnn.csv"
        raw_path.write_text(raw_path.read_text().replace(",0,G,", ",1,G,"), encoding="utf-8")
    if mutation != "position_origin":
        pdb_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="PDB|backbone|element|atom|width|position|contiguous"):
        _build(tmp_path)


def test_manifest_accepts_sidechains_but_rejects_raw_trailing_fields(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    pdb_path = tmp_path / "normalized_input.pdb"
    lines = pdb_path.read_text("ascii").splitlines()
    cb = lines[1][:6] + f"{5:5d}" + lines[1][11:12] + " CB " + lines[1][16:]
    lines.insert(-1, cb)
    pdb_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    _rehash_bundle(tmp_path)
    _build(tmp_path)

    _bundle(tmp_path)
    raw_path = tmp_path / "raw_frustrampnn.csv"
    rows = raw_path.read_text().splitlines()
    raw_path.write_text("\n".join([rows[0]] + [row + ",undeclared" for row in rows[1:]]) + "\n")
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="raw|field|width|column"):
        _build(tmp_path)


def _externalize_producer_authority(root: Path, *, entity_instance_id: str = "protein-1") -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    request_path = root / "workflow_component_request_v1.json"
    request = canonical_json_loads(request_path.read_bytes())
    request["identity_authority"] = "producer_manifest"

    authority = {
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": request["source_artifact"]["sha256"],
        "entities": [{
            "entity_type": "protein",
            "entity_instance_id": entity_instance_id,
            "source_entity_id": "1",
            "label_asym_id": "AA",
            "auth_asym_id": "A",
            "sequence": "G",
            "residue_mappings": [{
                "auth_seq_id": 1, "insertion_code": "", "label_seq_id": 1,
            }],
        }],
    }
    authority_payload = _write_json(root, "authority_artifact_v1.json", authority)
    request["identity_authority_artifact"] = {
        "relative_path": "authority_artifact_v1.json",
        "media_type": "application/json",
        "sha256": hashlib.sha256(authority_payload).hexdigest(),
        "canonical_json_base64": base64.b64encode(authority_payload).decode("ascii"),
    }
    request_path.write_bytes(canonical_json_bytes(request))

    structure_path = root / "frustrampnn_structure_map_v1.json"
    structure = canonical_json_loads(structure_path.read_bytes())
    structure.update(
        identity_authority="producer_manifest_v1",
        identity_domain="source_authoritative",
        authority_artifact_sha256=hashlib.sha256(authority_payload).hexdigest(),
    )
    row = structure["rows"][0]
    row.update(
        entity_instance_id=entity_instance_id,
        source_entity_id="1",
        label_asym_id="AA",
        label_seq_id=1,
    )
    structure_path.write_bytes(canonical_json_bytes(structure))

    landscape_path = root / "frustrampnn_landscape_v1.json"
    landscape = canonical_json_loads(landscape_path.read_bytes())
    landscape["residues"][0].update(
        entity_instance_id=entity_instance_id,
        source_entity_id="1",
        label_asym_id="AA",
        label_seq_id=1,
    )
    landscape_path.write_bytes(canonical_json_bytes(landscape))

    summary_path = root / "frustrampnn_summary_v1.json"
    summary = canonical_json_loads(summary_path.read_bytes())
    summary["support_by_entity_chain"][0]["entity_instance_id"] = entity_instance_id
    summary_path.write_bytes(canonical_json_bytes(summary))

    _rehash_bundle(root)
    result_path = root / "workflow_component_result_v1.json"
    result = canonical_json_loads(result_path.read_bytes())
    result["artifacts"].insert(0, {
        "relative_path": "authority_artifact_v1.json",
        "role": "identity_authority",
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "sha256": hashlib.sha256(authority_payload).hexdigest(),
        "bytes": len(authority_payload),
        "cardinality": {"kind": "records", "count": 1},
    })
    result_path.write_bytes(canonical_json_bytes(result))


def _externalize_cm_authority(root: Path) -> str:
    from services.frustrampnn.contracts import canonical_json_loads

    _externalize_producer_authority(root)
    snapshot_digest = "c" * 64
    authority_path = root / "authority_artifact_v1.json"
    authority = canonical_json_loads(authority_path.read_bytes())
    authority["cm_complex_snapshot_sha256"] = snapshot_digest
    authority_payload = canonical_json_bytes(authority)
    authority_path.write_bytes(authority_payload)

    request_path = root / "workflow_component_request_v1.json"
    request = canonical_json_loads(request_path.read_bytes())
    request["identity_authority"] = "cm_complex_snapshot"
    request["identity_authority_artifact"].update(
        sha256=hashlib.sha256(authority_payload).hexdigest(),
        canonical_json_base64=base64.b64encode(authority_payload).decode("ascii"),
        cm_complex_snapshot_sha256=snapshot_digest,
    )
    request_path.write_bytes(canonical_json_bytes(request))

    structure_path = root / "frustrampnn_structure_map_v1.json"
    structure = canonical_json_loads(structure_path.read_bytes())
    structure["authority_artifact_sha256"] = hashlib.sha256(authority_payload).hexdigest()
    structure_path.write_bytes(canonical_json_bytes(structure))
    _rehash_bundle(root)
    result_path = root / "workflow_component_result_v1.json"
    result = canonical_json_loads(result_path.read_bytes())
    authority_record = next(
        record for record in result["artifacts"]
        if record["relative_path"] == "authority_artifact_v1.json"
    )
    authority_record["sha256"] = hashlib.sha256(authority_payload).hexdigest()
    authority_record["bytes"] = len(authority_payload)
    result_path.write_bytes(canonical_json_bytes(result))
    return snapshot_digest


def test_manifest_closes_cm_projection_to_typed_snapshot_digest(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    snapshot_digest = _externalize_cm_authority(tmp_path)
    manifest = module.build_result_manifest(tmp_path)
    assert manifest["artifact_count"] == 11

    from services.frustrampnn.contracts import canonical_json_loads
    authority = canonical_json_loads((tmp_path / "authority_artifact_v1.json").read_bytes())
    assert authority["cm_complex_snapshot_sha256"] == snapshot_digest


@pytest.mark.parametrize("mutation", ["projection", "request"])
def test_manifest_rejects_hostile_cm_snapshot_digest_mutation(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    _externalize_cm_authority(tmp_path)
    if mutation == "projection":
        authority_path = tmp_path / "authority_artifact_v1.json"
        authority = canonical_json_loads(authority_path.read_bytes())
        authority["cm_complex_snapshot_sha256"] = "d" * 64
        authority_payload = canonical_json_bytes(authority)
        authority_path.write_bytes(authority_payload)
        request_path = tmp_path / "workflow_component_request_v1.json"
        request = canonical_json_loads(request_path.read_bytes())
        request["identity_authority_artifact"].update(
            sha256=hashlib.sha256(authority_payload).hexdigest(),
            canonical_json_base64=base64.b64encode(authority_payload).decode("ascii"),
        )
        request_path.write_bytes(canonical_json_bytes(request))
        structure_path = tmp_path / "frustrampnn_structure_map_v1.json"
        structure = canonical_json_loads(structure_path.read_bytes())
        structure["authority_artifact_sha256"] = hashlib.sha256(authority_payload).hexdigest()
        structure_path.write_bytes(canonical_json_bytes(structure))
    else:
        request_path = tmp_path / "workflow_component_request_v1.json"
        request = canonical_json_loads(request_path.read_bytes())
        request["identity_authority_artifact"]["cm_complex_snapshot_sha256"] = "d" * 64
        request_path.write_bytes(canonical_json_bytes(request))
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="snapshot|authority|digest|schema"):
        module.build_result_manifest(tmp_path)


def test_manifest_accepts_closed_external_producer_authority_artifact(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    _externalize_producer_authority(tmp_path)
    manifest = module.build_result_manifest(tmp_path)
    authority = next(
        record for record in manifest["artifacts"]
        if record["relative_path"] == "authority_artifact_v1.json"
    )
    assert authority["role"] == "identity_authority"
    assert authority["schema_name"] == "producer_manifest"
    assert manifest["artifact_count"] == 11
    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    module.validate_result_manifest(tmp_path, manifest)


@pytest.mark.parametrize("mutation", ["declared_digest", "source_binding", "entity", "sequence"])
def test_manifest_rejects_fully_rehashed_external_authority_forgery(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    _externalize_producer_authority(tmp_path)
    authority_path = tmp_path / "authority_artifact_v1.json"
    structure_path = tmp_path / "frustrampnn_structure_map_v1.json"
    authority = canonical_json_loads(authority_path.read_bytes())
    structure = canonical_json_loads(structure_path.read_bytes())
    if mutation == "declared_digest":
        structure["authority_artifact_sha256"] = "a" * 64
    else:
        if mutation == "source_binding":
            authority["source_sha256"] = "b" * 64
        elif mutation == "entity":
            authority["entities"][0]["entity_instance_id"] = "forged-protein"
        else:
            authority["entities"][0]["sequence"] = "A"
        authority_payload = canonical_json_bytes(authority)
        authority_path.write_bytes(authority_payload)
        structure["authority_artifact_sha256"] = hashlib.sha256(authority_payload).hexdigest()
    structure_path.write_bytes(canonical_json_bytes(structure))
    _rehash_bundle(tmp_path)

    with pytest.raises(
        module.ManifestValidationError,
        match="authority|source|entity|sequence|identity|digest",
    ):
        module.build_result_manifest(tmp_path)


def test_manifest_requires_external_authority_and_rejects_it_for_self_identity(
    tmp_path: Path,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    request_path = tmp_path / "workflow_component_request_v1.json"
    request = canonical_json_loads(request_path.read_bytes())
    request["identity_authority"] = "producer_manifest"
    request_path.write_bytes(canonical_json_bytes(request))
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="authority|presence|missing"):
        module.build_result_manifest(tmp_path)

    _bundle(tmp_path)
    (tmp_path / "authority_artifact_v1.json").write_bytes(canonical_json_bytes({
        "schema_name": "producer_manifest", "schema_version": 1,
        "source_sha256": "1" * 64, "entities": [],
    }))
    with pytest.raises(module.ManifestValidationError, match="authority|presence|extra"):
        module.build_result_manifest(tmp_path)


def test_manifest_rejects_symlinked_external_authority_artifact(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    target = tmp_path.parent / f"{tmp_path.name}-authority-target.json"
    target.write_bytes(canonical_json_bytes({"schema_name": "producer_manifest"}))
    (tmp_path / "authority_artifact_v1.json").symlink_to(target)
    with pytest.raises(module.ManifestValidationError, match="symlink|regular|follow"):
        module.build_result_manifest(tmp_path)


def test_manifest_reads_each_artifact_once_for_build_and_validation(
    tmp_path: Path, monkeypatch,
) -> None:
    module = _manifests(); _bundle(tmp_path)
    original_read = module._read_regular
    reads: dict[str, int] = {}

    def counted_read(root, relative: str) -> bytes:
        reads[relative] = reads.get(relative, 0) + 1
        return original_read(root, relative)

    monkeypatch.setattr(module, "_read_regular", counted_read)
    manifest = module.build_result_manifest(tmp_path)
    assert reads == {path: 1 for path in module.CANONICAL_ARTIFACT_PATHS}

    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    reads.clear()
    module.validate_result_manifest(tmp_path, manifest)
    assert reads == {
        **{path: 1 for path in module.CANONICAL_ARTIFACT_PATHS},
        module.MANIFEST_PATH: 1,
    }


def test_manifest_rejects_transient_path_set_mutation_after_initial_scandir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _manifests(); _bundle(tmp_path)
    original_read = module._read_regular
    mutated = False

    def mutate_directory_generation(root, relative: str) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            transient = tmp_path / "unmanifested-during-read.txt"
            transient.write_text("hostile", encoding="utf-8")
            transient.unlink()
        return original_read(root, relative)

    monkeypatch.setattr(module, "_read_regular", mutate_directory_generation)
    with pytest.raises(module.ManifestValidationError, match="generation|path set|mutat"):
        module.build_result_manifest(tmp_path)


def test_manifest_rejects_path_added_after_final_scandir_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _manifests(); _bundle(tmp_path)
    original_scan = module._scan_bundle_paths
    calls = 0

    def mutate_after_final_scan(root_fd: int):
        nonlocal calls
        calls += 1
        result = original_scan(root_fd)
        if calls == 2:
            (tmp_path / "late-unmanifested.txt").write_text("hostile", encoding="utf-8")
        return result

    monkeypatch.setattr(module, "_scan_bundle_paths", mutate_after_final_scan)
    with pytest.raises(module.ManifestValidationError, match="generation|path set|mutat"):
        module.build_result_manifest(tmp_path)


def test_manifest_derives_identity_and_closes_exact_physical_schema_cardinality_bundle(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    manifest = _build(tmp_path)
    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    module.validate_result_manifest(tmp_path, manifest)
    assert manifest["invocation_id"] == "invoke-1"
    assert manifest["parent_job_id"] == "job-1"
    assert manifest["candidate_id"] == "candidate-1"
    assert manifest["artifact_count"] == 10
    assert [record["relative_path"] for record in manifest["artifacts"]] == list(module.CANONICAL_ARTIFACT_PATHS)
    assert all(
        record["bytes"] >= 0
        if record["relative_path"] in {"frustrampnn_stdout.log", "frustrampnn_stderr.log"}
        else record["bytes"] > 0
        for record in manifest["artifacts"]
    )
    raw = next(record for record in manifest["artifacts"] if record["relative_path"] == "raw_frustrampnn.csv")
    assert raw["cardinality"] == {"kind": "rows", "count": 20}


def test_manifest_is_the_sole_on_disk_authority_and_schema_checks_cannot_be_bypassed(
    tmp_path: Path,
) -> None:
    module = _manifests(); _bundle(tmp_path)
    stale = {"schema_name": "stale"}
    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(stale))
    with pytest.raises(module.ManifestValidationError, match="pre-existing|manifest"):
        module.build_result_manifest(tmp_path)

    (tmp_path / module.MANIFEST_PATH).unlink()
    manifest = module.build_result_manifest(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="physical|manifest|missing"):
        module.validate_result_manifest(tmp_path, manifest)

    contradictory = copy.deepcopy(manifest)
    contradictory["candidate_id"] = "other-candidate"
    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(contradictory))
    with pytest.raises(module.ManifestValidationError, match="physical|supplied|equal|manifest"):
        module.validate_result_manifest(tmp_path, manifest)

    (tmp_path / module.MANIFEST_PATH).write_bytes(
        b" " + canonical_json_bytes(manifest)
    )
    with pytest.raises(module.ManifestValidationError, match="canonical|manifest"):
        module.validate_result_manifest(tmp_path, manifest)

    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    module.validate_result_manifest(tmp_path, manifest)
    assert "validate_payload_schemas" not in inspect.signature(module.build_result_manifest).parameters
    assert "validate_payload_schemas" not in inspect.signature(module.validate_result_manifest).parameters


def test_manifest_rejects_lexically_unsafe_root_before_path_normalization(tmp_path: Path) -> None:
    module = _manifests()
    base = tmp_path / "base"; base.mkdir()
    lexical_bundle = base / "bundle"; lexical_bundle.mkdir(); _bundle(lexical_bundle)
    actual = tmp_path / "actual"; actual.mkdir()
    target = actual / "nested"; target.mkdir()
    (base / "link").symlink_to(target, target_is_directory=True)
    supplied = base / "link" / ".." / "bundle"
    with pytest.raises(module.ManifestValidationError, match=r"lexical|unsafe|component|\.\."):
        module.build_result_manifest(supplied)


@pytest.mark.parametrize(
    ("relative", "mutation", "expected"),
    [
        ("workflow_component_request_v1.json", lambda value: value.update(candidate_id="other"), "identity|candidate"),
        ("frustrampnn_structure_map_v1.json", lambda value: value.update(source_sha256="a" * 64), "source"),
        ("frustrampnn_structure_map_v1.json", lambda value: value.update(normalized_pdb_sha256="a" * 64), "normalized"),
        ("frustrampnn_landscape_v1.json", lambda value: value.update(raw_csv_sha256="a" * 64), "raw"),
        ("frustrampnn_summary_v1.json", lambda value: value.update(landscape_sha256="a" * 64), "landscape"),
        ("frustrampnn_execution_receipt_v1.json", lambda value: value.update(assigned_physical_gpu_id="GPU-other"), "GPU|gpu"),
        ("workflow_component_result_v1.json", lambda value: value.update(request_sha256="a" * 64), "request"),
        ("workflow_component_result_v1.json", lambda value: value["artifacts"][0].update(sha256="a" * 64), "inventory|artifact"),
    ],
)
def test_manifest_rejects_cross_artifact_identity_hash_runtime_and_inventory_tamper(
    tmp_path: Path, relative: str, mutation, expected: str
) -> None:
    module = _manifests(); _bundle(tmp_path)
    value = module.canonical_json_loads((tmp_path / relative).read_bytes()) if hasattr(module, "canonical_json_loads") else __import__("services.frustrampnn.contracts", fromlist=["canonical_json_loads"]).canonical_json_loads((tmp_path / relative).read_bytes())
    mutation(value)
    (tmp_path / relative).write_bytes(canonical_json_bytes(value))
    with pytest.raises(module.ManifestValidationError, match=expected):
        _build(tmp_path)


def test_manifest_rejects_noncanonical_json_bytes_class_fraction_hash_and_partial_success(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    request_path = tmp_path / "workflow_component_request_v1.json"
    request_path.write_bytes(b"{ \"schema_name\": \"workflow_component_request\" }")
    with pytest.raises(module.ManifestValidationError, match="canonical|schema"):
        _build(tmp_path)

    _bundle(tmp_path)
    landscape_path = tmp_path / "frustrampnn_landscape_v1.json"
    landscape = __import__("services.frustrampnn.contracts", fromlist=["canonical_json_loads"]).canonical_json_loads(landscape_path.read_bytes())
    landscape["residues"][0]["slots"][0]["class"] = "high"
    landscape_path.write_bytes(canonical_json_bytes(landscape))
    with pytest.raises(module.ManifestValidationError, match="class"):
        _build(tmp_path)

    _bundle(tmp_path)
    summary_path = tmp_path / "frustrampnn_summary_v1.json"
    summary = __import__("services.frustrampnn.contracts", fromlist=["canonical_json_loads"]).canonical_json_loads(summary_path.read_bytes())
    summary["complete_landscape_fractions"]["neutral"] = 0.5
    summary_path.write_bytes(canonical_json_bytes(summary))
    with pytest.raises(module.ManifestValidationError, match="fraction|summary"):
        _build(tmp_path)

    _bundle(tmp_path)
    (tmp_path / "raw_frustrampnn.csv").write_bytes(b"frustration_pred,position,wildtype,mutation,chain,pdb\n")
    with pytest.raises(module.ManifestValidationError, match="raw|cardinality|empty|rows"):
        _build(tmp_path)


@pytest.mark.parametrize("unsafe", ["/absolute.json", "../escape.json", "a\\b.json", "a/./b.json"])
def test_manifest_rejects_unsafe_paths(tmp_path: Path, unsafe: str) -> None:
    module = _manifests(); _bundle(tmp_path)
    manifest = _build(tmp_path)
    manifest["artifacts"][0]["relative_path"] = unsafe
    with pytest.raises(module.ManifestValidationError, match="path"):
        module.validate_result_manifest(tmp_path, manifest)


def test_manifest_rejects_missing_extra_tampered_and_no_follow_files(tmp_path: Path) -> None:
    module = _manifests(); _bundle(tmp_path)
    manifest = _build(tmp_path)
    (tmp_path / module.MANIFEST_PATH).write_bytes(canonical_json_bytes(manifest))
    (tmp_path / "raw_frustrampnn.csv").write_bytes(b"tampered\n")
    with pytest.raises(module.ManifestValidationError, match="hash|size|cardinality|raw"):
        module.validate_result_manifest(tmp_path, manifest)

    (tmp_path / module.MANIFEST_PATH).unlink()
    _bundle(tmp_path)
    extra = tmp_path / "unmanifested.txt"; extra.write_text("extra")
    with pytest.raises(module.ManifestValidationError, match="unmanifested|path set"):
        _build(tmp_path)
    extra.unlink()

    target = tmp_path.parent / f"{tmp_path.name}-target.csv"; target.write_text("secret")
    raw = tmp_path / "raw_frustrampnn.csv"; raw.unlink(); raw.symlink_to(target)
    with pytest.raises(module.ManifestValidationError, match="symlink|regular|follow"):
        _build(tmp_path)


@pytest.mark.parametrize("mutation", ["pdb_identity", "raw_tuple", "landscape_score", "summary_content"])
def test_manifest_rejects_fully_rehashed_cross_artifact_physical_semantic_tamper(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    if mutation == "pdb_identity":
        pdb = (tmp_path / "normalized_input.pdb").read_text("ascii")
        (tmp_path / "normalized_input.pdb").write_text(
            "\n".join(
                line[:22] + f"{2:4d}" + line[26:] if line.startswith("ATOM") else line
                for line in pdb.splitlines()
            ) + "\n",
            encoding="ascii",
        )
    elif mutation == "raw_tuple":
        rows = list(csv.DictReader((tmp_path / "raw_frustrampnn.csv").read_text().splitlines()))
        rows[0]["mutation"] = rows[1]["mutation"]
        handle = io.StringIO(newline="")
        writer = csv.DictWriter(handle, fieldnames=["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"], lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
        (tmp_path / "raw_frustrampnn.csv").write_text(handle.getvalue(), encoding="utf-8")
    elif mutation == "landscape_score":
        path = tmp_path / "frustrampnn_landscape_v1.json"
        landscape = canonical_json_loads(path.read_bytes())
        landscape["residues"][0]["slots"][0]["score"] = 0.25
        path.write_bytes(canonical_json_bytes(landscape))
    else:
        path = tmp_path / "frustrampnn_summary_v1.json"
        summary = canonical_json_loads(path.read_bytes())
        summary["native_slot_counts"] = {"high": 1, "neutral": 0, "minimal": 0}
        summary["native_slot_fractions"] = {"high": 1.0, "neutral": 0.0, "minimal": 0.0}
        path.write_bytes(canonical_json_bytes(summary))
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="PDB|raw|landscape|summary|physical|identity|tuple|score"):
        _build(tmp_path)


@pytest.mark.parametrize("mutation", ["target", "workflow", "timestamps", "exit"])
def test_manifest_rejects_status_identity_and_timestamp_closure_tamper(
    tmp_path: Path, mutation: str,
) -> None:
    from services.frustrampnn.contracts import canonical_json_loads

    module = _manifests(); _bundle(tmp_path)
    if mutation == "target":
        path = tmp_path / "frustrampnn_structure_map_v1.json"
        value = canonical_json_loads(path.read_bytes()); value["target_id"] = "other-target"
    elif mutation == "workflow":
        path = tmp_path / "workflow_component_result_v1.json"
        value = canonical_json_loads(path.read_bytes()); value["parent_workflow_id"] = "other-workflow"
    elif mutation == "timestamps":
        path = tmp_path / "workflow_component_result_v1.json"
        value = canonical_json_loads(path.read_bytes()); value["duration_seconds"] = 0.25
    else:
        path = tmp_path / "frustrampnn_execution_receipt_v1.json"
        value = canonical_json_loads(path.read_bytes()); value["exit_code"] = 3
    path.write_bytes(canonical_json_bytes(value))
    _rehash_bundle(tmp_path)
    with pytest.raises(module.ManifestValidationError, match="target|workflow|time|duration|exit|status|identity"):
        _build(tmp_path)


def test_manifest_rejects_symlinked_parent_component(tmp_path: Path) -> None:
    module = _manifests()
    real = tmp_path / "real"; real.mkdir(); _bundle(real)
    linked = tmp_path / "linked"; linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(module.ManifestValidationError, match="symlink|follow|root"):
        _build(linked)
