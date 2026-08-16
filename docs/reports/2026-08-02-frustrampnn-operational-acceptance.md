# FrustraMPNN operational acceptance — Development

- **Acceptance timestamp:** 2026-08-01T23:42:26-05:00
- **Environment:** BioModStack Development (`test`)
- **Accepted deployed revision:** `fc253fb6409da19d86ee09a904554f0ae5c31dbc`
- **Production:** not promoted; `main` was not modified
- **Product label:** **Frustration analysis**
- **Model:** FrustraMPNN
- **Interpretation:** scientific frustration-landscape analysis, not sequence design and not universal quality control

## Verdict

**PASS for Development.** Scheduler-governed FrustraMPNN execution is live-proven for a genuine Structure Prediction owner path and a selected nanobody-design child path. Disabled Structure Prediction persists the exact canonical `not_requested` pair. Result ingestion, immutable artifacts, row-level landscape persistence, APIs, and focused viewer contracts are accepted. Forbidden production ownership paths pass the retirement scanner. Production promotion remains owner-controlled.

## Release lineage

| PR | Merge revision | Scope |
|---|---|---|
| [#54](https://github.com/MolBioFreak/BioModStack/pull/54) | `1f06bea0d1cd20372caaf0f6e851d92cdf2efc91` | Preserve typed launch and scheduler CPU/GPU authority |
| [#55](https://github.com/MolBioFreak/BioModStack/pull/55) | `32d63aa0d5c85230bb6480d5c291df79218d35a0` | Flush result parent before composite-FK children |
| [#56](https://github.com/MolBioFreak/BioModStack/pull/56) | `f98420221ef48afc1b7f291262c2737f38624025` | Classify standalone FrustraMPNN as analysis |
| [#57](https://github.com/MolBioFreak/BioModStack/pull/57) | `27e9028c293ae213085e63cdb41a317ae1b6f337` | Safe parent-workflow publication, ESMFold2 root discovery, antibody dereference |
| [#58](https://github.com/MolBioFreak/BioModStack/pull/58) | `4a7d0720642137e8c27a8681da3a3b2c683a74a4` | Preserve explicit job-root-relative stage outputs |
| [#59](https://github.com/MolBioFreak/BioModStack/pull/59) | `fc253fb6409da19d86ee09a904554f0ae5c31dbc` | Retire stale redesign wording; enforce `Frustration analysis` |

## Runtime identity

Accepted enabled runs reported:

| Field | Value |
|---|---|
| Image | `/mnt/BioModStack/apptainer/frustrampnn.sif` |
| Image SHA-256 | `c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da` |
| Executable | `/opt/venv/bin/frustrampnn` |
| Executable SHA-256 | `32089d959f619c08a550c0e7d0fc7b66b508d009ec3179d007f13773a170212f` |
| Checkpoint | `/opt/frustrampnn_weights/megascale.ckpt` |
| Checkpoint SHA-256 | `eaee71adb7eec366fc672d2aadef87f2c51243042a4518cd897634784dc2da3b` |
| Runtime GPU authority | scheduler-assigned physical GPU `3`; task-visible device `0` |
| Container policy | `apptainer_containall_v1` with read-only normalized input and bounded writable output |

No request-thread, browser-thread, direct CLI, or unmanaged production inference path was used for acceptance.

## Live enabled Structure Prediction acceptance

- **Job:** `7b8b860d-3a03-407c-8936-afddc05542d0`
- **Name:** `frustrampnn_structure_1ubq_final_acceptance_20260802`
- **Parent workflow:** ESMFold2 Structure Prediction, `run_frustrampnn=true`
- **Sequence:** 1UBQ, 76 residues
- **Terminal status:** `completed`
- **FrustraMPNN stage:** `complete`
- **Invocation:** `frustrampnn:53578d5e-3bec-5a7d-a36d-e5903d35206b`
- **Runtime exit code:** `0`
- **Runtime duration:** `13.08483` seconds
- **Persisted results:** `1`
- **Immutable artifacts:** `10`
- **Landscape rows:** `1,520`
- **Unique `(auth_asym_id, auth_seq_id, insertion_code, mutation_aa)` rows:** `1,520`
- **Native slots:** `76`
- **Manifest SHA-256:** `499b663834a40681350c6a42750129f007fa20e1a388c1e08a7492c8540b3bf0`
- **Landscape SHA-256:** `d1603df535aa3339326d30bb3d9f7bc92eb007362ab276ec97c1623c90290fc9`
- **Summary SHA-256:** `7914292530dbf17050fe06fe948a7248b5f650a8429682e6e174545a010b3d49`
- **Normalized input SHA-256:** `6a46f2a9f37c32b36d1dbf10a435dd9f465f47d290a9585b1b5fa8c8a4e0700d`

The persisted terminal outputs are exact job-root-relative paths:

```json
[
  "frustrampnn/results/53578d5e-3bec-5a7d-a36d-e5903d35206b/workflow_component_result_v1.json",
  "frustrampnn/results/53578d5e-3bec-5a7d-a36d-e5903d35206b/frustrampnn_result_manifest_v1.json",
  "frustrampnn/sources/esmfold2/1UBQ.normalized.pdb"
]
```

The Results API, manifest API, landscape API, and paginated rows API each returned HTTP 200 for the accepted result.

## Exact disabled-state acceptance

- **Job:** `675fd188-e085-4b80-8a42-bc123fbddfde`
- **Name:** `frustrampnn_structure_1ubq_disabled_final_20260802`
- **Parent workflow:** ESMFold2 Structure Prediction, `run_frustrampnn=false`
- **Terminal status:** `completed`
- **FrustraMPNN results/artifacts/rows:** `0 / 0 / 0`

Exact persisted pair:

```json
{
  "provenance.stage_terminal_states.frustrampnn": {
    "status": "not_requested",
    "outputs": []
  },
  "stage_outputs.frustrampnn": []
}
```

## Nanobody/de novo lineage acceptance

This gate exercised the typed selected-design analysis path on a genuine persisted nanobody design; it did not rerun the full upstream RFA/PPIFlow generation pipeline.

- **Source parent:** `85ab71f6-c411-4e6b-9020-394fd67ff1c7`
- **Selected design:** `196bfb71-5777-52f2-844b-997f92d7cc05`
- **Scheduler child:** `8391e25b-e7a4-4fad-b1a1-fcf5d0b888fb`
- **Trigger:** `design_analyze`
- **Source stage:** `post_fampnn`
- **Source stage family/mode:** `ppiflow` / `backbone_refine`
- **Terminal status:** `completed`
- **Invocation:** `frustrampnn:8391e25b-e7a4-4fad-b1a1-fcf5d0b888fb:1`
- **Persisted results/artifacts/rows:** `1 / 10 / 9,780`
- **Native slots:** `489`
- **Unique author-identity/mutation rows:** `9,780`
- **Source design projection:** `frustrampnn_status=succeeded`, contract version `1.0`
- **Manifest SHA-256:** `25ec743e390e88577b67e263813390364e39b423b2cccf77df6dae61d14f2ec6`
- **Landscape SHA-256:** `5b24567242e3b410009b5fac7751635e9ab0b46048d1fdc6597a693949038b40`
- **Summary SHA-256:** `d5deae89ccb8ee8d7c053b071a9091d5fcaf795b13ee2fb8733ce1e9949d9c37`

The scheduler receipt and results APIs returned HTTP 200. The source SHA was checked before immutable snapshotting and launch.

## Conformational Mapping qualification

Latest live persisted read-path canary:

- **Request/job:** `959d1064-4f92-5f97-8293-a0e220768570`
- **Status:** `completed`
- **Result contract:** `conformational_mapping_confornets_v1`
- **Persisted records:** `12`
- **Artifacts:** `33`
- Status, progress, results, and landscape APIs returned HTTP 200.
- Landscape returned a bounded 500-row page with continuation.

Focused qualification:

- Backend CM FrustraMPNN adapter, persistence, API, state projection, and analysis contracts: **59/59 passed**.
- Frontend CM launcher/semantics: **21/21 passed**.
- Mounted CM viewer behavior: **1/1 passed**.
- Mol* overlay identity remains API-owned `(auth_asym_id, auth_seq_id, insertion_code)`.
- Numerical classification thresholds remain backend-owned.

A broad frontend command unintentionally exercised the repository-wide test glob and reported four unrelated existing failures, including Stats Toolkit assertions. The explicitly targeted CM suites above passed; this packet does not claim the entire frontend repository is green.

## Focused source verification

| Gate | Result |
|---|---|
| Scheduler CPU/GPU authority | 38 passed |
| SQLite parent-before-child persistence | 31 passed |
| Standalone analysis classification | 35 passed |
| Parent publication/root resolution/antibody consumer | 111 passed |
| Job-root stage-reporter behavior and five publisher consumers | 120 passed |
| CM backend qualification | 59 passed |
| CM frontend semantics/launcher | 21 passed |
| Mounted CM viewer behavior | 1 passed |
| Retirement scanner and label regression | passed |

Independent exact-tree reviews returned PASS for CPU authority, persistence order, standalone classification, final antibody path validation, combined parent publication/root resolution, stage-reporter preservation, and the final product-label correction. Earlier reviewer FAIL findings about antibody task-directory dereference, dot-component normalization, and Nextflow backslash escaping were fixed and superseded by later stable-hash PASS reviews.

## Retirement and negative evidence

The active retirement scanner reports no forbidden production ownership for:

- retired upload routes;
- legacy standalone Nextflow ownership;
- generic batch triggers;
- loose `*_frustration.csv` discovery;
- basename-only candidate joins;
- frontend raw/native score parsing;
- fail-open FrustraMPNN error strategy;
- direct production model subprocess ownership;
- duplicate numerical threshold policy.

The final stale label `FrustraMPNN redesign` was replaced with `Frustration analysis` and is guarded by a negative regression test.

Historical probe/evidence scripts that record prior runtime observations are not active production launch paths. The canonical runtime adapter and scheduler-owned workflow processes remain intentionally preserved embedded capabilities.

## Development deployment proof

At acceptance time:

- Canonical checkout revision: `fc253fb6409da19d86ee09a904554f0ae5c31dbc`
- `biomodstack-api.service`: active
- `biomodstack-frontend.service`: active
- `biomodstack-workflow-adapter.service`: active
- API listener: `127.0.0.1:18002`
- Frontend listener: `127.0.0.1:18082`
- OpenAPI: HTTP 200, title `BioModStack Control Platform`, 326 paths

## Scope boundary

This is Development acceptance on `test`. It does not authorize or claim:

- promotion to `main`;
- production deployment;
- classification of FrustraMPNN as universal structural quality control;
- sequence redesign ownership;
- caller-selected physical GPU or runtime resource authority;
- replacement of backend CM numerical classification with frontend thresholds.
