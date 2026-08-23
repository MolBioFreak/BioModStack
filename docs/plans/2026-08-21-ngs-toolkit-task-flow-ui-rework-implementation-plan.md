# NGS Toolkit Task-Flow UI Rework Implementation Plan

> **For Hermes:** Execute this plan directly in bounded work packages. Do not start repeated independent review cycles. Complete each RED/GREEN verification gate before the next package.

**Goal:** Implement the approved NGS task-flow workspace and Project panel so `BMS-DEV-24` through `BMS-DEV-27` can be closed through current live Development acceptance.

**Architecture:** Keep all scientific and Project authority in the existing React state, query, and mutation paths. Convert `NgsMolBioProjectHub` into a compact launcher plus modal for `/ngs`, place it in the existing `NGSToolkit` header, and leave `/designer` outside this NGS-only rework. Extract only the workflow chooser into a small rendered component for discriminating state tests. Recompose `NanoporeTemplate` in place so no launch-payload adapter or duplicate form state is introduced.

**Tech stack:** React 19, TypeScript 5.9, React Router 7, TanStack Query 5, Tailwind 4, Node test runner, Vitest 4 with jsdom, Vite 6.

**Controlling specification:** `docs/specs/2026-08-21-ngs-toolkit-task-flow-ui-rework-sow.md`

**Approved assets:**

- `docs/specs/assets/ngs-toolkit-task-flow-ui/task-flow-workspace.png`
- `docs/specs/assets/ngs-toolkit-task-flow-ui/project-panel-open.png`

**Planning baseline:** `e416df44d675fbeb01fc2c279362d9ff9e530023`

---

## 1. Scope lock

### Included files

- `platform/frontend/src/App.tsx`
- `platform/frontend/src/components/NGSToolkit.tsx`
- `platform/frontend/src/components/NanoporeTemplate.tsx`
- `platform/frontend/src/components/ngs/NanoporeWorkflowChooser.tsx` (new)
- `platform/frontend/src/components/molbio-ngs/NgsMolBioProjectHub.tsx`
- `platform/frontend/src/components/molbio-ngs/DomainExperimentWorkspace.tsx`
- `platform/frontend/src/lib/nanoporeLaunchPayload.ts`
- `platform/frontend/src/lib/nanoporeCloneState.ts` when reopen compatibility requires it
- `platform/frontend/tests/nanoporeTemplateContract.test.ts`
- `platform/frontend/tests/immutableNgsReferencePlane.test.ts`
- `platform/frontend/tests/nanoporeSettingsContract.test.ts`
- `platform/frontend/tests/nanoporeLaunchPayload.test.ts`
- `platform/frontend/tests/vitest/ngsWorkflowChooserMounted.test.tsx`
- `platform/frontend/tests/vitest/ngsProjectPanelMounted.test.tsx`
- `platform/frontend/tests/vitest/ngsPayloadMounted.test.tsx`
- `platform/frontend/tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx` (new when mounted coverage requires a separate harness)
- `platform/frontend/vitest.md.config.ts`
- `platform/api/services/ont_ngs_contract.py`
- `platform/api/routers/jobs.py`
- `platform/api/routers/ont_runs.py`
- `platform/api/tests/test_ont_ngs_operator_settings.py`
- `platform/api/tests/test_ont_ngs_submission.py`
- `platform/api/tests/test_ont_ngs_stage_inference.py`
- `platform/api/tests/test_ont_ngs_workflow_products.py`
- `workflows/ngs/ont_construct_screening.nf`

### Forbidden changes

- New workflow families or database schemas
- Project ownership semantic changes
- Scheduler redesign or multi-GPU policy changes
- Scientific computation, retry, or provenance changes
- NGS result viewers
- Job history redesign
- MolBio Toolkit redesign outside shared Project-launcher integration
- Production deployment
- Scientific Job submission during browser acceptance
- Canonical Development synchronization, commit, or push without operator authorization. The operator granted this authority on 2026-08-23 for this bounded candidate.
- Issue records outside `BMS-DEV-24` through `BMS-DEV-27`

