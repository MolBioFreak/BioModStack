# NGS Toolkit Runtime Harmonization Specification

> **For Hermes:** Use `subagent-driven-development` only after Christian approves a phase. This document is a source-grounded harmonization spec, not proof that the current runtime is fully executing NGS jobs.

**Goal:** Re-harmonize BioModStack's NGS/Nanopore toolkit with the current containerized core runtime, host-native workflow adapter, typed artifact-manifest direction, and the separated MolBio/NGS product surfaces.

**Architecture:** Keep the web/API/control plane in the core container, keep Nextflow/Apptainer-heavy workflow execution owned by the host workflow adapter, and make a typed sequence-QC manifest the durable evidence contract between workflows, API, NGSToolkit, and any future MolBio read-evidence bridge. Do not fabricate artifacts or mark old runs as verified if their output directories/manifests are missing.

**Tech Stack:** FastAPI, React/TypeScript, Nextflow DSL2, Apptainer/Singularity, Dorado, modkit, minimap2/samtools, IGV/IGV reports, BMS workflow adapter, BMS core-runtime Compose.

---

## 1. Current Evidence Snapshot

Reviewed checkout:

- Repo: `/home/dalab/biomodstack/biomodstack`
- Branch/head: `test`, commit `8ead3f9544e03c7b3c5b010c8c72d9983b3929fc`
- Pre-existing unrelated dirty tree:
  - `platform/frontend/src/components/StructurePredictionTemplate.tsx`
  - `platform/frontend/tests/frontfacingCopyContract.test.ts`
  - `.protenix_cache`
  - `sketches/`

Primary source surfaces reviewed:

- Runtime/container boundary:
  - `compose.core-runtime.yml`
  - `biomodstack_runtime_profile.py`
  - `platform/api/runtime_policy.py`
  - `platform/api/services/workflow_adapter.py`
  - `platform/api/services/nextflow.py`
  - `platform/api/routers/jobs.py`
  - `platform/api/routers/workflow_adapter.py`
- NGS model/workflow/scripts:
  - `platform/api/config/models/nanopore.yaml`
  - `ngs.nf`
  - `workflows/nanopore_methylation.nf`
  - `modules/dorado.nf`
  - `nextflow.config`
  - `scripts/build_sequence_qc_manifest.py`
  - `scripts/build_fastq_igv_tracks.py`
  - `scripts/stage_reporter.py`
- API/frontend/docs:
  - `platform/api/routers/sequence_qc.py`
  - `platform/api/services/sequence_qc_manifest.py`
  - `platform/frontend/src/components/NGSToolkit.tsx`
  - `platform/frontend/src/components/NanoporeTemplate.tsx`
  - `platform/frontend/src/components/ngs/SequenceQcManifestPanel.tsx`
  - `docs/Lab_Automation_MolBio_and_Sequencing.md`
  - `docs/plans/2026-04-25-molbio-read-qc-harmonization-spec.md`

---

## 2. Executive Verdict

The NGS toolkit now has real pieces, but they were built across at least two architecture eras:

1. Earlier, a more direct/local Nextflow assumption.
2. Now, a containerized BMS core runtime with host-native workflow execution through the workflow adapter.
3. Recently, a typed sequence-QC manifest added around FASTQ plasmid QC.

The harmonization problem is not simply “fix the UI” or “add another report.” The real problem is that NGS currently has **four partially overlapping contracts**:

- Model/launch contract in `platform/api/config/models/nanopore.yaml` and `NanoporeTemplate.tsx`.
- Runtime/path contract across the core container, host adapter, Nextflow, and bind-mounted state roots.
- Stage-output/path-scraping contract in job responses and `NGSToolkit.tsx`.
- Typed artifact-manifest contract in `qc_manifest.json`, `sequence_qc.py`, and `sequence_qc_manifest.py`.

The full rework should collapse those into one explicit source of truth per concern:

- Host adapter owns launch-time executable paths and workflow preflight.
- Nextflow owns real artifact generation only.
- `sequence_qc.manifest.v1` owns reportable evidence semantics.
- API owns safe artifact lookup/download state.
- NGSToolkit owns visualization from typed view models, not regex path archaeology.
- MolBio remains construct-centric until an explicit read-evidence attachment contract is stable.

