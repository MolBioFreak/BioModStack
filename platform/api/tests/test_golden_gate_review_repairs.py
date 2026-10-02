"""Independent coordinate/translation and pipetting regressions from review."""
from __future__ import annotations

from fractions import Fraction
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
import pytest

from services.assembly.golden_gate_design import _map_features
from services.assembly.golden_gate_design_types import Feature, Material, Region
from services.assembly.golden_gate_reaction import (
    calculate_reaction, ReactionRequest, ReactionSettings, ReactionPart, DNAAmount, DNAStock, Dilution,
)


CDS = "ATGAAACCCGGGTTTAAACCCGGGTAA"


def extract(sequence, feature):
    parts = []
    for segment in feature.segments:
        spans = [(segment.start, len(sequence)), (0, segment.end)] if segment.wraps_origin else [(segment.start, segment.end)]
        parts.extend(FeatureLocation(a, b, strand=feature.strand or None) for a, b in spans if a < b)
    location = parts[0] if len(parts) == 1 else CompoundLocation(parts)
    return str(SeqFeature(location).extract(Seq(sequence)))


@pytest.mark.parametrize("source_strand", [1, -1])
@pytest.mark.parametrize("orientation", [1, -1])
@pytest.mark.parametrize("removed", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("old_start", [1, 2, 3])
def test_partial_cds_phase_tracks_biological_five_prime_loss(source_strand, orientation, removed, old_start):
    source = CDS if source_strand == 1 else str(Seq(CDS).reverse_complement())
    positions = list(range(removed, len(source))) if source_strand == 1 else list(range(len(source) - removed))
    if orientation == -1:
        positions.reverse()
    output = ''.join(source[p] for p in positions)
    if orientation == -1:
        output = str(Seq(output).complement())
    feature = Feature(id="cds", type="CDS", segments=[Region(start=0, end=len(source))],
        strand=source_strand, codon_start=old_start,
        qualifiers={"codon_start": [str(old_start)], "translation": ["STALE"], "note": ["retained annotation"]})
    mapped = _map_features([feature], Material(sequence=source, topology="linear"), positions, strand=orientation)[0]
    phase = (old_start - 1 - removed) % 3
    assert mapped.codon_start == phase + 1
    assert mapped.qualifiers["codon_start"] == [str(phase + 1)]
    assert extract(output, mapped) == CDS[removed:]
    partial = extract(output, mapped)[phase:]
    expected_start = removed + phase
    assert str(Seq(partial[:len(partial) // 3 * 3]).translate()) == str(Seq(CDS[expected_start:expected_start + len(partial) // 3 * 3]).translate())
    if removed:
        assert mapped.status == "truncated" and mapped.frame_preserved is False
        assert "translation" not in mapped.qualifiers
    else:
        assert mapped.status == "intact" and mapped.qualifiers["translation"] == ["STALE"]
    assert feature.codon_start == old_start and feature.qualifiers["translation"] == ["STALE"]
    assert mapped.qualifiers["note"] == ["retained annotation"]


@pytest.mark.parametrize("source_strand", [1, -1])
@pytest.mark.parametrize("orientation", [1, -1])
@pytest.mark.parametrize("origin", [0, 5, 19])
def test_compound_cds_keeps_ordered_biological_sequence_across_rotation(source_strand, orientation, origin):
    source = "ATGAAACCCGGGTTTAAACCCGGGTAATGC"
    regions = [Region(start=18, end=27), Region(start=3, end=12)]
    feature = Feature(id="ordered", type="CDS", segments=regions, strand=source_strand, codon_start=2)
    # Independent Biopython extraction is the same location convention as exported GenBank.
    expected = extract(source, feature)
    positions = list(range(len(source)))
    if orientation == -1:
        positions.reverse()
    positions = positions[origin:] + positions[:origin]
    output = ''.join(source[p] for p in positions)
    if orientation == -1:
        output = str(Seq(output).complement())
    mapped = _map_features([feature], Material(sequence=source, topology="circular"), positions, strand=orientation)[0]
    assert extract(output, mapped) == expected
    assert mapped.codon_start == 2 and mapped.status == "intact" and mapped.frame_preserved is True


def test_internal_cds_loss_is_disrupted_not_a_continuous_translation():
    feature = Feature(id="cds", type="CDS", segments=[Region(start=0, end=len(CDS))], codon_start=1,
        qualifiers={"translation": ["MKPGFKPG*"]})
    positions = [p for p in range(len(CDS)) if p != 7]
    mapped = _map_features([feature], Material(sequence=CDS, topology="linear"), positions)[0]
    assert mapped.status == "disrupted" and mapped.frame_preserved is False
    assert "translation" not in mapped.qualifiers
    assert mapped.codon_start == 1


@pytest.mark.parametrize("qualifier", [["2"], "2", 2])
def test_partial_cds_uses_existing_qualifier_phase_without_new_gate(qualifier):
    feature = Feature(id="cds", type="CDS", segments=[Region(start=0, end=len(CDS))], qualifiers={"codon_start": qualifier})
    mapped = _map_features([feature], Material(sequence=CDS, topology="linear"), list(range(1, len(CDS))))[0]
    assert mapped.codon_start == 1
    expected = ["1"] if isinstance(qualifier, list) else "1" if isinstance(qualifier, str) else 1
    assert mapped.qualifiers["codon_start"] == expected


@pytest.mark.parametrize("factor, flagged", [(1000, "stock"), (1.01, "diluent"), (1, None), (2, None)])
def test_dilution_preparation_transfer_minimum_is_diagnostic_only(factor, flagged):
    request = ReactionRequest(settings=ReactionSettings(total_volume_uL=20, minimum_transfer_uL=0.5),
        parts=(ReactionPart(part_id="insert", amount=DNAAmount(value=1 / factor, unit="pmol"),
            stock=DNAStock(value=1, unit="pmol/uL"), dilution=Dilution(factor=factor, preparation_volume_uL=10)),))
    result = calculate_reaction(request)
    row = result.rows[0]
    assert row.dilution_stock_uL == pytest.approx(float(Fraction(10) / Fraction(str(factor))))
    assert row.dilution_diluent_uL == pytest.approx(10 - float(Fraction(10) / Fraction(str(factor))))
    assert row.transfer_volume_uL == pytest.approx(1)
    notes = [d for d in result.diagnostics if d.code == "below_minimum_transfer"]
    if flagged:
        assert any(d.component_id == "insert" and flagged in d.message.lower() for d in notes)
    else:
        assert notes == []
    assert result.request == request and result.total_transfer_uL == pytest.approx(20)
    assert "ready" not in result.model_dump() and "can_save" not in result.model_dump()


@pytest.mark.parametrize("sequence,enzymes", [("ACGTACGT", ("EcoRI",)), ("TTGAATTCAA", ("EcoRI",)), ("TTCTGCAGAA", ("PstI",))])
@pytest.mark.parametrize("topology", ["linear", "circular"])
def test_terminal_boundaries_normalize_only_on_circular_source(sequence, enzymes, topology):
    from test_restriction_digest_persistence import _simulate
    result = _simulate(sequence, enzymes, topology)
    for fragment in result.fragments:
        for strand in ("top", "bottom"):
            for side in ("start", "end"):
                name = f"{strand}_{side}"
                raw = getattr(fragment, name + "_boundary")
                normalized = getattr(fragment, name + "_boundary_normalized")
                winding = getattr(fragment, name + "_winding")
                if topology == "linear":
                    assert normalized == raw and winding == 0
                    assert 0 <= normalized <= len(sequence)
                else:
                    assert normalized == raw % len(sequence)
                    assert winding == raw // len(sequence)
    reconstructed = ''.join(f.top_strand_sequence for f in result.fragments)
    assert len(reconstructed) == len(sequence)
    assert reconstructed == sequence if topology == "linear" else reconstructed in sequence + sequence


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_repaired_phase_survives_native_save_reopen_and_genbank(tmp_path, monkeypatch, reverse):
    import io
    from Bio import SeqIO
    from test_golden_gate_design_core import request, A
    from test_golden_gate_workflow_receiving import client_store
    from services.assembly.golden_gate_workflow_types import WorkflowResult, SavedDesign, SaveDesignRequest
    from services.assembly.golden_gate_workflow import freeze_selection
    from services.assembly.golden_gate_exports import build_design_exports, read_design_export

    data = request(kind="synthesis", reverse=reverse).model_dump(mode="json")
    source = str(Seq(CDS).reverse_complement()) if reverse else CDS
    data["sources"][1]["source"].update(sequence=source, features=[dict(id="partial", type="CDS",
        segments=[dict(start=0, end=len(CDS))], strand=-1 if reverse else 1, codon_start=1,
        qualifiers={"codon_start": ["1"], "translation": ["MKPGFKPG*"]})])
    data["parts"][1]["preparation"]["region"] = dict(start=0 if reverse else 1, end=len(CDS) - 1 if reverse else len(CDS), wraps_origin=False)
    expected = "AATG" + A + "GGAG" + CDS[1:]
    data["target"].update(exact_sequence=expected[17:] + expected[:17], display_origin=17)
    async with client_store(tmp_path) as (client, _):
        preview = await client.post('/api/molbio/assembly/golden-gate/design', json=data)
        assert preview.status_code == 200, preview.text
        result = WorkflowResult.model_validate(preview.json())
        assert result.selected_solution_id is not None
        selected = freeze_selection(result, result.selected_solution_id)
        payload = SaveDesignRequest(selection=selected, name="partial CDS", idempotency_key="phase-retention")
        response = await client.post('/api/molbio/assembly/golden-gate/design/save', json=payload.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        def forbidden(*args, **kwargs):
            raise AssertionError("Reopen/export must use retained science")
        monkeypatch.setattr('services.assembly.golden_gate_workflow_persistence.run_workflow', forbidden)
        reopened = await client.get('/api/molbio/assembly/golden-gate/design/' + response.json()['operation_id'])
        assert reopened.status_code == 200 and reopened.json() == response.json()
        saved = SavedDesign.model_validate(reopened.json())
        candidate = saved.result.solutions[0]
        product = candidate.design.solutions[0]
        assert product.sequence == data["target"]["exact_sequence"]
        cds = next(f for f in product.features if f.id.endswith("partial"))
        assert cds.codon_start == 3 and cds.status == "truncated" and cds.frame_preserved is False
        assert extract(product.sequence, cds) == CDS[1:]
        exported = build_design_exports(candidate.fixed_request, candidate.design)
        portable = read_design_export(exported["design.json"])
        assert portable.result == candidate.design
        record = SeqIO.read(io.StringIO(exported['product.gb'].decode()), 'genbank')
        feature = next(f for f in record.features if f.qualifiers.get('bms_feature_id') == [cds.id])
        assert feature.qualifiers['codon_start'] == ['3'] and 'translation' not in feature.qualifiers
        assert str(feature.extract(record.seq)[2:].translate()) == 'KPGFKPG*'
