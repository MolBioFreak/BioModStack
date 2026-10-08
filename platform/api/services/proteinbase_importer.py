from __future__ import annotations

import json
import re
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import urlopen

from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job
from paths import get_results_dir
from schemas import JobStatus


Downloader = Callable[[str, Path], None]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_percent_metric(value: Any) -> float | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    if numeric <= 1.0:
        return numeric * 100.0
    return numeric


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_positive_int(value: Any) -> int | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    integer = int(round(numeric))
    return integer if integer > 0 else None


def _sequence_length(sequence: str | None) -> int | None:
    text = _coerce_text(sequence)
    if not text:
        return None
    parts = ["".join(segment.split()) for segment in text.split("|")]
    lengths = [len(part) for part in parts if part]
    return sum(lengths) if lengths else None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return slug.strip("._-") or "proteinbase_design"


def _extract_nested_url(value: Any) -> str | None:
    if isinstance(value, dict):
        direct = _coerce_text(value.get("url"))
        if direct:
            return direct
        nested_file = value.get("file")
        if isinstance(nested_file, dict):
            return _coerce_text(nested_file.get("url"))
    return None


def _metric_values(record: dict[str, Any]) -> dict[str, list[Any]]:
    metrics: dict[str, list[Any]] = defaultdict(list)
    for evaluation in record.get("evaluations", []) or []:
        if not isinstance(evaluation, dict):
            continue
        metric_name = _coerce_text(evaluation.get("metric"))
        if not metric_name:
            continue
        metrics[metric_name].append(evaluation.get("value"))
    return metrics


def _latest_metric(metrics: dict[str, list[Any]], key: str) -> Any:
    values = metrics.get(key) or []
    return values[-1] if values else None


