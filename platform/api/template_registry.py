"""
Template Registry - Load and serve experiment templates.
"""

from pathlib import Path
from typing import Optional, List
import yaml
from pydantic import BaseModel


class TemplateStage(BaseModel):
    """A single stage in an experiment template."""
    name: str
    tool: str
    description: str


class TemplateParam(BaseModel):
    """A user-configurable parameter in a template."""
    name: str
    label: str
    type: str
    required: bool = False
    default: Optional[str | int | float | bool] = None
    description: str = ""
    enum: Optional[List[str]] = None
    enum_labels: Optional[dict] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    ui_control: Optional[str] = None
    ui_group: Optional[str] = None
    ui_order: Optional[int] = None
    ui_placeholder: Optional[str] = None
    placeholder: Optional[str] = None
    preset_type: Optional[str] = None
    file_type: Optional[str] = None
    recommended_range: Optional[str] = None
    default_source: Optional[str] = None
    condition: Optional[dict] = None  # Conditional visibility: {param: str, values: List[str]}


class NTPTemplate(BaseModel):
    """NTP template for ligand-aware templates."""
    id: str
    name: str
    smiles: str


class ExperimentTemplate(BaseModel):
    """Full experiment template definition."""
    id: str
    name: str
    icon: str
    color: str
    description: str
    goal: Optional[str] = None
    status: Optional[str] = None
    stages: List[TemplateStage]
    preset_params: dict
    user_params: List[TemplateParam]
    ntp_templates: Optional[List[NTPTemplate]] = None
    enabled: bool = True
    experimental: bool = False


class TemplateRegistry:
    """Registry for loading and serving experiment templates."""
    
    _instance: Optional['TemplateRegistry'] = None
    
    def __init__(self, templates_dir: Path):
        self.templates_dir = templates_dir
        self._templates: dict[str, ExperimentTemplate] = {}
        self._load_templates()
    
    @classmethod
    def get_instance(cls, templates_dir: Optional[Path] = None) -> 'TemplateRegistry':
        if cls._instance is None:
            if templates_dir is None:
                templates_dir = Path(__file__).parent / "config" / "templates"
            cls._instance = cls(templates_dir)
        return cls._instance
    
    def _load_templates(self):
        """Load all template YAML files."""
        if not self.templates_dir.exists():
            return
        
        for yaml_file in self.templates_dir.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                
                # Parse stages
                stages = [TemplateStage(**s) for s in data.get('stages', [])]
                
                # Parse user params
                user_params = [TemplateParam(**p) for p in data.get('user_params', [])]
                
                # Parse NTP templates if present
                ntp_templates = None
                if 'ntp_templates' in data:
                    ntp_templates = [NTPTemplate(**t) for t in data['ntp_templates']]
                
                template = ExperimentTemplate(
                    id=data['id'],
                    name=data['name'],
                    icon=data.get('icon', 'beaker'),
                    color=data.get('color', '#6366F1'),
                    description=data.get('description', ''),
                    goal=data.get('goal'),
                    status=data.get('status'),
                    stages=stages,
                    preset_params=data.get('preset_params', {}),
                    user_params=user_params,
                    ntp_templates=ntp_templates,
                    enabled=data.get('enabled', True),
                    experimental=data.get('experimental', False)
                )
                
                self._templates[template.id] = template
                
            except Exception as e:
                print(f"Warning: Failed to load template {yaml_file}: {e}")
    
    def list_templates(self, enabled_only: bool = True) -> List[ExperimentTemplate]:
        """List all templates."""
        templates = list(self._templates.values())
        if enabled_only:
            templates = [t for t in templates if t.enabled]
        return templates
    
    def get_template(self, template_id: str) -> Optional[ExperimentTemplate]:
        """Get a specific template by ID."""
        return self._templates.get(template_id)


# Singleton accessor
def get_template_registry() -> TemplateRegistry:
    return TemplateRegistry.get_instance()
