"""Run pinned ConforNets diversity with bounded gradient execution.

The adapter keeps the upstream objective, optimizer, checkpoint schedule, and
rollout implementation. For execution batches smaller than scientific k, it
recomputes deterministic objective outputs and accumulates every directed
pair contribution before the single upstream optimizer update.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import runpy
import sys
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
import torch.nn.functional as F
from openfold3.core.utils.tensor_utils import tensor_tree_map

import confornet.inference.diversity as diversity
from confornet.core.diffusion import create_noise_schedule, diffusion_sample
from confornet.core.losses import aligned_mse_loss, distogram_to_cdf
from confornet.core.trunk import (
    _run_cycle,
    _run_pre_pairformer,
)
from confornet.data import PredictionQuery
from confornet.utils.confidence import save_confidence
from confornet.utils.csv_io import write_training_loss_csv
from confornet.utils.io import write_cif

logger = logging.getLogger(__name__)

_EXECUTION_WIDTH_ENV = "BMS_CONFORNETS_EXECUTION_BATCH_WIDTH"
_H100_CLASS_MIN_BYTES = 70 * 1024**3
_original_training_step = diversity.diversity_training_step
_original_run_seed = diversity.run_diversity_seed
_original_generate_rollout_samples = diversity.generate_rollout_samples


def _configured_width(maximum: int) -> int | None:
    configured = os.environ.get(_EXECUTION_WIDTH_ENV, "").strip()
    if not configured:
        return None
    try:
        width = int(configured)
    except ValueError as exc:
        raise ValueError(
            f"{_EXECUTION_WIDTH_ENV} must be a positive integer"
        ) from exc
    if width < 1:
        raise ValueError(f"{_EXECUTION_WIDTH_ENV} must be a positive integer")
    return min(maximum, width)


def _execution_width(k: int, device: torch.device) -> int:
    """Select runtime width independently from the scientific ConforNet count."""
    configured = _configured_width(k)
    if configured is not None:
        return configured
    if k <= 2:
        return k
    if device.type == "cuda":
        total_memory = torch.cuda.get_device_properties(device).total_memory
        if total_memory >= _H100_CLASS_MIN_BYTES:
            return min(k, 2)
    return 1


def _prepare_shared_trunk(model, batch: dict, num_recycles: int) -> dict:
    """Run the exact ConforNets trunk prefix that is independent of scientific k."""
    num_cycles = num_recycles + 1
    mem = model._get_mode_mem_settings()
    with torch.no_grad():
        s_input, s_init, z_init = model.input_embedder(
            batch=batch,
            inplace_safe=False,
            use_high_precision_attention=True,
        )
        token_mask = batch["token_mask"]
        pair_mask = token_mask[..., None] * token_mask[..., None, :]
        s = torch.zeros_like(s_init)
        z = torch.zeros_like(z_init)
        for _cycle_no in range(num_cycles - 1):
            s, z = _run_cycle(
                model,
                batch,
                s_input,
                s_init,
                z_init,
                s,
                z,
                token_mask,
                pair_mask,
                mem,
            )
        s, z = _run_pre_pairformer(
            model,
            batch,
            s_input,
            s_init,
            z_init,
            s,
            z,
            pair_mask,
            mem,
        )
    return {
        "s_input": s_input.detach(),
        "s": s.detach(),
        "z": z.detach(),
        "token_mask": token_mask.detach(),
        "pair_mask": pair_mask.detach(),
        "mem": mem,
    }


def _run_pairformer_chunk(model, shared: dict, confornets: list) -> tuple:
    """Run the exact pinned pairformer suffix for one execution chunk."""
    width = len(confornets)
    z_batched = torch.cat([confornet(shared["z"]) for confornet in confornets], dim=0)
    s_batched = shared["s"].repeat(width, 1, 1)
    token_mask = shared["token_mask"].expand(width, -1)
    pair_mask = shared["pair_mask"].expand(width, -1, -1)
    mem = shared["mem"]
    s_batched, z_batched = model.pairformer_stack(
        s=s_batched,
        z=z_batched,
        single_mask=token_mask.to(dtype=z_batched.dtype),
        pair_mask=pair_mask.to(dtype=s_batched.dtype),
        chunk_size=mem.chunk_size,
        use_deepspeed_evo_attention=False,
        use_cueq_triangle_kernels=mem.use_cueq_triangle_kernels,
        use_lma=mem.use_lma,
        inplace_safe=False,
        _mask_trans=True,
    )
    si_input = shared["s_input"].expand(width, -1, -1).unsqueeze(1)
    return si_input, s_batched.unsqueeze(1), z_batched.unsqueeze(1)


def _objective_outputs(
    model,
    confornets: list,
    query,
    shared: dict,
    noise_schedule,
    fixed_noise,
    objective,
) -> torch.Tensor:
    """Produce one pinned upstream objective output for each network in a chunk."""
    width = len(confornets)
    si_input, si_trunk, zij_trunk = _run_pairformer_chunk(model, shared, confornets)
    if objective is diversity.ObjectiveType.COORD_MSE:
        if fixed_noise is None or noise_schedule is None:
            raise ValueError(
                "coord_mse objective requires fixed_noise and noise_schedule"
            )
        batch = query.batch
        ref_space_uid_to_perm = batch.get("ref_space_uid_to_perm")
        loss_weights = batch.get("loss_weights")
        batch_exp = tensor_tree_map(
            lambda tensor: tensor.unsqueeze(1) if tensor.dim() > 0 else tensor,
            {
                key: value
                for key, value in batch.items()
                if key not in {"ref_space_uid_to_perm", "loss_weights"}
            },
        )

        def expand_for_width(tensor):
            if tensor.dim() == 0:
                return tensor
            return tensor.repeat(width, *([1] * (tensor.dim() - 1)))

        batch_width = tensor_tree_map(expand_for_width, batch_exp)
        batch_width["ref_space_uid_to_perm"] = ref_space_uid_to_perm
        if loss_weights is not None:
            batch_width["loss_weights"] = loss_weights
        return diffusion_sample(
            diffusion_module=model.diffusion_module,
            batch=batch_width,
            si_input=si_input,
            si_trunk=si_trunk,
            zij_trunk=zij_trunk,
            noise_schedule=noise_schedule,
            no_samples=1,
            initial_noise=fixed_noise.repeat(width, 1, 1, 1),
        )
    zij = zij_trunk.squeeze(1)
    return distogram_to_cdf(model.aux_heads.distogram(z=zij))


def _flatten_coordinates(output: torch.Tensor) -> torch.Tensor:
    if output.dim() == 3 and output.shape[0] == 1:
        return output.squeeze(0)
    return output.reshape(-1, 3)


def _directed_pair_term(
    current: torch.Tensor,
    reference: torch.Tensor,
    objective,
) -> torch.Tensor:
    target = reference.to(device=current.device, dtype=current.dtype)
    if objective is diversity.ObjectiveType.COORD_MSE:
        return aligned_mse_loss(
            _flatten_coordinates(current),
            _flatten_coordinates(target),
        )
    return F.mse_loss(current, target)


def _bounded_training_step(
    model,
    confornet_manager,
    query,
    noise_schedule,
    fixed_noise,
    device,
    num_recycles=10,
    objective=diversity.ObjectiveType.COORD_MSE,
):
    """Accumulate the exact coupled diversity gradient in bounded chunks."""
    k = confornet_manager.k
    width = _execution_width(k, device)
    if width >= k:
        return _original_training_step(
            model,
            confornet_manager,
            query,
            noise_schedule,
            fixed_noise,
            device,
            num_recycles,
            objective,
        )

    logger.info(
        "Bounded ConforNets training: scientific_k=%d execution_batch_width=%d",
        k,
        width,
    )
    shared = _prepare_shared_trunk(model, query.batch, num_recycles)
    references: list[torch.Tensor] = []
    with torch.no_grad():
        for start in range(0, k, width):
            outputs = _objective_outputs(
                model,
                confornet_manager.confornets[start : start + width],
                query,
                shared,
                noise_schedule,
                fixed_noise,
                objective,
            )
            references.extend(output.detach().to("cpu") for output in outputs)
            del outputs
    if len(references) != k:
        raise RuntimeError("bounded ConforNets reference cardinality mismatch")

    directed_pair_count = k * (k - 1)
    undirected_pair_count = directed_pair_count // 2
    if directed_pair_count < 2:
        raise RuntimeError("ConforNets diversity requires at least two networks")
    # DIST_CDF_MSE has one symmetric scalar per unordered pair upstream. The
    # bounded recomputation expresses its two endpoint gradients as two
    # directed terms with detached targets. Both terms therefore retain the
    # upstream unordered-pair denominator. COORD_MSE already defines two
    # directed aligned terms and divides by k * (k - 1).
    gradient_denominator = (
        directed_pair_count
        if objective is diversity.ObjectiveType.COORD_MSE
        else undirected_pair_count
    )

    confornet_manager.optimizer.zero_grad()
    directed_metric_sum = 0.0
    for start in range(0, k, width):
        outputs = _objective_outputs(
            model,
            confornet_manager.confornets[start : start + width],
            query,
            shared,
            noise_schedule,
            fixed_noise,
            objective,
        )
        chunk_sum = None
        for chunk_index, current in enumerate(outputs):
            current_index = start + chunk_index
            for reference_index, reference in enumerate(references):
                if current_index == reference_index:
                    continue
                term = _directed_pair_term(current, reference, objective)
                chunk_sum = term if chunk_sum is None else chunk_sum + term
        if chunk_sum is None:
            raise RuntimeError("bounded ConforNets chunk has no pairwise terms")
        directed_metric_sum += float(chunk_sum.detach().cpu())
        (-chunk_sum / gradient_denominator).backward()
        del outputs, chunk_sum

    for confornet in confornet_manager.confornets:
        torch.nn.utils.clip_grad_norm_(
            confornet.parameters(),
            confornet_manager.grad_clip,
        )
    confornet_manager.optimizer.step()

    loss_value = -directed_metric_sum / directed_pair_count
    metrics = {
        "loss": loss_value,
        objective.metric_key: -loss_value,
        "lr": confornet_manager.get_lr(),
    }
    detached_outputs = (
        torch.stack(references)
        if objective is diversity.ObjectiveType.COORD_MSE
        else None
    )
    return loss_value, detached_outputs, metrics


def _generate_rollout_samples_bounded(
    model,
    confornet,
    query,
    device,
    num_samples: int,
    num_steps: int,
    num_recycles: int,
    *,
    compute_confidence: bool,
):
    """Delegate final rollout batching to the pinned upstream implementation."""
    if num_samples < 1:
        raise ValueError("ConforNets rollout sample count must be positive")
    return _original_generate_rollout_samples(
        model,
        confornet,
        query,
        device,
        num_samples,
        num_steps,
        num_recycles,
        compute_confidence=compute_confidence,
    )


def _canonical_selection_context() -> tuple[tuple[dict, ...], str] | None:
    context_name = os.environ.get("BMS_CM_COORDINATE_CONTEXT", "").strip()
    if not context_name:
        return None
    context = json.loads(Path(context_name).read_text(encoding="utf-8"))
    coordinates = context.get("coordinates")
    target_id = context.get("target_id")
    if not isinstance(coordinates, list) or not coordinates:
        raise RuntimeError("canonical selected ConforNets coordinates are missing")
    if not all(isinstance(coordinate, dict) for coordinate in coordinates):
        raise RuntimeError("canonical selected ConforNets coordinates are malformed")
    if not isinstance(target_id, str) or not target_id:
        raise RuntimeError("canonical selected ConforNets target is missing")
    return tuple(coordinates), target_id


def _selected_samples(
    coordinates: tuple[dict, ...],
    *,
    args,
    target_id: str,
    test_case_id: str,
    run_index: int,
) -> dict[tuple[int, int], tuple[int, ...]]:
    expected_fields = {
        "backend",
        "target_id",
        "task",
        "test_case_id",
        "reference_id",
        "run_index",
        "saved_step",
        "confornet_index",
        "sample_index",
    }
    selected: dict[tuple[int, int], list[int]] = {}
    seen: set[tuple] = set()
    save_steps = set(args.save_steps)
    for coordinate in coordinates:
        if set(coordinate) != expected_fields:
            raise RuntimeError("canonical ConforNets coordinate shape is invalid")
        identity = tuple(
            coordinate[field]
            for field in (
                "target_id",
                "task",
                "test_case_id",
                "reference_id",
                "run_index",
                "saved_step",
                "confornet_index",
                "sample_index",
            )
        )
        if identity in seen:
            raise RuntimeError("canonical ConforNets coordinate is duplicated")
        seen.add(identity)
        if (
            coordinate["backend"] != "confornets"
            or coordinate["target_id"] != target_id
            or coordinate["task"] != "diversity"
            or coordinate["reference_id"] is not None
            or coordinate["test_case_id"] != test_case_id
            or not isinstance(coordinate["target_id"], str)
            or not coordinate["target_id"]
        ):
            raise RuntimeError("canonical ConforNets diversity coordinate is invalid")
        integer_fields = (
            "run_index",
            "saved_step",
            "confornet_index",
            "sample_index",
        )
        if any(
            isinstance(coordinate[field], bool)
            or not isinstance(coordinate[field], int)
            for field in integer_fields
        ):
            raise RuntimeError("canonical ConforNets coordinate index is invalid")
        if (
            coordinate["run_index"] < 0
            or coordinate["run_index"] >= args.num_runs
            or coordinate["saved_step"] not in save_steps
            or coordinate["confornet_index"] < 0
            or coordinate["confornet_index"] >= args.k_confornets
            or coordinate["sample_index"] < 0
            or coordinate["sample_index"] >= args.num_samples
        ):
            raise RuntimeError("canonical ConforNets coordinate exceeds runtime controls")
        if coordinate["run_index"] == run_index:
            key = (coordinate["saved_step"], coordinate["confornet_index"])
            selected.setdefault(key, []).append(coordinate["sample_index"])

    normalized: dict[tuple[int, int], tuple[int, ...]] = {}
    for key, values in selected.items():
        ordered = tuple(sorted(values))
        if ordered != tuple(range(len(ordered))):
            raise RuntimeError(
                "selected ConforNets samples must be contiguous from sample zero"
            )
        normalized[key] = ordered
    return normalized


def _run_selected_diversity_seed(
    model,
    query,
    query_id,
    output_dir,
    seed,
    args,
    device,
    *,
    save_steps,
    test_case_id,
):
    """Run the pinned seed loop and emit only canonical selected rollouts."""
    canonical = _canonical_selection_context()
    if canonical is None:
        return _original_run_seed(
            model,
            query,
            query_id,
            output_dir,
            seed,
            args,
            device,
            save_steps=save_steps,
            test_case_id=test_case_id,
        )
    coordinates, target_id = canonical
    selected = _selected_samples(
        coordinates,
        args=args,
        target_id=target_id,
        test_case_id=test_case_id,
        run_index=seed,
    )

    seed_dir = Path(output_dir) / f"seed_{seed}"
    seed_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(seed * 1000)
    objective = diversity.ObjectiveType(args.objective)
    noise_cfg = model.config.architecture.noise_schedule
    c_z = model.config.architecture.shared.c_z
    manager = diversity.KConforNetManager(
        args.k_confornets,
        c_z,
        device,
        args.lr,
        args.grad_clip,
    )

    noise_schedule = None
    fixed_noise = None
    if objective.requires_diffusion:
        noise_schedule = create_noise_schedule(
            no_rollout_steps=1,
            sigma_data=noise_cfg.sigma_data,
            s_max=noise_cfg.s_max,
            s_min=noise_cfg.s_min,
            p=noise_cfg.p,
            dtype=torch.bfloat16,
            device=device,
        )
        num_atoms = query.batch["atom_mask"].shape[-1]
        fixed_noise = torch.randn(
            (1, 1, num_atoms, 3),
            device=device,
            dtype=torch.bfloat16,
        )

    loss_rows = []
    model.eval()
    for step in range(args.max_steps):
        batch_copy = copy.deepcopy(query.batch)
        step_query = PredictionQuery(
            query.atom_array,
            query.sequence,
            batch_copy,
            query.alignment_resid_ranges,
            query.metric_resid_ranges,
        )
        loss, _, metrics = diversity.diversity_training_step(
            model,
            manager,
            step_query,
            noise_schedule,
            fixed_noise,
            device,
            args.num_recycles,
            objective,
        )
        loss_rows.append(
            (
                step,
                metrics["loss"],
                metrics[objective.metric_key],
                metrics["lr"],
            )
        )
        if step % args.log_every == 0:
            logger.info(
                "Seed %d, step %d: loss=%.4f %s=%.4f",
                seed,
                step,
                loss,
                objective.metric_key,
                metrics[objective.metric_key],
            )
        if step > 0 and step % 5 == 0:
            manager.halve_lr()
        if step not in save_steps:
            continue
        for confornet_index, confornet in enumerate(manager.confornets):
            # Preserve the full configured candidate-pool RNG order. Returned
            # output selection controls publication only; it never changes the
            # upstream rollout width or skips an earlier configured rollout.
            samples, confidence = _generate_rollout_samples_bounded(
                model,
                confornet,
                query,
                device,
                args.num_samples,
                args.num_steps,
                args.num_recycles,
                compute_confidence=args.compute_confidence,
            )
            prefix = f"{query_id}_step_{step}_confornet_{confornet_index}"
            sample_indices = selected.get((step, confornet_index), ())
            for sample_index in sample_indices:
                output_path = seed_dir / f"{prefix}_sample_{sample_index}.cif"
                write_cif(
                    query.atom_array,
                    samples[sample_index],
                    output_path,
                )
                __import__(
                    "confornet.utils.cm_coordinate_ledger",
                    fromlist=["emit_coordinate"],
                ).emit_coordinate(
                    path=output_path,
                    target_id=query_id,
                    task="diversity",
                    test_case_id=test_case_id,
                    reference_id=None,
                    run_index=seed,
                    saved_step=step,
                    confornet_index=confornet_index,
                    sample_index=sample_index,
                )
            if sample_indices:
                save_confidence(
                    (
                        {
                            key: value[: len(sample_indices)]
                            for key, value in confidence.items()
                        }
                        if confidence is not None
                        else None
                    ),
                    seed_dir,
                    file_prefix=prefix,
                    save_full=args.save_full_confidence,
                )
            torch.save(confornet.state_dict(), seed_dir / f"{prefix}.pt")

    write_training_loss_csv(
        seed_dir / "training_loss.csv",
        loss_rows,
        extra_cols=[(objective.metric_key, ".6f"), ("lr", ".6e")],
    )
    (seed_dir / "objective.txt").write_text(objective.value + "\n")
    return None


diversity.diversity_training_step = _bounded_training_step
diversity.run_diversity_seed = _run_selected_diversity_seed

repo_name = os.environ.get("BMS_CONFORNETS_REPO_PATH", "").strip()
if not repo_name:
    raise RuntimeError("bounded ConforNets execution has no selected repository")
repo_root = Path(repo_name).resolve(strict=True)
if Path.cwd().resolve(strict=True) != repo_root:
    raise RuntimeError("bounded ConforNets working tree differs from selected repository")
upstream_driver = (repo_root / "scripts" / "run_diversity.py").resolve(strict=True)
upstream_driver.relative_to(repo_root)
if not upstream_driver.is_file() or upstream_driver.is_symlink():
    raise RuntimeError("selected upstream ConforNets diversity driver is unavailable")
sys.argv[0] = str(upstream_driver)
runpy.run_path(str(upstream_driver), run_name="__main__")
