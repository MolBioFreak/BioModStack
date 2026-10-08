# First-Class Molecular Dynamics Product Specification and Phase-Level Implementation Plan

> **Status:** Final implementation specification; experimental implementation in progress; not production-approved
> **Date:** 2026-07-18
> **Target:** BioModStack first-class molecular-dynamics product, initially contained in one default-off experimental workflow
> **Implementation worktree:** `/home/dalab/.hermes/worktrees/bms-md-suite`
> **Baseline commit:** `a69711f7e55786f3867e3952b546b3d6b8c48c11`
> **Bundled experimental engines:** GROMACS 2025.3 and OpenMM 8.5.2, selected explicitly with no fallback
> **Public model ID:** `molecular_dynamics`
> **Public launch mode:** `simulate`

## 1. Executive decision

BioModStack molecular dynamics (MD) will be a first-class platform product, not a standalone script hidden behind a model-registry entry.

The BioModStack API, job database, GPU orchestrator, job-control service, result ingester, analysis registry, and frontend remain the authoritative control plane. Nextflow and the selected MD engine are execution-plane workers. They may report facts and produce artifacts; they do not independently assign GPUs, create invisible replica work, or declare a job complete.

The product uses a common MD orchestration contract with explicit engine adapters:

- **GROMACS 2025.3** is the default and first full experimental adapter.
- **OpenMM 8.5.2** is bundled behind a separate adapter/container in the same experimental product. Its first enabled capability is deliberately narrow: a validated GROMACS `.gro`/recursively closed `.top` bundle, CUDA execution, and a production-only segment. Structure preparation, minimization, NVT, NPT, or resume semantics that have not passed their OpenMM gates fail before scheduling rather than being approximated.
- Capability negotiation is exact and fail-closed. The platform must never silently switch engines, omit unsupported settings, remove chemistry, or reinterpret a requested method.
- Classical fixed-topology MD is distinct from QM/MM or reactive chemistry. Classical MD may study stability, conformations, interactions, hydration, and pre-reactive geometry; it must not claim bond-formation barriers, reaction rates, or catalytic free energies.

### 1.1 Measured workload observation and provisional engine decision

The retained local benchmark used a 95,561-atom solvated alcohol dehydrogenase system, PME, 2 fs timestep, 50,000 steps (100 ps):

| GPU | Evidence ID | GROMACS ns/day | OpenMM ns/day | GROMACS advantage | GROMACS h/ns | OpenMM h/ns |
|---|---|---:|---:|---:|---:|---:|
| RTX 5060 Ti | `rtx5060ti` | 237.136 | 173.334 | 36.809% | 0.101 | 0.1385 |
| RTX 3090, PCI bus 11 | `rtx3090_bus11` | 305.412 | 209.749 | 45.608% | 0.079 | 0.1144 |
| RTX 5090 | `rtx5090` | 618.942 | 456.657 | 35.538% | 0.039 | 0.0526 |
| RTX 3090, PCI bus e1 | `rtx3090_buse1` | 298.155 | 210.037 | 41.954% | 0.080 | 0.1143 |

Evidence: `/home/dalab/.cache/bms-md/results/adh_engine_comparison.json` and repository copy `benchmarks/md/adh_engine_comparison_2026-07-17.json`.

This is a workload-specific engineering observation, not an isolated engine effect: the retained recipes used different dynamics methods, one short timing run per GPU, and no repeated randomized timing series. It provisionally supports GROMACS as the default on this local workload because GROMACS won every observed comparison and supplies strong checkpoint/tooling support. Before any general engine-performance claim or default-engine change, BMS must freeze equivalent physics where possible, output/reporting load, precision, offload, rank/thread settings, warm-up and timing windows; run repeated measurements in randomized order; and report dispersion plus raw logs. If exact algorithm parity is unavailable, the comparison must name the two distinct protocols rather than attributing the delta solely to engine implementation.

**Container-provenance drift discovered during final audit:** the benchmark JSON pins GROMACS base image `nvcr.io/nvidia/gromacs@sha256:8ee1822b8a34ace738e000a6c9cf8bc9a8abdbbf49e84506238a427b89ee6daf`, and live `apptainer inspect` reports that same base digest. Earlier retained outer-image evidence identified `/mnt/BioModStack/apptainer/gromacs-md-2025.3.sif` as 533,733,376 bytes with SHA-256 `a1bcc49a03171564e4c4590c51229410e852363e6a76843bc02c36db127c4106`; the live file audited on 2026-07-18 is 533,737,472 bytes with SHA-256 `97c117ea07496c0d1b13d80be84d33345b89063b47ccfb83f6cbff0145f1385b`. The throughput rows remain anchored to the pinned NGC image digest. Phase 0 must still reconcile and pin the outer SIF bytes used for production execution; if execution equivalence cannot be proven, rerun acceptance/performance against the canonical SIF.

### 1.2 Final experimental-packaging and CUDA-first decision

During development, **all first-class MD code and routes belong to one experimental workflow boundary**. `molecular_dynamics` remains the stable product/model ID, is always marked `experimental: true`, and is hidden and unlaunchable unless `BMS_FEATURE_MOLECULAR_DYNAMICS=1`. The flag defaults to `0` in install profiles, compatibility exports, and the core-runtime compose environment. Setting it exposes experimental capability only; it does not confer infrastructure acceptance, scientific validation, or production approval.

The workflow implementation is physically bounded under `workflows/experimental/molecular_dynamics/` and `modules/experimental/molecular_dynamics/`. Preparation, one-replica execution, and finalization are separate entrypoints/modules with durable file contracts. No `.nf` file may combine preparation, replica fan-out, GPU dynamics, and aggregation, and no Nextflow invocation may create a second unscheduled GPU replica. GROMACS and OpenMM replica processes are separate and use engine-specific immutable images; engine selection is exact and never implemented by changing arguments on a shared process or silently invoking the other engine.

The execution contract is CUDA-first and cloud-portable:

1. Every dynamics replica requests exactly one NVIDIA CUDA accelerator and runs with container-local device index `0`; CPU-only, OpenCL, Reference, and implicit `auto` fallback are rejected.
2. Provider-neutral resource requirements (accelerator vendor/API/count, CPU, memory, walltime, minimum VRAM) are separate from allocation evidence (provider/allocation ID, physical UUID/model/index when available, container ordinal).
3. Local execution validates a single-device `CUDA_VISIBLE_DEVICES` namespace and reconciles observed GPU UUID/model with the scheduler allocation. A future cloud adapter may omit host ordinals but must provide durable provider/allocation/device identity and the same single-CUDA-device proof.
4. CPU preparation/finalization remains permitted where scientifically and operationally appropriate; the CUDA-first rule applies to minimization/equilibration/production stages advertised as GPU dynamics. Any intentionally CPU-only minimization is declared as such and is never a fallback from failed CUDA initialization.
5. Both runtime images are bundled and version-pinned before experimental exposure. Image presence is not capability proof: each adapter must pass runtime CUDA preflight and emit immutable engine/platform provenance.

## 2. Product scope

### 2.1 In scope for the first production product

1. Submit, validate, queue, pause, resume, cancel, retry, and inspect an MD run through normal BMS APIs and UI.
2. Prepare a supported system once, minimize it on CPU, and run NVT, NPT, and production stages on scheduler-assigned GPUs.
3. Run independent replicas from a reproducibly materialized, domain-separated unique seed schedule, one scheduler-visible child job per replica. Seed reproducibility does not imply bitwise-identical GPU trajectories.
4. Preserve checkpoints, support no-append continuation, and retain immutable attempt history.
5. Store portable, checksummed manifests and provenance under the BMS result root.
6. Ingest replica and aggregate results transactionally before completion is published.
7. Expose bounded artifact APIs, structured analysis, replica-aware uncertainty, and trajectory viewing.
8. Validate in order:
   1. 1AKI infrastructure smoke test;
   2. a protein–DNA system;
   3. a DRT4-like protein–DNA–dNTP–Mn²⁺ system after chemistry review.
9. Support validated GROMACS `.top`, `.itp`, and `.gro` input bundles.
10. Preserve an engine-neutral API where semantics are genuinely common and explicitly expose engine-specific extensions where they are not.

### 2.2 Explicitly out of scope for the first production product

- Reaction-coordinate discovery, bond making/breaking, transition states, catalytic barriers, and rates.
- QM/MM and reactive force fields.
- Adaptive sampling, metadynamics, replica exchange, FEP/TI, constant-pH MD, polarizable force fields, and coarse graining.
- A trajectory whose timesteps migrate across unlike GPUs.
- Silent topology generation for unknown ligands, modified residues, nucleotides, metals, products, or covalent links.
- Treating an MD replica as a protein-design row in the existing `designs` table.
- Advertising a specialized analyzer or viewer because metric-shaped JSON happens to exist.

## 3. Non-negotiable architecture rules

1. **One authoritative owner per transition.** BMS owns durable job state; Nextflow and engine processes only emit observations and artifacts.
2. **One GPU reservation per running replica.** A BMS parent that reserves one GPU may not launch multiple concurrent GPU replicas inside Nextflow.
3. **Replicas are scheduler-visible child jobs.** The scheduler may place independent replicas on heterogeneous GPUs. One continuous trajectory lineage stays on one GPU model/capability class; checkpoint continuation may move to another physical card only when that class and the execution identity are compatible.
4. **Physical and container GPU identity are separate.** BMS stores physical index, UUID, and model. A container receiving one GPU normally uses device `0`; the engine must not receive the host physical index as its container-local index.
5. **Preparation is fail-closed.** Missing or unmatched chemistry is a validation error, never a warning followed by deletion or substitution.
6. **Completion follows ingestion.** `completed` is not durable until required manifests and artifacts validate, checksums pass, and the result transaction commits.
7. **Retries create attempts.** No retry overwrites the failed attempt's logs, checkpoint ledger, or artifact manifest.
8. **Durable paths are portable.** Manifests contain result-root-relative POSIX paths, not worktree paths, Nextflow work paths, `/tmp`, or host-absolute paths.
9. **Capabilities are implemented facts.** A registry declaration cannot lead implementation. Unknown or unimplemented capabilities fail closed.
10. **Scientific and infrastructure acceptance are separate.** A run can prove scheduling/restart integrity without proving a DRT4 model scientifically valid.

