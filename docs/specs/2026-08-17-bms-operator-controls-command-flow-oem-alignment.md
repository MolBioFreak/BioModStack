# BMS operator-controls command flow: OEM-aligned latency and successive-move dispatch

**Work package:** WP-BMS-CF-20260817 — BMS-side command-flow alignment only

**Date:** 2026-08-17

**Release claim:** none; this is an implementation and acceptance contract, not runtime or physical proof. No "resolved" claim follows from this document alone.

## 1. Purpose and scope

The BioXp operator controls currently exhibit two operator-visible deficiencies after a command completes:

1. UI lag: several seconds of refetch churn after every move action, blocking the next command.
2. Single-command serialization: a successive move while one is active is rejected (fail-closed `409`), and controls re-enable only after a dashboard poll, although the OEM robot layer accepts back-to-back commands with event-driven handoff.

This spec freezes the fix contract for these two deficiencies, aligned to the source-proven OEM fast multi-command model. It deliberately touches nothing else in the frozen 2026-08-12 Serial-206 parity matrix.

## 2. Frozen authority and base

### 2.1 OEM source authority

| Identity | Frozen value |
|---|---|
| OEM transport authority | `decompiled_src_can/ClassCanLib/ClassNovo.cs`, SHA-256 `11293074caec278076723666e69022b547c43f32b5fa886c99f75d5b60043d06` |
| OEM board authority | `decompiled_src_can/ClassCanLib/ClassHeadBoard.cs`, SHA-256 `342a9b2f09731002194b67e37f1d4e866ecbfb3c25effd85b3cd609e8cbdd1ea` |
| OEM motor authority | `decompiled_src_can/ClassCanLib/ClassMotor.cs`, SHA-256 `9fb1b4bec771165053a82b4fe95510615d6ed9beda1a041280584ceb4ab7fe99` |
| OEM command-queue class | `decompiled_src_can/ClassCanLib/ClassNovoCommandQueue.cs`, SHA-256 `70bed5af6c244d63f6506e1ed9003d5c3e9f07dae5aac1262520ec56506f35dd` |
| OEM high-level authority | `decompiled_src/BioXPControlLib/ClassControlInterface.cs`, SHA-256 `86093e5270c82ea2e45cb4de449076372ca79d9485ba6de9565d5eb255811e6e` |

### 2.2 BMS repo base

| Identity | Frozen value |
|---|---|
| Worktree | `/home/dalab/worktrees/bms-x-oem-terminal-20260817` |
| Base HEAD | `aec08cb6439f9c48d216522d2a9e1a5c26b393f0` |
| Base HEAD tree | `c330a3ce6fc1fe5a841a9f90ca6aa2b0a02e8654` |
| `origin/test` at base | `aec08cb6439f9c48d216522d2a9e1a5c26b393f0`; exact match |
| `platform/frontend/src/lib/bioxpClient.ts` | blob `f7b95423528736477a54c6ae919731f48473f522` |
| `platform/frontend/src/components/BioXpCockpit.tsx` | blob `4a72b6bda511a00038a0efe3581c74f7e5d46659` |
| `platform/api/routers/bioxp/operator_controls.py` | blob `318bfda978284c5585c43e197dab12f212a1ee4c` |
| `platform/api/services/bioxp/operator_models.py` | blob `db1196998174a2124fb3aa27f260d1a032cd789c` |
| `platform/api/services/bioxp/connection.py` | blob `0faa45286fa29b771d5b5632c04aa13ddd9effae` |
| `platform/api/services/bioxp/robot_client.py` | blob `2435d9af75d6184946f5d3630a012fcbcba3b515` |

### 2.3 Live robot base

Live robot release at spec date: immutable dir `bioxp_release_831d99d`, HEAD `831d99df8b6104b06d069fe3d356190d108f91b7`, tree `bd0128e4a9a5dfa0c63ebda810f98090ea250cfe` (X terminal verification parity). This spec builds on that release; it does not change its semantics.

### 2.4 Measured baseline (2026-08-17, read-only)

| Call | Latency |
|---|---|
| BMS relay `/operator-controls/dashboard` | 0.55 s |
| BMS relay `/operator-controls/catalog` | 0.69 s |
| Robot `/operator/dashboard` | 0.55 s |
| Robot `/status` | 0.57 s |
| Physical 90,000-step X move | 3.3 s (~27-28k steps/s) |

## 3. Problem evidence (current state, frozen at spec date)

1. `BioXpCockpit.tsx:202-205` mounts four always-on admission queries: `oem.x.move_steps` (negative), `oem.x.move_steps` (positive), `oem.x.move_absolute`, `oem.x.manual_panel_home`.
2. `bioxpClient.ts:993-999` invalidates five query groups after every invoked action: status, all operator admissions, catalog, dashboard, history.
3. Each admission and catalog call acquires the robot provider-state lock and performs fresh board reads (position, switches, lifecycle); the calls serialize on the lock at ~0.5-0.7 s each.
4. Net effect: 6-8 serialized lock-acquiring robot round trips per action completion, producing a 3-5 s window in which the UI is refetching and the next command's admission waits.
5. A successive single-axis move while one is physically active is rejected (`409`, fail-closed). Controls re-enable only after a dashboard poll (adaptive 1-10 s cadence) shows the receipt cleared.

## 4. OEM reference model (source-proven)