The reviewed remediation amendment permits only the narrow existing settings → payload → fresh-request validation → API → Construct Screening gate/stage response → artifact metadata chain listed in SOW §4.3.

### Mandatory preservation

- Eleven current workflow choices
- `selectedWorkflow` and `selectWorkflow` behavior
- `initialValues` hydration
- exact MolBio sequence and revision requirements
- Project query and mutation implementations
- URL context and query parameters
- all current launch fields and conditional validation
- current error translations

---

## 2. Pre-implementation baseline gate

### Task 0: Pin the current implementation baseline

**Objective:** Start from the current `origin/test` without contaminating canonical Development or unrelated worktrees.

**Files:** No source edits.

**Step 1: Fetch without modifying canonical Development**

Run from the implementation worktree root:

```bash
git fetch origin test
```

**Step 2: Record identity and target-scoped status**

```bash
git rev-parse HEAD origin/test
git status --short -- \
  platform/frontend/src/App.tsx \
  platform/frontend/src/components/NGSToolkit.tsx \
  platform/frontend/src/components/NanoporeTemplate.tsx \
  platform/frontend/src/components/molbio-ngs/NgsMolBioProjectHub.tsx \
  platform/frontend/src/components/molbio-ngs/DomainExperimentWorkspace.tsx \
  platform/frontend/tests \
  platform/frontend/vitest.md.config.ts
```

Expected:

- HEAD equals the intended current `origin/test`, or the worktree is rebased/fast-forwarded before edits.
- Target source and tests have no unrelated edits.

**Step 3: Reconfirm the four issues from the authoritative Development API**

```bash
curl -fsS http://127.0.0.1:18002/api/dev/issues
```

Expected: `BMS-DEV-24`, `BMS-DEV-25`, `BMS-DEV-26`, and `BMS-DEV-27` remain open until live acceptance.

**Step 4: Capture existing test behavior**

Run from `platform/frontend`:

```bash
pnpm exec tsx --test tests/nanoporeTemplateContract.test.ts
pnpm exec tsc -b --pretty false
```

Record exact results. Existing failures must be separated from candidate-only failures.

---

## 3. Workflow chooser implementation

### Task 1: Add a rendered workflow chooser contract

**Objective:** Create a discriminating mounted test that proves inactive and selected card truth.

**Files:**

- Create: `platform/frontend/tests/vitest/ngsWorkflowChooserMounted.test.tsx`
- Modify: `platform/frontend/vitest.md.config.ts`
- Create later in Task 2: `platform/frontend/src/components/ngs/NanoporeWorkflowChooser.tsx`

**Step 1: Register the mounted test**

Add this exact path to `test.include` in `vitest.md.config.ts`:

```ts
'./tests/vitest/ngsWorkflowChooserMounted.test.tsx',
```

**Step 2: Write RED tests**

The test shall render the chooser with `selectedWorkflow="dna"` and assert:

```ts
const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>(
    'button[data-ngs-workflow-key]',
));
const byKey = (key: string) => buttons.find(
    (button) => button.dataset.ngsWorkflowKey === key,
);

expect(buttons).toHaveLength(11);
expect(byKey('dna')?.getAttribute('aria-pressed')).toBe('true');
expect(byKey('clone')?.getAttribute('aria-pressed')).toBe('false');
expect(byKey('pooledAssignment')?.getAttribute('aria-pressed')).toBe('false');
expect(container.textContent).toContain('VENDOR REPORT');
expect(container.textContent).toContain('REVIEW ONLY');
```

Also assert:

- only one card contains the selected border, tint, and ring classes
- inactive clone and pooled cards contain the same neutral border class as other inactive cards
- clicking `ONT FASTQ QC` calls `onSelect('fastqQc')` once
- keyboard focus exposes a visible focus class contract

