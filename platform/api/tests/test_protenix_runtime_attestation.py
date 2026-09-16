from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

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
    reference = tmp_path / "runtime-image-reference.json"
    receipt = tmp_path / "runtime-image-receipt.json"
    preflight.create_verified_image_reference(
        image=image,
        expected_sha256=_sha(image),
        store_root=tmp_path / "store",
        reference=reference,
        receipt=receipt,
    )
    snapshot = preflight.resolve_verified_image_reference(
        reference=reference, receipt=receipt, expected_sha256=_sha(image), store_root=tmp_path / "store",
    )
    return image, snapshot, receipt


@pytest.mark.parametrize('replacement', [b'X' * len(b'immutable executed container bytes\n'), b'different length'])
def test_reused_object_attests_measured_object_not_changed_original(tmp_path, replacement):
    image, shared, _ = _verified_image(tmp_path)
    expected = _sha(shared)
    image.write_bytes(replacement)
    preflight = _load(PREFLIGHT_PATH, 'prepare_runtime_image_attestation_reuse')
    reference, receipt = tmp_path/'new-reference.json', tmp_path/'new-receipt.json'
    payload = preflight.create_verified_image_reference(
        image=image, expected_sha256=expected, store_root=tmp_path/'store',
        reference=reference, receipt=receipt)
    assert payload['observed_source']['inode'] == shared.stat().st_ino
    assert payload['observed_source']['path'] == str(shared)
    assert payload['observed_source']['bytes'] == shared.stat().st_size
    assert payload['observed_source']['sha256'] == expected != _sha(image)
    assert preflight.resolve_verified_image_reference(
        reference=reference, receipt=receipt, expected_sha256=expected,
        store_root=tmp_path/'store') == shared


