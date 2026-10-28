# AtomWorks Studio S03 patch series

Applies only to RosettaCommons/atomworks-studio release **0.0.5**, commit
`04fe5fbd03fea8b7d49a767156d7d33bd10e5d13`. Upstream remains BSD-3-Clause;
these are bounded source changes, not a vendored checkout or generated bundle.
S02 owns Python packaging, notices, service hosting, authentication and worker policy.
No model execution, live BMS store modification, deployment or rental is performed here.

## Changes

- Chain-role assignment uses the document owner: **backbone is exactly N/CA/C/O**,
  atom scoped; clears sidechain coordinate masks rather than residue-expanding them.
  Existing target-discard confirmation, revision conflicts, batch Undo and unrelated
  internal-coordinate targets remain native. Sequence conditioning remains residue scoped.
- Scalar/text controls become editable again after Enable is rechecked.
- `renderFrame` has explicit display-only picking ownership until `showDocument` /
  `openSession`. Pending selection resolution is invalidated. Mol* accepts picks only
  from its current document structure root; overlays cannot reuse document indices.
- Store calls pin sessions before queueing and through mutation/persistence completion.
  TTL/capacity pruning skips active and memory-committed/disk-failed sessions. If all
  candidates are protected, eviction is deferred rather than introducing an admission
  refusal; ordinary idle capacity is reclaimed by subsequent pruning.
- Failed snapshot writes preserve the committed document/Undo and return explicit
  `persistence: {state: "memory_only", revision, message}`. The browser retains a
  dedicated warning and Retry snapshot action. The owned retry endpoint recovers the
  snapshot. Successful subsequent writes clear the warning. Restart still resets
  revision and Undo; this store is not durable project custody.
- Sequence segment buttons open typed min/max and explicit item/side anchor controls.
  `/segments/edit` changes bounds and/or order in one revision-bound Undo transaction.
  AtomArrayPlus permutation preserves bonds, pair annotations and targets. Structural
  residue numbering follows the chosen order for parseable CIF; imported author
  numbering remains retained separately. Realized runs retain their realization until
  the existing Collapse action is used. There is no new segment/scientific default.
- Applied-condition details expose exact document atom lists and stored values,
  exact selection receiving, and per-atom target inspection using the existing endpoint.
- Embed event example uses `session_id`. Editor-QoL tests inspect preserved raw fields
  via export, not removed DOM rows, and distinguish positioned overlay atoms from
  unpositioned document atoms.

## Host/build integration

Use a **disposable Git checkout**, not a running service or model environment:

```sh
git clone https://github.com/RosettaCommons/atomworks-studio.git "$STUDIO_SOURCE"
git -C "$STUDIO_SOURCE" checkout --detach 04fe5fbd03fea8b7d49a767156d7d33bd10e5d13
python scripts/apply_atomworks_studio_patches.py --source "$STUDIO_SOURCE"
# With the upstream Rust/wasm-pack/Node/pnpm toolchain provisioned:
python scripts/apply_atomworks_studio_patches.py --source "$STUDIO_SOURCE" --verify-only --build
```

The applicator checks exact HEAD, patch SHA-256, before/after file hashes, locked
build inputs, and unrelated tracked/untracked source changes. It checks the complete
patch before writing and is idempotent. `--verify-only` performs no patch write.
`--build` runs actual `pnpm install --frozen-lockfile` and upstream `pnpm build`
(WASM, TypeScript, Vite), then prints a JSON receipt with emitted asset SHA-256 values.
Build output is **`$STUDIO_SOURCE/packages/web/dist`**; the host child should consume
this rebuilt output using its normal packaging owner, never the unpatched checked-in
`packages/api/annotation_studio/web` release assets. No Python install, source fetch,
service start, or wheel build is hidden in the helper.

Manifest hashes bind the patch series and original build inputs; toolchain/environment
provisioning remains S02-owned. Source checks are packaging verification, not new
scientific execution gates. Disable/isolate the upstream service worker at the host
boundary as specified by S02; this patch does not change its policy.

