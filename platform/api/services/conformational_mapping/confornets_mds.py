"""Controller-side ConforNets MDS embedding via BioModStack Stats Toolkit (R19)."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AnalysisRun, Job
from services.stats_toolkit_client import (
    STATS_TOOLKIT_UNAVAILABLE_REASON,
    StatsToolkitError,
    StatsToolkitUnavailableError,
    get_stats_toolkit_client,
)

CONFORNETS_MDS_SCHEMA_VERSION = 1
CONFORNETS_MDS_ARTIFACT_FILENAME = "confornets_mds_v1.json"


def validate_distance_matrix(distance_matrix: Sequence[Sequence[Any]]) -> list[list[float]]:
    n = len(distance_matrix)
    if n == 0:
        return []
    if any(len(row) != n for row in distance_matrix):
        raise ValueError("classical metric MDS requires a square distance matrix")
    cleaned: list[list[float]] = []
    for row_index, row in enumerate(distance_matrix):
        out_row: list[float] = []
        for column_index, value in enumerate(row):
            if value is None:
                raise ValueError("classical metric MDS cannot impute a missing distance")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("classical metric MDS distances must be numeric")
            fval = float(value)
            if not math.isfinite(fval) or fval < 0.0:
                raise ValueError("classical metric MDS requires finite nonnegative distances")
            if row_index == column_index and fval != 0.0:
                raise ValueError("classical metric MDS requires a zero diagonal")
            other = distance_matrix[column_index][row_index]
            if other is None or isinstance(other, bool) or not isinstance(other, (int, float)):
                raise ValueError("classical metric MDS distances must be numeric")
            if not math.isclose(fval, float(other), rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("classical metric MDS requires a symmetric distance matrix")
            out_row.append(fval)
        cleaned.append(out_row)
    return cleaned


def canonicalize_mds_2d(coords: Sequence[tuple[float, float]]) -> list[dict[str, float]]:
    """Place a 2D embedding into a deterministic rigid frame (translation + rotation + reflection)."""
    n = len(coords)
    if n == 0:
        return []
    mean_x = sum(x for x, _ in coords) / n
    mean_y = sum(y for _, y in coords) / n
    centered = [(x - mean_x, y - mean_y) for x, y in coords]
    anchor = next(((x, y) for x, y in centered if math.hypot(x, y) > 1e-12), None)
    if anchor is None:
        return [{"x": 0.0, "y": 0.0} for _ in range(n)]
    angle = math.atan2(anchor[1], anchor[0])
    cos_a = math.cos(-angle)
    sin_a = math.sin(-angle)
    rotated = [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in centered]
    second = next((y for _, y in rotated if abs(y) > 1e-12), 0.0)
    if second < 0.0:
        rotated = [(x, -y) for x, y in rotated]
    out: list[dict[str, float]] = []
    for x, y in rotated:
        cx = 0.0 if abs(x) < 5e-13 else x
        cy = 0.0 if abs(y) < 5e-13 else y
        out.append({"x": round(float(cx), 6), "y": round(float(cy), 6)})
    return out


def load_pairwise_rmsd_matrix(csv_path: Path) -> tuple[list[str], list[list[float]]]:
    """Read worker-produced evaluation/pairwise_rmsd_matrix.csv into (sample_ids, distance_matrix)."""
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames or fieldnames[0] != "sample_id":
            raise ValueError("pairwise_rmsd_matrix.csv must begin with a sample_id header column")
        sample_ids = fieldnames[1:]
        rows_by_id: dict[str, dict[str, str]] = {}
        for row in reader:
            sid = str(row.get("sample_id") or "").strip()
            if not sid:
                raise ValueError("pairwise_rmsd_matrix.csv contains an empty sample_id row")
            rows_by_id[sid] = row
    if set(rows_by_id) != set(sample_ids):
        raise ValueError("pairwise_rmsd_matrix.csv row ids do not match column headers")
    matrix: list[list[float]] = []
    for sid in sample_ids:
        row = rows_by_id[sid]
        parsed_row: list[float] = []
        for other in sample_ids:
            raw = (row.get(other) or "").strip()
            if not raw:
                raise ValueError("classical metric MDS cannot impute a missing distance")
            try:
                parsed_row.append(float(raw))
            except ValueError as exc:
                raise ValueError("classical metric MDS distances must be numeric") from exc
        matrix.append(parsed_row)
    return sample_ids, validate_distance_matrix(matrix)


def format_confornets_mds_coordinates(
    mds_raw: Any,
    *,
    sample_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(mds_raw, list):
        raise RuntimeError("Stats Toolkit multivariate.distance_clustering returned invalid mds_coordinates")
    pairs: list[tuple[float, float]] = []
    for pt in mds_raw:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            raise RuntimeError("Stats Toolkit multivariate.distance_clustering returned malformed coordinates")
        x, y = float(pt[0]), float(pt[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise RuntimeError("Stats Toolkit classical metric MDS produced non-finite coordinates")
        pairs.append((x, y))
    canonical = canonicalize_mds_2d(pairs)
    ids = list(sample_ids) if sample_ids is not None else [str(i) for i in range(len(canonical))]
    if len(ids) != len(canonical):
        ids = [str(i) for i in range(len(canonical))]
    return [{"sample_id": sid, "x": pt["x"], "y": pt["y"]} for sid, pt in zip(ids, canonical)]


def compute_confornets_mds_via_toolkit(
    distance_matrix: Sequence[Sequence[Any]],
    *,
    sample_ids: Sequence[str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Compute ConforNets classical metric MDS via Stats Toolkit only (no local eigendecomposition)."""
    matrix = validate_distance_matrix(distance_matrix)
    n = len(matrix)
    ids = list(sample_ids) if sample_ids is not None else [str(i) for i in range(n)]
    if n == 0:
        return {
            "schema_version": CONFORNETS_MDS_SCHEMA_VERSION,
            "status": "not_computed",
            "reason": "empty_distance_matrix",
            "stats_backend": "stats_toolkit",
            "stats_tool_id": "multivariate.distance_clustering",
            "stats_run": None,
            "sample_ids": [],
            "coordinates": [],
        }
    if n == 1:
        return {
            "schema_version": CONFORNETS_MDS_SCHEMA_VERSION,
            "status": "available",
            "method": "trivial_single_sample",
            "stats_backend": "stats_toolkit",
            "stats_tool_id": "multivariate.distance_clustering",
            "stats_run": None,
            "sample_ids": ids,
            "coordinates": [{"sample_id": ids[0], "x": 0.0, "y": 0.0}],
        }
    if n == 2:
        distance = float(matrix[0][1])
        return {
            "schema_version": CONFORNETS_MDS_SCHEMA_VERSION,
            "status": "available",
            "method": "exact_two_sample_distance",
            "stats_backend": "stats_toolkit",
            "stats_tool_id": "multivariate.distance_clustering",
            "stats_run": None,
            "sample_ids": ids,
            "coordinates": [
                {"sample_id": ids[0], "x": round(-distance / 2.0, 6), "y": 0.0},
                {"sample_id": ids[1], "x": round(distance / 2.0, 6), "y": 0.0},
            ],
        }
    client = get_stats_toolkit_client()
    try:
        run_ref, result = client.run_tool(
            None,
            "multivariate.distance_clustering",
            {
                "distance_matrix": matrix,
                "method": "hierarchical",
                "mds_components": 2,
            },
            idempotency_key=idempotency_key,
        )
    except StatsToolkitUnavailableError:
        return {
            "schema_version": CONFORNETS_MDS_SCHEMA_VERSION,
            "status": "unavailable",
            "reason": STATS_TOOLKIT_UNAVAILABLE_REASON,
            "stats_backend": "stats_toolkit",
            "stats_tool_id": "multivariate.distance_clustering",
            "stats_run": None,
            "sample_ids": ids,
            "coordinates": [],
        }
    except StatsToolkitError as exc:
        raise ValueError(str(exc)) from exc

    coords_raw = result.get("mds_coordinates")
    if not isinstance(coords_raw, list) or len(coords_raw) != n:
        raise RuntimeError("Stats Toolkit multivariate.distance_clustering returned invalid mds_coordinates")
    coordinates = format_confornets_mds_coordinates(coords_raw, sample_ids=ids)
    return {
        "schema_version": CONFORNETS_MDS_SCHEMA_VERSION,
        "status": "available",
        "stats_backend": "stats_toolkit",
        "stats_tool_id": "multivariate.distance_clustering",
        "stats_method_version": run_ref.get("method_version"),
        "stats_run": run_ref,
        "sample_ids": ids,
        "coordinates": coordinates,
    }


