# UI Surface Channel Cutover Implementation Spec

> **For Hermes:** Use `subagent-driven-development` if executing this plan task-by-task. Keep unrelated dirty worktree changes untouched.

**Goal:** Split BioModStack UI channels so the browser web UI is the instant Vite feature-development surface, while Electron and APK default to mature/stable production channels.

**Architecture:** Treat browser dev, stable hosted web, Electron, and APK as separate launch surfaces over shared API/runtime contracts. Browser dev owns Vite's documented default `http://127.0.0.1:5173/` with `strictPort` and HMR enabled by default. Stable hosted web moves to a different loopback port, default `http://127.0.0.1:18080/bms/` through `BMS_WEB_HOST_PORT`. Electron production loads that stable `/bms/` surface by default. APK stays pinned to its bundled/mobile update channel and must never follow arbitrary live dev assets.

**Tech Stack:** Python service launcher/control plane, systemd user services, Docker Compose with host-network nginx/API containers, Vite/React frontend, Electron shell, external Cordova/APK wrapper.

---

## Decision summary

### Default channel policy

| Surface | Default runtime | Default URL/channel | Purpose |
| --- | --- | --- | --- |
| Browser web UI | `dev` | `http://127.0.0.1:5173/` | Fast feature work, Vite HMR/fast reload |
| Stable hosted web | `container` | `http://127.0.0.1:18080/bms/` | Built production bundle served by nginx/core runtime |
| Electron | `container` | `http://127.0.0.1:18080/bms/` | Mature desktop/operator shell over stable hosted web |
| APK/Cordova | pinned bundled UI plus explicit OTA channel | Cordova localhost shell + versioned mobile bundle | Mature mobile/operator shell with rollback |

### Hard rule

`5173` belongs to browser dev/Vite. Production/stable hosting must not bind `5173` by default. If anything other than Vite owns `5173`, the browser dev workflow is not instant and the cutover is not done.

### Why this is normal

This is the standard web/desktop/mobile split:

- Browser dev uses Vite/webpack dev server with HMR.
- Production web uses built static assets from nginx/CDN/app server.
- Electron dev may opt into the dev server, but Electron production loads a stable production build/channel.
- Mobile/APK consumes bundled or signed/versioned bundles, not arbitrary live dev assets.

BioModStack's production Electron path intentionally loads a local production server (`/bms/`) rather than directly bundling all assets into Electron. That remains appropriate because the app already depends on local API/runtime services, nginx basename behavior, large upload/proxy settings, and operator service controls.

---

## Current code-review findings that drive this plan

These are observed from the current repo, not desired future behavior.

1. Production nginx/core-runtime currently occupies the Vite dev port.
   - `biomodstack_services.py` has `FRONTEND_PORT = 5173` and `FRONTEND_URL = http://127.0.0.1:5173/bms/`.
   - `runtime_frontend_origin()` does not accept runtime mode, so dev and container share the same origin.
   - `docker/web/nginx.conf` listens on `127.0.0.1:5173`.
   - `docker/web.Dockerfile` exposes `5173`.
   - `compose.core-runtime.yml` healthchecks `http://127.0.0.1:5173/bms/`.
   - `scripts/run_biomodstack_core_runtime.sh` defaults `BMS_WEB_HOST_PORT` to `5173`.
   - `biomodstack_runtime_profile.py` defaults `DEFAULT_WEB_HOST_PORT = 5173`.

2. Browser dev is not currently optimized for instant feature iteration.
   - `scripts/run_biomodstack_frontend.sh` starts Vite on `5173`, which is correct, but production nginx also binds that port.
   - `platform/frontend/vite.config.ts` sets `server.hmr: false`, so local edits get full reload behavior at best, not normal Vite hot-module replacement.
   - `vite.config.ts` does not currently set `strictPort: true`, so Vite can hide port drift by auto-incrementing.

3. Electron is a useful shell, but not yet mature enough to call production-hardened.
   - `platform/desktop-electron/src/windowState.ts` defaults container `frontendOrigin` to `http://127.0.0.1:5173`.
   - `scripts/launch_biomodstack_ui.py` passes `BMS_FRONTEND_ORIGIN` from the shared runtime descriptor, which currently reports `5173` for container.
   - `platform/desktop-electron/src/main.ts` uses hardened `webPreferences` (`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`), which is good.
   - However, privileged `ipcMain.handle(...)` calls do not validate `event.senderFrame.url` before service control actions.
   - `setWindowOpenHandler` opens any URL externally without protocol allowlisting.
   - Main-frame navigation is not explicitly constrained to the BioModStack shell origin/path.

4. Existing tests encode the old policy and must be migrated.
   - `platform/api/tests/test_biomodstack_services.py` expects container frontend URLs on `5173/bms/`.
   - `platform/api/tests/test_launch_biomodstack_ui.py` expects Electron/container env `BMS_FRONTEND_ORIGIN=http://127.0.0.1:5173`.
   - `platform/api/tests/test_core_runtime_scaffold.py` expects nginx listen `127.0.0.1:5173` and CORS only around `5173`.
   - `platform/desktop-electron/tests/windowUrl.test.ts` expects container context `http://127.0.0.1:5173/bms/`.
   - `platform/desktop-electron/tests/serviceControl.test.ts` has fixture URLs on `5173/bms/`.
   - `platform/frontend/tests/routerBasePath.test.ts` only covers root dev and `/bms/` path shape on `5173`; add a separate stable-port case.

5. APK already has a good pinned-channel foundation, but it needs release-discipline hardening.
   - External project path: `/home/dalab/Desktop/BioModStack Cordova Android Project`.
   - Runtime configs: `cordova.runtime.phone.json`, `cordova.runtime.emulator.json`, `cordova.runtime.json`.
   - Build/publish scripts already support `prepare:www:*`, `publish:ui-update:*`, wrapper tests, and APK verification.
   - The loader supports bundled/downloaded boot, shell API compatibility checks, invalid-state clearing, and bundled fallback.
   - The native plugin sanitizes relative file paths before writing/serving active bundles.
   - Remaining release hardening: make channel fields explicit in runtime configs and verify downloaded OTA bytes before install, not just after publication.

