# Protein Hunter Integration Status

## Primary product placement

Protein Hunter's first-priority BioModStack home is the **general de novo binder workflow** (`binder_design` migration target), not De Novo Design and not the retired antibody workflow.

Protein Hunter performs iterative whole-binder sequence–structure co-generation/search using SolubleMPNN/LigandMPNN with Boltz or Chai prediction and confidence-based internal triage. It is not target discovery, fixed-backbone local redesign, or antibody-framework/CDR-aware generation.

## Current status: internal and launch-blocked

The standalone public template and inventory entry are retired. The internal implementation remains for development:

- compatibility model: [`platform/api/config/models/protein_hunter_experimental.yaml`](../platform/api/config/models/protein_hunter_experimental.yaml)
- Nextflow workflow: [`workflows/protein_hunter_experimental.nf`](../workflows/protein_hunter_experimental.nf)
- module: [`modules/protein_hunter_experimental.nf`](../modules/protein_hunter_experimental.nf)
- request compiler: [`scripts/prep_protein_hunter_request.py`](../scripts/prep_protein_hunter_request.py)
- runtime wrapper: [`scripts/run_protein_hunter_inference.py`](../scripts/run_protein_hunter_inference.py)
- explicit-value helper: [`scripts/protein_hunter_runtime.py`](../scripts/protein_hunter_runtime.py)

Fresh launches fail closed. Protein Hunter is not advertised under De Novo Design.

## Scientific blocker

BioModStack requires **ipSAE** for Boltz-2 interface selection. iPTM is not accepted as a substitute.

The packaged Protein Hunter runtime currently:

- selects/saves candidates using `high_iptm_threshold`;
- emits `summary_high_iptm.csv` and `high_iptm_pdb/` cohorts;
- sets `write_full_pae: False` in `/opt/Protein-Hunter/boltz_ph/pipeline.py`;
- does not preserve the authoritative PAE artifact required by BioModStack's approved ipSAE computation.

Promoting it before repairing that data path would misstate interface quality.

## Work already retained

The internal launch path preserves explicit false/zero values rather than replacing them with defaults, including `percent_x = 0`, `cyclic = false`, `alanine_bias = false`, and explicit zero-valued numeric settings.

Historical Protein Hunter jobs remain classified as result-producing workflows so old successful jobs cannot be treated as valid while yielding zero authoritative Design rows.

## General binder integration requirements

1. Define canonical binder target, epitope, binder-chain, and target-chain roles.
2. Preserve one canonical candidate per requested trial; cycle snapshots remain lineage artifacts.
3. Preserve structure, full PAE, chain IDs, and role mapping together.
4. Compute BioModStack ipSAE and use it—not iPTM—for Boltz-2 interface selection.
5. Separate generator-internal triage from independent validation.
6. Emit canonical generated-complex producer metadata and authoritative Design rows.
7. Show Protein Hunter provenance, cycle lineage, validation provenance, and ipSAE in browser results.
8. Pass stub, managed Nextflow preview, ingestion, real GPU runtime, and browser-visible acceptance before enabling the launcher choice.

## Secondary uses

Other Protein Hunter use cases may be added after the primary binder path. It must not be presented as an antibody/nanobody generator unless a future method adds explicit framework, numbering, CDR, chain-role, and preservation semantics.
