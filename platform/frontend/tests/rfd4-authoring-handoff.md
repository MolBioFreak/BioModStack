# RFD4 / Complexa authoring lane handoff

## Scope and base

Worktree: `/home/dalab/biomodstack/worktrees/denovo-rfd4-ui-20261025`.
Base: `84280fcf4973cce9ce03a7eb9fda32f66a6c57a4` (HEAD and merge-base verified clean before edits).
The four inspected authoring/source/contract owners have no diff from research baseline `fc4f27f4ab14fc46e22f327ef6fbf8c6254e0b8e` to this base. No Python, canonical checkout, shared parent/Results or binder invocation owner was edited. No Jobs executed, GPU use, rentals or pushes.

## Delivered

- Existing native editor composes `Rfd4TaskWorkspace`, `Rfd4ExpertSettings`, `ComplexaTaskWorkspace`; no new shell/run owner.
- RFD4 explicit new-chains, protein-context, motif, mixed-annotated, partial, inverse and annotated recipes. Recipe identity is editor-only; same-kind recipe requests and sources are independent. Unclassified historical requests remain unclassified and can be restored explicitly.
- Source-free named multi-chain rows with independent length bounds; primary replicate/batch/seed controls preserve omission/zero/cleared text.
- Condition-spec example/source/parse-policy rows; 44 schema-discovered condition families, independent native value/selection/group rows, remove and one-step undo; explicit probability zero and paired representative-atom rows. Selection adopts only into a source-qualified single-source example. Multi-source uncertainty is informational; manual editing and submission remain available.
- Ordered generated length spans, source fragments, reorder/remove and chain breaks edit `example.contig` itself. Manual native contig remains available; no parallel graphical state can diverge from submitted contig.
- Partial tail fraction versus independent backbone/latent times; inverse native generation profile; annotated evaluation basket and task-label rows.
- Expert sections for profiles/recycling, integration arguments, each sampler modality, actual checkpoint-step map, typed reward/strict criteria leaves, provider profiles/constructors/oracles, guidance/transforms, runtime/output and retained asset slots. Existing schemas remain the field authority. Default/profile metadata and requested overrides are explicitly distinct; this lane does not compose Hydra in React.
- Complexa actual task/operation labels and executed stages; operation determines outgoing generate/evaluate mode, count semantics, exact source slot, source-native input/contig fields, manual target-hotspot indices, separate author-identity motif/ligand atom rows and explicit visual adoption. Dedicated Complexa projection never reuses RFD4 label queries or truncates author chains. Profile/off/configured reward retains the configured draft outside native params.
- Native source row now leads with available producer identity; MODEL changes reset position UI independently of path. Removed incorrect generic visual-adoption inference from condition dictionary keys (keys are values, not source slots).

## Exact caller integration required (parent-owned)

1. `ProteinModificationTemplate.tsx`:
   - `reportDraft` currently emits `{...draft, ...navigation, ...}` and therefore overwrites the child's authoritative Complexa `native_mode`. For Complexa, emit the child-reported native mode **after** navigation, without remounting on every operation edit. Keep RFD4 outer generate/evaluate drafts intact.
   - Hide the outer Native mode selector for Complexa (its explicit operation is authoritative); retain it for RFD4. Child already submits `analysis` as evaluate and `design` as generate, with actual stage verbs.
   - Replace “Native task (configure below)” with a truthful task-workspace label, or route outer RFD4 task choices by initial `native_recipe`. Do not turn recipe IDs into request fields or infer a recipe from ambiguous saved science.
2. `JobSubmission.tsx` / existing save envelope:
   - Preserve `native_recipe`, `native_control_drafts`, existing `native_task_drafts` and `native_source_drafts` in editor_state. None belong in Job params. The existing child emits native_job_request separately.
   - Continuation receiver can pass `initialValues.native_recipe='partial'|'inverse'|'annotated'`, exact `rfd4_request`, `native_sources`, and `native_mode`; this does NOT construct/replace a supplied request. Evaluation uses `native_mode='evaluate'`.
3. Shared mounted runner: include `tests/vitest/rfd4TaskWorkspaceMounted.test.tsx` and `tests/vitest/complexaNativeSelection.test.ts`. The lane runner `rfd4Authoring.vitest.config.ts` collects both .tsx and .ts explicitly.
4. Existing historical mounted fixtures using `task.task`, primary `input.input.name`, “Sampling”, or “Sequence Design” selectors need to follow the new named task/primary controls, native input compatibility disclosure, named expert sections, and “Optional operations”. Do not restore duplicate scientific controls only to preserve old selectors.

## Verification and evidence class

Run from `platform/frontend`:

```
./node_modules/.bin/vitest run --config rfd4Authoring.vitest.config.ts --reporter=default --reporter=junit --outputFile=.artifacts/rfd4-authoring.xml
./node_modules/.bin/tsc -b --pretty false
```

Mounted tests use real React editor/source/typed controls, real Axios serialization, model-owned schema exports and replay captured native requests through actual Python request normalizers. Only transport, WebGL, placement picker and independent optional-operation component are inert. Unexpected Axios/XHR/fetch is rejected. No API persistence, installed materialization, inference or live browser result is implied.

Coverage: source-free multichain native wire; old unclassified request/source restoration; two condition-spec recipe switches and serialized cold reopen with numeric blank; exact MODEL/author/insertion source-table selection into independent conditions and pair rows; all 44 discovered family rows through native request normalization (not enum/annotation scientific-consumption proof); graphical span/fragment reorder into actual contig; partial/inverse/evaluate wires; sparse search criteria/zero/null/empty checkpoint preservation; Complexa exact author atom adoption, inactive reward cold reopen and authoritative operation wire. Pure tests cover wrong source/MODEL/copy/altloc/unresolvable identity, nonpolymer author positions and refusal to truncate a chain (observational only).

## Original acceptance gaps (superseded by incremental section below)

- Selected **effective** RFD4 profile composition is not returned by this base discovery owner. UI labels that absence rather than fabricating values or hydrating schema defaults. Backend/discovery lane must deliver authoritative projection before complete inherited-value acceptance. Complexa task default/profile identity readback is provided; full selected effective scalar projection is not.
- All condition families have row-level transport, but native enum/value meanings, saved internal-coordinate targets, grouped materialization, installed VHH/custom transforms, chemistry creation, multi-source disambiguation and annotation overlays are not qualified by this frontend lane. Values remain explicit native keys rather than a duplicated unverified enum dictionary.
- Complexa motif/ligand selection is projected; target-hotspot visual-to-native input-spec index mapping is not inferred. Native hotspot indices are directly editable, and this missing projection is disclosed. Arbitrary CIF-to-PDB and evaluate-directory preparation remain API/preparer owner work.
- Complex expert leaves and provider constructors remain schema-typed controls inside named expert groups; there is no new dedicated searchable provider-output catalog. State/adapter assets use retained source slots, not a new producer-aware state browser.
- Existing source viewer/table remains intact, but synchronized sequence highlighting and condition overlays are not newly implemented here. Contig transport is tested against native request validation, not installed native atom-array materialization.
- Parent Template/Project Save/second Save/cold Prepare/clone, full current shared fixture roster, mounted narrow/wide visual review, native composition/materialization, and Results continuation acceptance remain integrator work. Scientific/remote qualification is separately unauthorized and unrun.

## Incremental authoring closeout (after ae78d76)

Imported `d9eb20e` locally as `808f29a` solely for discovery/evaluation schema visibility. **Do not cherry-pick 808f29a as new authoring work.**

### Implemented and exercised

- `native_profile_defaults` now displays image/source-qualified default, selected single-group RFD4 snapshots and selected Complexa task snapshots, independently of requested overrides. Combined selected RFD4 configuration has an explicit readback action wired to the backend lane's inspected `POST /api/models/rfd4_proteina/generation-settings/preview?mode=generate|evaluate`: body is the sparse request itself, response fields `request`, `effective_config`, `runtime_image`. Stale results are hidden after request changes; failure is informational and never changes launch availability. No invented React composition.
- Complexa `evaluation_slots` is an ordered candidate basket for evaluate/analysis, with separate context slot, add/edit/remove/omit and serialized cold reopen. Existing source bindings are not deleted when removing basket entries.
- Existing Molstar/sequence viewer receives controlled, exact source-qualified selected identities and `showSequenceTrack`. Source table adoption is reflected in that same selection. No new viewer.
- Multi-source condition/pair adoption is available only when every source has a current inspected ledger and native label-chain sets do not collide. Installed RFD4 preserves these chains and rejects collisions; no source-index syntax or renamed chains is invented. Missing evidence leaves manual expressions and execution unchanged.
- Searchable provider/profile/metric catalog is generated from every real `providers` branch. Catalog selection alone never activates a profile; the selected active constructor edits the same native override. This is a constructor catalog, **not** a fully qualified scalar-output metric registry.
- Retained resume/adapter slots have file-only suggestions, exact managed path readback, omission controls, and source binding access even for source-free generation. No filename-derived compatibility claim.
- Real browser review corrected indistinct inner controls, a stretched candidate remove button, and overflowing image/source hashes on phone width. Scoped styles stay in the owned editor; no shared theme edit.

### Actual qualification / evidence

Evidence root: `platform/frontend/.artifacts/` in this worktree (retained, ignored; not part of cherry-pick).

