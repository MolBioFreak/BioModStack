# Complexa native runtime packaging notices

## Scientific source and licenses

- NVIDIA-BioNeMo/Proteina-Complexa revision `acf55a89a9a7206d5ce9abc0bf2246c3b3829f92`, package 1.1.0. Native source, model/task/search/reward defaults and bundled ColabDesign are unchanged.
- Code: Apache-2.0, with separately licensed bundled components. The image retains `LICENSE` and all `licenses/` documents, including `license_third_party.txt`.
- Six NGC version 1.0 base checkpoints: NVIDIA Open Model License, release April 28, 2025. Attribution: **Licensed by NVIDIA Corporation under the NVIDIA Open Model License**. This is not an inference-result or experimental-validation claim.
- CUDA base has NVIDIA's accompanying container/toolkit licenses. Python distributions retain their installed license metadata; TMOL is pinned at upstream's `d8a6f7f9649d36e74440bca25246ee7c467ce490`.

## Dependency decisions

The upstream sequential builder successfully exits with inconsistent package metadata and an unusable import closure. This recipe replaces moving resolution with exact distribution versions and hashes, exact TMOL/source revisions, and pinned build tools. It does not run a minimal substitute environment or download optional oracle checkpoints.

- Torch stays **2.7.0**, but uses official **cu128**, paired torchvision 0.22.0/torchaudio 2.7.0 and matching PyG pt27cu128 wheels. The upstream cu126 wheel fails a tiny RTX 5060 Ti sm120 kernel with `no kernel image is available`. Official cu128 lists sm80/sm86/sm90/sm100/sm120 and passes tiny Torch/scatter/kNN probes. No inference was performed; Ampere/Hopper execution is not independently tested here.
- CUDA development base is 12.8.1, not upstream's older 24.08 PyTorch image. It provides the native compiler/toolchain for on-demand extensions without embedding another unused Torch environment.
- Atomworks **2.2.1** retains `AtomSelectionStack`, absent from the unpinned builder's resolved 3.0.4. Biotite **1.6.0** follows the upstream builder's explicit final ligand-compatibility override, despite Atomworks 2.2.1 metadata requesting 1.4.0.
- NumPy **1.26.4** avoids the unpinned builder's NumPy 2 upgrade and meets Graphein's `<2` requirement. SciPy **1.13.1** meets Atomworks' minimum despite Complexa's stale 1.12.0 declaration. Einops **0.8.2** meets Foundry/Atomworks while Complexa declares 0.6.0. These metadata disagreements remain visible; no upstream metadata is rewritten to conceal them.
- Lightning and pytorch-lightning both **2.5.6**, honoring upstream's `<2.6` checkpoint-loading warning. Python-dotenv **1.0.1** and tqdm **4.66.4** preserve Complexa's explicit pins. Bioservices 1.16.0 (Graphein's imported companion) declares tqdm >=4.67.1; its import succeeds with 4.66.4, but this fourth metadata disagreement is reported rather than hidden.
- JAX/JAXlib **0.4.29** preserve upstream's native ColabDesign stack. Chex 0.1.86, Optax 0.2.2, Haiku 0.0.12, Flax 0.9.0, Orbax 0.5.16 and Tensorstore 0.1.64 provide its compatible import closure rather than allowing current releases to replace JAX. **JAX 0.4.29 fails the tiny sm120 GPU probe** (`.target sm_90a cannot be compiled to future architecture`). No silent CPU fallback, JAX upgrade, AF2 replacement, reward removal, or runtime admission gate is added. Protein-binder native AF2 reward/refinement on Blackwell remains a concrete dependency limitation.

## Explicit unrun scope

CPU import/CLI/composition and tiny GPU kernels are not model inference. None of the six checkpoints has been loaded into a model in this task. No AF2/RF3/ESM/MPNN optional checkpoint, broad corpus, or `download --all` was acquired. Native oracle scoring, relaxation, full model sampling, native output quality and remote scientific execution remain unrun. Native SC/DSSP/HBPLUS/Foldseek/MMseqs external binary availability is not supplied by this Python recipe. No live runtime selector/environment/service is changed.