**Step 3: Run RED**

```bash
pnpm exec vitest run --config vitest.md.config.ts tests/vitest/ngsWorkflowChooserMounted.test.tsx
```

Expected: FAIL because the component does not exist.

### Task 2: Extract and implement `NanoporeWorkflowChooser`

**Objective:** Centralize workflow metadata and render exact closed visual states without altering selection semantics.

**Files:**

- Create: `platform/frontend/src/components/ngs/NanoporeWorkflowChooser.tsx`
- Modify: `platform/frontend/src/components/NanoporeTemplate.tsx:1562-1598`
- Modify: `platform/frontend/tests/nanoporeTemplateContract.test.ts`

**Step 1: Define the closed workflow metadata type**

Use the current `selectedWorkflow` key union. Define metadata with:

```ts
type WorkflowBadge = 'VENDOR REPORT' | 'REVIEW ONLY';

type WorkflowChoice = {
    key: NanoporeWorkflowKey;
    title: string;
    input: string;
    result: string;
    badge?: WorkflowBadge;
};
```

Do not store inactive border classes in workflow data.

**Step 2: Move all eleven existing entries without changing keys or copy**

Add badges only to:

```ts
{ key: 'clone', badge: 'VENDOR REPORT', ... }
{ key: 'pooledAssignment', badge: 'REVIEW ONLY', ... }
```

**Step 3: Implement one selected-state expression**

Use one neutral inactive style and one selected style:

```ts
const selected = selectedWorkflow === workflow.key;
```

Render:

- `data-ngs-workflow-key={workflow.key}` for deterministic mounted inspection
- `aria-pressed={selected}`
- neutral border/background when false
- accent border/tint/ring when true
- neutral semantic badge independent of selection
- `focus-visible` classes

**Step 4: Replace the inline chooser in `NanoporeTemplate`**

Pass the current selected key and existing callback:

```tsx
<NanoporeWorkflowChooser
    selectedWorkflow={selectedWorkflow}
    onSelect={selectWorkflow}
/>
```

**Step 5: Update static contract coverage**

Keep the existing all-workflow inventory assertions. Replace class-source assumptions with assertions that the template uses `NanoporeWorkflowChooser` and does not retain hard-coded `tone: 'border-[var(--accent-secondary)]'` metadata.

**Step 6: Run GREEN**

```bash
pnpm exec vitest run --config vitest.md.config.ts tests/vitest/ngsWorkflowChooserMounted.test.tsx
pnpm exec tsx --test tests/nanoporeTemplateContract.test.ts
```

Expected: PASS.

---

## 4. Project launcher and panel implementation

### Task 3: Add the closed/open Project-panel test harness

**Objective:** Prove the persistent Project editor is absent by default and one accessible launcher owns the complete Project interaction.

**Files:**

- Create: `platform/frontend/tests/vitest/ngsProjectPanelMounted.test.tsx`
- Modify: `platform/frontend/vitest.md.config.ts`
- Modify later: Project components and NGS route composition

**Step 1: Register the test**

Add:

```ts
'./tests/vitest/ngsProjectPanelMounted.test.tsx',
```

**Step 2: Mock existing query and mutation boundaries**

Mock the current API/query hooks at their existing module boundaries. Do not replace Project state with a new fake implementation inside production code.

Provide deterministic fixtures for:

- one local Project
- one local contained Experiment
- one broader Project
- zero active mutations

**Step 3: Write RED tests for closed state**

Assert:

- one `Projects` button exists
- no dialog exists
- no Project name input exists
- no local Project selector exists
- no `Open global Project Manager` link exists while closed
- opening state has caused no mutation call

**Step 4: Write RED tests for open state**

After clicking `Projects`, assert:

- one element with `role="dialog"` exists
- the dialog has an accessible name
- `Local Projects` and `Broader Projects` tabs exist
- local open controls exist
- local create controls exist
- exactly one link or button navigates to `/projects`
- the old inline `Open Project Manager` action is absent

