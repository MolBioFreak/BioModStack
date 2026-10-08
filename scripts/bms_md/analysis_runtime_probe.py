from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from .analysis import resolve_runtime_sha256, write_analysis_report

FIXTURE_SCHEMA = "bms.md.analysis-fixtures.v1"
QUALIFICATION_SCHEMA = "bms.md.analysis-runtime-qualification.v1"
REQUIRED_FORMAT_PAIRS = frozenset({"gro_xtc", "pdb_dcd"})
MAX_FIXTURES = 8
MAX_FIXTURE_FILE_BYTES = 32 * 1024 * 1024


class FixtureContractError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise FixtureContractError("fixture path is not a contained relative path")
    relative = PurePosixPath(raw)
    if relative.as_posix() != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise FixtureContractError("fixture path is not a contained relative path")
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*relative.parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise FixtureContractError("fixture path is not a contained relative path") from exc
    return candidate


def _verify_record(root: Path, record: Mapping[str, Any]) -> Path:
    size = record.get("bytes")
    expected = record.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FIXTURE_FILE_BYTES:
        raise FixtureContractError("fixture byte count is invalid")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise FixtureContractError("fixture SHA-256 is invalid")
    path = _safe_relative(root, record.get("path"))
    if not path.is_file() or path.is_symlink():
        raise FixtureContractError("fixture is unavailable or not a regular file")
    if path.stat().st_size != size:
        raise FixtureContractError("fixture byte count does not match")
    if _sha256(path) != expected:
        raise FixtureContractError("fixture SHA-256 does not match")
    return path


