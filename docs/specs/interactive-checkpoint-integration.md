# Interactive checkpoint integration contract

Implements the shared process boundary for legacy-region Protein Local Redesign
and PPIFLOW generator; native-request RFD3 continues to reject gates by its existing
contract. No inference acceptance or all-workflow acceptance is claimed here.

## Parent/controller integration (required before remote admission)

1. Stage a scheduler-owned `checkpoint_context` JSON file with **exactly** nonempty
   string keys `job_id`, `attempt_id`, `worker_id`, `source_digest`, `plan_digest`.
   It is not a browser-editable parameter. The plan digest must cover the original
   effective science, inputs, seeds and all settings; revalidate that plan before
   every continuation. Local placement uses a stable local worker ID, not a second
   gate implementation. Pass `--checkpoint_context /worker/attempt/context.json`.
2. `OpenInteractiveGate` now seals DAG-declared task files, writes
   `OUT/checkpoints/STAGE/receipt.json` and `state.json`, publishes
   `OUT/gates/gate_STAGE.json`, and **exits**. There is no HTTP call or sleeping
   waiter. Successful Nextflow exit with `awaiting_review` is **not scientific
   completion**. Controller must project this state before terminal-success/import.
3. Observe predecessor exit and all descendant ownership before releasing unused
   reservations. This module does **not** release reservations, stop instances,
   declare quiescence, delete outputs, or move the attempt. Retain the checkpoint,
   original attempt inputs, runtime/source, Nextflow work/cache and worker storage.
4. Default is no scientific retrieval. Explicit review retrieval binds
   `(attempt_id, checkpoint_id)` and transfers only receipt `artifacts` with
   containment, size/hash verification. Register those native bytes under a review
   generation, not full-result completion. Artifact `role=candidate` is selectable;
   `role=review` includes native scientific sidecars, not arbitrary work caches.
5. Use the host's authorized ownership/review-retrieval adapter to call:

   ```python
   from scripts.interactive_checkpoint import authorize_decision
   authorize_decision(checkpoint_root, decision,
       principal=authenticated_principal,
       authorize=check_job_ownership_and_verified_review_retrieval)
   ```

   The callback receives `(principal, immutable_receipt, decision)` and must return
   exactly authorization from trusted controller code. A request boolean or mere
   presence of a local file is NOT authorization. On remote placement dispatch
   the already authorized decision through the existing authenticated, strictly
   worker/attempt-bound control channel; the worker adapter supplies the trusted
   authorization assertion there. Do not expose the Python filesystem API directly
   to browser paths. Unauthorized, stale and repeated decisions raise; reconcile a
   repeated HTTP operation from persisted `state.decision`, do not resubmit it.
6. On explicit continue, reacquire compatible resources on the **original worker**,
   prove predecessor quiescence and call the trusted worker scheduler interface:

   ```python
   from scripts.interactive_checkpoint import prepare_continuation
   binding = prepare_continuation(checkpoint_root, original_context,
       execution_id=durable_nextflow_execution_id,
       quiescence=verified_predecessor_exit_evidence,
       reservation=verified_reacquired_lease_identity)
   ```

   Evidence arguments are scheduler attestations, not UI parameters.
   `quiescence` is closed: `predecessor_execution_id`, `worker_id`, `attempt_id`,
   `exit_observed: true`, `descendants_quiescent: true`. `reservation` is closed:
   `worker_id`, `attempt_id`, `plan_digest`, `lease_id`, positive integer `generation`.
   Identity mismatches and false/missing quiescence are rejected. This creates
   one durable claim. Another execution ID is rejected. Reopening the same ID
   verifies data and returns the same continuation, **not permission to launch
   again**. Controller must reconcile uncertain submission / worker boot identity;
   only restart a stopped supported Nextflow continuation using its saved session
   and `-resume`, never a fresh session after ambiguous SSH failure.
7. Launch the SAME workflow with unchanged effective science/plan, original input
   bindings and these additional scheduler parameters:
   `--checkpoint_continuation ROOT --checkpoint_context CONTEXT
   --checkpoint_execution_id EXECUTION_ID`.
   `lib/InteractiveCheckpoint.groovy` verifies via the CLI *before building the
   science DAG*. Legacy naked `interactive_gate_continue` and PLR resume paths are
   rejected. No browser-supplied resume directory can override the selected set.
   Local redesign skips only the already-completed prefix selected by stage;
   final validation and PPIFLOW generator reviews perform no repeated inference.

## Receipt and decision v1

`schema_version` is `bms.interactive-checkpoint/1`. Receipt contains all context
bindings, stage, `checkpoint_id` (SHA256 of canonical JSON of the entire receipt
excluding that field), ordered `artifacts`, review metadata,
`science_complete: false`, and `retrieval_policy: explicit_review_only`.
Artifacts contain `artifact_id`, `role`, contained `path`, `sha256`, `size_bytes`,
and `format`. Native bytes are copied unchanged and reverified on decisions and
continuations. The workflow adapters retain legacy filename-sorted directory review/resume order
without changing scientific producer order; the shared ledger preserves the
explicitly declared order. Selection must be a canonical
ordered subset. Equal sidecars are deduplicated, distinct sidecar bytes remain
separate identities even with the same filename.

