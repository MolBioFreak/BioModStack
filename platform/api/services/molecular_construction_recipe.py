"""Closed authoring contract for unpositioned nucleic-acid chemistry.

This is a preparation leaf, not a generator request or a durable source store.
All residue sequences run 5' to 3'; pairing never creates covalent bonds.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Terminus = Literal["hydroxyl", "phosphate_neutral"]


class Residue(ClosedModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    base: Literal["A", "C", "G", "T", "U"]


class Nick(ClosedModel):
    after: str
    left_three_prime: Terminus
    right_five_prime: Terminus


class Strand(ClosedModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    polymer: Literal["DNA", "RNA"]
    copy_ids: list[str] = Field(min_length=1)
    residues: list[Residue] = Field(min_length=1)
    five_prime: Terminus
    three_prime: Terminus
    nicks: list[Nick] = Field(default_factory=list)

    @model_validator(mode="after")
    def identities(self):
        ids = [r.id for r in self.residues]
        if len(set(ids)) != len(ids) or len(set(self.copy_ids)) != len(self.copy_ids):
            raise ValueError("residue and copy identities must be unique within a strand")
        if any(not c or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for x in c) for c in self.copy_ids):
            raise ValueError("copy IDs use letters, numbers, underscore, dot or dash")
        alphabet = set("ACGT" if self.polymer == "DNA" else "ACGU")
        if any(r.base not in alphabet for r in self.residues):
            raise ValueError("base does not belong to the explicitly selected polymer; no T/U conversion")
        breaks = [n.after for n in self.nicks]
        if len(set(breaks)) != len(breaks) or any(x not in ids[:-1] for x in breaks):
            raise ValueError("each nick must name a distinct nonterminal residue")
        return self


class ResidueEndpoint(ClosedModel):
    strand_id: str
    copy_id: str
    residue_id: str


class AtomEndpoint(ResidueEndpoint):
    atom_name: str = Field(min_length=1)


class Pair(ClosedModel):
    first: ResidueEndpoint
    second: ResidueEndpoint
    relation: Literal["canonical", "mismatch", "unspecified"]


class Pairing(ClosedModel):
    id: str
    orientation: Literal["antiparallel", "parallel", "unspecified"]
    pairs: list[Pair] = Field(min_length=1)


class CCDAtomTarget(ClosedModel):
    residue_id: str
    atom_name: str = Field(min_length=1)


class CCDReplacement(ClosedModel):
    """Replace a complete explicitly mapped residue span with a native CCD graph.

    Omitted atoms must be declared (e.g. phosphate leaving groups). Every retained
    heavy atom needs a map. External bonds reconnect by the existing atom names.
    This accommodates base, sugar, complete linkage and terminal components
    without guessing chemistry from a modification label.
    """
    id: str
    kind: Literal["base", "sugar", "linkage", "terminal"]
    strand_id: str
    copy_id: str
    residue_ids: list[str] = Field(min_length=1)
    ccd_id: str = Field(min_length=1)
    omit_atoms: list[str] = Field(default_factory=list)
    atom_map: dict[str, CCDAtomTarget]


class CovalentLink(ClosedModel):
    first: AtomEndpoint
    second: AtomEndpoint
    order: Literal["single", "double", "triple"]


class MolecularConstructionRecipe(ClosedModel):
    schema_version: Literal["bms.molecular-construction.v1"] = "bms.molecular-construction.v1"
    graph_provider: Literal["rdkit"]
    # RDKit canonical phosphates and selected CCD components retain their native
    # neutral acid state. This is not a pH/protonation calculation.
    phosphate_state: Literal["neutral_acid"]
    geometry: Literal["unpositioned"]
    strands: list[Strand] = Field(min_length=1)
    pairing: list[Pairing] = Field(default_factory=list)
    replacements: list[CCDReplacement] = Field(default_factory=list)
    covalent_links: list[CovalentLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def endpoints(self):
        ids = [s.id for s in self.strands]
        if len(set(ids)) != len(ids):
            raise ValueError("strand IDs must be unique")
        valid = {(s.id, c, r.id) for s in self.strands for c in s.copy_ids for r in s.residues}
        def check(e):
            if (e.strand_id, e.copy_id, e.residue_id) not in valid:
                raise ValueError("endpoint does not resolve to a declared strand/copy/residue")
        pair_ids = [p.id for p in self.pairing]
        if len(set(pair_ids)) != len(pair_ids):
            raise ValueError("pairing IDs must be unique")
        for group in self.pairing:
            for pair in group.pairs:
                check(pair.first); check(pair.second)
                a = (pair.first.strand_id, pair.first.copy_id, pair.first.residue_id)
                b = (pair.second.strand_id, pair.second.copy_id, pair.second.residue_id)
                if a == b:
                    raise ValueError("a pairing endpoint cannot be paired with itself")
        for link in self.covalent_links:
            check(link.first); check(link.second)
        modified = set()
        mod_ids = set()
        for replacement in self.replacements:
            if replacement.id in mod_ids:
                raise ValueError("replacement IDs must be unique")
            mod_ids.add(replacement.id)
            span = {(replacement.strand_id, replacement.copy_id, r) for r in replacement.residue_ids}
            if not span or len(span) != len(replacement.residue_ids) or not span <= valid or span & modified:
                raise ValueError("replacement spans must resolve and not overlap")
            modified.update(span)
            if any(t.residue_id not in replacement.residue_ids for t in replacement.atom_map.values()):
                raise ValueError("CCD atom maps must stay inside their declared residue span")
        return self


class ComplementRequest(ClosedModel):
    source: Strand
    new_strand_id: str
    new_copy_ids: list[str] = Field(min_length=1)
    new_residue_ids: list[str] = Field(min_length=1)
    five_prime: Terminus
    three_prime: Terminus


class GraphAtom(ClosedModel):
    id: str
    strand_id: str
    copy_id: str
    residue_id: str
    atom_name: str
    component_id: str
    native_atom_name: str
    native_residue_number: int | None
    element: str
    formal_charge: int
    isotope: int
    aromatic: bool
    chiral_tag: str
    cip: str | None
    hydrogen_count: int


class GraphBond(ClosedModel):
    first: str
    second: str
    order: float
    aromatic: bool
    stereo: str


class ConstructionResult(ClosedModel):
    schema_version: Literal["bms.molecular-graph.v1"] = "bms.molecular-graph.v1"
    recipe: MolecularConstructionRecipe
    atoms: list[GraphAtom]
    bonds: list[GraphBond]
    connected_components: list[list[str]]
    isomeric_smiles: str
    geometry: Literal["unpositioned"] = "unpositioned"
    coordinates: None = None
    provenance: dict[str, str]
    observations: list[str]
