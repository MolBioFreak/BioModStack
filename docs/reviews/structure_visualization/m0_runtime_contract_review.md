# M0 Runtime Contract Review — BioModStack Structure Visualization

**Date:** 2026-07-18
**Phase:** M0 — runtime contract and real-browser probe
**Decision:** **STOP — M0 acceptance is not met; do not start M1**

## 1. Executive decision

The pinned production viewer can load, render, select, color, and explicitly dispose its Mol* plugin through the current private-instance path. In a real Chrome 150 React StrictMode harness, all 55 test cycles became ready and usable, and every plugin reported disposed after BMS unmount. One final viewer also remained live and usable.

That apparent lifecycle success is not sufficient. After forced garbage collection and final teardown, the renderer retained linear per-cycle state:

- CDP live event listeners: **6 → 8,361** (`+8,355`);
- DOM nodes: **33 → 4,556** (`+4,523`);
- renderer heap: **3,971,876 → 122,987,120 bytes** (`+119,015,244 bytes`, **113.502 MiB**);
- backing storage: `+35,223,413 bytes` (**33.592 MiB**);
- page-observed heap slope: **2,047,531.87 bytes/cycle** (**1.953 MiB/cycle**);
- page-observed listener-operation slope: **131/cycle**;
- WebGL context requests: **1/cycle**.

The result is reproducible with CDP `Runtime.enable` disabled, so retained DevTools console object handles are not the cause.

The exact installed source identifies the dominant lifecycle defect:

1. `molstar@4.5.0/lib/mol-plugin-ui/react18.js` calls `createRoot(target).render(element)` and discards the React root handle.
2. `PluginContext.dispose()` removes the canvas container and releases Mol* resources, but cannot call `root.unmount()` for the discarded UI root.
3. `pdbe-molstar@3.3.0` has no custom-element `disconnectedCallback` override and exposes no public wrapper-level `dispose()`.
4. The current dirty BMS `disposeMolstarHost()` reaches private `viewerInstance.plugin.dispose()`. This closes the canvas/plugin but cannot reclaim the discarded React UI root.

A second independent STOP condition occurred during M0: pre-existing dirty `vite.config.ts` drifted from baseline SHA-256 `05cfc8f8…` to `5dfe3cd7…` without an M0 edit. The change removed the BioXP MJPEG proxy block/import. The Molstar alias remained intact, so the runtime probe still resolved 3.3.0, but the written dirty-path-drift gate is nevertheless triggered.

**Required disposition:** preserve the evidence and contract, stop before M1, and authorize a bounded lifecycle architecture decision. Do not treat `plugin.disposed === true`, zero connected canvases, or 55 successful selections as lifecycle closure.

---

## 2. Scope and non-actions

M0 was limited to:

- exact runtime/package resolution;
- typed capabilities and fail-closed identity rules;
- source-contract tests against the installed version;
- an isolated Chrome/React StrictMode lifecycle probe;
- a PDBe-wrapper versus direct-Mol* boundary comparison;
- this review.

M0 did **not**:

- upgrade or reinstall PDBe Molstar/Mol*;
- modify package dependencies or lockfiles;
- change production viewer behavior;
- migrate any workflow/viewer consumer;
- start M1 contracts/controller/adapter work;
- start Conformational Mapping Phase 13;
- build, deploy, restart, or commit the application.

The pre-existing dirty `MolstarViewer.tsx`, lifecycle helper, metric helper, package scripts, and unrelated work were preserved.

---

## 3. Repository and dirty-tree identity

### Initial M0 snapshot

| Field | Value |
|---|---|
| Repository | `/home/dalab/biomodstack/biomodstack` |
| Branch | `test` |
| HEAD | `188d69c2e266f82d14d4ac0778dc17785c2dfead` |
| HEAD subject | `feat(molbio): isolate scientific persistence in dedicated sqlite` |
| Complete porcelain lines | `266` |
| Complete porcelain SHA-256 | `e457acfa4c0985e6721be4bfad39c22a2a42800f27c4cef4424b06c3eceedab7` |
| Status classes | `25` deleted, `141` modified, `100` untracked |

The complete initial porcelain output was hashed and counted, not staged or rewritten. This is not a clean-tree claim.

