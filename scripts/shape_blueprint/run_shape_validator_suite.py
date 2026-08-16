#!/usr/bin/env python3
"""Run and normalize the declared Shape validator suite for one sequence.

ESMFold2 metrics are supplied by the existing ESMFold2 process.  Boltz-2 and
Protenix V2 are invoked here only when requested.  Every validator gets a
typed record, including a failure record; missing/failed evidence therefore
causes post-refold acceptance to reject instead of silently substituting one
model for another.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable


VALIDATORS = frozenset(("boltz2", "esmfold2", "protenix_v2"))


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"validator artifact {path} must be an object")
    return payload


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(payload) + b"\n")
    os.replace(temporary, path)


def _numeric_leaves(value: Any, prefix: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _numeric_leaves(child, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number == number and abs(number) != float("inf"):
            yield prefix, number


def _native_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the native payload and expose common metrics at the top level."""
    metrics = dict(payload)
    aliases = {
        "confidence_score": ("confidence_score", "confidence", "overall_confidence"),
        "ptm": ("ptm", "ptm_score", "predicted_tm_score"),
        "plddt": ("plddt", "plddt_score"),
        "plddt_mean": ("plddt_mean", "mean_plddt", "avg_plddt"),
        "ipSAE": ("ipSAE", "ipsae", "ip_sae"),
    }
    leaves = list(_numeric_leaves(payload))
    for target, candidates in aliases.items():
        if target in metrics and isinstance(metrics[target], (int, float)):
            continue
        for path, number in leaves:
            leaf = path.rsplit(".", 1)[-1].lower()
            if leaf in {candidate.lower() for candidate in candidates}:
                metrics[target] = number
                break
    return metrics


def _record(
    validator: str,
    *,
    status: str,
    task_type: str = "monomer",
    model_id: str | None = None,
    native_metrics: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "bms_shape_validator_record_v1",
        "validator": validator,
        "status": status,
        "task_type": task_type,
        "model_id": model_id,
        "native_metrics": native_metrics or {},
        "metric_namespace": f"{validator}.native",
        "artifacts": artifacts or [],
    }
    if error:
        result["error"] = error
    return result


def _artifact(path: Path) -> dict[str, Any]:
    return {"filename": path.name, "sha256": _sha(path), "bytes": path.stat().st_size}


def _latest_json(root: Path, patterns: tuple[str, ...]) -> Path | None:
    candidates = [path for pattern in patterns for path in root.rglob(pattern) if path.is_file() and not path.is_symlink()]
    if not candidates:
        return None
    return sorted(set(candidates), key=lambda path: (path.stat().st_mtime_ns, path.as_posix()))[-1]


