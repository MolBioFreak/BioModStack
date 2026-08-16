from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_comparison_panel_attribution.py"


def _module():
    spec = importlib.util.spec_from_file_location("comparison_panel", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_panel_snapshot_is_local_digest_bound_and_never_attributes_unclassified_reads(tmp_path: Path) -> None:
    module = _module()
    panel_fasta = tmp_path / "panel.fasta"
    panel_fasta.write_text(">ecoli_background\nACGTACGT\n", encoding="utf-8")
    digest = hashlib.sha256(panel_fasta.read_bytes()).hexdigest()
    snapshot = tmp_path / "panel.json"
    snapshot.write_text(json.dumps({"schema": "bms.ngs.comparison-panel.v1", "entries": [{
        "id": "ecoli_background", "role": "host", "label": "E. coli control reference", "fasta_path": "panel.fasta", "fasta_sha256": digest,
    }]}), encoding="utf-8")

    normalized = module.load_panel_snapshot(snapshot)
    assert normalized["entries"][0]["id"] == "ecoli_background"
    assert normalized["entries"][0]["role"] == "host"
    assert normalized["entries"][0]["fasta_sha256"] == digest
    assert module.classify_primary_read("*") == "unclassified"
    assert module.classify_primary_read("expected") == "expected_plasmid_unique"
    assert module.classify_primary_read("ecoli_background") == "panel_reference_unique"
    assert module.classify_primary_read("expected,ecoli_background") == "ambiguous_multimapping"


def test_panel_snapshot_rejects_path_escape_digest_mismatch_and_unknown_records(tmp_path: Path) -> None:
    module = _module()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "bms.ngs.comparison-panel.v1", "entries": [{
        "id": "x", "role": "plasmid_decoy", "label": "x", "fasta_path": "../outside.fasta", "fasta_sha256": "0" * 64,
    }]}), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_panel_snapshot(bad)
    local = tmp_path / "local.fasta"
    local.write_text(">local\nACGT\n", encoding="utf-8")
    bad.write_text(json.dumps({"schema": "bms.ngs.comparison-panel.v1", "entries": [{
        "id": "x", "role": "host", "label": "x", "fasta_path": "local.fasta", "fasta_sha256": "0" * 64,
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="digest-mismatched"):
        module.load_panel_snapshot(bad)
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        module.load_panel_snapshot(bad)


def test_panel_roles_survive_normalization_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    host = tmp_path / "host.fasta"
    decoy = tmp_path / "decoy.fasta"
    expected = tmp_path / "expected.fasta"
    fastq = tmp_path / "reads.fastq"
    host.write_text(">host\nACGT\n", encoding="utf-8")
    decoy.write_text(">decoy\nTGCA\n", encoding="utf-8")
    expected.write_text(">expected\nACGT\n", encoding="utf-8")
    fastq.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")
    snapshot = tmp_path / "panel.json"
    snapshot.write_text(json.dumps({
        "schema": module.SCHEMA,
        "entries": [
            {"id": "host", "role": "host", "label": "Host", "fasta_path": "host.fasta", "fasta_sha256": hashlib.sha256(host.read_bytes()).hexdigest()},
            {"id": "decoy", "role": "plasmid_decoy", "label": "Decoy", "fasta_path": "decoy.fasta", "fasta_sha256": hashlib.sha256(decoy.read_bytes()).hexdigest()},
        ],
    }), encoding="utf-8")
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(sys, "argv", [
        "build_comparison_panel_attribution.py",
        "--snapshot", str(snapshot),
        "--expected-fasta", str(expected),
        "--fastq", str(fastq),
        "--combined-fasta", str(tmp_path / "combined.fasta"),
        "--summary", str(summary),
    ])

    module.main()

    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert [entry["role"] for entry in payload["panel"]["entries"]] == ["host", "plasmid_decoy"]


def test_classifier_accounts_for_every_fastq_read_and_all_eligible_alignments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    fastq = tmp_path / "reads.fastq"
    fastq.write_text("@expected\nACGT\n+\n!!!!\n@panel\nACGT\n+\n!!!!\n@ambiguous\nACGT\n+\n!!!!\n@low\nACGT\n+\n!!!!\n@unmapped\nACGT\n+\n!!!!\n", encoding="utf-8")
    normalized = tmp_path / "normalized.fastq"
    occurrence_map = tmp_path / "occurrence_map.json"
    metadata = module.prepare_occurrences(fastq, normalized, occurrence_map)
    panel = {"entries": [{"id": "background", "role": "host"}]}
    ids = [module._occurrence_id(ordinal) for ordinal in range(1, 6)]

    class Result:
        stdout = "\n".join([
            f"{ids[0]}\t0\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            f"{ids[1]}\t0\tpanel__background\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            # secondary target is still a competing eligible alignment
            f"{ids[2]}\t256\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            f"{ids[2]}\t0\tpanel__background\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:95",
            f"{ids[3]}\t0\texpected_plasmid\t1\t19\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            f"{ids[4]}\t4\t*\t0\t0\t*\t*\t0\t0\tACGT\t!!!!",
        ])

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    evidence = module._summarize_sam(
        "samtools", tmp_path / "unused.bam", 20, 10, panel, normalized, occurrence_map,
        source_fastq_sha256=metadata["source_fastq_sha256"],
    )
    assert evidence["categories"] == {
        "expected_plasmid_unique": 1,
        "panel_reference_unique": 1,
        "ambiguous_multimapping": 1,
        "unclassified": 2,
    }
    assert {row["read_id"] for row in evidence["rows"]} == {"expected", "panel", "ambiguous", "low", "unmapped"}
    assert all(set(row) == {"read_id", "ordinal", "occurrence_id", "accepted_references", "category", "role"} for row in evidence["rows"])


def test_reference_and_panel_fastas_must_be_exactly_one_valid_record(tmp_path: Path) -> None:
    module = _module()
    multi = tmp_path / "multi.fasta"
    multi.write_text(">one\nACGT\n>two\nTGCA\n", encoding="utf-8")
    digest = hashlib.sha256(multi.read_bytes()).hexdigest()
    snapshot = tmp_path / "panel.json"
    snapshot.write_text(json.dumps({"schema": module.SCHEMA, "entries": [{
        "id": "panel", "role": "plasmid_decoy", "label": "Panel", "fasta_path": "multi.fasta", "fasta_sha256": digest,
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        module.load_panel_snapshot(snapshot)


def test_score_margin_prevents_false_ambiguity_from_distant_secondary_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    fastq = tmp_path / "reads.fastq"
    fastq.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")
    class Result:
        stdout = "\n".join([
            f"{module._occurrence_id(1)}\t0\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            f"{module._occurrence_id(1)}\t256\tpanel__background\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:70",
        ])
    normalized = tmp_path / "normalized.fastq"
    occurrence_map = tmp_path / "occurrence_map.json"
    metadata = module.prepare_occurrences(fastq, normalized, occurrence_map)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    evidence = module._summarize_sam(
        "samtools", tmp_path / "unused.bam", 20, 10,
        {"entries": [{"id": "background", "role": "host"}]}, normalized, occurrence_map,
        source_fastq_sha256=metadata["source_fastq_sha256"],
    )
    assert evidence["categories"]["expected_plasmid_unique"] == 1
    assert evidence["categories"]["ambiguous_multimapping"] == 0


def test_duplicate_qnames_remain_distinct_occurrences_and_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    fastq = tmp_path / "duplicate.fastq"
    fastq.write_text("@same\nACGT\n+\n!!!!\n@same\nTGCA\n+\n####\n", encoding="utf-8")
    normalized = tmp_path / "normalized.fastq"
    occurrence_map = tmp_path / "occurrence_map.json"
    metadata = module.prepare_occurrences(fastq, normalized, occurrence_map)
    assert normalized.read_text(encoding="utf-8").splitlines()[0::4] == ["@bms_occurrence_000000000001", "@bms_occurrence_000000000002"]
    mapping = json.loads(occurrence_map.read_text(encoding="utf-8"))
    assert mapping["source_fastq_sha256"] == metadata["source_fastq_sha256"]
    assert [(item["read_id"], item["ordinal"]) for item in mapping["occurrences"]] == [("same", 1), ("same", 2)]

    class Result:
        stdout = "\n".join([
            f"{module._occurrence_id(1)}\t0\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            f"{module._occurrence_id(2)}\t0\tpanel__host\t1\t60\t4M\t*\t0\t0\tTGCA\t####\tAS:i:100",
        ])

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    evidence = module._summarize_sam(
        "samtools", tmp_path / "unused.bam", 20, 10,
        {"entries": [{"id": "host", "role": "host"}]}, normalized, occurrence_map,
        source_fastq_sha256=metadata["source_fastq_sha256"],
    )
    assert [(row["read_id"], row["ordinal"], row["occurrence_id"]) for row in evidence["rows"]] == [
        ("same", 1, module._occurrence_id(1)),
        ("same", 2, module._occurrence_id(2)),
    ]
    assert evidence["classified_read_count"] == 2