At final local verification, the complete worktree had `305` porcelain entries with SHA-256 `e5a41d11e5f7f1a2608544907472ec4629b247ae44838ec66b98610b4e396660`. M0 itself appears as four untracked path groups (`src/structureViewer`, the runtime contract test, `browser-tests`, and `docs/reviews/structure_visualization`); the remaining growth is concurrent broader-tree activity. No claim is made that the whole checkout stayed stable.

### Focused pre-existing hashes captured before M0 edits

| Path | Initial SHA-256 |
|---|---|
| `platform/frontend/package.json` | `434da2ecabbd5033899816122ef5f8f02621755c0f9ae5f2aaa759dc3a5031c9` |
| `platform/frontend/pnpm-lock.yaml` | `b81a3fe8d54f5b93fb8e3ad2987d62d4176b13103d1fd9a8d7493c3f5d1eae3d` |
| `platform/frontend/vite.config.ts` | `05cfc8f8a7425f41406b57a5a4b2344f85e84141e67f004ce18c26184c377c0e` |
| `platform/frontend/src/components/MolstarViewer.tsx` | `ecdcc0e7ff9aeb003315432d66bc32308f1b7ea55e95a09e618903172ba78ad8` |
| `platform/frontend/src/components/EpitopeMolstarViewer.tsx` | `442b0d03d2721f9fb1f5721e49cfb42830c89270d35828febfca7aad50bc9d6e` |
| `platform/frontend/src/lib/molstar-loader.ts` | `7dc8c4c508ae88ef7e69b6753d0f41c4ce1db604502118692f178a0d0c20f8bb` |
| `platform/frontend/src/types/pdbe-molstar.d.ts` | `390675432b817039a75eaf5e72270aa556e688cbdbd6c198c08d5741dea637fe` |
| `platform/frontend/src/components/molstarLifecycle.ts` | `0c0769b9b4998dd08bcd8e5e8d01e6500a5c62178d60df61b274370280f4c31c` |
| `platform/frontend/src/lib/molstar-metrics.ts` | `cf5e8bc9c9afbea32ea09aca8b692dc4e55226153a11d193f16f99984daf1f92` |
| `platform/frontend/tests/molstarMetrics.test.ts` | `6654a1bef61f6a22c7db90bf3b93614714c6b7d1a48ce4aba99a11e9673cd306` |
| structure-viewer contract | `df0b61993a57ec59a83d7f07ab46c6eca1b2ce28523bf9e713eee932ea8393a4` |
| structure-viewer roadmap | `83ace11f713606321218dfc2835967f40d5922bcbce9e791f5473a6cd51921ef` |

### Concurrent drift

`platform/frontend/vite.config.ts` changed during M0 to SHA-256:

```text
5dfe3cd7584935180be6c1c1c9fe019c4eb5c9acb0095a73de4df3b83f4242b8
```

The observed delta removed:

- `IncomingMessage` type import;
- the dedicated `/api/bioxp/camera/mjpeg` proxy entry and anti-buffering response headers.

M0 did not make or reverse that edit. The Vite Molstar alias remained unchanged.

---

## 4. Exact runtime resolution

### Authoritative workspace resolution

| Layer | Result |
|---|---|
| Manifest range | `pdbe-molstar: ^3.3.0` |
| Plain Node/package resolution | `pdbe-molstar@3.9.0` |
| Stable alias | `pdbe-molstar-stable: npm:pdbe-molstar@3.3.0` |
| Root workspace lock | resolves stable alias to `pdbe-molstar@3.3.0` |
| Vite alias | `'pdbe-molstar' -> dirname(require.resolve('pdbe-molstar-stable/package.json'))` |
| Effective Vite viewer | `pdbe-molstar@3.3.0` |
| Bundled engine | `molstar@4.5.0` |
| Browser | Google Chrome `150.0.7871.128` |
| React application | `react@19.2.3` / StrictMode probe |

Plain Node resolution is **not** production Vite resolution. Source-contract tests therefore assert the Vite stable alias, root workspace lock, installed package version, and bundled engine version together.

`platform/frontend/pnpm-lock.yaml` only records the plain `3.9.0` dependency. The root `pnpm-lock.yaml` is the authoritative workspace lock and records both `3.9.0` and stable alias `3.3.0`.

