# BioXP Phase 1 verification — 2026-07-17

**Status:** Complete with explicit user review waiver. This is not an independent-review approval.

## Scope and ownership

- Branch / HEAD: `test` / `188d69c2e266f82d14d4ac0778dc17785c2dfead`
- Phase 1 implementation fingerprint: `411c7651dfaf51e3d99ab2ca274065fd495bebcfa4365c73d32a0f018a0cdd73`
- Canonical evidence checksum: `10ac494c87fd7d56286d079fef39f6c13c8cfa819d6e014eb1d86cf91f2e508b`
- Evidence JSON SHA-256: `e31187e2786d5d494d113a6bd46a8fe94987cf3d3307cf0c68586d79a4a6310e`
- Dirty entries: Phase 1 baseline `206`; final `526`. Unrelated concurrent work was preserved.
- `compose.core-runtime.yml` is partially owned: Phase 1 owns only the five `BMS_BIOXP_*` containment/policy lines.

## Delivered containment

- Removed BMS-owned robot logs, runtime reset, and robot reboot routes, shell helpers, client hooks, and UI controls.
- Added a default-off mutation switch and constant-time token auth with strict token-file precedence.
- Applied containment to every registered non-GET BioXP route; exact local/dry-run/compiler exemptions are regression-tested.
- Added trusted-network URL validation and proxy-time revalidation; unsafe address classes, public-by-default, credentialed, path/query/fragment, and mixed/untrusted DNS targets are rejected.
- Preserved read-only status, local profile/session operations, diagnostics, explicit dry-runs, and feature gating.

## Verification

| Gate | Result |
|---|---|
| Phase 1 containment | **29 passed** |
| Seeded focused BioXP + `strace` | **137 passed, 2 skipped** |
| Network syscalls | **0 AF_INET, 0 AF_INET6, 231 AF_UNIX** |
| Full API regression | **1,024 passed, 4 skipped** |
| Frontend | **319 passed**; production build passed |
| Ruff / py_compile / compose / manifests / diff | **pass** |

## Review and deferred validation

Christian explicitly waived independent review for Phases 0 and 1; verification was not waived.

**NOT RUN:** robot host access, SSH, systemd, journal retrieval, reboot, hardware motion, deployment, migration, service restart, or runtime integration. Offline evidence does not establish those facts.

Mutations remain disabled unless `BMS_BIOXP_MUTATIONS_ENABLED=1` and a valid token is supplied by an authorized client or trusted proxy. No token is embedded in frontend code.

Phases 2–8 remain unauthorized and unstarted.
