"""Independent arithmetic and typed-input tests for optional reaction workups."""
from __future__ import annotations

from fractions import Fraction

import pytest
from pydantic import ValidationError

from services.assembly.golden_gate_reaction import (
    CyclingBlock, CyclingProgram, CyclingStep, Dilution, DNAAmount, DNAStock,
    ReactionPart, ReactionReagent, ReactionRequest, ReactionResult, ReactionSettings,
    calculate_reaction, mass_ng_to_pmol, pmol_to_mass_ng, reaction_settings_schema,
)


def row(result, component_id):
    return next(r for r in result.rows if r.component_id == component_id)


def codes(result):
    return {d.code for d in result.diagnostics}


def test_independent_mass_amount_inverse():
    # 1000 bp, 66 ng = 0.1 pmol on the explicit approximate dsDNA basis.
    assert mass_ng_to_pmol(66, 1000) == pytest.approx(float(Fraction(1, 10)))
    assert pmol_to_mass_ng(0.1, 1000) == pytest.approx(66)
    assert mass_ng_to_pmol(60, 1000, mass_basis_g_per_mol_bp=600) == pytest.approx(0.1)
    assert mass_ng_to_pmol(0, 1000) == 0


@pytest.mark.parametrize("mass,length,basis", [(-1, 1, 660), (1, 0, 660), (1, -5, 660), (1, 10, 0), (float("inf"), 10, 660)])
def test_invalid_primitive_operands(mass, length, basis):
    with pytest.raises(ValueError):
        mass_ng_to_pmol(mass, length, mass_basis_g_per_mol_bp=basis)
    with pytest.raises(ValueError):
        pmol_to_mass_ng(mass, length, mass_basis_g_per_mol_bp=basis)


@pytest.mark.parametrize("stock", [DNAStock(value=66, unit="ng/uL"), DNAStock(value=0.1, unit="pmol/uL"), DNAStock(value=100, unit="nM"), DNAStock(value=0.1, unit="uM")])
def test_concentration_units_are_equivalent(stock):
    request = ReactionRequest(parts=(ReactionPart(part_id="insert", length_bp=1000,
        amount=DNAAmount(value=0.2, unit="pmol"), stock=stock),))
    r = row(calculate_reaction(request), "insert")
    assert r.stock_pmol_per_uL == pytest.approx(0.1)
    assert r.exact_volume_uL == pytest.approx(2)
    assert r.requested_mass_ng == pytest.approx(132)


def test_ratio_uses_actual_fragment_length_buffer_dilution_and_mastermix():
    request = ReactionRequest(settings=ReactionSettings(reference_part_id="vector", total_volume_uL=20,
        reaction_count=8, mastermix_overage_percent=10), parts=(
        ReactionPart(part_id="vector", length_bp=3000, amount=DNAAmount(value=66, unit="ng"), stock=DNAStock(value=33, unit="ng/uL")),
        ReactionPart(part_id="insert", length_bp=500, ratio_to_reference=3, stock=DNAStock(value=33, unit="ng/uL"))),
        reagents=(ReactionReagent(reagent_id="buffer", name="buffer", role="buffer", stock_multiple=10, final_multiple=1),
                  ReactionReagent(reagent_id="ligase", name="selected ligase", role="ligase", volume_uL=1),
                  ReactionReagent(reagent_id="enzyme", name="BsaI", role="restriction_enzyme", volume_uL=1)))
    result = calculate_reaction(request)
    vector, insert, buffer, water = (row(result, p) for p in ("vector", "insert", "buffer", "water"))
    assert vector.requested_pmol == pytest.approx(float(Fraction(1, 30)))
    assert insert.requested_pmol == pytest.approx(0.1)
    assert insert.requested_mass_ng == pytest.approx(33)  # not 198 ng from parent length
    assert vector.transfer_volume_uL == pytest.approx(2)
    assert insert.transfer_volume_uL == pytest.approx(1)
    assert insert.delivered_ratio_to_reference == pytest.approx(3)
    assert buffer.transfer_volume_uL == pytest.approx(2)
    assert water.transfer_volume_uL == pytest.approx(13)
    assert result.total_transfer_uL == pytest.approx(20)
    assert buffer.mastermix_volume_uL == pytest.approx(float(Fraction(88, 5)))
    assert result.mastermix_total_uL == pytest.approx(float(Fraction(748, 5)))  # 17 uL * 8 * 1.1
    assert vector.mastermix_volume_uL is None
    assert vector.batch_volume_uL == pytest.approx(16)
    assert ReactionResult.model_validate_json(result.model_dump_json()) == result


def test_dilution_rounding_delivered_amount_and_minimum_are_explicit():
    request = ReactionRequest(settings=ReactionSettings(total_volume_uL=10, rounding_step_uL=0.1, minimum_transfer_uL=0.5),
        parts=(ReactionPart(part_id="insert", length_bp=1000, amount=DNAAmount(value=0.0125, unit="pmol"),
            stock=DNAStock(value=1, unit="pmol/uL"), dilution=Dilution(factor=20, preparation_volume_uL=100)),))
    result = calculate_reaction(request)
    part = row(result, "insert")
    assert part.dilution_stock_uL == pytest.approx(5)
    assert part.dilution_diluent_uL == pytest.approx(95)
    assert part.diluted_stock_pmol_per_uL == pytest.approx(0.05)
    assert part.exact_volume_uL == pytest.approx(0.25)
    assert part.transfer_volume_uL == pytest.approx(0.3)  # explicit half up
    assert part.delivered_pmol == pytest.approx(0.015)
    assert "below_minimum_transfer" in codes(result)
    assert request.parts[0].dilution.factor == 20


