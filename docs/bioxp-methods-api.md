# BioXP Methods JSON API

The shared UI/agent facade is `/api/bioxp/methods`. It delegates authoring to the
pure method compiler, persistence to UserTemplate, and execution/observation to
the existing native protocol relay. There is no BMS method scheduler, child queue,
run-state store, automatic physical retry, or preview admission token.

## Discovery and disconnected authoring

- `GET /catalog`: model-owned action/input/status catalog.
- `GET /schema`: `{method: <model JSON Schema>, requests: {...}, results: {...}, openapi: "/openapi.json"}`.
  Requests include the discriminated `ProtocolControlRequest` and native review
  schema; the deployment's `/openapi.json` defines the same HTTP contracts.
- `GET /examples`: model-owned scientific skeletons. Unbound scientific values
  are not runnable recipes or evidence of physical qualification.
- `GET /liquid-classes/starters`: original-source projected class entries; no
  automatic database seed or promotion into live defaults.
- `GET /liquid-classes/source`: original source catalog JSON text, retaining its
  numerical spelling and documentary nulls.
- `POST /migrate` with `{method: <legacy draft>}` delegates lossless v1/v2
  migration to the model owner. This is a pure preview, not a Save or execution.
- `POST /check` and `/compile` accept the same body:

```json
{
  "method": {"schema":"bms.bioxp-method.v1","name":"Authored method","parameters":[],"procedures":[],"steps":[]},
  "bindings": {},
  "dependencies": {},
  "initial_state": null
}
```

Omit `initial_state` to retain omission rather than explicit null. Use embedded,
version-pinned procedure/class/Water/profile content in `dependencies` per the
model schema. The facade never resolves a mutable current class at submission.
To pin a stored class, GET its exact revision and embed its `method` value in the
compiler dependency shape. The stored class ID/revision can accompany that value
as provenance. Editing the saved class afterward cannot change the run snapshot.

Compile results contain `document` (or null), issues with paths/categories,
digest, resolved settings, dependencies, substitutions, provenance and simulation.
Only absence of a complete native document is a representation failure. Advisory
simulation/Water findings do not create an additional acknowledgement or gate.
Check/Compile/Save/Open do not need an initialized robot runtime or mutation lane.

## One revisioned library owner

Collections are `library`, `liquid-classes`, and `presets`. Every collection uses
UserTemplate with `mode=bioxp_workflow`, null model/base-template IDs. The raw
value key is **`method` in all three collections**, including classes/presets.
Discriminators are respectively `bms.bioxp-method.v1`,
`bms.bioxp-liquid-class.v1`, `bms.bioxp-method-preset.v1`.

| Operation | Body / result |
|---|---|
| GET `/{collection}?limit=100&offset=0&search=text` | Bounded array of current revision envelopes (limit 1–500) |
| POST `/{collection}` | `{method, name?, description?}` → 201 envelope |
| GET `/{collection}/{id}` | Exact current raw revision envelope |
| PUT `/{collection}/{id}` | `{method, expected_base_revision, name?, description?}` → new immutable revision |
| GET `/{collection}/{id}/revisions?limit=100&offset=0` | Newest-first revision envelopes |
| GET `/{collection}/{id}/revisions/{revision}` | Exact immutable revision |
| GET `/{collection}/{id}/diff?from_revision=1&to_revision=2` | `{kind:"raw",changes:[{op,path,before?,value?}],...}`; JSON Pointers |
| GET `/{collection}/{id}/export?revision=1` | Portable exact revision envelope |
| POST `/{collection}/import` | Create body or exported envelope; always new identity |
| POST `/{collection}/{id}/duplicate` | `{name,revision?}`; new identity, original raw contents |

Envelope: `{id, revision, name, description, method}`. A new draft starts at 1.
Names follow the existing global UserTemplate name uniqueness rule. Importing a
colliding name is an explicit HTTP 400; supply a different name. Export IDs and
revisions are not overwrite authority on import. Update with a stale/missing base
returns HTTP 409 `revision_conflict`; keep the unsaved draft, read current head,
then merge explicitly. Generic UserTemplate PUT cannot bypass revision checking
or change a revisioned collection discriminator.

