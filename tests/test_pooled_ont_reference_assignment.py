from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat

import pytest
from jsonschema import Draft202012Validator

from scripts.pooled_ont_reference_assignment import (
    PooledAssignmentError,
    AlignmentEvidence,
    FastqRecord,
    canonical_manifest_sha256,
    classify_assignments,
    occurrence_id_for_ordinal,
    run_classify,
    run_preflight,
    sha256_file,
    validate_reference_set,
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _write_manifest(
    root: Path,
    sequences: list[tuple[str, str]],
    *,
    groups: dict[str, str] | None = None,
) -> Path:
    refs = root / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    entries = []
    for ordinal, (target_id, sequence) in enumerate(sequences, start=1):
        fasta = refs / f"{target_id}.fasta"
        fasta.write_text(f">source_{target_id}\n{sequence}\n", encoding="ascii")
        entry = {
            "target_id": target_id,
            "label": f"Target {target_id}",
            "molbio_sequence_id": f"sequence-{ordinal}",
            "molbio_revision_id": f"revision-{ordinal}",
            "revision_sha256": _sha256_text(sequence),
            "fasta_path": f"refs/{target_id}.fasta",
            "fasta_sha256": sha256_file(fasta),
        }
        if groups and target_id in groups:
            entry["indistinguishable_group"] = groups[target_id]
        entries.append(entry)
    payload = {
        "schema": "bms.ngs.reference-set.v1",
        "mode": "pooled",
        "manifest_id": "pool-test-001",
        "manifest_sha256": "0" * 64,
        "entries": entries,
    }
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _record(
    occurrence_id: str,
    sequence: str = "ACGT",
    *,
    source_read_id: str | None = None,
    header: str | None = None,
    ordinal: int = 1,
) -> FastqRecord:
    source_read_id = source_read_id or occurrence_id
    return FastqRecord(
        ordinal=ordinal,
        occurrence_id=occurrence_id,
        source_read_id=source_read_id,
        source_header=header or source_read_id,
        sequence=sequence,
        quality="I" * len(sequence),
    )


def test_unique_competitive_assignment(tmp_path: Path) -> None:
    reference_set = validate_reference_set(
        _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    )
    assignments = classify_assignments(
        [_record("read-1"), _record("read-2", "TTTT")],
        {
            "read-1": [AlignmentEvidence("read-1", "target-a", 60, 100, False)],
            "read-2": [AlignmentEvidence("read-2", "target-b", 60, 100, False)],
        },
        reference_set,
        min_mapq=20,
        min_alignment_score_margin=10,
    )
    assert [item.disposition for item in assignments] == ["target:target-a", "target:target-b"]


def test_tie_and_shared_backbone_score_margin_is_ambiguous(tmp_path: Path) -> None:
    reference_set = validate_reference_set(
        _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "ACGTAAAA")])
    )
    assignment = classify_assignments(
        [_record("read-1")],
        {
            "read-1": [
                AlignmentEvidence("read-1", "target-a", 60, 100, False),
                AlignmentEvidence("read-1", "target-b", 60, 95, True),
            ]
        },
        reference_set,
        min_mapq=20,
        min_alignment_score_margin=5,
    )[0]
    assert assignment.disposition == "ambiguous"
    assert assignment.reason == "near_tie_within_score_margin"
    assert assignment.score_delta == 5


def test_unclassified_when_no_alignment_meets_min_mapq(tmp_path: Path) -> None:
    reference_set = validate_reference_set(
        _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    )
    assignment = classify_assignments(
        [_record("read-1")],
        {"read-1": [AlignmentEvidence("read-1", "target-a", 19, 100, False)]},
        reference_set,
        min_mapq=20,
        min_alignment_score_margin=10,
    )[0]
    assert assignment.disposition == "unclassified"
    assert assignment.reason == "no_alignment_at_min_mapq"


def test_identical_entries_require_common_group_and_remain_ambiguous(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "ACGTACGT")])
    with pytest.raises(PooledAssignmentError, match="identical FASTA entries require"):
        validate_reference_set(manifest)

    grouped_manifest = _write_manifest(
        tmp_path / "grouped",
        [("target-a", "ACGTACGT"), ("target-b", "ACGTACGT")],
        groups={"target-a": "same-sequence", "target-b": "same-sequence"},
    )
    reference_set = validate_reference_set(grouped_manifest)
    assignment = classify_assignments(
        [_record("read-1")],
        {"read-1": [AlignmentEvidence("read-1", "target-a", 60, 100, False)]},
        reference_set,
        min_mapq=20,
        min_alignment_score_margin=0,
    )[0]
    assert assignment.disposition == "ambiguous"
    assert assignment.reason == "identical_targets_require_indistinguishable_group"


