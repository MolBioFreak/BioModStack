# BMS container/module split status and warning RCA

Date: 2026-05-09
Repo: `/home/dalab/biomodstack/biomodstack`
Scope: Phase-1 core-runtime split validation, BMS DB service, Stats Toolkit control surface, and non-blocking warnings from targeted backend/frontend validation.

## Executive verdict

For the Phase-1 split that was requested, the core runtime is still functional:

- `bms-api` is running and healthy.
- `bms-web` is running and healthy.
- `bms-analytical-postgres` is running and healthy as the **BMS DB service**.
- `bms-stats-tools` is running and healthy as the optional Stats Toolkit service.
- API control endpoints report the DB service and stats-tools service as available.
- Analytical store status confirms the assay/statistics database is PostgreSQL and separate from the protein/workflow DB.
- Targeted backend tests passed: `82 passed, 9 warnings in 5.33s`.
- Frontend contract/type/build checks passed, including production build.
- Docker compose config validation passed.

Important precision: this confirms the intended Phase-1 decomposition and control-plane/workflow compatibility checks. It does **not** mean every scientific domain has been microservice-split. Workflow-native GPU/model executors remain Nextflow/container-native by design.

## Follow-up cleanup status

The smaller warning items outside the large-chunk/manual-chunking work have now been addressed:

- Pydantic schema models use Pydantic v2 `ConfigDict` instead of nested `class Config` blocks.
- `AntibodyDenovoTemplate.tsx` no longer mixes static and dynamic imports of `pdbUtils`.
- Missing optional `nvidia-smi` metadata discovery is logged at info level instead of warning level.
- The known PDBe Molstar vendor `eval` warning is suppressed only for the pinned vendor bundle; other Rollup warnings and the large-chunk warning remain visible.
- Tool-terminal loopback refusal now has an explicit reporting rule so it is not mislabeled as BMS downtime.

RCA-4, large frontend chunks/manual chunking, is intentionally left open for separate discussion.

## Runtime evidence captured

Command bundle captured to `/tmp/bms-rca/live-split-health.log`:

- `docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml ps ...`
- `docker inspect biomodstack-api biomodstack-web biomodstack-analytical-postgres biomodstack-stats-tools ...`
- `./scripts/bms db-service status`
- in-container FastAPI `TestClient` probes for:
  - `/api/health`
  - `/api/system/db-service`
  - `/api/assay-analytics/analytical-store/status`
  - `/api/system/stats-tools`

Observed service states:

- `biomodstack-api`: `running`, `healthy`
- `biomodstack-web`: `running`, `healthy`
- `biomodstack-analytical-postgres`: `running`, `healthy`
- `biomodstack-stats-tools`: `running`, `healthy`

Observed DB service summary:

- `service_id`: `bms-db-service`
- `display_name`: `BMS DB service`
- `component`: `db-service`
- `state`: `running`
- `health`: `healthy`
- `optional_at_boot`: `true`
- `runtime_available`: `true`
- `control_mode`: `docker-direct-transitional`

Observed analytical-store summary:

- `database_kind`: `postgresql`
- `database_name`: `bms_analytical_data`
- `separate_from_protein_workflow_db`: `true`

## Validation evidence captured

Backend targeted suite captured to `/tmp/bms-rca/backend-targeted.log`:

```text
82 passed, 9 warnings in 5.33s
```

Frontend targeted/build suite captured to `/tmp/bms-rca/frontend-targeted-build.log`:

- `npx tsc -p tsconfig.tests.json`: pass
- node contract tests: `3` pass
- `npx tsc -b --pretty false`: pass
- `npm run build`: pass

## RCA-1: Pydantic V2 deprecation warnings

### Symptom

Pytest emits 9 warnings of this class:

```text
PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead.
Deprecated in Pydantic V2.0 to be removed in V3.0.
```

Files/classes observed:

- `platform/api/schemas.py`
  - `JobCreate`
  - `DesignResponse`
- `platform/api/routers/assay_analytics.py`
  - `DeltaCqRequest`
- `platform/api/routers/designs.py`
  - `DesignResponse`
- `platform/api/routers/molbio_ops.py`
  - `MutationSchema`
  - `NucleotideSequenceResponse`
- `platform/api/routers/nucleotide_sequences.py`
  - `NucleotideSequenceResponse`
- `platform/api/routers/user_sequences.py`
  - `UserSequenceResponse`
- `platform/api/routers/user_templates.py`
  - `UserTemplateResponse`

### Root cause

The codebase runs on Pydantic v2 but still has Pydantic v1-style nested `class Config:` blocks in these models.

Examples:

- `class Config: json_schema_extra = {...}`
- `class Config: from_attributes = True`
- `class Config: populate_by_name = True`

