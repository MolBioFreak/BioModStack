#!/usr/bin/env python3
"""Fail-closed POD5/Dorado P4 preflight with immutable runtime/model identities."""
from __future__ import annotations

import argparse

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

SCHEMA = "biomodstack.dorado_preflight.v1"
LOCK_SCHEMA = "biomodstack.dorado_lock.v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DEVICE = re.compile(r"^cuda:(?:all|[0-9]+(?:,[0-9]+)*)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock(path: Path) -> dict[str, Any]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != LOCK_SCHEMA:
        raise ValueError(f"lock schema must be {LOCK_SCHEMA}")
    dorado = data.get("dorado")
    if not isinstance(dorado, dict) or not HEX64.fullmatch(str(dorado.get("sif_sha256", ""))):
        raise ValueError("lock has invalid Dorado SIF identity")
    models = data.get("models")
    if not isinstance(models, dict):
        raise ValueError("lock models must be an object")
    for molecule in ("dna", "rna"):
        choices = models.get(molecule)
        if not isinstance(choices, dict) or set(choices) != {"fast", "hac", "sup"}:
            raise ValueError(f"lock must define exact fast/hac/sup models for {molecule}")
        for model in choices.values():
            _validate_model_record(model)
    _validate_model_record(models.get("stereo"))
    for model in (models.get("modified_bases") or {}).values():
        _validate_model_record(model)
    return data


def _validate_model_record(model: Any) -> None:
    if not isinstance(model, dict):
        raise ValueError("model record must be an object")
    if not str(model.get("id", "")).strip() or not HEX64.fullmatch(str(model.get("aggregate_sha256", ""))):
        raise ValueError("model record has invalid exact identity")
    if int(model.get("files", 0)) < 1 or int(model.get("bytes", 0)) < 1:
        raise ValueError("model record has invalid file inventory")


def resolve_model(lock: dict[str, Any], molecule: str, quality: str) -> dict[str, Any]:
    molecule = str(molecule).strip().lower()
    quality = str(quality).strip().lower()
    if molecule not in {"dna", "rna"}:
        raise ValueError("molecule must be dna or rna")
    if quality not in {"fast", "hac", "sup"}:
        raise ValueError("quality must be exactly fast, hac, or sup; model IDs and paths are server-controlled")
    return dict(lock["models"][molecule][quality])


def _model_aggregate(model_dir: Path) -> tuple[str, int, int]:
    if not model_dir.is_dir() or model_dir.is_symlink():
        raise ValueError(f"model identity directory is unavailable or unsafe: {model_dir}")
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(model_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"model identity contains symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(model_dir).as_posix()
        size = path.stat().st_size
        file_digest = _sha256(path)
        digest.update(relative.encode("utf-8") + b"\0" + str(size).encode("ascii") + b"\0" + file_digest.encode("ascii") + b"\n")
        count += 1
        total += size
    return digest.hexdigest(), count, total


def verify_model_identity(model: dict[str, Any], model_dir: Path) -> dict[str, Any]:
    aggregate, count, total = _model_aggregate(Path(model_dir))
    expected = (str(model["aggregate_sha256"]), int(model["files"]), int(model["bytes"]))
    actual = (aggregate, count, total)
    if actual != expected:
        raise ValueError(f"model identity mismatch for {model.get('id')}: expected {expected}, observed {actual}")
    return {"path": str(Path(model_dir).resolve()), "aggregate_sha256": aggregate, "files": count, "bytes": total}


def _verify_runtime_capabilities(runtime_sif: Path, lock: dict[str, Any], *, mode: str,
                                 barcode_kit: str | None, modified_bases: str) -> dict[str, Any]:
    required = lock["required_runtime_capabilities"]
    commands: dict[str, set[str]] = {}
    primary = "duplex" if mode == "duplex" else "basecaller"
    commands[primary] = set(required[primary])
    if barcode_kit:
        commands["basecaller"].update(required["basecaller_barcoding"])
        commands["demux"] = set(required["demux"])
    if modified_bases != "none":
        commands["basecaller"].update(required["basecaller_modified_bases"])
    evidence: dict[str, Any] = {}
    for command, options in sorted(commands.items()):
        completed = subprocess.run(
            ["apptainer", "exec", str(runtime_sif), "dorado", command, "--help"],
            text=True, capture_output=True, check=False, timeout=60,
        )
        help_text = (completed.stdout or "") + "\n" + (completed.stderr or "")
        missing = sorted(option for option in options if option not in help_text)
        if completed.returncode != 0 or missing:
            raise ValueError(f"Dorado runtime capability mismatch for {command}: missing {missing}")
        evidence[command] = {
            "required_options": sorted(options),
            "help_sha256": hashlib.sha256(help_text.encode("utf-8")).hexdigest(),
        }
    return evidence


def _confined_file(path: Path, root: Path, label: str) -> Path:
    raw = Path(path)
    if raw.is_symlink():
        raise ValueError(f"{label} may not be a symlink")
    resolved = raw.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must be confined beneath POD5 root") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _pod5_files(root: Path, allowed_ancillary: set[Path]) -> list[Path]:
    if root.is_symlink():
        raise ValueError("POD5 root may not be a symlink")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("POD5 root must be a directory")
    files: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            if (current_path / name).is_symlink():
                raise ValueError(f"POD5 inventory contains symlink directory: {current_path / name}")
        for name in names:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"POD5 inventory contains symlink: {path}")
            resolved = path.resolve(strict=True)
            suffix = path.suffix.lower()
            if suffix == ".fast5":
                raise ValueError(f"legacy FAST5 is outside the production contract: {path}")
            if suffix == ".pod5":
                files.append(resolved)
            elif resolved not in allowed_ancillary:
                raise ValueError(f"POD5 root contains unsupported mixed file: {path}")
    if not files:
        raise ValueError("POD5 inventory contains no POD5 files")
    return sorted(files)


