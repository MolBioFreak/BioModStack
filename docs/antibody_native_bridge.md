# Antibody native component / remote bridge integration

This source change implements the antibody parent computation path on one native
Nextflow DAG. It is **offline orchestration regression evidence, not live model,
remote-worker, deployment, or full bridge acceptance**.

## Native authority

- `full_orchestrator` RFantibody grouping remains `designs_per_job`, with the
  legacy `job<ordinal>_rfantibody_child` output coordinate. Standard GPU grouping
  is unchanged. Framework input is a reusable value; RF `.trb` companions are
  explicitly staged instead of being assumed beside task symlinks.
- FAMPNN uses the existing `PrepFAMPNN` / `RunFAMPNN` / `FilterFAMPNN`, complete
  workflow analysis policy and prepared provenance. The additive
  `RunFAMPNN.out.antibody_batches` output carries the original batch ordinal;
  `CollectNativeAntibodyFAMPNN` preserves `job<ordinal>_` candidate names and emits
  hash-bound component result receipts. No child HTTP request reconstructs a
  subset of scientific settings, and no duplicate child preparation is needed.
- The same `MATURATION_CHILD_CORE` serves standalone children and native parent
  batches. Backbone refinement, sequence maturation and post-validation
  maturation use distinct stage-scoped instances. Top-N remains **per original
  sorted child group**, not global. Native anchor selection, PPIFlow samples,
  redesign, scoring, filtering and ANARCII remain in the DAG.
- Boltz, Protenix and ESMFold2 use the existing batch processes. Exploration
  preserves the old sorted balanced grouping (`ceil(N / ceil(N / size))`);
  sequential batching remains its original buffer policy. Boltz alignment is
  explicitly included. Former child stability/immunogenicity scoring remains
  before post-validation refinement. Optional output categories have explicit
  empty joins; declared artifacts cannot be silently omitted during collection.
- Review and terminal ANARCII execute on the worker using the existing native
  process, not a callback to the host annotation endpoint. Native review metrics
  and metadata accompany review structures and ANARCII outputs.
- Host ingestion/stage-report callbacks are removed from antibody orchestration
  and finalization. Nextflow schedules scientific tasks; no nested scheduler,
  full API server, host DB, or arbitrary command-submission adapter is introduced.

## Integration required from the parent work package

No `nextflow.py`, `jobs.py`, `bundle.py`, registry, or `component_runtime.py` edits
are part of this branch.

1. Map the antibody native workflow/component closure to the selected worker
   after the complete plan is admitted. Provision the selected model closure,
   plus `antibody_tools.sif` for required review/terminal ANARCII and worker
   support Python for native staging/receipts. Do not advertise untested modes
   simply because their former callback filenames disappeared.
2. Integrate the shared FrustraMPNN file-based runtime work. This branch still
   consumes `SchedulerFrustraMPNNParentFanout`; that shared adapter is owned by
   the sibling work package. Enabled FrustraMPNN conformance requires its merge.
3. Integrate shared MSA policy/artifact staging. The Protenix generated-MSA
   branch still consumes its shared preflight/runtime. Offline tests cover
   explicit no-MSA, not provider submission, fleet policy, or generated MSA.
4. Host-authorized review must supply the **complete validated effective
   scientific request** as system-owned `antibody_checkpoint_settings` (a map,
   not a new operator setting). Empty/missing authority blocks checkpoint
   publication. Do not put credentials or runtime secrets in that object.
5. Project review checkpoints and native result/annotation receipts into the
   existing host UI/database after separately authorized retrieval. A Nextflow
   exit of zero at a review boundary is **not science completion**. Detect
   `gates/<stage>/checkpoint.json` (`status=awaiting_review`,
   `science_complete=false`) before publishing a completed workflow. Dependency
   preflight checkpoints use `bms.antibody-dependency-checkpoint.v1` and
   `status=blocked_dependency`, never implicit approval or CPU/local fallback.
6. Quiescence, reservation release/reacquisition, authenticated decision staging,
   attempt/source fences and authorized transport remain host controller duties.

### Review transport contract

`bms.antibody-checkpoint.v1` contains job/workflow/stage identity, complete
settings and their canonical SHA256, native artifact roles, contained relative
paths, sizes, hashes and a `checkpoint_id` covering the whole receipt. Reusing a
checkpoint directory with different authority fails rather than overwriting a
sealed identity. `component_runtime.ResultReference` verifies selected bytes.

After native review, the host stages a trusted file:

```json
{
  "schema_name": "bms.antibody-checkpoint-decision.v1",
  "checkpoint_id": "<exact receipt digest>",
  "job_id": "<checkpoint job identity>",
  "stage": "post_fampnn",
  "authorized_by": "<authenticated operator identity>",
  "decision": "continue",
  "selected_artifacts": ["review/structure/<exact candidate>.pdb"]
}
```

The host starts the supported continuation with `interactive_gate_continue`,
`antibody_checkpoint`, `antibody_checkpoint_decision` and `selected_input_dir`.
The selected directory must contain exactly the selected PDB names/bytes.
Foreign, modified, empty, duplicated, unbound or unauthenticated-by-contract
selections fail before science is scheduled. The file's operator label is
**not itself authentication**: only the controller may stage decision authority.
A legacy Boolean alone can no longer authorize continuation.

## Scientific acceptance safeguards

Selected execution processes are required; process failure fails the DAG and
zero-yield maturation fails explicitly. Scientific candidate rejection remains
in canonical filters. Retiring collectors also retires their undocumented
"skip missing child" success behavior and accidental collection of redesign
**debug** PDBs alongside filtered results. This tightening must be reviewed in
the bridge requiredness/partial-failure matrix before production acceptance;
it is not represented as historical byte-for-byte failure-policy parity.

The work does not add a post-IgGM structure-revalidation loop. The existing
IgGM + FrustraMPNN stale-structure guard remains. AntiFold sequence-only limits,
Caliby, OpenMM, immunogenicity and all model-native settings remain their
existing authorities; they are not live-qualified by these fixtures.

## Reproducible offline evidence

From `platform/api`, set `BMS_TEST_NEXTFLOW_JAR` to a pinned local Nextflow 25.10.1
jar and run:

```sh
uv run --frozen --group dev python -m pytest \
  tests/test_antibody_native_worker_dag.py \
  tests/test_frustrampnn_antibody_parent_wiring.py -s -q
```

Tests copy source into isolated temporary roots and substitute **only scientific
model-output fixtures**. The real parent DAG, native grouping, staging,
finalization, checkpoint code, joins and guards execute. Coverage includes all
three validators, multi-stage maturation with per-group top-N, complete native
annotations, required failure, zero yield, no implicit review approval,
selected continuation, immutable receipts, and root-independent checkpoint
identity. No live provider calls or paid runs are made. Tests without a pinned
jar explicitly skip the DAG cases rather than report fabricated execution.
