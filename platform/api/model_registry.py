"""
Model Registry - Dynamic model/tool configuration system.

Loads model definitions from YAML config files, enabling new models
to be added without code changes.
"""

import os
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from functools import lru_cache

from services.md.feature_gate import MD_MODEL_ID, molecular_dynamics_feature_enabled


KNOWN_INTEGRATION_WORKFLOW_IDS = frozenset({
    "antibody_design",
    "complex_prediction",
    "conformational_mapping",
    "protein_design",
    "structure_prediction",
})

INTEGRATION_STAGE_PARAMETER_ALLOWLIST = {
    "frustrampnn": frozenset({"run_frustrampnn"}),
}


class ModelParameter(BaseModel):
    """Definition of a model parameter."""
    name: str
    type: str  # string, integer, number, boolean, file, directory, text
    description: str
    required: bool = False
    default: Optional[Any] = None
    enum: Optional[List[str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    pattern: Optional[str] = None
    hidden: bool = False  # Advanced params hidden by default
    preset_type: Optional[str] = None  # pdb, sequence, ligand - for enhanced UI
    file_type: Optional[str] = None  # pdb, sdf, cif - file extension hint


class ModelMode(BaseModel):
    """A mode/workflow within a model."""
    id: str
    name: str
    description: str
    params: List[str] = []  # Parameter names required for this mode


class NTPTemplate(BaseModel):
    """Pre-configured nucleotide template for LigandMPNN."""
    id: str
    name: str
    smiles: str
    description: str


class WorkflowIntegration(BaseModel):
    """Workflow-specific presentation and default for an optional model stage."""
    default_enabled: bool = False
    enabled_summary: str


class ModelIntegration(BaseModel):
    """Shared operator contract used when one model is embedded in many workflows."""
    stage_parameter: str
    operator_label: str
    checkpoint_label: Optional[str] = None
    model_summary: str
    semantic_roles: List[str] = Field(default_factory=list)
    workflows: Dict[str, WorkflowIntegration] = Field(default_factory=dict)


class ModelDefinition(BaseModel):
    """Complete definition of a model/tool."""
    id: str
    name: str
    version: str
    category: str  # backbone_generation, sequence_design, structure_prediction, docking
    description: str
    container: str
    workflow: Optional[str] = None
    engine_containers: Dict[str, str] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    public_launch: bool = True
    integration: Optional[ModelIntegration] = None
    
    # Modes available for this model
    modes: List[ModelMode] = []
    
    # Parameters (shared across modes)
    params: List[ModelParameter] = []
    
    # Input/output types
    inputs: List[str] = []  # pdb, fasta, yaml, smiles
    outputs: List[str] = []  # pdb, json, fasta
    
    # NTP templates (for LigandMPNN)
    ntp_templates: List[NTPTemplate] = []
    
    # UI hints
    ui_icon: str = "cube"
    ui_color: str = "#6366F1"
    
    # Status
    enabled: bool = True
    experimental: bool = False


class ModelRegistry:
    """
    Manages model definitions loaded from YAML config files.
    
    Models are loaded from config/models/*.yaml at startup.
    New models can be added by creating new YAML files.
    """
    
    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path(__file__).parent / "config" / "models"
        self.config_dir = config_dir
        self._models: Dict[str, ModelDefinition] = {}
        self._load_models()
    
    def _load_models(self) -> None:
        """Load all model definitions from YAML files."""
        if not self.config_dir.exists():
            self._models = {}
            return

        loaded_models: Dict[str, ModelDefinition] = {}
        for yaml_file in self.config_dir.glob("*.yaml"):
            try:
                with open(yaml_file, 'r', encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data:
                        model = ModelDefinition(**data)
                        self._validate_integration(model)
                        loaded_models[model.id] = model
            except Exception as e:
                raise ValueError(f"Failed to load model registry entry {yaml_file}: {e}") from e
        self._models = loaded_models

    @staticmethod
    def _validate_integration(model: ModelDefinition) -> None:
        integration = model.integration
        if integration is None:
            return

        if not integration.operator_label.strip():
            raise ValueError("integration operator label must be nonempty")
        if not integration.model_summary.strip():
            raise ValueError("integration model summary must be nonempty")

        allowed_parameters = INTEGRATION_STAGE_PARAMETER_ALLOWLIST.get(model.id, frozenset())
        if integration.stage_parameter not in allowed_parameters:
            raise ValueError(
                f"integration stage parameter '{integration.stage_parameter}' is not allowlisted for model '{model.id}'"
            )

        normalized_roles = [role.strip() for role in integration.semantic_roles]
        if any(not role for role in normalized_roles):
            raise ValueError("integration contains a blank semantic role")
        if len(normalized_roles) != len(set(normalized_roles)):
            raise ValueError("integration contains a duplicate semantic role")

        unknown_workflows = set(integration.workflows) - KNOWN_INTEGRATION_WORKFLOW_IDS
        if unknown_workflows:
            unknown = ", ".join(sorted(unknown_workflows))
            raise ValueError(f"integration references unknown workflow: {unknown}")
        for workflow_id, workflow in integration.workflows.items():
            if not workflow.enabled_summary.strip():
                raise ValueError(f"integration enabled summary must be nonempty for workflow '{workflow_id}'")
    
    def get_model(self, model_id: str) -> Optional[ModelDefinition]:
        """Get a publicly available model by ID."""
        if model_id == MD_MODEL_ID and not molecular_dynamics_feature_enabled():
            return None
        model = self._models.get(model_id)
        if model is not None and (not model.enabled or not model.public_launch):
            return None
        return model

    def get_internal_model_definition(self, model_id: str) -> Optional[ModelDefinition]:
        """Get a raw definition for trusted coordinators; never expose this via HTTP."""
        return self._models.get(model_id)
    
    def list_models(self, category: Optional[str] = None, enabled_only: bool = True) -> List[ModelDefinition]:
        """List all models, optionally filtered by category."""
        models = list(self._models.values())
        
        if enabled_only:
            models = [m for m in models if m.enabled]

        models = [m for m in models if m.public_launch]

        if not molecular_dynamics_feature_enabled():
            models = [m for m in models if m.id != MD_MODEL_ID]
        
        if category:
            models = [m for m in models if m.category == category]
        
        return sorted(models, key=lambda m: m.name)
    
    def get_categories(self) -> List[str]:
        """Get list of unique categories."""
        return sorted(set(m.category for m in self.list_models(enabled_only=False)))
    
    def validate_job_params(self, model_id: str, mode_id: str, params: Dict[str, Any]) -> List[str]:
        """
        Validate job parameters against model schema.
        Returns list of validation errors (empty if valid).
        """
        errors = []
        
        model = self.get_model(model_id)
        if not model:
            errors.append(f"Unknown model: {model_id}")
            return errors
        
        # Find the mode
        mode = next((m for m in model.modes if m.id == mode_id), None)
        if not mode:
            errors.append(f"Unknown mode '{mode_id}' for model '{model_id}'")
            return errors
        
        # Check required parameters
        for param in model.params:
            if param.required and param.name not in params:
                if not mode.params or param.name in mode.params:
                    errors.append(f"Missing required parameter: {param.name}")
        
        # Validate parameter values
        for param_name, value in params.items():
            param_def = next((p for p in model.params if p.name == param_name), None)
            if param_def:
                # Type validation
                if param_def.enum and value not in param_def.enum:
                    errors.append(f"Invalid value for {param_name}: must be one of {param_def.enum}")
                if param_def.pattern and (
                    not isinstance(value, str)
                    or re.fullmatch(param_def.pattern, value) is None
                ):
                    errors.append(f"{param_name} does not match required pattern")
                if param_def.minimum is not None and isinstance(value, (int, float)):
                    if value < param_def.minimum:
                        errors.append(f"{param_name} must be >= {param_def.minimum}")
                if param_def.maximum is not None and isinstance(value, (int, float)):
                    if value > param_def.maximum:
                        errors.append(f"{param_name} must be <= {param_def.maximum}")
        
        return errors
    
    def reload(self) -> None:
        """Atomically reload all model definitions from disk."""
        self._load_models()


# Global registry instance
@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    """Get the global model registry instance."""
    return ModelRegistry()