## 4. Control-plane and execution-plane model

### 4.1 Authoritative components

| Concern | Authoritative owner | Existing integration seam |
|---|---|---|
| Public submission and validation | FastAPI jobs/MD router | `platform/api/routers/jobs.py`, new `routers/molecular_dynamics.py` |
| Durable run/replica state | SQLAlchemy database | `platform/api/database.py` |
| GPU eligibility, reservation, placement, concurrency | BMS GPU orchestrator | `platform/api/services/gpu_orchestrator.py` |
| Launch/cancel process ownership | Nextflow launch/monitor helpers + job-control service | `platform/api/services/nextflow.py`, `job_control.py` |
| Workflow execution | One Nextflow invocation per schedulable child | `workflows/`, `modules/` |
| Engine behavior | Explicit adapter selected by resolved capability contract | new `platform/api/services/md/engines/`, `scripts/bms_md/` |
| Terminal-state reconciliation | BMS state transition service | `gpu_orchestrator.py`, `nextflow.py`, `result_state_integrity.py` |
| Result validation/commit | Result ingester | `platform/api/services/result_ingester.py` |
| Analysis eligibility/execution | Explicit analysis contract and worker | `result_contracts.py`, `analysis_*.py` |
| Queue/detail/result UI | Existing frontend job surfaces | `JobSubmission`, `JobQueuePanel`, `JobDetailPage`, `ResultsViewer` |

### 4.2 Run graph

A public `molecular_dynamics/simulate` submission creates this durable graph:

```text
MD parent job (orchestration record; does not own a GPU)
  ├─ preparation child (CPU-only Nextflow invocation)
  ├─ replica 0 child (one BMS GPU reservation; one Nextflow invocation)
  ├─ replica 1 child (one BMS GPU reservation; one Nextflow invocation)
  ├─ replica N child (one BMS GPU reservation; one Nextflow invocation)
  └─ finalization child (CPU-only, created after required-replica barrier)
       └─ optional/required analysis runs through analysis registry
```

The parent is a durable aggregate job with `queue_status=waiting_children`; it is not eligible for GPU packing. The preparation and finalization children use the CPU-only scheduling lane (`vram_estimate_mb == 0`) only after that lane is converted to the same durable dispatch-lease/startup-handshake contract as GPU children. Replica children use normal GPU packing and normal per-model/per-GPU concurrency controls.

### 4.3 Parent state derivation

The parent state is reduced from its child graph and committed through one coordinator:

| Child condition | Parent public state | Parent current stage |
|---|---|---|
| preparation queued/running | `running` | `preparation` |
| preparation failed | `failed` | `preparation` |
| preparation completed; replicas queued/running | `running` | `replicas i/n` |
| one replica failed but retry permitted | `running` | `retry replica i` |
| terminal replica failure with partial results allowed | `partial` | `replicas` |
| all required replicas completed; finalizer running | `running` | `finalization` |
| finalization/required ingestion failed | `failed` or `partial` | `ingestion` |
| manifests ingested and required analysis complete | `completed` | `completed` |
| cancellation requested, descendants draining | `cancelling` | current |
| all descendants stopped | `cancelled` | terminal |

`workflow_status` and `verification_status` are orthogonal. Artifact ingestion and required operational analysis may set `workflow_status=completed` while `verification_status` remains `not_assessed`, `review_required`, or `fail`. A 1AKI infrastructure smoke run, an external unreviewed topology, and a curated scientifically approved pack therefore cannot surface as equivalent evidence. The UI always displays workflow state, chemistry assurance, validation lane/version, and verification state separately; a failed or absent scientific assessment never rewrites execution history.

Do not overload `paused` or `pending_msa` to represent MD barriers. Define and expose the two state axes separately:

- public/workflow `status`: `queued`, `running`, `completed`, `partial`, `failed`, `cancelling`, `cancelled`;
- scheduler `queue_status`: `queued`, `running`, `paused`, `waiting_children`, `completed`, `partial`, `failed`, `cancelling`, `cancelled`.

`queue_status` controls scheduler eligibility. Bounded job responses and the frontend expose both without collapsing them into one enum.

Current code does not already provide this vocabulary: `platform/api/schemas.py::JobStatus` lacks `partial` and `cancelling`, and active/terminal status sets are repeated in services such as `job_control.py`. Phase 2 must update and centralize those contracts, not write unrecognized strings into `jobs.status`.

## 5. Data contracts

### 5.1 Public job contract

The public request remains one complete `bms.md.job.v1` document supplied as `md_job_config`. The API may accept it inline or as an uploaded BMS input artifact; it immediately stores a normalized managed copy. A client may not submit an arbitrary server-host path. Before launch, BMS:

1. parses JSON without executing or interpolating content;
2. validates against `schemas/md_job_v1.schema.json`;
3. resolves all referenced inputs through the BMS path-containment layer;
4. resolves requested engine and capability set;
5. materializes deterministic replica seeds;
6. stores the normalized immutable request and SHA-256;
7. creates the parent and preparation child in one transaction.

Any platform-native fields not represented in `bms.md.job.v1` must be added compatibly or introduced in a versioned `bms.md.job.v2`; they must not be smuggled through unvalidated `params` keys.

### 5.2 New durable records

Add dedicated MD records rather than mapping trajectories onto `Design`:

#### `md_runs`

- `job_id` — PK/FK to parent `jobs.id`
- `schema_version`
- `request_sha256`
- `normalized_request` JSON
- `engine_requested`
- `engine_resolved`
- `engine_version`
- `capability_resolution` JSON
- `replica_count`
- `preparation_job_id`
- `finalization_job_id`
- `preparation_manifest_path`
- `aggregate_manifest_path`
- `chemistry_manifest_path`
- `workflow_status`
- `verification_status` — `not_assessed`, `review_required`, `pass`, or `fail`
- `chemistry_assurance` — e.g. `smoke_fixture`, `external_unreviewed`, `approved_pack`
- `validation_lane` and `validation_lane_version`
- `scientific_analysis_plan_sha256` where scientific acceptance is requested
- `status_version` for optimistic transition checks
- `created_at`, `updated_at`

#### `md_replica_runs`

- `id`
- `md_job_id` — FK to `md_runs.job_id`
- `job_id` — unique FK to scheduler-visible child job
- `replica_index`
- `attempt`
- `seed`
- `engine`
- `assigned_gpu_index` — physical BMS index
- `assigned_gpu_uuid`
- `assigned_gpu_name`
- `container_gpu_index` — expected `0` for single-device isolation
- `checkpoint_path`
- `manifest_path`
- `current_stage`
- `completed_steps`
- `simulated_time_ps`
- `terminal_reason`
- timestamps

Uniqueness: `(md_job_id, replica_index, attempt)` and one active attempt per `(md_job_id, replica_index)`.

#### `job_artifacts`

Introduce or reuse a generic artifact table if the repository gains one before implementation:

- `id`, `job_id`, optional `replica_run_id`
- `artifact_role`, `media_type`, `relative_path`
- `size_bytes`, `sha256`
- `required`, `available`, `created_at`
- optional bounded metadata JSON

The API must expose these as artifacts, not hydrate trajectory bytes into job JSON.

### 5.3 Idempotency

- Public submission accepts an idempotency key or derives one from user scope plus request hash when explicitly requested.
- Child creation key: `(parent_job_id, child_stage, replica_index, attempt)`.
- Completion event key: `(job_id, nextflow_run_id, terminal_observation_id)`.
- Artifact ingestion key: `(job_id, relative_path)` where `job_id` is the parent/finalizer or scheduler-visible child attempt that owns the artifact.
- Reprocessing the same logical artifact with the same SHA-256 returns the prior committed result. The same logical identity with a different SHA-256 is a transactional conflict; it must not create a second row.

## 6. Common engine-adapter architecture

### 6.1 Package shape

```text
platform/api/services/md/
  contracts.py
  capabilities.py
  coordinator.py
  state.py
  artifacts.py
  preparation.py
  engines/
    base.py
    gromacs.py
    openmm.py
scripts/bms_md/
  cli.py
  contract.py
  runner.py
  preparation.py
  provenance.py
  analysis.py
```

The API-side adapter validates and builds an immutable execution plan. The worker-side CLI executes that plan. Nextflow calls the stable CLI with an argv array and files; it does not duplicate engine policy in Groovy or shell fragments.

### 6.2 Required adapter interface

Each adapter implements:

```python
class MolecularDynamicsEngineAdapter(Protocol):
    def identity(self) -> EngineIdentity: ...
    def capabilities(self, probe: RuntimeProbe) -> EngineCapabilities: ...
    def validate(self, job: MdJob, prepared: PreparedSystem | None) -> ValidationReport: ...
    def prepare_plan(self, job: MdJob) -> ExecutionPlan: ...
    def stage_plan(self, stage: MdStage, job: MdJob, prepared: PreparedSystem) -> ExecutionPlan: ...
    def inspect_checkpoint(self, checkpoint: ArtifactRef) -> CheckpointInspection: ...
    def resume_plan(self, job: MdJob, checkpoint: ArtifactRef) -> ExecutionPlan: ...
    def collect(self, output_root: Path) -> EngineRunEvidence: ...
```

`ExecutionPlan` contains only validated argv, environment allowlist, input artifact references, output roles, container identity, resource requirements, and expected evidence. Shell source text is not an execution-plan field.

### 6.3 Capability contract

Capabilities are typed, versioned, and runtime-probed:

- engine/version/container digest;
- runtime platforms and precision modes;
- CPU/GPU availability;
- supported input/topology families;
- integrators, constraints, thermostats, barostats, and ensembles;
- periodic boundary and electrostatics methods;
- checkpoint, continuation, and no-append support;
- deterministic seed controls;
- trajectory/energy/checkpoint formats;
- custom-force and plugin support;
- enhanced-sampling capabilities;
- explicit `classical_fixed_topology=true|false`;
- explicit `qmmm=false` and `reactive=false` for the first product.

### 6.4 Negotiation rules

1. Normalize requested requirements.
2. Probe the selected adapter and runtime image.
3. Compare every required capability and parameter domain.
4. Return a resolved contract with adapter, versions, container digest, mapped settings, and warnings that do not alter semantics.
5. If anything required is unsupported, return `422 MD_CAPABILITY_UNSUPPORTED` with field-level reasons.
6. Never:
   - switch an explicitly requested engine;
   - substitute a thermostat, barostat, integrator, water model, or force field;
   - disable constraints or PME;
   - reduce replicas or steps;
   - drop custom forces;
   - fall back from GPU to CPU except when the request explicitly permits it.

`engine=auto` may select the measured default only after validation and must persist both `requested=auto` and `resolved=gromacs`.

### 6.5 GROMACS adapter requirements

- Pin GROMACS 2025.3 container by immutable digest/SIF SHA-256.
- CPU minimization; GPU NVT/NPT/production.
- Explicit `mdrun` GPU mapping compatible with a one-device container.
- `grompp -maxwarn 1` only at the intentional pre-ionization charge stage, with a machine-readable reason.
- All scientific-stage `grompp` calls use `-maxwarn 0`.
- Parse and persist minimization termination reason and final maximum force; success requires explicit convergence evidence, not process exit alone.
- Support `-cpi` continuation with no-append behavior and a stage ledger that cannot rewrite completed-stage artifacts.
- Capture `gmx --version`, build flags, CUDA/runtime, command argv, MDP files, topology hashes, checkpoint metadata, and performance.

### 6.6 OpenMM parity gate

OpenMM is not selectable in production until all of the following pass:

1. The pinned runtime exposes the CUDA platform on every eligible local GPU.
2. A real adapter—not a GROMACS code path with an `engine` label—implements preparation or accepts a validated prepared bundle.
3. NVT, NPT, and production semantics map exactly for the advertised capability subset through a versioned mapping table of explicit integrator, thermostat, barostat, coupling-group, pressure-coupling, constraint, center-of-mass-removal, and all associated parameter semantics. An unavailable algorithm is engine-specific or rejected; an ensemble label alone is never treated as an exact mapping.
4. PME/PBC, constraints, thermostat/barostat, precision, reporters, seeds, and platform properties are explicit in manifests.
5. CPU minimization and GPU dynamics follow the same orchestration contract.
6. Checkpoint inspection, no-append restart, attempt retention, and completed-stage immutability pass fault-injection tests.
7. The adapter produces the same normalized `bms.md.run.v1` result contract, artifact roles, checksums, and provenance completeness as GROMACS.
8. Physical versus container-local GPU identity is correct.
9. Transactional ingestion, cancellation, retry, reconciliation, and UI behavior are engine-independent.
10. A preregistered parity ladder passes: canonical atom/order and topology inventory; masses, charges, molecule counts, exclusions, constraints, virtual sites, combination rules, and 1–4 terms; single-point total and decomposed energies; per-atom forces; deterministic short-step controls where applicable; then replicated NVT/NPT ensemble comparisons. Precision-specific tolerances and multiplicity policy are frozen from numerical-error analysis or an independent reference before results are inspected. Unsupported topology constructs fail capability negotiation.
11. The engine inventory and UI advertise only capabilities actually probed in the deployed runtime.
12. Adapter availability, capability-directed per-job selection, and global-default selection are separate decisions. A tested OpenMM-only capability may route only jobs that require it; it does not justify a global default. Default-engine promotion requires a separate operator-approved decision record covering a representative workload distribution, reliability/lifecycle evidence, uncertainty-aware repeated performance results, and scientific parity for the common advertised subset. No universal percentage threshold is assumed without a preregistered product SLO or cost model.

Until then, `engine=openmm` returns a structured unsupported-capability response. It must not reach the current `run_gromacs_job` path.

## 7. Nextflow execution design

### 7.1 Required entrypoints

Replace the current internally scattered all-replica workflow with singleton work units:

- `workflows/molecular_dynamics_prepare.nf`
- `workflows/molecular_dynamics_replica.nf`
- `workflows/molecular_dynamics_finalize.nf`

Corresponding process modules:

- `modules/molecular_dynamics_prepare.nf`
- `modules/molecular_dynamics_replica.nf`
- `modules/molecular_dynamics_finalize.nf`

One invocation receives one immutable work-item JSON and publishes one result JSON. `molecular_dynamics_replica.nf` must never iterate concurrent replicas or assign GPUs internally.

### 7.2 Registry and launch behavior

Update `MODEL_MODE_WORKFLOW_ENTRYPOINTS` and `resolve_nextflow_entrypoint()` in `platform/api/services/nextflow.py` for non-public internal modes `prepare`, `replica`, and `finalize`; use `WORKFLOW_ENTRYPOINTS` only for profile-level fallback. `build_nextflow_command()` receives normalized artifact paths and no raw user shell fragments.

The GPU orchestrator:

1. selects a replica child by priority and concurrency rules;
2. atomically reserves one physical GPU and stores its index/UUID/name;
3. launches the Nextflow child with only that device visible;
4. passes `container_gpu_index=0` to the execution plan;
5. records the Nextflow run ID before treating the child as running;
6. releases reservation only through the terminal-state path or recovery reconciliation.

Launch publication has one authoritative state writer. Phase 2 decomposes the current long-running `platform/api/services/nextflow.py::launch_nextflow_job()` coroutine—which returns `None` and mutates DB state before spawn—into:

- `start_nextflow_job() -> LaunchOutcome`, a bounded preflight/spawn handshake returning `accepted`, `nextflow_run_id`, `pid`, process-group identity, reservation token, or structured rejection; and
- `monitor_nextflow_job()`, which emits process/progress/terminal observations but does not independently publish `Job.status`/`queue_status`.

The MD state service uses an expected-state/version CAS to publish `launching -> running` only after an accepted outcome and identifiers are durable. A rejected/failed spawn releases or requeues through the same state service. This deliberately replaces the current split ownership in which `launch_nextflow_job()` writes `running` before subprocess acceptance while `platform/api/services/gpu_orchestrator.py` separately mutates queue state.

The same typed protocol is mandatory across the enabled workflow-adapter boundary. `platform/api/services/workflow_adapter.py`, `platform/api/routers/workflow_adapter.py`, and `platform/api/workflow_adapter_app.py` must not acknowledge launch before preflight/spawn or substitute a public job ID for actual execution identity. Native and adapter paths durably bind PID/process group/Nextflow run identity and share the same observation-only monitor and state writer. Adapter restart, rejected spawn, cancellation, replay, and spawn-before-identity-persistence windows are release-blocking fault tests.

The same state service is the only component allowed to publish `running`, terminal state, or reservation release. `monitor_nextflow_job()` reports observations only; the GPU orchestrator must not perform a second launch-state write.

### 7.3 Nextflow responsibilities

Nextflow may:

- stage immutable inputs;
- invoke the worker CLI;
- publish files into the assigned child result directory;
- expose process exit and trace metadata;
- resume Nextflow work when compatible.

Nextflow may not:

- choose or discover an unreserved GPU;
- create scheduler-invisible GPU replicas;
- mutate the parent DB state directly;
- mark completion before BMS ingestion;
- treat `publishDir` existence as proof of valid results;
- store work-directory paths in durable manifests.

## 8. Preparation and chemistry contract

### 8.1 Preparation modes

1. **`smoke_auto`** — narrowly limited to validated smoke fixtures such as 1AKI using the pinned stock recipe.
2. **`validated_gromacs_bundle`** — externally prepared `.gro` + `.top` plus the complete effective preprocessing closure. Structural self-consistency does not imply chemistry review; this lane reports `chemistry_assurance=external_unreviewed` unless bound to an approved pack.
3. **`validated_amber_bundle` (reserved capability)** — `prmtop` + `inpcrd` is absent from deployed capabilities and rejected before scheduling until a native adapter passes topology, restart, artifact, and scientific parity gates. Conversion to GROMACS remains deferred until a separately validated converter exists.
4. **`curated_complex`** — explicit, versioned preparation recipe and reviewed parameter pack for protein–nucleic-acid/ligand/metal systems.

### 8.2 Chemistry manifest

Every prepared system requires a `chemistry_manifest.json`. A pinned smoke fixture may generate it automatically, but no preparation mode bypasses chemistry provenance. It records:

- source accessions, revision/date, exact source mmCIF/PDB checksums, and biological-assembly operators;
- selected model/alternate conformers, occupancy policy, missing or rebuilt regions, unresolved density/ligands/linkages, modeled hydrogens, and every repair decision;
- every biological/chemical component and expected copy count;
- chain IDs and residue ranges;
- protein and nucleic-acid force-field families and versions;
- water/ion model and concentration;
- DNA/RNA termini and patching;
- ligands, nucleotide substrates/products, protonation/tautomer states;
- metal identities, oxidation states, coordination/treatment model;
- modified residues and custom atom/residue mappings;
- covalent links and reactant/product topology identity;
- custom parameter files and checksums;
- full effective topology preprocessing closure, including include roots, macro/`define` values, conditionally active files, restraint/table inputs, and the exact effective/preprocessed topology;
- preparation software versions and commands;
- atom/residue reconciliation report from source to prepared system;
- reviewer identity, decision scope, timestamp, and exact parameter-pack hash for curated approval.

### 8.3 Fail-closed checks

Preparation fails if any expected component, chain, residue, atom, ligand, metal, parameter, or covalent link is absent, duplicated unexpectedly, renamed without a mapping, or unparameterized. The error report includes source identity and exact unmatched records.

The workflow must never silently remove DNA, dCTP/dNTP, Mn²⁺, modified residues, covalent components, or product-state atoms to make topology generation succeed.