def load_fixture_catalog(fixtures: Path) -> dict[str, Any]:
    root = Path(fixtures).resolve()
    catalog_path = root / "fixtures.json"
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FixtureContractError("fixture catalog is unavailable or invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != FIXTURE_SCHEMA:
        raise FixtureContractError(f"fixture catalog schema must be {FIXTURE_SCHEMA}")
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        raise FixtureContractError("fixture provenance is required")
    for role in ("documentation", "license"):
        record = provenance.get(role)
        if not isinstance(record, Mapping):
            raise FixtureContractError(f"fixture provenance {role} is missing")
        expected = record.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise FixtureContractError(f"fixture provenance {role} SHA-256 is invalid")
        path = _safe_relative(root, record.get("path"))
        if not path.is_file() or path.is_symlink() or _sha256(path) != expected:
            raise FixtureContractError(f"fixture provenance {role} does not match")
    package = provenance.get("package")
    source_files = provenance.get("source_files")
    if (
        not isinstance(package, Mapping)
        or not isinstance(package.get("canonical_release_url"), str)
        or not package["canonical_release_url"].startswith("https://conda.anaconda.org/conda-forge/")
        or not isinstance(package.get("archive_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", package["archive_sha256"])
        or not isinstance(source_files, Mapping)
        or not source_files
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in source_files.values())
    ):
        raise FixtureContractError("fixture provenance package or source identities are invalid")
    records = payload.get("fixtures")
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_FIXTURES:
        raise FixtureContractError("fixture catalog cardinality is invalid")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_fixture in records:
        if not isinstance(raw_fixture, Mapping):
            raise FixtureContractError("fixture record is invalid")
        fixture_id = raw_fixture.get("id")
        pair = raw_fixture.get("format_pair")
        if not isinstance(fixture_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", fixture_id):
            raise FixtureContractError("fixture id is invalid")
        if fixture_id in seen_ids:
            raise FixtureContractError("fixture ids must be unique")
        seen_ids.add(fixture_id)
        if pair not in REQUIRED_FORMAT_PAIRS:
            raise FixtureContractError("fixture format pair is unsupported")
        manifest = _safe_relative(root, raw_fixture.get("manifest"))
        artifacts = raw_fixture.get("artifacts")
        if not isinstance(artifacts, Mapping) or not artifacts:
            raise FixtureContractError("fixture artifacts are missing")
        verified = {str(name): _verify_record(root, record) for name, record in artifacts.items() if isinstance(record, Mapping)}
        if len(verified) != len(artifacts):
            raise FixtureContractError("fixture artifact record is invalid")
        if not manifest.is_file() or manifest.is_symlink():
            raise FixtureContractError("fixture run manifest is unavailable")
        if manifest not in verified.values():
            raise FixtureContractError("fixture run manifest must be hash-bound in artifacts")
        try:
            from jsonschema import Draft202012Validator

            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            schema_path = Path(__file__).resolve().parents[2] / "schemas" / "md_run_v1.schema.json"
            Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(manifest_payload)
        except Exception as exc:
            raise FixtureContractError("fixture run manifest fails the authoritative schema") from exc
        normalized.append({"id": fixture_id, "format_pair": pair, "manifest": manifest, "artifacts": verified})
    return {"schema": FIXTURE_SCHEMA, "root": root, "fixtures": normalized}


def _verify_output_artifact(output_root: Path, record: Mapping[str, Any]) -> Path:
    try:
        return _verify_record(output_root, record)
    except FixtureContractError as exc:
        raise FixtureContractError(f"analysis output artifact invalid: {exc}") from exc


def qualify_fixture_catalog(
    fixtures: Path,
    output_root: Path,
    *,
    analysis_writer: Callable[..., tuple[Path, bool]] | None = None,
    runtime_sha256: str | None = None,
) -> dict[str, Any]:
    resolved_runtime = resolve_runtime_sha256(runtime_sha256)
    catalog = load_fixture_catalog(fixtures)
    pairs = {record["format_pair"] for record in catalog["fixtures"]}
    if pairs != REQUIRED_FORMAT_PAIRS:
        raise FixtureContractError("qualification requires exactly the GRO+XTC and PDB+DCD format lanes")
    writer = analysis_writer or write_analysis_report
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    implementation_hashes: set[str] = set()
    for fixture in catalog["fixtures"]:
        fixture_output = output_root / fixture["id"]
        fixture_output.mkdir(parents=True, exist_ok=True)
        report_path = fixture_output / "analysis.json"
        written, success = writer(
            fixture["manifest"],
            report_path,
            stride=1,
            max_points=128,
            runtime_sha256=resolved_runtime,
        )
        if not success or Path(written).resolve() != report_path.resolve():
            raise FixtureContractError(f"analysis failed for fixture {fixture['id']}")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise FixtureContractError("analysis report is invalid") from exc
        if report.get("schema") != "bms.md.analysis.v1" or report.get("status") != "completed":
            raise FixtureContractError("analysis report did not complete")
        tool = report.get("tool")
        implementation_sha256 = tool.get("implementation_sha256") if isinstance(tool, Mapping) else None
        if not isinstance(implementation_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", implementation_sha256):
            raise FixtureContractError("analysis report implementation identity is invalid")
        implementation_hashes.add(implementation_sha256)
        sidecar_path = fixture_output / "analysis.artifacts.json"
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise FixtureContractError("analysis artifact sidecar is invalid") from exc
        artifacts = sidecar.get("artifacts") if isinstance(sidecar, Mapping) else None
        if not isinstance(artifacts, Mapping):
            raise FixtureContractError("analysis artifact sidecar has no artifacts")
        roles = {record.get("semantic_role") for record in artifacts.values() if isinstance(record, Mapping)}
        required_roles = {"md_analysis_report", "md_analysis_timeseries", "md_analysis_residue_metrics"}
        if not required_roles.issubset(roles):
            raise FixtureContractError("analysis artifact sidecar is incomplete")
        verified_outputs = []
        for name, record in sorted(artifacts.items()):
            if not isinstance(record, Mapping):
                raise FixtureContractError("analysis artifact sidecar record is invalid")
            path = _verify_output_artifact(fixture_output, record)
            verified_outputs.append({"name": str(name), "path": path.relative_to(output_root.parent).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path), "semantic_role": record.get("semantic_role")})
        results.append({"id": fixture["id"], "format_pair": fixture["format_pair"], "status": "passed", "input_manifest_sha256": _sha256(fixture["manifest"]), "outputs": verified_outputs})
    if len(implementation_hashes) != 1:
        raise FixtureContractError("qualification fixtures did not use one immutable analysis implementation")
    return {
        "schema": QUALIFICATION_SCHEMA,
        "status": "passed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "runtime_sif_sha256": resolved_runtime,
        "implementation_sha256": next(iter(implementation_hashes)),
        "fixture_catalog_sha256": _sha256(Path(fixtures).resolve() / "fixtures.json"),
        "fixtures": results,
    }


def build_apptainer_probe_command(*, image: Path, fixtures: Path, output: Path, repo_root: Path) -> list[str]:
    image = Path(image).resolve()
    fixtures = Path(fixtures).resolve()
    output = Path(output).resolve()
    repo_root = Path(repo_root).resolve()
    return [
        "apptainer", "exec", "--cleanenv", "--containall",
        "--bind", f"{repo_root}:/opt/biomodstack:ro",
        "--bind", f"{fixtures}:/opt/md-fixtures:ro",
        "--bind", f"{output.parent}:/opt/md-evidence:rw",
        "--env", "PYTHONPATH=/opt/biomodstack",
        "--env", "BMS_MD_ANALYSIS_PROBE_INNER=1",
        "--env", f"BMS_MD_ANALYSIS_SIF_SHA256={_sha256(image)}",
        str(image),
        "python3", "-m", "scripts.bms_md.analysis_runtime_probe",
        "--fixtures", "/opt/md-fixtures",
        "--output", f"/opt/md-evidence/{output.name}",
    ]


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify the pinned BioModStack MDAnalysis runtime")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    if args.image:
        if os.environ.get("BMS_MD_ANALYSIS_PROBE_INNER") == "1":
            raise FixtureContractError("nested analysis-runtime probe invocation is forbidden")
        repo_root = Path(__file__).resolve().parents[2]
        command = build_apptainer_probe_command(image=args.image, fixtures=args.fixtures, output=args.output, repo_root=repo_root)
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            sys.stderr.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            return completed.returncode or 1
        return 0
    output_root = args.output_root or args.output.parent / f"{args.output.stem}-artifacts"
    evidence = qualify_fixture_catalog(args.fixtures, output_root)
    _write_json_atomic(args.output, evidence)
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