Raw blanks, decimal input strings, unknown JSON keys, null, false, zero, omitted
keys and array order survive persistence. Save does not validate scientific
completeness. Existing legacy v1/v2 heads appear as revision 0; an explicit update
to the new schema stores the original raw legacy snapshot at revision 0 in the
same transaction as the first new revision. No frozen migration is rewritten.
Migration 48 adds the append-only `user_template_revisions` table and immutable
UPDATE/DELETE triggers. Run the normal explicit database migration lifecycle as
part of an authorized deployment; request handlers never run DDL.

## Saved and Quick run

Saved runs address an immutable revision, not the editable head:

```json
{
  "revision": 1,
  "bindings": {},
  "dependencies": {},
  "idempotency_key": "operator-selected-original-key",
  "expected_generation": 77,
  "acknowledge_live": true
}
```

POST this to `/library/{id}/runs`. POST `/quick-runs` replaces `revision` with
`method` and otherwise uses exactly the same pipeline. Use the **actual current**
connection generation; 77 above is illustrative, not an authority value. Optional
`initial_state` and `recovery` record explicit assumptions and original-job/
occurrence linkage. Explicit `acknowledge_live` is forwarded to native
`live_execution_ack`; native admission/interlocks remain authoritative.

The response preserves canonical ProtocolJob fields and adds `method_snapshot`.
The snapshot contains raw method, exact bindings/dependencies/initial state,
saved ID/revision (or null), and complete compilation result (including effective
settings, substitutions, digest and provenance). It is also embedded under native
`protocol.document.metadata.bms_method_run` for durable original-job recovery.
The document inside `compilation` is the pre-envelope native document, preventing
recursive snapshot expansion. Subsequent draft/class edits cannot change it.

Native job ID is `protocol-live-` + SHA256(UTF-8 trimmed original key). Clients can
retain it **before** POST. Success is 202 for an active command or 200 for terminal.
A lost HTTP response is not evidence of no admission. Structured submission error
detail includes original job ID/key/generation, snapshot, `native_reason`, and
`delivery`: `not_submitted` only with compiler/no-dispatch evidence, otherwise
`uncertain`. Key/body conflict preserves the native reason; it does not establish
that the original key never moved anything. Do not automatically retry or allocate
a new key. GET the original job. A 404 is not proof of no execution. The facade
sends only one POST per explicit request and never automatically resubmits.
Legacy `/api/bioxp/protocols/submit` also publishes local pre-lease refusal as
`detail.delivery:not_submitted`, `dispatch_state:not_dispatched`, retaining the
original `native_reason`. Dispatched HTTP errors/conflicts are not relabeled.

## Observe, control, report and recovery

- GET `/runs?limit=20&offset=0&search=text` returns canonical `rows` plus `window`.
  Pagination/search is explicitly within the latest **at most 100 native jobs**,
  not a claim of complete robot history. `window.complete_history` is false.
  Exact known job IDs remain addressable outside that listing window.
- GET `/runs/{job_id}?expected_connection_generation=N&observation=true`
  uses the existing compact native observation route. Omit observation for full
  job detail. No per-client server poller is created.
- POST `/runs/{job_id}/control` uses the **existing** native contract:
  `expected_connection_generation`, `expected_ownership_generation`, addressed
  `command_id`, original control `idempotency_key`, and one of:
  `action=pause,mode=ordinary|deferred`; `wake,gate_id`;
  `continue,gate=ordinary_pause|deferred_pause|delaypoint,gate_id`;
  `safe_stop`; `abort`.
- POST `/runs/{job_id}/review` uses those same binding fields plus `stage_id`,
  optional `action_id`, `reviewer`, `note`. Accepted differs from reached.
  Native controls do not require a successful immediately preceding GET.
  Continue is same-job cursor advancement, never failed-action retry/Skip.
