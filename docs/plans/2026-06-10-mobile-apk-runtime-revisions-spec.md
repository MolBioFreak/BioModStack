# BioModStack mobile APK runtime revisions spec

Date: 2026-06-10
Scope: code-level review of BioModStack Android/Cordova phone shell runtime targeting, Tailscale/DNS override UX, safe-area clipping, and APK/mobile UI bundle publication flow.

## Current facts from source review

### Data-path fix already landed locally

Commit: `25ba81c fix(mobile): lighten jobs list data path`

The source now supports `GET /api/jobs?summary=true` and the main mobile/list consumers use bounded lightweight summaries plus detail hydration. This addresses the heavy recent-jobs spinner class where list panes parsed full job objects with large params/provenance/stage/review payloads.

Verified before commit:

- `git diff --check`
- `uv run --directory platform/api python -m pytest tests/test_jobs_list_summary.py -q`
- frontend test TS compile
- `node --test --test-concurrency=1 .../mobileJobsDataPathContract.test.js`
- `platform/frontend` production `tsc -b`

### Cordova wrapper is a separate non-git artifact surface

Reviewed path:

- `/home/dalab/Desktop/BioModStack Android APK/BioModStack Cordova Android Project`

This directory is not a git repo. It currently contains the shell generator, generated `www/` assets, local UI bundle plugin source, and built APK output. Any code revisions there either need to be migrated into the main repo, snapshotted as artifacts, or separately archived; they are not protected by the BioModStack git commit unless copied into repo-managed paths.

### Baked phone target

Reviewed files:

- `cordova.runtime.json`
- `cordova.runtime.phone.json`
- generated `www/bms-runtime-config.js`

Both default and phone runtime config point to:

- `https://compute-node.taileb3a90.ts.net`

The generated runtime exposes:

- `window.__BMS_CORDOVA_DEFAULT_RUNTIME__`
- `window.__BMS_CORDOVA_RUNTIME__`
- `window.__BMS_API_BASE_URL__`
- local override key: `bms.cordova.runtimeOverrides`

Implication: APK production/off-production behavior is determined by the baked default plus any saved WebView localStorage override. A good localhost workstation API check does not prove the phone APK can reach this target.

### Runtime override UX exists but is not idiot-proof enough

Reviewed file:

- `scripts/prepare-bms-assets.mjs`

The preflight surface already includes:

- API base URL input
- UI update channel input
- scale slider
- compact mode toggle
- Test connection
- Check UI update
- Update UI
- Save + reload
- Revert to bundled UI
- visible build default and active UI labels

Gaps:

- API URL normalization is only trim + trailing-slash strip. It does not sanitize or reject userinfo, path/query/hash, non-HTTPS production URLs, malformed hosts, or accidental dev/local targets.
- There is no one-tap “Reset endpoint/channel/scale to build defaults and reload” that clears all runtime overrides.
- There is no “copy diagnostics” packet showing resolved API base, default API base, override keys, UI channel, manifest URL, active bundle, shell API version, and last probe error.
- There is no automatic “target looks unreachable” first-run/offline screen beyond the manual preflight probe. If the app boots straight to the bundled UI and requests fail, the operator can still see a generic disconnected/spinner state instead of the exact endpoint failure.
- Override persistence can preserve a stale bad target after reinstall if app data remains.

### Safe-area clipping is real in shell CSS

Reviewed files:

- generated `www/bms-cordova-mobile-shell.css`
- source generator `scripts/prepare-bms-assets.mjs` `buildMobileShellCss()`
- generated `www/index.html` via injected viewport behavior

The shell uses `viewport-fit=cover`, but the generated mobile shell CSS only sets `min-height`, background, compact nav width/padding tweaks, and some responsive width overrides. It does not reserve top/bottom safe-area insets on root/body/app/nav/main surfaces.

The preflight floating button uses `env(safe-area-inset-right/bottom)`, but the main app chrome does not.

Implication: the WebView is allowed to draw under Android system bars. This explains top nav/bottom action clipping independently of backend/API health.

### APK build/publish flow exists but needs source-of-truth tightening

Reviewed files:

- `package.json`
- `scripts/build-apk.sh`
- `scripts/verify-apk.sh`
- `scripts/publish-ui-update-bundle.mjs`
- `artifacts/android/README.md`

Available commands:

- `npm run test:wrapper`
- `npm run publish:ui-update:phone`
- `npm run build:debug:phone`
- `npm run verify:apk`

The build script correctly handles the known Java/Gradle resource constraints and can use Temurin 17. Verification checks required APK zip entries and can install/launch if adb is connected.

Gaps:

- The Cordova source project is not in git, so rebuild logic and wrapper changes are not naturally reviewed with the main repo.
- Existing installed APKs receive UI changes only if the phone update channel is published and the shell installs it. A source commit alone does not update the APK.
- Fresh APK artifacts need deterministic copy into `artifacts/android/`, `.sha256`, README metadata, and explicit note whether adb install/launch was performed.

## Required revisions

### 1. APK target / production URL handling

Implement in wrapper source generator/runtime config:

1. Add explicit runtime profiles/configs:
   - `phone-prod`: default phone tailnet/prod target.
   - `phone-local-tailscale`: optional lab/workstation override target if needed.
   - `emulator`: keep `http://10.0.2.2:8000` as emulator-only.
