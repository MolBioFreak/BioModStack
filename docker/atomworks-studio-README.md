# Private pinned AtomWorks Studio host (S02 / R03)

This lane supplies a working isolated editor service, BMS private receiving router,
raw-export React wrapper and executable CPU/HTTP/browser tests. It does **not**
change live services/ingress, register the router in `app/main.py`, wire the parent
native source form, or publish scientific sources. Those remain parent-owned.

## Installation and launch

From the repository root, obtain the PyPI wheel
`atomworks_studio-0.0.5-py3-none-any.whl`, then:

```sh
sh scripts/prepare_atomworks_studio.sh "$NEW_STUDIO_VENV" "$STUDIO_WHEEL"
```

The installer refuses to overwrite an existing environment. Python 3.12 and `uv`
are required. It verifies release wheel SHA-256
`da60857d956a65d1b4dfe439326303b0a0c97b6fa520cece96fa02d041d0524d`, all 156
RECORD members, and release commit `04fe5fbd03fea8b7d49a767156d7d33bd10e5d13`.
`docker/atomworks-studio.lock` hash-locks all 62 transitive runtime packages;
CPU Torch is `2.7.1+cpu`. No Gradio extra, model weights or inference runner is
installed. This environment must remain separate from BMS API/model environments.
The installer smoke-imports Torch and the real editor after `uv pip check`.
An interrupted install can leave metadata present but binary files absent;
recreate the task-owned environment, or explicitly reinstall the affected locked
package before trusting it. Never patch uv shared-cache files; installation uses copy.

Configure the **same** random transport secret in both service environments via
their managed secret/environment owner (do not put it into browser code):

```sh
# Isolated editor service environment:
export BMS_ATOMWORKS_STUDIO_SECRET  # already provisioned secret
export ANNOTATION_STUDIO_SESSION_DIR="$STUDIO_SESSION_DIR"
"$NEW_STUDIO_VENV/bin/python" -m uvicorn atomworks_studio_service:create_app \
  --factory --app-dir "$REPOSITORY_ROOT/scripts" --host 127.0.0.1 --port "$STUDIO_PORT"

# BMS API environment:
export BMS_ATOMWORKS_STUDIO_URL="http://127.0.0.1:$STUDIO_PORT"
export BMS_ATOMWORKS_STUDIO_SECRET
```

Choose a task/managed-service-owned unused port; none is a universal default here.
No ingress should ever target this listener directly. All editor paths, including
health, static files and both API forms, require the transport secret and owner.
Parent registration in the existing API app is exactly:

```python
from routers import atomworks_studio
app.include_router(atomworks_studio.router)
```

Existing `/api/` private ingress carries all Studio paths; no new public route or
proxy implementation is needed. The service URL is operator-configured loopback
only, not a browser-controlled URL. Upstream redirects and cookie/cache/identity
headers are not forwarded. Request bodies retain the upstream 32 MiB upload plus
a bounded multipart envelope. Responses, including auth/config errors, are
`private, no-store`; conditional browser cache headers are not forwarded.

## Authority and endpoint contract

`/api/atomworks-studio/` serves the iframe entry page. Private upstream APIs are
available at `/api/atomworks-studio/api/...` and
`/api/atomworks-studio/api/v1/...`; both traverse the **same** BMS authority.
The router reuses `experiment_workspaces._authenticated_principal` and
`molbio_share.browser_mutation_origin`. It does not invent accounts, roles or
per-user gates. The existing trusted proxy secret resolves to shared
`local-application-operator`; that remains a shared operator. If trusted middleware
sets distinct authenticated principals, their hashed owner bindings are isolated
by Studio's existing owner checks. Client identity headers never set that owner.
Tests cover both authority branches; the fixture principal is not a claim that
production currently supplies per-user identities.

- `POST .../api/v1/sessions`: upstream multipart CIF/mmCIF import, with unchanged
  parse/model/assembly/options fields. `importStudio(file, options)` returns
  `{session_id, structure}`. This is a parsed editor derivative, not original custody.
- `GET .../api/v1/sessions/{id}`: current ephemeral document summary.
- `POST .../api/v1/sessions/{id}/export`: one frozen response
  `{cif, name, revision, issues}`. Advisory issues do not create refusal conditions.
- Upstream edits, Undo, Close and the rest of the editor API are forwarded without
  scientific reserialization. Session IDs are ephemeral, not durable source IDs.

The released JS transport function receives one exact, fail-on-drift path-prefix
patch; the entry page receives upstream's worker opt-out. All other packaged
scientific/editor assets remain released bytes. This is **not** the separate S03
upstream repair patch set. Coordinate a rebuilt patched wheel and transport patch
with its owner before substituting one; the digest and exact asset transform must
not silently accept an unrelated build.

## Parent UI / custody contract

`components/AtomWorksStudioHost.tsx` exports named `AtomWorksStudioHost`, a
`forwardRef<StudioCapture, AtomWorksStudioHostProps>`. Props:
`sessionId: string`, optional `onDirty(): void`, `onReady(): void`.
`lib/atomworksStudio.ts` exports:

```ts
interface StudioCapture {
  capture(): Promise<StudioSnapshot>;
  generation(): number;
}
interface StudioSnapshot {
  session_id: string; cif: string; name: string;
  revision: number; issues: Array<Record<string, string>>;
}
```

