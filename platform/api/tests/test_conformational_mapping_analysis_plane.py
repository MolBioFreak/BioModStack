from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import canonical_sha256
from services.frustrampnn.runtime import (
    FRUSTRAMPNN_RUNTIME_IDENTITY,
    RuntimeValidationError,
)


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent


def _load_analysis_plane():
    path = REPO_ROOT / "scripts" / "run_conformational_mapping_analysis_plane.py"
    spec = importlib.util.spec_from_file_location("cm_analysis_plane_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _allow_test_container(module, container: Path, monkeypatch) -> None:
    expected = container.absolute()

    def validate(path: Path | str) -> str:
        assert Path(path).absolute() == expected
        return str(expected)

    monkeypatch.setattr(
        module._frustrampnn_runtime,
        "validate_configured_container_path",
        validate,
    )


def test_production_cm_runner_uses_neutral_frustrampnn_adapter_and_analysis_core() -> None:
    source = (
        REPO_ROOT / "scripts" / "run_conformational_mapping_analysis_plane.py"
    ).read_text(encoding="utf-8")

    assert "services.conformational_mapping.frustrampnn_adapter import" in source
    assert "normalize_cm_structure" in source
    assert "services.frustrampnn.analysis import" in source
    assert "finalize_landscape as finalize_neutral_landscape" in source
    assert "services.conformational_mapping.frustration import finalize_landscape" not in source
    assert "normalize_conformational_mapping_structure" not in source
    assert "bind_candidate_complex_snapshot" not in source
    assert source.count("source_bytes = read_structure_bytes(structure)") == 1
    assert "source_bytes=source_bytes" in source


def test_candidate_structure_path_preserves_symlink_for_no_follow_adapter(
    tmp_path: Path,
) -> None:
    module = _load_analysis_plane()
    root = tmp_path / "bundle"
    real = tmp_path / "real"
    root.mkdir(); real.mkdir()
    (real / "candidate.pdb").write_text("END\n", encoding="ascii")
    (root / "linked").symlink_to(real, target_is_directory=True)

    candidate = module._candidate_structure_path(root, "linked/candidate.pdb")
    assert candidate == root / "linked" / "candidate.pdb"
    assert candidate.resolve() == real / "candidate.pdb"
    with pytest.raises(RuntimeError, match="relative|unsafe"):
        module._candidate_structure_path(root, "../escape.pdb")


def test_cm_main_rejects_unregistered_host_container_before_open_or_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_analysis_plane()
    registered = tmp_path / "registered" / "frustrampnn.sif"
    alternate = tmp_path / "alternate" / "frustrampnn.sif"
    registered.parent.mkdir(); alternate.parent.mkdir()
    registered.write_bytes(b"byte-identical-qualified-image")
    alternate.write_bytes(registered.read_bytes())
    identity = FRUSTRAMPNN_RUNTIME_IDENTITY

    def validate_host_path(path: Path | str) -> str:
        if Path(path).absolute() != registered.absolute():
            raise RuntimeValidationError(
                "configured FrustraMPNN container path does not match the central runtime registry"
            )
        return str(registered.absolute())

    monkeypatch.setattr(
        module._frustrampnn_runtime,
        "validate_configured_container_path",
        validate_host_path,
    )

    canonical = tmp_path / "canonical"; canonical.mkdir()
    (canonical / "cm_ensemble_v1.json").write_text(
        json.dumps({"candidates": []}), encoding="utf-8",
    )
    request = tmp_path / "request.json"; request.write_text("{}", encoding="utf-8")
    snapshots = tmp_path / "snapshots.json"; snapshots.write_text("[]", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"analysis_runtime": {
        "container_name": registered.name,
        "container_sha256": identity.sif_sha256,
    }}), encoding="utf-8")
    opened: list[Path] = []

    def forbidden_open(path: Path, _digest: object):
        opened.append(path)
        raise AssertionError("unregistered host image was opened")

    monkeypatch.setattr(module, "_open_verified_container", forbidden_open)
    monkeypatch.setattr(sys, "argv", [
        "run_conformational_mapping_analysis_plane.py",
        "--request", str(request), "--runtime-registry", str(registry),
        "--snapshots", str(snapshots), "--canonical", str(canonical),
        "--checkpoint", identity.checkpoint_path,
        "--checkpoint-id", identity.checkpoint_id,
        "--frustrampnn-container", str(alternate), "--gpu-id", "0",
        "--out", str(tmp_path / "output"),
    ])

    with pytest.raises(RuntimeValidationError, match="central runtime registry"):
        module.main()
    assert opened == []


