"""
Models API router - List available models and their configurations.
"""

from fastapi import APIRouter, HTTPException
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from model_registry import get_registry, ModelDefinition
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    complete_requested_settings_schema,
    default_settings,
    load_capability_inventory,
)


router = APIRouter()


class ModelIntegrationWorkflowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_enabled: bool
    enabled_summary: str


class ModelIntegrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    model_name: str
    model_version: str
    stage_parameter: str
    operator_label: str
    checkpoint_label: str | None = None
    model_summary: str
    semantic_roles: list[str]
    workflows: dict[str, ModelIntegrationWorkflowResponse]


class FrustraMPNNProductControlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    control_kind: str | None = None
    api_type: str | None = None


class FrustraMPNNPublicPredictOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_key: str
    ownership_class: str
    scientific_or_inference_setting: bool
    product_control: FrustraMPNNProductControlResponse
    model_native_default: str | None = None
    disposition: str


class FrustraMPNNCapabilityInventoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str
    schema_version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predict_options: list[FrustraMPNNPublicPredictOptionResponse]


class FrustraMPNNParameterDescriptorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    api_type: str
    ownership: str
    control_kind: str
    backing: str
    default_source: str


class FrustraMPNNCompatibilityRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    fields: list[str]
    requirement: str


class FrustraMPNNIntegrationResponse(ModelIntegrationResponse):
    capability_inventory: FrustraMPNNCapabilityInventoryResponse
    capability_inventory_byte_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_inventory_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings_schema: JsonValue
    canonical_defaults: FrustraMPNNRequestedSettings
    parameter_descriptors: list[FrustraMPNNParameterDescriptorResponse]
    field_ownership: dict[str, str]
    control_kind_hints: dict[str, str]
    compatibility_rules: list[FrustraMPNNCompatibilityRuleResponse]


def _frustrampnn_discovery_metadata() -> dict:
    inventory, byte_sha256 = load_capability_inventory()
    public_options = [
        {
            "option_key": option["option_key"],
            "ownership_class": option["ownership_class"],
            "scientific_or_inference_setting": option["scientific_or_inference_setting"],
            "product_control": option["product_control"],
            "model_native_default": option["model_native_default"],
            "disposition": option["disposition"],
        }
        for option in inventory["predict_options"]
        if option["product_control"]["status"] == "typed_product_control"
    ]
    descriptors = [
        {
            "field": "source_artifact",
            "api_type": "governed_artifact_reference",
            "ownership": "workflow_source",
            "control_kind": "governed_source_selector_or_upload",
            "backing": "pdb",
            "default_source": "owned_source",
        },
        {
            "field": "protein_selection.mode",
            "api_type": "closed_string",
            "ownership": "scientific_operator",
            "control_kind": "selection_mode",
            "backing": "chains",
            "default_source": "bms_default",
        },
        {
            "field": "protein_selection.entities",
            "api_type": "array_of_stable_entity_references",
            "ownership": "scientific_operator",
            "control_kind": "entity_multi_selector",
            "backing": "chains",
            "default_source": "bms_default",
        },
        {
            "field": "protein_selection.residues",
            "api_type": "array_of_stable_residue_references",
            "ownership": "scientific_operator",
            "control_kind": "residue_multi_selector",
            "backing": "positions",
            "default_source": "bms_default",
        },
        {
            "field": "source_structure.selected_model_number",
            "api_type": "integer",
            "ownership": "bms_source_interpretation",
            "control_kind": "source_model_selector",
            "backing": "bms_source_interpretation",
            "default_source": "source_metadata",
        },
        {
            "field": "source_structure.preferred_altloc",
            "api_type": "closed_string",
            "ownership": "bms_source_interpretation",
            "control_kind": "alternate_location_selector",
            "backing": "bms_source_interpretation",
            "default_source": "source_metadata",
        },
        {
            "field": "classification_policy.mode",
            "api_type": "closed_string",
            "ownership": "bms_classification_interpretation",
            "control_kind": "classification_mode_selector",
            "backing": "bms_classification_interpretation",
            "default_source": "bms_default",
        },
        {
            "field": "classification_policy.high_max",
            "api_type": "finite_number",
            "ownership": "bms_classification_interpretation",
            "control_kind": "numeric_input",
            "backing": "bms_classification_interpretation",
            "default_source": "bms_default",
        },
        {
            "field": "classification_policy.minimal_min",
            "api_type": "finite_number",
            "ownership": "bms_classification_interpretation",
            "control_kind": "numeric_input",
            "backing": "bms_classification_interpretation",
            "default_source": "bms_default",
        },
    ]
    return {
        "capability_inventory": {
            "schema_name": inventory["schema_name"],
            "schema_version": inventory["schema_version"],
            "content_sha256": inventory["content_sha256"],
            "predict_options": public_options,
        },
        "capability_inventory_byte_sha256": byte_sha256,
        "capability_inventory_content_sha256": inventory["content_sha256"],
        "settings_schema": complete_requested_settings_schema(),
        "canonical_defaults": default_settings().model_dump(mode="json"),
        "parameter_descriptors": descriptors,
        "field_ownership": {
            descriptor["field"]: descriptor["ownership"] for descriptor in descriptors
        },
        "control_kind_hints": {
            descriptor["field"]: descriptor["control_kind"]
            for descriptor in descriptors
        },
        "compatibility_rules": [
            {
                "rule_id": "structure_map_schema",
                "fields": ["structure_map.schema_name", "structure_map.schema_version"],
                "requirement": "frustrampnn_structure_map_v1",
            },
            {
                "rule_id": "source_model_exact_match",
                "fields": ["source_structure.selected_model_number"],
                "requirement": "exact_structure_map_match",
            },
            {
                "rule_id": "preferred_altloc_exact_match",
                "fields": ["source_structure.preferred_altloc"],
                "requirement": "exact_structure_map_match",
            },
            {
                "rule_id": "selector_exact_coverage",
                "fields": ["protein_selection.entities", "protein_selection.residues"],
                "requirement": "complete_source_identity_match",
            },
            {
                "rule_id": "mapped_residues_only",
                "fields": ["protein_selection"],
                "requirement": "status_mapped",
            },
            {
                "rule_id": "classification_threshold_order",
                "fields": [
                    "classification_policy.high_max",
                    "classification_policy.minimal_min",
                ],
                "requirement": "finite_high_max_less_than_minimal_min",
            },
            {
                "rule_id": "queue_reresolution_required",
                "fields": ["source_artifact"],
                "requirement": "phase3_must_re_resolve_exact_owned_source",
            },
        ],
    }


