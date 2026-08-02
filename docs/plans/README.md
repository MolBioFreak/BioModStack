# BioModStack plans

This directory is for active implementation plans and transition notes only.
Canonical product/runtime behavior belongs in `docs/*.md`; dated plans are not truth sources.

## Active plan surface

These files are still useful as current rollout, guardrail, or follow-on implementation context:

- [MSA control-plane guardrails and regression checklist](2026-04-22-msa-control-plane-guardrails-and-regression-checklist.md)
- [Local high-quality MSA target-DB sharding spec](2026-04-23-local-msa-target-db-sharding-spec.md)
- [DRAM→VRAM tiled runtime proof plan](2026-04-24-dram-vram-tile-runtime-proof.md)
- [Fold-CP DRAM context-spill workhorse implementation spec](2026-04-24-fold-cp-dram-context-spill-additional-work-spec.md)
- [RepA local MSA root-cause and fix spec](2026-04-24-repa-local-msa-root-cause-and-fix-spec.md)
- [MolBio read-QC harmonization implementation plan](2026-04-25-molbio-read-qc-harmonization-spec.md)
- [Caliby finishing changes specification](2026-04-27-caliby-finishing-changes-spec.md)
- [GPU MMseqs EnvDB fix implementation plan](2026-04-27-gpu-mmseqs-envdb-fix-spec.md)
- [UI surface channel cutover implementation spec](2026-04-28-ui-surface-channel-cutover-spec.md)
- [ONT MinKNOW instrument-control integration spec and gap assessment](2026-06-08-ont-minknow-instrument-control-integration-spec.md)

## Archived plans and historical specs

Superseded implementation tranches, older speculative specs, and one-off audit artifacts live under [archive/](archive/).
Keep them for traceability, but do not treat them as current product contracts.

Recent cleanup keeps this surface time-bounded:

- dated plans/specs from 2026-04-20 or earlier were removed from the tracked docs surface instead of being archived indefinitely
- superseded 2026-04-21 Fold-CP and local-MSA tranche notes live under `archive/`
- superseded 2026-04-23 Fold-CP reassessment/worker-pool notes live under `archive/`
- the 2026-04-24 Fold-CP GPU communication fact note is historical evidence under `archive/`, not an active rollout plan
- the four 2026-05 BioXP proxy/console/interlink plans remain in place only for
  traceability and are superseded by
  [`../BioXP_Compact_Control_Plane.md`](../BioXP_Compact_Control_Plane.md); they
  are not active implementation contracts

## How to use this folder

1. Read the canonical docs first.
2. Use this folder only for active rollout/spec/checklist material.
3. Archive stale plans instead of leaving them beside active plans.
4. When implementation makes a plan historical, move it to `archive/` and patch references in the same cleanup.
