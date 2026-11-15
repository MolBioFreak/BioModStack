"""The pileup AB1 carries the published consensus and the read shares at each base."""
import importlib.util
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/write_pileup_ab1.py"
HEADER = ("chrom\tposition_1based\treference_base\tdepth\tforward_depth\treverse_depth\ta_count\tc_count\tg_count\t"
          "t_count\tn_count\tinsertion_count\tdeletion_count\tconsensus_base\tmajor_allele_fraction\tlow_coverage\tambiguous\n")


@pytest.fixture(scope="module")
def ab1():
    spec = importlib.util.spec_from_file_location("write_pileup_ab1_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table(path, reference, overrides=None):
    lines = [HEADER]
    for index, base in enumerate(reference, 1):
        counts = {b: 0 for b in "ACGT"}
        counts[base] = 100
        counts.update((overrides or {}).get(index, {}))
        depth = sum(counts.values())
        lines.append(f"ref\t{index}\t{base}\t{depth}\t{depth}\t0\t{counts['A']}\t{counts['C']}\t{counts['G']}\t{counts['T']}\t0\t0\t0\t{base}\t1.0\tfalse\tfalse\n")
    path.write_text("".join(lines))
    return path


def entries(path):
    """Read the ABIF directory with nothing but struct."""
    data = path.read_bytes()
    assert data[:4] == b"ABIF"
    _, _, _, _, count, _, offset, _ = struct.unpack(">4sIHHIIII", data[6:34])
    found = {}
    for index in range(count):
        name, number, _etype, _esize, _n, size, field = struct.unpack(">4sIHHII4s", data[offset + index * 28: offset + index * 28 + 24])
        found[(name.decode(), number)] = field[:size] if size <= 4 else data[struct.unpack(">I", field)[0]:][:size]
    return found


def test_sequence_equals_the_consensus_and_heights_are_read_shares(ab1, tmp_path):
    reference = "ACGTACGTAC"
    counts = table(tmp_path / "t.tsv", reference, {4: {"T": 60, "C": 40}})
    (tmp_path / "c.fa").write_text(">s\n" + reference.lower() + "\n")
    assert ab1.main(["--per-base-support", str(counts), "--consensus-fasta", str(tmp_path / "c.fa"),
                     "--out", str(tmp_path / "o.ab1"), "--sample", "s"]) == 0
    found = entries(tmp_path / "o.ab1")
    assert found[("PBAS", 1)].decode() == reference
    assert found[("FWO_", 1)] == b"GATC"
    assert b"Not a Sanger trace" in found[("CMNT", 1)]
    peaks = struct.unpack(">10h", found[("PLOC", 1)])
    channel = {base: struct.unpack(f">{len(found[('DATA', n)]) // 2}h", found[("DATA", n)])
               for base, n in zip("GATC", (9, 10, 11, 12))}
    # Position 4 is mixed: 60 percent T and 40 percent C; position 1 is all A.
    assert channel["T"][peaks[3]] == 600 and channel["C"][peaks[3]] == 400
    # Position 1 is all A; the neighbouring C peak contributes only its tail there.
    assert channel["A"][peaks[0]] == 1000 and channel["C"][peaks[0]] < 10
    assert found[("PCON", 1)][0] == 60 and found[("PCON", 1)][3] == 4


def test_a_called_variant_uses_the_same_table(ab1, tmp_path):
    reference = "ACGTACGTAC"
    consensus = "ACGAACGTAC"   # the reads say A at position 4
    counts = table(tmp_path / "t.tsv", reference, {4: {"T": 5, "A": 95}})
    rows = ab1.read_counts(counts)
    assert ab1.table_is_in_consensus_coordinates(rows, consensus)
    (tmp_path / "c.fa").write_text(">s\n" + consensus + "\n")
    assert ab1.main(["--per-base-support", str(counts), "--consensus-fasta", str(tmp_path / "c.fa"),
                     "--out", str(tmp_path / "o.ab1"), "--sample", "s"]) == 0
    assert entries(tmp_path / "o.ab1")[("PBAS", 1)].decode() == consensus


def test_a_consensus_with_an_insertion_is_recounted_not_written_from_shifted_counts(ab1, tmp_path, monkeypatch):
    reference = "ACGTACGTAC"
    consensus = "ACGTTTACGTAC"
    counts = table(tmp_path / "t.tsv", reference)
    assert not ab1.table_is_in_consensus_coordinates(ab1.read_counts(counts), consensus)
    (tmp_path / "c.fa").write_text(">s\n" + consensus + "\n")
    args = ["--per-base-support", str(counts), "--consensus-fasta", str(tmp_path / "c.fa"),
            "--out", str(tmp_path / "o.ab1"), "--sample", "s"]
    # Without reads there is nothing to recount from, and no file is written.
    assert ab1.main(args) == 2 and not (tmp_path / "o.ab1").exists()
    recounted = ab1.read_counts(table(tmp_path / "c.tsv", consensus))
    seen = []
    monkeypatch.setattr(ab1, "recount_against_consensus", lambda *a: seen.append(a) or recounted)
    assert ab1.main([*args, "--reads", str(tmp_path / "reads.fastq")]) == 0
    assert len(seen) == 1
    assert entries(tmp_path / "o.ab1")[("PBAS", 1)].decode() == consensus


@pytest.mark.parametrize("length", [12000, 20000])
def test_long_consensus_opens_in_biopython(ab1, tmp_path, length):
    SeqIO = pytest.importorskip("Bio.SeqIO")
    reference = ("ACGGTCATTG" * (length // 10 + 1))[:length]
    counts = table(tmp_path / "t.tsv", reference)
    (tmp_path / "c.fa").write_text(">s\n" + reference + "\n")
    assert ab1.main(["--per-base-support", str(counts), "--consensus-fasta", str(tmp_path / "c.fa"),
                     "--out", str(tmp_path / "o.ab1"), "--sample", "long"]) == 0
    record = SeqIO.read(str(tmp_path / "o.ab1"), "abi")
    assert str(record.seq) == reference
    assert max(record.annotations["abif_raw"]["PLOC1"]) <= 32767


def test_a_consensus_past_the_format_limit_is_reported_not_truncated(ab1, tmp_path, capsys):
    (tmp_path / "c.fa").write_text(">s\n" + "A" * 40000 + "\n")
    (tmp_path / "t.tsv").write_text(HEADER)
    assert ab1.main(["--per-base-support", str(tmp_path / "t.tsv"), "--consensus-fasta", str(tmp_path / "c.fa"),
                     "--out", str(tmp_path / "o.ab1"), "--sample", "s"]) == 2
    assert "at most 32767" in capsys.readouterr().err and not (tmp_path / "o.ab1").exists()
