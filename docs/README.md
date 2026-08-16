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
- [Model Configuration, Operator Control, and Agent Parity](Model_Configuration_Operator_Control_and_Agent_Parity.md)
- [Documentation Harmonization Strategy](Documentation_Harmonization_Strategy.md)

## Platform and subsystem references

- [API README](../platform/api/README.md)
- [Frontend README](../platform/frontend/README.md)
- [Electron shell README](../platform/desktop-electron/README.md)
- [Model Integrations](ai_guidance/Model_Integrations.md)
- [FrustraMPNN global configuration and analysis workbench specification](specs/frustrampnn-global-configuration-analysis-workbench.md)

## Active plans

- [Global FrustraMPNN 100% implementation plan](plans/2026-08-08-frustrampnn-global-100-implementation.md)

- [Plans README](plans/README.md)
- [MSA control-plane guardrails and regression checklist](plans/2026-04-22-msa-control-plane-guardrails-and-regression-checklist.md)
- [Local high-quality MSA target-DB sharding spec](plans/2026-04-23-local-msa-target-db-sharding-spec.md)
- [DRAM→VRAM tiled runtime proof plan](plans/2026-04-24-dram-vram-tile-runtime-proof.md)
- [Fold-CP DRAM context-spill workhorse implementation spec](plans/2026-04-24-fold-cp-dram-context-spill-additional-work-spec.md)
- [RepA local MSA root-cause and fix spec](plans/2026-04-24-repa-local-msa-root-cause-and-fix-spec.md)
- [MolBio read-QC harmonization implementation plan](plans/2026-04-25-molbio-read-qc-harmonization-spec.md)
- [Caliby finishing changes specification](plans/2026-04-27-caliby-finishing-changes-spec.md)
- [GPU MMseqs EnvDB fix implementation plan](plans/2026-04-27-gpu-mmseqs-envdb-fix-spec.md)

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
- `docs/ai_guidance/Model_Integrations.md`:
  current model integration guidance. Older AI instruction docs are pruned once stale.

## Current scope

The canonical docs set now covers:

- containerized core runtime plus the host-native workflow-adapter boundary
- browser, Electron, GTK, and optional Android thin-shell/update surfaces
- structure design, validation, refinement, and experimental workflow families
- mol bio construct editing and sequence operations
- nanopore/NGS launch and review surfaces
- the bounded BioXP compact control plane and its robot-local authority boundary
- results, lineage, analytics, and persisted runtime-path management
- complete operator and AI-agent control of every relevant model setting through one typed schema
- reusable model data, statistics, visualization, capture, export, and result workbenches

For BioXP, the canonical contract is
[BioXP Compact Control Plane](BioXP_Compact_Control_Plane.md). BMS does not expose
the retired motion, liquid, power, thermal, camera, vision, arbitrary proxy,
host-lifecycle, or remote-log families. Robot-local behavior remains authoritative,
and unverified normal commands remain disabled.

For the core runtime, the canonical docs distinguish dashboard/control-plane
robustness from full scientific workflow readiness. A general Linux host should
be able to bring up API/web and show degraded capability states; full workflow
readiness still requires host-side Nextflow, Apptainer, GPU/tooling, model, and
reference-cache setup.

Older build/implementation planning docs that no longer belong on the repo-facing
surface should be pruned instead of left beside the canonical docs as passive
clutter.
