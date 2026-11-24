"""Validated Golden Gate assembly resolved through the immutable restriction catalog."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from services.molbio_ops import reverse_complement
from services.restriction_analysis import AnalysisLimitError, InvalidDNAError, analyze_sequence
from services.restriction_catalog import (
    CatalogUnavailable,
    CatalogView,
    RestrictionRecord,
    catalog_authority,
)

from .common import orient_fragment, require_declared_ends_match_sequence, require_unique_junction_classes
from .ligation import _ligate_oriented_fragments
from .types import (
    AssemblyError,
    AssemblyFragment,
    AssemblyProduct,
    GoldenGateCatalogAuthority,
)


class GoldenGateInvalidDNAError(AssemblyError):
    """Sanitized invalid-DNA boundary for public Golden Gate routes."""


class GoldenGateAnalysisLimitError(AssemblyError):
    """Sanitized resource-limit boundary for public Golden Gate routes."""


class GoldenGateCatalogNotFoundError(AssemblyError):
    """The requested immutable catalog identity is not active."""


class GoldenGateCatalogDigestMismatchError(AssemblyError):
    """The requested immutable catalog bytes are not active."""


@dataclass(frozen=True, slots=True)
class TypeIISEnzyme:
    enzyme_id: str
    canonical_name: str
    site: str
    overhang_length: int
    catalog_id: str
    catalog_sha256: str
    record: RestrictionRecord
    catalog: CatalogView

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
    if not record.golden_gate_compatible:
        if len(motifs) == 1 and motifs[0] == reverse_complement(motifs[0]):
            raise AssemblyError(
                f"Golden Gate enzyme '{identity}' has strand-symmetric recognition {motifs[0]}; "
                "its single directional catalog event cannot specify inward cleavage at both flanks"
            )
        if len(events) == 1 and events[0].overhang_length_nt and not (
            min(events[0].top_offset, events[0].bottom_offset) >= len(motifs[0])
            or max(events[0].top_offset, events[0].bottom_offset) <= 0
        ):
            event = events[0]
            raise AssemblyError(
                f"Golden Gate enzyme '{identity}' cuts at {event.top_offset}/{event.bottom_offset} "
                f"({event.overhang_length_nt} nt {'5-prime' if event.overhang_kind == 'five_prime' else '3-prime'} ends), "
                "overlapping its recognition site; arbitrary site-free scarless preparation is not supported"
            )
        raise AssemblyError(
            f"Golden Gate enzyme '{identity}' cannot remove its recognition site with one external cohesive double-strand cut"
        )
    event = events[0]
    motif = motifs[0]
    return TypeIISEnzyme(
        enzyme_id=record.enzyme_id,
        canonical_name=record.canonical_name,
        site=motif,
        overhang_length=event.overhang_length_nt,
        catalog_id=catalog.catalog_id,
        catalog_sha256=catalog.content_sha256,
        record=record,
        catalog=catalog,
    )


def resolve_golden_gate_enzyme(
    *,
    enzyme_id: str,
    catalog_id: str,
    expected_catalog_sha256: str,
) -> TypeIISEnzyme:
    try:
        catalog = catalog_authority.require()
    except CatalogUnavailable as exc:
        raise AssemblyError("restriction catalog authority is unavailable") from exc
    if catalog_id != catalog.catalog_id:
        raise GoldenGateCatalogNotFoundError("restriction catalog was not found")
    if expected_catalog_sha256 != catalog.content_sha256:
        raise GoldenGateCatalogDigestMismatchError(
            "restriction catalog digest does not match"
        )
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
    try:
        result = analyze_sequence(
            sequence=sequence,
            topology="circular" if circular else "linear",
            catalog=enzyme.catalog,
            records=(enzyme.record,),
            include_possible_sites=True,
        )
    except InvalidDNAError as exc:
        raise GoldenGateInvalidDNAError("Golden Gate fragment DNA is invalid") from exc
    except AnalysisLimitError as exc:
        raise GoldenGateAnalysisLimitError(
            "Golden Gate fragment exceeds supported analysis limits"
        ) from exc
    return (
        result.counts.recognition_site_count_definite
        + result.counts.recognition_site_count_possible
    )


def simulate_golden_gate(
    fragments: list[AssemblyFragment],
    *,
    enzyme: TypeIISEnzyme,
    circular: bool,
) -> AssemblyProduct:
    if not fragments or (len(fragments) == 1 and not circular):
        raise AssemblyError("Golden Gate assembly requires at least two fragments or one circularizing fragment")

    try:
        oriented = [orient_fragment(fragment) for fragment in fragments]
    except AssemblyError:
        raise
    except ValueError as exc:
        raise GoldenGateInvalidDNAError("Golden Gate fragment DNA is invalid") from exc

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
            if end.type != ("sticky_5" if enzyme.record.cleavage.events[0].overhang_kind == "five_prime" else "sticky_3"):
                raise AssemblyError(
                    f"Golden Gate fragment '{fragment.name}' {side_name} end polarity disagrees with catalog cleavage"
                )
            if len(end.overhang) != enzyme.overhang_length:
                raise AssemblyError(
                    f"Golden Gate fragment '{fragment.name}' {side_name} overhang is {len(end.overhang)} nt; "
                    f"{enzyme.name} requires {enzyme.overhang_length} nt overhangs"
                )

    product = _ligate_oriented_fragments(oriented, circular=circular, mode="golden_gate")
    for fragment in fragments:
        require_declared_ends_match_sequence(fragment)
    warnings = list(product.warnings)
    # Advisory classes only: do not reinterpret end metadata or alter ligation.
    # A sequence and its reverse complement describe the same cohesive-end class.
    require_unique_junction_classes(product.junctions)
    for sequence in {j.overhang_sequence for j in product.junctions if j.overhang_sequence}:
        if sequence == reverse_complement(sequence):
            warnings.append(
                f"Junction overhang {sequence} is palindromic; alternative ligations "
                "may occur, depending on the other fragment ends"
            )
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
        golden_gate_authority=GoldenGateCatalogAuthority(
            enzyme_id=enzyme.enzyme_id,
            catalog_id=enzyme.catalog_id,
            catalog_sha256=enzyme.catalog_sha256,
        ),
    )
