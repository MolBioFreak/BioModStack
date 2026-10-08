# BioModStack Electron Shell Port Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add an optional Electron desktop shell for BioModStack that wraps the existing UI and service controls while preserving direct browser/web access as a first-class supported mode.

**Architecture:** Keep `systemd --user` as the sole owner of the API/frontend runtime. The Electron app is only a client/controller: it can embed the existing web UI in a `BrowserWindow`, expose service/log/status actions, and provide a one-click "Open in Browser" escape hatch, but it must never become the long-lived supervisor for uvicorn/Vite or future production web services.

**Tech Stack:** Electron, TypeScript, pnpm workspace, existing Vite/React frontend in `platform/frontend`, Python service manager in `biomodstack_services.py`, user units rendered by `scripts/manage_desktop_services.py`.

---

## Constraints and non-negotiables

1. Browser access remains supported permanently.
   - Users must still be able to use BioModStack at `http://127.0.0.1:5173/bms/` or the future production-served equivalent.
   - Electron must not fork the product into an Electron-only UX.
2. Electron does not supervise backend lifetime.
   - API and frontend remain owned by `biomodstack-api.service`, `biomodstack-frontend.service`, and `biomodstack.target`.
   - Electron may call `scripts/manage_desktop_services.py` or a future structured control API, but it must not `spawn` long-lived uvicorn/Vite children directly.
3. One UI, two shells.
   - The React app remains the product UI.
   - Electron adds native packaging, menus, tray, notifications, and local OS integration.
4. Routing/base-path semantics must stay explicit.
   - Browser mode must keep working with `/bms/` in production.
   - Electron mode must not rely on brittle implicit path assumptions.
5. Chromium-only packaging is acceptable here.
   - This plan intentionally chooses Electron for maximum compatibility with the current visualization stack (`pdbe-molstar`, `plotly`, `igv`, Blueprint-heavy React UI).

---

## Current repo surfaces this plan depends on

### Runtime/control layer
- `biomodstack_services.py`
- `scripts/manage_desktop_services.py`
- `scripts/run_biomodstack_api.sh`
- `scripts/run_biomodstack_frontend.sh`
- `start_ui.sh`
- `restart_api.sh`
- `stop_services.sh`

### Existing desktop control surfaces
- `biomodstack_panel.py`
- `biomodstack_tray.py`

### Frontend surfaces Electron will wrap
- `platform/frontend/package.json`
- `platform/frontend/vite.config.ts`
- `platform/frontend/src/main.tsx`
- `platform/frontend/src/App.tsx`

### Workspace surface
- `pnpm-workspace.yaml`

---

## Recommended repo layout

Create a new workspace package instead of polluting `platform/frontend` directly:

- Create: `platform/desktop-electron/package.json`
- Create: `platform/desktop-electron/tsconfig.json`
- Create: `platform/desktop-electron/src/main.ts`
- Create: `platform/desktop-electron/src/preload.ts`
- Create: `platform/desktop-electron/src/serviceControl.ts`
- Create: `platform/desktop-electron/src/windowState.ts`
- Create: `platform/desktop-electron/src/menu.ts`
- Create: `platform/desktop-electron/src/tray.ts`
- Create: `platform/desktop-electron/assets/` (icons later if needed)
- Modify: `pnpm-workspace.yaml`
- Modify: `platform/frontend/src/main.tsx`
- Modify: `platform/frontend/src/App.tsx`
- Modify: `platform/frontend/package.json`
- Modify: `docs/Desktop_Runtime_and_Shell_Architecture.md`

Reasoning:
- keeps the Electron shell separate from the web app
- allows browser and Electron builds to evolve independently
- makes it easier to package or delete the shell later without touching core frontend logic

---

## Product behavior spec

### Supported launch modes

1. Browser mode
   - Start services with existing shell/controller tools.
   - Open BioModStack in the browser at the normal local URL.
   - This remains the default operationally safe fallback.

2. Electron embedded mode
   - Electron opens a `BrowserWindow` pointed at the same local BioModStack UI.
   - It uses the same API/runtime as browser mode.
   - It exposes native menus, notifications, log access, and service controls.

3. Open-in-browser from Electron
   - Always present an explicit menu/button to open the current UI in the external browser.
   - This is not a debug feature; it is a permanent supported escape hatch.

### Initial Electron scope

Electron v1 should do only this:
- show service status (API/frontend active/inactive)
- restart/start/stop via the existing service-control entrypoint
- load the main BioModStack UI in a window
- offer “Open in Browser”
- offer “Open Logs” / “Open Results Folder” / “Copy Local URL”
- send desktop notifications for service failures or job completion later

