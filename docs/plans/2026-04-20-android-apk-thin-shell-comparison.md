# BioModStack Android APK Thin-Shell Comparison and Implementation Plan

> **For Hermes:** Treat this as a launch-surface roadmap first, not immediate code churn. Preserve the existing browser and Electron paths, and keep the Android APK as an additive shell around the existing BioModStack stack.

**Goal:** Create a stable Android APK for BioModStack that gives Christian dedicated app-like access to the existing hosted stack without moving the runtime onto the phone or destabilizing the current browser/Electron launch surfaces.

**Architecture:** Keep the BioModStack backend and hosted web UI as the source of truth. The Android app is a thin shell around that existing stack: it opens the hosted `/bms/` UI in a persistent Android WebView, preserves app-local session state, and offers a more appliance-like operator experience than a normal mobile browser tab. Browser access remains supported permanently.

**Tech Stack:** Existing BioModStack React 19 + Vite 6 frontend in `platform/frontend`, existing `/bms/` production base path, existing runtime/control-plane scripts, and either Apache Cordova or Capacitor as the Android shell framework.

---

## Current repo facts that matter

These findings come from the current repo and constrain the APK design:

1. `platform/frontend/package.json`
   - The UI is already a modern Vite/React app.
   - This strongly favors a thin web-wrapper strategy over a fully native Android rewrite.

2. `platform/frontend/vite.config.ts`
   - Production base path is `/bms/`.
   - Dev base path is `/`.
   - The mobile shell should target the hosted production path, not the dev server path.

3. `platform/frontend/src/main.tsx`
   - The app already uses `BrowserRouter basename={...}`.
   - This means the existing web UI already has the beginnings of shell-aware routing behavior.

4. `platform/frontend/src/runtime/navigation.ts`
   - Base-path handling is centralized.
   - This reduces route fragility for additional shells.

5. No service-worker/PWA registration was found in `platform/frontend`.
   - There is no current PWA install/update/offline layer to piggyback on.
   - A dedicated APK wrapper is the shortest path to a stable Android operator surface.

6. Existing launch-surface direction in this repo already prefers additive shells.
   - Electron is being added without replacing the browser path.
   - Android should follow the same principle.

---

## Non-negotiables

1. The phone does not become the runtime host.
   - BioModStack still runs on the workstation/server.
   - The APK is only a client shell.

2. Browser access remains first-class.
   - The Android APK must not replace the normal hosted web UI.
   - It is an additional operator surface.

3. Existing operator entrypoints stay stable.
   - Do not repurpose `start_ui.sh` or existing service wrappers just to serve Android.
   - Android-specific launch behavior must be opt-in.

4. The APK must point at a stable network address.
   - This can be Tailscale HTTPS, a reverse proxy, or another stable private URL.
   - Without stable reachability, neither Cordova nor Capacitor solves the real problem.

5. Session persistence matters more than fancy native features in v1.
   - The main problem to solve is “dedicated stable app shell,” not “deep Android integration.”

---

## The actual problem being solved

Christian’s Android problem has three layers:

1. **Reachability** — the phone must reliably reach the stack at a stable URL.
2. **Shell persistence** — app session/state should live in a dedicated Android app container, not a disposable browser tab.
3. **Mobile usability** — the current web UI must be usable on a phone-sized touchscreen.

Cordova and Capacitor mainly solve layer 2.
They only partially solve layer 3, and they do not solve layer 1 by themselves.

---

## Option A — Apache Cordova thin wrapper

### What it is
A classic hybrid-app shell that wraps the existing hosted BioModStack UI in an Android WebView. Cordova configuration is centered around `config.xml`, an allowlist model, and a platform/plugin pipeline.

### Why it fits BioModStack well right now
Cordova is a good fit when the goal is:
- get a dedicated Android app quickly
- keep the hosted BioModStack web UI as the product truth
- allow top-level navigation to a known remote `/bms/` URL
- avoid a larger frontend refactor in phase 1

### Practical advantages
- Very direct mental model for a remote hosted-web shell.
- `config.xml` explicitly supports WebView navigation allowlists.
- Easy to keep the app extremely thin.
- Fine choice for a lab/internal/sideloaded operator app.
- Lower first-wave refactor pressure on the existing frontend.

### Practical disadvantages
- Older ecosystem and older developer ergonomics.
- Weaker long-term story if the Android shell later needs richer native behavior.
- More “legacy hybrid app” feel than Capacitor.
- Less attractive default if this later grows into a more serious mobile product.

### Best-use case
Choose Cordova when the priority is:
- fastest route to a stable APK around the existing hosted stack
- minimal repo churn
- little or no first-wave native feature work

---

## Option B — Capacitor thin shell

