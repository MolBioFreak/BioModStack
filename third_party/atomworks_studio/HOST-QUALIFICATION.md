# Repaired source → locked CPU private host qualification

This is a bounded S02/S03 packaging seam result, **not completion of S02, S03,
S04 or the consolidated implementation specification**. Parent base is
`60d65adad604ddcba9ed4e220141271131daae84`. No frontend form, API router/service,
production app registration, shared environment, managed service, live store or
worker was changed. Transport authentication, trusted owner and CSRF remain owned
by the existing private router/service.

## Supported build/install

From BMS root, with a disposable exact-release Git checkout and upstream web
build tools provisioned in task-owned directories:

```sh
export UV_CACHE_DIR="$SCRATCH/uv-cache"
export RUSTUP_HOME="$SCRATCH/rustup" CARGO_HOME="$SCRATCH/cargo"
export PATH="$CARGO_HOME/bin:$PATH"
sh scripts/prepare_atomworks_studio.sh "$SCRATCH/supported-venv" \
  "$RELEASE_WHEEL" "$SCRATCH/source"
```

The installer now builds and installs the S03-repaired package, not the original
release wheel. The build is an explicit deterministic repack of the verified
release: repaired API/document Python + freshly built `packages/web/dist`, all
other release members unchanged. It never relies on hatch's stale checked-in web
fallback. Exact source/patch/build hashes, original-versus-rebuilt wheel identity,
full RECORD integrity, complete installed web hashes, runtime lock and notices are
retained. This is not an editable install; runtime imports come from site-packages.

Reviewed release: `04fe5fbd03fea8b7d49a767156d7d33bd10e5d13`.
Base PyPI SHA-256: `da60857d956a65d1b4dfe439326303b0a0c97b6fa520cece96fa02d041d0524d`.
S03 patch SHA-256: `3ad8eb8df90e263f191339c9969e8371735ad4d352826679ab4514e13beee5b0`.
Rebuilt filename: `atomworks_studio-0.0.5-1bms03-py3-none-any.whl`.
Observed rebuilt SHA-256: `c57f88780388d3640d8a3d01642d46057f99c3aaf49ab99cc95dc76f182f121f`.
Rebuilt RECORD SHA-256: `a022b52977d09ec6283c65883acf04814506040c6147c95919ef680bfbf4e695`
(157 members, all checked).
Runtime lock SHA-256: `51fc498a8f781bbb217e5e998ad102301dfe93b9f25ebbf23b40e449b447bdab`.
Two independent invocations of the actual locked build/repack produced the same
wheel SHA-256 in this toolchain. This is not cross-platform reproducibility proof.

The builder explicitly supplies upstream's revision/version environment values;
otherwise its worker file embeds `Date.now()` and differs on every invocation.
No digest was relaxed to hide that difference. `host-build.json` binds all emitted
assets and the exact private API transport transform. The rebuilt entry bundle is
`assets/index-BFvHw4Ox.js`; after the reviewed private-prefix transform its SHA-256
is `5deafbd658a63ab429d1c096134a0c5d58d362a980b7fb8910558b252aa2d77c`.
Worker opt-out and worker-request denial remain in place.

Observed build tools: Node 22.23.2, pnpm 11.16.0, rustc/cargo 1.97.1,
wasm-pack 0.15.0. Observed runtime: Python 3.12.13, AtomWorks 3.0.4,
Torch 2.7.1+cpu, `torch.version.cuda is None`; 63 installed distributions,
`uv pip check` passed. All 152 copied notice hashes verified. Python/OS/toolchain
binaries are not a fully digest-pinned OS image or complete legal audit.

## Retained local handles

All new build/install/test artifacts are under the active fresh-profile scratch:
`/home/dalab/.hermes/profiles/fresh/cache/scratch/atomworks-patched-host/`.

- `source`: disposable exact-release checkout + tracked S03 patch + actual web build.
- `supported-venv`: final isolated supported CPU environment.
- `supported-venv-package/atomworks_studio-0.0.5-1bms03-py3-none-any.whl`: final wheel.
- `supported-venv/share/atomworks-studio/receipt.json`: final receipt and notices.
- `rebuild/`: independently rebuilt identical wheel.
- `supported-install.log`, `final-host.xml`, `final-host-tests.log`,
  `final-upstream.xml`, `final-upstream-tests.log`, `final-applicator.log`,
  `web-unit.log`, `web-before.log`, `web-after.log`, `final-encoding.json`.
