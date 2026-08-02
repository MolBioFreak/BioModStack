# Legacy branch consolidation — 2026-08-02

**Branch:** `legacy/combined-discrepancies-20260802`
**Initial canonical ancestor:** `8218384b9ffe27241155a4387777a58945a9cfc0`  
**Canonical test merged after consolidation:** `15a74e27de7e0d297958c805aaf48573bb2ace2b`

This is an archival, non-deployment branch that consolidates the remaining divergent historical source branches into one recoverable working tree.

## Merge policy

Each named source branch was merged into this branch with Git's recursive `ort` strategy and `-X ours`:

- non-conflicting historical changes are materialized in this branch's working tree;
- where a hunk conflicted, the already-consolidated branch side was retained rather than silently selecting a competing legacy implementation;
- every source branch tip is a merge ancestor of this branch, so the exact original variant remains recoverable through Git history even where the materialized working tree selected the canonical/consolidated side.

This branch is **not a deployment candidate** and must not be promoted to `test` or `main` without a component-specific review and acceptance gate.

## Consolidated divergent sources

- `feat/tailnet-environment-selector-20260726`
- `hermes/full-oem-control-parity-live-owner-20260726`
- `release/bioxp-axis-live-owner-20260726`
- `release/bioxp-inline-evidence-owner-20260727`
- `release/bioxp-inline-evidence-owner-v2-20260727`
- `release/bioxp-inline-integrated-owner-20260727`
- `release/bioxp-recovery-ee20e9f-20260729`
- `release/bioxp-release-owner-v3-20260727`
- `release/gibson-dnaweaver-ngs-20260726`
- `release/mobile-apk-0.4.1-20260728`
- `release/mobile-apk-0.4.2-20260729`
- `release/mobile-apk-0.4.3-20260729`

## Recovery

To inspect an original source-tip version of a path, use:

```bash
git show origin/<historical-branch>:<path>
```

After the old ref is removed, use the merge parent found with:

```bash
git log --merges --oneline legacy/combined-discrepancies-20260802
```

The original commit object remains reachable through the legacy branch's merge ancestry.
