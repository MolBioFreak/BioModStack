from __future__ import annotations

import csv
import copy
import hashlib
import importlib
from io import StringIO
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"


def _analysis():
    path = REPO_ROOT / "platform/api/services/frustrampnn/analysis.py"
    assert path.is_file(), "neutral FrustraMPNN analysis core is missing"
    return importlib.import_module("services.frustrampnn.analysis")


def _map() -> dict:
    sequence = "GA"
    rows = []
    for position, (name, wt, auth) in enumerate((("GLY", "G", 10), ("ALA", "A", 12))):
        rows.append({
            "entity_instance_id": "protein-1", "source_entity_id": "1", "label_asym_id": "AA",
            "auth_asym_id": "X", "label_seq_id": position + 1, "auth_seq_id": auth,
            "insertion_code": "A" if position == 0 else "", "sequence_index": position + 1,
            "pdb_chain_id": "A", "pdb_residue_id": auth, "pdb_insertion_code": "A" if position == 0 else "",
            "model_position": position, "residue_name": name, "wt": wt, "selected_model": 1,
            "selected_altloc": "", "backbone_complete": True,
            "backbone_atoms": {"N": f"{position}-N", "CA": f"{position}-CA", "C": f"{position}-C", "O": f"{position}-O"},
            "status": "mapped", "reason": None,
        })
    return {
        "schema_name": "frustrampnn_structure_map", "schema_version": 1,
        "target_id": "target-1", "parent_job_id": "job-1", "candidate_id": "candidate-1",
        "source_format": "mmcif", "source_sha256": "1" * 64, "source_bytes": 100,
        "identity_authority": "mmcif_atom_site_v1", "identity_domain": "source_authoritative",
        "authority_artifact_sha256": "1" * 64, "normalized_pdb_sha256": "2" * 64,
        "selected_source_model": 1, "altloc_policy": "blank_or_explicit:A",
        "normalizer_version": "frustrampnn_structure_normalizer_v1", "model_ready_sequence": sequence,
        "model_ready_sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "excluded_records": [], "rows": rows,
    }


def _raw(rows: list[dict[str, object]] | None = None) -> str:
    if rows is None:
        rows = []
        for position, wt in ((0, "G"), (1, "A")):
            for index, mutation in enumerate(AA_ORDER):
                score = -1.0 if index == 0 else 0.58 if index == 1 else 0.0
                rows.append({"frustration_pred": score, "position": position, "wildtype": wt,
                             "mutation": mutation, "chain": "A", "pdb": "fixture.normalized"})
    handle = StringIO(newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"],
        lineterminator="\n",
    )
    writer.writeheader(); writer.writerows(rows)
    return handle.getvalue()


def _rows(raw: str) -> list[dict[str, str]]:
    return list(csv.DictReader(raw.splitlines()))


def test_complete_n_by_20_landscape_uses_exact_order_identity_and_threshold_boundaries(tmp_path: Path) -> None:
    module = _analysis()
    raw = tmp_path / "raw.csv"; raw.write_text(_raw(), encoding="utf-8")
    structure_map = _map()
    landscape = module.finalize_landscape(
        raw, structure_map,
        expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
        expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
    )
    assert len(landscape["residues"]) == 2
    assert all([slot["mutation_aa"] for slot in residue["slots"]] == list(AA_ORDER) for residue in landscape["residues"])
    assert sum(len(residue["slots"]) for residue in landscape["residues"]) == 40
    assert [landscape["residues"][0]["slots"][i]["class"] for i in range(3)] == ["high", "minimal", "neutral"]
    assert landscape["threshold_policy"] == {"id": "frustrampnn_threshold_v1", "high_max": -1.0, "minimal_min": 0.58}
    assert landscape["threshold_policy_sha256"] == module.canonical_sha256(landscape["threshold_policy"])
    assert landscape["residues"][0]["auth_seq_id"] == 10
    assert landscape["residues"][0]["model_position"] == 0


