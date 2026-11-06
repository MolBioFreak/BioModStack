# WP5 incremental Results relations (on 1bd48d8)

## Delivered

- Native CSV input/refold/confidence references use producer path fields, not filenames. Repeated example IDs no longer silently overwrite metrics; ambiguous rows remain explicit. Dataset/task discriminate when writer supplies them.
- Reader resolves retained relation documents to registered JobArtifact IDs/hashes and selectable structures; missing/external relations remain explicit and do not gate execution.
- Passive return hooks observe Atomworks CIF writer calls under native input/trajectory writers and torch.save under beam search. State metadata preserves checkpoint IDs, selected indices, batch inputs, step and rank. A checkpoint batch is NOT claimed as final-candidate ancestry.
- Existing retained-state route digest now becomes NativeFileSource.expected_sha256. Actual existing prepare_native_sources acquisition copies the bytes and records their digest; the test proves its existing changed-byte rejection. No pickle load or state execution occurred. Artifact ID remains route provenance; backend acquisition verifies the registered digest carried by that route, not an independent artifact-ID lookup.
- Actual RFD4 AminoAcidCompositionMetric definitions from installed revision 8f7f702e46f944713fbe8ff5c566221aaa54eb0b provide descriptions/units for its five outputs. CSV numeric display conversion is limited to fields declared numeric; raw CSV values remain in exports.
- RFD3 comparison uses its current paginated /rfd3-generation reader, retaining length, CA radius of gyration, helix and strand counts, including zero/null. No confidence score is invented.

## Shared ResultsViewer integration (do NOT replace parent's newer selectors)

Add beside the existing lazy DesignComparePane declaration:

```tsx
const NativeBinderComparison = lazy(() => import('./NativeBinderComparison').then(module => ({ default: module.NativeBinderComparison })));
```

At `activeTab === 'compare_designs'`, wrap the existing DesignComparePane in a fragment and append:

```tsx
{isRFD3GenerationResultJob(activeJob) && activeJob && (
    <NativeBinderComparison jobIds={[activeJob.id]} selectedDesignIds={selectedDesignIds} />
)}
```

Preserve the parent's existing candidateDocuments/selectedNativeSources props and the DesignComparePane selection controls. This condition does not touch Shape/MD. For the multi-Job comparison surface, hasNativeComparison now recognizes the exact RFD3 generator; pass full Job params rather than a model/mode-only projection. No extra dispatcher is needed there.

The previous RESULTS-RECEIVING-HANDOFF.md JobSubmission hunks still apply; no new caller signature change is needed (state.sha256 is optional for old callers).

## Real evidence and execution

Read-only installed image: `/mnt/BioModStack/apptainer/.image-store/objects/sha256/058a27dff02778cd7304cf8955c73115f6baaa4dfc0202540c73a10a403fa957/runtime.sif`. CPU-only source reading inspected native staging, trajectory and checkpoint-save implementations; no inference invocation.

Retained scientific run: `/home/dalab/Documents/BMS/Releases/de-novo-full-20261021/resume-controlled-20261022/complete-20261023/native-qualification-20261024/rfd4/retained/bms`.

`test_retained_publication_cold_read` copies that retained tree to isolated scratch, derives the original root from the publisher's exact writer-path/document binding, replays the real publisher with explicit root relocation, registers into isolated SQLite, closes/reopens the session, and reads the native page. CIF bytes and raw metrics are unchanged. Mounted UI reads that page, emits exact continuation identity and actual Blob JSON retaining input artifact/hash relations. Evidence: `.artifacts/results/retained.json`, `.artifacts/results/retained-mounted-export.json` (ignored, local only).

Reproduce from worktree:

```sh
BMS_RETAINED_RFD4=/home/dalab/Documents/BMS/Releases/de-novo-full-20261021/resume-controlled-20261022/complete-20261023/native-qualification-20261024/rfd4/retained/bms BMS_RETAINED_RESULTS_WIRE="$PWD/.artifacts/results/retained.json" BMS_RESULTS_POPULATION_WIRE="$PWD/.artifacts/results/populations.json" /home/dalab/biomodstack/checkpoint-api-env/bin/python -m pytest platform/api/tests/test_rfd4_results_relations.py platform/api/tests/test_denovo_results_populations.py -q -s
```

From platform/frontend:

```sh
BMS_RETAINED_RESULTS_WIRE="$PWD/../../.artifacts/results/retained.json" BMS_RESULTS_POPULATION_WIRE="$PWD/../../.artifacts/results/populations.json" BMS_RECEIVING_EVIDENCE="$PWD/../../.artifacts/results" node_modules/.bin/vitest run --config vitest.md.config.ts tests/vitest/deNovoV15Relations.test.tsx tests/vitest/deNovoV15ResultsPopulations.test.tsx
node_modules/.bin/tsc -b --pretty false
```

## Actual remaining gaps — WP5 is NOT fully complete

1. Spawned native metrics workers do not inherit sys.setprofile. The trajectory/input writer hooks work in the observed process, but worker-process event collection/transport remains unimplemented. The pinned native producer supports these relations; this is NOT unsupported science. XTC writer return capture is also not implemented. Do not present those absent hooks as producer absence.
2. Checkpoint state hook has transport-unit qualification only; retained evidence here contains no saved checkpoint states. Exact final-candidate ancestry is genuinely not supplied by that save operation; captured IDs identify scored checkpoints and batch membership only. No loadable-state execution is claimed.
3. Retained example proves generated/input relation and real metrics; refold/trajectory/state paths are currently unit-tested transport specimens, not retained scientific end-to-end examples. Additional retained evidence is needed for those scientific producers.
4. Full model-owned metric definition inventory (all RFD4 providers, Complexa, La-Proteina, DISCO) remains incomplete. Only the exact composition definitions and existing BoltzGen descriptors plus RFD3 geometry descriptions are delivered. Unknown units remain unknown.
5. RFD3 per-Job comparison mounting in ResultsViewer awaits the exact caller hunk above; the adapter itself is tested. Parent JobSubmission integration/full mounted receiving remains parent-owned. Retained mounted test verifies emitted continuation identity, not scientific continuation execution.
6. Artifact-ID-based state reacquisition as an independent backend selector is not added. The existing managed-file owner now verifies the registered hash passed from Results, closing the dropped-digest bug without changing its schema or adding a gate.