---

## Non-goals for this cutover

- Do not replace BioModStack's API/runtime control plane.
- Do not make Electron directly own backend process lifetime beyond the existing service-control layer.
- Do not rewrite React routing wholesale.
- Do not convert APK to automatic/live-dev updates.
- Do not introduce placeholders, demo routes, fake assets, or compatibility stubs.
- Do not collapse dev and production onto one service or one port.

---

## Worktree and rollout safety

The repo is currently dirty on branch `test`, and this spec is an untracked plan file. Implementation must be targeted.

Before code changes:

```bash
git status --short
git diff -- biomodstack_services.py biomodstack_runtime_profile.py scripts/launch_biomodstack_ui.py scripts/run_biomodstack_frontend.sh scripts/run_biomodstack_core_runtime.sh compose.core-runtime.yml docker/web.Dockerfile docker/web/nginx.conf platform/frontend/vite.config.ts platform/desktop-electron/src/main.ts platform/desktop-electron/src/windowState.ts
```

Rules:

1. Do not stage unrelated assay, workflow, BioXP, or structure-viewer changes unless Christian explicitly asks.
2. If implementation spans multiple commits, split by concern:
   - port/runtime contract
   - Vite/browser dev behavior
   - Electron hardening
   - APK channel hardening
   - docs/smoke scripts
3. If a file already has unrelated edits, use focused patches and re-read the touched block after each patch.

---

## Phase 1: Port and runtime contract cutover

### Task 1: Add explicit dev-vs-stable frontend port helpers

**Objective:** Make shared service code describe dev and production frontend URLs without duplicating port constants or relying on one `FRONTEND_PORT`.

**Files:**
- Modify: `biomodstack_services.py`
- Test: `platform/api/tests/test_biomodstack_services.py`

**Implementation detail:**

Add default constants and env-coerced helpers. Keep `FRONTEND_PORT` only as a backwards-compatible alias for the dev port if needed by old tests/process detection.

```python
DEFAULT_DEV_FRONTEND_PORT = 5173
DEFAULT_WEB_HOST_PORT = 18080
FRONTEND_PORT = DEFAULT_DEV_FRONTEND_PORT


def _coerce_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ServiceManagerError(f"{name} must be an integer port, got {raw!r}") from exc


def dev_frontend_port() -> int:
    return _coerce_env_int("BMS_DEV_FRONTEND_PORT", DEFAULT_DEV_FRONTEND_PORT)


def stable_frontend_port() -> int:
    return _coerce_env_int("BMS_WEB_HOST_PORT", DEFAULT_WEB_HOST_PORT)


def runtime_frontend_port(runtime_mode: str | None = None) -> int:
    mode = resolve_runtime_mode(runtime_mode)
    return stable_frontend_port() if mode == CONTAINER_RUNTIME_MODE else dev_frontend_port()


def runtime_frontend_origin(runtime_mode: str | None = None) -> str:
    return f"http://127.0.0.1:{runtime_frontend_port(runtime_mode)}"
```

Update `runtime_frontend_url()` and `runtime_descriptor()` to pass `mode` into `runtime_frontend_origin(mode)`.

```python
def runtime_frontend_url(runtime_mode: str | None = None) -> str:
    mode = resolve_runtime_mode(runtime_mode)
    origin = runtime_frontend_origin(mode)
    basename = runtime_router_basename(mode)
    if basename == "/":
        return f"{origin}/"
    return f"{origin}{basename}"
```

**Tests to add/update:**

- `runtime_frontend_port("dev") == 5173`
- `runtime_frontend_port("container") == 18080`
- `runtime_frontend_url("dev") == "http://127.0.0.1:5173/"`
- `runtime_frontend_url("container") == "http://127.0.0.1:18080/bms/"`
- `BMS_DEV_FRONTEND_PORT` and `BMS_WEB_HOST_PORT` env overrides are honored independently.
- Invalid port env raises `ServiceManagerError` with the env var name.

**Acceptance criteria:**

- Dev and container descriptors report different frontend origins by default.
- Existing API URL remains `http://127.0.0.1:8000`.
- Container router basename remains `/bms/`; dev router basename remains `/`.

---

### Task 2: Make listener cleanup mode-aware

**Objective:** Avoid killing the wrong surface and avoid stale old nginx containers masking Vite.

**Files:**
- Modify: `biomodstack_services.py`
- Test: `platform/api/tests/test_biomodstack_services.py`

**Implementation detail:**

Current cleanup logic assumes the frontend listener is always `FRONTEND_PORT` (`5173`). After the cutover, dev frontend and stable web use different ports.

Add a mode-aware helper:

```python
def listener_ports_for_runtime(runtime_mode: str | None = None) -> tuple[tuple[str, int], ...]:
    mode = resolve_runtime_mode(runtime_mode)
    if mode == CONTAINER_RUNTIME_MODE:
        return (("api", API_PORT), ("frontend", stable_frontend_port()))
    return (("api", API_PORT), ("frontend", dev_frontend_port()))
```

Update `should_cleanup_legacy_listeners_before_start(...)`, `cleanup_legacy_listener(...)`, `start_all(...)`, and `restart_all(...)` so:

- dev mode checks/cleans `8000` and `5173`
- container mode checks/cleans `8000` and `18080`
- startup also detects an old BioModStack nginx listener on `5173/bms/` as a stale legacy condition and stops it before dev launch, but does not treat an active Vite process on `5173` as a production web listener

**Tests to add/update:**

- Starting dev waits for `http://127.0.0.1:5173/`.
- Starting container waits for `http://127.0.0.1:18080/bms/`.
- Container cleanup targets stable port, not dev port.
- Dev cleanup targets dev port and can clear stale old nginx from `5173` if it is not a Vite process.

**Acceptance criteria:**

