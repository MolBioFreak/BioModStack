# MolBioToolkit Production Readiness Spec

Date: 2026-04-14
Status: Draft
Priority: Highest
Owner: Molecular Toolkit / Platform

## Executive Summary

This document scopes the five highest-value changes needed to move the Molecular Biology Toolkit from a strong alpha into a production-ready daily-use tool.

These are not cosmetic upgrades. They are the workstreams that most directly determine whether the toolkit can replace or stand beside SnapGene or Geneious for real plasmid and RNA work.

The five workstreams are:

1. assembly workflows v2
2. alignment and sequencing validation v2
3. feature and annotation model v2
4. primer and oligo QC v2
5. workspace, history, and import/export v2

This document is intentionally execution-oriented. Each workstream defines:

- product objective
- exact system changes required
- frontend changes
- backend and data-model changes
- implementation sequence
- acceptance criteria
- test plan

This spec builds on, but does not replace, the broader alpha document in [MolBioToolkit_Alpha_Release_Spec_2026-04-12.md](./MolBioToolkit_Alpha_Release_Spec_2026-04-12.md).

## Why This Exists

The current toolkit has crossed the threshold from prototype to real alpha:

- the main workspace is unified in [platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx](../platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx)
- construct acquisition and search are real in [platform/frontend/src/components/MolBioToolkit/MolecularInputModal.tsx](../platform/frontend/src/components/MolBioToolkit/MolecularInputModal.tsx)
- pairwise alignment exists in [platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx) and [platform/api/services/sequence_alignment.py](../platform/api/services/sequence_alignment.py)
- RNA folding exists in [platform/frontend/src/components/MolBioToolkit/panels/RnaStructurePanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/RnaStructurePanel.tsx) and [platform/api/services/rna_structure.py](../platform/api/services/rna_structure.py)
- primer Tm and candidate design exist in [platform/frontend/src/components/MolBioToolkit/panels/PrimerPanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/PrimerPanel.tsx) and [platform/api/routers/molbio_ops.py](../platform/api/routers/molbio_ops.py)

That said, the current product still has obvious gaps versus mature molecular editors:

- assembly routes are disabled because fake logic was removed, not replaced
- alignment is still pairwise-first and does not yet cover sequencing validation workflows
- the feature model is still too simple for segmented, provenance-rich annotation editing
- primer design does not yet include the QC and specificity checks users expect
- history, compare, lineage, and export are still too shallow for long-lived project work

## Production Target

The component should be considered production-ready when a user can:

1. create or import a plasmid, RNA, primer, amplicon, or derived construct without type corruption
2. assemble new constructs through validated cloning and assembly workflows
3. compare a construct to reads, fragments, references, and alternate versions with confidence
4. design, QC, and order primers without leaving the product for basic validation
5. curate annotations with provenance, qualifiers, and reviewable merges
6. branch, compare, save, export, reload, and audit a construct history without losing meaning

## Current-State Constraints

These constraints are already visible in the current code and should be treated as design inputs:

- assembly routes are intentionally disabled in [platform/api/routers/molbio_ops.py](../platform/api/routers/molbio_ops.py)
  because previous implementations were not trustworthy
- alignment is currently pairwise nucleotide alignment only in
  [platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx)
  and [platform/api/services/sequence_alignment.py](../platform/api/services/sequence_alignment.py)
- RNA folding currently requires canonical `A/U/G/C`, and length limits are enforced in
  [platform/api/services/rna_structure.py](../platform/api/services/rna_structure.py)
- export is limited to `GenBank` and `FASTA` in
  [platform/frontend/src/components/MolBioToolkit/ExportDropdown.tsx](../platform/frontend/src/components/MolBioToolkit/ExportDropdown.tsx)
- undo and redo are still whole-document snapshot based in
  [platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts](../platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts)

These are not defects to hide. They should drive the implementation sequence below.

## Design Principles

- no fake simulation outputs
- no hidden primary workflows
- no silent DNA or RNA coercion
- provenance first for every derived object
- validated operations before convenience
- same construct should render, save, export, and reload without semantic drift
- a heavy construct should remain usable without the UI collapsing into lag or ambiguous state

## Comparator Baseline

SnapGene currently documents support for:

