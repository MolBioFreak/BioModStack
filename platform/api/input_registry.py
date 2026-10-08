"""
Input Registry - Load and serve input presets and standard paths.
"""

from pathlib import Path
from typing import Optional, List
import yaml
from pydantic import BaseModel


class PresetPDB(BaseModel):
    """A preset PDB file."""
    id: str
    name: str
    path: str
    description: str = ""
    category: str = "general"


class PresetSequence(BaseModel):
    """A preset amino acid sequence."""
    id: str
    name: str
    sequence: str
    description: str = ""
    length: int = 0
    uniprot: str = ""


class PresetYAML(BaseModel):
    """A preset YAML configuration."""
    id: str
    name: str
    description: str = ""
    content: str = ""


class PresetContig(BaseModel):
    """A preset contig specification."""
    id: str
    name: str
    value: str
    description: str = ""


class PresetNTP(BaseModel):
    """A preset NTP template."""
    id: str
    name: str
    smiles: str
    description: str = ""


class PresetDirectory(BaseModel):
    """A preset directory for batch processing."""
    id: str
    name: str
    path: str
    description: str = ""
    count: int = 0
    filter_ids: list[str] = []  # Optional filter to specific preset IDs
    absolute_path: str = ""


class StandardPath(BaseModel):
    """A standard directory path."""
    id: str
    path: str
    description: str = ""
    absolute_path: str = ""


class InputRegistry:
    """Registry for loading and serving input presets."""
    
    _instance: Optional['InputRegistry'] = None
    
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.project_root: str = ""
        self.standard_paths: dict[str, StandardPath] = {}
        self.preset_directories: list[PresetDirectory] = []
        self.preset_pdbs: list[PresetPDB] = []
        self.preset_sequences: list[PresetSequence] = []
        self.preset_yamls: list[PresetYAML] = []
        self.preset_contigs: list[PresetContig] = []
        self.preset_ntps: list[PresetNTP] = []
        self._load_config()
    
    @classmethod
    def get_instance(cls, config_path: Optional[Path] = None) -> 'InputRegistry':
        if cls._instance is None:
            if config_path is None:
                config_path = Path(__file__).parent / "config" / "inputs.yaml"
            cls._instance = cls(config_path)
        return cls._instance
    
    def _load_config(self):
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            return
        
        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f)
            
            self.project_root = data.get('project_root', '')
            
            # Load standard paths
            for path_id, path_data in data.get('standard_paths', {}).items():
                abs_path = str(Path(self.project_root) / path_data['path'])
                self.standard_paths[path_id] = StandardPath(
                    id=path_id,
                    path=path_data['path'],
                    description=path_data.get('description', ''),
                    absolute_path=abs_path
                )
            
            # Load preset PDBs
            for pdb in data.get('preset_pdbs', []):
                pdb['path'] = str(Path(self.project_root) / pdb['path'])
                self.preset_pdbs.append(PresetPDB(**pdb))
            
            # Load preset sequences
            for seq in data.get('preset_sequences', []):
                if 'length' not in seq:
                    seq['length'] = len(seq.get('sequence', ''))
                self.preset_sequences.append(PresetSequence(**seq))
            
            # Load preset YAMLs
            for yml in data.get('preset_yamls', []):
                self.preset_yamls.append(PresetYAML(**yml))
            
            # Load preset contigs
            for contig in data.get('preset_contigs', []):
                self.preset_contigs.append(PresetContig(**contig))
            
            # Load preset NTPs
            for ntp in data.get('preset_ntps', []):
                self.preset_ntps.append(PresetNTP(**ntp))
            
            # Load preset directories
            for dir_preset in data.get('preset_directories', []):
                abs_path = str(Path(self.project_root) / dir_preset['path'])
                dir_preset['absolute_path'] = abs_path
                if 'filter_ids' not in dir_preset:
                    dir_preset['filter_ids'] = []
                self.preset_directories.append(PresetDirectory(**dir_preset))
        except Exception as e:
            print(f"Warning: Failed to load input config: {e}")
    
    def list_presets(self, preset_type: str) -> list:
        """List presets by type."""
        if preset_type == 'pdb':
            return self.preset_pdbs
        elif preset_type == 'sequence':
            return self.preset_sequences
        elif preset_type == 'yaml':
            return self.preset_yamls
        elif preset_type == 'contig':
            return self.preset_contigs
        elif preset_type == 'ntp':
            return self.preset_ntps
        elif preset_type == 'directory':
            return self.preset_directories
        else:
            return []
    
    def get_preset(self, preset_type: str, preset_id: str):
        """Get a specific preset by type and ID."""
        presets = self.list_presets(preset_type)
        for p in presets:
            if p.id == preset_id:
                return p
        return None
    
    def get_standard_paths(self) -> dict[str, StandardPath]:
        """Get all standard paths."""
        return self.standard_paths


# Singleton accessor
def get_input_registry() -> InputRegistry:
    return InputRegistry.get_instance()
