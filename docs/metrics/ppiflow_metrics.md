# PPIFlow metric harmonization

BioModStack uses two distinct classes of PPIFlow-related metrics.

## BMS-local maturation metrics

Fields such as `ppiflow_objective_score`, `maturation_selected_delta_interface`, and `maturation_selected_rmsd` come from BioModStack local pair-energy/geometry scoring around selected movable regions.

- Direction: lower `ppiflow_objective_score` is better.
- Use: fast local triage after partial-flow maturation.
- Not use: upstream PPIFlow paper final ranking.

## Paper-style PPIFlow ranking

The PPIFlow preprint describes ranking with validator confidence and Rosetta interface energy, summarized as:

```text
AF3 ipTM * 100 - Rosetta interface score
```

BioModStack records this as:

```text
100 * validator_iptm - rosetta_interface_score
```

only when both validator iPTM and Rosetta interface score are present.

## Sign-convention warning

The reviewed upstream PPIFlow code used a formula equivalent to:

```text
100 * validator_iptm + rosetta_interface_score
```

while the preprint/notebook wording uses subtraction. If Rosetta interface energies are raw negative REU values, these formulas disagree. BioModStack must therefore store:

- raw Rosetta interface score
- sign convention
- rank formula
- rank direction
- whether the row is local-only or paper-style composite ranked

## Completeness

Rows with only BMS-local PPIFlow objective are `local_triage_only` / `partial`.
Rows with validator confidence plus Rosetta interface score can receive a paper-style composite rank.
Rows without DockQ/template-free refold validation should still show that final validation is incomplete.