### 8.4 DRT4 boundary

Stock `pdb2gmx` with `amber99sb-ildn`/TIP3P is acceptable only for the 1AKI infrastructure smoke test. It is not a DRT4 preparation method.

DRT4-like acceptance is blocked until a reviewed parameter pack covers:

- compatible protein and DNA force fields;
- DNA termini;
- dCTP/dNTP parameters;
- two Mn²⁺ ions per active site with an explicit metal-treatment model and preregistered sensitivity analysis across justified alternatives where model uncertainty is material;
- protonation/tautomer choices;
- source-to-prepared atom mapping;
- explicit covalent/reactant/product topology definitions;
- parameter provenance and checksums.

Create one immutable state contract per accession and biological assembly; `9VDP`, `9VDO`, and `9VDV` are not interchangeable generic controls. Each contract fixes sequence/residue numbering, assembly, modeled and unresolved atoms, DNA identity, dCTP/substrate/product occupancy, two-Mn occupancy/treatment per active site, mutant identity, and the covalent state. In particular, the Tyr125–5′ DNA linkage must be explicitly bonded, explicitly and scientifically justified as omitted, or represented as a preregistered sensitivity hypothesis. Unexplained omission of that linkage, DNA, dCTP, Mn²⁺, or other state-defining chemistry blocks the state. Experimentally unresolved density is not treated as an observed atomic-coordinate validation target. Matched in-silico mutants require their own immutable state contracts.

## 9. Stage lifecycle, restart, and failure semantics

### 9.1 Stage sequence

```text
validate → prepare/topology → solvate/ionize → minimize (CPU)
→ NVT (GPU) → NPT (GPU) → production segment(s) (GPU)
→ replica QC → aggregate → ingest → required analysis
```

Preparation may be shared by replicas only when the manifest hash is identical. Replica velocities and every stochastic component use domain-separated resolved seeds recorded in the manifest. This guarantees reproducible input materialization, not bitwise-identical GPU trajectories.

### 9.2 Checkpoint ledger

Each replica lineage has immutable attempts and append-only continuation segments. An attempt is one resolved scientific/execution contract. Infrastructure retry, checkpoint-terminate pause/resume, user resume, and any new process create a new segment or attempt according to a versioned transition table; no resume mutates its source attempt. Each attempt stores an append-only stage ledger with:

- stage ID and ordinal;
- engine and execution-plan hash;
- input artifact hashes;
- seed;
- start/end timestamps;
- step and simulated time;
- checkpoint path/hash;
- output artifact paths/hashes;
- termination reason;
- convergence/QC evidence;
- retry/resume parent attempt.

Completed stages are immutable. Resume validates the ledger, engine/container identity, topology hash, integrator-critical parameters, checkpoint hash, intended continuation point, and continuation compatibility class. Exact OpenMM binary-checkpoint continuation requires a compatible System, Platform, hardware/runtime fingerprint, and scheduler affinity. A serialized OpenMM `State` restart is approximate, records lost hidden integrator/random state, and starts a distinct lineage; it is never called exact resume. A mismatch fails with `MD_CHECKPOINT_INCOMPATIBLE`; it never starts fresh under the label “resume.”

### 9.3 Retry policy

- Infrastructure/transient launch failure: retry same replica in a new attempt, subject to policy.
- GPU OOM: preserve evidence; recompute reservation. Do not silently reduce system size, precision, PME settings, or physics.
- Scientific validation failure (NaN, non-convergence, broken topology): no automatic retry with altered scientific settings.
- Parent retries only failed/incomplete child units; completed replica manifests remain unchanged.
- Independent replicas may use heterogeneous GPU classes, which are recorded as a covariate.
- A checkpoint-continuation trajectory must remain on a compatible GPU model/capability class as well as the same engine/container/precision contract. If only an unlike GPU is available, the job waits for compatible capacity or starts a new non-concatenated attempt from the common prepared/equilibrated boundary; the old partial trajectory remains retained and excluded from concatenation.

### 9.4 Cancellation

`cancel parent` performs an idempotent cascade:

1. transactionally records `cancelling` and a cancellation token;
2. prevents new children/retries;
3. calls the existing Nextflow/job-control cancellation path for running descendants;
4. marks queued descendants cancelled without launch;
5. retains checkpoints and complete artifacts;
6. reaches parent `cancelled` only after descendants are terminal or explicitly orphan-classified.

Cancelling one replica stops only that replica unless the user cancels the parent or policy requires all replicas.

This is a required refactor, not existing behavior. `platform/api/services/job_control.py::cancel_job_lineage()` currently asks Nextflow to stop and then immediately marks the lineage `cancelled`, clears `assigned_gpu`, and commits. Phase 2 must introduce the durable `cancelling`/drain transition and release reservations only after terminal observation or explicit orphan classification.

### 9.5 Safe pause/resume

Generic database-only pause is forbidden for running MD parents or children. `POST /api/md/runs/{job_id}/pause` performs `cancelling_for_pause -> tracked Nextflow termination -> checkpoint/segment verification -> reservation release -> paused`. `paused` becomes durable only after the process is dead, the checkpoint is valid, and the reservation is released. Resume creates a new continuation segment from the exact recorded checkpoint. Queued, not-yet-started MD work may use ordinary queue pause. Fault tests prove no running process remains hidden from scheduler accounting.

### 9.6 Recovery after API restart

On startup/reconciliation, BMS compares DB state, process/Nextflow state, result manifests, and scheduler reservations. The repair path must be dry-run-first and idempotent. It may recover a valid terminal event and run ingestion; it may not infer completion from a directory name or one output file.

## 10. Artifact and provenance contract

### 10.1 Portable result layout

```text
<result-root>/<parent-job-id>/
  request/
    md_job.json
    md_job.sha256
    capability_resolution.json
  preparation/
    chemistry_manifest.json
    preparation_manifest.json
    system.gro
    system.top
    includes/...
    minimization/
  replicas/
    replica_0/
      attempts/attempt_0/
        execution_plan.json
        stage_ledger.json
        logs/...
        nvt/...
        npt/...
        production/...
        checkpoint/...
        manifest.json
      current.json
    replica_1/...
  aggregate/
    run_manifest.json
    replica_summary.json
    analysis/...
  checksums.sha256
```

All paths embedded in JSON are relative to `<result-root>/<parent-job-id>`. `current.json` is a small derived pointer/summary; it does not erase attempt history. Database artifact rows and immutable manifests are authoritative. A crash after DB commit but before pointer rename is repaired idempotently by startup/periodic reconciliation.

### 10.2 Required provenance

- BMS version/commit and schema versions;
- parent/child job IDs, replica index, attempt;
- requested and resolved engine;
- engine/runtime/container versions and immutable digest;
- host driver and CUDA/runtime compatibility;
- physical GPU index/UUID/name and container-local index;
- CPU, thread, MPI/rank, GPU-offload settings;
- exact argv and approved environment fields;
- seeds;
- preparation software, force fields, water/ions, chemistry manifest;
- all scientific input files and hashes;
- stage parameters and hashes;
- restart lineage;
- artifact sizes and hashes;
- performance and termination/convergence evidence.

Secrets, full host environment dumps, credentials, and unrelated paths are excluded.

### 10.3 Ingestion ordering

Terminal engine exit is an observation, not completion. The transaction is:

1. verify result-root containment and reject escaping symlinks;
2. validate result schema;
3. verify every required artifact exists, is regular, and matches size/hash;
4. run typed semantic validators: topology/trajectory atom identity and count, finite coordinates and energies, valid box matrices, monotonic frame times, expected output schedule, checkpoint step/time, engine/system identity, and cross-file/stage continuity;
5. for GROMACS no-append continuation, parse every `.partXXXX` trajectory/energy part and prove ordering, topology compatibility, monotonic nonduplicated step/time ranges, explicit duplicate-boundary policy, no unexplained gaps/overlaps, energy/trajectory alignment, and final-step agreement with the ledger;
6. validate cross-references, replica count, resolved seed schedule, and stage-ledger consistency;
7. upsert `md_runs`, `md_replica_runs`, and `job_artifacts`;
8. update child and derived parent workflow state consistently;
9. commit;
10. repair/publish derived pointers and only then publish websocket/event/UI completion.

Failure records actionable ingestion state and leaves the job `failed` or `partial`, never newly `completed`.

## 11. API specification

### 11.1 Existing generic endpoints

- `POST /api/jobs` accepts the public MD job through the existing model registry only after the MD feature and requested validation lane are enabled.
- Generic queue, cancel, retry, log, and status endpoints remain common. Generic pause is accepted only for queued MD work; running MD pause/resume uses the safe MD endpoints and transition contract.
- Existing job responses gain bounded MD summary fields or a typed extension; they do not inline manifests or trajectories.

### 11.2 MD endpoints

Add `platform/api/routers/molecular_dynamics.py`:

- `GET /api/md/capabilities` — deployed, probed engines/capabilities and availability.
- `POST /api/md/validate` — schema, path, chemistry, and capability validation without launch.
- `GET /api/md/runs/{job_id}` — parent summary and replica matrix.
- `GET /api/md/runs/{job_id}/replicas/{index}` — replica/attempt/stage detail.
- `POST /api/md/runs/{job_id}/replicas/{index}/retry` — explicit retry when allowed.
- `POST /api/md/runs/{job_id}/pause` — checkpoint-terminate, verify, release, then durable pause.
- `POST /api/md/runs/{job_id}/resume` — validated continuation from selected checkpoints.
- `GET /api/md/runs/{job_id}/artifacts` — paginated metadata.
- `GET /api/md/runs/{job_id}/artifacts/{artifact_id}/download` — contained, local-only, range-capable download in the first release.
- `GET /api/md/runs/{job_id}/analysis` — bounded normalized summaries.