**Step 5: Write lifecycle tests**

Assert:

- Escape closes the dialog
- focus returns to the launcher
- explicit close works
- background interaction is blocked by modal semantics
- open/close causes no durable mutation
- switching tabs preserves unsaved fields during one open session

**Step 6: Run RED**

```bash
pnpm exec vitest run --config vitest.md.config.ts tests/vitest/ngsProjectPanelMounted.test.tsx
```

Expected: FAIL because the current Project hub is permanently expanded.

### Task 4: Convert the NGS Project hub into the approved launcher and modal

**Objective:** Preserve current Project authority while changing only presentation and placement on `/ngs`.

**Files:**

- Modify: `platform/frontend/src/App.tsx:98-103`
- Modify: `platform/frontend/src/components/NGSToolkit.tsx:4140-4184`
- Modify: `platform/frontend/src/components/molbio-ngs/NgsMolBioProjectHub.tsx`
- Modify: `platform/frontend/src/components/molbio-ngs/DomainExperimentWorkspace.tsx:885-898`

**Step 1: Keep MolBio route behavior outside this NGS rework**

Leave the `/designer` composition unchanged unless the shared component API needs an explicit display mode.

Use a closed prop contract such as:

```ts
type NgsMolBioProjectHubProps = {
    presentation?: 'inline' | 'launcher-dialog';
};
```

Default to `inline` so `/designer` does not change. Use `launcher-dialog` only from `/ngs`.

**Step 2: Move NGS ownership into the NGS header**

Remove the persistent `<NgsMolBioProjectHub />` immediately before `<NGSToolkit />` in the `/ngs` route.

Import and render this in the existing `NGSToolkit` header action group:

```tsx
<NgsMolBioProjectHub presentation="launcher-dialog" />
```

Place it before the view buttons so `Projects` remains visible across NGS views.

**Step 3: Preserve all Project queries and mutations**

Keep existing TanStack Query calls, context updates, creation mutations, governed-link mutations, and error mapping in `NgsMolBioProjectHub`. A modal render must call the same handlers as the inline implementation.

**Step 4: Implement modal state**

Add one local `isOpen` state. Opening or closing shall not mutate durable data or NGS form state.

The launcher-dialog presentation shall return:

- compact `Projects` button
- optional compact current-context badge
- fixed backdrop only while open
- one dialog panel only while open

**Step 5: Implement accessible dialog mechanics**

Reuse the established BioModStack dialog pattern from:

- `platform/frontend/src/components/project-manager/ProjectAttachmentDialog.tsx`
- `platform/frontend/src/components/MolBioToolkit/MolecularInputModal.tsx`

Add:

- `role="dialog"`
- `aria-modal="true"`
- `aria-labelledby`
- initial focus target
- Escape handler
- focus containment
- launcher ref and focus restoration
- backdrop interaction blocking
- `max-h-[90vh] overflow-y-auto`
- responsive full-height/sheet behavior below the mobile breakpoint

Do not add a new dialog dependency.

**Step 6: Structure panel content**

Use two tabs:

- `Local Projects`
- `Broader Projects`

Local tab owns current local select/open and create flows. Broader tab owns broader Project Experiment creation and governed exposure controls. Existing advanced metadata remains behind the current progressive-disclosure button.

**Step 7: Remove duplicate navigation**

In `DomainExperimentWorkspace`, replace the lower `/projects` link with explanatory text that directs the operator to the single header `Projects` launcher.

Exactly one open-dialog action may navigate to `/projects`.

**Step 8: Run GREEN**

```bash
pnpm exec vitest run --config vitest.md.config.ts tests/vitest/ngsProjectPanelMounted.test.tsx
pnpm exec tsc -b --pretty false
```

Expected: PASS.

---

## 5. Task-flow layout implementation

### Task 5: Add section and state-preservation tests

**Objective:** Prove the approved four-section layout without weakening conditional controls or payload behavior.

