# FrustraMPNN global configuration and analysis workbench specification

**Status:** Canonical first-tranche specification

**Date:** 2026-08-08

**Source baseline:** BioModStack `test` at `2431ac3775abc5688159bec1c91b92a957a83f9f`

**Target:** 100% completion of global FrustraMPNN configuration, data, analytics, and result handling

**Consumer integration order after this tranche:** Structure Prediction, non-nanobody RFD3 de novo/redesign, Conformational Mapping

**Implementation plan:** [Global FrustraMPNN 100% implementation plan](../plans/2026-08-08-frustrampnn-global-100-implementation.md)

## 1. Purpose

Finish FrustraMPNN as a complete global BioModStack capability. Every later workflow shall consume the same typed settings, execution contract, data plane, statistical analysis, visualization, capture, persistence, and result workbench.

This tranche stops at the reusable global boundary. Workflow-specific Structure Prediction mutation controls, RFD3 feedback, and CM convergence follow after this gate reaches 100%.

The general policy in [Model configuration, operator control, and agent parity](../Model_Configuration_Operator_Control_and_Agent_Parity.md) is controlling.

## 2. Existing foundation to preserve

Preserve and complete the current global implementation:

- `modules/frustrampnn.nf`;
- `workflows/frustrampnn_analysis.nf`;
- `scripts/run_frustrampnn_component.py`;
- `platform/api/services/frustrampnn/`;
- global configuration identity `frustrampnn_global_v1`;
- scheduler-owned execution and physical GPU assignment;
- pinned SIF, executable, checkpoint, and runtime receipts;
- deterministic structure normalization and exact residue mapping;
- complete 20-slot substitution landscapes;
- immutable request, result, manifest, raw-output, and provenance artifacts;
- global row persistence and bounded APIs;
- global comparison and guidance substrate;
- `FrustraMpnnResultsViewer` and its structure-linked analysis components;
- historical read compatibility.

Existing workflow-local projections remain compatibility surfaces until each later consumer tranche migrates. They shall not define new global FrustraMPNN settings or numerical semantics.

## 3. Hard parameter-completeness requirement

### 3.1 Exact installed-capability inventory

Audit the exact digest-pinned FrustraMPNN executable and checkpoint. Record every supported scientific and inference setting from:

- installed CLI help;
- installed argument declarations;
- pinned source when available;
- checkpoint/runtime constraints;
- model-native input and output contracts.

Classify every discovered option as:

- operator-relevant scientific or inference setting;
- BMS-owned runtime, security, storage, or scheduler setting;
- diagnostic-only option;
- unsupported or inapplicable option with evidence.

The inventory is versioned and machine-readable. A prose list is insufficient.

### 3.2 Global setting schema

Every operator-relevant setting shall have one closed typed definition containing:

- canonical key;
- model-native flag or field;
- type, default, bounds, enum, units, and precision;
- applicability and cross-field rules;
- scientific meaning;
- reproducibility effect;
- UI control metadata;
- API and persistence mapping;
- installed-model support evidence.

The global configuration digest covers every effective scientific and inference value. Comparison compatibility uses that digest and reports incompatible fields.

### 3.3 Operator controls

The shared FrustraMPNN control expands from one checkbox into progressive disclosure:

- disabled state: **Frustration analysis** checkbox and concise purpose;
- enabled state: model/checkpoint identity plus all common relevant settings;
- advanced state: all remaining relevant expert settings;
- no raw JSON requirement for normal operation.

Use checkboxes, selectors, sliders with synchronized numeric values, bounded inputs, chain/residue selectors, and other typed controls as appropriate. Show defaults, valid ranges, units, and concise scientific effects.

### 3.4 Agent control

Expose the same schema through typed APIs and agent tools. An agent can discover, validate, submit, read back, clone, and replay every setting. Human and agent submissions use one compiler and one request authority.

## 4. Execution and provenance contract

Each invocation shall persist:

- exact source artifact and content hash;
- normalized structure and residue map;
- requested settings;
- effective validated settings;
- canonical settings digest;
- model, checkpoint, executable, container, source, and adapter identities;
- physical scheduler assignment and task-visible device identity;
- model-native command or input compilation receipt;
- raw stdout, stderr, exit code, and duration;
- raw FrustraMPNN output;
- canonical landscape, summary, manifest, and registered artifacts.

Unknown settings, unsupported combinations, value loss, and model-native compilation drift fail before model execution. Enabled analysis fails closed. Disabled analysis records exact `not_requested` state.

## 5. Global data plane

### 5.1 Numerical authority

`frustrampnn_landscape_v1` remains the sole global numerical authority. Each mapped residue retains exactly 20 ordered amino-acid slots with:

- complete residue and entity identity;
- wild type and proposed amino acid;
- raw score;
- native flag;
- canonical class;
- scoreable status;
- explicit missingness reason;
- configuration and source identity.

### 5.2 Persistence

Persist global invocation headers, artifacts, residue rows, substitution rows, summaries, comparisons, saved views, exports, captures, and review annotations through stable versioned records. Workflow identity is a lineage dimension. It shall not change the numerical schema.

### 5.3 Query and retrieval

Provide bounded APIs for:

- invocation and configuration identity;
- summary and coverage;
- residue pages;
- substitution pages;
- one residue's exact 20-slot profile;
- chain, region, class, score, and missingness filters;
- ranked substitutions;
- compatible-run comparison;
- saved views and captures;
- governed export and native artifact retrieval.