The first release is explicitly local single-user/private-network operation; it does not claim multi-user authorization. Remote public exposure is prohibited. A future multi-user release requires authenticated principals, job ownership, artifact authorization, and cross-user denial tests before MD artifact access is called authorized. Validation errors use stable codes and JSON pointers.

## 12. Analysis and viewing

### 12.1 Core phase-1 analysis contract

Implement `molecular_dynamics_v1` as a real analyzer before advertising it:

- stage completion and convergence/QC;
- temperature, pressure, density, volume, potential/kinetic/total energy time series;
- energy drift with documented fitting window;
- backbone/all-atom RMSD;
- per-residue RMSF;
- radius of gyration;
- SASA;
- hydrogen-bond counts;
- box/PBC integrity;
- performance (ns/day, wall time, GPU identity);
- replica summaries with mean, spread, and confidence intervals where statistically valid.

Each metric is a versioned analysis method, not just a label. Persist atom selections, units, reference structure, alignment/PBC transforms, equilibration/frame exclusions, sampling interval, probe radii or geometric criteria, fitting windows, software/version, estimator, and uncertainty method. Time-correlated frames are not treated as independent observations. Independent replicas are the principal uncertainty units. Analysis reports effective sample size where applicable, every submitted/failed/excluded replica and reason, and `insufficient_evidence` rather than manufacturing an interval. Hardware assignment is balanced/randomized for control comparisons where feasible and remains provenance/stratification unless a preregistered design supports estimating it.

### 12.2 Scientific acceptance plans

Protein–DNA, DRT4, engine-parity, mutant/control, and other scientific pass/fail claims require a checksummed, versioned plan committed before launch. The plan defines observables and atom selections; alignment/PBC processing; equilibration exclusion and sampling interval; estimands; replica and failed-run handling; control contrasts; acceptance regions and scientific basis; multiplicity policy; minimum evidence/effective-sample criteria; stationarity or extension rules; and stop/go policy. Every run and report stores the plan hash; results generated under a different or post hoc plan cannot satisfy that gate.

### 12.3 DRT4 analysis pack

After chemistry acceptance, add a versioned DRT4-specific analysis contract for:

- Y125 priming geometry;
- D240/D241–Mn²⁺ coordination;
- Mn²⁺–dCTP geometry;
- DNA 3′ alignment;
- active-site hydration;
- protein–DNA and oligomeric interfaces;
- symmetry/asymmetry across active sites;
- correlated motions;
- matched mutant/control deltas with replica-aware uncertainty.

These are geometry/dynamics analyses, not reaction barriers or rates.

### 12.4 Frontend behavior

Source targets:

- `platform/frontend/src/components/JobSubmission.tsx`
- `JobQueuePanel.tsx`
- `JobDetailPage.tsx`
- `JobDetailsPanel.tsx`
- `ResultsViewer.tsx`
- `MolstarViewer.tsx`
- `FloatingViewer.tsx`
- `platform/frontend/src/lib/resultCapabilities.ts`
- API client and model inventory modules used by these components

Required product surfaces:

1. First-class MD launch form with config upload plus structured fields for supported common settings.
2. Preflight validation and resolved-capability preview.
3. Parent queue row with preparation/replica/finalization progress.
4. Replica matrix showing state, attempt, seed, physical GPU, engine, stage, simulated time, and retry/cancel controls.
5. Detail page for manifests, provenance, checkpoint lineage, and ingestion/QC errors.
6. Lazy trajectory viewer using topology plus trajectory; never preload all trajectory bytes into React state.
7. Downsampled server-side chart data with explicit units and time ranges.
8. Artifact browser with size/hash and range download.
9. Comparison view across replicas and selected runs.
10. Generic metadata only when specialized capability is absent.

Mol* trajectory support must be exercised with real `.gro`/`.xtc` artifacts, resource disposal tests, and bounded-memory behavior. A capability string alone is not acceptance.

## 13. Security, containment, and operational requirements

- Resolve all inputs and outputs under configured BMS roots using canonical paths.
- Reject traversal, absolute durable paths, device files, FIFOs, sockets, and escaping symlinks.
- Never construct shell commands from request strings; use validated argv.
- Enforce configurable hard limits. Initial local defaults are: request JSON 4 MiB, uploaded input bundle 20 GiB, 100,000 artifacts/run, 1,024-byte relative paths with 255-byte components, 64 replicas, 5×10^8 integration steps/segment, 2×10^9 steps/parent, and 2 TiB projected retained output/parent. Operator config may lower these; increases require explicit documented approval and preflight evidence.
- Preflight computes projected trajectory/checkpoint/log/analysis volume from atom count, frame/checkpoint intervals, replicas, and duration. Stable rejection/status codes are `MD_REQUEST_TOO_LARGE`, `MD_INPUT_BUNDLE_TOO_LARGE`, `MD_ARTIFACT_LIMIT`, `MD_PATH_LIMIT`, `MD_REPLICA_LIMIT`, `MD_SIMULATION_LIMIT`, `MD_OUTPUT_LIMIT`, `MD_INSUFFICIENT_STORAGE`, and `MD_LOW_DISK`; no generic success or silent truncation is permitted.
- Maintain at least `max(100 GiB, 25% of projected remaining output)` free under the result root. At the runtime hard watermark (90% filesystem use or below reserve), stop scheduling new segments, request a clean checkpoint-terminate for running segments, set `MD_LOW_DISK`, and never continue into corruption or disk exhaustion.
- Redact credentials and sensitive environment variables from logs/manifests.
- Pin container identities and verify the GROMACS SIF SHA-256 before launch.
- Use atomic write-then-rename for manifests and current pointers.
- Stream/hash large artifacts; do not read trajectories into API memory.
- Artifact downloads are local/private-network only in Phase 5 and support range requests, bounded concurrency, and correct media types; any future multi-user mode adds authenticated authorization first.
- Queue/API reads remain bounded and side-effect free.
- No production DB mutation, deployment, or service restart occurs during offline implementation phases.

## 14. Phase-level implementation plan

Each phase is implemented test-first in the isolated worktree. Each boundary requires targeted tests, relevant broader suites, `git diff --check`, secret/generated-artifact scan, independent specification review, independent code-quality review, and explicit rollback notes. No merge/deploy occurs merely because a phase is coded.

### Phase 0 — Freeze evidence and correct misleading surface

**Objective:** Preserve proven work while preventing the current partial implementation from being mistaken for a production product.

**Source targets**

- `benchmarks/md/adh_engine_comparison_2026-07-17.json`
- `docs/Molecular_Dynamics_Suite.md`
- `platform/api/config/models/molecular_dynamics.yaml`
- `biomodstack_runtime_profile.py`
- `compose.core-runtime.yml`
- `platform/api/main.py`
- `platform/api/config/model_registry.py`
- `platform/api/model_registry.py`
- `platform/api/routers/models.py`
- `platform/api/routers/jobs.py`
- `platform/api/routers/workflow_adapter.py`
- new `platform/api/services/md/feature_gate.py`
- `platform/api/services/result_contracts.py`
- `workflows/molecular_dynamics.nf`
- `modules/molecular_dynamics.nf`

**Work**

1. Retain benchmark, CLI, schema, restart, and artifact tests as evidence.
2. Record current feature truth explicitly:
   - `workflows/molecular_dynamics.nf` creates a replica list and `modules/molecular_dynamics.nf::MD_RUN_REPLICA` launches each item inside one scheduler job, so the current replica work is not independently visible to BMS scheduling;
   - `platform/api/config/models/molecular_dynamics.yaml` promises one scheduler-owned GPU per replica although the current scheduler sees only the enclosing job;
   - `scripts/bms_md/gromacs_pipeline.py::run_gromacs_job` rejects `engine != gromacs`; schema/enumeration acceptance of `openmm` is not an implementation;
   - `platform/api/services/result_contracts.py` already declares `molecular_dynamics_v1`, MD analyzers, and `trajectory_viewer`, but `platform/api/services/analysis_registry.py` does not register those analyzers and the frontend has no implemented first-class MD run/replica/result surface; the declaration is premature/unbacked, not absent.
3. Add the default-off install/runtime flag `BMS_FEATURE_MOLECULAR_DYNAMICS`. Remove every disabled submission path, not only UI visibility: model listing/detail/categories, `POST /api/jobs`, and direct workflow-adapter launch all reject or hide MD with `MD_FEATURE_DISABLED` before command construction, database mutation, directory creation, background-task registration, or process launch. Trusted internal prepare/replica/finalize dispatch is a separate typed coordinator call, not a client-supplied bypass token. Regression tests prove zero durable or process side effects while disabled.
4. Remove `molecular_dynamics_v1` analyzer/viewer declarations that are not backed by registered analyzer and viewer implementations.
5. Label the current all-replica Nextflow workflow infrastructure-only and do not route production submissions through it. Fix or explicitly retire the current malformed Nextflow CLI/input formatting and require CLI lint plus parse tests before retaining it as evidence.
6. Reconcile the earlier `a1bcc49a…` / 533,733,376-byte SIF record with the live `97c117ea…` / 533,737,472-byte file, identify the exact bytes used for the retained benchmark, and choose or rebuild one canonical image. If byte identity cannot be proven, rerun the benchmark against the canonical image before retaining the workload observation.
7. Regenerate integration evidence so the retained result uses only relative paths and current schemas.

**Gate**

- No UI/API path claims production MD availability; direct `POST /api/jobs` returns `MD_FEATURE_DISABLED` with zero durable launch side effects.
- No specialized capability is declared without implementation.
- Current Nextflow evidence passes CLI-format lint and parse tests or is explicitly retired.
- Existing focused tests remain green.
- Current working evidence is retained and checksummed.
- The canonical GROMACS SIF checksum matches the live file, and the retained benchmark names that same checksum or has been rerun against it.

**Rollback:** documentation/feature-flag changes only; retain benchmark and test fixtures.

### Phase 1 — Contracts, CUDA capabilities, and separate bundled engine adapters

**Objective:** Turn the existing GROMACS runner into one implementation of a stable engine-neutral contract and add a truthful, narrowly scoped OpenMM CUDA adapter without sharing or falling back between engines.