`capture()` POSTs `/export` exactly once and returns the exact decoded CIF text;
`new TextEncoder().encode(snapshot.cif)` gives its UTF-8 bytes. It never subsequently
GETs `/cif`, persists a session URL, strips annotations, or adopts a source.
The parent can invoke it from Save/Template/Project/Generate, adopt **those exact
bytes** through its existing immutable source owner, then serialize its own native
request. No automatic export is triggered by a notification.

`generation()` is monotonic **mount-local** session/edit identity, not the native
revision. Validated changed/example events increment it and invoke `onDirty`;
ready events only invoke `onReady`. Origin, actual iframe `contentWindow`, payload
source `atomworks-annotation-studio` and the selected `session_id` are independently
bound. The iframe cannot silently switch the selected parent document. Session
A→B→A/unmount invalidates old capture completion. Concurrent edits do not rewrite
an already returned snapshot; parent scope/generation controls later adoption.
Successful export does **not** clear parent dirty or announce persistence. Export
failure shows a recoverable unsaved-edits alert; failed persistence remains
parent-owned dirty state and must not claim Save succeeded.

## Notices and receipt

`share/atomworks-studio/receipt.json` records installed versions/requirements,
modified asset hashes, release identity and full copied notice hashes. Installed
license/LICENCE/COPYING/NOTICE/COPYRIGHT files are collected for every distribution.
The released Cython wheel omits full texts, so its Apache license/COPYING are
supplied from digest-verified 3.3.0 source (provenance in
`docker/atomworks-studio-notices/SOURCES.json`). The Patinae full BSD notice omitted
from the compiled wheel is included under `notices/supplements/`. JS third-party
notice assets remain in the verified Studio wheel. Python interpreter, OS libraries,
build tooling and a final container image are not digest-pinned by this lock;
this is not a complete OS SBOM/legal audit.

Worker opt-out plus `worker-src 'none'` and blocked worker-script requests prevent
new Studio worker registration; tests preserve an unrelated BMS cache. This does
not migrate a previously installed origin-wide standalone Studio worker. Do not
claim legacy worker/cache migration or production-ingress acceptance from these tests.

## Reproduce qualification

Use the API's locked dev environment to run the root-owned cross-process tests;
no API conftest socket bypass or BioXP/live-service access is used:

```sh
(cd platform/api && uv sync --frozen --group dev)
STUDIO_TEST_PYTHON="$NEW_STUDIO_VENV/bin/python" STUDIO_RELEASE_WHEEL="$STUDIO_WHEEL" \
  platform/api/.venv/bin/python -m pytest tests/test_atomworks_studio_http.py \
  tests/test_atomworks_studio_package.py -q --basetemp "$SCRATCH/studio-http-tests"

pnpm install --frozen-lockfile --ignore-scripts
(cd platform/frontend && pnpm exec vitest run --config vitest.md.config.ts \
  tests/vitest/atomworksStudioMounted.test.tsx && pnpm exec tsc -b)
```

The browser case is explicit opt-in and otherwise skipped. For its separate test
instrumentation (not runtime dependencies), provision `playwright==1.58.0` into
`$SCRATCH/studio-browser-tools` and an existing Chrome binary, then:

```sh
uv pip install --python python3.12 --target "$SCRATCH/studio-browser-tools" playwright==1.58.0
BMS_STUDIO_BROWSER=1 BMS_STUDIO_CHROME="$CHROME" PYTHONPATH="$SCRATCH/studio-browser-tools" \
  STUDIO_TEST_PYTHON="$NEW_STUDIO_VENV/bin/python" STUDIO_RELEASE_WHEEL="$STUDIO_WHEEL" \
  platform/api/.venv/bin/python -m pytest tests/test_atomworks_studio_http.py \
  tests/test_atomworks_studio_package.py -q --basetemp "$SCRATCH/studio-browser-tests"
```

The fixture starts/stops only its own loopback listeners and uses scratch session
storage. Real Chrome loads the released iframe through the actual BMS receiving
router, imports a bundled unpositioned document, applies a real editor `applyEdit`,
uses the real Undo button, exports and checks worker/cache isolation. This is not
3D atom-picking/WebGL overlay acceptance: the fixture document has no positioned
atoms. React mounted tests separately exercise host notifications, spoof rejection,
raw export, recovery and A→B→A staleness. Full frontend typecheck/build also runs;
the wrapper is intentionally not yet imported by the parent production form.

Upstream exports regenerate `_entry.date/time`; semantic Undo tests ignore only
those serialization timestamps. Raw captured/adopted bytes are never normalized.
Missing CCD/PDB mirror environment warnings did not block this bounded bundled
fixture; broad chemistry/offline cache support remains unqualified.

## Exact unperformed boundaries

- Parent router registration, NativeSourceRow/context integration, actual
  Save/Template/Project/Generate custody and cold-reopen acceptance.
- S03 repaired/rebuilt upstream wheel integration; released upstream defects are
  not declared fixed by this transport lane.
- Positioned WebGL/WebGPU rendering, atom/frame/overlay picking, arbitrary chemical
  import and broad offline CCD/PDB cache behavior; legacy worker migration.
- Native RFD4 featurizer/encoding semantic parity: Studio bundle pin
  `e519afe329a0963172747e9f71e843a21b6995e8` differs from BMS native
  `8f7f702e46f944713fbe8ff5c566221aaa54eb0b`; prior four-of-five byte comparison and
  `conditions.py` encoding-import discrepancy are retained, not resolved here.
- Real private ingress/deployment, managed-service installation/restart, final OS
  image/legal audit, inference or enzyme-function validation. No push, deployment,
  live listener change, weights, rentals or scientific run was performed.
