"""Validated Golden Gate assembly using explicit post-digestion overhangs."""
from __future__ import annotations

from dataclasses import dataclass

from services.molbio_ops import find_pattern_positions

from .common import orient_fragment
from .ligation import simulate_ligation
from .types import AssemblyError, AssemblyFragment, AssemblyProduct


@dataclass(frozen=True, slots=True)
class TypeIISEnzyme:
    name: str
    site: str
    overhang_length: int


TYPE_IIS_ENZYMES: dict[str, TypeIISEnzyme] = {
    "BsaI": TypeIISEnzyme(name="BsaI", site="GGTCTC", overhang_length=4),
    "BsmBI": TypeIISEnzyme(name="BsmBI", site="CGTCTC", overhang_length=4),
    "BbsI": TypeIISEnzyme(name="BbsI", site="GAAGAC", overhang_length=4),
    "SapI": TypeIISEnzyme(name="SapI", site="GCTCTTC", overhang_length=3),
    "AarI": TypeIISEnzyme(name="AarI", site="CACCTGC", overhang_length=4),
}


def get_type_iis_enzyme(name: str) -> TypeIISEnzyme:
    enzyme = TYPE_IIS_ENZYMES.get((name or "").strip())
    if enzyme is None:
        supported = ", ".join(sorted(TYPE_IIS_ENZYMES))
        raise AssemblyError(f"Unsupported Golden Gate enzyme '{name}'. Supported enzymes: {supported}")
    return enzyme


def simulate_golden_gate(
    fragments: list[AssemblyFragment],
    *,
    enzyme_name: str,
    circular: bool,
) -> AssemblyProduct:
    if len(fragments) < 2:
        raise AssemblyError("Golden Gate assembly requires at least two fragments")

    enzyme = get_type_iis_enzyme(enzyme_name)
    oriented = [orient_fragment(fragment) for fragment in fragments]

    for fragment in oriented:
        for side_name, end in (("left", fragment.left_end), ("right", fragment.right_end)):
            if end is None:
                raise AssemblyError(
                    f"Golden Gate fragment '{fragment.name}' is missing an explicit {side_name} overhang"
                )
            if end.type != "sticky_5":
                raise AssemblyError(
                    f"Golden Gate fragment '{fragment.name}' {side_name} end must be a 5' overhang"
                )
            if len(end.overhang) != enzyme.overhang_length:
                raise AssemblyError(
                    f"Golden Gate fragment '{fragment.name}' {side_name} overhang is {len(end.overhang)} nt; "
                    f"{enzyme.name} requires {enzyme.overhang_length} nt overhangs"
                )

    product = simulate_ligation(fragments, circular=circular, mode="golden_gate")
    warnings = list(product.warnings)
    if find_pattern_positions(product.sequence, enzyme.site, circular=product.circular):
        warnings.append(
            f"Final product still contains at least one {enzyme.name} recognition site ({enzyme.site})"
        )

    return AssemblyProduct(
        mode="golden_gate",
        sequence=product.sequence,
        circular=product.circular,
        fragments=product.fragments,
        junctions=product.junctions,
        warnings=warnings,
        validation_notes=[f"Validated {enzyme.name} overhang length: {enzyme.overhang_length} nt"],
    )

