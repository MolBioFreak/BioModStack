# Stats Toolkit Add-on Repo Split Implementation Plan

> **Historical / superseded:** This extraction plan predates the P1 standalone ownership boundary. It is retained only as design history and is not active architecture or implementation guidance.

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task after Christian approves the phase slice.

**Goal:** Break the BioModStack Stats Toolkit out of the core BioModStack runtime into an explicitly optional add-on with its own repo, image(s), tests, release cadence, and lifecycle controls.

**Architecture:** Keep the core BioModStack runtime focused on dashboard, job/workflow orchestration, results, BMS DB service status, and add-on discovery/proxying. Move qPCR, chromatography/HPLC/Empower, DOE/statistics, assay QC, heavy R/Python statistical engines, and Stats Toolkit UI into a separate add-on service/repo that consumes the shared BMS DB service but does not own Postgres. Core must degrade cleanly when the add-on is absent.

**Tech Stack:** FastAPI/Python, React/Vite/TypeScript, Docker/Compose, Postgres via BMS DB service, SQLAlchemy/asyncpg, Plotly, optional R/CRAN/Bioconductor, GitHub Actions/CI, GHCR or local Docker registry for add-on images.

---

## Non-negotiable boundaries

- **BMS core keeps:** dashboard shell, Job Launcher, workflow adapter contract, Results/Data Viewer, Mol Bio Toolkit, NGS surfaces that are workflow/core-owned, BMS DB service status/control, Host Agent, BioXP link/handler control surface.
- **Stats Toolkit add-on owns:** qPCR imports/analytics, chromatography/HPLC/Empower imports/analytics, DOE/statistics workbench, assay QC/SST/capability charts, Plotly assay visualizations, R/Python statistics dependencies, add-on-specific reports.
- **BMS DB service remains separate:** do not put Postgres inside the Stats Toolkit container. The add-on connects to `bms_analytical_data` and later can read/write `bms_core_runtime` only through explicit contracts.
- **No fake/demo outputs:** add-on offline means clear degraded state, not placeholder assay results.
- **No workflow-model junk drawer:** RFantibody/FAMPNN/Caliby/PPIFlow/Boltz/Protenix/AntiBERTy/ThermoMPNN/OpenMM/FrustraMPNN remain workflow/model-native, not Stats Toolkit.
- **Install/removal must be boring:** core boots when Stats Toolkit is absent; installing the add-on should add nav/status surfaces without mutating core code at runtime.

---

## Current source surfaces to split

### Backend/API candidates

- Move or wrap later:
  - `platform/api/routers/assay_analytics.py`
  - `platform/api/services/assay_analytical_store.py`
  - `platform/api/services/assay_chrom_persistence.py`
  - `platform/api/services/assay_tool_integrations.py`
  - Stats-tool lifecycle currently in `platform/api/services/stats_tools.py`
- Keep in core or duplicate as a small shared client:
  - `platform/api/services/db_service.py` stays core-owned.
  - `platform/api/routers/system.py` keeps add-on status/proxy endpoints only.
  - BMS DB service naming stays **BMS DB service**, not analytical Postgres.

### Frontend candidates

- Move or convert into an add-on UI package:
  - `platform/frontend/src/components/AssayAnalytics.tsx`
  - `platform/frontend/src/components/qpcr/**`
  - `platform/frontend/src/components/hplc/**`
  - `platform/frontend/src/components/statistics/**`
  - `platform/frontend/src/components/assay/**`
  - `platform/frontend/src/components/StatsToolsControlPanel.tsx`
- Keep in core:
  - `platform/frontend/src/components/DbServiceControlPanel.tsx`
  - Top-bar BMS DB service controls.
  - Add-on registry/menu tile.

### Runtime/build candidates

- Split away:
  - `stats-tools-runtime` stage from `docker/api.Dockerfile`
  - `bms-stats-tools` service from `compose.core-runtime.yml`
  - `bms stats-tools ...` lifecycle internals once host-agent add-on control exists.
- Keep in core short-term as compatibility shims until the add-on repo is live.

---

## Recommended repo / branch topology

