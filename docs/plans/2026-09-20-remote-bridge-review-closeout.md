# Remote bridge: September 20 worker-review corrections

This supersedes the adoption status in the September 17 plan, not the original
worker's evidence. Scope: the remote bridge in BioModStack, not the BioXP robot
schema/lineage workstream. Production `main` is not part of this promotion.

## Original evidence and blockers

The Hermes review of `f953aeec5862f52409a0029701d236be1970c3e6` reported 64/64 new
cases and a separate 208/208 provisioning/launch selection passing in the locked
API environment. A separate 124-case selection had two failures: the obsolete
cold-total budget assertion and the stale runtime-source record. Those results
are historical worker evidence, not results claimed for the corrected tip.

## R1: exact source binding is required, not deferred

Finish all source, test, workflow, and documentation changes before freezing.
Create a commit which removes only the inherited generated implementation record;
export that record-free commit with `git archive` and its raw commit object with
`git cat-file commit`. Run the existing builder against the archive using the
real freeze SHA/tree/object, then commit only the generated JSON as a companion.
Do not edit its hashes, weaken the denominator, or patch out source verification.

The record deliberately continues to say `implemented_unverified`, acceptance
`open`, and `tests_run: 0`: it binds source bytes, not live scientific acceptance.
A corrected branch is eligible for Development integration only after the checked-in
record test **and** `validate_candidate_runtime_authority` accept the final bound
commit. A successful record-generation-only CI job is not this acceptance gate.

## R2: preserve cold safety and prove warm accounting through admit()

The existing managed-runtime safety test now calls the real helper's `admit()`
for three filesystem states: cold (upload + cache + destination), cache-only
(one destination copy), and installed (zero additional payload bytes). All three
retain metadata allowance, 1 GiB headroom, and `reservation == false`. The active
release marker must remain unchanged. No tests are skipped to hide the old failure.

## R3: accept verification cost explicitly

Keep independent verification of installed destinations and distinct cache objects.
One does not prove the other exists or is intact. The worker measured 0.007 s vs
0.276 s on a synthetic 300 x 1 MiB already-installed release. Its estimate of about
20 GB of hashing for the larger Fold-CP selection was an extrapolation, not a live
latency measurement. The install path can hash destinations again.

For live acceptance, record exact revision, selected manifest, destination/object
byte totals, storage type, cold/warm admission duration, and subsequent install
verification duration. Establish whether the bounded operation timeouts accommodate
that measured cost. Reusing a verified receipt safely is a separate optimization;
this correction does not credit filenames or cached success flags.

## R4: helper-owned declared request codes, controller-owned advice

The transport's recognized request/document codes are derived from the union of
both producing helpers' `REQUEST_CODES`. Operator advice can refine a declaration
but cannot introduce an undeclared accepted code. A newly declared code has its
fixed helper message as a safe fallback, and the parity test requires explicit
reviewed advice for every declaration before integration. Shared declarations must
agree. Existing bounded-envelope parsing, authentication precedence, unknown-error
handling, and cancellation ownership stay intact.

This closes catalog drift for the declared request/document protocol. It does not
claim that all literal ValueError strings or HF transfer failures now have typed
operator diagnostics. Expanding those protocols remains separate work: declare and
review codes at the producer first, then test their safe projection. Never expose
arbitrary exception strings, paths, URLs, or tokens as a shortcut.

## R5: exercise the controller, not a replica

`test_remote_inventory_supersession.py` executes the real
`PreloadController.refresh_inventory()` against asynchronous SQLite using both the
API's canonical JSON serializer and the default serializer. Only remote readback
is replaced by an injected failure. A newer observation, operation, attachment, or
endpoint must produce the superseded error and preserve the replacement. An
unchanged predecessor, expired provider inventory, a newly acquired lease, or an
unrelated probing worker must still invalidate the failed predecessor.