### Authoritative hashes

```text
9e817cfb7ba61d20eb6d404a2522b750d436caf36ced98cad2b15c1ee6a6aa8a  pnpm-lock.yaml
1cc8a7b0b934c4fb82875c8a91da3ffa8340b53fb229351daf8e69ec7722df45  pdbe-molstar@3.3.0/package.json
caf694b8bc178eeb4d9a4f1175e3529f8784332f5715be18a59664d43ee953f7  pdbe-molstar@3.3.0/lib/helpers.d.ts
28a265e508d59eb3acd1a838689c87965ca4a703bc494eda31c4ae996eb55cab  pdbe-molstar@3.3.0/lib/helpers.js
bfe7db7534c3cbc9443438f3785f422c9a5a5aaf64372967304210775d18377a  pdbe-molstar@3.3.0/lib/viewer.d.ts
b7abdd1b889ed949b411989a4d719f600bde17e6bd2a5318b1e2ceb7e9c4059b  pdbe-molstar@3.3.0/lib/viewer.js
142a6dbddad430fd614e43a3459378bf079ff254711bf8f4f5c210d96203dd65  pdbe-molstar@3.3.0/lib/pdbe-molstar-component-build.js
5ab469bddfdc2ec92dfc7e68096f7a1fb1108306db79fb8c8096230f10e9203d  pdbe-molstar@3.3.0/lib/custom-events.js
7eaf15773e26687836942bb7f5865db2b8fff41e2b8df4d5ae41f4123d1e1fe9  pdbe-molstar@3.3.0/lib/subscribe-events.js
23b67f8472ce5eca18edac29208d86b8f966845bcec99ad456d21d2e0fe14c4e  molstar@4.5.0/lib/mol-plugin-ui/react18.js
bcdb11e06c453d5f00311d427d8c6e71207b07d975646af20276e24eb4b413ae  molstar@4.5.0/lib/mol-plugin/context.js
```

---

## 5. Frozen capability matrix

The executable matrix is:

`platform/frontend/src/structureViewer/runtime/molstarStable33Capabilities.ts`

| Capability | Status | Stable boundary | Exact finding |
|---|---|---|---|
| Load completion | Partial | private wrapper instance | `events.loadComplete` exists, but the custom element exposes no awaited ready contract |
| Load errors | Unsupported | none | connected callback does not await/catch/publish render rejection |
| Disconnect/disposal | Unsupported | direct Mol* required | no subclass disconnect; no PDBe dispose; discarded React root survives plugin disposal |
| Label chain | Supported | PDBe wrapper | `struct_asym_id` is consumed as `label_asym_id` |
| Author chain | Partial | PDBe wrapper | consumed only when label chain is absent; namespaces are not cross-checked |
| Label residue | Supported | PDBe wrapper | `residue_number` maps to `label_seq_id` |
| Author residue | Partial | PDBe wrapper | plain author number works; insertion-code fidelity does not |
| Insertion code | Unsupported | none | declared `auth_ins_code_id` is ignored; predicate broadens to author number |
| Model identity | Unsupported | none | no query field |
| Alternate location | Unsupported | none | no query field |
| Operator instance | Unsupported | none | no query field |
| Repeated entity instance | Unsupported | none | no query field |
| Selection | Supported | PDBe wrapper | select/focus/highlight/clear methods declared |
| Coloring | Supported | PDBe wrapper | overpaint and clear are implemented |
| Overlays | Partial | private wrapper instance | append load is ID-addressable but not a custom-element contract |
| Overlay removal | Partial | private wrapper instance | `deleteStructure` is imperative/private-instance access |
| Measurements | Unsupported | direct Mol* required | no stable PDBe API |
| Trajectories | Unsupported | direct Mol* required | engine machinery bundled; no stable PDBe frame API |
| Assemblies | Partial | private wrapper instance | `assemblyId` load exists; repeated operator identity/switching do not |
| Symmetry | Partial | direct Mol* required | engine extension bundled; no bounded PDBe API |
| Volumes | Partial | private wrapper instance | PDBe map streaming only; no general governed volume contract |
| Snapshots | Unsupported | direct Mol* required | engine machinery bundled; no stable PDBe API |
| Event provenance | Partial | PDBe wrapper | bubbling `event.target` identifies host, but no BMS viewer/scene/document/generation identity |