1. Completion is event-driven per motor: `AutoResetEvent _waitForMotor` per motor (`ClassMotor.cs:39`), set by controller event 128 TARGET_POSITION_REACHED (`ClassHeadBoard.cs:541-549`); a move waits with `WaitOne(20000)` and returns the terminal position readback as evidence, never as an equality gate.
2. Transport is a queue plus dedicated dispatch thread: `BlockingCollection<TrafficPacket> m_messageQueue` with the "Novo" background thread (AboveNormal) running `MessageProcessingThread` -> `GotMessageProcess` (`ClassNovo.cs:39-57, 146-192`).
3. Sending is serialized only per CAN frame: `lock (m_sendingLock)` around `sendCommand` with ~11 ms of sleeps (`ClassNovo.cs:194-220`). No lock spans a move.
4. Between successive moves the OEM performs a single status query (`queryMotorStop`, `ClassMotor.cs:704-719`) then dispatches the next relative move (`ClassHeadBoard.cs:255`). Successive moves are never rejected; serialization happens at the controller.
5. `ClassNovoCommandQueue.cs` is a real Monitor-locked FIFO but is unreferenced by runtime code (vestigial). The live mechanism is the event plus transport-queue model.

## 5. Requirements

### WP-A: BMS-side latency alignment (no robot changes, no motion semantics change)

**R-A1. Admission fan-out collapse.** The cockpit derives admission/enable state from the dashboard snapshot (lifecycle state, envelope, reference, generations) instead of four always-on admission queries. Per-action admission calls fire only (a) on an explicit user gesture immediately before dispatch, or (b) when the dashboard snapshot shows a lifecycle/generation change. The robot admission endpoint is unchanged.

**R-A2. Post-action invalidation narrowing.** After an invoked action, the client invalidates only the dashboard and history query groups. Catalog and admission state remain cached and refresh only under the R-A1 lifecycle-change trigger. Target: at most two robot round trips per action completion (dashboard + receipt).

**R-A3. Active-move receipt polling.** While a receipt is non-terminal, the cockpit polls the receipt endpoint at 250-500 ms and re-enables the affected controls from the receipt terminal state, not from the dashboard poll. Target: controls re-enabled within 1 s after the robot marks the receipt terminal.

### WP-B: successive-move queue (robot + BMS; amends one frozen matrix item; gated on physical validation)

**R-B1. Queued successive moves.** A successive single-axis move (`oem.x.move_steps`, `oem.x.move_absolute`, `oem.z.move_steps`, `oem.z.move_absolute`) admitted while a move on the same axis is physically active is accepted as `queued` (HTTP 200, never 409). The robot dispatches the queued move when the motor stops, following OEM `queryMotorStop` semantics. Receipts carry the state sequence `queued -> dispatched -> completed` with timestamps. The queue is bounded (default depth 8, configurable) and surfaced in the dashboard (depth, head action, state). Stop/abort preempts and clears the queue.

**R-B2. Frozen-matrix amendment.** R-B1 supersedes the 2026-08-12 matrix requirement "safe rejection or disabling of successive X moves while an earlier move is active" for single-axis moves only. All other fail-closed gates remain: envelope admission, reference requirement, lifecycle prerequisites, connection and ownership generations, and per-command preflight.

## 6. Out of scope (touched by nothing in this spec)

- X/Z reference, home, envelope, lifecycle, or coordinate semantics.
- Receipt schema removal or renaming of existing fields; only the `queued`/`dispatched` states and timestamps are added.
- XY/XYZ combined moves (`oem.xy.move_xy`, `oem.xy.home_xy`, `oem.xyz.move_to`): Y plane is not ready; these remain disabled.
- Pipette, camera, chiller, thermal, liquid paths.
- Robot release, container, systemd, or deploy mechanics beyond the existing immutable-release flow.
- Any other frozen matrix item.

## 7. Acceptance criteria

WP-A (verifiable without physical motion):

1. After a completed move action, the cockpit performs at most two robot round trips (dashboard + receipt) and no admission calls unless lifecycle changed.
2. Time from receipt terminal to controls re-enabled is <= 1 s, measured in the browser against the live BMS API.
3. All existing BMS API tests (including `test_bioxp_operator_controls.py`, 45+ tests) and frontend component tests pass; strict model contract validation passes.
4. Live timing re-measured against the Section 2.4 baseline; the post-action churn window drops from 3-5 s to <= 1 s.

WP-B (requires physical validation by Christian before any closure claim):

5. A successive single-axis move while one is active returns 200 `queued` (not 409); the queued receipt reaches `dispatched` then `completed`.
6. Dispatch gap after the prior receipt terminal is <= 200 ms plus transport time (measured in robot receipts).
7. Stop/abort clears the queue; bounded depth enforced; dashboard shows queue state.
8. Robot and BMS test suites pass; live read-only verification; then Christian-run physical validation (successive fast moves) before closure.

## 8. Rollout

1. WP-A first: implement in the BMS worktree, run focused tests, push `test` fast-forward, restart the BMS API service, re-measure live timings against baseline.
2. WP-B second: implement robot admission queue in the robot worktree, run robot tests, release bundle via the immutable-release flow (as `831d99d`), read-only verify, then schedule Christian-run physical validation.
3. No physical robot command is issued by automation at any point in this work package.
