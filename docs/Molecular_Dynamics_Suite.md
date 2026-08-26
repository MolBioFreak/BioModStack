# BioModStack Molecular Dynamics Suite

## Status and scope

This subsystem turns BioModStack's existing structure-relaxation scaffolding into a restartable, provenance-bearing production molecular-dynamics workflow. Phase 1 intentionally targets **standard protein systems**. It does **not** yet claim validated support for arbitrary ligands, covalent adducts, metals, unusual residues, protein–nucleic-acid complexes, or DRT4's covalent Tyr125-linked nucleotide.

## Engine decision

**Primary production engine: GROMACS 2025.3 with CUDA.**

**Reference/specialized adapter: OpenMM 8.5.2 with CUDA 13.**

A same-system engineering benchmark used the official 95,561-atom GROMACS ADH dodecahedron system, 2 fs steps, PME, 0.9 nm cutoff, and hydrogen-bond constraints. GROMACS and OpenMM read the same coordinates and GROMACS topology. Thermostat/integrator implementations differed, so the comparison measures practical production throughput, not trajectory or floating-point equivalence.

| Host GPU | GROMACS ns/day | OpenMM ns/day | GROMACS advantage |
|---|---:|---:|---:|
| RTX 5060 Ti 16 GB | 237.136 | 173.334 | 36.81% |
| RTX 3090 24 GB (bus 11) | 305.412 | 209.749 | 45.61% |
| RTX 5090 32 GB | 618.942 | 456.657 | 35.54% |
| RTX 3090 24 GB (bus e1) | 298.155 | 210.037 | 41.95% |
| **Four independent replicas** | **1459.645** | **1049.778** | **39.04%** |

The heterogeneous GPUs are scheduled as **independent seeded replicas**, not one mixed-GPU trajectory. This avoids slow-device synchronization and gives the useful aggregate of 1.460 µs/day on this fixture.

Raw benchmark record: `benchmarks/md/adh_engine_comparison_2026-07-17.json`.

## Public contracts

- Canonical typed Job schema: `bms.md.job.v2`
- Typed launcher intent: `bms.md.launch-intent.v1`
- Preview envelope: `bms.md.launch-preview-request.v1`
- Launch envelope: `bms.md.launch-request.v1`
- Legacy compatibility schema: `schemas/md_job_v1.schema.json` (`bms.md.job.v1`)
- Run-manifest schema: `schemas/md_run_v1.schema.json` (`bms.md.run.v1`)
- CLI: `python3 -m scripts.bms_md.cli validate|run|aggregate`
- Runner: `scripts/bms_md/gromacs_pipeline.py`
- API model: `molecular_dynamics` / mode `simulate`
- Parent coordinator: `workflows/experimental/molecular_dynamics/orchestrator.nf`
- Scheduler-visible singleton replica entrypoint: `workflows/experimental/molecular_dynamics/replica.nf`

The normalized job contract explicitly records:

- engine and replica count;
- immutable base seed and derived per-replica seed;
- input structure or prepared coordinates/topology;
- force field and water model;
- box geometry and padding;
- ion names, salt concentration, neutralization, and solvent group;
- minimization, NVT, NPT, and production controls;
- trajectory, energy, and checkpoint cadence;
- GPU selection, rank/thread count, pinning, and offload policy.

The run manifest records the normalized config, engine version/platform, stage ledger, relative artifact paths, byte counts, and SHA-256 checksums. Absolute host paths are excluded from the artifact contract.

## Molecular Dynamics Gen 2 launcher

The Molecular Dynamics Gen 2 launcher uses a source-first boundary. The operator chooses one governed starting-structure identity, the server resolves immutable bytes and reports inspection/admission evidence, and the browser renders that evidence before any launch is possible. Supported sources are a verified managed fixture, an RCSB PDB accession, a direct PDB/mmCIF upload, a completed prediction artifact, an existing Design, a prior MD Job input (`prior_md_input`), or a policy-enumerated server file. The browser never submits a host filesystem path in the typed automatic-preparation lane.

**Prediction-result handoff:** a sequence can be sent to the standard Structure Prediction launcher without fabricating coordinates in MD. After the prediction Job completes, the MD launcher lists bounded, typed source candidates from that exact Job. The selected candidate is still inspected and admitted against the selected chemistry profile before preview.

The browser submits the complete requested scientific settings as `bms.md.launch-intent.v1`, including replicas, random seed, profile-owned preparation values, minimization/equilibration/production controls, output cadence, checkpoint cadence, CPU-thread request, and any Project `launch_context_id`. Unknown or omitted intent fields fail closed. A preview through `bms.md.launch-preview-request.v1` returns the server-compiled effective request, blockers/warnings, and an effective request digest. Final `bms.md.launch-request.v1` materialization requires that preview digest and creates the one canonical scheduler-visible Job.

Profile changes invalidate both admission and preview. Any operator-setting change invalidates the preview. Clones reopen the prior Job-owned immutable input by Job UUID; typed Project requests reopen their original Design source and requested settings. Neither path recovers a browser-visible source path.

### Compatibility boundary

The prepared-system compatibility lane remains available only for explicitly prepared coordinates/topology through the pinned OpenMM 8.5.2 adapter. It does not use the typed automatic-preparation route and makes no GROMACS profile-admission claim. Generic `bms.md.job.v1` and direct `md_job_config` handling remain compatibility surfaces for existing automation; new browser and agent automatic-preparation submissions use the typed Gen 2 preview/launch routes.