- `final-pytest/`: task-only session stores/service/browser logs. Services were
  started on fixture-owned ephemeral loopback sockets and terminated by fixtures.

The original release wheel and browser instrumentation were read from completed
S02 scratch artifacts, without modification. Rust/Cargo artifacts were copied
into this task's own directories. New uv installs use this task's own cache and
copy mode; no shared cache inode was repaired or edited.

## Executed checks and distinctions

- **16 passed**, no skips: root package + real cross-process private HTTP/browser
  union (`tests/test_atomworks_studio_package.py`, `tests/test_atomworks_studio_http.py`).
  This includes real import/edit/Undo/frozen export/owner isolation, auth spoofing,
  cache/CSRF policy, exact rebuilt asset/notice/provenance checks and drift refusal,
  repaired segment endpoint and selected-model parsing/export.
- That union actually enabled the Chrome iframe probe and the separate genuine
  positioned S03 Chromium/Mol* scenario **through the real private BMS router**.
  The first imports an unpositioned example and is not picking proof. The second
  passed real CATH input re-enable, visible inline segment bounds/after-anchor
  transaction, export/Undo, visible-pixel + Mol* hover-confirmed overlay/frame
  isolation and document restoration. No viewer callbacks or fake snapshots were
  substituted. Only module URL resolution and the existing trusted auth header
  were adapted in the upstream test harness. No secret was written to logs/files.
- **72 passed**, no skips: selected upstream Python union against the final
  installed repaired package: `test_bms_s03.py`, `test_api.py`,
  `test_condition_authoring.py`, `test_save_and_confirmation.py`,
  `test_close_session.py`, `test_editing_transactions.py`,
  `test_rfd4_internal_coords.py`. This includes the explicitly named reproduction
  of the old backbone shortcut; that reproduction is not repaired acceptance.
- **59 passed**: upstream web unit suite. Source before-probes reproduced the old
  defects; after-probes passed corrected DOM/callback/source contracts. Neither
  substitutes for the independently executed real browser scenario.
- **6 passed**: applicator tests including real disposable Git clones, idempotence
  and source/lock drift refusal.
- `git diff --check` passed. TestClient emitted its existing httpx deprecation;
  web build emitted upstream Rust metadata, browser-externalized Node-module and
  large-chunk warnings. No broad upstream picking-script pass is claimed here.

Reproduce the host union using the existing API test interpreter (read-only):

```sh
STUDIO_TEST_PYTHON="$SCRATCH/supported-venv/bin/python" \
STUDIO_RELEASE_WHEEL="$RELEASE_WHEEL" \
STUDIO_PATCHED_WHEEL="$SCRATCH/supported-venv-package/atomworks_studio-0.0.5-1bms03-py3-none-any.whl" \
STUDIO_PATCHED_SOURCE="$SCRATCH/source" BMS_STUDIO_BROWSER=1 \
PYTHONPATH="$BROWSER_TOOLS" "$API_TEST_PYTHON" -m pytest \
  tests/test_atomworks_studio_package.py tests/test_atomworks_studio_http.py \
  -q -s --basetemp "$SCRATCH/new-host-test-dir"
```

Browser and rebuilt-wheel checks are explicitly skipped/omitted when their opt-in
handles are not supplied; a default small suite is not equivalent evidence.
Upstream Python tests use task-only instrumentation `pytest==9.0.2` and
`httpx==0.28.1` via a separate target directory; the runtime lock was not expanded.

## Parent import/MODEL contract: measured, not inferred

`POST /api/atomworks-studio/api/v1/sessions` accepts multipart:

- `file`: UTF-8 **text CIF/mmCIF only**, filename ending `.cif` or `.mmcif`
  (case-insensitive). **PDB and BCIF return 415**. This host does not convert them.
- `parse_preset`: `minimal` (default) or `rcsb`.
- `assembly`: string, default `"1"`; `"asym"`/empty mean asymmetric unit,
  `"first"`/`"all"` or comma-separated assembly IDs use upstream parsing.
