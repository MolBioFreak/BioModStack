"""Portable projections of frozen Golden Gate results; no execution or resolver.

JSON is the lossless readback format. FASTA/GenBank and CSV are human/tool
projections, not alternative scientific authorities. Import this leaf lazily at
route boundaries, as the core DTO currently imports the shared router Tm DTO.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from typing import Literal

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from services.assembly.golden_gate_design import design_material
from services.assembly.golden_gate_design_types import (
    Feature, GoldenGateDesignRequest, GoldenGateDesignResult, Material, Solution,
)
from services.assembly.golden_gate_domestication import DomesticationResult
from services.assembly.golden_gate_reaction import ReactionResult, ReactionRow


class PortableDesign(BaseModel):
    """Retained native objects, not a competing scientific settings contract.

    Source bindings reference result.materials, including aliases of one
    immutable revision. Original request identities are never rewritten inline.
    Fidelity's native owner returns JSON dictionaries, not a Pydantic result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["bms.golden-gate-portable.v1"] = "bms.golden-gate-portable.v1"
    request: GoldenGateDesignRequest
    result: GoldenGateDesignResult
    source_material_ids: dict[str, str]
    fidelity: dict[str, JsonValue] | None = None
    domestication: DomesticationResult | dict[str, DomesticationResult] | None = None
    reaction: ReactionResult | None = None

    @model_validator(mode="after")
    def retained_references(self) -> PortableDesign:
        # Structural file integrity only, not score/chemistry/quality admission.
        solution = selected_solution(self.result)
        if solution is not None:
            if len(solution.part_material_ids) != len(self.request.parts):
                raise ValueError("Selected material references must cover the ordered frozen parts")
            for material_id in solution.part_material_ids:
                design_material(self.result, material_id)
        if self.source_material_ids != _source_bindings(self.request, self.result):
            raise ValueError("Portable source bindings must match the frozen request identities")
        for material_id in self.source_material_ids.values():
            if design_material(self.result, material_id).stage != "source":
                raise ValueError("Portable source binding must identify source material")
        return self


def selected_solution(result: GoldenGateDesignResult) -> Solution | None:
    """Resolve the chosen ID, never select the first candidate implicitly."""
    if result.selected_solution_id is None:
        return None
    matches = [s for s in result.solutions if s.id == result.selected_solution_id]
    if len(matches) != 1:
        raise ValueError("selected_solution_id must identify exactly one retained solution")
    return matches[0]


def _source_bindings(request: GoldenGateDesignRequest, result: GoldenGateDesignResult) -> dict[str, str]:
    revisions: dict[str, str] = {}
    bindings = {}
    for source in request.sources:
        material_id = f"source:{source.id}"
        if source.source.kind == "molecular_revision":
            material_id = revisions.setdefault(source.source.revision_id, material_id)
        design_material(result, material_id)
        bindings[source.id] = material_id
    return bindings


def read_design_export(data: bytes | str) -> PortableDesign:
    """Read exact retained request/results without catalog, cache, DB or science."""
    return PortableDesign.model_validate_json(data)


def portable_source(design: PortableDesign, source_id: str) -> Material:
    """Read an offline source by request source ID, retaining revision identity."""
    value = design_material(design.result, design.source_material_ids[source_id])
    return Material(sequence=value.sequence, topology=value.topology, features=value.features)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _feature(feature: Feature, length: int) -> SeqFeature:
    locations = []
    for segment in feature.segments:
        spans = [(segment.start, length), (0, segment.end)] if segment.wraps_origin else [(segment.start, segment.end)]
        locations.extend(FeatureLocation(a, b, strand=feature.strand or None) for a, b in spans if b > a)
    location = locations[0] if len(locations) == 1 else CompoundLocation(locations, operator="join")
    # Do not sort compound locations or reverse their stored traversal on -1.
    # Biopython's writer handles complement(join(...)) representation itself.
    qualifiers = {}
    for key, value in feature.qualifiers.items():
        values = value if isinstance(value, list) else [value]
        qualifiers[key] = [v if isinstance(v, str) else _json(v) for v in values]
    qualifiers.setdefault("label", [feature.name or feature.id])
    qualifiers["bms_feature_id"] = [feature.id]
    qualifiers["bms_status"] = [feature.status]
    qualifiers["bms_strand"] = [str(feature.strand)]
    if feature.codon_start is not None:
        qualifiers["codon_start"] = [str(feature.codon_start)]
    if feature.frame_preserved is not None:
        qualifiers["bms_frame_preserved"] = [str(feature.frame_preserved).lower()]
    return SeqFeature(location, type=feature.type, id=feature.id, qualifiers=qualifiers)


