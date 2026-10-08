# MolBioToolkit Alpha Revision Set Spec

Date: 2026-04-12
Last Updated: 2026-04-13
Status: Draft
Priority: High

## Executive Summary

This spec expands the original alpha target into a larger revision set for the molecular biology toolkit in BioModStack.

The goal is not to cosmetically improve the current editor. The goal is to move the toolkit toward practical parity with the day-to-day plasmid, primer, digest, annotation, and sequence-analysis workflows users expect from tools like SnapGene and Geneious, while keeping the BioModStack acquisition and workflow patterns consistent with the stronger protein-side systems already in the product.

This revision set is driven by three realities:

- the acquisition layer is still thinner than the protein-side selectors
- restriction analysis and digest UX are still awkward and split across disconnected controls
- the analytics, annotation, primer-design, and comparison surfaces are still too shallow for a credible alpha release

## Product Goal

For alpha, a user should be able to:

1. acquire a molecular asset from local library, import, paste/build, demo, preset, and external-search flows through a unified entry surface
2. work with plasmids, linear DNA, ssDNA, dsDNA, ssRNA, dsRNA, primers, inserts, and derived products without silent type coercion
3. search local and external molecular data the way protein workflows already search presets, runs, and RCSB-backed assets
4. analyze a construct through intuitive restriction analysis, digest simulation, primer/PCR workflows, annotation tools, translation tools, and sequence analytics
5. edit, annotate, save, compare, export, and reimport constructs without corrupting coordinates, qualifiers, provenance, or topology
6. trust that larger plasmids and annotation-heavy constructs remain responsive and recoverable

## Why This Revision Exists

Current molbio capability is meaningfully better than where it started, but still below the bar for an alpha users can trust as a first-class design environment.

What already exists:

- a working central editor and viewer in [platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx](../platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx)
- saved nucleotide sequence CRUD in [platform/api/routers/nucleotide_sequences.py](../platform/api/routers/nucleotide_sequences.py)
- primer library CRUD in [platform/api/routers/molbio_ops.py](../platform/api/routers/molbio_ops.py)
- digest, PCR, mutagenesis, Gibson, ligation, and Golden Gate backend routes in [platform/api/routers/molbio_ops.py](../platform/api/routers/molbio_ops.py)
- import/export and viewer improvements from the recent hardening passes

What still fails at product level:

- acquisition UX is still thinner than protein-side selectors
- local construct search is still not positioned as a real searchable database layer
- RNA support is still partially constrained by DNA-first assumptions
- restriction analysis is usable but not intuitive
- the GC track exists but the analytics surface is still underpowered
- annotation and feature editing are still too shallow
- primer handling is more simulation-ready than design-ready
- workspace comparison, lineage, and publication export are still missing
- there is no completed formal bug sweep or alpha acceptance matrix for the expanded workflow set

## Current-State Findings

### 1. Protein-side workflows already have a real acquisition layer

The protein stack already uses source-selection patterns the molecular side still lacks:

- tabbed acquisition in [platform/frontend/src/components/TargetAntigenSelector.tsx](../platform/frontend/src/components/TargetAntigenSelector.tsx)
- typed multi-component builder in [platform/frontend/src/components/LigandSelector.tsx](../platform/frontend/src/components/LigandSelector.tsx)
- library modal in [platform/frontend/src/components/SequenceManagerModal.tsx](../platform/frontend/src/components/SequenceManagerModal.tsx)
- external search/fetch/cache in [platform/api/routers/rcsb.py](../platform/api/routers/rcsb.py)

### 2. MolBioToolkit is still editor-first

The molecular side has improved, but it is still primarily centered on loading one construct into one editor and then operating on it.

That is not enough for alpha parity with protein workflows or with mature molecular design tools.

### 3. Restriction analysis UX is split and unintuitive

Current restriction behavior is divided between:

- a coarse `Restriction Sites` visibility toggle in [platform/frontend/src/components/MolBioToolkit/VisibilityPanel.tsx](../platform/frontend/src/components/MolBioToolkit/VisibilityPanel.tsx)
- a long enzyme list plus digest controls in [platform/frontend/src/components/MolBioToolkit/panels/DigestPanel.tsx](../platform/frontend/src/components/MolBioToolkit/panels/DigestPanel.tsx)

Observed issues:

- the visibility layer only exposes an on or off cut-site overlay, not meaningful cutter classes
- the digest panel mixes two different concepts: viewer overlay selection and digest composition
- digest composition currently relies on a right-click gesture that is not discoverable enough for a core workflow
- the enzyme list is grouped by hardcoded categories rather than by task-oriented filters
- there are no fast filters for unique cutters, dual cutters, non-cutters, nicking enzymes, overhang classes, or selection-aware cutters
- there is no explicit restriction-analysis workspace, fragment table, end-type table, or gel-style lane preview

### 4. The analytics surface is underpowered

The current analytics layer is effectively a single GC track:

- rendered by [platform/frontend/src/components/MolBioToolkit/GCContentTrack.tsx](../platform/frontend/src/components/MolBioToolkit/GCContentTrack.tsx)
- toggled in [platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx](../platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx)

Observed issues:

- there is no track manager or stacked analytics view
- the current GC graph is readable, but still visually rough for long constructs
- there are no alternative metrics such as GC skew, complexity, or restriction density
- the chart is not yet a broader sequence-analysis surface
- the chart cannot currently double as a publication-grade export or analysis summary

### 5. Annotation and feature editing are still too thin

Feature editing is still closer to label management than full GenBank-style annotation editing.

Missing areas include:

- structured qualifiers
- annotation provenance review
- merge or reject flow for auto-annotation results
- reusable feature and component catalogs
- richer metadata persistence across import and export

### 6. Primer and PCR support are more operation-ready than design-ready

PCR logic is much stronger than before, but primer workflows still need a dedicated design and ranking surface rather than only a management or simulation surface.

Missing areas include:

- target-driven primer generation
- product-size and Tm constraints
- overhang template systems
- pair ranking and off-target review
- sequencing, mutagenesis, qPCR, and RT-PCR design modes

### 7. Workspace, comparison, and lineage support are still missing

The toolkit still behaves too much like a single-document editor instead of a molecular workspace.

Missing areas include:

- multi-tab construct work
- branch or clone variants
- sequence and map diffing
- side-by-side comparison
- operation lineage browsing

### 8. Import, export, and external search parity are still incomplete

Import and export are much better than before, but the toolkit still lacks:

- richer SnapGene and GenBank round-trip fidelity
- publication export graphics
- import diagnostics for lost metadata
- a real external molecular search layer analogous to the RCSB protein experience

### 9. Performance and state integrity still need a formal pass

The recent hardening pass reduced risk, but the toolkit still needs a comprehensive sweep across:

- history churn
- derived-state separation
- analytics rendering
- enzyme-site indexing
- feature-heavy and primer-heavy construct responsiveness

## Design Principles

This revision set should follow a few hard rules.

- no hidden expert-only gestures for primary workflows
- viewer overlays and simulation selections must be separate concepts
- analysis surfaces must be selection-aware
- topology and polymer type must be explicit, not inferred late
- provenance should be first-class for every derived result
- local search comes first, external adapters come second
- incomplete workflows must be labeled `Preview` instead of presented as finished

## Parity Target

This is not a goal to copy another product literally.

It is a goal to close the practical workflow gap:

- SnapGene-like parity for restriction analysis, digest simulation, primer design, feature editing, map export, and routine plasmid/RNA handling
- Geneious-like parity for searchable asset management, annotation review, sequence comparison, and multi-document workspace behavior
- BioModStack-native parity for acquisition UX, searchable databases, and workflow provenance across molecular and protein systems

