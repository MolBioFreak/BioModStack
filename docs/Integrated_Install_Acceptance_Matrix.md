# Integrated installation acceptance matrix

## Scope and meaning

This candidate combines provision CLI `9d011a0f5b55f520d66f272e5f83220de9c5dc88`
and managed release `17a1001d796dfd1d65e90b9707d9035d0d97e64c`, plus integration
and independently reproduced rollback-boundary fixes. It is **not an accepted
clean-machine installer or a verified scientific model release**.

The install acceptance target is: an empty isolated HOME/XDG, the supported setup
interface, operator-selected external storage/settings, pinned runtimes and
separately licensed weights, durable resume and readiness, without pre-existing
SIFs, model caches, home-directory tools or source edits. Model acceptance also
requires every gate in `Model_Configuration_Operator_Control_and_Agent_Parity.md`.
The closed input scope is defined in `Bootstrap_Configuration_Preview.md`; do not
interpret this matrix as expanding that schema or accepting unrelated bridge/MSA
functionality. No completion percentage is meaningful across independent gates.

**COMPLETE** means the specifically named bounded implementation/test gate is
proved. **PARTIAL** means an implementation exists but the full installation
requirement still lacks integration or live evidence. **BLOCKED** means a named
prerequisite or unimplemented boundary prevents acceptance. Fixture evidence is
explicitly not production release or qualification evidence.

## Requirement-by-requirement disposition

