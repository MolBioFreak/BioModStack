"""Gibson/HiFi suggestions: cover a target with supplied-source regions and new DNA.

Each proposal is a complete ``GibsonDesignRequest`` that the existing design route
accepts unchanged. Matched regions become PCR fragments (or are reused as-is when
they equal a supplied linear fragment); new DNA becomes plain synthesis fragments
because the neighbouring PCR primers carry the overlap tails. The design is run for
every variant, and its product must equal the target before the variant is offered.
Sources are only read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from .common import orient_fragment
from .feature_mapping import region_positions, stored_to_features
from .feature_types import Material
from .pydna_gibson import design_gibson
from .region_match import Matcher, Piece, Source, cover_target, reverse_complement
from .types import AssemblyError, AssemblyFragment

SMALL_MATCH = 150  # matched pieces shorter than this may be absorbed into synthesis (variant 3)
MAX_SYNTHESIS = 3000


class TargetIn(BaseModel):
    name: str = "target"
    sequence: str
    circular: bool = True
    features: list[dict[str, Any]] | None = None


class SourceIn(BaseModel):
    id: str
    name: str = ""
    sequence: str
    circular: bool = False
    source_sequence_id: str | None = None
    source_revision: int | None = None
    features: list[dict[str, Any]] | None = None


class SuggestRequest(BaseModel):
    target: TargetIn
    sources: list[SourceIn] = Field(default_factory=list)
    min_match: int = Field(default=30, ge=12)
    overlap: int = Field(default=25, ge=15, le=80)
    target_tm: float = Field(default=58.0, ge=45.0, le=72.0)
    min_anneal: int = Field(default=14, ge=10, le=30)
    fixed_boundaries: list[int] | None = None
    protected_feature_ids: list[str] | None = None
    allow_synthesis: bool = True
    max_pcr_length: int | None = Field(default=None, ge=100)
    limit: int = Field(default=3, ge=1, le=3)


class SourceRef(BaseModel):
    id: str
    start: int
    end: int
    strand: int
    wraps_origin: bool


class FragmentOut(BaseModel):
    id: str
    name: str
    preparation: str
    orientation: str
    source: SourceRef | None = None
    sequence: str | None = None


class FeatureReport(BaseModel):
    kept: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    clipped: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    rank: int
    summary: str
    counts: dict[str, int]
    fragments: list[FragmentOut]
    design_request: dict[str, Any]
    features: FeatureReport
    notes: list[str] = Field(default_factory=list)


class SuggestResult(BaseModel):
    proposals: list[Proposal]
    diagnostics: list[str] = Field(default_factory=list)


def _clean(sequence: str) -> str:
    return re.sub(r"[^A-Za-z]", "", sequence).upper()


@dataclass
class Frag:
    kind: str  # pcr | synthesis | existing
    start: int
    end: int
    piece: Piece | None = None
    sequence: str | None = None
    source: SourceIn | None = None


def _label(feature: dict[str, Any], index: int) -> tuple[str, str]:
    return str(feature.get("id", index)), feature.get("name") or feature.get("type") or "feature"


def _feature_spans(stored: list[dict[str, Any]] | None, sequence: str, circular: bool) -> list[tuple[str, str, list[int]]]:
    material = Material(sequence=sequence, topology="circular" if circular else "linear", features=[])
    spans = []
    for index, feature in enumerate(stored_to_features(stored)):
        key, name = _label((stored or [])[index], index)
        positions = [p for segment in feature.segments for p in region_positions(segment, material)]
        spans.append((key, name, positions))
    return spans


def _snap_protected(pieces: list[Piece], protected: list[tuple[int, int]], notes: list[str]) -> list[Piece]:
    """A boundary inside a protected feature moves to the feature edge that gives the
    feature to new DNA (a match is only ever shortened, never extended)."""
    pieces = list(pieces)
    for fs, fe in protected:
        for i in range(len(pieces) - 1):
            left, right = pieces[i], pieces[i + 1]
            b = left.end
            if not fs < b < fe or left.is_match == right.is_match:
                continue
            target_b = fs if left.is_match else fe
            if not left.start < target_b < right.end:
                continue
            pieces[i], pieces[i + 1] = left.sub(left.start, target_b), right.sub(target_b, right.end)
            notes.append(f"Boundary {b} moved to {target_b} to keep a protected feature whole")
    return [p for p in pieces if p.end > p.start]


def _split(pieces: list[Piece], cuts: list[int], cap: int | None) -> list[Piece]:
    """Cut matched pieces at fixed boundaries and into PCR-sized chunks. New DNA stays whole."""
    out: list[Piece] = []
    for piece in pieces:
        if not piece.is_match:
            out.append(piece)
            continue
        edges = sorted({piece.start, piece.end, *(c for c in cuts if piece.start < c < piece.end)})
        for a, b in zip(edges, edges[1:]):
            count = -(-(b - a) // cap) if cap else 1
            step = -(-(b - a) // count)
            out.extend(piece.sub(x, min(x + step, b)) for x in range(a, b, step))
    return out


def _merge_new(frags: list[Frag], target: str) -> list[Frag]:
    out: list[Frag] = []
    for frag in frags:
        if frag.kind == "synthesis" and out and out[-1].kind == "synthesis":
            out[-1] = Frag("synthesis", out[-1].start, frag.end, sequence=target[out[-1].start:frag.end])
        else:
            out.append(frag)
    return out


def _fragments(pieces: list[Piece], target: str, sources: dict[str, SourceIn], variant: int) -> list[Frag]:
    frags: list[Frag] = []
    for piece in pieces:
        if not piece.is_match:
            frags.append(Frag("synthesis", piece.start, piece.end, sequence=piece.sequence))
            continue
        source = sources[piece.source or ""]
        whole = not source.circular and piece.end - piece.start == len(source.sequence)
        kind = "existing" if variant == 2 and whole else "pcr"
        frags.append(Frag(kind, piece.start, piece.end, piece=piece, source=source))
    if variant == 3:
        for i, frag in enumerate(frags):
            near_new = any(0 <= j < len(frags) and frags[j].kind == "synthesis"
                           for j in (i - 1, i + 1))
            if frag.kind == "pcr" and frag.end - frag.start < SMALL_MATCH and near_new:
                frags[i] = Frag("synthesis", frag.start, frag.end, sequence=target[frag.start:frag.end])
        frags = _merge_new(frags, target)
        if any(f.kind == "synthesis" and f.end - f.start > MAX_SYNTHESIS for f in frags):
            return []
    return frags


def _build_request(frags: list[Frag], target: str, req: SuggestRequest) -> tuple[dict[str, Any], list[FragmentOut]]:
    n = len(target)
    overlap = req.overlap + req.overlap % 2
    fragments, out = [], []
    for i, frag in enumerate(frags):
        fid = f"f{i + 1}"
        entry: dict[str, Any] = {"id": fid, "preparation": "pcr" if frag.kind == "pcr" else "ready_linear"}
        ref = None
        if frag.kind == "synthesis":
            a, b = frag.start, frag.end
            if i > 0 and frags[i - 1].kind == "existing":
                a -= overlap
            if i + 1 < len(frags) and frags[i + 1].kind == "existing":
                b += overlap
            if req.target.circular and i == 0 and frags[-1].kind == "existing":
                a -= overlap
            if req.target.circular and i == len(frags) - 1 and frags[0].kind == "existing":
                b += overlap
            doubled = target + target if req.target.circular else target
            sequence = doubled[a:b] if a >= 0 else (target + target)[n + a:n + b]
            entry.update(name=f"Synthetic {i + 1}", sequence=sequence, orientation="forward")
            out.append(FragmentOut(id=fid, name=entry["name"], preparation="synthesis", orientation="forward",
                                   sequence=sequence))
        else:
            piece, source = frag.piece, frag.source
            assert piece is not None and source is not None
            if frag.kind == "existing":
                sequence, start, end, wraps = source.sequence, 0, len(source.sequence), False
            else:
                start, end, wraps = piece.region()
                sequence = (source.sequence[start:] + source.sequence[:end]) if wraps else source.sequence[start:end]
            orientation = "forward" if piece.strand == 1 else "reverse"
            name = source.name or source.id
            entry.update(name=name if frag.kind == "existing" else f"{name} {start + 1}-{end}",
                         sequence=sequence, orientation=orientation)
            if source.circular:
                entry["circular"] = False
            if source.source_sequence_id:
                entry.update(source_sequence_id=source.source_sequence_id, source_revision=source.source_revision)
                if frag.kind != "existing":
                    entry.update(source_start=start, source_end=end, source_wraps_origin=wraps)
            ref = SourceRef(id=source.id, start=start, end=end, strand=piece.strand, wraps_origin=wraps)
            out.append(FragmentOut(id=fid, name=entry["name"], preparation=frag.kind, orientation=orientation,
                                   source=ref, sequence=sequence if frag.kind == "existing" else None))
        fragments.append(entry)
    # pydna splits the overlap between two PCR neighbours, so an odd value comes out one nt short.
    request = {"fragments": fragments, "circular": req.target.circular, "overlap": req.overlap + req.overlap % 2,
               "target_tm": req.target_tm, "min_anneal": req.min_anneal}
    return request, out


def _same_product(a: str, b: str, circular: bool) -> bool:
    return a == b or (circular and len(a) == len(b) and a in b + b)


def _evaluate(request: dict[str, Any], target: str, circular: bool):
    fragments = [AssemblyFragment(id=f["id"], name=f["name"], sequence=f["sequence"],
                                  orientation=f["orientation"]) for f in request["fragments"]]
    result = design_gibson(fragments, preparations=[f["preparation"] for f in request["fragments"]],
                           circular=circular, overlap=request["overlap"], target_tm=request["target_tm"],
                           min_anneal=request["min_anneal"])
    if not _same_product(result.candidates[0].product.sequence, target, circular):
        raise AssemblyError("designed fragments do not reproduce the target")
    return result


def _feature_report(req: SuggestRequest, cover_pieces: list[Piece], rotation: int, target: str,
                    sources: dict[str, SourceIn]) -> FeatureReport:
    report = FeatureReport()

    def sort(name: str, positions: list[int], used: set[int]) -> None:
        hit = sum(p in used for p in positions)
        (report.kept if hit == len(positions) else report.clipped if hit else report.removed).append(name)

    used: dict[str, set[int]] = {}
    for piece in cover_pieces:
        if piece.is_match:
            used.setdefault(piece.source or "", set()).update(piece.forward_positions())
    for source in sources.values():
        if source.features:
            for key, name, positions in _feature_spans(source.features, source.sequence, source.circular):
                sort(f"{source.name or source.id}: {name} ({key})", positions, used.get(source.id, set()))
    if req.target.features:
        matched = {t for p in cover_pieces if p.is_match for t in range(p.start, p.end)}
        n = len(target)
        for key, name, positions in _feature_spans(req.target.features, target, req.target.circular):
            rotated = [(p - rotation) % n for p in positions]
            if any(p in matched for p in rotated):
                sort(f"target: {name} ({key})", rotated, matched)
    return report


def suggest_gibson(req: SuggestRequest) -> SuggestResult:
    target = _clean(req.target.sequence)
    if not target:
        raise AssemblyError("Target sequence is empty")
    sources = {s.id: s.model_copy(update={"sequence": _clean(s.sequence)}) for s in req.sources}
    matcher = Matcher([Source(s.id, s.sequence, s.circular) for s in sources.values()], req.min_match)
    n = len(target)
    cuts = [b % n for b in req.fixed_boundaries or []]
    target_features = req.target.features or []
    protected_ids = set(req.protected_feature_ids or [])
    protected_spans = []
    if protected_ids:
        for key, _name, positions in _feature_spans(target_features, target, req.target.circular):
            if key in protected_ids:
                protected_spans.append(positions)
    origin = min(cuts) if cuts and req.target.circular else None
    base = cover_target(target, [], circular=req.target.circular, origin=origin, matcher=matcher)
    rotation = base.rotation
    rotated = target[rotation:] + target[:rotation] if req.target.circular else target
    notes: list[str] = []
    ranges = [(min((p - rotation) % n for p in span), max((p - rotation) % n for p in span) + 1)
              for span in protected_spans if span]
    pieces = _snap_protected(base.pieces, ranges, notes)
    shift = [(c - rotation) % n for c in cuts]
    pieces = _split(pieces, [c for c in shift if c], req.max_pcr_length)
    if not req.allow_synthesis and any(not p.is_match for p in pieces):
        return SuggestResult(proposals=[], diagnostics=["Part of the target is not found in the supplied sources "
                                                        "and synthesis is not allowed"])
    diagnostics: list[str] = []
    if not any(p.is_match for p in pieces):
        diagnostics.append("No supplied source matches the target; whole-target synthesis is planned by the DNA Weaver planner")
    features = _feature_report(req, pieces, rotation, target, sources)
    if req.target.features and any(source.features for source in sources.values()):
        notes.append("Feature report lists source coverage and target match coverage separately; "
                     "the same annotation can appear in both roles, not as duplicate product features.")
    scored = []
    seen: set[str] = set()
    for variant in (1, 2, 3):
        frags = _fragments(pieces, rotated, sources, variant)
        if not frags:
            continue
        request, outs = _build_request(frags, rotated, req)
        key = repr([(f["preparation"], f["sequence"], f["orientation"]) for f in request["fragments"]])
        if key in seen:
            continue
        seen.add(key)
        try:
            result = _evaluate(request, rotated, req.target.circular)
        except AssemblyError as exc:
            diagnostics.append(f"Variant {variant} not offered: {exc}")
            continue
        counts = {"pcr": sum(f.preparation == "pcr" for f in outs),
                  "synthesis": sum(f.preparation == "synthesis" for f in outs),
                  "existing": sum(f.preparation == "existing" for f in outs)}
        synth_bp = sum(len(f.sequence or "") for f in outs if f.preparation == "synthesis")
        tms = [p.tm for p in result.primers]
        overlap_tms = [j.overlap_tm for j in result.candidates[0].product.junctions if j.overlap_tm is not None]
        evidence = max((abs(t - req.target_tm) for t in tms), default=0.0)
        bits = [f"{counts['pcr']} PCR", f"{counts['synthesis']} synthesis ({synth_bp} bp)", f"{counts['existing']} existing"]
        if tms:
            bits.append(f"primer Tm {min(tms):.1f}-{max(tms):.1f} C")
        if overlap_tms:
            bits.append(f"min overlap Tm {min(overlap_tms):.1f} C")
        scored.append(((synth_bp, counts["pcr"] + counts["synthesis"], evidence), Proposal(
            rank=0, summary=", ".join(bits), counts=counts, fragments=outs, design_request=request,
            features=features, notes=list(notes))))
    scored.sort(key=lambda item: item[0])
    proposals = [p.model_copy(update={"rank": i + 1}) for i, (_, p) in enumerate(scored[:req.limit])]
    return SuggestResult(proposals=proposals, diagnostics=diagnostics)