- Starting container no longer reserves or requires `5173`.
- Starting dev fails loudly if `5173` is occupied by something that is not the intended Vite service.

---

### Task 3: Move install/runtime profile defaults to stable port 18080

**Objective:** Make profile generation and core runtime env agree with the new stable web port.

**Files:**
- Modify: `biomodstack_runtime_profile.py`
- Modify: `scripts/run_biomodstack_core_runtime.sh`
- Test: `platform/api/tests/test_install_profile.py`
- Test: `platform/api/tests/test_core_runtime_scaffold.py`

**Implementation detail:**

Change:

```python
DEFAULT_WEB_HOST_PORT = 18080
```

Expand default CORS origins to include both dev and stable origins:

```python
DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:18080",
    "http://localhost",
    "http://localhost:5173",
    "http://localhost:18080",
    "https://localhost",
    "https://localhost:5173",
    "https://127.0.0.1",
]
```

In `scripts/run_biomodstack_core_runtime.sh` change:

```bash
export BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-18080}"
```

**Tests to update:**

- `test_install_profile.py` expected env export defaults.
- `test_core_runtime_scaffold.py` expected compose CORS default and web healthcheck.

**Acceptance criteria:**

- Generated `core-runtime.env` exports `BMS_WEB_HOST_PORT=18080` by default.
- Existing explicit profile value, e.g. `web_host_port=19080`, is still respected.
- CORS includes dev `5173` and stable `18080` defaults.

---

### Task 4: Move nginx stable hosted web to a runtime-configured loopback port

**Objective:** Serve production `/bms/` from nginx on `127.0.0.1:18080` by default while preserving host-network container behavior.

**Files:**
- Replace/rename: `docker/web/nginx.conf` to `docker/web/templates/default.conf.template` or equivalent
- Modify: `docker/web.Dockerfile`
- Modify: `compose.core-runtime.yml`
- Test: `platform/api/tests/test_core_runtime_scaffold.py`

**Implementation detail:**

Use the official nginx image's template rendering instead of hardcoding a port. Prefer a template file copied into `/etc/nginx/templates/default.conf.template`:

```nginx
server {
    listen ${BMS_WEB_BIND_HOST}:${BMS_WEB_HOST_PORT};
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    absolute_redirect off;
    client_max_body_size 512m;

    location = / {
        return 302 /bms/;
    }

    location = /bms {
        return 302 /bms/;
    }

    location = /bms/index.html {
        add_header Cache-Control "no-store, must-revalidate" always;
        try_files /bms/index.html =404;
    }

    location /bms/assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    location /bms/ {
        try_files $uri $uri/ /bms/index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_request_buffering off;
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

In `docker/web.Dockerfile`:

```Dockerfile
FROM nginx:1.27-alpine
ENV BMS_WEB_BIND_HOST=127.0.0.1 \
    BMS_WEB_HOST_PORT=18080
COPY docker/web/templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/platform/frontend/dist /usr/share/nginx/html/bms
EXPOSE 18080
```

In `compose.core-runtime.yml` under `bms-web`:

```yaml
environment:
  BMS_WEB_BIND_HOST: ${BMS_WEB_BIND_HOST:-127.0.0.1}
  BMS_WEB_HOST_PORT: ${BMS_WEB_HOST_PORT:-18080}
healthcheck:
  test: ["CMD", "wget", "-qO-", "http://127.0.0.1:${BMS_WEB_HOST_PORT:-18080}/bms/"]
```

Do not add a `ports:` mapping while `network_mode: host` is in use.

**Acceptance criteria:**

- `docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml config` resolves bms-web `BMS_WEB_HOST_PORT` default to `18080`.
- `docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-web` succeeds.
- `docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml run --rm --no-deps bms-web nginx -t` succeeds after template rendering.
- With core runtime running, `curl http://127.0.0.1:18080/bms/` returns `200` or the expected HTML, and `curl http://127.0.0.1:5173/@vite/client` is reserved for Vite dev.

---

## Phase 2: Make browser web UI instant for feature work

### Task 5: Restore Vite HMR by default and enforce strict dev port ownership

**Objective:** Browser feature changes should reflect immediately in the web UI, and port collisions should fail loudly.

**Files:**
- Modify: `platform/frontend/vite.config.ts`
- Modify: `scripts/run_biomodstack_frontend.sh`
- Test: `platform/api/tests/test_core_runtime_scaffold.py` or a new source-level Vite config test if practical

**Implementation detail:**

In `scripts/run_biomodstack_frontend.sh`:

```bash
exec npm run dev -- --host 127.0.0.1 --port "${BMS_DEV_FRONTEND_PORT:-5173}"
```

In `platform/frontend/vite.config.ts`, add explicit env-driven dev server defaults:

```ts
const devPort = Number(process.env.BMS_DEV_FRONTEND_PORT || 5173)
const hmrDisabled = process.env.BMS_VITE_DISABLE_HMR === '1'
const hmrHost = process.env.BMS_VITE_HMR_HOST
const hmrProtocol = process.env.BMS_VITE_HMR_PROTOCOL as 'ws' | 'wss' | undefined
const hmrClientPort = process.env.BMS_VITE_HMR_CLIENT_PORT ? Number(process.env.BMS_VITE_HMR_CLIENT_PORT) : undefined
```

Then under `server`:

```ts
host: '127.0.0.1',
port: devPort,
strictPort: true,
hmr: hmrDisabled
  ? false
  : hmrHost
    ? { host: hmrHost, protocol: hmrProtocol, clientPort: hmrClientPort }
    : undefined,
allowedHosts: [/* preserve existing explicit remote dev host if needed */],
```

Keep the existing watcher ignores for `work`, `bms_results`, `models`, `apptainer`, and `binderscaffolds`.

**Acceptance criteria:**

- Local default does not set `hmr: false`.
- Dev server binds `127.0.0.1:5173` and fails instead of auto-incrementing when busy.
- Remote/proxy workflows can still opt out with `BMS_VITE_DISABLE_HMR=1` or configure HMR host/protocol explicitly.
- `curl -sS http://127.0.0.1:5173/@vite/client` returns Vite client JS while dev service is active.