## Scope

This larger revision set includes eleven major workstreams.

## Workstream 1: Molecular Acquisition, Library, and External Search

### Objective

Turn molecular acquisition into a first-class source-selection flow rather than a thin side rail.

### Deliverable

A unified `MolecularInputModal` or equivalent acquisition surface that becomes the canonical entry point for molecular assets.

### Required tabs

- `Library`
- `Import`
- `Paste / Build`
- `Primers / Oligos`
- `Runs / Outputs`
- `Templates / Presets`
- `External`

### Required supported entity classes

- plasmid
- linear DNA
- ssDNA
- dsDNA
- ssRNA
- dsRNA
- primer
- oligo
- insert
- transcript
- feature or component

### Searchable data sources

- local construct library
- primer and oligo library
- saved workflow outputs
- curated feature and component catalog
- external adapters

### External adapter targets

Alpha-ready or near-alpha candidates:

- Addgene-style plasmid search
- NCBI Gene or RefSeq or GenBank-backed sequence retrieval
- iGEM Registry-style parts search

Stretch targets:

- transcript-oriented adapters
- RNA-centric reference sources
- organism-specific sequence repositories

### Architecture requirement

External integration should follow the same pattern as the RCSB search and fetch architecture in [platform/api/routers/rcsb.py](../platform/api/routers/rcsb.py):

- query endpoint
- fetch or hydrate endpoint
- normalized result cards
- cached source payload
- local provenance persistence

## Workstream 2: Restriction Analysis and Digest UX

### Objective

Replace the current flat digest list and hidden right-click behavior with a dedicated restriction-analysis workspace that is intuitive for everyday plasmid work.

### Core design correction

Restriction visibility and digest simulation must stop being split across unrelated controls.

The user should have one coherent surface where they can:

- filter enzymes by the task they are trying to accomplish
- inspect where and how an enzyme cuts
- decide whether to overlay those cuts on the viewer
- add selected enzymes to a digest basket
- run a digest and inspect fragments, ends, and gel-like output

### Required enzyme metadata model

The current enzyme list only stores recognition sites. That is not enough for the UX the toolkit now needs.

Required metadata:

- `name`
- `recognition_site`
- `forward_cut_offset`
- `reverse_cut_offset`
- `overhang_type`
- `overhang_length`
- `is_nicking`
- `nicked_strand`
- `is_type_iis`
- `is_methylation_sensitive`
- `aliases`
- `category_tags`

### Required quick filters

The restriction browser must support one-click or near-one-click filtering for:

- non-cutters
- unique cutters
- double cutters
- 3+ cutters
- cutters in current selection
- cutters outside current selection
- cutters spanning the selected region
- blunt cutters
- 5-prime overhang cutters
- 3-prime overhang cutters
- nicking enzymes on the top strand
- nicking enzymes on the bottom strand
- Type IIS or Golden Gate enzymes
- rare cutters
- methylation-sensitive enzymes when metadata is available

### Required UI revisions

- replace the current long list with a true `Restriction Analysis` panel or workspace
- add a filter bar with chips or segmented toggles
- keep viewer-overlay selection separate from digest-basket selection
- remove right-click as a required interaction
- make cut count, recognition site, and end type visible without hover-only discovery
- allow enzyme search by name, site, end type, and category tags

### Required result surfaces

- fragment table with fragment size, start, end, wraps-origin flag, and enzyme provenance
- end-type table showing blunt or sticky ends when derivable from metadata
- color-coded fragment highlighting back onto the viewer
- gel-style lane preview with ladder presets
- digest recipe summary for single, dual, or multi-enzyme digest output

### Acceptance criteria

- a user can isolate unique cutters in one click
- a user can set up a dual digest without any hidden gesture
- a nicking enzyme can be filtered by strand class
- digest results visibly expose fragment sizes and end behavior
- the same restriction surface can support both quick viewer overlay and formal digest simulation