def find_confornets_pairwise_rmsd_csv(
    result_root: Path,
    *,
    output_path: Path | None = None,
) -> Path | None:
    candidates: list[Path] = [
        result_root / "native" / "evaluation" / "pairwise_rmsd_matrix.csv",
        result_root / "evaluation" / "pairwise_rmsd_matrix.csv",
    ]
    native_manifest = result_root / "cm_native_artifacts_v1.json"
    if native_manifest.is_file():
        try:
            payload = json.loads(native_manifest.read_text(encoding="utf-8"))
            for item in payload.get("artifacts") or []:
                rel = str((item or {}).get("relative_path") or "")
                if rel.endswith("pairwise_rmsd_matrix.csv"):
                    candidates.insert(0, result_root / rel)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    if output_path is not None:
        candidates.extend([
            output_path / "evaluation" / "pairwise_rmsd_matrix.csv",
            output_path / "final" / "conformational_mapping" / "canonical_confornets" / "native" / "evaluation" / "pairwise_rmsd_matrix.csv",
        ])
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            return path
    return None


def ingest_confornets_mds_for_result_root(
    result_root: Path,
    *,
    job_id: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any] | None:
    """Compute and persist controller-side MDS receipt from worker pairwise_rmsd_matrix.csv."""
    csv_path = find_confornets_pairwise_rmsd_csv(result_root, output_path=output_path)
    if csv_path is None:
        return None
    csv_sha256 = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    sample_ids, matrix = load_pairwise_rmsd_matrix(csv_path)
    idem = f"bms:confornets-mds:{job_id or 'root'}:{csv_sha256[:16]}"
    mds_payload = compute_confornets_mds_via_toolkit(
        matrix,
        sample_ids=sample_ids,
        idempotency_key=idem,
    )
    mds_payload["source_pairwise_rmsd_sha256"] = csv_sha256
    try:
        mds_payload["source_pairwise_rmsd_path"] = csv_path.relative_to(result_root).as_posix()
    except ValueError:
        mds_payload["source_pairwise_rmsd_path"] = csv_path.name
    artifact_path = result_root / CONFORNETS_MDS_ARTIFACT_FILENAME
    artifact_path.write_text(json.dumps(mds_payload, indent=2), encoding="utf-8")
    return mds_payload