**Default decision:** make Stats Toolkit a standalone GitHub repo, backed by a short-lived BioModStack core branch for the extraction work. Do **not** make core import Stats Toolkit from a submodule at runtime; core should discover/control it through an add-on manifest, HTTP health/capability endpoints, and Docker image/compose metadata.

- Core repo branch: `stats-toolkit/externalize-v0` in `MolBioFreak/BioModStack`.
  - Purpose: remove bundled Stats Toolkit implementation from core, add the add-on registry/proxy/shims, and keep BMS core bootable when the add-on is absent.
- New add-on repo: `MolBioFreak/BioModStack-StatsToolkit`.
  - Purpose: own qPCR/chromatography/HPLC/Empower/DOE/statistics implementation, UI, tests, Docker image(s), R/Python package build, and release cadence.
- Optional developer convenience subrepo: `addons/stats-toolkit` in the core repo **only after** the new repo exists.
  - Use this only as a checkout convenience via submodule/subtree if desired. It must not be required for normal core runtime boot.

### Acceptable extraction modes

1. **Preferred / cleanest: new standalone repo + core branch.**
   - Core branch removes/migrates code.
   - New repo owns implementation.
   - Runtime coupling is HTTP + manifest + Docker image.
   - Best fit for Christian's “code in a different repo” goal.

2. **Fast interim: separate branch in the existing BioModStack repo.**
   - Branch name: `stats-toolkit/extract-v0`.
   - Useful if we want a quick PR showing the moved tree before creating the GitHub repo.
   - Not the final state, because main/core still carries the history and branch remains part of the monorepo.

3. **Subrepo/submodule after repo creation.**
   - Submodule command shape: `git submodule add https://github.com/MolBioFreak/BioModStack-StatsToolkit.git addons/stats-toolkit`.
   - Subtree command shape: `git subtree add --prefix=addons/stats-toolkit https://github.com/MolBioFreak/BioModStack-StatsToolkit.git main --squash`.
   - Recommendation: avoid submodule/subtree for v1 runtime. If used, treat it as developer ergonomics only; build/deploy must still work from published images and manifest.

### Bootstrap command spec

Do this from a clean worktree after the DB-service naming tranche is committed or intentionally carried forward. Do not extract from the dirty active checkout unless the exact changed files are staged into a dedicated extraction commit first.

```bash
# Core extraction branch, isolated from unrelated dirty work.
cd /home/dalab/biomodstack/biomodstack
git fetch origin
git worktree add -b stats-toolkit/externalize-v0 /tmp/bms-core-stats-externalize origin/test

# New repo bootstrap, no-history mode. Fastest and least surprising.
mkdir -p /tmp/BioModStack-StatsToolkit
cd /tmp/BioModStack-StatsToolkit
git init -b main
mkdir -p src/bms_stats_toolkit/{routers,services} ui/src docker tests/api tests/ui

# Copy only Stats Toolkit implementation surfaces from the isolated core worktree.
cp /tmp/bms-core-stats-externalize/platform/api/routers/assay_analytics.py src/bms_stats_toolkit/routers/assay_analytics.py
cp /tmp/bms-core-stats-externalize/platform/api/services/assay_analytical_store.py src/bms_stats_toolkit/services/analytical_store.py
cp /tmp/bms-core-stats-externalize/platform/api/services/assay_chrom_persistence.py src/bms_stats_toolkit/services/chrom_persistence.py
cp /tmp/bms-core-stats-externalize/platform/api/services/assay_tool_integrations.py src/bms_stats_toolkit/services/tool_integrations.py
cp /tmp/bms-core-stats-externalize/docker/install_assay_r_packages.R docker/install_assay_r_packages.R
cp -R /tmp/bms-core-stats-externalize/platform/frontend/src/components/AssayAnalytics.tsx ui/src/AssayAnalytics.tsx
cp -R /tmp/bms-core-stats-externalize/platform/frontend/src/components/qpcr ui/src/qpcr
cp -R /tmp/bms-core-stats-externalize/platform/frontend/src/components/hplc ui/src/hplc
cp -R /tmp/bms-core-stats-externalize/platform/frontend/src/components/statistics ui/src/statistics
cp -R /tmp/bms-core-stats-externalize/platform/frontend/src/components/assay ui/src/assay
cp -R /tmp/bms-core-stats-externalize/platform/frontend/src/components/StatsToolsControlPanel.tsx ui/src/StatsToolkitControlPanel.tsx

# After repo scaffolding/tests are added:
git add .
git commit -m "feat: bootstrap BioModStack Stats Toolkit add-on"
gh repo create MolBioFreak/BioModStack-StatsToolkit --private --source=. --remote=origin --push
```

