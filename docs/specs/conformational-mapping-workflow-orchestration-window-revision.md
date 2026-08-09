# Conformational Mapping Workflow-Orchestration Window Revision

**Status:** Implemented and verified in the isolated worktree

**Owner:** Christian

**Specification date:** 2026-08-08

**Source baseline:** `origin/test` at `8112bed622740b890e25b69c2bcf29ebebeaf3d6`

**Implementation authority:** Authorized by Christian on 2026-08-08: “ok get it done.”

**Test authority:** Authorized with the implementation work package.

## 1. Purpose

Revise the existing Conformational Mapping launcher into a full-width workflow-orchestration workspace.

The revision must help an operator answer these questions before launch:

- Which source did I select?
- Which structure, model, sample, chains, and entities will the workflow use?
- Which workflow mode will run?
- Which scientific settings will affect the request?
- Which values will BioModStack derive from installed runtime policy?
- What exact request will become immutable when I submit it?

The launcher remains the existing Conformational Mapping window. This specification does not authorize a replacement launcher, a new workflow system, or a second source registry.

## 2. Controlling decisions

Christian approved these decisions:

1. Revise `ConformationalMappingLauncher.tsx` and its supporting contracts. Preserve sound existing behavior.
2. Use the full available workflow-page width on desktop.
3. Vertical scrolling is acceptable.
4. Avoid the current narrow middle-column presentation.
5. Use the selected card-grid concept as the rough visual direction.
6. Keep source selection and structure preview side by side.
7. Reuse mature BioModStack source, model, sample, chain, and provenance patterns.
8. Keep result viewing and post-run analysis outside this work package.

## 3. Scope

### 3.1 In scope

- Full-width launcher layout
- Run name and durable notes
- Source browsing and source registration
- Upload, Your Runs, RCSB, and Cached source paths
- Presets remain hidden until CM has an authoritative preset receipt
- Saved PDB and prior-run access where authoritative artifacts exist
- Structure preview during workflow configuration
- Resolved model or sample context with fail-closed ambiguity
- Source-derived chain and entity context, with selection only when the backend consumes it
- Backend-specific scientific controls
- Progressive disclosure for legitimate advanced controls
- Derived read-only runtime and provenance values
- Backend-specific validation
- Pre-submit request summary
- Typed frontend and API contracts for every operator-facing choice
- Persistence of selected input context and notes
- Focused responsive behavior for desktop, laptop, and narrow screens

### 3.2 Out of scope

- `ConformationalMappingViewer.tsx`
- Candidate result selection
- Result overlays
- State-landscape result analysis
- FrustraMPNN result visualization
- Exact-20 result heatmaps
- Result downloads and result evidence panels
- Changes to model algorithms
- New model installation or runtime bootstrap
- Scheduler policy changes
- Deployment, restart, or live acceptance
- Replacement of the CM source registry
- Broad refactoring of de novo nanobody components
- Historical result migration beyond compatibility required by the new request fields

A shared type or source adapter may change when the launcher requires it. Such a change must not alter result-view behavior.

## 4. Selected page composition

### 4.1 Wide-screen layout