| Installation requirement / acceptance gate | Status | Evidence and exact remaining condition |
|---|---|---|
| Supported CLI from empty HOME/XDG: configure → provision-plan → interrupted provision → resume → managed release acceptance | COMPLETE | `tests/test_integrated_install.py::test_configure_provision_resume_release_revalidate` executes real shell/manager CLI, tiny HTTP transport, real layout publication, real release orchestrator and receipt transition in one sandbox. External build/service/readiness observations are fixture adapters. This row accepts control flow, not a live install. |
| Closed versioned operator input and settings preservation | COMPLETE | Configuration-preview/transaction suites validate paths, typed features, CPU/memory, governed ports, unknown-key rejection, byte-pinned documents, and export/profile agreement. Only supported `bms.install.v1` fields are included. |
| External mutable storage, separate development/production state and image/licensed-weight stores | COMPLETE | Configuration/profile tests and provision tests cover external roots, source/cross-lane overlap rejection and image/weight disjointness; integrated test preserves actual configured binding paths. |
| Read-only planning, no implicit runtime activation or license grant | COMPLETE | CLI plan emits `ready: false`; acquisition is explicit and license-bound. Production has no test-mode/manifest URL override. Release CLI authorization gate remains mandatory. |
| Durable per-operation license acceptance | COMPLETE | Plan-bound licenses and timestamp persist; resume cannot add acceptance. Omitted acceptance requires a newly reviewed operation. Malformed acceptance/journal shapes now return structured blockers. |
| Interrupted pinned acquisition and safe resume | COMPLETE | Root transport suites exercise small real HTTP bytes, Range resume, checksums, sizes, redirects, oversize terminal rejection, locking and publication. Integrated test resumes a partial member without refetching the completed image. |
| Multi-model partial outcomes and journal failures | COMPLETE | Successful independent models retain their byte outcome when a later model receipt/journal fails. Aggregate remains blocked, including lock-context exit failures after all bytes materialize. No model is marked qualified. |
| Corruption, symlink/no-clobber and authority drift | COMPLETE | Root/integration tests reject corrupt outputs, journal symlinks, operation overwrite, changed plan/roots/selection and equal-size changed authority bytes. Saved result receipts are discarded and reconstructed by revalidation, not trusted. Malformed authority fields block without downloads. |
| Correct weight-directory handoff rather than opaque transport objects | COMPLETE | Integrated test reads `nested/model.bin` at the actual published weight directory and verifies identical dependency/path bindings after release/revalidation. Full receipts remain recorded. |
| Configuration guards across managed release generation switch | COMPLETE | Plan binds managed generation plus normalized profile digest. An interleaved switch blocks; pre-release plan fails `stale_plan`; new plan with old journal fails `journal_plan_mismatch`. New operation rehashes/reuses the same bindings without another HTTP request. Corrupt release exports block production CLI reads. |
| Recoverable first managed core-release receipt | COMPLETE | API release tests prove immutable originals, public-link preservation, before/after every acceptance fsync position, process-death checkpoints, recovery/readiness rejection and conflicting-file preservation. Services and image observations remain mocked. |
| Post-intent lock cleanup must not destructively roll back accepted candidate | COMPLETE | Independent review repro now raises `ManagedReleaseRecoveryRequired` on configuration unlock error; no stop/restore occurs while journal is committed. Regressions cover unlock, context close and intent-publication error plus unlock error. Pre-intent rollback still passes. |
| Legacy receipt publication must not leave old runtime with new receipt via rollback | COMPLETE | Independent review repro and API test inject known-good JSON failure after env publication. Legacy preserves pre-existing post-publication exception behavior without stop/restore/restart. This is not a new transactional legacy recovery implementation. |
| Python/shell/desktop agree on original and release generations | COMPLETE | API reader/interleaving suites plus all 19 desktop shell-path tests pass on combined sources. No Electron UI launch or package build occurred. |
| Real production acquisition from empty stores without workstation assets | BLOCKED | The actual production registry returns `approved_acquisition_metadata_missing`. Requires reviewed immutable SIFs and complete member-pinned weight manifests, direct delivery URLs, hashes/sizes, source approval and license identities. Fixtures cannot satisfy this. |
| Cache-free, reproducible model runtime distribution | BLOCKED | ESMFold2 runtime definition still needs reviewed locked base/packages/tools and an approved build/provenance artifact. See `ESMFold2_Acquisition_Release_Gap.md`; an existing workstation SIF or upstream source pin is not a release. |
| Offline selected-model receipt → existing validator handoff | COMPLETE | `start_ui.sh verify` revalidates current authority, exact image/member bytes and path/filesystem identities before and after the existing Protenix attestation validator. Actual validator fixture pass/failure, identity drift and post-core-release verification are exercised. A valid test attestation remains validator-blocked for scientific qualification/registration; see `Selected_Model_Qualification_Handoff.md`. |
| Provision receipts consumed by scientific runtime admission/registration | PARTIAL | Offline Protenix attestation-validation handoff now exists. Native scientific result acceptance, complete model-wide qualification and safe registration integration remain required. No new registry or fixture registration is created; core acceptance is separate. |
| Exact model settings: UI/API parity, execution mapping, persistence and result experience | BLOCKED | Canonical policy requires all model-specific gates and live request-to-result evidence for the exact distributed bytes. This installation change neither implements missing model surfaces nor certifies existing ones. |
| Host toolchain, GPU/driver compatibility, disk/mount capacity and real service readiness | PARTIAL | Existing validation authorities are retained, not bypassed in production. This task exercised no Docker/systemd/GPU activation, host-package install or physical storage-failure test. Empty HOME is not an empty OS. |
| No home-directory tools or pre-existing dependencies for full installation | PARTIAL | Model fixture acquisition uses no pre-existing SIF/cache. Test execution still uses the machine's Python/Node/bash and an isolated API environment populated from cached locked packages. A clean-machine prerequisite/bootstrap acceptance is outstanding. |
| Ingress: local-only or selected Tailnet behavior actually enforced | PARTIAL | Configuration records intent only (`applied: false`). Real authentication, ownership, listener/Serve behavior and safe activation need separately authorized acceptance; configure/provision do not enforce ingress. |
| General upgrades, existing-install migration and model lifecycle recovery | BLOCKED | Bounded managed protocol supports first receipt only. General managed profile migration/subsequent releases remain deliberately rejected. No deletion/repair/upgrade bypass is added. |
| Full independent-worker and provider/MSA install acceptance | BLOCKED | Not exercised or certified by these suites. Requires separately scoped runtime provisioning, provider configuration, qualified scientific runs, persistent results and restart/transfer recovery evidence. |
| Full clean-machine installation accepted for production | BLOCKED | Requires every applicable production artifact, live qualification and implementation gate above; local fixture/control-flow success is insufficient. No push, deploy or promotion was performed. |

## Missing production artifacts (not interchangeable with code gaps)

1. Release-owner approved immutable runtime binaries for each selected model,
   with exact SHA-256, byte length, build identity and reproducible provenance.
2. Complete reviewed licensed-weight member layouts: exact relative paths,
   hashes/sizes, dependency closure and immutable revisions. For ESMFold2 this
   includes the exposed Fast/full variants and ESMC dependency, not just one
   checkpoint. Candidate upstream inventories are not acquired/approved bytes.
