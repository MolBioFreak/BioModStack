# BioModStack Android wrapper

This optional Cordova client presents the BMS web workbench and talks to the
managed API. It does not own a second API, workflow scheduler, or robot runtime.
The native APK and web UI update payloads are distinct release surfaces.

## Source and generated directories

`local-plugins/` contains the first-party UI-bundle and APK-updater plugins.
Their nested `www/` JavaScript directories are source and must remain tracked.
Only wrapper-root `www/`, `platforms/`, `plugins/`, and `.cache/` are generated
output. Keep installed dependencies, signing credentials, private runtime
configuration, and ad-hoc APK builds out of Git.

[package.json](package.json) defines wrapper preparation, Android requirement,
build, verification, and UI-publishing commands. The wrapper has its own locked
npm manifest; do not add it to the pnpm workspace or regenerate either lockfile
as part of unrelated work. Local runtime overrides use the ignored
`cordova.runtime.local.json` or `cordova.runtime.*.local.json` paths. Select and
review the intended build/runtime configuration explicitly before packaging.

## Local source validation

From this directory, run the dependency-free wrapper tests:

```bash
node --test ./tests/*.test.mjs
```

These tests do not build an APK, install an Android SDK, sign a release, contact
a device, publish an update, or establish device qualification. Those operations
require the existing approved build/release process and its acceptance checks.

## Release ownership

Approved release payloads and their signing/update identity are described in
[artifacts/android/README.md](../../artifacts/android/README.md). Preserve the
active internal-updater APK and its checksum. A locally produced debug build
must not replace the approved payload or enter the update channel implicitly.

See [Repository Maintenance](../../docs/Repository_Maintenance.md) for exact-byte
exceptions and [the main README](../../README.md) for managed-service setup.