async def ingest_confornets_mds_for_job(
    session: AsyncSession,
    job: Job,
    *,
    result_root: Path,
    output_path: Path | None = None,
) -> dict[str, Any] | None:
    """Controller-side ingestion hook: compute durable Toolkit MDS and record an AnalysisRun."""
    mds_payload = ingest_confornets_mds_for_result_root(
        result_root,
        job_id=str(job.id),
        output_path=output_path,
    )
    if mds_payload is None:
        return None
    sig = str(mds_payload.get("source_pairwise_rmsd_sha256") or "")
    stats_run = mds_payload.get("stats_run")
    if mds_payload.get("status") == "available" and isinstance(stats_run, dict) and stats_run.get("run_id"):
        existing = (
            await session.execute(
                select(AnalysisRun)
                .where(
                    AnalysisRun.subject_kind == "job",
                    AnalysisRun.subject_id == str(job.id),
                    AnalysisRun.analysis_type == "confornets_mds",
                    AnalysisRun.status == "completed",
                )
                .order_by(AnalysisRun.queued_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None or existing.input_signature != sig:
            now = datetime.now(timezone.utc)
            _params_obj = {"mds_components": 2, "method": "hierarchical"}
            _params_hash = hashlib.sha256(json.dumps(_params_obj, sort_keys=True).encode("utf-8")).hexdigest()
            _code_ver = str((stats_run or {}).get("method_version") or "1.0.0")
            _cache_key = hashlib.sha256(f"confornets_mds:{job.id}:{sig}:{_params_hash}:{_code_ver}".encode("utf-8")).hexdigest()
            run_row = AnalysisRun(
                id=str(uuid.uuid4()),
                subject_kind="job",
                subject_id=str(job.id),
                analysis_type="confornets_mds",
                input_signature=sig,
                params_json=_params_obj,
                params_hash=_params_hash,
                code_version=_code_ver,
                cache_key=_cache_key,
                status="completed",
                supersedes_run_id=existing.id if existing is not None else None,
                summary_json={
                    "stats_run": stats_run,
                    "sample_ids": mds_payload.get("sample_ids") or [],
                    "source_pairwise_rmsd_sha256": sig,
                    "source_pairwise_rmsd_path": mds_payload.get("source_pairwise_rmsd_path"),
                },
                result_inline_json=None,
                queued_at=now,
                started_at=now,
                completed_at=now,
            )
            session.add(run_row)
            mds_payload["analysis_run_id"] = run_row.id
        else:
            mds_payload["analysis_run_id"] = existing.id
    return mds_payload
