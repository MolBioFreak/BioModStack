# Parent lifecycle acceptance — bounded evidence

Integrated base: `6a9b4bd` (fast-forwarded from `e710af9`, including the parent optional operations and La evaluation integrations). No JobSubmission or ProteinModificationTemplate edits in this leaf.

## Demonstrated repair

`deNovoAuthoringEnvelope` previously called generic placement fallback with `{}`. That helper reads the session target only at the literal `/submit` pathname. Mounted ordinary JobSubmission at `/bms/submit` therefore saved explicit Local while native editor submission uses the selected session target. The fix snapshots that same target explicitly; no scheduler, scientific normalization, gate or admission policy changed.

Before: `.../cache/scratch/denovo-parent-basename-before.log` — `/submit` passes; `/bms/submit` fails with null versus ambient-remote. After: all 11 mounted HTTP parent cases pass in `denovo-parent-final4.xml`, including the concurrently added conditioned Project case.

## Executed

Evidence prefix: `/home/dalab/.hermes/profiles/fresh/cache/scratch/`.

- `denovo-parent-final4.xml`: real HTTP parent suite **11 passed**; reconciled generation suite **11 passed**; reconciled source suite **11 passed** (**33 passed**). The broader `denovo-parent-final3.xml` run also records the old D20 suite's **5 failures**, all obsolete Sampling disclosure lookups; D20 was not edited or declared passing.
- `denovo-parent-persistence-final2.xml`: **2 passed**. Reads the same actual mounted unconditional and conditioned Project SQLite store, verifies normalized request hash, native adapter, exact native normalization/settings, immutable scheduler payload after reopening a connection, MODEL/hash and native closure for the partial case, zero run groups and zero Jobs. The conditioned mounted/audit cases and browser read-only harness guard were added concurrently by the parent and preserved.
- `denovo-parent-tsc-final.log`: tsc execution returned zero. This ran after the placement fix.
- `denovo-parent-build.log`: actual Vite build passed, before the small placement fix. Full browser at `/bms/submit` rendered the real saved Project using this build. `denovo-parent-lifecycle-final/browser-project-readonly.json` records the full-shell DOM, native lengths 73/99, seed 0, run name and resource statuses. No browser write controls clicked. WebGL/provider leaves are not mocked in this full browser.

The mounted suite uses production JobSubmission, Template Manager and native editors; only unrelated editor imports and GPU-integration leaves are inert. Axios forwards to real localhost FastAPI routers and scratch SQLite, preserving request headers. No manufactured persistence responses. Actual generation-settings discovery and structure materialization are native owners. MODEL 2 comes from an explicitly labelled synthetic two-MODEL PDB; opaque resume bytes are persistence-only, never deserialized or executed.

Covered: partial/inverse/evaluate/state continuation href receiving; source MODEL/hash, native recipe and resume seed/hash; cold Template loading for each route; inactive partial/source and Complexa reward controls; clone intent; explicit Local versus fresh inherited target at both pathnames; two acknowledged ordinary Project Saves, cold component/query-cache reopen, another Save, actual native Prepare and immutable SQLite readback. The Project is seeded from an actual parent-emitted Template envelope, not invented draft JSON. Generation/source regressions retain exact request and condition assertions, but now address primary typed controls and explicit condition creation rather than retired schema-field selectors. Legacy discovery failure in the narrow transport double is explicit; successful real discovery is covered by the new HTTP suite.

## Rerun

Use a NEW scratch evidence root and an unused loopback port. Python is `/home/dalab/.cache/bms-remote-worker-complete-venv/bin/python`. From repository root:

```sh
BMS_PARENT_PORT=18879 BMS_PARENT_LIFECYCLE_ROOT=/home/dalab/.hermes/profiles/fresh/cache/scratch/NEW-parent-run /home/dalab/.cache/bms-remote-worker-complete-venv/bin/python platform/frontend/browser-tests/denovo_parent_lifecycle_server.py
```

Wait for `/acceptance-health`; then in `platform/frontend`, set the same root/port and `BMS_SCHEMA_PYTHON` and run:

```sh
./node_modules/.bin/vitest run --config vitest.md.config.ts tests/vitest/deNovoV15ParentHttpLifecycleMounted.test.tsx tests/vitest/deNovoV15GenerationMounted.test.tsx tests/vitest/deNovoV15SourceAuthoringMounted.test.tsx --maxWorkers=1
```

From repository root with the same evidence root:

```sh
/home/dalab/.cache/bms-remote-worker-complete-venv/bin/python -m pytest platform/api/tests/test_denovo_parent_mounted_persistence.py -q --basetemp /home/dalab/.hermes/profiles/fresh/cache/scratch/NEW-parent-pytest
```

Locked dependencies are donor symlinks, not a clean reinstall. This leaf's direct runs bind localhost and refuse execution writes; they were not network-namespace isolated. The separately appearing qualification wrapper is not authored or modified by this leaf.

## Final integrated, namespace-isolated qualification

Superseding the earlier browser-build limitation above, the integration owner built the actual frontend after the placement fix at code commit `4434e081907b27e81ba8d7add08a1f16e3dce5a8`. `tsc -b` and Vite returned zero; build log: `/home/dalab/.hermes/profiles/fresh/cache/scratch/denovo-parent-build-4434e08.log`. Documentation-only commits after that SHA do not change the tested code.

Final evidence root: `/home/dalab/.hermes/profiles/fresh/cache/scratch/dn-parent-final-4434e08/`.

- `mounted.json`: **33 passed, 0 failed, 0 skipped** (11 each: ordinary native HTTP parent, reconciled generation, reconciled source).
- `persistence.xml`: **2 passed, 0 failed, 0 skipped**, auditing the actual mounted unconditional and MODEL-2 partial Project preparations. The core Jobs count and Project run-group count are both zero.
- `namespace.json`: actual tested code SHA, namespace identity, and empty network route table. Server, mounted tests and Chrome ran together in `unshare -Urn` with loopback only.
- `browser-readonly.json`: real built full-shell `/bms/submit` reopened the saved Project, displaying native lengths 73/99; **zero non-read requests and zero external request attempts**. A server-side read-only marker refused all browser-stage mutations. `browser-dom.json`, `browser-events.json` and `browser.png` retain actual rendering/network evidence.
- `frontend-source-sha256.json` and `build-sha256.json`: source and built-artifact hashes. Locked donor dependencies were reused rather than reinstalled.
- `process-exits.json`: task-owned namespace server/Chrome were terminated on completion.
- A separate original-code replay in `denovo-parent-placement-before/baseline.log` confirms `/submit` passes and `/bms/submit` loses the target using the original `6a9b4bd` deNovoAuthoring implementation. Only that module was resolved to its baseline version; mounted parent and HTTP owners stayed real.

The final route cases additionally clone the actual emitted native request after cold Template loading and assert complete native-request equality. The conditioned Project test exercises two real mounted Save clicks, cold reopening, source MODEL/hash and partial-time zero, then actual Prepare. It does not launch anything.

Reproduce the integrated qualification with a new scratch root and an already successful actual Vite build:

```sh
BMS_PARENT_NETNS="$(readlink /proc/self/ns/net)" \
BMS_PARENT_LIFECYCLE_ROOT=/home/dalab/.hermes/profiles/fresh/cache/scratch/NEW-isolated-parent \
BMS_PARENT_BROWSER_BUILD=/absolute/path/to/actual-vite-output \
unshare -Urn /home/dalab/.cache/bms-remote-worker-complete-venv/bin/python \
platform/frontend/browser-tests/denovo_parent_lifecycle_qualification.py
```

This final run does not supersede the unperformed boundaries below or the five failing historical D20 selector cases.

## Not closure

- Five obsolete D20 cases still fail and need their RFD4 expert-control owner reconciled.
- Results producer buttons themselves are not mounted by this suite: production continuationHref emits the route, then real ordinary parent and native materialization receive it.
- These new tests verify emitted `native_job_request` persistence, not successful Jobs POST or run-group dispatch. No scientific Jobs, rentals, deployments or production writes occurred.
- Project Prepare covers unconditional and MODEL-2 partial RFD4. State-resume Project preparation and La Project-bound run-group dispatch remain unperformed.
- Per-call optional-operation placement and disabled call histories are separate leaf coverage, not proven by parent target inheritance here.
- Browser coverage is read-only full-shell Project rendering, not a complete browser save/clone/continuation journey. Unmounted unrelated system/features, experiment-workspaces and execution-targets return 404 and are disclosed in browser resource evidence.
- First development attempts had harness mistakes (201 versus 200, duplicate Template names, absolute versus managed source path, old task selector, missing real window pathname). Their logs are retained. Per-test wire filenames were reused across attempts; final wire files are final evidence, not a complete archive of earlier attempts.
