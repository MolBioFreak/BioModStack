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

## Explicit remaining acceptance gaps — do not call the full controlling plan complete

- Selected **effective** RFD4 profile composition is not returned by this base discovery owner. UI labels that absence rather than fabricating values or hydrating schema defaults. Backend/discovery lane must deliver authoritative projection before complete inherited-value acceptance. Complexa task default/profile identity readback is provided; full selected effective scalar projection is not.
- All condition families have row-level transport, but native enum/value meanings, saved internal-coordinate targets, grouped materialization, installed VHH/custom transforms, chemistry creation, multi-source disambiguation and annotation overlays are not qualified by this frontend lane. Values remain explicit native keys rather than a duplicated unverified enum dictionary.
- Complexa motif/ligand selection is projected; target-hotspot visual-to-native input-spec index mapping is not inferred. Native hotspot indices are directly editable, and this missing projection is disclosed. Arbitrary CIF-to-PDB and evaluate-directory preparation remain API/preparer owner work.
- Complex expert leaves and provider constructors remain schema-typed controls inside named expert groups; there is no new dedicated searchable provider-output catalog. State/adapter assets use retained source slots, not a new producer-aware state browser.
- Existing source viewer/table remains intact, but synchronized sequence highlighting and condition overlays are not newly implemented here. Contig transport is tested against native request validation, not installed native atom-array materialization.
- Parent Template/Project Save/second Save/cold Prepare/clone, full current shared fixture roster, mounted narrow/wide visual review, native composition/materialization, and Results continuation acceptance remain integrator work. Scientific/remote qualification is separately unauthorized and unrun.

Dependencies were installed privately with `corepack pnpm install --frozen-lockfile --ignore-scripts --offline`; initial npm-ci attempt was unsuitable (pnpm workspace), and bare pnpm was absent. No lockfile changes.
