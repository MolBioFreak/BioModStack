#!/opt/complexa/.venv/bin/python
"""Keep native ColabFold AF2 data separate from Complexa's HF cache.

Installed as colabfold_batch in this image only. The pinned native caller sends
CACHE_DIR as --data. Translate that specific argument; leave explicit CLI paths,
all scientific options and the upstream CLI implementation unchanged.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
import sys


def native_data_args(args: list[str], env: Mapping[str, str]) -> list[str]:
    args = list(args)
    data = env.get("COMPLEXA_COLABFOLD_DATA")
    if not data and env.get("AF2_DIR"):
        params = Path(env["AF2_DIR"]).expanduser()
        # Existing BMS binding is <selected-root>/alphafold/params.
        if params.name == "params":
            data = str(params.parent)
    if data:
        for i, value in enumerate(args[:-1]):
            if value == "--data" and args[i + 1] in {env.get("CACHE_DIR"), "None"}:
                args[i + 1] = str(Path(data).expanduser())
    return args


def main() -> None:
    sys.argv[1:] = native_data_args(sys.argv[1:], os.environ)
    from colabfold.batch import main as native_main
    native_main()


if __name__ == "__main__":
    main()
