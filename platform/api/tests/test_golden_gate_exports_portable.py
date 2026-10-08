"""Export/readback against real core execution, not invented science responses."""
import csv
import io
import json
import os
import subprocess
import sys

from Bio import SeqIO
import pytest

from services.assembly.golden_gate_design import design_golden_gate, design_material
from services.assembly.golden_gate_design_types import GoldenGateDesignRequest, Material
from services.assembly.golden_gate_exports import (
    build_design_exports, portable_source, read_design_export, sequence_records,
)
from services.assembly.golden_gate_fidelity import evaluate_overhangs, discover_datasets
from services.assembly.golden_gate_domestication import (
    CDSConstraint, DomesticationSettings, EditRegion, UnwantedSite, propose_domestication,
)
from services.assembly.golden_gate_reaction import (
    ReactionRequest, ReactionSettings, ReactionPart, DNAAmount, DNAStock,
    CyclingProgram, CyclingBlock, CyclingStep, calculate_reaction,
)
from test_golden_gate_design_core import request, B


def records(payload, format_name):
    return list(SeqIO.parse(io.StringIO(payload.decode()), format_name))


def rows(payload):
    return list(csv.DictReader(io.StringIO(payload.decode())))


@pytest.mark.parametrize("enzyme", ["BsaI", "BsmBI", "Esp3I", "BbsI", "SapI"])
@pytest.mark.parametrize("reverse", [False, True])
def test_real_core_complete_exports_roundtrip(enzyme, reverse):
    req = request(enzyme, reverse=reverse, origin=77)
    result = design_golden_gate(req)
    before = result.model_dump(mode="json")
    exports = build_design_exports(req, result)
    restored = read_design_export(exports["design.json"])
    assert restored.request == req and restored.result == result
    assert result.model_dump(mode="json") == before
    assert build_design_exports(restored.request, restored.result) == exports
    expected = result.solutions[0]
    for group, original in sequence_records(result).items():
        gb = records(exports[group + ".gb"], "genbank")
        fa = records(exports[group + ".fasta"], "fasta")
        assert [str(r.seq) for r in gb] == [str(r.seq) for r in original] == [str(r.seq) for r in fa]
        for parsed, native in zip(gb, original):
            assert parsed.annotations["topology"] == native.annotations["topology"]
            assert [f.location for f in parsed.features] == [f.location for f in native.features]
            assert [f.qualifiers for f in parsed.features] == [f.qualifiers for f in native.features]
    assert str(records(exports["product.gb"], "genbank")[0].seq) == expected.sequence
    assert len(records(exports["digest_fragments.gb"], "genbank")) == sum(len(d.fragments) for d in result.digests)
    primers = rows(exports["primers.csv"])
    assert len(primers) == 2
    for exported, native in zip(primers, result.primers):
        assert exported["full_sequence"] == native.full_sequence
        assert exported["full_sequence"] == exported["tail_sequence"] + exported["annealing_sequence"]
        assert exported["full_sequence"] != native.annealing_sequence
        assert json.loads(exported["tm"]) == native.tm.model_dump(mode="json")
        assert json.loads(exported["effective_tm_settings"]) == result.primer_settings.model_dump(mode="json")
    assert len(primers[0]["spacer"]) == (2 if enzyme == "BbsI" else 1)
    assert len(primers[0]["fusion"]) == (3 if enzyme == "SapI" else 4)
    for row, prep in zip(rows(exports["parts.csv"]), result.preparations):
        material = design_material(result, prep.retained_material_id)
        assert row["retained_sequence"] == material.sequence
        assert row["prepared_material_id"] == material.parent_id
        assert json.loads(row["mappings"]) == [m.model_dump(mode="json") for m in material.mappings]
    assert len(rows(exports["junctions.csv"])) == len(expected.junctions)
    assert "unknown; none inferred" in exports["worksheet.txt"].decode()
    assert rows(exports["reaction.csv"]) == []