- `rfd4-authoring-incremental.xml`: 29 passing tests, 2 files; no skipped test when both environment variables below are supplied. Full real API discovery schemas are used, including Complexa `$defs` and provider variants. Transport/WebGL are inert in mounted tests, not represented as live API/scientific execution.
- `tsc -b --pretty false`: exit 0.
- `denovo-rfd4-materialize.py`, `denovo-rfd4-native.log`, `native-materialization/evidence.json`: actual installed SIF `058a27dff02778cd7304cf8955c73115f6baaa4dfc0202540c73a10a403fa957` CPU preparation. Derived two differently named chains from its official one-atom unconditional sentinel; actually materialized `X1-1,20-20,/0,Y1-1` and `C_SEQ_PROB_CYS=0` through `bms_rfd4.inputs.prepare_inputs` and reloaded native CIF. Observed three native atoms/sentinels with chains X,X,Y and CYS probabilities 0,0,NaN. Confirmed actual native duplicate-X rejection. This is native contig/condition materialization, **not** folded-protein geometry, every condition family, or inference.
- `denovo-rfd4-compose.py`, `native-materialization/readback.json`: actual installed Hydra combined inverse_folding + thinking_low + explicit nsteps=31 + seed=0; mounted replay checks exact endpoint body, returned composition and stale-result suppression. HTTP endpoint integration itself belongs to backend lane (not yet in this worktree).
- `denovo-complexa-hotspot.py`, `native-materialization/complexa-hotspot.json`: installed Complexa SIF `49d75dc1d9c9e50051953df1d30e033cc4e83a91c8e47194e40ccec8d2044eac`, actual bundled pdb1ake target `A1-1`: integer `[1]` produces `[false]`; exact native token `['A1']` produces `[true]`. Target loading only; no inference.
- Browser actual owned components with real discovery JSON, transport disabled: `rfd4-{390,1440}.png`, `complexa-{390,1440}.png`, `complexa-1440-final.png`, `rfd4-profile-390-final.png`, `visual-bounds.json`. Initial Tailwind-scanning harness error was corrected before final screenshots. Actual 390/1440 widths had no horizontally overflowing controls; final profile metadata readback scrollWidth 375 at width 390. Screenshots received visual review; this is isolated authoring inspection, not full shell/Project/API acceptance or rendered 3D annotation qualification.
- `schema-leaf-inventory.json`: full `$ref`-resolved schema inventory: 3,550 RFD4 and 278 Complexa syntactic leaf/variant entries, zero recursive unresolved refs. These are **not control counts or scientific support counts**. Each row explicitly says schema inventory is not per-leaf mounted/native-consumption proof.

Rerun mounted tests:

```sh
BMS_TEST_API_PYTHON=/home/dalab/biomodstack/biomodstack/platform/api/.venv/bin/python \
BMS_TEST_NATIVE_READBACK=/home/dalab/biomodstack/worktrees/denovo-rfd4-ui-20261025/platform/frontend/.artifacts/native-materialization/readback.json \
./node_modules/.bin/vitest run --config rfd4Authoring.vitest.config.ts
./node_modules/.bin/tsc -b --pretty false
```

The API interpreter is required for full discovery imports (plain host Python lacks SQLAlchemy). Initial test attempts exposed that dependency and a subsequently repaired test JSX typo; final run is green.

### Exact remaining shared interfaces / implementation gaps

1. Integrate backend `routers/models.py` readback route plus `services.rfd4_proteina.preview_settings`. Frontend action is already wired to the exact inspected pending route; do not invent another endpoint or default resolver. Current frontend worktree does not contain that backend implementation.
2. **Critical backend mismatch:** `scripts/lib/complexa_contract.py:Source.target_hotspots` is `list[int]`, and `scripts/bms_complexa/config.py` forwards it unchanged. Installed `proteinfoundation.utils.pdb_utils.load_target_from_pdb` instead matches `f"{atom.chain_id}{atom.res_id}"`. Repair backend transport with an explicit historical-value policy before visual token adoption. Original UI claim of input-relative native integer indexing was incorrect and is removed. Manual integer compatibility values remain editable, but the UI now discloses that they do not establish native hotspot targeting. This is an implementation defect, not a scientific unsupported boundary.
3. Selection highlight is wired; **saved condition/annotation role overlays are not complete**. No native condition-expression evaluator or full annotation projection exists in this owned source receipt interface. Do not evaluate arbitrary native expressions in JavaScript or claim residue selection equals role overlay. Exact annotation projection is needed from the source/native preparation owner, then viewer metric-layer wiring must follow that identity.
4. Scientific condition value/enum editors, actual saved C_INTCRD target inspection, VHH/custom-transform authoring and interactive chemistry creation remain unqualified/unfinished. The full schemas are supplied to typed native compatibility controls, but per-leaf reachability/consumption is not exhaustively proven; named sections, row transport and inventory entries are not full usability/native-consumption acceptance. Provider scalar output discovery and producer-aware state browsing likewise remain beyond the constructor/file-slot improvements.
5. Parent-owned Template/JobSubmission editor-state/native-mode hunks in the original caller section still apply; do not treat the authoring lane as full Project Save/second Save/cold Prepare/clone acceptance. This incremental commit adds no changes to those owners.

