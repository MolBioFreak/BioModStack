# Tailnet environment selection and updater delivery plan

Date: 2026-07-26
Base revision: `6bb1932aef2f67d9b38e961d5dbf06a823a6b9d5`
Candidate branch: `feat/tailnet-environment-selector-20260726`

## Problem

The private Tailnet root currently proxies an independently managed Vite listener. It can therefore become a third deployment identity rather than an explicit view of canonical development or production.

## Required contract

1. Add one fail-closed host control function and CLI action accepting only `development` or `production`.
2. Resolve targets from the shared runtime profile:
   - development: canonical Vite frontend plus the one managed API on `8000` and `/mnt/BioModStack/biomodstack.db`;
   - production: canonical stable web container plus that same managed API/state, reached through a reviewed loopback-only nginx transport shim on `18081` that rewrites only the backend `Host` header required by the production web container;
3. Never route Vite to isolated `18002`/`.biomodstack-dev`. Start the existing managed core if needed, but do not rebuild, migrate, stop, or repoint the unselected frontend.
4. Capture the prior Tailnet root mapping, update only `/`, preserve unrelated Serve paths, and restore the prior root if post-switch verification fails.
5. Reject Funnel/public exposure and reject unsupported environment names before any service or Serve side effect.
6. Report selected environment, local frontend/API targets, listener owners, working directory/image identity, Git/image revision, health, and final Serve mappings.
7. Expose a Tailscale-identity-authenticated control path mapped directly to the existing loopback-only workflow adapter. This path is control plane only, works regardless of the selected frontend, and is not a third application deployment.
8. Update the trusted local Cordova preflight so the operator must choose Development or Production before Launch. The shell requests the authenticated switch, verifies the returned environment, then reloads the exact private live origin with a cache-busting environment marker. The remote frame receives no native updater bridge.
9. Increment APK version monotonically, preserve package/signing/origin/update policy, build from the exact committed candidate, and prove same-signer in-place upgrade in API 35.
10. Push source and publish the exact APK through the authenticated immutable update channel. Debug publication may use a non-production validation channel/path only; do not weaken the stable publisher's non-debuggable guard.

## Verification gates

- Unit tests: valid/invalid selections, no pre-validation side effects, target/URL mapping, existing Serve-root parsing, update command, rollback after post-switch failure, Funnel rejection, API authentication/proxy behavior, Cordova selector contract.
- Focused Python and Node suites, syntax checks, secret scan, diff check.
- Independent review of the exact candidate before commit/push.
- Live switch to development: listener cwd/revision, explicit managed-API `8000`/database identity, proof that `18002` remains closed, Tailnet root/API health, browser build identity and zero fresh console errors.
- Live switch to production without rebuild: immutable container image/revision, production API/database, Tailnet root/API health, browser asset identity and zero fresh console errors.
- Restore the requested final environment (development unless Christian states otherwise), then verify the APK sees that same selected runtime.
- APK ledger: package, version/code, min/target SDK, signer, bytes, SHA-256, embedded private origin/control markers, no fatal/uncaught app log.
- Published manifest/artifact read-back through the Tailnet route with exact size/hash/version/signing metadata.

## Rollback

- Tailnet switch rollback restores the prior `/` proxy target and removes the control-path handler when that handler was newly installed by a failed first switch.
- Runtime selection never stops or rebuilds the other environment.
- APK installation remains Android user-approved; no uninstall, data deletion, production signing, or physical-device action is authorized.
