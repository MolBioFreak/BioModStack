# BioModStack Electron Shell Port Implementation Plan

> **For Hermes:** Use subagent-driven-development for execution only after this roadmap is approved. This document is intentionally more detailed than the earlier draft because the shell now has to fit the live core-runtime containerization seam, not just the pre-container desktop launcher model.

**Goal:** Add an optional Electron desktop shell for BioModStack that wraps the existing UI and workstation controls while preserving direct browser/web access, allowing the operator to choose either Electron or the hosted web UI, and working cleanly against both the current dev runtime and the new containerized BMS core runtime.

**Architecture:** BioModStack runtime ownership stays outside Electron. On the workstation, `systemd --user` remains the owner of the active runtime: either the dev pair (`biomodstack-api.service` + `biomodstack-frontend.service`) or the container runtime (`biomodstack-core-runtime.service` running `compose.core-runtime.yml` for `bms-api` + `bms-web`). Electron is a local client/controller, while thin launcher/control surfaces decide whether to raise Electron, raise the hosted web UI in the system browser, or start the runtime without raising either. Those surfaces discover runtime mode, URLs, and launch preferences through a structured Python control-plane contract, load the same web UI that the browser uses, and never become the owner of Nextflow, BioXP, or long-lived API/web processes.

**Tech Stack:** Electron, TypeScript, `tsup`, `vitest`, `jsdom`, pnpm workspace, existing Vite/React frontend in `platform/frontend`, Python service manager in `biomodstack_services.py`, CLI control surface in `scripts/manage_desktop_services.py`, Docker Compose core runtime in `compose.core-runtime.yml`, and workstation shell architecture in `docs/Desktop_Runtime_and_Shell_Architecture.md`.

---

## 1. Why this spec had to become more complete

The earlier Electron plan was directionally right, but it treated Electron mostly as a shell around the current web app. That is no longer enough context.

The repo now already has a concrete core-runtime container seam:

- `compose.core-runtime.yml`
- `docker/api.Dockerfile`
- `docker/web.Dockerfile`
- `docker/web/nginx.conf`
- `.env.core-runtime.example`
- `scripts/run_biomodstack_core_runtime.sh`
- `biomodstack-core-runtime.service` support in `biomodstack_services.py`
- runtime-mode switching through `scripts/manage_desktop_services.py --runtime dev|container`

That changes the correct shape of the Electron project:

1. the shell must treat the containerized core runtime as the packaged/default workstation target,
2. the shell must not invent a second production frontend stack,
3. the shell must discover whether it is talking to dev mode or container mode,
4. the shell must keep the browser path stable and first-class,
5. the shell must stay outside the host-coupled workflow and hardware edges.

This plan is therefore explicitly container-aware.

---

## 2. Repo-grounded current state

These are the specific repository facts this spec is based on.

### 2.1 Runtime/control layer facts

Current service ownership already exists in repo code:

- `biomodstack_services.py`
  - `resolve_runtime_mode(...)`
  - `runtime_service_names(...)`
  - `all_runtime_service_names()`
  - `incompatible_runtime_service_names(...)`
  - `start_all(...)`
  - `stop_all(...)`
  - `restart_all(...)`
  - `status_lines(...)`
- `scripts/manage_desktop_services.py`
  - supports `start|stop|restart|restart-api|status`
  - supports `--runtime dev|container`
- `docs/Desktop_Runtime_and_Shell_Architecture.md`
  - already states that shells should be clients, not supervisors

Important current limitation:

- `scripts/manage_desktop_services.py` currently emits human-readable text, not structured JSON.
- That is good enough for shell scripts and humans, but not yet good enough for a runtime-aware Electron client.

### 2.2 Core containerization facts

Current core container runtime:

- `compose.core-runtime.yml`
  - `bms-api`
  - `bms-web`
  - host port contract preserved through
    - `127.0.0.1:${BMS_API_HOST_PORT:-8000}:8000`
    - `127.0.0.1:${BMS_WEB_HOST_PORT:-5173}:80`
- `scripts/run_biomodstack_core_runtime.sh`
  - uses `compose.core-runtime.yml`
  - supports `.env.core-runtime.local` via `BMS_CORE_RUNTIME_ENV_FILE`
  - defaults `BMS_API_HOST_PORT=8000`
  - defaults `BMS_WEB_HOST_PORT=5173`
- `biomodstack_services.py`
  - already models container mode as `biomodstack-core-runtime.service`
  - already stops incompatible services when switching runtime modes

### 2.3 Frontend routing facts

Current frontend behavior:

- `platform/frontend/vite.config.ts`
  - production base path is `/bms/`
  - dev base path is `/`
- `platform/frontend/src/main.tsx`
  - uses `BrowserRouter` with no explicit `basename`
  - contains raw boot-time pathname logic:
    - `window.location.pathname.startsWith('/designer')`
- `platform/frontend/src/App.tsx`
  - defines routes as application-relative paths (`/`, `/submit`, `/results`, `/designer`, `/infra`, `/bioxp`)

Interpretation:

- the browser app is already close to dual-shell-ready,
- but basename handling is still implicit,
- and there is at least one raw pathname assumption that will break under `/bms/` if left unnormalized.

### 2.4 BioXP and Nextflow boundaries

These remain outside the shell and outside the first container wave.

`platform/api/routers/bioxp.py` is now explicitly linkage/proxy oriented:

- the robot should own the BioXP runtime locally,
- BMS links to that runtime over HTTP,
- workstation-owned daemon start/stop returns 409,
- linkage state persists on the workstation,
- normal BMS operation is no longer SSH-supervisor-driven.

`platform/api/services/nextflow.py` is still host-shaped:

- uses host path helpers such as `get_code_root()`, `get_data_root()`, `get_weights_root()`, `get_msa_cache_dir()`
- still injects workstation-specific profile suffixes like `workstation_ryzen7960x`
- still references host-oriented container/apptainer directories
- still invokes `apptainer`
- resume behavior is tied to host work/cache directories

Interpretation:

- BioXP is a linked host/hardware adapter surface, not an Electron responsibility.
- Nextflow remains a host-native execution edge, not a shell responsibility.
- Electron must not grow logic that bypasses those boundaries.

### 2.5 Viewer compatibility facts

Current viewer behavior is a strong argument for Electron over a Tauri-first shell in this repo:

- `platform/frontend/src/lib/molstar-loader.ts` loads installed `pdbe-molstar` assets
- `platform/frontend/vite.config.ts` explicitly pins a stable `pdbe-molstar` package alias
- the frontend also depends on Blueprint-heavy React UI, Plotly, IGV, and other web-viewer-heavy surfaces

Interpretation:

- the repo already has a compatibility-sensitive frontend,
- so an Electron shell is a reasonable first serious shell candidate if we want Chromium consistency.

---

## 3. Product shape and non-negotiables

1. Browser access remains permanent and first-class.
   - BioModStack must still work directly in a browser.
   - Operators must be able to choose the hosted web UI directly instead of Electron.
   - Whether the hosted web UI automatically pops up after runtime start must be controlled by an explicit default toggle, not by accidental launcher behavior.
   - The shell must never become the only supported way to use the product.

2. Electron is a shell, not the runtime owner.
   - Electron may request start/stop/restart actions through the service layer.
   - Electron must not directly spawn long-lived uvicorn, Vite, or Docker Compose children as its own managed subprocess tree.

3. Containerized BMS core is the packaged workstation default.
   - For a packaged/operator-focused Electron app, container runtime should be the default preferred runtime mode once installed.
   - Dev mode remains available for developers and debugging.

4. One product UI, multiple shells and launch surfaces.
   - The React app remains the main UI.
   - Operators can choose among:
     - hosted web UI in the system browser
     - Electron shell loading that same hosted web UI
     - background/runtime-only start that raises neither surface
   - Explicit launch requests must always override saved defaults.
   - Electron adds native affordances: menu, tray, notifications, file-manager integration, browser escape hatch.

5. No second production frontend pipeline in v1.
   - Electron v1 should load the same locally served web UI as the browser.
   - Do not introduce a second packaged renderer asset pipeline before the core container runtime is stable.

6. Nextflow and BioXP stay outside the shell.
   - Electron must not own workflow execution semantics.
   - Electron must not own robot daemon lifecycle.

7. Local-only allowlist in v1.
   - Electron v1 only loads local workstation BioModStack URLs unless an explicit future remote mode is designed.
   - External navigation opens in the system browser.

8. Closing Electron must not stop BioModStack runtime.
   - The runtime remains alive under `systemd --user` if it was already running.

9. Rollout must be non-disruptive by default.
   - Existing service-control/operator entrypoints must keep their current semantics until an explicit cutover is approved.
   - New browser-vs-Electron launch behavior should ship behind an opt-in launcher path or explicit flags first.
   - Packaged defaults for new installs must not silently rewrite the behavior of the current live workstation checkout.

---

## 4. Runtime matrix the shell must support

| Runtime mode | Owner | Frontend URL | Router basename | Default audience | Notes |
| --- | --- | --- | --- | --- | --- |
| `dev` | `biomodstack-api.service` + `biomodstack-frontend.service` | `http://127.0.0.1:5173/` | `/` | developers | current Vite-served frontend |
| `container` | `biomodstack-core-runtime.service` -> `compose.core-runtime.yml` | `http://127.0.0.1:5173/bms/` | `/bms/` | workstation operators / packaged shell | `bms-web` serves built assets and proxies `/api/` |
| future remote | not in scope for v1 | not in scope | not in scope | future | browser remains the preferred remote shell for now |

### 4.1 Launch-surface policy

The runtime matrix above is separate from the operator-facing launch surface. v1 should explicitly support these surface choices:

| Launch surface | Typical entry path | What gets raised | Default-open behavior |
| --- | --- | --- | --- |
| `browser` | `start_ui.sh`, GTK panel, tray, future launcher script | system browser at `browser_url` | initial workstation default |
| `electron` | Electron app / packaged shell | Electron window loading `frontend_url` | suppress browser auto-open for that invocation |
| `none` | service-control path / background start | nothing | used when the user wants runtime only |