---

### Task 6: Add a frontend runtime surface contract test

**Objective:** Encode the dev/stable/mobile path contract in the frontend test harness.

**Files:**
- Create: `platform/frontend/tests/runtimeSurfaceContract.test.ts`
- Modify if needed: `platform/frontend/tsconfig.tests.json`
- Verify existing helper: `platform/frontend/tests/routerBasePath.test.ts`

**Test coverage:**

- Dev basename resolves to `/`.
- Stable hosted basename resolves to `/bms/`.
- Cordova basename resolves to `/`.
- Joining dev browser URL keeps root routes on `5173`.
- Joining stable browser URL keeps `/bms/` routes on `18080`.
- API base stays same-origin `/api` unless Cordova runtime overrides it.

**Commands:**

```bash
cd platform/frontend
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/runtimeSurfaceContract.test.js
```

**Acceptance criteria:**

- The test fails if a future edit sends stable `/bms/` back to `5173` by default.
- The test fails if a future edit breaks root dev routing.

---

## Phase 3: Launch-surface behavior

### Task 7: Make browser launch default to dev and Electron launch default to production

**Objective:** User intent should be encoded by launch surface, not by one global runtime default.

**Files:**
- Modify: `scripts/launch_biomodstack_ui.py`
- Test: `platform/api/tests/test_launch_biomodstack_ui.py`
- Verify wrapper: `start_ui_electron.sh`

**Implementation detail:**

Add a helper:

```python
def default_runtime_for_surface(surface: str) -> str:
    if surface == BROWSER_LAUNCH_SURFACE:
        return DEV_RUNTIME_MODE
    if surface == ELECTRON_LAUNCH_SURFACE:
        return CONTAINER_RUNTIME_MODE
    return DEFAULT_RUNTIME_MODE
```

Import `DEV_RUNTIME_MODE`, `CONTAINER_RUNTIME_MODE`, and `DEFAULT_RUNTIME_MODE` from `biomodstack_services.py`.

In `launch_ui(...)`:

```python
effective_runtime_mode = runtime_mode or default_runtime_for_surface(chosen_surface)
start_all(runtime_mode=effective_runtime_mode)
descriptor = runtime_descriptor(runtime_mode=effective_runtime_mode)
```

Use `effective_runtime_mode` consistently for start, descriptor, browser open, and Electron env.

**Tests to add/update:**

- `--surface browser` with no runtime starts `dev` and opens `http://127.0.0.1:5173/`.
- `--surface electron` with no runtime starts `container` and passes `BMS_FRONTEND_ORIGIN=http://127.0.0.1:18080`, `BMS_ROUTER_BASENAME=/bms/`.
- `--surface none` with no runtime stays conservative (`container`) unless explicitly changed.
- Explicit override still works: `--surface electron --runtime dev` can be used for debugging and is tested as dev-only.
- Persisted default surface fallback still works when Electron is unavailable.

**Acceptance criteria:**

- `./start_ui_electron.sh` remains a thin wrapper around `scripts/launch_biomodstack_ui.py --surface electron`.
- Browser launch is the fast/dev/latest path.
- Electron launch is the stable/mature path.

---

### Task 8: Preserve service-manager semantics

**Objective:** Avoid surprising operator scripts while changing launch defaults.

**Files:**
- Verify: `start_ui.sh`
- Test: `platform/api/tests/test_start_ui_entrypoint.py`
- Verify: `scripts/manage_desktop_services.py`

**Implementation detail:**

`start_ui.sh` is a service manager wrapper, not the browser/Electron selector. It should keep forwarding explicit actions to `scripts/manage_desktop_services.py`.

Do not silently repurpose `start_ui.sh` into a browser-dev launcher.

**Acceptance criteria:**

- `./start_ui.sh start --runtime container` starts production core runtime.
- `./start_ui.sh start --runtime dev` starts dev runtime.
- `./start_ui_electron.sh` launches Electron stable by default when no runtime override is passed.

---

## Phase 4: Electron maturity gates

### Task 9: Update Electron context defaults to stable port

**Objective:** Stop regressions where Electron silently points back at Vite.

**Files:**
- Modify: `platform/desktop-electron/src/windowState.ts`
- Modify if duplicated: `platform/desktop-electron/src/preload.ts`
- Test: `platform/desktop-electron/tests/windowUrl.test.ts`
- Test: `platform/desktop-electron/tests/serviceControl.test.ts`

**Implementation detail:**

Set the fallback origin based on runtime mode:

```ts
function defaultFrontendOriginForRuntime(runtimeMode: ShellRuntimeMode): string {
  return runtimeMode === 'dev' ? 'http://127.0.0.1:5173' : 'http://127.0.0.1:18080'
}

export function resolveShellContext(options: Partial<ShellContext> = {}): ShellContext {
  const runtimeMode = options.runtimeMode ?? (process.env.BMS_RUNTIME_MODE === 'dev' ? 'dev' : 'container')
  const frontendOrigin = normalizeOrigin(
    options.frontendOrigin ?? process.env.BMS_FRONTEND_ORIGIN ?? defaultFrontendOriginForRuntime(runtimeMode),
  )
  // existing basename logic remains
}
```

If `preload.ts` has a duplicated resolver, either share the helper safely at build time or patch the duplicate. Remember sandboxed preload output must not contain local `require('./...')` dependencies unless bundled.

**Tests to update:**

- Dev context remains `http://127.0.0.1:5173/`.
- Container context becomes `http://127.0.0.1:18080/bms/`.
- Env override still wins for special debugging.
- Built preload output has no local `require("./...")` runtime dependency if a shared helper is introduced.

**Acceptance criteria:**

- `pnpm --dir platform/desktop-electron test` passes.
- Electron production defaults to stable port even if launched outside the Python launcher.

---

### Task 10: Add Electron IPC sender-origin validation