def test_digest_mismatch_is_rejected_before_execution(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    fasta = tmp_path / "refs" / "target-a.fasta"
    fasta.write_text(">source_target-a\nACGTACGA\n", encoding="ascii")
    with pytest.raises(PooledAssignmentError, match="FASTA file digest mismatch"):
        validate_reference_set(manifest)


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"][0]["fasta_path"] = "../outside.fasta"
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PooledAssignmentError, match="reference-set manifest|fasta_path"):
        validate_reference_set(manifest)


def test_strict_keys_reject_unknown_manifest_fields(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PooledAssignmentError, match="keys are not exact"):
        validate_reference_set(manifest)


def test_reference_set_schema_and_snapshot_symlink_contract(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas/ngs/reference_set.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)

    link = tmp_path / "refs" / "linked.fasta"
    link.symlink_to(tmp_path / "refs" / "target-a.fasta")
    payload["entries"][0]["fasta_path"] = "refs/linked.fasta"
    payload["entries"][0]["fasta_sha256"] = sha256_file(tmp_path / "refs" / "target-a.fasta")
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PooledAssignmentError, match="symlink"):
        validate_reference_set(manifest)


def test_duplicate_revision_and_multiple_fasta_records_are_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"][1]["molbio_revision_id"] = payload["entries"][0]["molbio_revision_id"]
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PooledAssignmentError, match="duplicate molbio_revision_id"):
        validate_reference_set(manifest)

    manifest = _write_manifest(tmp_path / "multirecord", [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    fasta = manifest.parent / "refs" / "target-a.fasta"
    fasta.write_text(">one\nACGTACGT\n>two\nTTTTCCCC\n", encoding="ascii")
    payload["entries"][0]["fasta_sha256"] = sha256_file(fasta)
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(PooledAssignmentError, match="exactly one record"):
        validate_reference_set(manifest)


def test_workflow_and_profile_are_review_only_and_use_competitive_alignment() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / "workflows/ngs/ont_pooled_reference_assignment.nf").read_text(encoding="utf-8")
    config = (root / "nextflow.config").read_text(encoding="utf-8")
    dispatcher = (root / "ngs.nf").read_text(encoding="utf-8")
    model_config = (root / "platform/api/config/models/nanopore.yaml").read_text(encoding="utf-8")
    assert "-x map-ont" in workflow
    assert "--secondary=yes" in workflow
    assert "samtools sort" in workflow
    assert "samtools index" in workflow
    assert "assignment_summary.json" in workflow
    assert "occurrence_map.json" in workflow
    assert "scientific_status" in (root / "scripts/pooled_ont_reference_assignment.py").read_text(encoding="utf-8")
    assert "ont_pooled_reference_assignment" in config
    assert 'container = "${params.container_dir}/dorado.sif"' in config
    assert "ont_pooled_reference_assignment" in dispatcher
    assert "ont_pooled_reference_assignment" in model_config
    for forbidden in ("FastqDimerAnalysis", "FastqPlasmidQC", "ConstructVerify", "consensus", "dimer"):
        assert forbidden not in workflow


def test_exact_count_closure_and_review_artifacts(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    manifest = _write_manifest(snapshot, [("target-a", "ACGTACGT"), ("target-b", "TTTTCCCC")])
    source_fastq = tmp_path / "reads.fastq"
    source_fastq.write_text(
        "@duplicate first\nACGT\n+\nIIII\n"
        "@duplicate second\nGGGG\n+\nIIII\n"
        "@tie\nACGT\n+\nIIII\n"
        "@rejected\nACGT\n+\nIII\n",
        encoding="ascii",
    )
    output = tmp_path / "preflight"
    run_preflight(manifest, snapshot, source_fastq, output)
    preflight = json.loads((output / "fastq_preflight.json").read_text(encoding="utf-8"))
    assert preflight["input_records"] == 4
    assert preflight["valid_fastq_reads"] == 3
    assert preflight["rejected_by_input_policy"] == 1
    assert "duplicate_read_id" not in preflight["rejected_reasons"]
    assert preflight["occurrence_map_path"] == "occurrence_map.json"
    assert preflight["occurrence_map_count"] == 3
    assert preflight["occurrence_map_sha256"] == sha256_file(output / "occurrence_map.json")
    assert (output / "valid_reads.fastq").read_text(encoding="ascii") == (
        "@occurrence_1\nACGT\n+\nIIII\n"
        "@occurrence_2\nGGGG\n+\nIIII\n"
        "@occurrence_3\nACGT\n+\nIIII\n"
    )
    occurrence_map = json.loads((output / "occurrence_map.json").read_text(encoding="utf-8"))
    assert occurrence_map["count"] == 3
    assert [record["occurrence_id"] for record in occurrence_map["records"]] == [
        occurrence_id_for_ordinal(1),
        occurrence_id_for_ordinal(2),
        occurrence_id_for_ordinal(3),
    ]
    assert [record["source_read_id"] for record in occurrence_map["records"][:2]] == [
        "duplicate",
        "duplicate",
    ]
    assert [record["source_header"] for record in occurrence_map["records"][:2]] == [
        "duplicate first",
        "duplicate second",
    ]
    assert [record["input_ordinal"] for record in occurrence_map["records"]] == [1, 2, 3]

    bam = output / "pooled_assignment.bam"
    bai = output / "pooled_assignment.bam.bai"
    bam.write_bytes(b"synthetic sorted BAM evidence")
    bai.write_bytes(b"synthetic BAM index")
    (output / "combined_intended_reference.fasta.fai").write_text(
        "target-a\t8\t9\t8\t9\ntarget-b\t8\t27\t8\t9\n", encoding="ascii"
    )
    (output / "pooled_reference_assignment.minimap2.log").write_text("synthetic minimap2 evidence\n", encoding="utf-8")
    sam = "\n".join(
        [
            "@HD\tVN:1.6\tSO:coordinate",
            "@SQ\tSN:target-a\tLN:8",
            "@SQ\tSN:target-b\tLN:8",
            "occurrence_1\t0\ttarget-a\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\tAS:i:100",
            "occurrence_2\t4\t*\t0\t0\t*\t*\t0\t0\tGGGG\tIIII",
            "occurrence_3\t0\ttarget-a\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\tAS:i:100",
            "occurrence_3\t256\ttarget-b\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\tAS:i:95",
            "",
        ]
    )
    samtools = tmp_path / "samtools"
    samtools.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"print({sam!r}, end='')\n",
        encoding="utf-8",
    )
    samtools.chmod(samtools.stat().st_mode | stat.S_IXUSR)

    run_classify(
        manifest,
        snapshot,
        source_fastq,
        output / "valid_reads.fastq",
        output / "fastq_preflight.json",
        bam,
        [str(samtools)],
        output / "combined_intended_reference.fasta",
        output,
        20,
        10,
    )
    summary = json.loads((output / "assignment_summary.json").read_text(encoding="utf-8"))
    assert summary["scientific_status"] == "REVIEW"
    assert summary["release_state"] == "awaiting_operator_release"
    assert summary["counts"] == {
        "input_fastq_records": 4,
        "valid_fastq_reads": 3,
        "occurrence_map_count": 3,
        "rejected_by_input_policy": 1,
        "target_assigned_reads": 1,
        "ambiguous_reads": 1,
        "unclassified_reads": 1,
    }
    assert summary["accounting"] == {
        "valid_fastq_reads": 3,
        "occurrence_map_count": 3,
        "sum_of_dispositions": 3,
        "input_fastq_records": 4,
        "valid_plus_rejected": 4,
        "occurrence_map_matches_valid_fastq_reads": True,
        "closure": True,
    }
    assert summary["occurrence_map_path"] == "occurrence_map.json"
    assert summary["occurrence_map_sha256"] == sha256_file(output / "occurrence_map.json")
    assert summary["occurrence_map_count"] == 3
    assert [row["occurrence_id"] for row in summary["read_assignments"]] == [
        "occurrence_1",
        "occurrence_2",
        "occurrence_3",
    ]
    assert [row["source_read_id"] for row in summary["read_assignments"][:2]] == [
        "duplicate",
        "duplicate",
    ]
    assert [row["input_ordinal"] for row in summary["read_assignments"]] == [1, 2, 3]
    assert [row["disposition"] for row in summary["read_assignments"][:2]] == [
        "target:target-a",
        "unclassified",
    ]
    rows = (output / "per_read_assignment.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[0].split("\t")[:4] == [
        "occurrence_id",
        "source_read_id",
        "source_header",
        "input_ordinal",
    ]
    assert rows[1].split("\t")[:5] == ["occurrence_1", "duplicate", "duplicate first", "1", "target:target-a"]
    assert rows[2].split("\t")[:5] == ["occurrence_2", "duplicate", "duplicate second", "2", "unclassified"]
    assert rows[3].split("\t")[:5] == ["occurrence_3", "tie", "tie", "3", "ambiguous"]
    assert (output / "target_target-a.fastq").read_text(encoding="ascii").startswith("@occurrence_1\n")
    assert (output / "unclassified.fastq").read_text(encoding="ascii").startswith("@occurrence_2\n")
    assert (output / "ambiguous.fastq").read_text(encoding="ascii").startswith("@occurrence_3\n")
    session = json.loads((output / "intended_pool.igv_session.json").read_text(encoding="utf-8"))
    assert session["artifacts"]
    assert all(len(artifact["sha256"]) == 64 for artifact in session["artifacts"])
    assert session["occurrence_map_path"] == "occurrence_map.json"
    assert session["occurrence_map_sha256"] == sha256_file(output / "occurrence_map.json")
    assert session["occurrence_map_count"] == 3
    occurrence_artifacts = [
        artifact for artifact in session["artifacts"] if artifact["kind"] == "occurrence_map"
    ]
    assert occurrence_artifacts == [
        {
            "count": 3,
            "kind": "occurrence_map",
            "path": "occurrence_map.json",
            "sha256": sha256_file(output / "occurrence_map.json"),
        }
    ]
