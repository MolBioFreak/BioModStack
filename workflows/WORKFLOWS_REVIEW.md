# Workflows Code Review — Concerns Summary

Scope: `workflows/*.nf` (Nextflow DSL2). Focus is correctness of implemented features, not model defaults or scientific choices.

## High severity (likely runtime failures)
- None outstanding after this pass.

## Medium severity (logic bugs / unrouted features)
- None outstanding after this pass.

## Low severity (compatibility / robustness)
- `THERMOMPNN` post‑filtering assumes a PDB exists at `${csv.parent}/${meta.id}.pdb`; if the module names outputs differently, downstream will pass CSV paths into PDB‑only logic. (`antibody_denovo.nf:954-957`)

## Currently unused / gated branches (not used yet, but would break if enabled)
- AntiFold emits FASTA per comments, but downstream assumes PDBs (ThermoMPNN, Boltz, `extractSequenceFromPDB`). If/when AntiFold is enabled, this path will likely misbehave. (`antibody_denovo.nf:854-877`)
- AntiBERTy uses PDBs as input though it expects FASTA per comment; filtered outputs are FASTA but downstream treats them as PDBs. Not used today, but breaks if enabled. (`antibody_denovo.nf:1192-1208`)

## Resolved in this pass
- Replaced missing `${projectDir}/lib/NO_JSON` placeholder references with existing `${projectDir}/lib/empty-meta.jsonl`.
- BindCraft now validates `bindcraft_target_pdb` existence; optional scaffold uses `${projectDir}/lib/NO_TARGET_PDB` instead of `file('null')`.
- BindCraft SWA toggle and boolean params now preserve explicit `false` values (no `?: true` default override).
- `WaitAndAggregateChildResults` now passes `batch_name`, warns if child count mismatches, and prefixes child index to avoid PDB filename collisions.
- Fixed questionable `toList()` usage by switching to `collect()` for channel aggregation.
- `rfdiffusion.nf` now uses actual batch size and guards `designStartnum`.
- `antibody_child.nf` now uses `trim()` for Java 8 compatibility.
- Added guard to error out when no designs exist before MSA generation.
- Implemented `bindcraft_boltz_validation` using `PrepBoltz` + `RunBoltz` and routed `final_pdbs` to validated outputs when enabled.
- FAMPNN constraints are now selectable (generic vs antibody) via `fampnn_constraint_mode`; PrepFAMPNN switches constraint generation accordingly.
- Antibody pipeline defaults `fampnn_constraint_mode` to `antibody`, and the FAMPNN child spawn now forwards this param.
- Added generic FAMPNN constraints CSV generator and surfaced constraint mode in UI/config/docs.
