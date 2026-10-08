#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from caliby_runtime import convert_fampnn_constraints_to_caliby


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert FAMPNN-style antibody constraints into Caliby positional constraints.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    convert_fampnn_constraints_to_caliby(Path(args.input).resolve(), Path(args.output).resolve())


if __name__ == "__main__":
    main()

