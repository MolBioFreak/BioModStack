"""Thin CPU graph construction adapter; deliberately no coordinate generation.

RDKit and the optional Biotite CCD are lazy native dependencies. No live store,
model registry, inference, protonation, pose, or source materialization is used.
The caller retains this recipe/graph as the unpositioned authoring companion;
only genuinely positioned documents go through existing immutable CIF custody.
"""
from __future__ import annotations

from hashlib import sha256
from importlib.metadata import version
from typing import Literal, cast
import json

from .molecular_construction_recipe import (
    ComplementRequest, ConstructionResult, GraphAtom, GraphBond,
    MolecularConstructionRecipe, Residue, Strand,
)


class ConstructionUnavailable(RuntimeError):
    """The selected graph dependency is not installed; never select a fallback."""


def _rdkit():
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise ConstructionUnavailable("Selected RDKit graph provider is not installed") from exc
    return Chem


def create_complement(request: ComplementRequest) -> Strand:
    """Explicit action only: returns a new strand, never changes its source."""
    source = request.source
    if request.new_strand_id == source.id:
        raise ValueError("a complementary strand needs a distinct stable identity")
    if len(request.new_residue_ids) != len(source.residues):
        raise ValueError("one explicit new residue identity is required per base")
    table = dict(zip("ACGT" if source.polymer == "DNA" else "ACGU",
                     "TGCA" if source.polymer == "DNA" else "UGCA"))
    # Chemistry and nicks are not copied or inferred for the new strand.
    return Strand(id=request.new_strand_id, polymer=source.polymer,
                  copy_ids=request.new_copy_ids,
                  residues=[Residue(id=i, base=cast(Literal["A", "C", "G", "T", "U"], table[r.base])) for i, r in
                            zip(request.new_residue_ids, reversed(source.residues))],
                  five_prime=request.five_prime, three_prime=request.three_prime)


def _identity(atom, strand, copy, residue, name, component, native_name=None, native_residue=None):
    atom.SetProp("bms_native_name", native_name if native_name is not None else name)
    if native_residue is not None:
        atom.SetIntProp("bms_native_residue", native_residue)
    for k, v in (("strand", strand), ("copy", copy), ("residue", residue),
                 ("name", name), ("component", component)):
        atom.SetProp("bms_" + k, v)


def _key(atom):
    return tuple(atom.GetProp("bms_" + k) for k in ("strand", "copy", "residue", "name"))


def _atom_id(key):
    return ":".join(key)


def _canonical_strand(Chem, strand, copy):
    breaks = {n.after: n for n in strand.nicks}
    segments = []
    start, five = 0, strand.five_prime
    for i, residue in enumerate(strand.residues):
        nick = breaks.get(residue.id)
        if nick is None and i != len(strand.residues) - 1:
            continue
        residues = strand.residues[start:i + 1]
        three = nick.left_three_prime if nick else strand.three_prime
        flavor = (6 if strand.polymer == "DNA" else 2)
        flavor += int(five == "phosphate_neutral") + 2 * int(three == "phosphate_neutral")
        mol = Chem.MolFromSequence("".join(r.base for r in residues), flavor=flavor)
        if mol is None:
            raise ValueError("RDKit could not construct the explicit canonical sequence")
        mol.RemoveAllConformers()
        for atom in mol.GetAtoms():
            info = atom.GetPDBResidueInfo()
            n = info.GetResidueNumber()
            name = info.GetName().strip()
            # RDKit's 3' phosphate cap occupies a synthetic n+1 residue. Map it
            # to the real terminal residue without aliasing its incoming P.
            if n == len(residues) + 1:
                n = len(residues)
                name = "3" + name
            residue = residues[n - 1]
            component = info.GetResidueName().strip()
            _identity(atom, strand.id, copy, residue.id, name, component,
                      native_name=info.GetName().strip(), native_residue=info.GetResidueNumber())
        segments.append(mol)
        start = i + 1
        if nick:
            five = nick.right_five_prime
    return segments