def test_cm_command_accepts_descriptor_backed_execution_path(tmp_path: Path) -> None:
    module = _load_analysis_plane()
    normalized = tmp_path / "normalized.pdb"
    normalized.write_bytes(_candidate_pdb())
    output_root = tmp_path / "output"
    output_root.mkdir()
    invocation = module._frustrampnn_command(
        apptainer="apptainer",
        container=Path("/proc/self/fd/17"),
        tool="/opt/venv/bin/frustrampnn",
        normalized=normalized,
        checkpoint=Path("/opt/frustrampnn_weights/megascale.ckpt"),
        raw=output_root / "raw.csv",
        output_root=output_root,
        gpu_id=0,
    )
    assert "/proc/self/fd/17" in invocation.argv
    assert invocation.physical_gpu_id == 0
    assert invocation.task_visible_gpu_id == 0


def _candidate_pdb() -> bytes:
    lines = []
    for serial, (atom, element) in enumerate(
        (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1,
    ):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} GLY X  10    "
            f"{serial:8.3f}{serial + 1:8.3f}{serial + 2:8.3f}"
            f"{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _candidate_snapshot(source_sha256: str) -> dict:
    snapshot = {
        "schema_name": "cm_complex_snapshot", "schema_version": 1,
        "target_id": "target", "target_order": 0,
        "original_source_path": "inputs/source.pdb",
        "original_source_sha256": source_sha256,
        "normalized_source_sha256": "0" * 64,
        "entities": [{
            "entity_type": "protein", "source_entity_id": "protein", "count": 1,
            "ordered_instance_ids": ["protein-1"], "sequence": "G",
        }],
        "bonds": [],
        "instance_mappings": [{
            "source_entity_id": "protein", "source_instance_id": "protein-1",
            "runtime_target_id": "target", "runtime_entity_id": "runtime-protein",
            "runtime_instance_id": "runtime-protein-1", "runtime_order": 0,
            "candidate_id": "candidate", "output_entity_id": "protein",
            "output_label_asym_id": "X", "output_auth_asym_id": "X",
            "output_entity_order": 0,
        }],
        "admission": {"token_count": 1, "atom_count": 4, "token_limit": 100,
                      "conversion_omissions": []},
        "unsupported_fields": [],
    }
    snapshot["normalized_source_sha256"] = canonical_sha256({
        key: value for key, value in snapshot.items() if key != "normalized_source_sha256"
    })
    return snapshot


@pytest.mark.parametrize("symlink_case", ["source_root", "ancestor"])
def test_production_cm_runner_preserves_input_symlinks_until_no_follow_rejection(
    symlink_case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_analysis_plane()
    payload = _candidate_pdb()
    canonical = tmp_path / "canonical"; canonical.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    external = outside / "candidate.pdb"; external.write_bytes(payload)
    if symlink_case == "source_root":
        (canonical / "candidate.pdb").symlink_to(external)
        relative = "candidate.pdb"
    else:
        (canonical / "linked").symlink_to(outside, target_is_directory=True)
        relative = "linked/candidate.pdb"
    (canonical / "cm_ensemble_v1.json").write_text(json.dumps({
        "candidates": [{
            "candidate_id": "candidate",
            "authoritative_structure_path": relative,
            "backend_coordinates": {"target_id": "target"},
        }],
    }), encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "request_id": "request", "request_sha256": "a" * 64,
        "backend": "external_import", "analysis_policy": {},
    }), encoding="utf-8")
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text(json.dumps([
        _candidate_snapshot(hashlib.sha256(payload).hexdigest())
    ]), encoding="utf-8")
    container = tmp_path / "frustrampnn.sif"; container.write_bytes(b"container")
    _allow_test_container(module, container, monkeypatch)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"analysis_runtime": {
        "container_name": container.name, "container_sha256": "a" * 64,
    }}), encoding="utf-8")
    monkeypatch.setattr(module, "_open_verified_container", lambda *_args: (
        os.open(container, os.O_RDONLY), "a" * 64,
    ))
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/apptainer")
    monkeypatch.setattr(module, "_container_sha256", lambda *_args, **_kwargs: (
        FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256
        if _args[2] == FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path
        else FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256
    ))
    inference_calls: list[object] = []

    def inference_must_not_run(*args, **kwargs):
        inference_calls.append((args, kwargs))
        raise AssertionError("model inference reached after symlink dereference")

    monkeypatch.setattr(
        module._frustrampnn_runtime,
        "execute_frustrampnn",
        inference_must_not_run,
    )
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", [
        "run_conformational_mapping_analysis_plane.py",
        "--request", str(request), "--runtime-registry", str(registry),
        "--snapshots", str(snapshots), "--canonical", str(canonical),
        "--checkpoint", FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
        "--checkpoint-id", FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_id,
        "--frustrampnn-container", str(container), "--gpu-id", "0", "--out", str(output),
    ])
    with pytest.raises(Exception, match="symlink|no-follow|without following"):
        module.main()
    assert inference_calls == []
    copied = output / relative
    assert copied.is_symlink() or copied.parent.is_symlink()


