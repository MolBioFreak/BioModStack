# MD closure integration contract

## Activation (global integration owner)

The common local/worker full-workflow entrypoint is
`workflows/experimental/molecular_dynamics/orchestrator.nf`, named workflow
`MD_CLOSURE`. `workflow.nf` delegates to it, rather than retaining the old
replica-only local graph. Standalone prepare/replica/analyze/finalize entrypoints
remain component/recovery tools; their exit status is **not** full MD success.

Project the already validated effective native MD request with
`scripts.bms_md.closure_plan.compile_md_closure(config)`. This returns the exact
required component DAG, native replica seeds, requested configuration/digest,
runtime identities, and required output paths. It does not validate or replace
the global scientific request schema. Keep original requested settings alongside
materialized effective bindings in the shared plan; do not reconstruct either
from shell arguments. Both placements must activate this same projection.

Bind these Nextflow parameters from the approved plan/runtime (not the browser):

- `md_job_config`: materialized native JSON with the parent's `job_id`;
  `md_input_root`: contained immutable biological input root. Preserve typed
  nested input hashes and effective physical `execution.gpu_id` binding.
- `job_id`, `gpu_id`, `code_root`, `out_dir`, `data_root`/`container_dir`.
  `out_dir` must be a unique attempt/generation root, never reused for a different
  request. All required input paths and working directories must be bound inside
  the scientific containers, including paths nested in the native config.
- `md_preparation_container`, `md_preparation_runtime_lock`, selected
  `md_gromacs_container` **or** `md_openmm_container`, and
  `md_analysis_container`. Use the existing MolecularDynamics* label definitions
  in `nextflow.config`, not generic shell or container-less scientific tasks.
- `md_analysis_enabled=true` only after normal feature/capability admission;
  `md_analysis_sif_sha256` must be
  `3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68`;
  `md_analysis_implementation_sha256` must match the selected release's
  `scripts.bms_md.analysis._implementation_sha256()`.
  Forward approved `md_analysis_stride` and `md_analysis_max_points` unchanged.
- Preserve `BMS_FEATURE_MOLECULAR_DYNAMICS=1` through the existing scientific
  runtime environment, after admission. Never enable it by fallback in a worker.

Runtime closure: Nextflow 25.10.1 (normal launcher/JVM options), Apptainer,
existing preparation runtime and lock, selected native MD engine runtime,
pinned MD analysis SIF, support Python with the frozen API/helper dependencies
(including jsonschema), `scripts/bms_md/**`, and native run/analysis/job schemas.
The new coordinator imports native validation/analysis identity code but does
not import the host API, database, or HTTP scheduler. Native MDAnalysis and
Parquet computation still runs inside the existing analysis runtime.

One GPU replica and one analysis task run at a time (`maxForks 1`, separately
surfaced in the plan). This avoids multiple replica schedulers competing for the
single assigned physical GPU. For existing resource labels the maximum is
8 CPU/16 GB for GROMACS, 4 CPU/12 GB for OpenMM, plus the one assigned GPU and
attempt storage. Preparation, replicas and analysis have dependency barriers;
no analysis task overlaps replica computation. Existing engine stage/timestep,
chemistry, platform, seed, output, and analysis behavior is unchanged. Native
`replica_seed()` wraparound is authoritative in both compiler and host validator.

## Component/attempt projection

Logical IDs are deterministic, scoped by the shared attempt outside the native
job identity: `{job_id}:md_preparation`, `{job_id}:md_replica:{index}`,
`{job_id}:md_aggregation`, `{job_id}:md_analysis:{index}`,
`{job_id}:md_completion`. Replica and analysis IDs are retained in the native
aggregate/analysis collection `child_ids`. Project these into the shared
attempt-local ledger and host logical children from the plan and Nextflow task
trace; no host child row is a prerequisite for starting computation. There are
no MD spawner/waiter HTTP calls in this execution path.

The global executor must retain its ordinary attempt/lease/source fencing,
durable cancellation intent, bounded Nextflow/process-tree termination and
quiescence before resource release or diagnostic sealing. Never import a
success marker from a cancelled/lost or superseded attempt. `-resume` is allowed
only for the same accepted attempt/request/runtime with supported Nextflow
cached tasks or explicit engine checkpoint policy; reboot is not automatic MD
checkpoint authorization. No MD-specific second scheduler/server is required.

## Required return/native import

Return `preparation/**`, `normalized_config.json`, `replicas/**`, `manifest.json`,
`analysis/**`, `md_completion_barrier.json`, and the shared execution receipts /
intentionally selected Nextflow task diagnostics. The barrier is created only
after exact requested replica/seed/config/runtime/artifact validation, native
analysis for every replica, and exact immutable native analysis collection.
Native schemas remain `bms.md.run.v1`, `bms.md.aggregate.v1`,
`bms.md.analysis.v1`, `bms.md.analysis-artifacts.v1`,
`bms.md.analysis-collection.v1`, `bms.md.completion-barrier.v1`.

The shared worker result seal must require the completion barrier and validate
all hashes, not merely trajectories or a replica process exit. Retain failed
and cancelled task diagnostics as failure, never manufacture missing analyses.
After authorized return, invoke the existing
`services.md.completion.validate_and_finalize_md_job` / native result importer
with `job.params.md_job_spec` intact and project component lineage into the
existing MD lifecycle records. The bridge tests exercise the unchanged native
`services.md.results.completion_barrier` on the collected result tree.

## Offline verification

From `platform/api`:

```sh
PYTHONPATH=../.. BMS_TEST_NEXTFLOW_JAR=/path/to/pinned/nextflow-25.10.1-one.jar \
  uv run --frozen --group dev --with MDAnalysis==2.9.0 python -m pytest \
  tests/test_md_bridge_closure.py tests/test_md_bridge_dag.py
```

`--with` is an ephemeral test overlay; it does not modify the frozen API lock or
external-API environment. Tests consume licensed checked-in GRO/XTC and PDB/DCD
fixtures, derive final-frame fixture coordinates with MDAnalysis, and run the
actual native analyzer/Parquet writer. They are not engine/SIF/GPU acceptance.
The executable fixture DAG reuses production joins/seal, tests singleton and
multiple replicas, missing replica, failed analysis, process cancellation and
cached restart. Production DAG preview includes both engines and mandatory
analysis. Crash-before-barrier replay and conflicting-seal tests use immutable
native publication. No paid GPU/provider/hardware call is needed or performed.

Source validation: the dedicated closure/DAG suites passed **23 tests**. The
expanded MD regression selection passed **69 tests**, with two existing global
integration-boundary tests excluded after observing their failures:
`test_nextflow_analysis_terminal_publication_cannot_flush_stale_state_before_guarded_cas`
asserts a stale literal in the untouched `services/nextflow.py`; and
`test_analysis_child_rejects_any_requested_gpu_before_persistence` passes an
`object()` session to the untouched `routers/jobs.py`, which now loads the
execution parent before that validation. The global integration owner must
reconcile those boundaries; no global code was changed in this MD worktree.