---

## 3. Non-Negotiable Constraints

1. **No fake/demo artifacts.** Missing BAM/BAI/TSV/HTML/manifest remains missing. Do not create placeholders to satisfy UI assumptions.
2. **Do not treat historical job metadata as artifact proof.** A completed DB row is not proof unless its output directory exists and manifest/artifact endpoints return usable files.
3. **Core container is not the workflow executor.** In core-runtime mode the API/control plane must delegate Nextflow/Apptainer workflows to the host adapter.
4. **Host/container paths must be explicit.** `/var/lib/biomodstack/...` and `/mnt/BioModStack/...` may refer to the same bind-mounted state but are not interchangeable in contracts.
5. **MolBio is not NGS.** MolBio should stay a SeqViz/construct editor. Read evidence is an optional attachment/bridge, not a reason to mutate `/designer` defaults.
6. **FASTQ-only runs do not have modified-base evidence.** Represent mod-base artifacts as unavailable/not-applicable, not failed.
7. **Fallback consensus/reference-copy states cannot count as verified construct success.**
8. **`map-ont` remains the safe FASTQ minimap2 default unless the bundled runtime is upgraded and validated.**
9. **NGS is utterly separate from `main.nf`.** NGS/Nanopore workflows must remain launched through `ngs.nf` only. `main.nf` must not include, dispatch, validate, or even carry compatibility branches for NGS-specific terms such as Nanopore, Dorado, modkit, FASTQ, BAM, methylation, or clone-validation. Future `main.nf` refactors for protein/design/structure must be able to proceed without touching NGS entrypoints, workflows, modules, model registry, tests, or artifact contracts.

---

## 4. Explicit Scope Boundary — NGS Must Not Be Coupled To `main.nf`

### Current evidence

- `ngs.nf` is the standalone NGS entrypoint and includes `workflows/nanopore_methylation.nf` directly.
- API command construction routes `model_id=nanopore`, `mode=methylation_analysis` to `nextflow run ngs.nf`, including resume paths.
- A source scan of tracked entrypoints shows no NGS/Nanopore/Dorado/modkit/FASTQ/BAM/reference FASTA terms in `main.nf`.

### Required scope rule

NGS isolation is an explicit part of this rework, not an incidental cleanup:

- Keep `main.nf` completely NGS-blind.
- Keep all NGS workflow selection in `ngs.nf` or future NGS-specific entrypoints under the NGS bounded context.
- Keep NGS orchestration in `workflows/ngs/` or the current `workflows/nanopore_methylation.nf` until it is moved there deliberately.
- Keep NGS model/tool processes in NGS-owned modules, eventually splitting the oversized `modules/dorado.nf` into NGS module files.
- Do not add fallback NGS compatibility branches to `main.nf` during protein/design/structure refactors.
- Do not make NGS depend on shared protein/design params such as `rfd_mode` except as a temporary compatibility alias inside `ngs.nf` only.

### Acceptance gates

- `platform/api/tests/test_nanopore_nextflow.py` must assert that `main.nf` contains no NGS/Dorado/modkit/FASTQ/BAM/methylation symbols.
- Fresh and resumed Nanopore launches must continue to invoke `nextflow run ngs.nf`, never `main.nf`.
- Refactoring `main.nf` must not require edits to `ngs.nf`, `workflows/nanopore_methylation.nf`, or NGS modules unless the task is explicitly in this NGS rework scope.

---

## 5. Harmonization Need A — Runtime Ownership and Launch Boundary

### Current evidence

- `platform/api/runtime_policy.py:50-69` blocks workflow launches in core-runtime mode unless a workflow adapter is configured; the error text says the container only owns the web/control-plane surface.
- `compose.core-runtime.yml:37-52` sets container-facing state paths such as `/var/lib/biomodstack` and configures `BMS_WORKFLOW_ADAPTER_URL`.
- `compose.core-runtime.yml:68-71` bind-mounts host `${BMS_STATE_DIR}` into the container state path.
- `platform/api/services/workflow_adapter.py:114-175` translates container-visible paths to host-visible paths before POSTing `/api/workflow-adapter/launch`.