**Files:**

- Create: `platform/frontend/tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx`
- Modify: `platform/frontend/vitest.md.config.ts`
- Modify: `platform/frontend/tests/nanoporeTemplateContract.test.ts`

**Step 1: Register the mounted test**

Add:

```ts
'./tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx',
```

**Step 2: Render the smallest production-bound surface**

Render `NanoporeTemplate` with existing providers and API mocks. Use current fixtures for GPU inventory and MolBio sequence/revision data.

**Step 3: Assert ordered sections**

Assert these headings appear in DOM order:

1. `Job and input`
2. `Reference / sample`
3. `Basecalling`
4. `Analysis`

**Step 4: Assert workflow-specific behavior**

Exercise at least:

- DNA simplex
- clone validation
- ONT FASTQ QC
- pooled FASTQ assignment

For each, prove the existing required controls appear and incompatible controls disappear.

**Step 5: Assert state survives Project-panel lifecycle**

Enter a Job name and choose one non-default control. Open and close the Project dialog through the header composition. Assert both values remain.

**Step 6: Assert payload integrity**

Use the current submit mock. Confirm the candidate payload for each exercised workflow is exactly equal to the baseline fixture except for no UI-only fields. Hidden stale controls shall not appear.

**Step 7: Run RED**

```bash
pnpm exec vitest run --config vitest.md.config.ts tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx
```

Expected: FAIL before layout headings and composition exist.

### Task 6: Recompose `NanoporeTemplate` into four task sections

**Objective:** Use the approved horizontal layout while retaining one source of typed form truth.

**Files:**

- Modify: `platform/frontend/src/components/NanoporeTemplate.tsx:1600-end-of-form`
- Modify: `platform/frontend/tests/nanoporeTemplateContract.test.ts`

**Step 1: Inventory every existing control before moving JSX**

Create an implementation ledger in the task notes with:

- field/state name
- current selected-workflow condition
- target section
- payload key or UI-only classification

Do not move a control until its ledger row exists.

**Step 2: Add semantic section wrappers**

Use one shared section style and headings:

```tsx
<section aria-labelledby="ngs-job-input-heading">...</section>
<section aria-labelledby="ngs-reference-sample-heading">...</section>
<section aria-labelledby="ngs-basecalling-heading">...</section>
<section aria-labelledby="ngs-analysis-heading">...</section>
```

**Step 3: Compose desktop rows**

At `xl`:

- row one: `Job and input` plus `Reference / sample`
- row two: `Basecalling` plus `Analysis`

Use equal columns. Remove fixed spans that leave empty right-side columns.

At narrower widths, stack without horizontal scrolling.

**Step 4: Move existing controls without changing state setters**

Reparent JSX only. Keep existing hooks, computed flags, setters, validation, and submit construction.

**Step 5: Collapse advanced analysis controls by default**

Add visual disclosure state only. Do not change underlying setting values when collapsed or expanded.

Use an accessible disclosure button with `aria-expanded` and `aria-controls`.

**Step 6: Implement the review/status bar**

Derive summary text from current state. Do not add mirrored state.

Include only applicable values:

- selected workflow
- input readiness
- molecule/input mode
- model
- GPU policy
- reference/revision readiness

Wire `Validate` and `Review and submit` only to existing validation/submission handlers.

**Step 7: Run GREEN**

```bash
pnpm exec vitest run --config vitest.md.config.ts \
  tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx \
  tests/vitest/ngsWorkflowChooserMounted.test.tsx \
  tests/vitest/ngsProjectPanelMounted.test.tsx
pnpm exec tsx --test tests/nanoporeTemplateContract.test.ts
```

Expected: PASS.

---

## 6. Responsive and accessibility hardening

### Task 7: Close viewport and keyboard behavior

**Objective:** Make the approved desktop design usable on tablet and mobile without adding another UI mode.

**Files:**

