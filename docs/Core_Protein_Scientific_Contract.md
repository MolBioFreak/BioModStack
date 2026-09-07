# Core-protein scientific contract

This document describes the future-result contract defined by BMS-CP-SCI-01.
The release gate remains empty in `core_protein_scientific_contract.py`.
Local software verification and later runtime activation have separate owners.

## Admission and historical results

`Job.provenance.core_protein_scientific_contract` owns the revision. Request
parameters carry that revision to the workflow. A result file cannot grant
itself authority. New submissions use the current caller release gate; existing
jobs retain their stored revision during resume. Old rows and artifacts remain
unchanged. Consumers keep incompatible metric cohorts separate.

## Issue groups and source owners

| Group | Contract | Main owners |
|---|---|---|
| G-01 | Preserve ordered residue axes, author/label identities and insertion codes. Bind values to structure bytes. Require explicit biological roles before role-dependent work. | `scripts/lib/structure_identity.py`, `scripts/lib/boltz_native_identity.py`, `scientific_alignment_identity.py`, API scientific viewer services |
| G-02 | Decode the pinned FA-MPNN vocabulary, including X, through a declared producer dialect. | `scripts/analyse_fampnn_seq_probs.py` |
| G-03 | Retain native BoltzGen design pTM, affinity probability and refolding RMSD. Native pLDDT has its own identity. | `scripts/lib/boltzgen_native.py`, `scripts/filter_boltzgen.py`, `boltzgen_candidate_publication.py` |
| G-04 | Distinguish present, selected, fixed and scored residues. Zero-total probability rows remain unscored. Workflow declarations govern summary and mutation scopes. | FA-MPNN analyzer, policy admission and resolution, existing parent/child workflows |
| G-05 | Each active criterion requires finite evidence in its declared domain. Retain every input disposition and computation-failure reason. | `scripts/lib/filtering`, `rf_filter_criteria.py`, `rf_filter_stage_accounting.py` |
| G-06 | Match declared publications to persisted candidate identities. Retain deliberate rejections separately from lost results. | Existing producer manifests, `core_protein_result_contract.py`, native publication services, `result_state_integrity.py` |
| G-07 | Paper-style interface rank requires compatible native scalar iPTM and Rosetta interface energy from the same candidate/interface. | `design_metrics.py`, existing ingestion and design response paths |
| G-08 | Preserve ESMFold2 MSA inputs and explicit OpenMM false values through argument construction. Display requested and effective values from retained execution receipts. | Existing ESMFold2/OpenMM modules, `core_protein_execution_settings.py`, `ExecutionSettingsPanel.tsx` |
| G-09 | Bind maturation comparisons to native source-to-output correspondence. Full-domain values require complete declared coverage. | `maturation_native_adapter.py`, `maturation_correspondence.py`, existing preparation/scorer/filter paths, `MaturationEvidence.tsx` |
| G-10 | Aggregate only compatible metric descriptors. Preserve missingness and same-candidate complete pairs. | `scientific_analytics.py`, existing analytics routes and `ScientificAnalytics.tsx` |

API service filenames in the table are relative to `platform/api/services`.
Frontend components are under `platform/frontend/src/components`.

## Storage and metric interpretation

Validated compact scalar records belong in `Design.confidence_metrics`.
Full matrices and residue maps stay in hash-bound artifact files. Ingestion
binds candidate, document and source identities before publishing records.

Canonical metric states are `ok`, `unavailable` and `invalid`. An `ok` value is
a finite real number with a null reason. The other states carry a null value
and a nonempty reason. Boolean values cannot represent measurements. Real zero
remains zero. A source-specific unit conversion requires a declared dialect.

The FA-MPNN dialect emits softmax probabilities. Each value must be in [0,1].
Positive row totals must be within an absolute tolerance of 1e-6 from one;
normalization only removes that floating-point roundoff. Boolean measurements
are invalid. Zero-total rows remain unscored.

The existing BoltzGen rank combination retains equal weights for native
`design_ptm`, `affinity_probability` and `filter_rmsd`. Missing weighted evidence
has no comparable aggregate rank. Contradictory CSV identity aliases reject
before normalization can discard them. Detailed native source identities are
in `S01_BoltzGen_Native_Scalars.md`.

## RF stage authority

The orchestrator records RF task scheduling in durable job provenance.
Launch settings govern stage applicability and enabled criteria. Filter output
files supply evidence for those tasks. Their inventory must match the recorded
task set before ingestion. An active stage with zero tasks is distinct from a
skipped stage. Resume retains task membership and observed execution history,
including cache hits and new work hashes. Stage counts do not establish final
candidate fanout.

## Maturation boundaries

Native parser/export hooks retain source residue identity through supported
transformations. The shared preparation step captures PDBInfo before any
positional restoration. Missing correspondence makes full-domain RMSD and
sequence identity unavailable. An explicit subset report retains its coverage
and cannot satisfy a full-domain criterion. The selected-interface objective
keeps its existing required inputs.

PPIFlow checkpoint selection remains deferred. This contract changes neither
checkpoint defaults nor the paper-style formula `100 * iPTM - interface_score`.

## Acceptance boundary

BMS-CP-SCI-01 defines ten issue groups and 48 acceptance cases. Completion
requires evidence for the full set on one reconciled candidate. A focused gate
proves only its tested surface. Data-only workflow fixtures, temporary SQLite
records and mounted UI tests establish software behavior; model inference,
image qualification and deployment require separate approval.

Activation, historical repair, image changes and service operations remain
outside this implementation. Existing FrustraMPNN and conformational-mapping
owners retain their stronger native contracts. Unsupported callers remain
held until their complete producer/consumer path is accepted.
