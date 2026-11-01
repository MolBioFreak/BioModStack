"""Layout-pinned weights (La-Proteina, Complexa, Caliby packer) resolve and carry manifests.

Their bytes live in immutable ``weights/layouts/<name>/<digest>/`` generations with no plain
``weights/<name>`` path. The remote preview used to look only at the plain path, and the three
model definitions carried no acquisition manifests, so the de novo pack preview reported
``host_asset_unavailable``.
"""
import hashlib
import json
from pathlib import Path

import pytest

import model_registry as mr
from paths import resolve_weight_dependency
from services.runtime_acquisition import _layouts

# Digests of the generations already installed under /mnt/BioModStack/weights/layouts.
INSTALLED_LAYOUTS = {
    "laproteina": "46b67aee0f2639e3b9dd844f6f01edd90edc07d6369e3153637b0ef4684b5d9f",
    "complexa": "83251bdd523240ebf9ecba634a0acf38835c58eacf8a0a4877fae4c2ed8366f5",
    "caliby": "ee87830fda3498c14b2bbd7ad578607efb8819d01963687ca0eb896860c99840",
}
DIGEST = "a" * 64


def _layout(root: Path, name: str, digest: str, members: dict[str, bytes]) -> Path:
    base = root / "layouts" / name / digest
    for member, body in members.items():
        target = base / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return base


def test_a_member_resolves_through_its_single_layout_generation(tmp_path):
    base = _layout(tmp_path, "complexa", DIGEST, {"complexa.ckpt": b"x"})
    assert resolve_weight_dependency("complexa", tmp_path) == base
    assert resolve_weight_dependency("complexa/complexa.ckpt", tmp_path) == base / "complexa.ckpt"


def test_nested_members_resolve_through_the_layout_root(tmp_path):
    base = _layout(tmp_path, "caliby", DIGEST, {"model_params/caliby/caliby_packer_010.ckpt": b"x"})
    assert resolve_weight_dependency("caliby/model_params/caliby/caliby_packer_010.ckpt", tmp_path) == (
        base / "model_params/caliby/caliby_packer_010.ckpt")


def test_a_plain_path_keeps_precedence(tmp_path):
    (tmp_path / "complexa").mkdir()
    _layout(tmp_path, "complexa", DIGEST, {"complexa.ckpt": b"x"})
    assert resolve_weight_dependency("complexa", tmp_path) == tmp_path / "complexa"


def test_plain_directory_missing_the_member_falls_back_to_the_layout(tmp_path):
    (tmp_path / "caliby" / "model_params" / "caliby").mkdir(parents=True)
    base = _layout(tmp_path, "caliby", DIGEST, {"model_params/caliby/caliby_packer_010.ckpt": b"x"})
    assert resolve_weight_dependency("caliby/model_params/caliby/caliby_packer_010.ckpt", tmp_path) == (
        base / "model_params/caliby/caliby_packer_010.ckpt")


def test_several_generations_are_never_guessed(tmp_path):
    _layout(tmp_path, "complexa", "a" * 64, {"complexa.ckpt": b"x"})
    _layout(tmp_path, "complexa", "b" * 64, {"complexa.ckpt": b"y"})
    assert resolve_weight_dependency("complexa/complexa.ckpt", tmp_path) == tmp_path / "complexa/complexa.ckpt"


def test_no_layout_returns_the_plain_path(tmp_path):
    assert resolve_weight_dependency("complexa/complexa.ckpt", tmp_path) == tmp_path / "complexa/complexa.ckpt"


def _digest(dependency, members):
    return hashlib.sha256(json.dumps({"dependency": dependency, "members": sorted(
        members, key=lambda e: e["member_path"])}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("model_id, layout", [
    ("laproteina", "laproteina"), ("complexa", "complexa"), ("caliby_experimental", "caliby")])
def test_registry_manifests_reproduce_the_installed_layout_digest(model_id, layout):
    plan = mr.model_acquisition_plan(model_id)
    assert not [b for b in plan["blockers"] if b.get("code") == "invalid_acquisition_dependency_binding"]
    groups = {dependency["relative_path"]: members for dependency, members in _layouts(plan)}
    assert layout in groups
    assert _digest({"kind": "weights", "relative_path": layout}, groups[layout]) == INSTALLED_LAYOUTS[layout]


@pytest.mark.parametrize("model_id", ["laproteina", "complexa", "caliby_experimental"])
def test_weights_are_no_longer_reported_as_missing_acquisition_metadata(model_id):
    plan = mr.model_acquisition_plan(model_id)
    missing = [b for b in plan["blockers"] if b.get("code") == "approved_acquisition_metadata_missing"
               and b.get("kind") == "weights"]
    # Only the unrelated pinned Caliby soluble_caliby_v1 file (never acquired) may remain.
    assert [b["relative_path"] for b in missing] in ([], ["caliby/model_params/caliby/soluble_caliby_v1.ckpt"])


def test_laproteina_closure_includes_its_checkpoints():
    paths = {(d.kind, d.relative_path) for d in mr.model_runtime_dependencies("laproteina")}
    assert ("weights", "laproteina") in paths


def test_every_new_manifest_is_a_valid_pinned_weight():
    from scripts.lib.pinned_acquisition import Artifact
    for model_id in ("laproteina", "complexa", "caliby_experimental"):
        for entry in mr.get_registry().get_model(model_id).acquisition:
            Artifact(**entry.model_dump(exclude={"dependency", "member_path"}), kind="weights").validate()