- `model`: optional integer, interpreted as **1-based frame ordinal**, not the
  original `_atom_site.pdbx_PDB_model_num` identifier.
- `remove_waters`: optional boolean; `hydrogen_policy`: optional `keep`/`remove`.
- `advanced_options`: JSON-object string, default `"{}"`; upstream permits
  `load_standard_annotations`, `add_missing_atoms`, `remove_waters`,
  `fix_ligands_at_symmetry_centers`, `fix_arginines`,
  `add_id_and_entity_annotations`, `convert_mse_to_met`, `long_bond_policy`,
  `struct_conn_distance_policy`, `hydrogen_policy`, `altloc`, `altloc_seed`,
  `model`, `remove_ccds`. Explicit top-level non-null model/water/hydrogen fields
  override their advanced-options counterparts. Defaults are upstream-owned.

The private HTTP test generated a real two-model mmCIF with source model IDs
**7 and 42** and distinct coordinates. `model=1` selected source ID7;
`model=2` selected ID42. Export of **either** produced only
`_atom_site.pdbx_PDB_model_num = 1`, with the selected coordinates preserved.
Omitting `model` exported the first frame, not the stack: the native document
owner takes the first parsed array. Thus the original model selector is source
ancestry, not the exported derivative selector. Parent must inspect the captured
bytes and reconcile the new managed derivative selector; do not carry source ID42
into a single-model export, silently assume that it remained42, or relabel PDB/BCIF
bytes as CIF. No receiving or frontend contract was changed in this lane.

## Bounded encoding differential (pins unchanged)

`third_party/atomworks_studio/tests/compare_encoding.py` downloads and hash-checks
three immutable native definition inputs from actual BMS pin
`8f7f702e46f944713fbe8ff5c566221aaa54eb0b`. It evaluates those standalone definitions
with the installed CPU dependencies, avoiding the unrelated model initializer via
an explicitly labeled scratch-only package namespace. Studio conditions are
loaded in a **separate process** so its preference for already imported native
modules cannot hide the discrepancy. No runtime package namespace is modified.

```sh
# Diagnostic dependency only, never a runtime lock change:
uv pip install --python python3.12 --target "$SCRATCH/encoding-tools" PyYAML==6.0.3
PYTHONPATH="$SCRATCH/encoding-tools" "$SCRATCH/supported-venv/bin/python" \
  third_party/atomworks_studio/tests/compare_encoding.py "$SCRATCH/native-definitions"
```

Observed native vocabulary **147 tokens**, Studio/AtomWorks vocabulary **34**;
all Studio tokens occur natively, and all shared token indices match. Only shared
atom-layout mismatch is `UNK`: native supplies N/CA/C/O (slots0/1/2/4), Studio's
layout is empty. Native adds113 NCAA/glycan tokens; therefore encoding equivalence
is false. Both real condition implementations nevertheless expose the same
**28 standard polymer sequence-probability channels and token indices**.

Native encoding source SHA-256:
`b09460edc31a90be47c49244785f76a66059a969520f055983ac2da4f3e13be1`;
native vocabulary YAML:
`0efd6c84938c2d944f24587769886d4cbd0e0a248106274bff12c64dede6d3a1`;
native conditions:
`b05effbc8a437413301752f1ea99cb5c246fae2df3eefe034ceefc927951d03b`;
Studio conditions at bundled pin `e519afe329a0963172747e9f71e843a21b6995e8`:
`090df37076db941201c319379bddebfbca9548f9b8130059c2ef2a717c510e06`.

This qualifies a bounded CPU vocabulary/condition-channel differential, **not**
native featurizer/consumer/inference round-trip or support for every modified
molecule. It creates no scientific admission gate and changes no native pin.

## Still unperformed here

Parent form/source-row adoption, preview/Save/Template/Project/Generate and
cold-reopen custody; production router registration/ingress/deployment; legacy
service-worker migration; broad offline chemical resources; Patinae/WebGPU,
phone/touch and full picking suite; complete OS image/legal audit; native model
featurizer/inference and enzyme function. No weights, jobs, rental or deployment.
