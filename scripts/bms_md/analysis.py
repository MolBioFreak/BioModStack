from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import statistics
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .analyzers import prepare_specialized_analyzers

ANALYSIS_SCHEMA = "bms.md.analysis.v1"
METHOD = "md_backbone_rmsd_v1"
SELECTION = "protein and backbone"
MAX_POINTS_LIMIT = 10_000


class MDAnalysisContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _implementation_sha256() -> str:
    return _sha256(Path(__file__).resolve())


def _safe_artifact_path(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise MDAnalysisContractError("MD_ANALYSIS_PATH_ESCAPE", "artifact path is not a contained relative path")
    relative = PurePosixPath(raw)
    if relative.as_posix() != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise MDAnalysisContractError("MD_ANALYSIS_PATH_ESCAPE", "artifact path is not a contained relative path")
    root = root.resolve()
    candidate = root.joinpath(*relative.parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_PATH_ESCAPE", "artifact escapes its manifest directory") from exc
    return candidate


def _verify_artifact(root: Path, record: Mapping[str, Any]) -> tuple[Path, str]:
    digest = record.get("sha256")
    size = record.get("bytes")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MDAnalysisContractError("MD_ANALYSIS_INVALID_ARTIFACT", "artifact SHA-256 is missing or invalid")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise MDAnalysisContractError("MD_ANALYSIS_INVALID_ARTIFACT", "artifact byte size is missing or invalid")
    path = _safe_artifact_path(root, record.get("path"))
    try:
        opened = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_ARTIFACT_MISSING", "declared artifact is unavailable") from exc
    actual_digest = hashlib.sha256()
    consumed = 0
    try:
        stat_result = os.fstat(opened)
        if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size != size:
            raise MDAnalysisContractError("MD_ANALYSIS_SIZE_MISMATCH", "artifact size does not match its manifest")
        while True:
            chunk = os.read(opened, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            actual_digest.update(chunk)
    finally:
        os.close(opened)
    if consumed != size:
        raise MDAnalysisContractError("MD_ANALYSIS_SIZE_MISMATCH", "artifact size changed while hashing")
    if actual_digest.hexdigest() != digest:
        raise MDAnalysisContractError("MD_ANALYSIS_HASH_MISMATCH", "artifact SHA-256 does not match its manifest")
    return path, digest


def _role_record(manifest: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise MDAnalysisContractError("MD_ANALYSIS_MISSING_ROLE", f"missing required artifact role: {role}")
    matches = [value for value in artifacts.values() if isinstance(value, Mapping) and value.get("semantic_role") == role]
    if len(matches) != 1:
        raise MDAnalysisContractError("MD_ANALYSIS_MISSING_ROLE", f"expected exactly one artifact role: {role}")
    return matches[0]


def _base_report(manifest_path: Path, *, version: str = "unavailable") -> dict[str, Any]:
    manifest_sha256 = _sha256(manifest_path) if manifest_path.is_file() else None
    return {
        "schema": ANALYSIS_SCHEMA,
        "status": "failed",
        "method": METHOD,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"manifest_sha256": manifest_sha256},
        "tool": {"name": "MDAnalysis", "version": version, "implementation_sha256": _implementation_sha256()},
    }


def analyze_manifest(manifest_path: Path, *, stride: int = 1, max_points: int = 2000) -> dict[str, Any]:
    manifest_path = Path(manifest_path).resolve()
    if stride < 1 or not 1 <= max_points <= MAX_POINTS_LIMIT:
        raise MDAnalysisContractError("MD_ANALYSIS_INVALID_POLICY", "stride and max_points are outside supported bounds")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_MANIFEST_INVALID", "replica manifest is unavailable or invalid") from exc
    if manifest.get("schema") != "bms.md.run.v1" or manifest.get("status") != "completed":
        raise MDAnalysisContractError("MD_ANALYSIS_UNSUPPORTED_PAIR", "analysis requires a completed bms.md.run.v1 manifest")
    topology_record = _role_record(manifest, "analysis_topology")
    trajectory_record = _role_record(manifest, "analysis_trajectory")
    identity = topology_record.get("atom_order_identity")
    if not isinstance(identity, str) or not identity or trajectory_record.get("atom_order_identity") != identity:
        raise MDAnalysisContractError("MD_ANALYSIS_ATOM_ORDER_MISMATCH", "topology and trajectory atom-order identities do not match")
    topology, topology_hash = _verify_artifact(manifest_path.parent, topology_record)
    trajectory, trajectory_hash = _verify_artifact(manifest_path.parent, trajectory_record)
    if (topology.suffix.lower(), trajectory.suffix.lower()) not in {(".gro", ".xtc"), (".pdb", ".dcd")}:
        raise MDAnalysisContractError(
            "MD_ANALYSIS_UNSUPPORTED_PAIR",
            "md_backbone_rmsd_v1 supports only checksum-bound GRO+XTC and PDB+DCD pairs",
        )

    try:
        import MDAnalysis as mda
        import numpy as np
        from MDAnalysis.analysis import align, rms
        from MDAnalysis.transformations import center_in_box
        from MDAnalysis.transformations.nojump import NoJump
    except ImportError as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_RUNTIME_UNAVAILABLE", "pinned MDAnalysis runtime is unavailable") from exc

    try:
        universe = mda.Universe(str(topology), str(trajectory))
        backbone = universe.select_atoms(SELECTION)
        if backbone.n_atoms == 0:
            raise MDAnalysisContractError("MD_ANALYSIS_EMPTY_SELECTION", f"selection is empty: {SELECTION}")
        universe.trajectory.add_transformations(NoJump(check_continuity=True), center_in_box(backbone, wrap=True))
        frame_count = len(universe.trajectory)
        effective_stride = max(stride, math.ceil(frame_count / max_points))
        admitted = list(range(0, frame_count, effective_stride))
        if admitted and admitted[-1] != frame_count - 1:
            if len(admitted) == max_points:
                admitted[-1] = frame_count - 1
            else:
                admitted.append(frame_count - 1)
        if not admitted:
            raise MDAnalysisContractError("MD_ANALYSIS_CORRUPT_TRAJECTORY", "trajectory contains no admitted frames")
        admitted_set = set(admitted)
        reference = None
        values: list[dict[str, Any]] = []
        coordinate_sum = np.zeros((backbone.n_atoms, 3), dtype=np.float64)
        coordinate_square_sum = np.zeros((backbone.n_atoms, 3), dtype=np.float64)
        specialized, specialized_states = prepare_specialized_analyzers(universe, manifest)
        for ts in universe.trajectory:
            source_frame = int(ts.frame)
            if source_frame not in admitted_set:
                continue
            if reference is None:
                reference = mda.Merge(backbone).load_new(backbone.positions.copy()[None, :, :])
            align.alignto(universe, reference, select=SELECTION, weights="mass")
            value = float(rms.rmsd(backbone.positions, reference.atoms.positions, weights=backbone.masses, center=False, superposition=False))
            if not math.isfinite(value):
                raise MDAnalysisContractError("MD_ANALYSIS_CORRUPT_TRAJECTORY", "RMSD produced a non-finite value")
            radius_of_gyration = float(backbone.radius_of_gyration())
            if not math.isfinite(radius_of_gyration):
                raise MDAnalysisContractError("MD_ANALYSIS_CORRUPT_TRAJECTORY", "radius of gyration produced a non-finite value")
            coordinates = backbone.positions.astype(np.float64, copy=True)
            coordinate_sum += coordinates
            coordinate_square_sum += coordinates * coordinates
            for analyzer in specialized:
                analyzer.sample(source_frame, float(ts.time))
            values.append({
                "replica": int(manifest["replica_index"]),
                "time_ps": float(ts.time),
                "source_frame": source_frame,
                "rmsd_angstrom": value,
                "radius_of_gyration_angstrom": radius_of_gyration,
            })
    except MDAnalysisContractError:
        raise
    except Exception as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_CORRUPT_TRAJECTORY", "MDAnalysis rejected the topology/trajectory pair") from exc

    post_topology, post_topology_hash = _verify_artifact(manifest_path.parent, topology_record)
    post_trajectory, post_trajectory_hash = _verify_artifact(manifest_path.parent, trajectory_record)
    if post_topology != topology or post_trajectory != trajectory or post_topology_hash != topology_hash or post_trajectory_hash != trajectory_hash:
        raise MDAnalysisContractError("MD_ANALYSIS_INPUT_DRIFT", "analysis inputs changed while the trajectory was being read")

    rmsd_values = [point["rmsd_angstrom"] for point in values]
    mean_coordinates = coordinate_sum / len(values)
    coordinate_variance = np.maximum(coordinate_square_sum / len(values) - mean_coordinates * mean_coordinates, 0.0)
    atom_rmsf = np.sqrt(coordinate_variance.sum(axis=1))
    local_atom_index = {int(atom_index): local_index for local_index, atom_index in enumerate(backbone.indices)}
    residue_metrics: list[dict[str, Any]] = []
    for residue in backbone.residues:
        local_indices = [local_atom_index[int(atom_index)] for atom_index in residue.atoms.indices if int(atom_index) in local_atom_index]
        if not local_indices:
            continue
        residue_metrics.append({
            "segid": str(residue.segid or ""),
            "resid": int(residue.resid),
            "resname": str(residue.resname),
            "backbone_rmsf_angstrom": float(np.sqrt(np.mean(np.square(atom_rmsf[local_indices])))),
            "backbone_atom_count": len(local_indices),
        })
    block_count = min(5, len(values))
    block_statistics: list[dict[str, Any]] = []
    for block_index in range(block_count):
        start = block_index * len(values) // block_count
        stop = (block_index + 1) * len(values) // block_count
        block = values[start:stop]
        if block:
            block_statistics.append({
                "block": block_index,
                "count": len(block),
                "source_frame_start": block[0]["source_frame"],
                "source_frame_stop": block[-1]["source_frame"],
                "time_ps_start": block[0]["time_ps"],
                "time_ps_stop": block[-1]["time_ps"],
                "mean_rmsd_angstrom": statistics.fmean(point["rmsd_angstrom"] for point in block),
                "mean_radius_of_gyration_angstrom": statistics.fmean(point["radius_of_gyration_angstrom"] for point in block),
            })
    report = _base_report(manifest_path, version=str(mda.__version__))
    report.update({
        "status": "completed",
        "job_id": str(manifest["job_id"]),
        "replica": int(manifest["replica_index"]),
        "selection": SELECTION,
        "reference": "first_admitted_frame",
        "policy": {
            "pbc": "nojump_then_center_protein",
            "alignment": "mass_weighted_backbone_fit",
            "exclusions": "non_protein_and_non_backbone",
            "stride": effective_stride,
            "max_points": max_points,
        },
        "units": {"time": "ps", "rmsd": "angstrom"},
        "points": values,
        "summary": {
            "count": len(values), "min": min(rmsd_values), "mean": statistics.fmean(rmsd_values),
            "max": max(rmsd_values), "final": rmsd_values[-1],
        },
        "residue_metrics": residue_metrics,
        "block_statistics": block_statistics,
        "evidence": {
            "status": "insufficient_evidence",
            "reason": "a per-replica structural trace cannot establish ensemble convergence or equilibrium",
            "statistical_unit": "replica",
            "frames_are_independent_replicates": False,
        },
        "observables": {
            "backbone_rmsd": "completed",
            "backbone_rmsf": "completed",
            "radius_of_gyration": "completed",
            "sasa": "unavailable_validated_backend",
        },
        "specialized_analyzers": specialized_states + [analyzer.result() for analyzer in specialized],
    })
    report["inputs"].update({"topology_sha256": topology_hash, "trajectory_sha256": trajectory_hash, "atom_order_identity": identity})
    return report


def write_analysis_report(manifest_path: Path, output_path: Path, *, stride: int = 1, max_points: int = 2000) -> tuple[Path, bool]:
    manifest_path = Path(manifest_path).resolve()
    try:
        report = analyze_manifest(manifest_path, stride=stride, max_points=max_points)
        success = True
    except MDAnalysisContractError as exc:
        report = _base_report(manifest_path)
        report["failure"] = {"code": exc.code, "message": str(exc)}
        success = False
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_tables: list[Path] = []
    if success:
        try:
            import pyarrow as pa  # pyright: ignore[reportMissingImports]
            import pyarrow.parquet as parquet  # pyright: ignore[reportMissingImports]

            tables = {
                "timeseries": pa.Table.from_pylist(report["points"]),
                "residue_metrics": pa.Table.from_pylist(report["residue_metrics"]),
            }
            derived: dict[str, dict[str, Any]] = {}
            for name, table in tables.items():
                target = output_path.parent / f"{output_path.stem}.{name}.parquet"
                temporary_table = target.with_suffix(target.suffix + ".tmp")
                parquet.write_table(table, temporary_table, compression="zstd")
                os.replace(temporary_table, target)
                created_tables.append(target)
                derived[name] = {"path": target.name, "bytes": target.stat().st_size, "sha256": _sha256(target)}
            report["derived_artifacts"] = derived
        except Exception:
            for target in created_tables:
                target.unlink(missing_ok=True)
            report = _base_report(manifest_path)
            report["failure"] = {
                "code": "MD_ANALYSIS_PARQUET_UNAVAILABLE",
                "message": "authoritative Parquet artifacts could not be emitted by the pinned runtime",
            }
            success = False
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output_path)
    artifact_manifest_path = output_path.parent / f"{output_path.stem}.artifacts.json"
    artifact_records: dict[str, dict[str, Any]] = {
        "analysis_report": {
            "path": output_path.name,
            "bytes": output_path.stat().st_size,
            "sha256": _sha256(output_path),
            "semantic_role": "md_analysis_report",
        }
    }
    if success:
        for name, record in report["derived_artifacts"].items():
            artifact_records[name] = {
                **record,
                "semantic_role": f"md_analysis_{name}",
            }
    artifact_manifest = {
        "schema": "bms.md.analysis-artifacts.v1",
        "status": report["status"],
        "job_id": report.get("job_id"),
        "replica": report.get("replica"),
        "input_manifest_sha256": report["inputs"]["manifest_sha256"],
        "artifacts": artifact_records,
    }
    temporary_manifest = artifact_manifest_path.with_suffix(artifact_manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(json.dumps(artifact_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, artifact_manifest_path)
    return output_path, success