def test_ordered_reverse_compound_circular_annotations():
    data = request(reverse=True, origin=77).model_dump()
    data["sources"][1]["source"].update(topology="circular", features=[
        dict(id="rev-compound", type="CDS", name="reverse compound", strand=-1,
             segments=[dict(start=35, end=49), dict(start=3, end=17)], codon_start=2,
             qualifiers={"note": ['a quoted "annotation"', "second note"], "transl_table": ["11"]}),
        dict(id="origin", type="misc_feature", strand=-1,
             segments=[dict(start=len(B)-8, end=7, wraps_origin=True)], qualifiers={"gene": ["origin_gene"]}),
        dict(id="unstranded", type="misc_feature", strand=0, segments=[dict(start=4, end=9)])])
    req = GoldenGateDesignRequest.model_validate(data)
    result = design_golden_gate(req)
    exports = build_design_exports(req, result)
    source = records(exports["sources.gb"], "genbank")[1]
    compound, origin, unstranded = source.features
    assert [(int(p.start), int(p.end), p.strand) for p in compound.location.parts] == [(35, 49, -1), (3, 17, -1)]
    assert [(int(p.start), int(p.end)) for p in origin.location.parts] == [(len(B)-8, len(B)), (0, 7)]
    assert compound.qualifiers["note"] == ['a quoted "annotation"', "second note"]
    assert compound.qualifiers["codon_start"] == ["2"]
    assert unstranded.qualifiers["bms_strand"] == ["0"]
    for group in ("product", "intermediates", "digest_fragments"):
        parsed = records(exports[group + ".gb"], "genbank")
        native = sequence_records(result)[group]
        for a, b in zip(parsed, native):
            # INSDC has no strand=0 syntax. It remains explicit in the BMS
            # qualifier (and losslessly in design.json), not inferred as +1.
            for feature in a.features:
                if feature.qualifiers.get("bms_strand") == ["0"]:
                    feature.location.strand = None
            assert [f.location for f in a.features] == [f.location for f in b.features]
            assert [str(f.extract(a.seq)) for f in a.features] == [str(f.extract(b.seq)) for f in b.features]
    assert read_design_export(exports["design.json"]).request == req


def test_deleted_revision_sources_aliases_and_fresh_process(tmp_path, monkeypatch):
    data = request("SapI", reverse=True).model_dump()
    source = data["sources"][1]["source"]
    original = Material(**{k: v for k, v in source.items() if k != "kind"})
    store = {"immutable-17": original}
    data["sources"][1]["source"] = dict(kind="molecular_revision", revision_id="immutable-17")
    data["sources"].append(dict(id="alias", source=dict(kind="molecular_revision", revision_id="immutable-17")))
    req = GoldenGateDesignRequest.model_validate(data)
    result = design_golden_gate(req, resolve_revision=store.__getitem__)
    store.clear()  # no resolver contents survive

    def forbidden(*args, **kwargs):
        raise AssertionError("Export must not rerun scientific execution")

    monkeypatch.setattr("services.assembly.golden_gate_design.design_golden_gate", forbidden)
    monkeypatch.setattr("services.assembly.golden_gate_design.simulate_digest", forbidden)
    monkeypatch.setattr("services.assembly.golden_gate_fidelity.evaluate_overhangs", forbidden)
    monkeypatch.setattr("services.assembly.golden_gate_reaction.calculate_reaction", forbidden)
    monkeypatch.setattr("services.restriction_catalog.catalog_authority.require", forbidden)
    exported = build_design_exports(req, result)
    design = read_design_export(exported["design.json"])
    assert portable_source(design, "alias") == portable_source(design, "insert") == original
    assert design.request.sources[1].source.revision_id == "immutable-17"
    assert design.source_material_ids["alias"] == "source:insert"
    path = tmp_path / "design.json"
    path.write_bytes(exported["design.json"])
    # Fresh interpreter under the same route-free pytest namespace, no resolver.
    script = """
import sys
from pathlib import Path
from services.assembly.golden_gate_exports import read_design_export, build_design_exports, portable_source
from services.restriction_catalog import catalog_authority
catalog_authority.require = lambda: (_ for _ in ()).throw(AssertionError('catalog called'))
d = read_design_export(Path(sys.argv[1]).read_bytes())
assert d.request.sources[1].source.revision_id == 'immutable-17'
assert portable_source(d, 'alias') == portable_source(d, 'insert')
assert build_design_exports(d.request, d.result)['design.json'] == Path(sys.argv[1]).read_bytes()
print('offline fresh-process readback verified')
"""
    completed = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, env=os.environ.copy())
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "offline fresh-process readback verified" in completed.stdout


def test_selected_solution_is_not_first_and_no_selection_is_not_invented():
    req = request(origin=77)
    result = design_golden_gate(req)
    alternative = design_golden_gate(request()).solutions[0].model_copy(update={"id": "other-origin"})
    result = result.model_copy(update={"solutions": [alternative, result.solutions[0]]})
    exports = build_design_exports(req, result)
    assert str(records(exports["product.fasta"], "fasta")[0].seq) == result.solutions[1].sequence
    assert len(read_design_export(exports["design.json"]).result.solutions) == 2
    unselected = result.model_copy(update={"selected_solution_id": None})
    exports = build_design_exports(req, unselected)
    assert exports["product.fasta"] == exports["product.gb"] == b""
    assert rows(exports["junctions.csv"]) == []
    assert read_design_export(exports["design.json"]).result.selected_solution_id is None