Rules:

- explicit user choice beats stored defaults
- the initial shared default should be `default_surface = browser`
- the hosted-web popup behavior should be controlled by a persisted toggle such as `auto_open_hosted_web_on_start`
- if the user explicitly launches Electron, do not also pop the browser unless a future `also-open-browser` option is intentionally added
- if hosted-web auto-open is off, a generic start may leave the runtime running in the background while both browser and Electron remain manually launchable
- during the initial rollout, existing stable operator entrypoints should keep their current behavior unless the user takes an explicit opt-in launcher path or passes explicit surface flags
- packaged defaults are allowed for new packaged installs, but they do not justify surprise behavior changes on the current live workstation checkout

Important rule:

- the Electron shell must not hardcode a single path shape and hope it works.
- it must know whether it is loading dev mode (`/`) or container mode (`/bms/`).

---

## 5. Shell-to-runtime contract required before writing real Electron code

The biggest missing piece is not Electron itself. It is a structured control-plane contract that tells the shell what runtime exists and how to talk to it.

### 5.1 Required Python-side additions

Modify these files first:

- `biomodstack_services.py`
- `scripts/manage_desktop_services.py`

Add a structured runtime descriptor in Python, not in Electron.

Recommended new function in `biomodstack_services.py`:

```python
def runtime_descriptor(project_root: Path | None = None, runtime_mode: str | None = None) -> dict[str, Any]:
    ...
```

Recommended responsibilities:

- resolve runtime mode (`dev` or `container`)
- report active service owner(s)
- report frontend URL
- report browser URL
- report router basename
- report API URL
- report supported launch surfaces
- report launch preferences
- report readiness flags
- report log descriptors
- report important local paths
- report which control actions are supported

Recommended JSON shape:

```json
{
  "runtime_mode": "container",
  "runtime_active": true,
  "runtime_manager": "systemd-user",
  "api_url": "http://127.0.0.1:8000",
  "frontend_origin": "http://127.0.0.1:5173",
  "frontend_url": "http://127.0.0.1:5173/bms/",
  "browser_url": "http://127.0.0.1:5173/bms/",
  "router_basename": "/bms/",
  "supported_launch_surfaces": ["browser", "electron", "none"],
  "launch_preferences": {
    "default_surface": "browser",
    "auto_open_hosted_web_on_start": true
  },
  "health": {
    "api_ready": true,
    "frontend_ready": true
  },
  "services": [
    {"name": "biomodstack-core-runtime.service", "active": true}
  ],
  "logs": [
    {"id": "runtime", "label": "Core runtime log", "path": "~/.local/state/biomodstack/logs/core-runtime.log"}
  ],
  "paths": {
    "project_root": "/home/dalab/biomodstack/biomodstack",
    "state_dir": "/mnt/BioModStack"
  },
  "capabilities": {
    "open_in_browser": true,
    "restart_all": true,
    "restart_api": true,
    "stop_all": true
  }
}
```

Container mode and dev mode must differ only in runtime-specific fields such as:

- `runtime_mode`
- `frontend_url`
- `router_basename`
- `services`
- `logs`

The shell should not have to rediscover this logic itself.

### 5.2 Required CLI surface

Extend `scripts/manage_desktop_services.py` to support structured output:

- `status --json`
- optionally `start --json`
- optionally `stop --json`
- optionally `restart --json`
- optionally `restart-api --json`

Recommendation:

- at minimum, `status --json` is required before Electron work starts,
- action commands can initially keep plain success output if the shell re-queries `status --json` afterward,
- but structured action output is preferred.

### 5.3 Why this matters for containerization

Without a Python-generated runtime descriptor, the Electron shell would have to guess all of this:

- whether container runtime or dev runtime is active,
- whether to load `/` or `/bms/`,
- which service is authoritative,
- which log surface is relevant,
- whether browser fallback should open `/` or `/bms/`.

That guesswork would become fragile immediately as the core runtime evolves.

### 5.4 Required launcher/control-surface policy

The runtime descriptor is necessary, but it is not sufficient. The repo also needs one thin place that decides whether the operator asked to raise Electron, raise the hosted web UI, or raise nothing.

Recommended files:

- Create: `scripts/launch_biomodstack_ui.py`
- Modify cautiously: `start_ui.sh`
- Later adapt: `biomodstack_panel.py`
- Later adapt: `biomodstack_tray.py`

Recommended responsibilities:

- call `scripts/manage_desktop_services.py start --runtime ...` for runtime ownership
- read `status --json` or the equivalent runtime descriptor afterward
- respect a shared preference file such as `~/.config/biomodstack/launch_preferences.json`
- if the chosen surface is `browser`, open `browser_url`
- if the chosen surface is `electron`, hand off to the Electron app / packaged desktop entry without also opening the browser
- if the chosen surface is `none`, raise no UI surface
- treat explicit `--surface browser|electron|none` as higher priority than stored defaults
- interpret `auto_open_hosted_web_on_start=false` to mean that a generic browser-first start does not automatically pop the hosted web UI
- preserve the current service-control behavior of plain `start_ui.sh start` during the initial rollout unless explicit launch flags or an approved cutover change that contract

