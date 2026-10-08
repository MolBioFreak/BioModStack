# Golden Gate normalized wire pairing

`wire-receiving.json` contains real ASGI bodies from `test_golden_gate_wire_receiving.py` (`aliases` and `split` fixtures). It is not an invented server response. `wire-browser-requests.json` contains the actual serialized Axios requests captured by `molbio-sanity-golden-gate-wire.test.tsx`. The mounted test pins those requests, and the native test replays them into real science/SQLite/export/import owners. Scratch operation UUIDs are opaque fixture identities; a new server instance compares scientific state, not those generated UUIDs.

Regeneration: run the native test with `BMS_GG_WIRE_EVIDENCE=<scratch directory>`. Assemble the aliases fields `request, preview, normalized, save_request, saved, saved_normalized, document, imported` plus `split: {preview, normalized}` from the emitted files. Run the mounted wrapper test with the same evidence environment to capture outbound requests. Native tests must then replay those captures successfully before replacing fixtures. Preserve expected full JSON separately from the normalized bodies so expansion is not its own oracle.

Run Node tests with `tsx --test tests/goldenGateWorkflowWire.test.ts`, mounted tests with `vitest run --config vitest.md.config.ts tests/vitest/molbio-sanity-golden-gate-wire.test.tsx tests/vitest/molbio-sanity-golden-gate-workflow.test.tsx`, and native tests with the repository's locked interpreter and scratch/offline environment. Both TS projects must typecheck. Parse fixture JSON as raw text in Vite: object-literal transformation treats `__proto__` differently from real HTTP JSON.

## Wire interface

Native helpers in `services.assembly.golden_gate_workflow_wire`:

- `project_workflow(payload: dict, kind: WireKind) -> WorkflowWire`
- `expand_workflow(wire: WorkflowWire | dict, kind: WireKind) -> dict`
- `WireKind = Literal['request', 'result', 'save', 'saved', 'portable']`

Inputs are native JSON (`model_dump(mode='json')`); helpers never mutate caller data. After expansion, the receiver **still validates its existing native DTO**. The wire model supplements rather than forks native science schemas. A batch owner can project each native result with kind `result` and expand each child request with kind `request`; batching does not require changing the scientific owner. This version's table scope is one envelope, not a cross-request cache or a batch-wide global store.

TypeScript equivalents in `goldenGateWorkflowWire.ts`:

- `projectGoldenGateWire(value: unknown, kind: WorkflowWireKind): WorkflowWire`
- `expandGoldenGateWire<T>(value: T | WorkflowWire, kind: WorkflowWireKind): T`

Envelope: `{schema_version:'bms.golden-gate-wire.v1', kind, payload, sequences:string[], materials:object[]}`. Declared DNA positions contain `{sequence_ref: integer}` into `sequences`. Candidate `design.materials` entries contain `{material_ref: integer}` into `materials`; their DNA uses the sequence table. The material table shares only exactly equal complete state, including ID, stage, transformation, parent, ordered mappings, ends and features. Equal DNA can share text storage but never merges identities or distinct molecular states. Arbitrary qualifier/scoring JSON is opaque and is never recursively interpreted as references. References are single-key objects with valid nonnegative indices; malformed/dangling references are wire syntax errors, not scientific admission policies.

Existing raw endpoints accept either full native bodies or this envelope. Design/Save/GET/import responses default to `view=full`; `view=normalized` opts in. Preview export accepts either full or normalized result; ZIPs remain full and self-contained. Existing browser wrappers now project all relevant outbound requests and request normalized responses while preserving their Axios response and frozen-selection/clone interfaces. No new request, pre-Save, persistence/cache/store or science execution is added.

Qualification scope: native ASGI, offline scratch SQLite, fresh-process normalized GET, real native ZIP/import, Node and mounted jsdom/Axios pairing. This is not a deployed browser waterfall, WAN transfer/latency measurement, batch integration, or all-38-case acceptance.