- Modify the same production components and mounted tests from Tasks 2, 4, and 6.

**Step 1: Add responsive class assertions**

Prove:

- workflow cards use one column on mobile and increase columns by breakpoint
- task sections stack below desktop
- modal width is viewport-safe
- modal body scrolls internally
- primary actions do not require horizontal scrolling

**Step 2: Add keyboard interaction assertions**

Prove:

- workflow cards are reachable and activate as native buttons
- Project launcher opens with keyboard activation
- dialog focus stays inside
- Escape closes
- focus returns to launcher
- advanced controls expose correct `aria-expanded`

**Step 3: Add reduced-motion behavior**

Use existing motion conventions or CSS `motion-reduce` classes. Do not introduce decorative animation as a requirement.

**Step 4: Run focused verification**

```bash
pnpm exec vitest run --config vitest.md.config.ts \
  tests/vitest/ngsWorkflowChooserMounted.test.tsx \
  tests/vitest/ngsProjectPanelMounted.test.tsx \
  tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx
```

Expected: PASS.

---

## 7. Complete local verification

### Task 8: Run the complete frontend denominator

**Objective:** Prove the UI rework introduces no candidate-only frontend regression.

**Step 1: Run static and mounted contracts**

From `platform/frontend`:

```bash
pnpm exec tsx --test tests/nanoporeTemplateContract.test.ts
pnpm exec vitest run --config vitest.md.config.ts \
  tests/vitest/ngsWorkflowChooserMounted.test.tsx \
  tests/vitest/ngsProjectPanelMounted.test.tsx \
  tests/vitest/ngsTaskFlowWorkspaceMounted.test.tsx
```

**Step 2: Run TypeScript and production build**

```bash
pnpm exec tsc -b --pretty false
pnpm run build
```

Expected: exit code 0 for each.

**Step 3: Run full frontend suite**

```bash
pnpm test
```

Expected: zero candidate-only failures. Any aggregate failure must be compared against an immutable baseline with exact test names.

**Step 4: Check diff integrity**

From repository root:

```bash
git diff --check
git status --short
git diff --stat
```

Expected:

- `git diff --check` exits 0
- changed files remain within the approved list
- no generated build output is tracked

**Step 5: Inspect the launch-payload diff**

Review the exact `NanoporeTemplate` submission-object diff. Expected: no launch key, condition, default, or value change.

---

## 8. Browser acceptance before integration

### Task 9: Validate the real rendered candidate

**Objective:** Compare the implemented page with the two approved renders before integration.

**Step 1: Start only an isolated candidate frontend/API arrangement if needed**

Use unique ports and Development-isolated state. Do not repoint canonical Development. Stop all temporary listeners after acceptance.

**Step 2: Capture required viewports**

Capture `/ngs` at:

- 1600 × 1200
- 1366 × 768
- 768 × 1024
- 390 × 844

Capture states:

- default task-flow workspace
- one selected workflow
- Project panel open
- advanced controls open
- mobile Project panel

**Step 3: Visual acceptance checklist**

Confirm:

- all eleven workflows are visible at desktop width
- inactive clone and pooled cards use neutral borders
- one selected card has the accent treatment
- badges remain readable and do not imply selection
- Project forms are absent while closed
- the Project panel matches the approved structure
- exactly one global Project Manager action exists
- task sections use full horizontal space
- no overlapping, clipping, dead columns, or horizontal scroll appears

**Step 4: Interaction acceptance**

Confirm:

- workflow switching updates compatible controls
- Project panel open/close retains Nanopore fields
- Project tabs retain unsaved values during one open session
- Escape and focus restoration work
- no Project mutation occurs from panel lifecycle alone
- no console error or failed frontend request occurs

**Step 5: Cleanup**

Stop and remove every temporary candidate listener/profile. Prove no extra BMS listener remains.

No scientific Job may be submitted.

---

## 9. Integration and live Development acceptance

### Task 10: Integrate only after explicit authorization

