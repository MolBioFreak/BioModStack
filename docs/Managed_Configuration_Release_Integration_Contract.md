# Managed configuration → first release: remaining integration contract

Status: **not implemented; first-install release remains blocked**. Configuration
is recorded, not a usable released installation. The acquisition workstream must
not advertise ready/setup-complete until this contract is implemented and tested.

## Concrete obstruction

- `scripts/biomodstack_release.py:ProductionReleaseBackend.__init__` calls
  `reject_managed_write()` even with `allow_first_install=True`.
- `commit_known_good()` appends/replaces five receipt keys in `runtime_env_file`
  using `_atomic_text_write()`, which independently rejects managed writes.
  The constructor currently resolves the publication symlink to its committed
  generation file. Removing either guard is not an integration implementation.
- `biomodstack_configuration._verify()` compares all three files to the original
  journal and recomputes env exports from the profile. Appending release metadata
  breaks that invariant even if journal hashes were updated. Desktop independently
  checks those original hashes. Journal and active pointer cannot be replaced as
  two writes and called an atomic generation switch.
- `run_release_transaction()` invokes `commit_known_good()` **outside** its
  rollback try/except. A commit interruption must be recoverable without rebuilding
  or assuming the running candidate was stopped. `known-good.json` is on the state
  filesystem, so its publication and configuration activation are not one rename.

## Minimal supported scope (separate integration worker)

Implement a release-receipt-only immutable generation transition, not a general
profile migration. Keep existing arbitrary managed-write guards in place.

Suggested public interfaces in `biomodstack_configuration.py`:

```
managed_release_base(project_root) -> {operation_id, generation_id, hashes}
commit_managed_release(project_root, expected_base, release_id, receipt) -> result
recover_managed_release(project_root, release_id) -> result
```

1. Read-only base discovery verifies an activated, committed first-install
   configuration and canonical HOME/XDG/source context. The release backend may
   construct in this case only through the supported managed path; an incomplete
   configuration, override env path, stale base, or foreign source stays blocked.
   Do not cache the `.resolve()` result of `core-runtime.env` as a write target.
2. Receipt input has an exact closed key set: `BMS_BUILD_SHA`, `BMS_BUILD_ID`,
   `BMS_BUILD_TIME`, `BMS_MANAGED_API_IMAGE_ID`, `BMS_MANAGED_WEB_IMAGE_ID`.
   Reuse release `BuildIdentity` validation; require immutable `sha256:` image IDs.
   No profile, runtime/storage paths, ports, ingress, scientific parameters, shell
   fragments, or arbitrary extra env keys are accepted.
3. Under `configuration_lock()`, compare operation/generation/hash expectations
   and re-verify the old generation. Persist an operation-bound recovery record
   with old/new identities and receipt. Stage a new, uniquely named directory
   containing all three immutable files **and its own manifest**. Profile and
   compatibility export remain byte-identical; core export is the canonical
   renderer output plus the validated five-key receipt. Validate that exact rule.
4. Extend Python and desktop readers to support the versioned active-generation
   manifest before enabling this writer. Keep the original v1 installation journal
   as immutable audit input. A new active target must bind its own manifest/hashes
   and the original operation identity; do not change the original committed
   `generation/*` files or separately mutate original journal hashes. Continue
   accepting the initial v1 `active -> generation` layout. Reject unknown targets,
   symlinks escaping the transaction root, and manifest/file mismatches.
5. fsync files and directories before switching the single `active` symlink by
   atomic rename. All public destination links continue targeting `active/<key>`.
   Readers capture the active identity before reading and recheck it on every
   exit; either return a verified single generation or fail/retry, never a mixture
   or legacy fallback. Existing shell/Python guards must exercise this same rule.
6. A post-activation crash must recover idempotently by verifying the intended
   active generation and completing the release receipt, not by restaging from
   current environment or rerunning first-install empty-state checks. Runtime
   databases now legitimately exist; the allowlisted receipt transition may not
   adopt or change their configured identities.
7. Integrate `ProductionReleaseBackend.commit_known_good()` with this API instead
   of `_atomic_text_write()` only for managed configurations. Bind
   `known-good.json` to release ID and generation ID. Persist enough information
   before activation to recover the cross-filesystem known-good publication; only
   report accepted once both are durable and verified. A failure between activation
   and known-good publication must produce explicit recovery-required status.
   Unmanaged release behavior remains unchanged. Keep readiness/identity/image/
   ownership validation before acceptance; construction is not readiness.

## Required disposable acceptance/fault tests

- Real `start_ui.sh configure --json` then construct the backend with
  `allow_first_install=True`; exercise existing release orchestration with fixture
  subprocess/service adapters (never host services). Prove accepted generation,
  Python/env/desktop parity, receipt identities, and known-good binding.
- Interrupt before/after recovery-record fsync, each staged file/manifest fsync,
  directory fsync, active rename, activation fsync, known-good publication, and
  final recovery acknowledgement. Recover with the same release ID twice.
- Interleave Python and desktop readers at each switch boundary, including missing
  and invalid legacy-profile returns. Assert no unrelated state or mixed generation.
- Two release writers, stale expected base, changed HOME/XDG/source, malformed or
  extra receipt keys, corrupt staged bytes/manifest, dangling/foreign links,
  pre-existing temp paths, and fsync errors all fail closed without clobbering.
- Snapshot original generation bytes/inodes and all public links; demonstrate
  original files and unrelated state survive success, every fault, and recovery.
- Existing first-install failure rollback remains stopped/not ready; failures
  after acceptance activation require explicit recoverable receipt completion.

The current regression `test_first_install_release_remains_explicitly_blocked`
records this unresolved gate. Replace it with the positive integration test only
when the supported transition and the above crash semantics actually exist.