Recommended shared preference shape:

```json
{
  "default_surface": "browser",
  "auto_open_hosted_web_on_start": true
}
```

Recommended operator-facing CLI shape during the safe rollout phase:

- `python3 scripts/launch_biomodstack_ui.py --runtime container`
- `python3 scripts/launch_biomodstack_ui.py --runtime container --surface browser`
- `python3 scripts/launch_biomodstack_ui.py --runtime container --surface electron`
- `python3 scripts/launch_biomodstack_ui.py --runtime container --surface none`
- `./start_ui.sh start --runtime container` remains the existing service-control path until cutover is explicitly approved

Recommended rule for existing stable entrypoints:

- initial rollout should add `scripts/launch_biomodstack_ui.py` as the opt-in UI launcher
- plain `start_ui.sh start` should remain a service-control entrypoint with current behavior until explicit approval to change it
- `start_ui.sh stop|restart|restart-api|status` continue to delegate to `scripts/manage_desktop_services.py`
- only after validation and explicit approval should `start_ui.sh` gain launch-aware default behavior, if that change is still desired

This keeps runtime ownership in the service layer while still giving the user the browser-vs-Electron choice they actually care about without disrupting the current operating path.

### 5.5 Required rollout guardrails for live workstation use

The first implementation pass must be safe to land on an actively used workstation checkout.

Guardrails:

- do not change `systemd --user` service names, owners, or readiness URLs as part of the launcher-surface work
- do not make Electron required for any existing browser-first or service-control flow
- do not surprise-open browser or Electron from pre-existing operator entrypoints during the first rollout pass
- ship the new launch-surface behavior through an opt-in path first, validate it, then decide whether any default cutover is desirable
- packaged-Electron defaults should be treated as new-install behavior, not as retroactive behavior changes for the current workstation checkout

This section is intentionally conservative because the goal is to add a new launch surface without perturbing ongoing operation.

---

## 6. Frontend routing and basename hardening spec

This must happen before a real shell integration.

### 6.1 Files to create or modify

Create:

- `platform/frontend/src/runtime/navigation.ts`
- `platform/frontend/tests/routerBasePath.test.tsx`

Modify:

- `platform/frontend/src/main.tsx`
- `platform/frontend/package.json`

Possibly modify if needed after test proof:

- `platform/frontend/src/App.tsx`
- any additional file found to use raw `window.location.pathname`

### 6.2 Required helper behavior

`platform/frontend/src/runtime/navigation.ts` should own route/base-path logic.

Recommended responsibilities:

- `getRouterBasename()`
- `getCurrentAppPath()`
- `isAppPath(prefix: string)`
- `joinBrowserUrl(baseUrl: string, appPath: string)`

Resolution order for basename:

1. `window.__BMS_ROUTER_BASENAME__` if explicitly injected later,
2. `import.meta.env.BASE_URL` if usable,
3. fallback `/`.

### 6.3 Immediate bug to remove

Current `platform/frontend/src/main.tsx` contains:

- `window.location.pathname.startsWith('/designer')`

That is wrong under `/bms/designer` unless normalized.

Phase 1 must replace that raw check with logic based on the shared helper so the app behaves consistently in both:

- browser dev mode (`/designer`)
- container/browser production mode (`/bms/designer`)
- Electron loading either of those modes

### 6.4 Test harness decision

The current frontend package does not yet define a React/jsdom unit-test harness. For route/base-path tests, this spec chooses:

- `vitest`
- `jsdom`
- `@testing-library/react`
- optionally `@testing-library/jest-dom`

Add them explicitly to `platform/frontend/package.json`.

Recommended frontend package script additions:

- `test:unit`
- `test:router`

### 6.5 Required routing tests

Add `platform/frontend/tests/routerBasePath.test.tsx` with at least these cases:

1. basename `/bms/` resolves dashboard route correctly
2. basename `/bms/` resolves `/results`
3. basename `/` resolves dashboard route correctly
4. normalized app path detects `/designer` correctly in both `/designer` and `/bms/designer`
5. browser fallback URL generation preserves the current app route when given container mode base URL

Acceptance gate:

- the same React app works unchanged under both `/` and `/bms/`
- no raw pathname assumption remains in app bootstrap logic

---

## 7. Install-root discovery spec

This is the earlier plan’s biggest missing operational detail.

A packaged Electron app will not necessarily live inside the BioModStack repo tree. But the service-control scripts and compose stack do.

Therefore the shell needs a clear way to find the BioModStack install/project root.

### 7.1 Required behavior

Create:

- `platform/desktop-electron/src/installRoot.ts`
- `platform/desktop-electron/tests/installRoot.test.ts`

Install-root resolution order should be:

1. explicit environment override
   - `BMS_HOME`
   - or a new shell-specific `BMS_PROJECT_ROOT`
