# Results and Analysis

BioModStack tracks runs as structured records, not just folders of output
files. The review surfaces in the UI are built on top of the API data model and
stage-aware artifacts.

## Core Objects

### Jobs

`Job` records represent orchestration-level runs:

- model/workflow selection
- mode
- params
- output directory
- queue and GPU state
- completed stages and stage outputs
- child/parent relationships for orchestrated workflows

### Designs

`Design` records represent concrete candidate outputs and derived artifacts:

- structure paths
- stage lineage
- parent/origin relationships
- confidence and interface metrics
- review metadata
- artifact identity and stage-family context

### Analyses

`AnalysisRun` records represent persisted downstream analysis tasks and cached
results.

## API Surfaces

The main result-facing routes are:

- `/api/jobs`
- `/api/designs`
- `/api/analytics`
- `/api/files`
- `/api/analyses`

Supporting routes also feed result review, for example framework, MSA, and
sequence-library endpoints.

## Frontend Surfaces

The current review-facing routes are:

- `/designs`
  results browser / data viewer
- `/jobs/:jobId`
  job detail page
- `/`
  dashboard
- `/infra`
  workstation telemetry rather than scientific analysis, but still part of the
  operator review surface

## Runtime Output Roots

Resolved via [platform/api/paths.py](../platform/api/paths.py):

- results root:
  `bms_results`
- work root:
  `work`
- analysis cache:
  `analysis_cache`
- inputs root:
  `inputs`

These are usually placed under `BMS_DATA`, which on the current workstation is
typically `/mnt/BioModStack`.

## Review Semantics

The structure pipelines persist enough metadata to support:

- stage-aware output browsing
- lineage-aware relaunch and refinement
- validator-aware confidence and aligned-error handling
- persisted analysis caching
- review refresh when richer metadata becomes available

That means downstream docs and tooling should refer to jobs/designs/analyses and
stage outputs together, not just “check the PDB folder.”

## Practical Reading Order

For operator use:

1. identify the workflow family in [Structure Design and Refinement](Structure_Design_and_Refinement.md)
2. use the job launcher / results viewer in the frontend
3. use the API routes above for programmatic inspection

For developers:

1. start with [platform/api/README.md](../platform/api/README.md)
2. then check [docs/ai_guidance/Database_Instructions.md](ai_guidance/Database_Instructions.md)
3. then inspect the relevant workflow and ingester/review services