def sequence_records(result: GoldenGateDesignResult) -> dict[str, list[SeqRecord]]:
    """Return selected product, all sources, preparations and native digest rows.

    Short unique format IDs avoid GenBank LOCUS constraints. The exact native
    identity is retained in each description/comment and in portable JSON.
    Materialization uses the core digest mapping helper, not digest simulation.
    """
    groups: dict[str, list[SeqRecord]] = {key: [] for key in ("product", "sources", "intermediates", "digest_fragments")}

    def add(group: str, identity: str, material: Material | Solution) -> None:
        identifier = f"gg_{group[:4]}_{len(groups[group]) + 1}"
        record = SeqRecord(Seq(material.sequence), id=identifier, name=identifier,
                           description=f"BMS identity={_json(identity)}")
        record.annotations = {"molecule_type": "DNA", "topology": material.topology,
                              "comment": f"Exact BMS identity: {_json(identity)}; coordinates in design.json are zero-based half-open."}
        record.features = [_feature(f, len(material.sequence)) for f in material.features]
        groups[group].append(record)

    solution = selected_solution(result)
    if solution is not None:
        add("product", solution.id, solution)
    for material in result.materials:
        group = {"source": "sources", "prepared": "intermediates", "digest": "digest_fragments"}[material.stage]
        add(group, material.id, material)
    emitted = {m.id for m in result.materials}
    for digest in result.digests:
        for material_id in digest.fragment_material_ids:
            if material_id not in emitted:
                add("digest_fragments", material_id, design_material(result, material_id))
                emitted.add(material_id)
    return groups


def _csv(fields: list[str], rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _json(value) if isinstance(value, (dict, list, tuple)) else value
                         for key, value in row.items()})
    return stream.getvalue().encode("utf-8")


