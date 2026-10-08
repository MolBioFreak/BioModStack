import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("extract_target_templates.py")
SPEC = importlib.util.spec_from_file_location("extract_target_templates_module", MODULE_PATH)
extract_target_templates = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(extract_target_templates)


def test_extract_templates_falls_back_without_biopython(monkeypatch, tmp_path: Path) -> None:
    pdb_path = Path(__file__).resolve().parent.parent / "rcsb" / "2lgv.pdb"

    monkeypatch.setattr(extract_target_templates, "PDBParser", None)
    monkeypatch.setattr(extract_target_templates, "MMCIFIO", None)
    monkeypatch.setattr(extract_target_templates, "Select", object)

    manifest = extract_target_templates.extract_templates(
        [pdb_path],
        ["A"],
        tmp_path / "mmcif",
        model_number=1,
    )

    template_info = manifest["2lgv"]
    cif_path = Path(template_info["cif"])

    assert template_info["chains"] == ["A"]
    assert template_info["model_number"] == 1
    assert template_info["writer"] == "simple"
    assert cif_path.exists()

    cif_text = cif_path.read_text(encoding="utf-8")
    assert "_atom_site.group_PDB" in cif_text
    assert "_entity_poly.entity_id" in cif_text
    assert " A " in cif_text
