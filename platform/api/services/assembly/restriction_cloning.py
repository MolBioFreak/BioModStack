"""Target-driven restriction-cloning proposals (observations, never a gate).

For a backbone and an insert, list enzyme pairs that cut the backbone once each and give the
insert matching ends, using the existing restriction analysis (site scan) and digest (real
duplex ends). Each proposal is then built with the existing ligation simulator, so the reported
orientation, self-ligation background and per-junction site regeneration come from real ends,
not from a motif table. A site is added to an insert only through an explicit PCR-primer tail;
the source sequences are never edited.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any, Iterable

from .feature_mapping import map_stored_features

from services.molbio_ops import reverse_complement
from services.restriction_analysis import analyze_sequence, normalize_dna
from services.restriction_catalog import CatalogView, RestrictionRecord, catalog_authority
from services.restriction_digest import DigestFragment, DigestGeometryError, simulate_digest

from .common import overhangs_compatible
from .junction_sites import observe_junction_sites, resolve_records
from .ligation import junction_seal, simulate_ligation
from .physical_ends import digest_end_to_fragment_end
from .types import AssemblyError, AssemblyFragment, FragmentEnd

CLAMP = "GCGCGC"  # 6-nt spacer so cutting near a PCR-product end is possible; shown in the proposal
ALTERNATIVES = [
    {"method": "overlap_assembly", "note": "Gibson/HiFi: add 20-30 bp overlaps by PCR or synthesis; no restriction sites needed."},
    {"method": "pcr_primer_tails", "note": "Add chosen restriction sites to the insert through primer 5' tails (clamp + site outside the payload)."},
    {"method": "synthesis", "note": "Order the insert with the flanking sites built into the synthetic sequence."},
]


def _receipt(catalog: CatalogView, sequence: str, topology: str) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = {k: v for k, v in catalog_authority.readiness().items() if k not in {"required", "ready", "status"}}
    receipt["digest_enabled"] = True
    source = {"kind": "inline_dna", "name": "restriction-cloning", "sequence_id": None, "revision_id": None,
              "revision_number": None, "content_sha256": sha256(sequence.encode("ascii")).hexdigest(),
              "content_length": len(sequence), "topology": topology}
    return source, receipt


def _digest(catalog: CatalogView, sequence: str, topology: str, records: Iterable[RestrictionRecord]):
    records = list(records)
    source, receipt = _receipt(catalog, sequence, topology)
    return simulate_digest(sequence=sequence, topology=topology, catalog=catalog, records=records,
                           selected_enzyme_ids=[r.enzyme_id for r in records], source_receipt=source,
                           catalog_receipt=receipt, persisted_identity=False)


def _candidate_records(catalog: CatalogView, names: list[str]) -> list[RestrictionRecord]:
    """One representative per isoschizomer group (commercial first, then name)."""
    if names:
        _catalog, records = resolve_records(names, catalog)
        return records
    pool = [r for r in catalog.ordered_records
            if r.enzyme_kind == "double_strand_endonuclease" and r.analysis_capability == "digest_simulation"
            and r.cleavage.status == "known_double_strand" and r.supplier_provenance.reported_commercial
            and len(r.cleavage.events) == 1]
    best: dict[str, RestrictionRecord] = {}
    # Within an isoschizomer group prefer the widely supplied member, then the name.
    for record in sorted(pool, key=lambda r: (-_supplier_count(r), r.canonical_name.lower())):
        best.setdefault(record.relationships.isoschizomer_group_id, record)
    return list(best.values())


def _supplier_count(record: RestrictionRecord) -> int:
    return len(record.supplier_provenance.historical_supplier_codes)


def _supplier_tier(record: RestrictionRecord) -> int:
    """0 = commonly stocked cloning enzyme (8+ historical supplier codes) ... 3 = no supplier codes recorded.

    Taken from the loaded catalog's supplier provenance; availability itself is not evaluated.
    """
    count = _supplier_count(record)
    return 0 if count >= 8 else 1 if count >= 3 else 2 if count >= 1 else 3


def _is_classic(record: RestrictionRecord) -> bool:
    """Well supplied, a 6+ base unambiguous recognition site, and cohesive (non-blunt) ends (from the catalog record)."""
    site = record.recognition.site_iupac
    events = record.cleavage.events
    return (_supplier_tier(record) == 0 and record.recognition.length_bp >= 6 and len(site) >= 6
            and set(site.upper()) <= set("ACGT")
            and bool(events) and all(e.overhang_kind != "blunt" and e.overhang_length_nt > 0 for e in events))


def _enzyme_rank(record: RestrictionRecord) -> tuple[int, int]:
    """(0 classic cloning enzyme | 1 well supplied but 4-base/blunt/ambiguous | 2 poorly supplied, supplier tier)."""
    tier = _supplier_tier(record)
    return (0 if _is_classic(record) else 1 if tier == 0 else 2, tier)


def _pair_tier(*records: RestrictionRecord) -> tuple[int, int]:
    return max(_enzyme_rank(r) for r in records)


def _proposal_rank(proposal: dict[str, Any], by_name: dict[str, RestrictionRecord]) -> tuple[int, int]:
    return _pair_tier(*(by_name[n] for n in proposal["vector_enzymes"] + proposal["insert_enzymes"]))


def _same_circular_product(a: str, b: str) -> bool:
    return len(a) == len(b) and (a in b + b or reverse_complement(a) in b + b)


def _is_duplicate(result: dict[str, Any], kept: Iterable[dict[str, Any]]) -> bool:
    """Same unordered vector pair, insert pair, orientation and circular product (rotation or reverse complement)."""
    key = (frozenset(result["vector_enzymes"]), frozenset(result["insert_enzymes"]), result["insert_orientation"])
    return any((frozenset(k["vector_enzymes"]), frozenset(k["insert_enzymes"]), k["insert_orientation"]) == key
               and _same_circular_product(result["product_sequence"], k["product_sequence"]) for k in kept)


def _single_cutters(sequence: str, topology: str, catalog: CatalogView, records: list[RestrictionRecord]) -> dict[str, dict[str, Any]]:
    """Enzymes with exactly one definite site and one complete cut. Value: cut position and end signature."""
    return {k: v[0] for k, v in _cutters(sequence, topology, catalog, records, exactly_one=True).items()}


def _cutters(sequence: str, topology: str, catalog: CatalogView, records: list[RestrictionRecord],
             *, exactly_one: bool) -> dict[str, list[dict[str, Any]]]:
    """Per enzyme, each definite site with one complete cut (cut position, end signature, site start)."""
    analysis = analyze_sequence(sequence=sequence, topology=topology, catalog=catalog, records=records,
                                include_possible_sites=False)
    per: dict[str, list] = {}
    for occurrence in analysis.occurrences:
        if occurrence.certainty == "definite":
            per.setdefault(occurrence.enzyme_id, []).append(occurrence)
    counts = {s.enzyme_id: s.recognition_site_count_definite for s in analysis.enzyme_summaries}
    out: dict[str, list[dict[str, Any]]] = {}
    for enzyme_id, occurrences in per.items():
        if exactly_one and (len(occurrences) != 1 or counts.get(enzyme_id, 1) != 1):
            continue
        sites = []
        for occurrence in occurrences:
            events = [e for e in occurrence.double_strand_events if e.status == "complete"]
            if len(events) != 1 or events[0].top_boundary is None:
                sites = []
                break
            event = events[0]
            ovh = event.overhang_sequence_5to3 or ""
            key = ("blunt",) if event.overhang_kind == "blunt" else (event.overhang_kind, min(ovh, reverse_complement(ovh)))
            sites.append({"cut": int(event.top_boundary), "key": key, "site_start": occurrence_start(occurrence)})
        if sites:
            out[enzyme_id] = sites
    return out


def occurrence_start(occurrence: Any) -> int:
    return int(occurrence.site_start)


def _single_enzyme_pairs(vector_single: dict[str, dict[str, Any]], insert_multi: dict[str, list[dict[str, Any]]],
                         by_id: dict[str, RestrictionRecord], explicit: bool, start: int, end: int) -> list[tuple[str, str]]:
    """(vector enzyme, insert enzyme) with equal end signatures whose insert sites flank the payload."""
    out = []
    for v_id, v in vector_single.items():
        for w_id, sites in insert_multi.items():
            if sites[0]["key"] != v["key"]:
                continue
            if explicit:
                flanked = any(s["cut"] <= start for s in sites) and any(s["cut"] >= end for s in sites)
            else:
                flanked = len(sites) == 2 and sites[0]["cut"] < sites[1]["cut"]
            if flanked:
                out.append(((_pair_tier(by_id[v_id], by_id[w_id]), v_id != w_id, by_id[v_id].canonical_name, by_id[w_id].canonical_name), v_id, w_id))
    out.sort(key=lambda t: t[0])
    return [(v, w) for _k, v, w in out]


def _backbone_piece(fragments: tuple[DigestFragment, ...], region: tuple[int, int] | None) -> DigestFragment | None:
    if len(fragments) != 2:
        return None
    if region is not None:
        def covers(fragment: DigestFragment) -> bool:
            return any(s < region[1] and region[0] < e for s, e in fragment.source_segments)
        keep = [f for f in fragments if not covers(f)]
        if len(keep) == 1:
            return keep[0]
    return max(fragments, key=lambda f: len(f.top_strand_sequence))


def _insert_piece(fragments: tuple[DigestFragment, ...]) -> DigestFragment | None:
    middle = [f for f in fragments if f.left_end.enzyme_created and f.right_end.enzyme_created]
    return middle[0] if len(middle) == 1 else None


def _piece_features(piece: DigestFragment, sequence: str, topology: str,
                    features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not features:
        return []
    positions = [i for start, end in piece.source_segments for i in range(start, end)]
    return map_stored_features(features, sequence, topology, positions)


def _to_fragment(piece: DigestFragment, fid: str, name: str, orientation: str = "forward",
                 features: list[dict[str, Any]] | None = None) -> AssemblyFragment:
    ends = {"left": list(piece.left_end.contributing_enzyme_ids), "right": list(piece.right_end.contributing_enzyme_ids)}
    return AssemblyFragment(id=fid, name=name, sequence=piece.top_strand_sequence, orientation=orientation,  # type: ignore[arg-type]
                            left_end=digest_end_to_fragment_end(piece.left_end),
                            right_end=digest_end_to_fragment_end(piece.right_end), features=features,
                            metadata={"end_enzymes": ends, "digest_fragment": piece.model_dump(mode="json")})


def _try(fragments: list[AssemblyFragment]):
    try:
        return simulate_ligation(fragments, circular=True)
    except AssemblyError:
        return None


def _distinct(records: Iterable[RestrictionRecord]) -> list[RestrictionRecord]:
    return list({r.enzyme_id: r for r in records}.values())


def _evaluate(catalog: CatalogView, backbone: str, btopo: str, region, insert: str,
              vector_enzymes: tuple[RestrictionRecord, RestrictionRecord],
              insert_enzymes: tuple[RestrictionRecord, RestrictionRecord],
              vector_phosphate: str, *, insert_topology: str = "linear",
              payload_region: tuple[int, int] | None = None,
              backbone_features: list[dict[str, Any]] | None = None,
              insert_features: list[dict[str, Any]] | None = None,
              selection: dict[str, Any] | None = None,
              force_orientation: str | None = None) -> dict[str, Any] | None:
    # One enzyme used for both vector ends: the single cut linearizes the backbone (one fragment).
    try:
        vector_digest = _digest(catalog, backbone, btopo, _distinct(vector_enzymes))
        vector = _backbone_piece(vector_digest.fragments, region) if len(vector_digest.fragments) != 1 else vector_digest.fragments[0]
        insert_digest = _digest(catalog, insert, insert_topology, _distinct(insert_enzymes))
    except DigestGeometryError:
        # The digest engine cannot model this enzyme/site geometry (for example a cut at a sequence end).
        # In a candidate search that pair simply produces no plan; it must not fail the whole request.
        return None
    payload = _insert_piece(insert_digest.fragments)
    if insert_topology == "circular" or payload_region is not None:
        def contains_payload(fragment):
            if payload_region is None:
                return not fragment.wraps_origin
            start, end = payload_region
            return sum(max(0, min(b, end) - max(a, start)) for a, b in fragment.source_segments) == end - start
        choices = [f for f in insert_digest.fragments if contains_payload(f)]
        payload = choices[0] if len(choices) == 1 else None
    if selection is not None:
        vector = next((f for f in vector_digest.fragments if f.fragment_index == selection["vector_fragment_index"]), None)
        payload = next((f for f in insert_digest.fragments if f.fragment_index == selection["insert_fragment_index"]), None)
    if vector is None or payload is None:
        return None
    vector_fragment = _to_fragment(vector, "vector", "backbone", features=_piece_features(vector, backbone, btopo, backbone_features or []))
    insert_fragment = _to_fragment(payload, "insert", "insert", features=_piece_features(payload, insert, insert_topology, insert_features or []))
    if vector_phosphate != "unknown":
        for end in (vector_fragment.left_end, vector_fragment.right_end):
            end.five_prime_phosphate = vector_phosphate  # type: ignore[assignment]
    forward = _try([vector_fragment, insert_fragment])
    reverse_fragment = replace(insert_fragment, orientation="reverse")
    reverse = _try([vector_fragment, reverse_fragment])
    if forward is None and reverse is None:
        return None
    orientation = "either" if forward and reverse else "forward" if forward else "reverse"
    chosen_orientation = (selection["insert_orientation"] if selection is not None else force_orientation
                          or ("forward" if forward else "reverse"))
    product = forward if chosen_orientation == "forward" else reverse
    if product is None:
        return None
    chosen_insert = insert_fragment if chosen_orientation == "forward" else reverse_fragment
    seal, _notes = junction_seal(vector_fragment.right_end, vector_fragment.left_end)
    self_ok, _why = overhangs_compatible(vector_fragment.right_end, vector_fragment.left_end)
    names = sorted({r.canonical_name for r in (*vector_enzymes, *insert_enzymes)})
    observed = observe_junction_sites(product, names) if len(names) <= 4 else []
    junctions = [{"between": f"{j['left_fragment_id']} → {j['right_fragment_id']}",
                  "regenerated": j["regenerated"], "destroyed": j["destroyed"], "note": j["note"]} for j in observed]
    directional = orientation != "either" and not self_ok
    if vector_enzymes[0].enzyme_id == vector_enzymes[1].enzyme_id:
        orientation = chosen_orientation  # single-enzyme cloning: one proposal per insert orientation
    return {
        "vector_enzymes": [r.canonical_name for r in vector_enzymes],
        "insert_enzymes": [r.canonical_name for r in insert_enzymes],
        "directional": directional,
        "single_enzyme": vector_enzymes[0].enzyme_id == vector_enzymes[1].enzyme_id,
        "insert_orientation": orientation,
        "vector_ends_match_each_other": self_ok,
        "vector_self_ligation": ("possible" if self_ok else "not possible") + (
            "" if not self_ok else (", reduced (vector 5' ends unphosphorylated)" if seal == "none" else
                                    ", nicked (one 5' phosphate)" if seal == "one_strand" else "")),
        "vector_end_seal": seal,
        "vector_length": len(vector.top_strand_sequence),
        "insert_payload_length": len(payload.top_strand_sequence),
        "product_length": len(product.sequence),
        "product_sequence": product.sequence,
        "vector_source_segments": vector.source_segments,
        "insert_source_segments": payload.source_segments,
        "junction_sites": junctions,
        "assembly_request": {"fragments": [asdict(vector_fragment), asdict(chosen_insert)],
                             "circular": True, "observe_enzymes": names},
        "selection": {"vector_enzymes": [r.enzyme_id for r in vector_enzymes],
                      "insert_enzymes": [r.enzyme_id for r in insert_enzymes],
                      "vector_fragment_index": vector.fragment_index,
                      "insert_fragment_index": payload.fragment_index,
                      "insert_orientation": chosen_orientation},
    }


def _tail_site(record: RestrictionRecord) -> str | None:
    """Plain palindromic ACGT site that cuts inside itself; only these get an automatic primer tail."""
    motifs = record.recognition.site_alternatives_iupac
    if len(motifs) != 1 or set(motifs[0]) - set("ACGT") or not record.recognition.palindromic:
        return None
    event = record.cleavage.events[0]
    return motifs[0] if 0 < min(event.top_offset, event.bottom_offset) and max(event.top_offset, event.bottom_offset) < len(motifs[0]) else None


def _backbone_region(body: dict[str, Any], length: int) -> tuple[int, int] | None:
    start, end = body.get("backbone_region_start"), body.get("backbone_region_end")
    if start is None and end is None:
        return None
    return 0 if start is None else int(start), length if end is None else int(end)


def _payload_interval(body: dict[str, Any], sequence: str) -> tuple[int, int]:
    start, end = body.get("insert_start"), body.get("insert_end")
    start = 0 if start is None else int(start)
    end = len(sequence) if end is None else int(end)
    if not 0 <= start < end <= len(sequence):
        raise AssemblyError("Insert payload interval is invalid")
    return start, end


def _tailed_insert(body: dict[str, Any], records: tuple[RestrictionRecord, RestrictionRecord]):
    sequence = normalize_dna(body["insert_sequence"])
    start, end = _payload_interval(body, sequence)
    site_left, site_right = _tail_site(records[0]), _tail_site(records[1])
    if site_left is None or site_right is None:
        raise AssemblyError("Selected enzymes do not support an automatic primer-tail preparation")
    prefix, suffix = CLAMP + site_left, site_right + reverse_complement(CLAMP)
    positions = [None] * len(prefix) + list(range(start, end)) + [None] * len(suffix)
    features = map_stored_features(body.get("insert_features") or [], sequence,
                                  body.get("insert_topology", "linear"), positions)
    return prefix + sequence[start:end] + suffix, features, {
        "forward_primer_5prime_tail": prefix, "reverse_primer_5prime_tail": reverse_complement(suffix),
        "note": "Tails are added to the primers' 5' ends outside the payload; the source sequence is unchanged. Clamp bases are a default and may be changed.",
    }


ANNEAL_MIN, ANNEAL_MAX = 18, 35
TM_TARGET_C, TM_PAIR_LIMIT_C = 62.0, 5.0


def _anneal_options(oligo_of_length, ops, limit: int) -> list[dict[str, Any]]:
    """Each annealing length in range with the owner Tm/GC numbers (same Tm function as the native primer search)."""
    options = []
    for length in range(ANNEAL_MIN, limit + 1):
        anneal = oligo_of_length(length)
        tm = ops.calculate_primer_tm_result(anneal, sequence_type="dna").tm
        if tm is None:
            continue
        options.append({"anneal": anneal, "length": length, "tm": round(tm, 2), "gc": ops.calculate_gc_percent(anneal),
                        "clamp": ops._gc_clamp(anneal), "homopolymer": ops._max_homopolymer_run(anneal)})
    return options


def _option_penalty(o: dict[str, Any]) -> float:
    return (abs(o["tm"] - TM_TARGET_C) + (5.0 if o["clamp"] < 1 else 0.0) + (2.0 if not 35.0 <= o["gc"] <= 65.0 else 0.0)
            + (2.0 if o["homopolymer"] > 4 else 0.0))


def _candidate(option: dict[str, Any], tail: str, start: int, end: int, strand: int, payload: str, ops) -> dict[str, Any]:
    sequence = tail + option["anneal"]
    candidate = {"sequence": sequence, "anneal_sequence": option["anneal"], "start": start, "end": end, "strand": strand,
                 "length": len(sequence), "anneal_length": option["length"], "overhang_length": len(tail), "tm": option["tm"],
                 "gc_percent": option["gc"], "gc_clamp": option["clamp"], "max_homopolymer": ops._max_homopolymer_run(sequence),
                 "warnings": list(ops.calculate_primer_tm_result(option["anneal"], sequence_type="dna").warnings)}
    try:
        from services.primer_qc import _evaluate_primer_qc_canonical, PRIMER_QC_PROFILE
        qc = _evaluate_primer_qc_canonical(sequence, sequence_type="dna", template_sequence=payload, circular_template=False,
                                           min_binding_anneal_length=PRIMER_QC_PROFILE.binding_core_bases, intended_binding=(start, end, strand))
        for field in ("max_self_complement", "three_prime_self_complement", "max_hairpin_stem", "hairpin_loop_size",
                      "binding_site_count", "off_target_site_count"):
            candidate[field] = getattr(qc, field)
        candidate["warnings"].extend(qc.warnings)
    except Exception:  # QC metrics are advisory; never lose the primer over them
        pass
    return candidate


def _primer_choices(body: dict[str, Any], tails: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Pick each primer's annealing length independently at its payload end (Tm closest to a common target).

    Forward anneals to payload[0:L]; reverse to rc(payload[-L:]); 5' tails are exactly the prepared tails.
    A shortfall (Tm spread, GC clamp, GC range) is reported as a note, never as an empty result.
    """
    from routers import molbio_ops as ops
    sequence = normalize_dna(body["insert_sequence"])
    start, end = _payload_interval(body, sequence)
    payload = sequence[start:end]
    n = len(payload)
    if n < 2 * ANNEAL_MIN:
        return [], ["No automatic primer pair: the payload is shorter than two minimum annealing regions "
                    f"({2 * ANNEAL_MIN} nt). Exact synthesis DNA and tails remain available."]
    limit = min(ANNEAL_MAX, n // 2)
    forward = _anneal_options(lambda L: payload[:L], ops, limit)
    reverse = _anneal_options(lambda L: reverse_complement(payload[n - L:]), ops, limit)
    if not forward or not reverse:
        return [], ["No automatic primer pair: melting temperature could not be computed for the payload ends."]
    f_opt, r_opt = min(((f, r) for f in forward for r in reverse),
                       key=lambda fr: (_option_penalty(fr[0]) + _option_penalty(fr[1]) + 3.0 * abs(fr[0]["tm"] - fr[1]["tm"]),
                                       fr[0]["length"] + fr[1]["length"]))
    fwd = _candidate(f_opt, tails["forward_primer_5prime_tail"], 0, f_opt["length"], 1, payload, ops)
    rev = _candidate(r_opt, tails["reverse_primer_5prime_tail"], n - r_opt["length"], n, -1, payload, ops)
    notes = ["Primers anneal at the payload ends; each annealing length was chosen independently to bring the annealing-part Tm "
             f"near {TM_TARGET_C:g} C and the pair together."]
    delta = abs(f_opt["tm"] - r_opt["tm"])
    if delta > TM_PAIR_LIMIT_C:
        notes.append(f"Shortfall: annealing-part Tm differs by {delta:.1f} C (forward {f_opt['tm']}, reverse {r_opt['tm']}); the payload ends do not allow a closer match in {ANNEAL_MIN}-{limit} nt.")
    for label, o in (("forward", f_opt), ("reverse", r_opt)):
        if o["clamp"] < 1:
            notes.append(f"Shortfall: {label} primer annealing part has no G/C in its last 5 nt (no GC clamp).")
        if not 35.0 <= o["gc"] <= 65.0:
            notes.append(f"Shortfall: {label} primer annealing GC is {o['gc']}%, outside 35-65%.")
    return [fwd, rev], notes


def selected_restriction_cloning(body: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    """Materialize only the chosen digestion/preparation, without repeating proposal search."""
    catalog = catalog_authority.require()
    def pair(names):
        return resolve_records([names[0]], catalog)[1][0], resolve_records([names[1]], catalog)[1][0]
    vector_records = pair(selection["vector_enzymes"])
    insert_records = pair(selection["insert_enzymes"])
    insert = normalize_dna(body["insert_sequence"])
    features = body.get("insert_features") or []
    topology = body.get("insert_topology", "linear")
    tails = None
    if selection["kind"] == "primer_tail":
        insert, features, tails = _tailed_insert(body, insert_records)
        topology = "linear"
    backbone = normalize_dna(body["backbone_sequence"])
    region = _backbone_region(body, len(backbone))
    result = _evaluate(catalog, backbone, body.get("backbone_topology", "circular"),
                       region, insert, vector_records, insert_records, body.get("vector_five_prime_phosphate", "unknown"),
                       insert_topology=topology, backbone_features=body.get("backbone_features") or [],
                       insert_features=features, selection=selection)
    if result is None:
        raise AssemblyError("The selected fragments do not produce a compatible circular ligation")
    result.update(kind=selection["kind"], primer_tails=tails)
    result["selection"]["kind"] = selection["kind"]
    if tails:
        result["prepared_insert"] = {"sequence": insert, "features": features}
    return result


def suggest_restriction_cloning(body: dict[str, Any]) -> dict[str, Any]:
    backbone = normalize_dna(body["backbone_sequence"])
    insert_full = normalize_dna(body["insert_sequence"])
    btopo = body.get("backbone_topology", "circular")
    itopo = body.get("insert_topology", "linear")
    if btopo != "circular":
        raise AssemblyError("Restriction cloning proposals need a circular backbone")
    explicit = body.get("insert_start") is not None or body.get("insert_end") is not None
    start, end = _payload_interval(body, insert_full)
    region = _backbone_region(body, len(backbone))
    limit = int(body.get("limit", 3))
    vector_phosphate = body.get("vector_five_prime_phosphate", "unknown")
    catalog = catalog_authority.require()
    records = _candidate_records(catalog, body.get("enzymes") or [])
    by_id = {r.enzyme_id: r for r in records}

    vector_single = _single_cutters(backbone, btopo, catalog, records)
    if region is not None:
        vector_single = {k: v for k, v in vector_single.items() if region[0] <= v["cut"] <= region[1]}
    insert_single = _single_cutters(insert_full, itopo, catalog, records)
    # Existing flank sites: left flank cuts at/before the payload start, right flank at/after its end.
    # With an explicit payload interval the flank sites must lie outside it; otherwise the payload is
    # whatever lies between a left-flank and a right-flank site that each cut the insert once.
    left_flank = {k: v for k, v in insert_single.items() if not explicit or v["cut"] <= start}
    right_flank = {k: v for k, v in insert_single.items() if not explicit or v["cut"] >= end}

    candidates: list[tuple[tuple, tuple[str, str], tuple[str, str], str]] = []
    for p_id, p in left_flank.items():
        for q_id, q in right_flank.items():
            if p_id == q_id:
                continue
            if not explicit and left_flank[p_id]["cut"] >= right_flank[q_id]["cut"]:
                continue
            for a_id, a in vector_single.items():
                if a["key"] != p["key"] and a["key"] != q["key"]:
                    continue
                for b_id, b in vector_single.items():
                    if a_id == b_id:
                        continue
                    if {a["key"], b["key"]} != {p["key"], q["key"]} and not (a["key"] == p["key"] and b["key"] == q["key"]):
                        continue
                    same = (a_id, b_id) == (p_id, q_id)
                    directional_guess = a["key"] != b["key"] and a["key"] != ("blunt",)
                    tier = _pair_tier(by_id[a_id], by_id[b_id], by_id[p_id], by_id[q_id])
                    rank = (not directional_guess, tier, not same, by_id[a_id].canonical_name, by_id[b_id].canonical_name)
                    candidates.append((rank, (a_id, b_id), (p_id, q_id), "existing_flank_sites"))
    candidates.sort(key=lambda c: c[0])
    by_id_name = {r.canonical_name: r for r in records}

    proposals: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for _rank, (a_id, b_id), (p_id, q_id), kind in candidates[:200]:
        result = _evaluate(catalog, backbone, btopo, region, insert_full, (by_id[a_id], by_id[b_id]), (by_id[p_id], by_id[q_id]), vector_phosphate,
                           insert_topology=itopo, payload_region=(start, end) if explicit else None,
                           backbone_features=body.get("backbone_features") or [], insert_features=body.get("insert_features") or [])
        if result is None:
            continue
        signature = (result["vector_enzymes"][0], result["vector_enzymes"][1], result["insert_enzymes"][0], result["insert_enzymes"][1])
        if signature in seen or _is_duplicate(result, proposals):
            continue
        seen.add(signature)
        result["kind"] = kind
        result["primer_tails"] = None
        proposals.append(result)
        if len(proposals) >= limit * 3:
            break

    # One enzyme (or two with compatible cohesive/blunt ends) cuts the vector once; the insert is flanked by
    # sites of that enzyme or of an end-compatible one. Non-directional: both orientations are separate proposals.
    insert_multi = _cutters(insert_full, itopo, catalog, records, exactly_one=False)
    for v_id, w_id in _single_enzyme_pairs(vector_single, insert_multi, by_id, explicit, start, end)[:60]:
        for orientation in ("forward", "reverse"):
            result = _evaluate(catalog, backbone, btopo, region, insert_full, (by_id[v_id], by_id[v_id]), (by_id[w_id], by_id[w_id]),
                               vector_phosphate, insert_topology=itopo, payload_region=(start, end) if explicit else None,
                               backbone_features=body.get("backbone_features") or [], insert_features=body.get("insert_features") or [],
                               force_orientation=orientation)
            if result is None or (result["selection"]["insert_orientation"] != orientation):
                continue
            signature = (result["vector_enzymes"][0], result["insert_enzymes"][0], "single", orientation)
            if signature in seen or _is_duplicate(result, proposals):
                continue
            seen.add(signature)
            result["kind"] = "existing_flank_sites"
            result["primer_tails"] = None
            proposals.append(result)

    # Sites added to the insert through explicit primer tails (never an edit of the source).
    def classic_count() -> int:
        return sum(1 for p in proposals if _proposal_rank(p, by_id_name)[0] == 0)

    if classic_count() < limit:
        tail_added = 0
        payload = insert_full[start:end]
        payload_all = analyze_sequence(sequence=payload, topology="linear", catalog=catalog, records=records, include_possible_sites=False)
        payload_hits = {s.enzyme_id for s in payload_all.enzyme_summaries if s.recognition_site_count_definite}
        free = [(k, v) for k, v in vector_single.items() if k not in payload_hits and _tail_site(by_id[k])]
        free.sort(key=lambda kv: (_enzyme_rank(by_id[kv[0]]), by_id[kv[0]].canonical_name))
        tail_candidates = []
        for a_id, a in free:
            for b_id, b in free:
                if a_id != b_id and a["key"] != b["key"]:
                    tail_candidates.append((a_id, b_id))
        tail_candidates.sort(key=lambda t: _pair_tier(by_id[t[0]], by_id[t[1]]))  # stable: keeps name order within a tier
        for a_id, b_id in tail_candidates[:300]:
            tailed, tailed_features, tails = _tailed_insert(body, (by_id[a_id], by_id[b_id]))
            result = _evaluate(catalog, backbone, btopo, region, tailed, (by_id[a_id], by_id[b_id]), (by_id[a_id], by_id[b_id]), vector_phosphate,
                               backbone_features=body.get("backbone_features") or [], insert_features=tailed_features)
            if result is None:
                continue
            signature = (result["vector_enzymes"][0], result["vector_enzymes"][1], "tail")
            if signature in seen or _is_duplicate(result, proposals):
                continue
            seen.add(signature)
            result["kind"] = "primer_tail"
            result["primer_tails"] = tails
            result["prepared_insert_sequence"] = tailed
            proposals.append(result)
            tail_added += 1
            if tail_added >= limit * 3:
                break

    proposals.sort(key=lambda p: (not p["directional"], _proposal_rank(p, by_id_name)[0], p["kind"] == "primer_tail",
                                  p["single_enzyme"], p["vector_self_ligation"] != "not possible",
                                  _proposal_rank(p, by_id_name)[1],
                                  set(p["vector_enzymes"]) != set(p["insert_enzymes"]), p["vector_enzymes"]))
    deduped: list[dict[str, Any]] = []
    for proposal in proposals:
        if not _is_duplicate(proposal, deduped):
            deduped.append(proposal)
    proposals = deduped[:limit]
    for proposal in proposals:
        proposal["selection"]["kind"] = proposal["kind"]
        primers, notes = _primer_choices(body, proposal["primer_tails"]) if proposal["primer_tails"] else ([], [])
        proposal["selection"].update(primers=primers, primer_notes=notes,
                                     parent_revisions=body.get("_source_parent_revisions") or {})
    return {
        "proposals": proposals,
        "no_suitable_pair": not proposals,
        "alternatives": ALTERNATIVES if not proposals else [],
        "considered": {"enzymes": len(records), "unique_in_backbone": len(vector_single), "unique_in_insert": len(insert_single)},
        "notes": ["Proposals are observations from sequence analysis; methylation sensitivity and enzyme activity are not evaluated.",
                  *(body.get("_source_notes") or [])],
    }