The same APIs serve Structure Prediction, de novo design, CM, uploaded structures, and future consumers.

## 6. Global descriptive statistics and analysis

The global analysis layer shall compute and persist model-appropriate results from authoritative rows. It shall include, where mathematically applicable:

- mapped and scoreable residue counts;
- missingness counts and fractions by reason;
- native-score distribution summaries;
- substitution-score distribution summaries;
- class counts and fractions;
- per-residue alternative-class burden;
- per-amino-acid substitution distributions;
- sequence-region and chain summaries;
- extrema, quantiles, medians, means, dispersion, and robust spread measures with explicit denominators;
- contiguous high-, neutral-, or minimally frustrated regions;
- compatible-run score deltas and class transitions;
- mapping coverage and unmatched-row accounting;
- effect summaries for operator-selected residue sets;
- multiple-comparison or uncertainty treatment whenever inferential statistics are offered.

Descriptive model evidence shall remain distinct from folding free energy, experimental stability, fitness, activity, or causal biological conclusions. Statistical methods, denominators, missingness policy, and configuration identity travel with each result.

## 7. Global visualization and manual review

Every FrustraMPNN result opens the same global workbench regardless of producing workflow.

The workbench shall provide:

- authoritative structure display with residue-linked coloring;
- synchronized structure, sequence, table, and chart selection;
- complete score heatmap;
- native frustration along sequence;
- exact 20-slot selected-residue card;
- score and class distributions;
- substitution-specific distributions and class composition;
- chain and region filters;
- missingness and mapping coverage views;
- compatible-result comparison with score deltas and class transitions;
- provenance and effective-setting inspection;
- bounded drill-down from every aggregate to source rows;
- consistent legends, labels, units, color policies, and scientific caveats.

A workflow may add context around this workbench. It shall not replace it with a reduced FrustraMPNN viewer.

## 8. Capture, export, and reproducible review

Support globally consistent:

- PNG or SVG chart export where supported;
- structure-view capture with selected residues and active metric recorded;
- CSV and JSON row export;
- summary and comparison export;
- native raw-output and manifest download;
- saved filters, selections, chart state, and annotations;
- immutable review/capture identity bound to source, configuration, and view state;
- reload of a saved review without recomputing the model.

Exports identify whether they contain complete data or a bounded filtered view.

## 9. Reuse contract for workflow consumers

A consuming workflow supplies:

- authorized source structure identity;
- stable candidate and producer coordinates;
- workflow, job, and invocation lineage;
- operator-selected global FrustraMPNN settings;
- optional workflow-specific biological context.

The global capability returns:

- canonical invocation and configuration identities;
- immutable result and artifact identities;
- complete landscape and statistical records;
- reusable workbench route;
- comparison and export capabilities.

Consumers shall not fork setting definitions, runtime invocation, threshold semantics, row schemas, statistics, chart implementations, export logic, or numerical authority.

## 10. First-tranche acceptance gates

### G0. Capability inventory

- The exact pinned executable and checkpoint are identified.
- Every supported option is inventoried and classified.
- Every excluded option has a recorded reason.

### G1. Full settings schema

- Every relevant setting has a closed typed definition.
- Defaults, bounds, units, applicability, and model-native mappings are complete.
- Unknown and incompatible values fail before queue insertion.

### G2. Human and agent parity

- The shared UI exposes every relevant setting with an appropriate control.
- The typed API exposes the same schema and values.
- Browser and agent requests compile to identical effective settings.
- Save, load, clone, retry, and replay preserve all values.

### G3. Execution integrity

- The scheduler-owned component consumes every effective value.
- No value is silently dropped or replaced.
- The receipt proves requested, effective, and model-native values.
- Enabled, disabled, malformed, and incompatible cases behave exactly as specified.

### G4. Global data and statistics

- Exact row cardinality, identity, missingness, summaries, and statistics agree.
- Bounded APIs and complete exports agree with persisted authority.
- Comparison rejects incompatible configurations or reports the exact incompatibility.

### G5. Global visualization and capture

- A result from an ordinary Structure Prediction owner path opens the global workbench.
- A scheduler child created from an unrelated uploaded or saved structure opens the same workbench.
- Both surfaces provide the same data, charts, filters, provenance, export, and capture behavior.
- Structure, sequence, table, and plot selection remain synchronized.

### G6. Live Development acceptance

- Exact source, frontend, API, workflow adapter, database, listener, and runtime identities are recorded.
- One enabled and one disabled governed request complete.
- One non-Structure-Prediction source proves reusable global result handling.
- Artifacts, database rows, APIs, statistics, workbench, exports, and captures agree.
- Current-build evidence is retained in a machine-readable acceptance packet.

## 11. Definition of 100%

This tranche reaches 100% only when G0 through G6 pass on the exact released Development revision. Documentation, source presence, mocked controls, or historical runtime evidence alone cannot close a gate.

Later workflow integration work begins from this accepted global capability. It may add workflow-specific context and actions. It shall reuse the accepted global parameter, data, statistical, visualization, capture, persistence, and viewing mechanisms.

## 12. Excluded from this tranche

- Internal BMS LLM campaign or autonomous experiment-management systems.
- Structure Prediction's integrated amino-acid mutation editor.
- RFD3 de novo or local-redesign feedback adapters.
- CM migration from compatibility projections to final global numerical authority.
- De novo nanobody changes.
- Production promotion.

AI agents may use the accepted standard BMS APIs to inspect results, prepare reports, and submit operator-directed work. Their conversational reasoning remains outside BMS.
