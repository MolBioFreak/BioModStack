# Fold-CP True Distributed Data-Plane Recovery Spec

> For Hermes: Use subagent-driven-development skill to implement this plan task-by-task after each phase is narrowed into PR-sized tasks.

Goal: turn BioModStack Fold-CP from a mixture of true CP, shared-cache publication, and spill-contract scaffolding into an auditable true distributed Boltz2 data-plane for large targets.

Architecture: keep `true-distributed-context-parallel` as the only path allowed to claim true context parallelism. Treat large-protein plan/store, shared-cache, metadata, DRAM-spill, and coordinator bundle flows as legacy/scaffold until they call into the same live multi-rank `boltz.distributed.main predict` path and partition/stream model state before final artifacts exist.

Tech stack: BioModStack Nextflow launcher, BioModStack FastAPI job normalization, external `/home/dalab/tmp/boltz-cp` torch.distributed/DTensor runtime, PyTorch DTensor/DeviceMesh, Boltz2 distributed model/layers.

---

## Current truth state

1. True CP exists only through `true-distributed-context-parallel`:
   - BioModStack module launches `python -m torch.distributed.run --nproc_per_node $NPROC src/boltz/distributed/main.py predict ... --size_cp $SIZE_CP`.
   - External runtime initializes `DistributedManager`, validates `WORLD_SIZE == size_dp * size_cp`, creates DP/CP meshes, builds `Boltz2InferenceDataModuleDTensor`, wraps `Boltz2` as `Boltz2Distributed`, and calls `trainer.predict`.

2. Large-protein spill/plan-store is not integrated into that live predictor:
   - `large-protein init-plan`, `run-bundle`, `run-local-plan`, and `finalize` are plan/store/contract surfaces.
   - Those surfaces can prove spill operations and publish bundle artifacts, but they do not prove live multi-rank Boltz2 prediction.

3. Feature holes remain in the true CP path:
   - constraints, templates, and affinity features are not fully supported in distributed v2.
   - preprocessing is CP-rank-zero staged before DTensor scatter.
   - output writing is CP-rank-zero oriented.

4. Triangle attention is the current data-plane blocker:
   - CP4 reference run physically reached distributed prediction and OOMed in triangular attention.
   - A trifast run failed because the deployed runtime does not have trifast installed.

5. CP4 launch is real but CP4 target success is not achieved:
   - post-fix CP4 proved `nproc_per_node=4`, `size_cp=4`, `CUDA_VISIBLE_DEVICES=0,2,3,1`, `torch.distributed.run`, and rank/elastic failure evidence.
   - duplicate-MSA mismatch is absent after canonical MSA reuse.
   - no terminal CIF/PDB/design artifacts were produced.

---

## Non-negotiable acceptance criteria

A run may be called true distributed CP only if all of these are true:

1. Launch plane:
   - backend is `true-distributed-context-parallel`.
   - launch manifest records `launcher=torch.distributed.run` or equivalent torchrun.
   - `nproc_per_node >= 2` for a multi-GPU claim.
   - `size_cp >= 4` for CP4 claims.
   - `WORLD_SIZE == size_dp * size_cp` in the distributed runtime.

2. Data plane:
   - runtime enters DTensor/DeviceMesh code.
   - logs show multi-rank execution (`rank`, `LOCAL_RANK`, `world_rank`, `cp_rank`).
   - model prediction happens before final artifacts exist; no serial full-prediction prerequisite.

3. Science plane:
   - non-empty final structure artifacts are present (`.cif`/`.pdb` plus confidence/metadata where configured).
   - DB/design ingestion sees at least one terminal design for the job.
   - output is not only preprocessing artifacts or empty manifests.

4. Failure honesty:
   - duplicate-MSA failure, missing optional backend, distributed OOM, and no-output failures must be classified separately.
   - no silent fallback from missing `trifast` to `reference`; fail closed with a preflight diagnostic.

---

## Phase 1: tighten launch-surface truth and preflight failures

Objective: prevent known-invalid runs and preserve enough evidence to classify failures without digging through volatile logs.

Files:
- Modify: `platform/api/tests/test_boltz_cp_experimental.py`
- Modify: `platform/api/services/nextflow.py`
- Modify: `platform/api/config/models/boltz_cp_experimental.yaml`
- Modify: `platform/api/config/templates/boltz_cp_experimental.yaml`
- Modify: `platform/api/tests/test_boltz_cp_experimental_workflow_contract.py`
- Modify: `modules/boltz_cp_experimental.nf`

Tasks:

1. Add public `triattn_backend` model/template param.
   - Default: `reference`.
   - Enum at least: `reference`, `trifast`, `cueq` if runtime supports it.
   - Description must say `trifast` requires installed kernels and fails closed if absent.

2. Map canonical `triattn_backend` to `bcp_triattn_backend` in `build_nextflow_command`.

3. Add a true-CP preflight for `trifast`.
   - If `BCP_TRIATTN_BACKEND=trifast`, run a small import/spec check inside the exact Boltz Python environment before launching torchrun.
   - If unavailable, write `true_cp_failure_diagnostics.json` with:
     - `stage: triattn-backend-preflight`
     - `triattn_backend: trifast`
     - `kind: MissingTriangleAttentionBackend`
     - `is_true_distributed_context_parallel: false` if torchrun was not launched yet
     - clear install/remediation text.
   - Exit nonzero before burning all four ranks.

