# Boltz API Native Controls and CLI Update Watcher Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make the remote Boltz API launcher expose only provider-native prediction inputs, preserve exact estimate/submission semantics, and surface a safe non-automatic CLI update status in the Structure Prediction workflow.

**Architecture:** The provider adapter is the authority for a versioned, explicitly allow-listed capability contract. The FastAPI status endpoint returns provider readiness plus a cached, read-only CLI update assessment. The React Boltz API panel renders only provider-native controls from that contract; it never reuses local Boltz-2 GPU/diffusion controls. No binary update endpoint is included in this tranche: the button reports status and links to the reviewed release source, while an operator-controlled deployment path remains the only updater.

**Tech Stack:** FastAPI/Pydantic/asyncio, existing `boltz-api` static CLI, React/TypeScript/TanStack Query, pytest, Vitest.

---

## Product contract

### Supported remote fields

The current BMS adapter sends provider-native entities plus `num_samples`. For provider-default MSA, protein entities omit `msa`; for disabled MSA, each protein entity includes `"msa": {"type":"empty"}`:

```json
{
  "entities": [
    {"type":"protein", "value":"SEQUENCE", "chain_ids":["A"], "msa":{"type":"empty"}},
    {"type":"dna", "value":"ATCG", "chain_ids":["B"]}
  ],
  "num_samples": 1
}
```

`num_samples` remains bounded to `1..10`. Remote request and component schemas are strict: unknown fields and all local-only controls are rejected with HTTP 422 by both estimate and submit rather than silently discarded.

### Explicit exclusions

Do not display or submit local-only Boltz controls under `boltz_api`: diffusion/sampling steps, recycling steps, potentials, denoiser chunking, local GPU pinning/parallelism, OOM retry, and conditioning. These controls must never be silently ignored.

### Templates

The provider CLI help documents up to four CIF/PDB templates, but this codebase has no verified provider payload syntax or secure template ownership/upload contract. Do **not** expose templates in this tranche. The capability contract marks them `unavailable_pending_schema_verification` rather than inventing JSON.

### CLI update semantics

The update watcher is capability-gated. Before an official provider-owned static-CLI release manifest URL/schema is captured, tested, and pinned, it returns `unavailable_pending_official_feed_verification` with the locally observed version only. It never treats the PyPI `boltz-api` SDK as a CLI update candidate. The status path remains informational and never downloads, replaces, or restarts anything.

## Task 1: Provider capability and update-status domain contract

**Objective:** Add typed provider-native capability/update status models without changing submission semantics.

**Files:**
- Modify: `platform/api/services/boltz_api_jobs.py`
- Modify: `platform/api/schemas.py`
- Modify: `platform/api/tests/test_boltz_api_jobs.py`

**Step 1: Write failing tests**

Cover:
- installed semantic CLI version is read from `--version` without exposing secret environment data;
- until an official provider-owned static CLI manifest contract is independently captured and pinned, update state is `unavailable_pending_official_feed_verification` and makes no network request;
- static CLI vs Python SDK source cannot be conflated;
- provider capability response lists native supported fields and explicit unsupported local fields;
- strict request/component schemas reject unknown fields and every listed local-only setting with HTTP 422 on both estimate and submit.

**Step 2: Implement minimal domain functions**

Add an immutable capability contract for `boltz-2.1`. Add a read-only local CLI-version probe with a minimal non-secret environment. Do not add a release-feed fetcher until an official HTTPS manifest URL, bounded schema, and test fixture are captured. The current update response must be truthful `unavailable_pending_official_feed_verification`; it performs no network, filesystem, package-manager, job, configuration, or service mutation.

**Step 3: Run focused tests**

```bash
cd platform/api
PYTHONPATH="$PWD" .venv/bin/python -m pytest --noconftest -q tests/test_boltz_api_jobs.py
```

**Step 4: Commit later with the integrated tranche.**

## Task 2: Typed provider status route

**Objective:** Return native capabilities and update state from the existing status endpoint without changing authentication behavior.

**Files:**
- Modify: `platform/api/routers/boltz_api_jobs.py`
- Modify: `platform/api/schemas.py`
- Modify: `platform/api/tests/test_boltz_api_jobs.py`

**Step 1: Write route tests**

Assert a status response includes: `available`, CLI/auth state, model, `capabilities`, and `cli_update`. Assert update failures still return HTTP 200 with an unavailable update state and do not claim an update exists.

**Step 2: Implement**

Call the read-only capability/update status service from the existing status route. Do not make the route mutate filesystem, provider configuration, jobs, or secrets.

**Step 3: Verify**

Run the route/provider focused test set and a direct local status request.

## Task 3: Frontend typed status and native-settings panel

**Objective:** Render the remote controls from provider capabilities and display the update-status button only for `boltz_api`.

**Files:**
- Modify: `platform/frontend/src/lib/api.ts`
- Modify: `platform/frontend/src/components/StructurePredictionTemplate.tsx`
- Modify: `platform/frontend/tests/structureBoltzApiImportContract.test.ts`
- Modify: `platform/frontend/tests/structurePredictionUiState.test.ts`

**Step 1: Write failing contract tests**

Assert:
- when `boltz_api` is selected, the UI labels the panel `Boltz API–native settings`;
- native MSA and `1..10` samples remain visible;
- provider update state renders `Check for updates`, `Update available: X`, `Update requires review`, or `Update status unavailable`;
- unsupported local fields are absent from the remote panel and absent from estimate/submit payloads;
- update button has no mutation endpoint and cannot trigger download/restart.

**Step 2: Implement**

Extend frontend status types, refresh provider status on predictor selection, and render a compact update detail disclosure with installed/latest version, source, check time, compatibility, and release URL when supplied. Show a precise template notice only if the backend reports `unavailable_pending_schema_verification`.

**Step 3: Verify**

```bash
cd platform/frontend
pnpm vitest run tests/structureBoltzApiImportContract.test.ts tests/structurePredictionUiState.test.ts
pnpm exec tsc -b
pnpm build
```

## Task 4: Exact-provider payload and live acceptance

**Objective:** Prove remote complex payload integrity and update watcher safety.

**Files:**
- Modify: focused tests only if gaps remain.

**Step 1: Back-end acceptance**

Verify provider input preserves protein + DNA + ion/CCD ligand + custom SMILES, disabled MSA syntax, and samples. Ensure cost fingerprint changes whenever any supported remote setting changes.

**Step 2: Browser acceptance**

Against the deployed test runtime, navigate:

`Job Launcher → Structure Prediction → Boltz API`

Verify the provider-configured state, native controls, component controls, update status button/disclosure, and zero browser console errors. Do not submit a paid prediction without explicit operator cost approval.

**Step 3: Review and delivery gates**

- Exact diff review
- Focused backend/frontend tests
- Python compilation, TypeScript compilation, production build, `git diff --check`
- Independent review of source and test coverage
- Patch-forward commit/push; deploy only from the reviewed current `origin/test` descendant.
