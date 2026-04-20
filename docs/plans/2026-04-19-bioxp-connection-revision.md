# BioXP Connection Revision Spec

> Status note: this dated file is the transition roadmap that moved BioXP toward the current robot-local runtime + BMS linkage/proxy model. For current operator-facing behavior, prefer `docs/Lab_Automation_MolBio_and_Sequencing.md`, `docs/Workstation Set Up and Install Guide.md`, and the live router/UI under `platform/api/routers/bioxp.py` and `platform/frontend/src/components/BioXpCockpit.tsx`.
>
> **For Hermes:** Treat this as a phased architecture roadmap first. Do not jump straight to implementation without reconciling the live robot runtime and the existing BioModStack cockpit contract.

**Goal:** Simplify BioModStack's BioXP integration so the robot owns its own API daemon lifecycle locally, while BioModStack becomes a linkage manager and HTTP proxy/client instead of a workstation-side SSH supervisor.

**Architecture:** The robot-local BioXP API should be supervised on the robot itself (ideally via `systemd`). BioModStack should persist a linkage URL, probe health over HTTP, proxy robot endpoints, and expose connection status in the cockpit. SSH must move out of the steady-state request path and become a break-glass maintenance mechanism only.

**Tech Stack:** FastAPI router in `platform/api/routers/bioxp.py`, React Query hooks in `platform/frontend/src/lib/bioxpClient.ts`, BioXP cockpit UI in `platform/frontend/src/components/BioXpCockpit.tsx`, Python tests in `platform/api/tests/test_bioxp_router.py`, robot-local `bioxp.api` runtime supervised outside this repo.

---

## Why this revision is needed

The current BioModStack BioXP router is doing too many jobs at once:

1. it persists the linkage URL
2. it SSHes to the robot host
3. it probes daemon health over SSH
4. it starts/stops the robot API daemon over SSH using `pkill` + `nohup uvicorn`
5. it proxies the actual hardware HTTP calls to the linked robot runtime

That mixes three separate concerns that should not share one control path:

- connection state (`/linkage`)
- daemon ownership / supervision (`/daemon/*`)
- application traffic proxying (`/status`, `/motion/*`, `/thermal/*`, etc.)

### Current failure mode

`platform/api/routers/bioxp.py` currently has split-brain daemon truth:

- first it tries `_daemon_probe()` over SSH
- if that fails, `daemon_status()` may still infer "running" from proxied `/status`

That means BioModStack has two competing health models:

- SSH says whether the workstation can probe the daemon process on the robot
- the HTTP proxy says whether the cockpit can still reach a live runtime

This is exactly the kind of ambiguous ownership that makes runtime robustness and containerization harder.

---

## Non-negotiables

1. The robot must own its own API daemon lifecycle.
   - BioModStack should not be the long-lived supervisor for `uvicorn bioxp.api:app`.
   - No steady-state `ssh ... nohup uvicorn ... &` control path.

2. BioModStack remains the operator-facing cockpit.
   - Linkage management, status display, proxying, artifacts, and UI workflows stay in BioModStack.

3. Physical-motion truth still requires operator verification.
   - This spec is about connection/runtime ownership, not loosening motion-safety expectations.
   - Controller telemetry remains insufficient proof of safe real-world motion.

4. SSH stays available as break-glass maintenance.
   - It can still be used for install/update/debug or manual recovery.
   - It should not be the normal runtime mechanism behind cockpit buttons.

5. BioXP remains outside the first containerization wave.
   - The BioXP bridge/control surface is still a host/hardware adapter until its boundary is cleaned up.

---

## Current repo surfaces affected

### Backend
- `platform/api/routers/bioxp.py`
  - currently combines linkage persistence, SSH daemon control, SSH daemon probing, and HTTP proxying
- `platform/api/tests/test_bioxp_router.py`
  - current test anchor for linkage/status behavior

