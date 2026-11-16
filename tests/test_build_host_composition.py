"""Host share is counted over the whole read population, once per read, with nothing dropped."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def host_module():
    spec = importlib.util.spec_from_file_location("build_host_composition_under_test", ROOT / "scripts/build_host_composition.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sam(references, records):
    lines = [f"@SQ\tSN:{name}\tLN:{length}" for name, length in references]
    lines += [f"{name}\t{flag}\t{reference}\t1\t60\t{cigar}\t*\t0\t0\t*\t*" for name, flag, reference, cigar in records]
    return "\n".join(lines) + "\n"


def run(host_module, tmp_path, lengths, plasmid_records, host_records):
    (tmp_path / "reads.fastq").write_text("".join(f"@{name} extra\n{'A' * n}\n+\n{'F' * n}\n" for name, n in lengths.items()))
    (tmp_path / "plasmid.sam").write_text(sam([("plasmid", 5000)], plasmid_records))
    (tmp_path / "host.sam").write_text(sam([("chr", 4_000_000), ("episome", 1_000_000)], host_records))
    assert host_module.main(["--sample", "s", "--reads-fastq", str(tmp_path / "reads.fastq"),
                             "--plasmid-sam", str(tmp_path / "plasmid.sam"), "--host-sam", str(tmp_path / "host.sam"),
                             "--out", str(tmp_path / "host_composition.json")]) == 0
    return json.loads((tmp_path / "host_composition.json").read_text())


def test_every_read_is_counted_once_and_the_categories_add_up(host_module, tmp_path):
    lengths = {"p1": 5000, "p2": 5000, "h1": 1000, "both_p": 5000, "both_h": 3000, "tie": 2000, "none": 400}
    document = run(
        host_module, tmp_path, lengths,
        plasmid_records=[("p1", 0, "plasmid", "5000M"), ("p2", 0, "plasmid", "2500M2500S"), ("p2", 2048, "plasmid", "2500H2500M"),
                         ("both_p", 0, "plasmid", "4000M1000S"), ("both_h", 0, "plasmid", "500M2500S"),
                         ("tie", 0, "plasmid", "1000M1000S"), ("none", 4, "*", "*"),
                         ("p1", 256, "plasmid", "5000M")],
        host_records=[("h1", 16, "chr", "1000M"), ("both_p", 0, "chr", "4000S1000M"), ("both_h", 0, "episome", "500S2500M"),
                      ("tie", 0, "chr", "1000S1000M")],
    )
    categories = document["categories"]
    assert {name: categories[name]["reads"] for name in categories} == {"plasmid": 3, "host": 2, "ambiguous": 1, "unclassified": 1}
    assert {name: categories[name]["bases"] for name in categories} == {"plasmid": 15000, "host": 4000, "ambiguous": 2000, "unclassified": 400}
    assert document["denominator"] == {"total_reads": 7, "total_bases": 21400, "source": "reads.fastq"}
    assert sum(item["reads"] for item in categories.values()) == 7
    assert sum(item["bases"] for item in categories.values()) == 21400
    assert categories["host"]["read_fraction"] == pytest.approx(2 / 7)
    assert categories["host"]["base_fraction"] == pytest.approx(4000 / 21400)
    assert document["reads_aligning_to_both"] == 3
    assert document["host_reference"] == {"records": 2, "length_bp": 5_000_000, "names": ["chr", "episome"]}


def test_the_estimate_is_aligned_bases_over_molecule_length_and_says_it_is_unchecked(host_module, tmp_path):
    document = run(
        host_module, tmp_path, {"p1": 5000, "h1": 5000},
        plasmid_records=[("p1", 0, "plasmid", "5000M")], host_records=[("h1", 0, "chr", "5000M")],
    )
    estimate = document["estimate"]
    assert estimate["host_mass_fraction"] == pytest.approx(0.5)
    # One plasmid's worth of bases against a thousandth of a host genome.
    assert estimate["host_molar_fraction"] == pytest.approx(0.001 / 1.001)
    assert (estimate["plasmid_length_bp"], estimate["host_length_bp"]) == (5000, 5_000_000)
    assert estimate["method"] == "aligned bases divided by molecule length"
    assert estimate["checked_against_known_mixture"] is False


def test_no_host_reads_gives_zero_not_a_missing_value(host_module, tmp_path):
    document = run(host_module, tmp_path, {"p1": 5000}, plasmid_records=[("p1", 0, "plasmid", "5000M")], host_records=[])
    assert document["categories"]["host"] == {"reads": 0, "bases": 0, "read_fraction": 0.0, "base_fraction": 0.0}
    assert document["estimate"]["host_mass_fraction"] == 0.0
