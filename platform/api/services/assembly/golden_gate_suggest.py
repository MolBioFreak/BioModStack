"""Target-driven Golden Gate suggestions.

Derives cut windows from the desired target (fixed boundaries, protected regions,
fragment count/size window) and feeds the EXISTING constrained splitter, joint
overhang optimizer and physical design (golden_gate_workflow.run_workflow with a
SplitTask). No second optimizer. Everything here is advisory ranking: internal
sites and missing datasets are reported, never refused.
"""
from __future__ import annotations

import math
from typing import Any, Callable, cast

from pydantic import Field

from services.restriction_catalog import catalog_authority
from .feature_types import Closed, Material, Region
from .golden_gate import resolve_golden_gate_enzyme
from .golden_gate_design import _digest, _scan, preparation_geometry
from .golden_gate_design_types import DesignMaterial, Source
from .golden_gate_workflow import material_for, run_workflow, _homopolymer_junction_rules
from .golden_gate_workflow_types import (
    CutWindow, FidelitySettings, SearchControls, SplitPreparation, SplitTask, WorkflowCandidate,
)
from .types import AssemblyError

# Pryor et al. 2020 datasets by enzyme name (existing empirical owner); others: no data.
DATASET_BY_ENZYME = {"BsaI": "pryor2020-s001", "BsmBI": "pryor2020-s002", "Esp3I": "pryor2020-s003",
                     "BbsI": "pryor2020-s004", "SapI": "pryor2020-s005"}
DEFAULT_ENZYMES = ("BsaI", "BsmBI", "Esp3I", "BbsI", "SapI", "PaqCI", "BtgZI")
NO_DATA = "no empirical data"


class PartSpec(Closed):
    """A supplied part: a source (whole, or `region` of it) with optional fixed junction overhangs (None = choose)."""
    source: Source
    region: CutWindow | None = None              # default: the whole source; end <= start wraps a circular source
    orientation: str = "forward"                 # "forward" | "reverse"
    left_overhang: str | None = None             # top-axis label of the junction on its left
    right_overhang: str | None = None            # top-axis label of the junction on its right
    name: str | None = None


class SuggestRequest(Closed):
    target: Source | None = None
    enzymes: list[str] = Field(default_factory=lambda: list(DEFAULT_ENZYMES))
    fragment_count: int | None = Field(default=None, ge=1)
    fixed_boundaries: list[int] = Field(default_factory=list)
    protected_regions: list[CutWindow] = Field(default_factory=list)
    protected_feature_ids: list[str] = Field(default_factory=list)
    min_fragment_length: int = Field(default=1, ge=1)
    max_fragment_length: int | None = Field(default=None, ge=1)
    target_fragment_length: int | None = Field(default=None, ge=1)
    max_homopolymer_run: int | None = Field(default=None, ge=1, description="Optional junction-only rule: the full cohesive footprint must avoid longer target homopolymer tracts; target bases are unchanged.")
    homopolymer_junction_buffer: int = Field(default=0, ge=0, description="Adjacent target bases excluded on each side of a long tract; active only when max_homopolymer_run is set.")
    avoid_repeats: bool = True
    preparation_kind: str = "synthesis"          # "synthesis" | "pcr"
    clamp: str = "TT"
    spacer: str | None = None                    # default: filler of the catalog spacer length
    terminal_right_fusion: str | None = None     # required for a linear target
    donor_source: Source | None = None           # optional donor: report fragment to retain
    donor_region: CutWindow | None = None
    seed: int = 0
    proposals: int = Field(default=3, ge=1)
    # Supplied-parts and imposed-overhang modes (golden_gate_suggest_parts.py). `parts` assembles the given
    # pieces; `standard_overhangs` on a target imposes the user's overhangs at the cuts instead of target bases.
    parts: list[PartSpec] = Field(default_factory=list)
    vector: Source | None = None                 # destination vector: its closure ends are fixed junctions
    vector_region: CutWindow | None = None       # vector region to keep; default: the largest digest fragment
    standard_overhangs: list[str] | None = None  # ordered; fills the junctions that are not already fixed
    linkers: list[str] = Field(default_factory=list)  # linkers[i]: bases between piece i's left overhang and its payload
    circular: bool = True                        # parts mode without a vector


class EnzymeReport(Closed):
    enzyme_id: str
    name: str
    spacer_length: int | None = None
    overhang_length: int | None = None
    internal_sites: int | None = None
    empirical: str
    status: str


