# BioModStack Documentation

This directory is the current tracked documentation set for BioModStack.
Stale dated design/spec/review notes have been removed from the repo-facing docs
surface so this index can act as the single starting point.

## Start Here

- [Platform Overview](Platform_Overview.md)
- [Workstation Setup and Runtime](<Workstation Set Up and Install Guide.md>)
- [Structure Design and Refinement](Structure_Design_and_Refinement.md)
- [Lab Automation, Mol Bio, and Sequencing](Lab_Automation_MolBio_and_Sequencing.md)
- [Results and Analysis](Results_and_Analysis.md)
- [Documentation Harmonization Strategy](Documentation_Harmonization_Strategy.md)

## Platform Docs

- [API README](../platform/api/README.md)
- [Frontend README](../platform/frontend/README.md)
- [Model Integrations](ai_guidance/Model_Integrations.md)
- [Database Instructions](ai_guidance/Database_Instructions.md)
- [Pathing and Centralization Rules](ai_guidance/Centralization_and_Standardization.md)

## Canonical Doc Roles

- `README.md` at repo root:
  GitHub landing page and first-run orientation.
- `docs/*.md` without a date in the filename:
  Current operator and feature documentation.
- `platform/api/README.md` and `platform/frontend/README.md`:
  subsystem-specific developer/operator references.
- `docs/ai_guidance/*.md`:
  implementation policies and technical guidance for contributors.

## Current Scope

The canonical docs set covers:

- structure design, validation, and refinement workflows
- antibody and binder-focused orchestration
- mol bio construct editing and sequence operations
- nanopore/NGS launch and review surfaces
- BioXP robotics integration
- results, lineage, analytics, and runtime layout