**Objective:** Publish the accepted source to `test` without contaminating unrelated work.

**Precondition:** Christian explicitly authorizes commit and push.

**Step 1: Reconcile current remote**

```bash
git fetch origin test
git rebase origin/test
```

Resolve only target-file conflicts. Rerun Tasks 8 and 9 after any source reconciliation.

**Step 2: Verify staging scope**

Ensure `GIT_INDEX_FILE` is unset. Stage only approved implementation, tests, specification, plan, and design assets.

```bash
git diff --cached --name-only
```

Inspect every path before commit.

**Step 3: Commit and push**

Use a scoped commit message, for example:

```bash
git commit -m "feat(ngs): rework toolkit task-flow UI"
git push origin HEAD:test
```

No force push.

### Task 11: Deploy and accept canonical Development

**Objective:** Close the issues only after current live proof.

**Precondition:** Christian explicitly authorizes deployment/restart.

**Step 1: Wait for or run the supported managed Development synchronization path**

Do not edit `/home/dalab/biomodstack/dev-test-canonical` directly.

**Step 2: Prove lane identity**

```bash
git -C /home/dalab/biomodstack/dev-test-canonical rev-parse HEAD origin/test
curl -fsS http://127.0.0.1:18002/api/health
python /home/dalab/biomodstack/dev-test-canonical/scripts/manage_desktop_services.py --runtime dev status --json
```

Require source, API, frontend, and `origin/test` identity to agree.

**Step 3: Repeat browser acceptance on live `/ngs`**

Use the same viewport and interaction matrix from Task 9.

**Step 4: Clear only accepted issues**

After live PASS, update `BMS-DEV-24` through `BMS-DEV-27` with:

- status `cleared`
- accepted deployed SHA
- concise resolution note naming the exact visible fix

Do not clear any issue from source tests alone.

---

## 10. Acceptance ledger

| Gate | Required evidence | Pass condition |
|---|---|---|
| Workflow truth | Mounted chooser test | One selected card; neutral inactive clone and pooled cards |
| Project closed state | Mounted panel test | One launcher; no inline Project editor |
| Project open state | Mounted panel test | Accessible dialog with local/broader actions |
| Navigation dedupe | Mounted query/link count | Exactly one global Project Manager action |
| Task-flow layout | Mounted section test and browser capture | Four ordered sections, no dead desktop columns |
| State preservation | Mounted interaction test | NGS fields survive Project panel lifecycle |
| Payload integrity | Existing and new payload assertions | No launch-payload semantic change |
| Responsive UI | Four viewport captures | No clipping or horizontal page scroll |
| Accessibility | Keyboard/focus tests | Correct dialog and disclosure behavior |
| Local quality | TypeScript, build, full frontend suite | Zero candidate-only failures |
| Integration | Remote SHA | Accepted commit is on `origin/test` |
| Live Development | Source/runtime/browser proof | Current live page matches accepted SHA and behavior |
| Issue closure | Development issue records | Only BMS-DEV-24 through 27 cleared with deployed SHA |

## 11. Rollback boundaries

- **WP1 rollback:** workflow chooser component and its tests
- **WP2 rollback:** Project launcher/dialog composition and duplicate-link removal
- **WP3 rollback:** Nanopore task-section layout and review bar
- **WP4 rollback:** responsive/accessibility refinements

Each boundary shall preserve existing launch and Project semantics. If live acceptance fails, revert or forward-fix only the responsible work package. Do not alter scientific data, Project records, or Job history.

## 12. Completion statement

The implementation is complete only after:

1. Tasks 1 through 9 pass on the exact candidate.
2. Authorized integration places that exact behavior on `origin/test`.
3. Canonical Development serves the accepted SHA.
4. Live browser acceptance passes.
5. `BMS-DEV-24` through `BMS-DEV-27` are cleared with the deployed SHA.

A locally rendered candidate is not a live completion. A push without deployed identity is not acceptance.
