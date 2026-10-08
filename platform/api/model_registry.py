"""
Model Registry - Dynamic model/tool configuration system.

Loads model definitions from YAML config files, enabling new models
to be added without code changes.
"""

import os
import re
import json
import math
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


class RuntimeDependencyRef(BaseModel):
    """Trusted managed-storage binding, never an operator path or download URL."""
    model_config = {"extra": "forbid", "frozen": True}
    kind: str = Field(pattern=r"^(image|weights)$")
    relative_path: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")


# Managed scientific assets shared by independent provisioning and selected
# native workflows. These are logical bindings; the existing image/runtime
# owners bind immutable bytes. A model's composed workflow may select more.
INDEPENDENT_RUNTIME_MODELS = frozenset({
    "protenix", "esmfold2", "esmfold2_experimental", "fampnn", "frustrampnn",
    "boltz2", "af2", "proteinmpnn", "unidock",
})


def model_runtime_dependencies(model_id: str, *, internal: bool = False) -> tuple[RuntimeDependencyRef, ...]:
    """Known managed bindings, not proof of the entire selected workflow closure.

    Trusted parent descriptors may resolve an embedded internal model; ordinary
    model provision callers retain public/enabled admission. Boltz uses its
    native semantic name, never an unproven qualified-file equivalence.
    """
    registry = get_registry()
    model = (registry.get_internal_model_definition(model_id) if internal
             else registry.get_model(model_id))
    known = INDEPENDENT_RUNTIME_MODELS | ({'diffdock', 'boltzgen'} if internal else set())
    if model is None or not model.enabled or model_id not in known:
        raise ValueError("Independent runtime closure is not available for this model")
    refs = [RuntimeDependencyRef(kind="image", relative_path=model.container)]
    weights = {"protenix": "protenix", "esmfold2": "esmfold2", "esmfold2_experimental": "esmfold2",
               "boltz2": "boltz", "af2": "alphafold"}
    if model_id in weights:
        refs.append(RuntimeDependencyRef(kind="weights", relative_path=weights[model_id]))
    # Native preparation/filter stages are part of these models, not optional
    # remote additions (modules/{fampnn,proteinmpnn,diffdock,af2,boltzgen}.nf).
    if model_id in {"fampnn", "proteinmpnn", "diffdock", "af2", "boltzgen"}:
        refs.append(RuntimeDependencyRef(kind="image", relative_path="pyrosetta_tools.sif"))
    return tuple(refs)