**Source targets**

- New `platform/api/services/md/contracts.py`
- New `platform/api/services/md/capabilities.py`
- New `platform/api/services/md/engines/base.py`
- New `platform/api/services/md/engines/gromacs.py`
- New `platform/api/services/md/engines/openmm.py`
- `scripts/bms_md/contract.py`
- `scripts/bms_md/runner.py`
- `scripts/bms_md/gromacs_pipeline.py`
- `scripts/bms_md/gromacs.py`
- new `scripts/bms_md/openmm_pipeline.py`
- new `scripts/bms_md/capabilities.py`
- `scripts/bms_md/cli.py`
- `schemas/md_job_v1.schema.json`
- `schemas/md_run_v1.schema.json`
- new adapter/capability tests
- `containers/openmm-md/Dockerfile`, `containers/openmm-md/environment.yml`, and canonical OpenMM Apptainer definition

**Work**

1. Define typed job, stage, capability, execution-plan, checkpoint, and normalized-result models.
2. Move GROMACS-specific argument generation and evidence parsing behind `GromacsAdapter`.
3. Add runtime probe and immutable container identity verification.
4. Implement field-level capability negotiation and stable errors.
5. Add an exact adapter dispatcher and a separate OpenMM adapter. Initially it accepts only recursively closed validated GROMACS coordinates/topology, CUDA, and production-only execution; all unsupported preparation, stage, and resume semantics return stable preflight errors and cannot dispatch GROMACS.
6. Preserve domain-separated resolved seeds, root-relative manifests, checksums, and restart behavior.
7. Make minimization QC engine-semantic: store stop reason, force maximum and force norm with units, configured tolerance, numeric comparison, and strict production-lane policy; do not accept a generic convergence boolean alone.
8. Pin and test GROMACS warning semantics: pre-ionization alone may pass exact `-maxwarn 1` and only for an allowlisted single expected warning parsed from logs; minimization, equilibration, and production require exact `-maxwarn 0` behavior. Unexpected warning text/count blocks the stage.
9. Require an explicit engine, exactly one scheduler-provided NVIDIA accelerator, container-local device `0`, and runtime identity/CUDA probes. GROMACS must report a CUDA build and GPU offload; OpenMM must create a CUDA context with explicit `DeviceIndex=0`, mixed precision, and CPU PME disabled. Probe failure is terminal and never retries on CPU/OpenCL/Reference.
10. Emit a provider-neutral work-item resource contract plus separate local/cloud allocation evidence. Pin both engine images before experimental exposure.

**Gate**

- Existing GROMACS functional tests pass through the adapter interface.
- Contract tests prove no GROMACS setting leaks into common fields without an extension namespace.
- A supported OpenMM production-only prepared-bundle fixture dispatches only the OpenMM adapter and emits the common manifest; unsupported OpenMM preparation/stage/resume requests fail before scheduling with structured errors.
- Unknown engine/capability fails closed; no silent fallback tests pass.
- Missing, empty, or multi-device CUDA visibility, nonzero container ordinal, allocation/observed UUID mismatch, unavailable engine CUDA runtime, and CPU/OpenCL/Reference contexts all fail with stable codes.
- Minimization fixtures prove numeric force acceptance/rejection with units and stop reasons; exact GROMACS warning-count/text fixtures prove only the pre-ionization allowlist can use `-maxwarn 1`.

**Rollback:** keep current GROMACS CLI path available behind the experimental flag until adapter equivalence is proven.

### Phase 2 — Scheduler-native parent/child orchestration and persistence

**Objective:** Make every GPU replica a normal BMS schedulable job and give the parent graph durable state.

**Source targets**

- `platform/api/database.py` and the repository's additive schema-initialization/migration path
- `platform/api/main.py`
- `platform/api/config/model_registry.py`
- `platform/api/schemas.py`
- `platform/api/routers/jobs.py`
- `platform/api/routers/workflow_adapter.py`
- `platform/api/workflow_adapter_app.py`
- new `platform/api/routers/molecular_dynamics.py`
- new `platform/api/services/md/coordinator.py`
- new `platform/api/services/md/state.py`
- `platform/api/services/gpu_orchestrator.py`
- `platform/api/services/job_control.py`
- `platform/api/services/queue.py`
- `platform/api/services/nextflow.py`
- `platform/api/services/workflow_adapter.py`
- `compose.core-runtime.yml` and adapter launch-parity tests
- new `workflows/experimental/molecular_dynamics/{prepare,replica,finalize}.nf`
- corresponding new `modules/experimental/molecular_dynamics/{prepare,replica_gromacs,replica_openmm,finalize}.nf`
- `nextflow.config`
- `nextflow_schema.json`
- scheduler config/model VRAM estimation path
- focused state-machine, scheduler, race, cancel, and recovery tests

**Work**

1. Add `md_runs`, `md_replica_runs`, and generic `job_artifacts` records/indexes, with checksum stored as content metadata and a database uniqueness constraint on logical artifact identity `(job_id, relative_path)`.
2. Add and centralize the `partial`, `cancelling`, and `waiting_children` contracts across Pydantic responses, scheduler eligibility, job-control active/terminal sets, and frontend labels.
3. Split the current long-running `launch_nextflow_job()` into typed `start_nextflow_job() -> LaunchOutcome` and observation-only `monitor_nextflow_job()`; apply the identical bounded acceptance/monitor/state-writer contract to both direct-host and runtime-selected workflow-adapter launches. Refactor `platform/api/services/workflow_adapter.py`, `platform/api/routers/workflow_adapter.py`, and `platform/api/workflow_adapter_app.py` so adapter acknowledgement is not treated as process acceptance, real run/process identity is returned, and no path substitutes `job_id` for `run_id`. Make the MD state service the sole publisher of launch/terminal state and remove direct competing `Job` state publication from Nextflow, adapter, and orchestrator helpers.
4. Create and parse-test singleton preparation, replica, and finalization workflows/modules inside the experimental MD namespace; register the internal `prepare`, `replica`, and `finalize` modes before any scheduler-native gate. The replica entrypoint branches exact engine identity into separate GROMACS/OpenMM modules and never scatters replicas. Every replica work item carries explicit global replica index, resolved domain-separated seed schedule, parent/preparation identity, attempt, provider-neutral resources, and runtime allocation evidence.
5. Register the MD router and coordinator lifecycle in `main.py`; route accepted MD submissions from `POST /api/jobs` into the coordinator only after feature/lane gates pass; start, reconcile, and stop the coordinator as part of API lifespan.
6. Create parent + preparation child transactionally, then dispatch every ready CPU preparation/finalization child exactly once via durable leases/idempotency keys. CPU children do not consume GPU reservations; restart/race tests prove no stranded or duplicate CPU work.
7. On validated preparation completion, create exactly one child per replica with resolved seeds and idempotency keys.
8. Queue replicas through existing GPU packing with per-model/per-GPU concurrency.
9. Persist physical GPU UUID/index/model; isolate one GPU and pass container-local `0`.
10. Add explicit parent barrier and derived-state reducer.
11. Replace immediate lineage cancellation with two-phase cancel/drain/reconcile semantics; implement child-only retry, checkpoint-terminate MD pause semantics, and startup reconciliation.
12. Add optimistic/idempotent transition handling for completion/cancel races.

**Gate**

- A two-replica submission creates two scheduler-visible GPU jobs; no Nextflow process can run a second unscheduled replica.
- Preparation and finalization CPU children dispatch exactly once without GPU reservations across API restart, concurrent coordinator ticks, and duplicate events.
- Scheduler can place independent replicas on different GPUs while each attempt records one physical GPU UUID/index/model and local device `0`.
- Resume scheduling enforces compatible GPU class; unlike-GPU retries are non-concatenated new attempts and preserve the old partial lineage.
- Cancellation, retry, duplicate completion, API restart, coordinator lifecycle, and orphan recovery fault-injection tests pass.
- Launch-ownership fault tests run through both direct and configured workflow-adapter paths and prove: rejected/failed spawn never publishes `running`; adapter acknowledgement alone is insufficient; accepted launch durably binds exactly one reservation plus real run/process identity before `running`; replay cannot double-launch; the adapter never substitutes `job_id` for `run_id`.
- Running-MD pause tests prove checkpoint-terminate and process death precede `paused`/reservation release; generic database-only pause is rejected for running MD.
- Parent state-reducer tests cannot derive `completed` while any required child is nonterminal or the durable result-commit barrier is false.
- DB consistency auditor is dry-run-first and idempotent.
- Database constraint tests reject any second artifact row for the same `(job_id, relative_path)`, keeping SHA-256 as content metadata rather than identity.

**Rollback:** additive tables may remain empty; disable public model and revert coordinator routing.

### Phase 3 — Singleton Nextflow hardening and transactional result ingestion

**Objective:** Make Nextflow a reliable singleton execution wrapper and commit MD results before terminal completion.

**Source targets**

- Phase-2 `workflows/experimental/molecular_dynamics/prepare.nf`
- Phase-2 `workflows/experimental/molecular_dynamics/replica.nf`
- Phase-2 `workflows/experimental/molecular_dynamics/finalize.nf`
- corresponding Phase-2 `modules/` files
- `nextflow.config`
- `nextflow_schema.json`
- `platform/api/services/nextflow.py`
- `platform/api/services/result_ingester.py`
- `platform/api/services/result_state_integrity.py`
- new `platform/api/services/md/artifacts.py`
- Nextflow stub/real, ingestion, integrity, and path-security tests

**Work**

1. Harden the Phase-2 singleton preparation, replica, and finalization entrypoints with production resource declarations, controlled termination, exit-code classification, and manifest publication.
2. Re-verify internal-mode routing and generate immutable work-item JSON with explicit replica index, seed, attempt, and preparation identity.
3. Publish one normalized result document per child.
4. Verify schema, containment, symlinks, sizes, hashes, replica/resolved-seed/stage consistency, and all typed semantic trajectory/energy/checkpoint invariants in §10.3.
5. Commit artifact rows and workflow state in one transaction; rebuild `current.json` as an idempotent derived projection after commit.
6. Finalize parent execution only after aggregate validation and required ingestion; do not infer scientific verification from execution completion.
7. Add no-append checkpoint continuation across a killed/restarted Nextflow child and validate every part semantically, not only by hash.