## Workstream 3: Sequence Analytics Tracks and Visualization

### Objective

Turn the current GC-only chart into a real analytics track system.

### Core design correction

The current `showGCTrack` toggle is too narrow. The toolkit needs a track manager, not a single hardcoded graph.

### Required baseline improvements

- replace the single-track toggle with an `Analytics` or `Tracks` control
- support multiple stacked tracks
- make window size and step size configurable
- use adaptive binning or downsampling for long constructs
- refine line rendering, markers, hover, and selection overlays
- expose overall and selected-region statistics
- support export of tracks with construct graphics

### GC content v2 requirements

- smoother rendering at scale
- clearer baseline and y-axis framing
- optional fill area under the curve
- more controlled point density
- better contrast and legend cues
- hover information that feels analytical rather than decorative

### Recommended simple-add metrics for the first analytics tranche

These metrics are strong first additions because they are sliding-window calculations over existing sequence data and do not require external services or deep new data models.

- GC skew: `(G - C) / (G + C)`
- AT skew: `(A - T) / (A + T)`
- local sequence complexity or Shannon entropy
- ambiguity density for `N` and other degenerate bases
- homopolymer or low-complexity repeat density
- restriction-site density for the currently selected enzyme set
- ORF density or stop-codon density for visible reading frames

### Nice-to-have but second-wave metrics

- primerability or local Tm proxy
- codon adaptation metrics
- motif density tracks
- repeat-family density tracks

### Acceptance criteria

- analytics remain responsive on larger plasmids
- selection on the viewer and analytics track stays synchronized
- users can switch between GC content and other basic metrics without leaving the editor
- at least four non-GC metrics ship in the first larger revision tranche

## Workstream 4: Feature System, Qualifiers, and Annotation Review

### Objective

Upgrade feature editing from lightweight labels to structured annotations with explicit review and provenance.

### Required feature qualifier editor

The toolkit needs a full qualifier editor with GenBank-style metadata rather than only name, type, and color.

Representative qualifier support should include:

- `gene`
- `locus_tag`
- `product`
- `note`
- `db_xref`
- `protein_id`
- `translation`
- `codon_start`
- `transl_table`
- `standard_name`
- `regulatory_class`
- `bound_moiety`
- `inference`

### Required annotation review flow

Auto-annotation should stop auto-appending directly into the canonical construct state without a review surface.

Required review actions:

- accept
- reject
- merge
- deduplicate
- preserve existing field
- replace existing field

### Required parts-catalog features

- reusable promoter, CDS, terminator, origin, and tag parts
- favorites and recently used parts
- drag-in or apply-to-selection workflows
- provenance showing source catalog or source construct

### Acceptance criteria

- qualifiers round-trip through save and export within documented limits
- users can review auto-annotations before commit
- duplicate annotations are inspectable instead of silently appended or discarded

## Workstream 5: Primer Design, PCR, and Oligo Workbench

### Objective

Extend the existing primer and PCR logic into a real design surface.

### Required primer design modes

- standard PCR
- sequencing primer
- mutagenesis primer
- RT-PCR or RNA-aware primer design
- overhang or cloning primer

### Required constraint controls

- target region or feature
- amplicon size range
- target Tm range
- GC clamp preference
- avoid repeat or low-complexity regions
- avoid strong self-dimer or hairpin candidates where feasible
- maximum degeneracy

### Required ranking and review

- batch candidate generation
- pair ranking
- visible annealing region vs added tail or overhang
- off-target or multiple-binding warnings
- export or ordering table

### Required template systems

- Gibson overlap tails
- Golden Gate overhang templates
- cloning primer tails
- sequencing primer presets

### Acceptance criteria

- users can generate primer pairs from a selected region or feature
- tailed and untailored primers are both represented clearly
- candidate ranking explains why one pair outranks another

## Workstream 6: Translation and Coding Analysis