def test_frozen_native_supplements_and_user_worksheet(monkeypatch):
    req = request("SapI")
    result = design_golden_gate(req)
    dataset = next(d["id"] for d in discover_datasets() if d["end_length"] == 3)
    fidelity = evaluate_overhangs(["ATG", "GAG"], dataset, include_pair_observations=True)
    domestication = propose_domestication(B, settings=DomesticationSettings())
    reaction = calculate_reaction(ReactionRequest(settings=ReactionSettings(total_volume_uL=20,
        reaction_count=3, mastermix_overage_percent=7, cycling=CyclingProgram(name="operator-selected",
            provenance="user fixture, not empirical assay", blocks=(CyclingBlock(repetitions=2,
                steps=(CyclingStep(temperature_C=37, duration_seconds=90),)),))),
        parts=(ReactionPart(part_id="insert", length_bp=len(B)+3, amount=DNAAmount(value=0.1, unit="pmol"),
                            stock=DNAStock(value=0.02, unit="pmol/uL")),)))
    def forbidden(*args, **kwargs):
        raise AssertionError("Frozen supplements must not be recalculated")
    monkeypatch.setattr("services.assembly.golden_gate_fidelity.evaluate_overhangs", forbidden)
    monkeypatch.setattr("services.assembly.golden_gate_reaction.calculate_reaction", forbidden)
    monkeypatch.setattr("services.assembly.golden_gate_domestication.propose_domestication", forbidden)
    exports = build_design_exports(req, result, fidelity=fidelity, domestication={"insert": domestication}, reaction=reaction)
    restored = read_design_export(exports["design.json"])
    assert restored.fidelity == fidelity
    assert restored.domestication == {"insert": domestication}
    assert restored.reaction == reaction
    assert exports == build_design_exports(restored.request, restored.result,
        fidelity=restored.fidelity, domestication=restored.domestication, reaction=restored.reaction)
    worksheet = exports["worksheet.txt"].decode()
    assert "operator-selected" in worksheet and '"duration_seconds": 90' in worksheet
    assert '"mastermix_overage_percent": 7' in worksheet
    assert rows(exports["reaction.csv"])[0]["transfer_volume_uL"] == "5.0"


def test_unavailable_score_unknown_stocks_and_no_solution_are_exportable():
    data = request(kind="synthesis").model_dump()
    payload = B[:25] + "GGTCTCAACTG" + B[25:]
    data["sources"][1]["source"].update(sequence=payload, features=[])
    data["parts"][1]["preparation"]["region"]["end"] = len(payload)
    req = GoldenGateDesignRequest.model_validate(data)
    result = design_golden_gate(req)
    assert not result.solutions
    score = evaluate_overhangs(["GGAG"], None)
    reaction = calculate_reaction(ReactionRequest(parts=(ReactionPart(part_id="insert", length_bp=100),)))
    exports = build_design_exports(req, result, fidelity=score, reaction=reaction)
    assert rows(exports["primers.csv"]) == []
    assert exports["product.gb"] == b""
    assert rows(exports["reaction.csv"])[0]["transfer_volume_uL"] == ""
    assert read_design_export(exports["design.json"]).fidelity["status"] == "unavailable"
    assert "unknown" in exports["worksheet.txt"].decode()


def test_proposed_not_accepted_edits_and_nondefault_settings_remain_frozen():
    source = "ATGGGTCTCTAA"
    region = EditRegion(start=0, end=len(source))
    proposal = propose_domestication(source, settings=DomesticationSettings(enabled=True,
        editable_regions=(region,), cds=(CDSConstraint(feature_id="gene", region=region, genetic_code=11),),
        unwanted_sites=(UnwantedSite(enzyme_id="BsaI", recognition_sequence="GGTCTC"),)))
    assert proposal.status == "proposal" and proposal.proposed_sequence != source
    data = request().model_dump()
    # Unaccepted proposal belongs to a retained extra source, not a silently
    # substituted source under an unchanged immutable identity.
    data["sources"].append(dict(id="proposed-source", source=dict(kind="inline", sequence=source, topology="linear")))
    data["primer_settings"].update(primer_concentration_nM=325, na_mM=61, dmso_percent=2)
    data["parts"][1]["name"] = 'insert, "operator name"\nsecond line'
    req = GoldenGateDesignRequest.model_validate(data)
    result = design_golden_gate(req)
    exports = build_design_exports(req, result, domestication={"proposed-source": proposal})
    restored = read_design_export(exports["design.json"])
    assert restored.domestication["proposed-source"] == proposal
    assert portable_source(restored, "proposed-source").sequence == source
    assert restored.request == req
    primer = rows(exports["primers.csv"])[0]
    assert primer["part_name"] == data["parts"][1]["name"]
    assert json.loads(primer["effective_tm_settings"])["dmso_percent"] == 2
    assert json.loads(primer["requested_tm_settings"])["primer_concentration_nM"] == 325


@pytest.mark.parametrize("change", ["version", "selection", "missing-source", "swapped-source"])
def test_corrupt_portable_references_are_not_silently_repaired(change):
    req = request()
    exports = build_design_exports(req, design_golden_gate(req))
    payload = json.loads(exports["design.json"])
    if change == "version":
        payload["schema_version"] = "future-version"
    elif change == "selection":
        payload["result"]["selected_solution_id"] = "absent"
    elif change == "swapped-source":
        payload["source_material_ids"]["insert"] = "source:donor"
    else:
        payload["source_material_ids"] = {}
    with pytest.raises(ValueError):
        read_design_export(json.dumps(payload))