def _ccd_graph(Chem, replacement):
    try:
        from biotite.structure.info import get_from_ccd
    except ImportError as exc:
        raise ConstructionUnavailable("Selected CCD replacement requires Biotite's native CCD") from exc
    atoms = get_from_ccd("chem_comp_atom", replacement.ccd_id)
    bonds = get_from_ccd("chem_comp_bond", replacement.ccd_id)
    if atoms is None or bonds is None:
        raise ValueError(f"CCD component {replacement.ccd_id!r} is absent from the selected native CCD")
    names = atoms["atom_id"].as_array(str).tolist()
    elements = atoms["type_symbol"].as_array(str).tolist()
    if "D" in elements:
        raise ValueError("isotopically labeled CCD component mapping is not yet implemented")
    heavy = {n for n, e in zip(names, elements) if e not in ("H", "D")}
    omitted = set(replacement.omit_atoms)
    if len(omitted) != len(replacement.omit_atoms) or not omitted <= heavy:
        raise ValueError("omit_atoms must name distinct native CCD heavy atoms")
    if set(replacement.atom_map) != heavy - omitted:
        raise ValueError("every retained CCD heavy atom must have exactly one explicit atom map")
    targets = [(t.residue_id, t.atom_name) for t in replacement.atom_map.values()]
    if len(set(targets)) != len(targets):
        raise ValueError("CCD atom correspondence must be one-to-one")
    if {t.residue_id for t in replacement.atom_map.values()} != set(replacement.residue_ids):
        raise ValueError("each replaced residue must retain mapped CCD atoms")
    mol = Chem.RWMol()
    indices, stereo = {}, {}
    charges = atoms["charge"].as_array(int)
    configs = atoms["pdbx_stereo_config"].as_array(str)
    for name, element, charge, config in zip(names, elements, charges, configs):
        if name not in replacement.atom_map:
            continue
        target = replacement.atom_map[name]
        atom = Chem.Atom(element)
        atom.SetFormalCharge(int(charge))
        _identity(atom, replacement.strand_id, replacement.copy_id,
                  target.residue_id, target.atom_name, replacement.ccd_id, native_name=name)
        indices[name] = mol.AddAtom(atom)
        if config in ("R", "S"):
            stereo[indices[name]] = config
    orders = {"SING": Chem.BondType.SINGLE, "DOUB": Chem.BondType.DOUBLE,
              "TRIP": Chem.BondType.TRIPLE, "AROM": Chem.BondType.AROMATIC}
    for a, b, order in zip(bonds["atom_id_1"].as_array(str),
                           bonds["atom_id_2"].as_array(str),
                           bonds["value_order"].as_array(str)):
        if a in indices and b in indices:
            if order not in orders:
                raise ValueError(f"CCD bond order {order!r} is not implemented")
            mol.AddBond(indices[a], indices[b], orders[order])
    # Preserve native CCD absolute stereochemistry without borrowing its ideal
    # coordinates. Resolve CW/CCW via RDKit CIP, never invent coordinates.
    Chem.SanitizeMol(mol)
    for idx in stereo:
        mol.GetAtomWithIdx(idx).SetChiralTag(Chem.ChiralType.CHI_TETRAHEDRAL_CW)
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    for idx, desired in stereo.items():
        atom = mol.GetAtomWithIdx(idx)
        if not atom.HasProp("_CIPCode"):
            raise ValueError("CCD stereocenter cannot be retained after the declared atom omission")
        if atom.GetProp("_CIPCode") != desired:
            atom.InvertChirality()
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    for idx, desired in stereo.items():
        if mol.GetAtomWithIdx(idx).GetProp("_CIPCode") != desired:
            raise ValueError("native CCD stereochemistry could not be represented")
    # Native CCD alkene stereochemistry needs an explicit atom-neighbor mapping;
    # do not silently discard it for arbitrary custom components.
    if "pdbx_stereo_config" in bonds:
        if any(x in ("E", "Z") for x in bonds["pdbx_stereo_config"].as_array(str)):
            raise ValueError("CCD E/Z bond mapping is not yet implemented")
    return mol.GetMol()


def _replace(Chem, mol, replacement):
    native = _ccd_graph(Chem, replacement)
    span = {(replacement.strand_id, replacement.copy_id, r) for r in replacement.residue_ids}
    removed = {a.GetIdx() for a in mol.GetAtoms() if _key(a)[:3] in span}
    if replacement.kind != "terminal":
        # A base/sugar/linkage label cannot silently remove an explicitly
        # requested strand-end phosphate or create a new one. Terminal chemistry
        # changes instead name their own explicit terminal replacement operation.
        old_keys = {_key(mol.GetAtomWithIdx(i)) for i in removed}
        new_keys = {_key(a) for a in native.GetAtoms()}
        terminal_names = {"3P", "3OP1", "3OP2", "3OP3"}
        for key in old_keys:
            if key[3] in terminal_names and key not in new_keys:
                raise ValueError("nonterminal replacement would remove explicitly requested 3-prime terminal chemistry")
        old_phosphorus = {_key(mol.GetAtomWithIdx(i)) for i in removed if mol.GetAtomWithIdx(i).GetSymbol() == "P"}
        new_phosphorus = {_key(a) for a in native.GetAtoms() if a.GetSymbol() == "P"}
        if old_phosphorus != new_phosphorus:
            raise ValueError("nonterminal replacement changes explicitly requested phosphate topology")
    boundary = []
    for bond in mol.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if (a in removed) != (b in removed):
            boundary.append((_key(mol.GetAtomWithIdx(a)), _key(mol.GetAtomWithIdx(b)), bond.GetBondType()))
    edited = Chem.RWMol(mol)
    for idx in sorted(removed, reverse=True):
        edited.RemoveAtom(idx)
    edited = Chem.RWMol(Chem.CombineMols(edited, native))
    index = {_key(a): a.GetIdx() for a in edited.GetAtoms()}
    for first, second, order in boundary:
        if first not in index or second not in index:
            raise ValueError("replacement does not preserve an explicit external attachment atom")
        edited.AddBond(index[first], index[second], order)
    Chem.SanitizeMol(edited)
    return edited.GetMol()


