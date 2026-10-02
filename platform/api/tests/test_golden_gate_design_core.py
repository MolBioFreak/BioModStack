"""Independent literal top-strand / cleavage-coordinate raw design fixtures."""
import json
from pathlib import Path

import pytest
from Bio.Seq import Seq
from pydantic import ValidationError

from services.assembly.golden_gate_design import design_golden_gate, design_material
from services.assembly.golden_gate_design_types import GoldenGateDesignRequest, Material
from services.restriction_catalog import catalog_authority
from services.molbio_ops import pcr_product
from routers.molbio_ops import calculate_primer_tm_result

A = "ACGTACCGTATGACCTGATCGTAGCTACGATCGTACCTAGGTCAGTACGATCGTACAGTC"
B = "TGACCGATGCTAGCATCGATGGTACCTAGCTGACTACGATGCGTATCGAGCTAGTTCGAC"
# Independently specified recognition/spacer/width, not read from implementation.
ENZYMES = [("BsaI", "GGTCTC", "A", 4), ("BsmBI", "CGTCTC", "A", 4),
           ("Esp3I", "CGTCTC", "A", 4), ("BbsI", "GAAGAC", "AC", 4),
           ("SapI", "GCTCTTC", "A", 3)]


def rc(s):
    return str(Seq(s).reverse_complement())


def binding(enzyme):
    cat = catalog_authority.require()
    return dict(enzyme_id=enzyme, catalog_id=cat.catalog_id, catalog_sha256=cat.content_sha256)


def request(enzyme="BsaI", kind="pcr", reverse=False, origin=0):
    _, site, spacer, width = next(e for e in ENZYMES if e[0] == enzyme)
    left, middle = ("AATG", "GGAG") if width == 4 else ("ATG", "GAG")
    donor = "TT" + site + spacer + left + A + middle + rc(spacer) + rc(site) + "AA"
    prep = dict(kind=kind, region=dict(start=0, end=len(B)),
                left=dict(clamp="TT", spacer=spacer, fusion=middle),
                right=dict(clamp="AA", spacer=spacer, fusion=left), removed_fragment_indices=[])
    if kind == "pcr":
        prep.update(forward_anneal_length=20, reverse_anneal_length=21)
    return GoldenGateDesignRequest.model_validate(dict(
        sources=[dict(id="donor", source=dict(kind="inline", sequence=donor, topology="circular")),
                 dict(id="insert", source=dict(kind="inline", sequence=rc(B) if reverse else B,
                    topology="linear", features=[dict(id="cds", type="CDS", name="insert CDS",
                    segments=[dict(start=0, end=len(B))], strand=-1 if reverse else 1, codon_start=2,
                    qualifiers={"transl_table": ["11"]})]))],
        parts=[dict(id="backbone", name="vector", role="backbone", source_id="donor",
                    preparation=dict(kind="donor", retained_fragment_index=0, removed_fragment_indices=[])),
               dict(id="insert", name="insert", source_id="insert", orientation="reverse" if reverse else "forward", preparation=prep)],
        enzyme=binding(enzyme), target=dict(topology="circular", display_origin=origin,
                                          exact_sequence=(left + A + middle + B)[origin:] + (left + A + middle + B)[:origin])))


@pytest.mark.parametrize("enzyme,site,spacer,width", ENZYMES)
@pytest.mark.parametrize("kind", ["pcr", "synthesis"])
@pytest.mark.parametrize("reverse", [False, True])
def test_raw_roundtrip_independent_expected(enzyme, site, spacer, width, kind, reverse):
    req = request(enzyme, kind, reverse)
    result = design_golden_gate(req)
    product = result.solutions[0]
    left, middle = ("AATG", "GGAG") if width == 4 else ("ATG", "GAG")
    assert product.sequence == left + A + middle + B
    assert product.exact_target_match is True
    assert not product.occurrences
    assert result.geometry.spacer_length == len(spacer)
    assert result.geometry.overhang_length == width
    assert result.selected_solution_id == product.id
    retained = design_material(result, result.preparations[1].retained_material_id)
    assert retained.sequence == middle + B
    assert retained.parent_id == "insert:prepared"
    assert retained.id not in {m.id for m in result.materials}  # no duplicate digest DNA
    donor, insert = result.digests
    assert donor.retained_fragment_index == 0
    assert donor.background_fragment_indices == [1]  # not implicitly purified
    assert insert.retained_fragment_index == 1
    assert insert.background_fragment_indices == [0, 2]
    selected = insert.fragments[1]
    assert selected.top_strand_sequence == middle + B
    assert selected.left_end.overhang_sequence_5to3 == middle
    assert selected.left_end.protruding_strand == "top"
    assert selected.right_end.overhang_sequence_5to3 == rc(left)
    assert selected.right_end.protruding_strand == "bottom"
    cut = 2 + len(site) + len(spacer)
    assert selected.top_start_boundary == cut
    assert selected.bottom_start_boundary == cut + width
    assert selected.top_end_boundary == cut + width + len(B)
    assert selected.bottom_end_boundary == cut + 2 * width + len(B)
    cds = next(f for f in product.features if f.type == "CDS")
    assert cds.segments[0].start == 2 * width + len(A)
    assert cds.segments[0].end == len(product.sequence)
    assert cds.strand == 1 and cds.codon_start == 2 and cds.frame_preserved
    if kind == "pcr":
        forward, reverse_primer = result.primers
        assert forward.full_sequence == "TT" + site + spacer + middle + B[:20]
        assert reverse_primer.full_sequence == "AA" + site + spacer + rc(left) + rc(B[-21:])
        assert result.preparations[1].pcr_verification == "verified"
        reconstructed = pcr_product(B, forward.full_sequence, reverse_primer.full_sequence)
        intermediate = next(m for m in result.materials if m.id == "insert:prepared")
        assert reconstructed.sequence == intermediate.sequence
        assert forward.tm == calculate_primer_tm_result(B[:20], "dna", req.primer_settings)
        assert forward.qc.length == len(forward.full_sequence)
        assert len([f for f in product.features if f.type == "primer_bind"]) == 2
        assert all(m.parent_id == "source:insert" for m in intermediate.mappings)
        assert all(m.output_start >= cut + width for m in intermediate.mappings)


