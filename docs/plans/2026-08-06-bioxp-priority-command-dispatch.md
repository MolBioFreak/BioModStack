# BioXP Priority Command Dispatch Change Specification

> **For Hermes:** Implement this specification as one bounded BioModStack control-layer change. Critical dispatch latency is the first acceptance gate. Do not add a general scheduler, message broker, priority-queue framework, or robot-side redesign.

**Status:** Approved design direction; implementation pending  
**Date:** 2026-08-06  
**Baseline:** BioModStack `4f927f543ab6145bfc24598061458e1b217bb6c8` on `test`  
**Primary owner:** BioModStack API and frontend  
**Robot authority:** BioXP robot API and its OEM-replication runtime

**Goal:** Make critical robot commands dispatch promptly from the BioModStack UI without waiting behind polling, analytics, or post-command refresh work, while retaining simple live UI updates from interval-based polling.

**Architecture:** BioModStack remains a thin relay. Critical commands use a protected command path that takes precedence over optional reads. BMS polls only lightweight operator data at bounded intervals, updates shared frontend query state live, and leaves robust analytics collection on the robot.

**Tech stack:** FastAPI, `asyncio`, `httpx`, React, TypeScript, TanStack Query.

---

## 1. Controlling priority

The first acceptance gate is command dispatch latency.

A critical command SHALL NOT wait behind any of these activities:

- operator catalog polling;
- dashboard polling;
- action-history polling;
- action-admission polling;
- camera-status polling;
- temperature or axis analytics collection;
- automatic hardware-snapshot collection;
- frontend post-command query invalidation.

The critical-command set initially includes:

- ordinary OEM motion actions;
- homing actions;
- gripper and thermal-door movement;
- typed recovery actions;
- STOP, ABORT, and emergency stop.

STOP, ABORT, and emergency stop SHALL retain their existing independent safety-interrupt path.

No motion command may be retried automatically.

## 2. Current defect and evidence

The current BMS connection service holds `_transition_lock` across each complete robot HTTP transaction in `platform/api/services/bioxp/connection.py`. Robot-bound reads and ordinary commands therefore serialize on one lock.

The `/bioxp` page currently generates frequent reads:

- operator catalog every 5 seconds;
- operator dashboard every 5 seconds;
- action history every 2 seconds;
- selected-action admission every 5 seconds;
- camera status every 1 second;
- active connection probe every 10 seconds.

The active probe can run `POST /hardware/snapshot/collect`, which can hold the same path for several seconds.

`useRefreshMutation()` in `platform/frontend/src/lib/bioxpClient.ts` awaits a broad `Promise.all()` invalidation set. TanStack therefore keeps the mutation pending until the slowest active refetch finishes. `BioXpCockpit.tsx` converts that state to a global `busy` flag that disables ordinary controls.

Observed browser-control evidence on 2026-08-05/06:

- initial Home robot execution: 10.396 seconds;
- Home completion to absolute-command arrival: 47.997 seconds;
- absolute robot execution: 12.669 seconds;
- absolute completion to final-Home arrival: 51.699 seconds;
- final Home robot execution: 14.653 seconds;
- 23 robot-bound reads completed between the first Home and absolute action POSTs;
- 25 robot-bound reads completed between the absolute and final Home action POSTs.

This proves that BMS polling and refresh work, rather than OEM controller execution, caused the long inter-command delay.

## 3. Required command-path behavior

### 3.1 Dispatch

When BMS receives a critical command:

1. Mark a critical command as waiting before any optional robot request can start.
2. Validate the BMS connection generation and capture the active robot client.
3. Dispatch the robot action without waiting for telemetry work.
4. Preserve the robot-owned expected ownership generation, admission, idempotency, and receipt contracts.
5. Await the robot response and return its receipt.
6. Clear the command-active state.
7. Resume optional polling.

The command path SHALL NOT fetch the full operator catalog as a BMS-side preflight when the robot action endpoint already performs authoritative action lookup, admission, ownership-generation validation, and input validation. BMS may perform local shape validation. The robot remains the final authority.

### 3.2 Fail-fast behavior

BMS SHALL NOT maintain an unbounded motion-command queue.

If a command cannot dispatch within 1 second because another ordinary critical command owns the command path, BMS SHALL:

- reject the new request before sending it to the robot;
- return a structured conflict or service-unavailable response;
- include a stable error code such as `command_dispatch_busy`;
- record the active command identifier when available;
- avoid retrying the command.

A timeout or disconnect after robot dispatch SHALL remain an ambiguous terminal result that requires state inspection before retry.

### 3.3 Timing evidence

Every BMS command response or structured log SHALL carry enough timestamps to calculate:

