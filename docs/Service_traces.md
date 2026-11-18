# Trace a request across services

Use these traces when a request succeeds at one layer but the expected record or result is missing at the next. The [module map](Services.md) lists the corresponding HTTP entry points. These are source boundaries, not instructions to run a workflow or contact a device.

## A Project run appears in Job detail but not in its experiment

Job detail reads core execution state. The Project view also needs a launch-context binding and the projection into the experiment store. Inspect the [setup](../platform/api/services/global_experiments/workflow_setups.py), [launch context](../platform/api/services/global_experiments/launch_contexts.py) and [Project read model](../platform/api/services/global_experiments/read_models.py) for the **same Job**. An attached result has its own [receipt](../platform/api/services/global_experiments/receipts.py); a missing receipt is a different problem from a Job that never started.

## A worker is visible but a remote result is missing

[Target inventory](../platform/api/services/remote_execution/targets.py) and [telemetry](../platform/api/services/remote_execution/telemetry.py) describe a target. [Claims](../platform/api/services/remote_execution/claims.py) and the [executor](../platform/api/services/remote_execution/executor.py) concern execution. [Bundles](../platform/api/services/remote_execution/bundle.py) describe what travels; [result generations](../platform/api/services/remote_execution/result_generation.py) identify what is returned. Inspect the affected attempt and result generation before interpreting a cache hit or terminal status as proof that a local file exists. See [retention and retrieval](Remote_Worker_Execution_and_Cache.md).

## An alignment Job finished; where is the reusable alignment?

Follow the [selected source](../platform/api/services/family_msa/sources.py) and [request](../platform/api/services/family_msa/requests.py) to the Job. Its [publication](../platform/api/services/family_msa/publication.py) and [result manifest](../platform/api/services/family_msa/result_manifest.py) are the starting points for the [reader](../platform/api/services/family_msa/read_model.py). For a Project-retained alignment, use its specific result ID. The [folding MSA consumer contract](Native_MSA_Consumer_Contracts.md) answers a different question: how a prediction workflow consumes an MSA input.

## A structure is visible but a specialist analysis is missing

For dynamics, inspect [lifecycle](../platform/api/services/md/lifecycle.py), [completion](../platform/api/services/md/completion.py) and the [read model](../platform/api/services/md/read_model.py) separately. For conformational mapping, distinguish the [request builder](../platform/api/services/conformational_mapping/request_builder.py) from [persistence](../platform/api/services/conformational_mapping/persistence.py) and [analysis](../platform/api/services/conformational_mapping/analysis.py). For FrustraMPNN, use the selected [identity](../platform/api/services/frustrampnn/identity.py), [manifests](../platform/api/services/frustrampnn/manifests.py) and [statistics Jobs](../platform/api/services/frustrampnn/statistics_jobs.py). A viewable structure does not establish that a particular analysis was requested, completed or published.

## A file exists, but the application will not reopen it

Distinguish an externally supplied archive from an artifact that BMS published. The [external import service](../platform/api/services/external_imports/service.py) and the [scientific artifact writer](../platform/api/services/scientific_artifacts/writer.py) have different roles. Follow the recorded identity through [artifact lookup](../platform/api/services/scientific_artifacts/query.py) and [resolution](../platform/api/services/scientific_artifacts/resolve.py). Do not substitute a similarly named file from a different Job to make the viewer open.