### What it is
A newer hybrid shell model with a more modern native-project workflow and a cleaner plugin story. Capacitor is generally the current default successor to Cordova-style projects.

### Why it is attractive
Capacitor is more attractive when the goal is:
- long-term maintainability
- first-class Android project ownership
- better future expansion into native plugins or app-specific native behavior
- a less legacy-feeling mobile shell stack

### Practical advantages
- More modern tooling and ecosystem.
- Cleaner native Android project workflow.
- Better long-term maintainability if Android becomes an important surface.
- Easier to evolve into “thin shell now, richer native affordances later.”

### Practical disadvantages for this exact BioModStack situation
- Capacitor’s `server.url` / `allowNavigation` model is documented for live-reload style use and is explicitly marked in the docs as “not intended for production.”
- If we want to stay within Capacitor’s happiest path, we should bundle local web assets into the app and call the remote BioModStack API over HTTPS.
- That would require extra frontend work to make API origin/configuration more explicit outside the current same-origin hosted-web assumptions.

### Best-use case
Choose Capacitor when the priority is:
- cleaner long-term mobile architecture
- better Android-native extensibility
- willingness to do more deliberate frontend/runtime refactoring up front

---

## Direct compare-and-contrast

| Dimension | Cordova | Capacitor |
|---|---|---|
| Best first-wave fit for a remote hosted BioModStack shell | Strong | Medium |
| Best long-term modern default | Medium | Strong |
| Amount of upfront repo refactor needed | Lower | Higher |
| Comfort with “open a stable hosted `/bms/` URL in-app” | Strong | Medium |
| Native-project ergonomics | Medium | Strong |
| Future plugin/native extensibility | Medium | Strong |
| Legacy feel | Higher | Lower |
| Fastest route to an internal APK | Strong | Medium |
| Best if Android later becomes a strategic product surface | Medium | Strong |

---

## Recommendation

### Recommendation for Christian’s stated goal
For the specific goal:

> “I want to access my stack stably off of my Android device.”

recommend **Cordova first**.

Why:
- The current BioModStack frontend is already a hosted web product.
- The current problem sounds more like “stable dedicated shell” than “deep native app.”
- Cordova is the simpler first-wave way to wrap the existing hosted `/bms/` UI in a dedicated Android container.
- Capacitor becomes more compelling if we later decide the app should bundle local assets, support richer native behaviors, or become a more strategic mobile product.

### Recommendation in one sentence
- **Cordova** if the goal is the fastest stable Android wrapper around the current hosted stack.
- **Capacitor** if the goal is the best long-term mobile shell architecture and we are willing to pay more refactor cost now.

---

## Recommended first-wave product shape

### Phase 1 product definition
Create a **thin Android shell** that:
- opens the stable hosted BioModStack URL
- keeps app-local web session state persistent across app restarts
- exposes a clean icon/splash/theme
- optionally opens certain external links in the system browser
- does not duplicate frontend business logic
- does not host BioModStack services locally on Android

### Explicit non-goals for phase 1
- No Android-native rewrite of BioModStack
- No local pipeline execution on phone
- No attempt to bundle the entire stack into the APK
- No replacement of browser or Electron access
- No new control-plane semantics required just for Android

---

## Stable networking prerequisite

Before either Cordova or Capacitor, define one stable Android target URL.

Recommended shape:
- `https://<stable-private-host>/bms/`

Examples of acceptable approaches:
- Tailscale HTTPS on a stable machine name
- reverse proxy with HTTPS and auth in front of BioModStack
- another private VPN/reverse-proxied URL with valid TLS

Definition of done for reachability:
- the URL loads from Android Chrome over the intended network path
- login/auth/session behaves correctly there
- route refreshes within `/bms/...` work correctly

If this is not already stable, fix it first.

---

## Cordova-first implementation plan

### Proposed repo layout

Create a separate Android shell package instead of polluting `platform/frontend`:

- Create: `platform/mobile-cordova/package.json`
- Create: `platform/mobile-cordova/config.xml`
- Create: `platform/mobile-cordova/www/index.html`
- Create: `platform/mobile-cordova/www/mobile-config.json`
- Create: `platform/mobile-cordova/www/boot.js`
- Create: `platform/mobile-cordova/resources/`
- Create: `platform/mobile-cordova/scripts/build-debug-apk.sh`
- Create: `platform/mobile-cordova/scripts/build-release-aab.sh`
- Create: `platform/mobile-cordova/README.md`
- Modify: `pnpm-workspace.yaml`
- Modify: `docs/Desktop_Runtime_and_Shell_Architecture.md` or a mobile-shell architecture doc if that lands separately

