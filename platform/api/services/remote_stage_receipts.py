"""Attempt-local stage metadata, applied only inside an explicitly pulled attempt.

Stdlib-only writer: usable by workflow sandboxes without API/network setup.
Receipts describe stages; ordinary result validators retain scientific authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid

RECEIPT_DIRECTORY = ".bms-stage-receipts"
SCHEMA = "bms.remote-stage-receipt.v1"
_FIELDS = {"schema", "job_id", "attempt_id", "stage", "status", "outputs"}
_STAGE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def relative_output(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("unsafe remote stage output path")
    if value.split("/")[0] == RECEIPT_DIRECTORY:
        raise ValueError("stage receipts cannot be scientific outputs")
    return value


def _confined(root: Path, relative: str, *, regular: bool = True) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("remote stage path escapes results")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("remote stage path traverses a symlink")
    if regular and not path.is_file():
        raise ValueError("remote stage output is not a regular file")
    return path


def _validate(payload: Any, *, job_id: str, attempt_id: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != _FIELDS or payload.get("schema") != SCHEMA:
        raise ValueError("invalid closed remote stage receipt")
    if not job_id or payload["job_id"] != job_id or payload["attempt_id"] != attempt_id:
        raise ValueError("remote stage receipt job/attempt mismatch")
    if str(uuid.UUID(attempt_id)) != attempt_id:
        raise ValueError("invalid remote attempt identity")
    if not isinstance(payload["stage"], str) or not _STAGE.fullmatch(payload["stage"]):
        raise ValueError("invalid remote stage name")
    if payload["status"] not in ("start", "complete", "failed", "not_requested"):
        raise ValueError("invalid remote stage status")
    outputs = payload["outputs"]
    if not isinstance(outputs, list) or len(outputs) > 100000:
        raise ValueError("invalid remote stage outputs")
    for value in outputs:
        relative_output(value)
    if len(outputs) != len(set(outputs)) or (payload["status"] in {"start", "not_requested"} and outputs):
        raise ValueError("invalid remote stage output cardinality")
    return payload


def _filename(payload: dict) -> str:
    return f"{payload['stage']}.{'start' if payload['status'] == 'start' else 'terminal'}.json"


def write_remote_stage_receipt(*, job_id: str, stage: str, status: str,
                               outputs: list[str], job_root_relative: bool = False) -> Path:
    if os.environ.get("BMS_REMOTE_EXECUTION") != "1":
        raise ValueError("remote stage writer requires remote execution")
    attempt_id = os.environ.get("BMS_REMOTE_ATTEMPT_ID", "")
    if job_id != os.environ.get("BMS_REMOTE_JOB_ID"):
        raise ValueError("remote stage reporter job mismatch")
    raw_root = os.environ.get("BMS_REMOTE_OUTPUT_ROOT", "")
    root = Path(raw_root)
    if not raw_root or not root.is_absolute() or str(root.resolve()) != raw_root:
        raise ValueError("remote stage output root must be trusted and absolute")
    cleaned = []
    for value in outputs:
        relative = relative_output(value) if job_root_relative else relative_output(
            Path(os.path.abspath(value)).relative_to(root).as_posix())
        # Publication may be asynchronous; existence is required at verified pull.
        _confined(root, relative, regular=False)
        cleaned.append(relative)
    payload = _validate(dict(schema=SCHEMA, job_id=job_id, attempt_id=attempt_id,
                             stage=stage, status=status, outputs=cleaned),
                        job_id=job_id, attempt_id=attempt_id)
    directory = _confined(root, RECEIPT_DIRECTORY, regular=False)
    directory.mkdir(parents=True, exist_ok=True)
    destination = _confined(root, f"{RECEIPT_DIRECTORY}/{_filename(payload)}", regular=False)
    encoded = canonical_bytes(payload)
    # Publish a complete fsynced file without replacing any prior terminal state.
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or destination.read_bytes() != encoded:
                raise ValueError("remote stage terminal receipt is immutable")
    finally:
        os.unlink(temporary)
    return destination


def validate_remote_stage_receipts(*, output_root: Path, job_id: str,
                                   attempt_id: str, manifest: Any) -> list[dict]:
    """Recheck receipt bytes and referenced outputs against verified transfer manifest."""
    if manifest.job_id != job_id or manifest.attempt_id != attempt_id or manifest.exit_code != 0:
        raise ValueError("remote result manifest job/attempt/success mismatch")
    root = Path(output_root)
    records = {record.relative_path: record for record in manifest.artifacts}
    receipt_names = sorted(name for name in records if name.startswith(RECEIPT_DIRECTORY + "/"))
    directory = _confined(root, RECEIPT_DIRECTORY, regular=False)
    actual = sorted(p.relative_to(root).as_posix() for p in directory.rglob("*") if not p.is_dir()) if directory.exists() else []
    if actual != receipt_names:
        raise ValueError("remote stage receipt set is not integrity manifested")

    def verified_bytes(name: str) -> bytes:
        record = records.get(name)
        if record is None or record.link_target is not None:
            raise ValueError("remote stage output is not integrity manifested")
        path = _confined(root, name)
        if path.stat().st_size != record.size_bytes:
            raise ValueError("remote stage artifact size mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != record.sha256:
            raise ValueError("remote stage artifact digest mismatch")
        return path.read_bytes() if name in receipt_names else b""

    receipts = []
    for name in receipt_names:
        if records[name].size_bytes > 16 * 1024 * 1024:
            raise ValueError("remote stage receipt is too large")
        encoded = verified_bytes(name)
        payload = _validate(json.loads(encoded), job_id=job_id, attempt_id=attempt_id)
        if encoded != canonical_bytes(payload) or name != f"{RECEIPT_DIRECTORY}/{_filename(payload)}":
            raise ValueError("remote stage receipt is not canonical")
        for output in payload["outputs"]:
            verified_bytes(output)
        receipts.append(payload)
    terminals = {p["stage"] for p in receipts if p["status"] != "start"}
    if any(p["stage"] not in terminals for p in receipts):
        raise ValueError("remote stage has no terminal receipt")
    return receipts


async def apply_remote_stage_receipts(*, session: Any, job: Any, attempt_id: str,
                                     output_root: Path, manifest: Any) -> int:
    """Apply to C's locked current Job/session after explicit verified download.

    No commits, callbacks, lifecycle transitions or scientific ingestion here.
    Caller owns current-attempt/source locks and the manual-pull transaction.
    """
    from sqlalchemy import inspect
    if inspect(job).session is not session.sync_session:
        raise ValueError("remote receipts require the exact current attached job/session")
    if (not job.execution_target_id or job.remote_attempt_id != attempt_id
            or job.remote_state != "returning" or job.status in {"completed", "failed", "cancelled"}
            or job.nextflow_run_id != f"remote:{attempt_id}"):
        raise ValueError("remote stage receipts lost current pull authority")
    expected_root = Path(job.child_output_dir or job.output_dir).resolve()
    if Path(output_root).resolve() != expected_root:
        raise ValueError("remote stage receipt root does not match current job")
    # Domain-specific lifecycle anchors must not be replaced by generic metadata.
    if job.model_id in {"nanopore", "molecular_dynamics"}:
        raise ValueError("remote generic stage receipts cannot authorize domain lifecycle anchors")
    receipts = validate_remote_stage_receipts(output_root=output_root, job_id=str(job.id),
                                               attempt_id=attempt_id, manifest=manifest)
    completed = list(job.completed_stages or [])
    outputs = dict(job.stage_outputs or {})
    provenance = dict(job.provenance or {})
    states = dict(provenance.get("stage_terminal_states") or {})
    for receipt in receipts:
        if receipt["status"] == "start":
            continue
        stage = receipt["stage"]
        terminal = {"status": receipt["status"], "outputs": receipt["outputs"]}
        if stage in states and states[stage] != terminal:
            raise ValueError("workflow stage terminal state is immutable")
        if stage in outputs and outputs[stage] != receipt["outputs"]:
            raise ValueError("workflow stage outputs are immutable")
        if receipt["status"] == "complete":
            if stage not in completed:
                completed.append(stage)
        elif stage in completed:
            raise ValueError("completed workflow stage cannot be reclassified")
        states[stage] = terminal
        outputs[stage] = receipt["outputs"]
    provenance["stage_terminal_states"] = states
    provenance["remote_stage_receipts"] = {"schema": SCHEMA, "attempt_id": attempt_id,
        "job_id": str(job.id), "receipts_sha256": hashlib.sha256(canonical_bytes(receipts)).hexdigest()}
    job.completed_stages = completed
    job.stage_outputs = outputs
    job.provenance = provenance
    if job.current_stage in states:
        job.current_stage = None
    await session.flush()
    return len(receipts)
