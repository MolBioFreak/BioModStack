#!/usr/bin/env python3
"""
Run Protenix inference from the repository code path with atom-confidence dumps enabled.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _parse_int_list(raw: str | None) -> list[int]:
    values: list[int] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if token:
            values.append(int(token))
    return values or [42]


def _parse_csv(raw: str | None) -> list[str]:
    values: list[str] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if token:
            values.append(token)
    return values


def _install_exact_template_duplicate_allowlist(allowed_pdb_ids: list[str]) -> None:
    normalized = {token.strip().lower() for token in allowed_pdb_ids if token.strip()}
    if not normalized:
        return

    from protenix.data.template import template_utils

    existing = getattr(template_utils.TemplateHitFilter, "_bms_allow_exact_duplicate_pdb_ids", None)
    if existing == normalized:
        return

    original = getattr(template_utils.TemplateHitFilter, "_bms_original_assess_hit", None)
    if original is None:
        original = template_utils.TemplateHitFilter._assess_hit
        template_utils.TemplateHitFilter._bms_original_assess_hit = original

    def _wrapped_assess_hit(
        self,
        hit,
        pdb_code,
        query_seq,
        cutoff,
        max_subseq_ratio: float = 0.95,
        min_align_ratio: float = 0.1,
    ):
        effective_ratio = max_subseq_ratio
        if str(pdb_code).lower() in normalized:
            # Anchored target conditioning needs the exact source template,
            # which Protenix otherwise rejects as a duplicate of the query.
            effective_ratio = max(effective_ratio, 1.01)
        return original(
            self,
            hit,
            pdb_code,
            query_seq,
            cutoff,
            max_subseq_ratio=effective_ratio,
            min_align_ratio=min_align_ratio,
        )

    template_utils.TemplateHitFilter._assess_hit = _wrapped_assess_hit
    template_utils.TemplateHitFilter._bms_allow_exact_duplicate_pdb_ids = normalized
    print(
        "[run_protenix_inference] Allowing exact duplicate templates for PDB IDs:",
        ",".join(sorted(normalized)),
    )


def _apply_default_params(args: argparse.Namespace) -> None:
    if not args.use_default_params:
        return
    if args.model_name != "protenix-v2":
        raise RuntimeError("Only the protenix-v2 checkpoint is supported")


def _install_coordinate_ledger(ledger_path: Path, context_path: Path) -> None:
    """Instrument the pinned dumper with runtime seed/sample identities."""

    from runner.dumper import DataDumper

    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context.get("schema_name") != "cm_protenix_coordinate_context" or context.get("schema_version") != 1:
        raise RuntimeError("invalid canonical Protenix coordinate context")
    target_by_pdb_id = context.get("target_by_pdb_id")
    if not isinstance(target_by_pdb_id, dict) or not target_by_pdb_id:
        raise RuntimeError("canonical Protenix coordinate context has no target mapping")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    original = DataDumper.dump_predictions

    def digest(path: Path) -> tuple[str, int]:
        value = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                value.update(chunk)
                size += len(chunk)
        return value.hexdigest(), size

    def instrumented(self, pred_dict, dump_dir, pdb_id, atom_array, entity_poly_type, seed):
        sorted_indices = list(self._get_ranker_indices(data=pred_dict))
        original(self, pred_dict, dump_dir, pdb_id, atom_array, entity_poly_type, seed)
        target_id = target_by_pdb_id.get(str(pdb_id))
        if not isinstance(target_id, str) or not target_id:
            raise RuntimeError(f"no canonical target mapping for Protenix task {pdb_id!r}")
        records = []
        prediction_root = Path(dump_dir) / "predictions"
        for rank_position, rank_value in enumerate(sorted_indices):
            rank_index = int(rank_value)
            structure = prediction_root / f"{pdb_id}_sample_{rank_index}.cif"
            confidence = prediction_root / f"{pdb_id}_summary_confidence_sample_{rank_index}.json"
            full_data = prediction_root / f"{pdb_id}_full_data_sample_{rank_index}.json"
            artifacts = []
            for role, path in (
                ("authoritative_cif", structure),
                ("confidence_json", confidence),
                ("full_data_json", full_data),
            ):
                if not path.is_file() or path.is_symlink():
                    raise RuntimeError(f"mandatory Protenix output is missing: {path}")
                file_sha256, size_bytes = digest(path)
                artifacts.append(
                    {
                        "semantic_role": role,
                        "relative_path": path.resolve().relative_to(Path(self.base_dir).resolve()).as_posix(),
                        "sha256": file_sha256,
                        "bytes": size_bytes,
                    }
                )
            records.append(
                {
                    "coordinates": {
                        "backend": "protenix_v2_ensemble",
                        "target_id": target_id,
                        "ordered_seed": int(seed),
                        "sample_index": rank_index,
                    },
                    "backend_rank_position": rank_position,
                    "artifacts": artifacts,
                }
            )
        descriptor = os.open(
            ledger_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
            0o640,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            for record in records:
                payload = memoryview(
                    (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                )
                while payload:
                    written = os.write(descriptor, payload)
                    if written <= 0:
                        raise OSError("short write while appending Protenix coordinate ledger")
                    payload = payload[written:]
            os.fsync(descriptor)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)

    DataDumper.dump_predictions = instrumented


def _seal_coordinate_ledger(ledger_path: Path) -> None:
    os.chmod(ledger_path, 0o440)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Protenix inference with raw confidence dumps enabled.")
    parser.add_argument("--input", required=True, help="Input JSON file or directory.")
    parser.add_argument("--out_dir", default="./output", help="Output directory.")
    parser.add_argument("--seeds", default="101", help="Comma-separated seeds.")
    parser.add_argument("--cycle", type=int, default=10, help="Pairformer cycle number.")
    parser.add_argument("--step", type=int, default=200, help="Diffusion steps.")
    parser.add_argument("--sample", type=int, default=5, help="Number of samples.")
    parser.add_argument("--dtype", default="bf16", help="Inference dtype.")
    parser.add_argument("--model_name", choices=("protenix-v2",), default="protenix-v2", help="Model checkpoint name (V2 only).")
    parser.add_argument("--use_msa", type=_parse_bool, default=True, help="Whether to use MSA for inference.")
    parser.add_argument("--use_default_params", type=_parse_bool, default=False, help="Use recommended default parameters.")
    parser.add_argument("--trimul_kernel", default="cuequivariance", help="Triangle multiplicative update kernel.")
    parser.add_argument("--triatt_kernel", default="cuequivariance", help="Triangle attention kernel.")
    parser.add_argument("--enable_cache", type=_parse_bool, default=True, help="Enable diffusion cache.")
    parser.add_argument("--enable_fusion", type=_parse_bool, default=True, help="Enable efficient fusion.")
    parser.add_argument("--enable_tf32", type=_parse_bool, default=True, help="Enable TF32.")
    parser.add_argument("--msa_server_mode", default="protenix", help="MSA search mode.")
    parser.add_argument("--use_template", type=_parse_bool, default=False, help="Use templates.")
    parser.add_argument("--use_rna_msa", type=_parse_bool, default=False, help="Use RNA MSA.")
    parser.add_argument("--use_seeds_in_json", type=_parse_bool, default=False, help="Use seeds defined in the input JSON.")
    parser.add_argument("--kalign_binary_path", default=None)
    parser.add_argument("--hmmsearch_binary_path", default=None)
    parser.add_argument("--hmmbuild_binary_path", default=None)
    parser.add_argument("--seqres_database_path", default=None)
    parser.add_argument("--nhmmer_binary_path", default=None)
    parser.add_argument("--hmmalign_binary_path", default=None)
    parser.add_argument("--hmmbuild_rna_binary_path", default=None)
    parser.add_argument("--ntrna_database_path", default=None)
    parser.add_argument("--rfam_database_path", default=None)
    parser.add_argument("--rna_central_database_path", default=None)
    parser.add_argument("--nhmmer_n_cpu", type=int, default=None)
    parser.add_argument(
        "--allow_exact_duplicate_template_pdb_ids",
        default="",
        help="Comma-separated PDB IDs whose exact templates should bypass duplicate-query filtering.",
    )
    parser.add_argument("--cm-coordinate-ledger", default=None)
    parser.add_argument("--cm-coordinate-context", default=None)
    args = parser.parse_args()

    _apply_default_params(args)

    if args.allow_exact_duplicate_template_pdb_ids:
        _install_exact_template_duplicate_allowlist(
            _parse_csv(args.allow_exact_duplicate_template_pdb_ids)
        )

    from configs.configs_inference import inference_configs
    from runner.batch_inference import get_default_runner, preprocess_input
    from runner.inference import infer_predict

    if bool(args.cm_coordinate_ledger) != bool(args.cm_coordinate_context):
        raise RuntimeError("canonical coordinate ledger and context must be supplied together")
    if args.cm_coordinate_ledger:
        _install_coordinate_ledger(
            Path(args.cm_coordinate_ledger).resolve(),
            Path(args.cm_coordinate_context).resolve(),
        )

    inference_configs["dump_dir"] = args.out_dir
    runner_options = dict(
        seeds=_parse_int_list(args.seeds),
        n_sample=args.sample,
        dtype=args.dtype,
        model_name=args.model_name,
        use_msa=args.use_msa,
        trimul_kernel=args.trimul_kernel,
        triatt_kernel=args.triatt_kernel,
        enable_cache=args.enable_cache,
        enable_fusion=args.enable_fusion,
        enable_tf32=args.enable_tf32,
        use_template=args.use_template,
        use_rna_msa=args.use_rna_msa,
        use_seeds_in_json=args.use_seeds_in_json,
        kalign_binary_path=args.kalign_binary_path,
    )
    if not args.use_default_params:
        runner_options.update(n_cycle=args.cycle, n_step=args.step)
    runner = get_default_runner(**runner_options)
    runner.init_dumper(
        need_atom_confidence=True,
        sorted_by_ranking_score=runner.configs.sorted_by_ranking_score,
    )

    input_path = Path(args.input).expanduser().resolve()
    if input_path.is_dir():
        infer_jsons = sorted(
            str(file) for file in input_path.rglob("*") if file.is_file() and file.suffix == ".json"
        )
    elif input_path.is_file():
        infer_jsons = [str(input_path)]
    else:
        raise RuntimeError(f"Can not read a special file: {input_path}")

    for infer_json in infer_jsons:
        runner.configs["input_json_path"] = preprocess_input(
            infer_json,
            out_dir=args.out_dir,
            use_msa=args.use_msa,
            use_template=args.use_template,
            use_rna_msa=args.use_rna_msa,
            msa_server_mode=args.msa_server_mode,
            hmmsearch_binary_path=args.hmmsearch_binary_path,
            hmmbuild_binary_path=args.hmmbuild_binary_path,
            seqres_database_path=args.seqres_database_path,
            nhmmer_binary_path=args.nhmmer_binary_path,
            hmmalign_binary_path=args.hmmalign_binary_path,
            hmmbuild_rna_binary_path=args.hmmbuild_rna_binary_path,
            ntrna_database_path=args.ntrna_database_path,
            rfam_database_path=args.rfam_database_path,
            rna_central_database_path=args.rna_central_database_path,
            nhmmer_n_cpu=args.nhmmer_n_cpu,
        )
        infer_predict(runner, runner.configs)

    if args.cm_coordinate_ledger:
        _seal_coordinate_ledger(Path(args.cm_coordinate_ledger).resolve())


if __name__ == "__main__":
    main()
