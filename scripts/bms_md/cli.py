from __future__ import annotations

import argparse
import json
from pathlib import Path

from .aggregate import write_aggregate
from .analysis import write_analysis_report
from .checkpoint_receipt import write_checkpoint_receipt
from .contract import normalize_job_config
from .engine_adapters import run_md_replica
from .feature_gate import require_experimental_md_feature


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bms-md", description="BioModStack production MD runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate and normalize an MD job JSON document")
    validate.add_argument("--config", type=Path, required=True)
    validate.add_argument("--output", type=Path)
    validate.add_argument("--gpu-id", help="override execution.gpu_id with the container-local device")
    validate.add_argument(
        "--scheduler-gpu-id",
        help="record the scheduler-owned physical device for provenance",
    )
    validate.add_argument(
        "--gpu-offload",
        choices=("auto", "full", "full_forces", "none"),
        help="override execution.gpu_offload with the qualified runtime policy",
    )
    validate.add_argument(
        "--base-dir",
        type=Path,
        help="resolve relative input structure/topology paths against this directory",
    )

    run = subparsers.add_parser("run", help="execute one MD replica")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--replica-index", type=int, default=0)
    run.add_argument("--preparation-bundle", type=Path)
    run.add_argument("--resume-checkpoint", type=Path)

    checkpoint_receipt = subparsers.add_parser(
        "checkpoint-receipt", help="validate and publish the latest interrupted GROMACS checkpoint"
    )
    checkpoint_receipt.add_argument("--config", type=Path, required=True)
    checkpoint_receipt.add_argument("--output-dir", type=Path, required=True)
    checkpoint_receipt.add_argument("--gmx-binary", default="gmx")

    aggregate = subparsers.add_parser("aggregate", help="combine completed replica manifests")
    aggregate.add_argument("--manifests", type=Path, nargs="+", required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    analyze = subparsers.add_parser("analyze", help="run checksum-bound engine-neutral MD analysis")
    analyze.add_argument("--manifest", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--stride", type=int, default=1)
    analyze.add_argument("--max-points", type=int, default=2000)
    analyze.add_argument(
        "--runtime-sha256",
        help="qualified MDAnalysis SIF SHA-256; defaults to BMS_MD_ANALYSIS_SIF_SHA256",
    )
    analyze.add_argument(
        "--report-failure-as-output",
        action="store_true",
        help="emit a typed failed report without failing the workflow task",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    require_experimental_md_feature()
    if args.command == "validate":
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if args.base_dir is not None:
            base_dir = args.base_dir.expanduser().resolve()
            input_config = dict(config.get("input") or {})
            for field in ("structure", "coordinates", "topology"):
                value = input_config.get(field)
                if not value:
                    continue
                candidate = Path(str(value)).expanduser()
                if not candidate.is_absolute():
                    input_config[field] = str((base_dir / candidate).resolve())
            config["input"] = input_config
        if args.gpu_id is not None or args.gpu_offload is not None:
            execution = dict(config.get("execution") or {})
            if args.gpu_id is not None:
                execution["gpu_id"] = str(args.gpu_id)
                if args.scheduler_gpu_id is not None:
                    execution["scheduler_gpu_id"] = str(args.scheduler_gpu_id)
            if args.gpu_offload is not None:
                execution["gpu_offload"] = args.gpu_offload
            config["execution"] = execution
        normalized = json.dumps(normalize_job_config(config), indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(normalized, encoding="utf-8")
        else:
            print(normalized, end="")
        return
    if args.command == "run":
        manifest = run_md_replica(
            args.config,
            args.output_dir,
            replica_index=args.replica_index,
            preparation_bundle=args.preparation_bundle,
            resume_checkpoint=args.resume_checkpoint,
        )
        print(manifest)
        return
    if args.command == "checkpoint-receipt":
        print(write_checkpoint_receipt(
            config_path=args.config,
            output_dir=args.output_dir,
            gmx_binary=args.gmx_binary,
        ))
        return
    if args.command == "aggregate":
        print(write_aggregate(args.manifests, args.output))
        return
    if args.command == "analyze":
        output, success = write_analysis_report(
            args.manifest,
            args.output,
            stride=args.stride,
            max_points=args.max_points,
            runtime_sha256=args.runtime_sha256,
        )
        print(output)
        if not success and not args.report_failure_as_output:
            raise SystemExit(2)
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
