# Sequence-design and packing harmonization

Status: implementation authorized and in progress. Christian also explicitly chose restoration of standalone ensemble design and fixed-sequence packing (SQ08). This is the focused sequence/packing slice of the existing [binder harmonization specification](de-novo-binder-harmonization.md). NGS is excluded.

## Outcome and boundaries

Finish the existing integrations: select the actual engine, edit its relevant settings, run locally or through the existing bridge, and reopen actual sequences, structures, native metrics and effective settings. Browser and agent use the same model contract.

Keep ProteinMPNN, FA-MPNN and Caliby as the shared binder sequence-design lineup. Keep sequence redesign, fixed-sequence packing, optional relaxation and diagnostic scoring distinct. No new engine, scheduler, database, readiness system or universal score. Reuse existing forms, model schemas, Jobs, source snapshots, publication, Project links and remote execution.

Missing development evidence does not become a launch/continuation gate. No additional diagnostic requirements, pass/fail cutoffs, proof receipts or startup requalification. Preserve existing ownership, integrity and native-input checks. This document authorizes no science runs, paid resources, deployment or unrequested scientific default changes.

## Baseline

The review at `7ac3c3682a65b4d8b1e6249f51ed3d6baa99feba` was reconciled against `5ce2210ad30fa22a94939d84865d852bcedb4e27`, matching canonical source, API build, frontend build and remote `test`. Reviewed sequence schemas/adapters and the Caliby descriptor were unchanged. Retain intervening remote-delivery and De Novo Design UI improvements.

Caliby's selected sequence-design leaf is implemented. FA-MPNN has retained completed native jobs. The prior review's 12 passing software cases do not establish native Caliby execution. No application tests or science were run for this documentation pass. Detailed evidence: `/home/dalab/.hermes/profiles/fresh/reports/bms-sequence-review-2026-09-25/review.md`.

## Fix list

### SQ01. Finish settings parity

FA-MPNN/ProteinMPNN mode lists omit values already consumed by their runners; Caliby's parent still uses sampling-overrides JSON.

Add `seqs_per_design`, FA-MPNN checkpoint selection and the consumed ProteinMPNN relaxation controls to the appropriate model schemas/modes. Reconcile other relevant pinned inference settings, including chain/residue controls, rather than treating these named omissions as the entire inventory. Reuse Caliby's existing typed Gaussian/Potts/side-chain controls in parent authoring. Normalize historical override JSON once at the existing request owner; new forms write typed settings.

Share definitions across standalone, selected and embedded forms. Use compact basic/advanced groups, not copied field lists or another form framework. Preserve saved/default/explicit false/zero/empty values through clone, retry and effective-settings readback. Scientific checkpoint choice is editable; runtime paths remain system-owned.

Edit owners: `platform/api/config/models/{fampnn,proteinmpnn,caliby_binder,antibody_denovo}.yaml`, existing normalization/compiler, `BinderSelectedControls.tsx`, `QualitySettingsPanel.tsx`. Compare with `modules/fampnn.nf:126–175` and `modules/proteinmpnn.nf:83–99`.

Evidence to close: nondefault browser/API requests reach the native arguments and reopen unchanged.

### SQ02. Fix ProteinMPNN selected-complex handling

Selected continuation supplies a multichain structure, but `scripts/prep_mpnn_designs.py` rejects it in generic mode. Removing the rejection alone is insufficient.

Carry explicit designed/fixed chain and residue roles through preparation, native features and output threading. Preserve target/context chains and exact source mapping. Check the installed runner's chain ordering and fixed-label handling; do not assume first/last chain, uppercase IDs or antibody CDRs for generic structures. Preserve existing monomer and historical antibody behavior.

Edit owners: `scripts/prep_mpnn_designs.py`, `modules/proteinmpnn.nf`, `platform/api/services/binder_continuation.py`, the model schema and only a narrowly necessary native adapter. Reuse existing source/role helpers.

Evidence to close: a selected multichain fixture traverses the real process shell/native boundary with the requested roles and exact output ancestry; existing monomer/antibody cases remain correct. Native execution is separate evidence.

### SQ03. Repair Caliby's parent descriptor

`platform/api/native_components.py:1097–1099` declares Caliby unavailable because retired `caliby_experimental` is disabled, although `workflows/antibody_denovo.nf:2508–2571` actually schedules `RunCaliby`.