def _native_metadata_bytes(root, relative):
    """Read one bounded managed text member without following links or SDK imports."""
    import stat
    if not root or not Path(str(root)).is_absolute():
        raise FileNotFoundError('Trusted weights_root binding is missing')
    descriptor = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
                raise ValueError('Native metadata must be a regular file of at most 1 MiB')
            with os.fdopen(os.dup(leaf), 'rb') as stream:
                data = stream.read(1024 * 1024 + 1)
            after = os.fstat(leaf)
            if len(data) > 1024 * 1024 or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('Native metadata changed during read')
            return data
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def native_checkpoint_dependencies(process: str, params: dict):
    """Read native checkpoint selections, never approve bytes or sweep caches.

    This is shared selected-plan metadata, not another acquisition catalog.
    Missing installed upstream defaults remain distinct from known member names
    whose host placement is not yet bound. Download approval still belongs to
    model_acquisition_plan / services.runtime_acquisition; a native URL or alias
    is not an approved immutable acquisition manifest.
    """
    from component_runtime import SelectedDependency, UnresolvedField

    dependencies, blockers = [], []

    def require(key, authority, reason, *, member=None, release=None):
        dependencies.append(SelectedDependency(key, 'weights', member, authority,
            semantic_release=release,
            compatibility_authority='model_acquisition_plan; scripts/lib/pinned_weight_layout.py'))
        blockers.append(UnresolvedField(key, 'dependency_closure', authority, reason))

    if process == 'RunRF3':
        owner = 'scripts/run_rf3.py:main overrides; modules/rf3.nf:RunRF3'
        if params.get('rf3_extra_config'):
            require('weights:rf3:selected-checkpoint', owner,
                'Native RF3 Hydra overrides require resolved ckpt_path metadata; do not substitute the default checkpoint')
        else:
            dependencies.append(SelectedDependency('weights:rf3:checkpoint', 'weights',
                'foundry/checkpoints/rf3_foundry_01_24_latest_remapped.ckpt', owner))
    elif process == 'RunShapeRFD3':
        dependencies.append(SelectedDependency('weights:shape-rfd3:checkpoint', 'weights',
            'foundry/checkpoints/rfd3_latest.ckpt',
            'scripts/shape_blueprint/run_shape_rfd3.py:DEFAULT_CHECKPOINT; nextflow.config:ShapeRFD3'))
    elif process == 'RunRFD3':
        owner = 'rfd3/cli.py; rfd3/configs/inference_engine/rfdiffusion3.yaml; foundry/inference_engines/checkpoint_registry.py'
        if params.get('rfd3_extra_config') and not (params.get('rfd3_request_path') or params.get('rfd3_generation_request_path')):
            require('weights:rfd3:selected-checkpoint', owner,
                'Native Hydra overrides require resolved checkpoint/config metadata')
        else:
            dependencies.append(SelectedDependency('weights:rfd3:checkpoint', 'weights',
                'foundry/checkpoints/rfd3_latest.ckpt', owner,
                semantic_release='https://files.ipd.uw.edu/pub/rfd3/rfd3_foundry_2025_12_01_remapped.ckpt'))
    elif process == 'RunBoltzGen':
        owner = 'boltzgen/cli/boltzgen.py:ARTIFACTS,BinderDesignPipeline; modules/boltzgen.nf:RunBoltzGen'
        mode = str(params.get('boltzgen_checkpoint_mode') or 'both')
        protocol = str(params.get('boltzgen_protocol') or 'auto')
        if mode not in {'both', 'diverse', 'adherence'}:
            require('weights:boltzgen:selected-checkpoint', owner, 'Unknown native checkpoint selection')
            return tuple(dependencies), tuple(blockers)
        from scripts.lib.boltzgen_native import ProtocolMetadata, selected_checkpoint_members, protocol_request_identity
        derived = params.get('_boltzgen_protocol_metadata')
        if (protocol == 'auto' and isinstance(derived, ProtocolMetadata)
                and derived.get('state') == 'ok' and derived.get('requested_protocol') == 'auto'
                and derived.get('request_identity') == protocol_request_identity(params)):
            protocol = derived['effective_protocol']
        try:
            members = selected_checkpoint_members(protocol, mode, bool(params.get('boltzgen_skip_inverse_folding')))
        except ValueError:
            # Keep the unresolved protocol gate below; do not guess affinity.
            members = selected_checkpoint_members('protein-anything', mode,
                                                   bool(params.get('boltzgen_skip_inverse_folding')))
        for member in members:
            dependencies.append(SelectedDependency('weights:boltzgen:' + member, 'weights',
                'boltzgen/' + member, owner,
                semantic_release='huggingface:boltzgen/boltzgen-1:' + member))
        dependencies.append(SelectedDependency('weights:boltzgen:mols.zip', 'weights',
            'boltzgen/mols.zip', owner,
            semantic_release='huggingface:boltzgen/inference-data:mols.zip'))
        if protocol == 'auto':
            blockers.append(UnresolvedField('weights:boltzgen:protocol', 'dependency_closure', owner,
                'Native auto protocol is resolved from generated YAML entity types. The preparation producer must '
                'publish its resolved protocol before selecting affinity assets; do not guess from a cache directory'))
        elif protocol not in {'protein-anything', 'protein-small_molecule', 'peptide-anything',
                              'nanobody-anything', 'antibody-anything'}:
            blockers.append(UnresolvedField('weights:boltzgen:protocol', 'dependency_closure', owner,
                'Selected protocol is not covered by the installed native protocol authority'))
        if params.get('boltzgen_extra_config'):
            require('weights:boltzgen:selected-config', owner,
                'Native passthrough can change checkpoints, moldir or per-step configs; resolved producer metadata required')
    elif process == 'RunDiffDock':
        owner = 'DiffDock/default_inference_args.yaml; inference.py; utils/inference_utils.py; esm/pretrained.py'
        # Model parameter YAMLs are external runtime inputs too. Numerical SO(3)
        # and torus .npy files are generated by native utilities, not transferred.
        members = ('workdir/v1.1/score_model/best_ema_inference_epoch_model.pt',
                   'workdir/v1.1/score_model/model_parameters.yml',
                   'workdir/v1.1/confidence_model/best_model_epoch75.pt',
                   'workdir/v1.1/confidence_model/model_parameters.yml',
                   'torch/hub/checkpoints/esm2_t33_650M_UR50D.pt',
                   'torch/hub/checkpoints/esm2_t33_650M_UR50D-contact-regression.pt')
        for member in members:
            dependencies.append(SelectedDependency('weights:diffdock:' + member, 'weights',
                'diffdock/' + member, owner))
        from dataclasses import replace
        import hashlib
        for index, dependency in enumerate(tuple(dependencies)):
            if not dependency.relative_path.endswith('model_parameters.yml'):
                continue
            try:
                payload = _native_metadata_bytes(params.get('weights_root'), dependency.relative_path)
                metadata = yaml.safe_load(payload)
                if not isinstance(metadata, dict):
                    raise ValueError('Model parameters must be a YAML mapping')
            except FileNotFoundError:
                blockers.append(UnresolvedField(dependency.logical_id, 'dependency_closure', owner,
                    'Missing native metadata asset: ' + dependency.relative_path))
                continue
            except (OSError, ValueError, yaml.YAMLError) as exc:
                blockers.append(UnresolvedField(dependency.logical_id, 'dependency_closure', owner,
                    'Invalid or unsafe native metadata asset: ' + dependency.relative_path + ' (' + type(exc).__name__ + ')'))
                continue
            dependencies[index] = replace(dependency, semantic_release='sha256:' + hashlib.sha256(payload).hexdigest())
            # inference.py's installed defaults use the old confidence model,
            # whose esm_embeddings_path is a feature switch, not a file reader.
            # The new score model can load an additional ESM model by name.
            if '/score_model/' not in dependency.relative_path:
                continue
            model = metadata.get('esm_embeddings_model')
            if model is None or model == 'precomputed':
                continue
            if not isinstance(model, str) or not re.fullmatch(r'esm[A-Za-z0-9_-]+', model):
                blockers.append(UnresolvedField(dependency.logical_id, 'dependency_closure', owner,
                    'Native esm_embeddings_model requires a bound supported hub name; local paths cannot be reinterpreted'))
                continue
            esm_members = [model + '.pt']
            # Exact esm.pretrained._has_regression_weights policy.
            if 'esm1v' not in model and 'esm_if' not in model:
                esm_members.append(model + '-contact-regression.pt')
            for member in esm_members:
                relative = 'diffdock/torch/hub/checkpoints/' + member
                if any(d.relative_path == relative for d in dependencies):
                    continue
                dependencies.append(SelectedDependency('weights:diffdock:' + relative.removeprefix('diffdock/'),
                    'weights', relative, owner,
                    semantic_release='esm/pretrained.py:' + model))
        if params.get('diffdock_extra_config'):
            require('weights:diffdock:selected-config', owner,
                'Native passthrough may select a different config or optional GNINA/ESMFold branch; '
                'resolved native configuration metadata required')
    return tuple(dependencies), tuple(blockers)