## Reproduce and verify

From the patched upstream checkout, with an isolated supported Python 3.12 environment
containing AtomWorks 3.0.4, CPU Torch and upstream test dependencies:

```sh
# This reads the original release Git objects and proves the reviewed frontend bugs.
# It is defect reproduction, NOT passing product acceptance.
node packages/web/tests/bms-s03-before.mjs
# Corrected-controller/callback tests use explicit DOM/viewer doubles, NOT WebGL.
node packages/web/tests/bms-s03-regressions.mjs
python -m pytest tests/test_bms_s03.py tests/test_api.py \
  tests/test_condition_authoring.py tests/test_save_and_confirmation.py \
  tests/test_close_session.py tests/test_editing_transactions.py \
  tests/test_rfd4_internal_coords.py -q -rs
(cd packages/web && pnpm test:unit)
```

The Python suite includes an explicitly labeled reproduction of the original
residue-wide backbone recipe as well as corrected-mask acceptance; do not count
that reproduction as repaired product acceptance. Initial unpatched store probes
actually evicted active sessions in both memory and disk modes and raised OSError
after a committed edit when persistence failed. Missing segment/role methods were
feature gaps, not successful pre-repair tests.

For genuine browser acceptance, build first and start the **isolated** upstream app
with a scratch session directory and loopback listener. Set `ANNOTATION_STUDIO_URL`
to that listener and optionally `CHROME_PATH` to Chromium:

```sh
ANNOTATION_STUDIO_URL="$ISOLATED_STUDIO_URL" node packages/web/tests/bms-s03-browser.mjs
ANNOTATION_STUDIO_URL="$ISOLATED_STUDIO_URL" VIEWER_MODE=molstar \
  node packages/web/tests/editor-qol.mjs
```

The S03 browser test uses real Chromium/Mol* WebGL, visible-pixel hit candidates and
Mol* hover identity to confirm actual overlay/frame hits before checking selection;
it does not inject pick callbacks. It exercises CATH text re-enable, inline segment
length/after-anchor transaction, CIF export, Undo, overlay isolation, frame isolation,
and real document picking after restoration. Python tests independently cover exact
backbone masks, internal-target confirmation/preservation, pair remapping, concurrent
TTL pruning, all-pinned capacity, disk failure, retry and cold reload.

Applicator tests from the BMS worktree:

```sh
BMS_ATOMWORKS_SOURCE="$STUDIO_SOURCE" python -m unittest discover \
  -s third_party/atomworks_studio/tests -v
```

## Observed qualification

- Isolated Python 3.12.13, AtomWorks 3.0.4, Torch 2.7.1+cpu; final selected union:
  **72 passed**, no skips. S03 cases are included, not an additional suite total.
- Original upstream web unit suite: **59 passed**. Before/after source probes both
  executed with their different documented assertions; neither substitutes for browser tests.
- Applicator: **6 passed**, including real disposable upstream Git clones,
  application/idempotence, drift rejection and post-apply verification.
- Locked browser build passed (WASM + `tsc --noEmit` + Vite). Build warnings:
  upstream Rust package metadata, browser-externalized Node modules and large chunks.
- Real S03 Chromium/Mol* browser scenario passed; corrected official editor-QoL
  scenario passed with Mol*. Patinae, touch/phone, deployed iframe/authentication,
  native inference and all-engine molecular acceptance were **not** qualified here.
- A separate full upstream `picking.mjs` attempt timed out while waiting for an
  annotation response from one pixel-derived atom candidate after its earlier
  residue/atom checks; that full script is **not** claimed passing. The dedicated
  S03 overlay/frame scenario uses confirmed hover identity and passed independently.

No generated logs, environments, checkout, screenshots, browser bundles or session
files are committed. The source checkout and isolated environment used for this
qualification live only in the active scratch area.
