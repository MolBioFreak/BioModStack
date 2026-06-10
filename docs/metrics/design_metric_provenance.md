# Design metric provenance and completeness

BioModStack result rows must distinguish model-native outputs, BioModStack-derived heuristics, validator confidence, and final ranking formulas. This prevents local triage metrics from being mistaken for FA-MPNN or PPIFlow paper-aligned evidence.

## FA-MPNN

- `fampnn_avg_psce` / `fampnn_psce` are predicted sidechain error/confidence values extracted from FA-MPNN output structure B-factors.
- Direction: lower is better.
- Use: sidechain QC gate.
- Not use: binding evidence, complete FA-MPNN sequence-design rank, or wet-lab priority by itself.
- `seq_probs` from FA-MPNN `sample_pkls` are collected as sampled residue probability, log-probability/pseudo-NLL, and entropy metrics.
- BMS also derives optional single-substitution mutation triage from the same `seq_probs` tensor:
  - `fampnn_mutation_score_source = seq_probs_log_odds_delta`
  - `fampnn_top_model_favored_mutations[]`
  - `fampnn_mutation_opportunity_count`
- Mutation deltas mean FA-MPNN assigned higher probability to an alternative residue than to the sampled residue in that local context. They are model-native likelihood triage, not experimental stability/binding truth.

## PPIFlow

- `ppiflow_objective_score` is currently a BioModStack-local maturation objective from local interface/geometry scoring.
- Direction: lower is better.
- Use: local refinement triage.
- Not use: upstream PPIFlow paper final rank unless validator confidence, Rosetta/interface score, and formula provenance are also present.

## Required row-level reporting

Every design row that surfaces these metrics should carry:

- metric key
- display name
- value
- unit
- direction
- source artifact
- scoring backend
- formula
- scope/region
- whether it is model-native, BMS-derived, validator-derived, or final-rank
- completeness status and missing upstream/paper-aligned metrics

## Completeness states

A candidate with pSCE but no FA-MPNN sequence probabilities is `partial`.

A candidate with BMS-local PPIFlow objective but no validator/Rosetta/composite rank is `partial`.

Only call a PPIFlow result paper-aligned when the final rank formula and source metrics are explicit.
