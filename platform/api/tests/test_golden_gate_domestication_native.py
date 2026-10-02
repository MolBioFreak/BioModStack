"""Native offline DNA Chisel qualification on synthetic, non-user DNA."""
from __future__ import annotations

import builtins

import pytest
from Bio.Seq import Seq
from pydantic import ValidationError

from services.assembly.golden_gate_domestication import (
    CDSConstraint, DomesticationResult, DomesticationSettings, EditRegion,
    UnwantedSite, apply_domestication_proposal, domestication_settings_schema,
    propose_domestication,
)


def settings(sequence, *, cds=(), **overrides):
    return DomesticationSettings(enabled=True,
        editable_regions=(EditRegion(start=0, end=len(sequence)),),
        cds=cds, unwanted_sites=(UnwantedSite(enzyme_id="BsaI", recognition_sequence="GGTCTC"),),
        **overrides)


def cds(sequence, **kwargs):
    return CDSConstraint(feature_id="gene", region=EditRegion(start=0, end=len(sequence)), **kwargs)


def no_site(sequence, motif="GGTCTC", circular=False):
    expanded = sequence + sequence[:len(motif)-1] if circular else sequence
    return motif not in expanded and str(Seq(motif).reverse_complement()) not in expanded


def test_disabled_never_loads_solver_or_changes_even_ambiguous_source(monkeypatch):
    original_import = builtins.__import__
    def reject(name, *args, **kwargs):
        assert not name.startswith("dnachisel")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", reject)
    result = propose_domestication("n? unchanged")
    assert result.status == "disabled"
    assert result.proposed_sequence is None
    assert apply_domestication_proposal("n? unchanged", result) == "n? unchanged"


def test_native_minimal_synonymous_proposal_and_explicit_acceptance():
    source = "ATGGGTCTCTAA"
    request = settings(source, cds=(cds(source, genetic_code=11),))
    result = propose_domestication(source, settings=request)
    assert result.proposed_sequence == "ATGGGACTCTAA"
    assert result.engine.startswith("DNA Chisel 3.2.16")
    assert result.status == "proposal" and result.minimal_edits_proven
    assert [(e.position, e.original, e.proposed, e.affected_feature_ids) for e in result.edits] == [(5, "T", "A", ("gene",))]
    assert result.translations[0].original == result.translations[0].proposed == "MGL*"
    assert source == "ATGGGTCTCTAA"
    assert apply_domestication_proposal(source, result) == source
    assert apply_domestication_proposal(source, result, accepted=True) == "ATGGGACTCTAA"
    assert DomesticationResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValueError, match="different source"):
        apply_domestication_proposal("ATAGGTCTCTAA", result, accepted=True)


def test_no_edit_outside_editable_and_protected_regions():
    source = "ATGGGTCTCTAA"
    request = settings(source, cds=(cds(source),), protected_regions=(EditRegion(start=0, end=8),))
    result = propose_domestication(source, settings=request)
    assert result.proposed_sequence == "ATGGGTCTATAA"
    assert [e.position for e in result.edits] == [8]
    immutable = request.model_copy(update={"editable_regions": ()})
    failed = propose_domestication(source, settings=immutable)
    assert failed.status == "no_proposal_found" and failed.search_complete
    assert failed.proposed_sequence is None


@pytest.mark.parametrize("strand", [1, -1])
@pytest.mark.parametrize("rotation", [0, 5, 7])
def test_reverse_and_circular_origin_spanning_cds_and_sites(strand, rotation):
    original = "ATGGGTCTCTAA"
    forward = original if strand == 1 else str(Seq(original).reverse_complement())
    source = forward[rotation:] + forward[:rotation]
    start = len(source) - rotation if rotation else 0
    region = EditRegion(start=start, end=start, wraps_origin=True) if rotation else EditRegion(start=0, end=len(source))
    constraint = CDSConstraint(feature_id="wrapped", region=region, strand=strand, genetic_code=11)
    result = propose_domestication(source, circular=True, settings=settings(source, cds=(constraint,)))
    assert result.status == "proposal"
    assert len(result.edits) == 1
    assert no_site(result.proposed_sequence, circular=True)
    restored = result.proposed_sequence[start:] + result.proposed_sequence[:start]
    if strand == -1:
        restored = str(Seq(restored).reverse_complement())
    assert str(Seq(restored).translate(table=11, cds=True)) == "MGL"
    assert restored[:3] == "ATG" and restored[-3:] == "TAA"
    assert result.translations[0].proposed == "MGL*"