If history preservation matters, use a disposable clone plus `git filter-repo` for the listed paths, then restructure with `git mv`. Do not run `git filter-repo` in the active core checkout.

```bash
git clone --no-hardlinks /home/dalab/biomodstack/biomodstack /tmp/BioModStack-StatsToolkit-history
cd /tmp/BioModStack-StatsToolkit-history
git filter-repo --force \
  --path platform/api/routers/assay_analytics.py \
  --path platform/api/services/assay_analytical_store.py \
  --path platform/api/services/assay_chrom_persistence.py \
  --path platform/api/services/assay_tool_integrations.py \
  --path docker/install_assay_r_packages.R \
  --path platform/frontend/src/components/AssayAnalytics.tsx \
  --path platform/frontend/src/components/StatsToolsControlPanel.tsx \
  --path platform/frontend/src/components/qpcr/ \
  --path platform/frontend/src/components/hplc/ \
  --path platform/frontend/src/components/statistics/ \
  --path platform/frontend/src/components/assay/
# Then git mv into src/bms_stats_toolkit and ui/src, add repo scaffolding, test, and push to the new GitHub repo.
```

---

## Target external repo shape

Repository name target: `BioModStack-StatsToolkit` / `biomodstack-stats-toolkit` under `MolBioFreak`.

```text
biomodstack-stats-toolkit/
  README.md
  pyproject.toml
  package.json
  compose.stats-toolkit.yml
  docker/
    stats-toolkit-api.Dockerfile
    stats-toolkit-web.Dockerfile       # only if UI is served separately
  src/bms_stats_toolkit/
    __init__.py
    app.py                             # FastAPI add-on API
    config.py
    db.py                              # BMS DB service client/settings
    routers/
      assay_analytics.py
      health.py
    services/
      analytical_store.py
      chrom_persistence.py
      tool_integrations.py
  ui/
    package.json
    src/
      AssayAnalytics.tsx
      qpcr/
      hplc/
      statistics/
      assay/
      StatsToolkitControlPanel.tsx
  tests/
    api/
    ui/
  addon-manifest.json
```

`addon-manifest.json` should be machine-readable and boring:

```json
{
  "id": "bms-stats-toolkit",
  "display_name": "Stats Toolkit",
  "version": "0.1.0",
  "api_base_path": "/api/addons/stats-toolkit",
  "health_path": "/health",
  "ui": {
    "mode": "remote-url",
    "route": "/assay",
    "entry_url": "http://127.0.0.1:18180/bms-stats-toolkit/"
  },
  "requires": {
    "db_service": true,
    "logical_databases": ["bms_analytical_data"]
  },
  "capabilities": [
    "qpcr",
    "chromatography",
    "hplc_empower",
    "doe_statistics",
    "assay_qc"
  ]
}
```

---

## Phase 0: Finish DB service naming cleanup

**Objective:** Ensure the shared database runtime is not called analytical Postgres in operator-facing code before moving Stats Toolkit out.

**Files:**
- Modify: `compose.core-runtime.yml`
- Modify: `.env.core-runtime.example`
- Modify: `scripts/bms_host_agent.py`
- Modify: `platform/api/services/db_service.py`
- Modify: `platform/frontend/src/components/DbServiceControlPanel.tsx`
- Test: `platform/api/tests/test_core_runtime_scaffold.py`

**Acceptance gates:**
- Source compose service is `bms-db` and container is `biomodstack-db`.
- UI panel shows `Service: BMS DB service`, not `bms-analytical-postgres`.
- Legacy live containers can still be discovered by fallback names during the migration window.
- No live stateful DB recreate unless a backup/migration step is explicit.

