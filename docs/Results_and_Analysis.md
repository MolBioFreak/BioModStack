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

## Global model-result rule

Every scientific model follows the mandatory
[Model configuration, operator control, and agent parity policy](Model_Configuration_Operator_Control_and_Agent_Parity.md).
Model-native outputs remain distinct. Shared BMS mechanisms handle typed data,
lineage, persistence, descriptive statistics, visualization, synchronized
selection, capture, comparison, export, and review wherever applicable.

A model result should provide the same core experience regardless of the workflow
that produced it. Workflow-specific pages may add biological context and actions.
They shall route into the global model workbench rather than create a reduced
parallel numerical authority.

The first complete implementation of this rule is the active
[FrustraMPNN global configuration and analysis workbench specification](specs/frustrampnn-global-configuration-analysis-workbench.md).
A FrustraMPNN result from Structure Prediction, de novo design, conformational
mapping, or an unrelated registered structure shall use the same authoritative
rows, statistics, charts, selection behavior, capture, exports, and provenance.

## Review Semantics

The structure pipelines persist enough metadata to support:

- stage-aware output browsing
- lineage-aware relaunch and refinement
- validator-aware confidence and aligned-error handling
- persisted analysis caching
- review refresh when richer metadata becomes available

Downstream docs and tooling should refer to jobs, designs, analyses, effective
model settings, configuration identities, and stage outputs together. A loose
output folder is not an authoritative result.

## Practical Reading Order

For operator use:

1. identify the workflow family in [Structure Design and Refinement](Structure_Design_and_Refinement.md)
2. use the job launcher / results viewer in the frontend
3. use the API routes above for programmatic inspection

For developers:

1. start with [platform/api/README.md](../platform/api/README.md)
2. then inspect the current database/data-model services under `platform/api/services/` and `platform/api/routers/`
3. then inspect the relevant workflow and ingester/review services
