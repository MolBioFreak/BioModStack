# Results receiving handoff (base 84280fcf4973cce9ce03a7eb9fda32f66a6c57a4)

Shared files were NOT edited. Integrator must wire JobSubmission before the new links are operational in the ordinary launcher.

## JobSubmission exact receiving changes

1. Add `isDeNovoDestination` to the existing `deNovoContinuation` import.
2. Immediately after `const continuationDestination = searchParams.get('continuation');` add:

```ts
const continuationState = searchParams.get('rfd4_state');
```

3. Replace the continuation key with:

```ts
const continuationKey = `${continuationDestination}:${continuationSource}:${continuationState}`;
```

4. In the existing continuation query `enabled`, replace only
`['redesign', 'sequence', 'prediction'].includes(continuationDestination || '')`
with `isDeNovoDestination(continuationDestination)`. Preserve existing Project logic.

5. Replace its queryFn with:

```ts
queryFn: () => prepareDeNovoContinuation(
    JSON.parse(continuationSource!) as StructureSourceSelection,
    continuationDestination as DeNovoDestination,
    continuationState ? JSON.parse(continuationState) : undefined,
),
```

Existing `setClonedValues(values); setParams(values)` receives the flattened native initial values. Routes contain `generator=rfd4_proteina`, `native_mode=generate|evaluate`, and `modification_mode=de_novo_design`. They never route to RFD3 except the preserved explicit Redesign action.

`_rfd4_continuation` is editor identity, NOT a scientific request field. Integrate it with the RFD4 recipe owner: `rfd4_partial` must open the partial settings controls without inventing a fraction/time; `rfd4_inverse` already contains `groups.generation=inverse_folding`; evaluate uses annotated slots; state supplies native `resume_slot` and deliberate optional seed (zero retained). No Jobs submission occurs during navigation/materialization. Do not spread editor-only keys into `rfd4_request`.

## ResultsViewer

No new dispatch hunk required: existing RFD4 native dispatch mounts NativeBinderGenerationResults, which recognizes `page.model_id`. Preserve the dedicated RFD3 panes and Shape/MD branches. Existing parent `sourceResultReturn` call now resolves mounted `/bms/` from the live route by default.

## Wire additions

Reader adds `record_counts` (publication records, molecular records, observations, population-record counts), `record_kind`, `native_request`, and registered artifact id/hash/source_path/producer role. Counts are NOT unique candidate counts; scoring events retain their original population and are separately classified as observations. Original totals/refs and scalar paging remain unchanged. Native exports include this accounting without modifying raw records. Unknown metric units are not guessed. Reward is surfaced separately from native metric payload and described as search reward.

## Verified scope and remaining gaps

- Real publisher -> isolated registration -> cold reader -> mounted Results test consumes actual serialized reader output. Specimen coordinates, native-writer events and state bytes are explicitly inert transport fixtures, NOT inference output or loadable states.
- Mounted Results emitted partial/inverse/evaluate routes are consumed by the real preparation helper and mounted native editor; exact MODEL 2 materialization body and native Job draft are asserted. State selection emits registered artifact/hash/path and seed zero; helper tests cover its payload.
- Parent JobSubmission integration and full mounted parent acceptance remain outstanding by ownership.
- Refold/input/trajectory/state-to-candidate associations are NOT fabricated. The inspected publisher does not retain those complete relations. State picker truthfully lists publication-level producer-classified states, says candidate association is unreported, and requires deliberate selection. Exact association publication hooks still need implementation/qualification; this is not complete WP5.
- `partial` opens the annotated-source editor without a made-up scientific fraction. Parent must select the matching recipe/settings panel. No GPU, oracle or native state execution was performed.
- Complete model-owned metric descriptor inventory and restored RFD3 comparison metrics remain outside this delivered patch; unknown units remain unknown, not generic confidence.
- State artifact id/hash travels on the route for provenance; current managed-file native binding accepts path only. Stronger artifact-aware cold state acquisition needs the existing source owner, not frontend digest claims.

## Reproduce

From this worktree, use `/home/dalab/biomodstack/checkpoint-api-env/bin/python` (default Python lacks pytest_socket). Frontend node_modules is an ignored symlink to existing locked dependencies; no dependency files changed.

```sh
mkdir -p .artifacts/results
BMS_RESULTS_POPULATION_WIRE="$PWD/.artifacts/results/populations.json" BMS_RECEIVING_EVIDENCE="$PWD/.artifacts/results" /home/dalab/biomodstack/checkpoint-api-env/bin/python -m pytest platform/api/tests/test_denovo_results_populations.py platform/api/tests/test_complete_receiving_exports.py platform/api/tests/test_denovo_results_receiving.py -q -s
```

From `platform/frontend`:

```sh
BMS_RESULTS_POPULATION_WIRE="$PWD/../../.artifacts/results/populations.json" BMS_RECEIVING_PAGE="$PWD/../../.artifacts/results/emitter-reader-partial.json" BMS_RECEIVING_EVIDENCE="$PWD/../../.artifacts/results" node_modules/.bin/vitest run --config vitest.md.config.ts tests/vitest/deNovoV15ResultsPopulations.test.tsx tests/vitest/completeReceivingExportsMounted.test.tsx tests/vitest/nativeGenerationWorkbenchMounted.test.tsx tests/vitest/exactContinuationReturnMounted.test.tsx tests/vitest/nativeGenerationCohortMounted.test.tsx tests/vitest/denovoMdReviewRepairMounted.test.tsx tests/vitest/shapeResultsCompletionMounted.test.tsx
node_modules/.bin/tsc -b --pretty false
```