def _flatten_metric_values(metrics: dict[str, list[Any]]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, values in metrics.items():
        if not values:
            continue
        flattened[key] = values[-1] if len(values) == 1 else values
    return flattened


def _default_downloader(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as response:
        destination.write_bytes(response.read())


def _url_suffix(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".cif", ".mmcif", ".pdb"}:
        return suffix
    return ".cif"


def normalize_proteinbase_record(record: dict[str, Any]) -> dict[str, Any]:
    metrics = _metric_values(record)
    flattened_metrics = _flatten_metric_values(metrics)

    sequence = _coerce_text(record.get("sequence"))
    length_aa = _safe_positive_int(record.get("length_aa")) or _sequence_length(sequence)

    boltz_structure_url = _extract_nested_url(_latest_metric(metrics, "boltz2_structure_prediction"))
    esmfold_structure_url = _extract_nested_url(_latest_metric(metrics, "esmfold_structure_prediction"))
    structure_url = boltz_structure_url or esmfold_structure_url

    pae_json_url = _extract_nested_url(_latest_metric(metrics, "pae_file"))

    plddt_overall = _normalize_percent_metric(
        _latest_metric(metrics, "boltz2_complex_plddt")
        or _latest_metric(metrics, "boltz2_plddt")
        or _latest_metric(metrics, "esmfold_plddt")
    )
    iptm = _safe_float(_latest_metric(metrics, "boltz2_iptm"))
    ptm = _safe_float(_latest_metric(metrics, "boltz2_ptm"))
    ipsae = _safe_float(_latest_metric(metrics, "boltz2_ipsae"))
    complex_iplddt = _safe_float(_latest_metric(metrics, "boltz2_complex_iplddt"))
    complex_ipde = _safe_float(_latest_metric(metrics, "boltz2_complex_pde"))
    mpnn_score = _safe_float(
        _latest_metric(metrics, "redesigned_proteinmpnn_score")
        or _latest_metric(metrics, "proteinmpnn_score")
    )

    confidence_metrics: dict[str, Any] = {
        **flattened_metrics,
        "structure_prediction_url": structure_url,
        "pae_json_url": pae_json_url,
        "min_iPSAE": _safe_float(_latest_metric(metrics, "boltz2_min_ipsae")),
        "LIS": _safe_float(_latest_metric(metrics, "boltz2_lis")),
        "pDockQ": _safe_float(_latest_metric(metrics, "boltz2_pdockq")),
        "pDockQ2": _safe_float(_latest_metric(metrics, "boltz2_pdockq2")),
        "proteinbase": {
            "id": _coerce_text(record.get("id")),
            "name": _coerce_text(record.get("name")),
            "author": _coerce_text(record.get("author")),
            "design_method": _coerce_text(record.get("designMethod")),
            "protein_url": _coerce_text(record.get("protein_url")),
            "length_aa": length_aa,
            "sequence": sequence,
            "raw_evaluations": record.get("evaluations") or [],
        },
    }

    return {
        "proteinbase_id": _coerce_text(record.get("id")),
        "name": _coerce_text(record.get("name")) or _coerce_text(record.get("id")) or str(uuid.uuid4()),
        "author": _coerce_text(record.get("author")),
        "design_method": _coerce_text(record.get("designMethod")),
        "sequence": sequence,
        "length_aa": length_aa,
        "binder_length": length_aa,
        "protein_url": _coerce_text(record.get("protein_url")),
        "structure_url": structure_url,
        "plddt_overall": plddt_overall,
        "mpnn_score": mpnn_score,
        "ptm": ptm,
        "iptm": iptm,
        "complex_iplddt": complex_iplddt,
        "complex_ipde": complex_ipde,
        "ipsae": ipsae,
        "confidence_metrics": confidence_metrics,
    }


async def import_proteinbase_bundle(
    *,
    session: AsyncSession,
    bundle_path: str | Path,
    dataset_name: str,
    job_name: str | None = None,
    downloader: Downloader | None = None,
    imported_at: datetime | None = None,
) -> Job:
    downloader = downloader or _default_downloader
    imported_at = imported_at or datetime.utcnow()
    bundle_path = Path(bundle_path).expanduser().resolve()

    rows = [
        json.loads(line)
        for line in bundle_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    normalized_rows = [normalize_proteinbase_record(row) for row in rows]

    job_id = str(uuid.uuid4())
    dataset_slug = _slugify(job_name or dataset_name)
    timestamp = imported_at.strftime("%Y%m%dT%H%M%SZ")
    output_dir = get_results_dir() / "imports" / f"{dataset_slug}_{timestamp}"
    structures_dir = output_dir / "structures"
    output_dir.mkdir(parents=True, exist_ok=True)
    structures_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle_path, output_dir / bundle_path.name)

    job = Job(
        id=job_id,
        name=job_name or dataset_name,
        status=JobStatus.COMPLETED.value,
        model_id="proteinbase",
        mode="external_import",
        params={
            "import_type": "proteinbase_bundle",
            "bundle_path": str(bundle_path),
            "dataset_name": dataset_name,
            "record_count": len(normalized_rows),
        },
        created_at=imported_at,
        started_at=imported_at,
        completed_at=imported_at,
        output_dir=str(output_dir),
        queue_status="completed",
        current_stage="proteinbase_import",
        completed_stages=["proteinbase_import"],
        stage_outputs={"proteinbase_import": []},
        stage_family="validation",
        stage_mode="proteinbase_import",
        selection_source_type="saved_dataset",
        selection_dataset_name=dataset_name,
        source_selection_count=len(normalized_rows),
        provenance={
            "source": "proteinbase",
            "bundle_path": str(bundle_path),
            "dataset_name": dataset_name,
            "imported_at": imported_at.isoformat() + "Z",
        },
    )
    session.add(job)

    name_counts: Counter[str] = Counter()
    created_designs: list[Design] = []
    stage_outputs: list[str] = []

    for normalized in normalized_rows:
        structure_url = normalized.get("structure_url")
        if not structure_url:
            continue

        base_name = normalized["name"]
        name_counts[base_name] += 1
        design_name = base_name if name_counts[base_name] == 1 else f"{base_name}_{name_counts[base_name]:02d}"
        structure_path = structures_dir / f"{_slugify(design_name)}{_url_suffix(structure_url)}"
        downloader(structure_url, structure_path)

        stage_outputs.append(str(structure_path))
        design = Design(
            id=str(uuid.uuid4()),
            job_id=job_id,
            name=design_name,
            pdb_path=str(structure_path),
            json_path=None,
            stage_family="validation",
            stage_mode="proteinbase_import",
            artifact_class="imported_structure",
            provenance={
                "source": "proteinbase",
                "dataset_name": dataset_name,
                "proteinbase_id": normalized.get("proteinbase_id"),
                "protein_url": normalized.get("protein_url"),
                "author": normalized.get("author"),
                "design_method": normalized.get("design_method"),
                "sequence": normalized.get("sequence"),
                "length_aa": normalized.get("length_aa"),
            },
            confidence_metrics=normalized.get("confidence_metrics"),
            binder_length=normalized.get("binder_length"),
            plddt_overall=normalized.get("plddt_overall"),
            mpnn_score=normalized.get("mpnn_score"),
            ptm=normalized.get("ptm"),
            iptm=normalized.get("iptm"),
            complex_iplddt=normalized.get("complex_iplddt"),
            complex_ipde=normalized.get("complex_ipde"),
            ipsae=normalized.get("ipsae"),
            created_at=imported_at,
        )
        session.add(design)
        created_designs.append(design)

    job.stage_outputs = {"proteinbase_import": stage_outputs}
    job.child_design_count = len(created_designs)

    (output_dir / "import_manifest.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "dataset_name": dataset_name,
                "record_count": len(normalized_rows),
                "imported_design_count": len(created_designs),
                "bundle_path": str(bundle_path),
                "structures_dir": str(structures_dir),
                "imported_at": imported_at.isoformat() + "Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    await session.commit()
    return job