def _read_pod5_inventory(files: list[Path], root: Path) -> tuple[dict[str, Any], set[str]]:
    try:
        import pod5  # type: ignore
    except ImportError as exc:
        raise ValueError("pod5==0.3.35 is required for deterministic preflight") from exc
    entries: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    rates: set[int] = set()
    products: set[str] = set()
    kits: set[str] = set()
    protocol_run_ids: set[str] = set()
    experiment_ids: set[str] = set()
    flow_cell_ids: set[str] = set()
    position_ids: set[str] = set()
    sample_sheet_indexes: set[tuple[str, str, str]] = set()
    read_ids: set[str] = set()
    for path in files:
        count = 0
        file_runs: set[str] = set()
        with pod5.Reader(path) as reader:
            for read in reader.reads():
                count += 1
                read_id = str(read.read_id)
                if read_id in read_ids:
                    raise ValueError(f"duplicate POD5 read ID: {read_id}")
                read_ids.add(read_id)
                info = read.run_info
                run_id = str(info.acquisition_id)
                run_ids.add(run_id)
                file_runs.add(run_id)
                rates.add(int(info.sample_rate))
                product = str(info.flow_cell_product_code or "").strip()
                kit = str(info.sequencing_kit or "").strip()
                protocol_run_id = str(info.protocol_run_id or "").strip()
                experiment_id = str(info.experiment_name or "").strip()
                flow_cell_id = str(info.flow_cell_id or "").strip()
                position_id = str(info.sequencer_position or "").strip()
                if product:
                    products.add(product)
                if kit:
                    kits.add(kit)
                if protocol_run_id:
                    protocol_run_ids.add(protocol_run_id)
                if experiment_id:
                    experiment_ids.add(experiment_id)
                if flow_cell_id:
                    flow_cell_ids.add(flow_cell_id)
                if position_id:
                    position_ids.add(position_id)
                if experiment_id:
                    sample_sheet_indexes.add((experiment_id, flow_cell_id, position_id))
        if count < 1:
            raise ValueError(f"POD5 file contains no reads: {path}")
        entries.append({
            "path": str(path),
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "read_count": count,
            "run_ids": sorted(file_runs),
        })
    return ({
        "files": entries,
        "file_count": len(entries),
        "read_count": len(read_ids),
        "run_ids": sorted(run_ids),
        "sample_rates": sorted(rates),
        "flow_cell_product_codes": sorted(products),
        "sequencing_kits": sorted(kits),
        "protocol_run_ids": sorted(protocol_run_ids),
        "experiment_ids": sorted(experiment_ids),
        "flow_cell_ids": sorted(flow_cell_ids),
        "position_ids": sorted(position_ids),
        "sample_sheet_indexes": [
            {"experiment_id": experiment, "flow_cell_id": flow_cell, "position_id": position}
            for experiment, flow_cell, position in sorted(sample_sheet_indexes)
        ],
    }, read_ids)


