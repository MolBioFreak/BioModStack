"""Pinned samtools consensus on synthetic known truth, not accuracy calibration."""
import json
import os
from pathlib import Path
import random
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETTING = json.loads((ROOT / "schemas/ngs_molbio/ngs-ont-fastq_qc-v1.schema.json").read_text())["properties"]["samtools_consensus_config"]


@pytest.mark.parametrize("preset", SETTING["enum"])
@pytest.mark.parametrize("variant", ["exact", "snp", "insertion", "deletion"])
def test_native_consensus_known_truth(tmp_path, preset, variant):
    image = os.environ.get("BMS_TEST_ONT_NATIVE_IMAGE")
    if not image:
        pytest.skip("requires explicitly selected pinned ONT native image")
    command = ["apptainer", "exec", "--bind", f"{tmp_path}:{tmp_path}", image, "samtools"]
    def run(*args):
        return subprocess.run([*command, *map(str, args)], text=True, capture_output=True, check=True)
    help_text = subprocess.run([*command, "consensus", "--help"], text=True, capture_output=True).stderr
    assert "hiseq, hifi, r10.4_sup, r10.4_dup and ultima" in help_text
    assert "Version: 1.24" in run("--version-only").stdout or run("--version-only").stdout.startswith("1.24")
    reference = "".join(random.Random(82).choices("ACGT", k=160))
    truth, cigar = reference, "160M"
    if variant == "snp":
        truth = reference[:80] + next(b for b in "ACGT" if b != reference[80]) + reference[81:]
    elif variant == "insertion":
        truth, cigar = reference[:80] + "GG" + reference[80:], "80M2I80M"
    elif variant == "deletion":
        truth, cigar = reference[:80] + reference[82:], "80M2D78M"
    sam = tmp_path / "reads.sam"
    sam.write_text("@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:truth\tLN:160\n" + "".join(
        f"r{i}\t{16 if i % 2 else 0}\ttruth\t1\t60\t{cigar}\t*\t0\t0\t{truth}\t{'I' * len(truth)}\n"
        for i in range(30)
    ))
    bam = tmp_path / "reads.bam"
    run("sort", "-o", bam, sam)
    args = [] if preset is None else ["--config", preset]
    fasta = run("consensus", "--mode", "bayesian", *args, "-f", "fasta", bam).stdout
    observed = "".join(line for line in fasta.splitlines() if not line.startswith(">"))
    (tmp_path / "consensus.fasta").write_text(fasta)
    assert observed == truth
    print(json.dumps({"preset": preset, "variant": variant, "truth_bases": len(truth), "observed_bases": len(observed), "exact_match": observed == truth}))