### Objective

Provide the coding and translation tools needed for expression constructs, optimization, and coding-sequence review.

### Required capabilities

- amino-acid map view
- frame-aware translation controls
- ORF scoring and ranking
- codon usage and codon-frequency summaries
- silent mutation optimization
- synonymous insertion or removal of restriction sites
- peptide-tag and fusion-aware viewing support

### Optional early extensions

- codon adaptation index
- organism-specific codon tables
- reverse translation helper

### Acceptance criteria

- users can inspect translated regions without leaving the toolkit
- coding changes can be evaluated against codon and restriction constraints

## Workstream 7: Workspace, Lineage, and Comparison

### Objective

Turn the toolkit from a single-document editor into a molecular workspace.

### Required workspace behavior

- multi-tab construct workspace
- pinned derived outputs
- session restore for open constructs
- branch or clone variant creation

### Required comparison tools

- side-by-side map comparison
- side-by-side sequence comparison
- feature and primer diffing
- lineage view for parent and child constructs
- operation manifest display for derived records

### Acceptance criteria

- users can compare variants without replacing the active construct
- derived operations produce inspectable lineage, not opaque output files

## Workstream 8: Alignment and Variant Review

### Objective

Add alignment tooling without forcing one viewer to do every job badly.

### Core recommendation

IGV is valid and should remain in the product, but only for the class of problems it is actually built to solve.

Recommended role split:

- `IGV.js` for read-to-reference inspection, BAM or CRAM pileups, coverage, mismatch review, and variant-support evidence
- native molbio compare views for plasmid-to-plasmid and construct-to-construct diffing
- an MSA viewer for multi-sequence alignment and consensus-style inspection
- optional JBrowse 2 only if dotplot, synteny, or richer comparative track views become a priority

### Why IGV remains valid

The existing codebase already embeds IGV on the NGS side in [platform/frontend/src/components/NGSToolkit.tsx](../platform/frontend/src/components/NGSToolkit.tsx), including BAM and reference selection, display settings, and track loading.

The molbio editor itself remains SeqViz-based in [platform/frontend/src/components/MolBioToolkit/SequenceViewer.tsx](../platform/frontend/src/components/MolBioToolkit/SequenceViewer.tsx), which is the right foundation for map editing, features, primers, and plasmid-centric operations.

That split is sound. It should be made more deliberate, not collapsed.

### Required alignment modes

- `Read Pileup`
- `Construct Compare`
- `Multiple Alignment`
- `Dotplot / Synteny` as optional or preview

### Mode 1: Read Pileup

This mode should use IGV.js.

Best-fit use cases:

- amplicon validation
- clone verification
- ONT or Illumina plasmid QC
- mismatch, indel, and soft-clip inspection
- methylation or auxiliary track overlays
- consensus review against a reference plasmid or amplicon

### Mode 2: Construct Compare

This should not be IGV-first.

It needs a plasmid-aware or construct-aware diff view that can show:

- sequence differences between two constructs
- insertions, deletions, substitutions, and inversions
- feature-level additions, removals, and moved boundaries
- primer-binding consequences
- circular-origin-aware comparisons

### Mode 3: Multiple Alignment

This should use a dedicated MSA viewer, not IGV.

Best-fit use cases:

- comparing clone families
- comparing promoter or ORF variants
- reviewing consensus sequences
- tracking silent mutations or part variants across many constructs

### Mode 4: Dotplot or Synteny

This is optional for alpha, but useful if comparative sequence analysis becomes more important.

Best-fit use cases:

- large insert or rearrangement review
- repeated-region inspection
- long-read or assembly-to-reference comparison
- assembly or reference rotation diagnostics

### Backend tooling recommendations

Recommended backend stack by task:

- `minimap2` or equivalent for larger pairwise construct alignments and PAF generation
- `edlib` or a similar fast edit-distance aligner for short sequence or construct diff operations
- `samtools` for BAM or CRAM indexing and slicing around selected loci
- optional `MUMmer`-style outputs or PAF-based compare products for dotplot generation