def test_unknown_stock_and_length_stay_null_not_zero():
    result = calculate_reaction(ReactionRequest(settings=ReactionSettings(total_volume_uL=20), parts=(
        ReactionPart(part_id="no_stock", length_bp=1000, amount=DNAAmount(value=1, unit="pmol")),
        ReactionPart(part_id="no_length", amount=DNAAmount(value=20, unit="ng"), stock=DNAStock(value=10, unit="ng/uL")),)))
    assert row(result, "no_stock").stock_pmol_per_uL is None
    assert row(result, "no_stock").requested_pmol == 1
    assert row(result, "no_length").requested_pmol is None
    assert row(result, "no_length").requested_mass_ng == 20
    assert row(result, "no_length").transfer_volume_uL == 2
    assert row(result, "no_length").delivered_mass_ng == 20
    assert row(result, "no_length").delivered_pmol is None
    assert result.water_exact_uL is None and result.total_transfer_uL is None
    assert result.known_transfer_subtotal_uL == 2
    assert result.mastermix_total_uL is None
    assert "unknown_dna_volume" in codes(result)


def test_missing_length_does_not_block_direct_molar_stock_math():
    result = calculate_reaction(ReactionRequest(parts=(ReactionPart(part_id="known_molar",
        amount=DNAAmount(value=0.1, unit="pmol"), stock=DNAStock(value=0.05, unit="pmol/uL")),)))
    assert row(result, "known_molar").transfer_volume_uL == pytest.approx(2)
    assert row(result, "known_molar").requested_mass_ng is None


@pytest.mark.parametrize("quantity", [-1, 0])
def test_impossible_stock_is_diagnostic_not_exception(quantity):
    result = calculate_reaction(ReactionRequest(parts=(ReactionPart(part_id="insert", length_bp=1000,
        amount=DNAAmount(value=0.1, unit="pmol"), stock=DNAStock(value=quantity, unit="ng/uL")),)))
    assert row(result, "insert").transfer_volume_uL is None
    assert "invalid_quantity" in codes(result)


def test_impossible_volume_total_does_not_fabricate_water_or_gate_design():
    request = ReactionRequest(settings=ReactionSettings(total_volume_uL=1),
        reagents=(ReactionReagent(reagent_id="buffer", name="buffer", role="buffer", volume_uL=2),))
    result = calculate_reaction(request)
    assert result.water_exact_uL == -1
    assert row(result, "water").transfer_volume_uL is None
    assert result.total_transfer_uL is None
    assert "volume_exceeds_reaction" in codes(result)
    assert result.request == request
    assert "ready" not in result.model_dump() and "can_save" not in result.model_dump()


def test_missing_reference_or_cyclic_ratio_not_invented():
    result = calculate_reaction(ReactionRequest(settings=ReactionSettings(reference_part_id="v"), parts=(
        ReactionPart(part_id="v", ratio_to_reference=1), ReactionPart(part_id="i", ratio_to_reference=3))))
    assert all(r.requested_pmol is None for r in result.rows)
    assert "unknown_ratio_amount" in codes(result)


def test_invalid_dilution_and_insufficient_batch_volume():
    common = dict(part_id="p", amount=DNAAmount(value=1, unit="pmol"), stock=DNAStock(value=1, unit="pmol/uL"))
    bad = calculate_reaction(ReactionRequest(parts=(ReactionPart(**common, dilution=Dilution(factor=0.5)),)))
    assert "invalid_dilution" in codes(bad)
    assert row(bad, "p").transfer_volume_uL is None
    insufficient = calculate_reaction(ReactionRequest(settings=ReactionSettings(reaction_count=4),
        parts=(ReactionPart(**common, dilution=Dilution(factor=2, preparation_volume_uL=5)),)))
    assert row(insufficient, "p").transfer_volume_uL == 2
    assert "insufficient_dilution_volume" in codes(insufficient)


def test_cycling_is_selected_not_inferred_from_enzyme_or_score():
    request = ReactionRequest(settings=ReactionSettings(cycling=CyclingProgram(name="operator program", provenance="local choice",
        blocks=(CyclingBlock(repetitions=12, steps=(CyclingStep(temperature_C=37, duration_seconds=60),
            CyclingStep(temperature_C=16, duration_seconds=120))),))))
    result = calculate_reaction(request)
    assert result.request.settings.cycling == request.settings.cycling
    assert calculate_reaction(ReactionRequest()).request.settings.cycling is None


def test_native_closed_settings_units_and_no_hidden_quantities():
    schema = reaction_settings_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["DNAStock"]["properties"]["unit"]["enum"] == ["ng/uL", "pmol/uL", "nM", "uM"]
    with pytest.raises(ValidationError):
        DNAStock(value=1, unit="mg/mL")
    with pytest.raises(ValidationError):
        ReactionSettings(hidden_kinetics=True)
    with pytest.raises(ValidationError):
        DNAStock(value=float("nan"), unit="nM")
    with pytest.raises(ValidationError):
        ReactionPart(part_id="p", amount=DNAAmount(value=1, unit="ng"), ratio_to_reference=3)
