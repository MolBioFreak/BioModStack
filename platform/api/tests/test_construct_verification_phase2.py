from __future__ import annotations

import hashlib
import json
import math
import runpy
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_construct.py"
PROFILE_CONFIG = REPO_ROOT / "config" / "ngs" / "construct_verify_profiles.json"
SCHEMA = REPO_ROOT / "schemas" / "ngs" / "construct_verification_manifest.schema.json"
PYTHON = Path("/home/dalab/biomodstack/biomodstack/platform/api/.venv/bin/python")
SAMTOOLS = Path("/home/dalab/micromamba/bin/samtools")
REFERENCE = "ACGTTGCAACGTGATCGTACCTGACTGACCTAGGCTAACGTTAGC"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fasta(path: Path, sequence: str, name: str) -> None:
    path.write_text(f">{name}\n{sequence}\n", encoding="utf-8")


def _reverse_complement(sequence: str) -> str:
    return sequence.translate(str.maketrans("ACGT", "TGCA"))[::-1]


def _levenshtein_oracle(left: str, right: str) -> int:
    """Independent, intentionally simple exact oracle for short test strings."""
    previous = list(range(len(right) + 1))
    for left_index, left_base in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_base in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_base != right_base),
                )
            )
        previous = current
    return previous[-1]


def _circular_representations(sequence: str) -> list[str]:
    return sorted(
        {
            oriented[offset:] + oriented[:offset]
            for oriented in (sequence, _reverse_complement(sequence))
            for offset in range(len(oriented))
        }
    )