class RuntimeAcquisitionArtifact(BaseModel):
    """Release-owner reviewed byte manifest, not user-supplied launch settings.

    Entries are added only to the existing model YAML authority after release
    review. No production acquisition approval is implied by an empty list.
    """
    model_config = {"extra": "forbid", "frozen": True}
    artifact_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    dependency: RuntimeDependencyRef
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, strict=True)
    source_authority: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    license_id: Optional[str] = None
    redirect_policy: Optional[Dict[str, Any]] = None
    member_path: Optional[str] = None


def model_acquisition_plan(model_id: str) -> dict:
    """Resolve approved metadata against existing runtime dependency authority.

    Missing closure/metadata is an explicit blocker; URLs from legacy scripts,
    local SIF paths and qualification attestations are not acquisition approval.
    """
    try:
        refs = model_runtime_dependencies(model_id)
    except ValueError as exc:
        return {"model_id": model_id, "artifacts": [], "blockers": [
            {"code": "runtime_closure_unavailable", "detail": str(exc)}]}
    model = get_registry().get_model(model_id)
    assert model is not None  # Closure resolution above already checked availability.
    artifacts, blockers = [], []
    allowed = {(ref.kind, ref.relative_path) for ref in refs}
    ids = [entry.artifact_id for entry in model.acquisition]
    if len(ids) != len(set(ids)) or any(
        (entry.dependency.kind, entry.dependency.relative_path) not in allowed
        for entry in model.acquisition
    ):
        return {"model_id": model_id, "artifacts": [], "blockers": [
            {"code": "invalid_acquisition_dependency_binding"}]}
    for ref in refs:
        entries = [entry for entry in model.acquisition
                   if entry.dependency == ref]
        if not entries:
            blockers.append({"code": "approved_acquisition_metadata_missing",
                             **ref.model_dump()})
        for entry in entries:
            artifacts.append({"dependency": ref.model_dump(), "member_path": entry.member_path,
                              "manifest": {
                **entry.model_dump(exclude={"dependency", "member_path"}), "kind": ref.kind}})
    return {"model_id": model_id, "artifacts": artifacts, "blockers": blockers}


class ModelDefinition(BaseModel):
    """Complete definition of a model/tool."""
    id: str
    name: str
    version: str
    category: str  # backbone_generation, sequence_design, structure_prediction, docking
    description: str
    container: str
    acquisition: List[RuntimeAcquisitionArtifact] = Field(default_factory=list)
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

    @property
    def execution_authority(self) -> str:
        """Existing declaration plus native selection resolver, source-bound by plan."""
        return (f'platform/api/config/models/{self.id}.yaml; '
                'platform/api/model_registry.py:selected_execution_metadata')


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
    
    def validate_job_params(self, model_id: str, mode_id: str, params: Dict[str, Any],
                            *, native_entrypoint: str | None = None) -> List[str]:
        """
        Validate job parameters against model schema.
        Returns list of validation errors (empty if valid).
        """
        errors = []
        if model_id == 'boltzgen_child' and native_entrypoint is None:
            return ['BoltzGen child requires a selected native parent expansion']
        
        model = self.get_model(model_id)
        native_required = None
        if native_entrypoint is not None:
            # Only the selected-native compiler supplies this keyword, never a DTO.
            from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS
            if MODEL_MODE_WORKFLOW_ENTRYPOINTS.get((model_id, mode_id)) != native_entrypoint:
                return ["Native model/mode does not match canonical compiler routing"]
            raw = self.get_internal_model_definition(model_id)
            if raw is not None and not raw.enabled:
                return [f"Disabled model: {model_id}"]
            # These aliases share the parent's parameter definitions, but have
            # their own native input contract (not the full parent workflow).
            if (model_id, mode_id) == ('rfantibody_child', 'antibody_backbone'):
                model = self.get_model('antibody_denovo')
                native_required = ('target_pdb',)
            elif (model_id, mode_id) == ('template_antibody_denovo', 'maturation_child'):
                model = self.get_model('antibody_denovo')
                native_required = ('pdb_paths',)
            elif raw is not None:
                model = raw
        if not model:
            errors.append(f"Unknown model: {model_id}")
            return errors
        
        # Find the mode
        mode = next((m for m in model.modes if m.id == mode_id), None)
        if not mode and native_required is None:
            errors.append(f"Unknown mode '{mode_id}' for model '{model_id}'")
            return errors
        
        # These are generator modes of the supported antibody workflow, not
        # standalone models or the RFantibody pipeline's target/epitope contract.
        if model_id == 'antibody_denovo' and mode_id == 'nanobody_binder':
            if not params.get('boltzgen_target_pdb_path') and not params.get('target_pdb'):
                errors.append('Missing required parameter: boltzgen_target_pdb_path')
            if params.get('diffusion_method', 'boltzgen') != 'boltzgen':
                errors.append('nanobody_binder requires the selected BoltzGen generator')
        elif model_id == 'antibody_denovo' and mode_id == 'generator_backbone_refine':
            if not any(params.get(key) for key in ('ppiflow_seed_complex_path', 'ppiflow_seed_input_dir')):
                errors.append('PPIFlow generation requires a seed complex or seed input directory')
            if params.get('ppiflow_mode', 'backbone_refine') != 'backbone_refine':
                errors.append('generator_backbone_refine requires backbone_refine')

        # Check required parameters
        if native_required is not None:
            for name in native_required:
                if not params.get(name):
                    errors.append(f"Missing required parameter: {name}")
        else:
            assert mode is not None
            for param in model.params:
                if param.required and param.name not in params:
                    if not mode.params or param.name in mode.params:
                        errors.append(f"Missing required parameter: {param.name}")
        
        # Validate parameter values
        for param_name, value in params.items():
            param_def = next((p for p in model.params if p.name == param_name), None)
            if param_def:
                # JSON booleans are not integer/number settings. Validate the
                # declared wire type before range/enum checks, without coercing
                # strings or silently letting invalid values reach native argv.
                if value is None and not param_def.required and param_def.default is None:
                    continue
                wire_type = param_def.type
                valid_type = {
                    "integer": type(value) is int,
                    "number": type(value) is int or isinstance(value, float) and math.isfinite(value),
                    "float": type(value) is int or isinstance(value, float) and math.isfinite(value),
                    "boolean": type(value) is bool,
                    "string": isinstance(value, str),
                    "text": isinstance(value, str),
                    "textarea": isinstance(value, str),
                    "file": isinstance(value, str),
                    "directory": isinstance(value, str),
                    "object": isinstance(value, dict),
                    "string_list": isinstance(value, list) and all(isinstance(item, str) for item in value),
                }.get(wire_type, False)
                if not valid_type:
                    errors.append(f"{param_name} requires typed {wire_type}")
                    continue
                if param_def.enum:
                    if param_def.type == "string_list":
                        if not isinstance(value, list) or any(
                            not isinstance(member, str) or member not in param_def.enum
                            for member in value
                        ):
                            errors.append(
                                f"Invalid value for {param_name}: members must be one of {param_def.enum}"
                            )
                    elif value not in param_def.enum:
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


