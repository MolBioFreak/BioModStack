BioModStack Cordova Android shell

Purpose
  This project builds the BioModStack Vite frontend into a Cordova Android shell. It has two deliberately separate update mechanisms:

  1. UI bundle update: replaces only signed/versioned Cordova web assets.
  2. Native APK update: downloads a complete, signed Android package and hands it to Android's user-approved package installer.

  A UI bundle update cannot replace Kotlin/Java, Cordova plugins, permissions, package metadata, or the native shell. Those changes require a newly signed APK.

Runtime configurations
  cordova.runtime.phone.json
    apiBaseUrl: https://compute-node.taileb3a90.ts.net

  cordova.runtime.emulator.json
    apiBaseUrl: http://10.0.2.2:8000

  The phone origin is the only production native-update origin accepted by the Android policy. Emulator HTTP is restricted to the emulator build's network-security configuration and is not accepted for native APK downloads.

Clean bootstrap and debug build
  cd platform/mobile-cordova
  npm ci

  # The referenced BioModStack frontend checkout also needs its workspace dependencies.
  cd ../..
  pnpm install --frozen-lockfile
  cd platform/mobile-cordova

  export JAVA_HOME=/path/to/jdk-17
  export ANDROID_SDK_ROOT=$HOME/Android/Sdk
  export ANDROID_HOME=$ANDROID_SDK_ROOT
  ./scripts/build-apk.sh ./cordova.runtime.phone.json

The clean build prepares www/ before adding the Android platform, installs both local plugins, patches the generated MainActivity, builds the APK, and runs the wrapper verification script.

Expected debug APK
  platforms/android/app/build/outputs/apk/debug/app-debug.apk

Native APK update trust model
  Backend routes:
    GET /api/mobile-apk/channels/stable/manifest
    GET /api/mobile-apk/channels/stable/files/<content-addressed-name>.apk

  The backend accepts Tailscale identity headers only from an explicitly trusted proxy source. It fails closed unless both are configured:
    BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS
      Comma-separated source IPs, CIDRs, or exact local proxy host tokens.
    BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS
      Comma-separated exact Tailscale login identities allowed to fetch native updates.

  Tailscale Serve must terminate HTTPS, inject Tailscale-User-Login, and proxy from a source covered by BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS. Do not expose the API directly with that source allowlist; a direct client able to originate from a trusted source could forge the identity header.

  Every request revalidates bounded metadata. Manifest checks use a bounded inode/ctime-aware verification cache, while Range and full downloads copy and hash into a private bounded snapshot under a two-slot concurrency limit and stream only that verified snapshot.

Android policy
  The native shell fails closed unless all of these are true:
    - exact HTTPS origin https://compute-node.taileb3a90.ts.net
    - exact stable channel and stable artifact path
    - no redirect, URL userinfo, query, or fragment
    - package org.biomodstack.mobile
    - strictly increasing versionCode
    - declared versionName and minimum SDK match the downloaded archive
    - installed signer, declared signer, and archive signer match
    - bounded size, exact Content-Length/Content-Range semantics, exact final size, and SHA-256

  Partial downloads resume only when the immutable manifest identity still matches and the server returns a coherent 206 range. Otherwise the partial file is discarded and the download restarts. Android installation always requires user approval. Returning from the installer is reconciled through PackageManager; launching the installer is not reported as success.

Publishing
  platform/mobile-cordova/tools/publish_apk_update.py snapshots the source APK once, then inspects, hashes, validates, and promotes only those snapshot bytes. It publishes a content-addressed APK before atomically replacing manifest.json under a per-channel lock.

  Production publication requires the durable release certificate digest and a non-debuggable release APK. Never publish a debug-signed artifact to the production stable channel.

Release signing guard
  Any release packaging/signing/install task fails unless all four values are supplied through environment variables or Gradle properties:
    BMS_ANDROID_KEYSTORE_PATH
    BMS_ANDROID_KEYSTORE_PASSWORD
    BMS_ANDROID_KEY_ALIAS
    BMS_ANDROID_KEY_PASSWORD

  Do not store those values in this repository or in command history.

Verification
  Python/API and publisher tests:
    cd platform/api
    uv run --group dev python -m pytest -q tests/test_mobile_apk_updates.py tests/test_mobile_ui_updates.py

    cd ../mobile-cordova
    python -m pytest -q tests/test_publish_apk_update.py tests/test_android_apk_updater.py
    node --test tests/*.test.mjs

  Generated Android project:
    cd platforms/android
    tools/gradlew --no-daemon --max-workers=1 -p . testDebugUnitTest lintDebug assembleDebug assembleDebugAndroidTest
    ANDROID_SERIAL=<emulator> tools/gradlew --no-daemon --max-workers=1 -p . connectedDebugAndroidTest

Release gates that emulator/debug evidence does not satisfy
  - The preserved production keystore must sign the candidate and match the signer installed on real BioModStack devices.
  - The exact production release APK must be published and retrieved through the configured authenticated Tailscale path.
  - An authorized physical handset must complete the user-approved upgrade and retain application state.
  - Debug-emulator upgrade continuity is developmental evidence only; it cannot replace those gates.
