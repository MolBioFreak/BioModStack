from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .aggregate_children import publish_file_immutable, publish_json_immutable, validate_collection_receipt


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_regular_file(root: Path, raw: str) -> Path:
    candidate = root / raw
    if candidate.is_symlink():
        raise ValueError("analysis artifact must not be a symbolic link")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("analysis artifact escapes its child output root") from exc
    if not resolved.is_file():
        raise ValueError("analysis artifact is not a regular file")
    return resolved


def _find_sidecar(child_dir: Path) -> Path:
    matches = sorted(child_dir.rglob("md_analysis_replica_*.artifacts.json"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one analysis artifact sidecar below {child_dir}, found {len(matches)}")
    return matches[0]


def _replica_manifest_hashes(parent_root: Path, aggregate: dict[str, Any]) -> dict[int, str]:
    hashes: dict[int, str] = {}
    for replica in aggregate["replicas"]:
        if not isinstance(replica, dict):
            raise ValueError("invalid aggregate replica entry")
        index = replica.get("replica_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index in hashes:
            raise ValueError("invalid or duplicate aggregate replica index")
        manifest = parent_root / "replicas" / f"replica_{index}" / "manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError("replica manifest is unavailable")
        hashes[index] = _sha256(manifest)
    return hashes


def run_native_wham(manifests, request, output_dir, *, gmx="gmx", gromacs_container=None):
    """Run GROMACS' 1D estimator on explicit window/coordinate selections."""
    import subprocess
    import tempfile
    from .analysis import _role_record, _verify_artifact
    from .analyzers import read_xvg

    output_dir.mkdir(parents=True, exist_ok=True)
    result = {"status": "failed", "dimension": 1, "method": "gmx_wham",
              "scope": "selected_coordinate_across_windows", "request": request,
              "coordinate_unit": request["unit"], "energy_unit": "kJ/mol", "inputs": []}
    try:
        with tempfile.TemporaryDirectory(prefix="wham-", dir=output_dir) as temporary:
            work = Path(temporary)
            tprs, pullx, masks = [], [], []
            for window in request["windows"]:
                manifest_path = manifests[window["replica"]]
                manifest = json.loads(manifest_path.read_text())
                tpr_record = _role_record(manifest, "production_tpr")
                pull_record = _role_record(manifest, "pull_coordinates")
                tpr, tpr_sha = _verify_artifact(manifest_path.parent, tpr_record, snapshot_root=work)
                coordinates, pull_sha = _verify_artifact(manifest_path.parent, pull_record, snapshot_root=work)
                count, selected = window["coordinate_count"], window["coordinate"]
                if not 1 <= selected <= count:
                    raise ValueError("WHAM coordinate is outside the declared native coordinate count")
                tprs.append(str(tpr))
                pullx.append(str(coordinates))
                masks.append(" ".join("1" if i == selected else "0" for i in range(1, count + 1)))
                result["inputs"].append({**window, "manifest_sha256": _sha256(manifest_path),
                                         "tpr_sha256": tpr_sha, "pull_sha256": pull_sha,
                                         "engine": manifest.get("engine")})
            for name, rows in (("tpr.dat", tprs), ("pullx.dat", pullx), ("selection.dat", masks)):
                (work / name).write_text("\n".join(rows) + "\n")
            command = [gmx]
            if gromacs_container:
                from lib.container_runtime import container_executable
                command = [container_executable() or "apptainer", "exec", "--bind",
                           f"{work.resolve()}:{work.resolve()}", str(gromacs_container), gmx]
            command += ["wham", "-it", str(work / "tpr.dat"), "-ix", str(work / "pullx.dat"),
                       "-is", str(work / "selection.dat"), "-o", str(work / "pmf.xvg"),
                       "-hist", str(work / "histogram.xvg"), "-xvg", "none", "-unit", "kJ", "-temp", str(request["temperature_k"]),
                       "-bins", str(request.get("bins", 200)), "-b", str(request.get("begin_ps", 0))]
            if request.get("end_ps") is not None:
                command += ["-e", str(request["end_ps"])]
            process = subprocess.run(command, cwd=work, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (output_dir / "wham.log").write_text(process.stdout)
            result["returncode"] = process.returncode
            for name in ("pmf.xvg", "histogram.xvg"):
                if (work / name).is_file():
                    publish_file_immutable(work / name, output_dir / name)
            if process.returncode:
                raise RuntimeError(f"gmx wham exited {process.returncode}: {process.stdout[-4000:]}")
            pmf = read_xvg(work / "pmf.xvg")
            histograms = read_xvg(work / "histogram.xvg")
            result.update(status="completed", points=[{"coordinate": row[0], "pmf_kj_mol": row[1]} for row in pmf],
                          histograms={"columns": request["windows"], "rows": histograms})
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = {"code": "MD_WHAM_FAILED", "message": str(exc)}
    return result


def collect_analysis(
    child_status_path: Path, aggregate_manifest: Path, output_dir: Path,
    *, spawn_receipt: Path | None = None, gmx: str = "gmx", gromacs_container: str | None = None,
) -> dict[str, Any]:
    status = json.loads(child_status_path.read_text(encoding="utf-8"))
    receipt = (
        validate_collection_receipt(status, spawn_receipt, "bms.md.analysis-spawn.v1")
        if spawn_receipt is not None else None
    )
    aggregate_manifest = aggregate_manifest.expanduser().resolve()
    parent_root = aggregate_manifest.parent
    output_dir = output_dir.expanduser().resolve()
    continuation = output_dir != parent_root
    if continuation and (receipt is None or receipt.get('aggregate_manifest') != str(aggregate_manifest)
            or not output_dir.is_relative_to(Path(receipt.get('artifact_root', '')).resolve() / 'generations')):
        raise ValueError("analysis continuation requires its retained aggregate and contained generation")
    aggregate = json.loads(aggregate_manifest.read_text(encoding="utf-8"))
    if (
        aggregate.get("schema") != "bms.md.aggregate.v1"
        or aggregate.get("status") != "completed"
        or not isinstance(aggregate.get("job_id"), str)
        or not isinstance(aggregate.get("replicas"), list)
        or not aggregate["replicas"]
    ):
        raise ValueError("completed MD aggregate manifest is required")
    parent_job_id = aggregate["job_id"]
    replica_hashes = _replica_manifest_hashes(parent_root, aggregate)
    manifests = {index: parent_root / "replicas" / f"replica_{index}" / "manifest.json" for index in replica_hashes}
    config = json.loads(manifests[min(manifests)].read_text()).get("config", {})
    optional = "analysis" in config or config.get("schema") == "bms.md.job.v3"
    if receipt is not None:
        submitted_hashes = {child["replica_index"]: child.get("manifest_sha256") for child in receipt["children"]}
        if (
            receipt["parent_job_id"] != parent_job_id
            or receipt.get("aggregate_manifest_sha256") != _sha256(aggregate_manifest)
            or submitted_hashes != replica_hashes
            or receipt.get("analysis_count") != len(replica_hashes)
        ):
            raise ValueError("MD analysis receipt does not match the immutable replica aggregate")

    if continuation:
        from .aggregate_children import publish_tree_immutable
        # Keep dynamics byte-for-byte; never invoke preparation or a simulator.
        # The fresh generation has no prior partial/terminal analysis manifest.
        publish_tree_immutable(parent_root / 'replicas', output_dir / 'replicas')
        publish_file_immutable(aggregate_manifest, output_dir / 'manifest.json',
            expected_sha256=receipt['aggregate_manifest_sha256'])

    child_dirs = [Path(value).expanduser().resolve() for value in status.get("child_output_dirs") or []]
    completed_records: list[dict[str, Any]] = []
    validated_children = []
    seen_replicas: set[int] = set()
    analysis_root = output_dir / "analysis"
    for child_dir in child_dirs:
        sidecar_path = _find_sidecar(child_dir)
        sidecar_bytes = sidecar_path.read_bytes()
        sidecar_sha256 = hashlib.sha256(sidecar_bytes).hexdigest()
        sidecar = json.loads(sidecar_bytes)
        replica_index = sidecar.get("replica")
        if (
            sidecar.get("schema") != "bms.md.analysis-artifacts.v1"
            or sidecar.get("status") not in {"completed", "not_applicable", "failed"}
            or sidecar.get("job_id") != parent_job_id
            or isinstance(replica_index, bool)
            or not isinstance(replica_index, int)
            or replica_index not in replica_hashes
            or replica_index in seen_replicas
            or sidecar.get("input_manifest_sha256") != replica_hashes[replica_index]
            or not isinstance(sidecar.get("artifacts"), dict)
            or not sidecar["artifacts"]
        ):
            raise ValueError("analysis artifact sidecar identity is invalid")
        seen_replicas.add(replica_index)

        artifact_records: list[dict[str, Any]] = []
        for name, record in sorted(sidecar["artifacts"].items()):
            if not isinstance(name, str) or not isinstance(record, dict):
                raise ValueError("analysis artifact record is invalid")
            relative = record.get("path")
            if not isinstance(relative, str) or Path(relative).name != relative:
                raise ValueError("analysis artifact path must be one contained basename")
            source = _contained_regular_file(sidecar_path.parent, relative)
            expected_size = record.get("bytes")
            expected_sha256 = record.get("sha256")
            if (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size < 0
                or not isinstance(expected_sha256, str)
                or not SHA256.fullmatch(expected_sha256)
                or source.stat().st_size != expected_size
                or _sha256(source) != expected_sha256
            ):
                raise ValueError("analysis artifact checksum is invalid")
            destination = analysis_root / relative
            publish_file_immutable(
                source,
                destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            artifact_records.append(
                {
                    "name": name,
                    "path": relative,
                    "bytes": expected_size,
                    "sha256": expected_sha256,
                    "semantic_role": record.get("semantic_role"),
                }
            )

        published_sidecar = analysis_root / sidecar_path.name
        publish_file_immutable(
            sidecar_path,
            published_sidecar,
            expected_size=len(sidecar_bytes),
            expected_sha256=sidecar_sha256,
        )
        completed_records.append(
            {
                "replica_index": replica_index,
                "status": sidecar["status"],
                "input_manifest_sha256": replica_hashes[replica_index],
                "artifact_sidecar": sidecar_path.name,
                "artifact_sidecar_sha256": sidecar_sha256,
                "artifacts": artifact_records,
            }
        )
        validated_children.append((child_dir, replica_index, sidecar,
            [sidecar_path] + [sidecar_path.parent / record["path"] for record in artifact_records]))

    completed_records.sort(key=lambda item: item["replica_index"])
    failed = max(int(status.get("failed") or 0), sum(item["status"] == "failed" for item in completed_records))
    cancelled = int(status.get("cancelled") or 0)
    required = len(replica_hashes)
    is_complete = not failed and not cancelled and seen_replicas == set(replica_hashes)
    collection = {
        "schema": "bms.md.analysis-collection.v1",
        "status": "completed" if is_complete else "partial_failure",
        "job_id": parent_job_id,
        "aggregate_manifest_sha256": _sha256(aggregate_manifest),
        "replica_manifest_set_sha256": hashlib.sha256(
            json.dumps(sorted(replica_hashes.items()), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "required_analysis_children": required,
        "optional": optional,
        "completed_analysis_children": sum(item["status"] != "failed" for item in completed_records),
        "failed_analysis_children": failed,
        "cancelled_analysis_children": cancelled,
        "child_ids": list(status.get("child_ids") or []),
        "analyses": completed_records,
    }
    wham = (config.get("analysis") or {}).get("wham")
    if wham:
        result = run_native_wham(manifests, wham, analysis_root / "wham", gmx=gmx, gromacs_container=gromacs_container)
        result_path = analysis_root / "wham" / "result.json"
        publish_json_immutable(result, result_path)
        collection["wham"] = {"path": "wham/result.json", "bytes": result_path.stat().st_size, "sha256": _sha256(result_path)}
    if is_complete or optional:
        publish_json_immutable(collection, analysis_root / "manifest.json")
    else:
        partial_identity = hashlib.sha256(
            json.dumps(collection, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        publish_json_immutable(collection, analysis_root / "collections" / f"partial_{partial_identity}.json")
    from scripts.child_job_utils import component_runtime_enabled, seal_validated_child_files
    if component_runtime_enabled():
        if receipt is None:
            raise ValueError("runtime MD analysis collection requires its exact spawn receipt")
        expected = {child["replica_index"]: child["id"] for child in receipt["children"]}
        for child_dir, index, sidecar, files in validated_children:
            seal_validated_child_files(expected[index], output_dir=child_dir, result=sidecar,
                                      files=files, role="md-native-analysis")
        if is_complete:
            from scripts.lib.component_adapter import join_children
            join_children([child["id"] for child in receipt["children"]])
    return collection


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect durable CPU MD analysis child outputs")
    parser.add_argument("--child-status", type=Path, required=True)
    parser.add_argument("--aggregate-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--spawn-receipt", type=Path)
    parser.add_argument("--gmx", default="gmx")
    parser.add_argument("--gromacs-container")
    args = parser.parse_args()

    collect_analysis(args.child_status, args.aggregate_manifest, args.output_dir, spawn_receipt=args.spawn_receipt, gmx=args.gmx, gromacs_container=args.gromacs_container)
    print(args.output_dir / "analysis" / "manifest.json")


if __name__ == "__main__":
    main()
