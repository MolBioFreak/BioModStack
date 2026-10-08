from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping, Sequence

from .analysis_runtime_probe import (
    QUALIFICATION_SCHEMA,
    REQUIRED_FORMAT_PAIRS,
    build_apptainer_probe_command,
    load_fixture_catalog,
)

RUNTIME_SCHEMA = "bms.md.analysis-runtime.v1"


class PromotionError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    raw_path = Path(path).expanduser()
    try:
        leaf_stat = raw_path.lstat()
    except OSError as exc:
        raise PromotionError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(leaf_stat.st_mode):
        raise PromotionError(f"{label} must be a nonempty regular file")
    try:
        resolved = raw_path.resolve(strict=True)
        file_stat = resolved.stat()
    except OSError as exc:
        raise PromotionError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size <= 0:
        raise PromotionError(f"{label} must be a nonempty regular file")
    return resolved


def _run(command_runner: Callable[..., Any], command: list[str], label: str) -> Any:
    result = command_runner(command, text=True, capture_output=True, check=False)
    if getattr(result, "returncode", 1) != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "unknown error").strip()
        raise PromotionError(f"{label} failed: {detail}")
    return result


def _load_probe_evidence(
    path: Path,
    *,
    expected_runtime_sha256: str,
    fixtures: Path,
) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PromotionError("runtime qualification evidence is unavailable or invalid") from exc
    fixture_root = Path(fixtures).resolve()
    try:
        catalog = load_fixture_catalog(fixture_root)
    except Exception as exc:
        raise PromotionError("runtime qualification fixture catalog is invalid") from exc
    expected_catalog_sha = _sha256(fixture_root / "fixtures.json")
    records = payload.get("fixtures") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != QUALIFICATION_SCHEMA
        or payload.get("status") != "passed"
        or payload.get("runtime_sif_sha256") != expected_runtime_sha256
        or payload.get("fixture_catalog_sha256") != expected_catalog_sha
        or not isinstance(payload.get("implementation_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["implementation_sha256"])
        or not isinstance(records, list)
        or len(records) != len(catalog["fixtures"])
    ):
        raise PromotionError("runtime qualification evidence is incomplete or does not bind the candidate and fixture catalog")
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("id"), str) or record["id"] in by_id:
            raise PromotionError("runtime qualification fixture results are invalid or duplicated")
        by_id[record["id"]] = record
    if {record.get("format_pair") for record in records} != REQUIRED_FORMAT_PAIRS:
        raise PromotionError("runtime qualification does not cover exactly the required format pairs")
    required_roles = {"md_analysis_report", "md_analysis_timeseries", "md_analysis_residue_metrics"}
    for fixture in catalog["fixtures"]:
        record = by_id.get(fixture["id"])
        outputs = record.get("outputs") if isinstance(record, Mapping) else None
        if (
            not isinstance(record, Mapping)
            or record.get("status") != "passed"
            or record.get("format_pair") != fixture["format_pair"]
            or record.get("input_manifest_sha256") != _sha256(fixture["manifest"])
            or not isinstance(outputs, list)
            or len(outputs) < len(required_roles)
        ):
            raise PromotionError("runtime qualification fixture result is incomplete or stale")
        roles: set[str] = set()
        for output in outputs:
            if (
                not isinstance(output, Mapping)
                or not isinstance(output.get("name"), str)
                or not isinstance(output.get("path"), str)
                or isinstance(output.get("bytes"), bool)
                or not isinstance(output.get("bytes"), int)
                or output["bytes"] <= 0
                or not isinstance(output.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", output["sha256"])
                or not isinstance(output.get("semantic_role"), str)
            ):
                raise PromotionError("runtime qualification output evidence is invalid")
            relative = PurePosixPath(output["path"])
            if relative.is_absolute() or relative.as_posix() != output["path"] or any(part in {"", ".", ".."} for part in relative.parts):
                raise PromotionError("runtime qualification output path is invalid")
            evidence_root = Path(path).resolve().parent
            output_path = evidence_root.joinpath(*relative.parts)
            try:
                output_path.resolve(strict=True).relative_to(evidence_root)
            except (OSError, ValueError) as exc:
                raise PromotionError("runtime qualification output escapes its evidence root") from exc
            verified_output = _regular_file(output_path, "runtime qualification output")
            if verified_output.stat().st_size != output["bytes"] or _sha256(verified_output) != output["sha256"]:
                raise PromotionError("runtime qualification output bytes do not match the evidence")
            roles.add(output["semantic_role"])
        if not required_roles.issubset(roles):
            raise PromotionError("runtime qualification output evidence is missing required semantic roles")
    return payload


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _staged_candidate_copy(candidate: Path, parent: Path) -> Iterator[tuple[Path, str]]:
    descriptor, raw_temporary = tempfile.mkstemp(prefix=".md-analysis-candidate-", suffix=".sif", dir=parent)
    temporary = Path(raw_temporary)
    source_descriptor = -1
    try:
        source_descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise PromotionError("candidate SIF must be a nonempty regular file")
        digest = hashlib.sha256()
        consumed = 0
        with os.fdopen(descriptor, "wb", closefd=True) as target:
            descriptor = -1
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                consumed += len(chunk)
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = os.fstat(source_descriptor)
        if consumed != before.st_size or (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise PromotionError("candidate SIF changed while creating the immutable qualification snapshot")
        _fsync_directory(parent)
        yield temporary, digest.hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
        temporary.unlink(missing_ok=True)


def _publish_runtime_pair(
    staged_sif: Path,
    destination: Path,
    staged_manifest: Path,
    manifest_path: Path,
) -> None:
    transaction = f"{os.getpid()}-{os.urandom(8).hex()}"
    targets = ((staged_sif, destination), (staged_manifest, manifest_path))
    backups: dict[Path, Path | None] = {}
    try:
        for staged, target in targets:
            with staged.open("rb") as handle:
                os.fsync(handle.fileno())
            backup: Path | None = None
            if target.exists():
                backup = target.with_name(f"{target.name}.backup-{transaction}")
                os.link(target, backup)
            backups[target] = backup
        _fsync_directory(destination.parent)
        for staged, target in targets:
            os.replace(staged, target)
        _fsync_directory(destination.parent)
    except BaseException:
        for _, target in reversed(targets):
            backup = backups.get(target)
            if backup is None:
                target.unlink(missing_ok=True)
            elif backup.exists():
                os.rename(backup, target)
        _fsync_directory(destination.parent)
        raise
    finally:
        staged_sif.unlink(missing_ok=True)
        staged_manifest.unlink(missing_ok=True)
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)


def _qualify_and_publish_staged(
    *,
    candidate: Path,
    candidate_hash: str,
    destination: Path,
    definition: Path,
    lockfile: Path,
    fixtures: Path,
    command_runner: Callable[..., Any],
    probe_evidence: Path | None,
) -> Path:
    _run(command_runner, ["apptainer", "test", str(candidate)], "apptainer test")
    apptainer_version_result = _run(command_runner, ["apptainer", "version"], "apptainer version")
    versions_result = _run(
        command_runner,
        [
            "apptainer", "exec", "--cleanenv", str(candidate), "python3", "-c",
            "import importlib.metadata,json,platform,MDAnalysis,numpy,pyarrow; print(json.dumps({'python':platform.python_version(),'MDAnalysis':MDAnalysis.__version__,'numpy':numpy.__version__,'pyarrow':pyarrow.__version__,'jsonschema':importlib.metadata.version('jsonschema')},sort_keys=True))",
        ],
        "analysis runtime version probe",
    )
    if probe_evidence is None:
        with tempfile.TemporaryDirectory(prefix="bms-md-analysis-probe-") as temporary_dir:
            evidence_path = Path(temporary_dir) / "qualification.json"
            repo_root = Path(__file__).resolve().parents[2]
            command = build_apptainer_probe_command(
                image=candidate,
                fixtures=fixtures,
                output=evidence_path,
                repo_root=repo_root,
            )
            _run(command_runner, command, "analysis runtime fixture probe")
            evidence = _load_probe_evidence(
                evidence_path,
                expected_runtime_sha256=candidate_hash,
                fixtures=fixtures,
            )
    else:
        evidence_path = _regular_file(probe_evidence, "runtime probe evidence")
        evidence = _load_probe_evidence(
            evidence_path,
            expected_runtime_sha256=candidate_hash,
            fixtures=fixtures,
        )
    try:
        versions = json.loads((getattr(versions_result, "stdout", "") or "").strip())
    except (ValueError, json.JSONDecodeError):
        versions = {"raw": (getattr(versions_result, "stdout", "") or "").strip()}
    if _sha256(candidate) != candidate_hash:
        raise PromotionError("immutable staged SIF changed during qualification")
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    runtime_manifest = {
        "schema": RUNTIME_SCHEMA,
        "status": "qualified",
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "sif": {"path": str(destination), "bytes": candidate.stat().st_size, "sha256": candidate_hash},
        "definition": {"path": str(definition), "bytes": definition.stat().st_size, "sha256": _sha256(definition)},
        "requirements_lock": {"path": str(lockfile), "bytes": lockfile.stat().st_size, "sha256": _sha256(lockfile)},
        "apptainer": (getattr(apptainer_version_result, "stdout", "") or "").strip(),
        "versions": versions,
        "analysis_implementation_sha256": evidence["implementation_sha256"],
        "qualification": evidence,
    }
    staged_manifest = destination.with_name(
        f".{destination.name}.manifest-stage-{os.getpid()}-{os.urandom(8).hex()}.json"
    )
    _write_json_atomic(staged_manifest, runtime_manifest)
    _publish_runtime_pair(candidate, destination, staged_manifest, manifest_path)
    return manifest_path


def promote_runtime(
    *,
    candidate: Path,
    destination: Path,
    definition: Path,
    lockfile: Path,
    fixtures: Path,
    command_runner: Callable[..., Any] = subprocess.run,
    probe_evidence: Path | None = None,
    replace_existing: bool = False,
) -> Path:
    candidate = _regular_file(candidate, "candidate SIF")
    definition = _regular_file(definition, "analysis definition")
    lockfile = _regular_file(lockfile, "analysis requirements lock")
    fixtures = Path(fixtures).resolve()
    if not fixtures.is_dir():
        raise PromotionError("analysis fixtures are unavailable")
    destination_raw = Path(destination).expanduser()
    destination = destination_raw.parent.resolve() / destination_raw.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not replace_existing:
        raise PromotionError("destination SIF already exists; explicit qualified replacement is required")
    if destination.exists() and (destination.is_symlink() or not destination.is_file()):
        raise PromotionError("existing destination SIF is not a replaceable regular file")

    with _staged_candidate_copy(candidate, destination.parent) as (staged_candidate, candidate_hash):
        return _qualify_and_publish_staged(
            candidate=staged_candidate,
            candidate_hash=candidate_hash,
            destination=destination,
            definition=definition,
            lockfile=lockfile,
            fixtures=fixtures,
            command_runner=command_runner,
            probe_evidence=probe_evidence,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify and atomically promote the BioModStack MDAnalysis SIF")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--lockfile", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--probe-evidence", type=Path)
    parser.add_argument(
        "--replace-existing-qualified",
        action="store_true",
        help="atomically replace an existing regular SIF only after all qualification gates pass",
    )
    args = parser.parse_args(argv)
    try:
        manifest = promote_runtime(
            candidate=args.candidate,
            destination=args.destination,
            definition=args.definition,
            lockfile=args.lockfile,
            fixtures=args.fixtures,
            probe_evidence=args.probe_evidence,
            replace_existing=args.replace_existing_qualified,
        )
    except PromotionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(manifest)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
