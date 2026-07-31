from __future__ import annotations

from scripts.bms_md.chemistry.audit_drt4_sources import _audit_9vdo, _audit_9vdp, _audit_9vdv


def _atoms(rows: list[tuple[str, str, str, str, str, float, float, float]]) -> dict:
    keys = (
        "_atom_site.group_PDB", "_atom_site.auth_asym_id", "_atom_site.label_asym_id",
        "_atom_site.auth_comp_id", "_atom_site.auth_seq_id", "_atom_site.label_atom_id",
        "_atom_site.Cartn_x", "_atom_site.Cartn_y", "_atom_site.Cartn_z",
    )
    return {key: [str(row[index]) for row in rows] for index, key in enumerate(keys)}


def test_drt4_source_audit_requires_two_manganese_per_dctp_site() -> None:
    data = _atoms([
        ("HETATM", "A", "DCP1", "DCP", "601", "P", 0, 0, 0),
        ("HETATM", "A", "MN1", "MN", "602", "MN", 1, 0, 0),
        ("HETATM", "A", "MN2", "MN", "603", "MN", 0, 1, 0),
    ])
    data["_pdbx_entity_nonpoly.comp_id"] = ["DCP", "MN"]
    data["_struct_conn.conn_type_id"] = ["metalc", "metalc"]
    audit = _audit_9vdo(data)
    assert audit["dctp_site_count"] == 1
    assert audit["manganese_site_count"] == 2
    assert audit["required_two_manganese_per_active_site_observed"] is True


def test_drt4_source_audit_rejects_tyr_phosphate_link_without_deposited_bond() -> None:
    data = _atoms([
        ("ATOM", "A", "A", "TYR", "125", "OH", 0, 0, 0),
        ("ATOM", "D", "D", "DC", "5", "P", 1.5, 0, 0),
    ])
    audit = _audit_9vdp(data, {"rcsb_entry_info": {"inter_mol_covalent_bond_count": 0}})
    assert audit["minimum_tyr125_oh_to_any_deposited_dna_p_angstrom"] == 1.5
    assert audit["deposited_struct_conn_present"] is False
    assert audit["required_tyr125_oh_to_dna_p_link_observed"] is False


def test_drt4_source_audit_requires_both_catalytic_mutations_per_chain() -> None:
    data = _atoms([
        ("ATOM", "A", "A", "ALA", "240", "CA", 0, 0, 0),
        ("ATOM", "A", "A", "ALA", "241", "CA", 1, 0, 0),
        ("ATOM", "B", "B", "ALA", "240", "CA", 0, 1, 0),
        ("ATOM", "B", "B", "ALA", "241", "CA", 1, 1, 0),
    ])
    audit = _audit_9vdv(data)
    assert audit["chains_with_both_positions"] == 2
    assert audit["d240a_d241a_observed_in_every_complete_chain"] is True