def _run_boltz(sequence: str, output_dir: Path, *, recycling_steps: int, sampling_steps: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    inputs = output_dir / "boltz_inputs"
    inputs.mkdir()
    # JSON is valid YAML and avoids a dependency in the wrapper process.
    (inputs / "shape.yaml").write_bytes(_canonical({
        "sequences": [{"protein": {"id": ["A"], "sequence": sequence, "msa": "empty"}}]
    }) + b"\n")
    command = [
        "boltz", "predict", str(inputs), "--output_format", "pdb",
        "--diffusion_samples", "1", "--recycling_steps", str(recycling_steps),
        "--sampling_steps", str(sampling_steps), "--cache", "/boltzcache",
    ]
    completed = subprocess.run(command, cwd=output_dir, text=True, capture_output=True, check=False)
    (output_dir / "boltz.stdout.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Boltz-2 exited {completed.returncode}")
    confidence = _latest_json(output_dir, ("*confidence*.json", "*metrics*.json"))
    if confidence is None:
        raise RuntimeError("Boltz-2 emitted no confidence JSON")
    payload = _json(confidence)
    structures = [path for path in output_dir.rglob("*.pdb") if path.is_file() and not path.is_symlink()]
    artifacts = [_artifact(confidence)] + [_artifact(path) for path in sorted(structures)]
    return _record("boltz2", status="completed", model_id="boltz2", native_metrics=_native_metrics(payload), artifacts=artifacts)


def _run_protenix(sequence: str, output_dir: Path, *, seed: int, code_root: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "protenix_input.json"
    input_path.write_bytes(_canonical([{
        "name": "shape",
        "modelSeeds": [int(seed)],
        "sequences": [{"proteinChain": {"sequence": sequence, "count": 1}}],
    }]) + b"\n")
    prediction_dir = output_dir / "protenix_predictions"
    command = [
        "python3", str(code_root / "scripts" / "run_protenix_inference.py"), "--input", str(input_path),
        "--out_dir", str(prediction_dir), "--model_name", "protenix-v2", "--seeds", str(seed),
        "--sample", "1", "--use_msa", "false", "--use_template", "false",
    ]
    completed = subprocess.run(command, cwd=output_dir, text=True, capture_output=True, check=False)
    (output_dir / "protenix.stdout.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Protenix V2 exited {completed.returncode}")
    confidence = _latest_json(prediction_dir, ("*confidence*.json", "*full_data*.json"))
    if confidence is None:
        raise RuntimeError("Protenix V2 emitted no confidence JSON")
    payload = _json(confidence)
    structures = [path for path in prediction_dir.rglob("*.cif") if path.is_file() and not path.is_symlink()]
    artifacts = [_artifact(confidence)] + [_artifact(path) for path in sorted(structures)]
    return _record("protenix_v2", status="completed", model_id="protenix-v2", native_metrics=_native_metrics(payload), artifacts=artifacts)


def run_validator_suite(
    *,
    sequence: str,
    sequence_name: str,
    esm_metrics_path: Path,
    output_path: Path,
    validators: list[str],
    seed: int,
    code_root: Path,
    recycling_steps: int = 3,
    sampling_steps: int = 50,
) -> dict[str, Any]:
    esm_metrics_path = esm_metrics_path.resolve(strict=True)
    output_path = output_path.resolve()
    code_root = code_root.resolve(strict=True)
    if not sequence.strip() or not sequence_name.strip():
        raise ValueError("sequence and sequence name are required")
    if any(validator not in VALIDATORS for validator in validators):
        raise ValueError("validator suite contains an unsupported validator")
    if len(set(validators)) != len(validators):
        raise ValueError("validator suite contains duplicates")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root = output_path.parent / "validator_runtime"
    root.mkdir(exist_ok=True)
    records: dict[str, Any] = {}
    if "esmfold2" in validators:
        payload = _json(esm_metrics_path)
        if payload.get("sequence_name") != sequence_name:
            records["esmfold2"] = _record("esmfold2", status="failed", error="ESMFold2 sequence-name binding mismatch")
        else:
            records["esmfold2"] = _record(
                "esmfold2", status="completed", model_id="esmfold2", native_metrics=payload,
                artifacts=[_artifact(esm_metrics_path)],
            )
    if "boltz2" in validators:
        try:
            records["boltz2"] = _run_boltz(sequence, root / "boltz2", recycling_steps=recycling_steps, sampling_steps=sampling_steps)
        except Exception as exc:
            records["boltz2"] = _record("boltz2", status="failed", error=str(exc))
    if "protenix_v2" in validators:
        try:
            records["protenix_v2"] = _run_protenix(sequence, root / "protenix_v2", seed=seed, code_root=code_root)
        except Exception as exc:
            records["protenix_v2"] = _record("protenix_v2", status="failed", error=str(exc))
    payload = {
        "schema": "bms_shape_validator_suite_v1",
        "status": "completed" if records and all(record.get("status") == "completed" for record in records.values()) else "incomplete",
        "sequence_name": sequence_name,
        "validators": validators,
        "records": records,
        "runtime": {"code_root": str(code_root), "seed": int(seed)},
    }
    _write(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--sequence-name", required=True)
    parser.add_argument("--esm-metrics", required=True, type=Path)
    parser.add_argument("--validators", required=True, help="comma-separated validator IDs")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recycling-steps", type=int, default=3)
    parser.add_argument("--sampling-steps", type=int, default=50)
    args = parser.parse_args()
    run_validator_suite(
        sequence=args.sequence,
        sequence_name=args.sequence_name,
        esm_metrics_path=args.esm_metrics,
        output_path=args.output,
        validators=[item for item in (value.strip() for value in args.validators.split(",")) if item],
        seed=args.seed,
        code_root=args.code_root,
        recycling_steps=args.recycling_steps,
        sampling_steps=args.sampling_steps,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