2. persisted Electron shell setting (first-launch configured project root)
3. development inference relative to the workspace package location
4. known workstation default install path if one is later standardized
5. if none works, show a setup/error screen instead of trying to guess blindly

### 7.2 Required validity checks

A discovered project root is only valid if it contains at least:

- `compose.core-runtime.yml`
- `scripts/manage_desktop_services.py`
- `biomodstack_services.py`

Optional stronger validation:

- `platform/frontend/package.json`
- `platform/api/main.py`

### 7.3 Why this matters

Without explicit install-root discovery:

- a packaged Electron app cannot safely find the control-plane CLI,
- runtime start/stop actions become unreliable,
- containerization-aware behavior becomes installation-specific folklore.

This is a real implementation requirement, not paperwork.

---

## 8. Electron workspace/package spec

### 8.1 Repo layout

Create a separate workspace package:

```text
platform/desktop-electron/
├── package.json
├── tsconfig.json
├── tsup.config.ts
├── src/
│   ├── main.ts
│   ├── preload.ts
│   ├── installRoot.ts
│   ├── runtimeDescriptor.ts
│   ├── serviceControl.ts
│   ├── windowUrl.ts
│   ├── menu.ts
│   ├── tray.ts
│   ├── security.ts
│   └── bootstrap/
│       └── loading.html
└── tests/
    ├── installRoot.test.ts
    ├── runtimeDescriptor.test.ts
    ├── serviceControl.test.ts
    └── windowUrl.test.ts
```

Modify:

- `pnpm-workspace.yaml`

Add:

- `platform/desktop-electron`

### 8.2 Why not bundle a second renderer in v1

Electron v1 should not add a second renderer asset pipeline.

Rationale:

- the repo now already has a production web frontend shape through `bms-web` + nginx,
- the browser and Electron should literally consume the same app surface,
- packaging a separate renderer before the container runtime settles would create two production entrypoints to maintain,
- it would blur the architecture boundary between “core runtime” and “optional shell”.

So for v1:

- Electron loads the locally served BioModStack UI,
- Electron does not bundle the BioModStack frontend itself,
- future packaged-renderer support is deferred until the core runtime contract is stable.

### 8.3 Recommended package dependencies

Runtime dependency:

- `electron`

Development dependencies:

- `typescript`
- `tsup`
- `vitest`
- `@types/node`
- `wait-on`
- `concurrently`

Intentionally defer:

- `electron-builder`
- `electron-forge`
- `electron-vite`

Reason:

- v1 is a shell around an external local web app, not a second Vite renderer.

### 8.4 Recommended package scripts

Recommended `platform/desktop-electron/package.json` scripts:

- `build`
- `watch`
- `start`
- `dev`
- `test`

Concrete shape:

- `build`: compile main + preload with `tsup`
- `watch`: watch build output
- `start`: `electron dist/main.js`
- `dev`: run watch build plus launch Electron against a running local BioModStack runtime
- `test`: `vitest run`

Important dev note:

- do not make Electron package responsible for starting frontend/api directly in its own Node process tree
- if dev convenience scripts start runtime, they must do so through the same Python control-plane contract

---

## 9. Main-process behavior spec

### 9.1 `src/main.ts`

Responsibilities:

- discover/install BioModStack project root
- resolve runtime descriptor from Python control plane
- create a hardened `BrowserWindow`
- show a bootstrap/loading page while waiting for runtime readiness
- load the correct BioModStack local URL when ready
- install native menu and tray integration
- route explicit start/stop/restart actions through the service-control wrapper
- open external browser for escape hatch actions

### 9.2 Required startup flow

Recommended startup flow:

1. resolve project root
2. treat Electron launch itself as an explicit `electron` surface choice
3. query runtime descriptor for preferred runtime mode
4. if runtime is inactive:
   - optionally request start through service control
   - poll readiness until timeout
   - otherwise show bootstrap page with explicit controls
5. once runtime is ready, compute the final URL and load it in the window
6. if the runtime later fails, keep Electron alive and show actionable diagnostics instead of crashing

### 9.3 Preferred runtime mode policy

Default policy:

- packaged/operator shell default: `container`
- source/dev shell default: `dev`
- allow user override in shell settings

Why this is correct:

- it aligns the packaged shell with the containerized BMS core plan,
- it keeps dev workflows simple for developers,
- it avoids pretending those two runtime shapes are identical.

### 9.4 Interaction with hosted-web default-open logic

Electron launch must be treated as an explicit surface choice, not as a generic “start BioModStack somehow” request.

Rules:

- launching Electron should not also auto-open the hosted web UI by default
- the shared `auto_open_hosted_web_on_start` toggle only applies to browser-first control surfaces unless the user explicitly asks for both
- “Open in Browser” from inside Electron remains available and should preserve the current route
- if runtime is already running, Electron should reconnect without retriggering browser auto-open behavior

---

## 10. Service-control wrapper spec

### 10.1 `src/serviceControl.ts`

This file must be thin and intentionally boring.

Responsibilities:

- execute the Python CLI at the configured project root
- pass `--runtime dev|container`
- request `status --json`
- request start/stop/restart/restart-api actions
- parse structured JSON or surface actionable errors