def _worksheet(design: PortableDesign) -> bytes:
    request, result = design.request, design.result
    solution = selected_solution(result)
    lines = ["Golden Gate preparation and reaction worksheet", "",
             f"Selected solution: {result.selected_solution_id or 'none selected'}",
             f"Selected ordered retained materials: {solution.part_material_ids if solution else 'none selected'}",
             f"Enzyme: {request.enzyme.enzyme_id}",
             "No calculation, optimization or condition substitution was performed during export.",
             "Coordinates: zero-based half-open. Physical protruding strands: individually 5′→3′.",
             "design.json is lossless readback. GenBank uses ordered join locations and bms_* qualifiers for feature identity/status/frame.",
             "GenBank cannot express unknown strand in location syntax: bms_strand=0 preserves it explicitly. Arbitrary qualifier JSON types remain in design.json.",
             "", "PREPARATION"]
    parts = {p.id: p for p in request.parts}
    for preparation in result.preparations:
        part = parts[preparation.part_id]
        lines.extend([f"{part.id} ({part.name}): {part.preparation.kind}; orientation={part.orientation}",
                      f"  source={preparation.source_material_id}; prepared={preparation.prepared_material_id}; retained={preparation.retained_material_id}",
                      f"  frozen preparation: {_json(part.preparation.model_dump(mode='json'))}",
                      f"  primers: {', '.join(preparation.primer_ids) or 'none'}; PCR evidence={preparation.pcr_verification}"])
        lines.extend(f"  {text}" for text in preparation.pcr_diagnostics)
    for digest in result.digests:
        lines.append(f"Digest {digest.part_id}: retained index={digest.retained_fragment_index}; removed={digest.removed_fragment_indices}; background still present={digest.background_fragment_indices}")
    lines.extend(["", "REACTION (user-selected; independent of empirical assay reference)"])
    if design.reaction is None:
        lines.append("Not supplied. Stocks, amounts, recipe and cycling program are unknown; none inferred.")
    else:
        reaction = design.reaction
        lines.extend([reaction.mass_basis_description,
                      "Frozen user inputs (units are in field names or explicit unit fields):",
                      json.dumps(reaction.request.model_dump(mode="json"), ensure_ascii=False, indent=2),
                      "Calculated transfers: see reaction.csv (empty cells mean unknown, not zero).",
                      f"Known transfer subtotal (uL): {reaction.known_transfer_subtotal_uL}",
                      f"Total transfer (uL): {reaction.total_transfer_uL}",
                      f"Water exact (uL): {reaction.water_exact_uL}",
                      f"Mastermix total (uL): {reaction.mastermix_total_uL}"])
        lines.extend(f"{d.code} [{d.component_id}]: {d.message}" for d in reaction.diagnostics)
    lines.extend(["", "RETAINED DIAGNOSTICS", *result.diagnostics, *result.limitations])
    if solution is not None:
        lines.extend(solution.warnings)
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_design_exports(
    request: GoldenGateDesignRequest,
    result: GoldenGateDesignResult,
    *,
    fidelity: dict[str, JsonValue] | None = None,
    domestication: DomesticationResult | dict[str, DomesticationResult] | None = None,
    reaction: ReactionResult | None = None,
) -> dict[str, bytes]:
    """Build deterministic self-contained files from frozen native objects.

    Unselected/unsuccessful designs still export retained evidence and material;
    product files are empty when there is no selected product. All alternatives
    remain in JSON. Missing scores/reaction chemistry do not prevent export.
    """
    design = PortableDesign(request=request, result=result, source_material_ids=_source_bindings(request, result),
                            fidelity=fidelity, domestication=domestication, reaction=reaction)
    exports = {"design.json": (_json(design.model_dump(mode="json")) + "\n").encode("utf-8")}
    for group, records in sequence_records(result).items():
        for extension, format_name in (("fasta", "fasta"), ("gb", "genbank")):
            stream = io.StringIO()
            SeqIO.write(records, stream, format_name)
            exports[f"{group}.{extension}"] = stream.getvalue().encode("utf-8")
    parts = {p.id: p for p in request.parts}
    primer_fields = ["id", "part_id", "part_name", "direction", "full_sequence", "annealing_sequence", "tail_sequence",
                     "clamp", "recognition_site", "recognition_orientation", "spacer", "fusion", "tm", "requested_tm_settings",
                     "effective_tm_settings", "qc", "qc_template_orientation", "footprint_mappings"]
    primer_rows = []
    for primer in result.primers:
        row = primer.model_dump(mode="json")
        row.update(part_name=parts[primer.part_id].name,
                   tail_sequence=primer.clamp + primer.recognition_site + primer.spacer + primer.fusion,
                   requested_tm_settings=request.primer_settings.model_dump(mode="json"),
                   effective_tm_settings=result.primer_settings.model_dump(mode="json"))
        primer_rows.append(row)
    exports["primers.csv"] = _csv(primer_fields, primer_rows)
    part_fields = ["solution_id", "part_id", "name", "role", "slot", "source_id", "source", "orientation", "preparation",
                   "source_material_id", "prepared_material_id", "retained_material_id", "retained_sequence",
                   "retained_topology", "left_end", "right_end", "mappings", "end_convention", "primer_ids",
                   "pcr_verification", "removed_fragment_indices", "background_fragment_indices"]
    preparations = {p.part_id: p for p in result.preparations}
    digests = {d.part_id: d for d in result.digests}
    sources = {s.id: s.source.model_dump(mode="json") for s in request.sources}
    rows = []
    solution = selected_solution(result)
    for index, part in enumerate(request.parts):
        preparation = preparations[part.id]
        retained_id = solution.part_material_ids[index] if solution else preparation.retained_material_id
        retained = design_material(result, retained_id) if retained_id else None
        digest = digests.get(part.id)
        rows.append(dict(solution_id=solution.id if solution else None,
                         part_id=part.id, name=part.name, role=part.role, slot=part.slot, source_id=part.source_id,
                         source=sources[part.source_id], orientation=part.orientation,
                         preparation=part.preparation.model_dump(mode="json"),
                         source_material_id=preparation.source_material_id, prepared_material_id=preparation.prepared_material_id,
                         retained_material_id=retained_id,
                         retained_sequence=retained.sequence if retained else None,
                         retained_topology=retained.topology if retained else None,
                         left_end=asdict(retained.left_end) if retained and retained.left_end else None,
                         right_end=asdict(retained.right_end) if retained and retained.right_end else None,
                         mappings=[m.model_dump(mode="json") for m in retained.mappings] if retained else [],
                         end_convention="Native retained fragment before part orientation; physical protruding strands individually 5prime-to-3prime; null strand retains legacy notation",
                         primer_ids=preparation.primer_ids, pcr_verification=preparation.pcr_verification,
                         removed_fragment_indices=digest.removed_fragment_indices if digest else [],
                         background_fragment_indices=digest.background_fragment_indices if digest else []))
    exports["parts.csv"] = _csv(part_fields, rows)
    junction_fields = ["solution_id", "index", "left_fragment_id", "right_fragment_id", "left_fragment_name", "right_fragment_name",
                       "mode", "left_end_type", "right_end_type", "overhang_sequence", "overlap_sequence", "overlap_length",
                       "junction_sequence", "validation", "notes", "end_convention"]
    exports["junctions.csv"] = _csv(junction_fields, [dict(solution_id=solution.id, index=i, **asdict(junction),
        end_convention="Native oriented left-fragment right-end overhang, physical 5prime-to-3prime when explicit; see parts.csv for original end notation")
        for i, junction in enumerate(solution.junctions)] if solution else [])
    exports["worksheet.txt"] = _worksheet(design)
    # Use the actual native result row schema even when no reaction was supplied.
    exports["reaction.csv"] = _csv(list(ReactionRow.model_fields),
        [row.model_dump(mode="json") for row in reaction.rows] if reaction else [])
    return exports
