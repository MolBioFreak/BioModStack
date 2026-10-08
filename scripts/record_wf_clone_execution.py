#!/usr/bin/env python3
"""Bind the actual wrapper argv and input bytes before/after nested execution."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

INPUT_FLAGS = ("--bam", "--full_reference", "--primers", "--insert_reference", "--host_reference", "--regions_bedfile")


def identity(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("begin", "finish"))
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--runtime-provenance", type=Path)
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--workflow-id", choices=("wf_clone_validation", "ont_construct_screening"))
    raw = sys.argv[1:]
    separator = raw.index("--") if "--" in raw else len(raw)
    args = parser.parse_args(raw[:separator])
    if args.phase == "begin":
        argv = raw[separator + 1:]
        inputs = {}
        for flag in INPUT_FLAGS:
            if flag in argv:
                if argv.count(flag) != 1:
                    raise ValueError("duplicate execution input flag")
                path = Path(argv[argv.index(flag) + 1]).resolve(strict=True)
                inputs[flag] = {"path": str(path), **identity(path)}
        if args.runtime_provenance is None or args.code_root is None or args.workflow_id is None:
            raise ValueError("clone execution needs runtime and executing wrapper identities")
        runtime_path = args.runtime_provenance.resolve(strict=True)
        runtime = json.loads(runtime_path.read_text())
        code = args.code_root.resolve(strict=True)
        source_names = [f"workflows/ngs/{args.workflow_id}.nf", "modules/ngs/clone_validation.nf",
                        "scripts/validate_wf_clone_runtime.py", "scripts/record_wf_clone_execution.py"]
        sources = {name: identity(code / name) for name in source_names}
        runtime_files = {str(runtime_path): identity(runtime_path)}
        for item in [runtime["lock"], runtime["compatibility_patch"], *runtime["images"],
                     *runtime["runtime_files"], *runtime["source_closure"]]:
            observed = identity(item["path"])
            if observed["sha256"] != item["sha256"] or ("size_bytes" in item and observed["size_bytes"] != item["size_bytes"]):
                raise ValueError("clone runtime changed after preflight")
            runtime_files[item["path"]] = observed
        receipt = {"schema": "bms.ngs.clone-execution.v1", "argv": argv,
                   "inputs": inputs, "state": "running", "exit_code": None,
                   "code_root": str(code), "executed_sources": sources,
                   "runtime_provenance_sha256": identity(runtime_path)["sha256"],
                   "runtime_files": runtime_files}
    else:
        receipt = json.loads(args.receipt.read_text())
        if receipt["state"] != "running" or args.exit_code is None:
            raise ValueError("clone execution does not have a pending receipt")
        for item in receipt["inputs"].values():
            if identity(item["path"]) != {key: item[key] for key in ("sha256", "size_bytes")}:
                raise ValueError("clone input bytes changed during execution")
        for name, expected in receipt["executed_sources"].items():
            if identity(Path(receipt["code_root"]) / name) != expected:
                raise ValueError("clone wrapper source changed during execution")
        for name, expected in receipt["runtime_files"].items():
            if identity(name) != expected:
                raise ValueError("clone runtime source/image changed during execution")
        receipt.update(state="succeeded" if args.exit_code == 0 else "failed", exit_code=args.exit_code)
    args.receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