### Identity fail-closed result

`assessMolstarStable33Identity()` returns:

- `supported` only for identity expressible without unsupported fields and with a chain/entity scope;
- `ambiguous` when no chain/entity scope is present;
- `unsupported` for insertion code, model, altloc, operator instance, or repeated entity instance.

This prevents the current 3.3 behavior from silently broadening `auth_seq_id + insertion_code` to every residue with the same author number.

---

## 6. Browser probe

### Reproduction

```bash
cd /home/dalab/biomodstack/biomodstack/platform/frontend
BMS_M0_CYCLES=55 node browser-tests/runMolstarRuntimeProbe.mjs \
  docs/reviews/structure_visualization/evidence/m0_runtime_probe_chrome150.json
```

The runner:

- starts an isolated Vite server with a temporary cache;
- uses the repository Vite config and production alias;
- starts Chrome with a fresh temporary profile, precise memory information, exposed GC, and CDP pipe;
- serves a local two-residue PDB from one blob URL;
- runs one raw custom-element disconnect probe;
- runs 55 React StrictMode BMS wrapper mount/load/select/clear/unmount cycles;
- leaves one live viewer and verifies selection usability;
- forces GC and records DOM/listener/heap state;
- tears down the final viewer, revokes the blob, forces GC again, and records final state;
- removes temporary Vite and Chrome state.

Evidence:

```text
docs/reviews/structure_visualization/evidence/m0_runtime_probe_chrome150.json
SHA-256 9ec260b15af5be1c966a98582f730030d49df60bd3b5beadd58c5d299fc35e21
Generated 2026-07-18T22:36:50.173Z
```

### Functional lifecycle results

| Gate | Result |
|---|---:|
| Requested cycles | 55 |
| Ready cycles | 55/55 |
| Usable selection cycles | 55/55 |
| Plugin disposed after BMS unmount | 55/55 |
| Final live hosts | 1 |
| Final live canvases | 1 |
| Final viewer ready | Yes |
| Final viewer usable | Yes |
| Final viewer disposed | No |
| Raw custom element ready | Yes |
| Raw disconnect disposed plugin | **No** |
| Manual raw cleanup disposed plugin | Yes |
| Page console errors | 0 |
| Severe browser events | 0 |
| Renderer crash | No |

### Retention results

| Metric | Before | Final live viewer | After final cleanup | Acceptance |
|---|---:|---:|---:|---|
| CDP documents | 1 | 1 | 1 | Stable |
| CDP nodes | 33 | 4,556 | 4,556 | **FAIL: +4,523** |
| CDP JS event listeners | 6 | 8,380 | 8,361 | **FAIL: +8,355** |
| CDP heap used | 3,971,876 | 125,899,784 | 122,987,120 | **FAIL: +119,015,244 bytes** |
| CDP backing storage | 3,735,742 | 38,959,155 | 38,959,155 | **FAIL: +35,223,413 bytes** |
| Connected Molstar hosts after cleanup | — | 1 | 0 | Pass |
| Connected canvases after cleanup | — | 1 | 0 | Pass |
| Active blob URLs after cleanup | — | 1 | 0 | Pass |
| Active animation frames after cleanup | — | 1 | 0 | Pass |
| Active tracked timeouts after cleanup | — | — | 1 | Not per-cycle growth |
| Active tracked intervals after cleanup | — | — | 1 | Not per-cycle growth |

Regression slopes over cycle samples:

| Metric | Slope |
|---|---:|
| Page-observed heap | `+2,047,531.87 bytes/cycle` (`+1.953 MiB/cycle`) |
| Listener operation delta | `+131/cycle` |
| WebGL context requests | `+1/cycle` |

Connected canvas and RAF teardown succeeds. The failure is retained detached UI/listener/state ownership, not a surviving visible canvas or active animation loop.

### Browser log classification

The five recorded CDP log events were:

- one missing `favicon.ico` (HTTP 404);
- four SwiftShader/WebGL `ReadPixels` performance warnings.

There were no runtime exceptions, application console errors, renderer crashes, or severe browser events. These warnings do not explain or invalidate the linear retention.