def test_shifted_overlapping_cds_success_and_antiparallel_conflict():
    source = "GGTCTC"
    first = cds(source, initiation="ordinary")
    shifted = CDSConstraint(feature_id="overlap", region=EditRegion(start=1, end=4), initiation="ordinary")
    result = propose_domestication(source, settings=settings(source, cds=(first, shifted)))
    assert result.proposed_sequence == "GGTCTA"
    assert [t.proposed for t in result.translations] == ["GL", "V"]
    reverse = CDSConstraint(feature_id="antisense", region=EditRegion(start=0, end=6), strand=-1, initiation="ordinary")
    failed = propose_domestication(source, settings=settings(source, cds=(first, reverse), max_edits=1))
    assert failed.status == "no_proposal_found" and failed.search_complete
    assert failed.candidates_evaluated == 18
    assert [t.original for t in failed.translations] == ["GL", "ET"]


@pytest.mark.parametrize("strand", [1, -1])
def test_partial_frame_boundary_bases_preserved(strand):
    biological = "AGGTCTCT"
    source = biological if strand == 1 else str(Seq(biological).reverse_complement())
    result = propose_domestication(source, settings=settings(source, cds=(cds(source, frame=1, strand=strand, initiation="ordinary"),)))
    proposed = result.proposed_sequence if strand == 1 else str(Seq(result.proposed_sequence).reverse_complement())
    assert proposed[0] == "A" and proposed[-1] == "T"
    assert str(Seq(proposed[1:7]).translate()) == "GL"
    assert no_site(proposed)


def test_subcodon_partial_feature_is_preserved_without_inventing_translation():
    source = "GGTCTC"
    partial = CDSConstraint(feature_id="partial", region=EditRegion(start=0, end=2), initiation="ordinary")
    result = propose_domestication(source, settings=settings(source, cds=(partial,)))
    assert result.proposed_sequence == "GGACTC"
    assert result.translations[0].original == result.translations[0].proposed == ""
    assert result.edits[0].position == 2


def test_alternative_initiation_keep_is_not_ordinary_valine_equality():
    source = "GTGAAATAA"
    site = (UnwantedSite(enzyme_id="synthetic-selected", recognition_sequence="GTG"),)
    common = dict(enabled=True, editable_regions=(EditRegion(start=0, end=3),), unwanted_sites=site, max_edits=1)
    kept = propose_domestication(source, settings=DomesticationSettings(**common, cds=(cds(source, genetic_code=11),)))
    assert kept.status == "no_proposal_found"
    allowed = propose_domestication(source, settings=DomesticationSettings(**common,
        cds=(cds(source, genetic_code=11, initiation="allowed", allowed_start_codons=("GTG", "ATG")),)))
    assert allowed.proposed_sequence == "ATGAAATAA"
    assert allowed.translations[0].original == allowed.translations[0].proposed == "MK*"
    assert allowed.translations[0].original_start_codon == "GTG"
    only_atg = propose_domestication(source, settings=DomesticationSettings(**common,
        cds=(cds(source, genetic_code=11, initiation="allowed", allowed_start_codons=("ATG",)),)))
    assert only_atg.proposed_sequence == "ATGAAATAA"
    ordinary = propose_domestication(source, settings=DomesticationSettings(**common,
        cds=(cds(source, genetic_code=11, initiation="ordinary"),)))
    assert ordinary.proposed_sequence == "GTAAAATAA"
    assert ordinary.translations[0].proposed == "VK*"