def _validate_chemistry(lock: dict[str, Any], molecule: str, inventory: dict[str, Any]) -> None:
    expected = lock["compatibility"][molecule]
    rates = inventory["sample_rates"]
    if rates != [int(expected["sample_rate"])]:
        raise ValueError(f"POD5 sample rate {rates} is incompatible with {molecule} model sample rate {expected['sample_rate']}")
    products = set(inventory["flow_cell_product_codes"])
    accepted = set(expected["flow_cell_product_codes"])
    if not products or not products <= accepted:
        raise ValueError(f"POD5 flow-cell product codes {sorted(products)} are incompatible with {molecule}")
    kits = inventory["sequencing_kits"]
    prefixes = tuple(str(value) for value in expected["sequencing_kit_prefixes"])
    if not kits or any(not str(kit).startswith(prefixes) for kit in kits):
        raise ValueError(f"POD5 sequencing kits {kits} are incompatible with {molecule}")
    if len(inventory["run_ids"]) != 1:
        raise ValueError("mixed POD5 run IDs are not accepted in one production basecall unit")


def _validate_pairs(path: Path, root: Path, read_ids: set[str]) -> dict[str, Any]:
    path = _confined_file(path, root, "duplex pairs file")
    pairs: list[tuple[str, str]] = []
    used: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        columns = [part.strip() for part in (line.split(",") if "," in line else line.split())]
        if len(columns) != 2 or not all(columns):
            raise ValueError(f"invalid duplex pairs row {number}")
        if columns[0] == columns[1] or any(item in used for item in columns):
            raise ValueError(f"duplicate/contradictory duplex pair at row {number}")
        if not set(columns) <= read_ids:
            raise ValueError(f"duplex pairs row {number} references absent POD5 read IDs")
        used.update(columns)
        pairs.append((columns[0], columns[1]))
    if not pairs:
        raise ValueError("duplex pairs file contains no pairs")
    return {
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "pair_count": len(pairs),
        "read_count": len(used),
    }


def _validate_sample_sheet(path: Path, root: Path, barcode_kit: str, inventory: dict[str, Any]) -> dict[str, Any]:
    path = _confined_file(path, root, "sample sheet")
    # Dorado 1.3.1 is not an RFC-4180 CSV parser: it performs a literal comma
    # split, preserves field bytes, and requires every row to have the same
    # cardinality as its header map. Accept only a canonical subset with the
    # exact same interpretation; never normalize input that Dorado sees
    # differently (quotes, surrounding whitespace, duplicate headers, or
    # ragged rows).
    try:
        raw_sheet = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("sample sheet is unreadable or not UTF-8") from exc
    if "\x00" in raw_sheet or '"' in raw_sheet:
        raise ValueError("sample sheet uses syntax unsupported by pinned Dorado 1.3.1")
    if "\r" in raw_sheet:
        without_crlf = raw_sheet.replace("\r\n", "")
        if "\r" in without_crlf or "\n" in without_crlf:
            raise ValueError("sample sheet uses mixed or unsupported line endings")
        lines = raw_sheet.split("\r\n")
    else:
        lines = raw_sheet.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) < 2 or any(line == "" for line in lines):
        raise ValueError("sample sheet must contain a header and at least one non-empty row")
    headers = lines[0].split(",")
    if len(set(headers)) != len(headers) or any(header != header.strip() for header in headers):
        raise ValueError("sample sheet contains duplicate or non-literal column headers")
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        values = line.split(",")
        if len(values) != len(headers) or any(value != value.strip() for value in values):
            raise ValueError("sample sheet row cardinality or literal field syntax is invalid")
        rows.append(dict(zip(headers, values, strict=True)))
    fieldnames = set(headers)
    allowed = {"experiment_id", "kit", "flow_cell_id", "position_id", "protocol_run_id", "sample_id", "flow_cell_product_code", "alias", "type", "barcode"}
    required = {"experiment_id", "kit", "barcode", "alias"}
    if not rows or not required <= fieldnames or not ({"flow_cell_id", "position_id"} & fieldnames) or not fieldnames <= allowed:
        raise ValueError("sample sheet has unsupported or missing required columns")
    barcodes: set[str] = set()
    aliases: set[str] = set()
    experiment_ids: set[str] = set()
    assignments: list[dict[str, str]] = []
    observed_indexes = {
        (str(item["experiment_id"]), str(item["flow_cell_id"]), str(item["position_id"]))
        for item in inventory["sample_sheet_indexes"]
    }
    for row in rows:
        barcode = str(row.get("barcode") or "").strip()
        alias = str(row.get("alias") or "").strip()
        kit = str(row.get("kit") or "").strip()
        experiment_id = str(row.get("experiment_id") or "").strip()
        flow_cell_id = str(row.get("flow_cell_id") or "").strip()
        position_id = str(row.get("position_id") or "").strip()
        selector_matches = bool(flow_cell_id or position_id) and any(
            observed_experiment == experiment_id
            and (not flow_cell_id or observed_flow_cell == flow_cell_id)
            and (not position_id or observed_position == position_id)
            for observed_experiment, observed_flow_cell, observed_position in observed_indexes
        )
        if (not re.fullmatch(r"barcode(?:0[1-9]|[1-8][0-9]|9[0-6])", barcode)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", alias)
                or re.fullmatch(r"barcode[0-9]+", alias)
                or alias == "unclassified"
                or kit != barcode_kit or experiment_id not in set(inventory["experiment_ids"])
                or not selector_matches
                or barcode in barcodes or alias in aliases):
            raise ValueError("sample sheet contains malformed, incompatible, or duplicate rows")
        barcodes.add(barcode)
        aliases.add(alias)
        experiment_ids.add(experiment_id)
        assignments.append({"barcode": barcode, "alias": alias})
    if len(experiment_ids) != 1:
        raise ValueError("sample sheet must contain exactly one experiment_id")
    return {
        "path": str(path),
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "rows": len(rows),
        "barcodes": sorted(barcodes),
        "assignments": sorted(assignments, key=lambda item: item["barcode"]),
    }