- BMS request receipt time;
- robot dispatch start time;
- robot response receipt time;
- total dispatch wait in milliseconds;
- total robot request duration in milliseconds.

This evidence SHALL identify BMS delay separately from robot execution time.

## 4. Minimal BMS concurrency change

Do not introduce a new framework.

### 4.1 Connection lifecycle

`_transition_lock` SHALL protect only connection lifecycle transitions and stable command ownership:

- connect;
- disconnect;
- target replacement;
- generation change;
- client shutdown;
- an active critical command that must not lose its client.

Optional query-only robot reads SHALL NOT hold `_transition_lock` for their complete upstream duration.

A query-only read SHALL:

1. capture the client and generation under the transition lock;
2. release the transition lock;
3. perform the read;
4. discard or mark the result stale if the generation changed before completion.

A failed or stale query SHALL affect telemetry freshness only. It SHALL NOT delay a critical command or relabel a healthy robot command as failed.

### 4.2 Poll suppression

Use one process-local critical-command waiting/active signal.

Before starting an optional robot read, BMS SHALL check this signal. If a command is waiting or active, BMS SHALL return cached data, skip the poll, or report that refresh was deferred.

Poll intervals SHALL be skip-based:

- one in-flight poll per query class;
- no accumulated missed ticks;
- no queued duplicate poll;
- the next interval starts after the current request finishes;
- command arrival prevents new optional polls from starting.

A telemetry request already running may finish. It must use a lightweight read route and a short route-specific bound.

## 5. Robot-side ownership

The BioXP robot remains responsible for:

- OEM-parity controller interaction;
- robust hardware evidence collection;
- detailed axis analytics;
- temperatures and instrument-specific telemetry;
- authoritative action admission;
- motion execution;
- controller events and errors;
- terminal receipts;
- immutable evidence and provenance.

BMS SHALL NOT replicate these analytics or create a second authority.

Automatic detailed snapshot collection SHALL be removed from the BMS ten-second connection probe. The connection probe SHALL use `probe_status_only()`.

Detailed hardware collection remains available robot-side and may run through an explicit or low-priority path that never blocks a waiting command.

## 6. Live UI updates

The operator UI SHALL update when polling returns new data. A page refresh must not be required.

Components SHALL render directly from shared TanStack Query data or one explicit derived selector. They SHALL NOT copy polled robot state into long-lived component-local state.

The first implementation SHALL use simple fixed intervals:

| Data | Initial interval | Source |
|---|---:|---|
| BMS connection state | 15 seconds | BMS-local snapshot |
| Motion armed/readiness and interlocks | 15 seconds | lightweight operator dashboard/status |
| Basic axis position, speed, and reference | 15 seconds idle | lightweight operator dashboard/status |
| Active operation and latest receipt | 1 second only while a command is active | BMS-local command state or cached receipt |
| Temperatures, including chillers | 60 seconds idle | robot-cached telemetry |
| Action history | after command completion and while Logs is open | robot history/cache |
| Full controller analytics | on demand | robot |
| Camera status | only while camera content is visible | robot camera status |

Adaptive polling beyond the active-command case is secondary work. For example, a chiller may later poll every 15 seconds during activation and every 60 seconds while idle. That enhancement SHALL NOT block the critical dispatch fix.

Hidden browser tabs SHALL reduce or stop optional polling. Returning to a visible tab SHALL trigger one catch-up refresh without creating a backlog.

## 7. Frontend command completion

`useRefreshMutation()` SHALL NOT await broad invalidation after a critical command.

After a robot response:

1. Render the returned receipt immediately.
2. Update the action-history cache with that receipt.
3. Update basic visible state from authoritative terminal evidence when present.
4. End the frontend command-pending state.
5. Start limited dashboard/catalog refreshes in the background.

Unrelated profile, jobs, and lifecycle queries SHALL NOT be invalidated by manual robot actions.

A dashboard or history refresh failure SHALL show stale/error state without keeping motion controls globally busy.

Controls SHALL be disabled by authoritative command/admission state. Analytics refresh state SHALL NOT disable them.

## 8. Minimal implementation surface

### API

Modify:

- `platform/api/services/bioxp/connection.py`
  - narrow transition-lock scope for query-only reads;
  - preserve client generation and command ownership;
  - add the command waiting/active signal;
  - suppress optional reads while a command waits or runs.

- `platform/api/services/bioxp/robot_client.py`
  - make the ten-second active probe status-only;
  - keep hardware snapshot collection explicit and low priority.

- `platform/api/routers/bioxp/operator_controls.py`
  - remove redundant full-catalog preflight from the ordinary command critical path when robot invocation provides the same authoritative checks;
  - add dispatch timing evidence and fail-fast busy errors.

