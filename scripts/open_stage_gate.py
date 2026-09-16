#!/usr/bin/env python3
"""
Open an interactive stage gate for a running job.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "platform" / "api"))

try:
    from paths import to_allowed_relative  # type: ignore
except Exception:
    to_allowed_relative = None  # type: ignore


API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


def normalize_path(path_value: str) -> str:
    if not path_value:
        return ""
    resolved = Path(path_value).expanduser().resolve()
    if to_allowed_relative is not None:
        try:
            return to_allowed_relative(resolved)
        except Exception:
            pass
    return str(resolved)


def list_preview_files(directory: Path, patterns: list[str], limit: int = 25) -> list[str]:
    if not directory.exists():
        return []
    files: list[Path] = []
    for pattern in patterns:
        files.extend(directory.glob(pattern))
    files = sorted({path.resolve() for path in files})[:limit]
    return [normalize_path(str(path)) for path in files]


def count_files(directory: Path, patterns: list[str]) -> int:
    if not directory.exists():
        return 0
    files: set[Path] = set()
    for pattern in patterns:
        files.update(path.resolve() for path in directory.glob(pattern))
    return len(files)


def parse_backbone_id(name: str) -> int | None:
    normalized = str(name or "").strip()
    while re.match(r"^\d+_", normalized):
        normalized = normalized.split("_", 1)[1]

    patterns = (
        r"(?:^|[_-])rfantibody[_-]?child[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])child[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])(?:job|input|design)[_-]?(\d+)(?=[_-]|$)",
    )
    for pattern in patterns:
        matches = re.findall(pattern, normalized)
        if matches:
            return int(matches[-1])
    return None


def summarize_backbones(directory: Path | None, patterns: list[str], preview_limit: int = 3) -> dict | None:
    if not directory or not directory.exists():
        return None

    files: list[Path] = []
    for pattern in patterns:
        files.extend(directory.glob(pattern))
    unique_files = sorted({path.resolve() for path in files})

    backbones: dict[int, dict] = {}
    unassigned_count = 0
    unassigned_preview: list[str] = []

    for path in unique_files:
        backbone_id = parse_backbone_id(path.stem)
        if backbone_id is None:
            unassigned_count += 1
            if len(unassigned_preview) < preview_limit:
                unassigned_preview.append(normalize_path(str(path)))
            continue

        entry = backbones.setdefault(
            backbone_id,
            {
                "count": 0,
                "representative_file": None,
                "preview": [],
                "sample_names": [],
            },
        )
        entry["count"] += 1
        if entry["representative_file"] is None:
            entry["representative_file"] = normalize_path(str(path))
        if len(entry["preview"]) < preview_limit:
            entry["preview"].append(normalize_path(str(path)))
        if len(entry["sample_names"]) < preview_limit:
            entry["sample_names"].append(path.name)

    return {
        "mode": "backbone_id",
        "total": len(unique_files),
        "assigned_total": sum(entry["count"] for entry in backbones.values()),
        "unassigned_total": unassigned_count,
        "unassigned_preview": unassigned_preview,
        "backbones": {str(backbone_id): data for backbone_id, data in sorted(backbones.items())},
    }


def open_component_gate(runtime, *, job_id: str, stage: str, payload: dict,
                        directories: dict[str, Path | None], payload_path: Path | None = None) -> dict:
    """Retain only the declared native review projection in the shared journal."""
    import hashlib
    from component_runtime import ResultReference, canonical_bytes, durable_write

    if job_id != runtime.root_job_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", stage):
        raise ValueError("gate must belong to the component root and a named native stage")
    checkpoint_id = f"{job_id}:{stage}"
    generation = int((runtime.root_state() or {}).get("generation", 0))
    if generation:
        checkpoint_id += f":{generation}"
    root = runtime.artifact_root.resolve()
    projection = root / ".bms-review" / hashlib.sha256(checkpoint_id.encode()).hexdigest()
    references = []

    def retain(path: Path, role: str, name: str) -> None:
        data = path.resolve(strict=True).read_bytes()
        destination = projection / role / name
        if destination.exists() and destination.read_bytes() != data:
            raise ValueError("immutable review projection conflicts")
        durable_write(destination, data)
        references.append(ResultReference(component_id=job_id,
            relative_path=destination.relative_to(root).as_posix(),
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
            schema="bms.stage-review." + role + ".v1"))

    for role, directory in directories.items():
        if directory is not None:
            files = sorted({p.resolve() for pattern in ("*.pdb", "*.cif", "*.json", "*.csv", "*.tsv")
                            for p in directory.glob(pattern) if p.is_file()})
            for path in files:
                retain(path, role, path.name)
    if payload_path is not None:
        retain(payload_path, "native-payload", payload_path.name)
    annotation = Path(f"gate_{stage}_annotations.json")
    if annotation.is_file() and (payload_path is None or annotation.resolve() != payload_path.resolve()):
        retain(annotation, "annotations", annotation.name)
    if not references:
        raise ValueError("gate requires actual declared review artifacts")
    # This is a derived control projection; native source JSON remains unchanged.
    edge = (runtime.root_state() or {}).get('continuation_edge')
    continuation = ({key: edge[key] for key in ('model_id', 'mode', 'native_parameters') if key in edge}
                    if edge else None)
    metadata = canonical_bytes(dict(stage=stage, payload=payload, continuation=continuation, generation=generation))
    metadata_path = projection / "gate-payload.json"
    if metadata_path.exists() and metadata_path.read_bytes() != metadata:
        raise ValueError("immutable gate payload conflicts")
    durable_write(metadata_path, metadata)
    references.append(ResultReference(component_id=job_id,
        relative_path=metadata_path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(metadata).hexdigest(), size_bytes=len(metadata),
        schema="bms.stage-review.control.v1"))
    return runtime.checkpoint(checkpoint_id, component_ids=[job_id], artifacts=references)


def component_checkpoint_projection(runtime) -> list[dict]:
    """Minimal host control metadata, never a full result retrieval or import."""
    from component_runtime import ResultReference
    projected = []
    for status in runtime.pending_checkpoints():
        checkpoint = status["checkpoint"]
        controls = [r for r in checkpoint["artifacts"] if r["schema"] == "bms.stage-review.control.v1"]
        if len(controls) != 1:
            raise ValueError("native checkpoint requires one bound gate control projection")
        control = json.loads(ResultReference(**controls[0]).resolve(runtime.artifact_root).read_text())
        projected.append(dict(checkpoint_id=checkpoint["checkpoint_id"],
            checkpoint_sha256=status["checkpoint_sha256"], stage=control["stage"],
            attempt_id=runtime.attempt_id, target_id=runtime.target_id, lease_id=runtime.lease_id,
            artifacts=checkpoint["artifacts"]))
    return projected


def main() -> int:
    parser = argparse.ArgumentParser(description="Open an interactive stage gate")
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--candidate_dir", default="")
    parser.add_argument("--raw_dir", default="")
    parser.add_argument("--filtered_dir", default="")
    parser.add_argument("--payload_json", default="")
    parser.add_argument("--framework_type", default="")
    parser.add_argument("--antibody_chains", default="")
    parser.add_argument("--structure_validator", default="")
    parser.add_argument("--api_url", default=API_BASE_URL)
    parser.add_argument("--output", default="gate.json")
    args = parser.parse_args()

    candidate_dir = Path(args.candidate_dir).expanduser().resolve() if args.candidate_dir else None
    raw_dir = Path(args.raw_dir).expanduser().resolve() if args.raw_dir else None
    filtered_dir = Path(args.filtered_dir).expanduser().resolve() if args.filtered_dir else None

    structure_patterns = ["*.pdb", "*.cif"]
    metric_patterns = ["*.json", "*.csv", "*.tsv"]
    payload = {
        "candidate_dir": normalize_path(str(candidate_dir)) if candidate_dir else None,
        "candidate_count": count_files(candidate_dir, structure_patterns) if candidate_dir else None,
        "candidate_preview": list_preview_files(candidate_dir, structure_patterns) if candidate_dir else [],
        "candidate_backbone_summary": summarize_backbones(candidate_dir, structure_patterns),
        "metric_count": count_files(candidate_dir, metric_patterns) if candidate_dir else None,
        "metric_preview": list_preview_files(candidate_dir, metric_patterns) if candidate_dir else [],
        "raw_dir": normalize_path(str(raw_dir)) if raw_dir else None,
        "raw_candidate_count": count_files(raw_dir, structure_patterns) if raw_dir else None,
        "raw_backbone_summary": summarize_backbones(raw_dir, structure_patterns) if raw_dir else None,
        "raw_metric_count": count_files(raw_dir, metric_patterns) if raw_dir else None,
        "filtered_dir": normalize_path(str(filtered_dir)) if filtered_dir else None,
        "filtered_candidate_count": count_files(filtered_dir, structure_patterns) if filtered_dir else None,
        "filtered_backbone_summary": summarize_backbones(filtered_dir, structure_patterns) if filtered_dir else None,
        "filtered_metric_count": count_files(filtered_dir, metric_patterns) if filtered_dir else None,
        "review_grouping": "backbone_id" if args.stage == "post_rfantibody" else None,
        "framework_type": args.framework_type or None,
        "antibody_chains": args.antibody_chains or None,
        "structure_validator": args.structure_validator or None,
    }
    if args.payload_json:
        payload_path = Path(args.payload_json).expanduser().resolve()
        extra_payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if not isinstance(extra_payload, dict):
            raise ValueError(f"Expected JSON object in {payload_path}")
        payload.update(extra_payload)

    sys.path.insert(0, str(CODE_ROOT))
    from scripts.lib.component_adapter import runtime_from_environment
    runtime = runtime_from_environment()
    if runtime is not None:
        result = open_component_gate(runtime, job_id=args.job_id, stage=args.stage, payload=payload,
            directories={"candidate": candidate_dir, "raw": raw_dir, "filtered": filtered_dir},
            payload_path=Path(args.payload_json).resolve() if args.payload_json else None)
    else:
        response = requests.post(
            f"{args.api_url}/api/jobs/{args.job_id}/stage-gates/{args.stage}/open",
            json={"payload": payload},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, indent=2))
    print(output_path.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
