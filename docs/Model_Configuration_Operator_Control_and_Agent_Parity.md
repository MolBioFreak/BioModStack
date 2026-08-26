# Model configuration, operator control, and agent parity policy

**Status:** Canonical BioModStack product and engineering policy

**Authority:** Applies to every scientific model used by BioModStack

**Scope:** Protein design, structure prediction, molecular simulation, molecular biology, Nanopore/NGS, analytics, and future model families

## Hard rule

BioModStack shall expose every relevant scientific and inference setting supported by each integrated model. The operator shall be able to inspect and set these values through suitable UI controls. AI agents shall be able to inspect and set the same values through the typed API contract.

A model integration is incomplete while a relevant model setting is fixed in hidden code, silently dropped, exposed only through raw JSON, or available to an agent but unavailable to the operator.

This rule has no partial-completion exception. New model integrations and changes to existing integrations must reach complete parameter parity before they can be called complete.

## Relevant settings

Relevant settings include each value that the pinned installed model proves it supports and that can alter scientific behavior, inference behavior, model output, or reproducibility.

Internal implementation details are outside the operator setting surface when they do not change the requested scientific operation. These include credentials, host filesystem paths, artifact storage roots, internal database IDs, container digests, command construction, security policy, and scheduler-owned physical resource assignment. BMS must still record applicable runtime identities and provenance in execution receipts.

When the boundary is uncertain, treat the setting as relevant until the model owner documents why it cannot change scientific or inference behavior.

## Canonical parameter contract

Each model shall have one versioned global parameter schema. It shall define:

- stable parameter key and model-native mapping;
- data type;
- default value;
- allowed values or numeric bounds;
- units and precision;
- scientific meaning;
- applicability conditions;
- incompatibilities and cross-field rules;
- whether the value affects reproducibility identity;
- safe UI control type;
- API request and persisted-result representation;
- model version or capability range that supports it.

Unknown settings fail closed. Unsupported combinations fail before queue insertion. Defaults are explicit and persisted. The execution receipt records the effective settings after validation and model-native compilation.

Workflow code may set a documented initial value or limit a setting to a model-supported context. It shall not create a second setting definition, silently remove a supported setting, or hide an active value from the operator.

### Setting authority classes

Every setting surface shall label and preserve one authority class:

- **Operator-owned** values are editable and must appear in the complete requested settings object submitted by both browser and agent.
- A **recommended default** is an explicit, editable initial value with a concise reason. It is not a hidden override and never replaces a saved operator value during hydration.
- Values **fixed by the selected profile** remain visible and read-only. Their exact profile value, unit, and fixed reason are part of the request and provenance; the browser cannot silently substitute a nearby value.
- **Scheduler-owned** placement, physical GPU identity, runtime paths, and internal materialization details are not scientific controls and cannot be browser fields. They appear only in the effective request or execution receipt where applicable.

For workflows with server compilation, the requested settings remain distinct from the effective request. Preview shall display the effective request, all blockers/warnings, and its preview digest before final launch. A final mutation shall bind to that preview digest so an operator or agent cannot unknowingly launch a different compiled configuration. Any request-affecting edit invalidates preview.

## Operator UI requirements

Use a control that matches the setting:

- checkbox for booleans;
- select, radio group, or segmented control for closed choices;
- slider with synchronized numeric input for bounded continuous or integer values;
- bounded numeric input where a slider would reduce precision;
- residue, chain, region, file, dataset, or model selectors for typed identities;
- list, table, or chip editor for bounded repeated values;
- conditional sections for settings that apply only to a selected mode;
- an advanced section for relevant expert settings that would overload the primary form.

The UI shall show the effective default, valid range, units, current value, and concise scientific meaning. Loading global configuration shall never overwrite a saved value or a value that the operator changed.

A raw JSON editor may supplement typed controls for import and export. It does not replace the required UI controls.

## AI-agent parity

AI agents use the same versioned parameter schema and validation rules as the browser. Agent tools shall support:

- discovery of all relevant settings and their metadata;
- retrieval of defaults and saved effective values;
- construction and validation of a complete request;
- clear error details for unsupported or incompatible values;
- readback of the exact effective settings and configuration digest;
- replay through immutable request identity.

Agent-only hidden parameters and UI-only scientific parameters are prohibited. Human and agent submissions compile through the same request authority.

## Global analysis and result experience

Model-native results remain distinct, while common handling is global and reusable. Every model integration shall use shared BMS mechanisms where applicable for:

- immutable artifact registration and content hashes;
- typed row-level or point-level persistence;
- source, model, runtime, configuration, and workflow lineage;
- bounded summaries and paginated detail APIs;
- missingness and unsupported-state representation;
- tables, charts, structure or sequence projection, and synchronized selection;
- comparison across compatible runs, candidates, states, or datasets;
- descriptive statistics and model-appropriate statistical analysis;
- image, table, JSON, and native-artifact export;
- saved views, captures, annotations, and review records;
- consistent result routing from every workflow that uses the model.

A workflow can add contextual interpretation and actions. It shall reuse the global model result contract and workbench instead of creating a separate numerical authority or a reduced viewer.

## Completion gate

A model reaches 100% integration only when all of these are accepted:

1. The exact installed model capability and parameter inventory is complete.
2. Every relevant setting has a typed global schema and model-native mapping.
3. The browser exposes every setting through an appropriate control.
4. AI agents have complete typed API parity.
5. Saved requests, templates, retries, and clones preserve effective values.
6. Scheduler execution consumes the validated values without silent fallback or loss.
7. Receipts and results record the effective configuration and digest.
8. Global data, visualization, statistical analysis, capture, persistence, and viewing mechanisms cover the model's outputs.
9. Each consuming workflow uses the global configuration and result mechanisms.
10. Focused and live acceptance proves request-to-result agreement for the exact released revision.

If one gate is incomplete, report the model as incomplete and identify that gate. Do not average away a missing parameter, UI, API, data, or result surface.