This is SQLite qualification; no PostgreSQL support is newly asserted. A
cross-database implementation would also need to replace the pre-existing
SQLite-specific JSON operations, not merely this observation predicate.

## R6: reproducible review gates and evidence

The review-only GitHub workflow runs the locked API dependencies on a complete
checkout. It uses a real route-free network namespace on the hosted runner,
drops back to the runner UID/GID before testing, and preserves the repository's
pytest network/subprocess policy. It has no provider secrets, deployment step,
or permission to update repository refs in its normal review form.

From a separate checkout/worktree, run from `platform/api`:

```bash
uv run --frozen --group dev python -m pytest -q \
  tests/test_remote_provision_isolation.py \
  tests/test_remote_inventory_supersession.py \
  tests/test_remote_incremental_admission.py \
  tests/test_remote_helper_diagnostics.py \
  tests/test_managed_runtime_safety.py \
  tests/test_ngs_molbio_runtime_record_builder.py \
  tests/test_remote_managed_inventory.py \
  tests/test_remote_preloading.py \
  tests/test_vast_auto_setup.py \
  tests/test_remote_cache_integration.py \
  tests/test_remote_runtime_images.py \
  tests/test_remote_runner_integrity.py \
  tests/test_artifact_cache.py \
  tests/test_cache_batches.py \
  tests/test_multiworker_scheduling.py \
  tests/test_multiworker_migration.py \
  tests/test_managed_ssh_transport.py \
  tests/test_vast_ssh_endpoint_selection.py \
  tests/test_hf_worker_acquisition.py \
  tests/test_remote_telemetry.py \
  --junitxml=/tmp/bms-remote-bridge-review.xml
```

Then validate the exact committed candidate with the updater's
`validate_candidate_runtime_authority(root, revision)` from
`scripts/biomodstack_dev_sync.py`. Validation does not call the updater's deployment
entrypoint. The workflow stores the revision, tree, raw commit object, JUnit case
identities, and updater result in its revision-named artifact. Record failures as
failures; an artifact's existence alone is not a passed gate.

## Integration and remaining acceptance

Preserve the original four reviewed commits and append these corrections. Fetch
and reconcile `origin/test` immediately before promotion, rerun affected gates on
the actual candidate, and use a fast-forward update only. Do not promote a
record-free intermediate commit. A concurrently advanced branch requires renewed
reconciliation and a fresh exact-source binding, not a force push.

A green software candidate does not establish live Vast attachment, telemetry
cadence, interrupted-bootstrap recovery, scientific execution, frontend behavior,
or native result round trips. These remain supervised/manual acceptance tasks.
The earlier telemetry-session, attachment-ownership, and inventory-pagination
work packages remain separate; this correction does not pretend to implement them.

## Expanded hosted-runner integration checks

The combined candidate c15ba525 retained corrected review 03c32b0 and
Development 2ba46790 and passed the updater source-authority validator.
The expanded hosted runner identified two additional test-fixture defects:
an activation double lacked the typed critical/backend qualification,
and the runtime packaging fixture copied all of /usr when CPython was
system-installed. Neither was repaired by relaxing production checks.
The fixture now returns ManagedRelease, asserts the selected backend,
and retains exact other-worker/Job preservation checks. Relocation tests
copy the real interpreter, full stdlib/extensions and shared libpython,
excluding unrelated host-prefix data; new tests execute real isolated
stdlib imports and verify that unrelated prefix members are not copied.

Include test_python_runtime_fixture.py, test_remote_bundle_runtime_gaps.py,
test_remote_bundle_container_gaps.py and test_remote_support_packaging.py
with the previous 20-module selection. Capture exact SHA, full command,
exit status and JUnit cases. The prior 64/208 counts remain historical,
not evidence of this combined candidate. Hosted CI still cannot certify
live Vast behavior, scientific output or deployed service ownership.
The independent verification cost decision remains unchanged: measure
cold/warm admission and subsequent install latency on the actual worker;
no existence-only credit, stale receipt reuse or skip was introduced.