### Problem

NGS inputs and outputs currently move through UI → container API → host adapter → Nextflow → stage reporter → API → UI. Some values are user-provided paths, some are uploaded state-managed paths, some are host runtime assets, and some are result paths. The contract does not yet classify those path kinds explicitly.

This is high risk after containerization because a path that validates inside the API container may be meaningless to the host, and a path produced by the host may be unsafe or unavailable to the API/frontend unless normalized back to a BMS-safe path.

### Required spec

Introduce a formal NGS path contract for these parameters:

- `pod5_dir`
- `bam_path`
- `fastq_path`
- `reference_fasta`
- `wf_clone_workflow_dir`
- `out_dir`
- all stage output paths

Each path must be classified as one of:

- `state_relative`: path under the BMS state root, safe to expose as `bms_results/...` or `inputs/...`.
- `container_absolute`: API/container-visible path, e.g. `/var/lib/biomodstack/...`.
- `host_absolute`: host workflow-visible path, e.g. `/mnt/BioModStack/...` or `/home/dalab/...`.
- `allowed_alias`: browse/upload alias resolved by the runtime path system.
- `workflow_asset`: host-only executable/runtime asset such as Apptainer SIFs, Dorado weights, or wf-clone assets.

Resolution ownership:

- API stores user intent and safe display paths.
- Host adapter resolves host launch paths.
- Nextflow receives only host-valid absolute paths for external inputs and runtime assets.
- API responses expose only allowed-relative artifact paths or signed/download URLs, never raw host internals as a primary contract.

### Implementation targets

- Modify: `platform/api/services/workflow_adapter.py`
  - Add explicit NGS path-class translation tests and helpers.
- Modify: `platform/api/routers/jobs.py`
  - Keep alias resolution but do not assume container-side aliases are host-valid.
- Modify: `scripts/stage_reporter.py`
  - Normalize outputs to allowed-relative paths whenever possible.
- Add tests under `platform/api/tests/` for host/container path equivalence.

### Acceptance gates

- In core-runtime mode, a FASTQ upload under container `/var/lib/biomodstack/inputs/...` launches on host as `${BMS_STATE_DIR}/inputs/...`.
- A result path written by host Nextflow under `${BMS_STATE_DIR}/bms_results/...` is returned by API/UI as safe state-relative or download URL.
- A browse alias cannot silently pass through to Nextflow unresolved.
- Stage outputs from host and container runtime variants normalize to the same public artifact IDs.

---

## 6. Harmonization Need B — Host Adapter NGS Preflight

### Current evidence

- `workflows/nanopore_methylation.nf:61-70` enforces exactly one of POD5/BAM/FASTQ.
- `workflows/nanopore_methylation.nf:81-90` requires reference FASTA for FASTQ and rejects unsupported minimap2 presets.
- `nextflow.config` carries Dorado, modkit, and wf-clone runtime assumptions.
- `modules/dorado.nf` expects Dorado containers/weights and optionally runs nested wf-clone-validation.

### Problem

Too many failures can currently occur late inside Nextflow instead of at launch. After containerization, the preflight must be host-adapter-aware, not just API-container-aware.

### Required spec

Add a host-adapter NGS preflight before invoking Nextflow for `model_id=nanopore`:

Required checks:

- Exactly one primary input exists on the host:
  - POD5 dir, BAM file, or FASTQ file.
- FASTQ requires host-readable reference FASTA.
- `fastq_minimap2_preset` is one of the runtime-validated presets.
- Apptainer/Singularity exists when any SIF-backed process will run.
- `${container_dir}/dorado.sif` exists for Dorado/modkit-backed paths.
- `${weights_root}/dorado` exists or the configured Dorado model download policy is explicit.
- `samtools`, `minimap2`, and Python dependencies needed by FASTQ QC are available inside the execution environment used by `dorado.sif`/module scripts.
- `nextflow` exists on the host adapter.
- If `run_assembly=true`, validate wf-clone prerequisites before launch:
  - local workflow dir exists or remote pull is explicitly allowed,
  - `wf_clone_singularity_cache` is writable,
  - `wf_clone_nxf_home` is writable,
  - nested Nextflow/Singularity settings are coherent.

