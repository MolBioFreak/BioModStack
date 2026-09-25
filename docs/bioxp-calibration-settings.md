# BioXP calibration settings and manual physical actions

The cockpit includes manual native `load_tip` (tray 1–5, well A1–B12,
explicit overpress/lift_z) and `measure_fluid_height` (integer speed, source
default 300). Both compile to `pipette_manual_physical` in an ordinary native
protocol, alone or in an ordered program. Pickup is physical XY/Z/query, never
software-only `/liquid/tip` assignment. Height measurement acts at the current
location, without automatic XY; it is not the full Detect Fluid wizard and
never writes calibration. No historical receipt or missing-observation locks
are added. Existing robot admission and OEM/controller interlocks remain.

`GET/PATCH /api/bioxp/calibration-settings` relay only the fixed robot path
`/motion/oem/calibration_settings`, with existing mutation authorization and
connection-generation leases. PATCH has a closed native mirror schema:
`positions: [{name, x?, y?, zLow?, zDelta?, inc_factor?}]`, strict signed Int32,
no null, duplicate names or empty rows. `expected_connection_generation` is
BMS-only and stripped before robot transport. The parity test compares the
schema with the source-generated schema. Responses are deliberately lossless:
no generic invoke output truncation, source provenance removal or second
calibration projection in the relay.

The station selector exposes every saved row and all five fields. Drafts
survive station switching and submit as one batch. Unchanged fields are omitted;
explicit zero/negative values survive. Readback follows save. Active and saved
raw values and native loader projections are distinct, including derived
read-only zHigh, TECAN zDelta=53000 and shared XY/height adjustments. These are
saved projections, not previews of unsaved edits. The robot SQLite owner stores
final values and consumes them only at next ordinary startup; the BMS editor
never restarts, homes, rebinds or claims physical accuracy. Raw measurements
must not be treated as final OEM offsets.

Offline qualification: mounted React/Axios tests, actual FastAPI routes and
BioXpRobotClient with HTTP MockTransport. The committed fixture is a compact
native-contract excerpt with captured XML/provenance removed, not hardware
acceptance. Set `BIOXP_NATIVE_CONTRACTS` to the externally generated native
contract file to verify full baseline/save responses losslessly without
committing captured provenance. No deployment or physical robot tests are
implied. Full OEM auto-calibration is separate from these settings controls;
scientific recipe authoring is outside this panel.

## Manual tip-tray Set and pipette operation flags (offline candidate)

The cockpit also mounts a typed tray 1–4 selector. Its Set button sends
`POST /api/bioxp/calibration-settings/manual-tip-set` with a BMS connection
generation; the fixed robot path is
`POST /motion/oem/pipette/tip_tray_set` with `{ "tray": 1..4 }`.
The robot owns the current-Z measurement and atomic paired zLow save: tray 1/2
selects TECANRACK1/2, tray 3/4 selects TECANRACK3/4. The UI displays returned
`measured_z_steps`, then independently GETs calibration settings for paired saved and
active zLow and revisions. It does not claim live application, physical
accuracy or tip pickup. A differing `committed_revision_id` and saved readback
revision is reported, not hidden.

`GET/PATCH /api/bioxp/operation-parameters` relay the fixed robot
`/liquid/pipette/settings` path. GET returns a `runtime_values`
object; PATCH accepts an omitted-or-strict-Boolean subset of
`CheckForStaticTipLoss` and `LogPressure` only. Explicit false is
preserved; unrelated settings are not editable here. The UI reads back after
saving. `CheckSnapTips` is a source-consumed, nonpersisted OEM UI state, not an
Operation_parameters field and cannot be represented by this saved settings
writer. The existing advanced catalog remains the diagnostic
command owner; no raw JSON/CAN control is added. Both BMS routes retain mutation
authorization and generation leases. Mock-HTTP qualification does not establish
a deployed robot endpoint or physical result; the paired robot candidate owns
these paths and response keys.
