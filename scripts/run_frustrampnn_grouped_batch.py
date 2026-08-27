#!/usr/bin/env python3
"""Execute one scheduler-owned grouped FrustraMPNN predict_batch task."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "platform" / "api"))

from services.frustrampnn import runtime as _runtime  # noqa: E402
from services.frustrampnn.contracts import canonical_json_bytes  # noqa: E402

import prepare_persisted_frustrampnn_candidate as _prepare  # noqa: E402
import run_frustrampnn_component as _component  # noqa: E402

UPSTREAM_SEQUENTIAL_SEMANTICS = (
    "Pinned upstream FrustraMPNN.predict_batch processes pdb_paths sequentially "
    "under one loaded model object, catches each per-structure exception, and omits "
    "failed structures from its returned DataFrame."
)
_BATCH_FIELDS = {
    "schema_name", "schema_version", "execution_owner_job_id", "batching_enabled",
    "structures_per_job", "settings_sha256", "expected_cardinality", "records",
}
_RECORD_FIELDS = {
    "record_schema_name", "record_schema_version", "ordinal", "candidate_id",
    "invocation_id", "request_relative_path", "request_sha256", "request_size_bytes",
    "source_relative_path", "source_sha256", "source_size_bytes",
    "structure_map_relative_path", "structure_map_sha256", "structure_map_size_bytes",
}


class GroupedBatchError(RuntimeError):
    pass


class PreparedCandidate(NamedTuple):
    record: Mapping[str, Any]
    request_path: Path
    source_path: Path
    structure_map_path: Path
    staged_pdb_path: Path


def _read_batch(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict) or payload != canonical_json_bytes(value):
        raise GroupedBatchError("scheduler batch manifest must be exact canonical JSON")
    if set(value) != _BATCH_FIELDS or (
        value.get("schema_name") != "bms_frustrampnn_scheduler_batch"
        or value.get("schema_version") != 3
        or value.get("batching_enabled") is not True
        or isinstance(value.get("structures_per_job"), bool)
        or not isinstance(value.get("structures_per_job"), int)
        or not 2 <= value["structures_per_job"] <= 250
        or not isinstance(value.get("records"), list)
        or not 2 <= len(value["records"]) <= value["structures_per_job"]
        or value.get("expected_cardinality") != len(value["records"])
    ):
        raise GroupedBatchError("scheduler batch manifest is not a valid grouped v3 batch")
    for ordinal, record in enumerate(value["records"]):
        if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
            raise GroupedBatchError(f"scheduler record fields are not exact at ordinal {ordinal}")
        if (
            record.get("record_schema_name") != "bms_frustrampnn_scheduler_record"
            or record.get("record_schema_version") != 2
            or record.get("ordinal") != ordinal
        ):
            raise GroupedBatchError(f"scheduler record identity is invalid at ordinal {ordinal}")
    return value


def _default_prepare_record(
    record: Mapping[str, Any], *, authority_root: Path, work_root: Path
) -> PreparedCandidate:
    ordinal = int(record["ordinal"])
    candidate_root = work_root / f"candidate-{ordinal:04d}"
    candidate_root.mkdir(mode=0o700)
    request = candidate_root / "workflow_component_request_v3.json"
    source = candidate_root / "canonical_source.pdb"
    structure_map = candidate_root / "frustrampnn_structure_map_v1.json"
    encoded = base64.b64encode(canonical_json_bytes(dict(record))).decode("ascii")
    _prepare.prepare(
        record_base64=encoded,
        request_path=authority_root / str(record["request_relative_path"]),
        source_path=authority_root / str(record["source_relative_path"]),
        structure_map_path=authority_root / str(record["structure_map_relative_path"]),
        output_request=request,
        output_source=source,
        output_structure_map=structure_map,
    )
    staged = work_root / f"{ordinal:04d}_{hashlib.sha256(str(record['candidate_id']).encode()).hexdigest()[:16]}.pdb"
    shutil.copyfile(source, staged)
    return PreparedCandidate(record, request, source, structure_map, staged)


def _default_finalize_candidate(
    candidate: PreparedCandidate,
    terminal: Mapping[str, Any],
    *,
    output_root: Path,
    batch_invocation: _runtime.FrustraMPNNInvocation,
    batch_stdout: Path,
    batch_stderr: Path,
    physical_gpu_id: int,
) -> Path:
    bundle = output_root / str(candidate.record["candidate_id"])
    return _component.finalize_batched_component(
        request_path=candidate.request_path,
        source_structure=candidate.source_path,
        structure_map=candidate.structure_map_path,
        raw_csv=(output_root.parent / "runtime-output" / str(terminal["output_csv"]))
        if terminal["status"] == "succeeded" else None,
        terminal_evidence=terminal,
        batch_argv=batch_invocation.argv,
        batch_argv_sha256=batch_invocation.argv_sha256,
        stdout_log=batch_stdout,
        stderr_log=batch_stderr,
        output_dir=bundle,
        physical_gpu_id=physical_gpu_id,
    )


def run_grouped_batch(
    *,
    batch_manifest_path: Path | str,
    job_root: Path | str,
    container: Path | str,
    physical_gpu_id: int,
    apptainer: Path | str = "apptainer",
    prepare_record: Callable[..., PreparedCandidate] = _default_prepare_record,
    build_command: Callable[..., _runtime.FrustraMPNNInvocation] = _runtime.build_frustrampnn_predict_batch_command,
    execute: Callable[..., subprocess.CompletedProcess[bytes]] = _runtime.execute_frustrampnn,
    finalize_candidate: Callable[..., Path] = _default_finalize_candidate,
    pinned_container: Any | None = None,
) -> dict[str, Any]:
    """Run exactly one batch command and materialize every candidate terminal bundle."""
    manifest_path = Path(batch_manifest_path).absolute()
    root = Path(job_root).absolute()
    batch = _read_batch(manifest_path)
    if batch["execution_owner_job_id"] != root.name:
        raise GroupedBatchError("scheduler batch owner does not match the exact job root")
    authority_root = manifest_path.parent.parent
    output_root = Path.cwd() / "grouped_results"
    if output_root.exists() or output_root.is_symlink():
        raise GroupedBatchError("grouped result output already exists")
    output_root.mkdir(mode=0o700)
    work_root = Path(tempfile.mkdtemp(prefix="frustrampnn-grouped-", dir=Path.cwd()))
    runtime_output = output_root.parent / "runtime-output"
    runtime_output.mkdir(mode=0o700)
    stdout_log = output_root.parent / "frustrampnn_batch_stdout.log"
    stderr_log = output_root.parent / "frustrampnn_batch_stderr.log"
    owned_pin = None
    try:
        prepared = [
            prepare_record(record, authority_root=authority_root, work_root=work_root)
            for record in batch["records"]
        ]
        adapter_manifest = {
            "schema_name": "frustrampnn_predict_batch_input",
            "schema_version": 1,
            "checkpoint_path": _runtime.FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
            "device": "cuda:0",
            "records": [
                {
                    "ordinal": item.record["ordinal"],
                    "candidate_id": item.record["candidate_id"],
                    "invocation_id": item.record["invocation_id"],
                    "staged_pdb_path": os.fspath(item.staged_pdb_path.absolute()),
                    "source_sha256": hashlib.sha256(item.staged_pdb_path.read_bytes()).hexdigest(),
                }
                for item in prepared
            ],
        }
        adapter_manifest_path = work_root / "frustrampnn_predict_batch_input_v1.json"
        adapter_manifest_path.write_bytes(canonical_json_bytes(adapter_manifest))
        pin = pinned_container
        if pin is None:
            configured = _runtime.validate_configured_container_path(container)
            owned_pin = _runtime.open_verified_container(
                configured, _runtime.FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256
            )
            _runtime.verify_container_assets(apptainer, owned_pin)
            pin = owned_pin
        invocation = build_command(
            apptainer=apptainer,
            container=pin.proc_path,
            manifest=adapter_manifest_path,
            output_root=runtime_output,
            adapter=REPO_ROOT / "scripts" / "run_frustrampnn_predict_batch.py",
            physical_gpu_id=physical_gpu_id,
        )
        with stdout_log.open("wb") as stdout_handle, stderr_log.open("wb") as stderr_handle:
            completed = execute(
                invocation, pin, stdout=stdout_handle, stderr=stderr_handle,
                timeout=_component.DEFAULT_TIMEOUT_SECONDS, check=False,
            )
        if completed.returncode not in {0, 1, 2}:
            raise GroupedBatchError(f"predict_batch command returned unsupported exit {completed.returncode}")
        evidence_path = runtime_output / "frustrampnn_batch_terminal_evidence_v1.json"
        evidence = json.loads(evidence_path.read_bytes())
        if (
            not isinstance(evidence, dict)
            or evidence.get("record_count") != len(prepared)
            or evidence.get("model_load_count") != 1
            or evidence.get("method_identity") != "frustrampnn.FrustraMPNN.predict_batch"
            or evidence.get("upstream_sequential_semantics") != UPSTREAM_SEQUENTIAL_SEMANTICS
            or not isinstance(evidence.get("records"), list)
        ):
            raise GroupedBatchError("predict_batch terminal evidence is invalid")
        terminals = {item.get("ordinal"): item for item in evidence["records"] if isinstance(item, dict)}
        statuses = [item.get("status") for item in evidence["records"] if isinstance(item, dict)]
        if (
            (completed.returncode == 0 and any(status != "succeeded" for status in statuses))
            or (completed.returncode == 1 and not any(status == "failed" for status in statuses))
            or (completed.returncode == 2 and any(status != "failed" for status in statuses))
        ):
            raise GroupedBatchError("predict_batch exit status contradicts terminal evidence")
        if set(terminals) != set(range(len(prepared))):
            raise GroupedBatchError("predict_batch terminal evidence cardinality is invalid")
        bundles = []
        for candidate in prepared:
            terminal = terminals[candidate.record["ordinal"]]
            if (
                terminal.get("candidate_id") != candidate.record["candidate_id"]
                or terminal.get("invocation_id") != candidate.record["invocation_id"]
            ):
                raise GroupedBatchError("predict_batch terminal evidence identity is invalid")
            bundles.append(
                finalize_candidate(
                    candidate, terminal, output_root=output_root,
                    batch_invocation=invocation, batch_stdout=stdout_log,
                    batch_stderr=stderr_log, physical_gpu_id=physical_gpu_id,
                )
            )
        return {**evidence, "bundles": [os.fspath(path) for path in bundles]}
    finally:
        if owned_pin is not None:
            owned_pin.close()
        shutil.rmtree(work_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-manifest", required=True, type=Path)
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--container", required=True, type=Path)
    parser.add_argument("--physical-gpu-id", required=True, type=int)
    parser.add_argument("--apptainer", default="apptainer")
    args = parser.parse_args(argv)
    try:
        result = run_grouped_batch(
            batch_manifest_path=args.batch_manifest, job_root=args.job_root,
            container=args.container, physical_gpu_id=args.physical_gpu_id,
            apptainer=args.apptainer,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"frustrampnn_grouped_batch_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
