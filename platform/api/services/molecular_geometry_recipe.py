"""Opt-in idealized native coordinates, separate from unpositioned chemistry."""
from typing import Literal
from pydantic import Field
from services.molecular_construction_recipe import ClosedModel, ConstructionResult, MolecularConstructionRecipe


class MolecularGeometryRequest(ClosedModel):
    schema_version: Literal['bms.molecular-geometry-request.v1'] = 'bms.molecular-geometry-request.v1'
    operation: Literal['create_new_idealized_geometry']
    provider: Literal['ambertools24_fd_helix']
    form: Literal['abdna', 'adna', 'arna']
    topology: Literal['canonical_antiparallel_duplex']
    hydrogen_policy: Literal['heavy_atom_coordinates_graph_implicit_hydrogens']
    construction: MolecularConstructionRecipe


class PositionedAtom(ClosedModel):
    graph_atom_id: str
    pdb_serial: int
    native_chain: str
    native_residue_number: int
    native_component: str
    native_atom_name: str
    x: float
    y: float
    z: float


class GeometryProvenance(ClosedModel):
    provider: Literal['ambertools24_fd_helix']
    ambertools_version: Literal['24.8']
    image_sha256: str
    library_sha256: str
    native_object_sha256: str
    wrapper_sha256: str
    build_receipt_sha256: str
    recipe_sha256: str
    source_sha256: str
    license: Literal['GPL-3.0-or-later']


class MolecularGeometryResult(ClosedModel):
    schema_version: Literal['bms.molecular-geometry.v1'] = 'bms.molecular-geometry.v1'
    request: MolecularGeometryRequest
    graph: ConstructionResult
    format: Literal['pdb'] = 'pdb'
    # ASCII is byte-exact: encode('ascii') for ordinary immutable-file custody.
    coordinate_text: str
    coordinate_sha256: str
    coordinate_size_bytes: int
    atom_map: list[PositionedAtom]
    provenance: GeometryProvenance
    observations: list[str] = Field(default_factory=list)