@pytest.mark.parametrize("origin", [1, 9, 77])
def test_display_origin_and_feature_mapping(origin):
    result = design_golden_gate(request(origin=origin))
    expected = "AATG" + A + "GGAG" + B
    product = result.solutions[0]
    assert product.sequence == expected[origin:] + expected[:origin]
    cds = next(f for f in product.features if f.type == "CDS")
    assert "".join(product.sequence[s.start:s.end] for s in cds.segments) == B
    for mapping in product.mappings:
        parent = next(m for m in result.materials if m.id == mapping.parent_id)
        span = parent.sequence[mapping.parent_start:mapping.parent_end]
        assert product.sequence[mapping.output_start:mapping.output_end] == (span if mapping.strand == 1 else rc(span))


def test_explicit_removal_and_independent_linear_topology():
    data = request().model_dump()
    data["target"] = dict(topology="linear")
    data["parts"][0]["preparation"]["removed_fragment_indices"] = [1]
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.solutions[0].topology == "linear"
    assert len(result.solutions[0].junctions) == 1
    assert result.digests[0].background_fragment_indices == []
    assert result.digests[0].removed_fragment_indices == [1]
    assert next(m for m in result.materials if m.id == "source:donor").topology == "circular"


def test_mixed_prepared_source_preserves_physical_end_strings():
    data = request().model_dump()
    data["sources"][1]["source"].update(sequence="GGAG" + B, features=[])
    data["parts"][1]["preparation"] = dict(kind="prepared",
        left_end=dict(type="sticky_5", overhang="GGAG", protruding_strand="top"),
        right_end=dict(type="sticky_5", overhang="CATT", protruding_strand="bottom"))
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.solutions[0].sequence == "AATG" + A + "GGAG" + B
    assert result.primers == []


def test_internal_sites_produce_complete_digest_without_false_intact_product():
    data = request(kind="synthesis").model_dump()
    payload = B[:25] + "GGTCTCAACTG" + B[25:]
    data["sources"][1]["source"].update(sequence=payload, features=[])
    data["parts"][1]["preparation"]["region"]["end"] = len(payload)
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.solutions == [] and result.selected_solution_id is None
    assert len(result.digests[1].fragments) == 4
    assert result.digests[1].background_fragment_indices == [0, 1, 2, 3]
    assert result.diagnostics


def test_bad_bbsI_generic_spacer_is_not_silently_repaired():
    data = request("BbsI").model_dump()
    data["parts"][1]["preparation"]["left"]["spacer"] = "A"
    with pytest.raises(ValueError, match="geometry"):
        design_golden_gate(GoldenGateDesignRequest.model_validate(data))


def test_revision_resolution_reused_and_no_source_mutation():
    req = request()
    data = req.model_dump()
    original = data["sources"][1]["source"]
    material = Material(**{k: v for k, v in original.items() if k != "kind"})
    data["sources"][1]["source"] = dict(kind="molecular_revision", revision_id="revision-fixture")
    data["sources"].append(dict(id="same-revision", source=dict(kind="molecular_revision", revision_id="revision-fixture")))
    calls = []
    def resolve(revision):
        calls.append(revision)
        return material
    snapshot = material.model_dump()
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data), resolve_revision=resolve)
    assert calls == ["revision-fixture"]
    assert material.model_dump() == snapshot
    assert result.solutions[0].sequence == "AATG" + A + "GGAG" + B