### Circular plasmid requirements

Alignment workflows must account for circular constructs explicitly.

Required handling:

- reference rotation or doubled-reference strategies for wrap-around events
- diff logic that does not treat origin-spanning changes as unrelated terminal edits
- plasmid-aware display of junction-spanning reads and variants

### UX requirements

- open alignment modes from the molbio workspace, not only from NGS job pages
- keep viewer selection synchronized with alignment loci where applicable
- allow jumping from read evidence to a sequence location and back
- keep alignment review separate from direct editing mode
- persist alignment result provenance and source files

### Acceptance criteria

- users can inspect read evidence for a construct without leaving the molecular workflow context
- users can compare two constructs without forcing them into a BAM-style viewer
- circular plasmid comparisons remain interpretable across the origin

## Workstream 9: Import, Export, Round-Trip Fidelity, and Publication Graphics

### Objective

Bring import and export closer to the fidelity users expect when moving between molecular tools.

### Required import and export areas

- richer SnapGene round-trip
- richer GenBank round-trip
- FASTA behavior with explicit metadata loss rules
- feature qualifier persistence
- primer, notes, color, and topology persistence where format permits
- import diagnostics for fields that cannot be preserved

### Required publication and reporting outputs

- map export
- linear annotation-strip export
- analytics track export
- digest gel export
- primer table export
- workflow result summaries suitable for documentation

### Acceptance criteria

- users can import, edit, export, and reimport core formats without unexpected major data loss
- the toolkit can generate shareable publication or documentation graphics without external redrawing

## Workstream 10: Comprehensive Bug Sweep

### Objective

Run a full molbio toolkit bug sweep before alpha signoff.

This is not a single ticket. It is a structured verification and fix pass.

### Bug sweep buckets

#### A. Import, Export, and Type Fidelity

- GenBank import correctness
- FASTA import correctness
- SnapGene import correctness
- demo construct sequence realism and restriction-site artifact audit
- RNA type preservation
- circularity preservation
- feature and primer round-tripping
- GenBank export fidelity
- qualifier export fidelity

#### B. Coordinate and Topology Integrity

- 0-based vs 1-based conversion issues
- half-open interval correctness
- origin-wrapping feature handling
- origin-wrapping primer handling
- selection bar correctness
- circular digest fragment coordinates
- circular PCR coordinates

#### C. Editing and State Safety

- insert, delete, and replace edge cases
- transform edge cases on selected regions
- annotation remapping after edits
- undo and redo correctness across import, edit, annotate, and save
- dirty-state accuracy
- keyboard shortcut safety

#### D. Primer, Search, and PCR Behavior

- reverse primer coordinate placement
- multiple binding site behavior
- circular-template primer matching
- RNA primer compatibility
- library primer insertion behavior
- reverse-strand search correctness
- motif search correctness with ambiguity codes

#### E. Restriction Analysis and Digest

- cut-site indexing correctness
- single and dual digest correctness
- nicking enzyme handling
- Type IIS behavior
- overhang and end-type reporting
- ladder and gel preview consistency

#### F. Feature and Annotation Management

- feature add, edit, and delete validation
- qualifier persistence
- duplicate handling after auto-annotation
- annotation merge policy clarity
- feature color persistence
- jump-to-feature correctness

#### G. Analytics and Selection Sync

- GC and non-GC track correctness
- selection synchronization between viewer and tracks
- large-track rendering behavior
- analytics export correctness

#### H. Backend Operation Correctness

- digest result correctness
- PCR result correctness
- mutagenesis validation
- ligation compatibility rules
- Gibson overlap validation
- Golden Gate overhang logic
- derived sequence metadata
- operation lineage persistence

#### I. UI, Error States, and Recovery