## API and Nextflow integration

For typed automatic preparation, the API validates the raw `bms.md.launch-intent.v1`, resolves and snapshots the admitted source bytes, compiles one authoritative `bms.md.job.v2` effective request, and binds the launch to the preview digest. Existing direct `bms.md.job.v1` / `md_job_config` handling is retained only for bounded compatibility. `build_nextflow_command` derives `md_input_root` from the server-materialized document's parent directory, allowing only server-owned relative structure/topology paths to be bound into the runtime.

The workflow has three bounded processes:

1. `MD_NORMALIZE_CONFIG` (`MolecularDynamicsCpu`, 1 CPU) validates, resolves input paths, and publishes `inputs/md/md_job.normalized.json`.
2. `MD_RUN_REPLICA` (`MolecularDynamics`, 1 GPU) scatters one task per deterministic replica and publishes `replicas/replica_<index>/`.
3. `MD_FINALIZE_RESULTS` (`MolecularDynamicsCpu`, 1 CPU) validates completed manifests and publishes `md_result.json` using schema `bms.md.aggregate.v1`.

GPU identity is deliberately split at the container boundary: `execution.scheduler_gpu_id` records the physical device selected by BioModStack, while `execution.gpu_id` is `0` inside the single-device `CUDA_VISIBLE_DEVICES` namespace. This prevents physical IDs such as `2` from being incorrectly forwarded to GROMACS when the container exposes one logical GPU.

`md_result.json` references manifests by stable published-relative paths such as `replicas/replica_0/manifest.json`; it never records disposable Nextflow work paths.

## Execution phases

1. **Preflight** — normalize and validate config; run `gmx mdrun -version`; reject a requested full-CUDA run if GROMACS lacks CUDA support. There is no silent CPU fallback.
2. **Preparation** — `pdb2gmx`, box construction, solvation, and neutralized salt placement. The pre-ionization net charge is the only admitted `grompp -maxwarn 1` case; all scientific stages use `-maxwarn 0`.
3. **Minimization** — steepest descent. Only short-range nonbonded work is offered to the GPU because GROMACS cannot GPU-offload PME/update for non-dynamical integrators.
4. **NVT** — restrained thermal equilibration with a replica-specific velocity seed.
5. **NPT** — restrained pressure equilibration with continuation from NVT checkpoint/state.
6. **Production** — unrestrained NPT production with CUDA nonbonded, PME, bonded, and update offload.
7. **Validation** — `gmx check` validates trajectory and energy files.
8. **Manifest** — atomically emit `manifest.json` only after every required stage and artifact passes.

## Checkpoint/restart semantics

Each stage has an atomic state ledger with artifact checksums. Completed stages are skipped only when every recorded output still exists and matches its checksum. An interrupted stage resumes from its `.cpt` file using `-cpi ... -append`. A real RTX 5090 test interrupted GROMACS with SIGINT after 8 seconds, wrote a valid checkpoint, resumed from step 20,961, appended to the existing outputs, and completed.

## Phase-1 acceptance gates

| Gate | Requirement |
|---|---|
| Isolation | Work occurs in a clean worktree; no modification of the dirty `test` checkout. |
| Runtime | Pinned GROMACS CUDA container launches on RTX 5090 and at least one RTX 3090; CUDA preflight passes. |
| Correctness | Deterministic standard-protein fixture completes preparation → minimization → NVT → NPT → production → validation. |
| Restart | Controlled interruption creates a checkpoint; rerun resumes with `-cpi -append`; no completed stage is repeated. |
| Provenance | Normalized job and final manifest validate against the v1 JSON Schemas; artifacts have SHA-256 and relative paths. |
| Performance | Same-system benchmark is captured with image identity and GPU mapping; selected production engine materially beats the reference adapter. |
| Tests | Contract, command construction, MDP rendering, atomic ledger, restart planning, schemas, fake-engine functional flow, and real GPU smoke pass. |
| Scientific honesty | Smoke success is not convergence. No biological claim is made until replica analysis and target-specific topology validation pass. |

## Deferred gates before protein–DNA and DRT4

- Validate a selected protein–DNA force-field combination (for example protein Amber + nucleic-acid OL15/OL21) as an installed, versioned topology set rather than assuming the stock force-field directory is sufficient.
- Add ligand/covalent-template intake and explicit refusal paths when parameters are absent.
- Add scientific replica analysis: RMSD/RMSF, radius of gyration, secondary structure, contact/interaction metrics, energy/density/temperature/pressure diagnostics, and convergence/stationarity checks. Artifact/manifest aggregation is already implemented by `MD_FINALIZE_RESULTS`.
- Validate DRT4 accession, sequence boundaries, chain mapping, numbering, state, DNA, metals/ligands, protonation, and the Tyr125-linked nucleotide topology before any DRT4 production campaign.

## Runtime builds

- GROMACS: `nvcr.io/nvidia/gromacs:v2025.3`, pinned base digest `sha256:8ee1822b8a34ace738e000a6c9cf8bc9a8abdbbf49e84506238a427b89ee6daf`; rebuilt SIF `/mnt/BioModStack/apptainer/gromacs-md-2025.3.sif`, 533,737,472 bytes, SHA-256 `97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b`.
- OpenMM reference: `containers/openmm-md/Dockerfile` and `environment.yml`, OpenMM 8.5.2 + CUDA 13.0.

The host CUDA compiler is not used to run either container. Runtime compatibility comes from NVIDIA driver 580.173.02 and containerized CUDA user-space libraries.