### Implementation targets

- Create or extend: `platform/api/services/workflow_preflight.py` or equivalent.
- Modify: `platform/api/services/workflow_adapter.py` to run NGS preflight in host path space.
- Modify: `platform/api/routers/jobs.py` to surface preflight errors as launch validation failures.
- Add tests: `platform/api/tests/test_nanopore_workflow_preflight.py`.

### Acceptance gates

- A missing FASTQ file fails before Nextflow starts, with an API-visible message naming the missing path class.
- Missing `dorado.sif` fails before launch for POD5/BAM modkit-capable runs.
- FASTQ-only runs do not require POD5/Dorado basecalling prerequisites unless the module path actually needs them.
- `run_assembly=true` reports wf-clone prerequisite failures before a nested workflow starts.

---

## 7. Harmonization Need C — Model Registry Parameter Separation

### Current evidence

- `platform/api/config/models/nanopore.yaml:29-42` declares a sequence-QC contract but only for a subset of FASTQ artifacts.
- `platform/api/config/models/nanopore.yaml:43-83` puts user inputs, Dorado options, FASTQ QC options, and wf-clone nested workflow controls into one mode param list.
- `platform/api/config/models/nanopore.yaml:86-121` marks POD5/BAM/FASTQ/reference individually optional, while Nextflow enforces cross-field requirements.

### Problem

The registry mixes user-facing scientific choices, advanced analysis options, and runtime/adapter internals. That makes frontend forms, API validation, and docs drift over time.

### Required spec

Split Nanopore config into concern groups:

1. `user_inputs`
   - `pod5_dir`, `bam_path`, `fastq_path`, `reference_fasta`
2. `analysis_options`
   - `dorado_model`, `modified_bases`, `run_modkit`, `bam_force_realign`, `bam_min_mapq`
3. `fastq_qc_options`
   - `run_fastq_qc`, `expected_plasmid_size`, `min_fastq_read_length`, `fastq_minimap2_preset`, IGV report windows
4. `assembly_options`
   - `run_assembly`, `wf_clone_sample`, construct size/coverage/quality knobs
5. `runtime_internal`
   - `wf_clone_workflow_dir`, `wf_clone_source`, `wf_clone_revision`, `wf_clone_profile`, caches/homes, container dirs, weights roots

Rules:

- `run_fastq_qc` is canonical.
- `run_multimer_qc` remains compatibility-only and should not be first-class in new UI/docs except as a legacy alias.
- Cross-field validation belongs in schema/API/preflight, not only Nextflow.
- Runtime internals must be hidden by default from standard users.

### Implementation targets

- Modify: `platform/api/config/models/nanopore.yaml`.
- Modify: model config loader if it lacks grouped/hidden/internal metadata.
- Modify: `platform/frontend/src/components/NanoporeTemplate.tsx` to render grouped canonical controls.
- Add contract tests in `platform/api/tests/test_nanopore_nextflow.py` and frontend tests for default payloads.

### Acceptance gates

- FASTQ payload from `NanoporeTemplate.tsx` includes `run_fastq_qc` and no longer depends on `run_multimer_qc` except compatibility.
- API rejects POD5+BAM, POD5+FASTQ, and BAM+FASTQ before Nextflow.
- API rejects FASTQ without reference before Nextflow.
- Runtime-internal wf-clone paths do not appear in default user form.

---

## 8. Harmonization Need D — One Typed Artifact Contract

### Current evidence

- `workflows/nanopore_methylation.nf:214-239` reports many FASTQ artifacts: read lengths, summary, alignment stats, coverage, per-base support, manifest, reference copies, IGV auxiliary tracks, IGV report, logs, consensus, and indexes.
- `modules/dorado.nf:752-770` builds `qc_manifest.json` from only part of that set.
- `platform/api/config/models/nanopore.yaml:29-42` declares a smaller subset of FASTQ artifacts.
- `platform/api/routers/sequence_qc.py:27-50` exposes manifest lookup by job or path.
- `platform/api/services/sequence_qc_manifest.py:251-283` discovers manifests at `fastq_qc/qc_manifest.json`, root `qc_manifest.json`, then nested matches.
- `platform/frontend/src/components/NGSToolkit.tsx:562-616` still resolves IGV artifacts primarily by scraping `stage_outputs` paths.