2. Add strict URL normalization helper shared by build-time config and preflight runtime:
   - parse with `new URL()`;
   - require `https:` for phone/prod profiles;
   - allow `http://10.0.2.2:*` only for emulator profile;
   - strip userinfo, path, query, and hash;
   - normalize to `scheme://host[:port]` with no trailing slash;
   - reject empty host and malformed URL with a visible error.
3. Generate and show a resolved runtime truth panel:
   - default API base;
   - effective API base;
   - whether effective value came from default or local override;
   - health URL;
   - manifest URL;
   - package/channel/shell API version.
4. Add regression tests in `tests/prepare-bms-assets.test.mjs` for URL normalization and rejection cases.

Acceptance:

- `www/bms-runtime-config.js` contains the intended profile target.
- A stale override with `http://localhost`, userinfo, path/query, or malformed host cannot silently become the active phone target.
- Phone/prod builds cannot accidentally target Vite/dev localhost.

### 2. Tailscale/DNS/runtime override UX

Implement in preflight script/CSS:

1. Add buttons:
   - “Reset runtime defaults” clears `bms.cordova.runtimeOverrides` and reloads.
   - “Copy diagnostics” copies or displays a diagnostics JSON block.
   - Optional “Open preflight on startup when API probe fails”.
2. Make connection testing explicit:
   - show exact URL tested: `<apiBaseUrl>/api/health`;
   - distinguish DNS/network/TLS/HTTP/JSON failure in copy;
   - also test `<apiBaseUrl>/api/mobile-ui/channels/<channel>/manifest` when checking UI update.
3. Persist last successful API base/channel and last failure timestamp/error for operator truth.
4. Consider first-run/open-on-error behavior:
   - if no successful health probe exists for current effective API base, open the preflight panel or show a compact offline banner with “Open preflight”.

Acceptance:

- A phone operator can identify stale override vs baked target vs tailnet DNS/API outage without adb/logcat.
- Resetting app runtime state does not require Android app-data clearing.
- Failure copy includes exact attempted endpoint and does not leak secrets.

### 3. Safe-area top/bottom UI overlap

Implement in `buildMobileShellCss()` and regenerated `www/bms-cordova-mobile-shell.css`:

1. Define safe-area variables on `html.bms-cordova-shell`:
   - `--bms-safe-top: max(env(safe-area-inset-top), 0px)`;
   - `--bms-safe-bottom: max(env(safe-area-inset-bottom), 0px)`;
   - `--bms-safe-left/right` similarly;
   - fallback chrome padding variables for Android devices where env vars report zero.
2. Reserve top space on app chrome:
   - nav/header top padding should include `var(--bms-safe-top)`.
   - root/body should use `min-height: 100dvh` where supported.
3. Reserve bottom space on scroll containers/action regions:
   - main content bottom padding should include `var(--bms-safe-bottom)` plus normal app spacing.
   - fixed/floating controls should use bottom/right safe variables.
4. Keep the patch broad enough for current Tailwind class-based app but not overfit to one screen. Prefer root/nav/main selectors over only component-specific selectors.
5. Add wrapper tests asserting generated CSS contains safe-area variables and applies them to root/nav/main/bottom surfaces.

Acceptance:

- Top nav/header is not under the status/notch area.
- Bottom buttons/floating controls are not under gesture/navigation bars.
- Existing compact layout width fixes remain intact.

### 4. APK rebuild / mobile bundle publish

After source fixes:

1. Run wrapper tests:
   - `npm run test:wrapper`
2. Publish phone mobile UI channel:
   - `npm run publish:ui-update:phone`
3. Build fresh debug phone APK:
   - `npm run build:debug:phone`
4. Verify APK:
   - `npm run verify:apk`
   - record whether adb install/launch was actually performed.
5. Copy artifact into main repo:
   - `artifacts/android/BioModStack-debug-phone-YYYY.MM.DD-mobile-runtime-beta-NNN.apk`
   - write `.sha256`
   - update `artifacts/android/README.md` with APK path, SHA256, size, package/version/versionCode, min/target SDK, signing type, UI channel version, bundle SHA, and whether handset smoke was skipped/performed.
6. Verify local API manifest if server is running:
   - `curl http://127.0.0.1:8000/api/mobile-ui/channels/phone/manifest`

Acceptance:

- Existing installed APKs can pull the phone-channel UI update.
- New APK artifact is reproducible and traceable by hash.
- Report distinguishes source/build proof from actual phone-on-Tailscale smoke proof.

## Recommended commit sequence

1. Already done: `fix(mobile): lighten jobs list data path`.
2. `docs(mobile): specify APK runtime hardening revisions` for this spec.
3. `fix(mobile): harden APK runtime target overrides`.
4. `fix(mobile): add safe-area shell padding`.
5. `chore(mobile): publish phone UI bundle and refresh debug APK artifact`.

Keep these separate. The Cordova wrapper is currently outside git, so before code commits for steps 3-5, either migrate the wrapper source into the repo or create a repo-tracked source snapshot; otherwise only generated artifacts will be protected by BioModStack git.

## Out of scope for this spec

- Replacing Tailscale/MagicDNS or reverse-proxy infrastructure.
- Release signing / Play Store distribution.
- Redesigning the mobile UI beyond shell-safe chrome and data-path loading.
- Claiming handset proof without adb/logcat or a real phone smoke test.
