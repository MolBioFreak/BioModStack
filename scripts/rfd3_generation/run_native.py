"""Execute native Foundry batches with an exact final remainder (no output slicing)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.lib.rfd3_scientific_settings import batch_plan, native_overrides


def commands(request, input_path, output_dir):
    execution = request["execution"]
    total = request.get("generation", execution)["num_designs"]
    plan = batch_plan(total, execution.get("diffusion_batch_size", 1))
    result = []
    for index, batch in enumerate(plan):
        command = ["rfd3", f"inputs={input_path}", f"out_dir={output_dir}",
                   f"n_batches={batch['n_batches']}",
                   f"diffusion_batch_size={batch['diffusion_batch_size']}",
                   *native_overrides(execution)]
        if len(plan) > 1:
            # Native writer owns sample IDs; disjoint prefixes distinguish the
            # two native invocations without renaming or slicing their outputs.
            command.append(f"global_prefix=bms_part{index}_")
        result.append(command)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text())
    for command in commands(request, args.inputs, args.out_dir):
        subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
