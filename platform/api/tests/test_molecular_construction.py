"""Real native CPU chemistry qualification, isolated from stores and inference."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
Chem = pytest.importorskip("rdkit.Chem", reason="requires the explicitly provisioned CPU molecular graph environment")
from rdkit.Chem import rdMolDescriptors
from biotite.structure.info import get_from_ccd

from services.molecular_construction import construct_graph, create_complement
from services.molecular_construction_recipe import (
    ComplementRequest, ConstructionResult, MolecularConstructionRecipe,
)


def strand(id="s", sequence="ACGT", polymer="DNA", copies=None, five="hydroxyl", three="hydroxyl"):
    return dict(id=id, polymer=polymer, copy_ids=copies or ["c1"],
                residues=[dict(id=f"r{i+1}", base=b) for i, b in enumerate(sequence)],
                five_prime=five, three_prime=three)


def recipe(*strands, **kwargs):
    return MolecularConstructionRecipe.model_validate(dict(graph_provider="rdkit", phosphate_state="neutral_acid",
                                                           geometry="unpositioned", strands=list(strands), **kwargs))


def endpoint(s, r, copy="c1", atom=None):
    x = dict(strand_id=s, copy_id=copy, residue_id=r)
    if atom:
        x["atom_name"] = atom
    return x


def rebuild(result):
    """Independent receiver reconstructs graph using only exported typed atoms/bonds."""
    result = ConstructionResult.model_validate_json(result.model_dump_json())
    m = Chem.RWMol()
    index = {}
    for row in result.atoms:
        a = Chem.Atom(row.element)
        a.SetFormalCharge(row.formal_charge)
        a.SetIsotope(row.isotope)
        a.SetIsAromatic(row.aromatic)
        a.SetNumExplicitHs(row.hydrogen_count)
        a.SetNoImplicit(True)
        a.SetChiralTag(getattr(Chem.ChiralType, row.chiral_tag))
        index[row.id] = m.AddAtom(a)
    for row in result.bonds:
        m.AddBond(index[row.first], index[row.second],
                  {1.0: Chem.BondType.SINGLE, 2.0: Chem.BondType.DOUBLE,
                   3.0: Chem.BondType.TRIPLE, 1.5: Chem.BondType.AROMATIC}[row.order])
    Chem.SanitizeMol(m)
    Chem.AssignStereochemistry(m, cleanIt=True, force=True)
    assert Chem.MolToSmiles(m) == result.isomeric_smiles
    assert m.GetNumConformers() == 0
    return m


@pytest.mark.parametrize("polymer,sequence,flavor", [("DNA", "ACGT", 6), ("RNA", "ACGU", 2),
                                                   ("DNA", "A", 6), ("RNA", "U", 2)])
@pytest.mark.parametrize("five,three,offset", [("hydroxyl", "hydroxyl", 0),
                                             ("phosphate_neutral", "hydroxyl", 1),
                                             ("hydroxyl", "phosphate_neutral", 2),
                                             ("phosphate_neutral", "phosphate_neutral", 3)])
def test_canonical_graph_and_explicit_termini(polymer, sequence, flavor, five, three, offset):
    result = construct_graph(recipe(strand(sequence=sequence, polymer=polymer, five=five, three=three)))
    actual = rebuild(result)
    reference = Chem.MolFromSequence(sequence, flavor=flavor + offset)
    assert Chem.MolToSmiles(actual) == Chem.MolToSmiles(reference)
    assert len(result.connected_components) == 1
    assert sum(a.element == "P" for a in result.atoms) == len(sequence) - 1 + (offset & 1) + (offset >> 1)
    assert all(a.formal_charge == 0 for a in result.atoms)
    assert result.coordinates is None and result.geometry == "unpositioned"
    assert result.provenance["coordinate_provider"] == "none"
    if three == "phosphate_neutral":
        cap = next(a for a in result.atoms if a.atom_name == "3P")
        assert cap.native_atom_name == "P" and cap.native_residue_number == len(sequence) + 1
        assert cap.component_id == "2PO" and cap.residue_id == f"r{len(sequence)}"
    # Each true interresidue phosphodiester bond has the correct 3' O -> next P.
    actual_bonds = {frozenset((b.first, b.second)) for b in result.bonds}
    for i in range(1, len(sequence)):
        assert frozenset((f"s:c1:r{i}:O3'", f"s:c1:r{i+1}:P")) in actual_bonds


@pytest.mark.parametrize("polymer,seq1,seq2", [("DNA", "AACGT", "ACGT"), ("RNA", "AACGU", "ACGU")])
def test_explicit_duplex_register_overhang_mismatch_and_copy_identity(polymer, seq1, seq2):
    pairs = [dict(first=endpoint("top", f"r{i+2}"), second=endpoint("bottom", f"r{4-i}"),
                  relation="mismatch" if i == 0 else "canonical") for i in range(4)]
    r = recipe(strand("top", seq1, polymer), strand("bottom", seq2, polymer),
               pairing=[dict(id="p", orientation="antiparallel", pairs=pairs)])
    result = construct_graph(r)
    assert len(result.connected_components) == 2
    assert len(result.recipe.strands) == 2
    assert result.recipe.pairing[0].pairs[0].relation == "mismatch"
    assert all(not ((b.first.startswith("top:")) != (b.second.startswith("top:"))) for b in result.bonds)
    # Independent rows alone do not imply a duplex, complement, or base pairing.
    unpaired = construct_graph(recipe(*[s.model_dump() for s in r.strands]))
    assert unpaired.isomeric_smiles == result.isomeric_smiles
    assert not unpaired.recipe.pairing
    single = construct_graph(recipe(strand("top", seq1, polymer, copies=["first", "second"])))
    assert len(single.connected_components) == 2 and not single.recipe.pairing
    reordered = construct_graph(recipe(*[s.model_dump() for s in reversed(r.strands)]))
    assert {a.id for a in reordered.atoms} == {a.id for a in result.atoms}


def test_nicks_are_physical_connected_fragments_with_independent_ends():
    s = strand(sequence="ACGT")
    s["nicks"] = [dict(after="r2", left_three_prime="phosphate_neutral", right_five_prime="hydroxyl")]
    result = construct_graph(recipe(s))
    assert len(result.connected_components) == 2
    assert {a.atom_name for a in result.atoms if a.residue_id == "r2"} >= {"3P", "3OP1", "3OP2", "3OP3"}
    assert not any(a.atom_name == "P" and a.residue_id == "r3" for a in result.atoms)
    expected = Chem.MolToSmiles(Chem.CombineMols(Chem.MolFromSequence("AC", flavor=8),
                                               Chem.MolFromSequence("GT", flavor=6)))
    assert result.isomeric_smiles == expected
    rebuild(result)


@pytest.mark.parametrize("polymer,seq,expected", [("DNA", "AACGT", "ACGTT"), ("RNA", "AACGU", "ACGUU")])
def test_complement_is_explicit_and_new_identity(polymer, seq, expected):
    source = recipe(strand(sequence=seq, polymer=polymer)).strands[0]
    req = ComplementRequest(source=source, new_strand_id="other", new_copy_ids=["new"],
                            new_residue_ids=[f"n{i}" for i in range(len(seq))],
                            five_prime="phosphate_neutral", three_prime="hydroxyl")
    original = source.model_dump_json()
    complement = create_complement(req)
    assert "".join(r.base for r in complement.residues) == expected
    assert not complement.nicks
    assert source.model_dump_json() == original
    assert len(construct_graph(recipe(source.model_dump())).connected_components) == 1


def ccd_replacement(id, ccd, residue, strand_id="s", copy="c1", kind="base", omit=None) -> dict[str, Any]:
    atoms = get_from_ccd("chem_comp_atom", ccd)
    names = [str(n) for n, e in zip(atoms["atom_id"].as_array(str), atoms["type_symbol"].as_array(str))
             if e not in ("H", "D") and n not in (omit or [])]
    return dict(id=id, kind=kind, strand_id=strand_id, copy_id=copy, residue_ids=[residue], ccd_id=ccd,
                omit_atoms=omit or [], atom_map={n: dict(residue_id=residue, atom_name=n) for n in names})


@pytest.mark.parametrize("selected_strand", ["top", "bottom"])
def test_native_ccd_base_modification_either_strand_and_roundtrip(selected_strand):
    mod = ccd_replacement("methylC", "5CM", "r2", strand_id=selected_strand, omit=["OP3"])
    r = recipe(strand("top", "ACG"), strand("bottom", "ACG"), replacements=[mod])
    result = construct_graph(r)
    assert any(a.atom_name == "C5A" and a.strand_id == selected_strand for a in result.atoms)
    assert not any(a.atom_name == "C5A" and a.strand_id != selected_strand for a in result.atoms)
    assert len(result.connected_components) == 2
    assert result.provenance["component_provider"] == "Biotite bundled PDB CCD"
    assert result.provenance["ccd_definitions_sha256"]
    rebuild(result)
    raw = result.model_dump_json()
    with patch("services.molecular_construction.construct_graph", side_effect=AssertionError("reopen must not run science")):
        reopened = ConstructionResult.model_validate_json(raw)
    assert reopened == result
    # Undo is the caller's retained preceding graph/recipe, not recomputation on reopen.
    plain = construct_graph(recipe(strand("top", "ACG"), strand("bottom", "ACG")))
    assert rdMolDescriptors.CalcMolFormula(rebuild(result)) != rdMolDescriptors.CalcMolFormula(rebuild(plain))


@pytest.mark.parametrize("polymer,base,ccd", [("DNA", "A", "DA"), ("DNA", "C", "DC"),
                                                   ("DNA", "G", "DG"), ("DNA", "T", "DT"),
                                                   ("RNA", "A", "A"), ("RNA", "C", "C"),
                                                   ("RNA", "G", "G"), ("RNA", "U", "U")])
def test_rdkit_canonical_differential_against_native_ccd(polymer, base, ccd):
    mod = ccd_replacement("native", ccd, "r1", omit=["P", "OP1", "OP2", "OP3"])
    native = construct_graph(recipe(strand(sequence=base, polymer=polymer), replacements=[mod]))
    canonical = construct_graph(recipe(strand(sequence=base, polymer=polymer)))
    assert native.isomeric_smiles == canonical.isomeric_smiles
    rebuild(native)


def test_intramolecular_pairing_and_gap_are_annotations_not_extra_strands():
    s = strand(sequence="ACGTT")
    r = recipe(s, pairing=[dict(id="hairpin", orientation="antiparallel", pairs=[
        dict(first=endpoint("s", "r1"), second=endpoint("s", "r5"), relation="canonical"),
        dict(first=endpoint("s", "r2"), second=endpoint("s", "r4"), relation="mismatch")])])
    actual = construct_graph(r)
    assert len(actual.connected_components) == 1
    assert actual.isomeric_smiles == construct_graph(recipe(s)).isomeric_smiles
    assert actual.recipe.pairing[0].pairs[1].relation == "mismatch"
    # The r3 loop/bulge remains a declared residue, not an inserted or deleted base.
    assert {a.residue_id for a in actual.atoms} == {"r1", "r2", "r3", "r4", "r5"}


def test_nonterminal_edit_cannot_silently_change_authored_end_chemistry():
    mod = ccd_replacement("m", "5CM", "r1")
    with pytest.raises(ValueError, match="phosphate topology"):
        construct_graph(recipe(strand(sequence="C"), replacements=[mod]))
    mod = ccd_replacement("m", "5CM", "r1", omit=["P", "OP1", "OP2", "OP3"])
    with pytest.raises(ValueError, match="terminal chemistry"):
        construct_graph(recipe(strand(sequence="C", three="phosphate_neutral"), replacements=[mod]))


def test_native_terminal_replacement_is_a_graph_and_annotation_transaction():
    before = construct_graph(recipe(strand(sequence="C")))
    terminal = ccd_replacement("five_prime_phosphate", "DC", "r1", kind="terminal")
    after = construct_graph(recipe(strand(sequence="C"), replacements=[terminal]))
    assert len(after.connected_components) == 1
    assert sum(a.element == "P" for a in before.atoms) == 0
    assert sum(a.element == "P" for a in after.atoms) == 1
    assert after.isomeric_smiles == construct_graph(recipe(strand(sequence="C", five="phosphate_neutral"))).isomeric_smiles
    retained_before = before.model_dump_json()
    retained_after = after.model_dump_json()
    assert ConstructionResult.model_validate_json(retained_after).recipe.replacements[0].kind == "terminal"
    assert ConstructionResult.model_validate_json(retained_before) == before
    rebuild(after)


def test_returned_recipe_does_not_alias_the_editable_input():
    authored = recipe(strand())
    snapshot = construct_graph(authored)
    frozen = snapshot.model_dump_json()
    authored.strands.reverse()
    authored.strands[0].residues[0].base = "T"
    assert snapshot.model_dump_json() == frozen


def test_native_sugar_component_and_terminal_chemistry_are_real_atoms():
    # A2M = 2'-O-methyladenosine 5'-(dihydrogen phosphate), native CCD.
    mod = ccd_replacement("sugar", "A2M", "r1", kind="sugar")
    result = construct_graph(recipe(strand(sequence="AC", polymer="RNA", five="phosphate_neutral"), replacements=[mod]))
    native = get_from_ccd("chem_comp_atom", "A2M")
    assert sum(a.component_id == "A2M" for a in result.atoms) == sum(native["type_symbol"].as_array(str) != "H")
    assert sum(a.element == "P" for a in result.atoms) == 2
    assert len(result.connected_components) == 1
    rebuild(result)


def test_native_ccd_stereospecific_linkage_component():
    # CSQ/CSV are complete C->U RNA phosphorothioate dinucleotides, not
    # disconnected nucleosides or an invented O->S graph patch. CCD atom identity
    # establishes the explicitly authored map below, including actual P/S stereo.
    mapping_first = {"O1": "O5'", "C2": "C5'", "C3": "C4'", "O4": "O4'", "C5": "C3'", "O6": "O3'",
                     "C7": "C2'", "O8": "O2'", "C9": "C1'", "N10": "N1", "C11": "C2", "N12": "N3",
                     "C13": "C4", "C14": "C5", "C15": "C6", "O16": "O2", "N17": "N4"}
    mapping_second = {"P18": "P", "O19": "OP1", "S20": "SP2", "O21": "O5'", "C22": "C5'",
                      "C23": "C4'", "O24": "O4'", "C25": "C3'", "O26": "O3'", "C27": "C2'",
                      "O28": "O2'", "C29": "C1'", "N30": "N1", "C31": "C2", "N32": "N3",
                      "C33": "C4", "C34": "C5", "C35": "C6", "O36": "O2", "O37": "O4"}
    outputs = []
    for ccd, expected_stereo in (("CSQ", "R"), ("CSV", "S")):
        # Native CCD inspection establishes identical atom names/connectivity
        # with opposite phosphorus stereochemistry for these exact components.
        assert get_from_ccd("chem_comp_atom", ccd)["atom_id"].as_array(str).tolist() == get_from_ccd("chem_comp_atom", "CSQ")["atom_id"].as_array(str).tolist()
        mod = dict(id="link", kind="linkage", strand_id="s", copy_id="c1", residue_ids=["r1", "r2"], ccd_id=ccd,
                   atom_map={**{n: dict(residue_id="r1", atom_name=v) for n, v in mapping_first.items()},
                             **{n: dict(residue_id="r2", atom_name=v) for n, v in mapping_second.items()}})
        result = construct_graph(recipe(strand(sequence="CU", polymer="RNA"), replacements=[mod]))
        assert len(result.connected_components) == 1
        assert sum(a.element == "S" for a in result.atoms) == 1
        p = next(a for a in result.atoms if a.element == "P")
        assert p.cip == expected_stereo
        assert any({b.first, b.second} == {"s:c1:r1:O3'", "s:c1:r2:P"} for b in result.bonds)
        rebuild(result)
        outputs.append(result)
    assert len(outputs) == 2
    assert outputs[0].isomeric_smiles != outputs[1].isomeric_smiles
    assert rdMolDescriptors.CalcMolFormula(rebuild(outputs[0])) == rdMolDescriptors.CalcMolFormula(rebuild(outputs[1]))


def test_external_covalent_endpoint_and_nick_reconnection():
    s = strand(sequence="AC")
    s["nicks"] = [dict(after="r1", left_three_prime="hydroxyl", right_five_prime="phosphate_neutral")]
    # Explicitly reconnect the phosphate after removal of its native leaving OH
    # using a full CCD replacement, not an extra valence-violating bond.
    mod = ccd_replacement("dc", "DC", "r2", omit=["OP3"])
    r = recipe(s, replacements=[mod], covalent_links=[dict(first=endpoint("s", "r1", atom="O3'"),
                                                         second=endpoint("s", "r2", atom="P"), order="single")])
    result = construct_graph(r)
    assert len(result.connected_components) == 1
    assert result.isomeric_smiles == construct_graph(recipe(strand(sequence="AC"))).isomeric_smiles
    rebuild(result)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(geometry="invented_helix"),
    lambda d: d.update(graph_provider="arbitrary_python"),
    lambda d: d.update(extra_science=True),
    lambda d: d["strands"][0]["residues"][0].update(base="U"),
    lambda d: d["strands"][0].update(copy_ids=["c1", "c1"]),
    lambda d: d["strands"][0].update(nicks=[dict(after="r4", left_three_prime="hydroxyl", right_five_prime="hydroxyl")]),
])
def test_closed_recipe_no_substitution(mutation):
    d = recipe(strand()).model_dump()
    mutation(d)
    with pytest.raises(ValidationError):
        MolecularConstructionRecipe.model_validate(d)


def test_missing_atom_map_and_external_attachment_fail_without_partial_graph():
    mod = ccd_replacement("m", "5CM", "r2", omit=["OP3"])
    del mod["atom_map"]["C5A"]
    with pytest.raises(ValueError, match="every retained"):
        construct_graph(recipe(strand(), replacements=[mod]))
    mod = ccd_replacement("m", "5CM", "r2", omit=["OP3"])
    mod["atom_map"]["O3'"]["atom_name"] = "wrong_attachment"
    with pytest.raises(ValueError, match="external attachment"):
        construct_graph(recipe(strand(), replacements=[mod]))


@pytest.mark.asyncio
async def test_typed_http_graph_and_explicit_complement_are_stateless():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    # Load this unregistered leaf directly; package __init__ eagerly imports all
    # application routers and is outside this lane's custody/registration scope.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("s05_router", Path(__file__).parents[1] / "routers/molecular_construction.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
        original = recipe(strand()).model_dump(mode="json")
        response = await client.post("/api/molecular-construction/graph", json=original)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["coordinates"] is None
        assert body["recipe"] == original
        rebuild(ConstructionResult.model_validate(body))
        assert (await client.post("/api/molecular-construction/graph", json={**original, "geometry": "B-DNA"})).status_code == 422
        schema = (await client.get("/openapi.json")).json()
        assert schema["components"]["schemas"]["MolecularConstructionRecipe"]["additionalProperties"] is False
        comp = await client.post("/api/molecular-construction/complement", json=dict(
            source=original["strands"][0], new_strand_id="new", new_copy_ids=["c2"],
            new_residue_ids=["a", "b", "c", "d"], five_prime="hydroxyl", three_prime="hydroxyl"))
        assert comp.status_code == 200, comp.text
        assert len(comp.json()["residues"]) == 4
        with patch("services.molecular_construction._rdkit", side_effect=__import__('services.molecular_construction', fromlist=['ConstructionUnavailable']).ConstructionUnavailable("missing")):
            assert (await client.post("/api/molecular-construction/graph", json=original)).status_code == 503
