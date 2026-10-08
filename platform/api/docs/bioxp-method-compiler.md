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

## Native integration

Manual intent schema and Transfer reuse `bioxp_workflow_authoring`. Checkpoint is native `pause_review`, not failed-action replay. Native `on_error` is inherited through groups/repeats/calls and emitted on each child. Consecutive structural call/group/loop occurrences retain native stage boundaries. Generated tip and repeated-single compound children retain parent/child provenance. Tip planning precedes physical lowering so pickup cannot invalidate an already-positioned transfer.

The disconnected native contract is in `bioxp_method_native`; its source revision is captured in `schemas/bioxp_method_native.json`. It maps waits/timers, thermal setpoint/hold/profile, chillers, snapshot versus cover-moving inspection, illumination/barcode, plate/cover movement, prepare/catch/release/press/cut and thermal door. Opening the thermal door has the source pipette initialization side effect. `fluid_search`, `pipette_settings` and `pressure_stream` construct typed ApplicationRequest instructions. Application and Recipe schemas are exported from the real native classes, not copied robot execution code. Scientific numeric strings normalize only at typed numeric fields.

`liquid_recipe` inputs contain `recipe` and optional `liquid:{requested,context,water,liquid_class,setting_phases}`. Class/Water is an embedded revisioned entry or an ID resolved in `dependencies.liquid_classes`. Exact matching omission-only resolution happens before native lowering. Defined source fields map explicitly to native recipe fields; slope/pressure fields require an explicitly chosen setting phase. Requested null/invalid/unmapped executable settings never become Water. Missing source values are advisory unknowns, never manufactured defaults. A source dash explicitly reporting no first segment remains in the resolved ledger but does not invent a stroke. Recipe-required gaps remain exact representation errors, not executable prefixes.

`resolved.liquids` and top-level `water_substitutions` retain occurrence identity and requested/resolved/emitted/applied separation. Applied remains unknown until a native result. Actual emitted fields record normalized values, native paths and action IDs; an unsuccessful whole compilation reports not_emitted. Run-document metadata retains class/Water provenance and original assumptions. Native `pipette_manual_physical` operation `cavro_liquid_recipe` must call the native `compile_liquid_recipe` and execute the resulting application under its single finite owner; this compiler deliberately does not expand that recipe a second time. Registration/connected runtime evidence is owned by the native integration lane.

`distribute` and `consolidate` accept explicit `mode:repeated_single,transfers:[...]`. This is never presented as true multi. True multi distribute uses `mode:multi,recipe:<complete multi Recipe>` with explicit total corrected aspiration, conditioning-return count, aliquots, excess destination/retention, reaspiration delay and optional final empty. Full native application instruction sequences remain available for other authored compound phase orders.

Signed channel accounting uses `dependencies.labware_profiles[].native_addressing:{row_increment,column_increment,reference_channel}` and stable `deck_plan.labware[].id`; a station is not a labware ID and selected plungers are not independent XY. Explicit channel transfers can be authored for recipe accounting. Unknown geometry/material/fill stays advisory. Deck assignments seed planned state; explicit initial assumptions take precedence. Each transfer aspiration/dispense child records its separate planned source/tip/destination effect. Native emitted positions drive simulated-after-occurrence head references; this is not readback. Simulated duration includes known waits/dispatch holds and leaves unknown timing portions unknown.

Remaining source/integration gaps are discoverable rather than replaced by notes: original-ADP start/cutoff mapping; dedicated Park/source seal-separation/pierce/status-light mappings and native multi-pose barcode/fan/ramp additions need the corresponding delivered native contract. Existing source diagnostics/plunger controls remain available through `diagnostic_pipette`. No physical qualification is claimed.

## Examples and tests

Eight unbound process skeletons remain available, each with a separately labelled `bound_fixture` companion containing explicit software-test bindings/dependencies. Fixtures include reaction transfer, positioned/lowered mixing, thermal/timed processing, operator external separation and final collection/QC. Their numbers and signed geometry are BMS test choices, not scientific defaults, native calibration or recommended PCR/chemistry recipes. No parameter gains a default. Original unbound requests still report missing bindings.

From platform/api:

```
BIOXP_METHOD_FINISH_EXPORT=<scratch>/documents.json uv run --frozen --group dev python -m pytest -s tests/test_bioxp_method_finish.py tests/test_bioxp_method_compiler.py tests/test_bioxp_method_liquids_simulation.py tests/test_bioxp_deck_authoring_backend.py
```

Run `tests/bioxp_method_native_oracle.py <scratch>/documents.json` separately with the pinned robot root/src on PYTHONPATH and native dependencies installed. It imports the offline hardware guard, verifies exact native document roundtrip and invokes actual native Application/Recipe compilers. `tests/export_bioxp_method_native_schema.py <asset-path> <full-native-commit>` regenerates the disconnected data-only export under the same guard. Parser/compiler qualification is separate from native provider execution and physical science.
