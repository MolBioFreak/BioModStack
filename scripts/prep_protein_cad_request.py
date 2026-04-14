#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _parse_lengths(raw: str) -> list[int]:
    values: list[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        values.append(int(token))
    if not values:
        raise ValueError("At least one target length is required")
    return values


def _normalize_sequence(raw: str) -> str:
    return "".join(ch for ch in str(raw or "").upper() if ch.isalpha())


def _reverse_complement_dna(sequence: str) -> str:
    mapping = str.maketrans("ACGT", "TGCA")
    return sequence.translate(mapping)[::-1]


def _copy_optional(path_value: str, input_dir: Path) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    source = Path(raw).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")
    target = input_dir / source.name
    if source != target:
        shutil.copy2(source, target)
    return str(target)


def _build_disco_jobs(
    task: str,
    target_lengths: list[int],
    ligand_path: str,
    ligand_name: str,
    na_sequence: str,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    normalized_na = _normalize_sequence(na_sequence)

    for length in target_lengths:
        masked_sequence = "-" * length
        job_name = f"length_{length}"
        sequences: list[dict[str, Any]] = [
            {"proteinChain": {"sequence": masked_sequence, "count": 1}}
        ]

        if task == "ligand_conditioned":
            if not ligand_path:
                raise ValueError("DISCO ligand_conditioned task requires --disco-ligand-sdf")
            ligand_label = ligand_name.strip() if ligand_name.strip() else Path(ligand_path).stem
            job_name = f"length_{length}_{ligand_label}"
            sequences.append(
                {"ligand": {"ligand": f"FILE_{ligand_path}", "count": 1}}
            )
        elif task in {"dna_conditioned", "rna_conditioned"}:
            if not normalized_na:
                raise ValueError(f"DISCO {task} requires --disco-na-sequence")
            chain_key = "dnaSequence" if task == "dna_conditioned" else "rnaSequence"
            job_name = f"length_{length}_{'dna' if task == 'dna_conditioned' else 'rna'}"
            sequences.append({chain_key: {"sequence": normalized_na, "count": 1}})
            if task == "dna_conditioned":
                sequences.append(
                    {chain_key: {"sequence": _reverse_complement_dna(normalized_na), "count": 1}}
                )
        elif task != "unconditional":
            raise ValueError(f"Unsupported compiled DISCO task: {task}")

        jobs.append({"name": job_name, "sequences": sequences})

    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile BMS Protein CAD params into a normalized request.")
    parser.add_argument("--job-id", default="unknown")
    parser.add_argument("--job-name", default="protein_cad_experimental")
    parser.add_argument("--backend", required=True, choices=["laproteina", "disco"])
    parser.add_argument("--task", required=True)
    parser.add_argument("--num-designs", type=int, required=True)
    parser.add_argument("--target-lengths", required=True)
    parser.add_argument("--laproteina-preset", default="ucond_tri")
    parser.add_argument("--laproteina-samples-per-length", type=int, default=8)
    parser.add_argument("--laproteina-num-steps", type=int, default=400)
    parser.add_argument("--laproteina-motif-task-name", default="")
    parser.add_argument("--laproteina-motif-pdb", default="")
    parser.add_argument("--laproteina-contig-string", default="")
    parser.add_argument("--laproteina-segment-order", default="")
    parser.add_argument("--laproteina-atom-selection-mode", default="all_atom")
    parser.add_argument("--laproteina-motif-min-length", default="")
    parser.add_argument("--laproteina-motif-max-length", default="")
    parser.add_argument("--laproteina-checkpoint-dir", default="")
    parser.add_argument("--laproteina-data-path", default="")
    parser.add_argument("--disco-experiment", default="designable")
    parser.add_argument("--disco-effort", default="fast")
    parser.add_argument("--disco-num-inference-seeds", type=int, default=8)
    parser.add_argument("--disco-seeds", default="")
    parser.add_argument("--disco-input-json-path", default="")
    parser.add_argument("--disco-ligand-sdf", default="")
    parser.add_argument("--disco-ligand-name", default="")
    parser.add_argument("--disco-na-sequence", default="")
    parser.add_argument("--disco-checkpoint-path", default="")
    parser.add_argument("--disco-use-deepspeed-evo-attention", type=_parse_bool, default=False)
    parser.add_argument("--disco-cutlass-path", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-dir", required=True)
    args = parser.parse_args()

    target_lengths = _parse_lengths(args.target_lengths)
    input_dir = Path(args.input_dir).resolve()
    input_dir.mkdir(parents=True, exist_ok=True)

    copied_motif_pdb = _copy_optional(args.laproteina_motif_pdb, input_dir)
    copied_disco_json = _copy_optional(args.disco_input_json_path, input_dir)
    copied_ligand_sdf = _copy_optional(args.disco_ligand_sdf, input_dir)

    if args.backend == "laproteina":
        if args.task == "motif_scaffolding" and not (args.laproteina_motif_task_name or (copied_motif_pdb and args.laproteina_contig_string)):
            raise ValueError(
                "La-Proteina motif_scaffolding requires either laproteina_motif_task_name "
                "or a custom laproteina_motif_pdb + laproteina_contig_string"
            )
        if args.task != "motif_scaffolding" and args.task != "unconditional":
            raise ValueError(
                "La-Proteina integration currently supports unconditional and motif_scaffolding tasks"
            )

    if args.backend == "disco":
        if args.task == "custom_json" and not copied_disco_json:
            raise ValueError("DISCO custom_json requires --disco-input-json-path")
        if args.task in {"ligand_conditioned", "dna_conditioned", "rna_conditioned"} and args.disco_effort == "fast":
            # Upstream docs explicitly recommend max for conditional generation.
            args.disco_effort = "max"

    request: dict[str, Any] = {
        "job_id": args.job_id,
        "job_name": args.job_name,
        "backend": args.backend,
        "task": args.task,
        "num_designs": args.num_designs,
        "target_lengths": target_lengths,
        "laproteina": {
            "preset": args.laproteina_preset,
            "samples_per_length": args.laproteina_samples_per_length,
            "num_steps": args.laproteina_num_steps,
            "motif_task_name": args.laproteina_motif_task_name.strip(),
            "motif_pdb": copied_motif_pdb,
            "contig_string": args.laproteina_contig_string.strip(),
            "segment_order": args.laproteina_segment_order.strip(),
            "atom_selection_mode": args.laproteina_atom_selection_mode.strip() or "all_atom",
            "motif_min_length": int(args.laproteina_motif_min_length) if str(args.laproteina_motif_min_length).strip() else None,
            "motif_max_length": int(args.laproteina_motif_max_length) if str(args.laproteina_motif_max_length).strip() else None,
            "checkpoint_dir": args.laproteina_checkpoint_dir.strip(),
            "data_path": args.laproteina_data_path.strip(),
        },
        "disco": {
            "experiment": args.disco_experiment.strip() or "designable",
            "effort": args.disco_effort.strip() or "fast",
            "num_inference_seeds": args.disco_num_inference_seeds,
            "seeds": [int(token.strip()) for token in args.disco_seeds.split(",") if token.strip()],
            "input_json_path": copied_disco_json,
            "ligand_sdf": copied_ligand_sdf,
            "ligand_name": args.disco_ligand_name.strip(),
            "na_sequence": _normalize_sequence(args.disco_na_sequence),
            "checkpoint_path": args.disco_checkpoint_path.strip(),
            "use_deepspeed_evo_attention": args.disco_use_deepspeed_evo_attention,
            "cutlass_path": args.disco_cutlass_path.strip(),
        },
    }

    if args.backend == "disco" and args.task != "custom_json":
        disco_jobs = _build_disco_jobs(
            task=args.task,
            target_lengths=target_lengths,
            ligand_path=copied_ligand_sdf,
            ligand_name=args.disco_ligand_name,
            na_sequence=args.disco_na_sequence,
        )
        disco_input_path = input_dir / "disco_input.json"
        disco_input_path.write_text(json.dumps(disco_jobs, indent=2), encoding="utf-8")
        request["disco"]["compiled_input_json"] = str(disco_input_path)

    output_path = Path(args.output).resolve()
    output_path.write_text(json.dumps(request, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