def test_unknown_science_fields_rejected():
    data = request().model_dump()
    data["primer_settings"]["hidden_default"] = 42
    with pytest.raises(ValidationError, match="extra_forbidden"):
        GoldenGateDesignRequest.model_validate(data)


def test_ambiguous_pcr_is_evidence_not_a_false_unique_product():
    data = request().model_dump()
    data["sources"][1]["source"].update(sequence=("GGAG" + B + "AATG") * 2, features=[])
    data["parts"][1]["preparation"]["region"] = dict(start=4, end=4 + len(B))
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.preparations[1].pcr_verification == "ambiguous"
    assert result.preparations[1].pcr_diagnostics
    assert result.solutions[0].sequence == "AATG" + A + "GGAG" + B


@pytest.mark.parametrize("shift,retained", [(0, 0), (5, 0), (20, 1)])
@pytest.mark.parametrize("reverse", [False, True])
def test_donor_origin_crossing_sites_and_reverse_duplex(shift, retained, reverse):
    data = request().model_dump()
    donor = data["sources"][0]["source"]["sequence"]
    if reverse:
        donor = rc(donor)
        data["parts"][0]["orientation"] = "reverse"
    donor = donor[shift:] + donor[:shift]
    data["sources"][0]["source"]["sequence"] = donor
    data["parts"][0]["preparation"]["retained_fragment_index"] = retained
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.solutions[0].sequence == "AATG" + A + "GGAG" + B
    for mapping in result.solutions[0].mappings:
        parent = next(m for m in result.materials if m.id == mapping.parent_id)
        span = parent.sequence[mapping.parent_start:mapping.parent_end]
        assert result.solutions[0].sequence[mapping.output_start:mapping.output_end] == (span if mapping.strand == 1 else rc(span))


def test_origin_wrapping_pcr_region_and_truncated_feature():
    data = request().model_dump()
    shift = 23
    # Selected payload crosses the source origin, with flanking non-payload DNA.
    circular = B[shift:] + "TTTAAA" + B[:shift]
    data["sources"][1]["source"].update(sequence=circular, topology="circular", features=[
        dict(id="whole", type="CDS", segments=[dict(start=0, end=len(circular))], codon_start=1),
        dict(id="wrap", type="misc_feature", segments=[dict(start=len(B)-shift+6, end=9, wraps_origin=True)])])
    data["parts"][1]["preparation"]["region"] = dict(start=len(B)-shift+6, end=len(B)-shift, wraps_origin=True)
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.preparations[1].pcr_verification == "verified"
    product = result.solutions[0]
    assert product.sequence == "AATG" + A + "GGAG" + B
    cds = next(f for f in product.features if f.type == "CDS")
    assert cds.status == "truncated" and cds.frame_preserved is False
    feature = next(f for f in product.features if f.type == "misc_feature")
    assert feature.status == "intact"


def test_reconstructed_junction_site_is_warning_not_stable_product():
    data = request(kind="synthesis").model_dump()
    # BsaI site is reconstructed across payload A suffix GG + junction TCTC.
    donor = data["sources"][0]["source"]["sequence"]
    a = A[:-2] + "GG"
    data["sources"][0]["source"]["sequence"] = donor.replace(A + "GGAG", a + "TCTC")
    data["parts"][1]["preparation"]["left"]["fusion"] = "TCTC"
    # The reconstructed site is already in the donor, so use explicit prepared
    # vector material (its original preparation is outside this design).
    data["sources"][0]["source"].update(sequence="AATG" + a, topology="linear")
    data["parts"][0]["preparation"] = dict(kind="prepared",
        left_end=dict(type="sticky_5", overhang="AATG", protruding_strand="top"),
        right_end=dict(type="sticky_5", overhang="GAGA", protruding_strand="bottom"))
    result = design_golden_gate(GoldenGateDesignRequest.model_validate(data))
    assert result.solutions[0].sequence == "AATG" + a + "TCTC" + B
    assert result.solutions[0].occurrences
    assert any("recut" in w for w in result.solutions[0].warnings)


def test_json_roundtrip_references_and_closed_contract():
    import jsonschema
    from services.assembly.golden_gate_design_types import GoldenGateDesignResult
    req = request("BbsI")
    result = design_golden_gate(req)
    restored = GoldenGateDesignResult.model_validate_json(result.model_dump_json())
    assert restored == result
    for material_id in restored.solutions[0].part_material_ids:
        assert design_material(restored, material_id).sequence
    jsonschema.validate(req.model_dump(mode="json"), GoldenGateDesignRequest.model_json_schema())
    jsonschema.validate(result.model_dump(mode="json"), GoldenGateDesignResult.model_json_schema())


def test_schema_matches_the_typed_authority():
    schema = Path(__file__).resolve().parents[3] / "schemas/ngs_molbio/molbio-assembly-golden_gate-design-v1.schema.json"
    assert json.loads(schema.read_text()) == GoldenGateDesignRequest.model_json_schema()
