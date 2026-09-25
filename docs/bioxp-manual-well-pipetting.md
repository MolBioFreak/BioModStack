# Manual well pipetting in BMS

The cockpit's **Well pipetting** panel authors native actions through the existing
`useSubmitBioXpProtocol` → `POST /api/bioxp/protocols/submit` → robot
`POST /protocol/execute` owner. It does not introduce a scheduler, direct motion
route, automatic preparation or a second admission authority.

## Operator controls

- Block choices use the catalog's canonical **locationID**, never a target-name
  ordinal. Explicit integer location entry is also available when catalog
  observations are missing. PositionTable revision is shown as evidence only.
- The A1–H12 grid selects the source head reference. **Move now** submits
  `pipette_position` with `operation=move`, selected location, well and explicit
  source Z flag (0 pseudo-home, 1 calibrated high, 2 calibrated low).
- **Lower now** submits in-place calibrated zLow. **Lift now** submits in-place
  calibrated zHigh (`height_steps=null`) or explicit zLow minus integer steps,
  including zero. Neither performs implicit XY positioning.
- Aspirate/Dispense use explicit volume, native speed and plunger channels.
  Mix expands explicit cycles into alternating native aspiration/dispense
  actions. It is not OEM scientific `mmix` or `mixAll`.
- No scientific values or channels are preselected. The operator chooses the
  location, well, positioning mode, lift target, volume, speed and cycle count
  needed by the selected operation.
- Append snapshots the current typed step without execution. The ordered list
  supports copy-to-editor, removal and reordering. Build a transfer explicitly
  as Move/Lower/Aspirate/Lift/Move/Lower/Dispense/Lift, then **Run ordered steps**.
- Advanced published controls remain available in the existing typed catalog
  UI. Its nested renderer resolves local `$defs` lazily, retains recursive
  schemas, and selects discriminator branches without submitting schema data.

## Alignment and scope

Plunger channel selection does **not** change source TipLocation, load tips or
move the head. With four tips, the selected well is the source head reference;
other channels retain fixed spacing. This panel does not establish current tip
presence/alignment, nor does the grid prove every address is usable at every
station. Existing source routing, calibration and controller interlocks remain
with the robot. No missing observations or historical receipts become a new
UI admission gate. Only this panel's in-flight submission is duplicate-reserved.

No implicit initialization, fluid detection, piercing, lid movement, tip loading,
sweeping, cleanup or Park is appended. Failures are displayed; no uncertain POST
is automatically replayed. A later click is a new explicit idempotent intent.

## Shared contracts and qualification

`src/lib/bioxpManualPipetting.ts` exports the typed authoring request/step union,
native action kinds and compiler for human/agent reuse. The BMS Python protocol
submission and job models already carry native documents as lossless JSON within
closed execution envelopes. There is no BMS native-action enum to extend:
`pipette_position`, `pipette_aspirate` and `pipette_dispense` pass unchanged to the
robot compiler. Do not narrow this existing boundary with a second native schema.
Tests verify strict outer fields while preserving native params/null/zero.

Mounted tests exercise the real controls, shared submit hook and Axios adapter.
The route bridge sends the mounted request through the real BMS FastAPI route,
strict response consumer and real robot HTTP client with `httpx.MockTransport`.
It covers accepted/failed completion and source interlock refusal. The cockpit
mount separately proves catalog ID wiring and independence from historical
availability. No listener or robot connection is started.

Run from `platform/frontend` with the locked dependencies installed:

```sh
BMS_TEST_PYTHON=/absolute/path/to/locked/api/.venv/bin/python \
BIOXP_MANUAL_UI_EXPORT=/path/in/task-scratch/manual-ui-requests.json \
./node_modules/.bin/vitest run --config vitest.md.config.ts \
  tests/vitest/bioxpWellPipettingMounted.test.tsx \
  tests/vitest/bioxpOperatorTypedInputsMounted.test.tsx \
  tests/vitest/bioxpCockpitAdmissionFanoutMounted.test.tsx
./node_modules/.bin/tsc -b --pretty false
```

The export is test evidence (synthetic catalog/calibration context), not a runtime
preset or physical proof. Robot-side offline validation can consume each
`requests[].request.document` using its ordinary native validator and manual
position/liquid validators. No deployment, restart, push, robot contact or
physical liquid accuracy is claimed.
