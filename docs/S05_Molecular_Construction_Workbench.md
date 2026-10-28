# Connected molecular workbench — bounded UI/discovery delivery

Base: `60d65adad604ddcba9ed4e220141271131daae84`.
Branch: `task/atomworks-construction-ui-exec-20261027`.

## Delivered

- `MolecularConstructionWorkbench.tsx`: primary independently authored strand ID,
  DNA/RNA, exact 5′→3′ sequence, named-copy and terminal controls; typed residue /
  nick, explicit pairing/register/mismatch, CCD replacement/full atom map and
  atom-qualified covalent-link controls through the existing BioXpSchemaInput.
  Complete typed recipe remains available, not a raw-JSON-only editor.
- `molecularConstruction.ts`: typed recipe/complement/result contracts, existing
  Axios API transport, separately retained incomplete editor/complement drafts,
  durable exact recipe+graph Undo snapshots. No implicit graph POST, second
  strand, T/U conversion, pairing/register, pH calculation or geometry choice.
- Additive `GET /api/molecular-construction/schema` projects the actual Pydantic
  recipe/complement/result JSON Schemas including `$defs`. UI choice lists use
  these schemas; there is no handwritten scientific option catalog.
- Explicit complement calls the existing endpoint only on operator action. A
  returned canonical strand is appended without inferred pairing/modifications.
- Monotonic edit and operation generations reject late graph/complement replies
  across edits, Undo A→B→A and unmount. Editing removes the current graph but
  retains it alongside its exact old recipe in Undo. Unknown imported companion
  and graph fields survive durable callback serialization and snapshot restore.
- Compact actual atom/bond/component status, native component/atom map, charge,
  hydrogen and stereo inspection, complete actual graph/provenance readback.
  Unpositioned graph stays distinct from CIF: no fake coordinates or 3D widget.
  Availability/errors are advisory and cannot disable the parent model launch.

## Parent receiving contract

Named component export and props:

```tsx
import { MolecularConstructionWorkbench } from './MolecularConstructionWorkbench';
import type { MolecularConstructionDraft, ConstructionGraph } from '../lib/molecularConstruction';

<MolecularConstructionWorkbench
  key={parentDocumentIdentityAndLoadRevision}
  initialDraft={retainedConstructionCompanion /* optional */}
  onDraftChange={(draft: MolecularConstructionDraft) => retainAuthoringCompanion(draft)}
  onGraphReady={(graph: ConstructionGraph) => optionalExplicitPreparationReceiver(graph)}
/>
```

`initialDraft` hydrates only at mount. Parent must intentionally remount via key
for another document / Template Load; ordinary callback echoes do not rehydrate
or overwrite a local edit. No initial empty callback is emitted. Every edit,
complement-draft edit, successful graph and Undo emits an independent structured
clone through `onDraftChange`. Persist that companion using the existing parent
Save/Template/Project owner; do not place it in scientific generator params or
source CIF lists. `onGraphReady` is optional and only reports an actual current
unpositioned graph; it does not itself position/adopt anything or promise future
geometry validity. No geometry sibling endpoint is consumed.

The parent must register the existing molecular-construction router. No parent
form, main router, shared test runner, scientific recipe/service or live store
was changed in this branch.

## Verified evidence

Task-owned native interpreter:
`/home/dalab/.hermes/profiles/fresh/cache/scratch/s05-molecular-venv/bin/python`.
Reused the already provisioned native graph environment and existing locked
frontend dependencies; no shared-environment installation.

- Mounted collection: **13 tests, 13 passed, zero failed/skipped** in one file.
  Real React + existing Axios instance use an inert adapter that sends the exact
  serialized Axios payload to the real FastAPI graph/complement routes through
  HTTPX ASGITransport in a subprocess. No live HTTP server/application stores.
  Actual native ssDNA, ssRNA, explicit complement/two covalent components,
  copy/end edits, nick/mismatch intent, native 5CM atoms and editable atom-map
  replay are exercised. Discovery, blank-required drafts, Undo, unknown fields,
  failed requests, late replies and unmount are covered separately.
- API/native collection: **47 tests, 47 passed, zero skipped**: 46 pre-existing
  native chemistry cases plus one new exact-schema discovery test. The native
  suite includes independent RDKit atom/bond reconstruction and stereo checks;
  these are not 47 newly added tests or an additional mounted-suite subtotal.
- Full frontend application TypeScript check and `git diff --check` passed.

Reproduction (from this worktree):

```sh
PYTHONPATH="$PWD:$PWD/platform/api" "$TMPDIR/s05-molecular-venv/bin/python" -m pytest \
  platform/api/tests/test_molecular_construction.py \
  platform/api/tests/test_molecular_construction_discovery.py -q -rs -s

cd platform/frontend
BMS_CONSTRUCTION_PYTHON="$TMPDIR/s05-molecular-venv/bin/python" \
  node_modules/.bin/vitest run \
  --config "$TMPDIR/construction-ui.vitest.config.mts" --configLoader runner \
  --reporter=json --outputFile="$TMPDIR/construction-ui-results.json"
node_modules/.bin/tsc -p tsconfig.app.json --noEmit
```

Private external config is at
`$TMPDIR/construction-ui.vitest.config.mts`, with its cache at
`$TMPDIR/construction-ui-vite-cache`. Its exact include entries are:

```ts
include: ['./tests/vitest/molecularConstructionWorkbench.test.tsx']
```

It uses `environment: 'jsdom'`, `setupFiles: ['./tests/vitest/setup.ts']`,
`esbuild: { jsx: 'automatic' }`, one worker, and the task worktree frontend as
root. Extend a parent's private runner with that include; the shared tracked
`vitest.md.config.ts` is intentionally untouched. The test requires explicit
`BMS_CONSTRUCTION_PYTHON` and fails rather than silently skipping native replay.
Retained scratch evidence: `construction-ui-results.json`,
`construction-ui-vitest.log`, `construction-native-pytest.log`.

Initial replay assertion exposed Pydantic's truthful `nicks: []` readback default;
request equality and normalized result equality are now asserted separately.
The native pytest runner emits an existing asyncio fixture-scope deprecation
warning. No chemistry or execution behavior was changed to satisfy these tests.

## Not claimed

No Studio transaction integration, positioned derivative/CIF adoption, browser
WebGL, parent placement, whole-app route registration, real Project/Template
Save-twice/cold reopen, model-native parser acceptance, inference, deployment,
workers or rentals. Cold JSON companion hydration is exercised, not substituted
for those unperformed parent/integration boundaries. The Amber geometry lane is
independent and not imported or called here.
