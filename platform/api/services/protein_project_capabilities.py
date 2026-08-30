"""Closed, server-owned Protein Project capability catalogue.

The catalogue describes product scope separately from launch availability.  A
capability is plannable only when Project Manager can compile and dispatch its
exact typed contract today; catalogued but incomplete integrations stay closed.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


# Compatibility names imported by the existing ESMFold2 Workflow Plan path.
CAPABILITY_ID = "protein.structure_prediction.esmfold2"
ADAPTER_ID = "bms.core-job.esmfold2.adapter.v1"
PARAMETER_SCHEMA_ID = "bms.workflow-parameters.protein.structure_prediction.esmfold2.v1"
SOURCE_PIN = "3a7e99afa19d696baf80ad33d2dcfad80a79d2e0"

_INVENTORY_SCHEMA = "bms.protein-project-capability-inventory.v1"
_PARAMETER_SCHEMA_PREFIX = "bms.workflow-parameters."
_PROTEIN_SEQUENCE_PATTERN = "^[ACDEFGHIKLMNPQRSTVWY]+$"
_DATA_ALIAS_PATTERN = "^data/(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$"
_RECEIPT_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SHA256_PATTERN = "^[0-9a-f]{64}$"


class ProteinProjectCapabilityError(ValueError):
    pass


def _field(
    *,
    title: str,
    kind: str,
    description: str,
    default: Any = None,
    has_default: bool = True,
    const: Any = None,
    enum: list[str] | None = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    exclusive_minimum: int | float | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    pattern: str | None = None,
    items: dict[str, Any] | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
    unique_items: bool | None = None,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    additional_properties: bool | None = None,
    ref: str | None = None,
    control: str = "typed_control",
    group: str = "Scientific settings",
    order: int = 0,
    units: str | None = None,
    applicability: str = "always",
    reproducibility_effect: str = "changes_output",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "title": title,
        "description": description,
        "type": kind,
        "x-bms-ui-control": control,
        "x-bms-ui-group": group,
        "x-bms-ui-order": order,
        "x-bms-applicability": applicability,
        "x-bms-precision": (
            "boolean" if kind == "boolean"
            else "integer" if kind == "integer"
            else "bounded_decimal" if kind == "number"
            else "typed_collection" if kind in {"array", "object"}
            else "exact_utf8_or_enum"
        ),
        "x-bms-persisted-representation": "requested_and_effective",
        "x-bms-reproducibility-effect": reproducibility_effect,
    }
    if has_default:
        value["default"] = default
        value["x-bms-default-policy"] = "schema_default"
    else:
        value["x-bms-default-policy"] = "required_explicit_or_authority_bound"
    if const is not None:
        value["const"] = const
    if enum is not None:
        value["enum"] = enum
    if minimum is not None:
        value["minimum"] = minimum
    if maximum is not None:
        value["maximum"] = maximum
    if exclusive_minimum is not None:
        value["exclusiveMinimum"] = exclusive_minimum
    if min_length is not None:
        value["minLength"] = min_length
    if max_length is not None:
        value["maxLength"] = max_length
    if pattern is not None:
        value["pattern"] = pattern
    if items is not None:
        value["items"] = items
    if min_items is not None:
        value["minItems"] = min_items
    if max_items is not None:
        value["maxItems"] = max_items
    if unique_items is not None:
        value["uniqueItems"] = unique_items
    if properties is not None:
        value["properties"] = properties
    if required is not None:
        value["required"] = required
    if additional_properties is not None:
        value["additionalProperties"] = additional_properties
    if ref is not None:
        value["allOf"] = [{"$ref": ref}]
    if units is not None:
        value["x-bms-units"] = units
    return value


def _schema(
    capability_id: str,
    title: str,
    properties: dict[str, Any],
    required: list[str],
    *,
    authority: str,
    source_contracts: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "$id": f"{_PARAMETER_SCHEMA_PREFIX}{capability_id}.v1",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "type": "object",
        "additionalProperties": False,
        "x-bms-source-pin": SOURCE_PIN,
        "x-bms-executable-authority": authority,
        "x-bms-source-contracts": list(source_contracts or []),
        "x-bms-unknown-fields": "reject_before_preparation",
        "properties": properties,
        "required": required,
    }


def _sequence_fields(*, method: str, name: str) -> tuple[dict[str, Any], list[str]]:
    properties = {
        "sequence": _field(
            title="Protein sequence",
            kind="string",
            description="Canonical one-letter amino-acid sequence.",
            has_default=False,
            min_length=1,
            max_length=10000,
            pattern=_PROTEIN_SEQUENCE_PATTERN,
            control="sequence_editor",
            group="Input",
            order=10,
        ),
        "sequence_name": _field(
            title="Sequence name",
            kind="string",
            description="Stable operator label used for generated structures.",
            default="predicted",
            min_length=1,
            max_length=255,
            control="text",
            group="Input",
            order=20,
            reproducibility_effect="labels_output",
        ),
        "pred_method": _field(
            title="Prediction method",
            kind="string",
            description=f"Server-bound {name} execution identity.",
            default=method,
            const=method,
            control="read_only",
            group="Model authority",
            order=1,
        ),
        "num_parallel_jobs": _field(
            title="Parallel jobs",
            kind="integer",
            description="Number of independent scheduler jobs.",
            default=1,
            minimum=1,
            maximum=32,
            control="integer",
            group="Execution",
            order=90,
        ),
    }
    return properties, list(properties)


def _structure_prediction_schemas() -> dict[str, dict[str, Any]]:
    esm, esm_required = _sequence_fields(method="esmfold2", name="ESMFold2")
    esm["sequence_name"] = _field(
        title="Sequence name",
        kind="string",
        description="Stable operator label used for generated structures.",
        default="Ubiquitin 1UBQ",
        min_length=1,
        max_length=255,
        control="text",
        group="Input",
        order=20,
        reproducibility_effect="labels_output",
    )
    esm["num_parallel_jobs"] = _field(
        title="Parallel jobs",
        kind="integer",
        description="The Project-owned ESMFold2 path launches one scheduler job.",
        default=1,
        const=1,
        minimum=1,
        maximum=1,
        control="read_only",
        group="Execution",
        order=90,
    )
    esm.update({
        "run_frustrampnn": _field(
            title="Run FrustraMPNN",
            kind="boolean",
            description="Project-owned ESMFold2 compatibility keeps analysis disabled at launch.",
            default=False,
            const=False,
            control="read_only",
            group="Analysis",
            order=100,
        ),
        "frustrampnn_requiredness": _field(
            title="FrustraMPNN policy",
            kind="string",
            description="Requiredness used when a governed child analysis is later attached.",
            default="required",
            const="required",
            control="read_only",
            group="Analysis",
            order=110,
        ),
        "model_variant": _field(
            title="ESMFold2 model variant",
            kind="string",
            description="Installed ESMFold2 inference profile.",
            default="fast",
            enum=["fast", "full"],
            control="select",
            group="Model settings",
            order=30,
        ),
        "local_files_only": _field(
            title="Use installed model files only",
            kind="boolean",
            description="Prevents runtime checkpoint downloads.",
            default=True,
            const=True,
            control="read_only",
            group="Model authority",
            order=2,
        ),
    })
    # Preserve the historical required-field order and defaults byte-for-byte in meaning.
    esm_required = [
        "sequence", "sequence_name", "pred_method", "num_parallel_jobs",
        "run_frustrampnn", "frustrampnn_requiredness", "model_variant", "local_files_only",
    ]

    boltz, boltz_required = _sequence_fields(method="boltz2", name="Boltz-2")
    boltz.update({
        "boltz_recycling_steps": _field(title="Recycling steps", kind="integer", description="Boltz-2 recycling iterations.", default=3, minimum=1, maximum=10, control="integer", group="Sampling", order=30),
        "boltz_diffusion_samples": _field(title="Diffusion samples", kind="integer", description="Final ranked candidates generated for this input.", default=1, minimum=1, maximum=32, control="integer", group="Sampling", order=40),
        "boltz_max_parallel_samples": _field(title="Parallel sample chunk", kind="integer", description="Maximum denoiser sample chunk size.", default=1, minimum=1, maximum=32, control="integer", group="Execution", order=50),
        "boltz_sampling_steps": _field(title="Sampling steps", kind="integer", description="Diffusion denoising steps.", default=200, minimum=1, maximum=1000, control="integer", group="Sampling", order=60),
        "boltz_use_msa": _field(title="Use MSA", kind="boolean", description="Use installed MSA features.", default=True, control="checkbox", group="Features", order=70),
        "boltz_method": _field(title="Experimental method conditioning", kind="string", description="Optional Boltz-2 method-conditioning label.", default="", enum=["", "md", "x-ray diffraction", "electron microscopy", "solution nmr", "solid-state nmr", "neutron diffraction", "electron crystallography", "fiber diffraction", "powder diffraction", "infrared spectroscopy", "fluorescence transfer", "epr", "theoretical model", "solution scattering", "other", "afdb", "boltz-1"], control="select", group="Features", order=80),
    })
    boltz_required = list(boltz)

    protenix, protenix_required = _sequence_fields(method="protenix", name="Protenix V2")
    protenix.update({
        "protenix_model_weights": _field(title="Model weights", kind="string", description="Installed Protenix V2 checkpoint.", default="protenix-v2", const="protenix-v2", control="read_only", group="Model authority", order=2),
        "protenix_use_msa": _field(title="Use MSA", kind="boolean", description="Use Protenix MSA features.", default=True, control="checkbox", group="Features", order=30),
        "protenix_msa_backend": _field(title="MSA backend", kind="string", description="Protenix V2 MSA feature source.", default="auto", enum=["auto", "local", "esm", "none"], control="select", group="Features", order=40),
        "protenix_use_template": _field(title="Use templates", kind="boolean", description="Enable template features when available.", default=False, control="checkbox", group="Features", order=50),
        "protenix_seeds": _field(title="Model seeds", kind="string", description="Comma-separated signed model seeds.", default="42", pattern="^-?[0-9]+(?:,-?[0-9]+)*$", control="seed_list", group="Sampling", order=60),
        "protenix_n_sample": _field(title="Samples per seed", kind="integer", description="Diffusion samples generated per seed.", default=1, minimum=1, maximum=16, control="integer", group="Sampling", order=70),
        "protenix_n_step": _field(title="Diffusion steps", kind="integer", description="Protenix V2 diffusion steps.", default=200, minimum=1, maximum=1000, control="integer", group="Sampling", order=80),
        "protenix_n_cycle": _field(title="Recycling cycles", kind="integer", description="Protenix V2 recycling cycles.", default=10, minimum=1, maximum=20, control="integer", group="Sampling", order=90),
    })
    protenix_required = list(protenix)

    return {
        "protein.structure_prediction.esmfold2": _schema("protein.structure_prediction.esmfold2", "Governed ESMFold2 structure prediction settings", esm, esm_required, authority="project_manager_typed_launcher_handoff"),
        "protein.structure_prediction.boltz2": _schema("protein.structure_prediction.boltz2", "Governed Boltz-2 structure prediction settings", boltz, boltz_required, authority="project_manager_typed_launcher_handoff"),
        "protein.structure_prediction.protenix_v2": _schema("protein.structure_prediction.protenix_v2", "Governed Protenix V2 structure prediction settings", protenix, protenix_required, authority="project_manager_typed_launcher_handoff"),
    }


def _source_receipt_field(title: str = "Source structure receipt") -> dict[str, Any]:
    return _field(
        title=title,
        kind="string",
        description="Immutable server-issued receipt for the selected protein structure.",
        has_default=False,
        min_length=1,
        max_length=128,
        pattern=_RECEIPT_ID_PATTERN,
        control="protein_structure_receipt_selector",
        group="Input",
        order=10,
    )


def _model_sequence_design_schema(capability_id: str, model: str) -> dict[str, Any]:
    source = {"source_structure_receipt_id": _source_receipt_field()}
    if model == "fampnn":
        source.update({
            "mode": _field(title="Design mode", kind="string", description="FA-MPNN design role.", default="design", enum=["design", "fixed_backbone", "binder_design"], control="select", group="Design", order=20),
            "design_chain": _field(title="Design chains", kind="string", description="Comma-separated source chain identifiers to redesign.", default="A", pattern="^[A-Za-z0-9]+(?:,[A-Za-z0-9]+)*$", control="chain_selector", group="Design", order=30),
            "target_chain": _field(title="Fixed target chains", kind="string", description="Optional fixed target chain identifiers.", default="", pattern="^(?:|[A-Za-z0-9]+(?:,[A-Za-z0-9]+)*)$", control="chain_selector", group="Design", order=40, applicability="binder_design"),
            "fixed_positions": _field(title="Fixed residues", kind="string", description="Typed chain/residue ranges retained during redesign.", default="", control="residue_range_selector", group="Constraints", order=50, applicability="fixed_backbone"),
            "fampnn_temperature": _field(title="Sampling temperature", kind="number", description="FA-MPNN sequence sampling temperature.", default=0.1, minimum=0.01, maximum=2.0, control="bounded_number", group="Sampling", order=60),
            "fampnn_exclude_cys": _field(title="Exclude cysteine", kind="boolean", description="Exclude cysteine from sampled positions.", default=True, control="checkbox", group="Constraints", order=70),
            "fampnn_fix_target_sidechains": _field(title="Fix target sidechains", kind="boolean", description="Keep target sidechain coordinates fixed.", default=False, control="checkbox", group="Constraints", order=80),
            "fampnn_psce_threshold": _field(title="PSCE threshold", kind="number", description="Sidechain-confidence filtering threshold.", default=0.3, minimum=0.0, maximum=1.0, control="bounded_number", group="Filtering", order=90),
            "fampnn_seq_only": _field(title="Sequence-only mode", kind="boolean", description="Skip sidechain diffusion.", default=False, control="checkbox", group="Sampling", order=100),
            "fampnn_num_steps": _field(title="Denoising steps", kind="integer", description="FA-MPNN denoising timesteps.", default=100, minimum=10, maximum=500, control="integer", group="Sampling", order=110),
            "fampnn_batch_size": _field(title="Batch size", kind="integer", description="FA-MPNN inference batch size.", default=16, minimum=1, maximum=64, control="integer", group="Execution", order=120),
            "fampnn_repack_last": _field(title="Final sidechain repack", kind="boolean", description="Repack sidechains after denoising.", default=True, control="checkbox", group="Sampling", order=130),
        })
    elif model == "proteinmpnn":
        source.update({
            "mpnn_temperature": _field(title="Sampling temperature", kind="number", description="ProteinMPNN sampling temperature.", default=0.1, minimum=0.01, maximum=2.0, control="bounded_number", group="Sampling", order=20),
            "mpnn_omitAAs": _field(title="Excluded amino acids", kind="string", description="One-letter amino acids excluded from sampling.", default="CX", pattern="^[ACDEFGHIKLMNPQRSTVWYX]*$", control="amino_acid_selector", group="Constraints", order=30),
            "mpnn_checkpoint_type": _field(title="Checkpoint family", kind="string", description="Installed ProteinMPNN checkpoint family.", default="soluble", enum=["vanilla", "soluble"], control="select", group="Model authority", order=40),
            "mpnn_checkpoint_model": _field(title="Checkpoint noise model", kind="string", description="Installed ProteinMPNN backbone-noise checkpoint.", default="v_48_020", enum=["v_48_002", "v_48_010", "v_48_020", "v_48_030"], control="select", group="Model authority", order=50),
            "mpnn_backbone_noise": _field(title="Backbone noise", kind="number", description="Gaussian backbone noise used during design.", default=0, minimum=0, maximum=1, control="bounded_number", group="Sampling", order=60, units="angstrom"),
            "mpnn_relax_max_cycles": _field(title="FastRelax cycles", kind="integer", description="Maximum post-design FastRelax cycles; zero disables refinement.", default=0, minimum=0, maximum=10, control="integer", group="Refinement", order=70),
        })
    else:
        source.update({
            "mode": _field(title="LigandMPNN mode", kind="string", description="Foundry-owned LigandMPNN scientific context.", default="ligand_aware", enum=["ligand_aware", "ntp_aware", "metal_aware", "dna_aware"], control="select", group="Design", order=20),
            "temperature": _field(title="Sampling temperature", kind="number", description="LigandMPNN sampling temperature.", default=0.1, minimum=0.01, maximum=2.0, control="bounded_number", group="Sampling", order=30),
            "ntp_type": _field(title="Nucleotide triphosphate", kind="string", description="NTP identity for nucleotide-aware design.", default="dATP", enum=["dATP", "dTTP", "dGTP", "dCTP", "ATP", "UTP", "GTP", "CTP"], control="select", group="Molecular context", order=40, applicability="ntp_aware"),
            "metal_type": _field(title="Metal ion", kind="string", description="Metal identity for coordination-aware design.", default="Mg2+", enum=["Mg2+", "Mn2+", "Zn2+", "Ca2+", "Fe2+", "Fe3+", "Cu2+"], control="select", group="Molecular context", order=50, applicability="metal_aware"),
            "dna_sequence": _field(title="DNA context", kind="string", description="DNA sequence used as design context.", default="", pattern="^[ACGTN]*$", control="sequence_editor", group="Molecular context", order=60, applicability="dna_aware"),
        })
    return _schema(capability_id, f"Governed {model} sequence-design settings", source, list(source), authority="catalogued_model_contract_without_project_dispatch")


def _closed_unavailable_schema(capability_id: str, title: str, reason: str) -> dict[str, Any]:
    schema = _schema(capability_id, title, {}, [], authority="unavailable")
    schema["x-bms-unavailable-reason"] = reason
    return schema


def _specialized_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}

    local_fields = {
        "input_structure": _field(title="Source structure alias", kind="string", description="Canonical public data-root alias resolved from the selected immutable structure.", has_default=False, pattern=_DATA_ALIAS_PATTERN, control="protein_structure_selector", group="Input", order=10),
        "redesign_mode": _field(title="Redesign mode", kind="string", description="Native RFD3 local-redesign behavior.", default="partial_diffusion", enum=["partial_diffusion", "minimal_insertion"], control="select", group="RFD3 redesign", order=20),
        "design_chains": _field(title="Design chains", kind="array", description="Source chains containing editable residues.", default=[], items={"type": "string", "minLength": 1, "maxLength": 8}, max_items=64, unique_items=True, control="chain_selector", group="RFD3 redesign", order=30),
        "context_chains": _field(title="Context chains", kind="array", description="Source chains retained as fixed molecular context.", default=[], items={"type": "string", "minLength": 1, "maxLength": 8}, max_items=64, unique_items=True, control="chain_selector", group="RFD3 redesign", order=40),
        "redesign_ranges": _field(title="Editable residue ranges", kind="string", description="Explicit source residue ranges for partial diffusion.", default="", control="residue_range_selector", group="RFD3 redesign", order=50, applicability="partial_diffusion"),
        "sequence_policy": _field(title="Sequence policy", kind="string", description="Amino-acid identity handling during coordinate redesign.", default="preserve", enum=["preserve", "explicit_positions", "insert_only"], control="select", group="Sequence policy", order=60),
        "select_unfixed_sequence": _field(title="Unfixed sequence positions", kind="string", description="Source residues whose amino-acid identities RFD3 may recall.", default="", control="residue_range_selector", group="Sequence policy", order=70, applicability="explicit_positions"),
        "insertion_anchor": _field(title="Insertion anchor", kind="string", description="Source residue immediately before inserted material.", default="", control="residue_selector", group="RFD3 redesign", order=80, applicability="minimal_insertion"),
        "insertion_min_length": _field(title="Minimum insertion length", kind="integer", description="Minimum inserted segment length.", default=1, minimum=1, maximum=512, control="integer", group="RFD3 redesign", order=90, applicability="minimal_insertion", units="residues"),
        "insertion_max_length": _field(title="Maximum insertion length", kind="integer", description="Maximum inserted segment length.", default=1, minimum=1, maximum=512, control="integer", group="RFD3 redesign", order=100, applicability="minimal_insertion", units="residues"),
        "partial_t": _field(title="Coordinate noise", kind="number", description="RFD3 partial-diffusion coordinate-noise magnitude.", default=2.0, minimum=0.0, maximum=1000000.0, control="bounded_number", group="Sampling", order=110),
        "ligand": _field(title="Ligand components", kind="string", description="CCD/component identifiers retained as molecular context.", default="", control="component_selector", group="Molecular context", order=120),
        "num_designs": _field(title="Candidate count", kind="integer", description="Number of RFD3 candidates.", default=8, minimum=1, maximum=512, control="integer", group="Execution", order=130),
        "seed": _field(title="Seed", kind="integer", description="Effective RFD3 random seed.", default=0, minimum=0, maximum=2147483647, control="integer", group="Execution", order=140),
        "dump_trajectories": _field(title="Retain trajectories", kind="boolean", description="Retain native noisy and denoised trajectories.", default=False, control="checkbox", group="Artifacts", order=150),
        "write_full_json": _field(title="Retain full metadata", kind="boolean", description="Retain complete native RFD3 prediction metadata.", default=True, control="checkbox", group="Artifacts", order=160),
        "profile_id": _field(title="Redesign profile", kind="string", description="Server-defined protected-context and acceptance profile.", default="generic_local_redesign_v1", enum=["generic_local_redesign_v1", "drt4_datp_gate_v1"], control="select", group="Acceptance", order=170),
    }
    schemas["protein.de_novo.local_redesign"] = _schema("protein.de_novo.local_redesign", "Governed RFD3 local-redesign settings", local_fields, list(local_fields), authority="typed_core_job_outside_project_manager", source_contracts=["bms.rfd3.local-redesign.request.v1"])

    schemas["protein.sequence_design.fampnn"] = _model_sequence_design_schema("protein.sequence_design.fampnn", "fampnn")
    schemas["protein.sequence_design.proteinmpnn"] = _model_sequence_design_schema("protein.sequence_design.proteinmpnn", "proteinmpnn")
    schemas["protein.sequence_design.foundry_ligandmpnn"] = _model_sequence_design_schema("protein.sequence_design.foundry_ligandmpnn", "foundry_ligandmpnn")

    for capability_id, backend, title in (
        ("protein.conformational_mapping.protenix_v2", "protenix_v2_ensemble", "Protenix V2 conformational mapping"),
        ("protein.conformational_mapping.confornets", "confornets", "ConforNets conformational mapping"),
    ):
        fields = {
            "backend": _field(title="Mapping backend", kind="string", description="Exact canonical conformational-mapping backend.", default=backend, const=backend, control="read_only", group="Model authority", order=1),
            "request": _field(title="Conformational mapping request", kind="object", description="Typed canonical conformational-mapping request.", has_default=False, ref="https://biomodstack.org/schemas/conformational_mapping/cm_request_v1.schema.json", control="typed_cm_request", group="Scientific settings", order=10),
        }
        schemas[capability_id] = _schema(capability_id, title, fields, list(fields), authority="dedicated_conformational_mapping_api", source_contracts=["cm_request_v1"])

    md_fields = {
        "engine": _field(title="MD engine", kind="string", description="GROMACS is the Protein Project molecular-dynamics authority.", default="gromacs", const="gromacs", control="read_only", group="Engine authority", order=1),
        "md_job": _field(title="Molecular-dynamics job", kind="object", description="Typed canonical MD v2 request compiled by the dedicated launcher.", has_default=False, ref="https://biomodstack.local/schemas/md_job_v2.schema.json", control="typed_md_request", group="Scientific settings", order=10),
    }
    schemas["protein.simulation.gromacs_md"] = _schema("protein.simulation.gromacs_md", "Governed GROMACS molecular-dynamics settings", md_fields, list(md_fields), authority="dedicated_md_api_feature_gated", source_contracts=["bms.md.job.v2"])

    frustr_fields = {
        "source_structure_receipt_id": _source_receipt_field(),
        "requested_settings": _field(title="FrustraMPNN settings", kind="object", description="Canonical typed FrustraMPNN requested-settings contract.", has_default=False, ref="https://biomodstack.org/schemas/frustrampnn/settings_v2.schema.json", control="typed_frustrampnn_settings", group="Analysis", order=20),
    }
    schemas["protein.analysis.frustrampnn"] = _schema("protein.analysis.frustrampnn", "Governed FrustraMPNN analysis settings", frustr_fields, list(frustr_fields), authority="scheduler_owned_child_component", source_contracts=["frustrampnn_settings_v2"])

    comparison_member = {
        "type": "object",
        "additionalProperties": False,
        "required": ["result_receipt_id", "content_sha256"],
        "properties": {
            "result_receipt_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _RECEIPT_ID_PATTERN},
            "content_sha256": {"type": "string", "pattern": _SHA256_PATTERN},
        },
    }
    compare_fields = {
        "members": _field(title="Protein results", kind="array", description="Immutable compatible protein-result members.", has_default=False, items=comparison_member, min_items=2, max_items=64, unique_items=True, control="protein_result_multiselect", group="Comparison", order=10),
    }
    schemas["protein.comparison.compatible_results"] = _schema("protein.comparison.compatible_results", "Compatible Protein result comparison", compare_fields, list(compare_fields), authority="result_compatibility_only_no_project_dispatch")

    recipe_fields = {
        "source_structure_receipt_id": _source_receipt_field("Polymerase structure receipt"),
        "engineering_objective": _field(title="Engineering objective", kind="string", description="Typed DNA-polymerase engineering objective.", default="substrate_specificity", enum=["substrate_specificity", "fidelity", "processivity", "thermostability"], control="select", group="Recipe objective", order=20),
        "nucleotide_context": _field(title="Nucleotide context", kind="string", description="Nucleotide triphosphate context for ligand-aware design.", default="dATP", enum=["dATP", "dTTP", "dGTP", "dCTP"], control="select", group="Molecular context", order=30),
        "variant_scope": _field(title="Variant scope", kind="string", description="Typed residue scope for candidate exploration.", has_default=False, control="residue_range_selector", group="Variant exploration", order=40),
        "sequence_designers": _field(title="Sequence designers", kind="array", description="Closed recipe sequence-design stages.", default=["fampnn", "foundry_ligandmpnn"], items={"enum": ["fampnn", "proteinmpnn", "foundry_ligandmpnn"]}, min_items=1, max_items=3, unique_items=True, control="multi_select", group="Recipe stages", order=50),
        "structure_validators": _field(title="Structure validators", kind="array", description="Accepted Protein structure validators.", default=["boltz2", "esmfold2", "protenix_v2"], items={"enum": ["boltz2", "esmfold2", "protenix_v2"]}, min_items=1, max_items=3, unique_items=True, control="multi_select", group="Recipe stages", order=60),
        "run_gromacs_md": _field(title="Run GROMACS MD", kind="boolean", description="Request a GROMACS molecular-dynamics stage after structure validation.", default=False, control="checkbox", group="Recipe stages", order=70),
        "run_frustrampnn": _field(title="Run FrustraMPNN", kind="boolean", description="Request FrustraMPNN analysis and mutation guidance.", default=True, control="checkbox", group="Recipe stages", order=80),
    }
    schemas["protein.recipe.dna_polymerase_engineering"] = _schema("protein.recipe.dna_polymerase_engineering", "DNA Polymerase Engineering recipe", recipe_fields, list(recipe_fields), authority="typed_recipe_without_atomic_orchestration")

    unavailable = {
        "protein.de_novo.rfd3": ("RFD3 general de novo design", "No Project-owned general RFD3 launcher is installed; local redesign is a separate child capability."),
        "protein.variant_exploration": ("Mutation and variant exploration", "No single typed variant exploration compiler and dispatch authority is installed."),
        "protein.design.antibody": ("Antibody design", "The installed antibody workflow remains experimental and is not an accepted Project validator pipeline."),
        "protein.design.nanobody": ("Nanobody design", "The installed nanobody workflow remains experimental and has no accepted Project launch contract."),
        "protein.analysis.frustrampnn_comparison": ("FrustraMPNN comparison", "Comparison is result-owned and has no independent Project workflow dispatcher."),
        "protein.analysis.frustrampnn_guidance": ("FrustraMPNN guidance", "Guidance is derived from immutable analysis results and has no independent Project workflow dispatcher."),
    }
    for capability_id, (title, reason) in unavailable.items():
        schemas[capability_id] = _closed_unavailable_schema(capability_id, title, reason)
    return schemas


_PARAMETER_SCHEMAS = {**_structure_prediction_schemas(), **_specialized_schemas()}


def _capability(
    capability_id: str,
    *,
    label: str,
    family: str,
    category: str,
    role: str,
    allowed_modes: list[str],
    plannable: bool,
    exposure_state: str,
    availability_state: str,
    availability_reason: str | None,
    workflow_family: str | None,
    workflow_adapter_id: str | None,
    launch_mode: str,
    destination: str | None,
    model_modes: list[dict[str, str]],
    result_adapter_ids: list[str],
    result_contracts: list[str],
    viewer_id: str | None,
    accepted_source_roles: list[str],
    receipt_contracts: list[str],
    parent_capability_id: str | None = None,
    execution_owner: str = "biomodstack",
    allowed_as_validator: bool = False,
    validator_domain_modes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "capability_version": "1",
        "label": label,
        "product_taxonomy": {
            "domain": "protein",
            "family": family,
            "category": category,
            "parent_capability_id": parent_capability_id,
        },
        "scientific_role": role,
        "allowed_as_validator": allowed_as_validator,
        "validator_domain_modes": list(validator_domain_modes or []),
        "applicability": {"domain_kind": "protein_in_silico", "experiment_modes": allowed_modes},
        "plannable": plannable,
        "exposure_state": exposure_state,
        "availability": {"state": availability_state, "reason": availability_reason},
        "allowed_domain_modes": allowed_modes,
        "workflow_family": workflow_family,
        "workflow_adapter_id": workflow_adapter_id,
        "launch_mode": launch_mode,
        "canonical_source_destination": destination,
        "parameter_schema_id": _PARAMETER_SCHEMAS[capability_id]["$id"],
        "allowed_model_modes": model_modes,
        "execution_owner": execution_owner,
        "accepted_source_roles": accepted_source_roles,
        "receipt_contracts": receipt_contracts,
        "result_adapter_ids": result_adapter_ids,
        "result_contracts": result_contracts,
        "viewer_id": viewer_id,
    }


_CAPABILITIES = [
    _capability("protein.structure_prediction.boltz2", label="Boltz-2 structure prediction", family="structure_prediction", category="structure_prediction", role="folding_structure_prediction", allowed_modes=["prediction", "validation", "design", "redesign", "exploration"], plannable=True, exposure_state="accepted", availability_state="operational", availability_reason=None, workflow_family="typed_core_job", workflow_adapter_id="bms.core-job.boltz2.adapter.v1", launch_mode="typed_launcher_handoff", destination="/submit?template=structure_prediction", model_modes=[{"model_id": "boltz2", "mode": "predict"}], result_adapter_ids=["bms.core-job.boltz2.adapter.v1", "bms.core.protein-result-reference.adapter.v1"], result_contracts=["structure_prediction_v1", "typed_core_job_result"], viewer_id="structure_viewer", accepted_source_roles=["target_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"], allowed_as_validator=True, validator_domain_modes=["design", "redesign", "exploration", "prediction", "validation"]),
    _capability("protein.structure_prediction.esmfold2", label="ESMFold2 structure prediction", family="structure_prediction", category="structure_prediction", role="folding_structure_prediction", allowed_modes=["prediction", "validation", "design", "redesign", "exploration"], plannable=True, exposure_state="accepted", availability_state="operational", availability_reason=None, workflow_family="typed_core_job", workflow_adapter_id=ADAPTER_ID, launch_mode="typed_launcher_handoff", destination="/submit?template=structure_prediction", model_modes=[{"model_id": "esmfold2", "mode": "predict"}], result_adapter_ids=[ADAPTER_ID, "bms.core.protein-result-reference.adapter.v1"], result_contracts=["core_job_result", "typed_core_job_result"], viewer_id="structure_viewer", accepted_source_roles=["target_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"], allowed_as_validator=True, validator_domain_modes=["design", "redesign", "exploration", "prediction", "validation"]),
    _capability("protein.structure_prediction.protenix_v2", label="Protenix V2 structure prediction", family="structure_prediction", category="structure_prediction", role="folding_structure_prediction", allowed_modes=["prediction", "validation", "design", "redesign", "exploration"], plannable=True, exposure_state="accepted", availability_state="operational", availability_reason=None, workflow_family="typed_core_job", workflow_adapter_id="bms.core-job.protenix.adapter.v1", launch_mode="typed_launcher_handoff", destination="/submit?template=structure_prediction", model_modes=[{"model_id": "protenix", "mode": "predict"}], result_adapter_ids=["bms.core-job.protenix.adapter.v1", "bms.core.protein-result-reference.adapter.v1"], result_contracts=["structure_prediction_v1", "typed_core_job_result"], viewer_id="structure_viewer", accepted_source_roles=["target_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"], allowed_as_validator=True, validator_domain_modes=["design", "redesign", "exploration", "prediction", "validation"]),
    _capability("protein.de_novo.rfd3", label="RFD3 de novo design", family="de_novo_design", category="generative_design", role="general_de_novo_generation", allowed_modes=["design"], plannable=False, exposure_state="unavailable", availability_state="unavailable", availability_reason="No Project-owned general RFD3 launcher is installed.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[], result_adapter_ids=[], result_contracts=["de_novo_generation_v1"], viewer_id="structure_viewer", accepted_source_roles=["target_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.de_novo.local_redesign", label="RFD3 local redesign", family="de_novo_design", category="local_redesign", role="structure_conditioned_local_redesign", allowed_modes=["redesign", "design"], plannable=False, exposure_state="accepted", availability_state="operational_outside_project_manager", availability_reason="The native typed core Job exists, but generic Protein capability planning is not connected to it.", workflow_family="typed_core_job", workflow_adapter_id="bms.core-job.protein_local_redesign.adapter.v1", launch_mode="typed_launcher_handoff", destination="/submit?template=protein_local_redesign", model_modes=[{"model_id": "protein_local_redesign", "mode": "local_redesign"}], result_adapter_ids=["bms.core-job.protein_local_redesign.adapter.v1"], result_contracts=["rfd3_local_redesign_v1", "protein_local_redesign_validation_v1"], viewer_id="structure_viewer", accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"], parent_capability_id="protein.de_novo.rfd3"),
    _capability("protein.variant_exploration", label="Mutation and variant exploration", family="variant_exploration", category="protein_engineering", role="mutation_variant_exploration", allowed_modes=["exploration", "redesign", "analysis"], plannable=False, exposure_state="unavailable", availability_state="unavailable", availability_reason="No closed variant compiler and dispatch authority is installed.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[], result_adapter_ids=[], result_contracts=[], viewer_id=None, accepted_source_roles=["source_structure_receipt", "source_sequence_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.sequence_design.fampnn", label="FA-MPNN sequence design", family="sequence_design", category="sequence_design", role="full_atom_sequence_design", allowed_modes=["design", "redesign"], plannable=False, exposure_state="catalogued", availability_state="unavailable", availability_reason="No Project-owned FA-MPNN workflow adapter is registered.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[{"model_id": "fampnn", "mode": "design"}, {"model_id": "fampnn", "mode": "fixed_backbone"}, {"model_id": "fampnn", "mode": "binder_design"}], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=["sequence_design_v1"], viewer_id="structure_viewer", accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.sequence_design.proteinmpnn", label="ProteinMPNN sequence design", family="sequence_design", category="sequence_design", role="backbone_conditioned_sequence_design", allowed_modes=["design", "redesign"], plannable=False, exposure_state="catalogued", availability_state="unavailable", availability_reason="No Project-owned ProteinMPNN workflow adapter is registered.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[{"model_id": "proteinmpnn", "mode": "design"}], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=["sequence_design_v1"], viewer_id="structure_viewer", accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.sequence_design.foundry_ligandmpnn", label="Foundry LigandMPNN sequence design", family="sequence_design", category="ligand_aware_sequence_design", role="ligand_aware_sequence_design", allowed_modes=["design", "redesign"], plannable=False, exposure_state="catalogued", availability_state="unavailable", availability_reason="Foundry owns LigandMPNN, but no Project-owned Foundry dispatch adapter is registered.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[{"model_id": "ligandmpnn", "mode": "ligand_aware"}, {"model_id": "ligandmpnn", "mode": "ntp_aware"}, {"model_id": "ligandmpnn", "mode": "metal_aware"}, {"model_id": "ligandmpnn", "mode": "dna_aware"}], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=["sequence_design_v1"], viewer_id="structure_viewer", accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"], execution_owner="foundry"),
    _capability("protein.design.antibody", label="Antibody design", family="antibody_nanobody_design", category="antibody_design", role="antibody_design", allowed_modes=["design", "redesign"], plannable=False, exposure_state="unavailable", availability_state="unavailable", availability_reason="The installed antibody pipeline remains experimental and is not an accepted Project launch authority.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=["antibody_backbone_v1", "sequence_design_v1"], viewer_id="structure_viewer", accepted_source_roles=["target_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.design.nanobody", label="Nanobody design", family="antibody_nanobody_design", category="nanobody_design", role="nanobody_design", allowed_modes=["design", "redesign"], plannable=False, exposure_state="unavailable", availability_state="unavailable", availability_reason="The installed nanobody pipeline remains experimental and is not an accepted Project launch authority.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=["antibody_backbone_v1", "sequence_design_v1"], viewer_id="structure_viewer", accepted_source_roles=["target_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.conformational_mapping.protenix_v2", label="Protenix V2 conformational mapping", family="conformational_mapping", category="ensemble_generation", role="conformational_hypothesis_mapping", allowed_modes=["exploration", "analysis"], plannable=False, exposure_state="accepted", availability_state="operational_outside_project_manager", availability_reason="The dedicated CM API is operational; Protein Project plan compilation is not connected to it.", workflow_family="conformational_mapping", workflow_adapter_id="bms.cm.protenix_v2.adapter.v1", launch_mode="managed_dispatch", destination="/api/conformational-mapping/requests", model_modes=[{"model_id": "conformational_mapping", "mode": "map"}], result_adapter_ids=["bms.cm.protenix_v2.adapter.v1"], result_contracts=["conformational_mapping_protenix_v1", "conformational_mapping_analysis_v1"], viewer_id="conformational_mapping_viewer", accepted_source_roles=["complete_complex_snapshot_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.conformational_mapping.confornets", label="ConforNets conformational mapping", family="conformational_mapping", category="ensemble_generation", role="conformational_hypothesis_mapping", allowed_modes=["exploration", "analysis"], plannable=False, exposure_state="accepted", availability_state="operational_outside_project_manager", availability_reason="The dedicated CM API is operational; Protein Project plan compilation is not connected to it.", workflow_family="conformational_mapping", workflow_adapter_id="bms.cm.confornets.adapter.v1", launch_mode="managed_dispatch", destination="/api/conformational-mapping/requests", model_modes=[{"model_id": "conformational_mapping", "mode": "map"}], result_adapter_ids=["bms.cm.confornets.adapter.v1"], result_contracts=["conformational_mapping_confornets_v1", "conformational_mapping_analysis_v1"], viewer_id="conformational_mapping_viewer", accepted_source_roles=["protein_sequence_receipt", "confornets_checkpoint_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.simulation.gromacs_md", label="GROMACS molecular dynamics", family="molecular_dynamics", category="simulation", role="molecular_dynamics", allowed_modes=["simulation"], plannable=False, exposure_state="experimental", availability_state="feature_gated", availability_reason="The dedicated typed GROMACS launcher is default-off and Project plan compilation is not connected to it.", workflow_family="typed_core_job", workflow_adapter_id="bms.core-job.molecular_dynamics.adapter.v1", launch_mode="typed_launcher_handoff", destination="/api/molecular-dynamics/launch", model_modes=[{"model_id": "molecular_dynamics", "mode": "simulate"}], result_adapter_ids=["bms.md.result-reference.adapter.v1"], result_contracts=["md_run_v1", "md_analysis_v1"], viewer_id=None, accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.analysis.frustrampnn", label="FrustraMPNN analysis", family="frustrampnn", category="analysis", role="structure_analysis", allowed_modes=["analysis", "exploration", "design", "redesign", "prediction", "validation"], plannable=False, exposure_state="integrated_component", availability_state="operational_as_child", availability_reason="FrustraMPNN is a scheduler-owned child component, not an independent structure generator or generic direct launch.", workflow_family=None, workflow_adapter_id=None, launch_mode="scheduler_owned_child", destination="/api/frustrampnn", model_modes=[{"model_id": "frustrampnn", "mode": "analyze"}], result_adapter_ids=["bms.frustrampnn.result-reference.adapter.v1"], result_contracts=["frustration_analysis_v1"], viewer_id="frustration_landscape", accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.analysis.frustrampnn_comparison", label="FrustraMPNN comparison", family="frustrampnn", category="comparison", role="frustration_landscape_comparison", allowed_modes=["comparison", "analysis"], plannable=False, exposure_state="result_action", availability_state="operational_from_results", availability_reason="Comparison is created from compatible immutable FrustraMPNN results, not launched as a Project workflow.", workflow_family=None, workflow_adapter_id=None, launch_mode="result_action", destination="/api/frustrampnn/comparisons", model_modes=[], result_adapter_ids=["bms.frustrampnn.comparison-reference.adapter.v1"], result_contracts=["frustrampnn_comparison_v1"], viewer_id="frustration_landscape", accepted_source_roles=["frustrampnn_result_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.analysis.frustrampnn_guidance", label="FrustraMPNN guidance", family="frustrampnn", category="guidance", role="mutagenesis_guidance", allowed_modes=["analysis", "exploration", "redesign"], plannable=False, exposure_state="result_action", availability_state="operational_from_results", availability_reason="Guidance is derived from an immutable FrustraMPNN result, not launched as a Project workflow.", workflow_family=None, workflow_adapter_id=None, launch_mode="result_action", destination="/api/frustrampnn/guidance", model_modes=[], result_adapter_ids=["bms.frustrampnn.guidance-reference.adapter.v1"], result_contracts=["frustrampnn_guidance_v1"], viewer_id="residue_mapping", accepted_source_roles=["frustrampnn_result_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.comparison.compatible_results", label="Compatible Protein result comparison", family="protein_comparison", category="comparison", role="compatible_result_comparison", allowed_modes=["comparison", "analysis"], plannable=False, exposure_state="result_action", availability_state="compatibility_gated", availability_reason="Comparison is available only through result-owned compatibility contracts; no independent workflow is fabricated.", workflow_family=None, workflow_adapter_id=None, launch_mode="result_action", destination=None, model_modes=[], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=[], viewer_id=None, accepted_source_roles=["protein_result_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
    _capability("protein.recipe.dna_polymerase_engineering", label="DNA Polymerase Engineering", family="protein_recipe", category="typed_recipe", role="dna_polymerase_engineering", allowed_modes=["design", "redesign", "exploration"], plannable=False, exposure_state="catalogued", availability_state="unavailable", availability_reason="The typed recipe is defined, but no atomic Project orchestration authority spans its required stages.", workflow_family=None, workflow_adapter_id=None, launch_mode="unavailable", destination=None, model_modes=[], result_adapter_ids=["bms.core.protein-result-reference.adapter.v1"], result_contracts=[], viewer_id=None, accepted_source_roles=["source_structure_receipt"], receipt_contracts=["bms.global.external-entity-receipt.v1"]),
]

_CAPABILITY_BY_ID = {record["capability_id"]: record for record in _CAPABILITIES}
if len(_CAPABILITY_BY_ID) != len(_CAPABILITIES):
    raise RuntimeError("duplicate Protein Project capability ID")
if set(_CAPABILITY_BY_ID) != set(_PARAMETER_SCHEMAS):
    raise RuntimeError("Protein Project capability and parameter-schema registries disagree")


def protein_capability_inventory() -> dict[str, Any]:
    payload = {"schema": _INVENTORY_SCHEMA, "capabilities": _CAPABILITIES}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return copy.deepcopy({**payload, "content_sha256": hashlib.sha256(canonical.encode()).hexdigest()})


def protein_capability_record(capability_id: str) -> dict[str, Any]:
    try:
        capability = _CAPABILITY_BY_ID[capability_id]
    except KeyError as exc:
        raise ProteinProjectCapabilityError(
            f"unknown Protein Project capability: {capability_id}"
        ) from exc
    return copy.deepcopy(capability)


def protein_parameter_schema(capability_id: str) -> dict[str, Any]:
    try:
        schema = _PARAMETER_SCHEMAS[capability_id]
    except KeyError as exc:
        raise ProteinProjectCapabilityError(
            f"unknown Protein Project capability: {capability_id}"
        ) from exc
    return copy.deepcopy(schema)
