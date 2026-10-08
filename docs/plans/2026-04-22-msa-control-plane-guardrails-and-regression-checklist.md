# BioModStack MSA Control-Plane Guardrails and Regression Checklist

> **For Hermes:** Treat this file as a pre-merge gate for any change that touches structure-launch MSA semantics, global MSA server settings, child-batch propagation, or local-MSA wrapper behavior. Do not call an MSA/control-plane change done just because one script accepts the new flag; preserve the full UI → API → workflow → runtime contract.

**Goal:** Preserve truthful, stable MSA behavior across the structure launcher, top-bar MSA server controls, API/job orchestration, Nextflow launch plumbing, and runtime wrappers while the local-MSA control-plane tranche is hardened.

**Architecture:** BioModStack currently has two distinct MSA-facing control surfaces: (1) per-job structure-launch controls in `platform/frontend/src/components/StructurePredictionTemplate.tsx`, and (2) global/persisted MSA server controls in `platform/frontend/src/components/Layout.tsx` backed by `/api/msa/server/*`. Those surfaces converge only later in backend launch plumbing (`platform/api/routers/jobs.py`, `platform/api/services/nextflow.py`) and runtime wrappers (`scripts/batch_msa.py`, `scripts/run_local_msa.py`). Safe changes must preserve the boundary semantics at each layer instead of assuming one layer is the source of truth for all MSA behavior.

**Tech Stack:** React/TypeScript launcher UI, FastAPI routers/services, Python orchestration, Nextflow command building, local MSA wrappers (`batch_msa.py`, `run_local_msa.py`), pytest, and the existing frontend `.test.ts` node-test surface.

---

## Current repo-grounded truth

These are the facts this checklist is built around.

- `StructurePredictionTemplate.tsx` currently emits the per-job MSA payload in two places:
  - `currentTemplateParams` at lines `586-695`
  - submit-time payload assembly at lines `976-1069`
- Those two blocks are almost the same, but not perfectly identical:
  - both include `msa_preset`, `msa_taxon_list`, `msa_evalue`, `msa_min_seq_id`, `msa_min_coverage`, `msa_min_depth_warning`, `msa_min_depth_fail`, `msa_cache_only`, `msa_allow_empty_fallback`, `msa_provider`, `colabfold_api_host`, `colabfold_api_min_interval`, `colabfold_api_poll_interval`, `msa_use_expand`, `msa_use_env`, and `msa_num_iterations`
  - submit-time payload also includes `msa_force_refresh` (`985`) while template-save intentionally does not
  - that divergence is currently consistent with the one-shot reset at `1071-1074`
- `StructurePredictionTemplate.tsx` already contains two important UI guardrails:
  - multi-job `colabfold_api` is coerced back to `local` at `831-835`
  - cache-info lookups clear `msa_cache_only` when no cache exists or cache lookup fails at `849-877`
- The top-bar/global MSA server menu in `Layout.tsx` is a separate contract surface:
  - `fetchState()` calls `/api/msa/server/status`, `/api/msa/server/settings`, and `/api/gpu/gpus` at `970-998`
  - settings are saved via `PUT /api/msa/server/settings` at `1013-1028`
  - manual start uses `POST /api/msa/server/start` with `include_envdb` and `gpu_id` at `1031-1048`
  - manual stop uses `POST /api/msa/server/stop` with `gpu_id` at `1050-1066`
- `platform/api/routers/msa.py` exposes the live server routes:
  - `/server/status` at `241-247`
  - `/server/settings` at `297-303`
  - `/server/start` at `320-326`
  - `/server/stop` at `377-383`
- `platform/api/services/msa_server.py` already treats server status as a requested-contract match, not just "something is running":
  - `requested_contract` is built at `697-703`
  - `matching_aliases` / `matching_servers` logic begins at `716`
- `platform/api/tests/test_msa_server.py` already covers that contract surface:
  - `_is_matching_gpuserver_process()` rejects empty cmdline
  - `server_status()` filters by `requested_contract` and `matching_aliases`
- `platform/api/services/nextflow.py` still couples the global MSA pin to per-job launches:
  - if `params["msa_preferred_gpus"]` is absent, persisted `pinned_gpu_id` from MSA server settings is injected at `2398-2434`
  - Protenix local-MSA launches currently special-case skipping that persisted pin at `2399-2417`
- `platform/api/services/nextflow.py` has two different MSA forwarding surfaces:
  - `launch_msa_batch_job(...)` builds the `batch_msa.py` CLI at `1213-1419`
  - general Nextflow param mapping forwards MSA keys at `2581-2607`