It must not:

- shell out to `docker compose` directly
- shell out to `systemctl` directly
- infer runtime topology from ports and guesses

### 10.2 Recommended wrapper methods

```ts
getStatus(runtimeMode?: 'dev' | 'container')
startAll(runtimeMode?: 'dev' | 'container')
stopAll(runtimeMode?: 'dev' | 'container')
restartAll(runtimeMode?: 'dev' | 'container')
restartApi(runtimeMode?: 'dev' | 'container')
```

Optional later methods:

```ts
listLogs(runtimeMode?: 'dev' | 'container')
openLog(logId: string)
```

### 10.3 Containerization-aware logging policy

The earlier plan mentioned `openApiLog()` and `openFrontendLog()`. That is too dev-shaped.

Revised rule:

- dev mode logs:
  - API log
  - frontend log
- container mode logs:
  - core runtime log first
  - later optionally per-container logs if we expose them safely

So the Python runtime descriptor should return a list of logs, not a hardcoded API/frontend pair.

---

## 11. Window URL resolution spec

### 11.1 `src/windowUrl.ts`

This file should take a runtime descriptor and decide the actual URL Electron loads.

Required inputs:

- `frontend_url`
- `router_basename`
- optionally last app path visited

Required outputs:

- initial URL for `BrowserWindow.loadURL(...)`
- browser escape-hatch URL for the current app path

### 11.2 Required URL rules

For v1, only these local URL bases are valid:

- `http://127.0.0.1:5173/`
- `http://localhost:5173/`
- `http://127.0.0.1:5173/bms/`
- `http://localhost:5173/bms/`

The runtime descriptor should supply the canonical choice.

### 11.3 Route preservation rule

When using “Open in Browser”, the shell must preserve the current app route.

Examples:

- if Electron currently displays `/submit` under dev mode, open `http://127.0.0.1:5173/submit`
- if Electron currently displays `/bms/results`, open `http://127.0.0.1:5173/bms/results`

This is another reason the basename helper and current-app-path helper must exist.

---

## 12. Preload and security model

### 12.1 Hardening baseline

`src/main.ts` and `src/security.ts` must enforce:

- `contextIsolation: true`
- `nodeIntegration: false`
- `sandbox: true` if feasible with the chosen preload surface
- no arbitrary command execution from the renderer
- strict navigation allowlist for local BioModStack URLs
- new-window / external links open in the system browser, not in permissive Electron child windows

### 12.2 `src/preload.ts`

Keep the preload surface minimal.

Preferred v1 preload surface:

```ts
window.biomodstackShell = {
  isDesktopShell: true,
  getRuntimeDescriptor: () => Promise<RuntimeDescriptor>,
  openInBrowser: () => Promise<void>,
  copyLocalUrl: () => Promise<void>
}
```

Do not expose generic process execution.

Important scope decision:

- native menu and tray can own most service-control actions in v1,
- the React renderer does not need full restart/start/stop privileges injected into it on day one.

This keeps the shell thinner and safer.

### 12.3 Navigation restrictions

The Electron window should only load:

- the bootstrap page packaged with the shell, or
- the allowed local BioModStack URLs returned by the runtime descriptor.

Any other outbound navigation must:

- be blocked in-window,
- be redirected to `shell.openExternal(...)`.

---

## 13. Menu, tray, and native affordance spec

### 13.1 `src/menu.ts`

Required menu items in v1:

- Open in Browser
- Copy Local URL
- Start Runtime
- Stop Runtime
- Restart Runtime
- Restart API
- Open Logs
- Open Results Folder
- Quit Shell

Menu actions should call main-process/service-control functions directly.

Once the shared launcher preferences exist, Electron should also expose them rather than inventing a private Electron-only setting model:

- Default Launch Surface: Browser / Electron / Background Only
- Auto-open Hosted Web UI on Generic Start: on/off

Those settings must write the shared launcher preference store so GTK/tray/browser-first entrypoints and Electron stay consistent.

### 13.2 `src/tray.ts`

Tray is optional in the first PR, but if present it should mirror the same control surface:

- current runtime status
- open window
- open in browser
- restart runtime
- quit shell

### 13.3 Results-folder behavior

For “Open Results Folder”, use the runtime descriptor’s state/path information instead of duplicating path logic in Electron.

---

## 14. Relationship to core-runtime containerization plan

This Electron plan intentionally assumes the first-wave containerization spec is real and should remain the foundation.

Reference document:

- `docs/plans/archive/2026-04-19-core-runtime-containerization-spec.md`

### 14.1 What Electron should rely on from that plan

Electron should rely on these core-runtime truths:

- container runtime is `bms-api` + `bms-web`
- the browser/web contract stays:
  - `/api`
  - `/bms/`
- host-facing local ports remain:
  - `127.0.0.1:8000`
  - `127.0.0.1:5173`
- `systemd --user` owns runtime lifetime
- the container runtime remains the owner of the web/control plane, not Electron

### 14.2 What Electron must not try to absorb

Do not use Electron to “solve” these containerization questions:

- host Nextflow execution semantics
- Apptainer ownership
- BioXP robot runtime lifecycle
- GPU workflow runtime ownership
- API-to-host workflow adapter design

Those belong to the runtime/containerization project, not the shell.

### 14.3 Why Electron should consume the web service, not bypass it

The containerization plan already gives BioModStack a production-serving shape:

- `bms-web` serves the built frontend under `/bms/`
- `bms-web` proxies `/api/`

That means the right Electron behavior is:

- consume `bms-web`
- do not embed a separate copy of frontend assets in v1
- do not bypass nginx/base-path behavior

This keeps the browser and Electron on the same application surface and makes the shell portable across runtime modes.

---

## 15. Recommended phase ordering

This is the corrected order, taking containerization seriously.

### Phase 0: control-plane descriptor and install-root groundwork

**Objective:** give the shell and browser-first launcher surfaces a reliable, runtime-aware control contract.

Files:

- Modify: `biomodstack_services.py`
- Modify: `scripts/manage_desktop_services.py`
- Document shared launch preference schema used by launcher/control surfaces
- Create tests in: `platform/api/tests/test_biomodstack_services.py` and/or a new CLI-focused test file

Deliverables:

- runtime descriptor function in Python
- `status --json`
- supported launch surfaces and shared launch preferences in the descriptor
- project-root/install-root assumptions documented
- runtime-aware log descriptor list

Acceptance gate:

- Python can report a structured descriptor for both `dev` and `container` modes
- the descriptor includes the browser-vs-Electron default/open-toggle logic without Electron having to guess it
- descriptor is enough for Electron to decide which URL to load without guessing

### Phase 1: frontend basename hardening

**Objective:** make the same React app work cleanly under `/` and `/bms/`.

Files:

- Create: `platform/frontend/src/runtime/navigation.ts`
- Modify: `platform/frontend/src/main.tsx`
- Modify: `platform/frontend/package.json`
- Create: `platform/frontend/tests/routerBasePath.test.tsx`

Acceptance gate:

- route bootstrap works in both dev and container modes
- `/designer` path detection is normalized
- browser fallback URL generation is test-covered

### Phase 2: Electron workspace skeleton

**Objective:** create a hardened Electron package that can discover project root, query runtime descriptor, and load the right local URL.

Files:

- Modify: `pnpm-workspace.yaml`
- Create: `platform/desktop-electron/package.json`
- Create: `platform/desktop-electron/tsconfig.json`
- Create: `platform/desktop-electron/tsup.config.ts`
- Create: `platform/desktop-electron/src/installRoot.ts`
- Create: `platform/desktop-electron/src/runtimeDescriptor.ts`
- Create: `platform/desktop-electron/src/windowUrl.ts`
- Create: `platform/desktop-electron/src/main.ts`
- Create: `platform/desktop-electron/src/preload.ts`
- Create: `platform/desktop-electron/src/security.ts`
- Create tests under `platform/desktop-electron/tests/`

Acceptance gate:

- Electron window opens the current BioModStack UI in both runtime modes
- runtime URL is selected through the Python descriptor, not hardcoding
- security baseline is enforced

### Phase 3: native controls and browser escape hatch

**Objective:** make the shell useful as a workstation-native client/controller while keeping browser-first launch surfaces coherent and preserving current operations by default.

Files:

- Create: `scripts/launch_biomodstack_ui.py`
- Modify cautiously: `start_ui.sh`
- Create: `platform/desktop-electron/src/serviceControl.ts`
- Create: `platform/desktop-electron/src/menu.ts`
- Create: `platform/desktop-electron/src/tray.ts`
- Possibly extend preload minimally
- Update docs

Acceptance gate:

- Start/stop/restart/restart-api work through Python control plane
- the new opt-in launcher path supports `browser|electron|none` without owning runtime lifetime itself
- plain `start_ui.sh start` preserves current service-control behavior until explicit approval to change it
- hosted web auto-open toggle works for browser-first launches on the new launcher path
- explicit Electron launch does not double-open the browser
- Open in Browser preserves route
- Close Electron and verify runtime survives

### Phase 4: container-first packaged workstation behavior

**Objective:** make the packaged shell default to container runtime and behave correctly on a workstation that uses the new core runtime, without forcing an unapproved cutover on the current live checkout.

Files:

- Update Electron settings/defaults
- Update docs/install surfaces
- Add container-mode manual validation notes

Acceptance gate:

- packaged/operator shell prefers `container` mode
- dev mode still works for developers
- packaged defaults are scoped to packaged/new-install behavior unless an explicit rollout decision changes the live workstation path
- missing project-root/runtime-install situation produces clear diagnostics instead of obscure failure

### Phase 5: polish and optional deeper shell integration

**Objective:** add operator conveniences after the core shell contract is proven.

Potential additions:

- tray polish
- notifications
- better diagnostics bundle export
- optional renderer awareness that it is inside Electron
- optional packaged-renderer experiments only after the container core contract is stable

Acceptance gate:

- no regression to browser-first support
- no regression to service ownership rules

