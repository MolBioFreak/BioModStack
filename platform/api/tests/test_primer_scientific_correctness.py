"""Scientific-audit regressions with independent native numerical oracles."""
import random
import pytest
from Bio.SeqUtils import MeltingTemp as mt
from pydna.amplify import pcr as native_pcr
from pydna.dseqrecord import Dseqrecord
from pydantic import ValidationError
from routers.molbio_ops import (PrimerDesignRequest, PrimerTmSettings, calculate_primer_tm_result,
    default_tm_settings_for_sequence_type, design_primer_pairs_for_request, validate_primer_binding_geometry)
from services.primer_qc import evaluate_primer_qc
SEQ = "ACGTTGCAAGTCCATGGTAC"

def test_nn_does_not_delete_ambiguity_and_gc_accounts_for_S():
    r = calculate_primer_tm_result("ACGTTGCAASSSSSGTCCATGGTAC")
    assert r.sequence == "ACGTTGCAASSSSSGTCCATGGTAC"
    assert r.length == 25 and r.gc_percent == 60 and r.tm is None
    assert any("No bases were removed" in w for w in r.warnings)
    assert evaluate_primer_qc(r.sequence).gc_percent == 60
    assert calculate_primer_tm_result(SEQ, complement_sequence="TGCNNNNNNNAGGTACCATG").tm is None

@pytest.mark.parametrize("salt, method", [("none",0),("schildkraut_lifson_1965",1),("wetmur_1991",2),("santalucia_1996",3),("santalucia_1998_tm",4)])
def test_gc_applies_selected_correction(salt, method):
    r=calculate_primer_tm_result(SEQ,settings=PrimerTmSettings(algorithm="gc_empirical",salt_correction=salt))
    expected=81.5+.41*50-600/len(SEQ)
    if method: expected+=mt.salt_correction(Na=50,Mg=1.5,dNTPs=.6,seq=SEQ,method=method)
    assert r.tm == round(expected,2) and r.salt_correction == salt

@pytest.mark.parametrize("salt",["santalucia_1998_entropy","owczarzy_2004","owczarzy_2008"])
def test_gc_rejects_incompatible_models(salt):
    r=calculate_primer_tm_result(SEQ,settings=PrimerTmSettings(algorithm="gc_empirical",salt_correction=salt))
    assert r.tm is None and any("not supported" in w for w in r.warnings)

def test_standard_dna_nn_preserves_units_and_model():
    r=calculate_primer_tm_result(SEQ)
    expected=mt.Tm_NN(SEQ,nn_table=mt.DNA_NN4,dnac1=250,dnac2=0,Na=50,Mg=1.5,dNTPs=.6,saltcorr=7)
    assert r.tm == round(expected,2) == 62.03

def test_rna_dangling_perfect_overlap_uses_rna_de_table():
    sequence="CGUUCCAAAGAUGUGGGCAUGAGCUUAC"
    complement="A"+sequence.translate(str.maketrans("ACGU","UGCA"))+"A"
    settings=default_tm_settings_for_sequence_type("rna")
    r=calculate_primer_tm_result(sequence,"rna",settings,complement,1)
    expected=mt.Tm_NN(sequence,nn_table=mt.RNA_NN3,de_table=mt.RNA_DE1,c_seq=complement,
        shift=1,saltcorr=0,Na=1000,dnac1=250,dnac2=0)
    assert r.tm is not None and r.tm == round(expected,2)

def test_rna_rejects_dna_salt_and_unsupported_mismatch_conditions():
    settings=default_tm_settings_for_sequence_type("rna")
    r=calculate_primer_tm_result("ACGUACGUACGU","rna",settings.model_copy(update={"salt_correction":"owczarzy_2008"}))
    assert r.tm is None
    r=calculate_primer_tm_result("ACGUACGUACGU","rna",settings,"UGCAUGCAUGCC")
    assert r.tm is None and any("mismatch" in w for w in r.warnings)
    r=calculate_primer_tm_result("ACGUACGUACGU","rna",settings.model_copy(update={"na_mM":50}))
    assert r.tm is None and any("1 M Na" in w for w in r.warnings)

def test_tailed_product_matches_native_pcr_and_length_limits():
    rng=random.Random(4); sequence="".join(rng.choice("ACGT") for _ in range(400))
    req=PrimerDesignRequest(sequence=sequence,sequence_type="dna",target_start=100,target_end=200,
        primer_min_length=18,primer_max_length=24,product_min_length=40,product_max_length=150,
        flank_search_span=50,gc_min_percent=0,gc_max_percent=100,tm_target_c=60,tm_max_delta_c=15,
        gc_clamp_min=0,max_poly_x=6,max_pairs=2,overhang_forward="GGTCTCAACGTACTGATCGAT",
        overhang_reverse="GGTCTCTGACGATCGACTAG")
    assert not design_primer_pairs_for_request(req,"test").pairs
    result=design_primer_pairs_for_request(req.model_copy(update={"product_max_length":200}),"test")
    assert result.pairs
    for pair in result.pairs:
        native=native_pcr(pair.forward.sequence,pair.reverse.sequence,Dseqrecord(sequence))
        assert pair.product_length == len(native) <= 200
        assert pair.product_length == pair.product_end-pair.product_start+41

def test_off_target_count_requires_verified_intended_site():
    template=SEQ+"AAAAAAAAAA"+SEQ
    unknown=evaluate_primer_qc(SEQ,template_sequence=template)
    assert unknown.binding_site_count == 2 and unknown.off_target_site_count is None
    assert evaluate_primer_qc(SEQ,template_sequence=template,intended_binding=(0,20,1)).off_target_site_count == 1
    stale=evaluate_primer_qc(SEQ,template_sequence=template,intended_binding=(5,25,1))
    assert stale.off_target_site_count is None and any("not verified" in w for w in stale.warnings)

@pytest.mark.asyncio
async def test_general_library_oligo_has_no_template_geometry():
    await validate_primer_binding_geometry(None,target_sequence_id=None,binding_start=None,binding_end=None,binding_strand=-1)

def test_unknown_tm_fields_fail_closed():
    with pytest.raises(ValidationError): PrimerTmSettings(unknown_scientific_setting=1)

def test_degenerate_secondary_structure_is_unknown_not_zero_or_literal_pairing():
    from services.primer_qc import evaluate_primer_pair_qc
    qc=evaluate_primer_qc('N'*20)
    assert qc.max_self_complement is None and qc.max_hairpin_stem is None
    pair=evaluate_primer_pair_qc('N'*20,SEQ)
    assert pair.heterodimer_complement is None and pair.three_prime_heterodimer is None


def test_profiles_are_closed_and_report_fixed_scientific_search_scope():
    from routers.molbio_ops import PrimerQcBatchResponse, PrimerDesignSearchProfile
    qc=PrimerQcBatchResponse(primers=[],pairwise=[]).profile
    assert qc.version == 'exact_match_v1' and qc.binding_core_bases == 12
    assert (qc.hairpin_min_loop,qc.hairpin_max_loop)==(3,12)
    assert qc.three_prime_requires_both_ends
    search=PrimerDesignSearchProfile()
    assert search.candidates_per_strand==48 and search.target_overlap_bases==4
    with pytest.raises(ValidationError): type(qc)(binding_core_bases=8)
