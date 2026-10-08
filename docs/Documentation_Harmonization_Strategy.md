# Documentation Harmonization Strategy

## Goal

Give BioModStack one coherent documentation surface that matches the live
system instead of a mix of stale README links, implementation notes, and dated
specs.

## Problems This Fixes

- the repo README pointed at missing files
- the docs tree mixed operator docs with design history
- subsystem coverage was fragmented across structure workflows, mol bio, NGS,
  robotics, and platform internals
- model/workflow documentation under-described the live feature surface

## Canonical Information Architecture

### 1. Landing

- `README.md`

Purpose:
- explain what BMS is
- list the major feature areas
- show how to start the local stack
- route readers into the canonical docs

### 2. Canonical Operator Docs

- `docs/Platform_Overview.md`
- `docs/Workstation Set Up and Install Guide.md`
- `docs/Structure_Design_and_Refinement.md`
- `docs/Lab_Automation_MolBio_and_Sequencing.md`
- `docs/Results_and_Analysis.md`

Purpose:
- describe what the live system does
- describe how the workstation is laid out and launched
- describe major user-facing workflows and subsystem surfaces

### 3. Subsystem References

- `platform/api/README.md`
- `platform/frontend/README.md`
- `docs/ai_guidance/Model_Integrations.md`

Purpose:
- give deeper technical reference for the control plane, UI, and live model set

## Rules Going Forward

### Keep canonical docs date-neutral

If a document is meant to describe current behavior, it should not need a date
in the filename.

### Do not keep stale design notes in tracked docs

If a document is obsolete, superseded, or only useful as temporary design
scratch space, delete it from the tracked docs tree instead of leaving it in
place as passive clutter.

### Keep README thin

The root README should summarize and route. It should not try to become the
full manual.

### Update docs when contracts change

The following changes should trigger doc review in the same branch:

- new workflow entrypoints or major mode changes
- validator/backend changes
- changes to runtime env vars or pathing rules
- new frontend surfaces or routes
- new API routers or user-facing endpoints
- new robotics, mol bio, or NGS capabilities

### Prefer capability docs over implementation speculation

Docs should describe what is live, what is internal-only, and what is
experimental. They should not imply support just because a draft spec exists.

## What Was Executed In This Pass

- rewrote the repo README
- added a docs index for the current documentation surface
- added current docs for platform overview, runtime, workflows, and lab-facing
  subsystems
- aligned API/frontend README files with the live system
- replaced the stale model-integration doc with a live inventory
- removed stale dated design/spec/review docs and bundled reference files from
  the tracked `docs/` surface

## Deferred Work

- add per-workflow operator runbooks if the antibody, sequencing, or robotics
  surfaces continue to grow
- add automated link and stale-doc checks in CI