**Gate**

- Nextflow 25.10.1 parses all entrypoints.
- Docker/stub two-replica end-to-end test proves separate scheduler children and portable aggregation.
- Real 1AKI run publishes `md_result.json` with no absolute durable paths.
- Corrupt/missing/hash-mismatched or semantically inconsistent artifacts prevent workflow completion.
- Re-ingesting the same `(job_id, relative_path)` with the same SHA-256 returns the prior committed artifact; a different SHA-256 for that identity fails transactionally and cannot create a second row.
- Crash after DB commit but before `current.json` rename is repaired without duplicate rows/events.
- Kill/restart test preserves completed-stage ledgers and artifact hashes and proves no-append parts have correct topology, monotonic nonduplicated time/steps, explicit boundary handling, no unexplained gaps/overlaps, and matching final checkpoint/energy/trajectory endpoints.
- Cancellation terminates tracked work and releases exactly one reservation.

**Rollback:** keep old infrastructure-only workflow out of registry; disable MD public launch.

### Phase 4 — Analysis contract, artifact API, and first-class frontend

**Objective:** Make MD operable and interpretable from normal BMS surfaces without loading unbounded data.

**Source targets**

- `platform/api/services/result_contracts.py`
- `platform/api/services/analysis_registry.py`
- `analysis_runs.py`, `analysis_subprocess.py`, `analysis_worker.py`, `analysis_autorun.py`
- `platform/api/routers/analyses.py`
- `platform/api/routers/molecular_dynamics.py`
- `platform/frontend/src/components/JobSubmission.tsx`
- `JobQueuePanel.tsx`, `JobDetailPage.tsx`, `JobDetailsPanel.tsx`
- `ResultsViewer.tsx`, `MolstarViewer.tsx`, `FloatingViewer.tsx`
- `platform/frontend/src/lib/resultCapabilities.ts`
- frontend API client/model inventory files
- backend/frontend contract, type, component, and browser tests

**Work**

1. Implement and register the real `molecular_dynamics_v1` analyzer with a versioned required-operational-analysis policy; before registration that required set is empty and no result claims analyzer completion.
2. Persist each analysis method/version, atom selections, units, alignment/PBC algorithm, exclusions, sampling interval, estimators, and uncertainty definitions with outputs.
3. Add bounded artifact, analysis, provenance, and range-download APIs.
4. Add structured launch/preflight UI and deployed capability preview.
5. Add parent/replica queue and detail surfaces that display workflow and scientific-verification states independently.
6. Add lazy trajectory playback and downsampled chart APIs.
7. Add replica-aware comparison and uncertainty display.
8. Dispose viewer resources and object URLs on replacement/unmount.
9. Advertise viewer/analyzer capabilities only after end-to-end contract tests pass.

**Gate**

- Unknown/disabled engine and incomplete result contracts render generic metadata only.
- Real `.gro` + `.xtc` opens, scrubs, and disposes without unbounded renderer/API memory growth.
- Large trajectory download uses range/streaming and never enters job-list/detail JSON.
- Analyzer output is method-versioned, units/selections/transforms/exclusions explicit, replica-aware, and re-runnable idempotently.
- Execution-complete, analysis-complete, and scientific-verification states render independently; a failed or unassessed verification cannot appear scientifically accepted.
- Frontend typecheck/build and focused browser/component tests pass.

**Rollback:** capability registry entry and UI route remain feature-gated; stored artifacts remain downloadable generically.

### Phase 5 — GROMACS infrastructure acceptance and controlled product release

**Objective:** Prove the complete GROMACS execution product on local hardware before enabling only the infrastructure-validated lane; this phase does not scientifically validate arbitrary uploaded systems.

**Validation matrix**

- 1AKI on RTX 5060 Ti, both RTX 3090s, and RTX 5090.
- At least two concurrent replicas on independently reserved GPUs.
- Heterogeneous-replica placement.
- Pinned-GPU placement.
- MD checkpoint-terminate pause/resume and parent cancellation.
- Forced process failure, API restart, checksum failure, OOM classification, retry, and no-append continuation.
- Fresh submission and completed-run resume rejection/handling.
- Projected-output preflight, replica/request/artifact quotas, low-disk watermark checkpoint termination, and quota-exhaustion cleanup.

**Acceptance evidence**

- CPU minimization explicitly reports converged termination and final maximum force meeting the configured target.
- NVT/NPT/production use the assigned GPU and manifest physical/local identities correctly.
- No NaN/infinite coordinates or energies; expected frames/checkpoints exist.
- Stage ledger and completed artifact hashes remain unchanged across resume.
- Required manifests are schema-valid, portable, and checksummed.
- BMS queue/detail/result UI agrees with DB and process truth.
- All focused/backend/frontend suites plus diff/security scans pass.

**Release gate**

Enable `molecular_dynamics/simulate` only for the explicitly infrastructure-validated GROMACS lanes after independent spec and code-quality reviews reconcile all findings. Runs using externally supplied validated bundles may complete execution but default to `verification_status=not_assessed` and `chemistry_assurance=uploaded_bundle_unreviewed`; they are never displayed as scientifically validated. Promotion follows the repository's test-to-main deployment policy with DB backup, migration dry run, explicit operator approval, health checks, quota/low-disk rehearsal, and rollback rehearsal.

**Rollback:** disable model/feature flag, stop new MD scheduling, allow running children to drain or explicitly cancel, and retain all artifacts.

### Phase 6 — Protein–DNA scientific preparation validation

**Objective:** Move beyond protein-only infrastructure and validate a reviewed protein–DNA preparation lane.

**Work**

1. Select a public reference system with stable experimental provenance.
2. Curate compatible protein/DNA force fields, termini, water/ions, protonation, and atom mapping.
3. Review the chemistry manifest independently.
4. Register an immutable validation-plan document before production: hypotheses/claims, system hashes, observables and exact atom selections/algorithms, equilibration and frame exclusions, minimum independent replica count with power/precision rationale, minimum effective sample size, stationarity/state-population criteria, extension/stop rules, estimator/uncertainty method, thresholds, multiplicity policy, and failure handling.
5. Run the frozen replica plan and predefined geometry/stability analyses.
6. Compare preparation integrity and ensemble observables with reference expectations and literature using only the registered plan; amendments create a new version and invalidate confirmatory claims for already inspected data.

**Gate**

- Zero unmatched/missing chemistry.
- Reproducible preparation hash from pinned inputs/tools.
- Explicit force-converged minimization.
- Stable, interpretable protein–DNA geometry across valid replicas.
- No threshold, exclusion, replica count, observable, or extension rule is invented after seeing outcomes.
- Independent scientific review approves the lane and sets `verification_status=pass`, `chemistry_assurance=curated_protein_dna`, and the validation-lane/version; failed review remains durable and visible.

**Rollback:** keep protein-only product enabled; hide curated-complex option.

### Phase 7 — DRT4 classical-MD research pack

**Objective:** Enable scientifically defensible fixed-topology DRT4 dynamics, not reaction simulation.

**Work**

1. Curate and version parameter packs for `9VDP`, `9VDO`, `9VDV`, controls, and selected mutants.
2. Freeze one exact chemical-state contract per pack before parameterization: complete residue/atom inventory and numbering; dCTP protonation/charge/covalent state; DNA strand lengths, sequence register, termini and 5′-triphosphate state; Mn²⁺ count/occupancy and active-site assignment; modeled/missing atoms or residues; protonation/tautomer choices; mutation and control definitions; covalent links/bonds; crystallographic waters/ions retained or removed; and the template/parameter source plus version/hash for every component.
3. Select the primary Mn²⁺ treatment and preregister a justified sensitivity analysis across materially plausible validated nonbonded/12-6-4, bonded, or restrained alternatives; define external coordination/hydration benchmarks and pass/fail criteria.
4. Freeze observables, exact algorithms/selections, exclusions, sampling plan, independent replica count with power/precision rationale, effective-sample-size/stationarity criteria, extension/stop rules, multiplicity policy, and uncertainty analysis in an immutable validation-plan version before production.
5. Implement the DRT4-specific analysis contract.
6. Run staged short-to-long validations with stop/go review at each stage without changing the frozen confirmatory plan after outcome inspection.
7. Publish full preparation, state-definition, validation-plan, and parameter provenance.

**Gate**

- Parameter/chemistry review is complete and checksummed.
- Source-to-prepared reconciliation has no unexplained loss or substitution.
- Metal/substrate/DNA geometries and metal hydration/coordination benchmarks remain physically interpretable under predefined primary-model and sensitivity criteria.
- Control/mutant comparisons use the frozen independent-replica plan, effective-sample-size/convergence rules, and report uncertainty/multiplicity handling.
- Independent review sets the durable DRT4 validation-lane/version and verification status; a failed lane cannot advertise DRT4 scientific capability.
- Reports state that classical MD does not predict bond formation, barriers, or rates.

**Rollback:** keep generic/protein–DNA MD available; withdraw only the DRT4 parameter pack and analyzer capability.

### Phase 8 — OpenMM experimental adapter, then optional promotion

**Objective:** Add a genuinely useful second engine without weakening the common product contract.

**Work**

1. Build/pin a CUDA-capable OpenMM runtime and probe every GPU.
2. Implement the adapter and explicit capability mappings.
3. Pass scheduler, lifecycle, restart, ingestion, artifact, API, analysis, and UI parity suites.
4. Validate the same 1AKI and protein–DNA initial-state/ensemble contracts with preregistered parity plans: exact observables, unit/selection/transform definitions, independent replica counts, minimum sampling/effective sample size, equivalence or non-inferiority margins, multiplicity policy, hardware class, and pass/fail rules frozen before comparison.
5. Re-run the local performance matrix as workload observations on all supported GPU classes.
6. Identify and test the Python-native/custom-force use case that justifies availability.

