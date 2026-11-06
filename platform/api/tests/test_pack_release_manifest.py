"""A pack's release manifest holds only release namespaces; runtime_data rides the artifact cache.

The de novo pack selects ``data/md-preparation/env-v1-explicit.txt``. The preview validated a
manifest that included it whenever the pack also carried layout links, and the managed-release
validator rejected the ``data/`` row (``invalid_artifact_path``), so the whole preview returned 409.
"""
import hashlib
from types import SimpleNamespace

import pytest

from services.remote_execution.contracts import WorkflowPackSelection
from services.remote_execution.managed_inventory import manifest_for
from tools import bms_artifact_cache, bms_managed_runtime

SOURCE = ("a" * 40, "b" * 40)
SELECTION = WorkflowPackSelection(kind="workflow_pack", workflow_id="de_novo_design")


def _entry(name, *, body=b"payload", role="runtime", mode=0o444, link=None):
    data = (link or "").encode() if link else body
    return SimpleNamespace(remote_destination=name, sha256=hashlib.sha256(data).hexdigest(),
                           size_bytes=len(data), mode=mode, role=role, link_target=link)


def _pack():
    return [
        _entry("containers/protenix.sif", role="image", mode=0o555),
        _entry("weights/complexa/complexa.ckpt"),
        _entry("weights/laproteina/AE1.ckpt", link="../complexa/complexa.ckpt"),
        _entry("data/md-preparation/env-v1-explicit.txt"),
    ]


def test_runtime_data_stays_out_of_the_release_manifest():
    names = [row["name"] for row in manifest_for(SELECTION, _pack(), SOURCE)["artifacts"]]
    assert names == ["containers/protenix.sif", "weights/complexa/complexa.ckpt", "weights/laproteina/AE1.ckpt"]


def test_a_pack_with_runtime_data_and_layout_links_validates():
    bms_managed_runtime.validate_manifest(manifest_for(SELECTION, _pack(), SOURCE), bms_artifact_cache)


def test_the_validator_still_rejects_a_foreign_namespace_row():
    manifest = manifest_for(SELECTION, _pack(), SOURCE)
    manifest["artifacts"].append(dict(name="data/other.txt", sha256="0" * 64, size_bytes=1, mode=0o444))
    with pytest.raises(ValueError, match="invalid_artifact_path"):
        bms_managed_runtime.validate_manifest(manifest, bms_artifact_cache)
