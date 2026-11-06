"""Features and primer sites carried onto assembly and PCR products.

One output->parent position list per contributing fragment, handed to the shared
mapper in ``feature_mapping``. Nothing here decides whether a product may be saved:
a feature that cannot be mapped is left off the product, never turned into a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from services.molbio_ops import PCRProductResult, _resolve_primer_binding_sites_canonical

from .common import reverse_complement_end
from .feature_mapping import map_stored_features
from .types import AssemblyError, AssemblyProduct, OrientedFragment


@dataclass
class FragmentSource:
    """Where one fragment's bases came from, in the coordinates its features use."""
    stored: list[dict[str, Any]] | None
    sequence: str                # parent material (only its length matters for inline fragments)
    topology: str                # "linear" | "circular"
    coords: list[int]            # parent coordinate of each top-strand base, before orientation


def slice_coordinates(length: int, start: int | None, end: int | None, wraps: bool) -> list[int]:
    if start is None or end is None:
        return list(range(length))
    if wraps:
        return list(range(start, length)) + list(range(end))
    return list(range(start, end))


def _original_length(fragment: OrientedFragment) -> int:
    """Top-strand length before orientation (inverse of ``orient_fragment``)."""
    if fragment.orientation != "reverse":
        return len(fragment.sequence)
    left, right = reverse_complement_end(fragment.right_end), reverse_complement_end(fragment.left_end)
    n = len(fragment.sequence)
    n -= len(left.overhang) if left and left.protruding_strand == "bottom" else 0
    n -= len(right.overhang) if right and right.protruding_strand == "bottom" else 0
    n += len(left.overhang) if left and left.protruding_strand == "top" else 0
    n += len(right.overhang) if right and right.protruding_strand == "top" else 0
    return n


def inline_original_length(fragment: OrientedFragment) -> int:
    return _original_length(fragment)


def oriented_coordinates(fragment: OrientedFragment, coords: list[int]) -> tuple[list[int | None], int]:
    """Parent coordinate of every base of the oriented fragment, and the strand sign."""
    if fragment.orientation != "reverse":
        return list(coords), 1
    # Mirror ``orient_fragment``: drop top protrusions, add bottom ones, then reverse.
    left, right = reverse_complement_end(fragment.right_end), reverse_complement_end(fragment.left_end)
    start = len(left.overhang) if left and left.protruding_strand == "top" else 0
    stop = len(coords) - (len(right.overhang) if right and right.protruding_strand == "top" else 0)
    out: list[int | None] = list(coords[start:stop])
    if left and left.protruding_strand == "bottom":
        out = [None] * len(left.overhang) + out
    if right and right.protruding_strand == "bottom":
        out += [None] * len(right.overhang)
    out.reverse()
    return out, -1


def output_layout(product: AssemblyProduct) -> list[list[int]] | None:
    """Product index of each base of each oriented fragment.

    Ligation and Golden Gate concatenate. Gibson simulation removes each junction
    overlap (and the closing one on a circle); a designed Gibson product is the plain
    concatenation of its source pieces.
    """
    lengths = [len(f.sequence) for f in product.fragments]
    total, n = sum(lengths), len(product.sequence)
    if product.mode != "gibson" or n == total:
        if n != total:
            return None
        layout, pos = [], 0
        for length in lengths:
            layout.append(list(range(pos, pos + length)))
            pos += length
        return layout
    overlaps = [j.overlap_length for j in product.junctions]
    layout, pos = [], 0
    for k, length in enumerate(lengths):
        overlap = overlaps[k - 1] if k else 0
        start = pos - overlap
        layout.append(list(range(start, start + length)))
        pos = start + length
    closing = overlaps[len(lengths) - 1] if product.circular and len(overlaps) == len(lengths) else 0
    if closing:
        layout[-1][len(layout[-1]) - closing:] = list(range(closing))
        pos -= closing
    return layout if pos == n else None


