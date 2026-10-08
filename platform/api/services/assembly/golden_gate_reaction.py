"""Optional, unit-checked Golden Gate worksheet; no design/save admission policy.

No shared DNA mass/molar utility exists in the API. This is the small common
conversion owner for the new workup. Unknowns stay None; impossible numerical
quantities return diagnostics, not fabricated zeroes or sequence-save gates.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DNAAmount(_Model):
    value: float
    unit: Literal["ng", "pmol"]


class DNAStock(_Model):
    value: float
    unit: Literal["ng/uL", "pmol/uL", "nM", "uM"]


class Dilution(_Model):
    factor: float = Field(default=1, description="Final volume / stock volume; 1 means undiluted. Invalid values are worksheet diagnostics.")
    preparation_volume_uL: float | None = None


class ReactionPart(_Model):
    part_id: str = Field(min_length=1)
    length_bp: int | None = Field(default=None, description="Length of actual purified/prepared DNA, not its parent donor plasmid.")
    amount: DNAAmount | None = None
    ratio_to_reference: float | None = Field(default=None, description="Requested molar ratio to settings.reference_part_id; mutually exclusive with amount.")
    stock: DNAStock | None = None
    dilution: Dilution = Field(default_factory=Dilution)
    in_mastermix: bool = False
    phosphorylation: Literal["unknown", "phosphorylated", "unphosphorylated"] = "unknown"
    purification: str | None = None

    @model_validator(mode="after")
    def amount_or_ratio(self):
        if self.amount is not None and self.ratio_to_reference is not None:
            raise ValueError("Choose explicit amount or molar ratio, not both")
        return self


class ReactionReagent(_Model):
    reagent_id: str = Field(min_length=1)
    name: str
    role: Literal["restriction_enzyme", "ligase", "buffer", "additive", "other"]
    formulation: str | None = None
    volume_uL: float | None = None
    stock_multiple: float | None = Field(default=None, description="Optional X stock strength for C1V1=C2V2 calculation.")
    final_multiple: float | None = None
    in_mastermix: bool = True

    @model_validator(mode="after")
    def direct_or_diluted(self):
        if self.volume_uL is not None and (self.stock_multiple is not None or self.final_multiple is not None):
            raise ValueError("Choose volume or X-stock dilution, not both")
        return self


class CyclingStep(_Model):
    temperature_C: float
    duration_seconds: float


class CyclingBlock(_Model):
    repetitions: int
    steps: tuple[CyclingStep, ...]


class CyclingProgram(_Model):
    name: str
    provenance: str | None = None
    blocks: tuple[CyclingBlock, ...]


class ReactionSettings(_Model):
    schema_version: Literal["golden-gate-reaction/v1"] = "golden-gate-reaction/v1"
    mass_basis_g_per_mol_bp: float = Field(default=660, description="Displayed approximate dsDNA molecular-weight basis, g/mol/bp; operator editable.")
    reference_part_id: str | None = None
    total_volume_uL: float | None = None
    reaction_count: int = 1
    mastermix_overage_percent: float = 0
    water_in_mastermix: bool = True
    rounding_step_uL: float | None = Field(default=None, description="Optional nearest pipetting increment; ties round half up. No automatic minimum-volume increase.")
    minimum_transfer_uL: float | None = None
    cycling: CyclingProgram | None = None


class ReactionRequest(_Model):
    settings: ReactionSettings = Field(default_factory=ReactionSettings)
    parts: tuple[ReactionPart, ...] = ()
    reagents: tuple[ReactionReagent, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [p.part_id for p in self.parts] + [r.reagent_id for r in self.reagents]
        if len(set(ids)) != len(ids) or "water" in ids:
            raise ValueError("Part/reagent IDs must be unique; water is reserved")
        return self


class WorksheetDiagnostic(_Model):
    code: str
    component_id: str | None = None
    message: str


class ReactionRow(_Model):
    component_id: str
    kind: Literal["dna", "reagent", "water"]
    requested_pmol: float | None = None
    requested_mass_ng: float | None = None
    stock_pmol_per_uL: float | None = None
    diluted_stock_pmol_per_uL: float | None = None
    stock_ng_per_uL: float | None = None
    diluted_stock_ng_per_uL: float | None = None
    exact_volume_uL: float | None = None
    transfer_volume_uL: float | None = None
    delivered_pmol: float | None = None
    delivered_mass_ng: float | None = None
    delivered_ratio_to_reference: float | None = None
    dilution_stock_uL: float | None = None
    dilution_diluent_uL: float | None = None
    batch_volume_uL: float | None = None
    mastermix_volume_uL: float | None = None


class ReactionResult(_Model):
    request: ReactionRequest
    rows: tuple[ReactionRow, ...]
    known_transfer_subtotal_uL: float
    total_transfer_uL: float | None
    water_exact_uL: float | None
    mastermix_total_uL: float | None
    diagnostics: tuple[WorksheetDiagnostic, ...]
    mass_basis_description: str = "Approximate dsDNA basis in g/mol/bp; not sequence-specific molecular weight."


def mass_ng_to_pmol(mass_ng: float, length_bp: int, *, mass_basis_g_per_mol_bp: float = 660) -> float:
    """Convert ng dsDNA to pmol; invalid physical quantities raise ValueError.

    The worksheet catches invalid input as diagnostics; this numerical primitive
    deliberately does not return a plausible number for impossible operands.
    """
    import math
    if not all(math.isfinite(x) for x in (mass_ng, length_bp, mass_basis_g_per_mol_bp)) or mass_ng < 0 or length_bp <= 0 or mass_basis_g_per_mol_bp <= 0:
        raise ValueError("Nonnegative mass, positive DNA length and mass basis required")
    return 1000 * mass_ng / (mass_basis_g_per_mol_bp * length_bp)


def pmol_to_mass_ng(pmol: float, length_bp: int, *, mass_basis_g_per_mol_bp: float = 660) -> float:
    """Inverse of mass_ng_to_pmol on the explicitly selected dsDNA mass basis."""
    # Share validation, including finite amount/length/basis semantics.
    mass_ng_to_pmol(pmol, length_bp, mass_basis_g_per_mol_bp=mass_basis_g_per_mol_bp)
    return pmol * mass_basis_g_per_mol_bp * length_bp / 1000


def calculate_reaction(request: ReactionRequest) -> ReactionResult:
    """Calculate a worksheet with requested/effective volumes and diagnostics.

    Ratios refer to the explicit amount on one selected reference DNA part, not
    a mass ratio. Rounding affects delivered quantities, which are reported
    separately. Dilution is explicit; minimum pipetting volume never silently
    changes it. Missing volume rows keep water and full totals unknown.
    """
    settings = request.settings
    diagnostics: list[WorksheetDiagnostic] = []

    def note(code, message, component_id=None):
        diagnostics.append(WorksheetDiagnostic(code=code, component_id=component_id, message=message))

    def physical(value, name, component_id=None, *, positive=False):
        if value is None:
            return None
        if value < 0 or (positive and value == 0):
            note("invalid_quantity", f"{name} must be {'positive' if positive else 'nonnegative'}; received {value}.", component_id)
            return None
        return value

    basis = physical(settings.mass_basis_g_per_mol_bp, "mass basis", positive=True)
    total = physical(settings.total_volume_uL, "reaction volume", positive=True)
    count = physical(settings.reaction_count, "reaction count", positive=True)
    overage = physical(settings.mastermix_overage_percent, "mastermix overage")
    step = physical(settings.rounding_step_uL, "rounding increment", positive=True)
    minimum = physical(settings.minimum_transfer_uL, "minimum transfer")
    mix_factor = count * (1 + overage / 100) if count is not None and overage is not None else None
    if settings.cycling is not None:
        for block in settings.cycling.blocks:
            physical(block.repetitions, "cycling repetitions", positive=True)
            for cycle_step in block.steps:
                physical(cycle_step.duration_seconds, "cycling duration")
    lengths = {p.part_id: physical(p.length_bp, "DNA length", p.part_id, positive=True) for p in request.parts}

    def from_mass(value, part_id):
        length = lengths[part_id]
        return mass_ng_to_pmol(value, length, mass_basis_g_per_mol_bp=basis) if length is not None and basis is not None else None

    def to_mass(value, part_id):
        length = lengths[part_id]
        return pmol_to_mass_ng(value, length, mass_basis_g_per_mol_bp=basis) if value is not None and length is not None and basis is not None else None

    def amount(part):
        if part.amount is None:
            return None
        value = physical(part.amount.value, "DNA amount", part.part_id)
        if value is None:
            return None
        return value if part.amount.unit == "pmol" else from_mass(value, part.part_id)

    amounts = {p.part_id: amount(p) for p in request.parts}
    reference = settings.reference_part_id
    if reference is not None and reference not in amounts:
        note("unknown_reference", "Reference DNA part not present.", reference)
    for part in request.parts:
        if part.ratio_to_reference is not None:
            ratio = physical(part.ratio_to_reference, "molar ratio", part.part_id)
            ref = amounts.get(reference) if reference is not None else None
            # Do not recursively infer a reference amount through ratio chains.
            ref_part = next((p for p in request.parts if p.part_id == reference), None)
            if ref_part is None or ref_part.amount is None or reference == part.part_id:
                ref = None
            amounts[part.part_id] = ref * ratio if ref is not None and ratio is not None else None
            if amounts[part.part_id] is None:
                note("unknown_ratio_amount", "Molar ratio requires an explicit reference amount.", part.part_id)

    def rounded(volume, component_id):
        if volume is None:
            return None
        if settings.rounding_step_uL is not None and step is None:
            return None
        if step is not None:
            volume = float((Decimal(str(volume)) / Decimal(str(step))).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(str(step)))
        if minimum is not None and 0 < volume < minimum:
            note("below_minimum_transfer", "Transfer is below selected pipetting minimum; select a dilution explicitly.", component_id)
        if volume == 0:
            note("zero_transfer", "Calculated/rounded transfer is zero.", component_id)
        return volume

    def batch(volume, in_mastermix):
        return dict(batch_volume_uL=volume * count if volume is not None and count is not None else None,
                    mastermix_volume_uL=volume * mix_factor if in_mastermix and volume is not None and mix_factor is not None else None)

    rows: list[ReactionRow] = []
    master_ids = set()
    for part in request.parts:
        pid = part.part_id
        pmol = amounts[pid]
        mass = to_mass(pmol, pid)
        if part.amount is not None and part.amount.unit == "ng" and part.amount.value >= 0:
            mass = part.amount.value
        concentration = None
        mass_concentration = None
        if part.stock is not None:
            value = physical(part.stock.value, "stock concentration", pid, positive=True)
            if value is not None:
                concentration = from_mass(value, pid) if part.stock.unit == "ng/uL" else value * {"pmol/uL": 1, "nM": 0.001, "uM": 1}[part.stock.unit]
                mass_concentration = value if part.stock.unit == "ng/uL" else to_mass(concentration, pid)
        dilution = physical(part.dilution.factor, "dilution factor", pid, positive=True)
        if dilution is not None and dilution < 1:
            note("invalid_dilution", "Dilution factor below 1 requires concentration, not dilution.", pid)
            dilution = None
        prep = physical(part.dilution.preparation_volume_uL, "dilution preparation volume", pid, positive=True)
        dilute_stock = prep / dilution if prep is not None and dilution is not None else None
        dilute_diluent = prep - dilute_stock if prep is not None and dilute_stock is not None else None
        for label, volume in (("stock", dilute_stock), ("diluent", dilute_diluent)):
            if minimum is not None and volume is not None and 0 < volume < minimum:
                note("below_minimum_transfer", f"Dilution {label} preparation transfer is below selected pipetting minimum; quantities are unchanged.", pid)
        diluted = concentration / dilution if concentration is not None and dilution is not None else None
        diluted_mass = mass_concentration / dilution if mass_concentration is not None and dilution is not None else None
        exact = pmol / diluted if pmol is not None and diluted is not None else None
        if exact is None and mass is not None and diluted_mass is not None:
            # ng / (ng/uL) needs no assumed fragment length or molecular weight.
            exact = mass / diluted_mass
        transfer = rounded(exact, pid)
        if prep is not None and transfer is not None:
            batch_factor = mix_factor if part.in_mastermix else count
            required = transfer * batch_factor if batch_factor is not None else None
            if required is not None and required > prep:
                note("insufficient_dilution_volume", "Dilution preparation is smaller than the requested batch transfer.", pid)
        delivered = transfer * diluted if transfer is not None and diluted is not None else None
        if exact is None:
            note("unknown_dna_volume", "DNA volume is unknown; supply amount, stock and any required length/mass basis.", pid)
        if part.in_mastermix:
            master_ids.add(pid)
        rows.append(ReactionRow(component_id=pid, kind="dna", requested_pmol=pmol, requested_mass_ng=mass,
            stock_pmol_per_uL=concentration, diluted_stock_pmol_per_uL=diluted,
            stock_ng_per_uL=mass_concentration, diluted_stock_ng_per_uL=diluted_mass,
            exact_volume_uL=exact, transfer_volume_uL=transfer, delivered_pmol=delivered,
            delivered_mass_ng=transfer * diluted_mass if transfer is not None and diluted_mass is not None else None,
            dilution_stock_uL=dilute_stock,
            dilution_diluent_uL=dilute_diluent,
            **batch(transfer, part.in_mastermix)))
    delivered_reference = next((r.delivered_pmol for r in rows if r.component_id == reference), None)
    if delivered_reference is not None and delivered_reference > 0:
        rows = [r.model_copy(update={"delivered_ratio_to_reference": r.delivered_pmol / delivered_reference if r.delivered_pmol is not None else None}) for r in rows]
    for reagent in request.reagents:
        rid = reagent.reagent_id
        volume = physical(reagent.volume_uL, "reagent volume", rid)
        if reagent.volume_uL is None:
            stock = physical(reagent.stock_multiple, "stock X strength", rid, positive=True)
            final = physical(reagent.final_multiple, "final X strength", rid)
            volume = total * final / stock if total is not None and stock is not None and final is not None else None
        if volume is None:
            note("unknown_reagent_volume", "Reagent volume is unknown.", rid)
        transfer = rounded(volume, rid)
        if reagent.in_mastermix:
            master_ids.add(rid)
        rows.append(ReactionRow(component_id=rid, kind="reagent", exact_volume_uL=volume,
                                transfer_volume_uL=transfer, **batch(transfer, reagent.in_mastermix)))
    subtotal = sum(r.transfer_volume_uL for r in rows if r.transfer_volume_uL is not None)
    all_known = all(r.transfer_volume_uL is not None for r in rows)
    water = total - subtotal if total is not None and all_known else None
    if total is not None and subtotal > total:
        note("volume_exceeds_reaction", "Known transfers exceed total reaction volume; negative water is not pipettable.")
    water_transfer = rounded(water, "water") if water is not None and water >= 0 else None
    if settings.water_in_mastermix:
        master_ids.add("water")
    rows.append(ReactionRow(component_id="water", kind="water", exact_volume_uL=water,
                            transfer_volume_uL=water_transfer, **batch(water_transfer, settings.water_in_mastermix)))
    if total is None:
        note("unknown_reaction_volume", "Total reaction volume is unknown; water cannot be calculated.")
    final_total = sum(r.transfer_volume_uL for r in rows if r.transfer_volume_uL is not None) if all(r.transfer_volume_uL is not None for r in rows) else None
    if final_total is not None and total is not None and abs(final_total - total) > 1e-9:
        note("rounding_volume_difference", "Rounded transfers differ from the requested total volume.")
    mix_rows = [r for r in rows if r.component_id in master_ids]
    mix_total = sum(r.mastermix_volume_uL for r in mix_rows if r.mastermix_volume_uL is not None) if all(r.mastermix_volume_uL is not None for r in mix_rows) else None
    return ReactionResult(request=request, rows=tuple(rows), known_transfer_subtotal_uL=subtotal,
        total_transfer_uL=final_total, water_exact_uL=water, mastermix_total_uL=mix_total,
        diagnostics=tuple(diagnostics))


def reaction_settings_schema() -> dict:
    """Closed native request schema for shared settings/UI/persistence wiring."""
    return ReactionRequest.model_json_schema()