class Proposal(Closed):
    rank: int
    enzyme_id: str
    enzyme_name: str
    empirical: str
    ranking_mode: str
    internal_sites: int
    junction_mode: str = "scarless"              # "scarless" | "imposed" (user's standard set) | "chosen" (optimizer, supplied parts)
    junctions: list[str]
    cuts: list[int]
    fragment_lengths: list[int]
    f_set: float | None = None
    split_request: dict | None = None
    candidate: WorkflowCandidate
    pcr_products: list[dict] = Field(default_factory=list)
    donor_retained_fragment_index: int | None = None
    # Supplied-parts / imposed modes (additive): POSTable AssembleTask for /design, one row per junction,
    # the sequences to order or amplify, the expected product.
    assemble_request: dict | None = None
    junction_slots: list[dict] = Field(default_factory=list)
    prepared_fragments: list[dict] = Field(default_factory=list)
    final_sequence: str | None = None
    notes: list[str] = Field(default_factory=list)


class SuggestResult(Closed):
    proposals: list[Proposal]
    enzymes: list[EnzymeReport]
    diagnostics: list[str] = Field(default_factory=list)


def default_retained_fragment_index(material: Material, enzyme, region: tuple[int, int]) -> int | None:
    """Donor fragment overlapping the chosen target region (largest overlap), not index 0."""
    n = len(material.sequence)
    prepared = DesignMaterial(id="donor", stage="source", transformation="source",
                              sequence=material.sequence, topology=material.topology)
    wanted = set(range(region[0], region[1])) if region[0] <= region[1] else set(range(region[0], n)) | set(range(region[1]))
    best, best_overlap = None, 0
    for fragment in _digest(prepared, enzyme).fragments:
        covered = {i % n for i in range(fragment.top_start_boundary, fragment.top_end_boundary)}
        overlap = len(covered & wanted)
        if overlap > best_overlap:
            best, best_overlap = fragment.fragment_index, overlap
    return best


