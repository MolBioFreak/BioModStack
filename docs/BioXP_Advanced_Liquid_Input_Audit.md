# Advanced BioXP liquid input audit

Scope: the retained service catalog in `platform/api/tests/fixtures/bioxp_retained_catalog_v1.json`, checked against robot `src/bioxp/api.py` request types and `src/bioxp/pipette/transport.py::_selected_channels`. This is offline contract/UI coverage, not hardware execution evidence.

Inventory: 30 published `/liquid/` actions, 76 input occurrences. Types: boolean=9, enum=3, integer=13, json=19, number=12, string=20.

## Rendering and ownership

- The catalog owns the action, input keys, scalar limits/defaults and existing admission. The shared `useInvokeBioXpOperatorAction` helper remains the only submission path.
- `OperatorInputSpec.json_schema` is optional, backward-compatible metadata relayed without losing nested values. The robot resolves local references. The UI renders properties, typed enums/constants, lists/items, nullable variants, booleans, numbers, strings and typed additional properties. It does not add schema/history/observation admission gates. Numeric bounds and list bounds remain service-owned.
- Legacy `channels` JSON receives zero-based OEM 0–3 checkboxes. Omission, null and an explicit empty selection are distinct; the controller still decides validity/default channel semantics.
- Legacy `LiquidLocationRequest` receives `location_id`, `well_id`, `plate_name`, `z_offset_steps`. With schema metadata, the same context fields use published types, descriptions and bounds. They do **not** position the robot or implement well transfer. No deck coordinates, geometry, motion ordering or calibration authority is inferred.
- `source`, `destination`, `dest` and mix `location` are preserved independently under their published names. The UI does not duplicate aliases or choose precedence on behalf of the service.
- Metadata and other unstructured legacy JSON use recursive typed object/list editors. Scalars, empty lists/objects/strings, false, zero and null remain typed. Omission removes the key. No JSON serialization/parsing occurs during edits.
- Legacy null defaults remain omitted because the old contract conflates absent and null defaults. A published schema with an explicit `default: null` is retained. Non-null defaults remain visible and editable. Existing required top-level empty/null checks are retained.

## Complete retained liquid inventory

| Route | Published inputs |
| --- | --- |
| `/liquid/application/plan` | `operation` (enum), `tip_tray` (string), `tip_well` (string), `tip_type` (integer), `tip_location` (integer), `home_z_after` (boolean), `fluid_class` (enum) |
| `/liquid/application/status` | None |
| `/liquid/aspirate` | `volume_ul` (number), `pressure_profile` (string), `source` (json), `liquid_class` (string), `tip_id` (string), `air_gap_ul` (number), `operator` (string), `channels` (json), `speed` (number), `metadata` (json) |
| `/liquid/aspirate-air` | `channels` (json), `volume_ul` (number), `front_air` (boolean), `dispense_type` (integer) |
| `/liquid/condition` | None |
| `/liquid/data` | `query` (string) |
| `/liquid/diagnoses` | `number` (integer) |
| `/liquid/dispense` | `volume_ul` (number), `pressure_profile` (string), `blow_out` (boolean), `destination` (json), `dest` (json), `liquid_class` (string), `tip_id` (string), `air_gap_ul` (number), `operator` (string), `dispense_type` (integer), `channels` (json), `speed` (number), `metadata` (json) |
| `/liquid/dispense-air` | `channels` (json), `volume_ul` (number), `front_air` (boolean), `dispense_type` (integer) |
| `/liquid/dispense-all` | `channels` (json) |
| `/liquid/eject-all` | `channels` (json), `check_missing_tip` (boolean), `wait` (boolean) |
| `/liquid/error-log` | `raw_byte` (integer) |
| `/liquid/firmware` | `number` (integer) |
| `/liquid/fluid-detection` | `dry_run` (boolean) |
| `/liquid/fluid-detection/{channel}/timestamp` | `channel` (integer) |
| `/liquid/heartbeat` | `enabled` (boolean) |
| `/liquid/init` | `pressure_profile` (string), `prime_volume_ul` (number) |
| `/liquid/keep-tip` | `tip` (integer) |
| `/liquid/mix` | `volume_ul` (number), `cycles` (integer), `pressure_profile` (string), `location` (json), `source` (json), `destination` (json), `dest` (json), `liquid_class` (string), `tip_id` (string), `operator` (string), `metadata` (json) |
| `/liquid/mix-all` | `count` (integer), `volume_ul` (number), `vigorous` (integer) |
| `/liquid/pressure` | None |
| `/liquid/readback` | `include_data` (boolean) |
| `/liquid/reinitialize` | None |
| `/liquid/requests` | None |
| `/liquid/set-top-speed` | `channels` (json), `velocity` (number) |
| `/liquid/status` | None |
| `/liquid/status/readback` | None |
| `/liquid/terminate` | `operator` (string), `reason` (string), `metadata` (json) |
| `/liquid/tip` | `action` (enum), `tip_id` (string), `operator` (string), `metadata` (json) |
| `/liquid/tip-status` | None |

## Verification and remaining scope

The mounted test iterates every action above, inspects every published input, and submits through the real mutation helper, generation payload and Axios interceptors to an offline transport adapter. Focused interaction tests cover channel selection, all source/well context fields, destination aliases, recursive metadata list edits/removal, schema-driven nested lists, numeric enums, nullable/default preservation, and omission. The API test validates all retained OperatorInputSpec-shaped inputs and a nested schema round trip while retaining strict unknown-envelope-key rejection.

The composed well-transfer/calibration panel and real robot-backed acceptance belong to the parent integration. Arbitrary JSON Schema validation is not implemented in the browser: local refs must already be resolved by the robot; validation and supported physical combinations remain server-owned. This change does not claim all schemas, hardware behavior or live release acceptance are qualified.