Electron v1 should NOT do this:
- duplicate job orchestration/business logic
- reimplement the React app in native widgets
- own DB writes directly
- replace the browser UI path
- spawn long-lived API/frontend children itself

---

## Routing and URL strategy

This is the biggest implementation detail to get right.

### Problem
Current frontend facts:
- `vite.config.ts` uses `base: '/bms/'` for production and `/` in dev
- `src/main.tsx` uses `BrowserRouter` with no explicit basename
- `App.tsx` defines routes like `/`, `/submit`, `/results`, `/designer`, `/infra`, `/bioxp`

### Required outcome
Both of these must work cleanly:
- browser/proxy mode: `/bms/...`
- Electron local mode: either `/` or a dedicated Electron-safe base

### Recommendation
Introduce a single shared frontend base-path helper.

Create something like:
- `platform/frontend/src/runtime/navigation.ts`

It should compute a router basename from one of:
- `window.__BMS_ROUTER_BASENAME__` injected by Electron preload or HTML bootstrap
- `import.meta.env.BASE_URL`
- default `/`

Then update `src/main.tsx` to:
- pass `basename={computedBasename}` into `BrowserRouter`

Acceptance rule:
- browser mode still works behind `/bms/`
- Electron can point at a local URL without fake brittle path rewrites

---

## Electron window-loading strategy

### Phase 1 recommendation: load the existing local web app URL

Electron should initially load:
- dev: `http://127.0.0.1:5173/`
- prod-on-workstation shell mode: `http://127.0.0.1:5173/bms/` or the future production-served local URL

Why this is the right first step:
- smallest diff
- keeps browser mode and Electron mode literally on the same frontend build
- avoids immediately inventing a second packaging pipeline for static assets
- makes it obvious that Electron is a shell, not a fork

### Phase 2 option: packaged local frontend assets

Only after Phase 1 is stable, optionally support a packaged renderer build inside Electron.
That requires a cleaner production asset story and stricter route handling.

Do not start with that.

---

## Service control strategy for Electron

### Do now
Electron main process calls the existing service controller.

Preferred options, in order:
1. spawn `python3 scripts/manage_desktop_services.py <action>` for now
2. later replace with a tiny structured local control API or CLI returning JSON

### Do not do
- do not call `uvicorn` directly
- do not call `npm run dev` directly as a long-lived child
- do not recreate the original GUI-scope supervision bug in Node/Electron form

### Required abstraction
Create a small Electron-side wrapper:
- `platform/desktop-electron/src/serviceControl.ts`

It should expose:
- `getStatus()`
- `startAll()`
- `stopAll()`
- `restartAll()`
- `restartApi()`
- `openApiLog()`
- `openFrontendLog()`

Implementation can begin by shelling out to the existing controller scripts.

---

## Security model

Use a standard Electron hardening baseline:
- `contextIsolation: true`
- `nodeIntegration: false`
- preload-only IPC surface
- explicit allowlist of IPC methods
- no arbitrary shell execution from renderer
- no direct filesystem access from renderer except via audited IPC endpoints

The renderer should never receive raw process-spawn capability.

---

## Testing strategy

### Test layer 1: existing service-layer regression tests
These already protect the runtime separation and should stay green.

Run:
- `uv run --with pytest python -m pytest platform/api/tests/test_biomodstack_services.py -q`

### Test layer 2: frontend routing tests
Add tests for basename and browser/Electron compatibility.

Files:
- Create: `platform/frontend/tests/routerBasePath.test.tsx`

Cases:
- `/bms/` browser path resolves dashboard route
- `/bms/results` resolves correctly
- Electron-provided basename `/` resolves correctly
- `Open in Browser` URL generation preserves the correct route

### Test layer 3: Electron main/preload unit tests
Files:
- Create: `platform/desktop-electron/tests/serviceControl.test.ts`
- Create: `platform/desktop-electron/tests/windowUrl.test.ts`

Cases:
- correct URL selected in dev vs production
- status/start/stop commands map to the existing controller correctly
- browser fallback URL is always available
- no IPC method exposes unrestricted shell execution

### Test layer 4: manual workstation validation
Required manual checks on this Pop!_OS workstation:
1. start `biomodstack.target`
2. launch Electron shell
3. verify API/frontend stay under `systemd --user`, not Electron scope
4. open main UI in Electron
5. click “Open in Browser” and verify the same app opens externally
6. close Electron and verify services remain alive
7. relaunch Electron and reconnect cleanly
8. verify structure/plot/molecule-heavy views still render under Electron’s Chromium