Describe that real process and its selected optional filter/dependencies, including generated and pre-collected inputs. Reuse the selected Caliby dependency owner. Preserve current filter settings; add no new filter or cutoff. Keep `caliby_binder/design` and its existing runner. Enabling the retired standalone product is not this fix.

Evidence to close: parent compilation, selected execution metadata and actual process graph agree, without the stale retired-model blocker. Selected-leaf success alone does not close parent wiring.

### SQ04. Complete Caliby packaging and remote selection

The reviewed installation contains `caliby.sif` and `soluble_caliby_v1`, not every offered checkpoint. Independent prewarm discovery omits Caliby; its image recipe uses moving upstream and a Torch range.

Pin the intended native source/dependency inputs in `apptainer/caliby.def` without opportunistically changing the scientific version. Register Caliby in the existing provisioning catalog using the same selected image/checkpoint dependency helper as execution. Supply missing assets for supported checkpoint choices through the current managed-asset mechanism; never substitute another checkpoint silently. Bind writable cache and `model_params` through the existing runtime.

Optional self-consistency assets are selected only when that operation is requested. No per-job installation, model-loading preflight, repeated whole-weight hashing or preparation of every alternative model.

Evidence to close: prewarm and execution select the same requested assets; local/remote requests preserve settings; returned native outputs reopen without the worker. Installation or CPU preflight is not a successful native run.

### SQ05. Use the same designers in initial and selected rounds

The selected operations exist, but common initial sequence-design/prediction processing is disconnected across the four binder generators. Legacy authoring locks BoltzGen/PPIFlow downstream stages.

Put designer, sequences per backbone, prediction choice and coverage on the existing launch screen. Reuse the three model-owned requests/runners for initial follow-on and selected-result execution. Sequence-design backbone-only outputs before sequence-specific prediction; do not unnecessarily redesign already sequence-bearing outputs. Use producer-declared output kind, not filenames or guessed sequence patterns.

Retain the previously agreed blind-prediction direction: Protenix V2 as visible editable default, with no generated pose/template supplied as conditioning and independently visible MSA settings. Preserve generator-only exit, stage-off behavior and historical saved choices. Child failure must not erase or prevent viewing/selecting the primary candidates.

Extend existing child submission and successful-result handling, not a new round service, DAG or queue. Reuse Job/Design lineage, retry/cancellation and Project destination owners. Preserve exact sample-to-source-to-prediction links, not sequence/rank/filename matching. Reconcile concurrent initial-round work before editing; do not build a competing coordinator.

Edit owners: current generator/selected authoring, workflow follow-on dispatch and existing child/publication/Project seams. Evidence to close: both paths consume the same model contract, off stages do no work, outputs/settings reopen, and optional failure preserves parents. Report PAE/ipSAE only where compatible prediction actually produced them; no synthetic confidence or universal score.

### SQ06. Repair ordinary LigandMPNN routes and misleading labels

Only `interface_context` has its dedicated route. Advertised `ligand_aware`, `ntp_aware`, `metal_aware` and `dna_aware` design modes lack equivalent execution wiring. The debug DNA Polymerase template labels FA-MPNN execution as LigandMPNN.

Keep the diagnostic separate and unchanged. Connect each advertised ordinary mode to the installed native LigandMPNN design entrypoint, with model-owned settings, selected assets and native publication. An ordinary adapter is absent at this baseline: add only the necessary thin invocation/module/entrypoint, reusing current container/Jobs/compiler/result infrastructure. Do not repurpose the diagnostic as redesign or fall through to ProteinMPNN/FA-MPNN.

Map actual native structural context. SMILES, a metal/nucleotide label or DNA sequence cannot stand in for required context coordinates; do not invent poses. Native mapping decisions stay local to this item. Correct the debug label to the engine it runs; changing its scientific engine requires an explicit choice.

Edit owners: `platform/api/config/models/ligandmpnn.yaml`, exact routes in `services/nextflow.py`, minimal native adapter/publication, `config/templates/dna_polymerase.yaml`. Evidence to close: every advertised mode invokes the named engine and reopens its own outputs. Hiding modes or relabeling a fallback does not close integration. LigandMPNN does not become a fourth default binder designer.

### SQ07. Fix retained legacy wrappers, without promoting them

In `modules/antifold.nf`, pass requested count/temperature rather than hardcoded values. Preserve its antibody-specific role and existing request defaults; do not add it to the default lineup.