Pydantic v2 still supports these for compatibility, but warns that they will be removed in Pydantic v3.

### Impact

- Current runtime impact: none observed.
- Current test impact: warnings only; targeted backend suite passed.
- Future impact: these models will break or need changes before a Pydantic v3 upgrade.

### Fix path

Replace nested Config classes with v2-style `model_config = ConfigDict(...)`, importing `ConfigDict` from `pydantic`.

Example:

```python
from pydantic import BaseModel, ConfigDict

class SomeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

For schema examples:

```python
model_config = ConfigDict(json_schema_extra={...})
```

For alias population:

```python
model_config = ConfigDict(populate_by_name=True)
```

### Priority

Medium maintenance cleanup. Not a Phase-1 blocker.

## RCA-2: Vite `eval` warning in PDBe Molstar bundle

### Symptom

Production build emits:

```text
pdbe-molstar-component.js (...): Use of eval ... is strongly discouraged as it poses security risks and may cause issues with minification.
```

### Root cause

The warning comes from the third-party package bundle:

- dependency: `pdbe-molstar@3.3.0`
- imported via `platform/frontend/src/lib/molstar-loader.ts`
- aliased in `platform/frontend/vite.config.ts` through `pdbe-molstar-stable`

The current Vite config intentionally pins the frontend to the stable 3.3.0 alias because newer PDBe/Molstar versions previously caused local Chromium renderer crashes on structure views.

### Impact

- Current build impact: warning only; build succeeds.
- Security posture: this is a real CSP/security smell because bundled vendor code uses eval-like behavior, but it is not BMS-authored application code.
- Runtime impact: no failure observed in this validation pass.
- Product tradeoff: replacing/upgrading Molstar must be tested carefully because the current pin is deliberate stability protection.

### Fix path

Options, safest first:

1. Keep current pin and document/accept warning until Molstar re-upgrade can be tested.
2. Test a newer `pdbe-molstar` release or alternate Molstar integration in Electron/browser/core-runtime.
3. If upgrade is stable, remove the alias pin and re-run structure-view smoke tests.
4. If strict CSP is required, Molstar may need to be isolated behind a separate viewer route/frame or replaced with a bundle that does not require eval.

### Priority

Medium security/build-hardening cleanup. Not a Phase-1 functionality blocker.

## RCA-3: Vite mixed static/dynamic import warning for `pdbUtils.ts`

### Symptom

Production build emits:

```text
pdbUtils.ts is dynamically imported by AntibodyDenovoTemplate.tsx ... but also statically imported by AntibodyDenovoTemplate.tsx, BoltzGenTemplate.tsx, MutagenesisTemplate.tsx, ProteinLocalRedesignTemplate.tsx, StructurePredictionTemplate.tsx, dynamic import will not move module into another chunk.
```

### Root cause

`platform/frontend/src/utils/pdbUtils.ts` is imported both ways:

- Static imports in multiple structure/design components.
- Dynamic imports inside `AntibodyDenovoTemplate.tsx`.

Because a statically imported module must be present in the main/static graph, Rollup cannot split the dynamic import into a separate lazy chunk. The dynamic import still works as code, but not as a chunk-splitting optimization.

### Impact

- Functional impact: none observed.
- Build impact: warning only; build succeeds.
- Performance impact: `pdbUtils.ts` remains in an already-loaded chunk instead of being deferred for the dynamic paths.

### Fix path

Pick one import strategy:

1. If `pdbUtils` is generally needed by core structure pages, remove the dynamic import calls in `AntibodyDenovoTemplate.tsx` and use the static import consistently.
2. If the goal is lazy loading, remove static imports from all eager paths and route all expensive PDB parsing through lazy helpers.
3. Add a small wrapper module if only specific heavy functions should be lazy-loaded.

### Priority

Low/medium performance cleanup. Not a Phase-1 blocker.

## RCA-4: Large frontend chunks warning

### Symptom

Production build warns that some chunks are larger than 500 kB after minification.

Observed >500 kB assets:

- `igv.esm-*.js`: ~1.42 MB minified / ~411.7 kB gzip
- `pdbe-molstar-component-*.js`: ~5.53 MB minified / ~1.57 MB gzip
- `index-*.js`: ~8.58 MB minified / ~2.44 MB gzip

### Root cause

The BMS frontend currently bundles several heavy scientific UI surfaces into the same app:

- IGV genome viewer support in `NGSToolkit.tsx` via dynamic `import('igv')`.
- PDBe Molstar structure viewer via `molstar-loader.ts` and `pdbe-molstar` component bundle.
- A very large main app chunk with many workflow templates, viewers, assay UI, and generated MolBio demo constructs.
- `vite.config.ts` currently has no `build.rollupOptions.output.manualChunks` policy and no custom `chunkSizeWarningLimit`.

### Impact

- Functional impact: none observed; build succeeds.
- Runtime impact: potential slower first-load/route-load, especially over remote Tailscale/mobile links.
- Operational impact: build output is noisy and hides more important warnings if left unmanaged.

### Fix path

1. Add manual chunking for known heavy viewer/tool families:
   - Molstar
   - IGV
   - Plotly
   - assay/statistics UI
   - generated MolBio construct data
2. Route-level lazy load large workflow templates where safe.
3. Keep generated demo construct data out of the main chunk unless explicitly requested by MolBio Toolkit.
4. Only raise `chunkSizeWarningLimit` after intentional chunking, not as the first fix.

### Priority

Medium performance cleanup. Not a Phase-1 functionality blocker.

## RCA-5: `[GPU-META] nvidia-smi not found` during in-container probe

### Symptom

In-container FastAPI `TestClient` import printed:

```text
[GPU-META] nvidia-smi not found; GPU metadata will be empty
```

### Root cause

The API import path calls the GPU metadata discovery service. Inside this validation context, `nvidia-smi` is not on the PATH available to that process/tool namespace, so the metadata service logs a warning and returns empty GPU metadata.

Source:

- `platform/api/services/gpu_metadata.py`

### Impact

- Functional impact for DB service/stats-tools split: none.
- It does not mean the physical workstation has no GPUs.
- It means this specific API/test import context cannot read NVIDIA metadata through `nvidia-smi`.

### Fix path

1. For containerized runtime, either install/expose NVIDIA tooling in the relevant image/context or use a host-agent/NVML-backed metadata source.
2. Degrade this log to info/debug when GPU metadata is optional, to avoid alarming unrelated API tests.
3. Keep hard warnings only for endpoints/actions where GPU inventory is required.

### Priority

Low for Phase 1; medium for GPU dashboard accuracy.

## RCA-6: tool-namespace `127.0.0.1` probes return connection refused

### Symptom

From this Hermes/tool terminal namespace:

```text
http://127.0.0.1:8000/api/health -> Connection refused
http://127.0.0.1:18080/bms/ -> Connection refused
```

But Docker reports the BMS containers healthy, and in-container FastAPI probes pass.

### Root cause

The terminal/tool execution namespace is not the same network namespace as the Docker host/runtime surface. `127.0.0.1` from the tool container points to the tool container itself, not necessarily the host-networked BMS services.

`compose.core-runtime.yml` also uses `network_mode: host` for `bms-api` and `bms-web`, which makes host-loopback behavior depend on which namespace the probe originates from.

### Impact

- This is a probe-context issue, not evidence that BMS API/web is down.
- For this validation, reliable checks were Docker health, in-container app probes, compose config, and browser/runtime checks when available.

### Fix path

1. Prefer Docker health/in-container probes for API verification from Hermes terminal contexts.
2. Use browser tooling for host-networked web surfaces when terminal networking is isolated.
3. When a terminal-loopback probe fails, label it explicitly as `terminal namespace probe refused`; do **not** report BMS API/web downtime unless Docker health, in-container probes, or browser-visible host-network probes also fail.
4. Long-term: add a small host-agent or explicit bridge endpoint if terminal namespace probes need to target host services reliably.

### Operator reporting rule

Treat loopback checks as probe-context-specific evidence:

- `127.0.0.1` from the Hermes terminal namespace may only test the tool container.
- Docker health/in-container FastAPI probes test the running BMS containers.
- Browser probes may test the actual host-network service even when terminal loopback is refused.

Therefore the correct failure wording is `terminal namespace cannot reach host-loopback service`, not `BMS is down`, unless the independent runtime probes also fail.

### Priority

Low operational/testing nuance. Not a Phase-1 functionality blocker.

## Bottom line

The smaller non-chunk warnings have been cleaned up or converted into precise, scoped reporting:

- Pydantic warnings: fixed with `ConfigDict`, and covered by warning-as-error pytest.
- Molstar eval: scoped suppression for the pinned third-party PDBe Molstar bundle only.
- Mixed `pdbUtils` imports: fixed by using the existing static import consistently.
- GPU metadata warning: downgraded because missing `nvidia-smi` is optional metadata in this context.
- `127.0.0.1` refused from Hermes terminal: documented as a namespace/probe issue, not product downtime by itself.

Still open by explicit deferral: RCA-4 large frontend chunks/manual chunking.

Recommended next hardening sequence:

1. Discuss and design the large-chunk/manual-chunking split for Molstar/IGV/Plotly/generated MolBio data.
2. Re-test Molstar upgrade/replacement separately before touching the current stable alias.
3. Continue longer-term host-agent/NVML work if GPU dashboard metadata needs to be reliable from containerized contexts.
