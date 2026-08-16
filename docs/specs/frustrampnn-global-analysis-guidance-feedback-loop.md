# FrustraMPNN global analysis, guidance, and feedback-loop scope correction

**Status:** Superseded scope marker

**Date:** 2026-08-08

The former proposal for a BMS-owned LLM campaign, autonomous experiment loop, proposal state machine, or conversation system is withdrawn. It was based on an incorrect expansion of the requested scope.

The active first-tranche specification is
[FrustraMPNN global configuration and analysis workbench](frustrampnn-global-configuration-analysis-workbench.md).

The active requirements are:

- complete global FrustraMPNN scientific and inference settings;
- suitable typed operator controls for every relevant setting;
- equal AI-agent access through the same standard BMS APIs;
- scheduler-owned execution and complete configuration provenance;
- reusable global data, descriptive statistics, visualization, capture, export, persistence, comparison, and result viewing;
- workflow consumers that reuse the global plane.

External AI agents may inspect persisted results, prepare reports, and submit operator-directed jobs through the standard BMS API. Their conversation with the operator, conclusion drafting, and selection of proposed next experiments remain outside BMS.

This marker exists to prevent the superseded expanded scope from returning through uncommitted copies or session history. It is not an implementation plan and does not count as source completion.