def test_host_preflight_snapshots_opened_image_and_emits_observed_identity(tmp_path: Path) -> None:
    image, snapshot, receipt_path = _verified_image(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert snapshot.read_bytes() == image.read_bytes()
    assert receipt["status"] == "verified_immutable_snapshot"
    assert receipt["observed_source"]["sha256"] == _sha(image)
    assert receipt["observed_source"]["device"] == snapshot.stat().st_dev
    assert receipt["observed_source"]["inode"] == snapshot.stat().st_ino
    assert receipt["observed_source"]["path"] == str(snapshot)
    assert receipt["verified_snapshot"]["sha256"] == _sha(snapshot)
    assert stat.S_IMODE(snapshot.stat().st_mode) & 0o222 == 0
    assert snapshot.stat().st_ino != image.stat().st_ino
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o444
    schema = json.loads((REPO_ROOT / "schemas/conformational_mapping/cm_runtime_image_receipt_v1.schema.json").read_text())
    Draft202012Validator(schema).validate(receipt)


def test_host_preflight_rejects_registry_digest_mismatch_without_outputs(tmp_path: Path) -> None:
    preflight = _load(PREFLIGHT_PATH, "prepare_runtime_image_attestation_mismatch")
    image = tmp_path / "protenix.sif"
    image.write_bytes(b"unexpected")
    reference = tmp_path / "reference.json"
    receipt = tmp_path / "runtime-image-receipt.json"
    with pytest.raises(preflight.RuntimeImageAttestationError, match="digest"):
        preflight.create_verified_image_reference(
            image=image, expected_sha256="0" * 64, store_root=tmp_path / "store",
            reference=reference, receipt=receipt,
        )
    assert not reference.exists()
    assert not receipt.exists()


def test_host_preflight_source_swap_after_publication_cannot_change_execution(tmp_path: Path, monkeypatch) -> None:
    preflight = _load(PREFLIGHT_PATH, "prepare_runtime_image_attestation_swap")
    image = tmp_path / "protenix.sif"
    image.write_bytes(b"opened bytes")
    replacement = tmp_path / "replacement.sif"
    replacement.write_bytes(b"replacement bytes")
    reference = tmp_path / "reference.json"
    receipt = tmp_path / "runtime-image-receipt.json"
    original_publish = preflight.publish_image

    def publish_then_swap(*args):
        result = original_publish(*args)
        os.replace(replacement, image)
        return result

    monkeypatch.setattr(preflight, "publish_image", publish_then_swap)
    expected = hashlib.sha256(b"opened bytes").hexdigest()
    payload = preflight.create_verified_image_reference(
        image=image, expected_sha256=expected,
        store_root=tmp_path / "store", reference=reference, receipt=receipt)
    shared = preflight.resolve_verified_image_reference(
        reference=reference, receipt=receipt, expected_sha256=expected, store_root=tmp_path/'store')
    assert shared.read_bytes() == b'opened bytes'
    assert image.read_bytes() == b'replacement bytes'
    assert payload['observed_source']['inode'] == shared.stat().st_ino


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


def _render_module_shell(script: str, **values: object) -> str:
    for key, value in values.items():
        script = script.replace("${" + key + "}", str(value))
    return script.replace("\\$", "$")


def test_canonical_transport_copies_only_receipts_and_executes_shared_reference(tmp_path: Path) -> None:
    """Exercise actual module shell commands and copy staging, not token checks.

    This is a transport test with tiny image bytes, not a scientific/container run.
    The Apptainer-owned environment value is supplied at the execution boundary.
    """
    module = (REPO_ROOT / "modules" / "conformational_mapping_protenix.nf").read_text()
    publish_command = module.split("    ${params.api_python} ${params.code_root}/scripts/prepare_runtime_image_attestation.py", 1)[1]
    publish_command = "${params.api_python} ${params.code_root}/scripts/prepare_runtime_image_attestation.py" + publish_command.split("    ${params.api_python} ${params.code_root}/scripts/prepare_protenix_execution_snapshot.py", 1)[0]
    before_script = module.split("    beforeScript {", 1)[1].split('"""', 2)[1]
    executing_script = '    RUNTIME_IMAGE="${runtime_image}"' + module.split('    RUNTIME_IMAGE="${runtime_image}"', 1)[1].split("    mkdir -p native_protenix", 1)[0]
    image = tmp_path / "source.sif"
    image.write_bytes(b"small transport fixture, not a real SIF")
    digest = _sha(image)
    store = tmp_path / "store"
    shared = store / "objects" / "sha256" / digest / "runtime.sif"
    identities = []
    for attempt in range(2):
        work = tmp_path / f"preflight-{attempt}"
        root = work / "protenix_preflight"
        (root / "request").mkdir(parents=True)
        registry = root / "request" / "cm_runtime_registry_v1.json"
        registry.write_text(json.dumps({"container_digest": "sha256:" + digest}))
        rendered = _render_module_shell(publish_command, **{
            "params.api_python": sys.executable, "params.code_root": REPO_ROOT,
            "image_path": image, "image_store": store,
        })
        subprocess.run(["bash", "-euc", rendered], cwd=work,
                       env=os.environ | {"REGISTRY": str(registry)}, check=True, capture_output=True)
        identities.append((shared.stat().st_dev, shared.stat().st_ino, shared.stat().st_ctime_ns))
        # Same physical staging primitive as Nextflow stageInMode copy; only the
        # small preflight directory is a path input, shared image is a val input.
        staged = tmp_path / f"execution-{attempt}" / "protenix_preflight"
        shutil.copytree(root, staged)
        assert not list(work.rglob("*.sif"))
        assert not list(staged.parent.rglob("*.sif"))
        values = {"params.api_python": sys.executable, "params.code_root": REPO_ROOT,
                  "store": store, "image_store": store, "object_dir": shared.parent,
                  "runtime_image": shared, "preflight": staged, "preflight_source": root}
        result = subprocess.run(["bash", "-euc", _render_module_shell(before_script, **values)],
                                cwd=staged.parent, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        env = os.environ | {"REGISTRY": str(staged / "request/cm_runtime_registry_v1.json"),
                            "PREFLIGHT": str(staged), "IMAGE_RECEIPT": str(staged / "runtime-image-receipt.json"),
                            "APPTAINER_CONTAINER": str(shared)}
        command = _render_module_shell(executing_script, **values)
        result = subprocess.run(["bash", "-euc", command], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        wrong = subprocess.run(["bash", "-euc", command],
                               env=env | {"APPTAINER_CONTAINER": str(image)}, capture_output=True, text=True)
        assert wrong.returncode != 0
        assert "actual executing runtime image differs" in wrong.stderr
    assert identities[0] == identities[1]
    assert list(store.rglob("*.sif")) == [shared]
    assert 'container { runtime_image }' in module
    assert 'tuple val(request_id), path(preflight), val(runtime_image)' in module


@pytest.mark.parametrize("tamper", ["path", "digest", "identity", "receipt", "replacement", "corrupt"])
def test_shared_reference_rejects_forgery_or_changed_object(tmp_path: Path, tamper: str) -> None:
    preflight = _load(PREFLIGHT_PATH, "prepare_runtime_image_reference_tamper")
    image, shared, receipt = _verified_image(tmp_path)
    reference = tmp_path / "runtime-image-reference.json"
    ref = json.loads(reference.read_text())
    if tamper == "path":
        ref["path"] = str(image)  # Same bytes outside the independently configured CAS.
    elif tamper == "digest":
        ref["identity"]["sha256"] = "0" * 64
    elif tamper == "identity":
        ref["identity"]["inode"] += 1
    elif tamper == "receipt":
        receipt.chmod(0o644)
        payload = json.loads(receipt.read_text())
        payload["expected_sha256"] = "0" * 64
        receipt.write_text(json.dumps(payload))
        ref["receipt_sha256"] = _sha(receipt)  # Rehashing a forged receipt is insufficient.
    elif tamper == "replacement":
        shared.parent.chmod(0o700)
        replacement = shared.parent / "replacement.sif"
        replacement.write_bytes(shared.read_bytes())
        replacement.chmod(0o400)
        os.replace(replacement, shared)
        shared.parent.chmod(0o500)
    else:
        shared.chmod(0o600)
        shared.write_bytes(b"corrupt shared object")
        shared.chmod(0o400)
    reference.chmod(0o644)
    reference.write_text(json.dumps(ref))
    with pytest.raises(preflight.RuntimeImageAttestationError):
        preflight.resolve_verified_image_reference(
            reference=reference, receipt=receipt, expected_sha256=_sha(image), store_root=tmp_path / "store",
        )
    if tamper == "corrupt":
        with pytest.raises(preflight.RuntimeImageAttestationError):
            preflight.create_verified_image_reference(
                image=image, expected_sha256=_sha(image), store_root=tmp_path / "store",
                reference=tmp_path / "retry/reference.json", receipt=tmp_path / "retry/receipt.json",
            )
        assert not (tmp_path / "retry/reference.json").exists()
        assert shared.read_bytes() == b"corrupt shared object"  # Never silently repair.


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
