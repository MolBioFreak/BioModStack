from datetime import datetime, timedelta, timezone

from routers.nucleotide_sequences import (
    _sortable_timestamp,
    clean_sequence,
    molecule_label_for,
    normalize_molecule_orientation,
    normalize_molecule_strandedness,
    normalize_sequence_type,
)


def test_explicit_rna_payload_canonicalizes_thymine_without_shortening() -> None:
    assert normalize_sequence_type("rna", "ATG TAA") == "rna"
    assert clean_sequence("ATG TAA", "rna") == "AUGUAA"


def test_explicit_dna_payload_canonicalizes_uracil_without_shortening() -> None:
    assert normalize_sequence_type("dna", "AUG UAA") == "dna"
    assert clean_sequence("AUG UAA", "dna") == "ATGTAA"


def test_sequence_type_inference_still_detects_u_only_rna() -> None:
    assert normalize_sequence_type(None, "AUGUAA") == "rna"
    assert normalize_sequence_type(None, "ATGTAA") == "dna"


def test_sequence_type_accepts_molecule_labels() -> None:
    assert normalize_sequence_type("ssRNA", "AGTAGT") == "rna"
    assert normalize_sequence_type("double-stranded RNA", "ATGTAA") == "rna"
    assert normalize_sequence_type("ssDNA", "AUGUAA") == "dna"
    assert normalize_sequence_type("dsDNA", "AUGUAA") == "dna"


def test_backend_persists_ds_ss_dna_rna_strandedness_and_orientation() -> None:
    cases = [
        ("dsDNA", "dna", None, "double", "not_applicable", "dsDNA"),
        ("single strand DNA", "dna", "positive strand", "single", "positive", "(+)ssDNA"),
        ("dsRNA", "rna", None, "double", "not_applicable", "dsRNA"),
        ("(-)ssRNA linear genome", "rna", "minus strand", "single", "negative", "(-)ssRNA"),
    ]

    for strandedness_input, sequence_type, orientation_input, strandedness, orientation, label in cases:
        normalized_strandedness = normalize_molecule_strandedness(strandedness_input, sequence_type)
        normalized_orientation = normalize_molecule_orientation(orientation_input, normalized_strandedness)
        assert normalized_strandedness == strandedness
        assert normalized_orientation == orientation
        assert molecule_label_for(sequence_type, normalized_strandedness, normalized_orientation) == label


def test_sortable_timestamp_normalizes_none_naive_and_offset_aware_values() -> None:
    naive = datetime(2026, 7, 12, 8, 30)
    eastern = datetime(2026, 7, 12, 4, 30, tzinfo=timezone(timedelta(hours=-4)))

    assert _sortable_timestamp(None) == datetime.min.replace(tzinfo=timezone.utc)
    assert _sortable_timestamp(naive) == datetime(2026, 7, 12, 8, 30, tzinfo=timezone.utc)
    assert _sortable_timestamp(eastern) == datetime(2026, 7, 12, 8, 30, tzinfo=timezone.utc)