- There is a real propagation mismatch inside `launch_msa_batch_job(...)`:
  - it reads `params['msa_force_refresh']` at `1255`
  - `jobs.py` stores `msa_cache_only` into the MSA batch job record at `4839-4860`
  - `scripts/batch_msa.py` fully supports `cache_only` in its parser and runtime (`303`, `400-441`, `726-727`, `793-794`)
  - but `launch_msa_batch_job(...)` never appends `--cache-only` to the `batch_msa.py` command at `1286-1347`
- `scripts/batch_msa.py` is still a behavior island even though it is explicitly marked deprecated:
  - parser flags live at `715-774`
  - function signature lives at `295-324`
  - the per-sequence ColabFold-compatible fallback path threads many flags via `_run_colabfold_per_sequence(...)` at `443-475`
  - the true-batch-fast path has separate logic starting at `507`
- `platform/api/routers/jobs.py` currently forwards only a subset of MSA semantics through antibody iteration helpers:
  - `BOLTZ_ITERATION_FORWARD_KEYS` includes 16 MSA-ish keys (`msa_preset`, GPU/runtime/server knobs, and ColabFold polling knobs) at `90-124`
  - it does not include launcher-emitted keys such as `msa_provider`, `msa_cache_only`, `msa_force_refresh`, `msa_allow_empty_fallback`, `msa_evalue`, `msa_min_seq_id`, `msa_min_coverage`, `msa_min_depth_warning`, `msa_min_depth_fail`, `msa_taxon_list`, `msa_use_expand`, `msa_use_env`, or `msa_num_iterations`
  - the Protenix-specific iteration copy lists around `3084-3108` and `3374-3398` show the same omission pattern
- Frontend coverage is materially incomplete for these surfaces:
  - `platform/frontend/tests/` currently has 15 `.test.ts` files
  - none mention `currentTemplateParams`
  - none mention `pinned_gpu_id`
  - none mention `/api/msa/server`
  - `jobSubmissionTemplateState.test.ts` contains zero `msa_` tokens
  - `platform/frontend/package.json` currently has no `test` script at all
- Existing targeted coverage is useful but partial:
  - `platform/frontend/tests/reorchestrateStructureSettings.test.ts` covers `msa_provider`, `msa_preset`, and `msa_allow_empty_fallback`
  - `platform/frontend/tests/structurePredictionUiState.test.ts` covers truthful predictor/launcher semantics and confirms Boltz-CP still exposes MSA controls
  - `platform/api/tests/test_boltz_cp_experimental.py` asserts many explicit MSA CLI forwards in `build_nextflow_command(...)`
  - `scripts/test_run_local_msa.py` and `scripts/test_prepare_protenix_msa.py` cover key runtime/package behavior
  - there is no checked-in `scripts/test_batch_msa.py` source file right now

---

## Non-negotiable guardrails

1. Keep the two MSA surfaces distinct.
   - `StructurePredictionTemplate.tsx` controls per-job MSA behavior.
   - `Layout.tsx` `MSAServerSettingsMenu` controls persisted/manual gpuserver state.
   - Do not silently repurpose one as the other.

2. Treat `pinned_gpu_id` as runtime-significant.
   - The top-bar GPU pin is not cosmetic metadata.
   - `nextflow.py` can inject it into `msa_preferred_gpus` by default.
   - Any change to pin semantics must be coordinated across UI, `/api/msa/server/settings`, and launch plumbing.

3. Do not conflate `include_envdb_on_start` with `msa_use_env`.
   - `include_envdb_on_start` is a persisted/manual server-start preference.
   - `msa_use_env` is a per-job search override.
   - They may both affect EnvDB, but they are not interchangeable and must not be merged into one flag by accident.

4. Preserve the dual guard for unsupported multi-job `colabfold_api` behavior.
   - The UI already coerces multi-job `colabfold_api` to `local`.
   - Submit-time/backend validation must continue to reject unsupported cases even if the UI regresses.
   - Never rely on frontend coercion alone.

5. Preserve cache-only truthfulness.
   - `msa_cache_only` means "do not generate uncached MSAs."
   - If any batch/control-plane layer drops that flag, the system lies.
   - Cache-info UX that clears invalid `msa_cache_only` selections is part of the contract, not just convenience.

6. Keep template-save and submit payloads aligned except for explicitly documented one-shot actions.
   - Current intentional exception: `msa_force_refresh` is submit-only and is reset after submit.
   - Any new divergence between `currentTemplateParams` and submit payload construction must be justified in code comments and tests.