def _cigar_for(reference: str, sequence: str) -> str:
    import difflib

    operations: list[tuple[int, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, reference, sequence, autojunk=False).get_opcodes():
        reference_length = i2 - i1
        sequence_length = j2 - j1
        if tag == "equal":
            operations.append((reference_length, "M"))
        elif tag == "insert":
            operations.append((sequence_length, "I"))
        elif tag == "delete":
            operations.append((reference_length, "D"))
        else:
            paired = min(reference_length, sequence_length)
            if paired:
                operations.append((paired, "M"))
            if reference_length > paired:
                operations.append((reference_length - paired, "D"))
            if sequence_length > paired:
                operations.append((sequence_length - paired, "I"))
    merged: list[tuple[int, str]] = []
    for length, operation in operations:
        if merged and merged[-1][1] == operation:
            merged[-1] = (merged[-1][0] + length, operation)
        else:
            merged.append((length, operation))
    return "".join(f"{length}{operation}" for length, operation in merged)


def _write_support(
    path: Path,
    reference: str,
    *,
    depth: int = 30,
    overrides: dict[int, dict[str, int | float | str]] | None = None,
) -> None:
    columns = [
        "chrom",
        "position_1based",
        "reference_base",
        "depth",
        "forward_depth",
        "reverse_depth",
        "a_count",
        "c_count",
        "g_count",
        "t_count",
        "n_count",
        "insertion_count",
        "deletion_count",
        "consensus_base",
        "major_allele_fraction",
        "low_coverage",
        "ambiguous",
    ]
    rows = ["\t".join(columns)]
    for position, base in enumerate(reference, start=1):
        counts = {nucleotide: 0 for nucleotide in "ACGT"}
        counts[base] = depth
        values: dict[str, int | float | str] = {
            "depth": depth,
            "ref_count": depth,
            "A": counts["A"],
            "C": counts["C"],
            "G": counts["G"],
            "T": counts["T"],
            "N": 0,
            "deletion_count": 0,
            "insertion_count": 0,
            "forward_depth": depth // 2,
            "reverse_depth": depth - (depth // 2),
            "major_allele": base,
            "major_allele_count": depth,
            "major_allele_fraction": "1.000000" if depth else "0.000000",
        }
        values.update((overrides or {}).get(position, {}))
        output = {
            "chrom": "plasmid",
            "position_1based": position,
            "reference_base": values.get("reference_base", base),
            "depth": values["depth"],
            "forward_depth": values["forward_depth"],
            "reverse_depth": values["reverse_depth"],
            "a_count": values["A"],
            "c_count": values["C"],
            "g_count": values["G"],
            "t_count": values["T"],
            "n_count": values["N"],
            "insertion_count": values["insertion_count"],
            "deletion_count": values["deletion_count"],
            "consensus_base": values["major_allele"],
            "major_allele_fraction": values["major_allele_fraction"],
            "low_coverage": "false",
            "ambiguous": "false",
        }
        rows.append("\t".join(str(output[column]) for column in columns))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _run_case(
    tmp_path: Path,
    *,
    observed: str | None = REFERENCE,
    method: str = "bcftools_consensus",
    independent: bool = True,
    declared_digest: str | None = None,
    declared_reference_digest: str | None = "actual",
    support_overrides: dict[int, dict[str, int | float | str]] | None = None,
    support_depth: int | None = None,
    malformed_support: bool = False,
    alignment_counts: tuple[int, int, int] | None = None,
    bam_counts: tuple[int, int, int] = (30, 30, 0),
    topology_state: str = "present",
    origin_spanning_reads: int = 2,
    secondary_anomaly_fraction: float = 0.0,
    contradictory_breakpoint_evidence: bool = False,
    topology_bam_digest: str | None = None,
    alignment_index_state: str = "present",
    malformed_observed_state: bool = False,
    source_reads_digest: str | None = None,
    topology_overrides: dict[str, object] | None = None,
    topology_breakpoint_digest: str | None = None,
    topology_secondary_digest: str | None = None,
    verification_samtools: Path = SAMTOOLS,
) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    reference_path = tmp_path / "reference.fasta"
    observed_path = tmp_path / "observed_consensus.fasta"
    state_path = tmp_path / "observed_sequence.json"
    support_path = tmp_path / "per_base_support.tsv"
    alignment_stats_path = tmp_path / "alignment_stats.tsv"
    alignment_sam_path = tmp_path / "alignment.sam"
    alignment_bam_path = tmp_path / "alignment.bam"
    alignment_index_path = tmp_path / "alignment.bam.bai"
    topology_path = tmp_path / "topology_evidence.json"
    breakpoint_path = tmp_path / "dimer_breakpoint_call.tsv"
    secondary_path = tmp_path / "dimer_secondary_summary.tsv"
    out_dir = tmp_path / "verification"

    _write_fasta(reference_path, REFERENCE, "plasmid")
    if observed is not None:
        _write_fasta(observed_path, observed, "observed")
    bam_total, bam_mapped, bam_unmapped = bam_counts
    assert bam_mapped + bam_unmapped == bam_total
    mapped_sequences = [list(REFERENCE) for _ in range(bam_mapped)]
    for position, overrides in (support_overrides or {}).items():
        cursor = 0
        for base in "ACGT":
            count = int(overrides.get(base, bam_mapped if base == REFERENCE[position - 1] else 0))
            for index in range(cursor, min(cursor + count, bam_mapped)):
                mapped_sequences[index][position - 1] = base
            cursor += count
    mapped_sequence_text = ["".join(sequence) for sequence in mapped_sequences]
    indel_support = max(
        (
            int(overrides.get(column, 0))
            for overrides in (support_overrides or {}).values()
            for column in ("insertion_count", "deletion_count")
        ),
        default=0,
    )
    if observed is not None and len(observed) != len(REFERENCE):
        for index in range(min(indel_support, bam_mapped)):
            mapped_sequence_text[index] = observed
    split_reads = min(origin_spanning_reads, bam_mapped) if set(mapped_sequence_text) == {REFERENCE} else 0
    split_at = (len(REFERENCE) + 1) // 2
    right_length = len(REFERENCE) - split_at
    for index in range(split_reads):
        mapped_sequence_text[index] = REFERENCE[split_at:] + REFERENCE[:split_at]
    sam_lines = ["@HD\tVN:1.6\tSO:coordinate", f"@SQ\tSN:plasmid\tLN:{len(REFERENCE)}"]
    source_fastq = tmp_path / "source_reads.fastq"
    fastq_records: list[str] = []
    for index in range(bam_mapped):
        qname = f"read{index + 1}"
        flag = 16 if index % 2 else 0
        sequence = mapped_sequence_text[index]
        if index < split_reads:
            position = 1
            cigar = f"{right_length}S{split_at}M"
        else:
            position = 1
            cigar = _cigar_for(REFERENCE, sequence)
        sam_lines.append(
            f"{qname}\t{flag}\tplasmid\t{position}\t60\t{cigar}\t*\t0\t0\t{sequence}\t{'I' * len(sequence)}"
        )
        fastq_records.append(f"@{qname}\n{sequence}\n+\n{'I' * len(sequence)}\n")
    for index in range(split_reads):
        qname = f"read{index + 1}"
        flag = 2048 | (16 if index % 2 else 0)
        sequence = mapped_sequence_text[index]
        sam_lines.append(
            f"{qname}\t{flag}\tplasmid\t{split_at + 1}\t60\t{right_length}M{split_at}S\t*\t0\t0\t{sequence}\t{'I' * len(sequence)}"
        )
    for index in range(bam_unmapped):
        qname = f"unmapped{index + 1}"
        sam_lines.append(f"{qname}\t4\t*\t0\t0\t*\t*\t0\t0\t{REFERENCE}\t{'I' * len(REFERENCE)}")
        fastq_records.append(f"@{qname}\n{REFERENCE}\n+\n{'I' * len(REFERENCE)}\n")
    alignment_sam_path.write_text("\n".join(sam_lines) + "\n", encoding="utf-8")
    source_fastq.write_text("".join(fastq_records), encoding="utf-8")
    subprocess.run(
        [str(SAMTOOLS), "view", "-b", "-o", str(alignment_bam_path), str(alignment_sam_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(SAMTOOLS), "index", str(alignment_bam_path), str(alignment_index_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    if alignment_index_state == "missing":
        alignment_index_path.unlink()
    elif alignment_index_state == "corrupt":
        alignment_index_path.write_bytes(b"not-a-bai")
    elif alignment_index_state != "present":
        raise ValueError(f"unsupported alignment_index_state: {alignment_index_state}")
    effective_support_depth = bam_mapped if support_depth is None else support_depth
    _write_support(support_path, REFERENCE, depth=effective_support_depth, overrides=support_overrides)
    if malformed_support:
        support_path.write_text("not\ta\tvalid\tsupport\ttable\n", encoding="utf-8")
    total_reads, mapped_reads, unmapped_reads = alignment_counts or bam_counts
    alignment_stats_path.write_text(
        "metric\tvalue\n"
        "reference_name\tplasmid\n"
        "fastq_minimap2_preset\tmap-ont\n"
        f"total_reads\t{total_reads}\n"
        f"mapped_reads\t{mapped_reads}\n"
        f"unmapped_reads\t{unmapped_reads}\n",
        encoding="utf-8",
    )
    breakpoint_path.write_text(
        "breakpoint_status\tconfidence\tprimary_breakpoint_in_boundary_window\n",
        encoding="utf-8",
    )
    secondary_path.write_text(
        f"non_boundary_split_reads\taligned_dimer_reads\n0\t{bam_mapped}\n",
        encoding="utf-8",
    )
    topology = {
        "schema": "biomodstack.construct_topology_evidence.v1",
        "state": topology_state,
        "expected_topology": "circular",
        "origin_spanning_reads": split_reads,
        "secondary_anomaly_fraction": secondary_anomaly_fraction,
        "non_boundary_split_reads": (
            0
            if not math.isfinite(secondary_anomaly_fraction)
            else int(round(secondary_anomaly_fraction * bam_mapped))
        ),
        "aligned_dimer_reads": bam_mapped,
        "mapped_unique_reads": bam_mapped,
        "alignment_records": bam_mapped + split_reads,
        "edge_window_bp": min(100, max(10, len(REFERENCE) // 50)),
        "contradictory_breakpoint_evidence": contradictory_breakpoint_evidence,
        "evidence_basis": "split_alignment_origin_bridge",
        "provenance": {
            "reference_sha256": _sha256(reference_path),
            "alignment_bam_sha256": topology_bam_digest or _sha256(alignment_bam_path),
            "breakpoint_call_sha256": topology_breakpoint_digest or _sha256(breakpoint_path),
            "secondary_summary_sha256": topology_secondary_digest or _sha256(secondary_path),
        },
    }
    topology.update(topology_overrides or {})
    topology_path.write_text(json.dumps(topology), encoding="utf-8")

    state = {
        "schema": "biomodstack.observed_sequence_state.v1",
        "state": "present" if observed is not None else "missing",
        "method": method if observed is not None else None,
        "source_kind": "independently_derived_consensus" if independent else "expected_reference_derived",
        "independent_from_expected": independent,
        "fallback": not independent,
        "observed_sha256": declared_digest
        if declared_digest is not None
        else (_sha256(observed_path) if observed_path.exists() else None),
        "source_reads_path": source_fastq.name,
        "source_reads_sha256": source_reads_digest or _sha256(source_fastq),
        "source_read_provenance": {
            "binding_method": "qname_and_sequence_against_primary_bam",
            "verification_status": "pending",
        },
        "reason": None if observed is not None else "no observed consensus was produced",
    }
    state_path.write_text(json.dumps([] if malformed_observed_state else state), encoding="utf-8")

    command = [
        str(PYTHON),
        str(SCRIPT),
        "--reference-fasta",
        str(reference_path),
        "--observed-state",
        str(state_path),
        "--observed-fasta",
        str(observed_path),
        "--per-base-support",
        str(support_path),
        "--alignment-stats",
        str(alignment_stats_path),
        "--alignment-bam",
        str(alignment_bam_path),
        "--alignment-index",
        str(alignment_index_path),
        "--samtools-bin",
        str(verification_samtools),
        "--topology-evidence",
        str(topology_path),
        "--breakpoint-call",
        str(breakpoint_path),
        "--secondary-summary",
        str(secondary_path),
        "--profile-config",
        str(PROFILE_CONFIG),
        "--profile",
        "plasmid_strict_v1",
        "--out-dir",
        str(out_dir),
    ]
    if declared_reference_digest is not None:
        trusted_reference_digest = (
            hashlib.sha256(REFERENCE.encode("ascii")).hexdigest()
            if declared_reference_digest == "actual"
            else declared_reference_digest
        )
        command.extend(["--expected-reference-sha256", trusted_reference_digest])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    manifest_path = out_dir / "qc_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return result, manifest, out_dir


def test_non_utf8_samtools_version_metadata_does_not_abort_verification(tmp_path: Path) -> None:
    wrapper = tmp_path / "samtools-with-binary-version"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if len(sys.argv) == 2 and sys.argv[1] == '--version':\n"
        "    os.write(1, b'samtools 1.13\\ncompiler metadata: \\xab\\n')\n"
        "    raise SystemExit(0)\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'idxstats' and '-X' in sys.argv:\n"
        "    os.write(2, b\"idxstats: invalid option -- 'X'\\n\")\n"
        "    raise SystemExit(2)\n"
        f"os.execv({str(SAMTOOLS)!r}, [{str(SAMTOOLS)!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result, manifest, _ = _run_case(tmp_path, verification_samtools=wrapper)

    assert result.returncode == 0, result.stderr
    assert manifest["schema"] == "biomodstack.construct_verification.v2"
    assert manifest["provenance"]["tool_versions"]["samtools"] == "samtools 1.13"
    assert manifest["inputs"]["alignment_index"]["semantic_validation"]["status"] == "valid"


def test_phase2_contract_files_exist() -> None:
    required = [
        PROFILE_CONFIG,
        REPO_ROOT / "schemas" / "ngs" / "construct_verification_manifest.schema.json",
        SCRIPT,
        REPO_ROOT / "modules" / "ngs" / "construct_verify.nf",
    ]
    missing = [str(path.relative_to(REPO_ROOT)) for path in required if not path.is_file()]
    assert missing == [], f"missing Phase 2 contract files: {missing}"


def test_exact_clean_evidence_under_experimental_profile_requires_review(tmp_path: Path) -> None:
    result, manifest, out_dir = _run_case(tmp_path)

    assert result.returncode == 0, result.stderr
    assert manifest["schema"] == "biomodstack.construct_verification.v2"
    assert manifest["verdict"] == "REVIEW"
    assert "UNCALIBRATED_PROFILE" in manifest["reason_codes"]
    assert all(check["status"] == "pass" for check in manifest["checks"].values())
    assert manifest["variants"] == []
    assert (out_dir / "observed_consensus.fasta").is_file()
    assert (out_dir / "variants.vcf").is_file()
    assert (out_dir / "per_base_metrics.tsv").is_file()
    assert (out_dir / "evidence.html").is_file()


@pytest.mark.parametrize(
    ("observed", "method", "independent", "declared_digest", "reason_code"),
    [
        (None, "bcftools_consensus", True, None, "MISSING_OBSERVED_CONSENSUS"),
        (REFERENCE, "reference_copy_fallback", False, None, "FALLBACK_OBSERVED_EVIDENCE"),
        (REFERENCE, "bcftools_consensus", True, "0" * 64, "OBSERVED_DIGEST_MISMATCH"),
    ],
)
def test_untrusted_or_missing_observed_evidence_is_review_not_pass(
    tmp_path: Path,
    observed: str | None,
    method: str,
    independent: bool,
    declared_digest: str | None,
    reason_code: str,
) -> None:
    result, manifest, out_dir = _run_case(
        tmp_path,
        observed=observed,
        method=method,
        independent=independent,
        declared_digest=declared_digest,
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert reason_code in manifest["reason_codes"]
    assert manifest["checks"]["sequence_identity"]["status"] in {"review", "not_evaluated"}
    if observed is None:
        assert not (out_dir / "observed_consensus.fasta").exists()


@pytest.mark.parametrize(
    "observed",
    [
        REFERENCE[13:] + REFERENCE[:13],
        _reverse_complement(REFERENCE[9:] + REFERENCE[:9]),
    ],
)
def test_circular_rotation_and_reverse_complement_do_not_change_review_verdict(tmp_path: Path, observed: str) -> None:
    result, manifest, _ = _run_case(tmp_path, observed=observed)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "UNCALIBRATED_PROFILE" in manifest["reason_codes"]
    assert manifest["summary"]["sequence_identity_fraction"] == pytest.approx(1.0)
    assert manifest["summary"]["reference_topology"] == "circular"


def test_supported_snv_is_normalized_and_fails_construct_identity(tmp_path: Path) -> None:
    position = 8
    ref = REFERENCE[position - 1]
    alt = next(base for base in "ACGT" if base != ref)
    observed = REFERENCE[: position - 1] + alt + REFERENCE[position:]
    result, manifest, out_dir = _run_case(
        tmp_path,
        observed=observed,
        support_overrides={
            position: {
                "ref_count": 3,
                ref: 3,
                alt: 27,
                "major_allele": alt,
                "major_allele_count": 27,
                "major_allele_fraction": "0.900000",
            }
        },
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "FAIL"
    assert "VARIANTS_DETECTED" in manifest["reason_codes"]
    assert manifest["variants"] == [
        {
            "alt": alt,
            "circular_event_id": None,
            "depth": 30,
            "end_1based": position,
            "id": "var1",
            "kind": "SNV",
            "position_1based": position,
            "ref": ref,
            "support_fraction": 0.9,
            "support_status": "supported",
        }
    ]
    assert f"\t{position}\tvar1\t{ref}\t{alt}\t" in (out_dir / "variants.vcf").read_text(encoding="utf-8")


def test_low_support_snv_is_review_not_fail(tmp_path: Path) -> None:
    position = 12
    ref = REFERENCE[position - 1]
    alt = next(base for base in "ACGT" if base != ref)
    observed = REFERENCE[: position - 1] + alt + REFERENCE[position:]
    result, manifest, _ = _run_case(
        tmp_path,
        observed=observed,
        support_overrides={
            position: {
                "ref_count": 15,
                ref: 15,
                alt: 15,
                "major_allele": alt,
                "major_allele_count": 15,
                "major_allele_fraction": "0.500000",
            }
        },
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "VARIANT_SUPPORT_AMBIGUOUS" in manifest["reason_codes"]
    assert manifest["variants"][0]["support_status"] == "ambiguous"


@pytest.mark.parametrize(
    ("kind", "observed", "support_position", "support_column"),
    [
        ("INS", REFERENCE[:20] + "AA" + REFERENCE[20:], 20, "insertion_count"),
        ("DEL", REFERENCE[:17] + REFERENCE[18:], 18, "deletion_count"),
    ],
)
def test_supported_indels_are_emitted_as_normalized_variants(
    tmp_path: Path,
    kind: str,
    observed: str,
    support_position: int,
    support_column: str,
) -> None:
    result, manifest, _ = _run_case(
        tmp_path,
        observed=observed,
        support_overrides={
            support_position: {
                support_column: 27,
                **(
                    {
                        REFERENCE[support_position - 1]: 3,
                        "major_allele": "-",
                        "major_allele_fraction": "0.900000",
                    }
                    if kind == "DEL"
                    else {}
                ),
            }
        },
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "FAIL"
    assert manifest["variants"][0]["kind"] == kind
    assert manifest["variants"][0]["support_status"] == "supported"


def test_origin_insertion_uses_exact_inserted_allele_across_circular_representations(tmp_path: Path) -> None:
    inserted = "T"
    altered = inserted + REFERENCE
    result, manifest, _ = _run_case(
        tmp_path,
        observed=altered,
        support_overrides={1: {"insertion_count": 30}},
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "FAIL"
    assert len(manifest["variants"]) == 1
    variant = manifest["variants"][0]
    assert variant["position_1based"] == 1
    assert variant["ref"] == REFERENCE[0]
    assert variant["alt"] == inserted + REFERENCE[0]
    assert variant["support_status"] == "supported"
    assert variant["depth"] == 30
    assert variant["support_fraction"] == pytest.approx(1.0)

    namespace = runpy.run_path(str(SCRIPT))
    call_variants = namespace["call_variants"]
    profile = {"min_depth": 20, "min_variant_support_fraction": 0.8}
    support_rows = {
        1: {
            "depth": 30,
            "insertion_alleles": {inserted: 30},
        }
    }
    for representation in _circular_representations(altered):
        variants, _ = call_variants(REFERENCE, representation, support_rows, profile)
        assert len(variants) == 1
        assert variants[0]["position_1based"] == 1
        assert variants[0]["ref"] == REFERENCE[0]
        assert variants[0]["alt"] == inserted + REFERENCE[0]
        assert variants[0]["support_status"] == "supported"
        assert variants[0]["support_fraction"] == pytest.approx(1.0)


def test_origin_spanning_deletion_is_linked_as_one_circular_event(tmp_path: Path) -> None:
    observed = REFERENCE[2:-2]
    result, manifest, _ = _run_case(
        tmp_path,
        observed=observed,
        support_overrides={
            1: {REFERENCE[0]: 0, "deletion_count": 30, "major_allele": "-"},
            2: {REFERENCE[1]: 0, "deletion_count": 30, "major_allele": "-"},
            len(REFERENCE) - 1: {
                REFERENCE[-2]: 0,
                "deletion_count": 30,
                "major_allele": "-",
            },
            len(REFERENCE): {
                REFERENCE[-1]: 0,
                "deletion_count": 30,
                "major_allele": "-",
            },
        },
    )

    assert result.returncode == 0, result.stderr
    assert manifest["summary"]["variant_count"] >= 1
    event_ids = {variant["circular_event_id"] for variant in manifest["variants"] if variant["circular_event_id"]}
    assert len(event_ids) == 1
    assert {variant["kind"] for variant in manifest["variants"]} == {"DEL"}


def test_repetitive_and_homopolymer_indels_are_deterministically_left_normalized() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    call_variants = namespace["call_variants"]
    profile = {"min_depth": 20, "min_variant_support_fraction": 0.8}

    homopolymer, _ = call_variants("AAAAACCCCC", "AAAAAACCCCC", {}, profile)
    tandem_repeat, _ = call_variants("ATATATAT", "ATATAT", {}, profile)

    assert [(item["position_1based"], item["ref"], item["alt"]) for item in homopolymer] == [(1, "A", "AA")]
    assert [(item["position_1based"], item["ref"], item["alt"]) for item in tandem_repeat] == [(1, "ATA", "A")]


def test_insertion_support_is_bound_to_exact_bam_allele_and_length() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    variant_support = namespace["_variant_support"]
    validate_binding = namespace["validate_observed_consensus_binding"]
    profile = {"min_depth": 20, "min_variant_support_fraction": 0.8}
    row = {
        "depth": 30,
        "A": 30,
        "C": 0,
        "G": 0,
        "T": 0,
        "N": 0,
        "deletion_count": 0,
        "insertion_count": 30,
        "insertion_alleles": {"T": 8, "G": 21, "GG": 1},
    }

    status, depth, fraction = variant_support("INS", "AT", "T", [1], {1: row}, profile)
    assert status == "ambiguous"
    assert depth == 30
    assert fraction == pytest.approx(8 / 30)

    published = {
        position: {"consensus_base": base}
        for position, base in enumerate("ACGT", start=1)
    }
    recomputed = {
        position: {
            "A": 30 if base == "A" else 0,
            "C": 30 if base == "C" else 0,
            "G": 30 if base == "G" else 0,
            "T": 30 if base == "T" else 0,
            "N": 0,
            "deletion_count": 0,
            "insertion_count": 30 if position == 1 else 0,
            "insertion_alleles": {"G": 30} if position == 1 else {},
        }
        for position, base in enumerate("ACGT", start=1)
    }
    with pytest.raises(ValueError, match="observed insertion"):
        validate_binding("ACGT", "ATCGT", published, recomputed)


def test_wrong_trusted_reference_digest_never_passes(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, declared_reference_digest="0" * 64)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "REFERENCE_DIGEST_MISMATCH" in manifest["reason_codes"]


def test_unbound_reference_digest_never_passes(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, declared_reference_digest=None)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "REFERENCE_DIGEST_UNBOUND" in manifest["reason_codes"]


def test_malformed_support_table_never_passes(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, malformed_support=True)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_SUPPORT_TABLE" in manifest["reason_codes"]


def test_zero_coverage_never_passes(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(
        tmp_path,
        support_depth=0,
        bam_counts=(30, 0, 30),
        alignment_counts=(30, 0, 30),
        origin_spanning_reads=0,
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] != "PASS"
    assert "INSUFFICIENT_COVERAGE" in manifest["reason_codes"]
    assert "INSUFFICIENT_DEPTH" in manifest["reason_codes"]


def test_mixed_allele_site_never_passes(tmp_path: Path) -> None:
    position = 10
    ref = REFERENCE[position - 1]
    alt = next(base for base in "ACGT" if base != ref)
    result, manifest, _ = _run_case(
        tmp_path,
        support_overrides={
            position: {
                "ref_count": 15,
                ref: 15,
                alt: 15,
                "major_allele": ref,
                "major_allele_count": 15,
                "major_allele_fraction": 0.5,
            }
        },
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "MIXED_ALLELES_DETECTED" in manifest["reason_codes"]


def test_excess_unmapped_fraction_fails_contamination_screen(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(
        tmp_path,
        alignment_counts=(100, 70, 30),
        bam_counts=(100, 70, 30),
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "FAIL"
    assert "CONTAMINATION_SCREEN_FAILED" in manifest["reason_codes"]
    assert manifest["checks"]["contamination"]["status"] == "fail"


@pytest.mark.parametrize(
    ("topology_state", "origin_reads", "anomaly_fraction", "expected_status", "reason"),
    [
        ("unavailable", 0, 0.0, "review", "TOPOLOGY_EVIDENCE_UNAVAILABLE"),
        ("present", 0, 0.0, "review", "TOPOLOGY_EVIDENCE_INSUFFICIENT"),
        ("present", 4, 0.5, "fail", "TOPOLOGY_CONTRADICTED"),
    ],
)
def test_topology_policy_is_fail_closed(
    tmp_path: Path,
    topology_state: str,
    origin_reads: int,
    anomaly_fraction: float,
    expected_status: str,
    reason: str,
) -> None:
    result, manifest, _ = _run_case(
        tmp_path,
        topology_state=topology_state,
        origin_spanning_reads=origin_reads,
        secondary_anomaly_fraction=anomaly_fraction,
    )

    assert result.returncode == 0, result.stderr
    assert manifest["checks"]["topology"]["status"] == expected_status
    assert reason in manifest["reason_codes"]


def test_generated_manifest_validates_against_draft_2020_schema(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path)

    assert result.returncode == 0, result.stderr
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)


def test_manifest_separates_execution_and_scientific_status(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path)

    assert result.returncode == 0, result.stderr
    assert manifest["execution"] == {"status": "SUCCEEDED", "exit_code": 0, "reason_codes": []}
    assert manifest["verdict"] == "REVIEW"


def test_manifest_binds_reference_workflow_and_experimental_policy(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path)

    assert result.returncode == 0, result.stderr
    expected_sequence_digest = hashlib.sha256(REFERENCE.encode("ascii")).hexdigest()
    assert manifest["inputs"]["reference"]["normalized_sequence_sha256"] == expected_sequence_digest
    assert manifest["inputs"]["reference"]["declared_sequence_sha256"] == expected_sequence_digest
    assert manifest["threshold_profile"]["calibration_status"] == "experimental"
    assert manifest["threshold_profile"]["public_accuracy_validated"] is False
    assert manifest["provenance"]["workflow"] == {
        "name": "ConstructVerify",
        "module": "modules/ngs/construct_verify.nf",
        "version": "2",
    }
    commands = manifest["provenance"]["commands"]
    assert commands and commands[0]["name"] == "construct_verifier"
    assert Path(commands[0]["argv"][0]).name.startswith("python")
    assert Path(commands[0]["argv"][1]).resolve() == SCRIPT.resolve()


def test_manifest_binds_and_validates_every_scientific_artifact(tmp_path: Path) -> None:
    result, manifest, out_dir = _run_case(tmp_path)

    assert result.returncode == 0, result.stderr
    assert set(manifest["inputs"]) == {
        "reference",
        "observed",
        "source_reads",
        "support",
        "alignment",
        "alignment_index",
        "alignment_stats",
        "topology",
    }
    for evidence in manifest["inputs"].values():
        assert evidence["state"] == "present"
        assert evidence["semantic_validation"]["status"] == "valid"
        assert evidence["sha256"]
        assert evidence["size_bytes"] > 0

    artifacts = {artifact["kind"]: artifact for artifact in manifest["artifacts"]}
    assert set(artifacts) == {
        "verification_summary",
        "normalized_variants",
        "per_base_metrics",
        "human_evidence_report",
        "observed_consensus",
    }
    for artifact in artifacts.values():
        assert artifact["state"] == "present"
        assert artifact["semantic_validation"]["status"] == "valid"
        path = out_dir / artifact["path"]
        assert artifact["sha256"] == _sha256(path)
        assert artifact["size_bytes"] == path.stat().st_size


@pytest.mark.parametrize("alignment_index_state", ["missing", "corrupt"])
def test_missing_or_corrupt_alignment_index_never_passes(
    tmp_path: Path,
    alignment_index_state: str,
) -> None:
    result, manifest, _ = _run_case(tmp_path, alignment_index_state=alignment_index_state)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "ALIGNMENT_EVIDENCE_INVALID" in manifest["reason_codes"]
    assert manifest["inputs"]["alignment_index"]["semantic_validation"]["status"] == "invalid"


def test_bam_read_counts_must_match_alignment_stats(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, alignment_counts=(100, 95, 5))

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "ALIGNMENT_STATS_INCONSISTENT" in manifest["reason_codes"]


def test_support_reference_base_and_count_arithmetic_are_validated(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(
        tmp_path,
        support_overrides={1: {"reference_base": "T", "A": 29}},
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_SUPPORT_TABLE" in manifest["reason_codes"]


def test_topology_provenance_must_bind_reference_and_bam_digests(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, topology_bam_digest="0" * 64)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "TOPOLOGY_PROVENANCE_INVALID" in manifest["reason_codes"]


def test_direct_contradictory_breakpoint_evidence_fails_topology(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, contradictory_breakpoint_evidence=True)

    assert result.returncode == 0, result.stderr
    assert manifest["checks"]["topology"]["status"] == "fail"
    assert "TOPOLOGY_CONTRADICTED" in manifest["reason_codes"]


def test_nonfinite_topology_number_is_typed_review_and_strict_json(tmp_path: Path) -> None:
    result, manifest, out_dir = _run_case(tmp_path, secondary_anomaly_fraction=float("nan"))

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_NUMERIC_EVIDENCE" in manifest["reason_codes"]
    raw = (out_dir / "qc_manifest.json").read_text(encoding="utf-8")
    json.loads(
        raw,
        parse_constant=lambda value: (_ for _ in ()).throw(AssertionError(f"non-standard JSON number: {value}")),
    )


def test_malformed_observed_state_list_still_emits_typed_review_manifest(tmp_path: Path) -> None:
    result, manifest, out_dir = _run_case(tmp_path, malformed_observed_state=True)

    assert result.returncode == 0, result.stderr
    assert (out_dir / "qc_manifest.json").is_file()
    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_OBSERVED_STATE" in manifest["reason_codes"]


def test_source_reads_must_be_retained_and_recomputed_not_merely_declared(tmp_path: Path) -> None:
    result, manifest, _ = _run_case(tmp_path, source_reads_digest="0" * 64)

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "SOURCE_READ_PROVENANCE_INVALID" in manifest["reason_codes"]


def test_repeat_deletion_is_orientation_invariant() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    call_variants = namespace["call_variants"]
    reverse_complement = namespace["reverse_complement"]
    profile = {"min_depth": 20, "min_variant_support_fraction": 0.8}
    reference = "AGAGAGCTCTCT"
    observed = "AGAGAGTCTCT"

    forward, _ = call_variants(reference, observed, {}, profile)
    reverse, _ = call_variants(reference, reverse_complement(observed), {}, profile)

    canonical = [(item["position_1based"], item["ref"], item["alt"]) for item in forward]
    assert canonical == [(item["position_1based"], item["ref"], item["alt"]) for item in reverse]


@pytest.mark.parametrize(
    ("reference", "observed"),
    [
        ("TAACTGTG", "TAAGCTGTG"),
        ("AAGAG", "AGAGAG"),
        ("AAAAACCCCC", "AAAAAACCCCC"),
        ("ATATATAT", "ATATAT"),
        ("ACGTCGTA", "ACATCGTAA"),
    ],
)
def test_circular_alignment_matches_independent_exhaustive_oracle(reference: str, observed: str) -> None:
    namespace = runpy.run_path(str(SCRIPT))
    best_circular_alignment = namespace["best_circular_alignment"]

    oracle_cost = min(
        _levenshtein_oracle(reference, representation)
        for representation in _circular_representations(observed)
    )

    assert best_circular_alignment(reference, observed)["edit_cost"] == oracle_cost


def test_adversarial_insertion_is_canonical_across_every_rotation_and_orientation() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    call_variants = namespace["call_variants"]
    profile = {"min_depth": 20, "min_variant_support_fraction": 0.8}
    reference = "TAACTGTG"
    observed = "TAAGCTGTG"

    calls = []
    for representation in _circular_representations(observed):
        variants, alignment = call_variants(reference, representation, {}, profile)
        calls.append(
            (
                alignment["edit_cost"],
                [
                    (
                        item["kind"],
                        item["position_1based"],
                        item["end_1based"],
                        item["ref"],
                        item["alt"],
                        item["circular_event_id"],
                    )
                    for item in variants
                ],
            )
        )

    assert set(map(repr, calls)) == {repr((1, [("INS", 3, 3, "A", "AG", None)]))}


def test_observed_reference_copy_contradicting_unanimous_alt_bam_fails(tmp_path: Path) -> None:
    position = 8
    ref = REFERENCE[position - 1]
    alt = next(base for base in "ACGT" if base != ref)

    result, manifest, out_dir = _run_case(
        tmp_path,
        observed=REFERENCE,
        support_overrides={
            position: {
                ref: 0,
                alt: 30,
                "major_allele": alt,
                "major_allele_fraction": "1.000000",
            }
        },
        origin_spanning_reads=0,
    )

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "FAIL"
    assert "OBSERVED_CONSENSUS_SUPPORT_CONTRADICTION" in manifest["reason_codes"]
    assert manifest["checks"]["sequence_identity"]["status"] == "fail"
    assert not (out_dir / "observed_consensus.fasta").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin_spanning_reads", 2.9),
        ("origin_spanning_reads", 2.0),
        ("origin_spanning_reads", True),
        ("mapped_unique_reads", float("inf")),
        ("alignment_records", float("nan")),
    ],
)
def test_topology_counts_require_finite_json_integer_tokens(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    result, manifest, out_dir = _run_case(tmp_path, topology_overrides={field: value})

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_NUMERIC_EVIDENCE" in manifest["reason_codes"]
    json.loads(
        (out_dir / "qc_manifest.json").read_text(encoding="utf-8"),
        parse_constant=lambda token: (_ for _ in ()).throw(AssertionError(token)),
    )


@pytest.mark.parametrize(
    "digest_parameter",
    ["topology_breakpoint_digest", "topology_secondary_digest"],
)
def test_topology_sidecar_digests_are_recomputed(
    tmp_path: Path,
    digest_parameter: str,
) -> None:
    result, manifest, _ = _run_case(tmp_path, **{digest_parameter: "0" * 64})

    assert result.returncode == 0, result.stderr
    assert manifest["verdict"] == "REVIEW"
    assert "TOPOLOGY_PROVENANCE_INVALID" in manifest["reason_codes"]


def test_final_profile_hash_is_the_reviewed_policy_identity() -> None:
    profile = json.loads(PROFILE_CONFIG.read_text(encoding="utf-8"))["profiles"]["plasmid_strict_v1"]
    encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == "90fad5ea643fc6509cd174020a52563c0a0ec4d38836328cd4bdc7eed9015553"


def test_verifier_ignores_secondary_alignment_with_omitted_sequence() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    recompute_alignment_semantics = namespace["recompute_alignment_semantics"]
    records = [
        {
            "qname": "read-a",
            "flag": 0,
            "rname": "ref",
            "position": 1,
            "mapq": 60,
            "cigar": "10M",
            "sequence": "ACGTACGTAC",
        },
        {
            "qname": "read-a",
            "flag": 0x100,
            "rname": "ref",
            "position": 1,
            "mapq": 0,
            "cigar": "5M1I4M",
            "sequence": "*",
        },
    ]

    semantics = recompute_alignment_semantics(records, "ref", "ACGTACGTAC")

    assert semantics["mapped_reads"] == 1
    assert semantics["alignment_records"] == 1


def test_verifier_recomputation_rejects_duplicated_split_query_intervals() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    recompute_alignment_semantics = namespace["recompute_alignment_semantics"]
    count_valid_origin_wraps = namespace["count_valid_origin_wraps"]
    reference = "A" * 1000
    records = [
        {
            "qname": "duplicate-a",
            "flag": 0,
            "rname": "ref",
            "position": 1,
            "mapq": 60,
            "cigar": "100M900S",
            "sequence": reference,
        },
        {
            "qname": "duplicate-a",
            "flag": 2048,
            "rname": "ref",
            "position": 901,
            "mapq": 60,
            "cigar": "100M900S",
            "sequence": reference,
        },
    ]

    semantics = recompute_alignment_semantics(records, "ref", reference)

    assert count_valid_origin_wraps(semantics["segments_by_read"], len(reference), 100) == 0
