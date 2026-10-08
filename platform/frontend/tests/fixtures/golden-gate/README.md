# Golden Gate raw UI native fixtures

`BbsI-*` and `SapI-*` are complete synthetic request/results copied unchanged from the committed first-wave core owner's executed examples (core commits `f73ae6678439eee9a1be43664a983f4952b196f8` and `58e4eea71f9934f5f01274031dca7776ed318483`). `published_11.json`, `bounded_search.json`, and `circular_windows.json` are actual first-wave fidelity outputs from `6329d0e57dcc1cb3cc475c8f39ad7ce567de8a95`, not fabricated backend responses. The fidelity reference is Pryor et al., DOI `10.1371/journal.pone.0238592`, pair-pooled metric v1.

`nativeDraft.ts` provides explicit test-only supplemental settings/templates. Production components do not import test fixtures or silently use these recommendations.

The registered mounted test is `tests/vitest/molbio-sanity-golden-gate-raw.test.tsx`, covered by the existing `molbio-sanity-*.test.tsx` allowlist. Setting `BMS_GG_UI_EVIDENCE` writes the actual edited component callback arguments into a task-owned directory. These outputs are then checked by `verify_native.py` against the checked-in design schema and real native models. The script also executes native BbsI/SapI design and compares the *entire* result to these fixtures, executes reaction/scoring/search with the mounted edited settings, and freshly reproduces the score/search viewer fixtures.

From `platform/frontend`:

```sh
BMS_GG_UI_EVIDENCE="$SCRATCH/emitted" BMS_SCIENTIFIC_VITE_CACHE="$SCRATCH/vite-cache" \
  node node_modules/vitest/vitest.mjs run --config vitest.md.config.ts \
  tests/vitest/molbio-sanity-golden-gate-raw.test.tsx
```

Then from `platform/api`, with the locked scientific interpreter, `PYTHONPATH` containing both repository root and API directory, and all BMS/database/artifact/cache/temp paths configured to **existing private scratch directories**:

```sh
unshare --user --map-root-user --net "$SCIENTIFIC_PYTHON" \
  ../frontend/tests/fixtures/golden-gate/verify_native.py "$SCRATCH/emitted"
```

This is leaf UI/native-contract qualification, not routed API/persistence/Project/Development acceptance. Immutable-revision and origin-wrapping callback artifacts receive native schema/model validation; they are not claimed as authorized database resolution or successful physical assembly. No native output is replaced with a mock to pass the contract script. API import may print an unrelated GPU metadata diagnostic; no GPU work or application lifespan is invoked.
