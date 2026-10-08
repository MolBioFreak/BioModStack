# Repository maintenance

Use an isolated worktree based on current `origin/test`. Never edit the
managed Development checkout, force-push a shared branch, or promote to `main`
as part of housekeeping. Fetch/reconcile again before integration and verify
the remote SHA afterward. A configured Development synchronizer may consume
`test`; a pushed revision is not evidence of a deployed, healthy service.

## What belongs in Git

Keep supported BMS source, configuration templates, schemas, migrations,
locked dependency manifests, reproducible runtime definitions, current
operational/scientific contracts, and controlled tests and fixtures. Active
plans and documents named by source-authority manifests are not disposable
because they are dated. Retire obsolete material only after checking runtime,
test, build, migration/recovery, and explicit release dependencies.

Keep credentials, private environment files, installed dependencies, caches,
local databases, model weights, logs, generated runtime outputs, and scratch
scripts out of Git. A directory named `build` or file named `generated` is not
by itself disposable: build-tool source and reproducible reference assets may
be required. Do not mask first-party Cordova plugin source with a recursive
`www/` exclusion; only the wrapper-root generated directory is excluded.

## Required repository checks

Stage only the intended changes, then run from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_repository_*.py' -v
python3 scripts/check_repository_hygiene.py
git diff --cached --check
```

The checker examines indexed blobs and indexed `.gitignore` bytes. Unstaged
changes, global ignore files, and `.git/info/exclude` cannot make its result
clean. Exit code 0 means the bounded scan passed, 1 reports hygiene findings,
and 2 means the scan could not complete. None proves all source is reachable,
absence of all secrets, binary/archive safety, or cleanliness of Git history.
The navigation tests check local file targets in entry-point READMEs and
canonical top-level documentation, not external URLs or Markdown anchors.

Run affected subsystem tests as well. For Cordova wrapper source:

```bash
(cd platform/mobile-cordova && node --test ./tests/*.test.mjs)
```

For API work use `uv run --frozen --group dev python -m pytest` from
`platform/api`, preserving its route-free guards. Native-tool skips must be
reported as not run; repository checks do not qualify scientific execution.

## Exact-byte exceptions

[config/repository-hygiene.json](../config/repository-hygiene.json) records the
reviewed APK and scientific-fixture exceptions by path, SHA-256, and reason.
A changed hash, missing file, duplicate entry, or unnecessary exception fails
validation. Private environment files, credential containers, and installed
dependency/cache paths cannot be approved through this mechanism. Selected
credential-pattern checks still apply to exception bytes.

The ordinary review threshold is 5 MiB. Individual objects over 64 MiB or a
unique-object total over 512 MiB exceed this checker's scan budget; an exception
does not disable those limits. These are repository policy, not hosting limits.

Both Android APKs remain explicitly approved by
[the Android artifact contract](../artifacts/android/README.md). Do not remove
the active internal-updater payload. Retiring the historical beta requires a
reviewed retention/distribution decision; remove its exception and update the
artifact documentation in the same change. No blanket archive/weight suffix
purge is safe: some scientific fixtures deliberately use those formats.

## Deployed source metadata

Runtime consumers use the deployed build revision and existing `SourceIdentity`
Git commit/tree metadata, not a generated whole-source implementation record.
Dirty tracked files do not prevent metadata reads. Packaged builds without Git
retain `BMS_BUILD_SHA`; missing revision/tree metadata is reported as `unknown`,
not fabricated and not a new execution condition. Remote bundle admission keeps
its existing clean-source checks.

The former `/api/operations/ngs-molbio/runtime-implementation` endpoint and record
builder are retired. Operator metadata is available at
`/api/operations/ngs-molbio/source-identity` (`source_revision`, `source_tree`).
Historical receipt digests remain readable and are checked against their retained
receipt peers, never against a newly synthesized implementation record. New package
evidence need not contain `runtime_implementation_sha256`. Backup/export verification
and staging recovery verify their retained creation receipts and scientific bytes;
a later deployment does not invalidate the creation revision.

The managed synchronizer preserves clean-tree, fast-forward, pause, active-work,
deployed-identity and rollback checks. No freeze/bind commits are required.

The hygiene workflow is read-only. Making its check required is a separate
repository-administration decision; a workflow file alone does not enforce
branch protection.
