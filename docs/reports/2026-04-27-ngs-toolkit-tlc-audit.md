# NGS Toolkit TLC Audit — 2026-04-27

> **Historical / superseded:** This report records an earlier runtime audit and is not active implementation guidance.

## Scope

Source-grounded audit of BioModStack's NGS Toolkit/Nanopore stack for expanded use. This pass intentionally focused on the NGS entrypoint and did not continue the earlier Mol Bio alignment incident detour.

Reviewed surfaces:
- Frontend: `platform/frontend/src/components/NGSToolkit.tsx`, `platform/frontend/src/components/NanoporeTemplate.tsx`, `platform/frontend/src/lib/api.ts`, `platform/frontend/src/App.tsx`, `platform/frontend/tests/nanoporeTemplateContract.test.ts`
- API: `platform/api/routers/jobs.py`, `platform/api/routers/sequence_qc.py`, `platform/api/services/sequence_qc_manifest.py`, `platform/api/config/models/nanopore.yaml`, `platform/api/tests/test_nanopore_nextflow.py`, `platform/api/tests/test_sequence_qc_manifest.py`
- Workflows/scripts: `ngs.nf`, `workflows/nanopore_methylation.nf`, `modules/dorado.nf`, `nextflow.config`, `scripts/build_fastq_igv_tracks.py`, `scripts/build_fastq_support_tables.py`, `scripts/build_sequence_qc_manifest.py`
- Docs: `docs/Lab_Automation_MolBio_and_Sequencing.md`
- Live runtime: `http://127.0.0.1:8000`, `http://127.0.0.1:5173/bms/ngs`, containers `biomodstack-api` and `biomodstack-web`

## Verdict

The NGS stack is no longer just a toy surface: there is a real `ngs.nf` entrypoint, Nanopore model registry entry, FASTQ read-QC artifact wiring, a typed `qc_manifest.json` parser/API, and a large read-inspection UI. The main risks before expanded use are operational hardening and contraction of sprawl: the UI/workflow/API modules are very large, older runs do not yet have sequence-QC manifests, the NGS UI still does not consume the typed manifest endpoint, and operator-facing state needs clearer unavailable-vs-failed semantics. I patched the safe quick wins found during the pass and left the deeper work as staged follow-up below.

## Quick fixes applied in this pass

1. NGS job polling no longer pulls the full job table every 5 seconds.
   - Added `model_id` and `mode` filters to `GET /api/jobs` in `platform/api/routers/jobs.py`.
   - Added matching params to `fetchJobs` in `platform/frontend/src/lib/api.ts`.
   - Scoped `NGSToolkit` runs query to `fetchJobs({ include_children: true, model_id: 'nanopore', limit: 100 })`.
   - Live impact: previous NGS page polling had been returning about 4.5 MB from `/api/jobs?include_children=true`; after the fix, `/api/jobs?include_children=true&model_id=nanopore&limit=100` returns 153,641 bytes / 80 Nanopore jobs.

2. Nanopore submit success now navigates to a live route.
   - `platform/frontend/src/components/NanoporeTemplate.tsx` now routes successful submissions to `/jobs/{job_id}` instead of stale `/results/{job_id}`.
   - Contract test added in `platform/frontend/tests/nanoporeTemplateContract.test.ts`.

3. NGS modkit table label now matches the actual preview count.
   - `platform/frontend/src/components/NGSToolkit.tsx` displayed `first 100 rows` while rendering `.slice(0, 20)`.
   - Label corrected to `first 20 rows` and covered by frontend contract test.

4. Canonical NGS docs now point at the real NGS entrypoint.
   - `docs/Lab_Automation_MolBio_and_Sequencing.md` now names `ngs.nf` and `workflows/nanopore_methylation.nf` instead of implying the NGS workflow logic lives in `main.nf`.

## Evidence and important findings

### 1. Live NGS runs polling was too broad for expanded use