def _normalize(stored: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Legacy start>end features without segments become an origin-wrapping segment."""
    out = []
    for value in stored or []:
        if not isinstance(value, dict) or "start" not in value and not value.get("segments"):
            continue
        if not value.get("segments") and value["start"] > value["end"]:
            value = {**value, "segments": [{"start": value["start"], "end": value["end"], "wraps_origin": True}]}
        out.append(value)
    return out


def _map_each(stored: list[dict[str, Any]], sequence: str, topology: str,
              positions: Sequence[int | None], strand: int) -> list[dict[str, Any]]:
    try:
        return map_stored_features(stored, sequence, topology, positions, strand)  # type: ignore[arg-type]
    except (AssemblyError, ValueError, KeyError, TypeError):
        mapped = []
        for feature in stored:
            try:
                mapped.extend(map_stored_features([feature], sequence, topology, positions, strand))  # type: ignore[arg-type]
            except (AssemblyError, ValueError, KeyError, TypeError):
                continue  # an unreadable annotation is left off the product, not a stop
        return mapped


def map_product_features(product: AssemblyProduct, sources: Sequence[FragmentSource | None]) -> list[dict[str, Any]]:
    """Stored-feature dicts for the product, in product coordinates."""
    layout = output_layout(product)
    if layout is None:
        return []
    size = len(product.sequence)
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for fragment, source, outputs in zip(product.fragments, sources, layout):
        if source is None:
            continue
        stored = _normalize(source.stored)
        if not stored:
            continue
        coords, strand = oriented_coordinates(fragment, source.coords)
        if len(coords) != len(outputs):
            continue
        positions: list[int | None] = [None] * size
        for output, coordinate in zip(outputs, coords):
            positions[output] = coordinate
        for feature in _map_each(stored, source.sequence, source.topology, positions, strand):
            if str(feature.get("id")) in used:
                feature["id"] = f"{fragment.id}:{feature.get('id')}"
            used.add(str(feature.get("id")))
            result.append(feature)
    return result


# ── PCR ───────────────────────────────────────────────────────────────────────

def _primer_entry(amplicon: str, *, key: str, name: str, full: str, annealing: str, tail: str,
                  reverse: bool, expected: int, tm: float | None, settings: dict[str, Any] | None) -> dict[str, Any]:
    sites = _resolve_primer_binding_sites_canonical(amplicon, annealing, reverse, False, "dna", len(annealing)) \
        if annealing else []
    site = next((s for s in sites if s.start == expected), sites[0] if sites else None)
    strand = -1 if reverse else 1
    entry: dict[str, Any] = {
        "id": key, "name": name, "sequence": full, "sequence_type": "dna", "strand": strand, "tm": tm,
        "notes": {"annealing_sequence": annealing, "tail_sequence": tail},
    }
    if settings:
        entry.update(tm_algorithm=settings.get("algorithm"), tm_salt_correction=settings.get("salt_correction"),
                     tm_settings=settings)
    if site is None:
        entry.update(start=0, end=0, sites=[], binding_status="needs_review",
                     binding_warning="Primer binding could not be placed on this product.")
    else:
        entry.update(start=site.start, end=site.end, sites=[{"start": site.start, "end": site.end, "strand": strand}])
    return entry


def pcr_product_content(template_features: Sequence[dict[str, Any]] | None, template_sequence: str,
                        circular: bool, product: PCRProductResult, forward: str, reverse: str, *,
                        forward_tm: float | None = None, reverse_tm: float | None = None,
                        tm_settings: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Template features mapped onto the amplicon, plus its two primer sites."""
    amplicon, n = product.sequence, len(template_sequence)
    f_over, r_over = product.forward_binding.overhang_length, product.reverse_binding.overhang_length
    template = [(product.start + i) % n for i in range(product.template_span)]
    positions: list[int | None] = [None] * f_over + template + [None] * r_over
    features = _map_each(_normalize(template_features), template_sequence,
                         "circular" if circular else "linear", positions, 1) if template_features else []
    f_anneal, r_anneal = forward[f_over:], reverse[r_over:]
    primers = [
        _primer_entry(amplicon, key="pcr-forward", name="Forward primer", full=forward, annealing=f_anneal,
                      tail=forward[:f_over], reverse=False, expected=f_over, tm=forward_tm, settings=tm_settings),
        _primer_entry(amplicon, key="pcr-reverse", name="Reverse primer", full=reverse, annealing=r_anneal,
                      tail=reverse[:r_over], reverse=True, expected=len(amplicon) - len(r_anneal), tm=reverse_tm,
                      settings=tm_settings),
    ]
    return features, primers