def selected_execution_metadata(model_id: str, mode: str, effective_params: Dict[str, Any],
                                entrypoint: str):
    """Resolve native selected descriptors for both compilation and provisioning.

    This is metadata only: no input existence probes, image resolution, provider
    calls or execution. Unreviewed native contracts remain required blockers.
    The native compiler has already chosen entrypoint and effective settings.
    """
    from dataclasses import replace
    from component_runtime import (SelectedExecutionMetadata, SelectedDependency,
        NativeComponent, NativeArtifactRole, ExternalServiceIntent, UnresolvedField, canonical_bytes)
    from services.result_contracts import resolve_result_contract

    registry = get_registry()
    model = registry.get_internal_model_definition(model_id)
    availability = ('unknown' if model is None else 'disabled' if not model.enabled else
                    'internal' if not model.public_launch else
                    'public' if registry.get_model(model_id) is not None else 'unavailable')
    authority = (model.execution_authority if model else
                 'platform/api/model_registry.py:ModelRegistry.get_internal_model_definition')
    p = effective_params
    workflow = Path(entrypoint).stem
    if (workflow == 'protein_sequence_design' and model is not None
            and model_id in {'fampnn', 'proteinmpnn'}
            and mode in {item.id for item in model.modes}):
        # A declared public identity selects its native engine even in a pure
        # preview. Do not derive a scientific branch from a compiler-profile alias
        # or overwrite an explicitly conflicting selection.
        p = dict(effective_params)
        p.setdefault('sequence_design_engine', model_id)
        p.setdefault('sequence_design_mode', mode)
    native_internal_contexts = {
        ('rfantibody_child', 'antibody_backbone'): 'rfantibody_backbone',
        ('fampnn_child', 'sequence_design'): 'fampnn_child',
        ('template_antibody_denovo', 'maturation_child'): 'maturation_child',
        ('template_antibody_denovo', 'antibody_denovo_pipeline'): 'antibody_denovo',
        ('template_antibody_denovo', 'antibody_refinement_pipeline'): 'antibody_denovo',
        ('template_antibody_denovo', 'default'): 'antibody_denovo',
    }
    if model is None and native_internal_contexts.get((model_id, mode)) == workflow:
        availability = 'internal'
        authority = 'platform/api/services/nextflow.py:MODEL_MODE_WORKFLOW_ENTRYPOINTS'
    components, dynamic, dependencies, roles, services, blockers = [], [], {}, [], [], []

    def native_bool(key, default=False):
        value = p.get(key)
        return default if value is None else value if type(value) is bool else str(value).lower() == 'true'

    def unresolved(owner, field, source, reason, blocks=None):
        if blocks is None:
            blocks = (('preview_acceptance', 'provision', 'launch') if field == 'dependency_closure'
                      else ('preview_acceptance', 'launch'))
        blockers.append(UnresolvedField(owner, field, source, reason, blocks))

    from native_components import (append_native_workflow_metadata,
        native_resource_policy, native_lifecycle_policy)

    def resource_policy(label='gpu'):
        return native_resource_policy(p, label)

    reviewed = False

    def native_stage(key, module, deps, inputs=(), outputs=(), *, after=(),
                     condition=None, label='CPU', optional_outputs=(), optional_inputs=()):
        source = module + ':' + key
        input_ids = tuple(key + ':input:' + name for name in inputs)
        output_ids = tuple(key + ':output:' + name for name in outputs)
        for direction, names, ids in (('input', inputs, input_ids), ('output', outputs, output_ids)):
            for name, role_id in zip(names, ids):
                roles.append(NativeArtifactRole(role_id, key, direction, name, source,
                    requiredness='optional' if (direction == 'output' and name in optional_outputs or
                                                direction == 'input' and name in optional_inputs) else 'required',
                    identity_authority=source, publication_authority=entrypoint if direction == 'output' else None,
                    condition=condition, cardinality_authority=source + ':' + direction))
        components.append(NativeComponent(key, source, canonical_bytes(dict(p)),
            depends_on=after, dependency_ids=tuple(deps) + ('support-python', 'nextflow', 'apptainer', 'bms-source'),
            input_role_ids=input_ids, output_role_ids=output_ids,
            resources_json=resource_policy(label), lifecycle_authority=source + '; nextflow.config:process',
            condition=condition))

    def managed(selected_model):
        try:
            refs = model_runtime_dependencies(selected_model, internal=True)
        except ValueError:
            unresolved(selected_model, 'dependency_closure',
                       'platform/api/model_registry.py:model_runtime_dependencies',
                       'Native managed bindings are not reviewed for this model')
            return ()
        definition = registry.get_internal_model_definition(selected_model)
        assert definition is not None  # Managed resolution checked this definition.
        ids = []
        for ref in refs:
            key = f'{ref.kind}:{ref.relative_path}'
            ids.append(key)
            selector = {'protenix.sif': 'protenix_container_path',
                        'frustrampnn.sif': 'frustrampnn_container_path',
                        'esmfold2.sif': 'esmf_container_path',
                        'protenix': 'protenix_weights', 'boltz': 'boltz_models'}.get(ref.relative_path)
            dependencies[key] = SelectedDependency(key, ref.kind, ref.relative_path,
                definition.execution_authority + '; nextflow.config:process labels',
                definition.version if ref.kind == 'image' and ref.relative_path == definition.container else
                'protenix-v2' if selected_model == 'protenix' else None,
                selector, 'docs/Shared_Scientific_Runtime_Images.md')
        return tuple(ids)

    # The managed support interpreter is required by native preparation and
    # publication, not merely the predictor SIF. Release/compatibility bindings
    # remain the existing bootstrap owner's responsibility, never guessed here.
    for key, kind, source in (
        ('support-python', 'support_python', 'nextflow.config:params.cm_api_runtime_dir'),
        ('nextflow', 'critical_runtime', 'platform/api/services/nextflow.py:resolve_nextflow_executable'),
        ('apptainer', 'critical_runtime', 'nextflow.config:apptainer'),
        ('bms-source', 'source_release', 'platform/api/component_runtime.py:SourceIdentity')):
        dependencies[key] = SelectedDependency(key, kind, None, source,
            compatibility_authority='platform/api/tools/bms_managed_runtime.py:CRITICAL_REQUIREMENTS'
                if kind != 'source_release' else source)
    # The complete approved source release contains native helpers and local
    # imports. Interpreter/Nextflow members are bound by the existing critical
    # runtime owner, not rehashed by logical compilation.

    prediction = workflow in {'structure_prediction', 'complex_prediction'}
    # Selection is read directly from the actual shared native workflow, not
    # from the top model's container or a rendered argv projection.
    method = p.get('pred_method') or 'boltz'
    selected = []
    if prediction:
        if workflow == 'complex_prediction':
            selected = (['protenix'] if method == 'protenix' else
                        ['boltz2', 'protenix'] if method == 'boltz_protenix' else ['boltz2'])
        else:
            selected = {'boltz': ['boltz2'], 'protenix': ['protenix'],
                        'boltz_protenix': ['boltz2', 'protenix'], 'esmfold2': ['esmfold2']}.get(method, [])
        if not selected:
            unresolved(workflow, 'dependency_closure', 'modules/structure_prediction.nf:structure_prediction_wf',
                       'No reviewed native predictor branch for selected pred_method')
    elif workflow == 'docking':
        engine = str(p.get('docking_engine') or 'diffdock').lower()
        if engine in {'diffdock', 'dual', 'dual_docking', 'compare', 'consensus'}:
            deps = managed('diffdock')
            selected_weights, weight_blockers = native_checkpoint_dependencies('RunDiffDock', p)
            dependencies.update((item.logical_id, item) for item in selected_weights)
            deps += tuple(item.logical_id for item in selected_weights)
            blockers.extend(weight_blockers)
            native_stage('PrepDiffDock', 'modules/diffdock.nf', deps,
                ('receptor_structures', 'ligand_smiles'), ('csv', 'pdbs'))
            native_stage('RunDiffDock', 'modules/diffdock.nf', deps, ('csv', 'pdbs'), ('sdfs', 'logs'),
                after=('PrepDiffDock',), label='gpu')
        if engine in {'unidock', 'dual', 'dual_docking', 'compare', 'consensus'}:
            deps = managed('unidock')
            native_stage('PrepUniDock', 'modules/unidock.nf', deps,
                ('receptor_structures', 'ligand_smiles'), ('receptor', 'flex_receptor', 'ligand_dir', 'box', 'logs'),
                optional_outputs=('flex_receptor',))
            native_stage('RunUniDock', 'modules/unidock.nf', deps,
                ('receptor', 'flex_receptor', 'ligand_dir', 'box'), ('poses', 'scores', 'logs'),
                after=('PrepUniDock',), label='gpu', optional_inputs=('flex_receptor',))
            native_stage('FilterUniDock', 'modules/unidock.nf', deps,
                ('poses', 'scores'), ('pdbs', 'json', 'logs'), after=('RunUniDock',), optional_outputs=('pdbs',))
        if not components:
            unresolved(workflow, 'dependency_closure', entrypoint, 'Unknown native docking engine')
    elif workflow == 'fampnn_child':
        deps = managed('fampnn')
        native_stage('PrepFAMPNN', 'modules/fampnn.nf', deps, ('pdbs', 'jsons'),
            ('pdbs', 'provenance', 'csv'), optional_outputs=('provenance',))
        native_stage('RunFAMPNN', 'modules/fampnn.nf', deps, ('pdbs', 'csv', 'analysis_policy'),
            ('pdbs_jsons', 'seq_prob_metrics'), after=('PrepFAMPNN',), label='gpu_light',
            optional_outputs=('seq_prob_metrics',))
        if p.get('enable_fampnn_filter') is not False and any(
                p.get(key) is not None for key in ('fampnn_max_psce', 'fampnn_max_residue_psce')):
            native_stage('FilterFAMPNN', 'modules/fampnn.nf', deps, ('pdbs_jsons',),
                ('pdbs', 'jsons', 'logs'), after=('RunFAMPNN',), optional_outputs=('pdbs', 'jsons'),
                condition='enable_fampnn_filter != false && (fampnn_max_psce != null || fampnn_max_residue_psce != null)')
        if p.get('fampnn_checkpoint_path'):
            dependencies['fampnn:checkpoint'] = SelectedDependency('fampnn:checkpoint', 'weights', None,
                'modules/fampnn.nf:RunFAMPNN', selector='fampnn_checkpoint_path')
    else:
        reviewed = append_native_workflow_metadata(model_id, mode, p, entrypoint,
            components, dynamic, dependencies, roles, services, unresolved)
        if not reviewed:
            unresolved(f'{model_id}/{mode}', 'dependency_closure', entrypoint,
                       'Entrypoint is not a supported native compiler dispatch contract')

    for predictor in selected:
        complex_input = workflow == 'complex_prediction'
        if predictor == 'protenix':
            module = 'modules/protenix.nf'
            process = 'ProtenixFromComplex' if complex_input else 'ProtenixPredict'
        elif predictor == 'boltz2':
            module = 'modules/structure_prediction.nf'
            process = ('BoltzFromComplex' if complex_input else
                       'BoltzFromSequenceWithMSATask' if native_bool('boltz_use_msa') else
                       'BoltzFromSequenceTask')
        else:
            module = 'modules/esmfold2_experimental.nf'
            process = 'ESMFold2MSAPredict' if 'core_protein_scientific_contract' in p else 'ESMFold2Predict'

        deps = managed(predictor)
        input_role, output_role = predictor + ':input', predictor + ':canonical_structures'
        source = module + ':' + process
        helpers = []
        if predictor == 'protenix':
            helpers.append('run_protenix_inference.py')
            if native_bool('protenix_use_msa', True):
                helpers.append('prepare_protenix_msa.py')
            if complex_input:
                helpers.extend(('prepare_protenix_constraints.py', 'write_structure_producer_manifest.py',
                    'extract_target_templates.py', 'prepare_protenix_exact_templates.py', 'finalize_target_geometry.py'))
        elif predictor == 'boltz2':
            helpers.append('write_structure_producer_manifest.py' if complex_input else
                           'write_sequence_producer_manifest.py')
            if complex_input:
                helpers.extend(('extract_target_templates.py', 'finalize_target_geometry.py', 'run_local_msa.py'))
        else:
            helpers.extend(('run_esmfold2_inference.py', 'bms_gpu_run_telemetry.py'))
        for helper in helpers:
            key = 'source:scripts/' + helper
            dependencies[key] = SelectedDependency(key, 'support_tool', 'scripts/' + helper, source)
            deps += (key,)

        roles.extend((
            NativeArtifactRole(input_role, process, 'input',
                'complex_json' if complex_input else 'typed_sequence', source,
                identity_authority='modules/structure_prediction.nf:normalizeSequenceProducerInputs' if not complex_input else source),
            NativeArtifactRole(output_role, process, 'output',
                'canonical_candidates' if complex_input else 'canonical_structures', source,
                identity_authority='modules/structure_prediction.nf:complexCanonicalProducerOutputs' if complex_input else
                                   'modules/structure_prediction.nf:canonicalProducerOutputs',
                publication_authority=entrypoint)))
        preparation = ()
        prediction_input_role = input_role
        if (predictor == 'boltz2' and not complex_input and
                native_bool('boltz_use_msa') and not p.get('msa_path')):
            # This is the native stage name, NOT authorization for local search.
            # Existing MSA policy/controller owns the hosted operation. Native
            # first-sequence fanout still needs adapter-qualified input identity.
            helper = 'source:scripts/run_local_msa.py'
            dependencies[helper] = SelectedDependency(helper, 'support_tool', 'scripts/run_local_msa.py',
                'modules/structure_prediction.nf:GenerateLocalMSA')
            components.append(NativeComponent('GenerateLocalMSA',
                'modules/structure_prediction.nf:structure_prediction_wf',
                canonical_bytes({'need_boltz_msa': True, 'msa_path': None}),
                dependency_ids=('support-python', 'bms-source', helper), input_role_ids=(input_role,),
                output_role_ids=('boltz2:msa_artifacts',), resources_json=resource_policy('CPU'),
                lifecycle_authority='modules/structure_prediction.nf:GenerateLocalMSA; nextflow.config:process',
                condition='need_boltz_msa && !hasProvidedMsa',
                grouping_authority='modules/structure_prediction.nf:typed_inputs.first().combine(msa_ch)'))
            preparation = ('GenerateLocalMSA',)
            unresolved('GenerateLocalMSA', 'external_service_roles', 'scripts/run_local_msa.py',
                       'Hosted handoff must bind native first-sequence fanout before launch; no local search authorized')
        if complex_input:
            prep = 'PrepProtenixComplex' if predictor == 'protenix' else 'PrepareComplexWithMSA'
            prediction_input_role = predictor + ':prepared_input'
            roles.append(NativeArtifactRole(prediction_input_role, prep, 'output',
                'protenix_json_and_prepared_msa' if predictor == 'protenix' else 'boltz_yaml_and_msa',
                module + ':' + prep, identity_authority=module + ':' + prep,
                cardinality_authority=module + ':' + prep + ':output'))
            components.append(NativeComponent(prep, module + ':' + prep,
                canonical_bytes({'pred_method': method}), dependency_ids=('support-python', 'bms-source'),
                input_role_ids=(input_role,), output_role_ids=(prediction_input_role,),
                resources_json=resource_policy('CPU'), lifecycle_authority=module + ':' + prep + '; nextflow.config:process'))
            preparation = (prep,)
        components.append(NativeComponent(process, source,
            canonical_bytes({'pred_method': method, 'workflow': workflow}), depends_on=preparation,
            dependency_ids=deps + ('support-python', 'nextflow', 'apptainer', 'bms-source'),
            input_role_ids=(prediction_input_role,), output_role_ids=(output_role,),
            resources_json=resource_policy(), lifecycle_authority=source + '; nextflow.config:process',
            condition='pred_method=' + method,
            grouping_authority='modules/structure_prediction.nf:normalizeSequenceProducerInputs' if not complex_input else
                'modules/structure_prediction.nf:complexCanonicalProducerOutputs'))
        # Keep optional native outputs distinct from the canonical structure
        # join. Optional output channels do not make inference optional.
        native_outputs = ({'confidence': ('json', True), 'full_confidence': ('json', True),
                           'msa_report': ('json', True), 'logs': ('text', True)} if predictor == 'protenix' else
                          {'jsons': ('json', True), 'native_identity_artifacts': ('npz', True)} if predictor == 'boltz2' else
                          {'metrics': ('json', False), 'telemetry': ('json', False),
                           'manifest': ('json', False), 'summary': ('tsv', False)})
        if predictor == 'boltz2' and complex_input:
            native_outputs['cifs'] = ('mmcif', True)
        elif predictor == 'boltz2' and not native_bool('boltz_use_msa'):
            native_outputs['msa'] = ('a3m', True)
        elif predictor not in {'boltz2', 'protenix'} and process == 'ESMFold2MSAPredict':
            native_outputs['effective_settings'] = ('json', False)
        extra_role_ids = []
        for name, (fmt, optional) in native_outputs.items():
            role_id = predictor + ':' + name
            extra_role_ids.append(role_id)
            roles.append(NativeArtifactRole(role_id, process, 'output', name, source,
                requiredness='optional' if optional else 'required', identity_authority=source,
                publication_authority=entrypoint, format=fmt, cardinality_authority=source + ':output'))
        components[-1] = replace(components[-1], output_role_ids=(output_role, *extra_role_ids))
        if predictor in {'boltz2', 'protenix'}:
            use_key = 'protenix_use_msa' if predictor == 'protenix' else 'boltz_use_msa'
            enabled = native_bool(use_key, predictor == 'protenix')
            supplied = bool(p.get('msa_path'))
            state = 'disabled' if not enabled else 'supplied_or_prepared' if supplied else 'planned'
            # Complex manifests can contain chain-specific supplied alignments.
            # Determining that roster belongs to the native adapter, not IO here.
            if complex_input and enabled:
                state = 'unresolved'
            msa_settings = {key: value for key, value in p.items()
                            if key.startswith(('msa_', 'colabfold_', 'protenix_', 'boltz_'))}
            services.append(ExternalServiceIntent(predictor + ':msa', p.get('msa_provider'),
                'platform/api/services/msa_preparation.py; platform/api/services/model_msa_handoff.py',
                canonical_bytes(msa_settings), (input_role,), (predictor + ':msa_artifacts',), state))
            roles.append(NativeArtifactRole(predictor + ':msa_artifacts', process, 'input',
                'native_chain_alignments', 'platform/api/services/model_msa_handoff.py',
                requiredness='required' if enabled else 'optional'))
            if enabled:
                components[-1] = replace(components[-1],
                    input_role_ids=components[-1].input_role_ids + (predictor + ':msa_artifacts',))
                unresolved(predictor + ':msa', 'external_service_roles',
                    'platform/api/services/model_msa_handoff.py; biomodstack_msa_handoff.py',
                    'Native task/chain roster and supplied/cache/search admission must be bound by adapter')

    # Native predicates deliberately differ. Missing structure flag selects
    # FrustraMPNN; missing complex/design flag does not. Do not alter params or
    # manufacture missing typed Frustra settings to make the plan appear ready.
    frustra = (p.get('run_frustrampnn') is not False if workflow == 'structure_prediction' else
               p.get('run_frustrampnn') is True if workflow in {'complex_prediction', 'protein_design'} else False)
    if frustra:
        from native_components import append_native_frustra_coordinator
        append_native_frustra_coordinator(
            p, entrypoint, components, dynamic, dependencies, roles, services, unresolved)
        deps = managed('frustrampnn')
        upstream = tuple(role.role_id for role in roles if role.direction == 'output'
                         and role.category in {'canonical_candidates', 'canonical_structures'})
        dynamic.append(NativeComponent('frustrampnn', entrypoint + ':FrustraMPNN',
            canonical_bytes({'run_frustrampnn': p.get('run_frustrampnn'),
                'predicate': '!= false' if workflow == 'structure_prediction' else '== true',
                'settings': p.get('frustrampnn_settings'), 'cardinality': None}),
            depends_on=tuple(component.component_key for component in components),
            dependency_ids=deps + ('support-python', 'bms-source', 'apptainer'), input_role_ids=upstream,
            output_role_ids=('frustrampnn:analysis',),
            grouping_authority='platform/api/component_runtime.py:plan_frustrampnn',
            resources_json=resource_policy('frustrampnn_gpu'),
            lifecycle_authority='modules/frustrampnn_remote.nf; modules/frustrampnn_parent_fanout.nf',
            lifecycle_json=canonical_bytes({'error_strategy': 'terminate', 'max_retries': 0,
                'join_authority': 'platform/api/component_runtime.py:GroupingPlan',
                'cancellation_authority': 'platform/api/component_runtime.py:ComponentLedger'}),
            condition='run_frustrampnn != false' if workflow == 'structure_prediction' else 'run_frustrampnn == true',
            expansion_authority='platform/api/component_runtime.py:plan_frustrampnn',
            expansion_json=canonical_bytes({'authority': 'scripts/run_frustrampnn_parent_fanout.py',
                'child_model': 'frustrampnn', 'child_mode': 'analyze', 'child_stage': 'frustrampnn'})))
        roles.append(NativeArtifactRole('frustrampnn:analysis', 'frustrampnn', 'output',
            'frustration_analysis', 'platform/api/services/result_contracts.py:frustration_analysis_v1',
            identity_authority='platform/api/component_runtime.py:GroupingPlan', publication_authority=entrypoint))
        dependencies['support_tool:scripts/lib/component_adapter.py'] = SelectedDependency(
            'support_tool:scripts/lib/component_adapter.py', 'support_tool',
            'scripts/lib/component_adapter.py', 'platform/api/component_runtime.py')
        frustra_settings = p.get('frustrampnn_settings')
        if isinstance(frustra_settings, str):
            try:
                frustra_settings = json.loads(frustra_settings)
            except (ValueError, TypeError):
                frustra_settings = None
        if not isinstance(frustra_settings, dict):
            unresolved('frustrampnn', 'effective_settings', entrypoint + ':requireCompleteFrustraMPNNSettings',
                       'Selected stage requires complete typed native settings; none fabricated')

    # Parent identity does not replace the selected native generator's result
    # contract. Resolve through the existing family contract, not RFantibody.
    generator_family = ({'nanobody_binder': 'boltzgen',
                         'generator_backbone_refine': 'ppiflow'}.get(mode)
                        if model_id == 'antibody_denovo' else None)
    result = resolve_result_contract(model_type=model_id, stage_mode=mode,
                                     stage_family=generator_family)
    result_payload = result.model_dump()
    admission_authority = 'platform/api/services/nextflow.py; nextflow.config:profiles'
    retrieval_authority = 'platform/api/services/result_contracts.py:resolve_result_contract'
    if model_id == 'molecular_dynamics' and mode == 'simulate' and reviewed:
        # MD owns native aggregate/mandatory-analysis completion, not Design
        # analysis rows. Bind that existing authority instead of demanding a
        # generic Design contract that this workflow deliberately never emits.
        retrieval_authority = 'platform/api/services/md/completion.py:validate_and_finalize_md_job'
        result_payload = {'native_contract_authority': retrieval_authority,
            'aggregate_schema': 'bms.md.aggregate.v1',
            'completion_authority': 'platform/api/services/md/results.py:completion_barrier'}
    elif result.analysis_contract_id is None:
        unresolved(model_id, 'result_contract', retrieval_authority,
                   'Native result resolver does not establish a contract for selected identity')
    if availability != 'public':
        unresolved(model_id, 'availability', authority, f'Declaration availability is {availability}; not public admission',
            blocks=('preview_acceptance', 'launch') if availability == 'internal' else
                   ('preview_acceptance', 'provision', 'launch'))
    if model is not None and mode not in {item.id for item in model.modes}:
        unresolved(f'{model_id}/{mode}', 'mode_descriptor', entrypoint,
                   'Compiler/internal mode is not a declared public mode; native descriptor needs review')
    # Every selected process carries lifecycle and configured resource semantics;
    # immutable runtime bytes/physical placement remain materialization bindings.
    components = [replace(row, lifecycle_json=row.lifecycle_json or
        native_lifecycle_policy(p, 'gpu' if row.resources_json and
            json.loads(row.resources_json).get('gpu') else 'CPU')) for row in components]
    return SelectedExecutionMetadata(availability, authority, tuple(components), tuple(dynamic),
        tuple(dependencies[key] for key in sorted(dependencies)), tuple(roles), tuple(services),
        canonical_bytes(result_payload), admission_authority, retrieval_authority, tuple(blockers),
        closure_reviewed=reviewed or prediction or workflow in {'docking', 'fampnn_child'},
        descriptors_reviewed=reviewed or prediction or workflow in {'docking', 'fampnn_child'})


# Global registry instance
@lru_cache(maxsize=1)
def get_registry() -> ModelRegistry:
    """Get the global model registry instance."""
    return ModelRegistry()
