# Native ONT signal receiving acceptance

`ont_signal_acceptance.py` builds the actual `ReadAndSignalWorkbench` and its
children using the repository production Vite configuration, then runs the bundle
under `/bms/` against native ONT routers and private SQLite. Chrome and HTTP run
together in a route-free user/network namespace. It does not import the normal
application lifespan, run science, use workers, or touch a live database.

## Run

Use a **new** absolute evidence root and the existing genuine native reproduction
root containing `raw/`, `dna/`, and `rna/`. Reuse the locked API interpreter and
frontend dependencies; `.tmp`, `.vite`, and `.vite-temp` must be private directories,
not links into the donor installation. From the repository root:

```sh
export ONT_SIGNAL_ROOT=/absolute/new/task-evidence
export ONT_SIGNAL_NATIVE=/absolute/qualified/native-reproduction
export ONT_SIGNAL_PARENT_NETNS=$(readlink /proc/self/ns/net)
unshare --user --map-root-user --net sh -c '
  ip link set lo up
  exec /absolute/locked/api/.venv/bin/python \
    platform/frontend/browser-tests/ont_signal_acceptance.py
'
```

Ports 18761–18763 exist only inside that namespace. All mutable BMS roots,
including `BMS_INPUTS`, are bound before API imports. The supervisor stops its
process groups on success or failure. No Docker/build of scientific runtimes,
GPU execution, network rental, instrument, deployment, or push is performed.

## What is real and what is synthetic

- **Real retained bytes:** BLOW5/index, BAM, read inventories, reform/realign PAF,
  native renderer receipts, waveform JSON from the native descriptor-transfer
  lookup, Squigualiser HTML, Squigulator output, and comparison HTML. The fixture
  copies them unchanged and hashes every imported file. Native artifact endpoints
  retain their descriptor/ownership/integrity checks. Every successful HTML
  response is compared with the imported native inventory.
- **Synthetic receiving metadata:** run, job, input, calibration/profile approval,
  viewer, and comparison row identities plus the authenticated operator. These
  explicitly labelled SQL records model the retained receiving state; they are
  **not evidence of scientific admission, calibration approval, genomic
  placement, or a fresh execution**. Own-read reference controls stay labelled.
- RNA's old upstream BAM lacks RG model metadata. The fixture does not invent RG
  tags or claim successful move-source admission; its move source remains failed
  with `legacy_missing_RG_not_admitted`. Its previously qualified direct native
  rendering/comparison bytes are still exercised as receiving artifacts.
- The fixture mounts the real workbench, not the full NGSToolkit/Project/IGV
  shell. Cross-shell navigation belongs to the other receiving harness. External
  move-BAM discovery remains a native 503 in this namespace; no candidate response
  is substituted.

## Required assertions

1. Clear and select the exact raw read through the real input, request its native
   waveform, and compare **every** SVG point with retained pA samples and the
   displayed full/returned sample counts.
2. Navigate to DNA and RNA Squigualiser views. Load native mapping/view metadata
   and native HTML; attach CDP to the opaque out-of-process iframe, traverse Bokeh
   shadow roots, and check painted pixels rather than just iframe/model existence.
3. Navigate to DNA and RNA comparisons. Assert two native Bokeh figures share
   their x-range. A trusted wheel event must update both ranges **and** native
   CustomJS annotation fonts, with no uncaught exception.
4. Save every native HTML view twice after acknowledgement, read back the exact
   SQL-backed session, cold-reload, and check restored comparison controls and
   embedded plots. Five viewer rows remain; no duplicate receiving rows appear.
5. Verify parent DOM access is denied, iframe sandbox remains `allow-scripts`
   without `allow-same-origin`, network CSP remains denied, and no external HTTP
   page requests were attempted. Native cross-job artifact requests return 404;
   divergent saved comparison settings return 409 without advancing the row.

`acceptance.json` is the assertion summary; `*-bokeh.json` records paint, shared
ranges, annotation font changes, and confinement. `*-restored-controls.json` and
`*-ownership-controls.json` record cold controls and native negative controls.
Per-step JSON captures CDP requests, responses, loading completion, headers,
exceptions and DOM. `response-*.body` contains actual received native response
bytes, including HTML. `imported-native.json` provides retained source provenance.
A failed process or `failed` summary is not a pass.

## Receiving defects covered

- Parent **Save session** previously dropped the child's comparison job, preview,
  immutable settings, and review tuple. Preserve it only for the same exact
  selected read/locus and keep already-persisted mapping IDs during refresh.
- Cold reopening a saved mapping previously compared a real nullable persisted
  alignment session with JavaScript `undefined`. Use the saved exact tuple while
  IGV is absent; a supplied current alignment still wins, and mismatches remain
  errors.
- When mapping metadata arrived after a comparison, identity reset cleared the
  job while the restore effect saw the old job and skipped reloading it. Restore
  on identity changes without consulting that stale job snapshot.
- Bokeh 3.1.1 paints with an inline-only CSP, but native CustomJS throws `EvalError`
  on pan/zoom. Enable callback compilation **only in the existing opaque-origin,
  network-denied report sandbox**. `secureOntSignalHtml` also updates the exact
  inline-only script directive in older native embedded policies (CSPs intersect),
  retaining all their other directives. It does not modify original artifact
  bytes or change the application's CSP. Standalone downloaded reports and
  scientific wrapper images are not changed by this frontend receiving fix.