In `modules/thermompnn.nf`, check the subprocess result and read only the attempt-owned output, not arbitrary CSVs from the shared native directory. Return genuine native measurements or the existing optional failure/missing state, not success-shaped placeholder rows. Preserve parent continuation and published candidates; an optional scoring failure must not become a new parent stop.

Evidence to close: AntiFold consumes requested settings; ThermoMPNN failure/stale-file fixtures cannot produce fabricated native results. Keep FrustraMPNN analysis, Rosetta refinement and GROMACS separate and otherwise untouched.

### SQ08. Restore standalone Caliby ensemble design and packing

Christian explicitly selected restoration of both standalone ensemble design and fixed-sequence packing during implementation. The existing `caliby_experimental` identifier remains available for historical request compatibility; removal of retirement applies only to genuinely connected native modes.

Expose explicit ensemble-design/fixed-sequence-packing modes with applicable typed inputs/settings, correct checkpoint assets, an exact executable route and native result reader under the existing Caliby owner. Reuse compatible runtime code, not the retired bundle wholesale. Packing preserves amino-acid identity; sequence design is not a substitute.

Restore these paths alongside the selected sequence-design repairs. Missing native execution evidence remains a disclosed test limitation, not a new restriction on `caliby_binder` or ordinary continuation.

## Completion without runtime bloat

Use existing focused suites, adding cases only at changed owners: `test_caliby_sequence_design_regressions.py`, `test_binder_harmonization_compiler.py`, `test_binder_remote_harmonization.py`, `test_binder_continuation.py`, `test_selected_binder_publication.py` and mounted selected/authoring tests. Check collected case identities. Exercise real Nextflow transport with inert science fixtures where adapters change; helper-only tests can miss native shell/chain mismatches.

During authorized implementation closeout, use bounded benign native examples for materially different repaired paths and a representative remote return. Reopen actual files, source lineage, native metrics and settings through BMS. Reuse applicable existing evidence; do not run a benchmark campaign or every checkpoint combination to fix routing. Disclose unrun native cases plainly, without adding runtime proof gates.

Measure changed preparation/publication paths using existing timing evidence with equivalent scientific work. Separate cold acquisition, warm preparation and native compute. Remove demonstrated duplicate parsing/staging/transfers at the owning function. No new telemetry service, recurring scans, per-candidate setup, or speed claims obtained by reducing samples or changing science.

## Delivery order and ownership

1. SQ01–SQ04: repair existing settings, ProteinMPNN adapter, Caliby descriptor and runtime catalog. Preserve working FA-MPNN/selected-Caliby implementation.
2. SQ05: connect initial/selected use once through those same contracts. SQ06/SQ07 are independent repairs. SQ08 is a separate product decision and does not hold other corrections.
3. Run affected combined checks and authorized native/remote examples. Remove replaced active parameter assembly/branches in the same edits; no unrelated cleanup.

Existing packet owners remain: A owns UI; D the sequence-model slice; B parent/legacy workflow composition; C selection/publication; G managed assets/bridge; the integrator alone edits shared schema/compiler/Project owners. These are file-ownership boundaries, not six mandatory agent lanes. Name any new SQ02/SQ06 leaf files once before implementation; no directory-wide parallel write grants.

Scoped crosswalk, not a re-audit of the whole historical binder ledger:

- SQ01: A03/C01/C12/X.1. Existing execution, incomplete settings. D/A/integrator. Close request/native/readback agreement.
- SQ02: A06/A08/C03/C09. Connected selection, incompatible adapter. D/C/integrator. Close multichain roles and retained legacy behavior.
- SQ03: H08/A09/P1.4. Working leaf, contradictory parent descriptor. Integrator/B/D. Close actual graph/dependency agreement.
- SQ04: A12/A13/P1.5/X.3. Partial asset/discovery coverage, native execution unproven. D/G. Close selected asset delivery and real returned readback.
- SQ05: A07/A08/C14/P1.2. Disconnected common initial processing. B/A/C/integrator. Close initial/selected parity and parent-preserving failure.
- SQ06: separate ordinary-design correction adjacent to P3. Diagnostic already connected. Model owner/integrator/A. Close exact native routing, not a diagnostic replacement.
- SQ07: H03/C01/A10. Executable legacy wrappers with settings/result defects. B/C. Close argument propagation and truthful optional failure.
- SQ08: standalone scope not closed by P1.4. Retained but retired. Christian/D. Explicit restore/retain/retire decision.
