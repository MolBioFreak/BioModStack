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

## Rebind every final source tree

The existing Development admission validator binds the whole committed source
tree with `runtime_implementation_v2.json` removed. Documentation, tests, CI,
ignore-rule changes, and rebases therefore require rebinding too. Do not hand-edit
hashes, alter acceptance fields, or weaken the validator to admit a cleanup.

After final source edits and focused checks, in the isolated worktree:

```bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
cd "$root"
record='platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json'
git rm -- "$record"
git diff --cached --check
git commit -m 'chore(repo): finalize reviewed source before runtime binding'
source_commit="$(git rev-parse HEAD)"
source_tree="$(git rev-parse 'HEAD^{tree}')"
evidence="$(mktemp -d "${TMPDIR:-/tmp}/bms-source-binding.XXXXXXXX")"
mkdir "$evidence/source"
git cat-file commit "$source_commit" > "$evidence/source.commit-object"
git archive --format=tar "$source_commit" | tar -xf - -C "$evidence/source"
(
  cd "$root/platform/api"
  PYTHONDONTWRITEBYTECODE=1 uv run --frozen --group dev python \
    "$evidence/source/scripts/build_ngs_molbio_runtime_implementation_record.py" \
    --successor-source-commit "$source_commit" \
    --successor-source-tree "$source_tree" \
    --successor-commit-object "$evidence/source.commit-object"
)
cp "$evidence/source/$record" "$root/$record"
git add -- "$record"
git diff --cached --check
git commit -m 'build(runtime): bind reviewed source tree'
```

Keep the record-free intermediate commit off shared integration/deployment tips.
Validate the final bound commit using
`validate_candidate_runtime_authority(root, revision)` from
[scripts/biomodstack_dev_sync.py](../scripts/biomodstack_dev_sync.py), and run the
locked runtime-record tests and applicable existing review lane. Invoke only
that read-only validator, not the synchronizer's service/deployment entrypoint.
Any further source change requires a new binding. Preserve external build/test
evidence until review is complete; do not commit it as local runtime output.

The hygiene workflow is read-only. Making its check required is a separate
repository-administration decision; a workflow file alone does not enforce
branch protection.