Tests:

- `platform/api/tests/test_bioxp_connection.py`
- `platform/api/tests/test_bioxp_operator_controls.py`

### Frontend

Modify:

- `platform/frontend/src/lib/bioxpClient.ts`
  - stop awaiting broad post-command invalidation;
  - set the fixed intervals in Section 6;
  - make history, camera, and heavy reads conditional.

- `platform/frontend/src/components/BioXpCockpit.tsx`
  - remove analytics refresh from the global motion `busy` condition;
  - render live query updates and immediate receipts.

- `platform/frontend/src/components/BioXpOperatorControlTabs.tsx`
  - poll admission only when needed;
  - poll history only when Logs is open or a command completes.

Add focused frontend tests in the existing frontend test structure. Do not introduce a new test framework.

## 9. Binding acceptance criteria

### 9.1 Synthetic command-priority tests

Using a fake robot client:

- Start a blocked query-only telemetry request.
- Submit an ordinary critical command.
- Prove the command reaches its fake robot request without waiting for the blocked telemetry request.
- Flood optional poll attempts and prove they are skipped or served from cache while the command waits or runs.
- Submit a second ordinary command during an active command and prove it fails within 1 second without reaching the robot.
- Prove STOP and ABORT still bypass an active ordinary command.
- Change the connection generation during a query and prove the stale query result is discarded.

### 9.2 Frontend tests

Prove that:

- a completed command clears command pending state before dashboard refetch finishes;
- dashboard refetch failure does not keep controls disabled;
- new polled motion-armed state renders without page refresh;
- new polled temperature renders without page refresh;
- repeated interval ticks do not create overlapping requests;
- hidden-tab polling does not accumulate work;
- returning to the tab causes one refresh;
- the receipt from the command response appears immediately.

### 9.3 Performance gates

Under synthetic poll load:

- BMS request receipt to robot dispatch start: under 250 ms at p95;
- hard undispatched wait: at most 1 second before fail-fast rejection;
- telemetry contribution to command dispatch latency: under 100 ms at p95;
- STOP/ABORT remain independent;
- no poll backlog remains after a command completes.

### 9.4 Live non-physical acceptance

Without motion:

- deploy exact reviewed `test` revision;
- verify API/frontend process, listener, source revision, and health;
- open `/bioxp` and prove motion-armed/readiness values update after a poll without page reload;
- prove temperature values update after their interval without page reload;
- verify no detailed snapshot collection runs on the ten-second connection probe;
- verify optional polling pauses while a synthetic command-active state is asserted in the test harness.

### 9.5 Physical acceptance gate

Physical motion testing requires separate explicit operator approval.

When approved, run one bounded browser-controlled command sequence and prove:

- UI click timestamp;
- BMS receipt timestamp;
- robot dispatch timestamp;
- robot command receipt;
- controller terminal evidence;
- final stationary state;
- dispatch latency within the performance gate.

Physical success is not inferred from HTTP status alone.

## 10. Non-goals

This change SHALL NOT:

- redesign the BioXP robot analytics system;
- add Kafka, Redis, Celery, a message broker, or a general priority queue;
- add WebSockets or server-sent events;
- create a new BMS receipt authority;
- duplicate robot hardware evidence;
- weaken ownership-generation or admission checks;
- increase robot movement timeouts to hide BMS delay;
- automatically retry motion;
- redesign unrelated BioModStack jobs or lifecycle workflows;
- implement advanced adaptive polling before critical dispatch passes.

## 11. Delivery order

1. Add failing API command-priority and fail-fast tests.
2. Narrow query lock scope and make critical command ownership explicit.
3. Remove redundant catalog preflight from the command critical path.
4. Make the active probe status-only.
5. Add dispatch timing evidence.
6. Add failing frontend tests for non-blocking completion and live query rendering.
7. Remove awaited broad invalidation and unrelated refreshes.
8. Apply the simple polling intervals and conditional history/camera behavior.
9. Run focused API tests and the existing frontend production build/test gates authorized for implementation.
10. Push to `test`, allow canonical sync, restart through the managed path if required, and prove the exact live revision.
11. Run non-physical live acceptance.
12. Request explicit approval before physical command-latency acceptance.

## 12. Completion rule

The change is complete only when critical dispatch passes before all secondary polling improvements. If live UI polling works but command dispatch can still queue behind telemetry, the change fails.

If critical dispatch is fast but a secondary analytics widget still needs manual refresh, record that widget as secondary follow-on work. Do not delay or weaken the command-path fix to rebuild the analytics layer.
