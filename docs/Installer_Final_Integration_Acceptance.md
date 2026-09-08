# Final installer integration acceptance

## Artifact and reconciliation

This is the combined local installer artifact on fetched `origin/test`
`3de5f0ed95c939c92fdffd2f2420ebd7682b77b4`, not another shared dependency snapshot.
The final worktree is `bms-installer-final-20260907`, branch
`integrate/installer-final-20260907`. No push, deployment, host service/configuration,
job, browser, image retirement/cleanup or scientific weight download was performed.

Installer commits were replayed in dependency order:
`6583934 d77df6c 45ac4d2 dd0ec33 964f275 0a36682 d7762cd 1a9251f d227d9f
b24195b 9d011a0 e79da86 283b4d3 565a3b8 12ed2ce 6a5bb69`.
Dependency snapshots `46153cf`, `986cc16`, `607cbeb` were deliberately excluded.
No shared branch was merged. The current publisher, lifecycle, canonical readers,
worker transport and shared specification are byte-for-byte unchanged from origin.
The service renderer's auto-merge was reviewed: its sole final delta replaces
inline Development storage resolution with the installer-owned pure lane resolver;
current shared-image selection and MSA/reference-database behavior remain intact.

The old alignment document describes historical validation only. This document
supersedes its pending-final-integration status and historical test counts.

## Requirement-to-evidence disposition

| Requirement | Final local evidence |
|---|---|
| Discover/plan/preview, closed settings, read-only startup | Bootstrap/configuration API suites pass; no production test-mode switch introduced. |
| Configure and interrupted configuration recovery | Transactional configuration suite, real disposable shell CLI, Python/shell/desktop generation readers pass. |
| Provision-plan, pinned provision, interrupted resume, offline verify | Root suites run real small HTTP transfers, persistent journals, layouts, CLI subprocesses and actual validators. Integrated install fixture chains configure → acquisition interruption → resume → managed first-release acceptance → new operation → verification. Service/build/readiness boundaries are fixture adapters, not host operations. |
| Shared-image alignment | All 15 installer alignment cases and owning lifecycle/reader/transport suites pass: same inode, canonical store, stale operation rejection, lifecycle locking, lane retention, no-clobber and authority drift. No reader/publisher replacement. |
| Managed release and recover-managed | First-install release tests pass, including process death, before/after all 22 acceptance fsync positions, readiness/authorization failure, committed-intent recovery and immutable original preservation. |
| Exact known rollback repros | `/tmp/bms-release-review.py` executed unchanged except ROOT replacement in memory: postcommit unlock raises `ManagedReleaseRecoveryRequired`, journal remains `committed`, known-good exists; neither managed nor legacy publication failure invokes stop-candidate/restore/restart. |
| Current MSA integration preserved | Ten MSA suites plus source-record/dev-sync suites pass on the final checkout, including controller/worker relocation and saved admission. No scientific inference claim. |
| Desktop | Locked dependencies, TypeScript build and complete `pnpm test`: 76 passed. Separate shell-path run: 19 passed (subset, not additional coverage count). No Electron window launched. |
| Source integrity | Initial record test correctly rejected changed `biomodstack_runtime_profile.py`. Supported record builder regenerated the record from an exact frozen Git tree, retaining the current 273-path denominator. Final record tests pass with `implemented_unverified`, acceptance `open`, exposure `fail_closed`. |
| Tests retained | No baseline test function removed (AST comparison of changed Python test files). Existing test modifications only isolate inherited environment and correct already-authoritative port expectations; no skipped tests or weakened image/validator assertions. Installer alignment test/acquisition/provision source matches `6a5bb69` exactly. |

## Reproducible focused acceptance commands

Install API dependencies with `uv sync --frozen --group dev` in `platform/api`.
Run root and API separately; do not weaken API network isolation to mix HTTP fixtures.

From the repository root (**205 passed**):