def build_preflight(*, lock_path: Path, pod5_root: Path, molecule: str, quality: str, mode: str,
                    model_root: Path, runtime_sif: Path, modified_bases: str = "none",
                    barcode_kit: str | None = None, sample_sheet: Path | None = None,
                    pairs: Path | None = None, batch_size: int | None = None,
                    min_qscore: int = 10, device: str = "cuda:0", verify_assets: bool = True) -> dict[str, Any]:
    lock = load_lock(Path(lock_path))
    molecule = str(molecule).strip().lower()
    quality = str(quality).strip().lower()
    mode = str(mode).strip().lower()
    modified_bases = str(modified_bases or "none").strip()
    if mode not in {"simplex", "duplex"}:
        raise ValueError("mode must be simplex or duplex")
    if molecule == "rna" and mode == "duplex":
        raise ValueError("RNA duplex is unsupported by the locked runtime")
    if mode == "duplex" and barcode_kit:
        raise ValueError("barcode classification is unsupported for duplex by the locked runtime")
    if barcode_kit and barcode_kit not in lock["barcoding"]["accepted_kits"]:
        raise ValueError("unsupported barcode kit")
    if sample_sheet and not barcode_kit:
        raise ValueError("sample sheet requires a barcode kit")
    if modified_bases != "none":
        mods = lock["models"]["modified_bases"]
        if molecule != "dna" or mode != "simplex" or modified_bases not in mods or quality != mods[modified_bases]["base_quality"]:
            raise ValueError("modified-base model is incompatible with molecule, quality, or basecalling mode")
    if not DEVICE.fullmatch(str(device)):
        raise ValueError("device must be an explicit CUDA device selection")
    policy = lock["policy"]
    batch = int(batch_size if batch_size is not None else policy["default_batch_size"][mode])
    if batch < int(policy["batch_size_min"]) or batch > int(policy["batch_size_max"]):
        raise ValueError("batch size is outside the locked bounded policy")
    qscore = int(min_qscore)
    if qscore < 0 or qscore > 30:
        raise ValueError("min_qscore must be an integer from 0 through 30")

    root = Path(pod5_root)
    if root.is_symlink():
        raise ValueError("POD5 root may not be a symlink")
    root = root.resolve(strict=True)
    ancillary: set[Path] = set()
    for value, label in ((pairs, "duplex pairs file"), (sample_sheet, "sample sheet")):
        if value is not None:
            ancillary.add(_confined_file(Path(value), root, label))
    files = _pod5_files(root, ancillary)
    inventory, read_ids = _read_pod5_inventory(files, root)
    _validate_chemistry(lock, molecule, inventory)

    model = resolve_model(lock, molecule, quality)
    pairs_record = None
    stereo = None
    if mode == "duplex":
        if pairs is None:
            raise ValueError("duplex mode requires a confined pairs file")
        pairs_record = _validate_pairs(Path(pairs), root, read_ids)
        stereo = dict(lock["models"]["stereo"])
    elif pairs is not None:
        raise ValueError("pairs file is only valid in duplex mode")
    sheet_record = _validate_sample_sheet(Path(sample_sheet), root, str(barcode_kit), inventory) if sample_sheet is not None else None

    assets: dict[str, Any] = {"verified": False}
    if verify_assets:
        runtime_sif = Path(runtime_sif)
        if runtime_sif.is_symlink() or not runtime_sif.is_file() or _sha256(runtime_sif) != lock["dorado"]["sif_sha256"]:
            raise ValueError("Dorado runtime SIF identity mismatch")
        completed = subprocess.run(["apptainer", "exec", str(runtime_sif), "dorado", "--version"], text=True, capture_output=True, check=False, timeout=60)
        version = (completed.stdout or completed.stderr).strip().splitlines()[0] if (completed.stdout or completed.stderr).strip() else ""
        if completed.returncode != 0 or version != lock["dorado"]["version"]:
            raise ValueError("Dorado runtime version identity mismatch")
        capabilities = _verify_runtime_capabilities(runtime_sif, lock, mode=mode, barcode_kit=barcode_kit, modified_bases=modified_bases)
        verified_models = {"base": verify_model_identity(model, Path(model_root) / model["id"])}
        if stereo:
            verified_models["stereo"] = verify_model_identity(stereo, Path(model_root) / stereo["id"])
        if modified_bases != "none":
            mod_model = lock["models"]["modified_bases"][modified_bases]
            verified_models["modified_bases"] = verify_model_identity(mod_model, Path(model_root) / mod_model["id"])
        assets = {"verified": True, "runtime_sif": {"path": str(runtime_sif.resolve()), "sha256": _sha256(runtime_sif), "version": version}, "capabilities": capabilities, "models": verified_models}

    return {
        "schema": SCHEMA,
        "lock": {"path": str(Path(lock_path).resolve()), "sha256": _sha256(Path(lock_path))},
        "runtime": {"version": lock["dorado"]["version"], "sif_sha256": lock["dorado"]["sif_sha256"], "assets": assets},
        "inputs": {"root": str(root), **inventory},
        "selection": {
            "molecule": molecule, "quality": quality, "mode": mode,
            "model_id": model["id"], "model_aggregate_sha256": model["aggregate_sha256"],
            "stereo_model_id": stereo["id"] if stereo else None,
            "modified_bases": modified_bases,
            "modified_bases_model_id": lock["models"]["modified_bases"][modified_bases]["id"] if modified_bases != "none" else None,
        },
        "pairs": pairs_record,
        "barcoding": {"kit": barcode_kit, "sample_sheet": sheet_record, "sample_sheet_sha256": sheet_record["sha256"] if sheet_record else None},
        "execution_policy": {
            "device": str(device),
            "batch_size": batch,
            "min_qscore": qscore,
            "min_gpu_total_mib": int(policy["min_gpu_total_mib"]),
            "min_gpu_free_mib": int(policy["min_gpu_free_mib"]),
            "runtime_network": "forbidden",
            "model_download": "forbidden",
        },
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--pod5-root", required=True, type=Path)
    parser.add_argument("--molecule", required=True, choices=("dna", "rna"))
    parser.add_argument("--quality", required=True, choices=("fast", "hac", "sup"))
    parser.add_argument("--mode", default="simplex", choices=("simplex", "duplex"))
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--runtime-sif", required=True, type=Path)
    parser.add_argument("--modified-bases", default="none")
    parser.add_argument("--barcode-kit")
    parser.add_argument("--sample-sheet", type=Path)
    parser.add_argument("--pairs", type=Path)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--min-qscore", type=int, default=10)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build_preflight(lock_path=args.lock, pod5_root=args.pod5_root, molecule=args.molecule,
        quality=args.quality, mode=args.mode, model_root=args.model_root, runtime_sif=args.runtime_sif,
        modified_bases=args.modified_bases, barcode_kit=args.barcode_kit, sample_sheet=args.sample_sheet,
        pairs=args.pairs, batch_size=args.batch_size, min_qscore=args.min_qscore,
        device=args.device, verify_assets=True)
    _atomic_json(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