The page uses the available workflow content width.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Conformational Mapping title, workflow status, and launcher help          │
├───────────────────────────────────┬────────────────────────────────────────┤
│ Run record                        │ Scientific controls                    │
│ Name, notes, derived run details  │ Mode, cardinality, bounded settings    │
├───────────────────────────────────┼────────────────────────────────────────┤
│ Source browser                    │ Selected-structure preview             │
│ Upload/Runs/RCSB/Cached           │ Model/sample, chains, entities         │
│ Cached entries and source search  │ Read-only selected-input identity      │
├───────────────────────────────────┴────────────────────────────────────────┤
│ Full-width pre-submit summary and launch action                           │
└────────────────────────────────────────────────────────────────────────────┘
```

The run-record and scientific-controls cards share a row when both cards meet their minimum readable width.

The source browser and structure preview share the main workspace row. Each side receives useful horizontal space. The preview must remain large enough for structure inspection and chain confirmation.

The pre-submit summary uses a normal full-width card at the bottom. It must not cover other controls.

### 4.2 Page-width behavior

- The launcher must not inherit a narrow content width that restricts it to the middle third of the page.
- The launcher may retain the application shell, navigation, and normal outer padding.
- Inner cards must use a responsive grid based on available width.
- Fixed pixel widths must not create horizontal scrolling at supported laptop widths.
- Numbered step badges are optional. If present, visual reading order must remain coherent. Section headings are authoritative.

### 4.3 Responsive behavior

- Wide desktop: two cards per row as shown above.
- Constrained desktop or laptop: preserve two columns while each card remains readable.
- Narrow layout: stack cards into one column in this order: Run record, Source browser, Structure preview, Scientific controls, Pre-submit summary.
- Expanded inline content may increase page height.
- Vertical scrolling remains normal page scrolling.

The implementation must use the established frontend breakpoint and spacing system where it exists. It must not add a launcher-specific parallel design system.

## 5. Run record

The run-record card appears at the top of the launcher.

### 5.1 Operator fields

| Field | Behavior | Persistence |
|---|---|---|
| Run name | Required typed text field. Preserve current request-name behavior. | CM request and owning job |
| Notes | Durable free-text field for purpose, hypothesis, handling instructions, or other operator context. Empty value is permitted. | CM request projection and immutable request JSON |

### 5.2 Compact run details

The card also shows relevant details without asking the operator to enter the same information twice:

- draft or ready state;
- owner;
- creation time after persistence;
- selected target or structure summary;
- selected workflow mode;
- planned candidate count when resolved;
- validation state.

These values derive from authoritative launcher state. They are not separate editable metadata fields.

The card must remain compact. Tags, priority, collaborators, and custom metadata are excluded unless an existing BioModStack authority already requires them.

### 5.3 Read-only record state

Before launch, show that the record is a draft configuration.

After request creation, the durable request ID, source IDs, content hashes, resolved model/sample context, retained or backend-consumed chains, backend, and effective runtime values become immutable request context.

The launcher must not fabricate a request ID before the API creates one.

### 5.4 Notes contract

The current CM submit schema rejects unknown fields. Notes therefore require an explicit typed API field.

The implementation must:

- add a typed notes field to the frontend submit contract;
- add the corresponding API schema field;
- persist it in the canonical request record;
- return it in request projections used by operator surfaces;
- preserve it through retry and audit history;
- keep existing requests with no notes readable.

## 6. Source browser

### 6.1 Source paths

The source browser exposes these recognizable paths:

- Upload
- Your Runs
- RCSB
- Cached

These paths use shared source-selection behavior where possible. They do not create separate CM-only copies of existing search or cache logic.

### 6.2 Reuse map

| Existing surface | CM use |
|---|---|
| `ReferenceSelector.tsx` | Your Runs, RCSB, and Cached structure patterns; its preset pattern is deferred pending CM authority |
| `TargetAntigenSelector.tsx` | Upload, run-derived target, and RCSB target patterns |
| `AntibodyDenovoTemplate.tsx` | Model selection, chain buttons, structure preview, and selected-input propagation patterns |
| CM source endpoints | Content-addressed source registration and authoritative source handles |

Reuse means shared components, extracted primitives, or adapters over current contracts. It does not require copying the de novo workflow into CM.

### 6.3 Source identity

Every selected source must produce one typed CM input selection envelope with operator-readable and backend-readable identity.

The envelope must support these concepts when applicable:

- origin kind;
- provider or source type;
- RCSB accession;
- prior run and artifact identity;
- preset identity;
- registered CM source ID;
- content SHA-256 and byte count;
- structure format;
- resolved model or sample context;
- retained chains and entities;
- backend-consumed chain identity where supported;
- context entities retained in the input;
- preview descriptor;
- provenance for any explicit normalization or extraction.

The exact TypeScript and API type names are implementation decisions. The envelope must remain typed and versionable.

### 6.4 Registry boundary

The CM source registry remains authoritative.

- Source tabs provide operator-friendly discovery.
- Selected bytes enter CM through governed registration endpoints.
- The request carries registered handles and typed selections.
- Raw registry administration stays out of the ordinary launcher surface.
- Source IDs and hashes remain available in read-only provenance details.
- The UI must not submit browser URLs, local paths, or unregistered bytes as request authority.

### 6.5 Upload

Upload must:

- accept only formats supported by the selected workflow path;
- show the file name and detected type;
- validate structure or sequence content before launch;
- register accepted bytes through the normal CM source path;
- show registration failures beside the upload control;
- preserve the original source identity.

A named transformation may create a derived input. The interface must describe the transformation and preserve parent-source provenance.

### 6.6 Your Runs

Your Runs must:

- show only accessible runs with authoritative reusable artifacts;
- identify the originating workflow, run name, completion state, and artifact type;
- require an explicit artifact choice when a run contains several structures;
- reject missing or unavailable artifact bytes before submission;
- register or resolve the artifact through the governed CM source boundary.

A generic completed status does not make every run artifact a valid CM input.

### 6.7 Presets

The launcher does not expose a Presets tab in this revision. CM has no
authoritative preset receipt or preset adapter. A label-only metadata heuristic
would let caller-supplied metadata claim preset authority. Presets can enter the
launcher after a versioned server-owned receipt and adapter exist.

### 6.8 RCSB

RCSB remains a distinct source path.

It must provide:

- accession or keyword search;
- human-readable entry metadata;
- cached RCSB entries;
- provider accession and retrieval provenance;
- fail-closed handling when an entry contains several models without a backend-native transformation;
- retained chain and entity context for complete-context workflows;
- raw mmCIF registration through the server-owned path.

The registered source authority receipt must retain the RCSB accession as the operator target identity. It must not fall back to an opaque source UUID when the accession is available.

Provider and normalization authority live in server-owned, content-bound receipt files outside caller metadata. Legacy metadata cannot claim those receipts.

### 6.9 Cached

Cached entries must:

- show provider, accession or run identity, registration time, and availability;
- retain content hashes;
- avoid duplicate visual entries for the same authoritative source where the current cache contract can resolve identity;
- require model and chain confirmation when ambiguity remains.

## 7. Structure preview and source resolution

### 7.1 Preview role

The structure preview is a workflow selector surface. It is not a result viewer.

The preview must show:

- selected source label;
- resolved model or sample context;
- chain and entity identity;
- target versus retained context;
- a compact structure visualization when coordinates exist;
- a sequence or entity summary when no previewable structure exists;
- clear unavailable state when preview data cannot be resolved.

A compact Mol* callsite is appropriate for this constrained selector.

### 7.2 Model and sample context

Show a source-defined singleton model or sample as derived context.

When a source contains several models or samples and the installed backend has no explicit transformation that consumes the choice, fail before launch. Do not offer a selector that changes only display metadata while leaving staged coordinates unchanged.

A later backend-native transformation may add an explicit selection only when it binds the selected identity into the typed input envelope and staged source.

The word `sample` must refer to the source-native coordinate dimension. Generated workflow candidates use separate backend cardinality labels.

### 7.3 Chain and entity selection

When a structure contains multiple chains or entities:

- show chain label, entity type, residue count, and role where known;
- show retained complete-complex context as read-only when the backend consumes the complete source;
- use a selector only when the backend contract consumes that selected chain identity;
- prevent free-text chain IDs when a structure defines the available chains;
- bind any supported selection into both staged execution input and the persisted record.
- derive the ConforNets single-chain identity from the server submission policy; caller-owned chain, test-case, and benchmark fields are forbidden;
- derive external-import model and retained-chain context from the staged mmCIF normalization snapshot before resealing request and coordinate-plan hashes.

Chain selection must not silently delete solvent, ligand, ion, nucleic acid, partner, or repeated-entity coordinates.

If a backend requires a derived protein-only or selected-chain artifact, create an explicit named transformation with parent-source provenance. Unsupported input must fail before launch.

### 7.4 Preview-payload invariant

The preview and payload must share one selected-input state.

Local-file preview is available only for the external-import lane before
registration. Choosing an immutable source clears that local preview, and
choosing a replacement local file clears the previously selected import source.

Submission is blocked when:

- the preview shows a model different from the payload model;
- a selected chain no longer exists;
- source registration is incomplete;
- an old-run artifact is unavailable;
- the backend cannot consume the resolved input type;
- an explicit source transformation has not completed.

## 8. Scientific controls

### 8.1 Backend selection

Use one authoritative backend control.

The existing dropdown-plus-card duplication must be removed. Backend cards may remain because they explain the scientific mode.

Supported modes remain based on the installed CM contract:

- Protenix v2 ensemble generation
- ConforNets
- External or imported structure analysis

The UI must not advertise a backend or setting that the installed runtime does not support.

### 8.2 Backend-owned form state

Each backend owns its valid form state.

Switching backends must:

- preserve valid values for the backend when safe;
- replace invalid defaults with valid backend defaults;
- clear stale references that the new backend cannot consume;
- explain any cleared value;
- recompute planned candidate cardinality;
- rerun preflight validation.

The current five-seed default must not remain in the ConforNets explicit-seed field when that path requires exactly one seed.

### 8.3 Control classes

Every current launcher field must be assigned to one class before implementation.

#### Ordinary operator controls

These controls remain visible when supported by the active backend:

- workflow mode;
- task or scientifically meaningful generation mode;
- target scope;
- candidate cardinality controls;
- explicit seed or seed count with backend-correct semantics;
- MSA choice;
- template choice;
- required reference selection;
- supported confidence or evaluation choice when it changes delivered scientific output.

#### Advanced scientific controls

These controls may expand inline in the scientific-controls card:

- recycles;
- diffusion steps;
- saved-step policy when the installed backend supports it;
- ConforNets runs and samples;
- optimization-step limit;
- learning rate;
- gradient clipping;
- task-specific ConforNets network count when it is a real task parameter.

Every advanced control needs a user-facing explanation and typed bounds.

Exact Protenix cycle and step overrides are the only launcher runtime overrides in this revision. The API rejects those overrides for ConforNets and external import, including direct callers that bypass the launcher.

#### Derived read-only values

Show these values in compact summaries when useful:

- effective backend implementation;
- model and checkpoint identity;
- config identity;
- planned candidate count;
- expected output classes;
- effective analysis policy;
- storage estimate when reliable;
- source IDs and hashes under provenance details.

#### Hidden internal fields

Do not present these as ordinary decisions:

- singleton checkpoint selectors;
- raw registry handles;
- raw request JSON;
- benchmark identifiers;
- test-case identifiers;
- scheduler policy;
- backend-contract field names;
- duplicate backend values;
- server-owned canonical analysis thresholds, which remain read-only and fail closed on API override;
- internal artifact paths.

If the installed runtime provides several approved checkpoints with scientifically distinct behavior, checkpoint selection may become an advanced typed choice. A singleton remains derived.

### 8.4 Typed UI and API parity

Every operator-facing scientific setting must have:

- a typed UI control;
- a typed frontend request field;
- an equal typed agent/API field;
- validation in the API domain boundary;
- persistence in the canonical request;
- a read-only effective value in the pre-submit summary.

UI-only settings and API-only scientific settings are prohibited.

### 8.5 Cardinality semantics

The launcher must show the exact planned candidate count from backend-native axes.

- Protenix cardinality must derive from the installed seed and sample contract.
- ConforNets MSE cardinality must derive from `reference count × runs × samples`.
- A ConforNets network-count field must not be presented as MSE output cardinality.
- External import cardinality must derive from the selected authoritative structures.

A one-coordinate plumbing run must be labeled as one coordinate. It must not be called an ensemble.

## 9. Pre-submit summary

The bottom full-width card is mandatory.

It must show:

- run name;
- notes-present state;
- source kind and human-readable identity;
- registered source identity under provenance detail;
- resolved model or sample context;
- backend-consumed chains and retained context;
- workflow mode and task;
- effective model, checkpoint, and config;
- exact planned candidate count;
- important scientific controls;
- expected output classes;
- storage estimate when reliable;
- validation state.

The launch action remains disabled until all required fields resolve.

The summary must be generated from the same normalized request state used for submission. It must not be a separately formatted approximation.

## 10. Validation and failure behavior

### 10.1 Validation timing

Validate during source selection, when the backend changes, when resolved source context changes, and before submission.

### 10.2 Error presentation

- Attach field errors to the owning card and control.
- Place source-resolution errors in the source or preview card.
- Keep a compact blocking-error list in the pre-submit summary.
- Preserve server error receipts without replacing them with generic success or fallback messages.

### 10.3 Fail-closed rules

The launcher must not:

- silently select the first model when explicit selection is required;
- silently select a chain from a multi-chain structure;
- silently strip non-protein entities;
- silently convert PDB to mmCIF in the browser;
- silently replace a missing old-run artifact;
- silently change backend;
- silently substitute a checkpoint or runtime;
- submit stale source or chain state;
- create a request when source registration failed.

## 11. Compatibility

- Existing CM requests and results remain readable.
- Existing registered CM sources remain valid when their bytes are available.
- Existing submit payloads remain accepted or receive an explicit versioned compatibility path.
- The launcher revision must not change canonical candidate ordering or result semantics.
- Historical requests with no notes return an empty or absent notes value according to the final schema choice.
- Retry behavior must preserve original input selections and notes.

## 12. Implementation boundary

The implementation may change:

- `ConformationalMappingLauncher.tsx`;
- launcher-specific components extracted from it;
- shared selector primitives when reuse requires a bounded generalization;
- `conformationalMappingApi.ts` request types;
- CM request schemas and persistence required for notes and selected-input context;
- focused tests for the changed launcher and API contracts.

The implementation must avoid:

- replacing the CM router or request system;
- copying whole de novo workflow components;
- modifying result-view composition;
- introducing a second structure cache;
- introducing a second content-addressed source registry;
- changing scientific model defaults without explicit contract evidence;
- unrelated frontend cleanup.

## 13. Acceptance criteria

### 13.1 Layout

- The CM launcher uses the available workflow-page width on a wide desktop.
- Run record and scientific controls appear side by side where readable.
- Source browser and structure preview appear side by side where readable.
- The page uses normal vertical scrolling.
- The launcher does not render as a narrow middle-third column.
- Narrow screens stack the cards in the specified order.
- No supported viewport has unintended horizontal scrolling.

### 13.2 Source selection

- Upload, Your Runs, RCSB, and Cached are reachable in the launcher.
- Presets remain absent because this CM path has no authoritative preset adapter.
- A saved run with several structures requires an artifact choice.
- An RCSB entry with several models fails closed until an explicit backend-native transformation exists.
- A multi-chain complete-context source shows all retained chains without implying a chain filter.
- The selected source and resolved model, sample, and chain context remain visible in the preview and summary.
- The submitted request contains the same selected identity.

### 13.3 Run record and persistence

- Run name and notes appear at the top.
- Notes persist in the canonical request and request projection.
- Relevant run details appear as compact derived values.
- Retry preserves the original run notes and selected-input context.
- Existing requests without notes remain readable.

### 13.4 Controls

- One backend control exists.
- Singleton checkpoint selectors are absent.
- Internal registry and benchmark fields are absent from ordinary configuration.
- Advanced model settings use typed controls with bounds and explanations.
- Each operator-facing setting has equal UI and API support.
- Switching to ConforNets produces a valid seed state.
- Planned cardinality matches backend-native axes.

### 13.5 Submission

- The pre-submit summary derives from normalized request state.
- Submission remains blocked while source, model, chain, or backend requirements are unresolved.
- The created request persists the displayed source and scientific configuration.
- Registration or validation failure cannot create a partially authoritative request.

### 13.6 Scope adherence

- Result-view files remain behaviorally unchanged.
- No candidate, overlay, landscape, or post-run analysis feature is added.
- No deployment or service restart occurs under this work package without new authority.

## 14. Verification evidence

The isolated implementation passed these checks:

- 16 focused frontend launcher and rendered-behavior tests;
- 183 focused API, routing, schema, import-normalization, persistence, and trust-boundary tests;
- frontend TypeScript checking;
- focused ESLint checks on changed frontend files;
- a production frontend build;
- `git diff --check`;
- browser acceptance through the normal `/submit?template=conformational_mapping` shell;
- wide-workspace inspection of both card rows and the full-width summary;
- responsive inspection at a 900-pixel viewport;
- browser-console inspection with no JavaScript errors.

The result viewer remained unchanged. No deployment, service restart, push, or merge occurred under this work package.

## 15. Rough-sketch disposition

The selected card-grid render is a layout reference. It does not define final typography, colors, dimensions, example protein content, or field values.

The binding concepts are:

- full-width page use;
- side-by-side run record and scientific controls;
- side-by-side source browsing and selected-structure preview;
- normal vertical scrolling;
- full-width pre-submit summary;
- revision of the existing launcher.