- pairwise DNA and protein alignment
- multiple sequence alignment
- Sanger alignment to reference
- whole-plasmid alignment to reference
- de novo assembly of Sanger sequences
- ligation, Gibson, Golden Gate, Gateway, TOPO, and related cloning workflows
- agarose gel simulation
- custom feature types and segmented features
- history view and text export
- RNA import and RNA alignment
- predicted secondary structure of single-stranded sequences and primers

Sources used:

- SnapGene User Guide:
  https://support.snapgene.com/hc/en-us/categories/10304176221716-SnapGene-User-Guide
- SnapGene alignment guide:
  https://support.snapgene.com/hc/en-us/articles/10384298841364-Align-Sanger-Reads-to-a-Reference-Sequence
- SnapGene primer secondary structure:
  https://support.snapgene.com/hc/en-us/articles/20584126491796-View-the-Predicted-Secondary-Structure-of-a-Primer
- SnapGene history view:
  https://support.snapgene.com/hc/en-us/articles/10384025031700-View-Export-Print-or-Copy-History-as-Text

Inference from those docs:

- SnapGene is still ahead on assembly, sequencing validation, history, and multi-file alignment
- the current molbio toolkit is already competitive on native RNA thermodynamic analysis depth

## Workstream 1: Assembly Workflows V2

### Objective

Replace disabled assembly endpoints with validated, first-class assembly systems for routine cloning work.

### Product Outcome

The toolkit must support:

- restriction ligation
- Gibson assembly
- Golden Gate assembly
- insert into vector
- multi-fragment assembly
- circularization where valid
- saved product provenance

### Current Problem

The current routes are intentionally disabled in [platform/api/routers/molbio_ops.py](../platform/api/routers/molbio_ops.py):

- `ligate`
- `gibson`
- `golden-gate`

This is correct behavior for now, but it leaves a major product gap.

### Exact Changes Required

#### Backend architecture

Create a dedicated assembly service layer:

- `platform/api/services/assembly/common.py`
- `platform/api/services/assembly/ligation.py`
- `platform/api/services/assembly/gibson.py`
- `platform/api/services/assembly/golden_gate.py`
- `platform/api/services/assembly/types.py`

Responsibilities:

- fragment normalization
- topology-aware fragment extraction
- overhang compatibility checks
- overlap validation
- orientation resolution
- circular product resolution
- provenance bundle generation

#### Data model changes

Derived sequences and operation payloads must persist:

- source fragment ids
- source fragment spans
- orientation per fragment
- enzyme set used
- overhangs or overlaps used
- junction sequences
- validation warnings

Add or standardize `operation_params` payload shape on derived sequence save paths.

#### API changes

Replace current 501 routes with validated routes:

- `POST /api/molbio/assembly/ligation/simulate`
- `POST /api/molbio/assembly/gibson/simulate`
- `POST /api/molbio/assembly/golden-gate/simulate`
- `POST /api/molbio/assembly/<mode>/save`

The current `/ligate`, `/gibson`, and `/golden-gate` routes should become compatibility wrappers or be deprecated cleanly.

Each response must include:

- product sequence
- product topology
- fragment order
- junction metadata
- warnings and failures
- optional exact cut or overlap coordinates

#### Frontend changes

Add a dedicated `Assembly` workspace rather than burying this in existing panels.

New UI surfaces:

- fragment basket
- vector and insert roles
- assembly mode selector
- enzyme and overhang configuration
- overlap preview
- junction preview
- product preview on the map
- validation sidebar

Direct selection actions should be able to send a region into the assembly basket.

#### Algorithms and validation rules

Ligation:

- detect blunt versus sticky ends
- validate compatible end pairing
- preserve exact junction sequence
- handle vector self-ligation only if chemically valid

Gibson:

- support minimum and preferred overlap lengths
- allow mismatch-tolerant overlap thresholds only if explicitly configured
- score ambiguous assemblies and reject conflicting graph solutions

Golden Gate:

- resolve Type IIS recognition sites and exact cut offsets
- validate generated four-base overhangs
- support user-defined enzyme and cutter tables
- reject assemblies with ambiguous or repeated incompatible overhang paths unless user explicitly chooses a path

### Implementation Sequence

