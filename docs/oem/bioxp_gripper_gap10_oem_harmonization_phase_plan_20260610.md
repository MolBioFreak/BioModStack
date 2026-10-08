# BioXP Gripper GAP10 OEM harmonization phase plan — 2026-06-10

## Scope

No live robot motion in this code tranche. The goal is to make BMS/robot-facing surfaces stop treating generic G GAP9/GAP10 as proven two-ended physical gripper limits, while keeping GAP10 visible as an unresolved raw/inhibit signal. Completion means the code is ready for a supervised robot-local passive/status test and then an explicitly acknowledged OEM-shaped gripper-clear test.

## OEM source comparison anchors

- `MotorGrip` is head board index 0 / axis 2.
- `initializeMotorsWithoutMotion()` sets G profile by `GripperVersion` and does not show a blanket G right/left switch-disable call in the inspected setup block.
- Startup gripper sequence raises current, performs `MotorGrip.moveSteps(..., 10000, true)`, then homes with `axisSearchHome(...)` at the OEM gripper speed.
- Manual gripper home raises current, calls `goHome(true, MotorGrip, speed, true)`, then restores safe current for gripper version 1.
- OEM confirmation for `g`/`gripper` is `queryHome(MotorGrip) OR getG() < 50`; it does not require a generic GAP10/right inactive condition.

## Phase 0 — freeze, compare, and document gate

Code changes:
- Add this phase plan/spec artifact.

Tests/spec comparison:
- Existing BMS gripper proxy/frontend tests remain baseline.
- Confirm no live USB/motion routes are called by planning/documentation.

Commit strategy:
- Include this plan in the first code commit with Phase 1 if no behavior changes are yet present.

Exit criteria:
- Phase plan explicitly distinguishes OEM logical home from raw GAP10 diagnostics.

## Phase 1 — BMS status harmonization / wording fix

Code changes:
- Add a BMS-side normalizer for `/motion/gripper/status` payloads.
- Preserve robot-local raw payload fields, but add a separate `bms_oem_interpretation` object.
- If OEM gripper home is true while GAP10/right is raw asserted, classify GAP10 as `unresolved_raw_asserted_not_physical_limit_proof` rather than `physical_right_limit_hit`.
- Expose a UI copy change: `OEM home true; GAP10 raw unresolved` instead of `both G limits are active`.

Tests:
- API test: fake robot payload with GAP9=1/GAP10=1 and OEM home true must return:
  - `bms_oem_interpretation.oem_home_confirmed == true`
  - `bms_oem_interpretation.gap10_role == unresolved_raw_asserted_not_physical_limit_proof`
  - `motion_test_state == blocked_until_gap10_truth_table_or_controlled_clear`
- Frontend source test: cockpit panel must contain the new OEM/GAP10 wording and must not contain the old physical-double-limit claim.

OEM/spec comparison at phase boundary:
- Pass if BMS states OEM logical home exactly as `queryHome OR G<50` and does not convert raw GAP10 into physical double-limit proof.

Commit strategy:
- Commit 1: `bioxp: harmonize gripper gap10 status with oem semantics`.

Exit criteria:
- Focused API and frontend source tests pass.

## Phase 2 — supervised action readiness envelope

Code changes:
- For BMS gripper clear/home proxy calls, add a BMS-visible readiness envelope without hiding the robot response:
  - default `capture_bundle=true` if caller omitted it;
  - default `require_motion_evidence=true`;
  - default `restore_idle_current=true`;
  - return `bms_oem_interpretation` preserving whether the action is test-ready, ambiguous/no-motion, or blocked by robot-local truth.
- Do not convert an ACK-only robot response into success; keep ambiguous/no-motion visible.

Tests:
- API test: gripper clear payload is forwarded with the readiness defaults and timeout headroom.
- API test: ACK success with `seen_nonzero=false` or `ambiguous_no_motion=true` returns BMS interpretation `ambiguous_no_motion_not_clear_proof`.

OEM/spec comparison at phase boundary:
- Pass if BMS action envelope matches OEM startup intent but still requires live motion evidence before declaring clear/home proof.

Commit strategy:
- Commit 2: `bioxp: add gripper action readiness envelope`.

Exit criteria:
- Focused API tests pass.

## Phase 3 — completion verification before live testing

Verification commands:
- `python -m py_compile platform/api/routers/bioxp.py`
- `PYTHONPATH=platform/api pytest platform/api/tests/test_bioxp_gripper_proxy.py -q`
- Frontend static/source test command used by this repo's existing harness, or direct node/tsx equivalent if available.
- `git diff --check`
- `git status --short`

Completion spec comparison:
- OEM logical home: BMS reports `queryHome OR G<50` when present.
- GAP10: BMS exposes raw state but labels role unresolved.
- Motion readiness: BMS requires explicit gripper action ACK and evidence defaults; no live movement is run by this code tranche.
- Operator readiness: next live step is passive `/motion/gripper/status`, not clear/home.

Ready-to-test sequence after code deploy/restart:
1. GET BMS `/api/bioxp/motion/gripper/status`; verify BMS interpretation and raw GAP9/GAP10/current fields.
2. Robot-local passive truth table under operator observation; no motion.
3. Only with explicit operator ACK, POST BMS `/api/bioxp/motion/gripper/clear` with `operator_ack=GRIPPER_CLEAR`, capture bundle, and immediate stop readiness.
4. Stop if ACK-only/no-motion or GAP10 inhibit remains unresolved; restore G idle current to 10/10.