### Problem

There is no single artifact registry that guarantees workflow outputs, manifest contents, model config, API responses, and frontend visualization agree. This is the core source of ongoing NGS drift.

### Required spec

Create a durable artifact contract family:

1. `sequence_qc.manifest.v1`
   - Top-level manifest version, producer, job, input, reference, runtime, artifact list, interpretation, limitations.
2. `sequence_qc.fastq_plasmid.v1`
   - FASTQ alignment, coverage, per-base support, consensus, IGV tracks, logs, report quality.
3. `sequence_qc.modified_bases.v1`
   - modkit summary, methylation BED, pileup logs, modified-base availability state.
4. `sequence_qc.igv_bundle.v1`
   - BAM/BAI/reference/FAI/track-config/report/auxiliary tracks.
5. `sequence_qc.wf_clone.v1`
   - wf-clone report/status/output directory/logs when assembly is enabled.

Minimum artifact fields:

- `kind`
- `path`
- `declared_path`
- `state`: `present`, `missing`, `not_applicable`, `failed`, `legacy_unavailable`
- `required`: boolean
- `media_type`
- `schema` for TSV/JSON artifacts
- `size_bytes`
- `sha256` optional for stable completed artifacts
- `producer`
- `quality`/`degraded_reason` for reports and fallback artifacts
- `coordinate_system` where relevant
- `safe_download_url` or API-resolvable artifact reference, not raw host path

FASTQ artifact kinds that must be represented:

- `read_lengths`
- `fastq_qc_summary`
- `fastq_alignment_stats`
- `fastq_coverage`
- `per_base_support`
- `reference_qc_fasta`
- `reference_qc_fai`
- `fastq_consensus_fasta`
- `fastq_consensus_fai`
- `fastq_consensus_log`
- `fastq_qc_log`
- `alignment_bam`
- `alignment_bai`
- `igv_track_config`
- `igv_report`
- `igv_report_log`
- `igv_coverage_depth`
- `igv_position_gradient`
- `igv_gc_content`
- `igv_gc_zscore`
- `igv_split_read_density`
- `igv_softclip_density`
- `igv_junction_hotspots`
- `igv_report_sites_bed`
- `igv_report_sites_tsv`

Modified-base artifact kinds:

- `modkit_summary`
- `methylation_bed`
- `modkit_pileup_log`
- `modkit_summary_log`

Assembly/wf-clone artifact kinds:

- `wf_clone_output_dir`
- `wf_clone_report_html`
- `wf_clone_sample_status`
- `wf_clone_log`

### Implementation targets

- Create: `platform/api/config/artifact_contracts/sequence_qc.manifest.v1.yaml`
- Create: `platform/api/config/artifact_contracts/sequence_qc.fastq_plasmid.v1.yaml`
- Modify: `scripts/build_sequence_qc_manifest.py`
- Modify: `modules/dorado.nf`
- Modify: `platform/api/services/sequence_qc_manifest.py`
- Modify: `platform/api/config/models/nanopore.yaml`
- Modify: `platform/frontend/src/components/ngs/useSequenceQcManifest.ts`
- Modify: `platform/frontend/src/components/ngs/SequenceQcManifestPanel.tsx`
- Add tests for manifest schema and artifact completeness.

### Acceptance gates

- Every artifact listed in `workflows/nanopore_methylation.nf` FASTQ stage has a manifest kind or is explicitly marked internal/debug.
- Manifest loader strips backend absolute paths and exposes only safe artifact references.
- Missing optional artifacts are represented as missing/not-applicable; no fake usable paths.
- Fallback IGV report is present but marked degraded.
- FASTQ-only manifest marks modified-base outputs as not-applicable.

---

## 9. Harmonization Need E — Make Manifest Primary in NGSToolkit

### Current evidence

