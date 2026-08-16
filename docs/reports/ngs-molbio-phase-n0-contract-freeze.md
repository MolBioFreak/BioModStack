# NGS/MolBio and Protein Phase N0 Contract Freeze

## Status

The static Phase N0 management-plane package is complete at baseline commit `d2fc413d6d0224fe9fbecb1cb1797e0456ca1517` and tree `f89094ba373e3dd8fa181fd17d942e54a6f0f63e`.

This package harmonizes `ngs_molbio` and `protein_in_silico` under the global Project, Experiment, Domain v2, Dataset, receipt, lineage, dispatch, and read-model authorities. It does not enable runtime dispatch or claim release acceptance.

No tests, verifier runs, imports, compilation, services, deployments, migrations, browser checks, or scientific computations were run after this harmonization.

## Shared authority

- The shared outer Domain schema is `bms.domain-experiment.v2`.
- Global authority owns Project, Experiment, Domain, revision, Dataset, workflow, attempt, receipt, lineage, dispatch, and read-model identities.
- Domain payloads remain owner-defined. Protein scientific payload semantics are not copied into the shared plane.
- Dataset members bind receipt ID, native identity, exact revision or generation, content and contract digests, role, ordinal, and bounded canonical member JSON.
- Historical preparation forbids current-head substitution.
- Runtime authority loads package-local bytes and validates their bound hashes. Runtime authority does not inspect Git.
- The Domain binding adapter remains first-class and `missing` until Phase N1.

## Protein Dataset denominator

The ten frozen Protein Dataset IDs remain visible. All ten stay disabled in Phase N0.

Seven IDs have closed, source-supported immutable member contracts:

- `protein.generated_candidate_cohort.v1`
- `protein.selected_finalist_cohort.v1`
- `protein.structure_prediction_validation_result_cohort.v1`
- `protein.cm_ensemble_conformer_cohort.v1`
- `protein.md_replica_analysis_cohort.v1`
- `protein.frustrampnn_landscape_guidance_cohort.v1`
- `protein.compatible_comparison_cohort.v1`

Their shared compatibility rules are:

- `same_project_domain_authority`
- `exact_immutable_revision_only`
- `adapter_role_intersection`
- `no_current_head_resolution_during_preparation`
- `exact_historical_reopen`

The member bounds are 0 through 10,000. The allowed domain is `protein_in_silico`.

Three IDs are explicitly unavailable because no immutable producer-native member adapter exists:

- `protein.target_set.v1`
- `protein.template_motif_partner_control_set.v1`
- `protein.saved_review_filter_selection.v1`

These rows have no allowed members. No target, context, or saved-review contract was fabricated.

The closed rows use eight existing native adapter authorities. The executable RFD3 adapter ID remains `bms.core-job.protein_local_redesign.adapter.v1`; the package does not normalize that source identity.

Cohort granularity follows current native authority. CM admits complete ensembles, MD admits whole-run generations, and RFD3 admits whole-result candidate sets. Independent conformer, replica, and candidate authorities require later native adapters.

## Protein constraint denominator

The Protein constraint payload registry contains zero schema IDs.

`design_constraints` has `maxItems: 0`. The only valid value is `[]`. Every non-empty list fails closed. The wrapper schema is closed-empty and cannot validate a constraint instance.

## Frozen package surface

The package contains:

- 21 capability IDs, all non-plannable;
- 27 member-adapter IDs plus the first-class binding adapter;
- 12 connector event families;
- 16 Dataset kind IDs;
- one empty Protein constraint payload registry;
- five payload ownership classes;
- package-local source pins, schema byte hashes, canonical schema hashes, registry hashes, and a payload manifest receipt.

The NGS/MolBio capability denominator and payload ownership boundary are unchanged.

## Later runtime gates

Phase N0 static closure does not satisfy these runtime gates:

- N1: persist exact Domain bindings and binding revisions.
- N2: persist ordered connector commands, events, acknowledgements, streams, and generations.
- N3: implement workflow wrappers, immutable Dataset preparation, and dispatch receipts.
- N4: expose typed operator actions and matching agent contracts.
- N5: implement the payload scanner and retain a no-active-job audit.
- N6: run runtime, UI, historical-reopen, deployment, and release acceptance.

These gates do not reopen the frozen N0 denominator. A future Protein payload or member kind needs a new source-supported contract version.

## Receipt semantics

`docs/reports/ngs-molbio-phase-n0-verification-v1.json` binds every package payload byte and records `not_run_by_operator_instruction` for post-harmonization verification. Its status is `static_contract_freeze_complete`.