1. build shared fragment and provenance types
2. implement ligation first because it has the simplest exact-end model
3. implement Golden Gate second because it depends on accurate cut-offset logic
4. implement Gibson third with graph-based overlap assembly
5. add frontend assembly workspace and product preview
6. persist derived products and lineage

### Acceptance Criteria

- no route returns a product unless compatibility validation passes
- every assembly product can be traced back to exact source fragments and junctions
- circular and linear products are explicit
- Golden Gate output reflects real cut offsets, not recognition-site deletion shortcuts
- failures are explanatory, not generic

### Test Matrix

- blunt-end ligation
- sticky-end ligation with compatible ends
- incompatible ends rejected
- vector self-ligation allowed or rejected correctly
- Gibson one-insert
- Gibson multi-insert
- Golden Gate with one insert
- Golden Gate multi-fragment
- repeated overhang ambiguity
- origin-wrapping fragments on circular templates

## Workstream 2: Alignment and Sequencing Validation V2

### Objective

Turn the current pairwise alignment panel into a complete construct comparison and sequencing validation system.

### Product Outcome

The user should be able to:

- align a fragment or full construct to a reference
- align a whole plasmid sequence to a circular reference
- align AB1 traces and inspect chromatograms
- compare alternate construct versions
- inspect variant calls directly on the map
- rebuild a consensus or derived construct from aligned evidence
- run multiple sequence alignment on related constructs or inserts

### Current Problem

The current alignment panel is a solid v1:

- explicit placement, local, and global modes
- strand control
- circular reference handling
- rendered alignment blocks
- variant calls

This is implemented in [platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/AlignmentPanel.tsx) and [platform/api/services/sequence_alignment.py](../platform/api/services/sequence_alignment.py).

However, it is still missing:

- sequencing trace workflows
- MSA
- scoring controls in the UI
- protein alignment
- consensus replacement workflow
- persistent comparison sessions
- stronger map-level discrepancy visualization

### Exact Changes Required

#### Backend architecture

Split alignment into three engines:

1. `pairwise_small`
   use current `Bio.Align.PairwiseAligner` path for small DNA or RNA compares

2. `pairwise_large`
   add `minimap2` integration for whole-plasmid, long-fragment, and long-read style comparisons

3. `msa`
   add a multiple alignment backend, preferably via MAFFT if available, with a strict fallback policy

Recommended files:

- `platform/api/services/alignment/pairwise.py`
- `platform/api/services/alignment/minimap.py`
- `platform/api/services/alignment/msa.py`
- `platform/api/services/alignment/traces.py`

#### Sequencing trace support

Add AB1 or Sanger support using Biopython trace parsing where feasible.

Required outputs:

- aligned read span
- orientation
- trimmed low-quality ends
- mismatch and indel summary
- consensus proposal
- per-position support summary

#### API changes

Add:

- `POST /api/molbio/alignment/pairwise`
- `POST /api/molbio/alignment/msa`
- `POST /api/molbio/alignment/sanger`
- `POST /api/molbio/alignment/consensus`

The existing `/alignment` route can stay as the pairwise entry point during migration.

#### Frontend changes

Expand `AlignmentPanel` into tabbed modes:

- `Compare`
- `Sanger`
- `MSA`
- `Consensus`

UI changes:

- expose alignment scoring presets and advanced controls
- show circular-reference rotation diagnostics
- add on-map lollipop or discrepancy overlays instead of only one span highlight
- add side-by-side compare mode for construct versus construct
- add read list and trace expansion for Sanger mode
- add consensus commit action for supported evidence sets

#### Visual and model changes

Persist alignment sessions as analysis artifacts:

- source file names
- reference id or construct id
- engine used
- settings used
- timestamp
- whether the result was accepted into a new derived construct

### Implementation Sequence

1. refactor current pairwise service into dedicated alignment module
2. expose scoring settings in UI for existing pairwise engine
3. add whole-plasmid circular compare flow
4. add AB1 import and Sanger review
5. add consensus generation and derived construct save path
6. add MSA view and backend

### Acceptance Criteria

- whole-plasmid comparisons produce stable circular-reference alignments
- Sanger traces can be aligned, trimmed, reviewed, and summarized
- MSA can compare at least a small construct set without UI collapse
- variant calls can be written back as curated annotations
- alignment provenance is persisted

### Test Matrix