**Gate to experimental selection**

All parity items in §6.6 pass through a staged ladder: (1) capability/semantic-field parity, (2) identical initial-state/topology and run-control provenance where scientifically comparable, (3) restart/artifact/lifecycle parity, and (4) preregistered ensemble equivalence/non-inferiority. Explicit user selection works; no silent fallback exists; the UI labels it experimental and shows resolved capabilities and lane limitations.

**Gate to default consideration**

Scientific parity passes and a separately reviewed selection record demonstrates the fit for named workload classes using measured throughput, time-to-solution, memory headroom, failure/restart behavior, custom-force/native-Python need, maintainability, and support burden. No universal percentage or automatic default switch applies; each default change is a separate reviewed decision.

**Rollback:** remove OpenMM from deployed capability response; GROMACS artifacts/contracts remain unaffected.

### Separate future lane — QM/MM or reactive chemistry

QM/MM/reactive work requires a different product/model contract, engine inventory, topology/state model, validation corpus, and scientific claims. It may consume equilibrated classical-MD snapshots through checksummed artifact lineage, but it does not masquerade as another `molecular_dynamics` engine adapter.

## 15. Test strategy

### 15.1 Unit/contract tests

- JSON schema positive/negative fixtures.
- Capability negotiation table tests.
- materialized domain-separated seed schedules are stable for a frozen manifest and unique across replica/component domains;
- execution-plan hash stability;
- path containment and symlink rejection;
- adapter argv without shell interpolation;
- GROMACS convergence/checkpoint parsers;
- portable path serialization;
- manifest checksum validation.

### 15.2 State-machine tests

Model transitions and race cases:

- duplicate submission/child creation;
- completion versus cancellation;
- completion event replay;
- preparation failure;
- one replica failure/retry;
- partial terminal run;
- finalizer/ingestion failure;
- API restart with live process;
- orphan process/reservation;
- corrupt checkpoint;
- mixed completed/queued/running descendants.

Property: no reachable state allows a completed parent with missing required ingestion or a running replica without exactly one scheduler reservation.

### 15.3 Integration tests

- FastAPI submission → DB graph → coordinator lifecycle → scheduler → singleton Nextflow → direct or workflow-adapter launch → ingestion → analysis → API result.
- Direct and configured workflow-adapter paths pass the same startup rejection, delayed acceptance, missing run/process identity, timeout, replay, cancellation, and API-restart fault matrix.
- Two replicas must yield two child jobs and two independent reservations.
- CPU preparation/finalization never get a GPU assignment.
- Real GROMACS 1AKI fresh/restart/cancel tests.
- Result reconciliation after service restart.
- Artifact range download and large-file memory bounds.

### 15.4 Frontend tests

- MD model visibility follows deployed capabilities.
- preflight errors bind to exact fields;
- queue parent/child progress;
- retry/cancel permissions and states;
- generic fail-closed rendering;
- trajectory lazy-load, disposal, and error recovery;
- units, replica uncertainty, and missing-data display;
- no polling explosion or full-trajectory fetch from list/detail surfaces.

### 15.5 Scientific acceptance

Infrastructure, chemistry, and scientific gates are separate reports and durable state axes. Every report includes exact inputs, an immutable validation-plan version registered before production, environment/container hashes, raw machine-readable output, exact methods/selections/units, exclusions, failures, independent-replica and effective-sample-size accounting, uncertainty/multiplicity policy, and thresholds/extension rules defined before execution. `workflow_status=completed` never implies `verification_status=pass`.

## 16. Accepted / rejected / deferred ledger

### Accepted now as design decisions

- GROMACS 2025.3 phase-1 default.
- Common orchestration plus explicit engine adapters.
- BMS scheduler/job DB as sole control-plane authority.
- One scheduler-visible child job and reservation per replica.
- CPU minimization; GPU NVT/NPT/production.
- Independent replicas may use heterogeneous GPUs; a continuous trajectory lineage may not cross incompatible GPU classes.
- Physical GPU provenance distinct from container-local device `0`.
- Materialized domain-separated seed schedules, append-only attempt/segment history, no-append restart.
- Workflow completion and scientific verification are orthogonal durable states.
- Result-root-relative paths, checksums, immutable provenance, transactional ingestion.
- 1AKI → protein–DNA → DRT4 validation order.
- Validated GROMACS bundles as the first research-capable input lane.
- DRT4 geometry/dynamics research only after chemistry acceptance.
- Fold-CP remains separate.

### Rejected

- One parent GPU reservation followed by concurrent internal Nextflow replica scatter.
- Sending scheduler physical GPU index to an isolated one-GPU container as the local engine index.
- A GROMACS-only function presented as engine-neutral dispatch.
- Declaring `molecular_dynamics_v1`, `trajectory_viewer`, or analyzers before implementation.
- Marking a job completed before artifact validation and ingestion commit.
- Silent fallback, parameter dropping, chemistry removal, or scientific-setting changes on retry.
- Database-only pause for a running MD process; pause must be checkpoint-terminate and process-verified.
- `grompp -maxwarn > 0` in scientific stages; only intentional pre-ionization charge permits `-maxwarn 1` with recorded reason.
- Stock protein-only `pdb2gmx` recipe for DRT4.
- Storing trajectories as `Design` records or embedding them in API JSON.
- Classical MD claims about bond formation, barriers, or rates.

### Deferred behind explicit gates

- OpenMM production selection/default.
- `amber_system_xml` is a reserved schema mode only: absent from deployed capability responses and rejected before scheduling until a native adapter passes topology, restart, artifact, and scientific-parity gates; AMBER-to-GROMACS conversion remains deferred.
- DRT4 enablement pending dNTP/Mn²⁺/termini/protonation/parameter review.
- Enhanced sampling, alchemical methods, custom-force UI, constant-pH, coarse-grain, and polarizable models.
- QM/MM and reactive methods as a separate product lane.
- Cloud/distributed multi-host scheduling.

## 17. Source-target index

### Existing files to modify

- `platform/api/database.py`
- `platform/api/main.py`
- `platform/api/config/model_registry.py`
- `platform/api/schemas.py`
- `platform/api/routers/jobs.py`
- `platform/api/routers/analyses.py`
- `platform/api/services/gpu_orchestrator.py`
- `platform/api/services/job_control.py`
- `platform/api/services/queue.py`
- `platform/api/services/nextflow.py`
- `platform/api/services/result_ingester.py`
- `platform/api/services/result_state_integrity.py`
- `platform/api/services/result_contracts.py`
- `platform/api/services/analysis_autorun.py`
- `platform/api/services/analysis_registry.py`
- `platform/api/services/analysis_runs.py`
- `platform/api/services/analysis_subprocess.py`
- `platform/api/services/analysis_worker.py`
- `platform/api/services/workflow_adapter.py`
- `platform/api/routers/workflow_adapter.py`
- `platform/api/workflow_adapter_app.py`
- `platform/api/config/models/molecular_dynamics.yaml`
- `compose.core-runtime.yml`
- `nextflow.config`
- `nextflow_schema.json`
- `schemas/md_job_v1.schema.json`
- `schemas/md_run_v1.schema.json`
- `scripts/bms_md/*`
- frontend components listed in §12.4
- frontend API/model-inventory modules used by those components

### New files/packages

- `platform/api/routers/molecular_dynamics.py`
- `platform/api/services/md/__init__.py`
- `platform/api/services/md/contracts.py`
- `platform/api/services/md/capabilities.py`
- `platform/api/services/md/coordinator.py`
- `platform/api/services/md/state.py`
- `platform/api/services/md/artifacts.py`
- `platform/api/services/md/preparation.py`
- `platform/api/services/md/engines/{base,gromacs,openmm}.py`
- singleton preparation/replica/finalization Nextflow workflows and modules
- focused backend/frontend tests for each phase

The workflow-adapter integration lives under `platform/api/{services,routers}/workflow_adapter.py` and `platform/api/workflow_adapter_app.py`; there is no separate `platform/workflow-adapter` source tree to target.

## 18. Definition of product completion

The first-class GROMACS MD product is complete only when all of the following are true:

- The user can validate, launch, observe, pause/resume where meaningful, cancel, retry, and inspect a run through normal BMS surfaces.
- Every replica is a scheduler-visible child with exactly one correct GPU reservation.
- Parent/child state survives service restarts and reconciles without contradictory DB/process truth.
- Real 1AKI runs pass on all local GPU classes, including continuation and fault injection.
- Required results are portable, checksummed, transactionally ingested, and range-downloadable.
- The analyzer and trajectory viewer work on real retained artifacts and remain bounded.
- Capability responses match deployed runtime facts.
- Unknown chemistry and unsupported capabilities fail closed.
- OpenMM is not falsely exposed.
- Independent specification and code-quality reviews are reconciled.
- No secret, generated work directory, or transient host-absolute path enters executable configuration or durable result manifests; this planning document may identify the isolated worktree and retained evidence locations.
- No unrelated dirty work enters the implementation change.
- Promotion, DB migration, service restart, and rollback are separately approved and verified.

## 19. Immediate implementation order

1. Execute Phase 0 and remove premature product/capability exposure.
2. Extract and test the GROMACS adapter and capability resolver.
3. Add durable MD run/replica/artifact records and coordinator state machine.
4. Replace internal replica scatter with scheduler-visible singleton children.
5. Make result ingestion transactional and restart/reconciliation fault-tolerant.
6. Add artifact/analysis APIs and first-class UI only after backend contracts pass.
7. Run full 1AKI hardware/failure matrix and independently review it.
8. Release GROMACS product behind a controlled feature flag.
9. Validate protein–DNA chemistry.
10. Curate and review DRT4 parameter packs before any DRT4 scientific claim.
11. Implement OpenMM only against the same parity suite; never by weakening it.
