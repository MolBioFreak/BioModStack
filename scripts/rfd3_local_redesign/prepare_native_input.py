#!/usr/bin/env python3
"""Materialize one canonical local-redesign request for native RFD3 execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from contract import canonical_json, request_sha256
from scripts.lib.rfd3_scientific_settings import task_dispatch
from scripts.rfd3_local_redesign.source_mapping import materialize_source, lower_native


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Canonical product request JSON")
    parser.add_argument("--input-structure", required=True, help="Staged source structure")
    parser.add_argument("--design-id", default="protein_local_redesign_0")
    parser.add_argument("--output-native", required=True, help="Native RFD3 JSON input")
    parser.add_argument("--output-receipt", required=True, help="Runtime preparation receipt")
    args = parser.parse_args()

    request_path = Path(args.request).expanduser().resolve()
    input_path = Path(args.input_structure).expanduser().resolve()
    request: dict[str, Any] = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != "bms.rfd3.local-redesign.request.v1":
        raise SystemExit("unsupported local-redesign request schema")
    native = request.get("rfd3")
    if not isinstance(native, dict):
        raise SystemExit("request.rfd3 must be an object")
    if not input_path.is_file():
        raise SystemExit(f"input structure does not exist: {input_path}")

    input_binding = request.get("input")
    if not isinstance(input_binding, dict) or not isinstance(input_binding.get("path"), str):
        raise SystemExit("request.input must contain a source path")
    staged_input_sha256 = _sha256_file(input_path)
    if input_binding.get("sha256") != staged_input_sha256:
        raise SystemExit("staged input structure does not match the canonical request")
    runtime_native = dict(native)
    runtime_native["input"] = input_path.name
    source_mapping = None
    if request.get("source_mapping") == "bms.rfd3.source-map.v1":
        derived = Path(args.output_native).resolve().with_name("rfd3_native_source.cif")
        source_mapping = materialize_source(input_path, derived, model_serial=request.get("source_model"))
        runtime_native = lower_native(native, source_mapping, derived.name)
    native_payload = {args.design_id: runtime_native}
    native_json = canonical_json(native_payload)
    Path(args.output_native).write_text(native_json + "\n", encoding="utf-8")

    request_digest = request_sha256(request)
    receipt = {
        "schema": "bms.rfd3.local-redesign.preparation-receipt.v1",
        "request_sha256": request_digest,
        "request_path": str(request_path),
        "native_input_sha256": hashlib.sha256(native_json.encode("utf-8")).hexdigest(),
        "runtime_input": {
            "path": input_path.name,
            "sha256": staged_input_sha256,
        },
        "design_id": args.design_id,
        "redesign_mode": request.get("redesign_mode"),
        "sequence_policy": request.get("sequence_policy"),
        "sequence_design": {
            "state": "not_requested" if request.get("sequence_policy") == "skip" else "requested",
            "reason": "sequence_design_not_requested" if request.get("sequence_policy") == "skip" else None,
        },
        "native_rfd3": runtime_native,
    }
    receipt["task_dispatch"] = task_dispatch(request.get("redesign_mode"), runtime_native)
    if source_mapping is not None:
        receipt["source_mapping"] = source_mapping
        receipt["runtime_input"] = {"path": runtime_native["input"], "sha256": source_mapping["native_sha256"]}
    Path(args.output_receipt).write_text(canonical_json(receipt) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