**Objective:** Prevent arbitrary navigated pages from using privileged service-control IPC.

**Files:**
- Modify: `platform/desktop-electron/src/main.ts`
- Prefer create: `platform/desktop-electron/src/originPolicy.ts`
- Test create/update: `platform/desktop-electron/tests/originPolicy.test.ts`

**Implementation detail:**

Extract pure helpers:

```ts
export function isAllowedShellUrl(candidateUrl: string, context: ShellContext): boolean {
  try {
    const candidate = new URL(candidateUrl)
    const allowed = new URL(context.windowUrl)
    if (candidate.origin !== allowed.origin) {
      return false
    }
    if (context.routerBasename === '/') {
      return true
    }
    const base = context.routerBasename.replace(/\/$/, '')
    return candidate.pathname === base || candidate.pathname.startsWith(`${base}/`)
  } catch {
    return false
  }
}

export function isAllowedExternalUrl(candidateUrl: string): boolean {
  try {
    const parsed = new URL(candidateUrl)
    return ['http:', 'https:', 'mailto:'].includes(parsed.protocol)
  } catch {
    return false
  }
}
```

Gate all exposed IPC handlers:

```ts
function requireAllowedSender(event: Electron.IpcMainInvokeEvent, context: ShellContext): void {
  const senderUrl = event.senderFrame?.url ?? ''
  if (!isAllowedShellUrl(senderUrl, context)) {
    throw new Error('Blocked BioModStack shell IPC from untrusted sender')
  }
}
```

Use the guard before `getStatus`, `startAll`, `stopAll`, `restartAll`, `restartApi`, `openInBrowser`, and zoom handlers. Mutating service-control calls should use the main-process `runtimeMode` by default; if renderer-requested runtime mode remains supported, validate it as a known `ShellRuntimeMode` and keep it behind the same origin gate.

**Tests to add:**

- allowed `/bms/` sender can invoke service status.
- allowed `/bms/results` sender can invoke service status.
- off-origin sender is rejected.
- same origin but wrong path is rejected for container mode.
- invalid/blank sender URL is rejected.

**Acceptance criteria:**

- A navigated external page cannot call privileged BioModStack IPC even if preload remains present.
- Error messages are generic and do not leak local paths/secrets.

---

### Task 11: Add Electron navigation and external-link allowlists

**Objective:** Make Electron behave like a BioModStack shell, not a general browser.

**Files:**
- Modify: `platform/desktop-electron/src/main.ts`
- Reuse: `platform/desktop-electron/src/originPolicy.ts`
- Test: Electron unit tests around policy helpers and source-level wiring if direct Electron events are hard to unit-test

**Implementation detail:**

Add main-frame navigation handling:

```ts
function attachNavigationPolicy(window: BrowserWindow, context: ShellContext): void {
  window.webContents.on('will-navigate', (event, url) => {
    if (isAllowedShellUrl(url, context)) {
      return
    }
    event.preventDefault()
    if (isAllowedExternalUrl(url)) {
      void shell.openExternal(url)
    }
  })

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedShellUrl(url, context)) {
      return { action: 'allow' }
    }
    if (isAllowedExternalUrl(url)) {
      void shell.openExternal(url)
    }
    return { action: 'deny' }
  })
}
```

Reject `file:`, `data:`, `javascript:`, custom protocols, and malformed URLs by default.

**Acceptance criteria:**

- Internal `/bms/` navigations work.
- External HTTP/HTTPS/mailto links open externally.
- Dangerous/custom protocols are denied.
- Electron preload remains useful only for approved BioModStack UI URLs.

---

## Phase 5: APK/mobile channel discipline

### Task 12: Make APK runtime channel fields explicit

**Objective:** Avoid phone/emulator/stable channel drift and make APK provenance clear.

**Files:**
- Modify: `/home/dalab/Desktop/BioModStack Cordova Android Project/cordova.runtime.phone.json`
- Modify: `/home/dalab/Desktop/BioModStack Cordova Android Project/cordova.runtime.emulator.json`
- Test: `/home/dalab/Desktop/BioModStack Cordova Android Project/tests/prepare-bms-assets.test.mjs`

**Implementation detail:**

Phone config should explicitly include:

```json
{
  "uiUpdateChannel": "phone",
  "uiUpdateManifestPath": "/api/mobile-ui/channels/phone/manifest",
  "shellApiVersion": 1,
  "bundledUiVersion": "<real-release-version>"
}
```

Emulator config should explicitly use emulator channel fields:

```json
{
  "uiUpdateChannel": "emulator",
  "uiUpdateManifestPath": "/api/mobile-ui/channels/emulator/manifest",
  "shellApiVersion": 1,
  "bundledUiVersion": "<real-release-version>"
}
```

Use a real release version string when building/publishing. Do not commit placeholder version text.

**Commands:**

```bash
cd "/home/dalab/Desktop/BioModStack Cordova Android Project"
npm run test:wrapper
npm run prepare:www:phone
node --check www/bms-runtime-config.js
node --check www/bms-cordova-preflight.js
node --check www/bms-cordova-shim.js
```

**Acceptance criteria:**

- Runtime configs do not rely on implicit channel defaults.
- Wrapper tests fail if phone/emulator channel fields drift.
- Generated `www/bms-runtime-config.js` contains the intended explicit channel/version values.

---

### Task 13: Verify APK OTA bundle integrity before install

**Objective:** Keep APK mature/stable even when mobile UI bundles are published faster than APKs.

**Files:**
- Modify: `/home/dalab/Desktop/BioModStack Cordova Android Project/scripts/publish-ui-update-bundle.mjs`
- Modify: `/home/dalab/Desktop/BioModStack Cordova Android Project/scripts/prepare-bms-assets.mjs`
- Modify if needed: `/home/dalab/Desktop/BioModStack Cordova Android Project/www/bms-cordova-update-loader.js`
- Modify if enforcing native-side verification: `/home/dalab/Desktop/BioModStack Cordova Android Project/local-plugins/cordova-plugin-bms-ui-bundle/src/android/BmsUiBundlePlugin.java`
- Test: Cordova wrapper tests

