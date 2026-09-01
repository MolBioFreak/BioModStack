"""Validated Golden Gate assembly resolved through the immutable restriction catalog."""
from __future__ import annotations

from dataclasses import dataclass

from services.restriction_analysis import analyze_sequence
from services.restriction_catalog import (
    CatalogUnavailable,
    CatalogView,
    RestrictionRecord,
    catalog_authority,
)

from .common import orient_fragment
from .ligation import simulate_ligation
from .types import AssemblyError, AssemblyFragment, AssemblyProduct


@dataclass(frozen=True, slots=True)
class TypeIISEnzyme:
    enzyme_id: str
    canonical_name: str
    site: str
    overhang_length: int
    catalog_id: str
    catalog_sha256: str
    record: RestrictionRecord

    @property
    def name(self) -> str:
        return self.canonical_name


def _resolve_from_catalog(catalog: CatalogView, enzyme_id: str) -> TypeIISEnzyme:
    identity = (enzyme_id or "").strip()
    record = catalog.by_id.get(identity)
    if record is None:
        raise AssemblyError(f"Unsupported Golden Gate enzyme identity '{enzyme_id}'")
    motifs = record.recognition.site_alternatives_iupac
    events = record.cleavage.events
    if (
        record.analysis_capability != "digest_simulation"
        or record.cleavage.status != "known_double_strand"
        or len(motifs) != 1
        or len(events) != 1
    ):
        raise AssemblyError(
            f"Golden Gate enzyme '{identity}' is not exact geometry-ready double-strand catalog authority"
        )
    event = events[0]
    motif = motifs[0]
    if (
        event.overhang_kind != "five_prime"
        or event.overhang_length_nt <= 0
        or max(event.top_offset, event.bottom_offset) <= len(motif)
    ):
        raise AssemblyError(
            f"Golden Gate enzyme '{identity}' does not have supported Type IIS cleavage geometry"
        )
    return TypeIISEnzyme(
        enzyme_id=record.enzyme_id,
        canonical_name=record.canonical_name,
        site=motif,
        overhang_length=event.overhang_length_nt,
        catalog_id=catalog.catalog_id,
        catalog_sha256=catalog.content_sha256,
        record=record,
    )


def get_type_iis_enzyme(enzyme_id: str) -> TypeIISEnzyme:
    try:
        catalog = catalog_authority.require()
    except CatalogUnavailable as exc:
        raise AssemblyError("restriction catalog authority is unavailable") from exc
    return _resolve_from_catalog(catalog, enzyme_id)


def golden_gate_options() -> tuple[TypeIISEnzyme, ...]:
    try:
        catalog = catalog_authority.require()
    except CatalogUnavailable as exc:
        raise AssemblyError("restriction catalog authority is unavailable") from exc
    resolved: list[TypeIISEnzyme] = []
    for record in catalog.ordered_records:
        try:
            resolved.append(_resolve_from_catalog(catalog, record.enzyme_id))
        except AssemblyError:
            continue
    return tuple(resolved)


def _site_count(sequence: str, *, circular: bool, enzyme: TypeIISEnzyme) -> int:
    result = analyze_sequence(
        sequence=sequence,
        topology="circular" if circular else "linear",
        catalog=catalog_authority.require(),
        records=(enzyme.record,),
        include_possible_sites=True,
    )
    return (
        result.counts.recognition_site_count_definite
        + result.counts.recognition_site_count_possible
    )


def simulate_golden_gate(
    fragments: list[AssemblyFragment],
    *,
    enzyme_id: str,
    circular: bool,
) -> AssemblyProduct:
    if len(fragments) < 2:
        raise AssemblyError("Golden Gate assembly requires at least two fragments")

    enzyme = get_type_iis_enzyme(enzyme_id)
    oriented = [orient_fragment(fragment) for fragment in fragments]

    for fragment in oriented:
        recognition_site_count = _site_count(fragment.sequence, circular=False, enzyme=enzyme)
        if recognition_site_count:
            raise AssemblyError(
                f"Golden Gate fragment '{fragment.name}' contains "
                f"{recognition_site_count} internal {enzyme.name} recognition site(s); "
                "post-digestion fragment geometry is ambiguous"
            )
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
    if _site_count(product.sequence, circular=product.circular, enzyme=enzyme):
        warnings.append(
            f"Final product still contains at least one {enzyme.name} recognition site "
            "according to the immutable restriction catalog analysis authority (either orientation)"
        )

    return AssemblyProduct(
        mode="golden_gate",
        sequence=product.sequence,
        circular=product.circular,
        fragments=product.fragments,
        junctions=product.junctions,
        warnings=warnings,
        validation_notes=[
            f"Validated catalog enzyme {enzyme.enzyme_id} overhang length: {enzyme.overhang_length} nt",
            f"Restriction catalog {enzyme.catalog_id} sha256:{enzyme.catalog_sha256}",
        ],
    )
