"""Pure API reader coverage: no Job insertion, inference, or live data."""
from __future__ import annotations

import hashlib
import os
import struct
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import shape_blueprint as owner


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["regular", "hardlink", "leaf_alias", "root_alias"])
@pytest.mark.parametrize("role", ["preview", "points"])
async def test_selected_geometry_readers_accept_shared_contained_bytes(tmp_path, monkeypatch, kind, role):
    data = tmp_path / "data"
    data.mkdir()
    root = data / "shape_blueprint"
    root.mkdir()
    payload = b"# tiny canonical preview\n" if role == "preview" else struct.pack("<fff", 1.0, 2.0, 3.0)
    member = root / "payload"
    member.write_bytes(payload)
    if kind == "hardlink":
        os.link(member, root / "retained")
    elif kind == "leaf_alias":
        member.rename(root / "concrete")
        member.symlink_to("concrete")
    elif kind == "root_alias":
        root.rename(data / "concrete")
        root.symlink_to("concrete", target_is_directory=True)
    row = SimpleNamespace(artifacts={"preview_obj": "payload", "points_f32": "payload"},
        manifest={"preview_obj_sha256": hashlib.sha256(payload).hexdigest(),
                  "point_pool_sha256": hashlib.sha256(payload).hexdigest(), "point_count": 1})
    async def selected(session, geometry_id):
        return row
    monkeypatch.setattr(owner, "_geometry_or_404", selected)
    monkeypatch.setattr(owner, "get_data_root", lambda: data)
    reader = owner.get_geometry_preview if role == "preview" else owner.get_geometry_points
    response = await reader("geometry", session=None)
    assert response.status_code == 200
    if role == "preview":
        assert response.body == payload
    else:
        assert b"data_geometry" in response.body
    key = "preview_obj_sha256" if role == "preview" else "point_pool_sha256"
    row.manifest[key] = "0" * 64
    with pytest.raises(HTTPException, match="hash mismatch") as error:
        await reader("geometry", session=None)
    assert error.value.status_code == 409
    row.manifest[key] = hashlib.sha256(payload).hexdigest()
    outside = tmp_path / "outside"
    outside.write_bytes(payload)
    (root / "escape").symlink_to(outside)
    row.artifacts["preview_obj" if role == "preview" else "points_f32"] = "escape"
    with pytest.raises(HTTPException, match="unavailable") as error:
        await reader("geometry", session=None)
    assert error.value.status_code == 409


@pytest.mark.parametrize("kind", ["regular", "hardlink", "leaf_alias", "root_alias"])
def test_shape_terminal_receiver_regular_and_retained_contract(tmp_path, kind):
    from services.result_ingester import _shape_contained_artifact
    root = tmp_path / "result"
    root.mkdir()
    member = root / "candidate.cif"
    member.write_bytes(b"data_INERT\n")
    if kind == "hardlink":
        os.link(member, tmp_path / "retained.cif")
    elif kind == "leaf_alias":
        member.rename(root / "concrete.cif")
        member.symlink_to("concrete.cif")
    elif kind == "root_alias":
        root.rename(tmp_path / "concrete")
        root.symlink_to("concrete", target_is_directory=True)
    descriptor = {"relative_path": member.name, "sha256": hashlib.sha256(member.read_bytes()).hexdigest(),
                  "bytes": member.stat().st_size}
    assert _shape_contained_artifact(root, descriptor, "structure") == member.resolve()
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        _shape_contained_artifact(root, {**descriptor, "sha256": "0" * 64}, "structure")
    with pytest.raises(RuntimeError, match="byte count mismatch"):
        _shape_contained_artifact(root, {**descriptor, "bytes": 0}, "structure")
    outside = tmp_path / "outside.cif"
    outside.write_bytes(member.read_bytes())
    (root / "escape.cif").symlink_to(outside)
    with pytest.raises(RuntimeError, match="outside"):
        _shape_contained_artifact(root, {**descriptor, "relative_path": "escape.cif"}, "structure")
    with pytest.raises(RuntimeError, match="unsafe"):
        _shape_contained_artifact(root, {**descriptor, "relative_path": "../outside.cif"}, "structure")
