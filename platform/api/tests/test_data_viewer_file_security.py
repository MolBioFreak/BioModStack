from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

import paths
from routers import files


def test_generic_allowed_roots_exclude_complete_runtime_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-data"
    runtime_root.mkdir()
    monkeypatch.setattr(paths, "get_data_root", lambda: runtime_root)

    roots = paths.get_allowed_roots()

    assert "data" not in roots
    assert runtime_root not in roots.values()


@pytest.mark.asyncio
async def test_generic_upload_refuses_to_replace_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "inputs"
    target_dir.mkdir()
    existing = target_dir / "candidate.cif"
    existing.write_bytes(b"original")
    monkeypatch.setattr(files, "_allowed_lexical_path", lambda _path: target_dir)
    monkeypatch.setattr(files, "resolve_allowed_path", lambda _path: target_dir)
    monkeypatch.setattr(files, "to_allowed_relative", lambda path: f"inputs/{path.name}")

    upload = UploadFile(filename=existing.name, file=io.BytesIO(b"replacement"))
    with pytest.raises(HTTPException) as exc_info:
        await files.upload_file(path="inputs", file=upload, governed_roots=())

    assert exc_info.value.status_code == 409
    assert existing.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_generic_upload_creates_new_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "inputs"
    target_dir.mkdir()
    monkeypatch.setattr(files, "_allowed_lexical_path", lambda _path: target_dir)
    monkeypatch.setattr(files, "resolve_allowed_path", lambda _path: target_dir)
    monkeypatch.setattr(files, "to_allowed_relative", lambda path: f"inputs/{path.name}")

    upload = UploadFile(filename="candidate.cif", file=io.BytesIO(b"new-content"))
    response = await files.upload_file(path="inputs", file=upload, governed_roots=())

    assert response["path"].endswith("candidate.cif")
    assert (target_dir / "candidate.cif").read_bytes() == b"new-content"