**Implementation options:**

Preferred path:

1. Download the published zip referenced by the manifest.
2. Compute SHA-256 in JS or native code.
3. Compare it to manifest `sha256` before extracting/installing.
4. Install from verified zip contents.
5. Retain bundled fallback and previous active state until the new bundle confirms readiness.

Acceptable interim path:

1. Add per-file SHA-256 hashes to the manifest/descriptor.
2. Verify every downloaded file before calling `installBundle`.
3. Reject the whole install if any file hash mismatches.

**Acceptance criteria:**

- Corrupted downloaded UI does not install.
- Shell API mismatch does not install.
- Missing required file does not install.
- Bundled UI fallback remains available.
- Failure clears only invalid downloaded state, not bundled assets.

---

## Workstation-owned update control direction

Defer this from the immediate cutover. When it is implemented, the update manager should live on the primary instrument stack installation rather than being centered inside the APK preflight screen. For Christian's current deployment, that primary instrument is the workstation. The main control panel/menu on that workstation should be the operator surface that builds, publishes, and pushes updates to managed clients.

The key architectural correction is ownership:

- The workstation stack is the update authority and operator control plane.
- Electron and APK are update targets/consumers, not the primary place where release decisions are authored.
- Device-side code still needs fail-closed validation and rollback, but only as a guardrail after the workstation has selected and published an update.
- Browser dev remains outside the update system entirely; it is Vite/live-edit only.

The update control plane should still be typed as a release/state machine, not as scattered manifest parsing inside UI scripts. This matters because stable web, Electron shell/app updates, APK installs, and APK UI-bundle updates have different safety rules.

### Recommended type boundaries

Define these concepts explicitly, even if the Cordova project keeps using `.mjs` plus runtime validators instead of TypeScript at first:

```ts
type UiSurface = 'web-dev' | 'stable-web' | 'electron' | 'apk-cordova' | 'workstation-control'
type UiUpdateChannel = 'dev' | 'stable' | 'desktop-stable' | 'phone' | 'emulator' | 'canary'
type UiUpdateTarget = 'stable-web-bundle' | 'electron-shell' | 'apk-shell' | 'apk-ui-bundle'
type UiBundleSource = 'vite-dev' | 'stable-web-build' | 'bundled' | 'downloaded' | 'workstation-published'
type UpdateInstallPhase =
  | 'idle'
  | 'building'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'verifying'
  | 'staging'
  | 'published'
  | 'pushed'
  | 'installed'
  | 'ready-confirmed'
  | 'rolled-back'
  | 'error'
```

Core payloads:

```ts
type UiBundleDescriptor = {
  schemaVersion: 1
  version: string
  shellApiVersion: number
  entryCss: string[]
  entryJs: string[]
  build?: {
    gitSha?: string
    builtAt?: string
    viteBase?: './' | '/bms/' | '/'
    frontendCheckout?: string
  }
}

type UiManifestFile = {
  path: string
  url: string
  sha256: string
  sizeBytes: number
  mimeType?: string
}

type MobileUiManifest = {
  schemaVersion: 1
  channel: UiUpdateChannel
  version: string
  downloadUrl: string
  sha256: string
  assetBaseUrl: string
  shellApiVersion: number
  publishedAt: string
  descriptor: UiBundleDescriptor
  files: UiManifestFile[]
}

type WorkstationUpdateJob = {
  schemaVersion: 1
  target: UiUpdateTarget
  channel: UiUpdateChannel
  version: string
  artifactPath?: string
  artifactSha256?: string
  manifestPath?: string
  requestedBy: 'workstation-control-panel'
  createdAt: string
  status: UpdateInstallPhase
  lastError?: string
}

type InstalledUiBundleState = {
  schemaVersion: 1
  source: 'downloaded'
  channel: UiUpdateChannel
  version: string
  basePath: '/__bms_ui__/active/'
  descriptor: UiBundleDescriptor
  manifestSha256?: string
  installedAt: string
  verifiedAt: string
}
```

Decision results should be discriminated unions, not booleans:

```ts
type WorkstationUpdateDecision =
  | { kind: 'ready-to-publish'; job: WorkstationUpdateJob }
  | { kind: 'missing-artifact'; target: UiUpdateTarget; message: string }
  | { kind: 'target-unreachable'; target: UiUpdateTarget; message: string }
  | { kind: 'channel-mismatch'; target: UiUpdateTarget; expectedChannel: string; actualChannel: string }
  | { kind: 'integrity-failed'; target: UiUpdateTarget; expected: string; actual: string }
  | { kind: 'blocked-by-running-session'; target: UiUpdateTarget; message: string }
```

Device-side APK bundle decisions should also be typed where OTA remains available:

```ts
type UpdateDecision =
  | { kind: 'up-to-date'; currentVersion: string }
  | { kind: 'update-available'; currentVersion: string; next: MobileUiManifest }
  | { kind: 'incompatible-shell'; manifestShellApiVersion: number; shellApiVersion: number }
  | { kind: 'channel-mismatch'; manifestChannel: string; expectedChannel: string }
  | { kind: 'integrity-failed'; path?: string; expected: string; actual: string }
  | { kind: 'network-error'; message: string }
  | { kind: 'schema-error'; message: string }
```

### Runtime validator rules

Fail closed on all of these:

- unknown `schemaVersion`
- manifest `channel` not equal to runtime `uiUpdateChannel`
- descriptor `shellApiVersion` not equal to APK `shellApiVersion`
- missing top-level zip `sha256`
- missing per-file `sha256` once per-file installs are supported
- path traversal, absolute paths, empty paths, duplicate paths, or files outside the active bundle root
- descriptor entry JS missing from `files`
- downloaded bundle does not call `__BMS_CORDOVA_CONFIRM_READY__` before timeout

### Manager ownership

