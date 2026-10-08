# Supported setup provisioning (byte materialization only)

The existing `start_ui.sh` and `scripts/manage_desktop_services.py` now expose
`provision-plan`, `provision`, and provisioning `resume`. No new downloader CLI,
source-URL option, registry-override option, test-mode flag, registration path, or
approval bypass is exposed to operators.

## Operator sequence

1. Use the existing configuration preview/configure transaction to select external
   storage. Provision resolves the same install profile and supported environment
   overrides. Image objects use **runtime_image_store**: explicit
   `BMS_RUNTIME_IMAGE_STORE`, otherwise `${BMS_CONTAINER_DIR}/.image-store`
   with `container_dir` resolved through the installation profile. This is the
   shared lifecycle store, not `container_dir/objects` or a lane/task cache.
   **weights_root** remains separate for licensed objects and directory
   generations. The two stores must be disjoint.
2. Preview a selected closure (repeat `--model` for multiple models):
   ```sh
   ./start_ui.sh provision-plan --model esmfold2 --json
   ```
   Inspect `plan.models[].artifacts`, all blockers, license IDs, source approval
   references and `plan.store_roots`. Record the returned `plan_digest`.
3. Only when the required release-reviewed metadata exists and the operator has
   actually accepted the listed licenses, run:
   ```sh
   ./start_ui.sh provision --model esmfold2 --operation-id my-provision-1 \
     --expect-plan-sha256 "$PLAN_DIGEST" --accept-license "$REVIEWED_LICENSE_ID" --json
   ```
   Repeat `--accept-license` for each accepted license. This is explicit acceptance,
   not a license or release approval grant. An empty list records no acceptance.
4. After a network/process interruption, rerun the same selection and identity:
   ```sh
   ./start_ui.sh resume --model esmfold2 --operation-id my-provision-1 \
     --expect-plan-sha256 "$PLAN_DIGEST" --json
   ```
   Resume uses the durable acceptance record; it cannot silently add licenses.
   If acceptance was omitted initially, review again and start a **new operation
   ID** with explicit acceptance. Completed verified objects can be reused.

`resume --expect-plan-sha256` selects provisioning resume; existing configuration
resume without that option is unchanged. Provision actions always emit JSON.
Exit 0 means `planned` or `bytes-materialized`, **never installation readiness**.
Exit 3 means blocked, including partial-model failure. Each model reports its own
`blocked` / `bytes-materialized` status and `qualification: not-qualified`.
`ready` and `registered` remain false. A plan may be blocked for one model while a
fully approved independent model can materialize; the aggregate exit stays 3.

## Durable state and concurrency

Journals live at the configured BMS configuration directory under
`provision-v1/<operation-id>/journal.json`. They bind source checkout location,
selected models, complete trusted registry plans, managed generation identity,
normalized profile digest, effective store roots, explicit
license IDs and acceptance timestamp, plus per-model events/results. Writes use
the existing fsync/atomic configuration writer. Provision holds the existing
configuration lock throughout root resolution, stale-plan checks, acceptance
recording, materialization and journaling; it never changes a configuration
transaction. Artifact/member locks and durable checkpoints remain authoritative.

Resume reloads trusted registry metadata and checks the complete plan under lock.
It rehashes completed objects and full layouts rather than trusting saved receipt
success. Changed metadata, roots, selection, mismatched journals, corruption,
unsafe paths or conflicting manifest identities fail closed. A changed plan must
be reviewed anew; a new operation does not override corrupt artifact checkpoints
or changed-manifest reconciliation blockers. A managed release switches generation
identity, so pre-release plans fail with `stale_plan`. Preview again and use a new
operation ID with reviewed acceptance; resuming an old journal with a new plan
fails `journal_plan_mismatch`. The new operation rehashes and reuses the same
verified image/layout bindings without downloading again. A generation switch
during planning fails rather than producing a mixed plan. No automatic deletion/repair command
is supplied. An interrupted state write never constitutes committed acceptance.
Stores and configuration state must remain operator/service-owned; this is not a
security boundary against a hostile actor with the same filesystem principal.

## Release handoff contract

`biomodstack_provision.receipt_bindings(receipt)` yields dependency/path pairs:

- Images: `artifacts[]` where `kind == image` → pinned verified object path.
- Weights: **`layouts[].dependency` → `layouts[].path`**, the verified immutable
  member directory. Individual weight objects named `runtime.sif` are transport
  storage, not runnable images or valid runtime weight-directory bindings.

Full acquisition/layout receipts remain in the per-model journal/report. A later
release transaction must independently revalidate and bind these paths and run
its required qualification/admission checks. This change does not rewrite that
transaction, activate a release, create runtime registrations, or satisfy GPU,
scientific acceptance, toolchain, storage-capacity or service readiness gates.

## Actual production blockers

The checked-in production model registry has no approved acquisition manifests.
Real default selected-model requests return `approved_acquisition_metadata_missing`
for each known dependency, or `runtime_closure_unavailable` when no supported
closure exists. No URLs, pins, licenses or approvals were invented for production.
An opaque weight artifact additionally requires a reviewed member layout before
this CLI will treat the model's bytes as materialized. See
`ESMFold2_Acquisition_Release_Gap.md` for the exact upstream distribution gap.

## Focused acquisition review and regression

Independent integration review found a resume laundering defect: an HTTP response
could deliver the exact pinned payload, then one extra byte. The first invocation
correctly rejected the oversized body but checkpointed the complete pinned prefix.
A second invocation could publish that prefix without preserving the rejection.
The downloader now durably records the terminal oversize rejection and refuses
resume pending explicit reconciliation. A real split-response HTTP regression
asserts the checkpoint reached the exact pinned size and that a second invocation
neither downloads nor publishes. Ordinary short-body/time-limit interruption
remains resumable.

Additional integration guards reject opaque weight directory bindings, ambiguous
image bindings, conflicting artifact IDs across selected models, and overlapping
configured image/license stores before acquisition.

## Tests, not release assets

`tests/provision_cli_fixture_harness.py` is explicitly a test-only subprocess
harness invoked through a temporary test `python3` wrapper. The real shell and
manager parser execute unchanged with an isolated in-process registry. Tiny real
loopback HTTP bytes enter only the transport's segregated
`test-fixtures-not-scientific-assets` namespaces. The harness never registers
fixtures, patches production YAML, or bypasses license acceptance. It is not
imported or selected by any production option/environment variable.
