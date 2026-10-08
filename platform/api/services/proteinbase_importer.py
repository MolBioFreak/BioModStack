from __future__ import annotations

import csv
import json
import ipaddress
import os
import re
import shutil
import socket
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job
from paths import get_results_dir
from schemas import JobStatus


Downloader = Callable[[str, Path], None]
DEFAULT_PROTEINBASE_ARTIFACT_HOSTS = frozenset({"proteinbase-pub.t3.storage.dev"})
MAX_PROTEINBASE_ARTIFACT_BYTES = 64 * 1024 * 1024
PROTEINBASE_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
PROTEINBASE_DOWNLOAD_TIMEOUT_SECONDS = 20.0


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _allowed_proteinbase_artifact_hosts() -> frozenset[str]:
    configured = os.getenv("BMS_PROTEINBASE_ARTIFACT_HOSTS", "")
    if not configured.strip():
        return DEFAULT_PROTEINBASE_ARTIFACT_HOSTS
    return frozenset(host.strip().lower().rstrip(".") for host in configured.split(",") if host.strip())


def _validate_proteinbase_artifact_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("ProteinBase artifact URLs must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("ProteinBase artifact URLs cannot contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ProteinBase artifact URL has an invalid port") from exc
    if port not in {None, 443}:
        raise ValueError("ProteinBase artifact URLs must use HTTPS port 443")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _allowed_proteinbase_artifact_hosts():
        raise ValueError("ProteinBase artifact URL host is not allowed")
    if parsed.fragment:
        raise ValueError("ProteinBase artifact URLs cannot contain fragments")
    return parsed.geturl(), host


def _validate_public_resolution(host: str) -> None:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("ProteinBase artifact host could not be resolved") from exc
    if not addresses:
        raise ValueError("ProteinBase artifact host did not resolve")
    for _family, _socktype, _proto, _canonname, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise ValueError("ProteinBase artifact host resolved to a non-public address")


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
    validated_url, host = _validate_proteinbase_artifact_url(url)
    _validate_public_resolution(host)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    request = Request(
        validated_url,
        headers={
            "Accept": "application/octet-stream,chemical/x-pdb,chemical/x-mmcif,text/plain",
            "User-Agent": "BioModStack-ProteinBase-Import/1",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=PROTEINBASE_DOWNLOAD_TIMEOUT_SECONDS) as response:
            final_url = str(response.geturl())
            if final_url != validated_url:
                raise ValueError("ProteinBase artifact redirects are not allowed")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise ValueError("ProteinBase artifact has an invalid Content-Length") from exc
                if declared_bytes < 0 or declared_bytes > MAX_PROTEINBASE_ARTIFACT_BYTES:
                    raise ValueError("ProteinBase artifact exceeds the download byte limit")

            total_bytes = 0
            with temporary_path.open("xb") as output:
                while True:
                    chunk = response.read(PROTEINBASE_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_PROTEINBASE_ARTIFACT_BYTES:
                        raise ValueError("ProteinBase artifact exceeds the download byte limit")
                    output.write(chunk)
            os.link(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _url_suffix(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".cif", ".mmcif", ".pdb"}:
        return suffix
    return ".cif"


def _load_proteinbase_rows(bundle_path: Path) -> tuple[list[dict[str, Any]], str]:
    if bundle_path.suffix.lower() == ".csv":
        rows: list[dict[str, Any]] = []
        with bundle_path.open("r", encoding="utf-8-sig", newline="") as bundle:
            reader = csv.DictReader(bundle)
            columns = set(reader.fieldnames or [])
            required_columns = {"id", "name", "sequence", "author", "designMethod", "evaluations"}
            if not required_columns.issubset(columns):
                raise ValueError("ProteinBase CSV is missing required columns")
            for row_number, row in enumerate(reader, start=2):
                try:
                    evaluations = json.loads(row.get("evaluations") or "[]")
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"ProteinBase CSV evaluations are invalid on row {row_number}"
                    ) from exc
                if not isinstance(evaluations, list):
                    raise ValueError(
                        f"ProteinBase CSV evaluations must be an array on row {row_number}"
                    )
                row["evaluations"] = evaluations
                rows.append(row)
        return rows, "csv"

    rows = [
        json.loads(line)
        for line in bundle_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("ProteinBase JSONL rows must be objects")
    return rows, "jsonl"


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

    rows, bundle_format = _load_proteinbase_rows(bundle_path)
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
            "bundle_format": bundle_format,
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