def _windows(n: int, circular: bool, width: int, request: SuggestRequest, protected: list[tuple[int, int]]):
    count = request.fragment_count
    if count is None:
        goal = request.target_fragment_length or request.max_fragment_length
        count = max(1, math.ceil(n / goal)) if goal else max(2, len(request.fixed_boundaries) + (0 if circular else 1))
        if request.max_fragment_length:
            count = max(count, math.ceil(n / request.max_fragment_length))
        count = min(count, max(1, n // request.min_fragment_length))
    cuts = count if circular else count - 1
    fixed = sorted(set(request.fixed_boundaries))
    cuts = max(cuts, len(fixed))
    ideal = [round(i * n / count) % n for i in (range(count) if circular else range(1, count))]
    free = [p for p in ideal if not fixed or min(abs(p - f) for f in fixed) > width]
    free = sorted(free, key=lambda p: -min([abs(p - f) for f in fixed] or [n]))[:cuts - len(fixed)]
    half = max(width + 1, n // (count * 4))
    slots = sorted([(f, f + 1, f) for f in fixed] + [(max(1 if not circular else 0, p - half), min(n, p + half), None) for p in free])
    return ([CutWindow(start=a, end=b) for a, b, _ in slots], [f for _, _, f in slots])


def _preparation_tails(request: SuggestRequest, enzyme, geometry) -> tuple[str, str]:
    """Choose concrete, site-safe default flanks; explicit authored bytes stay exact."""
    clamp = request.clamp
    if request.spacer is not None:
        return clamp, request.spacer
    length = geometry.spacer_length
    candidates = [("ACGT" * ((length + 3) // 4))[:length], *(base * length for base in "ACGT")]
    for spacer in candidates:
        scan = _scan(clamp + geometry.site + spacer, "linear", enzyme)
        locations = {(o.site_start, o.site_end_unwrapped) for o in scan.occurrences}
        if locations == {(len(clamp), len(clamp) + len(geometry.site))}:
            return clamp, spacer
    return clamp, candidates[0]  # actual digest returns the real geometry reason


def _repeat_preferences(material: Material, width: int, request: SuggestRequest, windows, fixed):
    """Advisory masks from the shared repeat owner; never change authored cuts."""
    from .repeat_breakpoints import RepeatAnalysis
    n = len(material.sequence)
    circular = material.topology == "circular"
    analysis = RepeatAnalysis(material.sequence, circular=circular, overlap=width)
    if not analysis.has_repeats:
        return analysis, windows, [None]
    count = len(windows) + (0 if circular else 1)
    plan = analysis.plan(min_len=request.min_fragment_length,
                         max_len=request.max_fragment_length or max(1, math.ceil(n / count * 1.5)))
    masks = []
    if plan.valid is not None:
        masks.append({p for p in range(n) if plan.valid[(p - plan.origin) % n]} | set(request.fixed_boundaries))
    masks.append({p for p in range(n) if analysis.clean_cuts()[p]} | set(request.fixed_boundaries))
    masks.append(None)  # preferences are not a new scientific refusal
    if not any(p is not None for p in fixed) and plan.valid is not None:
        recommended = sorted((p + plan.origin) % n for p in ([0] if circular else []) + plan.witness)
        if len(recommended) == len(windows):
            half = max(width + 1, n // (count * 4))
            windows = [CutWindow(start=max(0 if circular else 1, p-half), end=min(n, p+half+1)) for p in recommended]
    return analysis, windows, masks


def _repeat_notes(analysis, cuts: list[int], lengths: list[int], circular: bool) -> list[str]:
    if analysis is None or not analysis.has_repeats:
        return []
    notes = []
    if any(not analysis.clean_cuts()[p] for p in cuts):
        notes.append("Repeat compromise: selected boundaries include repeated sequence; exact user cuts are unchanged.")
    else:
        notes.append("Junctions avoid repeated sequence.")
    starts = cuts if circular else [0, *cuts]
    contained, split = 0, 0
    for family in analysis.families:
        owners = set()
        for a, b in family.copies:
            for position in range(a, b):
                owners.add(next((i for i, start in enumerate(starts)
                                 if ((position-start) % analysis.n if circular else position-start) in range(lengths[i])), -1))
        if len(owners) == 1:
            contained += 1
        else:
            split += 1
    if contained:
        notes.append(f"All copies of {contained} repeat group(s) stay within single fragments.")
    if split:
        notes.append(f"Repeat compromise: {split} repeat group(s) are split across fragments under the selected boundaries and size constraints.")
    return notes


def request_sources(request: SuggestRequest) -> list[Source]:
    return [s for s in (request.target, request.donor_source, request.vector, *(p.source for p in request.parts)) if s is not None]


def suggest_golden_gate(request: SuggestRequest, *, resolve_revision: Callable[[str], Material] | None = None) -> SuggestResult:
    if request.parts or request.standard_overhangs is not None:
        from .golden_gate_suggest_parts import suggest_parts
        return suggest_parts(request, resolve_revision=resolve_revision)
    if request.target is None:
        raise AssemblyError("A target or supplied parts are required")
    material = material_for(request.target, resolve_revision)
    n, circular = len(material.sequence), material.topology == "circular"
    catalog = catalog_authority.require()
    protected = [(w.start, w.end) for w in request.protected_regions]
    for feature in material.features:
        if feature.id in request.protected_feature_ids:
            protected += [(s.start, s.end) for s in feature.segments]
    donor = material_for(request.donor_source, resolve_revision) if request.donor_source else None
    reports, found, diagnostics = [], [], []
    for name in request.enzymes:
        try:
            enzyme = resolve_golden_gate_enzyme(enzyme_id=name, catalog_id=catalog.catalog_id,
                                                expected_catalog_sha256=catalog.content_sha256)
            geometry = preparation_geometry(enzyme)
        except AssemblyError as exc:
            reports.append(EnzymeReport(enzyme_id=name, name=name, empirical=NO_DATA, status=f"not usable: {exc}"))
            continue
        dataset = DATASET_BY_ENZYME.get(enzyme.name)
        sites = len(_scan(material.sequence, material.topology, enzyme).occurrences)
        report = dict(enzyme_id=name, name=enzyme.name, spacer_length=geometry.spacer_length,
                      overhang_length=geometry.overhang_length, internal_sites=sites,
                      empirical=dataset or NO_DATA)
        try:
            windows, fixed = _windows(n, circular, geometry.overhang_length, request, protected)
            terminal = request.terminal_right_fusion
            if not circular and terminal is None:
                raise AssemblyError("A linear target needs terminal_right_fusion")
            clamp, spacer = _preparation_tails(request, enzyme, geometry)
            task = SplitTask(task="split_target", target=request.target, enzyme=dict(
                    enzyme_id=name, catalog_id=catalog.catalog_id, catalog_sha256=catalog.content_sha256),
                windows=windows, fixed_positions=fixed,
                fixed_overhangs=[terminal] if (not circular and terminal) else [],
                protected_regions=[CutWindow(start=a, end=b) for a, b in protected],
                min_fragment_length=request.min_fragment_length, max_fragment_length=request.max_fragment_length,
                target_fragment_length=request.target_fragment_length,
                max_homopolymer_run=request.max_homopolymer_run,
                homopolymer_junction_buffer=request.homopolymer_junction_buffer,
                preparation=SplitPreparation(kind=request.preparation_kind, clamp=clamp, spacer=spacer),
                terminal_right_fusion=terminal,
                fidelity=FidelitySettings(dataset_id=dataset),
                search=SearchControls(seed=request.seed, alternatives=request.proposals,
                                      ranking_mode="empirical" if dataset else "lexicographic"))
            analysis, preferred_windows, masks = (_repeat_preferences(material, geometry.overhang_length, request, windows, fixed)
                if request.avoid_repeats else (None, windows, [None]))
            for allowed in masks:
                effective = task.model_copy(update={"windows": preferred_windows if allowed is not None else windows})
                result = run_workflow(effective, resolve_revision=resolve_revision, allowed_positions=allowed)
                if result.solutions:
                    break
        except (AssemblyError, ValueError) as exc:
            reports.append(EnzymeReport(**report, status=f"no proposal: {exc}"))
            continue
        if not result.solutions:
            search = cast(dict[str, Any], result.search_result or {})
            choices = search.get("solutions", [])
            if choices:
                stage = f"none of {len(choices)} searched junction set(s) yielded a physical design"
            else:
                stage = "no junction set returned by this search"
            evidence = list(dict.fromkeys([*result.diagnostics[:2], *search.get("diagnostics", [])]))
            if sites:
                evidence.insert(0, f"target contains {sites} internal recognition site(s)")
            reports.append(EnzymeReport(**report, status="no proposal: " + "; ".join([stage, *evidence])))
            continue
        reports.append(EnzymeReport(**report, status=f"{len(result.solutions)} proposal(s)"))
        donor_index = (default_retained_fragment_index(donor, enzyme, (request.donor_region.start, request.donor_region.end))
                       if donor is not None and request.donor_region is not None else None)
        choices = result.search_result["solutions"]
        for candidate in result.solutions:
            choice = choices[int(candidate.id.rsplit("-", 1)[1])]
            selected_cuts = [c["position"] for c in choice["cuts"]]
            # Replay the selected physical proposal exactly, without running a different advisory search.
            selected_task = task.model_copy(update={"windows": [CutWindow(start=p, end=p+1) for p in selected_cuts],
                                                     "fixed_positions": selected_cuts})
            prepared = {m.id: m for m in candidate.design.materials}
            pcr = []
            for outcome in candidate.design.preparations:
                if outcome.primer_ids:
                    part = next(p for p in candidate.fixed_request.parts if p.id == outcome.part_id)
                    r = part.preparation.region
                    payload = (r.end - r.start) % n if r.wraps_origin else r.end - r.start
                    pcr.append(dict(part_id=outcome.part_id, tailed_length=len(prepared[outcome.prepared_material_id].sequence),
                                    payload_length=payload))
            found.append((sites, 0 if dataset else 1, -(choice.get("log_f_set") or -math.inf) if dataset else 0,
                          Proposal(rank=0, enzyme_id=name, enzyme_name=enzyme.name, empirical=dataset or NO_DATA,
                                   ranking_mode="empirical" if dataset else "lexicographic", internal_sites=sites,
                                   junctions=choice["junctions"], cuts=[c["position"] for c in choice["cuts"]],
                                   fragment_lengths=choice["fragment_lengths"], f_set=choice.get("f_set"),
                                   split_request=selected_task.model_dump(mode="json"), candidate=candidate, pcr_products=pcr,
                                   notes=_repeat_notes(analysis, selected_cuts, choice["fragment_lengths"], circular) +
                                       _homopolymer_junction_rules(material, geometry.overhang_length,
                                           request.max_homopolymer_run, request.homopolymer_junction_buffer)[1] +
                                       (["For this 3-prime enzyme, terminal_right_fusion supplies the external left bottom-strand protrusion; target top-strand bases are unchanged."]
                                        if not circular and geometry.polarity == "three_prime" else []),
                                   donor_retained_fragment_index=donor_index)))
    found.sort(key=lambda x: x[:3])
    proposals = [p.model_copy(update={"rank": i + 1}) for i, (*_, p) in enumerate(found[:request.proposals])]
    if not proposals:
        diagnostics.append("No enzyme returned a physical split proposal in this search; see per-enzyme status")
    return SuggestResult(proposals=proposals, enzymes=reports, diagnostics=diagnostics)
