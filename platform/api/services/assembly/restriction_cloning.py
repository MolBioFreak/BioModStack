"""Target-driven restriction-cloning proposals (observations, never a gate).

For a backbone and an insert, list enzyme pairs that cut the backbone once each and give the
insert matching ends, using the existing restriction analysis (site scan) and digest (real
duplex ends). Each proposal is then built with the existing ligation simulator, so the reported
orientation, self-ligation background and per-junction site regeneration come from real ends,
not from a motif table. A site is added to an insert only through an explicit PCR-primer tail;
the source sequences are never edited.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Iterable

from services.molbio_ops import reverse_complement
from services.restriction_analysis import analyze_sequence, normalize_dna
from services.restriction_catalog import CatalogView, RestrictionRecord, catalog_authority
from services.restriction_digest import DigestFragment, simulate_digest

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
    for record in sorted(pool, key=lambda r: (r.canonical_name.lower(),)):
        best.setdefault(record.relationships.isoschizomer_group_id, record)
    return list(best.values())


def _single_cutters(sequence: str, topology: str, catalog: CatalogView, records: list[RestrictionRecord]) -> dict[str, dict[str, Any]]:
    """Enzymes with exactly one definite site and one complete cut. Value: cut position and end signature."""
    analysis = analyze_sequence(sequence=sequence, topology=topology, catalog=catalog, records=records,
                                include_possible_sites=False)
    per: dict[str, list] = {}
    for occurrence in analysis.occurrences:
        if occurrence.certainty == "definite":
            per.setdefault(occurrence.enzyme_id, []).append(occurrence)
    counts = {s.enzyme_id: s.recognition_site_count_definite for s in analysis.enzyme_summaries}
    out: dict[str, dict[str, Any]] = {}
    for enzyme_id, occurrences in per.items():
        if len(occurrences) != 1 or counts.get(enzyme_id, 1) != 1:
            continue
        events = [e for e in occurrences[0].double_strand_events if e.status == "complete"]
        if len(events) != 1 or events[0].top_boundary is None:
            continue
        event = events[0]
        ovh = event.overhang_sequence_5to3 or ""
        key = ("blunt",) if event.overhang_kind == "blunt" else (event.overhang_kind, min(ovh, reverse_complement(ovh)))
        out[enzyme_id] = {"cut": int(event.top_boundary), "key": key, "site_start": occurrence_start(occurrences[0])}
    return out


def occurrence_start(occurrence: Any) -> int:
    return int(occurrence.site_start)


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


def _to_fragment(piece: DigestFragment, fid: str, name: str, orientation: str = "forward") -> AssemblyFragment:
    # Enzymes that made each end, so junction observations name only the enzymes that formed that junction.
    ends = {"left": list(piece.left_end.contributing_enzyme_ids), "right": list(piece.right_end.contributing_enzyme_ids)}
    return AssemblyFragment(id=fid, name=name, sequence=piece.top_strand_sequence, orientation=orientation,  # type: ignore[arg-type]
                            left_end=digest_end_to_fragment_end(piece.left_end),
                            right_end=digest_end_to_fragment_end(piece.right_end),
                            metadata={"end_enzymes": ends})


def _try(fragments: list[AssemblyFragment]):
    try:
        return simulate_ligation(fragments, circular=True)
    except AssemblyError:
        return None


def _evaluate(catalog: CatalogView, backbone: str, btopo: str, region, insert: str,
              vector_enzymes: tuple[RestrictionRecord, RestrictionRecord],
              insert_enzymes: tuple[RestrictionRecord, RestrictionRecord],
              vector_phosphate: str) -> dict[str, Any] | None:
    vector_digest = _digest(catalog, backbone, btopo, vector_enzymes)
    vector = _backbone_piece(vector_digest.fragments, region)
    insert_digest = _digest(catalog, insert, "linear", insert_enzymes)
    payload = _insert_piece(insert_digest.fragments)
    if vector is None or payload is None:
        return None
    vector_fragment = _to_fragment(vector, "vector", "backbone")
    if vector_phosphate != "unknown":
        for end in (vector_fragment.left_end, vector_fragment.right_end):
            end.five_prime_phosphate = vector_phosphate  # type: ignore[assignment]
    forward = _try([vector_fragment, _to_fragment(payload, "insert", "insert")])
    reverse = _try([vector_fragment, _to_fragment(payload, "insert", "insert", "reverse")])
    if forward is None and reverse is None:
        return None
    orientation = "either" if forward and reverse else "forward" if forward else "reverse"
    product = forward or reverse
    assert product is not None
    seal, _notes = junction_seal(vector_fragment.right_end, vector_fragment.left_end)
    self_ok, _why = overhangs_compatible(vector_fragment.right_end, vector_fragment.left_end)
    names = sorted({r.canonical_name for r in (*vector_enzymes, *insert_enzymes)})
    observed = observe_junction_sites(product, names) if len(names) <= 4 else []
    junctions = [{"between": f"{j['left_fragment_id']} → {j['right_fragment_id']}",
                  "regenerated": j["regenerated"], "destroyed": j["destroyed"], "note": j["note"]} for j in observed]
    directional = orientation != "either" and not self_ok
    return {
        "vector_enzymes": [r.canonical_name for r in vector_enzymes],
        "insert_enzymes": [r.canonical_name for r in insert_enzymes],
        "directional": directional,
        "insert_orientation": orientation,
        "vector_ends_match_each_other": self_ok,
        "vector_self_ligation": ("possible" if self_ok else "not possible") + (
            "" if not self_ok else (", reduced (vector 5' ends unphosphorylated)" if seal == "none" else
                                    ", nicked (one 5' phosphate)" if seal == "one_strand" else "")),
        "vector_end_seal": seal,
        "vector_length": len(vector.top_strand_sequence),
        "insert_payload_length": len(payload.top_strand_sequence),
        "product_length": len(product.sequence),
        "junction_sites": junctions,
    }


def _tail_site(record: RestrictionRecord) -> str | None:
    """Plain palindromic ACGT site that cuts inside itself; only these get an automatic primer tail."""
    motifs = record.recognition.site_alternatives_iupac
    if len(motifs) != 1 or set(motifs[0]) - set("ACGT") or not record.recognition.palindromic:
        return None
    event = record.cleavage.events[0]
    return motifs[0] if 0 < min(event.top_offset, event.bottom_offset) and max(event.top_offset, event.bottom_offset) < len(motifs[0]) else None


def suggest_restriction_cloning(body: dict[str, Any]) -> dict[str, Any]:
    backbone = normalize_dna(body["backbone_sequence"])
    insert_full = normalize_dna(body["insert_sequence"])
    btopo = body.get("backbone_topology", "circular")
    if body.get("insert_topology", "linear") != "linear":
        raise AssemblyError("The insert must be linear (cut or amplify it first)")
    if btopo != "circular":
        raise AssemblyError("Restriction cloning proposals need a circular backbone")
    explicit = body.get("insert_start") is not None or body.get("insert_end") is not None
    start = body.get("insert_start") or 0
    end = body.get("insert_end") or len(insert_full)
    if not 0 <= start < end <= len(insert_full):
        raise AssemblyError("Insert payload interval is invalid")
    rs, re_ = body.get("backbone_region_start"), body.get("backbone_region_end")
    region = (int(rs), int(re_)) if rs is not None and re_ is not None else None
    limit = int(body.get("limit", 3))
    vector_phosphate = body.get("vector_five_prime_phosphate", "unknown")
    catalog = catalog_authority.require()
    records = _candidate_records(catalog, body.get("enzymes") or [])
    by_id = {r.enzyme_id: r for r in records}

    vector_single = _single_cutters(backbone, btopo, catalog, records)
    if region is not None:
        vector_single = {k: v for k, v in vector_single.items() if region[0] <= v["cut"] <= region[1]}
    insert_single = _single_cutters(insert_full, "linear", catalog, records)
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
                    rank = (not directional_guess, not same, by_id[a_id].canonical_name, by_id[b_id].canonical_name)
                    candidates.append((rank, (a_id, b_id), (p_id, q_id), "existing_flank_sites"))
    candidates.sort(key=lambda c: c[0])

    proposals: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for _rank, (a_id, b_id), (p_id, q_id), kind in candidates[:200]:
        result = _evaluate(catalog, backbone, btopo, region, insert_full, (by_id[a_id], by_id[b_id]), (by_id[p_id], by_id[q_id]), vector_phosphate)
        if result is None:
            continue
        signature = (result["vector_enzymes"][0], result["vector_enzymes"][1], result["insert_enzymes"][0], result["insert_enzymes"][1])
        if signature in seen:
            continue
        seen.add(signature)
        result["kind"] = kind
        result["primer_tails"] = None
        proposals.append(result)
        if len(proposals) >= limit * 3:
            break

    # Sites added to the insert through explicit primer tails (never an edit of the source).
    if len(proposals) < limit:
        payload = insert_full[start:end]
        payload_scan = _single_cutters(payload, "linear", catalog, records) if payload else {}
        payload_all = analyze_sequence(sequence=payload, topology="linear", catalog=catalog, records=records, include_possible_sites=False)
        payload_hits = {s.enzyme_id for s in payload_all.enzyme_summaries if s.recognition_site_count_definite}
        free = [(k, v) for k, v in vector_single.items() if k not in payload_hits and _tail_site(by_id[k])]
        free.sort(key=lambda kv: by_id[kv[0]].canonical_name)
        tail_candidates = []
        for i, (a_id, a) in enumerate(free):
            for b_id, b in free:
                if a_id != b_id and a["key"] != b["key"]:
                    tail_candidates.append((a_id, b_id))
        for a_id, b_id in tail_candidates[:300]:
            site_a, site_b = _tail_site(by_id[a_id]), _tail_site(by_id[b_id])
            tailed = CLAMP + site_a + payload + site_b + reverse_complement(CLAMP)  # type: ignore[operator]
            result = _evaluate(catalog, backbone, btopo, region, tailed, (by_id[a_id], by_id[b_id]), (by_id[a_id], by_id[b_id]), vector_phosphate)
            if result is None:
                continue
            signature = (result["vector_enzymes"][0], result["vector_enzymes"][1], "tail")
            if signature in seen:
                continue
            seen.add(signature)
            result["kind"] = "primer_tail"
            result["primer_tails"] = {
                "forward_primer_5prime_tail": CLAMP + site_a,  # type: ignore[operator]
                "reverse_primer_5prime_tail": reverse_complement(site_b + reverse_complement(CLAMP)),  # type: ignore[operator]
                "note": "Tails are added to the primers' 5' ends outside the payload; the source sequence is unchanged. "
                        "Clamp bases are a default and may be changed.",
            }
            proposals.append(result)
            if len(proposals) >= limit * 3:
                break

    proposals.sort(key=lambda p: (not p["directional"], p["kind"] == "primer_tail", p["vector_self_ligation"] != "not possible", p["vector_enzymes"]))
    proposals = proposals[:limit]
    return {
        "proposals": proposals,
        "no_suitable_pair": not proposals,
        "alternatives": ALTERNATIVES if not proposals else [],
        "considered": {"enzymes": len(records), "unique_in_backbone": len(vector_single), "unique_in_insert": len(insert_single)},
        "notes": ["Proposals are observations from sequence analysis; methylation sensitivity and enzyme activity are not evaluated."],
    }