---

## 7. Root cause analysis

### RCA-1 — Vendor custom element does not own disconnect teardown

Installed custom-element code performs:

```text
connectedCallback
  -> new PDBeMolstarPlugin
  -> viewerInstance.render(this, initParams)
```

The subclass defines no `disconnectedCallback`. Removing the raw host left `plugin.disposed === false` and one canvas retained under the detached host until the probe explicitly called `plugin.dispose()`.

### RCA-2 — Mol* React 18 root handle is discarded

Installed `molstar@4.5.0/lib/mol-plugin-ui/react18.js` is effectively:

```ts
export function renderReact18(element, target) {
    createRoot(target).render(element);
}
```

No root handle is retained, returned, or unmounted.

`PluginContext.dispose()` correctly stops subscriptions/animation, disposes canvas/context/state/managers, and calls `this.unmount()`. Its `unmount()` only removes `canvasContainer` from the DOM. It cannot unmount the React root created above.

This aligns with the measured slopes:

- one discarded UI root per viewer construction;
- approximately 131 net listener operations per cycle;
- approximately 1.953 MiB retained heap per cycle;
- thousands of detached nodes/listeners after forced GC.

### RCA-3 — Current private plugin disposal is necessary but insufficient

The dirty BMS lifecycle helper calls:

```text
host.viewerInstance.plugin.dispose()
```

That is why all 55 plugins reported disposed and all connected canvases/RAFs disappeared. It does not close the discarded React root, so `plugin.disposed` is not a complete resource-ownership predicate.

### RCA-4 — Optional PDBe cross-component subscriptions are an additional latent leak

`subscribeEvents` defaults to `false` in 3.3.0, so this path was not the dominant current probe leak.

If enabled, `subscribe-events.js` adds 12 anonymous listeners to `document` and removes none. Each closure captures the wrapper. A future BMS adapter must not enable this option without an explicit listener owner/disposer.

### RCA-5 — Dirty-path drift invalidates promotion

The pre-existing dirty Vite configuration changed during the phase. Although Molstar resolution stayed pinned to 3.3.0, the M0 plan explicitly requires STOP on dirty-path drift. Promotion cannot rely on a build/config snapshot that changed during the gate.

---

## 8. Boundary decision: PDBe wrapper versus direct Mol*

### Option A — Keep PDBe Molstar as the adapter boundary

**Benefits**

- preserves current load/select/color/PDBe map behavior;
- lower immediate feature migration;
- existing consumers already target its custom element.

**Blockers**

- custom element does not dispose on disconnect;
- wrapper exposes no public terminal disposer;
- its hardwired Mol* React renderer discards the root handle;
- exact insertion/model/altloc/operator/instance identity is unavailable;
- measurements, trajectories, snapshots, generalized volumes, and governed symmetry require private engine access;
- optional cross-component subscriptions have no disposer.

**What would be required**

- an upstream-fixed or BMS-patched/forked renderer that captures and unmounts the React root;
- an owned custom-element disconnect contract;
- bounded wrapper error/readiness APIs;
- source-contract tests pinned to the exact patch/version;
- explicit refusal of unsupported identity fields.

Using the current unmodified 3.3 custom element as the long-term BMS adapter boundary is **not acceptable**.

### Option B — Use direct Mol* behind the BMS adapter

**Benefits**

- the BMS adapter can pass a renderer callback that stores the React root and explicitly unmounts it;
- plugin/context/UI-root ownership can share one terminal lifecycle;
- direct Loci/MolScript can preserve model, altloc, operator, and repeated-instance identity where the engine supports it;
- measurements, trajectories, snapshots, assemblies/symmetry, and general volumes can be exposed through governed typed capabilities;
- no dependency on PDBe’s optional global cross-component subscription layer.

**Costs**

- BMS must own more engine configuration, UI composition, state reconciliation, and version adaptation;
- PDBe-specific conveniences must be selectively reimplemented or wrapped;
- migration requires a compatibility facade and complete consumer inventory.

### M0 recommendation

Prefer **direct Mol* behind the same BMS adapter contract**, while selectively retaining PDBe helpers only where they are bounded and testable. If retaining PDBe as the engine host is desired, require an explicit patched/forked lifecycle implementation first; do not add more private `viewerInstance` calls to the current custom element.

