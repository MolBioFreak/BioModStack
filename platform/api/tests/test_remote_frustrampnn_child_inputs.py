"""Closed scheduler snapshot relocation and consumed-byte worker preflight."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.remote_execution import bundle
from tools import bms_remote_worker as worker


def _fixture(root: Path) -> tuple[Any, dict[str, Any], dict[str, Any], Path]:
    root.mkdir()
    record = dict(record_schema_name="bms_frustrampnn_scheduler_record", record_schema_version=2,
                  ordinal=0, candidate_id="candidate", invocation_id="invocation")
    for kind, directory, filename, payload in (
        ("request", "requests", "workflow_component_request_v3.json", b'{"settings":"unchanged"}'),
        ("source", "sources", "canonical_source.pdb", b"ATOM preserved coordinates\n"),
        ("structure_map", "maps", "frustrampnn_structure_map_v1.json", b'{"map":"unchanged"}'),
    ):
        relative = f"inputs/{directory}/0000/{filename}"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
        record.update({f"{kind}_relative_path": relative, f"{kind}_size_bytes": len(payload),
                       f"{kind}_sha256": hashlib.sha256(payload).hexdigest()})
    batch = dict(schema_name="bms_frustrampnn_scheduler_batch", schema_version=3,
                 execution_owner_job_id="child-id", batching_enabled=False, structures_per_job=1,
                 settings_sha256="a" * 64, expected_cardinality=1, records=[record])
    manifest = root / "inputs/frustrampnn_scheduler_batch_v3.json"
    payload = bundle._canonical_bytes(batch)
    manifest.write_bytes(payload)
    authority = dict(batch_manifest_relative_path=str(manifest.relative_to(root)),
                     batch_manifest_size_bytes=len(payload), batch_manifest_sha256=hashlib.sha256(payload).hexdigest())
    job = SimpleNamespace(id="child-id", model_id="frustrampnn", params={"_frustrampnn_child_v1": authority})
    params = {"frustrampnn_batch_manifest_path": str(manifest)}
    return job, params, batch, manifest


@pytest.mark.parametrize("tamper", [None, "request", "source", "structure_map", "manifest"])
def test_relocated_consumed_bytes_are_preflight_verified(tmp_path, tamper):
    root = tmp_path / "local-child"
    job, params, batch, manifest = _fixture(root)
    (root / "unrelated-secret").write_text("must not transfer")
    assets = bundle._frustrampnn_child_inputs(job, params, root)
    assert len(assets) == 4
    attempt = tmp_path / "remote-attempt"
    records = []
    mapping = {}
    for source, relative in assets:
        destination = attempt / "bundle/inputs" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        records.append(bundle._record_file(source, f"inputs/{relative}", "input").model_dump(mode="json"))
        mapping[str(source)] = str(destination)
    relocated = Path(bundle._rewrite(str(manifest), mapping))
    assert relocated.read_bytes() == manifest.read_bytes()
    authority_root = relocated.parent.parent
    for kind in ("request", "source", "structure_map"):
        relative = batch["records"][0][f"{kind}_relative_path"]
        assert (authority_root / relative).read_bytes() == (root / relative).read_bytes()
    archive = attempt / "bundle/source/.bms-source.tar"
    archive.parent.mkdir()
    archive.write_bytes(b"test source archive identity")
    archive_record = bundle._record_file(archive, "source/.bms-source.tar", "source").model_dump(mode="json")
    records.append(archive_record)
    envelope = dict(schema="bms.remote-execution.v1", command=["nextflow"], files=records,
                    source_archive_sha256=archive_record["sha256"], working_directory=str(archive.parent),
                    output_directory=str(attempt / "results"))
    (attempt / worker.ENVELOPE_FILE).write_text(json.dumps(envelope))
    if tamper:
        target = relocated if tamper == "manifest" else authority_root / batch["records"][0][f"{tamper}_relative_path"]
        target.write_bytes(target.read_bytes() + b"tamper")
        with pytest.raises(RuntimeError, match="hash mismatch"):
            worker.verify_bundle(attempt)
    else:
        worker.verify_bundle(attempt)
        assert not list((attempt / "results").iterdir())


@pytest.mark.parametrize("failure", ["traversal", "symlink", "digest", "owner", "extra-field"])
def test_snapshot_admission_fails_closed(tmp_path, failure):
    root = tmp_path / "child"
    job, params, batch, manifest = _fixture(root)
    record = batch["records"][0]
    if failure == "traversal":
        record["request_relative_path"] = "inputs/requests/../../outside/workflow_component_request_v3.json"
    elif failure == "symlink":
        source = root / record["source_relative_path"]
        source.rename(source.with_suffix(".real"))
        source.symlink_to(source.with_suffix(".real"))
    elif failure == "digest":
        record["source_sha256"] = "0" * 64
    elif failure == "owner":
        batch["execution_owner_job_id"] = "other-child"
    else:
        record["undeclared_input"] = "/outside"
    payload = bundle._canonical_bytes(batch)
    manifest.write_bytes(payload)
    job.params["_frustrampnn_child_v1"].update(batch_manifest_size_bytes=len(payload),
        batch_manifest_sha256=hashlib.sha256(payload).hexdigest())
    with pytest.raises(bundle.RemoteBundleError):
        bundle._frustrampnn_child_inputs(job, params, root)
