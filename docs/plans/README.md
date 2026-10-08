# BioModStack plans

This directory is for active implementation plans and transition notes.

Rules:

- canonical product/runtime behavior belongs in `docs/*.md`, not here
- dated plan files are planning artifacts, not truth sources
- once a plan is superseded or largely absorbed into canonical docs, move it out
  of the active top-level plan surface

## Active plans

These are the plans still worth surfacing directly from the docs index:

- [2026-04-20 Android APK thin-shell comparison](2026-04-20-android-apk-thin-shell-comparison.md)
- [2026-04-20 control plane / Electron / install-path upgrade](2026-04-20-control-plane-electron-runtime-paths-upgrade.md)
- [2026-04-20 core-runtime workflow-adapter cutover](2026-04-20-core-runtime-workflow-adapter-cutover.md)
- [2026-04-20 Fold-CP large-protein sharding plan](2026-04-20-fold-cp-large-protein-sharding-plan.md)

## Archived plans

Older implementation/spec notes should live under [archive/](archive/) once they
are no longer the active planning surface.

Examples of what belongs in the archive bucket:

- one-off implementation tranche notes
- superseded containerization specs
- earlier Electron-port sketches that are now reflected in canonical docs and
  the live shell package
- transition notes kept only for historical auditability

## How to use this folder

1. read canonical docs first
2. come here only if you need an active rollout or migration note
3. archive old plans instead of letting them accumulate beside active ones
