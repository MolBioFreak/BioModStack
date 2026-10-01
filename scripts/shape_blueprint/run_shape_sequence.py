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
    "caliby_experimental": str(Path(__file__).resolve().parents[1] / "run_caliby_experimental.py"),
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
        raise ValueError("unsupported Shape sequence engine")
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
    effective_seed = settings.get("fampnn_seed", _effective_seed(seed, backbone_sha256, engine)) if engine == "fampnn" else _effective_seed(seed, backbone_sha256, engine)
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
        native_relax = engine == "proteinmpnn" and (
            settings.get("mpnn_relax_max_cycles", 0) > 0 or settings.get("mpnn_relax_output", False)
            or settings.get("mpnn_output_intermediates", False) or settings.get("mpnn_num_connections", 48) != 48
        )
        if native_relax:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from proteinmpnn_native_binding import prepare
            from prep_mpnn_designs import canonical_results
            input_dir = output_dir / "input"
            input_dir.mkdir()
            prepare(backbone_path, input_dir / backbone_path.name, settings)
            native_runner = "/dl_binder_design/mpnn_fr/dl_interface_design_multi.py"
            binding = Path(__file__).resolve().parents[1] / "proteinmpnn_native_binding.py"
            bootstrap = ("import sys,runpy,random,numpy as np,torch; seed=int(sys.argv[1]); "
                         "random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); "
                         "sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')")
            command = [sys.executable, "-c", bootstrap, str(effective_seed), str(binding), "--native", native_runner, "--",
                       "-pdbdir", str(input_dir), "-outpdbdir", str(runtime_dir),
                       "-seqs_per_struct", str(count), "-debug",
                       "-checkpoint_path", f"/dl_binder_design/mpnn_fr/ProteinMPNN/{settings['mpnn_checkpoint_type']}_model_weights/{settings['mpnn_checkpoint_model']}.pt"]
            mapping = {"mpnn_backbone_noise": "augment_eps", "mpnn_temperature": "temperature", "mpnn_omitAAs": "omit_AAs",
                       **{f"mpnn_{key}": key for key in ("relax_max_cycles", "relax_seqs_per_cycle", "relax_convergence_rmsd",
                                                          "relax_convergence_score", "relax_convergence_max_cycles", "num_connections", "bias_AA_jsonl")}}
            for key, native in mapping.items():
                if key in settings and settings[key] is not None:
                    command += ["-" + native, str(settings[key])]
            for key in ("relax_output", "output_intermediates"):
                if settings.get("mpnn_" + key, False):
                    command.append("-" + key)
            import shlex
            command.extend(shlex.split(settings.get("mpnn_extra_config") or ""))
            subprocess.run(command, check=True, env=run_environment)
            canonical = output_dir / "canonical"
            canonical_results(runtime_dir, canonical)
            by_native_id = {}
            for path in canonical.glob("*.json"):
                payload = json.loads(path.read_text())
                by_native_id[payload["native_output_tag"]] = payload
            records = []
            for index in range(count):
                native_id = f"{backbone_path.stem}_seq_{index}"
                payload = by_native_id[native_id]
                if payload["source_input_tag"] != backbone_path.stem:
                    raise ValueError("ProteinMPNN native source binding differs")
                records.append({"engine": engine, "backbone_name": backbone_path.name, "backbone_sha256": backbone_sha256,
                                "sample_index": index + 1, "sequence_name": f"{candidate_id}__proteinmpnn__{index + 1:03d}",
                                "sequence": _validate_sequence(payload["sequence"], expected_length),
                                "metadata": payload, "native_record_id": native_id})
            receipt["runtime"] = {"runner": native_runner, "command": command}
        elif engine == "proteinmpnn":
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
            if settings.get("mpnn_bias_AA_jsonl"):
                command += ["--bias_AA_jsonl", str(settings["mpnn_bias_AA_jsonl"])]
            if settings.get("fixed_positions"):
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from proteinmpnn_native_binding import role_contract, feature_axes
                roles = role_contract(backbone_path, settings)
                axes = feature_axes(roles["source_residues"])
                fixed = {tuple(value) for value in roles["fixed_positions"]}
                indices = {chain: [axis.index(index + 1) + 1 for index, residue in enumerate(roles["source_residues"])
                                   if residue[0] == chain and tuple(residue) in fixed] for chain, axis in axes.items()}
                fixed_path = output_dir / "fixed_positions.jsonl"
                _write_json(fixed_path, {backbone_path.stem: indices})
                command += ["--fixed_positions_jsonl", str(fixed_path)]
            import shlex
            command.extend(shlex.split(settings.get("mpnn_extra_config") or ""))
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
        elif engine == "caliby_experimental":
            # The ordinary Caliby preparation/runner owns cleaning, constraints,
            # sampling and native result identity. Each backbone is its own
            # ordered ensemble, never a member of another generated candidate.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from prep_caliby_request import prepare_request
            native_request = dict(settings)
            native_request.update(schema_version=1, task="ensemble_design", num_seqs_per_pdb=count)
            native_request["ensembles"] = [{"ensemble_id": candidate_id, "states": [{
                **request.get("sequence_input_settings", {}),
                "state_id": candidate_id, "path": str(backbone_path),
            }]}]
            prepared = output_dir / "prepared"
            prepare_request(native_request, prepared)
            command = _command_prefix(runner) + ["--request-dir", str(prepared.resolve()),
                                                "--output-dir", str(runtime_dir.resolve())]
            subprocess.run(command, check=True, env=run_environment)
            native = json.loads((runtime_dir / "caliby_results.json").read_text())
            records = []
            for row in native["records"]:
                source = row.get("source") or {}
                if source.get("ensemble_id") != candidate_id or source.get("state_id") != candidate_id:
                    raise ValueError("Caliby output source differs from generated backbone")
                structure = _regular(runtime_dir / row["structure_path"], "Caliby native CIF")
                if not structure.is_relative_to(runtime_dir.resolve()):
                    raise ValueError("Caliby output escaped native tree")
                records.append({
                    "engine": engine, "backbone_name": backbone_path.name,
                    "backbone_sha256": backbone_sha256, "sample_index": row["record_id"],
                    "sequence_name": f"{candidate_id}__caliby_experimental__{int(row['record_id']):03d}",
                    "sequence": _validate_sequence(row["native"]["seq"], expected_length),
                    "metadata": row["native"], "native_source": source,
                    "native_record_id": row["record_id"],
                    "native_structure": {"filename": structure.relative_to(output_dir.resolve()).as_posix(),
                                         "sha256": _sha(structure), "bytes": structure.stat().st_size,
                                         "format": structure.suffix.lstrip(".")},
                })
            receipt["runtime"] = native["runtime"]
            receipt["effective_seed"] = None  # Ordinary Caliby exposes no seed control.
        else:
            input_dir = output_dir / "input"
            input_dir.mkdir()
            staged_backbone = input_dir / backbone_path.name
            shutil.copyfile(backbone_path, staged_backbone)
            command = _command_prefix(runner) + [
                f"seed={effective_seed}",
                f"batch_size={settings['fampnn_batch_size']}",
                f"checkpoint_path={settings.get('fampnn_checkpoint_path') or '/app/fampnn/weights/' + settings.get('fampnn_checkpoint', 'fampnn_0_3.pt')}",
                f"pdb_dir={input_dir}",
                f"out_dir={runtime_dir}",
                f"num_seqs_per_pdb={count}",
                "fixed_pos_verbose=false",
                f"seq_only={str(settings['fampnn_seq_only']).lower()}",
                f"repack_last={str(settings['fampnn_repack_last']).lower()}",
                f"temperature={settings['fampnn_temperature']}",
                f"timestep_schedule.num_steps={settings['fampnn_num_steps']}",
                f"exclude_cys={str(settings['fampnn_exclude_cys']).lower()}",
                f"psce_threshold={json.dumps(settings['fampnn_psce_threshold'])}",
                f"hydra.run.dir={runtime_dir / '.hydra'}",
                "hydra.output_subdir=null",
                "hydra.job.chdir=false",
            ]
            receipt["runtime"] = {
                "runner": runner,
                "checkpoint_path": settings.get("fampnn_checkpoint_path") or "/app/fampnn/weights/" + settings.get("fampnn_checkpoint", "fampnn_0_3.pt"),
            }
            native_options = {
                "fampnn_seed": "seed", "fampnn_presort_by_length": "presort_by_length",
                "fampnn_timestep_mode": "timestep_schedule.mode",
                "fampnn_timestep_start": "timestep_schedule.t_start",
                "fampnn_timestep_end": "timestep_schedule.t_end",
                "fampnn_scn_num_steps": "scn_diffusion.num_steps",
                "fampnn_scn_timestep_mode": "scn_diffusion.timestep_schedule.mode",
                "fampnn_scn_timestep_start": "scn_diffusion.timestep_schedule.t_start",
                "fampnn_scn_timestep_end": "scn_diffusion.timestep_schedule.t_end",
                "fampnn_scn_step_scale": "scn_diffusion.step_scale",
                **{f"fampnn_scn_{key}": f"scn_diffusion.churn_cfg.{key}"
                   for key in ("s_churn", "s_noise", "s_t_min", "s_t_max")},
            }
            for key, native_key in native_options.items():
                if key in settings:
                    command = [arg for arg in command if not arg.startswith(native_key + "=")]
                    command.append(f"{native_key}={json.dumps(settings[key], allow_nan=False)}")
            if settings.get("fixed_positions") or settings.get("design_chain") or settings.get("target_chain"):
                import base64
                helper = Path(__file__).resolve().parents[1] / "prep_fampnn_constraints_generic.py"
                constraints = output_dir / "constraints.csv"
                subprocess.run([sys.executable, str(helper), "--input_dir", str(input_dir),
                                "--out_csv", str(constraints), "--request_base64",
                                base64.b64encode(_canonical({"sequence_design_mode": "design", "design_chain": "A", **settings})).decode()], check=True, env=run_environment)
                command.append(f"fixed_pos_csv={constraints}")
            import shlex
            command.extend(shlex.split(settings.get("fampnn_extra_config") or ""))
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
        _write_json(output_dir / "sequence_records.json", {"schema": "bms_shape_sequences_v2" if request.get("schema") == "bms_shape_design_request_v3" else "bms_shape_sequences_v1", "records": records})
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
