"""Memory-safe entrypoint for five-way OF3 diversity runs.

The pinned ConforNets runner defaults to a batched OF3 evaluation chunk size of
four. Five simultaneous ConforNets then exceed a 24 GiB device even though the
same immutable checkpoint and request are valid. This wrapper changes only the
OF3 evaluation memory setting; it does not alter model weights, seeds,
coordinate cardinality, or output semantics.
"""
from __future__ import annotations

import os
import runpy
import sys

# Reduce allocator fragmentation before torch/OpenFold3 is imported.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from openfold3.projects.of3_all_atom.project_entry import ModelUpdate
import confornet.core.of3 as of3

_original_load_model = of3.load_model


def _memory_safe_load_model(checkpoint_path, model_update=None, device=None, subsampled_all_msa_rows=None):
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
    return _original_load_model(
        checkpoint_path,
        model_update=update,
        device=device,
        subsampled_all_msa_rows=subsampled_all_msa_rows,
    )


of3.load_model = _memory_safe_load_model

# The pinned script imports load_model from confornet.core.of3 after this
# patch, so it receives the bounded memory configuration transparently.
sys.argv[0] = "/opt/confornets/scripts/run_diversity.py"
runpy.run_path("/opt/confornets/scripts/run_diversity.py", run_name="__main__")
