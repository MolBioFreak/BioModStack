BioModStack Cordova Android wrapper

Project root:
  /home/dalab/Desktop/BioModStack Cordova Android Project

Purpose:
  Build the existing BioModStack Vite frontend from an external checkout, copy the production web bundle into Cordova www/, and emit a debug Android APK.

Expected source checkout:
  /home/dalab/biomodstack/biomodstack

Important constraint:
  This wrapper does not edit files inside the BioModStack repo. It runs the frontend Vite build from /home/dalab/biomodstack/biomodstack/platform/frontend and sends the build output to this wrapper's .cache/ and www/ directories.

Files in this wrapper:
  package.json
  config.xml
  cordova.runtime.json
  cordova.runtime.phone.json
  cordova.runtime.emulator.json
  cordova.runtime.example.json
  docs/2026-04-20-standalone-cordova-bundled-apk-plan.md
  resources/android/xml/network_security_config.xml
  scripts/install-android-sdk.sh
  scripts/prepare-bms-assets.mjs
  scripts/build-apk.sh
  scripts/verify-apk.sh

Runtime configs:
  cordova.runtime.json
    default active config for phone-targeted builds
    frontendCheckout: /home/dalab/biomodstack/biomodstack
    apiBaseUrl: https://compute-node.taileb3a90.ts.net
    routerBasename: /

  cordova.runtime.phone.json
    same Tailscale/phone-oriented target as above

  cordova.runtime.emulator.json
    Android emulator target
    apiBaseUrl: http://10.0.2.2:8000

  Notes:
    - 10.0.2.2 is the Android emulator alias for the host machine's localhost.
    - For a physical phone, the current default is the BioModStack Tailscale host.
    - routerBasename stays / because Cordova serves the app from local www/.

Host toolchain expectations:
  - Node.js 20+
  - npm 10+
  - pnpm available on PATH
  - Java runtime 17 available on PATH
  - Android SDK installed under $HOME/Android/Sdk or another path exported as ANDROID_SDK_ROOT
  - local Gradle 8.7 is supported if present at $HOME/.local/gradle/gradle-8.7

Android SDK bootstrap from this wrapper:
    cd /home/dalab/Desktop/BioModStack Cordova Android Project
    npm run install:sdk

One-time source-checkout bootstrap:
  The BioModStack source checkout must already have its frontend dependencies installed.
  If /home/dalab/biomodstack/biomodstack/platform/frontend/node_modules is missing, bootstrap the source checkout once:

    cd /home/dalab/biomodstack/biomodstack
    pnpm install --frozen-lockfile

One-time Cordova wrapper bootstrap:
    cd /home/dalab/Desktop/BioModStack Cordova Android Project
    npm install
    npx cordova platform add android@13.0.0

Build the web assets only:
    cd /home/dalab/Desktop/BioModStack Cordova Android Project
    npm run prepare:www

Full debug APK builds:
    cd /home/dalab/Desktop/BioModStack Cordova Android Project
    export ANDROID_SDK_ROOT=$HOME/Android/Sdk

    npm run build:debug:phone
    npm run build:debug:emulator

Expected Cordova APK location:
    /home/dalab/Desktop/BioModStack Cordova Android Project/platforms/android/app/build/outputs/apk/debug/app-debug.apk

Convenience copy created for tomorrow:
    /home/dalab/Desktop/BioModStack Android APK/BioModStack-debug-phone.apk
    /home/dalab/Desktop/BioModStack Android APK/BioModStack-debug-phone.apk.sha256
    /home/dalab/Desktop/BioModStack Android APK/README.txt

Verification:
    cd /home/dalab/Desktop/BioModStack Cordova Android Project
    npm run verify:apk

What verify-apk.sh checks:
  - the APK file exists
  - size and sha256 are printed
  - AndroidManifest.xml, classes.dex, assets/www/index.html, assets/www/bms-runtime-config.js, and assets/www/bms-cordova-shim.js are present inside the APK zip
  - if apksigner is on PATH, signature verification is printed
  - if adb sees a connected device or emulator, the APK is installed and a launch smoke test is run

How API/base URL injection works:
  - scripts/prepare-bms-assets.mjs generates www/bms-runtime-config.js from the selected cordova.runtime*.json file
  - the generated runtime config sets window.__BMS_ROUTER_BASENAME__ and window.__BMS_API_BASE_URL__
  - www/bms-cordova-shim.js is injected into index.html before the built Vite module and rewrites /api... requests for fetch, XMLHttpRequest, sendBeacon, EventSource, WebSocket, and image/src consumers to the configured apiBaseUrl

Why the Vite build uses --base ./:
  The upstream frontend vite.config.ts uses /bms/ for production builds. Cordova needs relative asset paths so the packaged app can load its JS/CSS from local www/. This wrapper overrides the base only for the external build command and keeps the upstream repo untouched.

Current verified APK:
  - built successfully on this machine
  - sha256: 5a6095565479eb321c61cf67416143f1f5498a2bffab48b857773d5488839bf2
  - copied to Desktop for manual phone transfer

Important runtime note:
  If the app launches but API requests fail, the most likely blocker is backend CORS for the Cordova app origin (`https://localhost`). The wrapper is already rewriting `/api...` to the configured host; a backend allowlist change or a later native-HTTP fallback would be the next fix.