def construct_graph(recipe: MolecularConstructionRecipe) -> ConstructionResult:
    recipe = recipe.model_copy(deep=True)
    Chem = _rdkit()
    mol = Chem.Mol()
    for strand in recipe.strands:
        for copy in strand.copy_ids:
            for segment in _canonical_strand(Chem, strand, copy):
                mol = Chem.CombineMols(mol, segment)
    for replacement in recipe.replacements:
        mol = _replace(Chem, mol, replacement)
    edited = Chem.RWMol(mol)
    index = {_key(a): a.GetIdx() for a in edited.GetAtoms()}
    if len(index) != edited.GetNumAtoms():
        raise ValueError("constructed atom identities are not unique")
    orders = {"single": Chem.BondType.SINGLE, "double": Chem.BondType.DOUBLE, "triple": Chem.BondType.TRIPLE}
    for link in recipe.covalent_links:
        keys = [(e.strand_id, e.copy_id, e.residue_id, e.atom_name) for e in (link.first, link.second)]
        if any(k not in index for k in keys):
            raise ValueError("covalent endpoint atom is absent from the actual constructed graph")
        a, b = [index[k] for k in keys]
        if a == b or edited.GetBondBetweenAtoms(a, b) is not None:
            raise ValueError("covalent link must name two distinct, previously unbonded atoms")
        edited.AddBond(a, b, orders[link.order])
    Chem.SanitizeMol(edited)
    Chem.AssignStereochemistry(edited, cleanIt=True, force=True)
    edited.RemoveAllConformers()
    atoms = []
    for a in edited.GetAtoms():
        key = _key(a)
        atoms.append(GraphAtom(id=_atom_id(key), strand_id=key[0], copy_id=key[1],
                               residue_id=key[2], atom_name=key[3],
                               component_id=a.GetProp("bms_component"),
                               native_atom_name=a.GetProp("bms_native_name"),
                               native_residue_number=a.GetIntProp("bms_native_residue") if a.HasProp("bms_native_residue") else None,
                               element=a.GetSymbol(),
                               formal_charge=a.GetFormalCharge(), isotope=a.GetIsotope(),
                               aromatic=a.GetIsAromatic(), chiral_tag=str(a.GetChiralTag()),
                               cip=a.GetProp("_CIPCode") if a.HasProp("_CIPCode") else None,
                               hydrogen_count=a.GetTotalNumHs()))
    bonds = [GraphBond(first=atoms[b.GetBeginAtomIdx()].id, second=atoms[b.GetEndAtomIdx()].id,
                       order=b.GetBondTypeAsDouble(), aromatic=b.GetIsAromatic(), stereo=str(b.GetStereo()))
             for b in edited.GetBonds()]
    fragments = [[atoms[i].id for i in frag] for frag in Chem.GetMolFrags(edited)]
    from rdkit import rdBase
    provenance = {"graph_provider": "rdkit", "rdkit_version": version("rdkit"),
                  "rdkit_native_version": rdBase.rdkitVersion,
                  "rdkit_license": "BSD-3-Clause (Python wheel packaging MIT)",
                  "coordinate_provider": "none", "phosphate_state": recipe.phosphate_state,
                  "hydrogens": "native implicit valence; no pH/protonation calculation",
                  "recipe_sha256": sha256(json.dumps(recipe.model_dump(mode="json"), sort_keys=True,
                                                       separators=(",", ":")).encode()).hexdigest()}
    if recipe.replacements:
        provenance.update(biotite_version=version("biotite"), component_provider="Biotite bundled PDB CCD",
                          component_license="PDB data CC0; Biotite BSD-3-Clause")
        from biotite.structure.info import get_from_ccd
        definitions = {r.ccd_id: {category: {k: get_from_ccd(category, r.ccd_id)[k].as_array(str).tolist()
                                            for k in get_from_ccd(category, r.ccd_id).keys()}
                                  for category in ("chem_comp_atom", "chem_comp_bond")}
                       for r in recipe.replacements}
        provenance["ccd_definitions_sha256"] = sha256(json.dumps(definitions, sort_keys=True,
                                                                separators=(",", ":")).encode()).hexdigest()
    return ConstructionResult(recipe=recipe, atoms=atoms, bonds=bonds,
                              connected_components=fragments,
                              isomeric_smiles=Chem.MolToSmiles(edited, isomericSmiles=True),
                              provenance=provenance,
                              observations=["Unpositioned chemical graph, not a coordinate document or a bound pose.",
                                            "Pairing/register/mismatches are authored annotations, not covalent bonds or validated geometry.",
                                            "Imported geometry remains owned by existing structure custody; this leaf does not alter it."])
