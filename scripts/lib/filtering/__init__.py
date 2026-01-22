"""
Unified structure filtering library for BioModStack.

Provides common functionality for filtering protein structures
across different pipeline stages (backbone, sequence, prediction).
"""

from .base import StructureFilter
from .metrics import calculate_secondary_structure, calculate_radius_of_gyration
from .converters import cif_to_pdb, load_structure_coords

__all__ = [
    'StructureFilter',
    'calculate_secondary_structure',
    'calculate_radius_of_gyration', 
    'cif_to_pdb',
    'load_structure_coords',
]
