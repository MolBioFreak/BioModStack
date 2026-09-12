#!/usr/bin/env python3
"""Run and normalize the declared Shape validator suite for one sequence.

ESMFold2 metrics are supplied by the existing ESMFold2 process. Native mode
runs exactly one requested Boltz-2 or Protenix V2 peer in its canonical image;
aggregate mode only joins their records and reuses ESMFold2 evidence.
Every validator gets a typed record, including a failure record; missing/failed evidence therefore
causes post-refold acceptance to reject instead of silently substituting one
model for another.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.native_diagnostics import redact_text


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
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("validator artifact must be a private regular file")
    return {"filename": str(path.resolve()), "sha256": _sha(path), "bytes": path.stat().st_size}


def _native_artifacts(root: Path) -> list[dict[str, Any]]:
    # Same native scientific formats on success and failure. Never capture logs,
    # caches, serialized Python objects, or paths outside the peer output root.
    artifacts = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".pdb", ".cif", ".json", ".npz"}:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("validator artifact escaped native output root")
        artifacts.append(_artifact(path))
    return artifacts


def _latest_json(root: Path, patterns: tuple[str, ...]) -> Path | None:
    candidates = [path for pattern in patterns for path in root.rglob(pattern) if path.is_file() and not path.is_symlink()]
    if not candidates:
        return None
    return sorted(set(candidates), key=lambda path: (path.stat().st_mtime_ns, path.as_posix()))[-1]


def _run_boltz(sequence: str, output_dir: Path, *, recycling_steps: int, sampling_steps: int, diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
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
    environment = dict(os.environ)
    completed = subprocess.run(command, cwd=output_dir, text=True, capture_output=True, check=False, env=environment)
    for stream in ("stdout", "stderr"):
        path = output_dir / f"boltz.{stream}.log"
        # New named copies only: filtering precedes the first digest, including
        # failed peers. Never rewrite prior sealed evidence or native science.
        with path.open("x", encoding="utf-8") as handle:
            handle.write(redact_text(getattr(completed, stream), environment=environment))
        diagnostics.append(_artifact(path))
    if completed.returncode != 0:
        raise RuntimeError(f"Boltz-2 exited {completed.returncode}")
    confidence = _latest_json(output_dir, ("*confidence*.json", "*metrics*.json"))
    if confidence is None:
        raise RuntimeError("Boltz-2 emitted no confidence JSON")
    payload = _json(confidence)
    structures = [path for path in output_dir.rglob("*.pdb") if path.is_file() and not path.is_symlink()]
    if not structures:
        raise RuntimeError("boltz2 emitted no native structure")
    artifacts = _native_artifacts(output_dir)
    return _record("boltz2", status="completed", model_id="boltz2", native_metrics=_native_metrics(payload), artifacts=artifacts)


def _run_protenix(sequence: str, output_dir: Path, *, seed: int, code_root: Path, diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
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
    environment = dict(os.environ)
    completed = subprocess.run(command, cwd=output_dir, text=True, capture_output=True, check=False, env=environment)
    for stream in ("stdout", "stderr"):
        path = output_dir / f"protenix.{stream}.log"
        # New named copies only: filtering precedes the first digest, including
        # failed peers. Never rewrite prior sealed evidence or native science.
        with path.open("x", encoding="utf-8") as handle:
            handle.write(redact_text(getattr(completed, stream), environment=environment))
        diagnostics.append(_artifact(path))
    if completed.returncode != 0:
        raise RuntimeError(f"Protenix V2 exited {completed.returncode}")
    confidence = _latest_json(prediction_dir, ("*confidence*.json", "*full_data*.json"))
    if confidence is None:
        raise RuntimeError("Protenix V2 emitted no confidence JSON")
    payload = _json(confidence)
    structures = [path for path in prediction_dir.rglob("*.cif") if path.is_file() and not path.is_symlink()]
    if not structures:
        raise RuntimeError("protenix_v2 emitted no native structure")
    artifacts = _native_artifacts(prediction_dir)
    return _record("protenix_v2", status="completed", model_id="protenix-v2", native_metrics=_native_metrics(payload), artifacts=artifacts)


def run_validator_suite(
    *,
    sequence: str,
    sequence_name: str,
    esm_metrics_path: Path | None,
    esm_structure_path: Path | None,
    output_path: Path,
    validators: list[str],
    seed: int,
    code_root: Path,
    recycling_steps: int = 3,
    sampling_steps: int = 50,
    peer_evidence: list[Path] | None = None,
) -> dict[str, Any]:
    # None is a native invocation; an explicit list is aggregation only.
    # Aggregation never launches an engine, even if a peer is missing.
    if "esmfold2" in validators:
        if esm_metrics_path is None or esm_structure_path is None:
            raise ValueError("ESMFold2 evidence is required")
        esm_metrics_path = esm_metrics_path.resolve(strict=True)
        esm_structure_path = esm_structure_path.resolve(strict=True)
    output_path = output_path.resolve()
    code_root = code_root.resolve(strict=True)
    if not sequence.strip() or not sequence_name.strip():
        raise ValueError("sequence and sequence name are required")
    if any(validator not in VALIDATORS for validator in validators):
        raise ValueError("validator suite contains an unsupported validator")
    if len(set(validators)) != len(validators):
        raise ValueError("validator suite contains duplicates")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root = output_path.parent.parent / f"{output_path.parent.name}_runtime"
    root.mkdir(exist_ok=True)
    records: dict[str, Any] = {}
    if "esmfold2" in validators:
        payload = _json(esm_metrics_path)
        if payload.get("sequence_name") != sequence_name:
            records["esmfold2"] = _record("esmfold2", status="failed", error="ESMFold2 sequence-name binding mismatch")
        else:
            records["esmfold2"] = _record(
                "esmfold2", status="completed", model_id="esmfold2", native_metrics=payload,
                artifacts=[_artifact(esm_metrics_path), _artifact(esm_structure_path)],
            )
    if peer_evidence is not None:
        expected = set(validators) - {"esmfold2"}
        for bundle in peer_evidence:
            bundle = bundle.resolve(strict=True)
            payload = _json(bundle / "shape_validator_records.json")
            declared = payload.get("validators")
            if (payload.get("schema") != "bms_shape_validator_suite_v1"
                    or payload.get("sequence_name") != sequence_name
                    or payload.get("runtime", {}).get("seed") != int(seed)
                    or not isinstance(declared, list) or len(declared) != 1):
                raise ValueError("native validator evidence binding mismatch")
            validator = declared[0]
            if validator not in expected or validator in records:
                raise ValueError("unexpected or duplicate native validator evidence")
            if set(payload.get("records", {})) != {validator}:
                raise ValueError("native validator evidence record set mismatch")
            record = payload["records"][validator]
            if (record.get("schema") != "bms_shape_validator_record_v1"
                    or record.get("validator") != validator
                    or record.get("status") not in {"completed", "failed"}):
                raise ValueError("invalid native validator record")
            for descriptor in record["artifacts"]:
                relative = Path(descriptor["filename"])
                source = bundle / relative
                if (relative.is_absolute() or ".." in relative.parts
                        or source.is_symlink()
                        or not source.resolve(strict=True).is_relative_to(bundle)):
                    raise ValueError("native validator artifact escaped evidence bundle")
                descriptor["filename"] = str(source)
            records[validator] = record
        if set(records) - {"esmfold2"} != expected:
            raise ValueError("missing selected native validator evidence")
    if peer_evidence is None and "boltz2" in validators:
        diagnostics: list[dict[str, Any]] = []
        try:
            records["boltz2"] = _run_boltz(sequence, root / "boltz2", recycling_steps=recycling_steps, sampling_steps=sampling_steps, diagnostics=diagnostics)
        except Exception as exc:
            records["boltz2"] = _record(
                "boltz2", status="failed", model_id="boltz2", error=redact_text(str(exc), environment=os.environ),
                artifacts=_native_artifacts(root / "boltz2"),
            )
        records["boltz2"]["artifacts"].extend(diagnostics)
    if peer_evidence is None and "protenix_v2" in validators:
        diagnostics = []
        try:
            records["protenix_v2"] = _run_protenix(sequence, root / "protenix_v2", seed=seed, code_root=code_root, diagnostics=diagnostics)
        except Exception as exc:
            records["protenix_v2"] = _record(
                "protenix_v2", status="failed", model_id="protenix-v2", error=redact_text(str(exc), environment=os.environ),
                artifacts=_native_artifacts(root / "protenix_v2" / "protenix_predictions"),
            )
        records["protenix_v2"]["artifacts"].extend(diagnostics)
    # Retain native science plus the explicitly named, already filtered logs.
    for validator, record in records.items():
        for index, descriptor in enumerate(record["artifacts"]):
            source = Path(descriptor["filename"])
            if source.is_symlink() or not source.is_file() or source.stat().st_nlink != 1:
                raise ValueError("validator artifact is not a private regular file")
            if _sha(source) != descriptor["sha256"] or source.stat().st_size != descriptor["bytes"]:
                raise ValueError("validator artifact changed before publication")
            destination = output_path.parent / "native" / validator / f"{index:04d}_{source.name}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            descriptor["filename"] = destination.relative_to(output_path.parent).as_posix()
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
    parser.add_argument("--mode", required=True, choices=("native", "aggregate"))
    parser.add_argument("--validator", choices=("boltz2", "protenix_v2"))
    parser.add_argument("--peer-evidence", action="append", type=Path, default=[])
    parser.add_argument("--esm-metrics", type=Path)
    parser.add_argument("--esm-structure", type=Path)
    parser.add_argument("--validators", help="comma-separated validator IDs")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recycling-steps", type=int, default=3)
    parser.add_argument("--sampling-steps", type=int, default=50)
    args = parser.parse_args()
    if args.mode == "native":
        if args.validator is None or args.validators is not None or args.peer_evidence:
            parser.error("native mode requires exactly --validator and no suite/peer inputs")
        validators = [args.validator]
    else:
        if args.validator is not None or args.validators is None:
            parser.error("aggregate mode requires --validators and no --validator")
        validators = [item for item in (value.strip() for value in args.validators.split(",")) if item]
    run_validator_suite(
        sequence=args.sequence,
        sequence_name=args.sequence_name,
        esm_metrics_path=args.esm_metrics,
        esm_structure_path=args.esm_structure,
        output_path=args.output,
        validators=validators,
        peer_evidence=args.peer_evidence if args.mode == "aggregate" else None,
        seed=args.seed,
        code_root=args.code_root,
        recycling_steps=args.recycling_steps,
        sampling_steps=args.sampling_steps,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