---

## 16. Testing and validation plan

### 16.1 Backend/runtime regression anchors

Use the existing backend runtime tests as anchors and extend them for structured descriptor output.

Run:

```bash
cd /home/dalab/biomodstack/biomodstack
/home/dalab/.local/bin/uv run --directory platform/api python -m pytest \
  tests/test_biomodstack_services.py \
  tests/test_core_runtime_scaffold.py -q
```

New cases to add:

- runtime descriptor for dev mode returns `/` basename and dev service names
- runtime descriptor for container mode returns `/bms/` basename and container service name
- runtime descriptor reports `supported_launch_surfaces` and shared launch preferences
- log descriptors differ correctly between dev and container modes
- CLI `status --json` prints valid JSON

If `scripts/launch_biomodstack_ui.py` is added in the first implementation pass, also add mocked launcher tests that verify:

- `--surface browser` opens the descriptor’s `browser_url`
- `--surface electron` does not trigger hosted-browser auto-open
- `--surface none` raises no UI
- `auto_open_hosted_web_on_start=false` suppresses the browser popup for generic starts

### 16.2 Frontend routing tests

After adding `vitest` + `jsdom`:

```bash
cd /home/dalab/biomodstack/biomodstack/platform/frontend
pnpm exec vitest run tests/routerBasePath.test.tsx
```

### 16.3 Electron unit tests

```bash
cd /home/dalab/biomodstack/biomodstack
pnpm --filter desktop-electron test
```

Cases:

- install-root discovery works in source and configured-path modes
- runtime descriptor parsing handles dev and container payloads
- window URL resolution chooses `/` vs `/bms/` correctly
- route-preserving browser fallback produces correct URLs
- navigation allowlist rejects non-local URLs

### 16.4 Manual workstation validation

Container-mode validation:

```bash
cd /home/dalab/biomodstack/biomodstack
python3 scripts/manage_desktop_services.py start --runtime container
python3 scripts/manage_desktop_services.py status --runtime container
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:5173/bms/ >/dev/null
pnpm --filter desktop-electron dev
```

Manual checks:

1. verify the existing `./start_ui.sh start --runtime container` path keeps its current service-control behavior during the initial rollout and does not surprise-open browser or Electron
2. start container runtime through the new browser-first launcher path and verify the hosted web UI opens at `http://127.0.0.1:5173/bms/` when auto-open is enabled
3. disable hosted-web auto-open (or use `--surface none`) and verify the runtime starts without popping a browser window
4. launch Electron shell explicitly and verify BrowserWindow loads `http://127.0.0.1:5173/bms/` without also popping the browser
5. click “Open in Browser” and verify the same route opens externally
6. close Electron and verify `biomodstack-core-runtime.service` stays active
7. relaunch Electron and reconnect without restarting the runtime unnecessarily
8. verify Molstar/structure-heavy views still render inside Electron Chromium
9. verify BioXP cockpit still behaves as a linked HTTP proxy surface rather than a shell-supervised daemon manager

Dev-mode validation:

```bash
cd /home/dalab/biomodstack/biomodstack
python3 scripts/manage_desktop_services.py start --runtime dev
python3 scripts/manage_desktop_services.py status --runtime dev
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:5173/ >/dev/null
pnpm --filter desktop-electron dev
```

Manual checks:

1. verify the existing `./start_ui.sh start --runtime dev` path keeps its current service-control behavior during the initial rollout and does not surprise-open browser or Electron
2. verify hosted-web browser launch opens `http://127.0.0.1:5173/` when requested
3. verify hosted-web auto-open can be disabled without breaking runtime startup
4. verify Electron loads `http://127.0.0.1:5173/` when explicitly launched
5. verify `/designer` route works without basename breakage
6. verify browser escape hatch preserves route

---

## 17. Explicit non-goals for v1

Do not include these in the first implementation tranche:

- remote Electron shell support for off-workstation access
- bundling the BioModStack frontend into the Electron package itself
- moving job orchestration/business logic into Electron
- direct Docker Compose control from Node/Electron without the Python service layer
- direct Nextflow or BioXP process ownership in Electron
- replacing the browser UI path
- auto-update/install-distribution polish before runtime discovery and control contracts are stable

---

## 18. Final recommendation

For this repo, the correct Electron strategy is:

- keep the BioModStack runtime service-owned,
- align the shell with the new core container runtime instead of bypassing it,
- make container mode the packaged/operator default,
- keep browser access first-class,
- make the hosted web UI the initial default launch surface with an explicit no-popup toggle,
- still allow explicit Electron launch at any time,
- roll out new launch-surface behavior through an opt-in path first so current operations stay undisturbed,
- keep the React app as the product UI,
- add a structured Python runtime descriptor before real Electron work,
- postpone packaged-renderer ambitions until after the containerized core runtime is stable.

The key idea is simple:

- one BioModStack runtime
- one BioModStack web UI
- optional local shells on top
  - browser
  - Electron
  - existing GTK/tray surfaces during transition

That architecture is what will keep Electron useful without making it another supervision bug in a shinier form.
