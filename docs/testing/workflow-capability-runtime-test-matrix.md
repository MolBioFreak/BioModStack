# Workflow Capability Runtime Test Matrix

Status: prepared, not executed
Scope: ESMFold2 and BoltzGen workflow-only architecture
Authorization gate: execute after the workflow-only implementation is pushed to `test`

## Global pass criteria

- No submission creates a standalone `esmfold2`, `esmfold2_experimental`, `boltzgen_design`, or `boltzgen_ligand` workflow product.
- ESMFold2 artifacts use canonical provenance `"workflow": "esmfold2"`.
- False resume omits `-resume`; commands never emit `-resume false`.
- Every job reaches a terminal state and is ingested.
- CIF/PDB outputs open in Mol* and integrity validation passes.
- Historical ESMFold2 aliases canonicalize to Structure Prediction; direct BoltzGen launch requests are rejected.

## Negative boundary tests (non-GPU)

| ID | Submission | Expected |
|---|---|---|
| N1 | Resolve/build `model_id=boltzgen`, `mode=design` | Rejected with parent-workflow guidance |
| N2 | Inspect API templates and frontend cards | No dedicated ESMFold2/BoltzGen launcher |
| N3 | Inspect Nextflow registry/files | No `workflows/esmfold2_experimental.nf` or `workflows/boltzgen_design.nf` |
| N4 | Resolve `model_id=esmfold2_experimental`, `mode=predict` | `workflows/structure_prediction.nf`, profile `esmfold2` |

## Authorized runtime tests

### R1 — Structure Prediction → ESMFold2

- Parent: `workflows/structure_prediction.nf`
- API identity: `model_id=esmfold2`, `mode=predict`
- Input: one short protein sequence; one sample; fast/lowest-cost preset
- Expected process: `ESMFold2Predict`
- Expected outputs:
  - `final/esmfold2/esmfold2_results/*.cif`
  - `*.metrics.json`, `*.telemetry.json`, `manifest.json`, `summary.tsv`
  - canonical `"workflow": "esmfold2"`
- Pass: successful terminal state, ingestion count ≥1, Mol* load, finite pLDDT/pTM, no standalone workflow identity

### R2 — Mutagenesis Library → ESMFold2

- Parent UI: Mutagenesis Library
- Routed API identity: `model_id=esmfold2`, `mode=predict`
- Parameters: `predictor=esmfold2`, `pred_method=esmfold2`, one explicit mutation, one candidate
- Expected Nextflow parent: `workflows/structure_prediction.nf`
- Pass: submitted sequence reflects mutation; ESMFold2 artifact/telemetry/provenance pass R1 criteria; job remains categorized as mutagenesis-originated workflow context, not a standalone product

### R3 — De Novo → ESMFold2 validator

- Parent: `workflows/antibody_denovo.nf`
- API identity: `model_id=antibody_denovo`
- Parameters: minimal RFantibody generation, `run_structure_validation=true`, `structure_validator=esmfold2`, one retained candidate
- Expected process: `BatchESMFold2Validation`
- Pass: parent workflow reaches validation, normalized validation JSON forces `"workflow": "esmfold2"`, at least one validated candidate is ingested, no MSA requirement is introduced for ESMFold2

### R4 — De Novo → BoltzGen generator

- Parent: `workflows/antibody_denovo.nf`
- API identity: `model_id=antibody_denovo`
- Parameters: `denovo_generator=boltzgen`, minimal design count, supervised target/scaffold inputs
- Expected internal processes: `PrepBoltzGenInput`, `RunBoltzGen` or approved child-orchestrator equivalents, aggregation into the parent job
- Pass: no child or result is exposed as a standalone BoltzGen product; parent job owns status/artifacts; downstream gates remain reviewable

### R5 — Protein Design → BoltzGen required caller

- Parent: `workflows/protein_design.nf`
- Parameters: `diffusion_method=boltzgen`, `run_docking=false`, minimal design count
- Forbidden parameter: `run_boltzgen_only` (must not exist)
- Pass: BoltzGen output continues to parent analysis/publish stages; direct standalone exit path is absent

## Execution order

1. N1–N4
2. R1
3. R2
4. R3
5. R4
6. R5 only if the protein-design caller remains product-required after R4 review

Stop immediately on provenance mismatch, unexpected standalone identity, failed ingestion, missing telemetry, or any command that targets a deleted standalone entrypoint.
