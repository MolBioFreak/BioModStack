# BioModStack Documentation

This directory now has a canonical entry path. If a dated spec, plan, or fix
note disagrees with the docs below, treat the docs below as the current source
of truth unless the code has already moved again.

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

## How To Read The Rest Of `docs/`

The repo still contains many historical files with names like:

- `*_Plan*.md`
- `*_Spec*.md`
- `*_Fix*.md`
- `*_Review*.md`
- dated implementation notes such as `*_2026-03-*.md`

Those files are useful for design history and implementation context, but they
are not the primary operator docs.

## Canonical Doc Roles

- `README.md` at repo root:
  GitHub landing page and first-run orientation.
- `docs/*.md` without a date in the filename:
  Current operator and feature documentation.
- `platform/api/README.md` and `platform/frontend/README.md`:
  subsystem-specific developer/operator references.
- `docs/ai_guidance/*.md`:
  implementation policies and technical guidance for contributors.
- dated docs under `docs/`:
  historical plans, specs, and revision notes.

## Current Scope

The canonical docs set covers:

- structure design, validation, and refinement workflows
- antibody and binder-focused orchestration
- mol bio construct editing and sequence operations
- nanopore/NGS launch and review surfaces
- BioXP robotics integration
- results, lineage, analytics, and runtime layout
