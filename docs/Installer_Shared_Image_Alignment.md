# Installer alignment with the shared scientific image authority

## Pinned dependency and integration boundary

Installer base: `12ed2cec4cf1831ebc348d2797509bc7aa2462e3`.
Common base: `9c06faa484cd2b7795345852a81ae8a80761403e`.
Inspected shared-image snapshot: `bde6da24808ece5710eac80101ef2be5f573e873`
(clean when first inspected). Work was performed only in the separate
`fix/installer-shared-alignment-20260907` worktree. Shared/image-owned worktrees
were read-only throughout.

Local dependency commits `46153cf`, `986cc16`, and `607cbeb` bring the relevant
shared publisher, lifecycle, lane renderer, local canonical readers, worker image
transport, tests and specification into the installer base. These are dependency
ports, not a second image implementation. Worker transport was ported from shared
commits `ad11ae6`, `3e6018e`, and `3efacb7`; unrelated controller-MSA changes were
not imported through patch context. No blanket source overwrite or branch merge
was used. The publisher, lifecycle, lane publisher and scientific reader authority
are unchanged from the pinned snapshot.

The subsequent installer fix is the installer-owned delta to integrate after
both workstreams are reconciled. Do not overwrite the other worker's newer
source with these dependency commits. In particular preserve their selected-lane
references, retained releases, explicit selectors, worker transport and source
identity seals. Our existing renderer's installation-owned resource changes and
their image changes coexist; no running unit was changed.

Recheck found shared HEAD advanced to
`fa008d25c9124856c1475a897fdf084ca5b35ad9`, clean. Changes since the inspected
snapshot are CM routing regression expectations and the NGS runtime implementation
seal (`edfbd93`, `fa008d2`); publisher, lifecycle, renderer and specification are
unchanged. Those newer tests/seals belong to their workstream and were not rewritten
here. Final combined-source integration must retain them and run the existing
source/runtime identity validation, rather than copying this partial dependency
snapshot over the complete shared branch. This is not a deployable/requalified
combined release or a claim that all unrelated changes on either branch were merged.

## Installer behavior

- `resolve_runtime_paths` exposes `runtime_image_store`: explicit
  `BMS_RUNTIME_IMAGE_STORE`, otherwise the resolved profile/environment
  `container_dir` plus `.image-store`. No new profile field, registry or cache
  implementation is added. The explicit store path is not symlink-resolved before
  the no-follow authority sees it. Invalid relative/traversing stores fail planning.
- Provision plans bind `store_roots.runtime_image_store`, licensed `weights_root`,
  configuration generation, normalized profile digest and exact registry plans.
  Provision, resume and offline verification all use that same image root.
- Old `container_dir`-object plans/journals, changed overrides and copied/old
  acquisition checkpoints fail clearly. Nothing silently migrates or reconciles
  these records; operators must review a new plan/operation and separately
  reconcile any stale checkpoints. This change does not authorize moving images.
- Acquisition uses the shared lifecycle-fenced publisher. Existing-object reuse
  verifies under the same lifecycle lock and compares previously recorded object
  identity. Missing or replaced completed objects cannot trigger redownload or
  repair. Same-digest model names reuse the same inode.
- After durable verification, only the acquisition's own authenticated partial
  download is removed. Unknown staging bytes fail closed. This is private download
  staging completion, not object/alias/source cleanup, retirement or collection.
- Licensed weight objects and immutable layouts remain separate. No task-local
  SIF materialization or retired semantic-name copy is created by provisioning.
- Acquisition/verification never publish lane references, create leases, change
  retention, select releases, retire/quarantine images, activate/register models,
  start jobs or manage services. Explicit publication in the tests is performed
  separately through the lifecycle owner's existing publisher.

A receipt is an observation, **not a lifetime lease**. There remains a gap between
acquisition and later activation/admission: the caller must independently reverify
and use the release/lifecycle owner's supported selection/retention policy. No
unattended collector, automatic lease or new activation policy is invented here.

The existing Protenix handoff remains strict. Its path/inode/digest comparisons
accept default, profile and explicit canonical placement because the acquisition
receipt and execution evidence name the same canonical object. No scientific
validator was relaxed. Successful attestation validation still yields
`validator-blocked`, not scientific qualification or registration.

## Executed evidence

All tests below used synthetic small bytes and isolated temporary state. No image
or model downloads, real inference, jobs, deployment or service operations occurred.
Run from `platform/api` with `uv run --frozen --group dev python -m pytest`:

1. **197 passed**: `../../tests/test_installer_shared_image_alignment.py`
   `../../tests/test_pinned_acquisition.py` `../../tests/test_provision_cli.py`
   `../../tests/test_model_qualification_handoff.py` `../../tests/test_integrated_install.py`
   `../../tests/test_member_redirect_transport.py` `../../tests/test_runtime_image_lifecycle.py`
   `../../tests/test_runtime_image_references.py` `../../tests/test_shared_runtime_images.py`
   `../../tests/test_shared_image_label_selectors.py` `../../tests/test_dorado_image_location.py`
   with `-q -s --basetemp=/tmp/bms-installer-alignment-final-root-tests`.
2. **103 passed**: `tests/test_runtime_acquisition.py` `tests/test_runtime_member_layout.py`
   `tests/test_install_profile.py` `tests/test_cm_managed_image_selectors.py`
   `tests/test_frustrampnn_canonical_image.py` `tests/test_frustrampnn_path_portability.py`
   `tests/test_ngs_shared_runtime_images.py` `tests/test_remote_runtime_images.py`
   `tests/test_remote_cache_integration.py` `tests/test_remote_bundle_runtime_gaps.py`
   with `-q -s --basetemp=/tmp/bms-installer-alignment-final-api-tests`.
   Four existing multiprocessing/fork deprecation warnings; no failures.
3. **316 passed**: `tests/test_biomodstack_services.py`
   `tests/test_managed_release_configuration.py` `tests/test_configuration_preview.py`
   `tests/test_bootstrap_unified.py` `tests/test_transactional_configuration.py`
   with `-q -s --basetemp=/tmp/bms-installer-alignment-config-tests-2`.
4. **15 passed**, final focused rerun of
   `../../tests/test_installer_shared_image_alignment.py` with
   `-q -s --basetemp=/tmp/bms-installer-alignment-final-recheck`, including the
   strengthened assertion that supplying a newly reviewed plan still cannot
   migrate an old operation journal after an explicit store change.

The new integration module exercises actual loopback acquisition, real filesystem
publication/verifiers, Protenix receipt readers/attestation validator, FrustraMPNN
no-follow descriptor pinning, Dorado canonical selection, required Development
renderer projection, retained Development/Production releases, same-inode reuse
with no duplicate image object/full download staging, lifecycle exclusion in a
second process, stale stores/checkpoints, and no-clobber failures. References are
byte-for-byte unchanged across acquisition/resume/verification; image identity is
unchanged across lane publications and reads. Existing handoff tests reject actual
path, inode, digest, member and evidence drift.

During validation, mixed API/root collection inherited the API suite's socket ban
and blocked the loopback fixtures. Separate owning-subsystem runs above passed;
no network guard was disabled. A stale unit-test plan fixture was updated to the
new root key. A missing worker dependency was resolved by porting the actual
shared image transport, not by weakening its canonical-reader test. Renderer tests
retain the shared worker's portable telemetry assertion. `git diff --check` passed.