- `NGSToolkit.tsx` displays the manifest panel, but artifact readiness still depends heavily on `stage_outputs` path collection and regex matching.
- `platform/frontend/src/components/NGSToolkit.tsx:562-616` resolves IGV artifacts from stage-output paths.
- `platform/frontend/src/components/NanoporeTemplate.tsx:801-837` submits the canonical nanopore job payload.

### Problem

The frontend now has two truth layers:

- Typed manifest panel for sequence-QC artifacts.
- Legacy path-scraped artifact resolvers for IGV/readiness/download display.

This makes UI behavior fragile and encourages adding more regexes instead of enforcing the artifact contract.

### Required spec

Add a manifest-to-view-model adapter and make it the default for terminal jobs that have a manifest.

View models:

- `NgsRunEvidenceViewModel`
- `IgvBundleViewModel`
- `FastqQcSummaryViewModel`
- `ModifiedBaseEvidenceViewModel`
- `WfCloneEvidenceViewModel`
- `LegacyStageOutputViewModel`

Rules:

- For completed current-schema jobs, manifest is authoritative.
- For running jobs, use stages/progress only; do not cache 404 manifest as a permanent miss.
- For old jobs missing manifests, display `legacy/unavailable` and optionally stage-output fallback if files actually exist.
- Never infer verified QC from job status alone.

### Implementation targets

- Create: `platform/frontend/src/components/ngs/manifestViewModels.ts`
- Modify: `platform/frontend/src/components/ngs/useSequenceQcManifest.ts`
- Modify: `platform/frontend/src/components/NGSToolkit.tsx`
- Add tests under `platform/frontend/tests/`:
  - manifest primary over stage outputs,
  - old-run 404 handling,
  - running-job refetch policy,
  - fallback/degraded IGV report display.

### Acceptance gates

- Given a manifest with BAM/BAI/reference/IGV report, NGSToolkit renders IGV readiness without scanning stage-output regexes.
- Given a missing manifest on a completed old run, NGSToolkit says legacy/unavailable, not workflow failure.
- Given a queued/running job, manifest 404 is not cached as a permanent old-run miss.
- Given a fallback IGV report, UI labels it degraded rather than equivalent to full IGV-report output.

---

## 10. Harmonization Need F — Result/Stage Path Canonicalization

### Current evidence

- The API creates output directories under the configured BMS results root.
- The workflow adapter translates container paths to host paths for launch.
- `scripts/stage_reporter.py` tries to normalize outputs to allowed-relative paths but may run in a host environment with different root env values than the API container.
- Job response code sanitizes and infers nanopore stage outputs when possible.

### Problem

A job can have:

- DB `output_dir` in container-visible form.
- Host Nextflow writes in host-visible form.
- Stage reporter emits host paths or relative paths depending on env.
- Frontend expects paths it can turn into downloads.

### Required spec

Store and expose paths in three separate slots:

- `canonical_result_ref`: API/UI stable reference, ideally `bms_results/<run>/...`.
- `execution_output_dir`: host adapter absolute path, internal/debug only.
- `container_output_dir`: API-container absolute path, internal/debug only.

Stage outputs should be represented as artifact references, not raw paths, whenever possible.

### Implementation targets

- Modify: job model/stage output serialization in `platform/api/routers/jobs.py`.
- Modify: `scripts/stage_reporter.py` to include both raw and normalized refs if necessary.
- Modify: `platform/api/services/sequence_qc_manifest.py` to preserve declared path but null usable path when missing.

### Acceptance gates

- Historical absolute paths do not leak as the primary UI/download contract.
- Downloads are resolved through allowed roots/API endpoints.
- A host-produced stage path under `${BMS_STATE_DIR}` maps to the same artifact ref the container API uses.

---

## 11. Harmonization Need G — Docs and Plan Hygiene

### Current evidence

- `docs/Lab_Automation_MolBio_and_Sequencing.md:37-71` correctly lists the current NGS route, components, model config, and workflow files.
- `docs/plans/2026-04-25-molbio-read-qc-harmonization-spec.md` predates some implemented pieces, including the sequence-QC manifest endpoint.
- The canonical docs currently describe NGS but do not fully encode the new container/adapter/path/artifact contract.

### Problem

The plan/docs layer is now behind the code in some places and ahead of it in others. That is dangerous for operator-facing NGS because docs can imply capabilities or artifact locations that are not currently true.

