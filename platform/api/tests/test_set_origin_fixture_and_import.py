"""A9a: the TS set-origin cases are the mapper's output (shared fixture), and server import keeps origin-spanning features (I1)."""
import json
from pathlib import Path

from services.assembly.feature_mapping import map_stored_features
from services.molbio_sequence_import import SequenceImportRequest, build_sequence_import_preview

FIXTURE = Path(__file__).resolve().parents[2] / "frontend/tests/fixtures/set-origin-cases.json"


def test_set_origin_fixture_equals_python_mapper():
    data = json.loads(FIXTURE.read_text())
    seq, n = data["sequence"], len(data["sequence"])
    stored = data["features"] + [
        {"id": p["id"], "name": p["id"], "type": "primer_bind", "start": p["sites"][0]["start"], "end": p["sites"][0]["end"],
         "strand": p["strand"], "segments": [{"start": p["sites"][0]["start"], "end": p["sites"][0]["end"]}]}
        for p in data["primers"]]
    for case in data["cases"]:
        shift = case["shift"]
        positions = [(i + shift) % n for i in range(n)]
        if case["reverseComplement"]:
            positions.reverse()
        out = {f["id"]: f for f in map_stored_features(stored, seq, "circular", positions, -1 if case["reverseComplement"] else 1)}
        for f in case["features"]:
            assert out[f["id"]]["segments"] == f["segments"] and out[f["id"]]["strand"] == f["strand"]
        for p in case["primerSites"]:
            assert out[p["id"]]["segments"] == p["segments"]


def test_server_import_keeps_origin_spanning_feature_as_ordered_segments():
    seq = ("ACGT" * 30)
    text = ("LOCUS       t   120 bp    DNA     circular SYN 01-JAN-2026\nFEATURES             Location/Qualifiers\n"
            "     gene            join(110..120,1..8)\n                     /label=\"wrap\"\n"
            "     CDS             complement(join(10..30,50..70))\n                     /label=\"minus\"\n"
            "                     /codon_start=2\n     primer_bind     complement(60..80)\n                     /label=\"pr\"\nORIGIN\n"
            + "".join(f"{i*60+1:9d} " + " ".join(seq[i*60:(i+1)*60][j:j+10] for j in range(0, 60, 10)) + "\n" for i in range(2)) + "//\n")
    request = SequenceImportRequest(source_format="genbank", source_text=text, topology_default="linear", topology_overrides={1: "circular"})
    record = build_sequence_import_preview(request)["records"][0]
    by_name = {f["name"]: f for f in record["features"]}
    assert by_name["wrap"]["segments"] == [{"start": 109, "end": 120}, {"start": 0, "end": 8}]
    assert by_name["minus"]["segments"] == [{"start": 49, "end": 70}, {"start": 9, "end": 30}]
    assert by_name["minus"]["qualifiers"]["codon_start"] == ["2"]
    assert by_name["pr"]["strand"] == -1 and by_name["pr"]["segments"] == [{"start": 59, "end": 80}]