### Why this layout
- keeps Android shell concerns isolated
- preserves the current frontend package as the main web product
- allows the Android shell to be deleted, rebuilt, or replaced later without touching core UI files unnecessarily

### Phase 1 shell behavior

1. Boot local `www/index.html`.
2. Read `www/mobile-config.json` for the hosted BioModStack base URL.
3. Redirect the WebView to the stable hosted `/bms/` URL.
4. Allow in-app navigation only to the approved BioModStack origin.
5. Send other URLs to the system browser.
6. Preserve WebView storage and do not actively clear it on app close/update.

### Why use a local boot page instead of hardcoding only a remote `<content src="...">`
- gives a place for a branded loading screen
- allows future “server unreachable / retry” handling
- keeps app configuration explicit in repo files
- reduces the amount of Cordova behavior hidden in XML alone

### Required Cordova configuration principles

In `config.xml`:
- keep the app ID/package in reverse-DNS format
- allow navigation only to the exact trusted BioModStack host pattern
- allow external intents for normal browser/http(s) escape hatches
- default to secure HTTPS target URLs only
- avoid wildcarding the navigation policy more than necessary

### Build outputs
- debug APK for sideload/testing
- signed release AAB for Play-distribution if needed later

---

## Capacitor-first implementation plan

If a more modern path is preferred instead, use Capacitor in a separate package:

### Proposed repo layout
- Create: `platform/mobile-capacitor/package.json`
- Create: `platform/mobile-capacitor/capacitor.config.ts`
- Create: `platform/mobile-capacitor/android/` (generated)
- Create: `platform/mobile-capacitor/www/`
- Create: `platform/mobile-capacitor/scripts/build-debug-apk.sh`
- Create: `platform/mobile-capacitor/scripts/build-release-aab.sh`
- Create: `platform/mobile-capacitor/README.md`
- Modify: `pnpm-workspace.yaml`

### Important caveat
Capacitor is best when the mobile app bundles local web assets and talks to remote APIs deliberately.

That means the better Capacitor architecture is:
- bundle a mobile-targeted BioModStack web build into the app
- configure API base URL explicitly
- call the remote BioModStack backend over HTTPS

### Extra work this implies
To do Capacitor cleanly, expect to add:
- explicit runtime API-origin configuration in the frontend
- mobile-safe auth/session behavior independent from same-origin browser assumptions
- mobile-specific deep-link and refresh behavior tests
- likely a small mobile-shell runtime config layer inside `platform/frontend`

---

## Decision rule

Use this decision rule and do not overthink it:

### Pick Cordova if all of these are true
- the main goal is stable dedicated Android access soon
- the hosted web UI should remain the truth surface
- native-device integration is not the priority yet
- we want the least frontend refactor in phase 1

### Pick Capacitor if any of these become true
- Android is becoming a strategic long-term surface
- the app needs more native integration soon
- we are willing to make API origin/runtime configuration more explicit now
- we want the stronger long-term Android shell base immediately

---

## Verification checklist for either path

### Browser/runtime non-regression
- Browser access to `/bms/` still works normally.
- Existing desktop/Electron launch surfaces still work.
- No existing launcher/service scripts changed behavior unexpectedly.

### Android shell validation
- App launches from a home-screen icon.
- App reaches the hosted stack at the expected stable URL.
- Session persists across app close/reopen.
- Internal BioModStack navigation stays in-app.
- External links open safely outside the shell when intended.
- Refreshing on a deep path within `/bms/...` still works.
- Network loss/recovery is understandable to the operator.

### Security validation
- Only the approved BioModStack origin is navigable inside the WebView.
- No extra wildcard navigation policies are left enabled accidentally.
- Release builds use HTTPS target URLs only.
- Signing keys stay out of git.

---

## Suggested build commands for the eventual implementation

### Cordova
```bash
cd platform/mobile-cordova
pnpm dlx cordova platform add android
pnpm dlx cordova build android --debug
```

Typical debug artifact:
- `platform/mobile-cordova/platforms/android/app/build/outputs/apk/debug/app-debug.apk`

### Capacitor
```bash
cd platform/mobile-capacitor
pnpm install
npx cap add android
npx cap sync android
cd android
./gradlew assembleDebug
```

Typical debug artifact:
- `platform/mobile-capacitor/android/app/build/outputs/apk/debug/app-debug.apk`

---

## Final recommendation

For BioModStack as it exists today, the cleanest first wave is:

1. stabilize the phone-facing HTTPS URL to the hosted `/bms/` stack
2. build a thin **Cordova** Android wrapper around that URL
3. keep browser and Electron as supported parallel launch surfaces
4. revisit **Capacitor** only if the Android shell needs to grow beyond “stable dedicated wrapper”

That matches the current repo shape, the current user need, and the existing additive-shell philosophy.
