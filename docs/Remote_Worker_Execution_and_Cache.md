# Remote worker execution, cache, and explicit result retrieval

Status: implementation contract; software and live acceptance must be reported separately. This document does not declare the working tree deployed or every workflow remotely supported.

## Operator contract

A remote selection changes execution placement, not scientific settings. The whole selected supported workflow—including CPU preparation, selected inference, required downstream FrustraMPNN, and artifact publication—runs on that worker. A missing runtime, unsupported dependency, or unavailable worker must produce a visible blocker or failure, never silently move science locally or disable a selected stage.

The local BMS API/UI remains the control plane. SSH is the existing worker-control transport. The self-contained path must not require the remote worker to join the workstation Tailnet or call back to the workstation API. Remote activity/status metadata is distinct from scientific result payloads.

Remote scientific completion leaves outputs on the worker and produces a durable **Pull results** action. Opening a page, refreshing, reconnecting, polling, or restarting BMS must not authorize a scientific payload transfer. An explicit pull verifies the original job, attempt, target, source revision/tree, runtime, manifest and bytes before publishing into the normal BMS result/analysis mechanisms. It is not a new scientific run.

A failed remote attempt is not successful because diagnostic files can be retrieved. Preserve the failure receipt, error and original attempt identity. Any authorized failure-artifact retrieval must not call successful scientific finalization.

## Issues addressed by this revision

- **BMS-DEV-41:** structure-prediction launcher must represent actual remote-worker placement and availability; a selected remote worker must not be confused with workstation GPU selection. Preserve typed browser/API scientific-setting parity.
- **BMS-DEV-44:** show the actual current worker activity and artifact being transferred, and provide a separate operator-controlled mechanism to preload model/runtime assets and BMS workflow code before execution. Preloading is not a scientific job and never silently submits one.

The earlier deferred status of issue 44 is historical. The current revision request authorizes implementation. Neither issue is cleared merely because a source patch or unit test exists.

## Cache authority and provisioning

A cache is an optimization, not a new source of scientific truth. Reuse requires the same declared content hashes, byte sizes, roles and runtime identity that normal remote bundle verification consumes. A filename, modification time, directory existence, or cache-success marker alone is insufficient.

Required invariants:

- Template provisioning and BMS-controlled preload populate a cache consumed by normal launch, rather than parallel unused directories.
- Cache hits are verified; missing or corrupt objects are never used as model weights or executable code.
- Downloads/transfers publish only complete verified objects atomically. Interrupted writes must not poison a later hit.
- Concurrent preparations do not overwrite an in-use immutable asset. Source and runtime materializations belong to one attempt; a new attempt must not inherit unmanifested files from an older source working directory or runtime generation. Runtime relocation must preserve exact source authority, including supported runtime-internal symlinks and interpreter paths.
- Preloading admits only server-resolved model/runtime/workflow dependencies. It does not cache biological inputs, user results, credentials, or arbitrary browser-supplied filesystem paths.
- Workflow/source revision and model/runtime identity are explicit. New releases do not change the meaning of existing receipts or retained results.
- Cache status and preload progress persist on the target. They do not imply GPU/model readiness or scientific success.
- No unrequested cache eviction, instance destruction, price filter, GPU choice or disk allocation is introduced.

### Vast template instructions

Official references:

- https://docs.vast.ai/guides/templates/advanced-setup
- https://docs.vast.ai/guides/templates/template-settings
- https://docs.vast.ai/guides/templates/creating-templates

Vast documents a `PROVISIONING_SCRIPT` URL for startup installation/model downloads, and persistent installation under `/workspace/` for its documented base-image flow. For BMS's VM runtime, the [Linux VM instructions](https://docs.vast.ai/linux-virtual-machines) explicitly support the template **On-start script** and require an interpreter shebang, such as `#!/bin/bash`. Use that documented guest startup hook; do not assume setting the base-image `PROVISIONING_SCRIPT` variable alone executes it inside `vastai/kvm`.

Vast's [volume documentation](https://docs.vast.ai/guides/instances/storage/volumes) explicitly says provider volumes are currently Docker-only, not supported for VM instances. The worker cache is therefore instance-local reuse, not a promise of cache persistence across new rentals. Template configuration tests do not replace a real guest startup check.

The supported BMS scientific runtime requires real VM capabilities for its Apptainer stack. Switching to an ordinary Vast Docker container or adding an ignored privileged flag is not an acceptable cache integration shortcut.

Keep credentials out of image layers, source, template environment settings and startup URLs. Fetch executable provisioning assets by immutable identity and verify them. Template updates are configuration changes; renting, recreating, stopping or destroying an instance is a separate provider lifecycle action. A saved template does not retroactively provision an already-running worker.

### Implemented provisioning and preload interface

