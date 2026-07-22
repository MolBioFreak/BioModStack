#!/usr/bin/env python3
"""Compile Phase 0 conformational-mapping runtime evidence, fail closed.

This program never runs an inference workload.  It authenticates already-captured
observations, freezes live runtime identities, and emits one eight-file evidence
bundle for each registry vector.  Missing or ambiguous evidence always produces
STOP; it can never be interpreted as a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from verify_phase0_contract_report import classify_contract_report
from verify_protenix_layout_report import classify_layout_report

REQUIRED_FILES = (
    "command.json",
    "input_hashes.json",
    "output_hashes.json",
    "artifact_tree.json",
    "runtime_identity.json",
    "resources.json",
    "exit_status.json",
    "disposition.json",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
META_LINE_RE = re.compile(r"^([A-Za-z0-9_]+)=(.*)$")


class ProbeError(RuntimeError):
    """Fatal compiler/preflight error (no disposition can safely be emitted)."""


def reject_constant(value: str) -> None:
    raise ProbeError(f"non-finite JSON number is forbidden: {value}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except ProbeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"strict JSON load failed for {path}: {exc}") from exc
    return value


def dump_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProbeError(f"cannot serialize evidence JSON: {exc}") from exc


def sha256_file(path: Path, cache: dict[Path, str]) -> str:
    key = path.resolve(strict=True)
    if key in cache:
        return cache[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    value = digest.hexdigest()
    cache[key] = value
    return value


def safe_repo_file(repo: Path, raw: str) -> Path:
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise ProbeError(f"repository reference is not a contained relative path: {raw!r}")
    path = repo / rel
    cursor = repo
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ProbeError(f"symlink is forbidden in repository reference: {raw!r}")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(repo.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ProbeError(f"repository reference is unavailable or escapes repository: {raw!r}") from exc
    if not resolved.is_file():
        raise ProbeError(f"repository reference is not a regular file: {raw!r}")
    return resolved


def parse_meta(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProbeError(f"cannot read metadata {path}: {exc}") from exc
    for line in lines:
        match = META_LINE_RE.fullmatch(line)
        if not match:
            raise ProbeError(f"malformed metadata line in {path}: {line!r}")
        key, value = match.groups()
        if key in result:
            raise ProbeError(f"duplicate metadata key in {path}: {key}")
        result[key] = value
    return result


def run_readonly(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=180,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        output = proc.stdout
        return {
            "command": command,
            "exit_code": proc.returncode,
            "output_bytes": len(output),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_utf8": output.decode("utf-8", errors="replace"),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": command,
            "exit_code": None,
            "output_bytes": 0,
            "output_sha256": hashlib.sha256(b"").hexdigest(),
            "output_utf8": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def validate_registry(repo: Path, vectors: Path) -> dict[str, Any]:
    definitions = repo / "docs/specs/conformational_mapping/cm_contract_definitions_v1.md"
    validator = repo / "scripts/probes/conformational_mapping/validate_phase0_vectors.py"
    result = run_readonly([
        sys.executable,
        str(validator),
        "--definitions",
        str(definitions),
        "--vectors",
        str(vectors),
    ], cwd=repo)
    if result["exit_code"] != 0:
        raise ProbeError(
            "Phase 0 registry validation failed before evidence compilation: "
            + result["output_utf8"].strip()
        )
    return result


def vector_inventory(registry: Any) -> list[dict[str, Any]]:
    if not isinstance(registry, dict) or set(registry) != {
        "schema_name", "schema_version", "definitions", "canonicalization",
        "required_validation_command", "evidence_root_template",
        "runtime_status_policy", "vectors", "registry_sha256",
    }:
        raise ProbeError("registry top-level field inventory is not exact")
    vectors = registry.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != 53:
        raise ProbeError("registry must contain exactly 53 ordered vectors")
    ids: list[str] = []
    for vector in vectors:
        if not isinstance(vector, dict):
            raise ProbeError("every vector must be an object")
        vector_id = vector.get("id")
        if not isinstance(vector_id, str) or not re.fullmatch(r"P0-[A-Z0-9-]+", vector_id):
            raise ProbeError(f"invalid vector id: {vector_id!r}")
        if vector.get("probe_vector_id") != vector_id or vector.get("evidence_subdirectory") != vector_id:
            raise ProbeError(f"vector identity/subdirectory mismatch: {vector_id}")
        if tuple(vector.get("evidence_requirements", [])) != REQUIRED_FILES:
            raise ProbeError(f"required evidence inventory drift: {vector_id}")
        ids.append(vector_id)
    if len(ids) != len(set(ids)):
        raise ProbeError("duplicate vector id in registry")
    return vectors


def freeze_inputs(
    repo: Path,
    vectors_path: Path,
    vectors: list[dict[str, Any]],
    cache: dict[Path, str],
) -> dict[str, Any]:
    definitions = repo / "docs/specs/conformational_mapping/cm_contract_definitions_v1.md"
    entries: dict[str, dict[str, Any]] = {}
    for label, path in (("vectors", vectors_path), ("definitions", definitions)):
        entries[label] = {
            "path": str(path.relative_to(repo)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path, cache),
        }
    fixtures: dict[str, dict[str, Any]] = {}
    for vector in vectors:
        refs = vector.get("fixtures")
        if not isinstance(refs, list) or not refs:
            raise ProbeError(f"vector has no fixture references: {vector['id']}")
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "role", "case_key"}:
                raise ProbeError(f"malformed fixture reference: {vector['id']}")
            if ref["case_key"] != vector["id"] or not SHA256_RE.fullmatch(str(ref["sha256"])):
                raise ProbeError(f"fixture reference identity mismatch: {vector['id']}")
            path = safe_repo_file(repo, str(ref["path"]))
            actual = sha256_file(path, cache)
            if actual != ref["sha256"]:
                raise ProbeError(f"fixture hash mismatch for {ref['path']}")
            fixtures[str(ref["path"])] = {
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
    return {"documents": entries, "fixtures": dict(sorted(fixtures.items()))}


def identity_file(label: str, path: Path, cache: dict[Path, str]) -> dict[str, Any]:
    result: dict[str, Any] = {"label": label, "path": str(path), "available": False}
    if path.is_symlink():
        result["reason"] = "symlink_refused"
    elif path.is_file():
        result.update({
            "available": True,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path, cache),
        })
    else:
        result["reason"] = "missing_regular_file"
    return result


def freeze_identity(repo: Path, cache: dict[Path, str]) -> dict[str, Any]:
    head_result = run_readonly(["git", "rev-parse", "HEAD"], cwd=repo)
    head = head_result["output_utf8"].strip() if head_result["exit_code"] == 0 else None
    files = [
        ("protenix_image", Path("/mnt/BioModStack/apptainer/protenix.sif")),
        ("confornets_image", Path("/mnt/BioModStack/apptainer/confornets-canonical-4c2d104b3d5c7474c5a5799ca7ac0a24ac9c74267693e234b47aae20638e200a.sif")),
        ("frustrampnn_image", Path("/mnt/BioModStack/apptainer/frustrampnn.sif")),
        ("protenix_v2_checkpoint", Path("/mnt/BioModStack/weights/protenix/checkpoint/protenix-v2.pt")),
        ("confornets_openfold3_checkpoint", Path("/mnt/BioModStack/weights/openfold3/of3-p2-155k.pt")),
        ("confornets_toy_state", Path("/mnt/BioModStack/build/confornets/confornets_sandbox/opt/confornets/repo/toy_assets/toy_benchmark/confornet/TM_0287v2_6QV1_B.pt")),
        ("usalign_candidate", Path("/mnt/BioModStack/build/confornets/confornets_sandbox/opt/confornets/packages/USalign")),
    ]
    runtime_files = {label: identity_file(label, path, cache) for label, path in files}
    scripts: dict[str, Any] = {}
    for rel in (
        "scripts/normalize_target_pdb.py",
        "scripts/prep_confornets_request.py",
        "scripts/run_confornets_inference.py",
        "scripts/run_protenix_inference.py",
        "scripts/probes/conformational_mapping/adjudicate_phase0_contracts.py",
        "scripts/probes/conformational_mapping/probe_phase0_runtime.py",
        "scripts/probes/conformational_mapping/validate_phase0_vectors.py",
        "scripts/probes/conformational_mapping/verify_phase0_contract_report.py",
        "scripts/probes/conformational_mapping/verify_protenix_layout_report.py",
        "modules/protenix.nf",
        "modules/confornets.nf",
        "modules/frustrampnn.nf",
        "nextflow.config",
    ):
        path = repo / rel
        scripts[rel] = identity_file(rel, path, cache)
    embedded = run_readonly([
        "apptainer", "exec", str(files[2][1]),
        "sha256sum", "/opt/frustrampnn_weights/megascale.ckpt",
    ])
    match = re.search(r"(?m)^([0-9a-f]{64})\s+", embedded["output_utf8"])
    frustrampnn_checkpoint = {
        "label": "frustrampnn_megascale_checkpoint",
        "path": "container:/opt/frustrampnn_weights/megascale.ckpt",
        "available": embedded["exit_code"] == 0 and match is not None,
        "sha256": match.group(1) if match else None,
        "authentication_command": embedded["command"],
        "authentication_exit_code": embedded["exit_code"],
        "authentication_output_sha256": embedded["output_sha256"],
    }
    return {
        "repository": {"head": head, "head_command_exit_code": head_result["exit_code"]},
        "runtime_files": runtime_files,
        "frustrampnn_checkpoint": frustrampnn_checkpoint,
        "source_files": scripts,
        "hash_semantics": "sha256 of live bytes, computed once per unique resolved regular file",
    }


def contained_observation(root: Path, relative: str) -> Path | None:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return resolved


def first_file(root: Path, candidates: Iterable[str]) -> tuple[str, Path] | None:
    for rel in candidates:
        path = contained_observation(root, rel)
        if path is not None and path.is_file():
            return rel, path
    return None


def collect_tree(root: Path, paths: Iterable[Path], cache: dict[Path, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[Path] = set()
    root_resolved = root.resolve(strict=True)
    for supplied in paths:
        candidates = [supplied]
        if supplied.is_dir():
            candidates = sorted(p for p in supplied.rglob("*") if p.is_file() and not p.is_symlink())
        for path in candidates:
            if path in seen or path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            try:
                rel = resolved.relative_to(root_resolved)
            except ValueError:
                continue
            seen.add(path)
            result.append({
                "path": rel.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path, cache),
            })
    return sorted(result, key=lambda row: row["path"])


def status(
    runtime_status: str,
    tier: str,
    gate: str,
    rationale: str,
    refs: list[Path] | None = None,
) -> dict[str, Any]:
    if gate not in {"PASS", "STOP"}:
        raise ProbeError("invalid gate effect")
    if gate == "PASS" and tier not in {"fresh authenticated", "historical authenticated"}:
        raise ProbeError("only authenticated evidence may pass")
    return {
        "runtime_status": runtime_status,
        "evidence_tier": tier,
        "gate_effect": gate,
        "rationale": rationale,
        "refs": refs or [],
    }


def meta_exit(meta: dict[str, str]) -> int | None:
    raw = meta.get("exit_code")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def classify_protenix_layout(obs: Path, identity: dict[str, Any]) -> dict[str, Any]:
    base = contained_observation(obs, "runtime_inventory/protenix_layout_attempt2")
    if base is None or not base.is_dir():
        return status("unmeasured", "blocked", "STOP", "authenticated Protenix layout observation is unavailable")
    meta_path = base / "run.meta"
    log_path = base / "stdout_stderr.log"
    output = base / "output"
    refs = [p for p in (meta_path, log_path, output) if p.exists()]
    try:
        meta = parse_meta(meta_path)
    except ProbeError as exc:
        return status("blocked", "blocked", "STOP", str(exc), refs)
    image_identity = identity["runtime_files"]["protenix_image"]
    checkpoint_identity = identity["runtime_files"]["protenix_v2_checkpoint"]
    live_image = image_identity.get("sha256")
    live_checkpoint = checkpoint_identity.get("sha256")
    if image_identity.get("available") is not True or checkpoint_identity.get("available") is not True or not SHA256_RE.fullmatch(str(live_image)) or not SHA256_RE.fullmatch(str(live_checkpoint)):
        return status("blocked", "blocked", "STOP", "Protenix runtime identities are unavailable or malformed", refs)
    if meta_exit(meta) != 0 or meta.get("head") != identity["repository"]["head"]:
        return status("blocked", "blocked", "STOP", "Protenix run exit code or repository HEAD is not authenticated", refs)
    if meta.get("image_sha256") != live_image or meta.get("v2_checkpoint_sha256") != live_checkpoint:
        return status("blocked", "blocked", "STOP", "Protenix image/checkpoint identity mismatch", refs)
    if not output.is_dir() or output.is_symlink():
        return status("blocked", "blocked", "STOP", "Protenix output tree is unavailable or symlinked", refs)
    cifs = sorted(output.rglob("*_sample_*.cif"))
    confidences = sorted(output.rglob("*_summary_confidence_sample_*.json"))
    full_data = sorted(output.rglob("*_full_data_sample_*.json"))
    if len(cifs) != 25 or len(confidences) != 25 or len(full_data) != 25:
        return status("blocked", "blocked", "STOP", "Protenix tree is not exactly 25 CIF + 25 confidence + 25 full-data sidecars", refs)
    if any(not p.is_file() or p.is_symlink() or p.stat().st_size == 0 for p in cifs + confidences + full_data):
        return status("blocked", "blocked", "STOP", "Protenix output contains missing, empty, or symlinked artifacts", refs)
    return status("passed", "fresh authenticated", "PASS", "fresh rc=0 run has exact 5-seed x 5-sample CIF tree and mandatory sidecars with matching live identities", refs)


def classify_composition(vector_id: str, obs: Path, identity: dict[str, Any]) -> dict[str, Any]:
    base: Path | None = None
    for relative in (
        f"runtime_inventory/protenix_composition/{vector_id}",
        f"protenix_composition/{vector_id}",
    ):
        candidate = contained_observation(obs, relative)
        if candidate is not None and candidate.is_dir():
            base = candidate
            break
    if base is None:
        return status("unmeasured", "contract-only", "STOP", "no matching authenticated runtime composition output exists")
    report_path = base / "authenticated_report_v3.json"
    if not report_path.is_file():
        report_path = base / "authenticated_report_v2.json"
    if not report_path.is_file():
        report_path = base / "authenticated_report.json"
    if not report_path.is_file():
        report_path = base / "report.json"
    try:
        report = load_json(report_path)
    except ProbeError as exc:
        return status("blocked", "blocked", "STOP", str(exc), [base])
    if not isinstance(report, dict) or report.get("vector_id") != vector_id or report.get("result") != "pass":
        return status("blocked", "blocked", "STOP", "composition report identity/result mismatch", [base])
    image_identity = identity["runtime_files"]["protenix_image"]
    checkpoint_identity = identity["runtime_files"]["protenix_v2_checkpoint"]
    if image_identity.get("available") is not True or checkpoint_identity.get("available") is not True or not SHA256_RE.fullmatch(str(image_identity.get("sha256"))) or not SHA256_RE.fullmatch(str(checkpoint_identity.get("sha256"))):
        return status("blocked", "blocked", "STOP", "composition runtime identities are unavailable or malformed", [base])
    hashes = report.get("runtime_hashes")
    if not isinstance(hashes, dict) or hashes.get("protenix_image") != image_identity.get("sha256") or hashes.get("protenix_v2_checkpoint") != checkpoint_identity.get("sha256"):
        return status("blocked", "blocked", "STOP", "composition runtime identities are missing or mismatched", [base])
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
        return status("blocked", "blocked", "STOP", "composition report checks are missing or not all true", [base])
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) < 3:
        return status("blocked", "blocked", "STOP", "composition report does not authenticate at least three artifacts", [base])
    refs = [base]
    obs_resolved = obs.resolve(strict=True)
    seen: set[Path] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256", "bytes", "role"}:
            return status("blocked", "blocked", "STOP", "composition artifact record is malformed", refs)
        raw_path = artifact.get("path")
        expected_hash = artifact.get("sha256")
        expected_bytes = artifact.get("bytes")
        if not isinstance(raw_path, str) or not SHA256_RE.fullmatch(str(expected_hash)) or not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes <= 0:
            return status("blocked", "blocked", "STOP", "composition artifact identity is malformed", refs)
        path = Path(raw_path)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(obs_resolved)
        except (OSError, ValueError):
            return status("blocked", "blocked", "STOP", "composition artifact escapes the observation root or is unavailable", refs)
        if path.is_symlink() or not path.is_file() or resolved in seen:
            return status("blocked", "blocked", "STOP", "composition artifact is a symlink, non-file, or duplicate", refs)
        seen.add(resolved)
        if path.stat().st_size != expected_bytes or sha256_file(path, {}) != expected_hash:
            return status("blocked", "blocked", "STOP", "composition artifact bytes/hash mismatch", refs + [path])
        if artifact.get("role") == "predicted_cif" and vector_id in {"P0-PROTENIX-COMPOSITION-006", "P0-PROTENIX-COMPOSITION-007"}:
            cif_text = path.read_text(encoding="utf-8")
            if vector_id.endswith("006") and ("ZN" not in cif_text or "_atom_site." not in cif_text):
                return status("blocked", "blocked", "STOP", "ion CIF does not preserve ZN atom-site content", refs + [path])
            if vector_id.endswith("007") and not all(token in cif_text for token in ("SEP", "NAG", "_struct_conn.", "SG", "C1")):
                return status("blocked", "blocked", "STOP", "modification+covalent CIF lacks SEP/NAG/struct_conn endpoints", refs + [path])
        refs.append(path)
    return status("passed", "fresh authenticated", "PASS", "matching composition report authenticates checks, artifacts, and live runtime identities", refs)


def classify_confornets(vector_id: str, obs: Path, identity: dict[str, Any], repo: Path) -> dict[str, Any]:
    task = {
        "P0-CONFORNETS-LAYOUT-001": "diversity",
        "P0-CONFORNETS-LAYOUT-002": "mse",
        "P0-CONFORNETS-LAYOUT-003": "transfer",
    }[vector_id]
    expected_counts = {"diversity": 8, "mse": 4, "transfer": 4}
    directory_names = {"diversity": "diversity_retry1", "mse": "mse", "transfer": "transfer"}
    fixed_root = contained_observation(obs, "runtime_inventory/confornets_exact")
    if fixed_root is None:
        return status("unmeasured", "blocked", "STOP", "fresh ConforNets evidence root is unavailable")
    report_path = fixed_root / "validation_report.json"
    task_dir = fixed_root / directory_names[task]
    refs = [report_path, task_dir / "command.txt", task_dir / "run.meta", task_dir / "stdout_stderr.log"]
    try:
        report = load_json(report_path)
    except ProbeError as exc:
        return status("blocked", "blocked", "STOP", str(exc), refs)
    task_report = report.get("tasks", {}).get(task, {}) if isinstance(report, dict) else {}
    fixture = load_json(repo / "platform/api/tests/fixtures/conformational_mapping/phase_0_vectors/confornets_cases.json")
    fixture_case = next(case for case in fixture["cases"] if case["case_key"] == vector_id)
    expected_coordinates = {
        (run, step, confornet, sample, fixture_case.get("reference_id"))
        for run in fixture_case["runs"]
        for step in fixture_case["saved_steps"]
        for confornet in fixture_case["confornet_indices"]
        for sample in fixture_case["sample_indices"]
    }
    image_identity = identity["runtime_files"]["confornets_image"]
    checkpoint_identity = identity["runtime_files"]["confornets_openfold3_checkpoint"]
    expected_image = image_identity.get("sha256")
    expected_checkpoint = checkpoint_identity.get("sha256")
    runtime_identity_available = (
        image_identity.get("available") is True
        and checkpoint_identity.get("available") is True
        and SHA256_RE.fullmatch(str(expected_image)) is not None
        and SHA256_RE.fullmatch(str(expected_checkpoint)) is not None
    )
    artifacts = task_report.get("artifacts", []) if isinstance(task_report, dict) else []
    artifact_refs: list[Path] = []
    artifacts_ok = isinstance(artifacts, list) and len(artifacts) == expected_counts[task]
    if artifacts_ok:
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str) or not artifact["path"].startswith("/evidence/") or not SHA256_RE.fullmatch(str(artifact.get("sha256"))) or not isinstance(artifact.get("atom_count"), int) or artifact["atom_count"] <= 0:
                artifacts_ok = False
                break
            actual = fixed_root / artifact["path"].removeprefix("/evidence/")
            try:
                actual.resolve(strict=True).relative_to(fixed_root.resolve(strict=True))
            except (OSError, ValueError):
                artifacts_ok = False
                break
            if not actual.is_file() or actual.is_symlink() or sha256_file(actual, {}) != artifact["sha256"]:
                artifacts_ok = False
                break
            try:
                cif_text = actual.read_text(encoding="utf-8")
                if "_atom_site." not in cif_text or "\nATOM " not in cif_text:
                    raise ValueError("CIF lacks atom_site records")
            except (OSError, UnicodeError, ValueError):
                artifacts_ok = False
                break
            artifact_refs.append(actual)
    ledger_path = task_dir / "coordinate_ledger.jsonl"
    observed_coordinates: set[tuple[int, int, int, int, str | None]] = set()
    ledger_ok = ledger_path.is_file() and not ledger_path.is_symlink()
    if ledger_ok:
        try:
            for line in ledger_path.read_text(encoding="utf-8").splitlines():
                coordinate = json.loads(line)["coordinates"]
                observed_coordinates.add((
                    coordinate["run_index"], coordinate["saved_step"],
                    coordinate["confornet_index"], coordinate["sample_index"],
                    coordinate.get("reference_id"),
                ))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            ledger_ok = False
    checks = {
        "runtime_identity_available": runtime_identity_available,
        "schema": isinstance(report, dict) and report.get("schema") == "confornets-fresh-cif-validation-v1",
        "container_identity": isinstance(report, dict) and report.get("container_sha256") == expected_image,
        "checkpoint_identity": isinstance(report, dict) and report.get("checkpoint_sha256") == expected_checkpoint,
        "run_exit_zero": isinstance(task_report, dict) and task_report.get("run_exit_zero") is True,
        "log_done": isinstance(task_report, dict) and task_report.get("log_done") is True,
        "all_cifs_parsed": isinstance(task_report, dict) and task_report.get("all_cifs_parsed") is True,
        "exact_cif_count": isinstance(task_report, dict) and task_report.get("cif_count") == expected_counts[task],
        "artifact_hashes": artifacts_ok,
        "exact_registry_coordinate_set": ledger_ok and observed_coordinates == expected_coordinates,
    }
    if all(checks.values()):
        return status("passed", "fresh authenticated", "PASS", f"fresh {task} run produced {expected_counts[task]} parsed CIFs with authenticated runtime identities and artifact hashes", refs + artifact_refs)
    failed = [name for name, passed in checks.items() if not passed]
    return status("blocked", "blocked", "STOP", f"fresh {task} evidence failed checks: {', '.join(failed)}", refs + artifact_refs)


def classify_frustrampnn(vector_id: str, obs: Path, identity: dict[str, Any]) -> dict[str, Any]:
    if vector_id == "P0-FRUSTRAMPNN-001":
        checkpoint = identity["frustrampnn_checkpoint"]
        if checkpoint.get("available") and SHA256_RE.fullmatch(str(checkpoint.get("sha256"))):
            return status("passed", "fresh authenticated", "PASS", "embedded megascale checkpoint is present and live SHA-256 authenticated")
        return status("blocked", "blocked", "STOP", "FrustraMPNN checkpoint identity is unavailable")
    if vector_id == "P0-FRUSTRAMPNN-003":
        hit = first_file(obs, (
            "frustrampnn_score_shape.json",
            "runtime_inventory/frustrampnn_score_shape.json",
            "runtime_inventory/frustrampnn_historical_csv_validation.json",
        ))
        if hit:
            _, path = hit
            try:
                report = load_json(path)
            except ProbeError as exc:
                return status("blocked", "blocked", "STOP", str(exc), [path])
            source_ok = False
            evidence_tier = "historical authenticated"
            source: Path | None = None
            if isinstance(report, dict) and isinstance(report.get("source"), str) and SHA256_RE.fullmatch(str(report.get("source_sha256"))):
                source = Path(report["source"])
                try:
                    resolved_source = source.resolve(strict=True)
                    try:
                        resolved_source.relative_to(obs.resolve(strict=True))
                        evidence_tier = "fresh authenticated"
                    except ValueError:
                        resolved_source.relative_to(Path("/mnt/BioModStack/bms_results").resolve(strict=True))
                    source_ok = source.is_file() and not source.is_symlink() and sha256_file(source, {}) == report["source_sha256"]
                except (OSError, ValueError):
                    source_ok = False
            if isinstance(report, dict) and source_ok and report.get("amino_acid_order") == "ACDEFGHIKLMNPQRSTVWY" and not report.get("wrong_order_groups") and report.get("bad_group_count") == 0 and report.get("exact_20_score_groups", 0) > 0 and report.get("row_count") == 20 * report.get("residue_group_count", -1) and not report.get("invalid_amino_acid_rows") and not report.get("nonfinite_score_rows"):
                refs = [path]
                if source is not None:
                    refs.append(source)
                return status("passed", evidence_tier, "PASS", "CSV authenticates exact ordered 20 finite amino-acid score slots per residue and its source hash", refs)
        return status("unmeasured", "contract-only", "STOP", "no authenticated 20-slot score-shape observation exists")
    if vector_id == "P0-FRUSTRAMPNN-002":
        return status("unmeasured", "source-only", "STOP", "selected-chain/remapping semantics were not measured")
    return status("unmeasured", "source-only", "STOP", "malformed/non-finite row rejection was not measured")


def classify_normalize(obs: Path, identity: dict[str, Any]) -> dict[str, Any]:
    hit = first_file(obs, (
        "runtime_inventory/normalization_fixed/report.json",
        "normalization_synthetic/report.json",
        "runtime_inventory/normalization_synthetic/report.json",
    ))
    if not hit:
        return status("unmeasured", "blocked", "STOP", "normalization observation is unavailable")
    _, path = hit
    try:
        report = load_json(path)
    except ProbeError as exc:
        return status("blocked", "blocked", "STOP", str(exc), [path])
    checks = report.get("checks") if isinstance(report, dict) else None
    raw_artifacts = report.get("artifacts") if isinstance(report, dict) else None
    artifacts: list[dict[str, Any]] = raw_artifacts if isinstance(raw_artifacts, list) and all(isinstance(item, dict) for item in raw_artifacts) else []
    script_identity = identity["source_files"]["scripts/normalize_target_pdb.py"]
    live_script = script_identity.get("sha256")
    valid = (
        script_identity.get("available") is True
        and SHA256_RE.fullmatch(str(live_script)) is not None
        and isinstance(report, dict)
        and report.get("result") == "pass"
        and isinstance(checks, dict)
        and len(checks) >= 15
        and all(value is True for value in checks.values())
        and report.get("selected_model") == 1
        and report.get("selected_altloc") == "A"
        and report.get("auth_chains_retained") == ["A", "C"]
        and report.get("label_asym_ids_retained") == ["CHAIN_LONG_COPY1", "CHAIN_LONG_COPY2"]
        and report.get("instance_ids_retained") == ["copy1", "copy2"]
        and report.get("altlocs_retained") == []
        and report.get("hydrogen_records_retained") == 0
        and report.get("water_records_retained") == 0
        and report.get("hetero_records_retained") == 0
        and report.get("normalizer_sha256") == live_script
        and isinstance(artifacts, list)
        and len(artifacts) >= 4
    )
    refs = [path]
    if valid:
        for artifact in artifacts:
            raw = artifact.get("path") if isinstance(artifact, dict) else None
            expected = artifact.get("sha256") if isinstance(artifact, dict) else None
            if not isinstance(raw, str) or not SHA256_RE.fullmatch(str(expected)):
                valid = False
                continue
            candidate = Path(raw)
            if raw == report.get("normalizer"):
                valid = valid and candidate.is_file() and not candidate.is_symlink() and expected == live_script
                continue
            try:
                candidate.resolve(strict=True).relative_to(obs.resolve(strict=True))
                valid = valid and candidate.is_file() and not candidate.is_symlink() and sha256_file(candidate, {}) == expected
                refs.append(candidate)
            except (OSError, ValueError):
                valid = False
    if valid:
        return status("passed", "fresh authenticated", "PASS", "selected model, both mapped instances, multi-character source asym IDs, insertion-coded residues, and selected altloc survive while unselected model/chain/altloc, hydrogens, water, and hetero records are removed", refs)
    return status("blocked", "blocked", "STOP", "normalization report, live normalizer hash, preservation checks, removal checks, or artifact hashes are incomplete/mismatched", [path])


def classify_usalign(obs: Path, identity: dict[str, Any]) -> dict[str, Any]:
    hit = first_file(obs, (
        "usalign_probe/report.json",
        "runtime_inventory/usalign_probe/report.json",
        "runtime_inventory/usalign/report.json",
    ))
    if not hit:
        return status("unmeasured", "blocked", "STOP", "USalign report is unavailable")
    _, path = hit
    try:
        report = load_json(path)
    except ProbeError as exc:
        return status("blocked", "blocked", "STOP", str(exc), [path])
    usalign_identity = identity["runtime_files"]["usalign_candidate"]
    live = usalign_identity.get("sha256")
    raw_cases = report.get("cases") if isinstance(report, dict) else None
    cases: list[dict[str, Any]] = raw_cases if isinstance(raw_cases, list) and all(isinstance(case, dict) for case in raw_cases) else []
    valid = (
        usalign_identity.get("available") is True
        and SHA256_RE.fullmatch(str(live)) is not None
        and isinstance(report, dict)
        and report.get("result") == "pass"
        and report.get("binary_sha256") == live
        and len(cases) >= 1
    )
    if valid:
        for case in cases:
            scores = case.get("normalized_tm_scores")
            valid = valid and case.get("exit_code") == 0 and isinstance(scores, list) and len(scores) == 2
            if valid:
                valid = all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (scores or []))
            for path_key, hash_key in (("input_a", "input_a_sha256"), ("input_b", "input_b_sha256"), ("log", "log_sha256")):
                raw = case.get(path_key)
                expected = case.get(hash_key)
                if not isinstance(raw, str) or not SHA256_RE.fullmatch(str(expected)):
                    valid = False
                    continue
                candidate = Path(raw)
                try:
                    valid = valid and candidate.is_file() and not candidate.is_symlink() and sha256_file(candidate, {}) == expected
                except OSError:
                    valid = False
    if valid:
        refs = [path]
        for case in cases:
            raw = case.get("log")
            if isinstance(raw, str):
                candidate = Path(raw)
                if candidate.is_file() and not candidate.is_symlink():
                    refs.append(candidate)
        return status("passed", "fresh authenticated", "PASS", "live USalign candidate hash matches and every rc=0 case has exactly two finite normalized TM scores", refs)
    return status("blocked", "blocked", "STOP", "USalign candidate hash, exit status, or parsed TM-score pair is missing/mismatched", [path])


def classify_baseline(obs: Path, repo: Path) -> dict[str, Any]:
    hit = first_file(obs, (
        "baselines_final_attempt3/summary_v2.json",
        "baselines_rerun/summary_v2.json",
        "baselines/summary.json",
    ))
    if not hit:
        return status("unmeasured", "blocked", "STOP", "baseline aggregate is unavailable")
    _, path = hit
    try:
        report = load_json(path)
    except ProbeError as exc:
        return status("blocked", "blocked", "STOP", str(exc), [path])
    schema_v2 = isinstance(report, dict) and report.get("schema") == "phase0-baselines-authenticated-v2"
    rows = report.get("commands" if schema_v2 else "baselines") if isinstance(report, dict) else None
    if not isinstance(rows, list) or len(rows) != 4 or not all(isinstance(row, dict) for row in rows):
        return status("blocked", "blocked", "STOP", "baseline report does not contain exactly four command objects", [path])
    fixture = load_json(repo / "platform/api/tests/fixtures/conformational_mapping/phase_0_vectors/baseline.json")
    expected_commands = fixture.get("commands") if isinstance(fixture, dict) else None
    def normalize_command(command: Any) -> str:
        return " ".join(str(command).replace("\\\n", " ").split())
    command_key = "frozen_command" if schema_v2 else "command"
    if not isinstance(expected_commands, list) or [normalize_command(row.get(command_key)) for row in rows] != [normalize_command(command) for command in expected_commands]:
        return status("blocked", "blocked", "STOP", "baseline command strings do not match the frozen fixture", [path])
    if schema_v2:
        valid = report.get("all_passed") is True and isinstance(report.get("total_tests"), int) and report["total_tests"] > 0
        refs = [path]
        for row in rows:
            counts = row.get("counts")
            artifacts = row.get("artifacts")
            valid = valid and row.get("passed") is True and row.get("exit_code") == 0
            valid = valid and isinstance(counts, dict) and isinstance(counts.get("tests"), int) and counts["tests"] > 0
            valid = valid and counts.get("failures") == 0 and counts.get("errors") == 0
            valid = valid and isinstance(artifacts, list) and len(artifacts) >= 2
            if isinstance(artifacts, list):
                for artifact in artifacts:
                    if not isinstance(artifact, dict):
                        valid = False
                        continue
                    raw = artifact.get("path")
                    expected = artifact.get("sha256")
                    if not isinstance(raw, str) or not SHA256_RE.fullmatch(str(expected)):
                        valid = False
                        continue
                    candidate = Path(raw)
                    try:
                        candidate.resolve(strict=True).relative_to(obs.resolve(strict=True))
                        valid = valid and candidate.is_file() and not candidate.is_symlink() and sha256_file(candidate, {}) == expected
                        refs.append(candidate)
                    except (OSError, ValueError):
                        valid = False
        if not valid:
            return status("blocked", "blocked", "STOP", "authenticated baseline counts or artifact hashes failed validation", [path])
        return status("passed", "fresh authenticated", "PASS", f"all four frozen baseline suites passed ({report['total_tests']} test executions)", refs)
    nonzero = [row.get("id") for row in rows if not isinstance(row, dict) or row.get("exit_code") != 0]
    if nonzero:
        return status("observed-fail", "observed-fail", "STOP", f"baseline command(s) had nonzero rc: {', '.join(map(str, nonzero))}", [path])
    return status("passed", "fresh authenticated", "PASS", "all four exact baseline commands returned rc=0", [path])


def classify(vector: dict[str, Any], obs: Path, identity: dict[str, Any], repo: Path) -> dict[str, Any]:
    vector_id = vector["id"]
    family = vector["family"]
    if family in {"complex-positive", "complex-negative", "confornets-negative", "defaults", "frustrampnn"}:
        return classify_contract_report(vector_id, obs, repo, identity)

    if family in {"complex-positive", "complex-negative"}:
        return status("unmeasured", "contract-only", "STOP", "runtime complex converter/admission probe does not yet exist")
    if family == "protenix-layout":
        return classify_layout_report(obs, identity)
    if family == "protenix-composition":
        return classify_composition(vector_id, obs, identity)
    if family == "confornets-layout":
        return classify_confornets(vector_id, obs, identity, repo)
    if family == "confornets-negative":
        return status("unmeasured", "source-only", "STOP", "negative admission/rejection behavior is source-described but not measured")
    if family == "defaults":
        return status("unmeasured", "source-only", "STOP", "no authenticated effective runtime configuration proves this default")
    if family == "frustrampnn":
        return classify_frustrampnn(vector_id, obs, identity)
    if family == "normalize":
        return classify_normalize(obs, identity)
    if family == "usalign":
        return classify_usalign(obs, identity)
    if family == "baseline":
        return classify_baseline(obs, repo)
    return status("unmeasured", "blocked", "STOP", f"unsupported vector family: {family}")


def preflight_output(output: Path, vectors: list[dict[str, Any]]) -> None:
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise ProbeError("output must be a real directory or an absent path")
    for vector in vectors:
        path = output / vector["id"]
        if path.exists() or path.is_symlink():
            raise ProbeError(f"refusing pre-existing per-vector output directory/path: {path}")


def compile_evidence(vectors_path: Path, output: Path, observations: Path | None) -> int:
    script = Path(__file__).resolve(strict=True)
    repo = script.parents[3]
    vectors_path = vectors_path.resolve(strict=True)
    try:
        vectors_path.relative_to(repo.resolve(strict=True))
    except ValueError as exc:
        raise ProbeError("--vectors must be a contained repository file") from exc
    registry_validation = validate_registry(repo, vectors_path)
    registry = load_json(vectors_path)
    vectors = vector_inventory(registry)
    preflight_output(output, vectors)

    output.mkdir(parents=True, exist_ok=True)
    observations = observations or (output / "supporting_observations")
    if observations.exists() and (observations.is_symlink() or not observations.is_dir()):
        raise ProbeError("observation root must be a real directory when present")

    cache: dict[Path, str] = {}
    inputs = freeze_inputs(repo, vectors_path, vectors, cache)
    identity = freeze_identity(repo, cache)
    resources = {
        "nvidia_smi": run_readonly(["nvidia-smi"]),
        "read_only": True,
    }
    validation_record = dict(registry_validation)
    validation_record.pop("output_utf8", None)

    documents: dict[str, dict[str, bytes]] = {}
    pass_count = 0
    stop_count = 0
    summary_rows: list[dict[str, str]] = []
    for vector in vectors:
        result = classify(vector, observations, identity, repo)
        refs = result.pop("refs")
        git_report = first_file(observations, ("git_integrity/report.json",))
        if git_report:
            # Strict-load it so malformed global provenance is never silently retained.
            load_json(git_report[1])
            refs.append(git_report[1])
        if vector["family"] in {"confornets-layout", "confornets-negative"}:
            inventory = first_file(observations, ("confornets-readonly-inventory.txt",))
            if inventory:
                refs.append(inventory[1])
        tree = collect_tree(observations, refs, cache) if observations.exists() else []
        output_hashes = {row["path"]: row["sha256"] for row in tree}
        observed_disposition = (
            vector["expected_disposition"]
            if result["gate_effect"] == "PASS"
            else ("runtime_error" if result["runtime_status"] == "observed-fail" else "unsupported")
        )
        summary_rows.append({
            "id": vector["id"],
            "runtime_status": "measured",
            "observed_disposition": observed_disposition,
            "evidence_subdirectory": vector["id"],
        })
        if result["gate_effect"] == "PASS":
            pass_count += 1
        else:
            stop_count += 1
        per_vector = {
            "command.json": {"vector_id": vector["id"], "command": vector["probe_command"]},
            "input_hashes.json": inputs,
            "output_hashes.json": {"observation_sha256": output_hashes},
            "artifact_tree.json": {"observation_root": str(observations), "files": tree},
            "runtime_identity.json": identity,
            "resources.json": {"resources": resources, "result": result, "registry_validation": validation_record},
            "exit_status.json": {"vector_id": vector["id"], "exit_code": 0 if result["gate_effect"] == "PASS" else 1},
            "disposition.json": {"vector_id": vector["id"], "observed_disposition": observed_disposition},
        }
        if set(per_vector) != set(REQUIRED_FILES):
            raise ProbeError(f"internal output inventory error for {vector['id']}")
        documents[vector["id"]] = {name: dump_json(per_vector[name]) for name in REQUIRED_FILES}

    temp = output / f".phase0-compiler-{os.getpid()}"
    if temp.exists() or temp.is_symlink():
        raise ProbeError(f"temporary compiler path already exists: {temp}")
    try:
        temp.mkdir(mode=0o700)
        for vector in vectors:
            vector_id = vector["id"]
            staging = temp / vector_id
            staging.mkdir()
            for name in REQUIRED_FILES:
                (staging / name).write_bytes(documents[vector_id][name])
            if set(p.name for p in staging.iterdir()) != set(REQUIRED_FILES):
                raise ProbeError(f"staging inventory mismatch for {vector_id}")
        # Recheck immediately before publication, then atomically publish each directory.
        preflight_output(output, vectors)
        for vector in vectors:
            (temp / vector["id"]).replace(output / vector["id"])
    finally:
        if temp.exists():
            shutil.rmtree(temp)

    runtime_doc = {
        "schema_name": "cm_phase0_runtime_evidence",
        "schema_version": 1,
        "run_id": output.name,
        "registry_sha256": registry["registry_sha256"],
        "definitions_sha256": registry["definitions"]["sha256"],
        "vector_results": summary_rows,
    }
    (output / "runtime_evidence.json").write_bytes(dump_json(runtime_doc))
    ledger_rows = []
    for evidence_file in sorted(path for path in output.rglob("*") if path.is_file()):
        if evidence_file.is_symlink():
            raise ProbeError(f"symlink forbidden in compiled evidence: {evidence_file}")
        ledger_rows.append({"path": evidence_file.relative_to(output).as_posix(), "sha256": sha256_file(evidence_file, {})})
    ledger = {
        "schema_name": "cm_phase0_runtime_evidence_hashes",
        "schema_version": 1,
        "registry_sha256": registry["registry_sha256"],
        "definitions_sha256": registry["definitions"]["sha256"],
        "files": ledger_rows,
    }
    (output / "runtime_evidence_hashes.json").write_bytes(dump_json(ledger))

    gate_label = "PASS" if stop_count == 0 else "STOP"
    print(
        f"{gate_label}: compiled 53 fail-closed Phase 0 evidence bundles "
        f"({pass_count} PASS, {stop_count} STOP) under {output}"
    )
    return 0 if stop_count == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile deterministic, fail-closed Phase 0 runtime evidence (stdlib only; no inference)."
    )
    parser.add_argument("--vectors", required=True, type=Path, help="validated Phase 0 vector registry JSON")
    parser.add_argument("--output", required=True, type=Path, help="new evidence run root; vector subdirectories must not exist")
    parser.add_argument(
        "--observations-root",
        type=Path,
        help="read-only captured observations (default: <output>/supporting_observations)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return compile_evidence(args.vectors, args.output.absolute(), args.observations_root.absolute() if args.observations_root else None)
    except ProbeError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"STOP: evidence compiler I/O failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
