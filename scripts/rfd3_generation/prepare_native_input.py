#!/usr/bin/env python3
"""Validate a typed general-generation request and emit native RFD3 input."""

from __future__ import annotations

import argparse
import json
import hashlib
import os
import shutil
from pathlib import Path

from contract import PREPARATION_RECEIPT_SCHEMA, canonical_json, canonical_sha256, validate_request


def prepare(request: dict, *, source_destination: Path | None = None) -> tuple[dict, dict]:
    request = validate_request(request)
    generation = request["generation"]
    native = {
        "generation_0": {
            "dialect": 2,
            "length": f"{generation['min_length']}-{generation['max_length']}",
        }
    }
    if "conditioning" in request:
        from scripts.lib.rfd3_scientific_settings import conditioning_settings
        native["generation_0"].update(conditioning_settings(request["conditioning"]))
    source_mapping = None
    if "source" in request:
        source = Path(request["source"]["path"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != request["source"]["sha256"]:
            raise ValueError("conditioned source hash mismatch")
        native["generation_0"]["input"] = str(source)
        if request.get("source_mapping") == "bms.rfd3.source-map.v1":
            from scripts.rfd3_local_redesign.source_mapping import materialize_source, lower_native
            destination = source_destination or Path.cwd() / "rfd3_native_source.cif"
            source_mapping = materialize_source(source, destination, model_serial=request.get("source_model"))
            native["generation_0"] = lower_native(native["generation_0"], source_mapping, destination.name)
    receipt = {
        "schema": PREPARATION_RECEIPT_SCHEMA,
        "request_id": request["request_id"],
        "job_id": request["job_id"],
        "request_sha256": canonical_sha256(request),
        "native_input_sha256": canonical_sha256(native),
        "native_rfd3": native["generation_0"],
    }
    if source_mapping is not None:
        receipt["source_mapping"] = source_mapping
    return native, receipt


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise argparse.ArgumentTypeError("must be true or false")


def _parse_seed(value: str) -> int | None:
    if value.strip().lower() == "null":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer or null") from exc


def _require_expected(request: dict, args: argparse.Namespace) -> None:
    expected = {
        "generation.min_length": (request["generation"]["min_length"], args.expected_min_length),
        "generation.max_length": (request["generation"]["max_length"], args.expected_max_length),
        "generation.num_designs": (request["generation"]["num_designs"], args.expected_num_designs),
        "execution.seed": (request["execution"]["seed"], args.expected_seed),
        "execution.dump_trajectories": (
            request["execution"]["dump_trajectories"], args.expected_dump_trajectories
        ),
    }
    drift = [field for field, (actual, wanted) in expected.items() if actual != wanted]
    if drift:
        raise SystemExit(f"workflow param authority does not match canonical request: {', '.join(drift)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--expected-min-length", required=True, type=int)
    parser.add_argument("--expected-max-length", required=True, type=int)
    parser.add_argument("--expected-num-designs", required=True, type=int)
    parser.add_argument("--expected-seed", required=True, type=_parse_seed)
    parser.add_argument("--expected-dump-trajectories", required=True, type=_parse_bool)
    parser.add_argument("--output-native", required=True)
    parser.add_argument("--output-receipt", required=True)
    parser.add_argument("--source-dir", help="Owned source closure staged with the native input")
    args = parser.parse_args()

    request = validate_request(json.loads(Path(args.request).read_text(encoding="utf-8")))
    _require_expected(request, args)
    native, receipt = prepare(request, source_destination=Path(args.output_native).resolve().with_name("rfd3_native_source.cif"))
    if args.source_dir:
        source_dir = Path(args.source_dir).resolve()
        source_dir.mkdir(parents=True, exist_ok=True)
        runtime = native['generation_0']
        if 'input' in runtime:
            source = Path(runtime['input'])
            if not source.is_absolute():
                source = Path(args.output_native).resolve().parent / source
            target = source_dir / source.name
            if source.resolve() != target:
                shutil.copy2(source, target)
            runtime['input'] = os.path.relpath(target, Path(args.output_native).resolve().parent)
            receipt['native_input_sha256'] = canonical_sha256(native)
            receipt['runtime_input'] = {'path': runtime['input'], 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    Path(args.output_native).write_text(canonical_json(native) + "\n", encoding="utf-8")
    Path(args.output_receipt).write_text(canonical_json(receipt) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
