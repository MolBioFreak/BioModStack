# Managed configuration → first release integration

Status: **implemented for one first-install release receipt transition**. This
connects configured installations to the existing production release orchestrator;
it is not scientific acquisition/qualification or proof of a running installation.
No services, jobs, browsers, ingress or host configuration were changed during
implementation. General managed profile migration remains deliberately blocked.

## Supported entrypoints

After `start_ui.sh configure --document <install.json> --json`, the existing
`scripts/biomodstack_release.py deploy --allow-first-install
--confirm-runtime-activation` path can consume the committed managed configuration.
The runtime activation authorization is still mandatory. Construction and
configuration are not readiness. Existing build, provenance, running-image,
readiness, listener and ownership checks still precede acceptance.

For an interrupted recorded acceptance, use:

```
python3 scripts/biomodstack_release.py recover-managed \
  --release-id <recorded-build-id> --confirm-runtime-activation
```

Recovery revalidates the candidate and running image identities before completing
metadata. It does not rebuild, restart, mutate settings, or rerun empty-state
checks. A stopped or unhealthy candidate is not accepted by this command. The
low-level `recover_managed_release()` API completes metadata only; it is not a
runtime-readiness assertion. Subsequent releases/profile migrations remain blocked.
A repeated deploy with a recorded operation directs the operator to recovery,
rather than rebuilding over an interrupted candidate.

## Immutable generation protocol

- `managed_release_base(project_root)` verifies the committed install journal,
  canonical HOME/XDG/source context, public links and active bytes. Override env
  paths and state destinations are rejected. The backend retains the public env
  symlink path, not its resolved generation write target.
- Receipt keys are exactly `BMS_BUILD_SHA`, `BMS_BUILD_ID`, `BMS_BUILD_TIME`,
  `BMS_MANAGED_API_IMAGE_ID`, `BMS_MANAGED_WEB_IMAGE_ID`. BuildIdentity validation,
  shell-safe literal export validation, and immutable SHA256 image IDs are required.
- `commit_managed_release()` holds the configuration lock, compares the expected
  operation/generation/hashes, and persists `release.json` acceptance intent before
  staging. It includes the intended known-good payload and its canonical state
  destination so recovery does not reconstruct acceptance from process environment.
- A new `release-<sha256(release-id)>` directory contains the original profile and
  compatibility export byte-for-byte, the canonical original core export plus the
  five sorted receipt fields, and `manifest.json` (`bms.configuration-release.v1`).
  The manifest binds original operation/hashes, release ID, receipt and new hashes.
- Original `generation/*`, original journal and public destination links are never
  rewritten. New release-file publication uses Linux `renameat2(RENAME_NOREPLACE)`
  so a crash cannot leave an extra temporary hardlink; unsupported filesystems
  fail closed. Files and directories are synced before atomically replacing the
  single `active` symlink. Stale/corrupt/foreign files are rejected, not repaired.
- Known-good publication on the state filesystem follows verified activation.
  Its release ID/generation ID are bound to the manifest. Files and directory
  entries are synced before recording committed acknowledgement. Repeated recovery
  verifies the same payload and is idempotent.
- Python, shell and desktop readers accept original and release generations,
  validate the applicable hashes and recheck the active identity around reads.
  A generation change fails the read instead of returning mixed data or falling
  back to a legacy profile. General-purpose managed-write guards remain in place.
- The release execution lock covers the whole managed service transaction and
  recovery; the configuration lock separately serializes receipt writers. The
  backend uses configured exports for subprocesses and configured validation ports,
  rejecting conflicting inherited base export settings.

## Failure policy

Managed `commit_known_good` runs inside the release transaction's exception
boundary, including configuration-lock context exit. Unlock/close errors after
acceptance intent return recovery-required even if acceptance is already committed.
Legacy receipt publication retains its prior post-validation exception semantics:
its nontransactional env publication must not trigger runtime rollback without
restoring that env as well. Failures before managed acceptance intent use the
existing rollback path (a first install
remains stopped with no known-good runtime to restart). After intent publication,
errors return `managed_release_recovery_required`, not a misleading rollback or
accepted status. This preserves the validated candidate for explicit recovery.
Recovery verifies both generations and the intended payload; existing runtime
storage is allowed but its configured identity cannot change. Corrupt metadata or
a conflicting known-good file fails closed and requires operator investigation.

## Disposable validation and limitations

Validation commands (run locally, without runtime authorization bypass):

```
cd platform/api
uv run --frozen --group dev python -m pytest \
  tests/test_managed_release_configuration.py \
  tests/test_transactional_configuration.py tests/test_configuration_preview.py \
  tests/test_phase5_release_transaction.py -q -s

cd ../desktop-electron
pnpm run build && node --test dist/tests/shellPaths.test.js
```

Tests exercise the real disposable configure CLI and real release orchestrator,
receipt writer and recovery. Service/build boundaries use fixture adapters; a test
also exercises actual unit rendering/publication and start orchestration with the
external service boundary mocked. Coverage includes every acceptance checkpoint,
before/after all 22 acceptance fsync positions, process death at each checkpoint,
repeated recovery, original file byte/inode and public-link preservation, runtime
state preservation, stale/context/override rejection, corrupt staged files,
manifest corruption, writer locking, foreign symlinks and activation-temp conflicts.
Python/shell/desktop interleaving tests reject a switch during reading. Recovery
readiness failure and the CLI authorization gate are tested independently.

The release regression contained a stale 8000 proxy assertion although production
code and port authority already use 18000; that assertion is corrected to 18000.
The full Electron suite additionally imports its binary in unrelated menu/tray
modules. Installing locked dependencies with `--ignore-scripts` leaves that binary
uninstalled: those two tests cannot run in this setup. TypeScript compilation and
the focused shell-path suite run without launching Electron. API pytest reports
pre-existing garbage-directory cleanup permission warnings from other runs.

Final local results: **274 API tests passed** (2 unrelated cleanup warnings),
**TypeScript compilation passed**, and **19 desktop shell-path tests passed**.
The broader Electron run had 64 passes and two import failures caused by the
uninstalled Electron binary; it is not claimed as a passing full desktop suite.

These tests do not certify real Docker/systemd operation, physical power-loss
behavior of storage hardware, scientific models, acquisition, or a full live first
installation. Runtime qualification remains a separate authorized task. This is a
first-receipt-only transition, not a general upgrade/rollback migration framework.
