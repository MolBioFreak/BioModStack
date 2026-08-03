"""Memory-safe entrypoint for five-way OF3 diversity runs.

The pinned runner batches all k ConforNets through the OF3 pairformer. For
k=5 that batch allocation exceeds even the 32 GiB lane. This wrapper keeps
model/checkpoint/objective/seed semantics but evaluates the mathematically
equivalent pairwise loss one ConforNet at a time.
"""
from __future__ import annotations

import copy
import os
import runpy
import sys

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from openfold3.projects.of3_all_atom.project_entry import ModelUpdate
from openfold3.core.utils.tensor_utils import tensor_tree_map

import confornet.core.of3 as of3
import confornet.inference.diversity as diversity
from confornet.core.diffusion import diffusion_sample
from confornet.core.losses import distogram_to_cdf
from confornet.core.trunk import run_trunk_with_confornet

_original_load_model = of3.load_model


def _memory_safe_load_model(
    checkpoint_path,
    model_update=None,
    device=None,
    subsampled_all_msa_rows=None,
):
    update = ModelUpdate(
        custom={
            "settings": {
                "blocks_per_ckpt": 1,
                "ckpt_intermediate_steps": True,
                "memory": {
                    "train": {"chunk_size": 1, "use_deepspeed_evo_attention": True},
                    "eval": {"chunk_size": 1, "use_deepspeed_evo_attention": True},
                },
            },
            "architecture": {
                "msa": {"msa_module": {"tune_chunk_size": False}},
                "pairformer": {"tune_chunk_size": False},
                "template": {"template_pair_stack": {"tune_chunk_size": False}},
                "diffusion_module": {"diffusion_conditioning": {"tune_chunk_size": False}},
            },
        }
    )
    model = _original_load_model(
        checkpoint_path,
        model_update=update,
        device=device,
        subsampled_all_msa_rows=subsampled_all_msa_rows,
    )
    model.requires_grad_(False)
    return model


def _single_objective_output(
    model,
    confornet,
    query,
    objective,
    noise_schedule,
    fixed_noise,
    device,
    num_recycles,
):
    si_input, si_trunk, zij_trunk = run_trunk_with_confornet(
        model,
        query.batch,
        confornet=confornet,
        num_recycles=num_recycles,
    )
    si_input = si_input.unsqueeze(1)
    si_trunk = si_trunk.unsqueeze(1)
    zij_trunk = zij_trunk.unsqueeze(1)
    if objective is diversity.ObjectiveType.DIST_CDF_MSE:
        distogram_logits = model.aux_heads.distogram(z=zij_trunk.squeeze(1))
        return distogram_to_cdf(distogram_logits)

    batch = copy.deepcopy(query.batch)
    ref_space_uid_to_perm = batch.pop("ref_space_uid_to_perm", None)
    loss_weights = batch.pop("loss_weights", None)
    batch_exp = tensor_tree_map(
        lambda t: t.unsqueeze(1) if t.dim() > 0 else t,
        batch,
    )
    batch_exp["ref_space_uid_to_perm"] = ref_space_uid_to_perm
    if loss_weights is not None:
        batch_exp["loss_weights"] = loss_weights
    return diffusion_sample(
        diffusion_module=model.diffusion_module,
        batch=batch_exp,
        si_input=si_input,
        si_trunk=si_trunk,
        zij_trunk=zij_trunk,
        noise_schedule=noise_schedule,
        no_samples=1,
        initial_noise=fixed_noise,
    )


def _streaming_diversity_training_step(
    model,
    confornet_manager,
    query,
    noise_schedule,
    fixed_noise,
    device,
    num_recycles=10,
    objective=diversity.ObjectiveType.COORD_MSE,
):
    """Exact pairwise loss with one ConforNet resident in the pairformer at a time."""
    k = confornet_manager.k
    confornet_manager.optimizer.zero_grad()
    total_value = 0.0

    with torch.no_grad():
        references = [
            _single_objective_output(
                model, confornet, query, objective, noise_schedule,
                fixed_noise, device, num_recycles,
            )
            for confornet in confornet_manager.confornets
        ]

    for current_index, current_confornet in enumerate(confornet_manager.confornets):
        current = _single_objective_output(
            model, current_confornet, query, objective, noise_schedule,
            fixed_noise, device, num_recycles,
        )
        pair_loss = None
        for reference_index, reference in enumerate(references):
            if reference_index == current_index:
                continue
            term = (current - reference).square().mean()
            pair_loss = term if pair_loss is None else pair_loss + term
        if pair_loss is None:
            raise RuntimeError("streaming diversity requires at least two ConforNets")
        pair_loss = pair_loss / max(1, k - 1)
        pair_loss.backward()
        total_value += float(pair_loss.detach().cpu())
        del current

    del references

    for confornet in confornet_manager.confornets:
        torch.nn.utils.clip_grad_norm_(confornet.parameters(), confornet_manager.grad_clip)
    confornet_manager.optimizer.step()

    loss_value = total_value / max(1, k)
    return loss_value, None, {
        "loss": loss_value,
        objective.metric_key: -loss_value,
        "lr": confornet_manager.get_lr(),
    }


of3.load_model = _memory_safe_load_model
diversity.diversity_training_step = _streaming_diversity_training_step

sys.argv[0] = "/opt/confornets/scripts/run_diversity.py"
runpy.run_path("/opt/confornets/scripts/run_diversity.py", run_name="__main__")