`services.remote_execution.cache_template.render_vm_cache_template_fields` renders the actual guest bootstrap from `tools/bms_artifact_cache.py`. It compresses the exact helper and splits its base64 representation between a literal On-start prefix and nonsecret template environment values. The loader verifies the complete helper SHA256 before atomic installation and initializes `<worker_root>/cache/artifacts/v1`. There is no mutable download URL, credential dependency, implicit model download, or scientific command in this bootstrap.

Vast's [edit-template documentation](https://docs.vast.ai/api-reference/templates/edit-template) specifies the 4048-character On-start limit. The live API also enforces a 4096-character `env` limit. Check both **after** preserving the existing environment. VM environment variables may be exported or written into `/etc/environment`; the bootstrap reads only its three closed keys without executing that file. A configuration save must preserve existing VM image, filters, SSH options, resource choices and README. Despite documented partial-update behavior, an observed update without `readme` cleared its hash: include the preserved/updated README and verify its receipt explicitly.

The cache helper is the same protocol consumed by `cache.stage_cached_bundle` and `cache.prewarm_cache`. Regular runtime leaves and the immutable source archive use shared content-addressed storage. Warm probes/materializations are batched; destination-dependent support Python remains launch-prepared and explicitly excluded from preload readiness. Inputs, results and credentials are never cache objects. The cache has no automatic eviction or cross-rental persistence promise.

`POST /api/execution-targets/{id}/preload` accepts the typed body `{ "job_id": "<saved-job-id>" }`. The saved job is a recipe for exact current-source settings; the call does not submit or alter scientific work. Progress is persisted in the execution-target response. A historical recipe bound to a different source is rejected rather than silently upgraded. This is not yet a general model-catalog picker independent of saved jobs.

## Known workflow coverage boundary

The self-contained downstream FrustraMPNN implementation covers structure and complex prediction. The reviewed conformational-mapping, protein-design, experimental Boltz-CP, antibody-de-novo, local-redesign and PPIFLOW-generator workflow entrypoints still contain controller callback/child-orchestration dependencies. Remote dependency compilation now rejects those entrypoints explicitly before staging rather than claiming full remote parity. Molecular-dynamics controller orchestration and other unreviewed workflow families are **not accepted as remote-complete**. These coverage gaps require actual workflow conversion and acceptance; fail-closed admission is not their implementation.

## Visible lifecycle

Keep provider presence, attachment, cache preparation, scientific execution and result retrieval separate:

1. Provider inventory establishes currently owned/running identity and endpoint.
2. Attach verifies SSH host identity, runner and actual runtime prerequisites.
3. Optional explicit preload populates verified selected dependencies and reports the actual artifact/phase; it does not claim the scientific GPU lease.
4. Launch binds the complete validated request to immutable code/runtime/input authority and the chosen worker.
5. Running status exposes lightweight actual remote stage information without pulling scientific artifacts.
6. Remote success releases only its own compute lease, retains outputs and offers explicit pull.
7. Pull progress, failure and explicit retry survive reload. Successful local ingestion preserves native data, requested/effective settings, candidate/source lineage and normal result viewing.

GPU queue projections must include the exact remote-results waiting subset where a pull action is promised, without turning unrelated awaiting-input jobs into queued GPU work. Transfer progress must recognize the backend's actual returning state, not a fabricated frontend-only state.

## Retention and failure boundaries

Remote retention lasts only while the provider's storage remains available. Disconnecting BMS is not stopping provider billing. Destroying an instance or losing its disk can destroy unpulled results. Do not imply indefinite retention or automatically destroy/clean unpulled output. Cache data is reproducible; scientific output is not interchangeable with disposable cache data.

Retrying a failed transfer must bind the original attempt, source and manifest and leave other GPU leases untouched. A transport interruption is not permission to rerun science. Unknown provider ownership or endpoint drift blocks SSH and retrieval until identity is established.

## Verification and release gates

Before claiming closure, verify:

- original saved settings and all required stages survive remote compilation;
- exact workflow DAG, candidate grouping and required failure behavior;
- cache hit/miss, corrupt/partial files, concurrent population, path containment and template-to-launch consumption;
- provider/endpoint/attempt fences and absence of local fallback;
- actual backend-shaped queue/prompt/returning data through mounted UI tests;
- no result-payload transfer before explicit action;
- canonical native result ingestion and failure diagnostic handling;
- current development scientific-contract compatibility;
- exact deployed source/API/frontend/runner/runtime identity and an authorized real remote workflow.

Offline helper commands and miniature fixture weights prove software behavior, not model execution. Template rendering or storage readback proves configuration, not guest provisioning. A real remote run, rendered prompt, and authorized result retrieval remain distinct acceptance evidence.
