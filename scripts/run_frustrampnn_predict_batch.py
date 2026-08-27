#!/usr/bin/env python3
"""Strict product adapter for pinned FrustraMPNN.predict_batch()."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

import pandas as pd


INPUT_SCHEMA_NAME = "frustrampnn_predict_batch_input"
EVIDENCE_SCHEMA_NAME = "frustrampnn_batch_terminal_evidence"
EVIDENCE_FILENAME = "frustrampnn_batch_terminal_evidence_v1.json"
METHOD_IDENTITY = "frustrampnn.FrustraMPNN.predict_batch"
UPSTREAM_SEQUENTIAL_SEMANTICS = (
    "Pinned upstream FrustraMPNN.predict_batch processes pdb_paths sequentially "
    "under one loaded model object, catches each per-structure exception, and omits "
    "failed structures from its returned DataFrame."
)
INPUT_FIELDS = {
    "schema_name",
    "schema_version",
    "checkpoint_path",
    "device",
    "records",
}
RECORD_FIELDS = {
    "ordinal",
    "candidate_id",
    "invocation_id",
    "staged_pdb_path",
    "source_sha256",
}
UPSTREAM_OUTPUT_FIELDS = {
    "frustration_pred",
    "position",
    "wildtype",
    "mutation",
    "pdb",
    "chain",
}
CSV_COLUMNS = [
    "frustration_pred",
    "position",
    "wildtype",
    "mutation",
    "chain",
    "pdb",
]
_SAFE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class BatchAdapterError(RuntimeError):
    """A bounded, classified batch-adapter failure."""

    def __init__(self, failure_code: str, diagnostic: str):
        self.failure_code = failure_code
        self.diagnostic = " ".join(str(diagnostic).split())[:1024] or failure_code
        super().__init__(f"{failure_code}: {self.diagnostic}")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BatchAdapterError("evidence_invalid", "evidence is not canonicalizable") from exc
    return (text + "\n").encode("utf-8")


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BatchAdapterError("input_invalid", "input manifest is unavailable") from exc
    if path.is_symlink() or not path.is_file():
        raise BatchAdapterError("input_invalid", "input manifest must be a regular file")
    if metadata.st_size > _MAX_MANIFEST_BYTES:
        raise BatchAdapterError("input_invalid", "input manifest exceeds its byte bound")
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatchAdapterError("input_invalid", "input manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BatchAdapterError("input_invalid", "input manifest must be an object")
    if payload != _canonical_json(value):
        raise BatchAdapterError("input_invalid", "input manifest must be exact canonical JSON")
    return value, payload


def _nonempty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BatchAdapterError("input_invalid", f"{field} must be a non-empty string")
    return value


def _source_sha256(value: Any, *, index: int) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BatchAdapterError(
            "input_invalid", f"source_sha256 is invalid at index {index}"
        )
    return value


def _validated_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 2 <= len(value) <= 250:
        raise BatchAdapterError("input_invalid", "records must contain 2..250 entries")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BatchAdapterError("input_invalid", f"record {index} must be an object")
        if set(item) != RECORD_FIELDS:
            raise BatchAdapterError("input_invalid", f"record fields are not closed at index {index}")
        ordinal = item["ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise BatchAdapterError("input_invalid", f"ordinal is invalid at index {index}")
        candidate_id = _nonempty_string(item["candidate_id"], field="candidate_id")
        invocation_id = _nonempty_string(item["invocation_id"], field="invocation_id")
        staged_pdb_path = _nonempty_string(
            item["staged_pdb_path"], field="staged_pdb_path"
        )
        source_sha256 = _source_sha256(item["source_sha256"], index=index)
        records.append(
            {
                "ordinal": ordinal,
                "candidate_id": candidate_id,
                "invocation_id": invocation_id,
                "staged_pdb_path": staged_pdb_path,
                "source_sha256": source_sha256,
            }
        )

    ordinals = [record["ordinal"] for record in records]
    if ordinals != list(range(len(records))) or len(set(ordinals)) != len(ordinals):
        raise BatchAdapterError(
            "input_invalid",
            "record ordinal values must be unique and exactly match ordered indices",
        )
    for field in ("candidate_id", "invocation_id"):
        identities = [record[field] for record in records]
        if len(set(identities)) != len(identities):
            raise BatchAdapterError("input_invalid", f"record {field} values must be unique")

    stems: list[str] = []
    for record in records:
        path = Path(record["staged_pdb_path"])
        stem = path.stem
        if path.suffix != ".pdb" or path.name != f"{stem}.pdb" or not _SAFE_STEM.fullmatch(stem):
            raise BatchAdapterError(
                "input_invalid",
                "every record must use a safe staged PDB filename stem",
            )
        stems.append(stem)
    if len(set(stems)) != len(stems):
        raise BatchAdapterError("input_invalid", "staged PDB stems must be unique")

    for record in records:
        path = Path(record["staged_pdb_path"])
        try:
            is_symlink = path.is_symlink()
            is_file = path.is_file()
        except OSError as exc:
            raise BatchAdapterError("input_invalid", "staged PDB is unavailable") from exc
        if is_symlink or not is_file:
            raise BatchAdapterError(
                "input_invalid", "every staged PDB must be a regular non-symlink file"
            )
    return records


def _load_and_validate_manifest(path: Path | str) -> dict[str, Any]:
    value, _ = _read_manifest(Path(path))
    if set(value) != INPUT_FIELDS:
        raise BatchAdapterError("input_invalid", "input manifest fields are not closed")
    if value.get("schema_name") != INPUT_SCHEMA_NAME or value.get("schema_version") != 1:
        raise BatchAdapterError("input_invalid", "input manifest schema identity is invalid")
    checkpoint_path = _nonempty_string(value["checkpoint_path"], field="checkpoint_path")
    device = _nonempty_string(value["device"], field="device")
    records = _validated_records(value["records"])
    return {
        "schema_name": INPUT_SCHEMA_NAME,
        "schema_version": 1,
        "checkpoint_path": checkpoint_path,
        "device": device,
        "records": records,
    }


def _safe_output_directory(path: Path | str) -> Path:
    output = Path(path)
    if output.is_symlink():
        raise BatchAdapterError("publication_failed", "output directory must not be a symlink")
    if output.exists():
        if not output.is_dir():
            raise BatchAdapterError("publication_failed", "output path must be a directory")
        try:
            if any(output.iterdir()):
                raise BatchAdapterError(
                    "publication_failed", "output directory must be initially empty"
                )
        except OSError as exc:
            raise BatchAdapterError("publication_failed", "output directory is unreadable") from exc
    else:
        try:
            output.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise BatchAdapterError("publication_failed", "output directory cannot be created") from exc
    return output


def _utc_timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise BatchAdapterError(
            "evidence_invalid", "clock must return a timezone-aware datetime"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _base_terminal_record(
    record: Mapping[str, Any], stem: str, started_at: str
) -> dict[str, Any]:
    return {
        "ordinal": record["ordinal"],
        "candidate_id": record["candidate_id"],
        "invocation_id": record["invocation_id"],
        "pdb_stem": stem,
        "source_sha256": record["source_sha256"],
        "started_at": started_at,
        "terminal_at": None,
        "status": "failed",
        "failure_code": None,
        "diagnostic": None,
        "row_count": None,
        "output_csv": None,
        "output_sha256": None,
    }


def _evidence(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_name": EVIDENCE_SCHEMA_NAME,
        "schema_version": 1,
        "method_identity": METHOD_IDENTITY,
        "upstream_sequential_semantics": UPSTREAM_SEQUENTIAL_SEMANTICS,
        "model_load_count": 1,
        "record_count": len(records),
        "records": [dict(record) for record in records],
    }


def _publish_evidence(output: Path, evidence: Mapping[str, Any]) -> None:
    path = output / EVIDENCE_FILENAME
    payload = _canonical_json(evidence)
    try:
        path.write_bytes(payload)
    except OSError as exc:
        raise BatchAdapterError(
            "publication_failed", "terminal evidence could not be written"
        ) from exc


def _fail_all(
    output: Path,
    records: Sequence[Mapping[str, Any]],
    stems: Sequence[str],
    started_at: Sequence[str],
    failure_code: str,
    diagnostic: str,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    terminal = []
    bounded_diagnostic = " ".join(str(diagnostic).split())[:1024] or failure_code
    for record, stem, started in zip(records, stems, started_at, strict=True):
        entry = _base_terminal_record(record, stem, started)
        entry["failure_code"] = failure_code
        entry["diagnostic"] = bounded_diagnostic
        entry["terminal_at"] = _utc_timestamp(clock)
        terminal.append(entry)
    evidence = _evidence(terminal)
    _publish_evidence(output, evidence)
    return evidence


def _verify_source_hashes(records: Sequence[Mapping[str, Any]]) -> None:
    for index, record in enumerate(records):
        path = Path(record["staged_pdb_path"])
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError("not a regular file")
            payload = path.read_bytes()
        except OSError as exc:
            raise BatchAdapterError(
                "source_verification_failed",
                f"staged PDB source is unavailable at ordinal {index}",
            ) from exc
        if hashlib.sha256(payload).hexdigest() != record["source_sha256"]:
            raise BatchAdapterError(
                "source_verification_failed",
                f"source SHA-256 does not match at ordinal {index}",
            )


def _validate_batch_frame(
    value: Any,
    *,
    expected_stems: set[str],
) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame):
        raise BatchAdapterError("upstream_output_invalid", "predict_batch must return a DataFrame")
    columns = list(value.columns)
    if len(columns) != len(set(columns)):
        raise BatchAdapterError("upstream_output_invalid", "duplicate output fields are forbidden")
    if set(columns) != UPSTREAM_OUTPUT_FIELDS:
        raise BatchAdapterError(
            "upstream_output_invalid", "predict_batch output fields are not exact"
        )
    identities: list[str] = []
    for identity in value["pdb"].tolist():
        if not isinstance(identity, str) or not _SAFE_STEM.fullmatch(identity):
            raise BatchAdapterError("upstream_output_invalid", "unsafe pdb identity in batch output")
        if identity not in expected_stems:
            raise BatchAdapterError(
                "upstream_output_invalid", "unexpected pdb identity in batch output"
            )
        identities.append(identity)
    return value


def _csv_payload(frame: pd.DataFrame) -> bytes:
    try:
        text = frame.loc[:, CSV_COLUMNS].to_csv(index=False, lineterminator="\n")
        return text.encode("utf-8")
    except Exception as exc:
        raise BatchAdapterError(
            "upstream_output_invalid", "predict_batch output cannot be serialized exactly"
        ) from exc


def run_batch(
    *,
    manifest_path: Path | str,
    output_dir: Path | str,
    frustrampnn_module: ModuleType | Any | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Execute one strict upstream batch and publish per-input terminal evidence."""

    manifest = _load_and_validate_manifest(manifest_path)
    records = manifest["records"]
    pdb_paths = [record["staged_pdb_path"] for record in records]
    stems = [Path(path).stem for path in pdb_paths]
    output = _safe_output_directory(output_dir)
    active_clock = clock or (lambda: datetime.now(timezone.utc))
    started_at = [_utc_timestamp(active_clock) for _ in records]

    try:
        _verify_source_hashes(records)
    except BatchAdapterError as exc:
        _fail_all(
            output,
            records,
            stems,
            started_at,
            "source_verification_failed",
            exc.diagnostic,
            active_clock,
        )
        raise

    module = frustrampnn_module
    if module is None:
        try:
            module = importlib.import_module("frustrampnn")
        except Exception as exc:
            _fail_all(
                output, records, stems, started_at, "model_load_failed",
                "frustrampnn package could not be imported", active_clock,
            )
            raise BatchAdapterError(
                "model_load_failed", "frustrampnn package could not be imported"
            ) from exc

    try:
        model = module.FrustraMPNN.from_pretrained(
            manifest["checkpoint_path"], device=manifest["device"]
        )
    except Exception as exc:
        _fail_all(
            output, records, stems, started_at, "model_load_failed",
            "FrustraMPNN model could not be loaded", active_clock,
        )
        raise BatchAdapterError(
            "model_load_failed", "FrustraMPNN model could not be loaded"
        ) from exc

    try:
        batch_frame = model.predict_batch(
            pdb_paths,
            chains=None,
            show_progress=False,
        )
    except Exception as exc:
        _fail_all(
            output, records, stems, started_at, "batch_call_failed",
            "FrustraMPNN.predict_batch raised at batch scope", active_clock,
        )
        raise BatchAdapterError(
            "batch_call_failed", "FrustraMPNN.predict_batch raised at batch scope"
        ) from exc

    try:
        validated = _validate_batch_frame(batch_frame, expected_stems=set(stems))
    except BatchAdapterError as exc:
        _fail_all(
            output, records, stems, started_at, "upstream_output_invalid",
            exc.diagnostic, active_clock,
        )
        raise

    terminal: list[dict[str, Any]] = []
    for record, stem, started in zip(records, stems, started_at, strict=True):
        entry = _base_terminal_record(record, stem, started)
        selected = validated.loc[validated["pdb"] == stem]
        if selected.empty:
            entry["failure_code"] = "upstream_output_omitted"
            entry["diagnostic"] = (
                "upstream predict_batch returned no rows for this staged PDB"
            )
            entry["terminal_at"] = _utc_timestamp(active_clock)
            terminal.append(entry)
            continue
        payload = _csv_payload(selected)
        name = f"{stem}.csv"
        path = output / name
        try:
            path.write_bytes(payload)
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            entry["failure_code"] = "csv_publication_failed"
            entry["diagnostic"] = "CSV output could not be published"
            entry["terminal_at"] = _utc_timestamp(active_clock)
            terminal.append(entry)
            continue
        entry.update(
            {
                "status": "succeeded",
                "failure_code": None,
                "diagnostic": None,
                "row_count": len(selected),
                "output_csv": name,
                "output_sha256": hashlib.sha256(payload).hexdigest(),
                "terminal_at": _utc_timestamp(active_clock),
            }
        )
        terminal.append(entry)

    evidence = _evidence(terminal)
    _publish_evidence(output, evidence)
    return evidence


def main(
    argv: Sequence[str] | None = None,
    *,
    frustrampnn_module: ModuleType | Any | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run one strict FrustraMPNN.predict_batch adapter invocation."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        evidence = run_batch(
            manifest_path=args.manifest,
            output_dir=args.output_dir,
            frustrampnn_module=frustrampnn_module,
        )
    except BatchAdapterError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0 if all(record["status"] == "succeeded" for record in evidence["records"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
