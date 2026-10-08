# Standalone BioModStack Cordova APK Plan

> For Hermes: this project is intentionally outside the BioModStack repo and may consume `/home/dalab/biomodstack/biomodstack` as a source checkout without modifying it.

Goal: build a standalone Apache Cordova Android project that bundles the current BioModStack frontend into `www/`, emits a debug APK, and can be re-built for either emulator or physical-phone use tomorrow.

Architecture:
- Keep the Android wrapper in its own project root: `/home/dalab/Desktop/BioModStack Cordova Android Project`
- Build the existing BioModStack Vite frontend from the external checkout, overriding Vite base to `./` so assets work from Cordova `www/`
- Inject runtime config and a shim before the app bundle so relative `/api...` requests are rewritten to a configured remote API base URL without editing the upstream BioModStack repo
- Package the resulting web bundle through Cordova Android into a debug APK

Tech stack:
- Apache Cordova 13
- cordova-android 13
- Android SDK platform 34/35 + build-tools 34/35
- Node 20 + pnpm + Java 17

---

## Deliverables in this separate project

- `package.json` — wrapper scripts for prepare/build/verify/install-sdk
- `config.xml` — Cordova app configuration
- `cordova.runtime.json` — active runtime config
- `cordova.runtime.phone.json` — physical phone / Tailscale target
- `cordova.runtime.emulator.json` — Android emulator target
- `scripts/install-android-sdk.sh` — local SDK bootstrap
- `scripts/prepare-bms-assets.mjs` — external frontend build + runtime injection pipeline
- `scripts/build-apk.sh` — debug APK build flow
- `scripts/verify-apk.sh` — APK verification flow
- `www/` — generated bundled app assets
- `platforms/android/app/build/outputs/apk/debug/app-debug.apk` — debug APK output

## Active implementation status

Completed:
1. Standalone Cordova project created outside BMS
2. External frontend build pipeline wired to `/home/dalab/biomodstack/biomodstack/platform/frontend`
3. Runtime API/base-path injection implemented
4. Android SDK bootstrap script added
5. Emulator + phone runtime config files added
6. Cordova Android platform initialized
7. Local Android SDK installed under `$HOME/Android/Sdk`

Verification targets:
1. `npm run prepare:www` succeeds
2. `npm run build:debug` succeeds with local SDK + Java 17
3. `npm run verify:apk` confirms expected APK contents
4. Optional: install on attached device with `adb install -r`

## Tomorrow phone test procedure

1. On host machine:
   - `cd /home/dalab/Desktop/BioModStack Cordova Android Project`
   - ensure `cordova.runtime.phone.json` points at the phone-reachable BioModStack API host
   - `export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`
   - `export ANDROID_SDK_ROOT=$HOME/Android/Sdk`
   - `npm run build:debug:phone`
   - `npm run verify:apk`

2. APK path:
   - `/home/dalab/Desktop/BioModStack Cordova Android Project/platforms/android/app/build/outputs/apk/debug/app-debug.apk`

3. Delivery to phone:
   - either `adb install -r <apk>` with USB debugging enabled
   - or copy the APK to the phone and sideload it manually

## Known runtime caveat

Because the app is bundled locally, upstream relative `/api...` requests must be rewritten to an absolute backend origin. This wrapper now does that. If the target backend enforces strict browser CORS, the backend may still need to allow the Cordova app origin (`https://localhost`) or the wrapper may need a native-HTTP fallback in a later slice.