Keep the authority on the workstation control panel, with device-side pieces acting as validators/installers:

1. Workstation update control panel
   - source: new main control-panel/menu surface in the BioModStack workstation install
   - owns operator choices, target selection, version/channel selection, artifact discovery/build, publish/push intent, status history, and rollback requests
   - targets include `electron-shell`, `apk-shell`, and `apk-ui-bundle`

2. Publish/build manager
   - source: existing `scripts/publish-ui-update-bundle.mjs` for APK UI bundles, plus future Electron/APK packaging scripts
   - owns descriptor creation, zip/APK/Electron artifact creation, file list, hashes, manifests, and build provenance

3. Target/device update guard
   - source: generated from `scripts/prepare-bms-assets.mjs` for APK UI bundles, plus future Electron shell installer/launcher checks
   - owns schema validation, compatibility decision, hash verification, readiness checks, and refusing unsafe installs
   - should not be the primary operator UI for deciding what gets pushed

4. Native/mobile bundle store
   - source: `local-plugins/cordova-plugin-bms-ui-bundle/src/android/BmsUiBundlePlugin.java`
   - owns staged writes, path safety, active-slot promotion, active bundle serving, and clear/rollback

5. Boot manager
   - source: `www/bms-cordova-update-loader.js`
   - owns bundled-vs-downloaded boot selection, ready timeout, fallback, and invalid-state clearing

### Surface-specific policy

- Browser dev has no update manager. It is the live Vite dev surface and should expose diagnostics only.
- Stable web has build provenance. Updating it is a workstation action: rebuild/redeploy the core runtime stable bundle.
- Electron should consume stable web by default, expose diagnostics, and later accept shell/app updates initiated from the workstation control panel. Do not let Electron silently self-select arbitrary frontend channels.
- APK should remain pinned/stable on device, with bundled fallback and validation/rollback. The workstation control panel decides what APK shell or APK UI-bundle update to publish/push; the device refuses unsafe or incompatible artifacts.

### Test expectations

Add tests in two lanes:

Workstation control-panel/update-authority tests:

- target selection distinguishes `electron-shell`, `apk-shell`, and `apk-ui-bundle`
- missing artifact blocks publish/push
- wrong channel blocks publish/push
- hash mismatch blocks publish/push
- unreachable target reports `target-unreachable`, not success
- running/active Electron or APK session can be blocked or require explicit operator acknowledgement

APK/device guard tests that feed malformed manifests into pure validators before touching the Cordova bridge:

- missing `schemaVersion` rejects
- channel mismatch rejects
- shell API mismatch rejects
- missing JS entry rejects
- duplicate/path-traversal file rejects
- hash mismatch rejects
- valid phone manifest produces `update-available`
- currently installed same version produces `up-to-date`

Acceptance: the workstation control panel should render typed `WorkstationUpdateDecision` messages for publish/push flows. APK preflight/device guard code should render typed `UpdateDecision` messages for local compatibility/install checks, not bespoke string branches spread across check/install paths.

---

## Phase 6: Docs and operator-facing truth

### Task 14: Update docs that still describe stable UI on 5173

**Objective:** Prevent future regressions from stale docs.

**Files:**
- Modify: `README.md`
- Modify: `docs/Desktop_Runtime_and_Shell_Architecture.md`
- Modify: `docs/Workstation Set Up and Install Guide.md`
- Modify if needed: `docs/Platform_Overview.md`
- Modify if needed: `platform/api/README.md`

**Observed stale references:**

- `README.md` currently lists UI as `http://127.0.0.1:5173/bms/`.
- `docs/Workstation Set Up and Install Guide.md` currently tells the user to open `http://127.0.0.1:5173/bms/` for the hosted web container.
- `docs/Desktop_Runtime_and_Shell_Architecture.md` describes browser/Electron around the same hosted `/bms/` UI and needs the new channel split language.

**Operator-facing wording:**

- Web Dev UI: fastest path for feature work. Runs Vite on `127.0.0.1:5173` with HMR.
- Stable Web UI: production bundle served from the core runtime at `/bms/`, default `127.0.0.1:18080/bms/`.
- Electron Shell: stable desktop shell that loads the stable web UI by default.
- Mobile/APK: stable shell with bundled fallback and explicit versioned update channel.

**Acceptance criteria:**

- No doc claims production stable `/bms/` defaults to `5173`.
- Docs still explain that explicit dev overrides are possible for debugging.
- Tailscale/reverse-proxy docs distinguish stable proxy target from dev proxy target.

---

## Five optional pre-release robustness/QOL improvements

These are optional and should not block the core cutover, but they would make the first release safer and easier to operate.

### Optional 1: Add a far-left Diagnostics/About button in the top bar

Add one persistent button at the far left of the top bar. Label can be `Info`, `About`, or `Diagnostics`, but its job is the same: open a drawer/modal with the environment details that matter for the current surface.

Do not scatter separate status badges, footer widgets, and hidden menus. This one entry point should answer, "what am I running, where did it come from, and is it healthy?"

Show current surface/channel:

- `DEV / Vite / 5173` for browser dev
- `STABLE / /bms/ / 18080` for stable hosted/Electron
- `MOBILE / phone / <version>` for Cordova

Include relevant environment details:

- runtime mode and surface: web dev, stable web, Electron, APK/Cordova
- frontend origin, router basename, and build hash/git SHA where available
- Vite/HMR status for browser dev
- API base URL and `/api/health` status
- stable web host port and `/bms/` status
- Electron shell version, frontend URL, zoom factor, GPU workaround state, and log paths
- APK bundled UI version, active downloaded UI version, channel, shell API version, and update/fallback state
- service status summary for API, workflow adapter, web, and local dev server when available

Implementation path:

- Add a runtime config endpoint or injected env payload that exposes `runtime_mode`, `router_basename`, `frontend_origin`, build timestamp, and frontend git SHA/hash.
- Add a top-bar-left diagnostics button in the main layout component, before product navigation.
- Render the details in a drawer/modal that is passive by default; later update controls can be added to the workstation control panel, not mixed into the basic environment readout.
- For Electron, include the same values from `window.biomodstack.getShellContext()`.