Evidence:
- `NGSToolkit.tsx` is the NGS route surface and polls jobs at `platform/frontend/src/components/NGSToolkit.tsx:2220-2224`.
- Before the patch, the query used `fetchJobs({ include_children: true })`, and live web logs showed repeated `GET /api/jobs?include_children=true` responses around 4,509,647 bytes from `/bms/ngs`.
- After the patch, the live browser and logs show `GET /api/jobs?include_children=true&model_id=nanopore&limit=100` returning 153,641 bytes.
- Direct API smoke returned 80 jobs, total 80, and no non-Nanopore `model_id` rows.

Why it matters:
- Expanded NGS use means more jobs and more artifacts. Polling the full platform job table every 5 seconds from a specialized NGS page is an avoidable scaling footgun.

Status:
- Fixed and deployed to the local BMS containers.

### 2. Sequence-QC manifest contract exists, but live historical runs do not have manifests yet

Evidence:
- Manifest generator exists at `scripts/build_sequence_qc_manifest.py` and emits `artifact_schema_version`, reference metadata, consensus metadata, artifacts, and interpretation (`scripts/build_sequence_qc_manifest.py:107-155`).
- Parser/API exists at `platform/api/services/sequence_qc_manifest.py` and `platform/api/routers/sequence_qc.py`.
- Live OpenAPI exposes:
  - `/api/sequence-qc/jobs/{job_id}/manifest`
  - `/api/sequence-qc/manifest`
- Live result roots checked:
  - `/mnt/BioModStack/results`: 0 `qc_manifest.json`
  - `/home/dalab/biomodstack/biomodstack/bms_results`: 0 `qc_manifest.json`
- A representative completed Nanopore job (`76c19c29-fbb2-40c8-a430-5a34af5381a9`) returns 404 from `/api/sequence-qc/jobs/{job_id}/manifest`, not 500.

Why it matters:
- The contract is the right direction, but expanded use needs the UI/report layer to understand both new runs with typed manifests and old runs without them.

Recommended next step:
- Add an NGS UI manifest panel/loader with explicit `manifest unavailable for older run` state.
- Add reingest/backfill or rerun guidance only where real artifacts exist; do not synthesize fake manifests for old jobs.

### 3. The NGS frontend is too monolithic for sustained expansion

Evidence:
- `platform/frontend/src/components/NGSToolkit.tsx`: 5,077 lines / 270,103 bytes.
- `platform/frontend/src/components/NanoporeTemplate.tsx`: 2,166 lines / 122,453 bytes.

Why it matters:
- The NGS UI currently mixes run list polling, artifact discovery, IGV state, methylation parsing, multimer/dimer views, logs, and launch UI in very large components. That makes read-QC expansion risky: a small artifact-contract change can unintentionally break launch defaults, IGV loading, or run table behavior.

Recommended next step:
- Split without changing behavior:
  - `components/ngs/NgsRunsTable.tsx`
  - `components/ngs/NgsRunInspector.tsx`
  - `components/ngs/useNgsJobs.ts`
  - `components/ngs/useSequenceQcManifest.ts`
  - `components/ngs/igv/*`
  - `components/ngs/reports/*`
- Keep `/bms/ngs` as the route and regression-test the extracted pieces before adding new capability.

### 4. The workflow/script layer has real FASTQ QC wiring, but artifact semantics need one more hardening pass

Evidence:
- `nextflow.config` sets `fastq_minimap2_preset = 'map-ont'`, avoiding the bundled minimap2 2.24 `lr:hq` incompatibility.
- `workflows/nanopore_methylation.nf` references `params.fastq_minimap2_preset ?: 'map-ont'` and validates unsupported presets.
- `modules/dorado.nf` contains FASTQ QC/process wiring and emits artifacts including per-base support, BAM/BAI, and `qc_manifest.json` per the current tests.
- `scripts/build_sequence_qc_manifest.py` does not yet carry explicit `missing_reason` / `unavailable_reason` fields for absent optional artifacts; it omits artifacts whose args are not passed.

Why it matters:
- Omitting absent artifacts is safe and better than fake paths, but operators need explicit unavailable reasons when comparing POD5/BAM/FASTQ modes, especially for modified bases and consensus/IGV artifacts.

Recommended next step:
- Extend manifest schema v1 conservatively, or introduce v2, with explicit artifact states:
  - `present`
  - `not_requested`
  - `not_applicable_to_input_mode`
  - `failed`
  - `missing_after_workflow`
