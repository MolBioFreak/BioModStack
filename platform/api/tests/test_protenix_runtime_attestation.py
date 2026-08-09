from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

from services.conformational_mapping.protenix import (
    ProtenixMappingError,
    _validate_runtime_attestation,
    finalize_protenix,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PREFLIGHT_PATH = REPO_ROOT / "scripts" / "prepare_runtime_image_attestation.py"
EXECUTION_SNAPSHOT_PATH = REPO_ROOT / "scripts" / "prepare_protenix_execution_snapshot.py"
ATTEST_PATH = REPO_ROOT / "scripts" / "attest_protenix_runtime.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry(image: Path, checkpoint: Path, commit: str) -> dict[str, object]:
    return {
        "schema_name": "cm_runtime_registry",
        "schema_version": 1,
        "backend_version": "expected-only",
        "backend_commit": commit,
        "runtime_identity": "expected-only",
        "container_digest": f"sha256:{_sha(image)}",
        "checkpoint_sha256": _sha(checkpoint),
        "checkpoint_relative_path": "checkpoint/protenix-v2.pt",
        "model_id": "protenix-v2",
    }


def _verified_image(tmp_path: Path):
    preflight = _load(PREFLIGHT_PATH, "prepare_runtime_image_attestation")
    tmp_path.mkdir(parents=True, exist_ok=True)
    image = tmp_path / "protenix.sif"
    image.write_bytes(b"immutable executed container bytes\n")
    snapshot = tmp_path / "verified-protenix.sif"
    receipt = tmp_path / "runtime-image-receipt.json"
    preflight.create_verified_image_snapshot(
        image=image,
        expected_sha256=_sha(image),
        snapshot=snapshot,
        receipt=receipt,
    )
    return image, snapshot, receipt


def test_host_preflight_snapshots_opened_image_and_emits_observed_identity(tmp_path: Path) -> None:
    image, snapshot, receipt_path = _verified_image(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert snapshot.read_bytes() == image.read_bytes()
    assert receipt["status"] == "verified_immutable_snapshot"
    assert receipt["observed_source"]["sha256"] == _sha(image)
    assert receipt["observed_source"]["device"] == image.stat().st_dev
    assert receipt["observed_source"]["inode"] == image.stat().st_ino
    assert receipt["verified_snapshot"]["sha256"] == _sha(snapshot)
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o444
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o444


def test_host_preflight_rejects_registry_digest_mismatch_without_outputs(tmp_path: Path) -> None:
    preflight = _load(PREFLIGHT_PATH, "prepare_runtime_image_attestation_mismatch")
    image = tmp_path / "protenix.sif"
    image.write_bytes(b"unexpected")
    snapshot = tmp_path / "verified-protenix.sif"
    receipt = tmp_path / "runtime-image-receipt.json"

    with pytest.raises(preflight.RuntimeImageAttestationError, match="digest"):
        preflight.create_verified_image_snapshot(
            image=image,
            expected_sha256="0" * 64,
            snapshot=snapshot,
            receipt=receipt,
        )
    assert not snapshot.exists()
    assert not receipt.exists()


def test_host_preflight_detects_path_swap_after_open(tmp_path: Path, monkeypatch) -> None:
    preflight = _load(PREFLIGHT_PATH, "prepare_runtime_image_attestation_swap")
    image = tmp_path / "protenix.sif"
    image.write_bytes(b"opened bytes")
    replacement = tmp_path / "replacement.sif"
    replacement.write_bytes(b"replacement bytes")
    snapshot = tmp_path / "verified-protenix.sif"
    receipt = tmp_path / "runtime-image-receipt.json"
    original_copy = preflight._copy_descriptor

    def copy_then_swap(source_fd: int, destination: Path):
        result = original_copy(source_fd, destination)
        os.replace(replacement, image)
        return result

    monkeypatch.setattr(preflight, "_copy_descriptor", copy_then_swap)
    with pytest.raises(preflight.RuntimeImageAttestationError, match="changed"):
        preflight.create_verified_image_snapshot(
            image=image,
            expected_sha256=hashlib.sha256(b"opened bytes").hexdigest(),
            snapshot=snapshot,
            receipt=receipt,
        )
    assert not snapshot.exists()
    assert not receipt.exists()


def _attestation_fixture(tmp_path: Path):
    attest = _load(ATTEST_PATH, "attest_protenix_runtime")
    prepare = _load(EXECUTION_SNAPSHOT_PATH, "prepare_protenix_execution_snapshot")
    image, snapshot, image_receipt = _verified_image(tmp_path)
    weights_root = tmp_path / "weights"
    checkpoint_source = weights_root / "checkpoint" / "protenix-v2.pt"
    checkpoint_source.parent.mkdir(parents=True)
    checkpoint_source.write_bytes(b"checkpoint bytes")
    commit = "b" * 40
    source_root = tmp_path / "site-packages" / "protenix"
    source_root.mkdir(parents=True)
    (source_root / "__init__.py").write_text("__version__ = '2.0-observed'\n", encoding="utf-8")
    (source_root / "model.py").write_text("def execute(): return 'observed'\n", encoding="utf-8")
    wrapper = tmp_path / "run_protenix_inference.py"
    wrapper.write_text("print('wrapper')\n", encoding="utf-8")
    registry = _registry(image, checkpoint_source, commit)
    registry_path = tmp_path / "cm_runtime_registry_v1.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    execution_root = tmp_path / "execution-snapshot"
    execution_receipt = tmp_path / "execution-snapshot-receipt.json"
    prepare.prepare_execution_snapshot(
        registry_path=registry_path,
        weights_root=weights_root,
        wrapper=wrapper,
        runtime_root=execution_root,
        receipt_path=execution_receipt,
    )
    checkpoint = execution_root / "checkpoint" / "protenix-v2.pt"
    wrapper_snapshot = execution_root / "bms-wrapper" / "run_protenix_inference.py"
    direct_url = {"vcs_info": {"vcs": "git", "commit_id": commit}}
    global_artifacts = [
        {"semantic_role": role, "relative_path": f"runtime/{role}.json"}
        for role in (
            "runtime_input", "feature_policy", "log", "runtime_config", "composition_audit",
            "coordinate_ledger", "coordinate_context", "preprocessing_record", "msa_record",
            "template_record", "runtime_attestation", "runtime_image_receipt",
            "execution_snapshot_receipt",
        )
    ]
    runtime = attest.build_runtime_attestation(
        registry=registry,
        image_receipt_path=image_receipt,
        runtime_image=snapshot,
        checkpoint=checkpoint,
        source_roots=[source_root],
        direct_url=direct_url,
        distribution_version="2.0-observed",
        wrapper=wrapper_snapshot,
        execution_receipt_path=execution_receipt,
        global_artifacts=global_artifacts,
    )
    return (
        attest,
        registry,
        runtime,
        snapshot,
        image_receipt,
        checkpoint,
        source_root,
        wrapper_snapshot,
        execution_receipt,
    )


def test_execution_attestation_measures_distinct_image_checkpoint_and_source_bytes(
    tmp_path: Path,
) -> None:
    (
        _, registry, runtime, snapshot, image_receipt, checkpoint, source_root, wrapper, execution_receipt
    ) = _attestation_fixture(tmp_path)

    assert runtime["runtime_image"]["sha256"] == _sha(snapshot)
    assert runtime["checkpoint"]["sha256"] == _sha(checkpoint)
    assert runtime["backend_source"]["commit"] == registry["backend_commit"]
    assert runtime["backend_source"]["manifest_sha256"] not in {
        runtime["runtime_image"]["sha256"],
        runtime["checkpoint"]["sha256"],
    }
    assert runtime["executed_wrapper"]["sha256"] == _sha(wrapper)
    assert runtime["backend_commit"] == runtime["backend_source"]["commit"]
    assert runtime["container_digest"] == f"sha256:{runtime['runtime_image']['sha256']}"
    assert runtime["checkpoint_sha256"] == runtime["checkpoint"]["sha256"]
    assert runtime["runtime_identity"].startswith("apptainer-sif-sha256:")
    assert any(record["relative_path"].endswith("model.py") for record in runtime["backend_source"]["files"])

    before = runtime["backend_source"]["manifest_sha256"]
    (source_root / "model.py").write_text("def execute(): return 'changed bytes'\n", encoding="utf-8")
    attest = _load(ATTEST_PATH, "attest_protenix_runtime_changed")
    changed = attest.build_runtime_attestation(
        registry=registry,
        image_receipt_path=image_receipt,
        runtime_image=snapshot,
        checkpoint=checkpoint,
        source_roots=[source_root],
        direct_url={"vcs_info": {"vcs": "git", "commit_id": registry["backend_commit"]}},
        distribution_version="2.0-observed",
        wrapper=wrapper,
        execution_receipt_path=execution_receipt,
    )
    assert changed["backend_source"]["manifest_sha256"] != before


def test_finalizer_accepts_only_the_complete_observed_attestation(tmp_path: Path) -> None:
    attest, _registry, runtime, _snapshot, _image_receipt, _checkpoint, _source_root, _wrapper, _execution_receipt = _attestation_fixture(tmp_path)
    _validate_runtime_attestation(runtime)
    assert runtime["execution_snapshot"]["receipt"]["status"] == "verified_before_execution"
    assert runtime["runtime_image"]["host_verified_snapshot"]["sha256"] == runtime["runtime_image"]["sha256"]


def test_execution_snapshot_receipt_binds_the_wrapper_bytes(tmp_path: Path) -> None:
    attest, registry, _runtime, snapshot, image_receipt, checkpoint, source_root, wrapper, execution_receipt = _attestation_fixture(tmp_path)
    wrapper.chmod(0o644)
    wrapper.write_bytes(b"wrapper changed after preflight")
    with pytest.raises(attest.ProtenixRuntimeAttestationError, match="wrapper"):
        attest.build_runtime_attestation(
            registry=registry,
            image_receipt_path=image_receipt,
            runtime_image=snapshot,
            checkpoint=checkpoint,
            source_roots=[source_root],
            direct_url={"vcs_info": {"vcs": "git", "commit_id": registry["backend_commit"]}},
            distribution_version="2.0-observed",
            wrapper=wrapper,
            execution_receipt_path=execution_receipt,
        )


def test_execution_attestation_rejects_staged_image_swap_and_source_commit_mismatch(
    tmp_path: Path,
) -> None:
    (
        attest,
        registry,
        runtime,
        snapshot,
        image_receipt,
        checkpoint,
        source_root,
        wrapper,
        execution_receipt,
    ) = _attestation_fixture(tmp_path)
    snapshot.chmod(0o644)
    snapshot.write_bytes(b"swapped after preflight")
    with pytest.raises(attest.ProtenixRuntimeAttestationError, match="runtime image"):
        attest.build_runtime_attestation(
            registry=registry,
            image_receipt_path=image_receipt,
            runtime_image=snapshot,
            checkpoint=checkpoint,
            source_roots=[source_root],
            direct_url={"vcs_info": {"vcs": "git", "commit_id": registry["backend_commit"]}},
            distribution_version="2.0-observed",
            wrapper=wrapper,
            execution_receipt_path=execution_receipt,
        )

    _, snapshot, receipt = _verified_image(tmp_path / "second")
    with pytest.raises(attest.ProtenixRuntimeAttestationError, match="commit"):
        attest.build_runtime_attestation(
            registry=registry | {"container_digest": f"sha256:{_sha(snapshot)}"},
            image_receipt_path=receipt,
            runtime_image=snapshot,
            checkpoint=checkpoint,
            source_roots=[source_root],
            direct_url={"vcs_info": {"vcs": "git", "commit_id": "c" * 40}},
            distribution_version="2.0-observed",
            wrapper=wrapper,
            execution_receipt_path=execution_receipt,
        )


def test_finalizer_rejects_registry_shaped_identity_without_observed_attestation() -> None:
    copied_expected_values = {
        "backend_version": "protenix-v2",
        "backend_commit": "b" * 40,
        "runtime_identity": "installed-protenix-v2",
        "container_digest": "sha256:" + "c" * 64,
        "checkpoint_sha256": "d" * 64,
        "model_id": "protenix-v2",
        "command": ["run_protenix_inference.py"],
        "global_artifacts": [],
    }
    with pytest.raises(ProtenixMappingError, match="observed runtime attestation"):
        _validate_runtime_attestation(copied_expected_values)


def test_finalizer_rejects_copied_registry_identity_before_reading_native_outputs(
    tmp_path: Path,
) -> None:
    copied_expected_values = {
        "backend_version": "protenix-v2",
        "backend_commit": "b" * 40,
        "runtime_identity": "installed-protenix-v2",
        "container_digest": "sha256:" + "c" * 64,
        "checkpoint_sha256": "d" * 64,
        "model_id": "protenix-v2",
        "command": ["run_protenix_inference.py"],
        "global_artifacts": [],
    }
    with pytest.raises(ProtenixMappingError, match="observed runtime attestation"):
        finalize_protenix(
            {},
            [],
            tmp_path / "native-does-not-exist",
            tmp_path / "canonical",
            copied_expected_values,
        )
