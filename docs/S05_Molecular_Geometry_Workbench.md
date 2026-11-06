# Explicit optional geometry workbench

The existing `MolecularConstructionWorkbench` now exposes **Configure optional
geometry**, followed by a separate **Create new idealized geometry** action.
Opening controls discovers `GET /api/molecular-geometry/schema`; options come
from the actual request Pydantic schema. Operation, provider, form, topology and
hydrogen policy start unselected. No scientific choices are filled on discovery.
The POST includes exactly those authored choices plus the current construction
editor; the existing native route remains responsible for validation and science.
No complement, pairing, duplex, coordinates or source adoption is synthesized.

Visible scope distinguishes canonical antiparallel dsDNA A/B and dsRNA A form,
hydroxyl ends, neutral-acid graph and heavy-atom output from unsupported ss,
modified, mismatched, hybrid, nicked or overhung constructions. Errors are advisory
and do not modify imported geometry or parent launch behavior. Graph-only use
requires neither geometry discovery nor construction.

## Durable parent interface

`MolecularConstructionDraft.geometry?: MolecularGeometryDraft` carries:

- `choices`: incomplete typed request choices, preserving unknown imported keys;
- `request`: last exact submitted request (explicitly labeled potentially historical);
- `result`: current native result, with exact ASCII PDB, SHA256, byte size, atom map,
  separate connected graph, normalized native request and provenance.

Keep this in the parent's shared authoring companion, not scientific parameters.
`initialDraft` remains mount-only hydration; deliberate document/template loads
must remount by key. Cold hydration performs discovery only and emits no callback.

`onPositioned?: (result: MolecularGeometryResult) => void` reports a successful
current construction, **not adoption**. It is not emitted on hydration or Undo.
The parent owns explicit immutable-source adoption and source selection.

Recipe and geometry-choice edits remove the current geometry result and preserve
exact prior request/result and unknown fields in durable Undo snapshots. Undo
restores the historical snapshot, never applies a pending response. Monotonic edit
and operation generations reject late responses across A→B→Undo A, newer requests
and unmount. A newly requested construction also snapshots the previous result.
Changing only geometry choices preserves the independent unpositioned graph.

## Executed evidence

Private runner (shared tracked runner untouched):
`$TMPDIR/geometry-ui.vitest.config.mts`; exact includes:

- `./tests/vitest/molecularConstructionWorkbench.test.tsx`
- `./tests/vitest/molecularGeometryWorkbench.test.tsx`

Actual mounted collection: **21 passed, zero failed/skipped** (13 existing graph,
8 new geometry). Real React/Axios serialized requests replay through inert HTTPX
ASGI subprocess transport to actual routes, including real AmberTools execution.
Delayed/error responses are explicit lifecycle test interception, not native
coordinate substitutes. New tests cover schema-derived blank choices and scope,
exact request/PDB/hash/map/callback, unknown-field JSON cold hydration, exact Undo,
incomplete-native-422 with unchanged graph path, optional runtime/discovery errors,
A→B→Undo A, old-response-last and unmount.

Evidence: `$TMPDIR/geometry-ui-results-final.json` and
`geometry-ui-vitest-final.log`; runner exit **0**. An initial private config omitted
the pre-existing construction runner's 60-second native setup-hook budget and hit
the Vitest 10-second hook default before tests ran. That failed attempt remains in
`geometry-ui-results.json`. Final runner restores only that established setup-hook
budget; individual tests retain ordinary Vitest deadlines (no testTimeout override).

Native regression: **86 passed, zero skipped**, exit **0**, including all 39 native
geometry, 46 graph and one graph-discovery cases. Evidence:
`$TMPDIR/geometry-ui-native.xml`. Existing asyncio fixture-loop deprecation warning
only. Full application TypeScript and `git diff --check` exit **0**.

Environment reused without installations:
`$TMPDIR/s05-molecular-venv/bin/python`,
`BMS_MOLECULAR_GEOMETRY_BUNDLE=$TMPDIR/s05-geometry-bundle-qualified`, and existing
locked frontend node_modules through a worktree symlink. Here `$TMPDIR` is
`/home/dalab/.hermes/profiles/fresh/cache/scratch`.

Reproduce from worktree frontend:

```sh
BMS_CONSTRUCTION_PYTHON="$TMPDIR/s05-molecular-venv/bin/python" \
BMS_MOLECULAR_GEOMETRY_BUNDLE="$TMPDIR/s05-geometry-bundle-qualified" \
node_modules/.bin/vitest run --config "$TMPDIR/geometry-ui.vitest.config.mts" \
  --configLoader runner --reporter=json --outputFile="$TMPDIR/geometry-ui-results-final.json"
node_modules/.bin/tsc -p tsconfig.app.json --incremental \
  --tsBuildInfoFile "$TMPDIR/geometry-ui.tsbuildinfo"
```

Native regression from worktree root:

```sh
BMS_MOLECULAR_GEOMETRY_BUNDLE="$TMPDIR/s05-geometry-bundle-qualified" \
PYTHONPATH="$PWD:$PWD/platform/api" "$TMPDIR/s05-molecular-venv/bin/python" -m pytest \
  platform/api/tests/test_molecular_geometry.py \
  platform/api/tests/test_molecular_construction.py \
  platform/api/tests/test_molecular_construction_discovery.py -q -rs -s \
  --junitxml="$TMPDIR/geometry-ui-native.xml"
```

No parent form, source-adoption owner, API main, scientific service, managed image,
live API environment, shared runner, launch, deployment or inference was changed.
JSON cold hydration is not whole Project/Template/Studio persistence acceptance.