- GET `/runs/{job_id}/report` projects the original snapshot, native workflow,
  per-occurrence/native-child outcomes, raw action results, review and events.
  Requested/resolved/emitted settings remain compilation data; reported-applied
  evidence comes only from native results. Missing outcomes stay unknown.
  Creation/update timestamps are not execution clocks: duration remains explicitly
  unknown when the native contract provides no execution duration.
  `duration.action_intervals` reports available native receipt dispatch-to-finish
  clocks separately; these exclude holds and may overlap, so are never summed
  into a whole-run duration. `reported_applied.fields` exposes exact Cavro field
  events, operation indices, source identities, controller evidence and per-channel
  results, plus explicit thermal/timer completion fields. Parent cursor completion
  cannot hide nested failed/pending/uncertain `child_outcomes`.
- POST `/runs/{job_id}/clone` returns the original raw method/bindings/dependencies
  as an unsaved draft. It never reads a mutable library head or submits anything.
- POST `/runs/{job_id}/recovery-draft` accepts `{occurrence?,initial_state?}`.
  Use original occurrence/step/call/loop/native-action identities, not ordinal N.
  Response retains the **whole original draft** and explicit recovery linkage,
  empty automatic-setup/excluded-action lists, and an advisory to author intended
  recovery actions. It does not claim a partial-transfer suffix is self-contained.
  Omitted `initial_state` preserves the original run's authored assumptions (not
  hardware observations); explicit null or a new object replaces them. If both
  snapshot and request omit assumptions, the response retains that omission.
  Keep the response's `recovery` object when explicitly submitting the new run.
  `recovery.occurrence_resolution` reports `matched`, `unmatched`, `ambiguous`, or
  `not_requested` against immutable compiler provenance. Supplied `action_id` or
  `native_action_id` must belong to the same addressed occurrence; all supplied
  provenance components are compared without normalization. Unmatched component
  values/reasons are returned as evidence, not an HTTP refusal or motion gate.
  `original_assumptions` preserves original presence/null/value independently of
  `assumptions_overridden`. `included_occurrences` explicitly lists retained original
  intentions; no suffix slicing, replay or automatic setup occurs.
  Missing original snapshots return `lossless_reconstruction_unavailable` instead
  of reconstructing guessed science or replaying a native prefix.

Optional SSE is not implemented; GET uses the existing observer. No robot
execution authorization, physical qualification, deployment, live migration or
hardware query is implied by these source/API contracts.

## Validation scope

`tests/test_bioxp_methods_integrated.py` exercises actual disconnected discovery,
class source, compiler, file SQLite/UserTemplate revisions/CAS and frozen submit,
GET/control/report/recovery. It replays exact captured UI authoring/run requests;
unbound science remains a representation error. Admission is inert, not hardware.
Unmodified native ASGI/dispatcher/executor/SQLite thermal exports enter the real
HTTP robot client and mounted receiver, including combined robot source 0fa5843.
See `platform/api/tests/fixtures/bioxp_methods/README.md` for producer hashes.

`tests/test_bioxp_methods_api.py` retains focused fault-injection function seams;
these are not counted as native compilation. Constructed partial owned-child
faults use actual compiler provenance but are not native execution proof.
`test_bioxp_methods_close.py` additionally qualifies all eight discovered bound
companions through actual compiler/library/submit/report/recovery, and consumes six
immutable native success/partial/error-hold result exports through the HTTP client.
`native-finish-receiving.json` records producer commit and wrapper/slice hashes;
JSON job value bytes are sliced unchanged, never relabelled with BMS provenance.
These native-authored jobs have no BMS snapshot. Complete actual-execution recovery
still requires native-produced BMS-snapshot partial/held/aborted jobs and control
receipts; BMS mock admission is not that native dispatch proof. Existing protocol/
action/control/connection suites remain regression controls; retired BMS v2 method
relays/types were removed, not ordinary XY actions or addressed Stop workers.
