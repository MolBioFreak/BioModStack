from __future__ import annotations

import hashlib
import importlib.util
import json
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
        "id": "ecoli_background", "label": "E. coli control reference", "fasta_path": "panel.fasta", "fasta_sha256": digest,
    }]}), encoding="utf-8")

    normalized = module.load_panel_snapshot(snapshot)
    assert normalized["entries"][0]["id"] == "ecoli_background"
    assert normalized["entries"][0]["fasta_sha256"] == digest
    assert module.classify_primary_read("*") == "unclassified"
    assert module.classify_primary_read("expected") == "expected_plasmid_unique"
    assert module.classify_primary_read("ecoli_background") == "panel_reference_unique"
    assert module.classify_primary_read("expected,ecoli_background") == "ambiguous_multimapping"


def test_panel_snapshot_rejects_path_escape_digest_mismatch_and_unknown_records(tmp_path: Path) -> None:
    module = _module()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "bms.ngs.comparison-panel.v1", "entries": [{
        "id": "x", "label": "x", "fasta_path": "../outside.fasta", "fasta_sha256": "0" * 64,
    }]}), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_panel_snapshot(bad)
    local = tmp_path / "local.fasta"
    local.write_text(">local\nACGT\n", encoding="utf-8")
    bad.write_text(json.dumps({"schema": "bms.ngs.comparison-panel.v1", "entries": [{
        "id": "x", "label": "x", "fasta_path": "local.fasta", "fasta_sha256": "0" * 64,
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="digest-mismatched"):
        module.load_panel_snapshot(bad)
    bad.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        module.load_panel_snapshot(bad)


def test_classifier_accounts_for_every_fastq_read_and_all_eligible_alignments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    fastq = tmp_path / "reads.fastq"
    fastq.write_text("@expected\nACGT\n+\n!!!!\n@panel\nACGT\n+\n!!!!\n@ambiguous\nACGT\n+\n!!!!\n@low\nACGT\n+\n!!!!\n@unmapped\nACGT\n+\n!!!!\n", encoding="utf-8")
    panel = {"entries": [{"id": "background"}]}

    class Result:
        stdout = "\n".join([
            "expected\t0\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            "panel\t0\tpanel__background\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            # secondary target is still a competing eligible alignment
            "ambiguous\t256\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            "ambiguous\t0\tpanel__background\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:95",
            "low\t0\texpected_plasmid\t1\t19\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            "unmapped\t4\t*\t0\t0\t*\t*\t0\t0\tACGT\t!!!!",
        ])

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    evidence = module._summarize_sam("samtools", tmp_path / "unused.bam", 20, 10, panel, fastq)
    assert evidence["counts"] == {
        "expected_plasmid_unique": 1,
        "panel_reference_unique": 1,
        "ambiguous_multimapping": 1,
        "unclassified": 2,
    }
    assert {row["read_id"] for row in evidence["rows"]} == {"expected", "panel", "ambiguous", "low", "unmapped"}


def test_reference_and_panel_fastas_must_be_exactly_one_valid_record(tmp_path: Path) -> None:
    module = _module()
    multi = tmp_path / "multi.fasta"
    multi.write_text(">one\nACGT\n>two\nTGCA\n", encoding="utf-8")
    digest = hashlib.sha256(multi.read_bytes()).hexdigest()
    snapshot = tmp_path / "panel.json"
    snapshot.write_text(json.dumps({"schema": module.SCHEMA, "entries": [{
        "id": "panel", "label": "Panel", "fasta_path": "multi.fasta", "fasta_sha256": digest,
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        module.load_panel_snapshot(snapshot)


def test_score_margin_prevents_false_ambiguity_from_distant_secondary_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    fastq = tmp_path / "reads.fastq"
    fastq.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")
    class Result:
        stdout = "\n".join([
            "read\t0\texpected_plasmid\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:100",
            "read\t256\tpanel__background\t1\t60\t4M\t*\t0\t0\tACGT\t!!!!\tAS:i:70",
        ])
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    evidence = module._summarize_sam("samtools", tmp_path / "unused.bam", 20, 10, {"entries": [{"id": "background"}]}, fastq)
    assert evidence["counts"]["expected_plasmid_unique"] == 1
    assert evidence["counts"]["ambiguous_multimapping"] == 0
