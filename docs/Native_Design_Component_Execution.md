# Native design component execution

The design workflow migrations use the same Nextflow modules for local and worker placement. They do not submit computational children through workstation HTTP endpoints or require host child Job rows before computation.

## Parent bindings

The launcher supplies the immutable source root (`code_root`), the supported frozen helper Python (`api_python`), contained attempt output root (`out_dir`), canonical image bindings, parent `job_id`, and system-owned `component_attempt_id`. The attempt identifier is not a Nextflow task hash. FrustraMPNN requires the scheduler-assigned `frustrampnn_physical_gpu_id`; the parent retains ownership while the native DAG is running. Model subprocesses continue through the existing native runners. The coordinator does not allocate GPUs or start another scheduler.

Bundle the transitive dependencies of `modules/frustrampnn_native_parent.nf`, `modules/boltzgen_native_campaign.nf`, `scripts/native_frustrampnn_parent.py`, and `scripts/native_boltzgen_campaign.py`, including their existing scientific imports. Helper Python dependencies are part of the supported runtime, not an implicit workstation dependency.

## FrustraMPNN

- Protein-design and Fold-CP producers pass typed candidate metadata with their structures to native preparation.
- Conformational mapping consumes its original prepared candidate requests. It does not upload or re-normalize those requests through a host callback.
- Shared grouping preserves candidate order, requested settings, batching enablement, group sizes, and singleton remainders.
- The final join rejects missing, duplicate, foreign, failed, or request-conflicting results. Required native results are not replaced by a generic success receipt.
- Canonical bundles are published beneath `frustrampnn/results/<candidate>/`. The native parent terminal receipt and SQLite grouping ledger are beneath `frustrampnn/component_runtime/`.
- A failed native group writes an attempt-scoped, request-hash-bound diagnostic beneath `frustrampnn/component_runtime/failed/` before re-raising the original failure. This records failure, not scientific success, and does not change fail-fast behavior.
- Ordinary protein-design metadata retains canonical candidate identity even when legacy fold and sequence IDs are absent. Nullable native ranks remain integers or null; structured scientific extensions retain JSON encoding in CSV cells. Projection still requires exact manifest/candidate agreement.

## BoltzGen campaigns

Preparation produces a portable native input directory with exact SHA-256 bindings, including typed scaffold references. Missing optional inputs are empty path collections, not missing repository placeholder files. The native root YAML and complete bound file set are required; symlinked or changed members are rejected before expansion.

The existing wrapper and filter execute each campaign component. Native execution/filter settings remain bound in the plan; unknown child overrides and overrides of grouping/runtime fields are rejected. One-child campaigns and singleton remainder groups retain the same metadata/path association as larger campaigns.

The collector requires an exact terminal join. The existing partial-failure policy remains at least one completed child; missing terminals are not interpreted as failed children. Successful filtering with zero selected designs is distinct from a missing filter report. Native child evidence and selected structures are retained beneath `components/boltzgen/campaign/`; raw inference outputs remain separately published.

## Fold-CP publication

Raw native results remain under `run/boltz_cp_experimental/native/`. Staged inputs are published under `inputs/boltz_cp/`, and processed input evidence under `processed/boltz_cp/`. Processed input files are excluded from flattened prediction publication. Ambiguous duplicate prediction basenames fail explicitly rather than overwriting an earlier artifact.

## Operational boundaries

Nextflow owns task execution and process cancellation. A cancellation request or telemetry event is not proof of process quiescence or scientific success. Original source bytes, native settings, native validation, and native import authority remain in force.

These interfaces describe implementation, not a deployment or scientific acceptance certificate. A committed workflow is not proof that its release is installed, that every workflow mode is admitted, or that live GPU/remote execution is accepted. No automatic remote rental or scientific launch is part of this change.