---

## Phase 1: Add core add-on registry and status contract

**Objective:** Make Stats Toolkit appear as an optional add-on, not a baked-in core feature.

**Files:**
- Create: `platform/api/services/addon_registry.py`
- Modify: `platform/api/routers/system.py`
- Modify: `scripts/bms_host_agent.py`
- Modify: `platform/frontend/src/components/Layout.tsx`
- Create: `platform/frontend/src/components/AddonsMenu.tsx`
- Test: `platform/api/tests/test_addon_registry.py`
- Test: `platform/frontend/tests/addonsMenuContract.test.tsx`

**Backend contract:**

```json
{
  "id": "bms-stats-toolkit",
  "display_name": "Stats Toolkit",
  "state": "missing|running|degraded|stopped",
  "runtime_available": false,
  "control_mode": "host-agent|unavailable",
  "install_mode": "external-repo",
  "entry_url": null,
  "offline_message": "stats_toolkit_addon_missing — install/start Stats Toolkit add-on"
}
```

**Verification:**

```bash
cd /home/dalab/biomodstack/biomodstack
pytest platform/api/tests/test_addon_registry.py -q
pnpm --dir platform/frontend test -- addonsMenuContract.test.tsx
```

Expected: add-on missing returns degraded/offline state without breaking `/api/health` or dashboard load.

---

## Phase 2: Proxy current in-repo Stats Toolkit through the add-on contract

**Objective:** Convert core callers to talk to `bms-stats-toolkit` through a single proxy/client seam while the implementation still lives in this repo.

**Files:**
- Create: `platform/api/services/stats_toolkit_client.py`
- Modify: `platform/api/routers/assay_analytics.py`
- Modify: `platform/api/routers/system.py`
- Modify: `platform/frontend/src/api/client.ts`
- Modify: `platform/frontend/src/components/AssayAnalytics.tsx`
- Test: `platform/api/tests/test_stats_toolkit_proxy.py`
- Test: `platform/frontend/tests/statsToolkitAddonContract.test.tsx`

**Rules:**
- No page/component should hard-require local Stats Toolkit routes without checking add-on availability.
- Existing `/api/assay-analytics/*` can remain as a compatibility alias for one release, but should call the client/proxy seam.
- Offline panels must point to the add-on control/status surface, not bury fixes in body text.

**Verification:**

```bash
pytest platform/api/tests/test_stats_toolkit_proxy.py platform/api/tests/test_assay_analytics_router.py -q
pnpm --dir platform/frontend test -- statsToolkitAddonContract.test.tsx
```

---

## Phase 3: Create the external Stats Toolkit repo

**Objective:** Move implementation code into the new repo with its own build/test/release path.

### Phase 3A: scaffold the repo

**Files in new repo:**
- Create: `README.md`
- Create: `pyproject.toml`
- Create: `package.json` or `pnpm-workspace.yaml` if the UI is kept in `ui/`
- Create: `src/bms_stats_toolkit/app.py`
- Create: `src/bms_stats_toolkit/config.py`
- Create: `src/bms_stats_toolkit/db.py`
- Create: `src/bms_stats_toolkit/routers/health.py`
- Create: `src/bms_stats_toolkit/routers/assay_analytics.py`
- Create: `src/bms_stats_toolkit/services/analytical_store.py`
- Create: `src/bms_stats_toolkit/services/chrom_persistence.py`
- Create: `src/bms_stats_toolkit/services/tool_integrations.py`
- Create: `ui/src/**` from current qPCR/HPLC/statistics components
- Create: `compose.stats-toolkit.yml`
- Create: `docker/stats-toolkit-api.Dockerfile`
- Create: `docker/stats-toolkit-web.Dockerfile` only if UI is served separately from the API image
- Create: `docker/install_assay_r_packages.R`
- Create: `addon-manifest.json`
- Create: `.github/workflows/ci.yml`

### Phase 3B: source-to-destination map