- Keep required artifacts strict; make optional artifacts truthful instead of synthetic.

### 5. Live NGS route is healthy after patch/redeploy

Evidence:
- `http://127.0.0.1:8000/api/health` returned 200.
- `http://127.0.0.1:5173/bms/ngs` loaded the NGS Toolkit.
- Browser console on `/bms/ngs` had 0 console messages and 0 JS errors after loading Launch and Runs views.
- Web/API logs after smoke showed 200 responses for NGS route polling and stage calls, with no `Traceback`, `ERROR`, or `500` lines in the inspected recent tails.
- Deployed bundle contains the expected markers: `model_id:'nanopore'`, `limit:100`, `modkit summary (first 20 rows)`, and no stale `/results/${response.data.job_id}` literal.

## Validation run

Commands executed successfully:

```bash
# Frontend NGS/Nanopore contract tests
cd /home/dalab/biomodstack/biomodstack/platform/frontend
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/nanoporeTemplateContract.test.js
# 6 passed

# API/workflow contract tests
cd /home/dalab/biomodstack/biomodstack
uv run --directory platform/api python -m pytest tests/test_nanopore_nextflow.py -q
# 7 passed
uv run --directory platform/api python -m pytest tests/test_sequence_qc_manifest.py -q
# 12 passed
python -m pytest scripts/test_build_fastq_igv_tracks.py scripts/test_build_fastq_support_tables.py scripts/test_build_sequence_qc_manifest.py -q
# 5 passed

# Nextflow FASTQ preview; no execution of heavy tools
nextflow run ngs.nf -preview -offline -profile nanopore_methylation,workstation_ryzen7960x \
  --reference_fasta <tmp>/ref.fasta \
  --out_dir <tmp>/out \
  --rfd_mode nanopore_methylation \
  --run_modkit false \
  --run_fastq_qc true \
  --fastq_path <tmp>/reads.fastq \
  --fastq_minimap2_preset map-ont
# exit 0; planned FastqAlign and FastqPlasmidQC
```

Deployment/smoke:

```bash
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-api bms-web
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml up -d --no-deps bms-api bms-web
# biomodstack-api healthy; biomodstack-web healthy
```

## Recommended remediation order

### Phase 0 — Done in this pass
- Scope NGS run polling.
- Fix stale success navigation.
- Fix stale modkit row-count label.
- Fix canonical NGS doc entrypoint pointer.
- Deploy and live-smoke API/UI.

### Phase 1 — Manifest-first UI hardening
- Add `fetchSequenceQcManifest(jobId)` and `fetchSequenceQcManifestByPath(path)` in `platform/frontend/src/lib/api.ts`.
- Add `useSequenceQcManifest(selectedJobId)` hook with explicit states: loading, available, unavailable-old-run, malformed, forbidden.
- Render a small manifest contract panel in NGS inspector before adding new charts.
- Add tests for old-run 404 and malformed-manifest display.

### Phase 2 — Artifact state semantics
- Extend `scripts/build_sequence_qc_manifest.py` and `platform/api/services/sequence_qc_manifest.py` to carry explicit missing/unavailable reasons.
- Add fixtures for FASTQ-only, BAM-only, POD5+modkit, and reference-copy fallback consensus.
- Preserve rule: missing optional artifacts are truthful absences, not fake paths.

### Phase 3 — Component decomposition
- Extract run list, run inspector, manifest loader, IGV loader, methylation report, and multimer/dimer panels from the 5k-line `NGSToolkit.tsx`.
- Keep behavior identical first; only then expand.

### Phase 4 — Expanded read-QC deliverables
- Add bounded per-base support API/report surface.
- Add variant candidate summaries only after per-base support fixtures are green.
- Add amino-acid/CDS consequences only when CDS parsing/translation is implemented and tested.
- Keep AB1/chromatogram/virtual-gel claims out unless they are actually generated.

## Dirty-tree caution

The BioModStack worktree already contains many unrelated modified/untracked files from other active work. This audit touched only the NGS/docs/API/frontend surfaces listed in the quick-fix section, but final staging/commit should use explicit paths rather than `git add .`.
