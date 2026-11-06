"""One coordinate/feature mapper for every assembly-style product.

Promoted unchanged in behavior from the Golden Gate design owner. A mapping is an
output-index -> parent-coordinate list (None for synthesized bases), so slices,
reverse orientation, circular rotation, concatenation and overlap removal are all
the same operation. Compound locations stay in biological part order.
"""
from __future__ import annotations

from typing import Any, Literal, Sequence

from .feature_types import Feature, Mapping, Material, Region
from .types import AssemblyError


def region_positions(region: Region, material: Material) -> list[int]:
    n = len(material.sequence)
    if region.start >= n or region.end > n:
        raise AssemblyError("Selected interval is outside its source")
    if region.wraps_origin:
        if material.topology != "circular" or region.end > region.start:
            raise AssemblyError("Origin-wrapping interval requires a circular source and end <= start")
        return list(range(region.start, n)) + list(range(region.end))
    if region.end <= region.start:
        raise AssemblyError("Selected interval must be nonempty")
    return list(range(region.start, region.end))


def position_mappings(positions: Sequence[int | None], parent: str, strand: Literal[-1, 1] = 1) -> list[Mapping]:
    """Compress coordinate runs; synthesized bases have no invented parent slice."""
    runs: list[Mapping] = []
    i = 0
    while i < len(positions):
        first = positions[i]
        if first is None:
            i += 1
            continue
        j = i + 1
        while j < len(positions) and positions[j] == first + (j - i) * strand:
            j += 1
        a, b = first, first + (j - i - 1) * strand
        runs.append(Mapping(output_start=i, output_end=j, parent_id=parent,
                            parent_start=min(a, b), parent_end=max(a, b) + 1, strand=strand))
        i = j
    return runs


def map_features(features: list[Feature], parent: Material, positions: Sequence[int | None],
                 strand: Literal[-1, 1] = 1) -> list[Feature]:
    lookup: dict[int, list[int]] = {}
    for out, coordinate in enumerate(positions):
        if coordinate is not None:
            lookup.setdefault(coordinate, []).append(out)
    mapped = []
    for feature in features:
        # Ordered location parts, each traversed biologically on its own strand.
        # Reverse orientation must not swap exons.
        chunks = []
        for segment in feature.segments:
            coordinates = region_positions(segment, parent)
            if segment.wraps_origin:
                split = len(parent.sequence) - segment.start
                chunks.extend((coordinates[:split], coordinates[split:]))
            else:
                chunks.append(coordinates)
        original = [p for chunk in chunks for p in (reversed(chunk) if feature.strand == -1 else chunk)]
        retained_indices = [i for i, p in enumerate(original) if p in lookup]
        retained = list(dict.fromkeys(out for i in retained_indices for out in lookup[original[i]]))
        if not retained:
            continue
        direction = (feature.strand or 1) * strand
        segments = []
        start = previous = retained[0]
        for coordinate in retained[1:]:
            if coordinate != previous + direction:
                segments.append(Region(start=min(start, previous), end=max(start, previous) + 1))
                start = coordinate
            previous = coordinate
        segments.append(Region(start=min(start, previous), end=max(start, previous) + 1))
        intact = len(retained_indices) == len(original)
        internal_loss = any(b != a + 1 for a, b in zip(retained_indices, retained_indices[1:]))
        status = feature.status if intact else "disrupted" if internal_loss or feature.status == "disrupted" else "truncated"
        qualifiers = dict(feature.qualifiers)
        codon_start = feature.codon_start
        if feature.type == "CDS" and not intact:
            if feature.strand:
                old = codon_start if codon_start is not None else qualifiers.get("codon_start", 1)
                if isinstance(old, list):
                    old = old[0] if old else None
                if old in (1, 2, 3, "1", "2", "3"):
                    codon_start = (int(old) - 1 - retained_indices[0]) % 3 + 1
                    if "codon_start" in qualifiers:
                        previous_value = qualifiers["codon_start"]
                        qualifiers["codon_start"] = ([str(codon_start)] if isinstance(previous_value, list)
                            else str(codon_start) if isinstance(previous_value, str) else codon_start)
            # The source translation no longer describes this truncated/disrupted feature.
            qualifiers.pop("translation", None)
        mapped.append(feature.model_copy(update={
            "segments": segments, "strand": feature.strand * strand, "status": status,
            "codon_start": codon_start, "qualifiers": qualifiers,
            "frame_preserved": (feature.frame_preserved if feature.frame_preserved is not None else True)
            if intact and feature.type == "CDS" else (False if feature.type == "CDS" else None),
        }))
    return mapped


# ── stored-feature adapters (shelf/revision snapshot dicts <-> mapper models) ──

_KEEP = ("color", "description", "notes", "provenance")


def stored_to_features(stored: Sequence[dict[str, Any]] | None) -> list[Feature]:
    """Stored dicts carry ordered segments; internal ids are the list index."""
    out: list[Feature] = []
    for index, value in enumerate(stored or []):
        segments = value.get("segments") or [{"start": value["start"], "end": value["end"]}]
        qualifiers = value.get("qualifiers")
        if qualifiers is None:
            qualifiers = value.get("notes") if isinstance(value.get("notes"), dict) else {}
        strand = value.get("strand", 1)
        out.append(Feature(
            id=str(index), type=value.get("type") or "misc_feature", name=value.get("name") or "",
            segments=[Region(start=int(s["start"]), end=int(s["end"]), wraps_origin=bool(s.get("wraps_origin", False)))
                      for s in segments],
            strand=-1 if strand == -1 else 1, qualifiers=dict(qualifiers or {}),
            codon_start=value.get("codon_start") if value.get("codon_start") in (1, 2, 3) else None,
        ))
    return out


def features_to_stored(mapped: Sequence[Feature], originals: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Back to stored dicts: ordered non-wrapping segments, min/max start/end, original extras kept."""
    stored: list[dict[str, Any]] = []
    for feature in mapped:
        base = originals[int(feature.id)] if originals is not None and feature.id.isdigit() and int(feature.id) < len(originals) else {}
        segments = [{"start": s.start, "end": s.end} for s in feature.segments]
        record: dict[str, Any] = {
            "id": base.get("id", feature.id), "name": feature.name or base.get("name", ""),
            "type": feature.type, "start": min(s["start"] for s in segments),
            "end": max(s["end"] for s in segments), "strand": -1 if feature.strand == -1 else 1,
            "segments": segments, "qualifiers": dict(feature.qualifiers),
        }
        for key in _KEEP:
            if base.get(key) is not None:
                record[key] = base[key]
        if feature.status != "intact":
            provenance = dict(record.get("provenance") or {})
            provenance["mapping_status"] = feature.status
            record["provenance"] = provenance
        if feature.type == "CDS" and feature.status != "intact" and feature.codon_start is not None \
                and "codon_start" not in record["qualifiers"]:
            record["qualifiers"]["codon_start"] = [str(feature.codon_start)]
        stored.append(record)
    return stored


def map_stored_features(stored: Sequence[dict[str, Any]] | None, sequence: str, topology: str,
                        positions: Sequence[int | None], strand: Literal[-1, 1] = 1) -> list[dict[str, Any]]:
    """Map stored feature dicts through an output->parent position list."""
    if not stored:
        return []
    parent = Material(sequence=sequence, topology=topology, features=[])
    mapped = map_features(stored_to_features(stored), parent, positions, strand)
    return features_to_stored(mapped, list(stored))