def test_cm_analysis_plane_omits_state_artifact_index_member_without_comparison_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_analysis_plane()
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "derived").mkdir()
    (canonical / "cm_ensemble_v1.json").write_text(json.dumps({"candidates": []}))
    request = tmp_path / "cm_request_v1.json"
    request.write_text(json.dumps({
        "request_id": "request-no-authority", "request_sha256": "a" * 64,
        "backend": "protenix_v2_ensemble",
        "analysis_policy": {"clash_detector_id": "test", "clash_detector_version": "1"},
    }))
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text("[]")
    container = tmp_path / "frustrampnn.sif"
    container.write_bytes(b"container")
    _allow_test_container(module, container, monkeypatch)
    runtime_registry = tmp_path / "runtime-registry.json"
    runtime_registry.write_text(json.dumps({
        "analysis_runtime": {
            "container_name": container.name,
            "container_sha256": "a" * 64,
        }
    }))
    tool = tmp_path / "apptainer"
    tool.write_bytes(b"tool")
    monkeypatch.setattr(module.shutil, "which", lambda _name: str(tool))
    monkeypatch.setattr(
        module,
        "_open_verified_container",
        lambda *_args: (os.open(container, os.O_RDONLY), "a" * 64),
    )
    monkeypatch.setattr(
        module,
        "_container_sha256",
        lambda _apptainer, _container, internal_path, **_kwargs: (
            FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_sha256
            if internal_path == FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path
            else FRUSTRAMPNN_RUNTIME_IDENTITY.executable_sha256
        ),
    )
    monkeypatch.setattr(
        module,
        "analyze_landscapes",
        lambda *_args, **_kwargs: {
            "analysis_id": "analysis", "support_records": [], "pair_ledger": [],
            "ranking_policy": {}, "clash_records": [], "exclusions": [], "results": [],
        },
    )
    monkeypatch.setattr(module, "derive_state_landscape_analysis_for_request", lambda *_args: None)
    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_conformational_mapping_analysis_plane.py",
            "--request", str(request), "--runtime-registry", str(runtime_registry),
            "--snapshots", str(snapshots), "--canonical", str(canonical),
            "--checkpoint", FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
            "--checkpoint-id", "test",
            "--frustrampnn-container", str(container), "--gpu-id", "0", "--out", str(output),
        ],
    )

    module.main()

    index = json.loads((output / "cm_derived_index_v1.json").read_text())
    assert "state_landscape_analyses" not in index


def test_cm_analysis_plane_closes_pinned_container_when_asset_verification_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    module = _load_analysis_plane()
    canonical = tmp_path / "canonical"; canonical.mkdir()
    (canonical / "cm_ensemble_v1.json").write_text('{"candidates":[]}', encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text('{"request_id":"r","request_sha256":"' + "a" * 64 + '","backend":"external_import","analysis_policy":{}}', encoding="utf-8")
    snapshots = tmp_path / "snapshots.json"; snapshots.write_text("[]", encoding="utf-8")
    container = tmp_path / "frustrampnn.sif"; container.write_bytes(b"container")
    _allow_test_container(module, container, monkeypatch)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"analysis_runtime": {
        "container_name": container.name, "container_sha256": "a" * 64,
    }}), encoding="utf-8")
    descriptor = os.open(container, os.O_RDONLY)
    closed: list[int] = []

    class SpyPin:
        def __init__(self, fd: int, sha256: str):
            self.fd = fd; self.sha256 = sha256
        def close(self) -> None:
            closed.append(self.fd)
            os.close(self.fd)

    monkeypatch.setattr(module, "_open_verified_container", lambda *_args: (descriptor, "a" * 64))
    monkeypatch.setattr(module._frustrampnn_runtime, "PinnedContainer", SpyPin)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/apptainer")
    monkeypatch.setattr(module, "_container_sha256", lambda *_args, **_kwargs: "0" * 64)
    monkeypatch.setattr(sys, "argv", [
        "run_conformational_mapping_analysis_plane.py",
        "--request", str(request), "--runtime-registry", str(registry),
        "--snapshots", str(snapshots), "--canonical", str(canonical),
        "--checkpoint", FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
        "--checkpoint-id", "megascale.ckpt", "--frustrampnn-container", str(container),
        "--gpu-id", "0", "--out", str(tmp_path / "out"),
    ])
    with pytest.raises(RuntimeError, match="checkpoint SHA-256"):
        module.main()
    assert closed == [descriptor]
    with pytest.raises(OSError):
        os.fstat(descriptor)