Backend move:
- `platform/api/routers/assay_analytics.py` → `src/bms_stats_toolkit/routers/assay_analytics.py`
- `platform/api/services/assay_analytical_store.py` → `src/bms_stats_toolkit/services/analytical_store.py`
- `platform/api/services/assay_chrom_persistence.py` → `src/bms_stats_toolkit/services/chrom_persistence.py`
- `platform/api/services/assay_tool_integrations.py` → `src/bms_stats_toolkit/services/tool_integrations.py`
- `docker/install_assay_r_packages.R` → `docker/install_assay_r_packages.R`

Frontend move:
- `platform/frontend/src/components/AssayAnalytics.tsx` → `ui/src/AssayAnalytics.tsx`
- `platform/frontend/src/components/qpcr/**` → `ui/src/qpcr/**`
- `platform/frontend/src/components/hplc/**` → `ui/src/hplc/**`
- `platform/frontend/src/components/statistics/**` → `ui/src/statistics/**`
- `platform/frontend/src/components/assay/**` → `ui/src/assay/**`
- `platform/frontend/src/components/StatsToolsControlPanel.tsx` → `ui/src/StatsToolkitControlPanel.tsx`

Core replacement/shim:
- Replace core `AssayAnalytics` route with an add-on launcher/offline panel.
- Keep `/assay` as the BMS navigation contract, but have it open/embed the add-on entry URL when installed.
- Keep `/api/assay-analytics/*` in core as a reverse-proxy compatibility alias for one release.
- Keep `/api/system/stats-tools` as a compatibility alias, but back it with the generic add-on registry.
- Remove R/statistics package installation from core `docker/api.Dockerfile` once the add-on repo image is green.

### Phase 3C: package/dependency split

Stats Toolkit `pyproject.toml` owns assay-specific deps:
- `mocca2`
- `pydoe3`
- `statsmodels`
- `scikit-learn`
- `bofire`
- `qpcr`
- `qslib`
- `openpyxl`
- `xlrd`
- `asyncpg` / `sqlalchemy` for BMS DB service access

Core `platform/api/pyproject.toml` should later drop deps that are assay-only after route proxy tests pass. Do **not** remove broad deps such as `numpy`/`pandas` blindly; they may still be used by core workflow/result surfaces.

Stats Toolkit `ui/package.json` owns assay UI deps:
- `plotly.js-dist-min`
- `react-plotly.js`
- React/Vite/Tailwind stack needed for the add-on shell

### Phase 3D: public add-on HTTP contract

Stats Toolkit service should expose:
- `GET /health`
- `GET /capabilities`
- `GET /packages`
- `GET /api/assay-analytics/tools`
- `GET /api/assay-analytics/capabilities`
- `GET /api/assay-analytics/analytical-store/status`
- Existing qPCR/chromatography/statistics routes under `/api/assay-analytics/...` to minimize frontend churn.

Core should call the add-on through a single client seam:
- `BMS_STATS_TOOLKIT_URL=http://127.0.0.1:18181`
- `BMS_STATS_TOOLKIT_MODE=auto|required|disabled`, default `auto`
- `BMS_STATS_TOOLKIT_TIMEOUT_SECONDS=30`
- `BMS_STATS_TOOLKIT_ENTRY_URL=http://127.0.0.1:18180/bms-stats-toolkit/`

### Import strategy

- Preserve route payloads and schemas first; do not redesign analytics math while moving code.
- Keep `bms_analytical_data` as the DB name.
- Read DB URL from `BMS_ANALYTICAL_DATABASE_URL` or `BMS_DB_SERVICE_URL`-derived config.
- Add deterministic sample fixtures only for parser/unit tests, never as operator-facing demo results.
- Keep BMS DB service external. The add-on connects to it; it does not launch or own Postgres.

**Verification:**

```bash
pytest tests/api -q
pnpm --dir ui test
pnpm --dir ui build
docker build -f docker/stats-toolkit-api.Dockerfile .
```

Expected: add-on API `/health`, `/capabilities`, and qPCR/chrom/stat route tests pass independently of BMS core.

---

## Phase 4: Core consumes external add-on