`review:artifact_bindings` is a declared JSON review artifact with schema
`bms.review-artifact-bindings/1`: its `bindings` array contains
`{worker_path, artifact_id}` entries for original/staged source aliases. Native
import adapters resolve *typed path-bearing manifest fields* through this table
and their verified host artifact registrations. Do not perform global string
replacement, execute these paths, or rewrite archived native manifests. This
binding table is provenance, not a scientific metric table.

The closed decision object is:

```json
{
  "schema_version": "bms.interactive-checkpoint/1",
  "checkpoint_id": "RECEIPT_SHA256",
  "job_id": "job", "attempt_id": "attempt", "worker_id": "original-worker",
  "source_digest": "original-source", "plan_digest": "original-plan",
  "decision_id": "durable-controller-operation-id",
  "action": "continue",
  "selected_artifacts": [{"artifact_id": "candidate:name.pdb", "sha256": "FILE_SHA256"}]
}
```

`reject` requires an empty selected list and never creates a continuation.
`continue` requires a nonempty ordered unique candidate subset. There is no auto
approval field, arbitrary path, shell command, placement or scientific override.
States: `awaiting_review -> approved -> continuing`, or
`awaiting_review -> rejected`; `sequence` increments on committed transitions.
State is atomically replaced under a process lock with fsync, with the full
accepted decision/principal retained. Source/worker/attempt/plan fencing is exact.
A receipt committed immediately before a crash can reconstruct initial state on
idempotent sealing. Resource release remains outside this state machine.

Worker CLI (no unauthenticated decision/approval CLI is intentionally exposed):

```
python3 scripts/interactive_checkpoint.py seal --request REQUEST.json --output GATE.json
python3 scripts/interactive_checkpoint.py verify-continuation --root ROOT --context CONTEXT.json --execution-id ID
```

Seal request keys: root, context (file), stage, ordered candidates (task file
paths), review (declared task file paths), metadata. This request is generated by
Nextflow, not the browser. Schema/authorization and host retrieval projection must
be integrated by the controller owner. This file alone does not enable remote
workflow admission.

## Offline evidence and remaining acceptance

- `tests/test_interactive_checkpoint.py`: 37 passing cases with Nextflow 25.10.1.
  Actual workflow DAGs run with explicitly labeled synthetic external-science
  process contracts: all three local-redesign gates with FAMPNN and MPNN, ESMFold2
  validation transport, PPIFLOW generator join/collect/gate; explicit continuation
  and Nextflow `-resume` do not resubmit completed scientific fixture processes.
  Production final-review continuation DAGs also execute without science tasks.
- Actual mandatory validator-suite enforcement rejects partial/failed receipts;
  injected failures in RFD3/filter, FAMPNN/filter, MPNN/filter, ESMFold2 and all four
  PPIFLOW stages never create a final-review checkpoint. Unauthorized, stale,
  reordered/hash-changed/replayed decisions, unbound approval flags, wrong-worker
  continuation, missing/corrupt files, receipt/state crash recovery and interrupted
  selection materialization are tested.
- Existing API local-redesign regression suite: 39 passing tests.
- Testing exposed and fixed the existing PPIFLOW filtered collector passing a
  metadata tuple to a `path` input. Only tuple projection changed; scoring,
  filters, seeds and sample generation are unchanged. The legacy candidate sets
  remain intentional compatibility contracts: MPNN sequence review uses raw native
  PDBs after its filter finishes; FAMPNN uses filtered PDBs; PPIFLOW uses the filtered
  collector's PDB set, including enriched seed structures. Changing those sets
  requires separate scientific review, not an orchestration-side correction.
  Declared missing collector
  artifacts and malformed/duplicate backbone manifest entries now fail rather
  than silently disappearing from required review evidence.
- No native inference, paid job, worker deployment or live native import was run.
  These synthetic fixtures are **not** frozen native-model outputs. Other validator
  combinations and exact-release real remote acceptance remain outstanding.
  Controller authorization, review transfer/native registration, quiescence/lease
  observation and uncertain-submit/reboot reconciliation must use the integration
  points above before enabling remote admission.

Run from `platform/api` with an external frozen environment:

```
UV_PROJECT_ENVIRONMENT=/external/api-env uv run --frozen --group dev python -m pytest ../../tests/test_interactive_checkpoint.py -q
UV_PROJECT_ENVIRONMENT=/external/api-env uv run --frozen --group dev python -m pytest tests/test_protein_local_redesign.py -q --capture=no
```

Set `BMS_TEST_NEXTFLOW_JAR` to an existing pinned Nextflow one-jar for fully offline
execution when the system `nextflow` command is a container wrapper. The harness
then uses Java with the module-opening options required by its serialization
runtime; it never downloads a scientific runtime or starts a model container.