- clear user-facing errors instead of silent console failures
- modal dismissal and focus behavior
- loading and cancel states
- empty-library and empty-search states
- unsaved-changes behavior
- no-data and large-data rendering states

### Bug sweep deliverable

A tracked issue list grouped by bucket, with:

- repro steps
- expected behavior
- severity
- owner
- fixed build reference
- verification notes

## Workstream 11: Performance and Stability Pass

### Objective

Make the toolkit stable for larger plasmids, annotation-heavy constructs, and denser analytics or digest views.

### Required performance revisions

#### 1. History model

Replace or further reduce full-snapshot history churn in [platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts](../platform/frontend/src/components/MolBioToolkit/hooks/useSequenceHistory.ts).

Targets:

- no serialization-based deep equality on ordinary state sets
- avoid storing unnecessary derived-state updates in undo history
- cap memory growth more intelligently than snapshot count alone

#### 2. Derived-state separation

Separate persisted construct state from transient derived state:

- ORFs
- highlights
- search results
- digest previews
- analytics-track data
- compare-session state

#### 3. Enzyme-site indexing and digest caching

Restriction analysis will get heavier as the enzyme metadata model and filters improve.

Targets:

- cache site-index results by sequence hash and enzyme set
- avoid recomputing all enzymes on every lightweight UI change
- support selection-aware calculations without full recomputation where possible

#### 4. Analytics rendering

Revise [platform/frontend/src/components/MolBioToolkit/GCContentTrack.tsx](../platform/frontend/src/components/MolBioToolkit/GCContentTrack.tsx) into a bounded, reusable analytics renderer.

Targets:

- bounded trace count
- fast redraws
- responsive zoom and selection
- room for multiple tracks without runaway repaint cost

#### 5. Query and API consistency

Continue refactoring sequence operations toward the shared API and query caching patterns already used elsewhere in the frontend.

Goals:

- request deduplication
- retry and error consistency
- fewer ad hoc loading flags
- cleaner optimistic refresh behavior

#### 6. Large-list behavior

For feature, primer, and enzyme-heavy constructs:

- debounce search inputs
- avoid unnecessary re-sorts and recomputations
- virtualize long lists if needed

## Alpha Non-Goals

The following are not required for the initial alpha:

- a full Benchling-equivalent multi-user collaborative editor
- a complete external biotech database federation layer on day one
- comprehensive RNA secondary-structure design tooling
- enterprise-grade permissions and audit systems

## Implementation Phases

## Phase 1: Foundation and Data Models

- expand construct data model for richer provenance and entity typing
- add enzyme metadata model beyond raw recognition sites
- define structured qualifier storage
- continue separating transient derived state from saved construct state

## Phase 2: Restriction Analysis and Analytics Overhaul

- replace the current digest interaction model with a dedicated restriction-analysis workspace
- add quick filters for unique, dual, nicking, overhang, and Type IIS classes
- add fragment table and gel-style digest preview
- replace the single GC toggle with a track manager
- ship GC content v2 plus the first set of additional metrics:
  - GC skew
  - AT skew
  - local complexity
  - restriction-site density

## Phase 3: Feature, Qualifier, and Annotation Review System

- ship full qualifier editor
- add annotation review queue with accept, reject, and merge actions
- add reusable feature or part catalog foundation

## Phase 4: Primer Design and Translation Tools

- ship primer design modes and candidate ranking
- add overhang template systems
- ship amino-acid map, ORF scoring, and codon-usage tools

## Phase 5: Workspace, Alignment, Comparison, and Export Parity

- add multi-tab workspace and lineage comparison
- expose read-pileup review from molbio context using the existing IGV foundation
- add construct compare and multiple-alignment views
- add side-by-side construct comparison
- improve import and export round-trip fidelity
- ship publication and reporting graphics

## Phase 6: Comprehensive QA and Alpha Gate