@router.get("", response_model=List[dict])
async def list_models(
    category: Optional[str] = None,
    include_experimental: bool = False
):
    """
    List all available models.
    
    - **category**: Filter by category (backbone_generation, sequence_design, etc.)
    - **include_experimental**: Include enabled models marked as experimental
    """
    registry = get_registry()
    models = registry.list_models(
        category=category,
        enabled_only=True,
    )
    if not include_experimental:
        models = [model for model in models if not model.experimental]
    
    return [
        {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "category": m.category,
            "description": m.description,
            "modes": [
                {
                    "id": mode.id, 
                    "name": mode.name,
                    "description": mode.description,
                    "params": mode.params
                }
                for mode in m.modes
            ],
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                    "enum": p.enum,
                    "minimum": p.minimum,
                    "maximum": p.maximum,
                    "hidden": p.hidden,
                    "preset_type": getattr(p, 'preset_type', None),
                    "file_type": getattr(p, 'file_type', None),
                }
                for p in m.params
            ],
            "enabled": m.enabled,
            "experimental": m.experimental,
            "ui_icon": m.ui_icon,
            "ui_color": m.ui_color,
        }
        for m in models
    ]


@router.get("/categories")
async def list_categories():
    """Get list of model categories."""
    registry = get_registry()
    return {"categories": registry.get_categories()}


@router.get("/{model_id}")
async def get_model(model_id: str):
    """
    Get full details of a specific model including parameter schema.
    """
    registry = get_registry()
    model = registry.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    return {
        "id": model.id,
        "name": model.name,
        "version": model.version,
        "category": model.category,
        "description": model.description,
        "container": model.container,
        "inputs": model.inputs,
        "outputs": model.outputs,
        "modes": [
            {
                "id": mode.id,
                "name": mode.name,
                "description": mode.description,
                "params": mode.params,
            }
            for mode in model.modes
        ],
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "description": p.description,
                "required": p.required,
                "default": p.default,
                "enum": p.enum,
                "minimum": p.minimum,
                "maximum": p.maximum,
                "hidden": p.hidden,
                "preset_type": getattr(p, 'preset_type', None),
                "file_type": getattr(p, 'file_type', None),
            }
            for p in model.params
        ],
        "ntp_templates": [
            {
                "id": t.id,
                "name": t.name,
                "smiles": t.smiles,
                "description": t.description,
            }
            for t in model.ntp_templates
        ],
        "enabled": model.enabled,
        "experimental": model.experimental,
        "ui_icon": model.ui_icon,
        "ui_color": model.ui_color,
    }


@router.get(
    "/{model_id}/integration",
    response_model=FrustraMPNNIntegrationResponse | ModelIntegrationResponse,
)
async def get_model_integration(model_id: str):
    """Return the shared, non-launchable workflow-integration contract for a model."""
    registry = get_registry()
    model = registry.get_internal_model_definition(model_id)
    if not model or not model.enabled or model.integration is None:
        raise HTTPException(status_code=404, detail=f"Model integration '{model_id}' not found")
    payload = {
        "model_id": model.id,
        "model_name": model.name,
        "model_version": model.version,
        "stage_parameter": model.integration.stage_parameter,
        "operator_label": model.integration.operator_label,
        "checkpoint_label": model.integration.checkpoint_label,
        "model_summary": model.integration.model_summary,
        "semantic_roles": model.integration.semantic_roles,
        "workflows": {
            workflow_id: {
                "default_enabled": workflow.default_enabled,
                "enabled_summary": workflow.enabled_summary,
            }
            for workflow_id, workflow in model.integration.workflows.items()
        },
    }
    if model.id == "frustrampnn" and model.version == "MegaScale":
        payload.update(_frustrampnn_discovery_metadata())
    return payload


@router.get("/{model_id}/modes")
async def get_model_modes(model_id: str):
    """Get available modes for a model."""
    registry = get_registry()
    model = registry.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    return {
        "model_id": model_id,
        "modes": [
            {
                "id": mode.id,
                "name": mode.name,
                "description": mode.description,
                "params": mode.params,
            }
            for mode in model.modes
        ]
    }


@router.get("/{model_id}/ntp-templates")
async def get_ntp_templates(model_id: str):
    """Get NTP templates for nucleotide-aware models."""
    registry = get_registry()
    model = registry.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    if not model.ntp_templates:
        return {"model_id": model_id, "templates": [], "message": "No NTP templates for this model"}
    
    return {
        "model_id": model_id,
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "smiles": t.smiles,
                "description": t.description,
            }
            for t in model.ntp_templates
        ]
    }