7. Do not assume backend defaults override user-visible launcher defaults.
   - The structure launcher default is currently `msa_preset='fast'`.
   - Runtime wrapper defaults elsewhere do not change what the user sees unless the launcher state/defaults also change.

8. Any new MSA parameter must be traced across every active propagation seam.
   - launcher save/load
   - launcher submit
   - re-orchestrate surfaces if relevant
   - job creation / child-batch / iteration propagation
   - Nextflow argv mapping
   - wrapper CLI parsing
   - runtime behavior
   - tests

9. `batch_msa.py` cannot be treated as a transparent alias for `run_local_msa.py`.
   - It has its own parser, its own fast-path behavior, and its own fallback behavior.
   - Every new flag or semantic change must either be implemented in both paths or explicitly rejected in one path with tests.

10. Do not break `msa_provider=colabfold_api` single-job support while hardening local MSA.
   - The unsupported boundary is multi-job/server-backed behavior expansion, not single-job ColabFold API launches.

---

## Required paired edits by change type

| If you change… | You must also inspect/update… | Minimum proof required |
| --- | --- | --- |
| Per-job MSA fields in the structure launcher | `platform/frontend/src/components/StructurePredictionTemplate.tsx` in both `currentTemplateParams` and submit-time payload assembly; `platform/frontend/src/components/dashboard/reorchestrateStructureSettings.ts` if the field should survive retry/re-orchestration | frontend test covering save/load payload shape and submit payload shape |
| Global MSA server settings/status fields | `platform/frontend/src/components/Layout.tsx`, `platform/api/routers/msa.py`, `platform/api/services/msa_server.py`, and any Nextflow/default-injection logic that consumes persisted settings | API test for server status/settings + frontend menu test |
| MSA param propagation through child batches / iteration relaunches | `platform/api/routers/jobs.py` MSA batch-job param block (`4835-4860`), `BOLTZ_ITERATION_FORWARD_KEYS`, Protenix iteration copy lists (`3084-3108`, `3374-3398`), and downstream Nextflow forwarding | pytest proving the param survives from source job to child launch |
| Nextflow-facing MSA CLI flags | `platform/api/services/nextflow.py` both in direct Nextflow param mapping (`2581-2607`) and `launch_msa_batch_job(...)` CLI construction (`1254-1347`) | pytest asserting exact CLI tokens |
| Runtime wrapper CLI flags | `scripts/batch_msa.py` parser (`715-774`), `run_batch_msa(...)` signature (`295-324`), `_run_colabfold_per_sequence(...)`, and `scripts/run_local_msa.py` if parity is expected | focused wrapper tests, not just API tests |
| GPU pin / server-default semantics | `Layout.tsx`, `/api/msa/server/settings`, `services/msa_server.py`, `services/nextflow.py` injected defaults, plus any Protenix special-casing | API test for injected defaults and documented exception paths |
| User-visible MSA defaults | launcher state defaults in `StructurePredictionTemplate.tsx`, backend normalization, docs, and tests that pin the selected default | frontend assertion plus backend assertion |

---

## Minimum regression suite that should exist before merging more MSA/control-plane changes

### 1. Frontend launcher payload contract test

Create a focused test file for the structure launcher MSA payload contract.

Target behaviors:
- `currentTemplateParams` includes the stable persisted MSA fields:
  - `msa_preset`
  - `msa_taxon_list`
  - `msa_evalue`
  - `msa_min_seq_id`
  - `msa_min_coverage`
  - `msa_min_depth_warning`
  - `msa_min_depth_fail`
  - `msa_cache_only`
  - `msa_allow_empty_fallback`
  - `msa_provider`
  - `colabfold_api_host`
  - `colabfold_api_min_interval`
  - `colabfold_api_poll_interval`
  - `msa_use_expand`
  - `msa_use_env`
  - `msa_num_iterations`
- submit payload includes the same stable fields plus the intentional one-shot `msa_force_refresh`
- multi-job `colabfold_api` coercion to `local` still happens
- cache-info no-hit/error still clears `msa_cache_only`

Suggested file:
- `platform/frontend/tests/structurePredictionTemplateMsaContract.test.ts`

### 2. Frontend top-bar MSA server menu test

Add a focused test for `MSAServerSettingsMenu`.

Target behaviors:
- fetches `/api/msa/server/status`, `/api/msa/server/settings`, and `/api/gpu/gpus`
- renders `pinned_gpu_id`, `include_envdb_on_start`, and current running summary truthfully
- `saveSettings(...)` sends only the intended patch to `PUT /api/msa/server/settings`
- manual start sends `include_envdb` and `gpu_id` to `POST /api/msa/server/start`
- manual stop sends `gpu_id` to `POST /api/msa/server/stop`