- run full bug sweep
- close P0 and P1 issues
- complete import, edit, digest, PCR, annotation, analytics, and export verification matrix
- document known alpha limitations and preview-only workflows

## Acceptance Criteria

The alpha is considered ready only if all of the following are true.

### Product acceptance

- the user can acquire constructs through a dedicated multi-source modal flow
- plasmid and RNA entry paths are both first-class
- construct, primer, and parts search are all available and useful
- restriction analysis no longer depends on hidden gestures
- the analytics surface includes more than one track and feels like a real analysis tool

### Correctness acceptance

- no silent DNA coercion for RNA assets
- digest and PCR behave correctly for circular and linear templates
- nicking and Type IIS metadata are correctly represented where supported
- edits preserve or intentionally update coordinates
- qualifiers persist within documented import and export limits
- derived workflows persist explicit lineage metadata

### Stability acceptance

- no known P0 bugs
- no unresolved state-corruption bugs
- no unresolved save-loss bugs
- no unresolved import or export corruption bugs

### Performance acceptance

- toolkit remains responsive on common plasmid sizes and annotation densities
- restriction analysis remains usable with large enzyme panels
- analytics and viewer interactions remain usable on larger constructs
- undo and redo do not degrade sharply under normal editing

## QA Matrix

Alpha QA must cover at minimum:

- small linear DNA
- large circular plasmid
- imported SnapGene plasmid
- imported GenBank plasmid
- RNA transcript
- annotation-heavy construct
- primer-heavy construct
- enzyme-heavy restriction analysis case
- nicking enzyme case
- Type IIS digest case
- read-alignment review case
- construct-to-construct comparison case
- multi-sequence alignment case
- origin-wrapping digest case
- origin-wrapping PCR case
- qualifier-heavy annotation case
- edit followed by save, reload, export, and reimport
- side-by-side compare case

Each case should be tested against:

- import
- save and load
- search
- selection
- edit
- feature operations
- qualifier operations
- primer operations
- digest and PCR where applicable
- analytics tracks where applicable
- export

## Release Risks

### Risk 1: Overpromising assembly workflows

If ligation, Gibson, and Golden Gate remain simplistic, they must be labeled `Preview` or held from alpha.

### Risk 2: RNA support that is nominal rather than real

RNA should not be advertised as supported unless acquisition, save, search, reverse-complement logic, and derived operations preserve RNA semantics.

### Risk 3: Restriction UX that remains technically correct but still awkward

If restriction analysis remains split between visibility toggles and an expert-only digest panel, the toolkit will still feel unfinished even if the backend math is correct.

### Risk 4: Analytics that remain decorative rather than useful

If the track system stops at a prettier GC graph, it will not materially close the gap with mature molecular tools.

## Recommended First Large Revision Slice

The first implementation slice after this spec should be:

1. add the richer enzyme metadata model and restriction-analysis quick filters
2. replace the current digest interaction with an explicit digest basket, fragment table, and gel-style preview
3. replace the single GC toggle with a track manager and ship GC content v2 plus GC skew, AT skew, local complexity, and restriction-site density
4. lay the qualifier-storage groundwork needed for the later feature-editor upgrade
5. run a focused bug sweep across find, edit, digest, PCR, primer, and annotation behavior while these surfaces are changing

This slice is the right starting point because it addresses the most obvious UX friction in the current toolkit, produces immediate visible value for testing, and creates the data-model foundation needed for the larger parity workstreams that follow.

## Summary

The next molbio milestone should not be framed as a handful of extra panels.

It should be framed as:

- a real molecular acquisition system
- a searchable local and external molecular database layer
- an intuitive restriction-analysis and digest workspace
- a real analytics-track surface
- structured annotations and qualifier editing
- real primer-design, translation, workspace, and comparison systems
- a deliberate correctness, bug-sweep, and performance pass

That is the minimum credible path for the molbio toolkit to evolve from a promising editor into a real molecular design environment inside BioModStack.
