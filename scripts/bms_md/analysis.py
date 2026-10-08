from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .analyzers import prepare_specialized_analyzers
from .contract import atom_order_identity, build_atom_order_manifest

ANALYSIS_SCHEMA = "bms.md.analysis.v1"
METHOD = "md_backbone_rmsd_v1"
SELECTION = "protein and backbone"
MAX_POINTS_LIMIT = 10_000
RUNTIME_SHA256_ENV = "BMS_MD_ANALYSIS_SIF_SHA256"


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


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_finite_json(value: Any, *, location: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise MDAnalysisContractError(
            "MD_ANALYSIS_NON_FINITE",
            f"analysis evidence contains a non-finite number at {location}",
        )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _assert_finite_json(nested, location=f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_finite_json(nested, location=f"{location}[{index}]")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    _assert_finite_json(payload)
    try:
        return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_JSON_INVALID", "analysis evidence is not strict JSON") from exc


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        temporary.write_bytes(_json_bytes(payload))
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_staged_generation(
    staged: list[tuple[Path, Path]],
    *,
    commit_marker: Path,
) -> None:
    if not staged or staged[-1][1] != commit_marker:
        raise ValueError("commit marker must be the final staged target")
    transaction = f"{os.getpid()}-{os.urandom(8).hex()}"
    backups: dict[Path, Path | None] = {}
    parent = commit_marker.parent
    if any(target.parent != parent for _, target in staged):
        raise ValueError("all generation artifacts must share one directory")
    try:
        for temporary, target in staged:
            if temporary.parent != parent:
                raise ValueError("staged artifact must share the target directory")
            if not temporary.is_file():
                raise FileNotFoundError(temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            backup: Path | None = None
            if target.exists():
                backup = target.with_name(f"{target.name}.backup-{transaction}")
                os.link(target, backup)
            backups[target] = backup
        _fsync_directory(parent)
        for temporary, target in staged:
            os.replace(temporary, target)
        _fsync_directory(parent)
    except BaseException:
        for _, target in reversed(staged):
            backup = backups.get(target)
            if backup is None:
                target.unlink(missing_ok=True)
            elif backup.exists():
                os.rename(backup, target)
        _fsync_directory(parent)
        raise
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)


def _implementation_sha256() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    paths = [
        Path(__file__).resolve(),
        Path(__file__).resolve().with_name("analyzers.py"),
        Path(__file__).resolve().with_name("contract.py"),
        repo_root / "schemas" / "md_analysis_v1.schema.json",
    ]
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: value.relative_to(repo_root).as_posix()):
        relative = path.relative_to(repo_root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def resolve_runtime_sha256(explicit: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environ is None else environ
    value = explicit or environment.get(RUNTIME_SHA256_ENV)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MDAnalysisContractError(
            "MD_ANALYSIS_RUNTIME_IDENTITY_MISSING",
            "qualified MDAnalysis runtime SIF SHA-256 is missing or invalid",
        )
    return value


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_identity_sha256(report: Mapping[str, Any]) -> str:
    scientific_content = {
        key: value
        for key, value in report.items()
        if key not in {"created_at", "analysis_identity_sha256"}
    }
    return _canonical_json_sha256(scientific_content)


def _open_contained_artifact(root: Path, raw: Any) -> tuple[int, Path]:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise MDAnalysisContractError("MD_ANALYSIS_PATH_ESCAPE", "artifact path is not a contained relative path")
    relative = PurePosixPath(raw)
    if relative.as_posix() != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise MDAnalysisContractError("MD_ANALYSIS_PATH_ESCAPE", "artifact path is not a contained relative path")
    resolved_root = root.resolve(strict=True)
    directory_descriptor = os.open(resolved_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        opened = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        return opened, resolved_root.joinpath(*relative.parts)
    except OSError as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_ARTIFACT_MISSING", "declared artifact is unavailable") from exc
    finally:
        os.close(directory_descriptor)


def _verify_artifact(
    root: Path,
    record: Mapping[str, Any],
    *,
    snapshot_root: Path | None = None,
) -> tuple[Path, str]:
    digest = record.get("sha256")
    size = record.get("bytes")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MDAnalysisContractError("MD_ANALYSIS_INVALID_ARTIFACT", "artifact SHA-256 is missing or invalid")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise MDAnalysisContractError("MD_ANALYSIS_INVALID_ARTIFACT", "artifact byte size is missing or invalid")
    try:
        opened, source_path = _open_contained_artifact(root, record.get("path"))
    except OSError as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_ARTIFACT_MISSING", "declared artifact is unavailable") from exc
    actual_digest = hashlib.sha256()
    consumed = 0
    snapshot_path: Path | None = None
    snapshot_handle: Any = None
    try:
        stat_result = os.fstat(opened)
        if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size != size:
            raise MDAnalysisContractError("MD_ANALYSIS_SIZE_MISMATCH", "artifact size does not match its manifest")
        if snapshot_root is not None:
            snapshot_root = Path(snapshot_root)
            snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            snapshot_path = snapshot_root / f"{os.urandom(16).hex()}{source_path.suffix.lower()}"
            snapshot_handle = snapshot_path.open("xb")
        while True:
            chunk = os.read(opened, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            actual_digest.update(chunk)
            if snapshot_handle is not None:
                snapshot_handle.write(chunk)
        if snapshot_handle is not None:
            snapshot_handle.flush()
            os.fsync(snapshot_handle.fileno())
    finally:
        if snapshot_handle is not None:
            snapshot_handle.close()
        os.close(opened)
    if consumed != size:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
        raise MDAnalysisContractError("MD_ANALYSIS_SIZE_MISMATCH", "artifact size changed while hashing")
    if actual_digest.hexdigest() != digest:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
        raise MDAnalysisContractError("MD_ANALYSIS_HASH_MISMATCH", "artifact SHA-256 does not match its manifest")
    return snapshot_path or source_path, digest


def _role_record(manifest: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise MDAnalysisContractError("MD_ANALYSIS_MISSING_ROLE", f"missing required artifact role: {role}")
    matches = [value for value in artifacts.values() if isinstance(value, Mapping) and value.get("semantic_role") == role]
    if len(matches) != 1:
        raise MDAnalysisContractError("MD_ANALYSIS_MISSING_ROLE", f"expected exactly one artifact role: {role}")
    return matches[0]


def _base_report(
    manifest_path: Path,
    *,
    version: str = "unavailable",
    runtime_sha256: str | None = None,
) -> dict[str, Any]:
    manifest_sha256 = _sha256(manifest_path) if manifest_path.is_file() else None
    tool: dict[str, Any] = {
        "name": "MDAnalysis",
        "version": version,
        "implementation_sha256": _implementation_sha256(),
    }
    if runtime_sha256 is not None:
        tool["runtime_sif_sha256"] = runtime_sha256
    return {
        "schema": ANALYSIS_SCHEMA,
        "status": "failed",
        "method": METHOD,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"manifest_sha256": manifest_sha256},
        "tool": tool,
    }


def _analyze_manifest_from_snapshots(
    manifest_path: Path,
    *,
    stride: int = 1,
    max_points: int = 2000,
    runtime_sha256: str | None = None,
    snapshot_root: Path,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path).resolve()
    resolved_runtime_sha256 = resolve_runtime_sha256(runtime_sha256)
    if stride < 1 or not 1 <= max_points <= MAX_POINTS_LIMIT:
        raise MDAnalysisContractError("MD_ANALYSIS_INVALID_POLICY", "stride and max_points are outside supported bounds")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_MANIFEST_INVALID", "replica manifest is unavailable or invalid") from exc
    _validate_run_manifest_schema(manifest)
    if manifest.get("status") != "completed":
        raise MDAnalysisContractError("MD_ANALYSIS_UNSUPPORTED_PAIR", "analysis requires a completed bms.md.run.v1 manifest")
    topology_record = _role_record(manifest, "analysis_topology")
    trajectory_record = _role_record(manifest, "analysis_trajectory")
    atom_order_record = _role_record(manifest, "atom_order_manifest")
    identity = topology_record.get("atom_order_identity")
    if not isinstance(identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity) or trajectory_record.get("atom_order_identity") != identity:
        raise MDAnalysisContractError("MD_ANALYSIS_ATOM_ORDER_MISMATCH", "topology and trajectory atom-order identities do not match")
    topology, topology_hash = _verify_artifact(
        manifest_path.parent,
        topology_record,
        snapshot_root=snapshot_root,
    )
    trajectory, trajectory_hash = _verify_artifact(
        manifest_path.parent,
        trajectory_record,
        snapshot_root=snapshot_root,
    )
    atom_order_path, atom_order_hash = _verify_artifact(
        manifest_path.parent,
        atom_order_record,
        snapshot_root=snapshot_root,
    )
    if identity != f"sha256:{atom_order_hash}":
        raise MDAnalysisContractError("MD_ANALYSIS_ATOM_ORDER_MISMATCH", "atom-order identity does not bind the declared atom-order manifest")
    try:
        atom_order_manifest = json.loads(atom_order_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_ATOM_ORDER_MISMATCH", "atom-order manifest is unavailable or invalid") from exc
    if (
        not isinstance(atom_order_manifest, Mapping)
        or atom_order_manifest.get("schema") != "bms.md.atom-order.v1"
        or not isinstance(atom_order_manifest.get("atom_count"), int)
        or atom_order_manifest.get("atom_count", 0) < 1
        or not isinstance(atom_order_manifest.get("atoms"), list)
        or len(atom_order_manifest["atoms"]) != atom_order_manifest["atom_count"]
    ):
        raise MDAnalysisContractError("MD_ANALYSIS_ATOM_ORDER_MISMATCH", "atom-order manifest contract is invalid")
    try:
        topology_atom_order = build_atom_order_manifest(topology)
    except ValueError as exc:
        raise MDAnalysisContractError(
            "MD_ANALYSIS_ATOM_ORDER_MISMATCH",
            "analysis topology cannot establish canonical atom order",
        ) from exc
    if topology_atom_order != atom_order_manifest or atom_order_identity(topology_atom_order) != identity:
        raise MDAnalysisContractError(
            "MD_ANALYSIS_ATOM_ORDER_MISMATCH",
            "atom-order manifest does not match the canonical topology atom order",
        )
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
        if universe.atoms.n_atoms != atom_order_manifest["atom_count"]:
            raise MDAnalysisContractError(
                "MD_ANALYSIS_ATOM_ORDER_MISMATCH",
                "topology atom count does not match the atom-order manifest",
            )
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

    _, post_topology_hash = _verify_artifact(manifest_path.parent, topology_record)
    _, post_trajectory_hash = _verify_artifact(manifest_path.parent, trajectory_record)
    _, post_atom_order_hash = _verify_artifact(manifest_path.parent, atom_order_record)
    if (
        post_topology_hash != topology_hash
        or post_trajectory_hash != trajectory_hash
        or post_atom_order_hash != atom_order_hash
    ):
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
    policy = {
        "pbc": "nojump_then_center_protein",
        "alignment": "mass_weighted_backbone_fit",
        "exclusions": "non_protein_and_non_backbone",
        "requested_stride": stride,
        "effective_stride": effective_stride,
        "max_points": max_points,
    }
    report = _base_report(
        manifest_path,
        version=str(mda.__version__),
        runtime_sha256=resolved_runtime_sha256,
    )
    report.update({
        "status": "completed",
        "job_id": str(manifest["job_id"]),
        "replica": int(manifest["replica_index"]),
        "selection": SELECTION,
        "reference": "first_admitted_frame",
        "policy": policy,
        "policy_sha256": _canonical_json_sha256(policy),
        "frame_admission": {
            "source_frame_count": frame_count,
            "admitted_frame_count": len(values),
            "first_source_frame": values[0]["source_frame"],
            "last_source_frame": values[-1]["source_frame"],
            "first_time_ps": values[0]["time_ps"],
            "last_time_ps": values[-1]["time_ps"],
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
    report["inputs"].update({
        "topology_sha256": topology_hash,
        "trajectory_sha256": trajectory_hash,
        "atom_order_manifest_sha256": atom_order_hash,
        "atom_order_identity": identity,
    })
    report["analysis_identity_sha256"] = analysis_identity_sha256(report)
    return report


def analyze_manifest(
    manifest_path: Path,
    *,
    stride: int = 1,
    max_points: int = 2000,
    runtime_sha256: str | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="bms-md-analysis-inputs-") as temporary:
        return _analyze_manifest_from_snapshots(
            manifest_path,
            stride=stride,
            max_points=max_points,
            runtime_sha256=runtime_sha256,
            snapshot_root=Path(temporary),
        )


def _validate_report_schema(report: Mapping[str, Any]) -> None:
    _assert_finite_json(report)
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise MDAnalysisContractError(
            "MD_ANALYSIS_RUNTIME_UNAVAILABLE",
            "pinned JSON Schema validator is unavailable",
        ) from exc
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "md_analysis_v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(report)
    except MDAnalysisContractError:
        raise
    except Exception as exc:
        raise MDAnalysisContractError("MD_ANALYSIS_SCHEMA_INVALID", "analysis report failed its pinned JSON Schema") from exc


def _validate_run_manifest_schema(manifest: Mapping[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator

        schema_path = Path(__file__).resolve().parents[2] / "schemas" / "md_run_v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(manifest)
    except Exception as exc:
        raise MDAnalysisContractError(
            "MD_ANALYSIS_MANIFEST_INVALID",
            "replica manifest fails the authoritative bms.md.run.v1 schema",
        ) from exc


def write_analysis_report(
    manifest_path: Path,
    output_path: Path,
    *,
    stride: int = 1,
    max_points: int = 2000,
    runtime_sha256: str | None = None,
) -> tuple[Path, bool]:
    manifest_path = Path(manifest_path).resolve()
    resolved_runtime: str | None = None
    try:
        resolved_runtime = resolve_runtime_sha256(runtime_sha256)
        report = analyze_manifest(
            manifest_path,
            stride=stride,
            max_points=max_points,
            runtime_sha256=resolved_runtime,
        )
        success = True
    except MDAnalysisContractError as exc:
        report = _base_report(manifest_path, runtime_sha256=resolved_runtime)
        report["failure"] = {"code": exc.code, "message": str(exc)}
        report["analysis_identity_sha256"] = analysis_identity_sha256(report)
        success = False
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    transaction = f"{os.getpid()}-{os.urandom(8).hex()}"
    staged_tables: list[tuple[Path, Path]] = []
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
                temporary_table = target.with_name(f"{target.name}.tmp-{transaction}")
                parquet.write_table(table, temporary_table, compression="zstd")
                staged_tables.append((temporary_table, target))
                derived[name] = {
                    "path": target.name,
                    "bytes": temporary_table.stat().st_size,
                    "sha256": _sha256(temporary_table),
                }
            report["derived_artifacts"] = derived
            report["analysis_identity_sha256"] = analysis_identity_sha256(report)
            _validate_report_schema(report)
        except Exception as exc:
            for temporary, _ in staged_tables:
                temporary.unlink(missing_ok=True)
            staged_tables = []
            code = exc.code if isinstance(exc, MDAnalysisContractError) else "MD_ANALYSIS_PARQUET_UNAVAILABLE"
            message = str(exc) if isinstance(exc, MDAnalysisContractError) else "authoritative Parquet artifacts could not be emitted by the pinned runtime"
            report = _base_report(manifest_path, runtime_sha256=resolved_runtime)
            report["failure"] = {"code": code, "message": message}
            report["analysis_identity_sha256"] = analysis_identity_sha256(report)
            success = False
    _validate_report_schema(report)

    report_temporary = output_path.with_name(f"{output_path.name}.tmp-{transaction}")
    report_temporary.write_bytes(_json_bytes(report))
    artifact_manifest_path = output_path.parent / f"{output_path.stem}.artifacts.json"
    artifact_records: dict[str, dict[str, Any]] = {
        "analysis_report": {
            "path": output_path.name,
            "bytes": report_temporary.stat().st_size,
            "sha256": _sha256(report_temporary),
            "semantic_role": "md_analysis_report",
        }
    }
    if success:
        for name, record in report["derived_artifacts"].items():
            artifact_records[name] = {**record, "semantic_role": f"md_analysis_{name}"}
    artifact_manifest = {
        "schema": "bms.md.analysis-artifacts.v1",
        "status": report["status"],
        "job_id": report.get("job_id"),
        "replica": report.get("replica"),
        "input_manifest_sha256": report["inputs"]["manifest_sha256"],
        "analysis_identity_sha256": report["analysis_identity_sha256"],
        "artifacts": artifact_records,
    }
    artifact_manifest_temporary = artifact_manifest_path.with_name(
        f"{artifact_manifest_path.name}.tmp-{transaction}"
    )
    artifact_manifest_temporary.write_bytes(_json_bytes(artifact_manifest))
    staged = [
        *staged_tables,
        (report_temporary, output_path),
        (artifact_manifest_temporary, artifact_manifest_path),
    ]
    _publish_staged_generation(staged, commit_marker=artifact_manifest_path)
    return output_path, success
