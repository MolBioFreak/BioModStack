# BioModStack Electron Shell

This package provides the optional Electron desktop shell for BioModStack.

It is a shell around the hosted `/bms/` UI, not a second backend supervisor.
The Electron app launches the same web surface used by the browser path and
bridges runtime actions through `scripts/manage_desktop_services.py`.

## Role in the platform

The current runtime split is:

- browser is the default shell
- Electron is an additive packaged desktop shell
- API/web runtime is normally owned by the shared service layer
- workflow execution remains host-native through the workflow adapter
- Electron should request start/stop/restart/status through the shared Python
  control plane rather than owning API/frontend processes directly

## Entry points

- `src/main.ts`
  main-process bootstrap, tray/menu wiring, window lifecycle, diagnostics, IPC
- `src/preload.ts`
  audited renderer bridge
- `src/serviceControl.ts`
  bridge to `scripts/manage_desktop_services.py`
- `src/shellPaths.ts`
  path discovery using `BMS_HOME`, install profile, and workstation heuristics
- `src/windowState.ts`
  runtime/shell context passed into the hosted UI

## Current shell features

The Electron shell currently includes:

- load of the hosted `/bms/` UI through a dedicated persistent partition
- tray and application menu integration
- hide-to-tray lifecycle behavior
- open-in-browser support
- runtime status/start/stop/restart/restart-api actions
- zoom controls with persisted zoom factor
- open results folder, logs, and shell-data helpers
- diagnostics for renderer/load failures
- browser fallback behavior when Electron is only a stored preference and the
  shell runtime is unavailable

## Install and run

From the repo root:

```bash
pnpm --dir platform/desktop-electron install
./start_ui_electron.sh --runtime container
```

Or run package-local scripts once dependencies are installed:

```bash
pnpm --dir platform/desktop-electron test
```

## Environment and path notes

Important inputs include:

- `BMS_HOME`
- `BMS_RUNTIME_MODE`
- `BMS_FRONTEND_ORIGIN`
- `BMS_ROUTER_BASENAME`
- the persisted install profile in `~/.config/biomodstack/install_profile.json`

The shell also uses per-user Electron storage under the normal Electron
`userData` location and persists zoom state there.

## Relationship to Android/mobile work

The Electron package is separate from the optional Android thin-shell path.
They share the same hosted UI contract and mobile/Electron docs should describe
that commonality, but this package does not build the APK wrapper.

## Related docs

- [../../docs/Desktop_Runtime_and_Shell_Architecture.md](../../docs/Desktop_Runtime_and_Shell_Architecture.md)
- [../../docs/Workstation Set Up and Install Guide.md](../../docs/Workstation%20Set%20Up%20and%20Install%20Guide.md)
- [../../README.md](../../README.md)