- fragment versus full plasmid placement
- circular whole-plasmid compare
- reverse-complement only match
- repeated-region compare
- AB1 read with mismatches
- AB1 read with indel
- multi-construct MSA
- consensus replacement creates correct derived sequence

## Workstream 3: Feature and Annotation Model V2

### Objective

Replace the current single-span, lightweight feature system with a richer annotation model suitable for real plasmid curation.

### Product Outcome

The user should be able to:

- edit full GenBank-style qualifiers
- create segmented features
- define and reuse custom feature types
- review imported or auto-detected annotations before merge
- track provenance and confidence for feature origin
- compare annotation sets across versions

### Current Problem

The current feature panel is much better than the original, but the model is still too thin for production annotation work.

Current strengths:

- structured qualifier editor in [platform/frontend/src/components/MolBioToolkit/panels/FeaturePanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/FeaturePanel.tsx)
- feature dedupe utilities in [platform/frontend/src/components/MolBioToolkit/utils/features.ts](../platform/frontend/src/components/MolBioToolkit/utils/features.ts)

Current gaps:

- features are still effectively single interval spans
- custom feature type management does not exist
- import normalization can flatten richer source data
- annotation review is direct-append oriented rather than merge-review oriented

### Exact Changes Required

#### Data model changes

Extend the feature model to support:

- `segments: Array<{ start, end }>`
- `qualifiers: Record<string, string | string[]>`
- `source`
- `source_id`
- `confidence`
- `review_status`
- `custom_type_id`
- `is_translated`

The current `notes` field can be retained as backward-compatible storage during migration, but the runtime model should stop treating qualifiers as ad hoc notes.

#### Backend changes

Update nucleotide sequence save and load logic to preserve segmented features and qualifiers without flattening.

Add endpoints for:

- custom feature type CRUD
- annotation review merge
- batch feature apply

#### Frontend changes

Feature panel additions:

- segmented feature editor
- custom feature type picker
- qualifier templates
- provenance badge
- review status badge
- merge or reject flow for imported or auto-detected features

Viewer changes:

- render segmented features cleanly in linear view
- avoid collapsing multi-part annotations into misleading spans
- allow feature hiding by category, type, origin, and review state

#### Auto-annotation changes

Auto-annotation should create review candidates rather than immediately mutating the active feature set.

Required workflow:

1. detect candidates
2. diff against existing features
3. show conflicts and duplicates
4. accept, reject, or merge
5. persist accepted annotations with provenance

### Implementation Sequence

1. define v2 feature schema and migration path
2. update API serialization and import normalization
3. update viewer rendering for segmented features
4. add custom feature types
5. add annotation review queue and merge UI

### Acceptance Criteria

- segmented features round-trip through save and reload
- qualifiers persist through GenBank export where the format supports them
- imported and auto-detected annotations can be reviewed before merge
- custom feature types can be created and reused

### Test Matrix

- segmented intron or exon-like feature
- origin-wrapping feature
- auto-annotation duplicate detection
- custom feature type import and reuse
- qualifier-rich GenBank export and reimport

## Workstream 4: Primer and Oligo QC V2

### Objective

Upgrade primer design from candidate generation plus Tm into a fuller QC and specificity system.

### Product Outcome

The user should be able to:

- design PCR, sequencing, mutagenesis, and RNA-aware primers
- inspect hairpins, homodimers, heterodimers, and risky self-complementarity
- detect multi-binding or off-target binding on the active construct
- support circular-template origin-wrapping candidates
- produce primer tables ready for ordering

### Current Problem

Current strengths:

- real Tm model selection and chemistry settings
- pair ranking
- overhang support
- candidate generation in [platform/frontend/src/components/MolBioToolkit/panels/PrimerPanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/PrimerPanel.tsx)
  and [platform/api/routers/molbio_ops.py](../platform/api/routers/molbio_ops.py)

Current gaps:

- no hairpin or dimer analysis
- no heterodimer pair QC
- no explicit off-target review surface
- circular-template origin-wrapping candidates are still excluded on the backend
- no real ordering export table

### Exact Changes Required

#### Backend changes

Add a dedicated primer QC service:

- `platform/api/services/primer_qc.py`

Recommended engine choices:

- use `primer3-py` and the underlying thermodynamic routines for
  - hairpin
  - homodimer
  - heterodimer
  - primer pair penalty refinement

Retain the current Tm service for configurable algorithm support if it stays superior for model selection. Do not silently downgrade.

Add specificity scanning:

- exact and near-exact binding-site search on the current construct
- configurable mismatch rules
- report all viable binding loci, not only the best locus

Add circular candidate support:

- support origin-wrapping annealing windows on circular templates
- score them correctly for product size and placement

#### API changes

Add:

- `POST /api/molbio/primer-qc`
- `POST /api/molbio/primer-pair-qc`
- `POST /api/molbio/primer-specificity`
- `POST /api/molbio/primer-order-table`

Enhance `/primer-design` to optionally include:

- hairpin and dimer summaries
- off-target summary
- multi-binding summary

#### Frontend changes

Primer panel additions:

- `QC` tab
- per-primer structure summary
- pairwise heterodimer matrix
- specificity hit list
- circular-template candidate toggle
- design modes:
  - PCR
  - sequencing
  - mutagenesis
  - RT-PCR or RNA
  - cloning or overhang

Result display additions:

- pass or warn badges
- off-target hit count
- 3-prime risk markers
- order table export

#### RNA-specific extensions

When sequence type is RNA:

- preserve RNA alphabet in oligo calculations where appropriate
- support RNA-aware primer and probe modes explicitly
- surface when a DNA oligo is being designed against an RNA template

### Implementation Sequence

1. add primer secondary-structure QC backend
2. add specificity scan backend
3. enable circular-template origin-wrapping candidates
4. add frontend QC tab and result badges
5. add ordering export

### Acceptance Criteria

- primer QC reports hairpin and dimer risks from a validated engine
- multi-binding primers are not silently accepted as clean
- circular-template candidates can be designed and scored
- ordering table can be exported cleanly

### Test Matrix

- strong hairpin candidate
- strong homodimer candidate
- heterodimer-prone pair
- multi-binding primer on repetitive plasmid
- circular wrap primer pair
- RNA-targeted design mode

## Workstream 5: Workspace, History, and Import/Export V2

### Objective

Turn the toolkit from a powerful single-session editor into a durable multi-construct workspace with reliable history and exchange.

### Product Outcome

The user should be able to:

- work across multiple constructs at once
- branch or derive alternate versions
- inspect operation lineage
- compare constructs side by side
- export maps, tables, and analysis artifacts
- reload exported files without major semantic loss

### Current Problem

Current strengths:

- construct library and input modal are solid
- undo and redo exist

Current gaps:

- undo and redo are still whole-snapshot based in [platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts](../platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts)
- there is no persisted history graph
- there is no multi-tab workspace
- export is limited to `GenBank` and `FASTA` in [platform/frontend/src/components/MolBioToolkit/ExportDropdown.tsx](../platform/frontend/src/components/MolBioToolkit/ExportDropdown.tsx)
- there is no publication-style map export, primer table export, or digest export

### Exact Changes Required

#### Workspace changes

Add a multi-document workspace model:

- open constructs as tabs
- keep per-tab panel state
- allow pinning a reference construct
- allow side-by-side compare layout
- allow derived construct open-in-new-tab behavior

#### History and lineage changes

Introduce a persisted lineage model:

- parent sequence id
- operation type
- operation payload
- timestamp
- user or session provenance

Add a `History` view with:

- graph or list mode
- compare against parent
- open historical version
- export history text

Undo or redo should remain local and immediate, but persisted lineage should become the audit layer.

#### Import and export changes

Expand export targets to include:

- GenBank
- FASTA
- primer table CSV
- feature table CSV
- alignment report text
- map PNG or SVG
- digest fragment table CSV
- history text

Import diagnostics must be added for:

- unsupported feature segmentation
- dropped qualifiers
- type coercions
- unsupported metadata

#### Round-trip fidelity work

Add round-trip tests for:

- SnapGene `.dna`
- GenBank
- FASTA

Preserve where format permits:

- sequence type
- topology
- feature qualifiers
- colors
- primers
- notes
- accession or source metadata

### Implementation Sequence

1. add workspace tab model and open-document state
2. add persisted lineage schema and history API
3. add compare view and open-parent flow
4. add expanded export surfaces
5. add import diagnostics and round-trip matrix

