#!/usr/bin/env python3
"""Run one direct Shape Blueprint sequence-design lane with closed outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
DEFAULT_RUNNERS = {
    "proteinmpnn": "/dl_binder_design/mpnn_fr/ProteinMPNN/protein_mpnn_run.py",
    "fampnn": "/app/fampnn/fampnn/inference/seq_design.py",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _regular(path: Path, label: str) -> Path:
    path = path.resolve(strict=True)
    stat_result = path.stat()
    if not path.is_file() or path.is_symlink() or stat_result.st_nlink != 1:
        raise ValueError(f"{label} must be a private regular file")
    return path


def _backbone_length(path: Path) -> int:
    residues: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("ATOM") or line[12:16].strip() != "CA":
            continue
        if len(line) < 54:
            raise ValueError("backbone contains a truncated ATOM record")
        chain = line[21:22].strip() or "A"
        if chain != "A":
            raise ValueError("Shape sequence lanes require one chain named A")
        coordinates = tuple(float(line[start:end]) for start, end in ((30, 38), (38, 46), (46, 54)))
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("backbone contains non-finite coordinates")
        identity = (chain, line[22:26].strip(), line[26:27].strip())
        if identity not in residues:
            residues.append(identity)
    if not residues:
        raise ValueError("backbone has no chain-A CA atoms")
    return len(residues)


def _effective_seed(seed: int, backbone_sha256: str, engine: str) -> int:
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if seed:
        return seed
    digest = hashlib.sha256(f"{backbone_sha256}:{engine}:shape-sequence-v1".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 998 + 1


def _validate_sequence(sequence: str, expected_length: int) -> str:
    sequence = sequence.strip().upper()
    if len(sequence) != expected_length or not sequence or any(letter not in AMINO_ACIDS for letter in sequence):
        raise ValueError("sequence output has invalid alphabet or length")
    return sequence


def _read_fasta(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    header: str | None = None
    chunks: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(chunks)))
            header, chunks = line[1:], []
        elif header is None:
            raise ValueError("FASTA sequence precedes its header")
        else:
            chunks.append(line)
    if header is not None:
        records.append((header, "".join(chunks)))
    return records


def _metadata(header: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {"header": header}
    for token in header.split(","):
        if "=" not in token:
            continue
        key, value = (part.strip() for part in token.split("=", 1))
        try:
            parsed[key] = float(value)
        except ValueError:
            parsed[key] = value
    return parsed


def _command_prefix(runner: str) -> list[str]:
    return [sys.executable, runner] if runner.endswith(".py") else [runner]


def run_sequence_lane(
    *,
    engine: str,
    backbone_path: Path,
    output_dir: Path,
    receipt_path: Path,
    count: int,
    seed: int,
    candidate_id: str,
    request: dict[str, Any],
    runner: str | None = None,
    environment: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("sequence lane requires the immutable RFD3 candidate ID")
    if engine not in DEFAULT_RUNNERS:
        raise ValueError("engine must be proteinmpnn or fampnn")
    if not 1 <= count <= 32:
        raise ValueError("sequence count must be between 1 and 32")
    request_body = {key: value for key, value in request.items() if key != "request_sha256"}
    if hashlib.sha256(_canonical(request_body)).hexdigest() != request.get("request_sha256"):
        raise ValueError("sequence lane canonical request hash mismatch")
    if (request.get("sequence_engine") or "proteinmpnn") != engine or request.get("sequence_policy") == "skip":
        raise ValueError("sequence lane does not match canonical request")
    if request.get("sequences_per_backbone") != count or request.get("seed") != seed:
        raise ValueError("sequence count/seed does not match canonical request")
    settings = request.get("sequence_settings")
    settings_identity = request.get("sequence_settings_identity")
    if not isinstance(settings, dict) or not isinstance(settings_identity, dict) or settings_identity.get("engine") != engine:
        raise ValueError("canonical request lacks resolved global sequence settings; resubmit for explicit normalization")
    backbone_path = _regular(backbone_path, "backbone")
    if output_dir.exists():
        raise ValueError("sequence output directory must be new")
    output_dir.mkdir(parents=True)
    expected_length = _backbone_length(backbone_path)
    backbone_sha256 = _sha(backbone_path)
    effective_seed = _effective_seed(seed, backbone_sha256, engine)
    runtime_dir = output_dir / "runtime"
    runtime_dir.mkdir()
    runner = runner or DEFAULT_RUNNERS[engine]
    run_environment = os.environ.copy()
    if environment:
        run_environment.update(environment)
    receipt: dict[str, Any] = {
        "schema": "bms_shape_sequence_runtime_v1",
        "status": "running",
        "engine": engine,
        "backbone_name": backbone_path.name,
        "backbone_sha256": backbone_sha256,
        "requested_count": count,
        "requested_seed": seed,
        "effective_seed": effective_seed,
        "expected_length": expected_length,
        "request_sha256": request["request_sha256"],
        "requested_settings": request["requested_sequence_settings"],
        "effective_settings": settings,
        "settings_identity": settings_identity,
    }
    try:
        if engine == "proteinmpnn":
            command = _command_prefix(runner) + [
                "--pdb_path", str(backbone_path),
                "--pdb_path_chains", "A",
                "--out_folder", str(runtime_dir),
                "--num_seq_per_target", str(count),
                "--batch_size", "1",
                "--sampling_temp", str(settings["mpnn_temperature"]),
                "--omit_AAs", settings["mpnn_omitAAs"],
                "--backbone_noise", str(settings["mpnn_backbone_noise"]),
                "--seed", str(effective_seed),
                "--path_to_model_weights", {
                    "soluble": "/dl_binder_design/mpnn_fr/ProteinMPNN/soluble_model_weights",
                    "vanilla": "/dl_binder_design/mpnn_fr/ProteinMPNN/vanilla_model_weights",
                }[settings["mpnn_checkpoint_type"]],
                "--model_name", settings["mpnn_checkpoint_model"],
            ]
            receipt["runtime"] = {
                "runner": runner,
                "model_weights_path": command[command.index("--path_to_model_weights") + 1],
                "model_name": settings["mpnn_checkpoint_model"],
            }
            subprocess.run(command, check=True, env=run_environment)
            fasta = runtime_dir / "seqs" / f"{backbone_path.stem}.fa"
            source_records = _read_fasta(_regular(fasta, "ProteinMPNN FASTA"))
            if len(source_records) != count + 1:
                raise ValueError("ProteinMPNN did not emit native plus exact generated count")
            generated = source_records[1:]
            records = [
                {
                    "engine": engine,
                    "backbone_name": backbone_path.name,
                    "backbone_sha256": backbone_sha256,
                    "sample_index": index,
                    "sequence_name": f"{candidate_id}__proteinmpnn__{index:03d}",
                    "sequence": _validate_sequence(sequence, expected_length),
                    "metadata": _metadata(header),
                }
                for index, (header, sequence) in enumerate(generated, start=1)
            ]
        else:
            input_dir = output_dir / "input"
            input_dir.mkdir()
            staged_backbone = input_dir / backbone_path.name
            shutil.copyfile(backbone_path, staged_backbone)
            command = _command_prefix(runner) + [
                f"seed={effective_seed}",
                f"batch_size={settings['fampnn_batch_size']}",
                "checkpoint_path=/app/fampnn/weights/fampnn_0_3.pt",
                f"pdb_dir={input_dir}",
                f"out_dir={runtime_dir}",
                f"num_seqs_per_pdb={count}",
                "fixed_pos_verbose=false",
                f"seq_only={str(settings['fampnn_seq_only']).lower()}",
                f"repack_last={str(settings['fampnn_repack_last']).lower()}",
                f"temperature={settings['fampnn_temperature']}",
                f"timestep_schedule.num_steps={settings['fampnn_num_steps']}",
                f"exclude_cys={str(settings['fampnn_exclude_cys']).lower()}",
                f"psce_threshold={settings['fampnn_psce_threshold']}",
                f"hydra.run.dir={runtime_dir / '.hydra'}",
                "hydra.output_subdir=null",
                "hydra.job.chdir=false",
            ]
            receipt["runtime"] = {
                "runner": runner,
                "checkpoint_path": "/app/fampnn/weights/fampnn_0_3.pt",
            }
            run_environment["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
            subprocess.run(command, check=True, env=run_environment)
            fasta_dir = runtime_dir / "fastas"
            indexed: dict[int, Path] = {}
            pattern = re.compile(rf"^{re.escape(backbone_path.stem)}_sample(\d+)\.fasta$")
            for path in fasta_dir.glob("*.fasta"):
                match = pattern.fullmatch(path.name)
                if match is None:
                    raise ValueError("FAMPNN emitted an unexpected FASTA filename")
                index = int(match.group(1))
                if index in indexed:
                    raise ValueError("FAMPNN emitted a duplicate sample index")
                indexed[index] = path
            if sorted(indexed) != list(range(count)):
                raise ValueError("FAMPNN did not emit the exact sample index set")
            records = []
            for index in sorted(indexed):
                source_records = _read_fasta(_regular(indexed[index], "FAMPNN FASTA"))
                if len(source_records) != 1 or source_records[0][0] != f"{backbone_path.stem}_sample{index}":
                    raise ValueError("FAMPNN FASTA header is invalid")
                records.append(
                    {
                        "engine": engine,
                        "backbone_name": backbone_path.name,
                        "backbone_sha256": backbone_sha256,
                        "sample_index": index,
                        "sequence_name": f"{candidate_id}__fampnn__{index:03d}",
                        "sequence": _validate_sequence(source_records[0][1], expected_length),
                        "metadata": {"header": source_records[0][0]},
                    }
                )
        source_backbone = output_dir / "source_backbone.pdb"
        shutil.copyfile(backbone_path, source_backbone)
        for record in records:
            record["sequence_settings"] = settings
            record["requested_sequence_settings"] = request["requested_sequence_settings"]
            record["sequence_settings_identity"] = settings_identity
            record["backbone_candidate_id"] = candidate_id
            record["source_backbone"] = source_backbone.name
        _write_json(output_dir / "sequence_records.json", {"schema": "bms_shape_sequences_v1", "records": records})
        (output_dir / "sequences.fasta").write_text(
            "".join(f">{record['sequence_name']}\n{record['sequence']}\n" for record in records),
            encoding="utf-8",
        )
        receipt.update(status="completed", output_count=len(records), source_backbone=source_backbone.name)
        _write_json(receipt_path, receipt)
        return records
    except Exception as exc:
        receipt.update(status="failed", failure_type=type(exc).__name__, failure_message=str(exc))
        _write_json(receipt_path, receipt)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=sorted(DEFAULT_RUNNERS))
    parser.add_argument("--backbone", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--runner")
    args = parser.parse_args()
    run_sequence_lane(
        engine=args.engine,
        backbone_path=args.backbone,
        candidate_id=args.candidate_id,
        output_dir=args.output_dir,
        receipt_path=args.receipt,
        count=args.count,
        seed=args.seed,
        request=json.loads(args.request.read_text(encoding="utf-8")),
        runner=args.runner,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
