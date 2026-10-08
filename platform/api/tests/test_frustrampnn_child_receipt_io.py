"""Receipt I/O boundaries using synthetic metadata, without model execution."""
from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from database import Job

from services.frustrampnn import jobs, manifests
from services.frustrampnn.contracts import canonical_json_bytes


def _child(root: Path):
    path = root / "inputs" / "frustrampnn_scheduler_batch_v3.json"
    path.parent.mkdir(parents=True)
    payload = canonical_json_bytes({
        "schema_name": "bms_frustrampnn_scheduler_batch", "schema_version": 3,
        "execution_owner_job_id": "child", "expected_cardinality": 1,
        "records": [{"candidate_id": "fixture", "invocation_id": "frustrampnn:fixture"}],
    })
    path.write_bytes(payload)
    envelope = {
        "batch_manifest_relative_path": "inputs/frustrampnn_scheduler_batch_v3.json",
        "batch_manifest_size_bytes": len(payload),
        "batch_manifest_sha256": hashlib.sha256(payload).hexdigest(), "selection": [],
    }
    child = SimpleNamespace(
        id="child", name="I/O fixture", model_id="frustrampnn", status="completed",
        parent_job_id="parent", output_dir=str(root),
        params={"frustrampnn_batch_manifest_path": str(path), jobs.ENVELOPE_KEY: envelope},
        created_at=None, started_at=None, completed_at=None,
    )
    return child, path


@pytest.mark.parametrize("target", ["manifest", "grouped"])
def test_receipt_rejects_oversized_metadata_before_reading(tmp_path, target):
    child, path = _child(tmp_path)
    if target == "grouped":
        path = tmp_path / "frustrampnn/batches/grouped_batch_terminal_receipt_v1.json"
        path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.truncate(jobs.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(jobs.FrustraMPNNChildError, match="unavailable"):
        jobs._child_receipt_artifacts(child_id=child.id, output_dir=child.output_dir, params=child.params)


def test_receipt_rejects_symlinked_metadata_parent(tmp_path):
    child, _path = _child(tmp_path)
    (tmp_path / "inputs").rename(tmp_path / "actual-inputs")
    (tmp_path / "inputs").symlink_to(tmp_path / "actual-inputs", target_is_directory=True)
    with pytest.raises(jobs.FrustraMPNNChildError):
        jobs._child_receipt_artifacts(child_id=child.id, output_dir=child.output_dir, params=child.params)


@pytest.mark.asyncio
async def test_receipt_file_validation_is_off_loop_and_database_stays_on_loop(tmp_path, monkeypatch):
    child, _path = _child(tmp_path)
    owner = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    original = manifests._read_regular
    reads = []

    def delayed_read(*args, **kwargs):
        reads.append(threading.get_ident())
        assert threading.get_ident() != owner
        started.set()
        assert release.wait(5), "event loop failed to release file validation"
        assert kwargs["max_bytes"] == jobs.MAX_UPLOAD_BYTES
        return original(*args, **kwargs)

    class Session:
        async def execute(self, _query):
            assert threading.get_ident() == owner
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    monkeypatch.setattr(manifests, "_read_regular", delayed_read)
    task = asyncio.create_task(jobs.child_receipt(cast(AsyncSession, Session()), child=cast(Job, child)))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        assert not task.done()
        assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), timeout=1) == "responsive"
    finally:
        release.set()
    receipt = await asyncio.wait_for(task, timeout=3)
    assert reads and receipt["batch_manifest"]["ordered_candidate_ids"] == ["fixture"]