3. Authorized immutable distribution endpoint and direct final HTTPS URLs
   compatible with the transport policy, approved source authority and genuine
   approval references. No expiring redirect URLs or invented approvals.
4. Pinned applicable runtime/weight redistribution notices and actual reviewed
   license IDs; operator acceptance does not confer redistribution approval.
5. Scientific qualification/admission records bound to exact released runtime,
   member-layout identities, parameter schema/effective settings, supported
   modes and native outputs, plus global result/persistence/UI/API acceptance.
6. Real deployed core image/build provenance, service ownership/readiness and
   clean-machine/independent-worker acceptance evidence. Mock image IDs and
   known-good fixture files are never copied into production.

## Remaining implementation/integration boundaries

- The supported offline Protenix receipt-to-attestation validator handoff is
  implemented; scientific native-result acceptance and model-wide qualification/
  registration are not supplied by the core release receipt transition or verify.
  See `Selected_Model_Qualification_Handoff.md` for exact authorities and blockers.
- Existing-install migration, general managed upgrades and automatic corrupt-state
  reconciliation are intentionally unsupported, not merely waiting for URLs.
- Ingress intent is not enforcement. Full clean-host prerequisite provisioning,
  hardware/capacity validation and deployment acceptance are separate work.
- Model/operator/agent parity and broader provider/worker lifecycle gaps must be
  assessed and closed by their owning integrations, not averaged into this result.

## Follow-on offline model handoff validation

The selected-model handoff delta reproduces **131 root tests passed** (the five
root suites below plus `tests/test_model_qualification_handoff.py`) and **81 API
tests passed** (`test_protenix_runtime_attestation.py`, `test_runtime_acquisition.py`,
`test_runtime_member_layout.py`, `test_bootstrap_cli.py`,
`test_bootstrap_review_regressions.py`, `test_bootstrap_unified.py`). This accepts
offline handoff control flow only; production artifact and scientific gates remain
blocked. The following historical combined-candidate evidence is retained, not
claimed as a full rerun of every subsystem after the handoff delta.

## Validation evidence for this combined candidate

Run owning subsystems separately; root HTTP fixtures do not belong in the API's
network-isolated test collection. API guards were retained. The API invocation
uses `-s` because this repository's namespace re-exec can otherwise lose captured
failure output. Initial broader profile checks exposed two inherited-environment
fixture leaks and an obsolete expected port table; test isolation now clears the
existing result/experiment aliases and expects the already-authoritative mobile
publisher port. No production port or environment precedence was changed.

Final combined validation (all zero failures): **103 root tests passed**, **355
API tests passed**, and **19 desktop shell-path tests passed**. These are focused
owning suites, not a claim that the entire repository or Electron app is green.

```sh
# Repository root
platform/api/.venv/bin/python -m pytest \
  tests/test_integrated_install.py tests/test_provision_cli.py \
  tests/test_pinned_acquisition.py tests/test_member_redirect_transport.py \
  tests/test_shared_runtime_images.py -q

# platform/api (repository's network/subprocess guards active)
uv run --frozen --group dev python -m pytest \
  tests/test_release_acceptance_boundaries.py tests/test_managed_release_configuration.py \
  tests/test_transactional_configuration.py tests/test_configuration_preview.py \
  tests/test_phase5_release_transaction.py tests/test_runtime_acquisition.py \
  tests/test_runtime_member_layout.py tests/test_bootstrap_cli.py \
  tests/test_bootstrap_review_regressions.py tests/test_bootstrap_unified.py \
  tests/test_install_profile.py -q -s

# platform/desktop-electron; disposable loader from this validation session
node --loader /tmp/bms-integrated-ts-loader.mjs --test tests/shellPaths.test.ts
```

Desktop tests
ran directly from TypeScript with a disposable Node 22 `stripTypeScriptTypes`
loader, resolving relative `.js` imports to their `.ts` sources; no emitted build,
Electron binary download or desktop launch was needed. This is runtime reader
coverage, not TypeScript compilation or the complete Electron application suite.

The independent review reproducer `/tmp/bms-release-review.py` was rerun by
substituting only its ROOT string to this isolated worktree in memory. Its output
showed managed `committed`, known-good present, recovery-required exception and no
rollback calls; the legacy publication error likewise produced no rollback calls.
Warnings include pre-existing pytest garbage-directory permissions, an occasional
Python fork/thread warning, and Node experimental loader/type-stripping notices.