def test_alternative_genetic_code_and_exact_stop_policy():
    source = "ATGTGATAA"
    common = dict(enabled=True, editable_regions=(EditRegion(start=3, end=6),),
        unwanted_sites=(UnwantedSite(enzyme_id="synthetic", recognition_sequence="TGA"),), max_edits=1)
    mito = propose_domestication(source, settings=DomesticationSettings(**common, cds=(cds(source, genetic_code=2),)))
    assert mito.proposed_sequence == "ATGTGGTAA"
    assert mito.translations[0].proposed == "MW*"
    standard = propose_domestication(source, settings=DomesticationSettings(**common, cds=(cds(source),)))
    assert standard.status == "no_proposal_found"
    synonym_stop = propose_domestication(source, settings=DomesticationSettings(**common, cds=(cds(source, stop_policy="synonymous"),)))
    assert synonym_stop.proposed_sequence == "ATGTAATAA"
    assert synonym_stop.translations[0].original == synonym_stop.translations[0].proposed == "M**"


def test_only_selected_occurrence_removed_no_new_sites_and_both_strands():
    source = "GGTCTCAAAAGAGACC"
    request = DomesticationSettings(enabled=True, editable_regions=(EditRegion(start=10, end=16),),
        unwanted_sites=(UnwantedSite(enzyme_id="BsaI", recognition_sequence="GGTCTC", starts=(10,)),))
    result = propose_domestication(source, settings=request)
    assert result.status == "proposal"
    assert result.proposed_sequence[:10] == source[:10]
    assert no_site(result.proposed_sequence[10:])
    assert result.proposed_sequence.count("GGTCTC") == 1


def test_budget_exhaustion_is_not_global_infeasibility():
    source = "ATGGGTCTCTAA"
    request = settings(source, cds=(cds(source),), candidate_budget=1)
    result = propose_domestication(source, settings=request)
    assert result.status == "no_proposal_found"
    assert not result.search_complete and not result.minimal_edits_proven
    assert result.candidates_evaluated == 1
    assert "budget" in result.diagnostics[0].lower()
    assert apply_domestication_proposal(source, result) == source


def test_two_changes_proven_minimal_against_independent_exhaustive_oracle():
    source = "GGTCTCAAAGGTCTC"
    request = settings(source, max_edits=2)
    result = propose_domestication(source, settings=request)
    assert result.status == "proposal" and len(result.edits) == 2
    for i in range(len(source)):
        for b in "ACGT":
            if b != source[i]:
                assert not no_site(source[:i] + b + source[i+1:])
    assert no_site(result.proposed_sequence)
    assert result.minimal_edits_proven


def test_empty_constraints_unchanged_iupac_motif_and_invalid_source():
    result = propose_domestication("ATG", settings=DomesticationSettings(enabled=True))
    assert result.status == "unchanged" and result.proposed_sequence == "ATG"
    result = propose_domestication("GGTCTC", settings=DomesticationSettings(enabled=True,
        editable_regions=(EditRegion(start=0, end=6),), unwanted_sites=(UnwantedSite(enzyme_id="synthetic", recognition_sequence="GGTCNC"),)))
    assert result.proposed_sequence == "AGTCTC"
    with pytest.raises(ValueError, match="uppercase"):
        propose_domestication("acgn", settings=DomesticationSettings(enabled=True))


def test_closed_native_setting_schema_and_cross_field_validation():
    schema = domestication_settings_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["enabled"]["default"] is False
    assert schema["properties"]["candidate_budget"]["minimum"] == 1
    assert schema["$defs"]["CDSConstraint"]["properties"]["initiation"]["enum"] == ["ordinary", "preserve", "allowed"]
    with pytest.raises(ValidationError):
        DomesticationSettings(hidden_codon_optimization=True)
    with pytest.raises(ValidationError):
        cds("ATGGGT", frame=1)
    with pytest.raises(ValueError):
        EditRegion(start=5, end=1, wraps_origin=True).positions(9, False)
    with pytest.raises(ValueError, match="Selected site start"):
        propose_domestication("GGTCTC", settings=DomesticationSettings(enabled=True,
            unwanted_sites=(UnwantedSite(enzyme_id="BsaI", recognition_sequence="GGTCTC", starts=(2,)),)))