---

## Phased roadmap

### Phase 1: frontend routing/base-path cleanup
**Objective:** make the same React app load correctly in both `/bms/` browser mode and Electron-local mode.

Files:
- Modify: `platform/frontend/src/main.tsx`
- Create: `platform/frontend/src/runtime/navigation.ts`
- Add tests: `platform/frontend/tests/routerBasePath.test.tsx`

Acceptance gate:
- browser mode still works
- no route regressions
- basename is explicit and test-covered

### Phase 2: create minimal Electron shell package
**Objective:** boot a hardened Electron app that loads the existing BioModStack UI.

Files:
- Modify: `pnpm-workspace.yaml`
- Create: `platform/desktop-electron/package.json`
- Create: `platform/desktop-electron/tsconfig.json`
- Create: `platform/desktop-electron/src/main.ts`
- Create: `platform/desktop-electron/src/preload.ts`
- Create: `platform/desktop-electron/src/windowState.ts`

Acceptance gate:
- `pnpm` can install/build the Electron workspace package
- Electron window opens the BioModStack UI
- renderer is hardened

### Phase 3: wire service controls and browser escape hatch
**Objective:** let Electron act as a native client/controller without becoming supervisor.

Files:
- Create: `platform/desktop-electron/src/serviceControl.ts`
- Create: `platform/desktop-electron/src/menu.ts`
- Create: `platform/desktop-electron/src/tray.ts`
- Create: `platform/desktop-electron/tests/serviceControl.test.ts`

Acceptance gate:
- start/stop/restart/status actions work through the existing controller
- “Open in Browser” works from Electron
- closing Electron does not stop services

### Phase 4: package and operator polish
**Objective:** make the shell feel like a real workstation app.

Files:
- Add packaging config inside `platform/desktop-electron/package.json`
- Add icons/assets under `platform/desktop-electron/assets/`
- Update docs

Acceptance gate:
- packaged app launches cleanly on Pop!_OS
- menus, tray, logs, and results-folder affordances work
- browser mode remains documented and supported

---

## Exact implementation notes

### `platform/desktop-electron/package.json`
Should include scripts like:
- `dev`
- `build`
- `start`
- `test`

and dependencies/devDependencies for:
- `electron`
- `typescript`
- a lightweight test runner if desired (`vitest` is fine)
- optionally `electron-builder` later, but not required in Phase 2

### `platform/desktop-electron/src/main.ts`
Responsibilities:
- create hardened `BrowserWindow`
- compute target BioModStack URL
- install app menu
- forward explicit IPC service-control requests
- support external-browser open action

### `platform/desktop-electron/src/preload.ts`
Expose a tiny typed surface like:
- `window.biomodstack.getStatus()`
- `window.biomodstack.startAll()`
- `window.biomodstack.restartApi()`
- `window.biomodstack.openInBrowser()`

### `platform/frontend/src/main.tsx`
Modify to consume an explicit basename helper instead of assuming raw `BrowserRouter`
state.

### `platform/frontend/src/App.tsx`
Likely minimal route changes only; avoid app-wide rewrites unless basename work proves insufficient.

---

## Validation commands for the eventual implementation

Repo/workspace checks:
- `cd /home/dalab/biomodstack/biomodstack && pnpm install`
- `cd /home/dalab/biomodstack/biomodstack && pnpm --filter frontend build`
- `cd /home/dalab/biomodstack/biomodstack && pnpm --filter desktop-electron test`
- `cd /home/dalab/biomodstack/biomodstack && pnpm --filter desktop-electron dev`

Runtime checks:
- `python3 scripts/manage_desktop_services.py status`
- `systemctl --user status biomodstack-api.service biomodstack-frontend.service --no-pager`
- `curl -fsS http://127.0.0.1:8000/api/health`

Process-ownership checks:
- verify Electron is not the parent/supervisor of the API/frontend runtime
- verify API lives under `biomodstack-api.service`
- verify frontend lives under `biomodstack-frontend.service`

---

## Recommendation summary

For this codebase, an Electron port is reasonable if the goal is:
- Chromium-consistent rendering for complex scientific web surfaces
- a packaged workstation app with native menus/tray/notifications
- minimal risk to the existing React app

But the correct product shape is:
- one BioModStack web UI
- one service-owned runtime
- multiple optional shells
  - browser
  - Electron
  - current GTK/tray tools during transition

That preserves the web view access option cleanly and avoids rebuilding the original supervision bug in a shinier wrapper.
