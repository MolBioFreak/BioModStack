# BioXP method compiler v1

`bioxp_method_model` exposes `method_schema()`, `method_catalog()`, `method_examples()`, `validate_method(raw)` and `migrate_legacy(raw)`. `bioxp_method_compiler.compile_method(request)` is pure and takes `{method, bindings, dependencies, initial_state?}`. No network, persistence, scheduler or device owner is imported.

## Raw versus executable

Save/Open validation only checks the discriminator, top-level arrays and finite JSON. Unknown/incomplete fields, numeric spelling, explicit null, false and zero persist. v1/v2 migration keeps `legacy_original` plus every original top-level and row field; legacy intents become `native_intent` actions. Original capability presence is retained.

Compile validates selected executable values and returns no document on any representation error. Unsupported actions never become notes; valid prefixes never become executable whole methods. Errors are not hardware-readiness findings. Simulation remains advisory, including incomplete initial state or unavailable simulation integration.

## Expressions and scope

Expression envelope: `{expr:{version:1,op,...}}`. Operators: literal (`value`, optional `unit`), param/arg (`id`), loop_index/loop_item, add/sub/mul/div/eq/ne/lt/le/gt/ge/and/or/not (`args`). No Python/JavaScript eval or runtime sensor references. Ordinary strings such as `$param.x` are just strings. Only the selected `if` branch evaluates action inputs. Both branches remain in the raw snapshot.

Parameters declare `id,type`, optional `label,unit,default,minimum,maximum,choices`. Types: number, integer, boolean, string, array, object. Defaults are only authored defaults. Bindings distinguish missing/null/false/zero. Numeric strings are accepted at numeric boundaries, never Boolean coercion. Procedure arguments are lexical and do not see caller arguments. Method parameters are visible within procedures; loop references address the nearest enclosing loop and do not leak into called procedures (pass them explicitly as arguments). Duplicated list items have distinct zero-based occurrence indices.

Units normalize to uL, s, degC, uL/s, mm and controller steps; mL/L, ms/min/h, um and mL/s convert exactly in Decimal arithmetic. Calculation uses 28 significant digits and ROUND_HALF_EVEN. Quantity JSON is `{value:<normalized decimal string>,unit:<base unit>}` in resolved output. Native manual emission converts only known volume/speed/delay/step fields to that field's units. Addition/subtraction/comparison require matching dimensions. Quantity division by a matching quantity produces a scalar; multiplication of two quantities is not in v1. No dimensional guesses from field labels.

## Finite lowering and identity

Inline procedures and embedded `dependencies.procedures` are captured content; duplicate IDs and recursive cycles are representation errors. Count pass precedes occurrence allocation. Limits: depth 64, 10,000 occurrences, 100,000 native actions, 200,000 traversal visits per traversal class; decimals capped at 1,000 coefficient digits/exponent magnitude. These protect compiler resources, not hardware readiness. Empty overall expansion reports `empty_expansion` because the native parser requires a nonempty stage; zero-count subgroups emit nothing.

Occurrence IDs derive from stable step/group/call identities and ordered loop indices, not random run IDs. JSON-pointer paths separately locate the source and may change on reordering. Every generated native child carries occurrence metadata and is listed in provenance. Transfers remain eight native children per ordered well pair, not atomic operations.

SHA256 covers canonical UTF-8 JSON (sorted keys, no whitespace) of semantic method, bindings, dependencies, initial state, compiler version and emitted document. `editor_state` is excluded recursively. Raw numeric strings remain strings in the semantic snapshot; their spelling is intentional raw content. Executable Decimal values normalize to base units; native numbers follow the existing emitter. Saved raw snapshot is added after content hashing, so presentation-only changes do not alter the digest. Native protocol identity derives from the digest; execution idempotency belongs to the existing API relay.

## Native scope and remaining integration

Manual intent schema and Transfer use `bioxp_workflow_authoring` directly. Checkpoint lowers to native `pause_review`; note lowers to native `note`, the existing executor host-only cases. Checkpoint is ordinary review, never failed-action replay. `pause_for_operator` failure policy remains a precise integration-required error until native failure-hold support is integrated. Non-manual tip policies similarly do not invent pickup/ejection coordinates.

The complete palette enumerates §5 mechanisms, but marks missing emitters honestly. Pending entries are not complete typed native bindings: thermal holds/profiles, standalone timers, custody/door adapters, camera/barcode, coordinated fluid search, seal mechanisms, expanded Cavro settings and classifiers need the runtime/Cavro contract. Pinned original manual schemas remain available in `native_definitions`. No field claims applied status from a successful compile.

Simulation module is discovered when integrated and receives resolved occurrences. Transfer channel/material effect lowering and liquid-class resolution/application glue are not implemented in this P1 commit; Water substitutions therefore remain empty, never invented. Automatic tip policies need P2's planner plus native pickup/ejection mapping. These are integration work, not new runtime refusal policies.

## Examples and tests

Eight unbound examples cover CFPS, protein purification, combined CFPS/purification, Gibson, Golden Gate, PCR, DNA and RNA purification. Every example includes preparation through collection/QC, operator separation/external equipment stages and integration-needed thermal/time stages. They deliberately require authored input objects instead of invented science. The catalogue marks their current emitter limits; they are not physical demonstrations.

Run from platform/api:

```
uv run --frozen --group dev python -m pytest tests/test_bioxp_method_compiler.py tests/test_bioxp_deck_authoring_backend.py
```

Set `BIOXP_METHOD_EXPORT` to a scratch JSON path to export actual compiled documents. Run `tests/bioxp_method_native_oracle.py` separately with the pinned robot checkout on PYTHONPATH and PyUSB installed; it imports `tests.z_stop_offline_guard` before the actual native compiler and checks exact payload roundtrip. Do not run it as a subprocess inside API pytest's network/process guard.
