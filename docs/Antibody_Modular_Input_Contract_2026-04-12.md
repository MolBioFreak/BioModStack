# Antibody Modular Input Contract

Date: 2026-04-12

## Purpose

Define the canonical control-plane contract for antibody refinement inputs so
workflow compatibility is based on artifact semantics rather than legacy source
param names like `rfantibody_input_pdbs` and `fampnn_collected_pdbs`.

## Canonical Selected-Input Fields

- `selected_input_dir`
- `selected_input_manifest`
- `selected_input_artifact_class`
- `selected_input_schema_version`
- `selected_input_stage_family`
- `selected_input_stage_mode`
- `selected_input_source_job_id`

Legacy path params remain compatibility shims only:

- `rfantibody_input_pdbs`
- `fampnn_collected_pdbs`

## Artifact Classes

- `backbone_complex`
- `sequence_designed_complex`
- `validated_complex`
- `post_validation_refined_complex`

## Routing Rule

- Compatibility checks should use `selected_input_artifact_class`.
- `source_stage_family` / `source_stage_mode` are provenance only, except when a
  stage needs a specific retry path such as post-PPIFlow backbone reattempt.

## Selection Manifest Contract

Top-level manifest keys:

- `selected_input_artifact_class`
- `selected_input_schema_version`

Per-design manifest keys:

- `design_artifact_class`
- `design_artifact_schema_version`

## Schema Version

Current schema version: `1`