### Frontend
- `platform/frontend/src/lib/bioxpClient.ts`
  - `useGetLinkage()`
  - `useSetLinkage()`
  - `useDisconnectLinkage()`
  - `useDaemonStatus()`
  - `useDaemonStart()`
  - `useDaemonStop()`
- `platform/frontend/src/components/BioXpCockpit.tsx`
  - consumes linkage and daemon hooks directly in the connection tab
  - currently exposes daemon start/stop actions tied to workstation-side semantics

### Adjacent documentation / architecture surfaces
- `docs/Desktop_Runtime_and_Shell_Architecture.md`
  - relevant because the same ownership rule applies here too: clients should not supervise long-lived runtimes they merely connect to

---

## Target architecture

### 1. Robot-local runtime ownership

On the robot:
- supervise `bioxp.api` locally, ideally with `systemd`
- keep restart policy, logs, and boot-time behavior on the robot
- make local robot health the single source of truth for the daemon lifecycle

What BioModStack should assume:
- if the linkage URL responds, the robot runtime is up enough to serve the cockpit
- if the linkage URL does not respond, the robot runtime is unavailable or unhealthy
- BioModStack does not need process-level knowledge in the steady state

### 2. BioModStack as linkage manager + HTTP proxy/client

BioModStack should keep doing these jobs:
- persist the linkage URL
- normalize linkage input
- expose recommended/default connection targets
- proxy robot-facing HTTP endpoints
- present operator-facing connection state in the cockpit

BioModStack should stop doing these jobs in the normal path:
- SSH daemon liveness checks
- remote `pkill`
- remote `nohup uvicorn`
- workstation-owned process recovery logic for the robot API

### 3. Optional break-glass admin path

If remote restart from BioModStack is still desired, add a dedicated admin mechanism instead of reusing raw SSH in the normal request path.

Acceptable future options:
- a small robot-local admin wrapper around `systemctl`
- a separate authenticated robot maintenance endpoint
- an explicit operator-only maintenance script/runbook outside the main cockpit path

Not acceptable as the steady-state design:
- `ssh user@robot "pkill ... && nohup uvicorn ... &"`

---

## Recommended API contract shift

### Keep
- `GET /api/bioxp/linkage`
- `POST /api/bioxp/linkage`
- `POST /api/bioxp/linkage/disconnect`
- proxy-backed hardware/status routes under `/api/bioxp/*`

### Revise
- `GET /api/bioxp/daemon/status`
  - stop presenting SSH probe results as the primary truth
  - repurpose this into linked-runtime HTTP health, or replace it with a clearer linked-runtime status surface
  - if compatibility is needed, return fields that explicitly separate:
    - `linked_runtime_reachable`
    - `linkage_configured`
    - `admin_control_available`
  - do not silently claim daemon truth from one mechanism while labeling another

### Deprecate from the normal cockpit path
- `POST /api/bioxp/daemon/start`
- `POST /api/bioxp/daemon/stop`

Short-term compatibility option:
- keep the routes temporarily, but mark them deprecated and stop centering them in the cockpit UI
- if they still exist during transition, they must clearly indicate they are maintenance actions, not normal connect/disconnect behavior

### Important semantic distinction

The cockpit should distinguish between:
- link configured
- linked runtime reachable
- hardware responding
- maintenance/admin restart available

Those are not the same thing.

---

## UI behavior revision for the BioXP cockpit

### Current UI problem

`BioXpCockpit.tsx` currently models daemon state as a mix of:
- SSH-probed running/stopped
- proxy-inferred running
- unknown

That leaks the backend split-brain into the operator UX.

### Revised UI model

The connection tab should focus on:
1. linkage configured / not configured
2. linked runtime reachable / unreachable
3. robot hardware connected / not connected
4. maintenance controls available / unavailable

Primary actions should become:
- Connect / Update Linkage
- Disconnect Linkage
- Reconnect Runtime (HTTP-level reconnect if supported by the robot runtime)
- Open maintenance instructions or maintenance console only when needed

Normal operators should not need a big workstation-side Start Daemon / Stop Daemon path.

---

## Phased roadmap