This is a recommendation, not M1 authorization.

---

## 9. Verification ledger

| Gate | Command/evidence | Result |
|---|---|---|
| RED contract compile | focused `tsc` before modules existed | Expected fail: two missing M0 modules, exit 2 |
| Focused contract typecheck | isolated `tsc --noEmit` | Pass |
| Focused contract runtime tests | isolated compile + `node --test` | **8/8 pass** after final RCA/source-contract addition |
| Browser probe | 55 cycles, Chrome 150, fresh profile | Functional pass; retention **FAIL**, exit 2 |
| Confounder-free browser rerun | no CDP `Runtime.enable` | Same retention **FAIL**, exit 2 |
| Raw disconnect | evidence JSON | Does not dispose |
| Final live viewer | evidence JSON | One host/canvas; ready and usable |
| Browser severe errors | evidence JSON | 0 |
| Focused harness typecheck | imports current dirty wrapper | Blocked by pre-existing undefined `UntypedApiValue` at four lines |
| Repo-wide `git diff --check` | current dirty tree | Blocked by unrelated `workflowModelInventory.ts:181` blank-line-at-EOF error |
| M0 untracked whitespace | no-index checks | **Pass** after removing two review hard-break spaces |
| Full frontend tests | `pnpm run test` | **305/305 pass**, exit 0 |
| Isolated production build | temporary external output | Exit 2: unrelated `ResultsViewer.tsx:2430` uses `showPpiflowColumns` before declaration/assignment |
| Independent runtime/frontend review | pending | Must confirm STOP/required remediation |

### Pre-existing focused TypeScript blocker

The browser harness imports the real dirty `MolstarViewer.tsx`. Focused TypeScript checking reaches that file and fails on an undefined type name at:

```text
MolstarViewer.tsx:231
MolstarViewer.tsx:233
MolstarViewer.tsx:284
MolstarViewer.tsx:338
```

All four references are `UntypedApiValue`. M0 did not modify the production wrapper to repair this unrelated dirty-source blocker. Vite strips the type-only assertions and the real browser probe executed successfully.

---

## 10. Required gates before M1 can be reconsidered

1. **Freeze the worktree/config snapshot.** Resolve or explicitly partition the concurrent Vite/BioXP proxy drift.
2. **Choose lifecycle architecture.** Approve direct Mol* ownership or a pinned PDBe fork/upstream fix that returns and unmounts the React root.
3. **Add a RED lifecycle regression for both owners.** It must fail against the current discarded-root path.
4. **Own all terminal resources together.** React root, plugin context, WebGL context, subscriptions, listeners, timers, blobs, and late async work must share one idempotent terminal owner.
5. **Prohibit or wrap `subscribeEvents`.** Default stays false; any enabled cross-component bridge must return a disposer and be host-scoped.
6. **Rerun at least 55 cycles after the fix.** Require listener/node/heap slopes to plateau after warm-up and final teardown to return near the post-module-load baseline.
7. **Retain fail-closed identity.** No insertion/model/altloc/operator/instance request may silently broaden.
8. **Repair focused TypeScript blockers.** The real wrapper and M0 harness must typecheck together.
9. **Run final frontend gates after the last edit.** Focused tests, canonical tests, lint, isolated production build, untracked whitespace, and exact hashes.
10. **Obtain independent GO.** Independent frontend/runtime review must approve the repaired evidence, not this failed baseline.

---

## 11. Final M0 disposition

| Acceptance item | Result |
|---|---|
| Exact runtime evidence current/reproducible | **Pass**, with concurrent config drift explicitly recorded |
| StrictMode leaves one live usable viewer | **Pass** |
| Teardown metrics plateau | **Fail** |
| Unrepresentable identity fails closed | **Pass** in M0 contract |
| No package upgrade/workflow behavior change | **Pass** |
| Dirty-path stability | **Fail** |
| Independent review says GO | **Not met** |

# Decision: STOP

Do not start M1 from the current viewer lifecycle. The contract and probe are useful and reproducible; the runtime is not ready for platform consolidation until UI-root ownership and dirty-path stability are resolved.