### Required spec

Update canonical docs after implementation phases land:

- `docs/Lab_Automation_MolBio_and_Sequencing.md`
  - Add runtime boundary summary.
  - Add sequence-QC manifest contract summary.
  - Add current limitations/historical-run caveat.
- `docs/Platform_Overview.md`
  - Mention host adapter ownership for workflow execution.
- `docs/Desktop_Runtime_and_Shell_Architecture.md`
  - Clarify container API vs host workflow adapter behavior for workflow families including NGS.
- `docs/plans/README.md`
  - Mark this spec as active until phased work is complete.
- Archive or supersede stale dated plan sections once they become canonical docs.

### Acceptance gates

- Docs distinguish shipped behavior, active plan, and historical artifacts.
- No docs claim AB1/chromatogram/virtual-gel/Q60-style deliverables unless implemented and tested.
- Docs say old jobs may lack manifests/output dirs and therefore are not proof of artifact availability.

---

## 12. Phased Implementation Plan

### Phase 0 — Baseline Truth and Regression Harness

Objective: lock in current behavior before refactor.

Tasks:

1. Add a small NGS fixture set:
   - perfect synthetic plasmid FASTQ + reference,
   - SNP fixture,
   - insertion fixture,
   - deletion fixture,
   - low-coverage fixture,
   - FASTQ-without-reference negative fixture.
2. Fix the `build_fastq_igv_tracks.py` test harness if direct temp-script execution is blocked; invoke fake `samtools` through the current Python interpreter.
3. Add a live-runtime smoke checklist that runs through BMS launch/adapter path, not host `nextflow` alone.
4. Preserve existing API/frontend tests.

Acceptance:

- Unit/API/frontend tests pass for existing contract.
- One synthetic FASTQ+reference job through live BMS produces:
  - `align/aligned.bam`
  - `align/aligned.bam.bai`
  - `fastq_qc/qc_manifest.json`
  - `fastq_qc/per_base_support.tsv`
  - 200 from `/api/sequence-qc/jobs/{job_id}/manifest`

### Phase 1 — Runtime Path Contract and Host Preflight

Objective: make container/host boundaries explicit before more UI work.

Modify:

- `platform/api/services/workflow_adapter.py`
- `platform/api/routers/jobs.py`
- `platform/api/services/nextflow.py`
- `scripts/stage_reporter.py`
- `platform/api/tests/test_nanopore_nextflow.py`
- new `platform/api/tests/test_nanopore_workflow_preflight.py`

Acceptance:

- Missing host inputs fail before Nextflow.
- Container-state inputs translate to host-state inputs.
- Stage outputs normalize to safe public refs.

### Phase 2 — Artifact Contract Registry and Manifest Expansion

Objective: eliminate drift between workflow outputs, manifest, registry, API, and frontend.

Create/modify:

- `platform/api/config/artifact_contracts/sequence_qc.manifest.v1.yaml`
- `platform/api/config/artifact_contracts/sequence_qc.fastq_plasmid.v1.yaml`
- `scripts/build_sequence_qc_manifest.py`
- `modules/dorado.nf`
- `workflows/nanopore_methylation.nf`
- `platform/api/services/sequence_qc_manifest.py`
- `platform/api/config/models/nanopore.yaml`

Acceptance:

- Manifest includes all FASTQ QC artifacts or explicitly marks them internal.
- Manifest includes degraded/fallback IGV report quality.
- Manifest includes mod-base not-applicable state for FASTQ-only runs.
- No absolute backend filesystem paths are returned as primary artifact paths.

### Phase 3 — NGSToolkit Manifest-First Refactor

Objective: move the frontend from regex path scraping to typed evidence view models.

Create/modify:

- `platform/frontend/src/components/ngs/manifestViewModels.ts`
- `platform/frontend/src/components/ngs/useSequenceQcManifest.ts`
- `platform/frontend/src/components/ngs/SequenceQcManifestPanel.tsx`
- `platform/frontend/src/components/NGSToolkit.tsx`
- frontend tests under `platform/frontend/tests/`

Acceptance:

- Manifest artifacts drive IGV/readiness/download display for current jobs.
- Stage-output fallback is only used for old/legacy jobs.
- Running-job manifest lookup does not permanently cache 404.

### Phase 4 — Model Registry/UI Parameter Cleanup

Objective: stop exposing runtime internals and legacy aliases as normal controls.

Modify:

- `platform/api/config/models/nanopore.yaml`
- model config loader if needed
- `platform/frontend/src/components/NanoporeTemplate.tsx`
- launch payload tests

Acceptance:

- `run_fastq_qc` is canonical.
- `run_multimer_qc` remains compatibility-only.
- Runtime-internal wf-clone params are hidden/advanced.
- Cross-field validation is enforced before workflow launch.

### Phase 5 — POD5/BAM Modified-Base Manifest

Objective: put modkit/POD5/BAM evidence under the same typed contract.

Modify:

- `modules/dorado.nf`
- `workflows/nanopore_methylation.nf`
- `scripts/build_sequence_qc_manifest.py` or create a second producer script
- `platform/api/services/sequence_qc_manifest.py`

Acceptance:

- BAM/POD5 jobs emit manifest sections for modkit summary/pileup where applicable.
- FASTQ-only jobs show modified bases as not-applicable.
- UI displays modified-base availability without implying missing evidence is a workflow failure.

### Phase 6 — Optional MolBio Read-Evidence Bridge

Objective: attach sequencing evidence to constructs without destabilizing `/designer`.

Create/modify only after Phase 2/3 are stable:

- `platform/frontend/src/components/MolBioToolkit/experimental/MolBioReadQcWorkbench.tsx`
- `platform/frontend/src/components/MolBioToolkit/experimental/ReadEvidencePanel.tsx`
- API contract for `molbio.read_evidence_attachment.v1`

Acceptance:

- `/designer` existing defaults/regression tests remain green.
- Read evidence attachment carries job ID, manifest ref, reference identity, coordinate system, and artifact links.
- No large read ledgers are stored in `NucleotideSequence.analysis_tracks`.

---

## 13. Validation Matrix

Required test classes:

- API unit/contract:
  - Nanopore cross-field validation.
  - Host/container path translation.
  - Preflight failure messages.
  - Manifest discovery and path safety.
  - Artifact missing/not-applicable/degraded states.
- Workflow/source:
  - Nextflow command golden tests for POD5, BAM, FASTQ, and wf-clone modes.
  - FASTQ artifact list parity between workflow and manifest contract.
- Frontend:
  - NGSToolkit manifest-first rendering.
  - Old-run missing manifest state.
  - Running-job refetch behavior.
  - Fallback IGV report labeling.
  - Nanopore launch payload defaults.
- Live runtime:
  - Synthetic FASTQ+reference through BMS API/container/adapter path.
  - Optional POD5/BAM smoke only when real input and hardware/runtime prerequisites are available.

Commands likely relevant after each tranche:

```bash
uv run --directory platform/api python -m pytest tests/test_nanopore_nextflow.py tests/test_sequence_qc_manifest.py -q
npm --prefix platform/frontend run build
npm --prefix platform/frontend test -- --run
```

The live-runtime smoke must be launched through BMS, not treated as proven by local host `nextflow` alone.

---

## 14. Final Definition of Done

The NGS toolkit is harmonized when all of these are true:

1. A current FASTQ job launched through the containerized BMS UI/API and host workflow adapter produces a typed manifest and verified artifacts.
2. A current POD5/BAM job emits typed modified-base evidence or truthful unavailable states.
3. NGSToolkit uses manifest-derived view models for current jobs.
4. Legacy jobs without result dirs/manifests are clearly labeled as legacy/unavailable.
5. Runtime preflight catches missing files/tools/images/weights before Nextflow starts.
6. Registry, workflow, manifest producer, API parser, frontend renderer, and docs agree on artifact names and semantics.
7. No raw host filesystem path is the primary frontend contract.
8. MolBio remains stable unless/until an opt-in read-evidence bridge is explicitly promoted.
9. Docs reflect the container/adapter/runtime truth and do not overclaim deliverables.
10. No fake artifacts, dry-run claims, or placeholder success states are used as proof.