## Phase 0: Document and freeze the intended ownership model

**Objective:** Stop treating workstation-side SSH daemon supervision as the desired end state.

**Files:**
- Create: `docs/plans/2026-04-19-bioxp-connection-revision.md`
- Review: `platform/api/routers/bioxp.py`
- Review: `platform/frontend/src/lib/bioxpClient.ts`
- Review: `platform/frontend/src/components/BioXpCockpit.tsx`

**Acceptance gate:**
- team has a written source of truth saying robot-local supervision is the target architecture
- containerization planning can now treat BioXP as a thin adapter boundary, not a workstation-owned daemon tree

## Phase 1: Separate connection truth from maintenance truth

**Objective:** Make BioModStack status surfaces reflect linkage/runtime reachability without depending on SSH process inspection.

**Likely files:**
- Modify: `platform/api/routers/bioxp.py`
- Modify: `platform/frontend/src/lib/bioxpClient.ts`
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Modify: `platform/api/tests/test_bioxp_router.py`

**Required behavior:**
- normal connection state comes from the linked HTTP runtime
- no more proxy-vs-SSH ambiguity in operator-facing labels
- daemon/admin capability, if retained, is labeled separately

## Phase 2: Remove workstation-owned daemon lifecycle from the normal UI path

**Objective:** BioModStack becomes linkage/proxy first; maintenance actions become explicit and secondary.

**Likely files:**
- Modify: `platform/frontend/src/components/BioXpCockpit.tsx`
- Modify: `platform/frontend/src/lib/bioxpClient.ts`
- Modify: `platform/api/routers/bioxp.py`

**Required behavior:**
- main connection flow never needs SSH start/stop
- connect means “store/use this robot-local runtime URL,” not “launch a daemon over SSH”
- maintenance actions are hidden, gated, or moved to a separate advanced surface

## Phase 3: Introduce a proper robot-side admin interface only if truly needed

**Objective:** If remote restart is still operationally valuable, make it explicit and bounded.

**Out of scope for first pass:**
- deciding exact auth scheme
- deciding whether this lives in robot repo vs separate helper

**Acceptance gate:**
- no raw SSH+nohup lifecycle orchestration remains on the primary BioModStack user path

---

## Containerization and robustness implications

This spec directly supports the broader containerization/robustness work.

### What it improves
- removes one major split-responsibility runtime edge from BioModStack
- makes the BioXP integration look more like a normal remote service link
- reduces workstation-specific behavior in the steady-state path
- makes the core runtime easier to reason about when containerizing the API/frontend

### What it does not change yet
- BioXP still remains a host/hardware adapter surface in the first migration wave
- robot-local service management is still outside the first-wave container boundary
- motion-safety and physical-verification requirements still remain

### Containerization rule this spec reinforces

First-wave containers should own:
- core BioModStack API/frontend runtime

First-wave containers should not newly absorb:
- robot SSH lifecycle control
- robot daemon supervision
- hardware-adjacent maintenance semantics

---

## Validation gates

A future implementation is incomplete unless it verifies all of these:

1. BioXP cockpit can still set and persist a linkage URL
2. BioXP cockpit can still report not-configured vs configured vs unreachable cleanly
3. linked robot runtime health is determined without requiring SSH process probes in the normal path
4. proxied BioXP `/status` still works when the linkage is valid
5. operator-facing labels no longer blur together:
   - linkage configured
   - runtime reachable
   - hardware connected
   - maintenance/admin available
6. if maintenance restart exists at all, it is clearly separated from the normal connection flow
7. supervised safe-motion checks still require operator observation and physical confirmation

---

## Definition of done

This revision is done when:
- the robot owns `bioxp.api` lifecycle locally
- BioModStack treats BioXP as a linked remote runtime, not a remote shell session
- the cockpit UX reflects connection truth instead of SSH/proxy ambiguity
- any restart/admin path is explicit, secondary, and bounded
- containerization planning can safely keep BioXP in the host-adapter bucket without carrying forward the current split-brain daemon model
