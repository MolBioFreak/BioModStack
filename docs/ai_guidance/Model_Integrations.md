# AI guidance for model integrations

**Status:** Canonical AI instruction document

AI agents that plan, implement, review, or accept a BioModStack model integration must follow
[Model configuration, operator control, and agent parity](../Model_Configuration_Operator_Control_and_Agent_Parity.md).

## Required behavior

1. Inspect the exact installed model, pinned source, executable, and supported input contract before defining the BMS parameter surface.
2. Inventory every supported scientific and inference setting. Classify exclusions with evidence.
3. Give every relevant setting one closed global schema definition with type, default, bounds or choices, units, conditions, scientific meaning, UI metadata, API mapping, persistence mapping, and model-native mapping.
4. Expose every relevant setting through an appropriate typed browser control. Raw JSON does not replace controls.
5. Give AI agents equal access through the same discoverable typed API schema.
6. Compile human and agent requests through one validation and execution authority.
7. Persist requested values, effective values, configuration digest, model-native compilation, and runtime identity.
8. Reuse global BMS data, statistics, visualization, capture, comparison, export, and result-viewing mechanisms.
9. Keep workflow context local without forking model settings, numerical semantics, or result authority.
10. Report the integration as incomplete until every parameter, UI, API, execution, persistence, analysis, visualization, and live-acceptance gate passes.

## Prohibited shortcuts

- hidden scientific defaults;
- a subset UI presented as full control;
- agent-only scientific parameters;
- UI-only scientific parameters;
- silent omission or renaming during request compilation;
- raw JSON as the normal operator interface;
- workflow-local copies of global model settings;
- reduced workflow-specific result viewers when the global workbench applies;
- completion percentages that average away a missing hard gate.

## Current first adopter

The active first tranche is
[FrustraMPNN global configuration and analysis workbench](../specs/frustrampnn-global-configuration-analysis-workbench.md).
It must reach 100% before Structure Prediction, non-nanobody RFD3 de novo/redesign, and Conformational Mapping consumer integration tranches proceed.

The FrustraMPNN tranche includes complete settings, agent parity, execution mapping, data persistence, descriptive statistics, visualization, capture, export, comparison, and one consistent global result workbench. Internal LLM campaign machinery is outside that tranche. External agents use the standard BMS APIs under operator direction.