```sh
platform/api/.venv/bin/python -m pytest \
 tests/test_installer_shared_image_alignment.py tests/test_pinned_acquisition.py \
 tests/test_provision_cli.py tests/test_model_qualification_handoff.py \
 tests/test_integrated_install.py tests/test_member_redirect_transport.py \
 tests/test_runtime_image_lifecycle.py tests/test_runtime_image_references.py \
 tests/test_shared_runtime_images.py tests/test_shared_image_label_selectors.py \
 tests/test_dorado_image_location.py tests/test_esmfold2_upstream_inventory.py \
 -q -s --basetemp=/tmp/bms-installer-final-root
```

From `platform/api` (**528 passed**, 17 fork/thread deprecation warnings):

```sh
uv run --frozen --group dev python -m pytest \
 tests/test_release_acceptance_boundaries.py tests/test_managed_release_configuration.py \
 tests/test_transactional_configuration.py tests/test_configuration_preview.py \
 tests/test_phase5_release_transaction.py tests/test_runtime_acquisition.py \
 tests/test_runtime_member_layout.py tests/test_bootstrap_cli.py \
 tests/test_bootstrap_review_regressions.py tests/test_bootstrap_unified.py \
 tests/test_install_profile.py tests/test_biomodstack_services.py \
 tests/test_protenix_runtime_attestation.py tests/test_cm_managed_image_selectors.py \
 tests/test_frustrampnn_canonical_image.py tests/test_frustrampnn_path_portability.py \
 tests/test_ngs_shared_runtime_images.py tests/test_remote_runtime_images.py \
 tests/test_remote_cache_integration.py tests/test_remote_bundle_runtime_gaps.py \
 -q -s --basetemp=/tmp/bms-installer-final-api
```

Also from `platform/api` (**160 passed**, seven deprecation warnings):

```sh
uv run --frozen --group dev python -m pytest \
 tests/test_ngs_molbio_runtime_record_builder.py tests/test_biomodstack_dev_sync.py \
 tests/test_msa_bundle_integration.py tests/test_msa_controller_handoff.py \
 tests/test_msa_policy_saved_admission.py tests/test_msa_provider_setup.py \
 tests/test_msa_provider_controls.py tests/test_model_msa_handoff.py \
 tests/test_interim_msa_policy.py tests/test_jobs_msa_batch_param_propagation.py \
 tests/test_msa_server.py tests/test_nextflow_msa_batch.py \
 -q -s --basetemp=/tmp/bms-installer-final-msa-seal
```

From `platform/desktop-electron`:

```sh
pnpm install --frozen-lockfile --ignore-scripts
# Complete the locked Electron package's own installation; do not launch it.
node ../../node_modules/.pnpm/electron@35.7.5/node_modules/electron/install.js
pnpm test
```

The first broad desktop attempt with package scripts disabled had 66 passes and
two Electron import errors. Installing the locked binary through its own installer
resolved both; the final full suite has 76 passes, zero failures/skips. Expected
injected error logging in menu tests is not a failing test.

## Source sealing

Only the final checkout's implementation record was updated. The supported
`build_ngs_molbio_runtime_implementation_record.py` ran on a disposable archive of
this checkout with the record excluded. An actual Git commit object bound the
frozen tree; no builder verification was patched or bypassed.

Frozen source commit: `0042205a59b0f18d77a227bb813bf941be557186`.
Frozen source tree: `80e0056282a8d34e60baf99effd0492fa880bfa5`.
The seal does not assert runtime or scientific acceptance. Documentation added
subsequently is outside the unchanged source denominator.

## Remaining gates, not test authorizations

No local focused test remains authorization-blocked. Production is not accepted:
reviewed immutable runtime/member manifests, source and redistribution approval,
actual license acceptance, exact-model scientific qualification and admission,
model/UI/API parity and result evidence remain required. Missing production
acquisition metadata is not repaired by fixture success. General managed upgrades
and existing-install migration remain intentionally unsupported.

Live service/GPU/storage/ingress, independent-worker/provider deployment and
clean-machine acceptance require separately scoped authorization and real evidence.
No host operations were attempted to manufacture that evidence. Parent independent
verification of the final SHA/diff is required before any push or deployment.
