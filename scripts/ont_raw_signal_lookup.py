#!/usr/bin/env python3
"""Read one bounded waveform from an indexed BLOW5 source."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blow5", required=True)
    parser.add_argument("--read-id", required=True)
    parser.add_argument("--max-samples", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.max_samples < 1 or args.max_samples > 20_000:
        raise ValueError("max-samples exceeds the bounded waveform contract")

    import pyslow5

    slow5 = pyslow5.Open(args.blow5, "r")
    read = slow5.get_read(args.read_id, pA=True)
    if read is None:
        raise KeyError("read ID is absent from indexed BLOW5")
    signal = read.get("signal")
    if signal is None:
        raise ValueError("BLOW5 read lacks signal")
    full_count = int(len(signal))
    stride = max(1, (full_count + args.max_samples - 1) // args.max_samples)
    bounded = [float(value) for value in signal[::stride]][: args.max_samples]
    receipt = {
        "schema": "bms.ont.raw-waveform.v1",
        "read_id": args.read_id,
        "sample_count": full_count,
        "returned_sample_count": len(bounded),
        "stride": stride,
        "units": "pA",
        "samples": bounded,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
