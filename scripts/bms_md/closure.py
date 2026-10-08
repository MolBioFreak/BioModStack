"""Attempt-local MD joins. Nextflow runs science; these functions only validate/publish.

The normalized native request is the exact-set authority, not the set of tasks
which happened to finish. No HTTP client, database or host scheduler is needed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import jsonschema

from .aggregate_children import publish_json_immutable, publish_tree_immutable
from .aggregate import aggregate_manifests
from .collect_analysis import collect_analysis, _sha256, _contained_regular_file
from .runner import replica_seed
from .native_contract import replica_protocol_matches
from .chemistry.prepare import verify_preparation_bundle
from .analysis import analysis_identity_sha256, _implementation_sha256

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def preparation_policy(config: dict, bundle: Path | None) -> str | None:
    if config.get("schema") != "bms.md.job.v2":
        return None
    if bundle is None:
        raise ValueError("MD v2 requires its verified preparation bundle")
    chemistry = config["chemistry"]
    manifest = verify_preparation_bundle(bundle, expected_profile_id=chemistry["profile_id"],
                                         expected_profile_sha256=chemistry["profile_sha256"])
    if (manifest["source"]["sha256"] != config["input"]["structure_sha256"]
            or manifest["source"]["bytes"] != config["input"]["structure_bytes"]
            or manifest["runtime"]["image_sha256"] != chemistry["runtime_identity"]["sif_sha256"]):
        raise ValueError("MD preparation source/runtime identity mismatch")
    return manifest["preparation"]["gromacs_gpu_offload"] if config["engine"] == "gromacs" else None


def validate_replicas(config: dict, directories: list[Path], bundle: Path | None = None) -> list[Path]:
    policy = preparation_policy(config, bundle)
    expected = set(range(config["replicas"]))
    seen = set()
    manifests = []
    for directory in directories:
        manifest = directory / "manifest.json"
        record = load(manifest)
        jsonschema.validate(record, load(SCHEMAS / "md_run_v1.schema.json"))
        index = record["replica_index"]
        if index not in expected or index in seen:
            raise ValueError("MD replica join has duplicate or unexpected identity")
        if (record["status"] != "completed" or record["job_id"] != config["job_id"]
                or record["replica_seed"] != replica_seed(config["random_seed"], index)
                or record["engine"]["name"] != config["engine"]
                or record["engine"]["runtime"] != config["engine_runtime"]
                or not replica_protocol_matches(config, record["config"], qualified_gpu_offload=policy)):
            raise ValueError("MD replica request/seed/status identity mismatch")
        roles = {item.get("semantic_role") for item in record["artifacts"].values()}
        if not {"analysis_topology", "analysis_trajectory", "atom_order_manifest", "representative_structure"} <= roles:
            raise ValueError("MD replica required native artifact roles are missing")
        for stage, settings in config.get("stages", {}).items():
            if settings.get("enabled") and record["stages"].get(stage, {}).get("status") != "completed":
                raise ValueError("MD replica required native stage is incomplete")
        for artifact in record["artifacts"].values():
            source = _contained_regular_file(directory, artifact["path"])
            if source.stat().st_size != artifact["bytes"] or _sha256(source) != artifact["sha256"]:
                raise ValueError("MD replica artifact checksum mismatch")
        seen.add(index)
        manifests.append(manifest)
    if seen != expected:
        raise ValueError("MD exact-set join is missing required replicas")
    return sorted(manifests, key=lambda p: load(p)["replica_index"])


def aggregate(config_path: Path, directories: list[Path], destination: Path, bundle: Path | None = None) -> dict:
    config = load(config_path)
    manifests = validate_replicas(config, directories, bundle)
    if bundle is not None:
        publish_tree_immutable(bundle, destination / "preparation/preparation_bundle")
    for manifest in manifests:
        index = load(manifest)["replica_index"]
        publish_tree_immutable(manifest.parent, destination / "replicas" / f"replica_{index}")
    result = aggregate_manifests(manifests)
    result["lineage"] = {
        "total_children": config["replicas"], "completed_children": config["replicas"],
        "failed_children": 0, "cancelled_children": 0,
        "child_ids": [f'{config["job_id"]}:md_replica:{i}' for i in range(config["replicas"])],
    }
    publish_json_immutable(result, destination / "manifest.json")
    # Preserve the complete requested native settings independently of placement.
    publish_json_immutable(config, destination / "normalized_config.json")
    return result


def complete(root: Path, analysis_dirs: list[Path], *, cancelled: bool = False) -> dict:
    if cancelled:
        raise ValueError("cancelled MD attempt cannot seal successful results")
    config = load(root / "normalized_config.json")
    validate_replicas(config, list((root / "replicas").iterdir()), root / "preparation/preparation_bundle")
    expected = set(range(config["replicas"]))
    seen = set()
    for directory in analysis_dirs:
        sidecars = list(directory.glob("md_analysis_replica_*.artifacts.json"))
        if len(sidecars) != 1:
            raise ValueError("MD analysis requires exactly one native sidecar per replica")
        sidecar = load(sidecars[0])
        index = sidecar.get("replica")
        if index not in expected or index in seen or sidecar.get("status") != "completed":
            raise ValueError("MD analysis failed or has duplicate/unexpected identity")
        roles = {item.get("semantic_role") for item in sidecar.get("artifacts", {}).values()}
        if not {"md_analysis_report", "md_analysis_timeseries", "md_analysis_residue_metrics"} <= roles:
            raise ValueError("MD native analysis artifacts are incomplete")
        report_record = next(item for item in sidecar["artifacts"].values()
                             if item.get("semantic_role") == "md_analysis_report")
        report = load(_contained_regular_file(directory, report_record["path"]))
        jsonschema.validate(report, load(SCHEMAS / "md_analysis_v1.schema.json"))
        identity = analysis_identity_sha256(report)
        if (report.get("analysis_identity_sha256") != identity
                or sidecar.get("analysis_identity_sha256") != identity
                or report.get("tool", {}).get("implementation_sha256") != _implementation_sha256()
                or report.get("tool", {}).get("runtime_sif_sha256") != "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"):
            raise ValueError("MD native analysis implementation/runtime identity mismatch")
        if (report.get("status") != "completed" or report.get("replica") != index
                or report.get("job_id") != config["job_id"]
                or report.get("inputs", {}).get("manifest_sha256") != _sha256(root / f"replicas/replica_{index}/manifest.json")):
            raise ValueError("MD native analysis failed")
        seen.add(index)
    if seen != expected:
        raise ValueError("MD exact-set analysis join is missing required replicas")
    status = {"child_output_dirs": [str(p.resolve()) for p in analysis_dirs],
              "child_ids": [f'{config["job_id"]}:md_analysis:{i}' for i in sorted(expected)],
              "failed": 0, "cancelled": 0}
    # Transient task-local file: never scientific result authority.
    status_path = root / ".analysis-inputs.json"
    status_path.write_text(json.dumps(status))
    try:
        collection = collect_analysis(status_path, root / "manifest.json", root)
    finally:
        status_path.unlink(missing_ok=True)
    if collection["status"] != "completed":
        raise ValueError("MD native analysis collection failed")
    barrier = {"schema": "bms.md.completion-barrier.v1", "status": "completed",
               "job_id": config["job_id"],
               "aggregate_manifest_sha256": _sha256(root / "manifest.json"),
               "analysis_manifest_sha256": _sha256(root / "analysis/manifest.json")}
    publish_json_immutable(barrier, root / "md_completion_barrier.json")
    return barrier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("aggregate", "complete"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    if args.operation == "aggregate":
        if args.config is None:
            parser.error("aggregate requires --config")
        aggregate(args.config, args.inputs, args.root, args.bundle)
    else:
        complete(args.root, args.inputs)


if __name__ == "__main__":
    main()
