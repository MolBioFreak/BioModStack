# BioModStack Documentation

This directory is the current tracked documentation surface for BioModStack.
Canonical docs describe what is live today. Active plans are kept separately
under `docs/plans/` and should not be mistaken for the product truth.

## Start Here

- [Platform Overview](Platform_Overview.md)
- [Workstation Setup and Runtime](Workstation%20Set%20Up%20and%20Install%20Guide.md)
- [Desktop Runtime and Shell Architecture](Desktop_Runtime_and_Shell_Architecture.md)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Experimental Protein CAD Workflow](Experimental_Protein_CAD_Workflow.md)
- [Caliby Experimental Workflow](Caliby_Experimental_Workflow.md)
- [Protein Hunter Experimental Workflow](Protein_Hunter_Experimental_Workflow.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](Results_and_Analysis.md)
- [Documentation Harmonization Strategy](Documentation_Harmonization_Strategy.md)

## Platform and subsystem references

- [API README](../platform/api/README.md)
- [Frontend README](../platform/frontend/README.md)
- [Electron shell README](../platform/desktop-electron/README.md)
- [Model Integrations](ai_guidance/Model_Integrations.md)
- [Database Instructions](ai_guidance/Database_Instructions.md)
- [Pathing and Centralization Rules](ai_guidance/Centralization_and_Standardization.md)

## Active plans

- [Plans README](plans/README.md)
- [Android APK thin-shell comparison](plans/2026-04-20-android-apk-thin-shell-comparison.md)
- [Control plane / Electron / install-path upgrade](plans/2026-04-20-control-plane-electron-runtime-paths-upgrade.md)
- [Core-runtime workflow-adapter cutover](plans/2026-04-20-core-runtime-workflow-adapter-cutover.md)
- [Fold-CP large-protein sharding plan](plans/2026-04-20-fold-cp-large-protein-sharding-plan.md)

## Canonical doc roles

- `README.md` at the repo root:
  GitHub landing page and first-run orientation.
- `docs/*.md` without a date in the filename:
  current operator, workflow, and platform documentation.
- `platform/api/README.md` and `platform/frontend/README.md`:
  subsystem-specific technical references.
- `docs/plans/*.md`:
  active implementation plans and transition notes only; these do not define the
  canonical product contract.
- `docs/plans/archive/*.md`:
  archived historical planning/spec material kept for auditability.
- `docs/ai_guidance/*.md`:
  implementation policies and contributor guidance.

## Current scope

The canonical docs set now covers:

- containerized core runtime plus the host-native workflow-adapter boundary
- browser, Electron, GTK, and optional Android thin-shell/update surfaces
- structure design, validation, refinement, and experimental workflow families
- mol bio construct editing and sequence operations
- nanopore/NGS launch and review surfaces
- BioXP robotics linkage and the current cockpit/proxy surface rather than a
  full mirror of every robot-local endpoint
- results, lineage, analytics, and persisted runtime-path management

For BioXP, the canonical docs intentionally distinguish between the current BMS
cockpit surface and the broader robot-local runtime. Reference-state,
liquid-handling, and some recovery semantics still live more completely on the
robot runtime than in the BMS proxy today, and current reliability language
should be read as unresolved transport/recovery instability rather than a
blanket hardware-failure verdict.

Older build/implementation planning docs that no longer belong on the repo-facing
surface should be pruned instead of left beside the canonical docs as passive
clutter.