### Scientific unsupported boundaries (not implementation gaps)

- Released Complexa adapter has no external remodeling/partial-diffusion task; native `gradient_ascent` is unimplemented; TMOL ligand/AME support is absent; binder native evaluator supports ColabDesign/RF3, not docstring-only Boltz2/Protenix.
- RFD4 native condition-spec does not auto-rename colliding source chains, and operates on asymmetric units. Copy/altloc identities without a native mapping are not guessed. No claim of universal BioIR support, chemistry synthesis, native search-state full RNG replay, or support from a newer runtime.
- No GPU, rental, inference, deployment, push, default/gate/scientific change occurred.

Dependencies were installed privately with `corepack pnpm install --frozen-lockfile --ignore-scripts --offline`; initial npm-ci attempt was unsuitable (pnpm workspace), and bare pnpm was absent. No lockfile changes.

## Saved-condition increment (after 60731cc)

- `1328b95` plus follow-up: retained CIF inspection uses Molstar's real CIF parser and the existing metric-layer/atom-sequence pipeline, not selection highlighting. Scalar atom_site conditions and saved vector idx0/valN payloads retain document, model-local atom index, label/author/atom identity. Optional annotation model_num is honored. Original coordinates are not overwritten or used as saved targets. Readback is explicitly requested per source and stale receipts do not retain layers.
- Pinned `058a27...` installed classes supplied the checked-in condition metadata; all 29 exported enum-name choices passed its actual `coerce_enum_value`. Boolean choices follow the installed boolean/ground-truth branch, not enum inference. Centroid xyz edits the installed comma-separated grammar. Manual native keys remain editable; no defaults or launch gates were added.
- Native CPU writer/parser specimen retained group 7 and C_INTCRD `[1,2,3]` while current coordinates were `[91,92,93]`. The committed CIF is this writer output, not an invented producer result. Probe/export scripts remain in active-profile scratch (`rfd4-saved-specimen.py`, `export-rfd4-conditions.py`). No inference ran.
- Mounted suite now checks actual `projectAtomMetricLayer` output (including explicit palette domain), separate empty temporary selection, absence/NaN, wrong MODEL, model-scoped vector category, enum/manual keys, centroid components and Complexa mixed historical integers/string reopen. `.artifacts/rfd4-saved.xml` retains the run. `tsc -b` succeeds.
- Actual browser `saved.html` loaded real Molstar and the native writer CIF: selected Saved C_CRDGRP showed `X:1:UNK` value 7 in the atom sequence layer and Molstar reported `Created Overpaint (1 Layers)`. `.artifacts/saved-role-browser.json` retains DOM/log evidence. Separate actual authoring browser showed NONE/DONOR/ACCEPTOR/BOTH from the pinned metadata. This is isolated component/browser qualification, not Project or live API acceptance.
- Complexa UI consumes string-capable discovery when available and preserves integers as numbers, strings as strings. Backend lane `3a8af73` was inspected, not cherry-picked; its real normalizer preserved `[3,"A42"]`. Installed `49d75dc1` target loader was re-inspected: matches exact `chain_id+res_id` against selected CA atoms, no integer/index inference. Source-to-loaded-target correspondence is not supplied by this receipt, so visual hotspot adoption is not fabricated; historical manual entries remain editable.

### Precise remaining boundaries

- Saved annotations are not unsaved condition-spec expression evaluation. Arbitrary draft-query overlays still need the native preparation owner's projection; JavaScript does not reinterpret the query language.
- Pair-category overlays and string-category coloring are unfinished; scalar numeric/boolean and vector-presence roles are colored, and retained scalar/vector values are readable. Saved string values are not converted to invented scientific scores.
- The shared viewer metric adapter does not match MODEL IDs. A selected-model derivative or single-model source is colored; all-model source values remain readable with a specific explanation. This is a viewer implementation boundary, not a new native scientific limitation or launch restriction.
- Sources without these saved categories genuinely supply no such targets. Missing C_INTCRD is reported by absence, never manufactured from generated coordinates. This is distinct from the unfinished pair/string rendering above.
- No VHH, chemistry creation, runtime science jobs, deployment or shared backend/source materializer edits.