### Acceptance Criteria

- multiple constructs can remain open without state collision
- derived constructs preserve lineage
- history is auditable beyond local undo and redo
- core export surfaces exist beyond `GenBank` and `FASTA`
- import and reimport do not silently lose major supported metadata

### Test Matrix

- open three constructs simultaneously
- derive a child construct from parent
- compare child versus parent
- export history text
- export map graphic
- export primer table
- SnapGene import then GenBank export then reimport

## Cross-Workstream Architecture Changes

These changes should be treated as shared platform work, not left to each panel independently.

### 1. Provenance schema

Standardize provenance payloads for:

- alignment results
- assembly results
- auto-annotation candidates
- imported evidence tracks
- derived constructs

### 2. Background jobs

Longer-running tasks should be able to run asynchronously:

- MSA
- large alignment jobs
- multi-fragment assembly validation
- large RNA folding jobs if bounds expand

### 3. Viewer overlay contract

Define one shared overlay model for:

- alignment spans
- variant calls
- digest fragments
- primer hits
- evidence tracks
- annotation review candidates

### 4. Notification and error pattern

Replace broad use of `alert()` with structured in-app notifications and task-level error panels.

## Recommended Execution Order

This order is optimized for dependency management and user value.

### Phase 0: Stabilizers

- replace disruptive `alert()` paths in molbio workspace
- standardize provenance models
- define shared overlay primitives

### Phase 1: Assembly Foundations

- fragment model
- provenance model
- ligation
- Golden Gate
- Gibson

### Phase 2: Alignment and Primer QC

- pairwise v2 UI
- whole-plasmid compare
- Sanger trace workflows
- primer QC
- specificity scanning

### Phase 3: Feature Model

- segmented features
- custom feature types
- review workflow

### Phase 4: Workspace and History

- multi-tab workspace
- persisted lineage
- compare and history views

### Phase 5: Export and Round-Trip

- map graphics
- tables
- history export
- import diagnostics
- format fidelity matrix

## Acceptance Gates

Do not call this production-ready until these gates are met.

### Gate 1: Trust

- no fake assembly outputs
- no silent type coercion
- no silent metadata loss for supported fields

### Gate 2: Workflow Completion

- user can import, edit, align, annotate, design primers, assemble, and export without leaving the tool for the core workflow

### Gate 3: Recoverability

- user can compare against parent, inspect history, and reopen prior states

### Gate 4: Fidelity

- import and export round trips are documented and verified

### Gate 5: Performance

- large, annotation-heavy constructs remain responsive

## Detailed Test Plan

### Assembly

- validated positive and negative cases for each assembly mode
- exact junction and overhang verification

### Alignment

- pairwise
- circular whole-plasmid
- repetitive sequence
- Sanger trace
- consensus generation
- MSA

### Annotation

- segmented features
- qualifier preservation
- review workflow
- duplicate handling

### Primer QC

- secondary-structure checks
- specificity checks
- circular wrap candidates
- RNA-aware cases

### Workspace and Exchange

- multi-tab state separation
- lineage persistence
- compare view correctness
- map and table exports
- SnapGene and GenBank round trip

## Risks

### Risk 1: Assembly correctness is harder than UI work

Mitigation:

- do backend validation first
- no assembly route returns success unless compatibility is exact and explainable

### Risk 2: Alignment scope can sprawl

Mitigation:

- ship pairwise and whole-plasmid compare before MSA and full trace ecosystem polish

### Risk 3: Feature model migration can break imports

Mitigation:

- version the feature schema
- keep backward compatibility during migration

### Risk 4: Primer QC can become tool-sprawl

Mitigation:

- keep Tm, QC, and specificity in one coordinated primer system instead of separate utilities

### Risk 5: History can become confusing if local undo and persisted lineage are mixed badly

Mitigation:

- keep local undo for in-session edits
- keep lineage for saved operations and cross-version audit

## Immediate Next Action

Start with a short architecture tranche, not a UI tranche.

1. define shared provenance and derived-operation payloads
2. design assembly service contracts and validation rules
3. design alignment v2 API split
4. design feature schema v2 migration
5. choose and integrate primer QC backend engine

That sequencing keeps the product from accumulating more UI on top of incomplete core semantics.