4. Preserve launch manifest even for preflight failures.

5. Fix the corrupted context-spill token variables in `BuildBoltzCPPlanManifest`.
   - The module must use `${contextTileTokens}`, `${contextKeyTileTokens}`, and `${contextQueryTileTokens}`.
   - Tests must reject literal `cont...` truncation artifacts.

Verification:
- `uv run --group dev python -m pytest tests/test_boltz_cp_experimental.py tests/test_boltz_cp_experimental_workflow_contract.py -q`
- `nextflow run workflows/boltz_cp_experimental.nf -preview ... --bcp_backend true-distributed-context-parallel --bcp_triattn_backend reference`
- `nextflow run workflows/boltz_cp_experimental.nf -preview ... --bcp_backend true-distributed-context-parallel --bcp_triattn_backend trifast`

---

## Phase 2: make the CP4 memory failure measurable, not anecdotal

Objective: turn the reference-backend CP4 OOM into rank-level memory telemetry and a reproducible reducer.

Files:
- Modify external Boltz-CP runtime in `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/models/boltz2.py`
- Modify/read distributed triangular attention files under `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/layers/`
- Add/modify regression tests under `/home/dalab/tmp/boltz-cp/regression_tests/`

Tasks:

1. Add rank-level memory snapshots around Pairformer / triangular attention:
   - before trunk
   - before each pairformer block
   - before and after triangular attention
   - on OOM exception

2. Persist memory diagnostics to a JSON artifact per rank.

3. Include token count, pair tensor shape, CP grid coordinates, rank/global GPU mapping, selected tri-attn backend, and attempted allocation if available.

4. Add a tiny synthetic distributed regression that asserts diagnostic JSON is produced on an intentional OOM or simulated OOM path.

Acceptance:
- CP4 failure artifact identifies which tensor/path retains full-size memory and whether the OOM is due incomplete sharding, imbalance, or backend allocation strategy.

---

## Phase 3: feature-support fail-closed matrix

Objective: do not let unsupported constraints/templates/affinity silently run as if full Boltz2 behavior were preserved.

Files:
- Modify external distributed input/data module checks.
- Modify BioModStack model/template copy if necessary.
- Add tests in BioModStack and external Boltz-CP.

Tasks:

1. Add a distributed capability matrix artifact to launch output:
   - constraints_supported: false/true
   - templates_supported: false/true
   - affinity_supported: false/true
   - preprocessing_mode: rank_zero_stage_then_scatter
   - output_writer_mode: cp_rank_zero

2. Fail closed when inputs request unsupported constraints/templates/affinity.

3. If a future implementation supports a feature, require tests that prove it enters distributed model execution.

Acceptance:
- Unsupported features fail before prediction with a typed diagnostic, not a successful-but-feature-dropped output.

---

## Phase 4: integrate spill/store with true CP data plane

Objective: retire the split-brain architecture where spill plan/store proves one thing and true CP prediction does another.

Target architecture:

1. Introduce a `ContextStoreRuntime` owned by the distributed predictor, not by the coordinator.
   - It must be constructed inside `run_predict` after `DistributedManager` initialization.
   - It must know the CP mesh and rank coordinates.
   - It must expose APIs for pair/context state residency: VRAM, host DRAM, mmap/NVMe if explicitly enabled.

2. Thread context-store handles into `Boltz2Distributed` / Pairformer layers.
   - No serial full-prediction prerequisite.
   - No post-hoc slicing as a substitute for model execution.

3. Replace operation-level spill contract with model-step evidence:
   - per-layer tile materialization
   - per-rank ownership map
   - load/store counters
   - no placeholder states
   - no full pair/context tensor allocated on every rank unless explicitly documented and accepted.

4. Keep coordinator bundle mode as legacy only.
   - It may remain for diagnostics/publication, but it cannot be named or marketed as true CP.

Acceptance:
- a target that serially OOMs can complete because live model state is partitioned/streamed, not because outputs were sliced after a full prediction.

---

## Phase 5: rerun ladder

Objective: prove progress without wasting full CP4 runs.

Rerun order:

1. Tiny CP4 smoke, reference backend, no MSA, low sampling/recycling.
   - Acceptance: non-empty CIF/PDB artifact.

2. 2+2 duplicated-sequence CP4, reference backend, MSA enabled, reduced sampling/recycling.
   - Acceptance: no duplicate-MSA error and non-empty artifact.

3. Requested target profile, CP4, 200 sampling, 10 recycling, 1 sample, ColabFold API MSA/cache fallback.
   - Acceptance: physical CP4 + non-empty terminal artifacts.

4. If reference OOM persists, do not claim success. Use Phase 2 telemetry to choose one of:
   - install/build a viable triangle-attention backend in the container,
   - reduce per-rank pair memory by real sharding/streaming,
   - route through the integrated context store from Phase 4.

---

## Immediate next implementation slice

Do now:

1. Add tests for `triattn_backend` API/template exposure and Nextflow mapping.
2. Add tests that reject corrupted `cont...` placeholder strings in `modules/boltz_cp_experimental.nf`.
3. Fix those tests.
4. Add typed preflight diagnostics for missing `trifast`.
5. Re-run targeted tests and Nextflow previews.

Do not yet claim the large target is solved. The next runtime claim requires a successful tiny CP4 artifact first.
