from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from services.conformational_mapping.import_stager import (
    ImportStagingError,
    RegisteredArtifact,
    finalize_staged_import,
    stage_registered_artifacts,
    verify_registered_artifact,
)


PDB = b"ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 20.00           C\n"


def _registered(root: Path, relative: str = "input.pdb", *, principal: str = "alice") -> RegisteredArtifact:
    path = root / relative
    payload = path.read_bytes() if path.is_file() and not path.is_symlink() else PDB
    return RegisteredArtifact("artifact-1", principal, root, relative, hashlib.sha256(payload).hexdigest(), len(payload))


@pytest.mark.parametrize("value", ["../input.pdb", "dir/../../input.pdb"])
def test_cm6_001_rejects_dotdot(tmp_path: Path, value: str) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="unsafe|canonical"):
        verify_registered_artifact(_registered(tmp_path, value), principal_id="alice")


def test_cm6_002_rejects_absolute_path(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="canonical"):
        verify_registered_artifact(_registered(tmp_path, str(path)), principal_id="alice")


@pytest.mark.parametrize("value", ["%2e%2e/input.pdb", "%252e%252e/input.pdb"])
def test_cm6_003_rejects_encoded_traversal(tmp_path: Path, value: str) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="unsafe"):
        verify_registered_artifact(_registered(tmp_path, value), principal_id="alice")


@pytest.mark.parametrize("token", ["*", "?", "[a]", "{a}", ";", "|", "$()", "`", "\n"])
def test_cm6_004_rejects_glob_and_metacharacters(tmp_path: Path, token: str) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    with pytest.raises(ImportStagingError, match="unsafe"):
        verify_registered_artifact(_registered(tmp_path, f"input{token}.pdb"), principal_id="alice")


def test_cm6_005_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.pdb"
    outside.write_bytes(PDB)
    (tmp_path / "input.pdb").symlink_to(outside)
    artifact = RegisteredArtifact("artifact-1", "alice", tmp_path, "input.pdb", hashlib.sha256(PDB).hexdigest(), len(PDB))
    with pytest.raises(ImportStagingError, match="unsafe"):
        verify_registered_artifact(artifact, principal_id="alice")


def test_cm6_006_rejects_symlink_swap_or_retarget(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(PDB)
    artifact = _registered(tmp_path)
    path.unlink()
    path.symlink_to(tmp_path / "missing.pdb")
    with pytest.raises(ImportStagingError, match="unsafe|unavailable"):
        verify_registered_artifact(artifact, principal_id="alice")


def test_cm6_007_rejects_registered_artifact_retarget_before_schedule(tmp_path: Path) -> None:
    path = tmp_path / "input.pdb"
    path.write_bytes(PDB)
    artifact = _registered(tmp_path)
    path.write_bytes(PDB + b"REMARK changed\n")
    with pytest.raises(ImportStagingError, match="identity changed"):
        stage_registered_artifacts([artifact], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    assert not (tmp_path / "staged").exists()


def test_cm6_008_regular_file_and_content_match(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    assert staged.root.joinpath(staged.receipt["entries"][0]["destination_relative_path"]).read_bytes() == PDB


def test_cm6_009_rehash_after_copy(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    entry = staged.receipt["entries"][0]
    assert entry["source_content_sha256"] == entry["staged_content_sha256"] == hashlib.sha256(PDB).hexdigest()


def test_cm6_010_limits_and_collision_safe_names(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id="r", destination_root=tmp_path / "staged")
    assert staged.receipt["entries"][0]["destination_relative_path"].startswith("structures/000000_")
    fifo = tmp_path / "pipe.pdb"
    os.mkfifo(fifo)
    bad = RegisteredArtifact("pipe", "alice", tmp_path, fifo.name, "0" * 64, 0)
    with pytest.raises(ImportStagingError, match="regular"):
        verify_registered_artifact(bad, principal_id="alice")


def test_cm6_011_authorized_registered_id(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    artifact = _registered(tmp_path)
    assert verify_registered_artifact(artifact, principal_id="alice") == (artifact.content_sha256, artifact.size_bytes)
    with pytest.raises(ImportStagingError, match="authorized"):
        verify_registered_artifact(artifact, principal_id="mallory")


def test_cm6_012_immutable_receipt_and_import_identity(tmp_path: Path) -> None:
    (tmp_path / "input.pdb").write_bytes(PDB)
    request_id = "5bd7e715-6f73-4e6a-a270-09f486d1da86"
    staged = stage_registered_artifacts([_registered(tmp_path)], principal_id="alice", request_id=request_id, destination_root=tmp_path / "staged")
    request = {
        "request_id": request_id, "request_sha256": "a" * 64, "targets": [{"target_id": "t"}],
        "runtime_policy": {"use_default_params": True}, "feature_policy": {"mode": "regenerate_mutated_protein_v1"},
    }
    native, ensemble = finalize_staged_import(request, staged.root, tmp_path / "canonical")
    assert ensemble["expected_cardinality"] == 1
    assert native["files"][0]["backend_coordinates"]["staged_receipt_sha256"] == staged.receipt["receipt_sha256"]
