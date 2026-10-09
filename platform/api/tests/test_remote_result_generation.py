"""Offline crash/retry tests of the integrated return path and durable journal."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text

from database import Job
from services.remote_execution import executor as ex, result_generation as gen
from services.remote_execution.contracts import RemoteResultManifest
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready, success


def job_at(root):
    return SimpleNamespace(id="job", remote_attempt_id="attempt", execution_target_id="target",
        execution_source_revision="a" * 40, execution_source_tree="b" * 40,
        execution_bundle_sha256="c" * 64, output_dir=str(root / "output"),
        child_output_dir=None, provenance={})


def package(job, *, state="succeeded"):
    status = success().model_copy(update={"state": state, "exit_code": 0 if state == "succeeded" else 1})
    artifacts = [dict(relative_path=name, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), role="result")
                 for name, data in [("first.txt", b"first"), ("second.txt", b"second")]]
    assert status.exit_code is not None and status.completed_at is not None
    manifest = RemoteResultManifest(job_id=job.id, attempt_id=job.remote_attempt_id,
        source_revision=job.execution_source_revision, source_tree=job.execution_source_tree,
        execution_envelope_sha256=job.execution_bundle_sha256, artifacts=artifacts,
        exit_code=status.exit_code, completed_at=status.completed_at)
    encoded = manifest.model_dump_json().encode()
    digest = hashlib.sha256(encoded).hexdigest()
    incoming = gen.staging_path(job, digest)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "result-manifest.json").write_bytes(encoded)
    (incoming / "first.txt").write_bytes(b"first")
    (incoming / "second.txt").write_bytes(b"second")
    return manifest, incoming, status.model_copy(update={"result_manifest_sha256": digest})
