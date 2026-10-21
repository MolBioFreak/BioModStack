"""Protenix v2 ordinary-component scientific wire contract.

Only these fields are validated; unrelated ordinary model parameters retain
 their existing authority. Native task payloads do not pass through this adapter.
"""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

Positive = Annotated[StrictInt, Field(gt=0)]
Text = Annotated[StrictStr, Field(min_length=1)]
CCD = Annotated[StrictStr, Field(pattern=r'^CCD_.+')]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ProteinModification(Record):
    ptmPosition: Positive
    ptmType: CCD


class BaseModification(Record):
    basePosition: Positive
    modificationType: CCD


class LegacyMSA(Record):
    precomputed_msa_dir: Text


class Component(Record):
    # Explicit display metadata only; unknown science must not silently disappear.
    # `name` and scalar `id` are the existing BMS editor labels.
    type: Literal['protein', 'peptide', 'dna', 'rna', 'ligand', 'ion'] = 'protein'
    id: Text | list[Text] | None = None
    name: StrictStr | None = None
    count: Positive = 1
    copy_ids: list[Text] | None = None
    sequence: Text | None = None
    modifications: list[ProteinModification] | list[BaseModification] | None = None
    ccd: Text | None = None
    smiles: Text | None = None
    ligand_file: Text | None = Field(None, description='Managed BMS file selection; staged by compiler, not an arbitrary runtime path.')
    ion: Text | None = None
    element: Text | None = None
    unpairedMsa: StrictStr | None = None
    pairedMsa: StrictStr | None = None
    unpairedMsaPath: Text | None = None
    pairedMsaPath: Text | None = None
    msa: LegacyMSA | None = None
    templatesPath: Text | None = None

    @model_validator(mode='after')
    def scientific_shape(self):
        ids = self.copy_ids if self.copy_ids is not None else self.id if isinstance(self.id, list) else None
        if ids is not None and (len(ids) != self.count or len(set(ids)) != len(ids)):
            raise ValueError('copy_ids/native id must contain count unique IDs')
        if self.copy_ids is not None and isinstance(self.id, list) and self.copy_ids != self.id:
            raise ValueError('copy_ids conflicts with native list id')
        polymer = self.type in ('protein', 'peptide', 'dna', 'rna')
        if polymer and not self.sequence:
            raise ValueError('polymer sequence is required')
        if self.modifications is not None:
            cls = ProteinModification if self.type in ('protein', 'peptide') else BaseModification
            if not polymer or any(not isinstance(m, cls) for m in self.modifications):
                raise ValueError('modifications must use polymer-specific native position/type fields')
            for m in self.modifications:
                pos = m.ptmPosition if isinstance(m, ProteinModification) else m.basePosition
                if pos > len(self.sequence or ''):
                    raise ValueError('modifications position exceeds sequence length')
        if not polymer and self.sequence is not None:
            raise ValueError('sequence applies only to polymers')
        if self.type not in ('ligand', 'ion') and self.ccd is not None:
            raise ValueError('ccd applies to ligands/ions; polymers use modifications')
        if self.type != 'ligand' and (self.smiles is not None or self.ligand_file is not None):
            raise ValueError('smiles and ligand_file apply only to ligands')
        if self.type != 'ion' and (self.ion is not None or self.element is not None):
            raise ValueError('ion and element apply only to ions')
        if self.type == 'ligand' and sum(v is not None for v in (self.ccd, self.smiles, self.ligand_file)) != 1:
            raise ValueError('ligand requires exactly one of ccd, smiles, ligand_file')
        if self.smiles and self.smiles.startswith('FILE_'):
            raise ValueError('FILE ligands use managed ligand_file, not smiles')
        if self.type == 'ion' and not (self.ion or self.element or self.ccd):
            raise ValueError('ion requires ion, element or ccd')
        protein_fields = ('pairedMsa', 'pairedMsaPath', 'msa', 'templatesPath')
        if self.type not in ('protein', 'peptide') and any(getattr(self, key) is not None for key in protein_fields):
            raise ValueError('paired MSA, msa and templatesPath apply only to proteins')
        if self.type not in ('protein', 'peptide', 'rna') and any(getattr(self, key) is not None for key in ('unpairedMsa', 'unpairedMsaPath')):
            raise ValueError('unpaired MSA applies only to protein/RNA')
        return self