def test_policy_neutral_summary_separates_native_and_complete_support_without_rank_or_pass(tmp_path: Path) -> None:
    module = _analysis()
    raw = tmp_path / "raw.csv"; raw.write_text(_raw(), encoding="utf-8")
    structure_map = _map()
    landscape = module.finalize_landscape(raw, structure_map, expected_normalized_pdb_sha256="2" * 64, expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"])
    summary = module.summarize_landscape(landscape, structure_map)
    assert summary["residue_support"] == {"expected": 2, "mapped": 2, "scoreable": 2, "excluded": 0, "ambiguous": 0}
    assert summary["slot_support"] == {"expected": 40, "observed": 40, "scoreable": 40}
    assert sum(summary["native_slot_counts"].values()) == 2
    assert sum(summary["complete_landscape_counts"].values()) == 40
    assert summary["missingness_by_reason"] == {}
    assert summary["support_by_entity_chain"][0]["scoreable_slots"] == 40
    assert not {"rank", "candidate_rank", "pass", "decision"}.intersection(summary)


def test_summary_counts_explicit_structure_missingness_once_and_keeps_mapped_n_by_20_complete(
    tmp_path: Path,
) -> None:
    module = _analysis()
    structure_map = _map()
    missing_backbone = copy.deepcopy(structure_map["rows"][1])
    missing_backbone.update({
        "label_seq_id": 3,
        "auth_seq_id": 13,
        "pdb_residue_id": 13,
        "sequence_index": 3,
        "model_position": 2,
        "residue_name": "SER",
        "wt": "S",
        "backbone_complete": False,
        "backbone_atoms": {"N": "2-N", "CA": "2-CA", "C": "2-C", "O": None},
        "status": "missing_backbone",
        "reason": "missing required backbone atoms: O",
    })
    nonstandard = copy.deepcopy(missing_backbone)
    nonstandard.update({
        "label_seq_id": 4,
        "auth_seq_id": 14,
        "pdb_residue_id": 14,
        "sequence_index": 4,
        "residue_name": "MSE",
        "wt": None,
        "backbone_complete": False,
        "backbone_atoms": {"N": "3-N", "CA": "3-CA", "C": "3-C", "O": "3-O"},
        "status": "nonstandard_residue",
        "reason": "nonstandard protein residue: MSE",
    })
    structure_map["rows"].extend([missing_backbone, nonstandard])
    structure_map["excluded_records"] = [
        {
            "source_identity": "X:13:SER",
            "reason_code": "missing_backbone",
            "reason": "missing required backbone atoms: O",
        },
        # The row and its exclusion are two representations of one missing residue.
        {
            "source_identity": "X:13:SER",
            "reason_code": "missing_backbone",
            "reason": "missing required backbone atoms: O",
        },
        {
            "source_identity": "X:14:MSE",
            "reason_code": "nonstandard_residue",
            "reason": "nonstandard protein residue: MSE",
        },
        {
            "source_identity": "Z:1:LIG",
            "reason_code": "non_protein_entity",
            "reason": "non-protein coordinate record is excluded from model input",
        },
    ]
    raw = tmp_path / "raw.csv"
    raw.write_text(_raw(), encoding="utf-8")
    landscape = module.finalize_landscape(
        raw,
        structure_map,
        expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
        expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
    )
    assert len(landscape["residues"]) == 2
    assert all(len(residue["slots"]) == 20 for residue in landscape["residues"])

    summary = module.summarize_landscape(landscape, structure_map)
    assert summary["residue_support"] == {
        "expected": 4,
        "mapped": 2,
        "scoreable": 2,
        "excluded": 2,
        "ambiguous": 0,
    }
    assert summary["slot_support"] == {"expected": 40, "observed": 40, "scoreable": 40}
    assert summary["missingness_by_reason"] == {
        "missing_backbone": 1,
        "non_protein_entity": 1,
        "nonstandard_residue": 1,
    }


@pytest.mark.parametrize("mutation", ["duplicate", "nonfinite", "malformed", "missing", "wt_disagreement"])
def test_raw_output_fails_closed_instead_of_publishing_partial_or_fabricated_slots(tmp_path: Path, mutation: str) -> None:
    module = _analysis()
    rows = _rows(_raw())
    if mutation == "duplicate": rows.append(dict(rows[0]))
    elif mutation == "nonfinite": rows[0]["frustration_pred"] = "NaN"
    elif mutation == "malformed": rows[0]["position"] = "not-an-integer"
    elif mutation == "missing": rows.pop()
    else: rows[0]["wildtype"] = "V"
    raw = tmp_path / "raw.csv"; raw.write_text(_raw(rows), encoding="utf-8")
    structure_map = _map()
    with pytest.raises(module.LandscapeValidationError):
        module.finalize_landscape(raw, structure_map, expected_normalized_pdb_sha256="2" * 64, expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"])


def test_landscape_rejects_duplicate_positions_and_stale_structure_or_sequence(tmp_path: Path) -> None:
    module = _analysis()
    raw = tmp_path / "raw.csv"; raw.write_text(_raw(), encoding="utf-8")
    duplicate = _map(); duplicate["rows"][1]["model_position"] = 0
    with pytest.raises(module.LandscapeValidationError, match="duplicate.*position"):
        module.finalize_landscape(raw, duplicate, expected_normalized_pdb_sha256="2" * 64, expected_model_ready_sequence_sha256=duplicate["model_ready_sequence_sha256"])
    current = _map()
    with pytest.raises(module.LandscapeValidationError, match="normalized PDB"):
        module.finalize_landscape(raw, current, expected_normalized_pdb_sha256="3" * 64, expected_model_ready_sequence_sha256=current["model_ready_sequence_sha256"])
    with pytest.raises(module.LandscapeValidationError, match="sequence"):
        module.finalize_landscape(raw, current, expected_normalized_pdb_sha256="2" * 64, expected_model_ready_sequence_sha256="4" * 64)


def test_actual_audited_vendor_header_and_order_are_strictly_accepted_without_filename_identity(
    tmp_path: Path,
) -> None:
    module = _analysis()
    raw = tmp_path / "deliberately-unrelated-name.csv"
    raw.write_text(_raw(), encoding="utf-8")
    structure_map = _map()
    landscape = module.finalize_landscape(
        raw, structure_map,
        expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
        expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
    )
    assert len(landscape["residues"]) == 2
    assert landscape["raw_csv_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()

    alias_header = _raw().replace(
        "frustration_pred,position,wildtype,mutation,chain,pdb",
        "chain,position,wt,mutation,score,pdb",
        1,
    )
    raw.write_text(alias_header, encoding="utf-8")
    with pytest.raises(module.LandscapeValidationError, match="header"):
        module.finalize_landscape(
            raw, structure_map,
            expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
        )


def test_raw_csv_read_is_no_follow_and_success_requires_exact_finite_n_by_20(tmp_path: Path) -> None:
    module = _analysis()
    target = tmp_path / "target.csv"
    target.write_text(_raw(), encoding="utf-8")
    link = tmp_path / "raw.csv"
    link.symlink_to(target)
    structure_map = _map()
    with pytest.raises(module.LandscapeValidationError, match="symlink|regular|follow"):
        module.finalize_landscape(
            link, structure_map,
            expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
        )

    real = tmp_path / "real"; real.mkdir()
    nested = real / "raw.csv"; nested.write_text(_raw(), encoding="utf-8")
    parent_link = tmp_path / "parent-link"; parent_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(module.LandscapeValidationError, match="symlink|follow|path"):
        module.finalize_landscape(
            parent_link / "raw.csv", structure_map,
            expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
        )


def test_vendor_pdb_column_is_required_nonempty_metadata_not_filename_identity(tmp_path: Path) -> None:
    module = _analysis()
    rows = _rows(_raw())
    rows[0]["pdb"] = ""
    raw = tmp_path / "anything.csv"; raw.write_text(_raw(rows), encoding="utf-8")
    structure_map = _map()
    with pytest.raises(module.LandscapeValidationError, match="pdb|metadata|empty"):
        module.finalize_landscape(
            raw, structure_map,
            expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
        )


def test_raw_csv_rejects_lexical_parent_escape_before_normalization(tmp_path: Path) -> None:
    module = _analysis(); structure_map = _map()
    lexical = tmp_path / "lexical"; lexical.mkdir()
    (lexical / "raw.csv").write_text(_raw(), encoding="utf-8")
    actual = tmp_path / "actual"; actual.mkdir()
    nested = actual / "nested"; nested.mkdir()
    (lexical / "link").symlink_to(nested, target_is_directory=True)
    with pytest.raises(module.LandscapeValidationError, match=r"lexical|unsafe|component|\.\."):
        module.finalize_landscape(
            lexical / "link" / ".." / "raw.csv", structure_map,
            expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
        )


def test_raw_csv_rejects_extra_or_trailing_fields_and_retained_1ubq_has_exact_width() -> None:
    module = _analysis()
    header = "frustration_pred,position,wildtype,mutation,chain,pdb\n"
    for hostile in (
        header + "0,0,G,A,A,fixture,extra\n",
        header + "0,0,G,A,A,fixture,\n",
    ):
        with pytest.raises(module.LandscapeValidationError, match="field|width|column|row"):
            module._raw_rows(hostile.encode("utf-8"))

    fixture = (
        Path(__file__).parent / "fixtures" / "conformational_mapping" /
        "real_1ubq" / "frustrampnn.csv"
    )
    payload = fixture.read_bytes()
    rows = module._raw_rows(payload)
    assert hashlib.sha256(payload).hexdigest() == (
        "2084353640cbe5f06847bc78c0787f1062edb2c891d3808adfe2d6aa57b0fa36"
    )
    assert len(rows) == 76 * 20
    assert all(set(row) == {
        "frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"
    } for row in rows)