**Objective:** Remove core's direct implementation dependency and make Stats Toolkit start/stop/status target the external repo/image.

**Core files:**
- Modify: `compose.core-runtime.yml` to remove the in-repo `bms-stats-tools` service.
- Modify: `scripts/bms` so `bms stats-toolkit ...` delegates to Host Agent add-on service or prints install/start commands.
- Modify: `scripts/bms_host_agent.py` to use add-on manifest/config instead of hardcoded core compose service.
- Modify: `platform/api/routers/assay_analytics.py` into compatibility proxy or remove after route deprecation window.
- Modify: `platform/frontend/src/components/Layout.tsx` to show Stats Toolkit only when add-on is installed/enabled.
- Test: `platform/api/tests/test_core_runtime_scaffold.py`
- Test: `platform/api/tests/test_stats_toolkit_proxy.py`

**Core compose after this phase:**
- `bms-api`
- `bms-web`
- `bms-db`
- `bms-cpu-power` only until folded into Host Agent
- no `bms-stats-tools` in core compose

**External compose after this phase:**
- `bms-stats-toolkit-api`
- optionally `bms-stats-toolkit-web`
- no Postgres server; connects to BMS DB service

---

## Phase 5: Frontend add-on loading and navigation cleanup

**Objective:** Make Stats Toolkit feel separate in the UI without breaking the core dashboard.

**Options, in recommended order:**

1. **Remote URL first:** core nav tile opens `http://127.0.0.1:18180/bms-stats-toolkit/` in the same browser tab or embedded route. Lowest coupling.
2. **Built asset mount:** add-on publishes static assets that core nginx can mount under `/addons/stats-toolkit/`. More integrated, still separable.
3. **Module federation/dynamic import:** only if remote URL feels too disconnected. Higher build complexity; defer.

**Acceptance gates:**
- Core dashboard loads with add-on absent.
- Stats Toolkit nav/menu shows `Install`/`Start`/`Open` based on add-on state.
- No `Stats Toolkit` top-level nav link if add-on is disabled and not installed, unless an Add-ons menu is explicitly enabled.
- No API 500s from missing add-on.

---

## Phase 6: Release, migration, and rollback

**Objective:** Make the split operationally safe.

**Required docs:**
- New repo `README.md`: install/start/stop/logs/status.
- Core docs: `docs/plans/` note that Stats Toolkit is an add-on.
- Migration doc: how to point add-on at existing BMS DB service.
- Rollback doc: how to re-enable compatibility proxy or old in-repo service for one release if needed.

**Required release artifacts:**
- `ghcr.io/<owner>/bms-stats-toolkit-api:<version>`
- optional `ghcr.io/<owner>/bms-stats-toolkit-web:<version>`
- `addon-manifest.json` with version/capabilities
- checksummed example compose file

**Rollback boundary:**
- If add-on API fails: core dashboard remains up, BMS DB service remains up, workflows/results remain up.
- If add-on UI fails: core Add-ons panel reports add-on degraded and offers logs.
- If DB service is unavailable: add-on reports DB degraded; it does not synthesize assay output.

---

## Final definition of done

- Core BioModStack repo has no Stats Toolkit implementation code except compatibility proxy/client, launcher/offline UI, and add-on registry.
- Core branch `stats-toolkit/externalize-v0` proves dashboard/API boot with Stats Toolkit absent.
- New GitHub repo `MolBioFreak/BioModStack-StatsToolkit` exists and owns the implementation source, CI, Dockerfile(s), compose file, manifest, README, and tests.
- Core compose does not define `bms-stats-tools` after the external repo image/compose path is green.
- New Stats Toolkit repo builds and tests independently.
- New Stats Toolkit container connects to **BMS DB service** and never embeds Postgres.
- Dashboard and core API work when Stats Toolkit is absent.
- Add-on installed/running state is visible from the core UI and Host Agent.
- `/assay` and `/api/assay-analytics/*` compatibility behavior is covered by tests before bundled code is removed.
- If a submodule/subtree is added under `addons/stats-toolkit`, core runtime still works when that checkout is missing; it is a developer convenience, not a runtime dependency.