class CovalentBond(Record):
    left_entity: Positive = Field(validation_alias=AliasChoices("left_entity", "entity1"))
    right_entity: Positive = Field(validation_alias=AliasChoices("right_entity", "entity2"))
    left_position: Positive = Field(validation_alias=AliasChoices("left_position", "position1"))
    right_position: Positive = Field(validation_alias=AliasChoices("right_position", "position2"))
    left_atom: Text | StrictInt = Field(validation_alias=AliasChoices("left_atom", "atom1"))
    right_atom: Text | StrictInt = Field(validation_alias=AliasChoices("right_atom", "atom2"))
    left_copy: Positive | None = Field(None, validation_alias=AliasChoices("left_copy", "copy1"))
    right_copy: Positive | None = Field(None, validation_alias=AliasChoices("right_copy", "copy2"))


class Contact(Record):
    entity1: Positive
    entity2: Positive
    copy1: Positive
    copy2: Positive
    position1: Positive
    position2: Positive
    atom1: Text | StrictInt | None = None
    atom2: Text | StrictInt | None = None
    max_distance: Annotated[float, Field(gt=0)]
    min_distance: Annotated[float, Field(ge=0)] = 0


class Chain(Record):
    entity: Positive
    copy_index: Positive = Field(alias="copy")


class Residue(Chain):
    position: Positive


class Pocket(Record):
    binder_chain: Chain
    contact_residues: list[Residue]
    max_distance: Annotated[float, Field(gt=0)]


class Constraint(Record):
    contact: list[Contact] | None = None
    pocket: Pocket | None = None


class ProtenixChemistry(Record):
    complex_components: list[Component] | None = None
    covalent_bonds: list[CovalentBond] | None = None
    constraint: Constraint | None = None

    @model_validator(mode='after')
    def task_shape(self):
        if (self.covalent_bonds or self.constraint) and not self.complex_components:
            raise ValueError('covalent_bonds and constraint require ordered complex_components')
        components = self.complex_components or []

        def endpoint(entity, copy_index=None, position=None):
            if entity > len(components):
                raise ValueError('endpoint entity exceeds ordered component roster')
            component = components[entity - 1]
            if copy_index is not None and copy_index > component.count:
                raise ValueError('endpoint copy exceeds entity count')
            if component.sequence is not None:
                length = len(component.sequence)
            elif component.type == 'ligand' and component.ccd:
                length = len(component.ccd.removeprefix('CCD_').split('_'))
            else:
                length = 1
            if position is not None and position > length:
                raise ValueError('endpoint position exceeds native entity residue count')
            return component

        for bond in self.covalent_bonds or []:
            left = endpoint(bond.left_entity, bond.left_copy, bond.left_position)
            right = endpoint(bond.right_entity, bond.right_copy, bond.right_position)
            # Native add_bonds_between_entities zips equal-sized atom selections.
            if (1 if bond.left_copy is not None else left.count) != (1 if bond.right_copy is not None else right.count):
                raise ValueError('bond endpoints must select equal copy counts')
        if self.constraint:
            for contact in self.constraint.contact or []:
                endpoint(contact.entity1, contact.copy1, contact.position1)
                endpoint(contact.entity2, contact.copy2, contact.position2)
                if (contact.entity1, contact.copy1) == (contact.entity2, contact.copy2):
                    raise ValueError('native contact endpoints cannot be on the same chain')
                if contact.max_distance < contact.min_distance:
                    raise ValueError('max_distance must be at least min_distance')
            pocket = self.constraint.pocket
            if pocket:
                binder = pocket.binder_chain
                endpoint(binder.entity, binder.copy_index)
                for residue in pocket.contact_residues:
                    endpoint(residue.entity, residue.copy_index, residue.position)
                    if (binder.entity, binder.copy_index) == (residue.entity, residue.copy_index):
                        raise ValueError('native pocket residue cannot be on binder chain')
        return self


def validate_chemistry(params):
    """Validate only owned science, without normalizing the stored request."""
    return ProtenixChemistry.model_validate({k: params[k] for k in ProtenixChemistry.model_fields if k in params})


def parameter_schema():
    schema = ProtenixChemistry.model_json_schema()
    schema['schema_name'] = 'protenix_chemistry'
    schema['schema_version'] = 1
    return schema