Suggested file:
- `platform/frontend/tests/msaServerSettingsMenu.test.ts`

### 3. API/server contract tests

Extend `platform/api/tests/test_msa_server.py` so server status/settings remain a real contract, not a loose smoke test.

Target behaviors:
- `requested_contract` remains explicit and complete
- `matching_aliases` only includes servers matching the requested contract
- persisted settings round-trip correctly for `include_envdb_on_start` and `pinned_gpu_id`
- route defaults still match the intended runtime defaults

### 4. MSA batch CLI propagation tests

Add a focused API-side regression around `launch_msa_batch_job(...)` or extract a helper so the command can be tested directly.

Must assert exact CLI forwarding for at least:
- `--cache-only`
- `--force_refresh`
- `--num-iterations`
- `--taxon-list`
- `--gpu-server-db-load-mode`
- any other MSA flags currently surfaced by the launcher

Suggested file:
- extend `platform/api/tests/test_structure_prediction_batch.py`

### 5. Child/iteration propagation tests

Add pytest coverage that proves MSA fields survive iteration/relaunch helpers in `routers/jobs.py`.

At minimum cover:
- `msa_provider`
- `msa_cache_only`
- `msa_force_refresh`
- `msa_allow_empty_fallback`
- `msa_evalue`
- `msa_min_seq_id`
- `msa_min_coverage`
- `msa_min_depth_warning`
- `msa_min_depth_fail`
- `msa_taxon_list`
- `msa_use_expand`
- `msa_use_env`
- `msa_num_iterations`

Suggested file:
- extend `platform/api/tests/test_jobs_boltz_param_propagation.py` or add a dedicated iteration-propagation test module

### 6. Wrapper-level `batch_msa.py` tests

Add the missing `scripts/test_batch_msa.py` source file.

Target behaviors:
- parser accepts and threads `--cache-only`, `--force_refresh`, `--num-iterations`, `--taxon-list`, and gpuserver flags
- `cache_only=True` fails uncached sequences without generating MSAs
- advanced overrides trigger the ColabFold-compatible per-sequence path
- fast preset without advanced overrides stays on the true batch path
- persisted MSA GPU pin is only used where intended

### 7. Keep the existing strong tests alive

The following tests already provide useful signal and must keep passing after changes:
- `scripts/test_run_local_msa.py`
- `scripts/test_prepare_protenix_msa.py`
- `platform/api/tests/test_msa_server.py`
- `platform/api/tests/test_boltz_cp_experimental.py`
- `platform/frontend/tests/reorchestrateStructureSettings.test.ts`
- `platform/frontend/tests/structurePredictionUiState.test.ts`

---

## Immediate gaps this checklist is meant to prevent from recurring

1. `msa_cache_only` is currently stored on the MSA batch job record but not forwarded by `launch_msa_batch_job(...)` to `batch_msa.py`.
2. Antibody iteration forwarding lists are missing many launcher-emitted MSA semantics.
3. Frontend MSA control surfaces are under-tested, and the repo does not currently expose a first-class frontend `test` script.
4. `batch_msa.py` is still deprecated-but-live, which makes it easy for behavior to drift unless it gets its own direct tests.

---

## Recommended focused verification commands

Backend/runtime-focused checks that should stay cheap and repeatable:

```bash
uv run --directory platform/api python -m pytest \
  tests/test_msa_server.py \
  tests/test_structure_prediction_batch.py \
  tests/test_jobs_boltz_param_propagation.py \
  tests/test_boltz_cp_experimental.py -q

python -m pytest \
  scripts/test_run_local_msa.py \
  scripts/test_prepare_protenix_msa.py \
  scripts/test_batch_msa.py -q
```

Frontend gap note:
- `platform/frontend/package.json` currently has no `test` script, so adding one is part of making this checklist enforceable.
- Do not treat existing `.test.ts` files as sufficient protection until they are wired into a repeatable command.

---

## Definition of done for future MSA/control-plane edits

Do not mark a change complete until all of the following are true:

1. The changed semantics are reflected at every affected boundary, not only in one script.
2. Per-job launcher behavior and global MSA server behavior remain clearly distinct.
3. Any intentional divergence between template-save and submit payloads is explicit and tested.
4. Child-batch / relaunch / iteration helpers preserve the intended MSA fields.
5. Exact CLI tokens are asserted for both Nextflow-facing MSA forwarding and wrapper-facing batch MSA forwarding.
6. The focused regression suite above passes.
7. Docs/plans are updated if the user-visible contract changed.
