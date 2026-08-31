#!/usr/bin/env python3
"""Validate a typed general-generation request and emit native RFD3 input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from contract import PREPARATION_RECEIPT_SCHEMA, canonical_json, canonical_sha256, validate_request


def prepare(request: dict) -> tuple[dict, dict]:
    request = validate_request(request)
    generation = request["generation"]
    native = {
        "generation_0": {
            "dialect": 2,
            "length": f"{generation['min_length']}-{generation['max_length']}",
        }
    }
    receipt = {
        "schema": PREPARATION_RECEIPT_SCHEMA,
        "request_id": request["request_id"],
        "job_id": request["job_id"],
        "request_sha256": canonical_sha256(request),
        "native_input_sha256": canonical_sha256(native),
        "native_rfd3": native["generation_0"],
    }
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
    args = parser.parse_args()

    request = validate_request(json.loads(Path(args.request).read_text(encoding="utf-8")))
    _require_expected(request, args)
    native, receipt = prepare(request)
    Path(args.output_native).write_text(canonical_json(native) + "\n", encoding="utf-8")
    Path(args.output_receipt).write_text(canonical_json(receipt) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