Value: prevents accidental editing/testing against the wrong surface and gives one obvious support/debug entry point.

### Optional 2: Add a one-command surface smoke checker

Create `scripts/smoke_ui_surfaces.py` that emits JSON and exits nonzero on drift.

Checks:

- `5173/@vite/client` is Vite when dev mode is expected.
- `18080/bms/` serves production HTML when stable mode is expected.
- `5173/bms/` is not nginx production by default.
- `/api/health` returns healthy JSON.
- Electron context defaults resolve to `18080/bms/`.

Value: gives a fast pre-release and post-restart sanity check.

### Optional 3: Add actionable port-collision diagnostics

When `start_all(...)` or Vite startup detects a busy port, print:

- port number
- expected owner for that surface
- detected PID/process/container where available
- safe next command, e.g. `./start_ui.sh stop --runtime container` or `systemctl --user stop biomodstack-frontend.service`

Value: avoids silent Vite auto-increment and avoids operators killing the wrong service.

### Optional 4: Add diagnostics copy/export inside the Diagnostics/About panel

Add a `Copy diagnostics` action inside the same far-left Diagnostics/About drawer/modal. In Electron this can also be mirrored as `Help -> Copy BioModStack diagnostics`, but the top-bar entry remains the primary visible UI.

Include:

- shell version
- runtime mode
- frontend origin
- router basename
- API health status
- service status summary
- current zoom factor
- GPU workaround state
- log file paths

Do not include secrets, tokens, raw env dumps, or private hostnames.

Value: makes debugging the mature desktop shell and browser/APK surfaces much faster without adding multiple competing diagnostics entry points.

### Optional 5: Add APK version/rollback visibility, with push controls deferred to workstation panel

Show APK update state in the Diagnostics/About panel and Cordova preflight UI, but keep authoritative update/push controls for a later workstation main control-panel/menu implementation.

Visible state:

- bundled UI version
- active downloaded UI version
- latest manifest version, if checked
- hash verification status
- fallback/rollback availability
- whether the device is pinned to the current version

Deferred workstation controls:

- publish/push APK UI bundle
- push rebuilt APK shell
- request `Revert to bundled UI`
- request `Pin current version`

Value: keeps mobile stable and debuggable now, while preserving Christian's desired architecture that update authority lives on the primary workstation stack.

---

## Validation commands for the cutover branch

Run from repo root unless a command says otherwise.

### Python/control-plane tests

```bash
uv run --directory platform/api python -m pytest \
  tests/test_biomodstack_services.py \
  tests/test_manage_desktop_services_cli.py \
  tests/test_launch_biomodstack_ui.py \
  tests/test_start_ui_entrypoint.py \
  tests/test_core_runtime_scaffold.py \
  tests/test_install_profile.py -q
```

### Frontend checks

```bash
cd platform/frontend
npx tsc -b --pretty false
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/*.test.js
```

Do not use full `npm run lint` as the first cutover gate until the existing lint debt has a new-code-only lane.

### Electron checks

```bash
cd platform/desktop-electron
pnpm test
```

### Core-runtime/nginx checks

```bash
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml config
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-web
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml run --rm --no-deps bms-web nginx -t
```

### Cordova wrapper checks

```bash
cd "/home/dalab/Desktop/BioModStack Cordova Android Project"
npm run test:wrapper
```

If generated assets change:

```bash
npm run prepare:www:phone
node --check www/bms-runtime-config.js
node --check www/bms-cordova-preflight.js
node --check www/bms-cordova-shim.js
```

### Live smoke checks after services are running

```bash
# Stable hosted production UI
curl -sS -o /tmp/bms_stable.html -w '%{http_code}\n' http://127.0.0.1:${BMS_WEB_HOST_PORT:-18080}/bms/

# Browser dev Vite client, while dev service is active
curl -sS -o /tmp/bms_vite_client.js -w '%{http_code}\n' http://127.0.0.1:${BMS_DEV_FRONTEND_PORT:-5173}/@vite/client

# API health
curl -sS http://127.0.0.1:8000/api/health
```

Expected:

- Stable UI returns `200` from `/bms/` on the stable port.
- Vite client returns `200` from `5173/@vite/client` while dev service is active.
- API returns healthy JSON.
- Electron opens stable UI, not Vite client.

---

## Definition of done

1. `5173` serves Vite dev with HMR/fast refresh for browser feature work.
2. Stable hosted `/bms/` defaults to `18080` or an explicitly configured non-`5173` `BMS_WEB_HOST_PORT`.
3. Browser launcher with no runtime override opens/starts dev frontend.
4. Electron launcher with no runtime override opens/starts production/container frontend.
5. Electron IPC sender origin is validated.
6. Electron main-frame navigation and external URLs are allowlisted.
7. Tests prove Electron cannot silently regress to the dev server by default.
8. Docs clearly state that browser web is latest/dev, while Electron/APK are stable consumers.
9. APK update channel remains pinned/versioned with bundled fallback; device-side OTA integrity verification is implemented or explicitly tracked as a release blocker, and future publish/push authority is reserved for the workstation control panel.
10. Live smoke checks prove dev and stable surfaces are physically separated by port.

---

## Rollback plan

If the cutover causes trouble:

1. Stop dev frontend service: `systemctl --user stop biomodstack-frontend.service`.
2. Keep core runtime on the configured production port, default `18080/bms/`.
3. Open stable web directly: `http://127.0.0.1:${BMS_WEB_HOST_PORT:-18080}/bms/`.
4. Launch browser explicitly against container if needed: `python3 scripts/launch_biomodstack_ui.py --surface browser --runtime container`.
5. Revert only launcher/port changes first; do not revert passing Electron IPC/navigation hardening unless it directly caused the incident.
6. If an old nginx/container still owns `5173`, stop the old core runtime and restart dev so Vite can reclaim `5173`.
